"""
modules/remember.py — Remember Module
========================================
Stores things the user wants to remember — facts, preferences,
references, information to keep handy. Categorized and timestamped.
"""

import json
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from app.models import User, Entry, RememberItem
from app.schemas import InputResponse


async def handle_remember(
    user: User,
    raw_input: str,
    intent_data: dict,
    db: Session,
    input_type: str = "audio",
    image_description: str = None,
) -> InputResponse:
    """Store one or more things to remember."""
    data = intent_data.get("data", {})
    items_data = data.get("items", [])

    # If classifier returned a single item
    if not items_data and data.get("content"):
        items_data = [{
            "content": data["content"],
            "category": data.get("category", "General"),
            "tags": data.get("tags", []),
        }]

    # Get existing categories for consistency
    existing_cats = (
        db.query(RememberItem.category)
        .join(Entry)
        .filter(Entry.user_id == user.id)
        .distinct()
        .all()
    )
    existing_cat_names = [c[0] for c in existing_cats if c[0]]

    created = []
    first_entry_id = None

    for item in items_data:
        # Match existing category casing
        category = item.get("category", "General")
        for existing in existing_cat_names:
            if existing.lower() == category.lower():
                category = existing
                break

        tags = item.get("tags", [])
        if isinstance(tags, list):
            tags_str = ",".join(tags)
        else:
            tags_str = str(tags)

        entry = Entry(
            user_id=user.id,
            input_type=input_type,
            raw_transcript=raw_input if input_type != "image" else None,
            raw_image_description=image_description,
            processed_content=item.get("content", ""),
            title=item.get("content", "Remember")[:80],
            module="remember",
            module_data=json.dumps(item),
        )
        db.add(entry)
        db.commit()
        db.refresh(entry)

        if first_entry_id is None:
            first_entry_id = entry.id

        remember = RememberItem(
            entry_id=entry.id,
            content=item.get("content", ""),
            category=category,
            tags=tags_str if tags_str else None,
        )
        db.add(remember)
        db.commit()
        created.append(remember)

        if category not in existing_cat_names:
            existing_cat_names.append(category)

    if len(created) == 1:
        response = f"Noted under {created[0].category}: {created[0].content[:60]}"
    else:
        cats = set(r.category for r in created)
        response = f"Saved {len(created)} items under {', '.join(sorted(cats))}."

    return InputResponse(
        spoken_response=intent_data.get("spoken_response", response),
        entry_id=first_entry_id or 0,
        module="remember",
    )
