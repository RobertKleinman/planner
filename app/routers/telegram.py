"""
routers/telegram.py — Telegram Bot Webhook
============================================
Voice, photo, and text messages → AssistantService with persistent sessions.
Auth linking: /start <api_key> → stores chat_id on User.
"""

import asyncio
import io
import logging

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse

from app.config import settings
from app.database import SessionLocal
from app.auth import hash_api_key
from app.models import User
from app.services.transcription import transcribe_audio
from app.services import assistant as assistant_service

logger = logging.getLogger("planner.telegram")

router = APIRouter(prefix="/telegram", tags=["telegram"])

# Lazy-initialized bot for downloading files
_bot = None


def _get_bot():
    global _bot
    if _bot is None:
        from aiogram import Bot
        _bot = Bot(token=settings.telegram_bot_token)
    return _bot


def _get_user_by_chat_id(db, chat_id: str) -> User:
    return db.query(User).filter(
        User.telegram_chat_id == chat_id, User.is_active == True
    ).first()


async def _download_file(file_id: str) -> bytes:
    """Download a file from Telegram CDN."""
    bot = _get_bot()
    file = await bot.get_file(file_id)
    buf = io.BytesIO()
    await bot.download_file(file.file_path, buf)
    return buf.getvalue()


async def _handle_start(chat_id: str, text: str, db) -> str:
    """Handle /start <api_key> — link Telegram chat to user account."""
    parts = text.strip().split(maxsplit=1)
    if len(parts) < 2:
        return "Welcome! Send /start <your_api_key> to link your account."

    api_key = parts[1].strip()
    key_hash = hash_api_key(api_key)
    user = db.query(User).filter(User.api_key_hash == key_hash, User.is_active == True).first()

    if not user:
        return "Invalid API key. Check your key and try again."

    # Clear any existing chat_id link for this user or this chat
    old = db.query(User).filter(User.telegram_chat_id == chat_id).first()
    if old and old.id != user.id:
        old.telegram_chat_id = None

    user.telegram_chat_id = chat_id
    db.commit()

    return f"Linked! Hi {user.name}. Send me voice, photos, or text and I'll process them."


@router.post("/webhook")
async def telegram_webhook(request: Request):
    """Receive Telegram webhook updates."""
    # Verify webhook secret if configured
    if settings.telegram_webhook_secret:
        secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if secret != settings.telegram_webhook_secret:
            raise HTTPException(status_code=403, detail="Invalid webhook secret")

    body = await request.json()
    message = body.get("message")
    if not message:
        return JSONResponse(content={"ok": True})

    chat_id = str(message["chat"]["id"])
    text = message.get("text", "")

    db = SessionLocal()
    try:
        # Handle /start command (auth linking)
        if text.startswith("/start"):
            reply = await _handle_start(chat_id, text, db)
            bot = _get_bot()
            await bot.send_message(chat_id=chat_id, text=reply)
            return JSONResponse(content={"ok": True})

        # All other messages require a linked account
        user = _get_user_by_chat_id(db, chat_id)
        if not user:
            bot = _get_bot()
            await bot.send_message(chat_id=chat_id, text="Account not linked. Send /start <your_api_key> first.")
            return JSONResponse(content={"ok": True})

        session_id = f"telegram:{chat_id}"
        transcript = None
        image_bytes = None
        image_media_type = None
        input_type = "text"

        # Voice message
        voice = message.get("voice") or message.get("audio")
        if voice:
            audio_bytes = await _download_file(voice["file_id"])
            transcript = transcribe_audio(audio_bytes, "telegram_voice.ogg")
            input_type = "audio"
            logger.info(f"Telegram voice from {user.name}: {transcript[:80]}")

        # Photo
        elif message.get("photo"):
            photo = message["photo"][-1]
            image_bytes = await _download_file(photo["file_id"])
            image_media_type = "image/jpeg"
            caption = message.get("caption", "")
            transcript = caption if caption else None
            input_type = "image"
            logger.info(f"Telegram photo from {user.name}, caption: {(caption or '')[:60]}")

        # Text message
        elif text:
            transcript = text
            input_type = "text"
            logger.info(f"Telegram text from {user.name}: {text[:80]}")

        if not transcript and not image_bytes:
            bot = _get_bot()
            await bot.send_message(chat_id=chat_id, text="I didn't get anything there. Try again?")
            return JSONResponse(content={"ok": True})

        # Run assistant in a thread to avoid blocking the event loop
        result = await asyncio.to_thread(
            assistant_service.run,
            user=user,
            message_text=transcript,
            image_bytes=image_bytes,
            image_media_type=image_media_type,
            db=db,
            session_id=session_id,
            input_type=input_type,
        )
        reply = result.text

        if reply:
            bot = _get_bot()
            await bot.send_message(chat_id=chat_id, text=reply)

    except Exception as e:
        logger.error(f"Telegram webhook error: {e}", exc_info=True)
        try:
            bot = _get_bot()
            await bot.send_message(chat_id=chat_id, text="Something went wrong processing your message.")
        except Exception:
            pass
    finally:
        db.close()

    return JSONResponse(content={"ok": True})
