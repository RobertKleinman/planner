"""
routers/dashboard.py — Web Dashboard
======================================
- Cookie-based auth with 90-day persistence
- Soft delete: items go to Trash, auto-purge after 10 days
- CRUD on all item types with inline editing
- Advanced search across all modules
- Quick-capture with intent classification
- Polished dark UI with mobile optimization
"""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from sqlalchemy.orm import Session
from sqlalchemy import desc, and_, or_
from datetime import datetime, timezone, timedelta
from collections import defaultdict
import json
import logging

from app.database import get_db
from app.auth import hash_api_key
from app.models import User, Entry, Task, CalendarEvent, RememberItem, JournalEntry, NotificationContact
from app.services.google_auth import get_calendar_service
from app.services.intent import classify_intent
from app.modules.memo import handle_memo
from app.modules.calendar import handle_calendar
from app.modules.task import handle_task
from app.modules.remember import handle_remember
from app.modules.journal import handle_journal
from app.services.google_calendar import create_calendar_event as create_gcal_event

logger = logging.getLogger("planner.dashboard")

router = APIRouter(tags=["dashboard"])


def _user(request: Request, db: Session) -> User:
    api_key = request.cookies.get("planner_auth")
    if not api_key:
        return None
    return db.query(User).filter(User.api_key_hash == hash_api_key(api_key), User.is_active == True).first()


def _not_deleted():
    return Entry.deleted_at.is_(None)


def _purge_old_trash(db: Session, user: User):
    """Permanently delete items trashed more than 10 days ago."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=10)
    old = db.query(Entry).filter(Entry.user_id == user.id, Entry.deleted_at < cutoff).all()
    for entry in old:
        if entry.task:
            db.delete(entry.task)
        if entry.remember_item:
            db.delete(entry.remember_item)
        if entry.journal_entry:
            db.delete(entry.journal_entry)
        if entry.calendar_event:
            db.delete(entry.calendar_event)
        db.delete(entry)
    if old:
        db.commit()


# ─── Auth ──────────────────────────────────────────────────

@router.get("/dashboard/login", response_class=HTMLResponse)
async def login_page(request: Request, db: Session = Depends(get_db)):
    if _user(request, db):
        return RedirectResponse("/dashboard", status_code=302)
    return HTMLResponse(content=LOGIN_HTML)


@router.post("/dashboard/login")
async def login(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    api_key = form.get("api_key", "").strip()
    if not api_key:
        return HTMLResponse(content=LOGIN_HTML.replace("<!--ERROR-->", '<div class="error">Please enter your API key.</div>'))
    user = db.query(User).filter(User.api_key_hash == hash_api_key(api_key), User.is_active == True).first()
    if not user:
        return HTMLResponse(content=LOGIN_HTML.replace("<!--ERROR-->", '<div class="error">Invalid API key.</div>'))
    resp = RedirectResponse("/dashboard", status_code=302)
    resp.set_cookie(key="planner_auth", value=api_key, max_age=60*60*24*90, httponly=True, samesite="lax")
    return resp


@router.get("/dashboard/logout")
async def logout():
    resp = RedirectResponse("/dashboard/login", status_code=302)
    resp.delete_cookie("planner_auth")
    return resp


# ─── CRUD: Soft Delete / Restore / Permanent Delete ───────

@router.post("/dashboard/api/trash/{entry_id}")
async def trash_item(entry_id: int, request: Request, db: Session = Depends(get_db)):
    user = _user(request, db)
    if not user:
        return JSONResponse(status_code=401, content={"error": "Not logged in"})
    entry = db.query(Entry).filter(Entry.id == entry_id, Entry.user_id == user.id).first()
    if not entry:
        return JSONResponse(status_code=404, content={"error": "Not found"})

    # If it's a calendar event, try to delete from Google Calendar too
    if entry.calendar_event and entry.calendar_event.google_event_id:
        try:
            service = get_calendar_service()
            if service:
                service.events().delete(calendarId='primary', eventId=entry.calendar_event.google_event_id).execute()
                logger.info(f"Deleted Google Calendar event: {entry.calendar_event.google_event_id}")
        except Exception as e:
            logger.warning(f"Could not delete from Google Calendar (may already be gone): {e}")

    entry.deleted_at = datetime.now(timezone.utc)
    db.commit()
    return JSONResponse(content={"ok": True})


@router.post("/dashboard/api/restore/{entry_id}")
async def restore_item(entry_id: int, request: Request, db: Session = Depends(get_db)):
    user = _user(request, db)
    if not user:
        return JSONResponse(status_code=401, content={"error": "Not logged in"})
    entry = db.query(Entry).filter(Entry.id == entry_id, Entry.user_id == user.id).first()
    if not entry:
        return JSONResponse(status_code=404, content={"error": "Not found"})
    entry.deleted_at = None
    db.commit()
    return JSONResponse(content={"ok": True})


@router.delete("/dashboard/api/permanent/{entry_id}")
async def permanent_delete(entry_id: int, request: Request, db: Session = Depends(get_db)):
    user = _user(request, db)
    if not user:
        return JSONResponse(status_code=401, content={"error": "Not logged in"})
    entry = db.query(Entry).filter(Entry.id == entry_id, Entry.user_id == user.id).first()
    if not entry:
        return JSONResponse(status_code=404, content={"error": "Not found"})
    if entry.task:
        db.delete(entry.task)
    if entry.remember_item:
        db.delete(entry.remember_item)
    if entry.journal_entry:
        db.delete(entry.journal_entry)
    if entry.calendar_event:
        db.delete(entry.calendar_event)
    db.delete(entry)
    db.commit()
    return JSONResponse(content={"ok": True})


@router.post("/dashboard/api/empty-trash")
async def empty_trash(request: Request, db: Session = Depends(get_db)):
    user = _user(request, db)
    if not user:
        return JSONResponse(status_code=401, content={"error": "Not logged in"})
    trashed = db.query(Entry).filter(Entry.user_id == user.id, Entry.deleted_at.isnot(None)).all()
    for entry in trashed:
        if entry.task: db.delete(entry.task)
        if entry.remember_item: db.delete(entry.remember_item)
        if entry.journal_entry: db.delete(entry.journal_entry)
        if entry.calendar_event: db.delete(entry.calendar_event)
        db.delete(entry)
    db.commit()
    return JSONResponse(content={"ok": True, "deleted": len(trashed)})


# ─── CRUD: Tasks ──────────────────────────────────────────

@router.post("/dashboard/api/tasks")
async def add_task(request: Request, db: Session = Depends(get_db)):
    user = _user(request, db)
    if not user:
        return JSONResponse(status_code=401, content={"error": "Not logged in"})
    body = await request.json()
    desc = body.get("description", "").strip()
    if not desc:
        return JSONResponse(status_code=400, content={"error": "Description required"})

    entry = Entry(user_id=user.id, input_type="dashboard", processed_content=desc, title=desc, module="task", module_data=json.dumps(body))
    db.add(entry)
    db.commit()
    db.refresh(entry)

    task = Task(entry_id=entry.id, description=desc, group=body.get("group", "General"), priority=body.get("priority", "this_week"), status="open")
    db.add(task)
    db.commit()
    return JSONResponse(content={"ok": True, "id": task.id})


@router.put("/dashboard/api/tasks/{task_id}")
async def edit_task(task_id: int, request: Request, db: Session = Depends(get_db)):
    user = _user(request, db)
    if not user:
        return JSONResponse(status_code=401, content={"error": "Not logged in"})

    body = await request.json()
    task = db.query(Task).join(Entry).filter(Task.id == task_id, Entry.user_id == user.id).first()
    if not task:
        return JSONResponse(status_code=404, content={"error": "Not found"})

    if "description" in body:
        desc = body.get("description", "").strip()
        if not desc:
            return JSONResponse(status_code=400, content={"error": "Description required"})
        task.description = desc
        task.entry.processed_content = desc
        task.entry.title = desc

    if "group" in body:
        group = body.get("group", "").strip()
        if group:
            task.group = group

    if "priority" in body:
        priority = body.get("priority", "").strip()
        if priority in ["urgent", "do_today", "this_week", "keep_in_mind"]:
            task.priority = priority

    db.commit()
    logger.info(f"Task {task_id} edited by user {user.id}")
    return JSONResponse(content={"ok": True})


@router.post("/dashboard/api/tasks/{task_id}/complete")
async def complete_task(task_id: int, request: Request, db: Session = Depends(get_db)):
    user = _user(request, db)
    if not user:
        return JSONResponse(status_code=401, content={"error": "Not logged in"})
    task = db.query(Task).join(Entry).filter(Task.id == task_id, Entry.user_id == user.id).first()
    if not task:
        return JSONResponse(status_code=404, content={"error": "Not found"})
    task.status = "done"
    task.completed_at = datetime.now(timezone.utc)
    db.commit()
    return JSONResponse(content={"ok": True})


@router.post("/dashboard/api/tasks/{task_id}/reopen")
async def reopen_task(task_id: int, request: Request, db: Session = Depends(get_db)):
    user = _user(request, db)
    if not user:
        return JSONResponse(status_code=401, content={"error": "Not logged in"})
    task = db.query(Task).join(Entry).filter(Task.id == task_id, Entry.user_id == user.id).first()
    if not task:
        return JSONResponse(status_code=404, content={"error": "Not found"})
    task.status = "open"
    task.completed_at = None
    db.commit()
    return JSONResponse(content={"ok": True})


# ─── CRUD: Remember ───────────────────────────────────────

@router.post("/dashboard/api/remember")
async def add_remember(request: Request, db: Session = Depends(get_db)):
    user = _user(request, db)
    if not user:
        return JSONResponse(status_code=401, content={"error": "Not logged in"})
    body = await request.json()
    content = body.get("content", "").strip()
    if not content:
        return JSONResponse(status_code=400, content={"error": "Content required"})

    entry = Entry(user_id=user.id, input_type="dashboard", processed_content=content, title=content[:80], module="remember", module_data=json.dumps(body))
    db.add(entry)
    db.commit()
    db.refresh(entry)

    item = RememberItem(entry_id=entry.id, content=content, category=body.get("category", "General"), tags=body.get("tags", ""))
    db.add(item)
    db.commit()
    return JSONResponse(content={"ok": True, "id": item.id})


@router.put("/dashboard/api/remember/{item_id}")
async def edit_remember(item_id: int, request: Request, db: Session = Depends(get_db)):
    user = _user(request, db)
    if not user:
        return JSONResponse(status_code=401, content={"error": "Not logged in"})

    body = await request.json()
    item = db.query(RememberItem).join(Entry).filter(RememberItem.id == item_id, Entry.user_id == user.id).first()
    if not item:
        return JSONResponse(status_code=404, content={"error": "Not found"})

    if "content" in body:
        content = body.get("content", "").strip()
        if not content:
            return JSONResponse(status_code=400, content={"error": "Content required"})
        item.content = content
        item.entry.processed_content = content
        item.entry.title = content[:80]

    if "category" in body:
        cat = body.get("category", "").strip()
        if cat:
            item.category = cat

    if "tags" in body:
        item.tags = body.get("tags", "").strip()

    db.commit()
    logger.info(f"Remember item {item_id} edited by user {user.id}")
    return JSONResponse(content={"ok": True})


# ─── CRUD: Journal ────────────────────────────────────────

@router.post("/dashboard/api/journal")
async def add_journal(request: Request, db: Session = Depends(get_db)):
    user = _user(request, db)
    if not user:
        return JSONResponse(status_code=401, content={"error": "Not logged in"})

    body = await request.json()
    content = body.get("content", "").strip()
    if not content:
        return JSONResponse(status_code=400, content={"error": "Content required"})

    entry = Entry(user_id=user.id, input_type="dashboard", processed_content=content, title=content[:50], module="journal", module_data=json.dumps(body))
    db.add(entry)
    db.commit()
    db.refresh(entry)

    activity_type = body.get("activity_type", "").strip().lower()
    topic = body.get("topic", "").strip()

    je = JournalEntry(entry_id=entry.id, content=content, activity_type=activity_type, topic=topic, date=datetime.now(timezone.utc).date())
    db.add(je)
    db.commit()
    return JSONResponse(content={"ok": True, "id": je.id})


@router.put("/dashboard/api/journal/{entry_id}")
async def edit_journal(entry_id: int, request: Request, db: Session = Depends(get_db)):
    user = _user(request, db)
    if not user:
        return JSONResponse(status_code=401, content={"error": "Not logged in"})

    body = await request.json()
    je = db.query(JournalEntry).join(Entry).filter(JournalEntry.entry_id == entry_id, Entry.user_id == user.id).first()
    if not je:
        return JSONResponse(status_code=404, content={"error": "Not found"})

    if "content" in body:
        content = body.get("content", "").strip()
        if not content:
            return JSONResponse(status_code=400, content={"error": "Content required"})
        je.content = content
        je.entry.processed_content = content
        je.entry.title = content[:50]

    if "activity_type" in body:
        je.activity_type = body.get("activity_type", "").strip().lower()

    if "topic" in body:
        je.topic = body.get("topic", "").strip()

    db.commit()
    logger.info(f"Journal entry {entry_id} edited by user {user.id}")
    return JSONResponse(content={"ok": True})


# ─── CRUD: Calendar ───────────────────────────────────────

@router.post("/dashboard/api/calendar")
async def add_calendar(request: Request, db: Session = Depends(get_db)):
    user = _user(request, db)
    if not user:
        return JSONResponse(status_code=401, content={"error": "Not logged in"})

    body = await request.json()
    title = body.get("title", "").strip()
    if not title:
        return JSONResponse(status_code=400, content={"error": "Title required"})

    try:
        start_str = body.get("date", "") + "T" + body.get("time", "09:00")
        start_time = datetime.fromisoformat(start_str).replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return JSONResponse(status_code=400, content={"error": "Invalid date/time format"})

    end_str = body.get("date", "") + "T" + body.get("end_time", "10:00")
    try:
        end_time = datetime.fromisoformat(end_str).replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        end_time = start_time + timedelta(hours=1)

    location = body.get("location", "").strip()

    # Create Entry
    entry = Entry(user_id=user.id, input_type="dashboard", processed_content=title, title=title, module="calendar", module_data=json.dumps(body))
    db.add(entry)
    db.commit()
    db.refresh(entry)

    # Create CalendarEvent
    ce = CalendarEvent(entry_id=entry.id, title=title, start_time=start_time, end_time=end_time, location=location)
    db.add(ce)
    db.commit()

    # Try to create in Google Calendar if connected
    try:
        gcal_event = await create_gcal_event(
            title=title,
            start_time=start_time.isoformat(),
            end_time=end_time.isoformat(),
            location=location or None,
            description=f"Created from dashboard",
        )
        if gcal_event:
            ce.google_event_id = gcal_event.get("id")
            db.commit()
            logger.info(f"Created Google Calendar event: {ce.google_event_id}")
    except Exception as e:
        logger.warning(f"Could not create Google Calendar event: {e}")

    return JSONResponse(content={"ok": True, "id": ce.id})


# ─── Search ───────────────────────────────────────────────

@router.get("/dashboard/api/search")
async def search_entries(request: Request, db: Session = Depends(get_db)):
    user = _user(request, db)
    if not user:
        return JSONResponse(status_code=401, content={"error": "Not logged in"})

    q = request.query_params.get("q", "").strip()
    if not q or len(q) < 2:
        return JSONResponse(content={"results": {}, "query": q})

    # Search across all entry types
    query_filter = or_(
        Entry.processed_content.ilike(f"%{q}%"),
        Entry.title.ilike(f"%{q}%"),
        Entry.raw_transcript.ilike(f"%{q}%")
    )

    entries = db.query(Entry).filter(
        Entry.user_id == user.id,
        _not_deleted(),
        query_filter
    ).order_by(Entry.created_at.desc()).limit(50).all()

    # Group by module
    results = defaultdict(list)
    for entry in entries:
        if entry.task:
            results["tasks"].append({
                "id": entry.task.id,
                "entry_id": entry.id,
                "description": entry.task.description,
                "priority": entry.task.priority,
                "status": entry.task.status
            })
        elif entry.calendar_event:
            results["calendar"].append({
                "id": entry.calendar_event.id,
                "entry_id": entry.id,
                "title": entry.calendar_event.title,
                "start_time": entry.calendar_event.start_time.isoformat() if entry.calendar_event.start_time else None,
                "location": entry.calendar_event.location
            })
        elif entry.remember_item:
            results["remember"].append({
                "id": entry.remember_item.id,
                "entry_id": entry.id,
                "content": entry.remember_item.content[:80],
                "category": entry.remember_item.category
            })
        elif entry.journal_entry:
            results["journal"].append({
                "id": entry.journal_entry.id,
                "entry_id": entry.id,
                "content": entry.journal_entry.content[:80],
                "topic": entry.journal_entry.topic
            })
        elif entry.module == "memo":
            results["memos"].append({
                "id": entry.id,
                "entry_id": entry.id,
                "title": entry.title,
                "content": entry.processed_content[:80]
            })

    return JSONResponse(content={"results": dict(results), "query": q, "total": len(entries)})


# ─── Quick Capture ────────────────────────────────────────

MODULE_HANDLERS = {
    "memo": handle_memo, "diary": handle_memo, "screenshot_note": handle_memo,
    "expense": handle_memo, "food": handle_memo, "mood": handle_memo,
    "idea": handle_memo, "gym": handle_memo, "work": handle_memo,
    "calendar": handle_calendar, "task": handle_task,
    "remember": handle_remember, "journal": handle_journal,
}

@router.post("/dashboard/api/quick-capture")
async def quick_capture(request: Request, db: Session = Depends(get_db)):
    user = _user(request, db)
    if not user:
        return JSONResponse(status_code=401, content={"error": "Not logged in"})

    body = await request.json()
    text = body.get("text", "").strip()
    if not text:
        return JSONResponse(status_code=400, content={"error": "Text required"})

    try:
        # Classify the intent using the same pipeline as voice input
        intents = await classify_intent(transcript=text)
        logger.info(f"Quick capture: '{text[:60]}' -> {len(intents)} intent(s)")

        responses = []
        for intent_data in intents:
            module_name = intent_data.get("module", "memo")
            handler = MODULE_HANDLERS.get(module_name, handle_memo)
            try:
                response = await handler(
                    user=user, raw_input=text,
                    intent_data=intent_data, db=db,
                    input_type="text", image_description=None,
                )
                responses.append(response.spoken_response)
            except Exception as e:
                logger.error(f"Quick capture handler error ({module_name}): {e}")
                responses.append(f"Error processing {module_name}")

        return JSONResponse(content={
            "ok": True,
            "spoken_response": " ".join(responses),
            "module": intents[0].get("module", "memo") if intents else "memo",
        })
    except Exception as e:
        logger.error(f"Quick capture error: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})


# ─── Notification Contacts ──────────────────────────────────

@router.post("/dashboard/api/contacts")
async def add_contact(request: Request, db: Session = Depends(get_db)):
    user = _user(request, db)
    if not user:
        return JSONResponse(status_code=401, content={"error": "Not logged in"})
    body = await request.json()
    name = body.get("name", "").strip()
    phone = body.get("phone", "").strip()
    mode = body.get("notify_mode", "always")
    if not name or not phone:
        return JSONResponse(status_code=400, content={"error": "Name and phone required"})
    contact = NotificationContact(user_id=user.id, name=name, phone=phone, notify_mode=mode)
    db.add(contact)
    db.commit()
    return JSONResponse(content={"ok": True, "id": contact.id})


@router.post("/dashboard/api/contacts/{contact_id}/mode")
async def update_contact_mode(contact_id: int, request: Request, db: Session = Depends(get_db)):
    user = _user(request, db)
    if not user:
        return JSONResponse(status_code=401, content={"error": "Not logged in"})
    body = await request.json()
    contact = db.query(NotificationContact).filter(NotificationContact.id == contact_id, NotificationContact.user_id == user.id).first()
    if not contact:
        return JSONResponse(status_code=404, content={"error": "Not found"})
    contact.notify_mode = body.get("notify_mode", contact.notify_mode)
    db.commit()
    return JSONResponse(content={"ok": True})


@router.delete("/dashboard/api/contacts/{contact_id}")
async def delete_contact(contact_id: int, request: Request, db: Session = Depends(get_db)):
    user = _user(request, db)
    if not user:
        return JSONResponse(status_code=401, content={"error": "Not logged in"})
    contact = db.query(NotificationContact).filter(NotificationContact.id == contact_id, NotificationContact.user_id == user.id).first()
    if not contact:
        return JSONResponse(status_code=404, content={"error": "Not found"})
    db.delete(contact)
    db.commit()
    return JSONResponse(content={"ok": True})


# ─── Main Dashboard ───────────────────────────────────────

@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request, db: Session = Depends(get_db)):
    user = _user(request, db)
    if not user:
        return RedirectResponse("/dashboard/login", status_code=302)

    # Auto-purge old trash
    _purge_old_trash(db, user)

    now = datetime.now(timezone.utc)
    today_start = datetime.combine(now.date(), datetime.min.time()).replace(tzinfo=timezone.utc)
    today_end = today_start + timedelta(days=1)

    # Today's data
    today_tasks = db.query(Task).join(Entry).filter(
        Entry.user_id == user.id, _not_deleted(),
        or_(Task.priority == "do_today", Task.priority == "urgent")
    ).all()

    today_events = db.query(CalendarEvent).join(Entry).filter(
        Entry.user_id == user.id, _not_deleted(),
        CalendarEvent.start_time >= today_start,
        CalendarEvent.start_time < today_end
    ).order_by(CalendarEvent.start_time.asc()).all()

    today_memos = db.query(Entry).filter(
        Entry.user_id == user.id, Entry.module == "memo", _not_deleted(),
        Entry.created_at >= today_start,
        Entry.created_at < today_end
    ).order_by(Entry.created_at.desc()).all()

    today_journal = db.query(JournalEntry).join(Entry).filter(
        Entry.user_id == user.id, _not_deleted(),
        JournalEntry.date == now.date()
    ).order_by(JournalEntry.created_at.desc()).all()

    # Active items (not deleted)
    open_tasks = db.query(Task).join(Entry).filter(Entry.user_id == user.id, _not_deleted(), Task.status == "open").order_by(Task.priority.asc(), Task.created_at.desc()).all()
    done_tasks = db.query(Task).join(Entry).filter(Entry.user_id == user.id, _not_deleted(), Task.status == "done").order_by(Task.completed_at.desc()).limit(20).all()
    upcoming = db.query(CalendarEvent).join(Entry).filter(Entry.user_id == user.id, _not_deleted(), CalendarEvent.start_time >= now).order_by(CalendarEvent.start_time.asc()).limit(20).all()
    past_ev = db.query(CalendarEvent).join(Entry).filter(Entry.user_id == user.id, _not_deleted(), CalendarEvent.start_time < now).order_by(CalendarEvent.start_time.desc()).limit(10).all()
    memos = db.query(Entry).filter(Entry.user_id == user.id, Entry.module == "memo", _not_deleted()).order_by(Entry.created_at.desc()).limit(20).all()
    remember_items = db.query(RememberItem).join(Entry).filter(Entry.user_id == user.id, _not_deleted()).order_by(RememberItem.created_at.desc()).all()
    journal_entries = db.query(JournalEntry).join(Entry).filter(Entry.user_id == user.id, _not_deleted()).order_by(JournalEntry.date.desc()).limit(50).all()

    # Trash
    trashed = db.query(Entry).filter(Entry.user_id == user.id, Entry.deleted_at.isnot(None)).order_by(Entry.deleted_at.desc()).all()

    # Notification contacts
    contacts = db.query(NotificationContact).filter(NotificationContact.user_id == user.id).order_by(NotificationContact.name).all()

    # Stats
    total_open = len(open_tasks)
    total_done_today = len([t for t in done_tasks if t.completed_at and t.completed_at.date() == now.date()])
    total_journal_today = len(today_journal)

    # Existing groups/categories for dropdowns
    all_tasks = open_tasks + done_tasks
    task_groups = sorted(set(t.group for t in all_tasks)) if all_tasks else ["General", "Errands", "House", "Work", "Health", "Personal", "Dogs"]
    remember_cats = sorted(set(r.category for r in remember_items)) if remember_items else ["General", "People", "Passwords", "Home", "Work", "Reference"]

    html = _render(user, open_tasks, done_tasks, upcoming, past_ev, memos, remember_items, journal_entries, trashed, contacts, task_groups, remember_cats, total_open, total_done_today, total_journal_today, today_tasks, today_events, today_memos, today_journal)
    return HTMLResponse(content=html)

# ─── Helpers ──────────────────────────────────────────────

def _fmt(dt):
    if not dt: return ""
    if isinstance(dt, str):
        try: dt = datetime.fromisoformat(dt)
        except: return dt
    return dt.strftime("%b %d, %I:%M %p")

def _fdate(dt):
    if not dt: return ""
    if isinstance(dt, str):
        try: dt = datetime.fromisoformat(dt)
        except: return dt
    return dt.strftime("%b %d, %Y")

def _time_only(dt):
    if not dt: return ""
    if isinstance(dt, str):
        try: dt = datetime.fromisoformat(dt)
        except: return dt
    return dt.strftime("%I:%M %p")

def _day_key(dt):
    if isinstance(dt, str):
        try: dt = datetime.fromisoformat(dt)
        except: return dt
    return dt.strftime("%A, %B %d")

def _badge(p):
    c = {"urgent":"#dc2626","do_today":"#ea580c","this_week":"#ca8a04","keep_in_mind":"#2563eb"}.get(p,"#6b7280")
    l = {"urgent":"Urgent","do_today":"Today","this_week":"This Week","keep_in_mind":"Someday"}.get(p,p)
    return f'<span class="badge" style="background:{c}">{l}</span>'

def _e(s):
    if not s: return ""
    return s.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;").replace('"',"&quot;").replace("'","&#39;")

def _trash_label(entry):
    if entry.task:
        return f"Task: {entry.task.description}"
    if entry.remember_item:
        return f"Remember: {entry.remember_item.content[:60]}"
    if entry.journal_entry:
        return f"Journal: {entry.journal_entry.content[:60]}"
    if entry.calendar_event:
        return f"Calendar: {entry.calendar_event.title}"
    return f"Memo: {entry.title or entry.processed_content or 'Untitled'}".strip()[:80]

def _days_left(deleted_at):
    if not deleted_at: return 10
    if deleted_at.tzinfo is None:
        deleted_at = deleted_at.replace(tzinfo=timezone.utc)
    elapsed = (datetime.now(timezone.utc) - deleted_at).days
    return max(0, 10 - elapsed)


# ─── Render ──────────────────────────────────────────────

def _render(user, open_tasks, done_tasks, upcoming, past_ev, memos, remember_items, journal_entries, trashed, contacts, task_groups, remember_cats, total_open, total_done_today, total_journal_today, today_tasks, today_events, today_memos, today_journal):

    # ── Stats bar ──
    stats_html = f'''<div class="stats-bar">
        <div class="stat"><span class="stat-num">{total_open}</span><span class="stat-label">Open tasks</span></div>
        <div class="stat"><span class="stat-num">{total_done_today}</span><span class="stat-label">Done today</span></div>
        <div class="stat"><span class="stat-num">{len(upcoming)}</span><span class="stat-label">Upcoming</span></div>
        <div class="stat"><span class="stat-num">{total_journal_today}</span><span class="stat-label">Logged today</span></div>
    </div>'''

    # ── Quick Capture ──
    quick_capture_html = '''<div class="quick-capture">
        <input type="text" id="quick-capture-input" placeholder="What's on your mind? (processed like voice input)" onkeydown="if(event.key==='Enter')quickCapture()">
        <button class="quick-capture-btn" onclick="quickCapture()">Send</button>
    </div>'''

    # ── Search Modal ──
    search_modal_html = '''<div id="search-modal" class="modal" style="display:none">
        <div class="modal-content">
            <div class="modal-header">
                <input type="text" id="search-input" placeholder="Search tasks, calendar, remember..." onkeydown="if(event.key==='Enter')performSearch()">
                <button class="modal-close" onclick="closeSearchModal()">&times;</button>
            </div>
            <div id="search-results" class="search-results"></div>
        </div>
    </div>'''

    # ── Today Tab ──
    today_html = '<div class="card"><div class="card-title">Today\'s Overview</div>'

    if today_tasks:
        today_html += '<div class="section-label"><span class="section-icon">&#10003;</span> Today\'s Tasks</div>'
        for t in today_tasks:
            today_html += f'''<div class="item row" id="entry-{t.entry_id}">
                <div class="left"><span class="item-text">{_e(t.description)}</span></div>
                <div class="right">{_badge(t.priority)} <button class="done-btn" onclick="completeTask({t.id})" title="Mark done">&#10003;</button><button class="del-btn" onclick="trashItem({t.entry_id})" title="Move to trash">&#128465;</button></div>
            </div>'''
    else:
        today_html += '<div style="color:var(--text-muted);font-size:12px;padding:8px">No urgent or do_today tasks</div>'

    if today_events:
        today_html += '<div class="section-label" style="margin-top:12px"><span class="section-icon">&#128197;</span> Today\'s Events</div>'
        for ev in today_events:
            today_html += f'''<div class="ev-card" id="entry-{ev.entry_id}">
                <div class="ev-left"><div class="ev-dot"></div></div>
                <div class="ev-body">
                    <div class="ev-time">{_time_only(ev.start_time)}</div>
                    <div class="ev-title">{_e(ev.title)}</div>
                    {f'<div class="ev-loc">{_e(ev.location)}</div>' if ev.location else ''}
                </div>
                <button class="del-btn" onclick="trashItem({ev.entry_id})" title="Move to trash">&#128465;</button>
            </div>'''

    if today_journal:
        today_html += '<div class="section-label" style="margin-top:12px"><span class="section-icon">&#128214;</span> Today\'s Journal</div>'
        for j in today_journal:
            today_html += f'''<div class="item" id="entry-{j.entry_id}">
                <div class="journal-row"><div>{_e(j.content[:150])}</div><button class="del-btn" onclick="trashItem({j.entry_id})" title="Move to trash">&#128465;</button></div>
            </div>'''

    today_html += '</div>'

    # ── Tasks ──
    tg = defaultdict(list)
    for t in open_tasks: tg[t.group].append(t)

    tasks_html = ""
    if tg:
        for group in sorted(tg.keys()):
            tasks_html += f'<div class="group-hdr">{_e(group)} <span class="group-count">{len(tg[group])}</span></div>'
            for t in tg[group]:
                due = f'<span class="due">Due {_fdate(t.due_date)}</span>' if t.due_date else ""
                tasks_html += f'''<div class="item row" id="entry-{t.entry_id}">
                    <div class="left">
                        <span class="item-text editable" data-type="task" data-id="{t.id}" data-field="description" onclick="makeEditable(this)">{_e(t.description)}</span>
                    </div>
                    <div class="right">{_badge(t.priority)} {due} <span class="ts">{_fmt(t.created_at)}</span><button class="done-btn" onclick="completeTask({t.id})" title="Mark done">&#10003;</button><button class="del-btn" onclick="trashItem({t.entry_id})" title="Move to trash">&#128465;</button></div>
                </div>'''
    else:
        tasks_html = '<div class="empty-state"><div class="empty-icon">&#10003;</div><div>You\'re all caught up!</div></div>'

    done_html = ""
    if done_tasks:
        for t in done_tasks:
            done_html += f'''<div class="item row done" id="entry-{t.entry_id}">
                <div class="left"><span class="item-text">{_e(t.description)}</span></div>
                <div class="right"><span class="tag">{_e(t.group)}</span><span class="ts">{_fmt(t.completed_at)}</span><button class="reopen-btn" onclick="reopenTask({t.id})" title="Reopen">&#8634;</button><button class="del-btn" onclick="trashItem({t.entry_id})" title="Move to trash">&#128465;</button></div>
            </div>'''
    else:
        done_html = '<div class="empty">Complete a task to see it here.</div>'

    group_opts = "".join(f'<option value="{_e(g)}">{_e(g)}</option>' for g in task_groups)
    group_opts += '<option value="__custom">+ New group...</option>'

    # ── Calendar ──
    upcoming_html = ""
    if upcoming:
        for ev in upcoming:
            loc = f'<span class="ev-loc">{_e(ev.location)}</span>' if ev.location else ""
            sms = '<span class="sms-indicator">SMS</span>' if ev.sms_sent else ""
            upcoming_html += f'''<div class="ev-card" id="entry-{ev.entry_id}">
                <div class="ev-left"><div class="ev-dot"></div></div>
                <div class="ev-body">
                    <div class="ev-time">{_fmt(ev.start_time)}</div>
                    <div class="ev-title">{_e(ev.title)} {sms}</div>
                    {f'<div class="ev-loc">{_e(ev.location)}</div>' if ev.location else ''}
                </div>
                <button class="del-btn" onclick="trashItem({ev.entry_id})" title="Move to trash">&#128465;</button>
            </div>'''
    else:
        upcoming_html = '<div class="empty-state"><div class="empty-icon">&#128197;</div><div>No upcoming events</div></div>'

    past_html = ""
    for ev in past_ev:
        past_html += f'''<div class="ev-card past" id="entry-{ev.entry_id}">
            <div class="ev-left"><div class="ev-dot past"></div></div>
            <div class="ev-body"><div class="ev-time">{_fmt(ev.start_time)}</div><div class="ev-title">{_e(ev.title)}</div></div>
            <button class="del-btn" onclick="trashItem({ev.entry_id})" title="Move to trash">&#128465;</button>
        </div>'''

    calendar_form_html = '''<div class="add-form" id="calendar-form">
        <input type="text" id="cal-title" placeholder="Event title" onkeydown="if(event.key==='Enter')document.getElementById('cal-date').focus()">
        <input type="date" id="cal-date">
        <div class="form-row">
            <input type="time" id="cal-start-time" value="09:00">
            <input type="time" id="cal-end-time" value="10:00">
        </div>
        <input type="text" id="cal-location" placeholder="Location (optional)">
        <div class="form-actions">
            <button class="btn btn-ghost" onclick="toggleForm('calendar-form')">Cancel</button>
            <button class="btn btn-primary" onclick="addCalendarEvent()">Create Event</button>
        </div>
    </div>'''

    # ── Remember ──
    rg = defaultdict(list)
    for r in remember_items: rg[r.category].append(r)

    remember_html = ""
    if rg:
        for cat in sorted(rg.keys()):
            remember_html += f'<div class="group-hdr">{_e(cat)} <span class="group-count">{len(rg[cat])}</span></div>'
            for r in rg[cat]:
                tags = ""
                if r.tags:
                    tags = " ".join(f'<span class="tag">{_e(t.strip())}</span>' for t in r.tags.split(",") if t.strip())
                remember_html += f'''<div class="item" id="entry-{r.entry_id}">
                    <div class="rem-row">
                        <div class="rem-content editable" data-type="remember" data-id="{r.id}" data-field="content" onclick="makeEditable(this)">{_e(r.content)}</div>
                        <button class="del-btn" onclick="trashItem({r.entry_id})" title="Move to trash">&#128465;</button>
                    </div>
                    <div class="rem-meta">{tags}<span class="ts">{_fmt(r.created_at)}</span></div>
                </div>'''
    else:
        remember_html = '<div class="empty-state"><div class="empty-icon">&#128161;</div><div>Say "remember that..." to save things here</div></div>'

    cat_opts = "".join(f'<option value="{_e(c)}">{_e(c)}</option>' for c in remember_cats)
    cat_opts += '<option value="__custom">+ New category...</option>'

    # ── Journal ──
    jbd = defaultdict(list)
    for j in journal_entries: jbd[_day_key(j.date)].append(j)

    all_topics = defaultdict(list)
    for j in journal_entries:
        if j.topic: all_topics[j.topic].append(j)

    journal_html = ""
    if jbd:
        work_types = {"work", "learning"}
        for day in jbd:
            journal_html += f'<div class="day-hdr">{day}</div>'
            work_items = [j for j in jbd[day] if (j.activity_type or "").lower() in work_types]
            life_items = [j for j in jbd[day] if (j.activity_type or "").lower() not in work_types]
            if work_items:
                journal_html += '<div class="section-label"><span class="section-icon">&#128188;</span> Work</div>'
                for j in work_items:
                    topic = f'<span class="topic-tag">{_e(j.topic)}</span>' if j.topic else ""
                    atype = f'<span class="tag">{(j.activity_type or "").replace("_"," ").title()}</span>' if j.activity_type else ""
                    journal_html += f'''<div class="item" id="entry-{j.entry_id}">
                        <div class="journal-row">
                            <div class="editable" data-type="journal" data-id="{j.entry_id}" data-field="content" onclick="makeEditable(this)">{_e(j.content)}</div>
                            <button class="del-btn" onclick="trashItem({j.entry_id})" title="Move to trash">&#128465;</button>
                        </div>
                        {topic} {atype}
                        <div class="ts">{_fmt(j.created_at)}</div>
                    </div>'''
            if life_items:
                journal_html += '<div class="section-label"><span class="section-icon">&#127793;</span> Life</div>'
                for j in life_items:
                    topic = f'<span class="topic-tag">{_e(j.topic)}</span>' if j.topic else ""
                    atype = f'<span class="tag">{(j.activity_type or "").replace("_"," ").title()}</span>' if j.activity_type else ""
                    journal_html += f'''<div class="item" id="entry-{j.entry_id}">
                        <div class="journal-row">
                            <div class="editable" data-type="journal" data-id="{j.entry_id}" data-field="content" onclick="makeEditable(this)">{_e(j.content)}</div>
                            <button class="del-btn" onclick="trashItem({j.entry_id})" title="Move to trash">&#128465;</button>
                        </div>
                        {topic} {atype}
                        <div class="ts">{_fmt(j.created_at)}</div>
                    </div>'''
    else:
        journal_html = '<div class="empty-state"><div class="empty-icon">&#128214;</div><div>Tell me what you did today</div></div>'

    journal_form_html = '''<div class="add-form" id="journal-form">
        <textarea id="journal-content" placeholder="What's on your mind?" style="min-height:100px;resize:vertical"></textarea>
        <div class="form-row">
            <select id="journal-activity">
                <option value="">Activity Type</option>
                <option value="work">Work</option>
                <option value="learning">Learning</option>
                <option value="exercise">Exercise</option>
                <option value="social">Social</option>
                <option value="creative">Creative</option>
                <option value="reflection">Reflection</option>
            </select>
            <input type="text" id="journal-topic" placeholder="Topic/Project">
        </div>
        <div class="form-actions">
            <button class="btn btn-ghost" onclick="toggleForm('journal-form')">Cancel</button>
            <button class="btn btn-primary" onclick="addJournalEntry()">Save Entry</button>
        </div>
    </div>'''

    topics_html = ""
    if all_topics:
        topics_html = '<div class="card"><div class="card-title">Topics &amp; Projects</div>'
        for topic in sorted(all_topics.keys()):
            count = len(all_topics[topic])
            latest = all_topics[topic][0]
            topics_html += f'<div class="topic-row"><div class="topic-name">{_e(topic)}</div><div class="topic-meta"><span class="tag">{count} entries</span><span class="ts">{_e(latest.content[:50])}</span></div></div>'
        topics_html += '</div>'

    # ── Memos ──
    memos_html = ""
    if memos:
        for m in memos:
            c = _e((m.processed_content or m.raw_transcript or "")[:250])
            memos_html += f'''<div class="item" id="entry-{m.id}">
                <div class="memo-row"><div><div class="memo-title">{_e(m.title or "Memo")}</div><div class="memo-body">{c}</div></div><button class="del-btn" onclick="trashItem({m.id})" title="Move to trash">&#128465;</button></div>
                <div class="ts">{_fmt(m.created_at)}</div>
            </div>'''
    else:
        memos_html = '<div class="empty-state"><div class="empty-icon">&#128221;</div><div>No memos yet</div></div>'

    # ── Trash ──
    trash_count = len(trashed)
    trash_html = ""
    if trashed:
        for entry in trashed:
            label = _e(_trash_label(entry))
            days = _days_left(entry.deleted_at)
            trash_html += f'''<div class="item trash-item" id="entry-{entry.id}">
                <div class="trash-row">
                    <div class="trash-label">{label}</div>
                    <div class="trash-actions">
                        <span class="trash-days">{days}d left</span>
                        <button class="restore-btn" onclick="restoreItem({entry.id})" title="Restore">&#8634;</button>
                        <button class="perm-del-btn" onclick="permDelete({entry.id})" title="Delete forever">&#128465;</button>
                    </div>
                </div>
                <div class="ts">Deleted {_fmt(entry.deleted_at)}</div>
            </div>'''
    else:
        trash_html = '<div class="empty-state"><div class="empty-icon">&#128465;</div><div>Trash is empty</div></div>'

    # ── Settings / Contacts ──
    contacts_html = ""
    mode_labels = {"always": "Always notify", "mentioned": "When mentioned", "never": "Never"}
    mode_colors = {"always": "#16a34a", "mentioned": "#ca8a04", "never": "#6b7a8d"}
    if contacts:
        for c in contacts:
            mode_options = ""
            for m in ["always", "mentioned", "never"]:
                sel = "selected" if c.notify_mode == m else ""
                mode_options += f'<option value="{m}" {sel}>{mode_labels[m]}</option>'
            contacts_html += f'''<div class="item contact-row" id="contact-{c.id}">
                <div class="contact-info">
                    <div class="contact-name">{_e(c.name)}</div>
                    <div class="contact-phone">{_e(c.phone)}</div>
                </div>
                <div class="contact-actions">
                    <select class="mode-select" onchange="updateContactMode({c.id}, this.value)">{mode_options}</select>
                    <button class="del-btn" onclick="deleteContact({c.id})" title="Remove contact">&#128465;</button>
                </div>
            </div>'''
    else:
        contacts_html = '<div class="empty">No contacts yet. Add someone to receive calendar SMS notifications.</div>'

    return f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Planner — {_e(user.name)}</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">
<style>
:root{{--bg:#06090f;--surface:#0d1219;--card:#111820;--border:#1a2233;--border-light:#232f42;--text:#cdd5e0;--text-dim:#6b7a8d;--text-muted:#3d4d5f;--accent:#6366f1;--accent-hover:#4f46e5;--danger:#dc2626;--success:#16a34a;--warning:#ca8a04;--radius:10px}}
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:'Inter',system-ui,sans-serif;background:var(--bg);color:var(--text);min-height:100vh;-webkit-font-smoothing:antialiased}}

/* Header */
.header{{background:var(--surface);border-bottom:1px solid var(--border);padding:14px 24px;display:flex;justify-content:space-between;align-items:center;position:sticky;top:0;z-index:100}}
.header h1{{font-size:20px;font-weight:700;color:#f1f3f9;letter-spacing:-.5px}}
.header h1 span{{color:var(--accent);font-weight:300}}
.user-area{{display:flex;align-items:center;gap:12px}}
.user-name{{font-size:12px;color:var(--text-dim);font-weight:500;padding:4px 10px;background:var(--card);border-radius:20px;border:1px solid var(--border)}}
.search-icon{{cursor:pointer;font-size:16px;color:var(--text-dim);transition:color .2s}}
.search-icon:hover{{color:var(--accent)}}
.logout-btn{{font-size:11px;color:var(--text-muted);cursor:pointer;padding:4px 10px;border:1px solid var(--border);border-radius:6px;background:transparent;transition:all .2s;text-decoration:none}}
.logout-btn:hover{{border-color:var(--accent);color:var(--accent)}}

.container{{max-width:860px;margin:0 auto;padding:20px 16px 60px}}

/* Stats */
.stats-bar{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-bottom:16px}}
.stat{{background:var(--card);border:1px solid var(--border);border-radius:var(--radius);padding:14px 16px;text-align:center}}
.stat-num{{display:block;font-size:24px;font-weight:700;color:#f1f3f9;line-height:1.2}}
.stat-label{{font-size:11px;color:var(--text-dim);text-transform:uppercase;letter-spacing:.5px;margin-top:2px}}

/* Quick Capture */
.quick-capture{{display:flex;gap:8px;margin-bottom:16px}}
.quick-capture input{{flex:1;padding:10px 12px;background:var(--card);border:1px solid var(--border);border-radius:var(--radius);color:#e2e8f0;font-size:13px;font-family:inherit;outline:none;transition:border .2s}}
.quick-capture input:focus{{border-color:var(--accent)}}
.quick-capture input::placeholder{{color:var(--text-muted)}}
.quick-capture-btn{{padding:10px 16px;background:var(--accent);color:#fff;border:none;border-radius:var(--radius);font-size:12px;font-weight:600;cursor:pointer;transition:background .2s}}
.quick-capture-btn:hover{{background:var(--accent-hover)}}

/* Modal */
.modal{{display:none;position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,.7);z-index:1000;overflow-y:auto}}
.modal.show{{display:flex;align-items:flex-start;justify-content:center;padding-top:20px}}
.modal-content{{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);width:90%;max-width:500px;max-height:80vh;overflow:hidden;display:flex;flex-direction:column}}
.modal-header{{display:flex;gap:8px;padding:16px;border-bottom:1px solid var(--border)}}
.modal-header input{{flex:1;padding:8px 12px;background:var(--card);border:1px solid var(--border);border-radius:6px;color:#e2e8f0;font-size:13px;outline:none}}
.modal-header input:focus{{border-color:var(--accent)}}
.modal-close{{background:none;border:none;color:var(--text-dim);font-size:20px;cursor:pointer;width:28px;height:28px;display:flex;align-items:center;justify-content:center;border-radius:4px}}
.modal-close:hover{{background:rgba(255,255,255,.05);color:var(--accent)}}
.search-results{{flex:1;overflow-y:auto;padding:12px}}
.search-result-group{{margin-bottom:16px}}
.search-result-title{{font-size:11px;font-weight:700;color:var(--text-dim);text-transform:uppercase;letter-spacing:1px;padding:8px 12px;background:var(--card);border-radius:6px;margin-bottom:4px}}
.search-result-item{{padding:8px 12px;border-radius:6px;cursor:pointer;transition:background .2s;font-size:12px}}
.search-result-item:hover{{background:rgba(99,102,241,.1)}}

/* Tabs */
.tabs{{display:flex;gap:2px;background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:3px;margin-bottom:20px;overflow-x:auto;-webkit-overflow-scrolling:touch}}
.tab{{flex:1;padding:9px 6px;text-align:center;border-radius:8px;cursor:pointer;font-size:12px;font-weight:500;color:var(--text-dim);transition:all .2s;border:none;background:none;white-space:nowrap;position:relative}}
.tab:hover{{color:var(--text)}}
.tab.active{{background:var(--accent);color:#fff;font-weight:600}}
.tab .tab-badge{{position:absolute;top:3px;right:8px;font-size:9px;background:var(--danger);color:#fff;width:16px;height:16px;border-radius:50%;display:flex;align-items:center;justify-content:center}}
.tc{{display:none}}.tc.active{{display:block}}

/* Cards */
.card{{background:var(--card);border:1px solid var(--border);border-radius:var(--radius);padding:18px 20px;margin-bottom:14px}}
.card-title{{font-size:14px;font-weight:600;color:#e2e8f0;margin-bottom:14px;display:flex;justify-content:space-between;align-items:center}}
.add-btn{{font-size:11px;color:var(--accent);cursor:pointer;padding:4px 12px;border:1px solid var(--border-light);border-radius:6px;background:transparent;transition:all .2s;font-weight:600}}
.add-btn:hover{{background:rgba(99,102,241,.1);border-color:var(--accent)}}

/* Items */
.item{{padding:10px 12px;border-radius:8px;margin-bottom:3px;transition:all .25s ease}}
.item:hover{{background:rgba(255,255,255,.03)}}
.row{{display:flex;justify-content:space-between;align-items:center;gap:8px}}
.left{{display:flex;align-items:center;gap:10px;flex:1;min-width:0}}
.right{{display:flex;align-items:center;gap:8px;flex-shrink:0;flex-wrap:wrap}}
.item-text{{font-size:13px;color:var(--text);cursor:text}}
.item-text.editable:hover{{opacity:.7;background:rgba(99,102,241,.1);padding:2px 6px;border-radius:4px}}
.item.done{{opacity:.45}}.item.done .item-text{{text-decoration:line-through}}

/* Editable */
.editable-input{{background:var(--card)!important;border:1px solid var(--accent)!important;color:#e2e8f0!important;padding:8px 10px!important;border-radius:6px!important;font-size:13px!important;font-family:inherit!important}}

/* Badges */
.badge{{color:#fff;padding:2px 8px;border-radius:10px;font-size:10px;font-weight:600;letter-spacing:.3px;white-space:nowrap}}
.tag{{background:var(--surface);color:var(--text-dim);padding:2px 8px;border-radius:6px;font-size:10px;border:1px solid var(--border)}}
.topic-tag{{background:rgba(99,102,241,.2);color:#a5b4fc;padding:2px 7px;border-radius:6px;font-size:10px;font-weight:600;margin-left:4px;border:1px solid rgba(99,102,241,.3)}}
.due{{color:var(--warning);font-size:11px}}
.ts{{color:var(--text-muted);font-size:10px;white-space:nowrap}}

/* Groups */
.group-hdr{{font-size:10px;font-weight:700;color:var(--text-dim);text-transform:uppercase;letter-spacing:1.2px;margin:14px 0 6px;padding-bottom:4px;border-bottom:1px solid var(--border);display:flex;justify-content:space-between;align-items:center}}
.group-hdr:first-child{{margin-top:0}}
.group-count{{font-size:9px;background:var(--surface);color:var(--text-muted);padding:1px 6px;border-radius:10px;font-weight:500}}
.day-hdr{{font-size:13px;font-weight:600;color:var(--accent);margin:16px 0 8px;padding-bottom:4px;border-bottom:1px solid var(--border)}}
.day-hdr:first-child{{margin-top:0}}
.section-label{{font-size:11px;font-weight:700;color:var(--text-dim);margin:10px 0 4px 0;padding:4px 10px;background:var(--surface);border-radius:6px;display:inline-flex;align-items:center;gap:4px}}
.section-icon{{font-size:13px}}

/* Calendar */
.ev-card{{display:flex;align-items:flex-start;gap:12px;padding:12px;margin-bottom:6px;border-radius:8px;transition:background .2s}}
.ev-card:hover{{background:rgba(255,255,255,.02)}}
.ev-card.past{{opacity:.4}}
.ev-left{{padding-top:4px}}
.ev-dot{{width:10px;height:10px;border-radius:50%;background:var(--accent);box-shadow:0 0 0 3px rgba(99,102,241,.2)}}
.ev-dot.past{{background:var(--text-muted);box-shadow:none}}
.ev-body{{flex:1}}
.ev-time{{font-size:11px;color:var(--accent);font-weight:600;margin-bottom:1px}}
.ev-title{{font-size:13px;font-weight:600;color:#e2e8f0;display:flex;align-items:center;gap:6px}}
.ev-loc{{font-size:11px;color:var(--text-dim);margin-top:1px}}
.sms-indicator{{font-size:9px;background:var(--success);color:#fff;padding:1px 5px;border-radius:4px;font-weight:600}}

/* Remember */
.rem-row{{display:flex;justify-content:space-between;align-items:flex-start;gap:8px}}
.rem-content{{font-size:13px;color:var(--text);line-height:1.5;flex:1;cursor:text}}
.rem-content.editable:hover{{opacity:.7;background:rgba(99,102,241,.1);padding:2px 6px;border-radius:4px}}
.rem-meta{{display:flex;align-items:center;gap:6px;margin-top:4px;flex-wrap:wrap}}

/* Journal */
.journal-row{{display:flex;justify-content:space-between;align-items:flex-start;gap:8px}}
.journal-row>div:first-child{{flex:1;font-size:13px;line-height:1.5;cursor:text}}
.journal-row>div:first-child.editable:hover{{opacity:.7;background:rgba(99,102,241,.1);padding:2px 6px;border-radius:4px}}

/* Topics */
.topic-row{{padding:10px 12px;border-radius:8px;margin-bottom:3px}}.topic-row:hover{{background:rgba(255,255,255,.03)}}
.topic-name{{font-size:13px;font-weight:600;color:#e2e8f0}}
.topic-meta{{display:flex;align-items:center;gap:8px;margin-top:2px}}

/* Memos */
.memo-row{{display:flex;justify-content:space-between;align-items:flex-start;gap:8px}}
.memo-title{{font-size:13px;font-weight:600;color:#e2e8f0}}
.memo-body{{font-size:12px;color:var(--text-dim);margin-top:3px;line-height:1.5}}

/* Contacts */
.contact-row{{display:flex;justify-content:space-between;align-items:center;gap:12px;padding:12px}}
.contact-info{{flex:1}}
.contact-name{{font-size:14px;font-weight:600;color:#e2e8f0}}
.contact-phone{{font-size:12px;color:var(--text-dim);margin-top:1px}}
.contact-actions{{display:flex;align-items:center;gap:8px}}
.mode-select{{padding:5px 8px;background:var(--surface);border:1px solid var(--border);border-radius:6px;color:var(--text);font-size:11px;font-family:inherit;cursor:pointer;outline:none}}
.mode-select:focus{{border-color:var(--accent)}}

/* Trash */
.trash-item{{border-left:3px solid var(--danger);padding-left:14px}}
.trash-row{{display:flex;justify-content:space-between;align-items:center;gap:8px}}
.trash-label{{font-size:13px;color:var(--text-dim);flex:1}}
.trash-actions{{display:flex;align-items:center;gap:6px}}
.trash-days{{font-size:10px;color:var(--text-muted);white-space:nowrap}}
.restore-btn{{background:none;border:1px solid var(--border-light);color:var(--text-dim);cursor:pointer;font-size:14px;padding:3px 8px;border-radius:6px;transition:all .15s}}
.restore-btn:hover{{color:var(--success);border-color:var(--success);background:rgba(22,163,106,.1)}}
.perm-del-btn{{background:none;border:1px solid var(--border-light);color:var(--text-muted);cursor:pointer;font-size:12px;padding:3px 8px;border-radius:6px;transition:all .15s}}
.perm-del-btn:hover{{color:var(--danger);border-color:var(--danger);background:rgba(220,38,38,.1)}}

/* Action buttons */
.del-btn{{background:none;border:none;color:var(--text-muted);cursor:pointer;font-size:13px;padding:4px 6px;border-radius:4px;transition:all .15s}}
.del-btn:hover{{color:var(--danger)}}
.done-btn{{background:none;border:1px solid var(--border-light);color:var(--success);cursor:pointer;font-size:12px;padding:3px 8px;border-radius:6px;transition:all .15s}}
.done-btn:hover{{background:rgba(22,163,106,.15);border-color:var(--success)}}
.reopen-btn{{background:none;border:1px solid var(--border-light);color:var(--text-dim);cursor:pointer;font-size:14px;padding:3px 8px;border-radius:6px;transition:all .15s}}
.reopen-btn:hover{{color:var(--accent);border-color:var(--accent);background:rgba(99,102,241,.1)}}

/* Forms */
.add-form{{display:none;padding:14px;background:var(--surface);border:1px solid var(--border-light);border-radius:8px;margin-bottom:14px}}
.add-form.show{{display:block}}
.add-form input,.add-form select,.add-form textarea{{width:100%;padding:9px 12px;background:var(--card);border:1px solid var(--border);border-radius:6px;color:#e2e8f0;font-size:13px;font-family:inherit;margin-bottom:8px;outline:none;transition:border .2s}}
.add-form input:focus,.add-form select:focus,.add-form textarea:focus{{border-color:var(--accent)}}
.add-form input::placeholder,.add-form textarea::placeholder{{color:var(--text-muted)}}
.form-row{{display:flex;gap:8px}}.form-row>*{{flex:1}}
.form-actions{{display:flex;gap:8px;justify-content:flex-end;margin-top:6px}}
.btn{{padding:7px 16px;border-radius:6px;font-size:12px;font-weight:600;cursor:pointer;border:none;transition:all .2s;font-family:inherit}}
.btn-primary{{background:var(--accent);color:#fff}}.btn-primary:hover{{background:var(--accent-hover)}}
.btn-ghost{{background:transparent;color:var(--text-dim);border:1px solid var(--border)}}.btn-ghost:hover{{border-color:var(--text-dim)}}
.btn-danger{{background:transparent;color:var(--danger);border:1px solid rgba(220,38,38,.3)}}.btn-danger:hover{{background:rgba(220,38,38,.1)}}

/* Empty */
.empty{{color:var(--text-muted);font-size:12px;padding:12px;text-align:center}}
.empty-state{{padding:32px 16px;text-align:center;color:var(--text-muted)}}
.empty-icon{{font-size:28px;margin-bottom:8px;opacity:.5}}
.empty-state div:last-child{{font-size:13px}}

/* Animations */
.fade-out{{opacity:0;transform:translateY(-8px);transition:all .3s ease}}

/* Mobile */
@media(max-width:640px){{
    .stats-bar{{grid-template-columns:repeat(2,1fr);gap:8px;margin-bottom:12px}}
    .header{{padding:12px 16px}}
    .header h1{{font-size:18px}}
    .quick-capture{{margin-bottom:12px}}
    .row{{flex-direction:column;align-items:flex-start;gap:4px}}
    .right{{margin-left:28px;flex-wrap:nowrap;justify-content:flex-start}}
    .form-row{{flex-direction:column}}
    .contact-row{{flex-direction:column;align-items:flex-start}}
    .contact-actions{{width:100%;justify-content:flex-start}}
    .tab{{font-size:11px;padding:7px 4px}}
    .card{{padding:14px 16px}}
    .item{{padding:8px 10px}}
}}
</style>
</head>
<body>

<div class="header">
    <h1>Planner<span> /</span></h1>
    <div style="flex:1"></div>
    <div class="user-area">
        <span class="search-icon" onclick="openSearchModal()" title="Search">🔍</span>
        <span class="user-name">{_e(user.name)}</span>
        <a href="/dashboard/logout" class="logout-btn">Sign out</a>
    </div>
</div>

<div class="container">
    {stats_html}
    {quick_capture_html}
    {search_modal_html}

    <div class="tabs">
        <button class="tab active" onclick="showTab('today',this)">Today</button>
        <button class="tab" onclick="showTab('tasks',this)">Tasks</button>
        <button class="tab" onclick="showTab('calendar',this)">Calendar</button>
        <button class="tab" onclick="showTab('remember',this)">Remember</button>
        <button class="tab" onclick="showTab('journal',this)">Journal</button>
        <button class="tab" onclick="showTab('memos',this)">Memos</button>
        <button class="tab" onclick="showTab('trash',this)">Trash{f' <span class="tab-badge">{trash_count}</span>' if trash_count else ''}</button>
        <button class="tab" onclick="showTab('settings',this)">Settings</button>
    </div>

    <div id="today" class="tc active">
        {today_html}
    </div>

    <div id="tasks" class="tc">
        <div class="card">
            <div class="card-title"><span>Open Tasks</span><button class="add-btn" onclick="toggleForm('task-form')">+ Add task</button></div>
            <div class="add-form" id="task-form">
                <input type="text" id="task-desc" placeholder="What needs to be done?" onkeydown="if(event.key==='Enter')addTask()">
                <div class="form-row">
                    <select id="task-group" onchange="handleCustom(this,'task-group-custom')">{group_opts}</select>
                    <input type="text" id="task-group-custom" placeholder="New group name" style="display:none">
                    <select id="task-priority">
                        <option value="this_week">This Week</option>
                        <option value="do_today">Today</option>
                        <option value="urgent">Urgent</option>
                        <option value="keep_in_mind">Someday</option>
                    </select>
                </div>
                <div class="form-actions">
                    <button class="btn btn-ghost" onclick="toggleForm('task-form')">Cancel</button>
                    <button class="btn btn-primary" onclick="addTask()">Add Task</button>
                </div>
            </div>
            {tasks_html}
        </div>
        <div class="card">
            <div class="card-title">Completed</div>
            {done_html}
        </div>
    </div>

    <div id="calendar" class="tc">
        <div class="card">
            <div class="card-title"><span>Upcoming</span><button class="add-btn" onclick="toggleForm('calendar-form')">+ Create event</button></div>
            {calendar_form_html}
            {upcoming_html}
        </div>
        {f'<div class="card"><div class="card-title">Past</div>{past_html}</div>' if past_html else ''}
    </div>

    <div id="remember" class="tc">
        <div class="card">
            <div class="card-title"><span>Things to Remember</span><button class="add-btn" onclick="toggleForm('rem-form')">+ Add</button></div>
            <div class="add-form" id="rem-form">
                <input type="text" id="rem-content" placeholder="What do you want to remember?" onkeydown="if(event.key==='Enter')addRemember()">
                <div class="form-row">
                    <select id="rem-cat" onchange="handleCustom(this,'rem-cat-custom')">{cat_opts}</select>
                    <input type="text" id="rem-cat-custom" placeholder="New category" style="display:none">
                    <input type="text" id="rem-tags" placeholder="Tags (comma separated)">
                </div>
                <div class="form-actions">
                    <button class="btn btn-ghost" onclick="toggleForm('rem-form')">Cancel</button>
                    <button class="btn btn-primary" onclick="addRemember()">Save</button>
                </div>
            </div>
            {remember_html}
        </div>
    </div>

    <div id="journal" class="tc">
        <div class="card">
            <div class="card-title"><span>Daily Journal</span><button class="add-btn" onclick="toggleForm('journal-form')">+ New entry</button></div>
            {journal_form_html}
            {journal_html}
        </div>
        {topics_html}
    </div>

    <div id="memos" class="tc">
        <div class="card"><div class="card-title">Memos</div>{memos_html}</div>
    </div>

    <div id="trash" class="tc">
        <div class="card">
            <div class="card-title"><span>Trash</span>{f'<button class="btn btn-danger" onclick="emptyTrash()">Empty trash</button>' if trashed else ''}</div>
            <div style="font-size:11px;color:var(--text-muted);margin-bottom:12px">Items are permanently deleted after 10 days.</div>
            {trash_html}
        </div>
    </div>

    <div id="settings" class="tc">
        <div class="card">
            <div class="card-title"><span>SMS Notification Contacts</span><button class="add-btn" onclick="toggleForm('contact-form')">+ Add contact</button></div>
            <div style="font-size:12px;color:var(--text-dim);margin-bottom:14px;line-height:1.5">
                When you create a calendar event, these contacts can be notified via SMS.<br>
                <strong>Always</strong> = text on every event &nbsp;|&nbsp; <strong>When mentioned</strong> = text only when you say their name &nbsp;|&nbsp; <strong>Never</strong> = paused
            </div>
            <div class="add-form" id="contact-form">
                <input type="text" id="contact-name" placeholder="Name (e.g. Johnny)" onkeydown="if(event.key==='Enter')document.getElementById('contact-phone').focus()">
                <input type="text" id="contact-phone" placeholder="Phone number (e.g. +14165551234)" onkeydown="if(event.key==='Enter')addContact()">
                <select id="contact-mode">
                    <option value="always">Always notify</option>
                    <option value="mentioned">When mentioned</option>
                    <option value="never">Never</option>
                </select>
                <div class="form-actions">
                    <button class="btn btn-ghost" onclick="toggleForm('contact-form')">Cancel</button>
                    <button class="btn btn-primary" onclick="addContact()">Add Contact</button>
                </div>
            </div>
            {contacts_html}
        </div>
    </div>
</div>

<script>
function showTab(n,el){{document.querySelectorAll('.tc').forEach(e=>e.classList.remove('active'));document.querySelectorAll('.tab').forEach(e=>e.classList.remove('active'));document.getElementById(n).classList.add('active');el.classList.add('active');window.location.hash=n}}
function toggleForm(id){{document.getElementById(id).classList.toggle('show')}}
function handleCustom(sel,cid){{const c=document.getElementById(cid);if(sel.value==='__custom'){{c.style.display='block';c.focus()}}else{{c.style.display='none';c.value=''}}}}
async function api(m,u,b){{const o={{method:m,headers:{{'Content-Type':'application/json'}}}};if(b)o.body=JSON.stringify(b);return(await fetch(u,o)).json()}}
function fadeOut(id){{const el=document.getElementById(id);if(el){{el.classList.add('fade-out');setTimeout(()=>el.remove(),300)}}}}

// Restore tab from URL hash
document.addEventListener('DOMContentLoaded',function(){{
    const h=window.location.hash.replace('#','') || 'today';
    const tab=document.querySelector('.tab[onclick*="'+h+'"]');
    if(tab)showTab(h,tab);
}})

// Search
function openSearchModal(){{document.getElementById('search-modal').classList.add('show');document.getElementById('search-input').focus()}}
function closeSearchModal(){{document.getElementById('search-modal').classList.remove('show');document.getElementById('search-results').innerHTML=''}}
async function performSearch(){{
    const q=document.getElementById('search-input').value.trim();
    if(!q)return;
    const r=await api('GET','/dashboard/api/search?q='+encodeURIComponent(q));
    let html='';
    if(r.results){{
        for(const[type,items] of Object.entries(r.results)){{
            if(items.length){{
                html+='<div class="search-result-group"><div class="search-result-title">'+type+'</div>';
                items.forEach(i=>{{
                    const text=(i.description||i.title||i.content||'').substring(0,60);
                    html+='<div class="search-result-item" onclick="closeSearchModal();window.location.hash=\\''+type+'\\';setTimeout(()=>window.location.reload(),100)">'+_esc(text)+'</div>';
                }});
                html+='</div>';
            }}
        }}
    }}
    document.getElementById('search-results').innerHTML=html||'<div style="color:var(--text-muted);padding:16px">No results found</div>';
}}
document.getElementById('search-input')?.addEventListener('keydown',e=>{{if(e.key==='Enter')performSearch()}})

// Quick Capture
async function quickCapture(){{
    const text=document.getElementById('quick-capture-input').value.trim();
    if(!text)return;
    const r=await api('POST','/dashboard/api/quick-capture',{{text:text}});
    if(r.ok){{
        document.getElementById('quick-capture-input').value='';
        alert('Captured: '+r.spoken_response);
        setTimeout(()=>location.reload(),300);
    }}else{{
        alert('Error: '+(r.error||'Unknown'));
    }}
}}

// Inline editing
function makeEditable(el){{
    if(el.querySelector('input'))return;
    const type=el.getAttribute('data-type');
    const id=el.getAttribute('data-id');
    const field=el.getAttribute('data-field');
    const text=el.textContent;
    const input=document.createElement('input');
    input.className='editable-input';
    input.value=text;
    input.onblur=()=>saveEdit(type,id,field,input.value);
    input.onkeydown=e=>{{if(e.key==='Enter')input.blur();if(e.key==='Escape'){{input.remove();el.style.display=''}}}};
    el.innerHTML='';
    el.appendChild(input);
    input.focus();
    input.select();
}}
async function saveEdit(type,id,field,value){{
    if(!value.trim()){{alert('Cannot be empty');location.reload();return}}
    const endpoint=type==='task'?'/dashboard/api/tasks/'+id:type==='remember'?'/dashboard/api/remember/'+id:'/dashboard/api/journal/'+id;
    const body={{}};body[field]=value.trim();
    const r=await api('PUT',endpoint,body);
    if(r.ok)location.reload();else alert('Error');
}}

// Existing functions
async function addTask(){{
    const d=document.getElementById('task-desc').value.trim();if(!d)return;
    let g=document.getElementById('task-group').value;
    const gc=document.getElementById('task-group-custom').value.trim();
    if(g==='__custom'&&gc)g=gc;
    await api('POST','/dashboard/api/tasks',{{description:d,group:g,priority:document.getElementById('task-priority').value}});
    location.reload();
}}
async function addCalendarEvent(){{
    const title=document.getElementById('cal-title').value.trim();
    const date=document.getElementById('cal-date').value;
    const time=document.getElementById('cal-start-time').value;
    const endTime=document.getElementById('cal-end-time').value;
    const loc=document.getElementById('cal-location').value.trim();
    if(!title||!date||!time)return;
    await api('POST','/dashboard/api/calendar',{{title:title,date:date,time:time,end_time:endTime,location:loc}});
    window.location.reload();
}}
async function addJournalEntry(){{
    const c=document.getElementById('journal-content').value.trim();
    if(!c)return;
    const t=document.getElementById('journal-activity').value;
    const top=document.getElementById('journal-topic').value.trim();
    await api('POST','/dashboard/api/journal',{{content:c,activity_type:t,topic:top}});
    location.reload();
}}
async function addRemember(){{
    const c=document.getElementById('rem-content').value.trim();if(!c)return;
    let cat=document.getElementById('rem-cat').value;
    const cc=document.getElementById('rem-cat-custom').value.trim();
    if(cat==='__custom'&&cc)cat=cc;
    await api('POST','/dashboard/api/remember',{{content:c,category:cat,tags:document.getElementById('rem-tags').value.trim()}});
    location.reload();
}}
async function completeTask(id){{await api('POST','/dashboard/api/tasks/'+id+'/complete');location.reload()}}
async function reopenTask(id){{await api('POST','/dashboard/api/tasks/'+id+'/reopen');location.reload()}}
async function trashItem(entryId){{await api('POST','/dashboard/api/trash/'+entryId);fadeOut('entry-'+entryId);setTimeout(()=>location.reload(),400)}}
async function restoreItem(entryId){{await api('POST','/dashboard/api/restore/'+entryId);fadeOut('entry-'+entryId);setTimeout(()=>location.reload(),400)}}
async function permDelete(entryId){{if(!confirm('Permanently delete this item? This cannot be undone.'))return;await api('DELETE','/dashboard/api/permanent/'+entryId);fadeOut('entry-'+entryId)}}
async function emptyTrash(){{if(!confirm('Permanently delete ALL trashed items? This cannot be undone.'))return;await api('POST','/dashboard/api/empty-trash');location.reload()}}
async function addContact(){{
    const name=document.getElementById('contact-name').value.trim();
    const phone=document.getElementById('contact-phone').value.trim();
    const mode=document.getElementById('contact-mode').value;
    if(!name||!phone)return;
    await api('POST','/dashboard/api/contacts',{{name:name,phone:phone,notify_mode:mode}});
    location.reload();
}}
async function updateContactMode(id,mode){{await api('POST','/dashboard/api/contacts/'+id+'/mode',{{notify_mode:mode}})}}
async function deleteContact(id){{if(!confirm('Remove this contact?'))return;await api('DELETE','/dashboard/api/contacts/'+id);fadeOut('contact-'+id)}}
function _esc(s){{return(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;')}}
</script>
</body>
</html>'''


LOGIN_HTML = '''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Planner — Sign In</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Inter',system-ui,sans-serif;background:#06090f;color:#cdd5e0;min-height:100vh;display:flex;align-items:center;justify-content:center}
.box{width:100%;max-width:360px;padding:40px 32px;background:#111820;border:1px solid #1a2233;border-radius:16px;text-align:center}
h1{font-size:26px;font-weight:700;color:#f1f3f9;margin-bottom:6px}
h1 span{color:#6366f1;font-weight:300}
p{font-size:13px;color:#3d4d5f;margin-bottom:24px}
input{width:100%;padding:11px 14px;background:#0d1219;border:1px solid #1a2233;border-radius:8px;color:#e2e8f0;font-size:14px;font-family:inherit;outline:none;margin-bottom:14px;transition:border .2s}
input:focus{border-color:#6366f1}
input::placeholder{color:#3d4d5f}
button{width:100%;padding:11px;background:#6366f1;color:#fff;border:none;border-radius:8px;font-size:14px;font-weight:600;cursor:pointer;font-family:inherit;transition:all .2s}
button:hover{background:#4f46e5}
.error{background:rgba(220,38,38,.1);border:1px solid rgba(220,38,38,.2);color:#fca5a5;padding:10px;border-radius:8px;margin-bottom:14px;font-size:12px}
</style>
</head>
<body>
<div class="box">
    <h1>Planner<span> /</span></h1>
    <p>Enter your API key to sign in</p>
    <!--ERROR-->
    <form method="POST" action="/dashboard/login">
        <input type="password" name="api_key" placeholder="API Key" autofocus>
        <button type="submit">Sign In</button>
    </form>
</div>
</body>
</html>'''
