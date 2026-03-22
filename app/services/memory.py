"""
services/memory.py — Layered Memory System
=============================================
Handles consolidation (end-of-session extraction), retrieval (keyword+ranking),
and injection (formatting memories for the system prompt).

Modules:
  - ProfileMemory: stable facts (user-stated or inferred)
  - EpisodicMemory: notable events or recurring situations
  - HypothesisMemory: inferred patterns with confidence tracking
"""

import json
import logging
from datetime import datetime, timezone, timedelta
from sqlalchemy.orm import Session
from sqlalchemy import or_

from app.models import (
    User, ProfileMemory, EpisodicMemory, HypothesisMemory,
    ConversationMessage,
)
from app.services.clients import anthropic_client
from app.config import settings

logger = logging.getLogger("planner.memory")

# How many memories to inject into the system prompt
MAX_PROFILE_INJECT = 50
MAX_EPISODE_INJECT = 15
MAX_HYPOTHESIS_INJECT = 8
HYPOTHESIS_INJECT_THRESHOLD = 0.4  # minimum confidence to inject


# ─── Retrieval ───────────────────────────────────────────────

def get_relevant_memories(user: User, message_text: str, db: Session) -> str:
    """Retrieve and format relevant memories for system prompt injection."""
    sections = []

    # 1. Profile memories — always include top active ones
    profiles = _get_profile_memories(user, message_text, db)
    if profiles:
        lines = [f"- [{p.category}] {p.content}" for p in profiles]
        sections.append("### Known facts about you\n" + "\n".join(lines))

    # 2. Episodic memories — recent/important episodes
    episodes = _get_episodic_memories(user, message_text, db)
    if episodes:
        lines = []
        for e in episodes:
            time_str = ""
            if e.time_start:
                time_str = f" ({e.time_start.strftime('%b %Y')})"
            lines.append(f"- {e.summary}{time_str}")
        sections.append("### Notable events/situations\n" + "\n".join(lines))

    # 3. Hypothesis memories — only confident ones
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


def _get_profile_memories(user: User, message_text: str, db: Session) -> list:
    """Get active profile memories, optionally filtered by relevance."""
    query = db.query(ProfileMemory).filter(
        ProfileMemory.user_id == user.id,
        ProfileMemory.status == "active",
    )

    # If message has keywords, prioritize matching ones but still include all
    if message_text:
        keywords = _extract_keywords(message_text)
        if keywords:
            # Get keyword-matching ones first
            pattern_filters = []
            for kw in keywords[:5]:
                pat = f"%{kw}%"
                pattern_filters.append(ProfileMemory.content.ilike(pat))
                pattern_filters.append(ProfileMemory.tags.ilike(pat))

            matching = query.filter(or_(*pattern_filters)).limit(MAX_PROFILE_INJECT).all()
            matching_ids = {m.id for m in matching}

            # Fill remaining slots with recent non-matching ones
            remaining = MAX_PROFILE_INJECT - len(matching)
            if remaining > 0:
                others = query.filter(
                    ~ProfileMemory.id.in_(matching_ids)
                ).order_by(ProfileMemory.created_at.desc()).limit(remaining).all()
                return matching + others
            return matching

    return query.order_by(ProfileMemory.created_at.desc()).limit(MAX_PROFILE_INJECT).all()


def _get_episodic_memories(user: User, message_text: str, db: Session) -> list:
    """Get recent/important episodic memories."""
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

    # Default: most important recent episodes
    return query.order_by(
        EpisodicMemory.importance.desc(),
        EpisodicMemory.updated_at.desc(),
    ).limit(MAX_EPISODE_INJECT).all()


def _get_hypothesis_memories(user: User, message_text: str, db: Session) -> list:
    """Get confident hypothesis memories relevant to the conversation."""
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

    return query.order_by(
        HypothesisMemory.confidence.desc(),
    ).limit(MAX_HYPOTHESIS_INJECT).all()


def _extract_keywords(text: str) -> list[str]:
    """Simple keyword extraction: split, remove stopwords, keep meaningful words."""
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


# ─── Consolidation ──────────────────────────────────────────

CONSOLIDATION_TOOLS = [
    {
        "name": "save_profile_fact",
        "description": "Save a stable fact about the user: identity, preference, relationship, project, or other durable information.",
        "input_schema": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "The fact (20-80 tokens, concise)"},
                "category": {"type": "string", "enum": ["Identity", "Preference", "Relationship", "Project", "Work", "Health", "Home", "Finance", "Personal"], "description": "Category of fact"},
                "tags": {"type": "array", "items": {"type": "string"}, "description": "2-4 keyword tags"},
                "confidence": {"type": "number", "description": "1.0 if user stated it directly, 0.7-0.9 if strongly implied"},
                "source": {"type": "string", "enum": ["explicit", "inferred"], "description": "Whether user stated this directly or it was inferred"},
            },
            "required": ["content", "category"],
        },
    },
    {
        "name": "save_episode",
        "description": "Save a notable event or recurring situation from the conversation.",
        "input_schema": {
            "type": "object",
            "properties": {
                "summary": {"type": "string", "description": "Summary of the event/situation (50-120 tokens)"},
                "importance": {"type": "number", "description": "0.0-1.0, how significant this is for understanding the user"},
                "tags": {"type": "array", "items": {"type": "string"}, "description": "2-4 keyword tags"},
            },
            "required": ["summary"],
        },
    },
    {
        "name": "propose_hypothesis",
        "description": "Propose a behavioral pattern or tendency you noticed about the user. Only use if you see a genuine pattern, not a one-time occurrence.",
        "input_schema": {
            "type": "object",
            "properties": {
                "short_summary": {"type": "string", "description": "Brief pattern description (30-50 tokens)"},
                "long_summary": {"type": "string", "description": "Detailed explanation with context (80-140 tokens)"},
                "category": {"type": "string", "enum": ["behavioral", "emotional", "preference", "relational", "work_style"], "description": "Type of pattern"},
                "tags": {"type": "array", "items": {"type": "string"}, "description": "2-4 keyword tags"},
            },
            "required": ["short_summary", "long_summary", "category"],
        },
    },
    {
        "name": "update_hypothesis",
        "description": "Update an existing hypothesis with new supporting or contradicting evidence.",
        "input_schema": {
            "type": "object",
            "properties": {
                "hypothesis_id": {"type": "integer", "description": "ID of the hypothesis to update"},
                "supports": {"type": "boolean", "description": "True if this conversation supports the hypothesis, false if it contradicts it"},
                "reason": {"type": "string", "description": "Brief explanation of the evidence"},
            },
            "required": ["hypothesis_id", "supports"],
        },
    },
    {
        "name": "update_profile_fact",
        "description": "Update or supersede an existing profile fact that has changed.",
        "input_schema": {
            "type": "object",
            "properties": {
                "old_fact_id": {"type": "integer", "description": "ID of the fact to supersede"},
                "new_content": {"type": "string", "description": "Updated content"},
                "reason": {"type": "string", "description": "Why it changed"},
            },
            "required": ["old_fact_id", "new_content"],
        },
    },
]


async def consolidate_session(user: User, session_id: str, db: Session):
    """
    End-of-session consolidation: extract facts, episodes, and hypotheses
    from the conversation. Called when session ends.
    """
    # Load session messages
    messages = (
        db.query(ConversationMessage)
        .filter(
            ConversationMessage.user_id == user.id,
            ConversationMessage.session_id == session_id,
        )
        .order_by(ConversationMessage.created_at.asc())
        .all()
    )

    if len(messages) < 2:
        logger.info(f"Skipping consolidation for session {session_id}: too few messages")
        return

    # Format conversation for the consolidator
    conversation_text = _format_conversation_for_consolidation(messages)

    # Load existing memories for context (avoid duplicates)
    existing_profiles = db.query(ProfileMemory).filter(
        ProfileMemory.user_id == user.id,
        ProfileMemory.status == "active",
    ).all()

    existing_hypotheses = db.query(HypothesisMemory).filter(
        HypothesisMemory.user_id == user.id,
        HypothesisMemory.status.in_(["provisional", "active"]),
    ).all()

    existing_context = ""
    if existing_profiles:
        lines = [f"- [ID:{p.id}] [{p.category}] {p.content}" for p in existing_profiles]
        existing_context += "## Existing profile facts:\n" + "\n".join(lines) + "\n\n"
    if existing_hypotheses:
        lines = [f"- [ID:{h.id}] [{h.category}] {h.short_summary} (confidence: {h.confidence})" for h in existing_hypotheses]
        existing_context += "## Existing hypotheses:\n" + "\n".join(lines) + "\n\n"

    system_prompt = f"""You are a memory consolidation system. Your job is to extract durable knowledge from conversations.

Read the conversation and extract:
1. **Profile facts**: Stable things about the user (identity, preferences, relationships, projects). Only save things likely to be true beyond this conversation.
2. **Episodes**: Notable events or situations worth remembering for context.
3. **Hypotheses**: Behavioral patterns you notice — but ONLY if there's genuine evidence, not speculation from a single remark.
4. **Updates**: If anything in the conversation contradicts or updates existing memories, use the update tools.

Rules:
- Do NOT save trivial task details or ephemeral information (what they had for lunch, routine requests)
- Do NOT duplicate existing facts — check the existing memories below
- If a fact already exists but has changed, use update_profile_fact to supersede it
- If an existing hypothesis is supported or contradicted by this conversation, use update_hypothesis
- Keep summaries concise: profile facts 20-80 tokens, episodes 50-120 tokens
- Confidence: 1.0 = user stated it directly, 0.7-0.9 = strongly implied, 0.3-0.6 = inferred
- It's fine to extract nothing if the conversation has no durable information

{existing_context}"""

    try:
        response = anthropic_client.messages.create(
            model=settings.intent_model,
            max_tokens=2048,
            system=system_prompt,
            tools=CONSOLIDATION_TOOLS,
            messages=[{"role": "user", "content": f"Here is the conversation to consolidate:\n\n{conversation_text}"}],
        )

        # Process tool calls
        for block in response.content:
            if block.type == "tool_use":
                _process_consolidation_tool(
                    tool_name=block.name,
                    tool_input=block.input,
                    user=user,
                    session_id=session_id,
                    db=db,
                )

        db.commit()
        logger.info(f"Consolidation complete for session {session_id}")

    except Exception as e:
        logger.error(f"Consolidation failed for session {session_id}: {e}", exc_info=True)


def _format_conversation_for_consolidation(messages) -> str:
    """Format conversation messages into readable text for the consolidator."""
    lines = []
    for msg in messages:
        try:
            content = json.loads(msg.content)
        except (json.JSONDecodeError, TypeError):
            content = msg.content

        role = "User" if msg.role == "user" else "Assistant"

        if isinstance(content, list):
            # Extract text blocks, skip tool_use/tool_result details
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
    """Process a single tool call from the consolidation LLM."""
    try:
        if tool_name == "save_profile_fact":
            # Check for near-duplicates
            existing = db.query(ProfileMemory).filter(
                ProfileMemory.user_id == user.id,
                ProfileMemory.status == "active",
                ProfileMemory.content.ilike(f"%{tool_input['content'][:50]}%"),
            ).first()
            if existing:
                logger.info(f"Skipping duplicate profile fact: {tool_input['content'][:60]}")
                return

            mem = ProfileMemory(
                user_id=user.id,
                content=tool_input["content"],
                category=tool_input.get("category", "General"),
                confidence=tool_input.get("confidence", 1.0),
                source=tool_input.get("source", "explicit"),
                tags=",".join(tool_input.get("tags", [])),
                source_session_id=session_id,
                last_confirmed=datetime.now(timezone.utc),
            )
            db.add(mem)
            logger.info(f"Saved profile fact: {tool_input['content'][:60]}")

        elif tool_name == "save_episode":
            mem = EpisodicMemory(
                user_id=user.id,
                summary=tool_input["summary"],
                importance=tool_input.get("importance", 0.5),
                tags=",".join(tool_input.get("tags", [])),
                source_session_ids=json.dumps([session_id]),
                time_start=datetime.now(timezone.utc),
            )
            db.add(mem)
            logger.info(f"Saved episode: {tool_input['summary'][:60]}")

        elif tool_name == "propose_hypothesis":
            mem = HypothesisMemory(
                user_id=user.id,
                short_summary=tool_input["short_summary"],
                long_summary=tool_input["long_summary"],
                confidence=0.3,  # always starts low
                evidence_for=1,
                evidence_against=0,
                category=tool_input.get("category"),
                tags=",".join(tool_input.get("tags", [])),
                source_session_ids=json.dumps([session_id]),
                status="provisional",
            )
            db.add(mem)
            logger.info(f"Proposed hypothesis: {tool_input['short_summary'][:60]}")

        elif tool_name == "update_hypothesis":
            hyp_id = tool_input.get("hypothesis_id")
            hypothesis = db.query(HypothesisMemory).filter(
                HypothesisMemory.id == hyp_id,
                HypothesisMemory.user_id == user.id,
            ).first()
            if hypothesis:
                supports = tool_input.get("supports", True)
                if supports:
                    hypothesis.evidence_for += 1
                    hypothesis.last_confirmed = datetime.now(timezone.utc)
                else:
                    hypothesis.evidence_against += 1

                # Update confidence
                total = hypothesis.evidence_for + hypothesis.evidence_against
                base = hypothesis.evidence_for / total if total > 0 else 0.5

                # Decay: reduce if not confirmed recently
                if hypothesis.last_confirmed:
                    days_since = (datetime.now(timezone.utc) - hypothesis.last_confirmed).days
                    decay = max(0.5, 1.0 - (days_since / 180))
                else:
                    decay = 0.8
                hypothesis.confidence = round(base * decay, 2)

                # Status transitions
                if hypothesis.confidence >= 0.7 and hypothesis.evidence_for >= 4:
                    hypothesis.status = "active"
                elif hypothesis.confidence < 0.3:
                    hypothesis.status = "challenged"

                # Append session to source list
                try:
                    sessions = json.loads(hypothesis.source_session_ids or "[]")
                except (json.JSONDecodeError, TypeError):
                    sessions = []
                if session_id not in sessions:
                    sessions.append(session_id)
                    hypothesis.source_session_ids = json.dumps(sessions)

                logger.info(f"Updated hypothesis {hyp_id}: confidence={hypothesis.confidence}, status={hypothesis.status}")

        elif tool_name == "update_profile_fact":
            old_id = tool_input.get("old_fact_id")
            old_fact = db.query(ProfileMemory).filter(
                ProfileMemory.id == old_id,
                ProfileMemory.user_id == user.id,
            ).first()
            if old_fact:
                old_fact.status = "outdated"
                old_fact.superseded_by_id = None  # will be set after new one is created

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
                db.flush()  # get the new ID
                old_fact.superseded_by_id = new_mem.id
                logger.info(f"Superseded profile fact {old_id} with: {tool_input['new_content'][:60]}")

    except Exception as e:
        logger.error(f"Error processing consolidation tool {tool_name}: {e}", exc_info=True)


# ─── Memory Management ──────────────────────────────────────

def search_memories(user: User, query: str, db: Session, module: str = None) -> str:
    """Search across all memory modules. Returns formatted results."""
    results = []

    if not module or module == "profile":
        pattern = f"%{query}%"
        profiles = db.query(ProfileMemory).filter(
            ProfileMemory.user_id == user.id,
            ProfileMemory.status == "active",
            or_(
                ProfileMemory.content.ilike(pattern),
                ProfileMemory.tags.ilike(pattern),
            ),
        ).limit(10).all()
        for p in profiles:
            results.append(f"[Profile | {p.category}] {p.content}")

    if not module or module == "episodic":
        pattern = f"%{query}%"
        episodes = db.query(EpisodicMemory).filter(
            EpisodicMemory.user_id == user.id,
            EpisodicMemory.status == "active",
            or_(
                EpisodicMemory.summary.ilike(pattern),
                EpisodicMemory.tags.ilike(pattern),
            ),
        ).limit(10).all()
        for e in episodes:
            results.append(f"[Episode] {e.summary}")

    if not module or module == "hypothesis":
        pattern = f"%{query}%"
        hypotheses = db.query(HypothesisMemory).filter(
            HypothesisMemory.user_id == user.id,
            HypothesisMemory.status.in_(["provisional", "active"]),
            or_(
                HypothesisMemory.short_summary.ilike(pattern),
                HypothesisMemory.long_summary.ilike(pattern),
                HypothesisMemory.tags.ilike(pattern),
            ),
        ).limit(10).all()
        for h in hypotheses:
            results.append(f"[Hypothesis | confidence:{h.confidence}] {h.short_summary}")

    if not results:
        return f"No memories found matching '{query}'."

    return f"{len(results)} memory(ies) found:\n" + "\n".join(f"- {r}" for r in results)


def correct_belief(user: User, belief_summary: str, correction: str, db: Session) -> str:
    """User corrects a system belief. Searches and updates/supersedes matching memories."""
    pattern = f"%{belief_summary}%"

    # Check hypotheses first
    hypothesis = db.query(HypothesisMemory).filter(
        HypothesisMemory.user_id == user.id,
        HypothesisMemory.status.in_(["provisional", "active"]),
        or_(
            HypothesisMemory.short_summary.ilike(pattern),
            HypothesisMemory.long_summary.ilike(pattern),
        ),
    ).first()

    if hypothesis:
        hypothesis.status = "superseded"
        db.commit()
        return f"Corrected: hypothesis '{hypothesis.short_summary}' has been superseded. Noted: {correction}"

    # Check profile memories
    profile = db.query(ProfileMemory).filter(
        ProfileMemory.user_id == user.id,
        ProfileMemory.status == "active",
        ProfileMemory.content.ilike(pattern),
    ).first()

    if profile:
        profile.status = "outdated"
        new_mem = ProfileMemory(
            user_id=user.id,
            content=correction,
            category=profile.category,
            confidence=1.0,
            source="explicit",
            tags=profile.tags,
            last_confirmed=datetime.now(timezone.utc),
        )
        db.add(new_mem)
        db.flush()
        profile.superseded_by_id = new_mem.id
        db.commit()
        return f"Updated: '{profile.content}' → '{correction}'"

    db.commit()
    return f"Couldn't find a matching belief for '{belief_summary}'. The correction has been noted."


def forget_memory(user: User, memory_description: str, db: Session) -> str:
    """Remove a specific memory. Cascades to hypotheses built on it."""
    pattern = f"%{memory_description}%"
    deleted = []

    # Check profiles
    profiles = db.query(ProfileMemory).filter(
        ProfileMemory.user_id == user.id,
        ProfileMemory.status == "active",
        ProfileMemory.content.ilike(pattern),
    ).all()
    for p in profiles:
        p.status = "deleted"
        deleted.append(f"profile: {p.content[:60]}")

    # Check episodes
    episodes = db.query(EpisodicMemory).filter(
        EpisodicMemory.user_id == user.id,
        EpisodicMemory.status == "active",
        EpisodicMemory.summary.ilike(pattern),
    ).all()
    for e in episodes:
        e.status = "archived"
        deleted.append(f"episode: {e.summary[:60]}")

    # Check hypotheses
    hypotheses = db.query(HypothesisMemory).filter(
        HypothesisMemory.user_id == user.id,
        HypothesisMemory.status.in_(["provisional", "active"]),
        or_(
            HypothesisMemory.short_summary.ilike(pattern),
            HypothesisMemory.long_summary.ilike(pattern),
        ),
    ).all()
    for h in hypotheses:
        h.status = "superseded"
        deleted.append(f"hypothesis: {h.short_summary[:60]}")

    db.commit()

    if not deleted:
        return f"No memories found matching '{memory_description}'."

    return f"Forgotten {len(deleted)} memory(ies):\n" + "\n".join(f"- {d}" for d in deleted)


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

    # Decay hypothesis confidence
    old_hypotheses = db.query(HypothesisMemory).filter(
        HypothesisMemory.user_id == user.id,
        HypothesisMemory.status.in_(["provisional", "active"]),
        HypothesisMemory.last_confirmed < cutoff,
    ).all()
    for hyp in old_hypotheses:
        days_since = (datetime.now(timezone.utc) - hyp.last_confirmed).days
        decay = max(0.5, 1.0 - (days_since / 180))
        base = hyp.evidence_for / (hyp.evidence_for + hyp.evidence_against) if (hyp.evidence_for + hyp.evidence_against) > 0 else 0.5
        hyp.confidence = round(base * decay, 2)
        if hyp.confidence < 0.2:
            hyp.status = "challenged"

    db.commit()
    logger.info(f"Decay pass: {len(old_episodes)} episodes, {len(old_hypotheses)} hypotheses")


# ─── Compaction ──────────────────────────────────────────────

COMPACTION_THRESHOLD = 30  # run compaction when a module exceeds this many active records


async def compact_memories(user: User, db: Session):
    """
    Compact the memory store: merge duplicates, absorb near-duplicates,
    archive low-value memories. Uses one LLM call per module that needs it.
    """
    stats = {"profiles": 0, "episodes": 0, "hypotheses": 0}

    # Check if compaction is needed
    profile_count = db.query(ProfileMemory).filter(
        ProfileMemory.user_id == user.id,
        ProfileMemory.status == "active",
    ).count()

    episode_count = db.query(EpisodicMemory).filter(
        EpisodicMemory.user_id == user.id,
        EpisodicMemory.status == "active",
    ).count()

    hypothesis_count = db.query(HypothesisMemory).filter(
        HypothesisMemory.user_id == user.id,
        HypothesisMemory.status.in_(["provisional", "active"]),
    ).count()

    if profile_count > COMPACTION_THRESHOLD:
        stats["profiles"] = await _compact_profiles(user, db)

    if episode_count > COMPACTION_THRESHOLD:
        stats["episodes"] = await _compact_episodes(user, db)

    if hypothesis_count > COMPACTION_THRESHOLD:
        stats["hypotheses"] = await _compact_hypotheses(user, db)

    logger.info(f"Compaction complete: {stats}")
    return stats


async def _compact_profiles(user: User, db: Session) -> int:
    """Merge redundant profile facts via LLM."""
    profiles = db.query(ProfileMemory).filter(
        ProfileMemory.user_id == user.id,
        ProfileMemory.status == "active",
    ).order_by(ProfileMemory.created_at.asc()).all()

    # Format for LLM
    lines = [f"[ID:{p.id}] [{p.category}] {p.content} (confidence:{p.confidence}, source:{p.source})" for p in profiles]

    response = anthropic_client.messages.create(
        model=settings.intent_model,
        max_tokens=4096,
        system="""You are a memory compaction system. You receive a list of profile facts about a user.

Your job:
1. Identify DUPLICATE or NEAR-DUPLICATE facts (same information stated differently)
2. Identify CONTRADICTORY facts (newer one should win)
3. Identify facts that can be MERGED into a single richer fact
4. Identify LOW-VALUE facts that are too trivial to keep

For each action, output a JSON object. Return a JSON array of actions.

Action types:
- {"action": "merge", "keep_id": <id>, "remove_ids": [<ids>], "merged_content": "<combined content>"}
- {"action": "remove", "id": <id>, "reason": "duplicate|trivial|outdated"}

Rules:
- When merging, prefer the HIGHER confidence and MORE RECENT fact as the keeper
- Never remove a fact with source "explicit" unless it's truly duplicated
- Be conservative — only act on clear duplicates/contradictions, not vague similarity
- Return [] if nothing needs compacting""",
        messages=[{"role": "user", "content": f"Here are {len(profiles)} profile facts to review:\n\n" + "\n".join(lines)}],
    )

    # Parse response
    actions = _parse_compaction_response(response)
    removed = 0

    for action in actions:
        try:
            if action.get("action") == "merge":
                keep_id = action["keep_id"]
                remove_ids = action.get("remove_ids", [])
                merged_content = action.get("merged_content")

                keeper = db.query(ProfileMemory).filter(ProfileMemory.id == keep_id).first()
                if keeper and merged_content:
                    keeper.content = merged_content
                    keeper.updated_at = datetime.now(timezone.utc)

                for rid in remove_ids:
                    victim = db.query(ProfileMemory).filter(ProfileMemory.id == rid).first()
                    if victim:
                        victim.status = "outdated"
                        victim.superseded_by_id = keep_id
                        removed += 1

            elif action.get("action") == "remove":
                rid = action["id"]
                victim = db.query(ProfileMemory).filter(ProfileMemory.id == rid).first()
                if victim:
                    victim.status = "outdated"
                    removed += 1
        except Exception as e:
            logger.warning(f"Compaction action failed: {e}")

    db.commit()
    logger.info(f"Profile compaction: {removed} facts removed/merged from {len(profiles)}")
    return removed


async def _compact_episodes(user: User, db: Session) -> int:
    """Merge related episodes and archive old low-importance ones."""
    episodes = db.query(EpisodicMemory).filter(
        EpisodicMemory.user_id == user.id,
        EpisodicMemory.status == "active",
    ).order_by(EpisodicMemory.created_at.asc()).all()

    lines = [f"[ID:{e.id}] {e.summary} (importance:{e.importance}, recurrence:{e.recurrence_count}, created:{e.created_at.strftime('%Y-%m-%d') if e.created_at else 'unknown'})" for e in episodes]

    response = anthropic_client.messages.create(
        model=settings.intent_model,
        max_tokens=4096,
        system="""You are a memory compaction system. You receive a list of episodic memories (events/situations).

Your job:
1. Identify episodes that describe the SAME EVENT or RECURRING SITUATION and should be merged
2. Identify episodes that are too old and trivial to keep (low importance, no recurrence)

Action types:
- {"action": "merge", "keep_id": <id>, "remove_ids": [<ids>], "merged_summary": "<combined summary>", "new_recurrence": <count>}
- {"action": "archive", "id": <id>, "reason": "trivial|outdated|resolved"}

Rules:
- Merge episodes about the same recurring situation into one with higher recurrence count
- Archive episodes older than 3 months with importance < 0.3 and recurrence = 1
- Be conservative — keep anything that might provide useful context
- Return [] if nothing needs compacting""",
        messages=[{"role": "user", "content": f"Here are {len(episodes)} episodes to review:\n\n" + "\n".join(lines)}],
    )

    actions = _parse_compaction_response(response)
    removed = 0

    for action in actions:
        try:
            if action.get("action") == "merge":
                keep_id = action["keep_id"]
                remove_ids = action.get("remove_ids", [])
                merged_summary = action.get("merged_summary")
                new_recurrence = action.get("new_recurrence", 1)

                keeper = db.query(EpisodicMemory).filter(EpisodicMemory.id == keep_id).first()
                if keeper:
                    if merged_summary:
                        keeper.summary = merged_summary
                    keeper.recurrence_count = new_recurrence
                    keeper.updated_at = datetime.now(timezone.utc)

                for rid in remove_ids:
                    victim = db.query(EpisodicMemory).filter(EpisodicMemory.id == rid).first()
                    if victim:
                        victim.status = "archived"
                        removed += 1

            elif action.get("action") == "archive":
                rid = action["id"]
                victim = db.query(EpisodicMemory).filter(EpisodicMemory.id == rid).first()
                if victim:
                    victim.status = "archived"
                    removed += 1
        except Exception as e:
            logger.warning(f"Episode compaction action failed: {e}")

    db.commit()
    logger.info(f"Episode compaction: {removed} episodes archived/merged from {len(episodes)}")
    return removed


async def _compact_hypotheses(user: User, db: Session) -> int:
    """Merge overlapping hypotheses and archive weak ones."""
    hypotheses = db.query(HypothesisMemory).filter(
        HypothesisMemory.user_id == user.id,
        HypothesisMemory.status.in_(["provisional", "active"]),
    ).order_by(HypothesisMemory.created_at.asc()).all()

    lines = [f"[ID:{h.id}] {h.short_summary} | {h.long_summary} (confidence:{h.confidence}, for:{h.evidence_for}, against:{h.evidence_against}, status:{h.status})" for h in hypotheses]

    response = anthropic_client.messages.create(
        model=settings.intent_model,
        max_tokens=4096,
        system="""You are a memory compaction system. You receive a list of hypotheses about user behavior.

Your job:
1. Identify hypotheses that describe the SAME PATTERN and should be merged
2. Identify hypotheses that CONTRADICT each other — keep the one with more evidence
3. Identify hypotheses with very low confidence and no recent evidence that should be dropped

Action types:
- {"action": "merge", "keep_id": <id>, "remove_ids": [<ids>], "merged_short": "<summary>", "merged_long": "<detail>", "combined_evidence_for": <n>, "combined_evidence_against": <n>}
- {"action": "supersede", "winner_id": <id>, "loser_id": <id>, "reason": "contradiction"}
- {"action": "drop", "id": <id>, "reason": "weak|stale|unfounded"}

Rules:
- When merging, combine evidence counts
- When hypotheses contradict, the one with higher evidence_for wins
- Only drop hypotheses with confidence < 0.25 and evidence_for <= 1
- Be conservative — uncertain hypotheses that haven't been disproven yet should stay
- Return [] if nothing needs compacting""",
        messages=[{"role": "user", "content": f"Here are {len(hypotheses)} hypotheses to review:\n\n" + "\n".join(lines)}],
    )

    actions = _parse_compaction_response(response)
    removed = 0

    for action in actions:
        try:
            if action.get("action") == "merge":
                keep_id = action["keep_id"]
                keeper = db.query(HypothesisMemory).filter(HypothesisMemory.id == keep_id).first()
                if keeper:
                    if action.get("merged_short"):
                        keeper.short_summary = action["merged_short"]
                    if action.get("merged_long"):
                        keeper.long_summary = action["merged_long"]
                    keeper.evidence_for = action.get("combined_evidence_for", keeper.evidence_for)
                    keeper.evidence_against = action.get("combined_evidence_against", keeper.evidence_against)
                    total = keeper.evidence_for + keeper.evidence_against
                    keeper.confidence = round(keeper.evidence_for / total, 2) if total > 0 else 0.3
                    keeper.updated_at = datetime.now(timezone.utc)

                for rid in action.get("remove_ids", []):
                    victim = db.query(HypothesisMemory).filter(HypothesisMemory.id == rid).first()
                    if victim:
                        victim.status = "superseded"
                        victim.superseded_by_id = keep_id
                        removed += 1

            elif action.get("action") == "supersede":
                loser = db.query(HypothesisMemory).filter(HypothesisMemory.id == action["loser_id"]).first()
                if loser:
                    loser.status = "superseded"
                    loser.superseded_by_id = action.get("winner_id")
                    removed += 1

            elif action.get("action") == "drop":
                victim = db.query(HypothesisMemory).filter(HypothesisMemory.id == action["id"]).first()
                if victim:
                    victim.status = "superseded"
                    removed += 1
        except Exception as e:
            logger.warning(f"Hypothesis compaction action failed: {e}")

    db.commit()
    logger.info(f"Hypothesis compaction: {removed} hypotheses removed/merged from {len(hypotheses)}")
    return removed


def _parse_compaction_response(response) -> list:
    """Extract JSON array of actions from LLM response."""
    for block in response.content:
        if block.type == "text":
            text = block.text.strip()
            # Try to find JSON array in the response
            try:
                # Direct parse
                return json.loads(text)
            except json.JSONDecodeError:
                pass
            # Try extracting from markdown code block
            import re
            match = re.search(r'```(?:json)?\s*(\[.*?\])\s*```', text, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group(1))
                except json.JSONDecodeError:
                    pass
            # Try finding array in text
            match = re.search(r'\[.*\]', text, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group(0))
                except json.JSONDecodeError:
                    pass
    logger.warning("Could not parse compaction response as JSON array")
    return []
