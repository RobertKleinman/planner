"""
modules/journal.py — Journal Module
"""

import json
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from app.models import User, Entry, JournalEntry
from app.schemas import InputResponse


async def handle_journal(user, raw_input, intent_data, db, input_type="audio", image_description=None):
    data = intent_data.get("data", {})
    activities = data.get("activities", [])

    if not activities and data.get("content"):
        activities = [{"content": data["content"], "activity_type": data.get("activity_type", "general"), "topic": data.get("topic")}]

    existing_topics = [t[0] for t in db.query(JournalEntry.topic).join(Entry).filter(Entry.user_id == user.id, Entry.deleted_at.is_(None), JournalEntry.topic.isnot(None)).distinct().all() if t[0]]

    created = []
    first_entry_id = None

    for activity in activities:
        topic = activity.get("topic")
        if topic:
            for existing in existing_topics:
                if existing.lower() == topic.lower():
                    topic = existing
                    break

        entry = Entry(
            user_id=user.id, input_type=input_type,
            raw_transcript=raw_input if input_type != "image" else None,
            raw_image_description=image_description,
            processed_content=activity.get("content", ""),
            title=activity.get("content", "Journal")[:80],
            module="journal", module_data=json.dumps(activity),
        )
        db.add(entry); db.commit(); db.refresh(entry)
        if first_entry_id is None:
            first_entry_id = entry.id

        journal = JournalEntry(
            entry_id=entry.id, content=activity.get("content", ""),
            activity_type=activity.get("activity_type", "general"),
            topic=topic, date=datetime.now(timezone.utc),
        )
        db.add(journal); db.commit()
        created.append(journal)
        if topic and topic not in existing_topics:
            existing_topics.append(topic)

    if len(created) == 1:
        topic_str = f" [{created[0].topic}]" if created[0].topic else ""
        response = f"Logged: {created[0].content[:60]}{topic_str}"
    else:
        response = f"Logged {len(created)} activities for today."

    return InputResponse(spoken_response=intent_data.get("spoken_response", response), entry_id=first_entry_id or 0, module="journal")
