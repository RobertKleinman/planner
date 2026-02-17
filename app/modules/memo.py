"""
modules/memo.py — Memo Module
================================
Handles: memo, diary, screenshot_note, idea, mood, work, gym, food, expense

IMPORTANT DESIGN DECISION:
For Phase 1, most modules that are really just "save to database with
structured metadata" share this handler. The LLM does the heavy lifting
of extracting the right fields into module_data. This handler just saves it.

As modules get more complex (e.g., expense tracking wants running totals,
gym wants progress charts), they'll get their own handlers. But for now,
this one function handles everything that's essentially "classify → store → confirm."

The calendar module is separate because it has side effects (Google Calendar
API call, SMS to Johnny) — it DOES something beyond just storing data.
"""

import json
from sqlalchemy.orm import Session
from app.models import User, Entry
from app.schemas import InputResponse


async def handle_memo(
    user: User,
    raw_input: str,
    intent_data: dict,
    db: Session,
    input_type: str = "audio",
    image_description: str = None,
) -> InputResponse:
    """
    Universal handler for modules that just need to store data.

    Works for: memo, diary, screenshot_note, idea, mood, work, gym,
    food, expense, task — anything that's "save and confirm."
    """
    module = intent_data.get("module", "memo")
    data = intent_data.get("data", {})

    # Determine processed content based on module type
    processed = (
        data.get("content")
        or data.get("description")
        or data.get("concept")
        or data.get("notes")
        or raw_input
    )

    entry = Entry(
        user_id=user.id,
        input_type=input_type,
        raw_transcript=raw_input if input_type in ("audio", "text", "video") else None,
        raw_image_description=image_description,
        processed_content=processed,
        title=intent_data.get("title", "Entry"),
        module=module,
        module_data=json.dumps(data) if data else None,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)

    return InputResponse(
        spoken_response=intent_data.get("spoken_response", "Saved."),
        entry_id=entry.id,
        module=module,
    )
