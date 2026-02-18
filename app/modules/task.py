"""
modules/task.py — Task Module
================================
Handles creating, completing, and organizing tasks.
Supports:
- Creating one or multiple tasks from a single voice memo
- Auto-grouping tasks into categories
- Priority levels: urgent, do_today, this_week, keep_in_mind
- Due dates when mentioned
- Completing tasks via LLM-powered fuzzy matching
"""

import json
from datetime import datetime, timezone
from anthropic import Anthropic
from sqlalchemy.orm import Session
from app.models import User, Entry, Task
from app.schemas import InputResponse
from app.config import settings

client = Anthropic(api_key=settings.anthropic_api_key)


async def handle_task(
    user: User,
    raw_input: str,
    intent_data: dict,
    db: Session,
    input_type: str = "audio",
    image_description: str = None,
) -> InputResponse:
    """
    Process task input: create new tasks or complete existing ones.
    """
    data = intent_data.get("data", {})
    action = data.get("action", "create")

    if action == "complete":
        return await _complete_tasks(user, raw_input, intent_data, db, input_type)
    else:
        return await _create_tasks(user, raw_input, intent_data, db, input_type, image_description)


async def _create_tasks(
    user: User,
    raw_input: str,
    intent_data: dict,
    db: Session,
    input_type: str,
    image_description: str = None,
) -> InputResponse:
    """Create one or more tasks from voice input."""
    data = intent_data.get("data", {})
    tasks_data = data.get("tasks", [])
    default_group = data.get("group", "General")
    default_priority = data.get("priority", "keep_in_mind")

    # If classifier returned a single task instead of a list
    if not tasks_data and data.get("description"):
        tasks_data = [{
            "description": data["description"],
            "group": data.get("group", default_group),
            "priority": data.get("priority", default_priority),
            "due": data.get("due"),
        }]

    # Get existing groups for this user to reuse consistent naming
    existing_groups = (
        db.query(Task.group)
        .join(Entry)
        .filter(Entry.user_id == user.id)
        .distinct()
        .all()
    )
    existing_group_names = [g[0] for g in existing_groups if g[0]]

    created_tasks = []
    first_entry_id = None

    for task_data in tasks_data:
        # Resolve group — reuse existing group name if case-insensitive match
        group = task_data.get("group", default_group)
        for existing in existing_group_names:
            if existing.lower() == group.lower():
                group = existing
                break

        # Save entry
        entry = Entry(
            user_id=user.id,
            input_type=input_type,
            raw_transcript=raw_input if input_type != "image" else None,
            raw_image_description=image_description,
            processed_content=task_data.get("description", ""),
            title=task_data.get("description", "Task"),
            module="task",
            module_data=json.dumps(task_data),
        )
        db.add(entry)
        db.commit()
        db.refresh(entry)

        if first_entry_id is None:
            first_entry_id = entry.id

        # Parse due date if present
        due_date = None
        if task_data.get("due"):
            try:
                due_date = datetime.fromisoformat(task_data["due"])
            except (ValueError, TypeError):
                pass

        # Save task record
        task = Task(
            entry_id=entry.id,
            description=task_data.get("description", "Task"),
            group=group,
            priority=task_data.get("priority", default_priority),
            due_date=due_date,
            status="open",
        )
        db.add(task)
        db.commit()
        created_tasks.append(task)

        # Add to known groups for subsequent tasks in same batch
        if group not in existing_group_names:
            existing_group_names.append(group)

    # Build response
    if len(created_tasks) == 1:
        t = created_tasks[0]
        priority_label = t.priority.replace("_", " ").title()
        response = f"Added task: {t.description} [{priority_label}] under {t.group}."
    else:
        groups = set(t.group for t in created_tasks)
        response = f"Added {len(created_tasks)} tasks under {', '.join(sorted(groups))}."

    return InputResponse(
        spoken_response=intent_data.get("spoken_response", response),
        entry_id=first_entry_id or 0,
        module="task",
    )


async def _match_tasks_with_llm(raw_input: str, open_tasks: list) -> list:
    """
    Use Claude to match the user's completion description against open tasks.
    Returns a list of task IDs that should be marked complete.
    """
    task_list = "\n".join(
        f"  ID {task.id}: {task.description} [{task.group}]"
        for task in open_tasks
    )

    response = client.messages.create(
        model=settings.intent_model,
        max_tokens=512,
        system="You are a task matching assistant. Given what the user said and their open tasks, determine which tasks they completed. Respond with ONLY valid JSON — no markdown, no backticks.",
        messages=[{
            "role": "user",
            "content": f"""The user said: "{raw_input}"

Their open tasks are:
{task_list}

Which tasks did they complete? Return a JSON object:
{{"matched_ids": [list of task IDs that match], "explanation": "brief reason"}}

Rules:
- Be generous with matching. "did the laundry thing" matches "wash and fold clothes".
- "picked up the stuff from the cleaners" matches "pick up dry cleaning".
- If unsure but it's a reasonable match, include it.
- If nothing matches, return an empty list.
- Only return IDs from the list above."""
        }],
    )

    raw_text = response.content[0].text.strip()

    try:
        result = json.loads(raw_text)
        return result.get("matched_ids", [])
    except json.JSONDecodeError:
        return []


async def _complete_tasks(
    user: User,
    raw_input: str,
    intent_data: dict,
    db: Session,
    input_type: str,
) -> InputResponse:
    """Mark one or more tasks as complete using LLM matching."""
    # Get all open tasks for this user
    open_tasks = (
        db.query(Task)
        .join(Entry)
        .filter(Entry.user_id == user.id, Task.status == "open")
        .all()
    )

    matched = []

    if open_tasks:
        # Use Claude to figure out which tasks were completed
        matched_ids = await _match_tasks_with_llm(raw_input, open_tasks)

        for task in open_tasks:
            if task.id in matched_ids:
                task.status = "done"
                task.completed_at = datetime.now(timezone.utc)
                matched.append(task)

        db.commit()

    # Save an entry for the completion action
    entry = Entry(
        user_id=user.id,
        input_type=input_type,
        raw_transcript=raw_input,
        processed_content=f"Completed {len(matched)} task(s)",
        title="Tasks completed",
        module="task",
        module_data=json.dumps({"action": "complete", "completed": [t.description for t in matched]}),
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)

    if matched:
        names = ", ".join(t.description for t in matched)
        response = f"Done! Marked as complete: {names}."
    else:
        response = "I couldn't find a matching open task. Could you be more specific?"

    return InputResponse(
        spoken_response=intent_data.get("spoken_response", response),
        entry_id=entry.id,
        module="task",
    )
