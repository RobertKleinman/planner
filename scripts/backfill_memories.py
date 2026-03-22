"""
backfill_memories.py — Consolidate existing conversations into memory
======================================================================
Idempotent: tracks which sessions have been processed via ConsolidationCheckpoint.
Only processes messages not yet consolidated.

Usage:
    python -m scripts.backfill_memories
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import func
from app.database import SessionLocal, engine, Base
from app.models import User, ConversationMessage

Base.metadata.create_all(bind=engine)


def backfill():
    db = SessionLocal()
    try:
        users = db.query(User).filter(User.is_active == True).all()

        for user in users:
            print(f"\nProcessing user: {user.name} (ID: {user.id})")

            sessions = (
                db.query(
                    ConversationMessage.session_id,
                    func.count(ConversationMessage.id).label("msg_count"),
                )
                .filter(ConversationMessage.user_id == user.id)
                .group_by(ConversationMessage.session_id)
                .having(func.count(ConversationMessage.id) >= 4)
                .order_by(func.min(ConversationMessage.created_at))
                .all()
            )

            print(f"  Found {len(sessions)} sessions with 4+ messages")

            for session_id, msg_count in sessions:
                # Check if already fully consolidated
                from app.services.memory import _get_or_create_checkpoint
                checkpoint = _get_or_create_checkpoint(user, session_id, db)

                max_msg_id = db.query(func.max(ConversationMessage.id)).filter(
                    ConversationMessage.user_id == user.id,
                    ConversationMessage.session_id == session_id,
                ).scalar() or 0

                remaining = max_msg_id - checkpoint.last_consolidated_message_id
                if remaining < 4:
                    print(f"  Skipping {session_id} — already consolidated")
                    continue

                print(f"  Consolidating {session_id} ({remaining} new messages)...", end=" ", flush=True)
                try:
                    from app.services.memory import consolidate_session
                    consolidate_session(user, session_id, db)
                    print("done")
                except Exception as e:
                    print(f"FAILED: {e}")

        print("\nBackfill complete!")

    finally:
        db.close()


if __name__ == "__main__":
    backfill()
