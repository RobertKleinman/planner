"""
routers/entries.py — Entry CRUD Endpoints (for Dashboard)
==========================================================
Browse, search, and filter all your entries. Used by the future React
dashboard. Supports filtering by module type, date range, and search.
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from datetime import datetime
from typing import Optional

from app.database import get_db
from app.auth import get_current_user
from app.models import User, Entry
from app.schemas import EntryResponse, EntryListResponse

router = APIRouter(prefix="/api/v1/entries", tags=["entries"])


@router.get("", response_model=EntryListResponse)
def list_entries(
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1, le=100),
    module: Optional[str] = Query(default=None, description="Filter by module: memo, calendar, mood, etc."),
    since: Optional[str] = Query(default=None, description="ISO date: entries after this date"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> EntryListResponse:
    """List entries with pagination and optional filtering."""
    query = db.query(Entry).filter(Entry.user_id == user.id)

    if module:
        query = query.filter(Entry.module == module)

    if since:
        try:
            since_dt = datetime.fromisoformat(since)
            query = query.filter(Entry.created_at >= since_dt)
        except ValueError:
            pass  # Ignore invalid date format

    total = query.count()
    entries = (
        query
        .order_by(Entry.created_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )

    return EntryListResponse(
        entries=[EntryResponse.model_validate(e) for e in entries],
        total=total,
        page=page,
        per_page=per_page,
    )


@router.get("/{entry_id}", response_model=EntryResponse)
def get_entry(
    entry_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> EntryResponse:
    """Get a single entry by ID."""
    entry = (
        db.query(Entry)
        .filter(Entry.id == entry_id, Entry.user_id == user.id)
        .first()
    )
    if not entry:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Entry not found.")
    return EntryResponse.model_validate(entry)
