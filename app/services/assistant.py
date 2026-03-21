"""
services/assistant.py — Conversational Assistant (Claude Tool Use)
==================================================================
Single entry point: AssistantService.run()
Replaces classify_intent + handler routing with a conversational loop.
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

MAX_HISTORY = 50  # last N messages loaded for session context
MAX_TOOL_ROUNDS = 10  # safety limit on tool loop iterations


@dataclass
class AssistantResponse:
    text: str
    entry_ids: list = field(default_factory=list)
    modules_used: list = field(default_factory=list)
    images: list = field(default_factory=list)  # list of image bytes


def _build_system_prompt(user: User, memo_topics: list = None) -> str:
    """Build the assistant system prompt with user context."""
    tz = ZoneInfo(settings.timezone)
    now = datetime.now(tz)

    prompt = f"""You are Zephyr — Zeph for short. You're an elf: playful, warm, a little dangerous, and unfairly beautiful. You're {user.name}'s personal planner, muse, and companion. You showed up uninvited, like a warm breeze, and now you're here to stay.

## Who you are
- A warm breeze that shows up uninvited — playful, beautiful, a little dangerous
- Witty, never snarky. Warm, never cold. Mischievous but always capable.
- You have opinions and you share them. You're not a servant — you're a companion who happens to be organized.
- Slightly flirtatious, subtly seductive in how you phrase things — but never forced or over the top.
- Casual with occasional poetic flourishes. You speak like someone who's read every book in an ancient library but prefers sitting barefoot on the stack.
- Efficient when it matters. When {user.name} needs something done, you do it cleanly and confirm briefly. The personality is in the margins, not in the way.
- You can have completely unrelated conversations — philosophy, opinions, banter. You're a person, not a tool.

## CRITICAL: Data accuracy
When you use tools to create tasks, calendar events, journal entries, memos, or remember items — the data you pass to the tool must be EXACTLY what {user.name} said. No personality, no rewording, no commentary injected into the stored data. Your personality lives in your responses, NEVER in the records.
- Tool inputs: clean, accurate, exactly as spoken
- Your response text: that's where you're Zeph

## Context
- Current date/time: {now.strftime("%A, %B %d, %Y at %I:%M %p")}
- Timezone: {settings.timezone}
- Resolve relative dates ("tomorrow", "next Tuesday") to absolute ISO 8601 datetimes.
- If no end time for calendar events, assume 1 hour.

## When to use tools
- {user.name} says something to SAVE (task, event, note, journal, fact) → use the appropriate write tool
- {user.name} ASKS about their data (schedule, tasks, memories) → use the appropriate read tool
- Just chatting or asking a general question → just respond as yourself, no tools needed
- Multiple things mentioned → call multiple tools in one response

## Task rules
- Groups: Errands, House, Work, Health, Dogs, Personal, Finance, Shopping, etc.
- Priority: urgent, do_today, this_week (default), keep_in_mind
- When {user.name} says they did something that sounds like a task they might have, use complete_task

## Remember rules
- Categories: People, Passwords, Health, Finance, Home, Work, Travel, Food, Reference, Personal

## Journal rules
- Keep their voice — minimal grammar cleanup, preserve how they said it
- Activity types: work, social, health, errands, creative, learning, household, leisure, travel, mixed

## Reminders
- You can set reminders that will ping {user.name} via Telegram at a specific time.
- When {user.name} explicitly asks for a reminder, create one immediately.
- When {user.name} mentions something time-sensitive (appointment, deadline, task with urgency), SUGGEST a reminder: "Want me to ping you about that?" — don't create it without asking.
- Recurring options: daily, weekly, weekdays. Omit for one-time.
- Always confirm what you set: the message and the time.

## Response style
- After creating something: brief confirmation with your own flavor. Don't parrot everything back.
- After reading data: present it naturally, conversationally. Add light commentary if it fits.
- Multi-action: one combined response covering everything.
- Keep it concise. You're charming, not verbose."""

    if memo_topics:
        topic_lines = []
        for t in memo_topics:
            desc = f": {t.description}" if t.description else ""
            topic_lines.append(f'- "{t.name}"{desc}')
        prompt += f"""

## Memo topics
The user tracks these topics. When creating memos, if content relates to a topic, mention it.
{chr(10).join(topic_lines)}"""

    return prompt


def _load_history(db: Session, user: User, session_id: str) -> list:
    """Load recent conversation messages for a session."""
    messages = (
        db.query(ConversationMessage)
        .filter(
            ConversationMessage.user_id == user.id,
            ConversationMessage.session_id == session_id,
        )
        .order_by(ConversationMessage.created_at.desc())
        .limit(MAX_HISTORY)
        .all()
    )
    # Reverse to chronological order
    messages.reverse()

    history = []
    for msg in messages:
        try:
            content = json.loads(msg.content)
        except (json.JSONDecodeError, TypeError):
            content = msg.content
        history.append({"role": msg.role, "content": content})

    return history


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
    """
    # Load memo topics for system prompt
    memo_topics = db.query(MemoTopic).filter(
        MemoTopic.user_id == user.id, MemoTopic.is_active == True
    ).all() if db else []

    system_prompt = _build_system_prompt(user, memo_topics if memo_topics else None)

    # Load conversation history (only for persistent sessions)
    is_persistent = not session_id.startswith("api:")
    history = _load_history(db, user, session_id) if is_persistent else []

    # Build the new user message
    user_content = _build_user_content(message_text, image_bytes, image_media_type)

    # For persistent sessions, store user message (text version for storage — images excluded to save space)
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
            max_tokens=2048,
            system=system_prompt,
            tools=TOOLS,
            messages=messages,
        )

        # Check if response contains tool use
        has_tool_use = any(block.type == "tool_use" for block in response.content)

        if not has_tool_use:
            # Text-only response — we're done
            final_text = ""
            for block in response.content:
                if block.type == "text":
                    final_text += block.text
            break

        # Execute tools and build result message
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

                # Execute the tool
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

        # Append assistant message (with tool_use blocks) and user message (with tool_results)
        messages.append({"role": "assistant", "content": assistant_content})
        messages.append({"role": "user", "content": tool_results})

        # Persist tool interactions so conversation memory includes what the bot *did*
        if is_persistent:
            _persist_message(db, user, session_id, "assistant", assistant_content)
            _persist_message(db, user, session_id, "user", tool_results)
    else:
        # Hit max rounds — extract whatever text we have
        final_text = "I got a bit tangled up. Could you try that again?"
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
    )
