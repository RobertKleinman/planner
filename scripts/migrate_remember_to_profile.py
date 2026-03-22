"""
migrate_remember_to_profile.py — One-time migration
=====================================================
Copies existing RememberItem records into the new ProfileMemory table.
All migrated records get confidence=1.0 and source="explicit" since
they were explicitly saved by the user.

Usage:
    python -m scripts.migrate_remember_to_profile
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timezone
from sqlalchemy.orm import Session
from app.database import SessionLocal, engine, Base
from app.models import RememberItem, ProfileMemory, Entry

# Ensure tables exist
Base.metadata.create_all(bind=engine)


def migrate():
    db: Session = SessionLocal()
    try:
        # Get all active RememberItems
        items = (
            db.query(RememberItem)
            .join(Entry)
            .filter(Entry.deleted_at.is_(None))
            .all()
        )

        if not items:
            print("No RememberItems to migrate.")
            return

        migrated = 0
        skipped = 0

        for item in items:
            # Check if already migrated (avoid duplicates on re-run)
            existing = db.query(ProfileMemory).filter(
                ProfileMemory.user_id == item.entry.user_id,
                ProfileMemory.content == item.content,
                ProfileMemory.status == "active",
            ).first()

            if existing:
                skipped += 1
                continue

            profile = ProfileMemory(
                user_id=item.entry.user_id,
                content=item.content,
                category=item.category or "General",
                confidence=1.0,
                source="explicit",
                tags=item.tags,
                status="active",
                last_confirmed=item.created_at or datetime.now(timezone.utc),
                created_at=item.created_at or datetime.now(timezone.utc),
            )
            db.add(profile)
            migrated += 1

        db.commit()
        print(f"Migration complete: {migrated} migrated, {skipped} skipped (already exist)")

    finally:
        db.close()


if __name__ == "__main__":
    migrate()
