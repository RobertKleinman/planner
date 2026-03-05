"""
modules/journal.py — Journal Module
=====================================
Keeps entries whole (no activity splitting), applies minimal grammar cleanup
via the LLM, and stores multiple tags per entry. The raw transcript is
preserved on Entry.raw_transcript for comparison.
"""

import json
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from app.models import User, Entry, JournalEntry
from app.schemas import InputResponse


async def handle_journal(user, raw_input, intent_data, db, input_type="audio", image_description=None):
    data = intent_data.get("data", {})

    # New format: single content + tags (no activities array)
    content = data.get("content", "")
    activity_type = data.get("activity_type", "general")
    tags_list = data.get("tags", [])

    # Backward compat: if LLM still returns old activities format, take first
    if not content and data.get("activities"):
        activities = data["activities"]
        content = activities[0].get("content", "") if activities else ""
        activity_type = activities[0].get("activity_type", "general") if activities else "general"
        tags_list = []
        for act in activities:
            topic = act.get("topic")
            if topic:
                tags_list.append(topic)

    if not content:
        content = raw_input or ""

    # Convert tags list to comma-separated string
    if isinstance(tags_list, list):
        tags_str = ",".join(t.strip() for t in tags_list if t and str(t).strip())
    else:
        tags_str = str(tags_list).strip()

    entry = Entry(
        user_id=user.id,
        input_type=input_type,
        raw_transcript=raw_input if input_type != "image" else None,
        raw_image_description=image_description,
        processed_content=content,
        title=content[:80] or "Journal",
        module="journal",
        module_data=json.dumps(data),
    )
    db.add(entry)
    db.flush()

    journal = JournalEntry(
        entry_id=entry.id,
        content=content,
        activity_type=activity_type,
        tags=tags_str if tags_str else None,
        date=datetime.now(timezone.utc),
    )
    db.add(journal)
    db.commit()

    # Build response
    tags_display = f" [{tags_str}]" if tags_str else ""
    response = f"Logged: {content[:60]}{tags_display}"

    return InputResponse(
        spoken_response=intent_data.get("spoken_response", response),
        entry_id=entry.id,
        module="journal",
    )
