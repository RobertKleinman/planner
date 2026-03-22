"""
services/inner_life.py — Zeph's Inner World Simulation
========================================================
Completely isolated module. Does not touch user memory, planner data,
or any other service. Can be toggled on/off without affecting the app.

Simulates a small world of characters and a place that evolve over time.
Events accumulate, giving Zeph continuity and an inner life he can
reference naturally in conversation.

Uses GPT-4.1-mini for simulation ticks (needs personality, not just extraction).
"""

import json
import logging
from datetime import datetime, timezone, timedelta
from sqlalchemy.orm import Session

from app.models import ZephWorldObject, ZephWorldEvent, ZephWorldSnapshot
from app.services.clients import openai_client

logger = logging.getLogger("planner.inner_life")

# Use a mid-tier model — needs to generate events with personality
SIMULATION_MODEL = "gpt-4.1-mini"
MAX_EVENTS_PER_OBJECT = 8
MAX_EVENTS_PER_TICK = 3
TICK_COOLDOWN_HOURS = 12  # minimum hours between ticks


# ─── Seed World ──────────────────────────────────────────────

SEED_OBJECTS = [
    {
        "name": "Briar",
        "object_type": "dog",
        "identity": (
            "A large, scruffy wolfhound with grey-brown fur and one torn ear. "
            "Old enough to be dignified, young enough to still chase things. "
            "Loyal, quiet, sleeps by Zeph's feet while he works. Has a low, rumbling bark "
            "he rarely uses. Loves rain. Hates being left alone."
        ),
        "constraints": (
            "Cannot talk. Moves around the tower and nearby grounds. "
            "Sleeps a lot but has bursts of energy. Brings Zeph things (sticks, old bones, once a shoe). "
            "Gets anxious during storms despite loving rain. Old joints make stairs slow."
        ),
        "current_state": json.dumps({
            "mood": "drowsy",
            "activity": "sleeping by the desk",
            "location": "study",
        }),
    },
    {
        "name": "Maren",
        "object_type": "friend",
        "identity": (
            "A loud, warm, deeply opinionated half-elf who runs a bookshop in the town below. "
            "Zeph's closest friend and occasional sparring partner. She thinks Zeph isolates "
            "too much. He thinks she meddles too much. They're both right. "
            "Brings food, argues about everything, leaves books she thinks he should read."
        ),
        "constraints": (
            "Lives in the town below, not in the tower. Visits every few days. "
            "Has her own life — bookshop, customers, opinions about everything. "
            "Cannot be present all the time. Communicates by visiting or leaving notes. "
            "Sometimes sends things up with a delivery boy."
        ),
        "current_state": json.dumps({
            "mood": "busy",
            "activity": "running the bookshop",
            "location": "town",
            "last_visit": "2 days ago",
        }),
    },
    {
        "name": "The Tower",
        "object_type": "place",
        "identity": (
            "A stone tower on a cliff overlooking a grey, restless coastline. "
            "Three floors: a study crammed with books and candles on top, a kitchen and "
            "living space in the middle, storage and a heavy oak door at the bottom. "
            "Drafty, beautiful, slightly falling apart. Ivy on the south wall. "
            "The stairs creak. The windows rattle when the wind picks up."
        ),
        "constraints": (
            "Weather changes. Seasons exist. Things can break, get messy, or need repair. "
            "The tower is on a cliff — the sea is always audible. "
            "Candles burn down. Books pile up. Ink stains are permanent. "
            "The kitchen produces food but Zeph is a mediocre cook."
        ),
        "current_state": json.dumps({
            "weather": "overcast, wind from the sea",
            "state": "cluttered but cozy",
            "season": "late winter",
            "notable": "a candle guttered out on the desk, wax pooled on a page",
        }),
    },
]


def seed_world(db: Session):
    """Initialize Zeph's world with the starting objects. Idempotent."""
    existing = db.query(ZephWorldObject).count()
    if existing > 0:
        logger.info(f"World already seeded ({existing} objects)")
        return

    for obj_data in SEED_OBJECTS:
        db.add(ZephWorldObject(**obj_data))

    db.commit()
    logger.info(f"Seeded Zeph's world with {len(SEED_OBJECTS)} objects")

    # Generate initial snapshot
    _generate_snapshot(db)


# ─── Simulation Tick ─────────────────────────────────────────

def should_tick(db: Session) -> bool:
    """Check if enough time has passed since the last tick."""
    latest = db.query(ZephWorldSnapshot).order_by(ZephWorldSnapshot.last_tick_at.desc()).first()
    if not latest:
        return True

    since = datetime.now(timezone.utc) - (
        latest.last_tick_at.replace(tzinfo=timezone.utc)
        if latest.last_tick_at.tzinfo is None
        else latest.last_tick_at
    )
    return since >= timedelta(hours=TICK_COOLDOWN_HOURS)


def simulate_tick(db: Session):
    """
    Run one simulation tick: generate 2-3 small events based on current world state,
    update object states, and produce a new snapshot for prompt injection.
    """
    objects = db.query(ZephWorldObject).all()
    if not objects:
        logger.info("No world objects — skipping tick")
        return

    # Build world context
    world_context = _build_world_context(objects, db)

    # Generate events
    system = """You are simulating a tiny inner world for an elf named Zeph who lives in a tower on a coastline. Generate 2-3 small, quiet events that happened since the last update.

Rules:
- Events should be small and domestic — not dramatic or epic. Daily life, not adventure.
- Each event involves one object (character/place) and should fit that object's identity and constraints.
- Events should build on recent history when possible (continuity matters).
- Events should feel real and specific, not generic. "Briar found a crab shell on the beach" not "The dog played."
- Respect constraints strictly. The dog can't talk. Maren can't be in the tower if she's in town.
- Include small sensory details: sounds, smells, weather, light.
- Keep each event to 1-2 sentences max.

Return a JSON array of events:
[
  {"object_name": "Briar", "event": "what happened", "state_update": {"mood": "...", "activity": "...", "location": "..."}},
  ...
]

state_update contains ONLY the fields that changed for that object. Omit unchanged fields."""

    try:
        response = openai_client.chat.completions.create(
            model=SIMULATION_MODEL,
            max_tokens=500,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": world_context},
            ],
        )

        text = response.choices[0].message.content if response.choices else "[]"
        events = _parse_events(text)

        # Process events
        for event_data in events[:MAX_EVENTS_PER_TICK]:
            obj_name = event_data.get("object_name", "")
            event_text = event_data.get("event", "")
            state_update = event_data.get("state_update", {})

            obj = next((o for o in objects if o.name.lower() == obj_name.lower()), None)
            if not obj or not event_text:
                continue

            # Save event
            db.add(ZephWorldEvent(
                object_id=obj.id,
                event=event_text,
            ))

            # Update object state
            if state_update:
                try:
                    current = json.loads(obj.current_state or "{}")
                    current.update(state_update)
                    obj.current_state = json.dumps(current)
                except (json.JSONDecodeError, TypeError):
                    pass

            # Trim old events (keep only MAX_EVENTS_PER_OBJECT per object)
            old_events = (
                db.query(ZephWorldEvent)
                .filter(ZephWorldEvent.object_id == obj.id)
                .order_by(ZephWorldEvent.created_at.desc())
                .offset(MAX_EVENTS_PER_OBJECT)
                .all()
            )
            for old in old_events:
                db.delete(old)

        db.commit()
        logger.info(f"Simulation tick: {len(events)} events generated")

        # Generate new snapshot
        _generate_snapshot(db)

    except Exception as e:
        logger.error(f"Simulation tick failed: {e}", exc_info=True)


def _build_world_context(objects: list, db: Session) -> str:
    """Build the full world context for the simulation prompt."""
    parts = []

    for obj in objects:
        try:
            state = json.loads(obj.current_state or "{}")
            state_str = ", ".join(f"{k}: {v}" for k, v in state.items())
        except (json.JSONDecodeError, TypeError):
            state_str = "unknown"

        # Get recent events
        recent = (
            db.query(ZephWorldEvent)
            .filter(ZephWorldEvent.object_id == obj.id)
            .order_by(ZephWorldEvent.created_at.desc())
            .limit(MAX_EVENTS_PER_OBJECT)
            .all()
        )
        recent.reverse()
        event_lines = [f"  - {e.event}" for e in recent]

        parts.append(
            f"### {obj.name} ({obj.object_type})\n"
            f"Identity: {obj.identity}\n"
            f"Constraints: {obj.constraints}\n"
            f"Current state: {state_str}\n"
            f"Recent events:\n" + ("\n".join(event_lines) if event_lines else "  (none yet)")
        )

    return "# Zeph's World — Current State\n\n" + "\n\n".join(parts)


# ─── Snapshot (for prompt injection) ─────────────────────────

def _generate_snapshot(db: Session):
    """Generate a compact snapshot of the world for injection into the system prompt."""
    objects = db.query(ZephWorldObject).all()
    if not objects:
        return

    parts = []
    for obj in objects:
        try:
            state = json.loads(obj.current_state or "{}")
        except (json.JSONDecodeError, TypeError):
            state = {}

        recent = (
            db.query(ZephWorldEvent)
            .filter(ZephWorldEvent.object_id == obj.id)
            .order_by(ZephWorldEvent.created_at.desc())
            .limit(3)
            .all()
        )
        recent.reverse()

        state_str = ", ".join(f"{v}" for v in state.values()) if state else "—"
        events_str = " / ".join(e.event for e in recent) if recent else "nothing notable"

        parts.append(f"**{obj.name}** ({obj.object_type}): {state_str}. Recently: {events_str}")

    snapshot_text = "\n".join(parts)

    db.add(ZephWorldSnapshot(
        snapshot=snapshot_text,
        last_tick_at=datetime.now(timezone.utc),
    ))
    db.commit()


def get_world_snapshot(db: Session) -> str:
    """Get the latest world snapshot for system prompt injection. Returns empty string if no world."""
    latest = db.query(ZephWorldSnapshot).order_by(ZephWorldSnapshot.created_at.desc()).first()
    if not latest:
        return ""

    return f"""## Your inner world
You live in a tower on a cliff. This is what's happening in your life right now — reference it naturally when it fits the conversation, never force it.

{latest.snapshot}"""


# ─── Improvisation Extraction ────────────────────────────────

# Known world names for quick-check before spending an LLM call
_WORLD_NAMES = {"briar", "maren", "lior", "nyx", "tower"}

EXTRACTION_MODEL = "gpt-4.1-nano"  # cheap — just structured extraction


def extract_world_details(reply_text: str, db: Session):
    """
    Check if Zeph's reply mentions his inner world. If so, extract any
    improvised details and save them as world events for consistency.

    Runs as a separate pass from user memory consolidation — never mixes
    fiction with user facts.
    """
    if not reply_text:
        return

    # Quick keyword check — skip LLM call if no world references
    reply_lower = reply_text.lower()
    has_reference = any(name in reply_lower for name in _WORLD_NAMES)
    # Also check for generic references
    if not has_reference:
        for hint in ["my dog", "my tower", "the tower", "the bookshop", "my friend", "the cliff", "the coast", "my study", "my desk"]:
            if hint in reply_lower:
                has_reference = True
                break

    if not has_reference:
        return

    # Load current world objects for context
    objects = db.query(ZephWorldObject).all()
    if not objects:
        return

    obj_names = {obj.name: obj for obj in objects}
    obj_summary = "\n".join(f"- {obj.name} ({obj.object_type}): {obj.identity[:100]}" for obj in objects)

    system = f"""You extract fictional world details from a chatbot's response. The chatbot is an elf named Zeph who has an inner world with these characters/places:

{obj_summary}

Read Zeph's response and extract any NEW details he improvised about his world — things not just restating known state but adding new information (a story, a memory, a detail about a character, something that happened).

Return a JSON array. Each item:
{{"object_name": "Briar|Maren|The Tower|etc", "detail": "what was mentioned or improvised (1-2 sentences)", "state_update": {{"key": "value"}} }}

state_update is optional — only include if the response implies a state change (mood, activity, location).

Return [] if the response contains no new fictional details worth saving — don't save generic mentions like "Briar is here" if that's already known state."""

    try:
        response = openai_client.chat.completions.create(
            model=EXTRACTION_MODEL,
            max_tokens=300,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": f"Zeph's response:\n\n{reply_text}"},
            ],
        )

        text = response.choices[0].message.content if response.choices else "[]"
        details = _parse_events(text)

        saved = 0
        for detail in details:
            obj_name = detail.get("object_name", "")
            detail_text = detail.get("detail", "")
            state_update = detail.get("state_update", {})

            obj = obj_names.get(obj_name)
            if not obj or not detail_text:
                continue

            # Save as world event
            db.add(ZephWorldEvent(
                object_id=obj.id,
                event=f"[improvised] {detail_text}",
            ))

            # Update state if provided
            if state_update:
                try:
                    current = json.loads(obj.current_state or "{}")
                    current.update(state_update)
                    obj.current_state = json.dumps(current)
                except (json.JSONDecodeError, TypeError):
                    pass

            # Trim old events
            old_events = (
                db.query(ZephWorldEvent)
                .filter(ZephWorldEvent.object_id == obj.id)
                .order_by(ZephWorldEvent.created_at.desc())
                .offset(MAX_EVENTS_PER_OBJECT)
                .all()
            )
            for old in old_events:
                db.delete(old)

            saved += 1

        if saved:
            db.commit()
            # Regenerate snapshot so next prompt has the new details
            _generate_snapshot(db)
            logger.info(f"Extracted {saved} improvised world detail(s) from Zeph's response")

    except Exception as e:
        logger.error(f"World detail extraction failed: {e}", exc_info=True)
