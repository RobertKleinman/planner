"""
services/tools.py — Claude Tool Definitions + Execution
=========================================================
6 write tools (thin adapters over existing handlers) + 4 read tools (DB queries).
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from sqlalchemy.orm import Session
from sqlalchemy import or_

from app.models import (
    User, Entry, Task, CalendarEvent, RememberItem, JournalEntry, MemoTopic,
)
from app.config import settings
from app.modules.memo import handle_memo
from app.modules.task import handle_task
from app.modules.calendar import handle_calendar
from app.modules.remember import handle_remember
from app.modules.journal import handle_journal

logger = logging.getLogger("planner.tools")


@dataclass
class ToolResult:
    content: str
    entry_ids: list = field(default_factory=list)
    module: str = ""


# ─── Tool Definitions (Anthropic format) ─────────────────────

TOOLS = [
    # ── Write tools ──
    {
        "name": "create_memo",
        "description": "Save a note, thought, observation, idea, expense, workout log, or any freeform text the user wants to keep. Use this when the user says something worth saving that doesn't fit a more specific tool.",
        "input_schema": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "The memo content to save"},
                "title": {"type": "string", "description": "Short title for the memo (optional)"},
            },
            "required": ["content"],
        },
    },
    {
        "name": "create_task",
        "description": "Create one or more tasks/to-dos for the user. Use when they mention something they need to do.",
        "input_schema": {
            "type": "object",
            "properties": {
                "tasks": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "description": {"type": "string", "description": "What needs to be done"},
                            "group": {"type": "string", "description": "Category: Errands, House, Work, Health, Dogs, Personal, Finance, Shopping, etc."},
                            "priority": {"type": "string", "enum": ["urgent", "do_today", "this_week", "keep_in_mind"], "description": "Priority level (default: this_week)"},
                            "due_date": {"type": "string", "description": "ISO 8601 due date if mentioned"},
                        },
                        "required": ["description"],
                    },
                    "description": "List of tasks to create",
                },
            },
            "required": ["tasks"],
        },
    },
    {
        "name": "complete_task",
        "description": "Mark one or more tasks as done. Use when the user says they finished or completed something.",
        "input_schema": {
            "type": "object",
            "properties": {
                "descriptions": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Descriptions of what was completed — will be fuzzy-matched against open tasks",
                },
            },
            "required": ["descriptions"],
        },
    },
    {
        "name": "create_calendar_event",
        "description": "Create a calendar event with a specific date and time. Use when the user mentions scheduling something.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Event title"},
                "start": {"type": "string", "description": "ISO 8601 start datetime"},
                "end": {"type": "string", "description": "ISO 8601 end datetime (default: 1 hour after start)"},
                "location": {"type": "string", "description": "Event location if mentioned"},
                "notify_contacts": {"type": "boolean", "description": "Whether to SMS notification contacts (default: false)"},
            },
            "required": ["title", "start"],
        },
    },
    {
        "name": "create_journal_entry",
        "description": "Log what the user did — activities, experiences, how they spent their time. Use when they're reporting on something they already did (past tense).",
        "input_schema": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "What they did, with minimal grammar cleanup"},
                "activity_type": {"type": "string", "enum": ["work", "social", "health", "errands", "creative", "learning", "household", "leisure", "travel", "mixed"], "description": "Type of activity"},
                "tags": {"type": "array", "items": {"type": "string"}, "description": "2-5 keyword tags"},
            },
            "required": ["content"],
        },
    },
    {
        "name": "remember",
        "description": "Store facts the user wants to recall later: passwords, preferences, people's info, references. Use when they say 'remember that', 'the password is', 'don't forget', etc.",
        "input_schema": {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "content": {"type": "string", "description": "The fact to remember"},
                            "category": {"type": "string", "description": "Category: People, Passwords, Health, Finance, Home, Work, Travel, Food, Reference, Personal"},
                            "tags": {"type": "array", "items": {"type": "string"}, "description": "Keyword tags"},
                        },
                        "required": ["content"],
                    },
                    "description": "Items to remember",
                },
            },
            "required": ["items"],
        },
    },
    # ── Read tools ──
    {
        "name": "get_tasks",
        "description": "Get the user's tasks. Use when they ask about their to-do list, what they need to do, or task status.",
        "input_schema": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": ["open", "done", "all"], "description": "Filter by status (default: open)"},
                "group": {"type": "string", "description": "Filter by group/category"},
            },
        },
    },
    {
        "name": "get_calendar_events",
        "description": "Get upcoming calendar events. Use when the user asks about their schedule, what's coming up, or calendar.",
        "input_schema": {
            "type": "object",
            "properties": {
                "days_ahead": {"type": "integer", "description": "How many days ahead to look (default: 7)"},
            },
        },
    },
    {
        "name": "search",
        "description": "Search across the user's entries (memos, tasks, calendar, journal, remember items). Use when the user asks about something they previously saved.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "module": {"type": "string", "enum": ["memo", "task", "calendar", "journal", "remember"], "description": "Limit search to a specific module"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_remember_items",
        "description": "Look up facts the user previously saved with the remember tool. Use when they ask 'what was that password?', 'do you remember...', etc.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search within remembered items"},
                "category": {"type": "string", "description": "Filter by category"},
            },
        },
    },
]


# ─── Tool Execution ──────────────────────────────────────────

def execute_tool(
    tool_name: str,
    tool_input: dict,
    user: User,
    db: Session,
    raw_input: str = "",
    input_type: str = "text",
) -> ToolResult:
    """Dispatch a tool call to the right handler/query."""
    try:
        if tool_name == "create_memo":
            return _exec_create_memo(tool_input, user, db, raw_input, input_type)
        elif tool_name == "create_task":
            return _exec_create_task(tool_input, user, db, raw_input, input_type)
        elif tool_name == "complete_task":
            return _exec_complete_task(tool_input, user, db, raw_input, input_type)
        elif tool_name == "create_calendar_event":
            return _exec_create_calendar_event(tool_input, user, db, raw_input, input_type)
        elif tool_name == "create_journal_entry":
            return _exec_create_journal_entry(tool_input, user, db, raw_input, input_type)
        elif tool_name == "remember":
            return _exec_remember(tool_input, user, db, raw_input, input_type)
        elif tool_name == "get_tasks":
            return _exec_get_tasks(tool_input, user, db)
        elif tool_name == "get_calendar_events":
            return _exec_get_calendar_events(tool_input, user, db)
        elif tool_name == "search":
            return _exec_search(tool_input, user, db)
        elif tool_name == "get_remember_items":
            return _exec_get_remember_items(tool_input, user, db)
        else:
            return ToolResult(content=f"Unknown tool: {tool_name}")
    except Exception as e:
        logger.error(f"Tool execution error ({tool_name}): {e}", exc_info=True)
        return ToolResult(content=f"Error executing {tool_name}: {e}")


# ─── Write Tool Executors ────────────────────────────────────

def _exec_create_memo(tool_input, user, db, raw_input, input_type):
    intent_data = {
        "module": "memo",
        "title": tool_input.get("title", tool_input["content"][:80]),
        "spoken_response": "",
        "data": {"content": tool_input["content"]},
    }
    resp = handle_memo(user, raw_input, intent_data, db, input_type)
    return ToolResult(
        content=f"Memo saved (ID: {resp.entry_id}): {tool_input['content'][:100]}",
        entry_ids=[resp.entry_id],
        module="memo",
    )


def _exec_create_task(tool_input, user, db, raw_input, input_type):
    tasks = tool_input.get("tasks", [])
    intent_data = {
        "module": "task",
        "title": "Tasks",
        "spoken_response": "",
        "data": {
            "action": "create",
            "tasks": [
                {
                    "description": t["description"],
                    "group": t.get("group", "General"),
                    "priority": t.get("priority", "this_week"),
                    "due": t.get("due_date"),
                }
                for t in tasks
            ],
        },
    }
    resp = handle_task(user, raw_input, intent_data, db, input_type)
    descs = ", ".join(t["description"] for t in tasks)
    return ToolResult(
        content=f"Created {len(tasks)} task(s): {descs}",
        entry_ids=[resp.entry_id] if resp.entry_id else [],
        module="task",
    )


def _exec_complete_task(tool_input, user, db, raw_input, input_type):
    descriptions = tool_input.get("descriptions", [])
    combined = ", ".join(descriptions)
    intent_data = {
        "module": "task",
        "title": "Complete tasks",
        "spoken_response": "",
        "data": {"action": "complete", "completed": descriptions},
    }
    resp = handle_task(user, combined, intent_data, db, input_type)
    return ToolResult(
        content=resp.spoken_response or f"Attempted to complete: {combined}",
        entry_ids=[resp.entry_id] if resp.entry_id else [],
        module="task",
    )


def _exec_create_calendar_event(tool_input, user, db, raw_input, input_type):
    notify = tool_input.get("notify_contacts", False)
    intent_data = {
        "module": "calendar",
        "title": tool_input["title"],
        "spoken_response": "",
        "data": {
            "title": tool_input["title"],
            "start": tool_input["start"],
            "end": tool_input.get("end"),
            "location": tool_input.get("location"),
            "notify_partner": notify,
        },
    }
    resp = handle_calendar(user, raw_input, intent_data, db, input_type)
    return ToolResult(
        content=f"Calendar event created: {tool_input['title']} at {tool_input['start']}",
        entry_ids=[resp.entry_id] if resp.entry_id else [],
        module="calendar",
    )


def _exec_create_journal_entry(tool_input, user, db, raw_input, input_type):
    intent_data = {
        "module": "journal",
        "title": tool_input["content"][:80],
        "spoken_response": "",
        "data": {
            "content": tool_input["content"],
            "activity_type": tool_input.get("activity_type", "mixed"),
            "tags": tool_input.get("tags", []),
        },
    }
    resp = handle_journal(user, raw_input, intent_data, db, input_type)
    return ToolResult(
        content=f"Journal entry logged: {tool_input['content'][:100]}",
        entry_ids=[resp.entry_id] if resp.entry_id else [],
        module="journal",
    )


def _exec_remember(tool_input, user, db, raw_input, input_type):
    items = tool_input.get("items", [])
    intent_data = {
        "module": "remember",
        "title": "Remember",
        "spoken_response": "",
        "data": {
            "items": [
                {
                    "content": item["content"],
                    "category": item.get("category", "General"),
                    "tags": item.get("tags", []),
                }
                for item in items
            ],
        },
    }
    resp = handle_remember(user, raw_input, intent_data, db, input_type)
    return ToolResult(
        content=f"Saved {len(items)} item(s) to remember",
        entry_ids=[resp.entry_id] if resp.entry_id else [],
        module="remember",
    )


# ─── Read Tool Executors ─────────────────────────────────────

def _exec_get_tasks(tool_input, user, db):
    status_filter = tool_input.get("status", "open")
    group_filter = tool_input.get("group")

    query = db.query(Task).join(Entry).filter(
        Entry.user_id == user.id,
        Entry.deleted_at.is_(None),
    )
    if status_filter and status_filter != "all":
        query = query.filter(Task.status == status_filter)
    if group_filter:
        query = query.filter(Task.group.ilike(group_filter))

    tasks = query.order_by(Task.created_at.desc()).limit(50).all()

    if not tasks:
        return ToolResult(content=f"No {status_filter} tasks found.", module="task")

    lines = []
    for t in tasks:
        parts = [f"- {t.description}"]
        parts.append(f"[{t.priority.replace('_', ' ')}]")
        parts.append(f"({t.group})")
        if t.due_date:
            parts.append(f"due {t.due_date.strftime('%Y-%m-%d')}")
        if t.status == "done":
            parts.append("✓ done")
        lines.append(" ".join(parts))

    return ToolResult(content=f"{len(tasks)} task(s):\n" + "\n".join(lines), module="task")


def _exec_get_calendar_events(tool_input, user, db):
    days = tool_input.get("days_ahead", 7)
    tz = ZoneInfo(settings.timezone)
    now = datetime.now(tz)
    cutoff = now + timedelta(days=days)

    events = (
        db.query(CalendarEvent)
        .join(Entry)
        .filter(
            Entry.user_id == user.id,
            Entry.deleted_at.is_(None),
            CalendarEvent.start_time >= now.astimezone(timezone.utc).replace(tzinfo=None),
            CalendarEvent.start_time <= cutoff.astimezone(timezone.utc).replace(tzinfo=None),
        )
        .order_by(CalendarEvent.start_time)
        .limit(30)
        .all()
    )

    if not events:
        return ToolResult(content=f"No events in the next {days} days.", module="calendar")

    lines = []
    for e in events:
        start_local = e.start_time.replace(tzinfo=timezone.utc).astimezone(tz)
        line = f"- {e.title} — {start_local.strftime('%a %b %d, %I:%M %p')}"
        if e.location:
            line += f" @ {e.location}"
        lines.append(line)

    return ToolResult(content=f"{len(events)} upcoming event(s):\n" + "\n".join(lines), module="calendar")


def _exec_search(tool_input, user, db):
    query_str = tool_input.get("query", "")
    module_filter = tool_input.get("module")

    q = db.query(Entry).filter(
        Entry.user_id == user.id,
        Entry.deleted_at.is_(None),
    )
    if module_filter:
        q = q.filter(Entry.module == module_filter)

    pattern = f"%{query_str}%"
    q = q.filter(
        or_(
            Entry.processed_content.ilike(pattern),
            Entry.title.ilike(pattern),
            Entry.raw_transcript.ilike(pattern),
        )
    )

    results = q.order_by(Entry.created_at.desc()).limit(20).all()

    if not results:
        return ToolResult(content=f"No results for '{query_str}'.")

    lines = []
    for r in results:
        date = r.created_at.strftime("%Y-%m-%d") if r.created_at else ""
        snippet = (r.processed_content or r.title or "")[:100]
        lines.append(f"- [{r.module}] {date}: {snippet}")

    return ToolResult(content=f"{len(results)} result(s) for '{query_str}':\n" + "\n".join(lines))


def _exec_get_remember_items(tool_input, user, db):
    query_str = tool_input.get("query")
    category_filter = tool_input.get("category")

    q = db.query(RememberItem).join(Entry).filter(
        Entry.user_id == user.id,
        Entry.deleted_at.is_(None),
    )
    if category_filter:
        q = q.filter(RememberItem.category.ilike(category_filter))
    if query_str:
        pattern = f"%{query_str}%"
        q = q.filter(
            or_(
                RememberItem.content.ilike(pattern),
                RememberItem.tags.ilike(pattern),
            )
        )

    items = q.order_by(RememberItem.created_at.desc()).limit(20).all()

    if not items:
        msg = f"No remember items found"
        if query_str:
            msg += f" matching '{query_str}'"
        return ToolResult(content=msg + ".", module="remember")

    lines = []
    for item in items:
        line = f"- [{item.category}] {item.content}"
        if item.tags:
            line += f" (tags: {item.tags})"
        lines.append(line)

    return ToolResult(content=f"{len(items)} remembered item(s):\n" + "\n".join(lines), module="remember")
