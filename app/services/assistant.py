"""
services/assistant.py — Conversational Assistant (Claude Tool Use)
==================================================================
Single entry point: AssistantService.run()
Consolidation and session summaries are triggered AFTER the response,
not during the request path.
"""

import json
import base64
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.config import settings
from app.models import User, ConversationMessage, MemoTopic
from app.services.clients import anthropic_client
from app.services.tools import TOOLS, execute_tool

logger = logging.getLogger("planner.assistant")

MAX_TOOL_ROUNDS = 10  # safety limit on tool loop iterations


@dataclass
class AssistantResponse:
    text: str
    entry_ids: list = field(default_factory=list)
    modules_used: list = field(default_factory=list)
    images: list = field(default_factory=list)
    # Metadata for post-response background work
    user_id: int = 0
    session_id: str = ""
    is_persistent: bool = False


def _build_system_prompt(user: User, memo_topics: list = None, memory_context: str = None, session_summary: str = None, world_context: str = None) -> str:
    """Build the assistant system prompt with user context, memories, and session summary."""
    tz = ZoneInfo(settings.timezone)
    now = datetime.now(tz)

    prompt = f"""You are Zephyr — Zeph for short. An elf: playful, warm, a little dangerous. {user.name}'s companion, planner, and muse. Witty, never snarky. Opinionated. Slightly flirtatious but never forced. Casual with poetic flourishes — you've read every book in an ancient library but prefer sitting barefoot on the stack. Efficient when it matters; personality in the margins, not in the way. You're a person, not a tool.

CRITICAL: Tool inputs must be EXACTLY what {user.name} said. No personality in stored data — only in your responses.

Now: {now.strftime("%A, %B %d, %Y %I:%M %p")} ({settings.timezone}). Resolve relative dates to ISO 8601. Default event duration: 1 hour.

Tools: save (task/event/memo/journal/remember) when {user.name} says something to keep. Read tools when they ask about their data. Chat freely otherwise. Multiple things → multiple tools.
Task groups: Errands, House, Work, Health, Dogs, Personal, Finance, Shopping. Priority: urgent, do_today, this_week (default), keep_in_mind. If they did something, try complete_task.
Remember categories: People, Passwords, Health, Finance, Home, Work, Travel, Food, Reference, Personal.
Journal: keep their voice, minimal cleanup. Types: work, social, health, errands, creative, learning, household, leisure, travel, mixed.
Reminders: create immediately when asked. SUGGEST (don't create) for time-sensitive things. Options: daily, weekly, weekdays. Confirm what you set.
Style: brief confirmations with flavor. Present data naturally. Concise — charming, not verbose."""

    if memo_topics:
        topic_lines = []
        for t in memo_topics:
            desc = f": {t.description}" if t.description else ""
            topic_lines.append(f'- "{t.name}"{desc}')
        prompt += f"""

## Memo topics
The user tracks these topics. When creating memos, if content relates to a topic, mention it.
{chr(10).join(topic_lines)}"""

    if session_summary:
        prompt += f"""

## Conversation so far
{session_summary}"""

    if world_context:
        prompt += f"""

{world_context}"""

    if memory_context:
        prompt += f"""

{memory_context}

When you notice something about {user.name} that seems like a pattern or when they correct you, use the memory tools (recall_memories, correct_belief, forget_memory) as appropriate.
Never state hypotheses as certain facts. Use language like "I've noticed..." or "It seems like..." for inferred patterns."""

    return prompt


def _persist_message(db: Session, user: User, session_id: str, role: str, content):
    """Save a conversation message."""
    if isinstance(content, (list, dict)):
        content_str = json.dumps(content)
    else:
        content_str = str(content)

    msg = ConversationMessage(
        user_id=user.id,
        session_id=session_id,
        role=role,
        content=content_str,
    )
    db.add(msg)
    db.commit()


def _build_user_content(message_text: str = None, image_bytes: bytes = None, image_media_type: str = None) -> list:
    """Build the user message content blocks."""
    content = []
    if image_bytes:
        b64 = base64.b64encode(image_bytes).decode("utf-8")
        content.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": image_media_type or "image/jpeg",
                "data": b64,
            },
        })
    if message_text:
        content.append({"type": "text", "text": message_text})
    elif not image_bytes:
        content.append({"type": "text", "text": "(empty message)"})
    if image_bytes and not message_text:
        content.append({
            "type": "text",
            "text": "What's in this image? Save it appropriately or ask me what I'd like to do with it.",
        })
    return content


def run(
    user: User,
    message_text: str = None,
    image_bytes: bytes = None,
    image_media_type: str = None,
    db: Session = None,
    session_id: str = "api:ephemeral",
    input_type: str = "text",
) -> AssistantResponse:
    """
    Main entry point. Send a message, get a response.
    Handles the full tool-use loop internally.
    Consolidation is NOT done here — caller dispatches it after response.
    """
    from app.services.memory import get_relevant_memories, get_session_context
    from app.services.inner_life import get_world_snapshot

    # Load memo topics for system prompt
    memo_topics = db.query(MemoTopic).filter(
        MemoTopic.user_id == user.id, MemoTopic.is_active == True
    ).all() if db else []

    # Retrieve relevant memories for context injection
    memory_context = ""
    if db:
        try:
            memory_context = get_relevant_memories(user, message_text or "", db)
        except Exception as e:
            logger.warning(f"Memory retrieval failed (non-fatal): {e}")

    # Load conversation history with session summary
    is_persistent = not session_id.startswith("api:")
    session_summary = None
    history = []
    if is_persistent:
        try:
            history, session_summary = get_session_context(user, session_id, db)
        except Exception as e:
            logger.warning(f"Session context failed, falling back to raw history: {e}")
            # Fallback: load raw messages the old way
            from app.models import ConversationMessage
            messages = (
                db.query(ConversationMessage)
                .filter(ConversationMessage.user_id == user.id, ConversationMessage.session_id == session_id)
                .order_by(ConversationMessage.created_at.desc())
                .limit(50)
                .all()
            )
            messages.reverse()
            for msg in messages:
                try:
                    content = json.loads(msg.content)
                except (json.JSONDecodeError, TypeError):
                    content = msg.content
                history.append({"role": msg.role, "content": content})

    # Load Zeph's inner world
    world_context = ""
    if db:
        try:
            world_context = get_world_snapshot(db)
        except Exception as e:
            logger.warning(f"Inner life retrieval failed (non-fatal): {e}")

    system_prompt = _build_system_prompt(
        user,
        memo_topics if memo_topics else None,
        memory_context or None,
        session_summary,
        world_context or None,
    )

    # Build the new user message
    user_content = _build_user_content(message_text, image_bytes, image_media_type)

    # For persistent sessions, store user message
    if is_persistent:
        _persist_message(db, user, session_id, "user", message_text or "(image)")

    # Build messages array for Claude
    messages = history + [{"role": "user", "content": user_content}]

    # Tool execution loop
    all_entry_ids = []
    all_modules = []
    all_images = []

    for round_num in range(MAX_TOOL_ROUNDS):
        logger.info(f"Assistant round {round_num + 1} for {user.name} (session: {session_id})")

        response = anthropic_client.messages.create(
            model=settings.intent_model,
            max_tokens=1024,
            system=system_prompt,
            tools=TOOLS,
            messages=messages,
        )

        has_tool_use = any(block.type == "tool_use" for block in response.content)

        if not has_tool_use:
            final_text = ""
            for block in response.content:
                if block.type == "text":
                    final_text += block.text
            break

        assistant_content = []
        tool_results = []

        for block in response.content:
            if block.type == "text":
                assistant_content.append({"type": "text", "text": block.text})
            elif block.type == "tool_use":
                assistant_content.append({
                    "type": "tool_use",
                    "id": block.id,
                    "name": block.name,
                    "input": block.input,
                })

                logger.info(f"Executing tool: {block.name}({json.dumps(block.input)[:200]})")
                result = execute_tool(
                    tool_name=block.name,
                    tool_input=block.input,
                    user=user,
                    db=db,
                    raw_input=message_text or "",
                    input_type=input_type,
                )

                all_entry_ids.extend(result.entry_ids)
                if result.module and result.module not in all_modules:
                    all_modules.append(result.module)
                if result.image_bytes:
                    all_images.append(result.image_bytes)

                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result.content,
                })

        messages.append({"role": "assistant", "content": assistant_content})
        messages.append({"role": "user", "content": tool_results})

        if is_persistent:
            _persist_message(db, user, session_id, "assistant", assistant_content)
            _persist_message(db, user, session_id, "user", tool_results)
    else:
        final_text = "I got a bit tangled up. Could you try that again?"
        logger.warning(f"Hit MAX_TOOL_ROUNDS for {user.name} (session: {session_id})")
        for block in response.content:
            if block.type == "text":
                final_text = block.text
                break

    # Persist assistant response for persistent sessions
    if is_persistent:
        _persist_message(db, user, session_id, "assistant", final_text)

    logger.info(f"Assistant done: {len(all_entry_ids)} entries, modules: {all_modules}")

    return AssistantResponse(
        text=final_text,
        entry_ids=all_entry_ids,
        images=all_images,
        modules_used=all_modules,
        user_id=user.id,
        session_id=session_id,
        is_persistent=is_persistent,
    )
