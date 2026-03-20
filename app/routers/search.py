"""
routers/search.py — Standalone Search API
===========================================
GET /api/v1/search?q=X with API key auth.
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import or_
from collections import defaultdict

from app.database import get_db
from app.auth import get_current_user
from app.models import User, Entry

router = APIRouter(prefix="/api/v1", tags=["search"])


@router.get("/search")
def search(
    q: str = Query(..., min_length=2),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    query_filter = or_(
        Entry.processed_content.ilike(f"%{q}%"),
        Entry.title.ilike(f"%{q}%"),
        Entry.raw_transcript.ilike(f"%{q}%"),
    )

    entries = db.query(Entry).filter(
        Entry.user_id == user.id,
        Entry.deleted_at.is_(None),
        query_filter,
    ).order_by(Entry.created_at.desc()).limit(50).all()

    results = defaultdict(list)
    for entry in entries:
        item = {
            "id": entry.id,
            "title": entry.title,
            "content": (entry.processed_content or "")[:120],
            "module": entry.module,
            "created_at": entry.created_at.isoformat() if entry.created_at else None,
        }

        if entry.task:
            item["task"] = {
                "description": entry.task.description,
                "priority": entry.task.priority,
                "status": entry.task.status,
            }
        elif entry.calendar_event:
            item["event"] = {
                "start_time": entry.calendar_event.start_time.isoformat() if entry.calendar_event.start_time else None,
                "location": entry.calendar_event.location,
            }
        elif entry.remember_item:
            item["remember"] = {
                "category": entry.remember_item.category,
            }
        elif entry.journal_entry:
            item["journal"] = {
                "tags": entry.journal_entry.tags or "",
            }

        results[entry.module].append(item)

    return {"results": dict(results), "query": q, "total": len(entries)}
