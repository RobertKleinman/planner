"""
tests/test_memory.py — Focused tests for memory system
=========================================================
Tests: checkpoint advancement, duplicate suppression, null-decay,
session summary injection, and concurrency lock behavior.

Uses a temporary SQLite database — does not touch production.
"""

import json
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import (
    User, ConversationMessage, ProfileMemory, EpisodicMemory,
    HypothesisMemory, SessionSummary, ConsolidationCheckpoint,
)


@pytest.fixture
def db():
    """Create a fresh in-memory SQLite database for each test."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    # Create test user
    user = User(id=1, email="test@test.com", name="Test", api_key_hash="fake")
    session.add(user)
    session.commit()

    yield session
    session.close()


@pytest.fixture
def user(db):
    return db.query(User).first()


def _add_messages(db, user, session_id, count, start_id=1):
    """Helper: add N conversation messages."""
    for i in range(count):
        msg = ConversationMessage(
            id=start_id + i,
            user_id=user.id,
            session_id=session_id,
            role="user" if i % 2 == 0 else "assistant",
            content=json.dumps(f"Test message {start_id + i}"),
        )
        db.add(msg)
    db.commit()


# ─── Checkpoint Tests ────────────────────────────────────────

class TestCheckpointAdvancement:

    def test_checkpoint_created_on_first_check(self, db, user):
        from app.services.memory import _get_or_create_checkpoint
        cp = _get_or_create_checkpoint(user, "test:session", db)
        assert cp is not None
        assert cp.last_consolidated_message_id == 0
        assert cp.is_consolidating is False

    def test_should_consolidate_false_with_few_messages(self, db, user):
        from app.services.memory import should_consolidate
        _add_messages(db, user, "test:session", 5)
        assert should_consolidate(user, "test:session", db) is False

    def test_should_consolidate_true_with_enough_messages(self, db, user):
        from app.services.memory import should_consolidate, CONSOLIDATION_MESSAGE_THRESHOLD
        _add_messages(db, user, "test:session", CONSOLIDATION_MESSAGE_THRESHOLD + 1)
        assert should_consolidate(user, "test:session", db) is True

    @patch("app.services.memory._background_llm_call")
    def test_checkpoint_advances_on_success(self, mock_llm, db, user):
        """After successful consolidation, checkpoint should advance to max message ID."""
        from app.services.memory import consolidate_session, _get_or_create_checkpoint

        _add_messages(db, user, "test:session", 25)

        # Mock LLM returns no tool calls (nothing to extract)
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.tool_calls = None
        mock_llm.return_value = mock_response

        consolidate_session(user, "test:session", db)

        cp = _get_or_create_checkpoint(user, "test:session", db)
        assert cp.last_consolidated_message_id == 25
        assert cp.last_consolidated_at is not None
        assert cp.is_consolidating is False  # lock released

    @patch("app.services.memory._background_llm_call")
    def test_checkpoint_does_not_advance_on_failure(self, mock_llm, db, user):
        """If consolidation fails, checkpoint should NOT advance."""
        from app.services.memory import consolidate_session, _get_or_create_checkpoint

        _add_messages(db, user, "test:session", 25)
        mock_llm.side_effect = Exception("LLM call failed")

        consolidate_session(user, "test:session", db)

        cp = _get_or_create_checkpoint(user, "test:session", db)
        assert cp.last_consolidated_message_id == 0  # unchanged
        assert cp.is_consolidating is False  # lock released despite failure

    @patch("app.services.memory._background_llm_call")
    def test_delta_processing_only_new_messages(self, mock_llm, db, user):
        """Second consolidation should only process messages after the checkpoint."""
        from app.services.memory import consolidate_session, _get_or_create_checkpoint

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.tool_calls = None
        mock_llm.return_value = mock_response

        # First batch
        _add_messages(db, user, "test:session", 25, start_id=1)
        consolidate_session(user, "test:session", db)

        cp = _get_or_create_checkpoint(user, "test:session", db)
        assert cp.last_consolidated_message_id == 25

        # Second batch
        _add_messages(db, user, "test:session", 25, start_id=26)
        consolidate_session(user, "test:session", db)

        cp = _get_or_create_checkpoint(user, "test:session", db)
        assert cp.last_consolidated_message_id == 50


# ─── Duplicate Suppression Tests ─────────────────────────────

class TestDuplicateSuppression:

    def test_profile_duplicate_suppressed(self, db, user):
        """Inserting a profile fact with similar content should be skipped."""
        from app.services.memory import _process_consolidation_tool

        _process_consolidation_tool(
            "save_profile_fact",
            {"content": "User works in data governance", "category": "Work"},
            user, "test:session", db,
        )
        db.commit()

        # Try to insert a near-duplicate
        _process_consolidation_tool(
            "save_profile_fact",
            {"content": "User works in data governance and privacy", "category": "Work"},
            user, "test:session", db,
        )
        db.commit()

        count = db.query(ProfileMemory).filter(
            ProfileMemory.user_id == user.id,
            ProfileMemory.status == "active",
        ).count()
        assert count == 1  # duplicate suppressed

    def test_episode_duplicate_increments_recurrence(self, db, user):
        """Inserting a similar episode should increment recurrence, not create duplicate."""
        from app.services.memory import _process_consolidation_tool

        _process_consolidation_tool(
            "save_episode",
            {"summary": "Discussion about house renovation timeline", "importance": 0.6},
            user, "test:session", db,
        )
        db.commit()

        # Similar episode
        _process_consolidation_tool(
            "save_episode",
            {"summary": "Another discussion about house renovation timeline and costs", "importance": 0.7},
            user, "test:session", db,
        )
        db.commit()

        episodes = db.query(EpisodicMemory).filter(
            EpisodicMemory.user_id == user.id,
            EpisodicMemory.status == "active",
        ).all()
        assert len(episodes) == 1
        assert episodes[0].recurrence_count == 2

    def test_hypothesis_duplicate_strengthens_existing(self, db, user):
        """Proposing a similar hypothesis should strengthen the existing one."""
        from app.services.memory import _process_consolidation_tool

        _process_consolidation_tool(
            "propose_hypothesis",
            {"short_summary": "User tends to withdraw during conflict",
             "long_summary": "When criticism becomes intense the user tends to withdraw",
             "category": "behavioral"},
            user, "test:session", db,
        )
        db.commit()

        # Similar hypothesis
        _process_consolidation_tool(
            "propose_hypothesis",
            {"short_summary": "User tends to withdraw during conflict situations",
             "long_summary": "Pattern of withdrawal when criticized harshly",
             "category": "behavioral"},
            user, "test:session", db,
        )
        db.commit()

        hypotheses = db.query(HypothesisMemory).filter(
            HypothesisMemory.user_id == user.id,
            HypothesisMemory.status.in_(["provisional", "active"]),
        ).all()
        assert len(hypotheses) == 1
        assert hypotheses[0].evidence_for == 2  # strengthened


# ─── Null-Decay Tests ────────────────────────────────────────

class TestNullDecay:

    def test_null_last_confirmed_hypothesis_decays(self, db, user):
        """Hypotheses with NULL last_confirmed should be caught by decay."""
        from app.services.memory import decay_old_memories

        # Create hypothesis with NULL last_confirmed, old enough to decay significantly
        # 200 days old: decay = max(0.5, 1.0 - 200/180) = 0.5
        # base = 1/(1+1) = 0.5, confidence = 0.5 * 0.5 = 0.25 → challenged
        h = HypothesisMemory(
            user_id=user.id,
            short_summary="Test pattern",
            long_summary="Test long pattern",
            confidence=0.5,
            evidence_for=1,
            evidence_against=1,  # mixed evidence so base < 1.0
            status="provisional",
            last_confirmed=None,  # intentionally NULL
            created_at=datetime.now(timezone.utc) - timedelta(days=200),
        )
        db.add(h)
        db.commit()

        decay_old_memories(user, db)

        db.refresh(h)
        assert h.confidence < 0.5  # should have decayed: base=0.5 * decay=0.5 = 0.25
        # 0.25 is above the 0.2 "challenged" threshold, so stays provisional
        assert h.status in ("provisional", "challenged")

    def test_confirmed_hypothesis_within_window_does_not_decay(self, db, user):
        """Recently confirmed hypotheses should NOT decay."""
        from app.services.memory import decay_old_memories

        h = HypothesisMemory(
            user_id=user.id,
            short_summary="Recent pattern",
            long_summary="Recently confirmed pattern",
            confidence=0.7,
            evidence_for=4,
            evidence_against=0,
            status="active",
            last_confirmed=datetime.now(timezone.utc) - timedelta(days=10),
            created_at=datetime.now(timezone.utc) - timedelta(days=60),
        )
        db.add(h)
        db.commit()

        decay_old_memories(user, db)

        db.refresh(h)
        assert h.confidence == 0.7  # unchanged
        assert h.status == "active"

    def test_old_episode_importance_decays(self, db, user):
        """Old episodes with no updates should lose importance."""
        from app.services.memory import decay_old_memories

        e = EpisodicMemory(
            user_id=user.id,
            summary="Old event",
            importance=0.5,
            status="active",
            updated_at=datetime.now(timezone.utc) - timedelta(days=120),
            created_at=datetime.now(timezone.utc) - timedelta(days=120),
        )
        db.add(e)
        db.commit()

        decay_old_memories(user, db)

        db.refresh(e)
        assert e.importance < 0.5  # decayed


# ─── Session Summary Tests ───────────────────────────────────

class TestSessionSummary:

    def test_should_generate_summary_false_with_few_messages(self, db, user):
        from app.services.memory import should_generate_summary
        _add_messages(db, user, "test:session", 5)
        assert should_generate_summary(user, "test:session", db) is False

    def test_should_generate_summary_true_with_enough_messages(self, db, user):
        from app.services.memory import should_generate_summary, SUMMARY_MESSAGE_INTERVAL
        _add_messages(db, user, "test:session", SUMMARY_MESSAGE_INTERVAL + 1)
        assert should_generate_summary(user, "test:session", db) is True

    def test_get_session_context_without_summary(self, db, user):
        """Without a summary, should load up to MAX_HISTORY_NO_SUMMARY messages."""
        from app.services.memory import get_session_context, MAX_HISTORY_NO_SUMMARY
        _add_messages(db, user, "test:session", 60)

        history, summary = get_session_context(user, "test:session", db)
        assert summary is None
        assert len(history) <= MAX_HISTORY_NO_SUMMARY

    def test_get_session_context_with_summary(self, db, user):
        """With a summary, should load fewer raw messages + the summary text."""
        from app.services.memory import get_session_context, MAX_HISTORY

        _add_messages(db, user, "test:session", 60)

        # Add a session summary
        db.add(SessionSummary(
            user_id=user.id,
            session_id="test:session",
            summary="User discussed work projects and scheduled meetings.",
            message_id_start=1,
            message_id_end=40,
            message_count=40,
        ))
        db.commit()

        history, summary = get_session_context(user, "test:session", db)
        assert summary is not None
        assert "work projects" in summary
        assert len(history) <= MAX_HISTORY


# ─── Concurrency Lock Tests ──────────────────────────────────

class TestConcurrencyLock:

    @patch("app.services.memory._background_llm_call")
    def test_lock_released_after_success(self, mock_llm, db, user):
        from app.services.memory import consolidate_session, _get_or_create_checkpoint

        _add_messages(db, user, "test:session", 25)
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.tool_calls = None
        mock_llm.return_value = mock_response

        consolidate_session(user, "test:session", db)

        cp = _get_or_create_checkpoint(user, "test:session", db)
        assert cp.is_consolidating is False

    @patch("app.services.memory._background_llm_call")
    def test_lock_released_after_failure(self, mock_llm, db, user):
        from app.services.memory import consolidate_session, _get_or_create_checkpoint

        _add_messages(db, user, "test:session", 25)
        mock_llm.side_effect = Exception("boom")

        consolidate_session(user, "test:session", db)

        cp = _get_or_create_checkpoint(user, "test:session", db)
        assert cp.is_consolidating is False

    def test_concurrent_consolidation_skipped(self, db, user):
        """If lock is held, second consolidation should skip."""
        from app.services.memory import consolidate_session, _get_or_create_checkpoint

        _add_messages(db, user, "test:session", 25)

        # Manually set lock
        cp = _get_or_create_checkpoint(user, "test:session", db)
        cp.is_consolidating = True
        db.commit()

        # This should skip without error
        consolidate_session(user, "test:session", db)

        db.refresh(cp)
        assert cp.last_consolidated_message_id == 0  # unchanged — was skipped
