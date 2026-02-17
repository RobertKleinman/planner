"""
routers/input.py — Universal Input Endpoint
=============================================
WHAT CHANGED FROM V1:
- Renamed from voice.py to input.py — accepts any input type
- Handles audio, image, video, and plain text
- Video: extracts audio track via ffmpeg, transcribes that
- Image: sends directly to Claude's vision for analysis
- The response is minimal — just a confirmation string

Your Shortcut still only needs to do: record → POST → show notification.
But now you can also send screenshots, photos, or typed text from
the future dashboard or PWA.
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

router = APIRouter(prefix="/api/v1", tags=["input"])

# Module dispatcher — maps module names to handler functions.
# Adding a new module = adding one line here.
MODULE_HANDLERS = {
    "memo": handle_memo,
    "diary": handle_memo,        # Same handler, different module_data schema
    "screenshot_note": handle_memo,
    "task": handle_memo,
    "expense": handle_memo,
    "food": handle_memo,
    "mood": handle_memo,
    "idea": handle_memo,
    "gym": handle_memo,
    "work": handle_memo,
    "calendar": handle_calendar,  # This one has Google Calendar + SMS side effects
}


def detect_input_type(filename: str, content_type: str = None) -> str:
    """Determine input type from filename/content type."""
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
    return "audio"  # Default assumption for phone recordings


async def extract_audio_from_video(video_bytes: bytes, filename: str) -> bytes:
    """
    Extract audio track from a video file using ffmpeg.

    ffmpeg is a command-line tool for processing media files. It comes
    pre-installed on most Linux servers (including DigitalOcean droplets).
    We write the video to a temp file, run ffmpeg to extract audio as mp3,
    and return the audio bytes.
    """
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
        # Clean up temp files
        for path in [video_path, audio_path]:
            if os.path.exists(path):
                os.unlink(path)


def get_image_media_type(filename: str) -> str:
    """Map file extension to MIME type for Claude's vision API."""
    ext = filename.lower().rsplit(".", 1)[-1] if "." in filename else "jpeg"
    return {
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "png": "image/png",
        "gif": "image/gif",
        "webp": "image/webp",
    }.get(ext, "image/jpeg")


@router.post("/input", response_model=InputResponse)
async def process_input(
    file: Optional[UploadFile] = File(None, description="Audio, image, or video file"),
    text: Optional[str] = Form(None, description="Direct text input (alternative to file)"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> InputResponse:
    """
    Universal input endpoint. Accepts audio, image, video, or text.

    Your Shortcut sends audio here. The dashboard can send text or images.
    Everything goes through the same pipeline: input → understand → route → respond.
    """

    transcript = None
    image_bytes = None
    image_description = None
    image_media_type = None
    input_type = "text"
    file_bytes = None

    # --- Read the uploaded file ---
    if file:
        file_bytes = await file.read()
        if len(file_bytes) == 0:
            raise HTTPException(status_code=400, detail="Empty file.")
        input_type = detect_input_type(file.filename, file.content_type)

    # --- Process based on input type ---

    if input_type == "audio" and file_bytes:
        # Audio → Whisper transcription
        try:
            transcript = await transcribe_audio(file_bytes, file.filename or "recording.m4a")
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Transcription failed: {e}")

    elif input_type == "video" and file_bytes:
        # Video → extract audio → Whisper transcription
        try:
            audio_bytes = await extract_audio_from_video(file_bytes, file.filename or "video.mp4")
            transcript = await transcribe_audio(audio_bytes, "extracted.mp3")
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Video processing failed: {e}")

    elif input_type == "image" and file_bytes:
        # Image → Claude Vision (no transcription needed)
        image_bytes = file_bytes
        image_media_type = get_image_media_type(file.filename or "image.jpg")

    elif text:
        # Direct text input
        transcript = text
        input_type = "text"

    else:
        raise HTTPException(status_code=400, detail="No input provided. Send a file or text.")

    if not transcript and not image_bytes:
        raise HTTPException(status_code=400, detail="Could not process the input.")

    # --- Classify intent ---
    try:
        intent_data = await classify_intent(
            transcript=transcript,
            image_bytes=image_bytes,
            image_media_type=image_media_type or "image/jpeg",
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Intent classification failed: {e}")

    # If image was analyzed, store Claude's description
    if image_bytes and not transcript:
        image_description = intent_data.get("data", {}).get("content", "Image analyzed")

    # --- Route to the appropriate module ---
    module_name = intent_data.get("module", "memo")
    handler = MODULE_HANDLERS.get(module_name, handle_memo)

    try:
        # Calendar module has a different signature than the generic memo handler
        if module_name == "calendar":
            response = await handler(
                user=user,
                raw_input=transcript or image_description or "",
                intent_data=intent_data,
                db=db,
                input_type=input_type,
                image_description=image_description,
            )
        else:
            response = await handle_memo(
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
