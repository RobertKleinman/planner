"""
routers/dashboard.py — Web Dashboard
======================================
- Cookie-based auth with 90-day persistence
- Soft delete: items go to Trash, auto-purge after 10 days
- CRUD on all item types
- Polished dark UI
"""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from sqlalchemy.orm import Session
from sqlalchemy import desc, and_
from datetime import datetime, timezone, timedelta
from collections import defaultdict
import json

from app.database import get_db
from app.auth import hash_api_key
from app.models import User, Entry, Task, CalendarEvent, RememberItem, JournalEntry

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
    entry = Entry(user_id=user.id, input_type="dashboard", processed_content=body.get("description",""), title=body.get("description","Task"), module="task", module_data=json.dumps(body))
    db.add(entry); db.commit(); db.refresh(entry)
    task = Task(entry_id=entry.id, description=body.get("description",""), group=body.get("group","General"), priority=body.get("priority","this_week"), status="open")
    db.add(task); db.commit()
    return JSONResponse(content={"ok": True, "id": task.id})


@router.post("/dashboard/api/tasks/{task_id}/complete")
async def complete_task(task_id: int, request: Request, db: Session = Depends(get_db)):
    user = _user(request, db)
    if not user:
        return JSONResponse(status_code=401, content={"error": "Not logged in"})
    task = db.query(Task).join(Entry).filter(Task.id == task_id, Entry.user_id == user.id).first()
    if not task:
        return JSONResponse(status_code=404, content={"error": "Not found"})
    task.status = "done"; task.completed_at = datetime.now(timezone.utc)
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
    task.status = "open"; task.completed_at = None
    db.commit()
    return JSONResponse(content={"ok": True})


# ─── CRUD: Remember ───────────────────────────────────────

@router.post("/dashboard/api/remember")
async def add_remember(request: Request, db: Session = Depends(get_db)):
    user = _user(request, db)
    if not user:
        return JSONResponse(status_code=401, content={"error": "Not logged in"})
    body = await request.json()
    entry = Entry(user_id=user.id, input_type="dashboard", processed_content=body.get("content",""), title=body.get("content","")[:80], module="remember", module_data=json.dumps(body))
    db.add(entry); db.commit(); db.refresh(entry)
    item = RememberItem(entry_id=entry.id, content=body.get("content",""), category=body.get("category","General"), tags=body.get("tags",""))
    db.add(item); db.commit()
    return JSONResponse(content={"ok": True, "id": item.id})


# ─── Main Dashboard ───────────────────────────────────────

@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request, db: Session = Depends(get_db)):
    user = _user(request, db)
    if not user:
        return RedirectResponse("/dashboard/login", status_code=302)

    # Auto-purge old trash
    _purge_old_trash(db, user)

    # Active items (not deleted)
    open_tasks = db.query(Task).join(Entry).filter(Entry.user_id == user.id, _not_deleted(), Task.status == "open").order_by(Task.priority.asc(), Task.created_at.desc()).all()
    done_tasks = db.query(Task).join(Entry).filter(Entry.user_id == user.id, _not_deleted(), Task.status == "done").order_by(Task.completed_at.desc()).limit(20).all()
    now = datetime.now(timezone.utc)
    upcoming = db.query(CalendarEvent).join(Entry).filter(Entry.user_id == user.id, _not_deleted(), CalendarEvent.start_time >= now).order_by(CalendarEvent.start_time.asc()).limit(20).all()
    past_ev = db.query(CalendarEvent).join(Entry).filter(Entry.user_id == user.id, _not_deleted(), CalendarEvent.start_time < now).order_by(CalendarEvent.start_time.desc()).limit(10).all()
    memos = db.query(Entry).filter(Entry.user_id == user.id, Entry.module == "memo", _not_deleted()).order_by(Entry.created_at.desc()).limit(20).all()
    remember_items = db.query(RememberItem).join(Entry).filter(Entry.user_id == user.id, _not_deleted()).order_by(RememberItem.created_at.desc()).all()
    journal_entries = db.query(JournalEntry).join(Entry).filter(Entry.user_id == user.id, _not_deleted()).order_by(JournalEntry.date.desc()).limit(50).all()

    # Trash
    trashed = db.query(Entry).filter(Entry.user_id == user.id, Entry.deleted_at.isnot(None)).order_by(Entry.deleted_at.desc()).all()

    # Stats
    total_open = len(open_tasks)
    total_done_today = len([t for t in done_tasks if t.completed_at and t.completed_at.date() == datetime.now(timezone.utc).date()])
    total_journal_today = len([j for j in journal_entries if j.date and j.date.date() == datetime.now(timezone.utc).date()])

    # Existing groups/categories for dropdowns
    all_tasks = open_tasks + done_tasks
    task_groups = sorted(set(t.group for t in all_tasks)) if all_tasks else ["General", "Errands", "House", "Work", "Health", "Personal", "Dogs"]
    remember_cats = sorted(set(r.category for r in remember_items)) if remember_items else ["General", "People", "Passwords", "Home", "Work", "Reference"]

    html = _render(user, open_tasks, done_tasks, upcoming, past_ev, memos, remember_items, journal_entries, trashed, task_groups, remember_cats, total_open, total_done_today, total_journal_today)
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
    elapsed = (datetime.now(timezone.utc) - deleted_at).days
    return max(0, 10 - elapsed)


# ─── Render ──────────────────────────────────────────────

def _render(user, open_tasks, done_tasks, upcoming, past_ev, memos, remember_items, journal_entries, trashed, task_groups, remember_cats, total_open, total_done_today, total_journal_today):

    # ── Stats bar ──
    stats_html = f'''<div class="stats-bar">
        <div class="stat"><span class="stat-num">{total_open}</span><span class="stat-label">Open tasks</span></div>
        <div class="stat"><span class="stat-num">{total_done_today}</span><span class="stat-label">Done today</span></div>
        <div class="stat"><span class="stat-num">{len(upcoming)}</span><span class="stat-label">Upcoming</span></div>
        <div class="stat"><span class="stat-num">{total_journal_today}</span><span class="stat-label">Logged today</span></div>
    </div>'''

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
                    <div class="left" onclick="completeTask({t.id})"><div class="circle-wrap"><span class="circle"></span></div><span class="item-text">{_e(t.description)}</span></div>
                    <div class="right">{_badge(t.priority)} {due} <span class="ts">{_fmt(t.created_at)}</span><button class="del-btn" onclick="trashItem({t.entry_id})" title="Move to trash">&#128465;</button></div>
                </div>'''
    else:
        tasks_html = '<div class="empty-state"><div class="empty-icon">&#10003;</div><div>You\'re all caught up!</div></div>'

    done_html = ""
    if done_tasks:
        for t in done_tasks:
            done_html += f'''<div class="item row done" id="entry-{t.entry_id}">
                <div class="left" onclick="reopenTask({t.id})"><span class="check-mark">&#10003;</span><span class="item-text">{_e(t.description)}</span></div>
                <div class="right"><span class="tag">{_e(t.group)}</span><span class="ts">{_fmt(t.completed_at)}</span><button class="del-btn" onclick="trashItem({t.entry_id})" title="Move to trash">&#128465;</button></div>
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
                    <div class="rem-row"><div class="rem-content">{_e(r.content)}</div><button class="del-btn" onclick="trashItem({r.entry_id})" title="Move to trash">&#128465;</button></div>
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
        for day in jbd:
            journal_html += f'<div class="day-hdr">{day}</div>'
            by_type = defaultdict(list)
            for j in jbd[day]: by_type[j.activity_type or "general"].append(j)
            for atype in sorted(by_type.keys()):
                label = atype.replace("_"," ").title()
                journal_html += f'<div class="atype-label">{label}</div>'
                for j in by_type[atype]:
                    topic = f'<span class="topic-tag">{_e(j.topic)}</span>' if j.topic else ""
                    journal_html += f'''<div class="item" id="entry-{j.entry_id}">
                        <div class="journal-row"><div>{_e(j.content)} {topic}</div><button class="del-btn" onclick="trashItem({j.entry_id})" title="Move to trash">&#128465;</button></div>
                        <div class="ts">{_fmt(j.created_at)}</div>
                    </div>'''
    else:
        journal_html = '<div class="empty-state"><div class="empty-icon">&#128214;</div><div>Tell me what you did today</div></div>'

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
.logout-btn{{font-size:11px;color:var(--text-muted);cursor:pointer;padding:4px 10px;border:1px solid var(--border);border-radius:6px;background:transparent;transition:all .2s;text-decoration:none}}
.logout-btn:hover{{border-color:var(--accent);color:var(--accent)}}

.container{{max-width:860px;margin:0 auto;padding:20px 16px 60px}}

/* Stats */
.stats-bar{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-bottom:24px}}
.stat{{background:var(--card);border:1px solid var(--border);border-radius:var(--radius);padding:14px 16px;text-align:center}}
.stat-num{{display:block;font-size:24px;font-weight:700;color:#f1f3f9;line-height:1.2}}
.stat-label{{font-size:11px;color:var(--text-dim);text-transform:uppercase;letter-spacing:.5px;margin-top:2px}}

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
.left{{display:flex;align-items:center;gap:10px;flex:1;cursor:pointer;min-width:0}}
.right{{display:flex;align-items:center;gap:8px;flex-shrink:0;flex-wrap:wrap}}
.item-text{{font-size:13px;color:var(--text)}}
.item.done{{opacity:.45}}.item.done .item-text{{text-decoration:line-through}}

/* Circles & checks */
.circle-wrap{{flex-shrink:0}}
.circle{{display:block;width:18px;height:18px;border:2px solid var(--border-light);border-radius:50%;transition:all .2s}}
.circle:hover{{border-color:var(--accent);box-shadow:0 0 0 3px rgba(99,102,241,.15)}}
.check-mark{{width:18px;height:18px;background:var(--success);border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:10px;color:#fff;flex-shrink:0;cursor:pointer}}

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
.atype-label{{font-size:10px;color:var(--text-muted);font-weight:600;margin:6px 0 3px 4px;text-transform:uppercase;letter-spacing:.5px}}

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
.rem-content{{font-size:13px;color:var(--text);line-height:1.5;flex:1}}
.rem-meta{{display:flex;align-items:center;gap:6px;margin-top:4px;flex-wrap:wrap}}

/* Journal */
.journal-row{{display:flex;justify-content:space-between;align-items:flex-start;gap:8px}}
.journal-row>div:first-child{{flex:1;font-size:13px;line-height:1.5}}

/* Topics */
.topic-row{{padding:10px 12px;border-radius:8px;margin-bottom:3px}}.topic-row:hover{{background:rgba(255,255,255,.03)}}
.topic-name{{font-size:13px;font-weight:600;color:#e2e8f0}}
.topic-meta{{display:flex;align-items:center;gap:8px;margin-top:2px}}

/* Memos */
.memo-row{{display:flex;justify-content:space-between;align-items:flex-start;gap:8px}}
.memo-title{{font-size:13px;font-weight:600;color:#e2e8f0}}
.memo-body{{font-size:12px;color:var(--text-dim);margin-top:3px;line-height:1.5}}

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

/* Delete button (universal) */
.del-btn{{background:none;border:none;color:var(--text-muted);cursor:pointer;font-size:13px;padding:2px 6px;border-radius:4px;transition:all .15s;opacity:0}}
.item:hover .del-btn,.ev-card:hover .del-btn{{opacity:1}}
.del-btn:hover{{color:var(--danger)}}

/* Forms */
.add-form{{display:none;padding:14px;background:var(--surface);border:1px solid var(--border-light);border-radius:8px;margin-bottom:14px}}
.add-form.show{{display:block}}
.add-form input,.add-form select{{width:100%;padding:9px 12px;background:var(--card);border:1px solid var(--border);border-radius:6px;color:#e2e8f0;font-size:13px;font-family:inherit;margin-bottom:8px;outline:none;transition:border .2s}}
.add-form input:focus,.add-form select:focus{{border-color:var(--accent)}}
.add-form input::placeholder{{color:var(--text-muted)}}
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

@media(max-width:640px){{
    .stats-bar{{grid-template-columns:repeat(2,1fr)}}
    .header{{padding:12px 16px}}
    .header h1{{font-size:18px}}
    .row{{flex-direction:column;align-items:flex-start;gap:4px}}
    .right{{margin-left:28px}}
    .form-row{{flex-direction:column}}
    .del-btn{{opacity:1}}
}}
</style>
</head>
<body>

<div class="header">
    <h1>Planner<span> /</span></h1>
    <div class="user-area">
        <span class="user-name">{_e(user.name)}</span>
        <a href="/dashboard/logout" class="logout-btn">Sign out</a>
    </div>
</div>

<div class="container">
    {stats_html}

    <div class="tabs">
        <button class="tab active" onclick="showTab('tasks',this)">Tasks</button>
        <button class="tab" onclick="showTab('calendar',this)">Calendar</button>
        <button class="tab" onclick="showTab('remember',this)">Remember</button>
        <button class="tab" onclick="showTab('journal',this)">Journal</button>
        <button class="tab" onclick="showTab('memos',this)">Memos</button>
        <button class="tab" onclick="showTab('trash',this)">Trash{f' <span class="tab-badge">{trash_count}</span>' if trash_count else ''}</button>
    </div>

    <div id="tasks" class="tc active">
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
        <div class="card"><div class="card-title">Upcoming</div>{upcoming_html}</div>
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
        <div class="card"><div class="card-title">Daily Journal</div>{journal_html}</div>
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
</div>

<script>
function showTab(n,el){{document.querySelectorAll('.tc').forEach(e=>e.classList.remove('active'));document.querySelectorAll('.tab').forEach(e=>e.classList.remove('active'));document.getElementById(n).classList.add('active');el.classList.add('active')}}
function toggleForm(id){{document.getElementById(id).classList.toggle('show')}}
function handleCustom(sel,cid){{const c=document.getElementById(cid);if(sel.value==='__custom'){{c.style.display='block';c.focus()}}else{{c.style.display='none';c.value=''}}}}
async function api(m,u,b){{const o={{method:m,headers:{{'Content-Type':'application/json'}}}};if(b)o.body=JSON.stringify(b);return(await fetch(u,o)).json()}}
function fadeOut(id){{const el=document.getElementById(id);if(el){{el.classList.add('fade-out');setTimeout(()=>el.remove(),300)}}}}

async function addTask(){{
    const d=document.getElementById('task-desc').value.trim();if(!d)return;
    let g=document.getElementById('task-group').value;
    const gc=document.getElementById('task-group-custom').value.trim();
    if(g==='__custom'&&gc)g=gc;
    await api('POST','/dashboard/api/tasks',{{description:d,group:g,priority:document.getElementById('task-priority').value}});
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
