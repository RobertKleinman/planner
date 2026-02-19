"""
routers/input.py — Universal Input Endpoint
=============================================
Supports multi-intent: a single recording can trigger multiple modules.
Post-processing: auto-completes tasks when actions imply they're done.
"""

import json
import traceback
from fastapi import APIRouter, Depends, UploadFile, File, Form, HTTPException
from sqlalchemy.orm import Session
from typing import Optional
from datetime import datetime, timezone
import subprocess
import tempfile
import os

from anthropic import Anthropic
from app.database import get_db
from app.auth import get_current_user
from app.models import User, Entry, Task
from app.schemas import InputResponse
from app.config import settings
from app.services.transcription import transcribe_audio
from app.services.intent import classify_intent
from app.modules.memo import handle_memo
from app.modules.calendar import handle_calendar
from app.modules.task import handle_task
from app.modules.remember import handle_remember
from app.modules.journal import handle_journal

router = APIRouter(prefix="/api/v1", tags=["input"])

client = Anthropic(api_key=settings.anthropic_api_key)

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


async def auto_complete_tasks(user: User, intents: list, db: Session) -> list:
    """
    Post-processing: checks if processed intents imply an open task is done.
    e.g. "ate an apple" (food) completes open task "eat an apple"
    """
    open_tasks = (
        db.query(Task).join(Entry)
        .filter(Entry.user_id == user.id, Task.status == "open")
        .all()
    )
    if not open_tasks:
        return []

    actions_done = []
    for intent in intents:
        module = intent.get("module", "")
        data = intent.get("data", {})

        if module == "task":
            continue

        if module == "journal":
            for act in data.get("activities", []):
                actions_done.append(act.get("content", ""))
        elif module == "food":
            items = data.get("items", [])
            if items:
                actions_done.append(f"Ate {', '.join(items) if isinstance(items, list) else items}")
        elif module == "calendar":
            actions_done.append(f"Scheduled {data.get('title', '')}")
        elif module == "gym":
            exercises = data.get("exercises", [])
            if exercises:
                actions_done.append(f"Did gym: {', '.join(e.get('name','') for e in exercises)}")
            else:
                actions_done.append("Went to the gym")
        elif module == "expense":
            actions_done.append(f"Spent money at {data.get('vendor', 'somewhere')}")
        elif module in ("mood", "remember", "diary"):
            pass
        else:
            content = data.get("content", "")
            if content:
                actions_done.append(content)

    if not actions_done:
        return []

    task_list = "\n".join(f"  ID {t.id}: {t.description} [{t.group}]" for t in open_tasks)
    actions_list = "\n".join(f"  - {a}" for a in actions_done if a)

    print(f"[PLANNER] Auto-complete: {len(actions_done)} actions vs {len(open_tasks)} open tasks")

    try:
        response = client.messages.create(
            model=settings.intent_model,
            max_tokens=512,
            system="You match completed activities to open tasks. Respond with ONLY valid JSON — no markdown, no backticks.",
            messages=[{
                "role": "user",
                "content": f"""The user just reported doing these things:
{actions_list}

Their open tasks are:
{task_list}

Which open tasks were implicitly completed by the activities above? Return:
{{"matched_ids": [list of task IDs], "explanation": "brief reason"}}

Rules:
- "ate an apple" completes "eat an apple"
- "went to the gym" completes "go to the gym"
- "walked the dogs" completes "walk Biscuit and Bentley"
- Be generous if the activity clearly fulfills the task.
- Don't match unrelated things.
- If nothing matches, return empty list.
- Only return IDs from the list above."""
            }],
        )
        result = json.loads(response.content[0].text.strip())
        matched_ids = result.get("matched_ids", [])
    except Exception as e:
        print(f"[PLANNER] Auto-complete error: {e}")
        return []

    completed = []
    for task in open_tasks:
        if task.id in matched_ids:
            task.status = "done"
            task.completed_at = datetime.now(timezone.utc)
            completed.append(task)
            print(f"[PLANNER] Auto-completed: {task.description}")

    if completed:
        db.commit()
    return completed


@router.post("/input", response_model=InputResponse)
async def process_input(
    file: Optional[UploadFile] = File(None),
    text: Optional[str] = Form(None),
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

    print(f"[PLANNER] === New input ===")
    print(f"[PLANNER] User: {user.name} | Type: {input_type}")
    print(f"[PLANNER] Transcript: {transcript}")

    try:
        intents = await classify_intent(
            transcript=transcript,
            image_bytes=image_bytes,
            image_media_type=image_media_type or "image/jpeg",
        )
    except Exception as e:
        print(f"[PLANNER] ERROR classify_intent: {e}")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Intent classification failed: {e}")

    print(f"[PLANNER] Intents: {len(intents)}")
    for i, intent in enumerate(intents):
        print(f"[PLANNER]   {i+1}. {intent.get('module')} — {intent.get('title')}")

    if image_bytes and not transcript:
        image_description = intents[0].get("data", {}).get("content", "Image analyzed") if intents else "Image analyzed"

    responses = []
    first_entry_id = None

    for i, intent_data in enumerate(intents):
        module_name = intent_data.get("module", "memo")
        handler = MODULE_HANDLERS.get(module_name, handle_memo)
        print(f"[PLANNER] Processing {i+1}/{len(intents)}: {module_name}")

        try:
            response = await handler(
                user=user, raw_input=transcript or image_description or "",
                intent_data=intent_data, db=db,
                input_type=input_type, image_description=image_description,
            )
            responses.append(response.spoken_response)
            if first_entry_id is None:
                first_entry_id = response.entry_id
            print(f"[PLANNER]   OK: {response.spoken_response[:80]}")
        except Exception as e:
            print(f"[PLANNER]   ERROR {module_name}: {e}")
            traceback.print_exc()
            responses.append(f"Error processing {module_name}: {e}")

    # Post-processing: auto-complete tasks
    try:
        auto_completed = await auto_complete_tasks(user, intents, db)
        if auto_completed:
            names = ", ".join(t.description for t in auto_completed)
            responses.append(f"Also marked as done: {names}.")
            print(f"[PLANNER] Auto-completed: {names}")
    except Exception as e:
        print(f"[PLANNER] Auto-complete error: {e}")
        traceback.print_exc()

    combined_response = " ".join(responses)
    primary_module = intents[0].get("module", "memo") if intents else "memo"
    print(f"[PLANNER] === Done. {len(responses)} responses. Primary: {primary_module} ===")

    return InputResponse(
        spoken_response=combined_response,
        entry_id=first_entry_id or 0,
        module=primary_module,
    )
