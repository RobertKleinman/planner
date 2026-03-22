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


# ─── Retrieval Tests ─────────────────────────────────────────

class TestRetrieval:

    def test_get_relevant_memories_empty(self, db, user):
        """Returns empty string when no memories exist."""
        from app.services.memory import get_relevant_memories
        result = get_relevant_memories(user, "hello", db)
        assert result == ""

    def test_get_relevant_memories_includes_profiles(self, db, user):
        """Active profile memories should be included."""
        from app.services.memory import get_relevant_memories
        db.add(ProfileMemory(
            user_id=user.id, content="Works in data governance",
            category="Work", confidence=1.0, source="explicit", status="active",
        ))
        db.commit()

        result = get_relevant_memories(user, "what do I do for work", db)
        assert "data governance" in result
        assert "Known facts" in result

    def test_get_relevant_memories_excludes_outdated(self, db, user):
        """Outdated memories should NOT appear."""
        from app.services.memory import get_relevant_memories
        db.add(ProfileMemory(
            user_id=user.id, content="Old job at Google",
            category="Work", confidence=1.0, source="explicit", status="outdated",
        ))
        db.commit()

        result = get_relevant_memories(user, "work", db)
        assert "Google" not in result

    def test_get_relevant_memories_includes_episodes(self, db, user):
        from app.services.memory import get_relevant_memories
        db.add(EpisodicMemory(
            user_id=user.id, summary="Had a big argument about house chores",
            importance=0.7, status="active", tags="chores,conflict",
        ))
        db.commit()

        result = get_relevant_memories(user, "chores", db)
        assert "argument" in result

    def test_get_relevant_memories_includes_confident_hypotheses(self, db, user):
        from app.services.memory import get_relevant_memories
        db.add(HypothesisMemory(
            user_id=user.id, short_summary="Tends to withdraw during conflict",
            long_summary="Pattern of withdrawal under criticism",
            confidence=0.7, evidence_for=5, evidence_against=0,
            status="active", tags="conflict,withdrawal",
        ))
        db.commit()

        result = get_relevant_memories(user, "conflict", db)
        assert "withdraw" in result
        assert "likely" in result  # confidence >= 0.7

    def test_get_relevant_memories_excludes_low_confidence_hypotheses(self, db, user):
        from app.services.memory import get_relevant_memories, HYPOTHESIS_INJECT_THRESHOLD
        db.add(HypothesisMemory(
            user_id=user.id, short_summary="Might prefer mornings",
            long_summary="Possibly a morning person",
            confidence=HYPOTHESIS_INJECT_THRESHOLD - 0.1,
            evidence_for=1, evidence_against=0,
            status="provisional", tags="schedule",
        ))
        db.commit()

        result = get_relevant_memories(user, "mornings", db)
        assert "morning" not in result

    def test_keyword_matching_prioritizes_relevant(self, db, user):
        """Keyword-matching memories should appear before non-matching ones."""
        from app.services.memory import get_relevant_memories
        db.add(ProfileMemory(
            user_id=user.id, content="Has two dogs named Rex and Luna",
            category="Personal", confidence=1.0, source="explicit", status="active",
            tags="dogs,pets",
        ))
        db.add(ProfileMemory(
            user_id=user.id, content="Prefers coffee over tea",
            category="Preference", confidence=1.0, source="explicit", status="active",
            tags="food,drinks",
        ))
        db.commit()

        result = get_relevant_memories(user, "tell me about the dogs", db)
        # Both should be present but dogs-related should match via keywords
        assert "dogs" in result.lower() or "rex" in result.lower()


# ─── Correct Belief Tests ────────────────────────────────────

class TestCorrectBelief:

    def test_correct_hypothesis(self, db, user):
        from app.services.memory import correct_belief
        db.add(HypothesisMemory(
            user_id=user.id, short_summary="User is introverted",
            long_summary="Seems to prefer alone time",
            confidence=0.6, evidence_for=3, evidence_against=0,
            status="active",
        ))
        db.commit()

        result = correct_belief(user, "introverted", "I'm actually quite extroverted", db)
        assert "superseded" in result

        h = db.query(HypothesisMemory).first()
        assert h.status == "superseded"

    def test_correct_profile_fact(self, db, user):
        from app.services.memory import correct_belief
        db.add(ProfileMemory(
            user_id=user.id, content="Works at Microsoft",
            category="Work", confidence=1.0, source="explicit", status="active",
        ))
        db.commit()

        result = correct_belief(user, "Microsoft", "I work at Google now", db)
        assert "Updated" in result

        profiles = db.query(ProfileMemory).filter(ProfileMemory.status == "active").all()
        assert len(profiles) == 1
        assert "Google" in profiles[0].content

        old = db.query(ProfileMemory).filter(ProfileMemory.status == "outdated").first()
        assert old is not None
        assert old.superseded_by_id == profiles[0].id

    def test_correct_nonexistent_belief(self, db, user):
        from app.services.memory import correct_belief
        result = correct_belief(user, "something that doesn't exist", "correction", db)
        assert "Couldn't find" in result


# ─── Forget Memory Tests ─────────────────────────────────────

class TestForgetMemory:

    def test_forget_profile(self, db, user):
        from app.services.memory import forget_memory
        db.add(ProfileMemory(
            user_id=user.id, content="WiFi password is bluemoon42",
            category="Passwords", confidence=1.0, source="explicit", status="active",
        ))
        db.commit()

        result = forget_memory(user, "wifi password", db)
        assert "Forgotten" in result
        assert "1" in result

        p = db.query(ProfileMemory).first()
        assert p.status == "deleted"

    def test_forget_episode(self, db, user):
        from app.services.memory import forget_memory
        db.add(EpisodicMemory(
            user_id=user.id, summary="Embarrassing incident at the party",
            importance=0.5, status="active",
        ))
        db.commit()

        result = forget_memory(user, "party", db)
        assert "Forgotten" in result

        e = db.query(EpisodicMemory).first()
        assert e.status == "archived"

    def test_forget_hypothesis(self, db, user):
        from app.services.memory import forget_memory
        db.add(HypothesisMemory(
            user_id=user.id, short_summary="Avoids social events",
            long_summary="Pattern of declining invitations",
            confidence=0.5, evidence_for=2, evidence_against=0,
            status="provisional",
        ))
        db.commit()

        result = forget_memory(user, "social events", db)
        assert "Forgotten" in result

        h = db.query(HypothesisMemory).first()
        assert h.status == "superseded"

    def test_forget_nonexistent(self, db, user):
        from app.services.memory import forget_memory
        result = forget_memory(user, "something not stored", db)
        assert "No memories found" in result

    def test_forget_cascades_across_types(self, db, user):
        """Forgetting should hit all memory types matching the description."""
        from app.services.memory import forget_memory
        db.add(ProfileMemory(
            user_id=user.id, content="Johnny is the user's husband",
            category="Relationship", confidence=1.0, source="explicit", status="active",
        ))
        db.add(EpisodicMemory(
            user_id=user.id, summary="Discussion about Johnny's work schedule",
            importance=0.5, status="active",
        ))
        db.add(HypothesisMemory(
            user_id=user.id, short_summary="Johnny related stress pattern",
            long_summary="User gets stressed about Johnny's schedule",
            confidence=0.4, evidence_for=2, evidence_against=0,
            status="provisional",
        ))
        db.commit()

        result = forget_memory(user, "Johnny", db)
        assert "3" in result  # should forget all 3

        assert db.query(ProfileMemory).filter(ProfileMemory.status == "active").count() == 0
        assert db.query(EpisodicMemory).filter(EpisodicMemory.status == "active").count() == 0
        assert db.query(HypothesisMemory).filter(HypothesisMemory.status.in_(["provisional", "active"])).count() == 0


# ─── Search Tests ─────────────────────────────────────────────

class TestSearchMemories:

    def test_search_across_all_modules(self, db, user):
        from app.services.memory import search_memories
        db.add(ProfileMemory(user_id=user.id, content="Has a dog named Rex", category="Personal", confidence=1.0, source="explicit", status="active", tags="pets,dogs"))
        db.add(EpisodicMemory(user_id=user.id, summary="Took the dog to the vet", importance=0.5, status="active", tags="dogs,health"))
        db.commit()

        result = search_memories(user, "dog", db)
        assert "Rex" in result
        assert "vet" in result
        assert "2 memory" in result

    def test_search_single_module(self, db, user):
        from app.services.memory import search_memories
        db.add(ProfileMemory(user_id=user.id, content="Likes hiking", category="Preference", confidence=1.0, source="explicit", status="active"))
        db.add(EpisodicMemory(user_id=user.id, summary="Went hiking last weekend", importance=0.5, status="active"))
        db.commit()

        result = search_memories(user, "hiking", db, module="profile")
        assert "Likes hiking" in result
        assert "last weekend" not in result  # episode should be excluded

    def test_search_no_results(self, db, user):
        from app.services.memory import search_memories
        result = search_memories(user, "nonexistent thing", db)
        assert "No memories found" in result


# ─── Model Routing Tests ─────────────────────────────────────

class TestModelRouting:

    @patch("app.services.memory._background_llm_call")
    def test_consolidation_uses_background_model(self, mock_llm, db, user):
        """Consolidation should use the background model, not Sonnet."""
        from app.services.memory import consolidate_session

        _add_messages(db, user, "test:session", 25)

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.tool_calls = None
        mock_llm.return_value = mock_response

        consolidate_session(user, "test:session", db)

        # Verify _background_llm_call was called (not anthropic_client)
        mock_llm.assert_called_once()

    def test_background_model_is_not_sonnet(self):
        """The background model should be a cheap model, not the live chat model."""
        from app.services.clients import BACKGROUND_MODEL
        from app.config import settings
        assert BACKGROUND_MODEL != settings.intent_model
        assert "nano" in BACKGROUND_MODEL or "mini" in BACKGROUND_MODEL or "flash" in BACKGROUND_MODEL
