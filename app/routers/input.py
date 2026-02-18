"""
routers/input.py — Universal Input Endpoint
=============================================
"""

from fastapi import APIRouter, Depends, UploadFile, File, Form, HTTPException
from sqlalchemy.orm import Session
from typing import Optional
import subprocess
import tempfile
import os

from app.database import get_db
from app.auth import get_current_user
from app.models import User
from app.schemas import InputResponse
from app.services.transcription import transcribe_audio
from app.services.intent import classify_intent
from app.modules.memo import handle_memo
from app.modules.calendar import handle_calendar
from app.modules.task import handle_task
from app.modules.remember import handle_remember
from app.modules.journal import handle_journal

router = APIRouter(prefix="/api/v1", tags=["input"])

MODULE_HANDLERS = {
    "memo": handle_memo,
    "diary": handle_memo,
    "screenshot_note": handle_memo,
    "expense": handle_memo,
    "food": handle_memo,
    "mood": handle_memo,
    "idea": handle_memo,
    "gym": handle_memo,
    "work": handle_memo,
    "calendar": handle_calendar,
    "task": handle_task,
    "remember": handle_remember,
    "journal": handle_journal,
}


def detect_input_type(filename: str, content_type: str = None) -> str:
    if not filename:
        return "text"
    ext = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    audio_exts = {"m4a", "mp3", "wav", "ogg", "flac", "webm", "mpeg", "mpga"}
    image_exts = {"jpg", "jpeg", "png", "gif", "webp", "heic", "heif"}
    video_exts = {"mp4", "mov", "avi", "mkv"}
    if ext in audio_exts:
        return "audio"
    elif ext in image_exts:
        return "image"
    elif ext in video_exts:
        return "video"
    return "audio"


async def extract_audio_from_video(video_bytes: bytes, filename: str) -> bytes:
    with tempfile.NamedTemporaryFile(suffix=f".{filename.rsplit('.', 1)[-1]}", delete=False) as video_file:
        video_file.write(video_bytes)
        video_path = video_file.name
    audio_path = video_path + ".mp3"
    try:
        subprocess.run(
            ["ffmpeg", "-i", video_path, "-vn", "-acodec", "libmp3lame", "-q:a", "4", audio_path, "-y"],
            capture_output=True, check=True, timeout=60,
        )
        with open(audio_path, "rb") as f:
            return f.read()
    finally:
        for path in [video_path, audio_path]:
            if os.path.exists(path):
                os.unlink(path)


def get_image_media_type(filename: str) -> str:
    ext = filename.lower().rsplit(".", 1)[-1] if "." in filename else "jpeg"
    return {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "gif": "image/gif", "webp": "image/webp"}.get(ext, "image/jpeg")


@router.post("/input", response_model=InputResponse)
async def process_input(
    file: Optional[UploadFile] = File(None, description="Audio, image, or video file"),
    text: Optional[str] = Form(None, description="Direct text input"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> InputResponse:
    transcript = None
    image_bytes = None
    image_description = None
    image_media_type = None
    input_type = "text"
    file_bytes = None

    if file:
        file_bytes = await file.read()
        if len(file_bytes) == 0:
            raise HTTPException(status_code=400, detail="Empty file.")
        input_type = detect_input_type(file.filename, file.content_type)

    if input_type == "audio" and file_bytes:
        try:
            transcript = await transcribe_audio(file_bytes, file.filename or "recording.m4a")
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Transcription failed: {e}")
    elif input_type == "video" and file_bytes:
        try:
            audio_bytes = await extract_audio_from_video(file_bytes, file.filename or "video.mp4")
            transcript = await transcribe_audio(audio_bytes, "extracted.mp3")
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Video processing failed: {e}")
    elif input_type == "image" and file_bytes:
        image_bytes = file_bytes
        image_media_type = get_image_media_type(file.filename or "image.jpg")
    elif text:
        transcript = text
        input_type = "text"
    else:
        raise HTTPException(status_code=400, detail="No input provided.")

    if not transcript and not image_bytes:
        raise HTTPException(status_code=400, detail="Could not process the input.")

    try:
        intent_data = await classify_intent(
            transcript=transcript,
            image_bytes=image_bytes,
            image_media_type=image_media_type or "image/jpeg",
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Intent classification failed: {e}")

    if image_bytes and not transcript:
        image_description = intent_data.get("data", {}).get("content", "Image analyzed")

    module_name = intent_data.get("module", "memo")
    handler = MODULE_HANDLERS.get(module_name, handle_memo)

    try:
        response = await handler(
            user=user,
            raw_input=transcript or image_description or "",
            intent_data=intent_data,
            db=db,
            input_type=input_type,
            image_description=image_description,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Module processing failed: {e}")

    return response
