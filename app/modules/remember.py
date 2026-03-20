"""
modules/remember.py — Remember Module
"""

import json
from sqlalchemy.orm import Session
from app.models import User, Entry, RememberItem
from app.schemas import InputResponse


def handle_remember(user, raw_input, intent_data, db, input_type="audio", image_description=None):
    data = intent_data.get("data", {})
    items_data = data.get("items", [])

    if not items_data and data.get("content"):
        items_data = [{"content": data["content"], "category": data.get("category", "General"), "tags": data.get("tags", [])}]

    existing_cats = [c[0] for c in db.query(RememberItem.category).join(Entry).filter(Entry.user_id == user.id, Entry.deleted_at.is_(None)).distinct().all() if c[0]]

    created = []
    first_entry_id = None
    pending = []

    for item in items_data:
        category = item.get("category", "General")
        for existing in existing_cats:
            if existing.lower() == category.lower():
                category = existing
                break

        tags = item.get("tags", [])
        tags_str = ",".join(tags) if isinstance(tags, list) else str(tags)

        entry = Entry(
            user_id=user.id, input_type=input_type,
            raw_transcript=raw_input if input_type != "image" else None,
            raw_image_description=image_description,
            processed_content=item.get("content", ""),
            title=item.get("content", "Remember")[:80],
            module="remember", module_data=json.dumps(item),
        )
        db.add(entry)
        pending.append({"entry": entry, "content": item.get("content", ""), "category": category, "tags_str": tags_str})
        if category not in existing_cats:
            existing_cats.append(category)

    # Flush to get entry IDs, then create all remember items
    db.flush()
    for info in pending:
        if first_entry_id is None:
            first_entry_id = info["entry"].id
        remember = RememberItem(
            entry_id=info["entry"].id, content=info["content"],
            category=info["category"], tags=info["tags_str"] if info["tags_str"] else None,
        )
        db.add(remember)
        created.append(remember)
    db.commit()

    if len(created) == 1:
        response = f"Noted under {created[0].category}: {created[0].content[:60]}"
    else:
        cats = set(r.category for r in created)
        response = f"Saved {len(created)} items under {', '.join(sorted(cats))}."

    return InputResponse(spoken_response=intent_data.get("spoken_response", response), entry_id=first_entry_id or 0, module="remember")
