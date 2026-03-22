"""
services/memory.py — Layered Memory System
=============================================
Handles consolidation (delta-based extraction), retrieval (keyword+ranking),
session summaries (medium-term context), compaction, and decay.

Uses GPT-4.1-nano for all background tasks (consolidation, compaction, summaries).
Live conversation stays on Claude Sonnet via assistant.py.

Modules:
  - ProfileMemory: stable facts (user-stated or inferred)
  - EpisodicMemory: notable events or recurring situations
  - HypothesisMemory: inferred patterns with confidence tracking
  - SessionSummary: compressed conversation windows
"""

import json
import re
import logging
from datetime import datetime, timezone, timedelta
from sqlalchemy.orm import Session
from sqlalchemy import or_, func, text

from app.models import (
    User, ProfileMemory, EpisodicMemory, HypothesisMemory,
    ConversationMessage, SessionSummary, ConsolidationCheckpoint,
)
from app.services.clients import openai_client, BACKGROUND_MODEL
from app.config import settings

logger = logging.getLogger("planner.memory")

# ─── Constants ───────────────────────────────────────────────

MAX_PROFILE_INJECT = 50
MAX_EPISODE_INJECT = 15
MAX_HYPOTHESIS_INJECT = 8
HYPOTHESIS_INJECT_THRESHOLD = 0.4

CONSOLIDATION_MESSAGE_THRESHOLD = 20   # consolidate every N new messages
CONSOLIDATION_MAX_INPUT_MESSAGES = 100  # hard cap on messages sent to LLM
SUMMARY_MESSAGE_INTERVAL = 20          # generate session summary every N messages
COMPACTION_THRESHOLD = 30
MAX_HISTORY = 15                        # raw messages to load (with summary)
MAX_HISTORY_NO_SUMMARY = 50             # raw messages to load (without summary)


# ─── Background LLM Call ─────────────────────────────────────

def _background_llm_call(system: str, user_content: str, tools: list = None, max_tokens: int = 2048) -> dict:
    """Make an LLM call using the cheap background model (GPT-4.1-nano via OpenAI)."""
    messages = [{"role": "user", "content": user_content}]

    kwargs = {
        "model": BACKGROUND_MODEL,
        "max_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user_content},
        ],
    }

    if tools:
        # Convert Anthropic tool format to OpenAI function format
        openai_tools = []
        for t in tools:
            openai_tools.append({
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t.get("description", ""),
                    "parameters": t.get("input_schema", {}),
                },
            })
        kwargs["tools"] = openai_tools

    response = openai_client.chat.completions.create(**kwargs)
    return response


# ─── Retrieval ───────────────────────────────────────────────

def get_relevant_memories(user: User, message_text: str, db: Session) -> str:
    """Retrieve and format relevant memories for system prompt injection."""
    sections = []

    profiles = _get_profile_memories(user, message_text, db)
    if profiles:
        lines = [f"- [{p.category}] {p.content}" for p in profiles]
        sections.append("### Known facts about you\n" + "\n".join(lines))

    episodes = _get_episodic_memories(user, message_text, db)
    if episodes:
        lines = []
        for e in episodes:
            time_str = ""
            if e.time_start:
                time_str = f" ({e.time_start.strftime('%b %Y')})"
            lines.append(f"- {e.summary}{time_str}")
        sections.append("### Notable events/situations\n" + "\n".join(lines))

    hypotheses = _get_hypothesis_memories(user, message_text, db)
    if hypotheses:
        lines = []
        for h in hypotheses:
            conf_label = "likely" if h.confidence >= 0.7 else "possible"
            lines.append(f"- [{conf_label}] {h.short_summary}")
        sections.append(
            "### Patterns I've noticed (use carefully — these are inferences, not facts)\n"
            + "\n".join(lines)
        )

    if not sections:
        return ""

    return "## Your Memory\n" + "\n\n".join(sections)


def get_session_context(user: User, session_id: str, db: Session) -> tuple:
    """
    Get conversation context: session summary + recent raw messages.
    Returns (history_messages: list, summary_text: str or None).
    """
    # Check for a session summary
    latest_summary = (
        db.query(SessionSummary)
        .filter(
            SessionSummary.user_id == user.id,
            SessionSummary.session_id == session_id,
        )
        .order_by(SessionSummary.created_at.desc())
        .first()
    )

    if latest_summary:
        # Load only recent messages after the summary
        limit = MAX_HISTORY
        messages = (
            db.query(ConversationMessage)
            .filter(
                ConversationMessage.user_id == user.id,
                ConversationMessage.session_id == session_id,
            )
            .order_by(ConversationMessage.created_at.desc())
            .limit(limit)
            .all()
        )
        messages.reverse()
    else:
        # No summary yet — load more raw messages
        limit = MAX_HISTORY_NO_SUMMARY
        messages = (
            db.query(ConversationMessage)
            .filter(
                ConversationMessage.user_id == user.id,
                ConversationMessage.session_id == session_id,
            )
            .order_by(ConversationMessage.created_at.desc())
            .limit(limit)
            .all()
        )
        messages.reverse()

    history = []
    for msg in messages:
        try:
            content = json.loads(msg.content)
        except (json.JSONDecodeError, TypeError):
            content = msg.content
        history.append({"role": msg.role, "content": content})

    return history, latest_summary.summary if latest_summary else None


def _get_profile_memories(user: User, message_text: str, db: Session) -> list:
    query = db.query(ProfileMemory).filter(
        ProfileMemory.user_id == user.id,
        ProfileMemory.status == "active",
    )
    if message_text:
        keywords = _extract_keywords(message_text)
        if keywords:
            pattern_filters = []
            for kw in keywords[:5]:
                pat = f"%{kw}%"
                pattern_filters.append(ProfileMemory.content.ilike(pat))
                pattern_filters.append(ProfileMemory.tags.ilike(pat))
            matching = query.filter(or_(*pattern_filters)).limit(MAX_PROFILE_INJECT).all()
            matching_ids = {m.id for m in matching}
            remaining = MAX_PROFILE_INJECT - len(matching)
            if remaining > 0:
                others = query.filter(
                    ~ProfileMemory.id.in_(matching_ids)
                ).order_by(ProfileMemory.created_at.desc()).limit(remaining).all()
                return matching + others
            return matching
    return query.order_by(ProfileMemory.created_at.desc()).limit(MAX_PROFILE_INJECT).all()


def _get_episodic_memories(user: User, message_text: str, db: Session) -> list:
    query = db.query(EpisodicMemory).filter(
        EpisodicMemory.user_id == user.id,
        EpisodicMemory.status == "active",
    )
    if message_text:
        keywords = _extract_keywords(message_text)
        if keywords:
            pattern_filters = []
            for kw in keywords[:5]:
                pat = f"%{kw}%"
                pattern_filters.append(EpisodicMemory.summary.ilike(pat))
                pattern_filters.append(EpisodicMemory.tags.ilike(pat))
            matching = query.filter(or_(*pattern_filters)).limit(MAX_EPISODE_INJECT).all()
            if matching:
                return matching
    return query.order_by(
        EpisodicMemory.importance.desc(),
        EpisodicMemory.updated_at.desc(),
    ).limit(MAX_EPISODE_INJECT).all()


def _get_hypothesis_memories(user: User, message_text: str, db: Session) -> list:
    query = db.query(HypothesisMemory).filter(
        HypothesisMemory.user_id == user.id,
        HypothesisMemory.status.in_(["provisional", "active"]),
        HypothesisMemory.confidence >= HYPOTHESIS_INJECT_THRESHOLD,
    )
    if message_text:
        keywords = _extract_keywords(message_text)
        if keywords:
            pattern_filters = []
            for kw in keywords[:5]:
                pat = f"%{kw}%"
                pattern_filters.append(HypothesisMemory.short_summary.ilike(pat))
                pattern_filters.append(HypothesisMemory.tags.ilike(pat))
            matching = query.filter(or_(*pattern_filters)).limit(MAX_HYPOTHESIS_INJECT).all()
            if matching:
                return matching
    return query.order_by(HypothesisMemory.confidence.desc()).limit(MAX_HYPOTHESIS_INJECT).all()


def _extract_keywords(text: str) -> list[str]:
    stopwords = {
        "i", "me", "my", "we", "our", "you", "your", "the", "a", "an", "is", "are",
        "was", "were", "be", "been", "being", "have", "has", "had", "do", "does",
        "did", "will", "would", "could", "should", "can", "may", "might", "shall",
        "to", "of", "in", "for", "on", "with", "at", "by", "from", "as", "into",
        "about", "between", "through", "during", "before", "after", "above", "below",
        "and", "but", "or", "nor", "not", "so", "yet", "both", "either", "neither",
        "it", "its", "this", "that", "these", "those", "what", "which", "who",
        "whom", "how", "when", "where", "why", "all", "each", "every", "any",
        "few", "more", "most", "some", "such", "no", "only", "same", "than",
        "too", "very", "just", "if", "then", "also", "much", "well", "here",
        "there", "now", "up", "out", "get", "got", "go", "going", "went",
        "hey", "hi", "hello", "ok", "okay", "yeah", "yes", "no", "thanks",
        "please", "want", "need", "like", "know", "think", "tell", "say",
        "said", "make", "made", "let", "see", "look", "come", "came",
    }
    words = text.lower().split()
    return [w.strip(".,!?;:'\"()") for w in words if len(w) > 2 and w.lower().strip(".,!?;:'\"()") not in stopwords]


# ─── Consolidation (Delta-Based) ────────────────────────────

CONSOLIDATION_SYSTEM_PROMPT = """You are a memory consolidation system. Extract durable knowledge from conversations.

Extract:
1. **Profile facts**: Stable things about the user (identity, preferences, relationships). Only save things likely to be true beyond this conversation.
2. **Episodes**: Notable events or situations worth remembering.
3. **Hypotheses**: Behavioral patterns — but ONLY if there's genuine evidence, not speculation from a single remark.
4. **Updates**: If anything contradicts or updates existing memories, use update tools.

Rules:
- Do NOT save trivial task details or ephemeral information
- Do NOT duplicate existing facts — check existing memories below
- Keep summaries concise: profile facts 20-80 tokens, episodes 50-120 tokens
- Confidence: 1.0 = user stated it directly, 0.7-0.9 = strongly implied, 0.3-0.6 = inferred
- It's fine to extract nothing if the conversation has no durable information"""

CONSOLIDATION_TOOLS = [
    {
        "name": "save_profile_fact",
        "description": "Save a stable fact about the user.",
        "input_schema": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "The fact (20-80 tokens)"},
                "category": {"type": "string", "enum": ["Identity", "Preference", "Relationship", "Project", "Work", "Health", "Home", "Finance", "Personal"]},
                "tags": {"type": "array", "items": {"type": "string"}},
                "confidence": {"type": "number"},
                "source": {"type": "string", "enum": ["explicit", "inferred"]},
            },
            "required": ["content", "category"],
        },
    },
    {
        "name": "save_episode",
        "description": "Save a notable event or recurring situation.",
        "input_schema": {
            "type": "object",
            "properties": {
                "summary": {"type": "string", "description": "Summary (50-120 tokens)"},
                "importance": {"type": "number"},
                "tags": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["summary"],
        },
    },
    {
        "name": "propose_hypothesis",
        "description": "Propose a behavioral pattern. Only use for genuine patterns, not one-time remarks.",
        "input_schema": {
            "type": "object",
            "properties": {
                "short_summary": {"type": "string"},
                "long_summary": {"type": "string"},
                "category": {"type": "string", "enum": ["behavioral", "emotional", "preference", "relational", "work_style"]},
                "tags": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["short_summary", "long_summary", "category"],
        },
    },
    {
        "name": "update_hypothesis",
        "description": "Update an existing hypothesis with new evidence.",
        "input_schema": {
            "type": "object",
            "properties": {
                "hypothesis_id": {"type": "integer"},
                "supports": {"type": "boolean"},
                "reason": {"type": "string"},
            },
            "required": ["hypothesis_id", "supports"],
        },
    },
    {
        "name": "update_profile_fact",
        "description": "Supersede an existing profile fact that has changed.",
        "input_schema": {
            "type": "object",
            "properties": {
                "old_fact_id": {"type": "integer"},
                "new_content": {"type": "string"},
                "reason": {"type": "string"},
            },
            "required": ["old_fact_id", "new_content"],
        },
    },
]


def should_consolidate(user: User, session_id: str, db: Session) -> bool:
    """Check if consolidation should run based on message count and time thresholds."""
    checkpoint = _get_or_create_checkpoint(user, session_id, db)

    # Don't run if already consolidating (simple lock)
    if checkpoint.is_consolidating:
        return False

    # Count new messages since last consolidation
    new_count = db.query(ConversationMessage).filter(
        ConversationMessage.user_id == user.id,
        ConversationMessage.session_id == session_id,
        ConversationMessage.id > checkpoint.last_consolidated_message_id,
    ).count()

    if new_count >= CONSOLIDATION_MESSAGE_THRESHOLD:
        return True

    # Also consolidate if it's been 24+ hours
    if checkpoint.last_consolidated_at:
        hours_since = (datetime.now(timezone.utc) - checkpoint.last_consolidated_at).total_seconds() / 3600
        if hours_since >= 24 and new_count >= 4:
            return True

    return False


def consolidate_session(user: User, session_id: str, db: Session):
    """
    Delta-based consolidation: only process messages since last checkpoint.
    Uses PostgreSQL advisory lock for concurrency safety.
    """
    checkpoint = _get_or_create_checkpoint(user, session_id, db)

    # Atomic lock acquisition
    acquired = db.execute(
        text("UPDATE consolidation_checkpoints SET is_consolidating = true WHERE id = :id AND is_consolidating = false RETURNING id"),
        {"id": checkpoint.id}
    ).fetchone()
    db.commit()

    if not acquired:
        logger.info(f"Consolidation already running for session {session_id}, skipping")
        return

    try:
        # Drain loop: keep processing until no new messages
        while True:
            # Refresh checkpoint
            db.refresh(checkpoint)

            new_messages = (
                db.query(ConversationMessage)
                .filter(
                    ConversationMessage.user_id == user.id,
                    ConversationMessage.session_id == session_id,
                    ConversationMessage.id > checkpoint.last_consolidated_message_id,
                )
                .order_by(ConversationMessage.created_at.asc())
                .limit(CONSOLIDATION_MAX_INPUT_MESSAGES)
                .all()
            )

            if len(new_messages) < 4:
                break  # Not enough new material

            conversation_text = _format_conversation_for_consolidation(new_messages)
            max_message_id = max(m.id for m in new_messages)

            # Build existing memory context
            existing_context = _build_existing_memory_context(user, db)

            system = CONSOLIDATION_SYSTEM_PROMPT
            if existing_context:
                system += f"\n\n{existing_context}"

            # LLM call via cheap background model
            response = _background_llm_call(
                system=system,
                user_content=f"Here is the conversation to consolidate:\n\n{conversation_text}",
                tools=CONSOLIDATION_TOOLS,
            )

            # Process tool calls — all writes + checkpoint in one transaction
            if response.choices and response.choices[0].message.tool_calls:
                for tool_call in response.choices[0].message.tool_calls:
                    tool_input = json.loads(tool_call.function.arguments)
                    _process_consolidation_tool(
                        tool_name=tool_call.function.name,
                        tool_input=tool_input,
                        user=user,
                        session_id=session_id,
                        db=db,
                    )

            # Advance checkpoint atomically with memory writes
            checkpoint.last_consolidated_message_id = max_message_id
            checkpoint.last_consolidated_at = datetime.now(timezone.utc)
            db.commit()
            logger.info(f"Consolidated session {session_id} up to message {max_message_id}")

            # Check if there are more messages (drain loop)
            remaining = db.query(ConversationMessage).filter(
                ConversationMessage.user_id == user.id,
                ConversationMessage.session_id == session_id,
                ConversationMessage.id > max_message_id,
            ).count()

            if remaining < 4:
                break

    except Exception as e:
        db.rollback()
        logger.error(f"Consolidation failed for session {session_id}: {e}", exc_info=True)
    finally:
        # Release lock
        checkpoint.is_consolidating = False
        db.commit()


def _get_or_create_checkpoint(user: User, session_id: str, db: Session) -> ConsolidationCheckpoint:
    checkpoint = db.query(ConsolidationCheckpoint).filter(
        ConsolidationCheckpoint.user_id == user.id,
        ConsolidationCheckpoint.session_id == session_id,
    ).first()
    if not checkpoint:
        checkpoint = ConsolidationCheckpoint(
            user_id=user.id,
            session_id=session_id,
            last_consolidated_message_id=0,
        )
        db.add(checkpoint)
        db.commit()
        db.refresh(checkpoint)
    return checkpoint


def _build_existing_memory_context(user: User, db: Session) -> str:
    existing_profiles = db.query(ProfileMemory).filter(
        ProfileMemory.user_id == user.id,
        ProfileMemory.status == "active",
    ).all()
    existing_hypotheses = db.query(HypothesisMemory).filter(
        HypothesisMemory.user_id == user.id,
        HypothesisMemory.status.in_(["provisional", "active"]),
    ).all()

    parts = []
    if existing_profiles:
        lines = [f"- [ID:{p.id}] [{p.category}] {p.content}" for p in existing_profiles]
        parts.append("## Existing profile facts:\n" + "\n".join(lines))
    if existing_hypotheses:
        lines = [f"- [ID:{h.id}] [{h.category}] {h.short_summary} (confidence: {h.confidence})" for h in existing_hypotheses]
        parts.append("## Existing hypotheses:\n" + "\n".join(lines))
    return "\n\n".join(parts)


def _format_conversation_for_consolidation(messages) -> str:
    lines = []
    for msg in messages:
        try:
            content = json.loads(msg.content)
        except (json.JSONDecodeError, TypeError):
            content = msg.content

        role = "User" if msg.role == "user" else "Assistant"

        if isinstance(content, list):
            text_parts = []
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "text":
                        text_parts.append(block["text"])
                    elif block.get("type") == "tool_use":
                        text_parts.append(f"[Used tool: {block.get('name', 'unknown')}]")
                    elif block.get("type") == "tool_result":
                        text_parts.append(f"[Tool result: {str(block.get('content', ''))[:200]}]")
                else:
                    text_parts.append(str(block))
            if text_parts:
                lines.append(f"{role}: {' '.join(text_parts)}")
        elif isinstance(content, str):
            lines.append(f"{role}: {content}")

    return "\n".join(lines)


def _process_consolidation_tool(tool_name: str, tool_input: dict, user: User, session_id: str, db: Session):
    """Process a single tool call from the consolidation LLM. No commit — caller handles transaction."""
    try:
        if tool_name == "save_profile_fact":
            # Duplicate check
            existing = db.query(ProfileMemory).filter(
                ProfileMemory.user_id == user.id,
                ProfileMemory.status == "active",
                ProfileMemory.content.ilike(f"%{tool_input['content'][:50]}%"),
            ).first()
            if existing:
                logger.info(f"Skipping duplicate profile fact: {tool_input['content'][:60]}")
                return

            db.add(ProfileMemory(
                user_id=user.id,
                content=tool_input["content"],
                category=tool_input.get("category", "General"),
                confidence=tool_input.get("confidence", 1.0),
                source=tool_input.get("source", "explicit"),
                tags=",".join(tool_input.get("tags", [])),
                source_session_id=session_id,
                last_confirmed=datetime.now(timezone.utc),
            ))

        elif tool_name == "save_episode":
            # Duplicate check for episodes
            existing = db.query(EpisodicMemory).filter(
                EpisodicMemory.user_id == user.id,
                EpisodicMemory.status == "active",
                EpisodicMemory.summary.ilike(f"%{tool_input['summary'][:50]}%"),
            ).first()
            if existing:
                # Update recurrence instead of duplicating
                existing.recurrence_count += 1
                existing.updated_at = datetime.now(timezone.utc)
                logger.info(f"Incremented episode recurrence: {tool_input['summary'][:60]}")
                return

            db.add(EpisodicMemory(
                user_id=user.id,
                summary=tool_input["summary"],
                importance=tool_input.get("importance", 0.5),
                tags=",".join(tool_input.get("tags", [])),
                source_session_ids=json.dumps([session_id]),
                time_start=datetime.now(timezone.utc),
            ))

        elif tool_name == "propose_hypothesis":
            # Duplicate check for hypotheses
            existing = db.query(HypothesisMemory).filter(
                HypothesisMemory.user_id == user.id,
                HypothesisMemory.status.in_(["provisional", "active"]),
                or_(
                    HypothesisMemory.short_summary.ilike(f"%{tool_input['short_summary'][:40]}%"),
                    HypothesisMemory.long_summary.ilike(f"%{tool_input['short_summary'][:40]}%"),
                ),
            ).first()
            if existing:
                # Treat as supporting evidence
                existing.evidence_for += 1
                existing.last_confirmed = datetime.now(timezone.utc)
                _update_hypothesis_confidence(existing)
                logger.info(f"Strengthened existing hypothesis: {existing.short_summary[:60]}")
                return

            db.add(HypothesisMemory(
                user_id=user.id,
                short_summary=tool_input["short_summary"],
                long_summary=tool_input["long_summary"],
                confidence=0.3,
                evidence_for=1,
                evidence_against=0,
                category=tool_input.get("category"),
                tags=",".join(tool_input.get("tags", [])),
                source_session_ids=json.dumps([session_id]),
                status="provisional",
                # last_confirmed intentionally NULL — not yet confirmed
            ))

        elif tool_name == "update_hypothesis":
            hyp_id = tool_input.get("hypothesis_id")
            hypothesis = db.query(HypothesisMemory).filter(
                HypothesisMemory.id == hyp_id,
                HypothesisMemory.user_id == user.id,
            ).first()
            if hypothesis:
                if tool_input.get("supports", True):
                    hypothesis.evidence_for += 1
                    hypothesis.last_confirmed = datetime.now(timezone.utc)
                else:
                    hypothesis.evidence_against += 1
                _update_hypothesis_confidence(hypothesis)

                try:
                    sessions = json.loads(hypothesis.source_session_ids or "[]")
                except (json.JSONDecodeError, TypeError):
                    sessions = []
                if session_id not in sessions:
                    sessions.append(session_id)
                    hypothesis.source_session_ids = json.dumps(sessions)

        elif tool_name == "update_profile_fact":
            old_id = tool_input.get("old_fact_id")
            old_fact = db.query(ProfileMemory).filter(
                ProfileMemory.id == old_id,
                ProfileMemory.user_id == user.id,
            ).first()
            if old_fact:
                old_fact.status = "outdated"
                new_mem = ProfileMemory(
                    user_id=user.id,
                    content=tool_input["new_content"],
                    category=old_fact.category,
                    confidence=1.0,
                    source="explicit",
                    tags=old_fact.tags,
                    source_session_id=session_id,
                    last_confirmed=datetime.now(timezone.utc),
                )
                db.add(new_mem)
                db.flush()
                old_fact.superseded_by_id = new_mem.id

    except Exception as e:
        logger.error(f"Error processing consolidation tool {tool_name}: {e}", exc_info=True)


def _update_hypothesis_confidence(hypothesis):
    """Recalculate hypothesis confidence from evidence counts + decay."""
    total = hypothesis.evidence_for + hypothesis.evidence_against
    base = hypothesis.evidence_for / total if total > 0 else 0.5

    if hypothesis.last_confirmed:
        days_since = (datetime.now(timezone.utc) - hypothesis.last_confirmed).days
        decay = max(0.5, 1.0 - (days_since / 180))
    else:
        decay = 0.8

    hypothesis.confidence = round(base * decay, 2)

    if hypothesis.confidence >= 0.7 and hypothesis.evidence_for >= 4:
        hypothesis.status = "active"
    elif hypothesis.confidence < 0.3:
        hypothesis.status = "challenged"


# ─── Session Summaries ───────────────────────────────────────

def should_generate_summary(user: User, session_id: str, db: Session) -> bool:
    """Check if we should generate a new session summary."""
    latest_summary = (
        db.query(SessionSummary)
        .filter(SessionSummary.user_id == user.id, SessionSummary.session_id == session_id)
        .order_by(SessionSummary.created_at.desc())
        .first()
    )

    since_id = latest_summary.message_id_end if latest_summary else 0

    new_count = db.query(ConversationMessage).filter(
        ConversationMessage.user_id == user.id,
        ConversationMessage.session_id == session_id,
        ConversationMessage.id > since_id,
    ).count()

    return new_count >= SUMMARY_MESSAGE_INTERVAL


def generate_session_summary(user: User, session_id: str, db: Session):
    """Generate a compressed summary of recent conversation for medium-term context."""
    latest_summary = (
        db.query(SessionSummary)
        .filter(SessionSummary.user_id == user.id, SessionSummary.session_id == session_id)
        .order_by(SessionSummary.created_at.desc())
        .first()
    )

    since_id = latest_summary.message_id_end if latest_summary else 0
    prior_summary = latest_summary.summary if latest_summary else None

    messages = (
        db.query(ConversationMessage)
        .filter(
            ConversationMessage.user_id == user.id,
            ConversationMessage.session_id == session_id,
            ConversationMessage.id > since_id,
        )
        .order_by(ConversationMessage.created_at.asc())
        .limit(CONSOLIDATION_MAX_INPUT_MESSAGES)
        .all()
    )

    if len(messages) < 4:
        return

    conversation_text = _format_conversation_for_consolidation(messages)
    max_id = max(m.id for m in messages)
    min_id = min(m.id for m in messages)

    prior_context = ""
    if prior_summary:
        prior_context = f"\n\nPrevious conversation summary:\n{prior_summary}\n\nNow summarize the NEW messages below, building on the previous summary:"

    system = f"""You are a conversation summarizer. Create a concise summary (200-300 tokens) that captures:
- Key topics discussed
- Decisions made or actions taken
- Important context for continuing the conversation
- The user's current mood/state if apparent

Keep it factual and concise. This will be injected as context for future messages.{prior_context}"""

    try:
        response = _background_llm_call(
            system=system,
            user_content=conversation_text,
            max_tokens=500,
        )

        summary_text = response.choices[0].message.content if response.choices else None
        if summary_text:
            db.add(SessionSummary(
                user_id=user.id,
                session_id=session_id,
                summary=summary_text,
                message_id_start=min_id,
                message_id_end=max_id,
                message_count=len(messages),
            ))
            db.commit()
            logger.info(f"Generated session summary for {session_id}: {len(summary_text)} chars")

    except Exception as e:
        logger.error(f"Session summary failed for {session_id}: {e}", exc_info=True)


# ─── Memory Management ──────────────────────────────────────

def search_memories(user: User, query: str, db: Session, module: str = None) -> str:
    results = []
    if not module or module == "profile":
        pattern = f"%{query}%"
        for p in db.query(ProfileMemory).filter(
            ProfileMemory.user_id == user.id, ProfileMemory.status == "active",
            or_(ProfileMemory.content.ilike(pattern), ProfileMemory.tags.ilike(pattern)),
        ).limit(10).all():
            results.append(f"[Profile | {p.category}] {p.content}")

    if not module or module == "episodic":
        pattern = f"%{query}%"
        for e in db.query(EpisodicMemory).filter(
            EpisodicMemory.user_id == user.id, EpisodicMemory.status == "active",
            or_(EpisodicMemory.summary.ilike(pattern), EpisodicMemory.tags.ilike(pattern)),
        ).limit(10).all():
            results.append(f"[Episode] {e.summary}")

    if not module or module == "hypothesis":
        pattern = f"%{query}%"
        for h in db.query(HypothesisMemory).filter(
            HypothesisMemory.user_id == user.id,
            HypothesisMemory.status.in_(["provisional", "active"]),
            or_(HypothesisMemory.short_summary.ilike(pattern), HypothesisMemory.long_summary.ilike(pattern), HypothesisMemory.tags.ilike(pattern)),
        ).limit(10).all():
            results.append(f"[Hypothesis | confidence:{h.confidence}] {h.short_summary}")

    if not results:
        return f"No memories found matching '{query}'."
    return f"{len(results)} memory(ies) found:\n" + "\n".join(f"- {r}" for r in results)


def correct_belief(user: User, belief_summary: str, correction: str, db: Session) -> str:
    pattern = f"%{belief_summary}%"
    hypothesis = db.query(HypothesisMemory).filter(
        HypothesisMemory.user_id == user.id,
        HypothesisMemory.status.in_(["provisional", "active"]),
        or_(HypothesisMemory.short_summary.ilike(pattern), HypothesisMemory.long_summary.ilike(pattern)),
    ).first()
    if hypothesis:
        hypothesis.status = "superseded"
        db.commit()
        return f"Corrected: hypothesis '{hypothesis.short_summary}' has been superseded. Noted: {correction}"

    profile = db.query(ProfileMemory).filter(
        ProfileMemory.user_id == user.id, ProfileMemory.status == "active",
        ProfileMemory.content.ilike(pattern),
    ).first()
    if profile:
        profile.status = "outdated"
        new_mem = ProfileMemory(user_id=user.id, content=correction, category=profile.category,
                                confidence=1.0, source="explicit", tags=profile.tags,
                                last_confirmed=datetime.now(timezone.utc))
        db.add(new_mem)
        db.flush()
        profile.superseded_by_id = new_mem.id
        db.commit()
        return f"Updated: '{profile.content}' → '{correction}'"

    db.commit()
    return f"Couldn't find a matching belief for '{belief_summary}'. The correction has been noted."


def forget_memory(user: User, memory_description: str, db: Session) -> str:
    pattern = f"%{memory_description}%"
    deleted = []
    for p in db.query(ProfileMemory).filter(ProfileMemory.user_id == user.id, ProfileMemory.status == "active", ProfileMemory.content.ilike(pattern)).all():
        p.status = "deleted"
        deleted.append(f"profile: {p.content[:60]}")
    for e in db.query(EpisodicMemory).filter(EpisodicMemory.user_id == user.id, EpisodicMemory.status == "active", EpisodicMemory.summary.ilike(pattern)).all():
        e.status = "archived"
        deleted.append(f"episode: {e.summary[:60]}")
    for h in db.query(HypothesisMemory).filter(HypothesisMemory.user_id == user.id, HypothesisMemory.status.in_(["provisional", "active"]),
                                                or_(HypothesisMemory.short_summary.ilike(pattern), HypothesisMemory.long_summary.ilike(pattern))).all():
        h.status = "superseded"
        deleted.append(f"hypothesis: {h.short_summary[:60]}")
    db.commit()
    if not deleted:
        return f"No memories found matching '{memory_description}'."
    return f"Forgotten {len(deleted)} memory(ies):\n" + "\n".join(f"- {d}" for d in deleted)


# ─── Decay ───────────────────────────────────────────────────

def decay_old_memories(user: User, db: Session):
    """Decay episodic importance and hypothesis confidence for old unconfirmed memories."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=90)

    # Decay episode importance
    old_episodes = db.query(EpisodicMemory).filter(
        EpisodicMemory.user_id == user.id,
        EpisodicMemory.status == "active",
        EpisodicMemory.updated_at < cutoff,
        EpisodicMemory.importance > 0.1,
    ).all()
    for ep in old_episodes:
        ep.importance = round(ep.importance * 0.8, 2)
        if ep.importance < 0.1:
            ep.status = "decayed"

    # Decay hypothesis confidence — including NULL last_confirmed (never confirmed)
    old_hypotheses = db.query(HypothesisMemory).filter(
        HypothesisMemory.user_id == user.id,
        HypothesisMemory.status.in_(["provisional", "active"]),
        or_(
            HypothesisMemory.last_confirmed.is_(None),
            HypothesisMemory.last_confirmed < cutoff,
        ),
    ).all()
    for hyp in old_hypotheses:
        if hyp.last_confirmed:
            days_since = (datetime.now(timezone.utc) - hyp.last_confirmed).days
        else:
            # Never confirmed — use created_at for decay calculation
            days_since = (datetime.now(timezone.utc) - hyp.created_at).days if hyp.created_at else 180
        decay = max(0.5, 1.0 - (days_since / 180))
        base = hyp.evidence_for / (hyp.evidence_for + hyp.evidence_against) if (hyp.evidence_for + hyp.evidence_against) > 0 else 0.5
        hyp.confidence = round(base * decay, 2)
        if hyp.confidence < 0.2:
            hyp.status = "challenged"

    db.commit()
    logger.info(f"Decay pass: {len(old_episodes)} episodes, {len(old_hypotheses)} hypotheses")


# ─── Compaction ──────────────────────────────────────────────

def compact_memories(user: User, db: Session):
    """Compact memory store: merge duplicates, archive low-value. Uses background model."""
    stats = {"profiles": 0, "episodes": 0, "hypotheses": 0}

    profile_count = db.query(ProfileMemory).filter(ProfileMemory.user_id == user.id, ProfileMemory.status == "active").count()
    episode_count = db.query(EpisodicMemory).filter(EpisodicMemory.user_id == user.id, EpisodicMemory.status == "active").count()
    hypothesis_count = db.query(HypothesisMemory).filter(HypothesisMemory.user_id == user.id, HypothesisMemory.status.in_(["provisional", "active"])).count()

    if profile_count > COMPACTION_THRESHOLD:
        stats["profiles"] = _compact_module(user, db, "profile")
    if episode_count > COMPACTION_THRESHOLD:
        stats["episodes"] = _compact_module(user, db, "episode")
    if hypothesis_count > COMPACTION_THRESHOLD:
        stats["hypotheses"] = _compact_module(user, db, "hypothesis")

    logger.info(f"Compaction complete: {stats}")
    return stats


def _compact_module(user: User, db: Session, module: str) -> int:
    """Generic compaction for any memory module."""
    if module == "profile":
        items = db.query(ProfileMemory).filter(ProfileMemory.user_id == user.id, ProfileMemory.status == "active").order_by(ProfileMemory.created_at.asc()).all()
        lines = [f"[ID:{p.id}] [{p.category}] {p.content} (confidence:{p.confidence}, source:{p.source})" for p in items]
    elif module == "episode":
        items = db.query(EpisodicMemory).filter(EpisodicMemory.user_id == user.id, EpisodicMemory.status == "active").order_by(EpisodicMemory.created_at.asc()).all()
        lines = [f"[ID:{e.id}] {e.summary} (importance:{e.importance}, recurrence:{e.recurrence_count})" for e in items]
    elif module == "hypothesis":
        items = db.query(HypothesisMemory).filter(HypothesisMemory.user_id == user.id, HypothesisMemory.status.in_(["provisional", "active"])).order_by(HypothesisMemory.created_at.asc()).all()
        lines = [f"[ID:{h.id}] {h.short_summary} (confidence:{h.confidence}, for:{h.evidence_for}, against:{h.evidence_against})" for h in items]
    else:
        return 0

    system = f"""You are a memory compaction system. Review these {module} memories and identify:
1. DUPLICATES or NEAR-DUPLICATES to merge
2. CONTRADICTIONS (newer wins)
3. LOW-VALUE entries to remove

Return a JSON array of actions:
- {{"action": "merge", "keep_id": <id>, "remove_ids": [<ids>], "merged_content": "<combined>"}}
- {{"action": "remove", "id": <id>, "reason": "duplicate|trivial|outdated"}}

Be conservative. Return [] if nothing needs compacting."""

    try:
        response = _background_llm_call(
            system=system,
            user_content=f"Here are {len(items)} {module} memories:\n\n" + "\n".join(lines),
            max_tokens=4096,
        )

        text = response.choices[0].message.content if response.choices else "[]"
        actions = _parse_json_array(text)
        removed = 0

        for action in actions:
            try:
                if action.get("action") == "merge":
                    keep_id = action["keep_id"]
                    for rid in action.get("remove_ids", []):
                        _mark_memory_removed(db, module, rid, keep_id)
                        removed += 1
                elif action.get("action") == "remove":
                    _mark_memory_removed(db, module, action["id"])
                    removed += 1
            except Exception as e:
                logger.warning(f"Compaction action failed: {e}")

        db.commit()
        logger.info(f"{module} compaction: {removed} removed from {len(items)}")
        return removed

    except Exception as e:
        logger.error(f"Compaction failed for {module}: {e}", exc_info=True)
        return 0


def _mark_memory_removed(db: Session, module: str, mem_id: int, superseded_by: int = None):
    if module == "profile":
        mem = db.query(ProfileMemory).filter(ProfileMemory.id == mem_id).first()
        if mem:
            mem.status = "outdated"
            if superseded_by:
                mem.superseded_by_id = superseded_by
    elif module == "episode":
        mem = db.query(EpisodicMemory).filter(EpisodicMemory.id == mem_id).first()
        if mem:
            mem.status = "archived"
    elif module == "hypothesis":
        mem = db.query(HypothesisMemory).filter(HypothesisMemory.id == mem_id).first()
        if mem:
            mem.status = "superseded"
            if superseded_by:
                mem.superseded_by_id = superseded_by


def _parse_json_array(text: str) -> list:
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = re.search(r'```(?:json)?\s*(\[.*?\])\s*```', text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass
    match = re.search(r'\[.*\]', text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass
    logger.warning("Could not parse compaction response as JSON array")
    return []


# ─── Recovery Sweep ──────────────────────────────────────────

def recovery_sweep(db: Session):
    """Find sessions with unconsolidated messages and trigger consolidation."""
    checkpoints = db.query(ConsolidationCheckpoint).filter(
        ConsolidationCheckpoint.is_consolidating == False,
    ).all()

    for cp in checkpoints:
        new_count = db.query(ConversationMessage).filter(
            ConversationMessage.user_id == cp.user_id,
            ConversationMessage.session_id == cp.session_id,
            ConversationMessage.id > cp.last_consolidated_message_id,
        ).count()

        if new_count >= 4:
            user = db.query(User).filter(User.id == cp.user_id).first()
            if user:
                logger.info(f"Recovery sweep: consolidating {cp.session_id} ({new_count} stranded messages)")
                try:
                    consolidate_session(user, cp.session_id, db)
                except Exception as e:
                    logger.error(f"Recovery consolidation failed for {cp.session_id}: {e}")
