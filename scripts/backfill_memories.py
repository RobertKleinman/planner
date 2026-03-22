"""
backfill_memories.py — Consolidate existing conversations into memory
======================================================================
Runs the consolidation pipeline on all existing conversation sessions
to populate ProfileMemory, EpisodicMemory, and HypothesisMemory.

Usage:
    python -m scripts.backfill_memories
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import func
from app.database import SessionLocal, engine, Base
from app.models import User, ConversationMessage

# Ensure tables exist
Base.metadata.create_all(bind=engine)


def backfill():
    db = SessionLocal()
    try:
        # Get all users
        users = db.query(User).filter(User.is_active == True).all()

        for user in users:
            print(f"\nProcessing user: {user.name} (ID: {user.id})")

            # Get all unique session IDs with message counts
            sessions = (
                db.query(
                    ConversationMessage.session_id,
                    func.count(ConversationMessage.id).label("msg_count"),
                )
                .filter(ConversationMessage.user_id == user.id)
                .group_by(ConversationMessage.session_id)
                .having(func.count(ConversationMessage.id) >= 4)  # skip tiny sessions
                .order_by(func.min(ConversationMessage.created_at))
                .all()
            )

            print(f"  Found {len(sessions)} sessions with 4+ messages")

            for session_id, msg_count in sessions:
                print(f"  Consolidating session {session_id} ({msg_count} messages)...", end=" ", flush=True)
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
