"""
tests/test_inner_life.py — Tests for Zeph's inner world simulation
====================================================================
Tests: seeding, simulation tick, snapshot generation, world detail
extraction from Zeph's responses, event trimming, and isolation from
user memory.
"""

import json
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import (
    User, ZephWorldObject, ZephWorldEvent, ZephWorldSnapshot,
    ProfileMemory, EpisodicMemory, HypothesisMemory,
)


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    user = User(id=1, email="test@test.com", name="Test", api_key_hash="fake")
    session.add(user)
    session.commit()

    yield session
    session.close()


@pytest.fixture
def user(db):
    return db.query(User).first()


@pytest.fixture
def seeded_world(db):
    """Seed the world and return the db session."""
    from app.services.inner_life import seed_world
    seed_world(db)
    return db


# ─── Seeding Tests ───────────────────────────────────────────

class TestSeeding:

    def test_seed_creates_objects(self, db):
        from app.services.inner_life import seed_world
        seed_world(db)

        objects = db.query(ZephWorldObject).all()
        assert len(objects) == 3

        names = {o.name for o in objects}
        assert "Briar" in names
        assert "Maren" in names
        assert "The Tower" in names

    def test_seed_creates_initial_snapshot(self, db):
        from app.services.inner_life import seed_world
        seed_world(db)

        snapshots = db.query(ZephWorldSnapshot).all()
        assert len(snapshots) == 1
        assert "Briar" in snapshots[0].snapshot

    def test_seed_is_idempotent(self, db):
        from app.services.inner_life import seed_world
        seed_world(db)
        seed_world(db)  # second call should be no-op

        objects = db.query(ZephWorldObject).all()
        assert len(objects) == 3

    def test_objects_have_valid_state(self, seeded_world):
        objects = seeded_world.query(ZephWorldObject).all()
        for obj in objects:
            state = json.loads(obj.current_state)
            assert isinstance(state, dict)
            assert len(state) > 0

    def test_objects_have_constraints(self, seeded_world):
        briar = seeded_world.query(ZephWorldObject).filter(ZephWorldObject.name == "Briar").first()
        assert "cannot talk" in briar.constraints.lower()
        assert briar.object_type == "dog"


# ─── Tick Tests ──────────────────────────────────────────────

class TestSimulationTick:

    def test_should_tick_true_with_no_snapshots(self, seeded_world):
        """First tick after seeding should always run (seed creates a snapshot, but tick checks last_tick_at)."""
        from app.services.inner_life import should_tick
        # should_tick checks time since last tick — seed just created one
        # so it should be False immediately after seeding
        result = should_tick(seeded_world)
        assert result is False  # just seeded, cooldown not elapsed

    def test_should_tick_true_after_cooldown(self, seeded_world):
        from app.services.inner_life import should_tick, TICK_COOLDOWN_HOURS

        # Backdate the snapshot
        snapshot = seeded_world.query(ZephWorldSnapshot).first()
        snapshot.last_tick_at = datetime.now(timezone.utc) - timedelta(hours=TICK_COOLDOWN_HOURS + 1)
        seeded_world.commit()

        assert should_tick(seeded_world) is True

    @patch("app.services.inner_life.openai_client")
    def test_tick_generates_events(self, mock_openai, seeded_world):
        from app.services.inner_life import simulate_tick

        # Mock LLM response with events
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = json.dumps([
            {
                "object_name": "Briar",
                "event": "Found a piece of driftwood on the beach and carried it up the stairs slowly.",
                "state_update": {"mood": "pleased", "activity": "chewing driftwood"}
            },
            {
                "object_name": "The Tower",
                "event": "The south window rattled loose in the wind. A draft crept through the study.",
                "state_update": {"notable": "south window rattling"}
            }
        ])
        mock_openai.chat.completions.create.return_value = mock_response

        simulate_tick(seeded_world)

        events = seeded_world.query(ZephWorldEvent).all()
        assert len(events) == 2
        assert any("driftwood" in e.event for e in events)

        # Check state was updated
        briar = seeded_world.query(ZephWorldObject).filter(ZephWorldObject.name == "Briar").first()
        state = json.loads(briar.current_state)
        assert state["mood"] == "pleased"

    @patch("app.services.inner_life.openai_client")
    def test_tick_trims_old_events(self, mock_openai, seeded_world):
        from app.services.inner_life import simulate_tick, MAX_EVENTS_PER_OBJECT

        briar = seeded_world.query(ZephWorldObject).filter(ZephWorldObject.name == "Briar").first()

        # Pre-fill with max events
        for i in range(MAX_EVENTS_PER_OBJECT):
            seeded_world.add(ZephWorldEvent(object_id=briar.id, event=f"Old event {i}"))
        seeded_world.commit()

        # Tick adds new events
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = json.dumps([
            {"object_name": "Briar", "event": "New event after trim.", "state_update": {}}
        ])
        mock_openai.chat.completions.create.return_value = mock_response

        simulate_tick(seeded_world)

        briar_events = seeded_world.query(ZephWorldEvent).filter(
            ZephWorldEvent.object_id == briar.id
        ).count()
        assert briar_events <= MAX_EVENTS_PER_OBJECT

    @patch("app.services.inner_life.openai_client")
    def test_tick_generates_new_snapshot(self, mock_openai, seeded_world):
        from app.services.inner_life import simulate_tick

        initial_count = seeded_world.query(ZephWorldSnapshot).count()

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "[]"
        mock_openai.chat.completions.create.return_value = mock_response

        simulate_tick(seeded_world)

        new_count = seeded_world.query(ZephWorldSnapshot).count()
        assert new_count > initial_count


# ─── Extraction Tests ────────────────────────────────────────

class TestWorldDetailExtraction:

    @patch("app.services.inner_life.openai_client")
    def test_extraction_saves_improvised_details(self, mock_openai, seeded_world):
        from app.services.inner_life import extract_world_details

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = json.dumps([
            {
                "object_name": "Briar",
                "detail": "Zeph found Briar as a pup on the beach during a storm, shivering under a fishing boat.",
                "state_update": {}
            }
        ])
        mock_openai.chat.completions.create.return_value = mock_response

        extract_world_details(
            "I found Briar years ago — he was just a pup, shivering under a fishing boat during a storm on the beach below.",
            seeded_world,
        )

        events = seeded_world.query(ZephWorldEvent).filter(
            ZephWorldEvent.event.like("%improvised%")
        ).all()
        assert len(events) == 1
        assert "fishing boat" in events[0].event

    def test_extraction_skips_when_no_world_references(self, seeded_world):
        """Should not make an LLM call when reply has no world references."""
        from app.services.inner_life import extract_world_details

        # This should return immediately without any LLM call
        with patch("app.services.inner_life.openai_client") as mock_openai:
            extract_world_details(
                "Sure, I'll set that reminder for tomorrow at 9am.",
                seeded_world,
            )
            mock_openai.chat.completions.create.assert_not_called()

    @patch("app.services.inner_life.openai_client")
    def test_extraction_detects_generic_references(self, mock_openai, seeded_world):
        """Should detect generic references like 'my dog' not just 'Briar'."""
        from app.services.inner_life import extract_world_details

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "[]"
        mock_openai.chat.completions.create.return_value = mock_response

        extract_world_details(
            "My dog is being ridiculous today — won't stop barking at the seagulls.",
            seeded_world,
        )

        # Should have made the LLM call (detected "my dog")
        mock_openai.chat.completions.create.assert_called_once()

    @patch("app.services.inner_life.openai_client")
    def test_extraction_updates_state(self, mock_openai, seeded_world):
        from app.services.inner_life import extract_world_details

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = json.dumps([
            {
                "object_name": "Maren",
                "detail": "Maren is traveling to the northern markets and won't be back for a week.",
                "state_update": {"location": "northern markets", "mood": "adventurous"}
            }
        ])
        mock_openai.chat.completions.create.return_value = mock_response

        extract_world_details(
            "Maren left for the northern markets yesterday — she won't be back for a week.",
            seeded_world,
        )

        maren = seeded_world.query(ZephWorldObject).filter(ZephWorldObject.name == "Maren").first()
        state = json.loads(maren.current_state)
        assert state["location"] == "northern markets"


# ─── Snapshot Tests ──────────────────────────────────────────

class TestSnapshot:

    def test_get_world_snapshot_returns_formatted_text(self, seeded_world):
        from app.services.inner_life import get_world_snapshot

        snapshot = get_world_snapshot(seeded_world)
        assert "inner world" in snapshot.lower()
        assert "Briar" in snapshot
        assert "Maren" in snapshot
        assert "Tower" in snapshot

    def test_get_world_snapshot_empty_without_world(self, db):
        from app.services.inner_life import get_world_snapshot, _snapshot_cache

        # Clear cache from previous tests
        _snapshot_cache["text"] = None
        _snapshot_cache["fetched_at"] = None

        snapshot = get_world_snapshot(db)
        assert snapshot == ""


# ─── Isolation Tests ─────────────────────────────────────────

class TestIsolation:

    @patch("app.services.inner_life.openai_client")
    def test_world_events_dont_create_user_memories(self, mock_openai, seeded_world, user):
        """Inner life events must never create ProfileMemory, EpisodicMemory, or HypothesisMemory."""
        from app.services.inner_life import simulate_tick

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = json.dumps([
            {"object_name": "Briar", "event": "Briar dug up an old bone in the garden.", "state_update": {}}
        ])
        mock_openai.chat.completions.create.return_value = mock_response

        simulate_tick(seeded_world)

        # Verify no user memories were created
        assert seeded_world.query(ProfileMemory).count() == 0
        assert seeded_world.query(EpisodicMemory).count() == 0
        assert seeded_world.query(HypothesisMemory).count() == 0

        # But world events were created
        assert seeded_world.query(ZephWorldEvent).count() > 0

    @patch("app.services.inner_life.openai_client")
    def test_extraction_doesnt_create_user_memories(self, mock_openai, seeded_world, user):
        """Extraction of improvised details must stay in world tables only."""
        from app.services.inner_life import extract_world_details

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = json.dumps([
            {"object_name": "Briar", "detail": "Briar once swam out to a fishing boat.", "state_update": {}}
        ])
        mock_openai.chat.completions.create.return_value = mock_response

        extract_world_details("Briar once swam out to a fishing boat and nearly gave me a heart attack.", seeded_world)

        assert seeded_world.query(ProfileMemory).count() == 0
        assert seeded_world.query(EpisodicMemory).count() == 0
        assert seeded_world.query(HypothesisMemory).count() == 0
