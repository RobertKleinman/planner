"""
modules/task.py — Task Module
================================
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
    data = intent_data.get("data", {})
    action = data.get("action", "create")
    if action == "complete":
        return await _complete_tasks(user, raw_input, intent_data, db, input_type)
    else:
        return await _create_tasks(user, raw_input, intent_data, db, input_type, image_description)


async def _create_tasks(user, raw_input, intent_data, db, input_type, image_description=None):
    data = intent_data.get("data", {})
    tasks_data = data.get("tasks", [])
    default_group = data.get("group", "General")
    default_priority = data.get("priority", "keep_in_mind")

    if not tasks_data and data.get("description"):
        tasks_data = [{"description": data["description"], "group": data.get("group", default_group), "priority": data.get("priority", default_priority), "due": data.get("due")}]

    existing_groups = [g[0] for g in db.query(Task.group).join(Entry).filter(Entry.user_id == user.id).distinct().all() if g[0]]

    created_tasks = []
    first_entry_id = None

    for task_data in tasks_data:
        group = task_data.get("group", default_group)
        for existing in existing_groups:
            if existing.lower() == group.lower():
                group = existing
                break

        entry = Entry(
            user_id=user.id, input_type=input_type,
            raw_transcript=raw_input if input_type != "image" else None,
            raw_image_description=image_description,
            processed_content=task_data.get("description", ""),
            title=task_data.get("description", "Task"),
            module="task", module_data=json.dumps(task_data),
        )
        db.add(entry)
        db.commit()
        db.refresh(entry)
        if first_entry_id is None:
            first_entry_id = entry.id

        due_date = None
        if task_data.get("due"):
            try: due_date = datetime.fromisoformat(task_data["due"])
            except: pass

        task = Task(entry_id=entry.id, description=task_data.get("description", "Task"), group=group, priority=task_data.get("priority", default_priority), due_date=due_date, status="open")
        db.add(task)
        db.commit()
        created_tasks.append(task)
        if group not in existing_groups:
            existing_groups.append(group)

    if len(created_tasks) == 1:
        t = created_tasks[0]
        response = f"Added task: {t.description} [{t.priority.replace('_',' ').title()}] under {t.group}."
    else:
        groups = set(t.group for t in created_tasks)
        response = f"Added {len(created_tasks)} tasks under {', '.join(sorted(groups))}."

    return InputResponse(spoken_response=intent_data.get("spoken_response", response), entry_id=first_entry_id or 0, module="task")


async def _match_tasks_with_llm(raw_input, open_tasks):
    task_list = "\n".join(f"  ID {t.id}: {t.description} [{t.group}]" for t in open_tasks)
    response = client.messages.create(
        model=settings.intent_model, max_tokens=512,
        system="You are a task matching assistant. Given what the user said and their open tasks, determine which tasks they completed. Respond with ONLY valid JSON — no markdown, no backticks.",
        messages=[{"role": "user", "content": f'The user said: "{raw_input}"\n\nTheir open tasks are:\n{task_list}\n\nWhich tasks did they complete? Return: {{"matched_ids": [list of IDs], "explanation": "reason"}}\n\nRules:\n- Be generous with matching.\n- If unsure but reasonable, include it.\n- If nothing matches, return empty list.\n- Only return IDs from the list above.'}],
    )
    try:
        result = json.loads(response.content[0].text.strip())
        return result.get("matched_ids", [])
    except:
        return []


async def _complete_tasks(user, raw_input, intent_data, db, input_type):
    open_tasks = db.query(Task).join(Entry).filter(Entry.user_id == user.id, Task.status == "open").all()
    matched = []

    if open_tasks:
        matched_ids = await _match_tasks_with_llm(raw_input, open_tasks)
        for task in open_tasks:
            if task.id in matched_ids:
                task.status = "done"
                task.completed_at = datetime.now(timezone.utc)
                matched.append(task)
        db.commit()

    entry = Entry(user_id=user.id, input_type=input_type, raw_transcript=raw_input, processed_content=f"Completed {len(matched)} task(s)", title="Tasks completed", module="task", module_data=json.dumps({"action": "complete", "completed": [t.description for t in matched]}))
    db.add(entry)
    db.commit()
    db.refresh(entry)

    if matched:
        response = f"Done! Marked as complete: {', '.join(t.description for t in matched)}."
    else:
        response = "I couldn't find a matching open task. Could you be more specific?"

    return InputResponse(spoken_response=intent_data.get("spoken_response", response), entry_id=entry.id, module="task")
