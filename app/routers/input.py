"""
routers/input.py — Universal Input Endpoint
=============================================
Transcribes audio/video/image, then delegates to AssistantService.
"""

import logging
import traceback
from uuid import uuid4
from fastapi import APIRouter, Depends, UploadFile, File, Form, HTTPException, BackgroundTasks
from sqlalchemy.orm import Session
from typing import Optional
import subprocess
import tempfile
import os

from app.database import get_db, SessionLocal
from app.auth import get_current_user
from app.models import User, MemoTopic
from app.schemas import InputResponse
from app.services.transcription import transcribe_audio
from app.services import assistant as assistant_service

logger = logging.getLogger("planner.input")

router = APIRouter(prefix="/api/v1", tags=["input"])


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


def extract_audio_from_video(video_bytes: bytes, filename: str) -> bytes:
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


def _bg_link_memo_topics(user_id: int, entry_ids: list):
    """Background task: link entries to memo topics using entry content."""
    db = SessionLocal()
    try:
        from app.models import Entry
        active_memo_topics = db.query(MemoTopic).filter(
            MemoTopic.user_id == user_id, MemoTopic.is_active == True
        ).all()
        if not active_memo_topics or not entry_ids:
            return

        # For now, memo topic linking relies on the LLM having mentioned topics
        # in the system prompt. The assistant's tool calls create entries that
        # can be linked to topics via the dashboard or future enhancement.
        # This is a placeholder for backward compatibility.
        logger.info(f"Memo topic linking: {len(entry_ids)} entries, {len(active_memo_topics)} active topics")
    except Exception as e:
        logger.error(f"Memo topic linking error: {e}")
    finally:
        db.close()


@router.post("/input", response_model=InputResponse)
def process_input(
    background_tasks: BackgroundTasks,
    file: Optional[UploadFile] = File(None),
    text: Optional[str] = Form(None),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> InputResponse:

    transcript = None
    image_bytes = None
    image_media_type = None
    input_type = "text"
    file_bytes = None

    if file:
        file_bytes = file.file.read()
        if len(file_bytes) == 0:
            raise HTTPException(status_code=400, detail="Empty file.")
        input_type = detect_input_type(file.filename, file.content_type)

    if input_type == "audio" and file_bytes:
        try:
            transcript = transcribe_audio(file_bytes, file.filename or "recording.m4a")
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Transcription failed: {e}")
    elif input_type == "video" and file_bytes:
        try:
            audio_bytes = extract_audio_from_video(file_bytes, file.filename or "video.mp4")
            transcript = transcribe_audio(audio_bytes, "extracted.mp3")
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

    logger.info(f"=== New input === User: {user.name} | Type: {input_type}")
    if transcript:
        logger.info(f"Transcript: {transcript}")

    try:
        result = assistant_service.run(
            user=user,
            message_text=transcript,
            image_bytes=image_bytes,
            image_media_type=image_media_type,
            db=db,
            session_id=f"api:{uuid4()}",
            input_type=input_type,
        )
    except Exception as e:
        logger.error(f"Assistant error: {e}")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Processing failed: {e}")

    # Background: link memo topics if applicable
    if result.entry_ids:
        background_tasks.add_task(_bg_link_memo_topics, user.id, result.entry_ids)

    logger.info(f"=== Done. Modules: {result.modules_used} ===")

    return InputResponse(
        spoken_response=result.text,
        entry_id=result.entry_ids[0] if result.entry_ids else 0,
        module=result.modules_used[0] if result.modules_used else "memo",
    )
