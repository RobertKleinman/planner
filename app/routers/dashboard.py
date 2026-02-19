"""
routers/dashboard.py — Web Dashboard
======================================
Features:
- Cookie-based auth (remembers you across sessions)
- CRUD: add, complete, delete items from the dashboard
- Multi-user support
- Polished dark theme UI
"""

from fastapi import APIRouter, Depends, Request, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from sqlalchemy.orm import Session
from sqlalchemy import desc
from datetime import datetime, timezone
from collections import defaultdict
import json

from app.database import get_db
from app.auth import hash_api_key
from app.models import User, Entry, Task, CalendarEvent, RememberItem, JournalEntry

router = APIRouter(tags=["dashboard"])

# ─── Auth Helpers ──────────────────────────────────────────

def get_user_from_cookie(request: Request, db: Session) -> User:
    api_key = request.cookies.get("planner_auth")
    if not api_key:
        return None
    key_hash = hash_api_key(api_key)
    return db.query(User).filter(User.api_key_hash == key_hash, User.is_active == True).first()


# ─── Login / Logout ───────────────────────────────────────

@router.get("/dashboard/login", response_class=HTMLResponse)
async def login_page(request: Request, db: Session = Depends(get_db)):
    user = get_user_from_cookie(request, db)
    if user:
        return RedirectResponse("/dashboard", status_code=302)
    return HTMLResponse(content=LOGIN_HTML)


@router.post("/dashboard/login")
async def login(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    api_key = form.get("api_key", "").strip()
    if not api_key:
        return HTMLResponse(content=LOGIN_HTML.replace("<!--ERROR-->", '<div class="error">Please enter your API key.</div>'))

    key_hash = hash_api_key(api_key)
    user = db.query(User).filter(User.api_key_hash == key_hash, User.is_active == True).first()
    if not user:
        return HTMLResponse(content=LOGIN_HTML.replace("<!--ERROR-->", '<div class="error">Invalid API key.</div>'))

    response = RedirectResponse("/dashboard", status_code=302)
    response.set_cookie(
        key="planner_auth", value=api_key,
        max_age=60 * 60 * 24 * 90,  # 90 days
        httponly=True, samesite="lax",
    )
    return response


@router.get("/dashboard/logout")
async def logout():
    response = RedirectResponse("/dashboard/login", status_code=302)
    response.delete_cookie("planner_auth")
    return response


# ─── CRUD API Endpoints ───────────────────────────────────

@router.post("/dashboard/api/tasks")
async def add_task(request: Request, db: Session = Depends(get_db)):
    user = get_user_from_cookie(request, db)
    if not user:
        return JSONResponse(status_code=401, content={"error": "Not logged in"})
    body = await request.json()
    entry = Entry(
        user_id=user.id, input_type="dashboard",
        processed_content=body.get("description", ""),
        title=body.get("description", "Task"),
        module="task", module_data=json.dumps(body),
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    task = Task(
        entry_id=entry.id, description=body.get("description", ""),
        group=body.get("group", "General"),
        priority=body.get("priority", "this_week"), status="open",
    )
    db.add(task)
    db.commit()
    return JSONResponse(content={"ok": True, "id": task.id})


@router.post("/dashboard/api/tasks/{task_id}/complete")
async def complete_task(task_id: int, request: Request, db: Session = Depends(get_db)):
    user = get_user_from_cookie(request, db)
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
    user = get_user_from_cookie(request, db)
    if not user:
        return JSONResponse(status_code=401, content={"error": "Not logged in"})
    task = db.query(Task).join(Entry).filter(Task.id == task_id, Entry.user_id == user.id).first()
    if not task:
        return JSONResponse(status_code=404, content={"error": "Not found"})
    task.status = "open"
    task.completed_at = None
    db.commit()
    return JSONResponse(content={"ok": True})


@router.delete("/dashboard/api/tasks/{task_id}")
async def delete_task(task_id: int, request: Request, db: Session = Depends(get_db)):
    user = get_user_from_cookie(request, db)
    if not user:
        return JSONResponse(status_code=401, content={"error": "Not logged in"})
    task = db.query(Task).join(Entry).filter(Task.id == task_id, Entry.user_id == user.id).first()
    if not task:
        return JSONResponse(status_code=404, content={"error": "Not found"})
    entry = db.query(Entry).filter(Entry.id == task.entry_id).first()
    db.delete(task)
    if entry:
        db.delete(entry)
    db.commit()
    return JSONResponse(content={"ok": True})


@router.post("/dashboard/api/remember")
async def add_remember(request: Request, db: Session = Depends(get_db)):
    user = get_user_from_cookie(request, db)
    if not user:
        return JSONResponse(status_code=401, content={"error": "Not logged in"})
    body = await request.json()
    entry = Entry(
        user_id=user.id, input_type="dashboard",
        processed_content=body.get("content", ""),
        title=body.get("content", "")[:80],
        module="remember", module_data=json.dumps(body),
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    item = RememberItem(
        entry_id=entry.id, content=body.get("content", ""),
        category=body.get("category", "General"),
        tags=body.get("tags", ""),
    )
    db.add(item)
    db.commit()
    return JSONResponse(content={"ok": True, "id": item.id})


@router.delete("/dashboard/api/remember/{item_id}")
async def delete_remember(item_id: int, request: Request, db: Session = Depends(get_db)):
    user = get_user_from_cookie(request, db)
    if not user:
        return JSONResponse(status_code=401, content={"error": "Not logged in"})
    item = db.query(RememberItem).join(Entry).filter(RememberItem.id == item_id, Entry.user_id == user.id).first()
    if not item:
        return JSONResponse(status_code=404, content={"error": "Not found"})
    entry = db.query(Entry).filter(Entry.id == item.entry_id).first()
    db.delete(item)
    if entry:
        db.delete(entry)
    db.commit()
    return JSONResponse(content={"ok": True})


@router.delete("/dashboard/api/journal/{item_id}")
async def delete_journal(item_id: int, request: Request, db: Session = Depends(get_db)):
    user = get_user_from_cookie(request, db)
    if not user:
        return JSONResponse(status_code=401, content={"error": "Not logged in"})
    item = db.query(JournalEntry).join(Entry).filter(JournalEntry.id == item_id, Entry.user_id == user.id).first()
    if not item:
        return JSONResponse(status_code=404, content={"error": "Not found"})
    entry = db.query(Entry).filter(Entry.id == item.entry_id).first()
    db.delete(item)
    if entry:
        db.delete(entry)
    db.commit()
    return JSONResponse(content={"ok": True})


@router.delete("/dashboard/api/memos/{entry_id}")
async def delete_memo(entry_id: int, request: Request, db: Session = Depends(get_db)):
    user = get_user_from_cookie(request, db)
    if not user:
        return JSONResponse(status_code=401, content={"error": "Not logged in"})
    entry = db.query(Entry).filter(Entry.id == entry_id, Entry.user_id == user.id, Entry.module == "memo").first()
    if not entry:
        return JSONResponse(status_code=404, content={"error": "Not found"})
    db.delete(entry)
    db.commit()
    return JSONResponse(content={"ok": True})


# ─── Main Dashboard ───────────────────────────────────────

@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request, db: Session = Depends(get_db)):
    user = get_user_from_cookie(request, db)
    if not user:
        return RedirectResponse("/dashboard/login", status_code=302)

    open_tasks = db.query(Task).join(Entry).filter(Entry.user_id == user.id, Task.status == "open").order_by(Task.priority.asc(), Task.created_at.desc()).all()
    done_tasks = db.query(Task).join(Entry).filter(Entry.user_id == user.id, Task.status == "done").order_by(Task.completed_at.desc()).limit(20).all()
    now = datetime.now(timezone.utc)
    upcoming = db.query(CalendarEvent).join(Entry).filter(Entry.user_id == user.id, CalendarEvent.start_time >= now).order_by(CalendarEvent.start_time.asc()).limit(20).all()
    past_ev = db.query(CalendarEvent).join(Entry).filter(Entry.user_id == user.id, CalendarEvent.start_time < now).order_by(CalendarEvent.start_time.desc()).limit(10).all()
    memos = db.query(Entry).filter(Entry.user_id == user.id, Entry.module == "memo").order_by(Entry.created_at.desc()).limit(20).all()
    remember_items = db.query(RememberItem).join(Entry).filter(Entry.user_id == user.id).order_by(RememberItem.created_at.desc()).all()
    journal_entries = db.query(JournalEntry).join(Entry).filter(Entry.user_id == user.id).order_by(JournalEntry.date.desc()).limit(50).all()

    # Collect existing groups/categories for dropdowns
    task_groups_list = sorted(set(t.group for t in open_tasks + done_tasks)) or ["General", "Errands", "House", "Work", "Health", "Personal", "Dogs"]
    remember_cats_list = sorted(set(r.category for r in remember_items)) or ["General", "People", "Passwords", "Home", "Work", "Reference"]

    html = render_dashboard(user, open_tasks, done_tasks, upcoming, past_ev, memos, remember_items, journal_entries, task_groups_list, remember_cats_list)
    return HTMLResponse(content=html)


# ─── Rendering ────────────────────────────────────────────

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

def _esc(s):
    if not s: return ""
    return s.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;").replace('"',"&quot;").replace("'","&#39;")


def render_dashboard(user, open_tasks, done_tasks, upcoming, past_ev, memos, remember_items, journal_entries, task_groups, remember_cats):

    # ── Tasks HTML ──
    tg = defaultdict(list)
    for t in open_tasks:
        tg[t.group].append(t)

    tasks_html = ""
    if tg:
        for group in sorted(tg.keys()):
            tasks_html += f'<div class="group-hdr">{_esc(group)}</div>'
            for t in tg[group]:
                due = f'<span class="due">Due {_fdate(t.due_date)}</span>' if t.due_date else ""
                tasks_html += f'''<div class="item row" id="task-{t.id}">
                    <div class="left" onclick="completeTask({t.id})"><span class="circle"></span><span>{_esc(t.description)}</span></div>
                    <div class="right">{_badge(t.priority)} {due} <span class="ts">{_fmt(t.created_at)}</span><button class="del" onclick="deleteItem('tasks',{t.id})">&times;</button></div>
                </div>'''
    else:
        tasks_html = '<div class="empty">No open tasks — you\'re all caught up!</div>'

    done_html = ""
    if done_tasks:
        for t in done_tasks:
            done_html += f'''<div class="item row done" id="task-{t.id}">
                <div class="left" onclick="reopenTask({t.id})"><span class="check">&#10003;</span><span>{_esc(t.description)}</span></div>
                <div class="right"><span class="tag">{_esc(t.group)}</span><span class="ts">{_fmt(t.completed_at)}</span><button class="del" onclick="deleteItem('tasks',{t.id})">&times;</button></div>
            </div>'''
    else:
        done_html = '<div class="empty">No completed tasks yet.</div>'

    # Task group options
    group_opts = "".join(f'<option value="{_esc(g)}">{_esc(g)}</option>' for g in task_groups)
    group_opts += '<option value="__custom">+ New group...</option>'

    # ── Calendar HTML ──
    upcoming_html = ""
    if upcoming:
        for ev in upcoming:
            loc = f' — {_esc(ev.location)}' if ev.location else ""
            sms = '<span class="sms-dot"></span>' if ev.sms_sent else ""
            upcoming_html += f'''<div class="ev-card">
                <div class="ev-date">{_fmt(ev.start_time)}</div>
                <div class="ev-title">{sms}{_esc(ev.title)}</div>
                <div class="ev-meta">{loc}</div>
            </div>'''
    else:
        upcoming_html = '<div class="empty">No upcoming events.</div>'

    past_html = ""
    for ev in past_ev:
        loc = f' — {_esc(ev.location)}' if ev.location else ""
        past_html += f'<div class="ev-card past"><div class="ev-date">{_fmt(ev.start_time)}</div><div class="ev-title">{_esc(ev.title)}</div><div class="ev-meta">{loc}</div></div>'

    # ── Remember HTML ──
    rg = defaultdict(list)
    for r in remember_items:
        rg[r.category].append(r)

    remember_html = ""
    if rg:
        for cat in sorted(rg.keys()):
            remember_html += f'<div class="group-hdr">{_esc(cat)}</div>'
            for r in rg[cat]:
                tags = ""
                if r.tags:
                    tags = " ".join(f'<span class="tag">{_esc(t.strip())}</span>' for t in r.tags.split(",") if t.strip())
                remember_html += f'''<div class="item" id="remember-{r.id}">
                    <div class="rem-content">{_esc(r.content)}</div>
                    <div class="rem-meta">{tags}<span class="ts">{_fmt(r.created_at)}</span><button class="del" onclick="deleteItem('remember',{r.id})">&times;</button></div>
                </div>'''
    else:
        remember_html = '<div class="empty">Nothing saved yet. Say "remember that..." to add items.</div>'

    cat_opts = "".join(f'<option value="{_esc(c)}">{_esc(c)}</option>' for c in remember_cats)
    cat_opts += '<option value="__custom">+ New category...</option>'

    # ── Journal HTML ──
    jbd = defaultdict(list)
    for j in journal_entries:
        jbd[_day_key(j.date)].append(j)

    all_topics = defaultdict(list)
    for j in journal_entries:
        if j.topic:
            all_topics[j.topic].append(j)

    journal_html = ""
    if jbd:
        for day in jbd:
            journal_html += f'<div class="day-hdr">{day}</div>'
            by_type = defaultdict(list)
            for j in jbd[day]:
                by_type[j.activity_type or "general"].append(j)
            for atype in sorted(by_type.keys()):
                label = atype.replace("_"," ").title()
                journal_html += f'<div class="atype">{label}</div>'
                for j in by_type[atype]:
                    topic = f'<span class="topic-tag">{_esc(j.topic)}</span>' if j.topic else ""
                    journal_html += f'''<div class="item" id="journal-{j.id}">
                        <div>{_esc(j.content)} {topic}</div>
                        <div class="ts">{_fmt(j.created_at)} <button class="del" onclick="deleteItem('journal',{j.id})">&times;</button></div>
                    </div>'''
    else:
        journal_html = '<div class="empty">No journal entries yet.</div>'

    topics_html = ""
    if all_topics:
        topics_html = '<div class="card" style="margin-top:16px"><div class="card-title">Topics &amp; Projects</div>'
        for topic in sorted(all_topics.keys()):
            count = len(all_topics[topic])
            latest = all_topics[topic][0]
            topics_html += f'<div class="topic-row"><span class="topic-name">{_esc(topic)}</span><span class="topic-count">{count} entries</span><span class="ts">{_esc(latest.content[:60])}</span></div>'
        topics_html += '</div>'

    # ── Memos HTML ──
    memos_html = ""
    if memos:
        for m in memos:
            c = _esc((m.processed_content or m.raw_transcript or "")[:250])
            memos_html += f'''<div class="item" id="memo-{m.id}">
                <div class="memo-title">{_esc(m.title or "Memo")}</div>
                <div class="memo-body">{c}</div>
                <div class="ts">{_fmt(m.created_at)} <button class="del" onclick="deleteItem('memos',{m.id})">&times;</button></div>
            </div>'''
    else:
        memos_html = '<div class="empty">No memos yet.</div>'

    return f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Planner — {_esc(user.name)}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:'Inter',system-ui,-apple-system,sans-serif;background:#080c15;color:#d1d5e0;min-height:100vh}}
a{{color:#818cf8;text-decoration:none}}

/* Header */
.header{{background:linear-gradient(135deg,#0f1629 0%,#1a1f3a 100%);border-bottom:1px solid #1e2a42;padding:16px 24px;display:flex;justify-content:space-between;align-items:center;position:sticky;top:0;z-index:100;backdrop-filter:blur(12px)}}
.header h1{{font-size:22px;font-weight:700;color:#f1f3f9;letter-spacing:-0.5px}}
.header .user-area{{display:flex;align-items:center;gap:12px}}
.header .user-name{{font-size:13px;color:#94a3b8;font-weight:500}}
.header .logout{{font-size:12px;color:#64748b;cursor:pointer;padding:6px 12px;border:1px solid #2a3352;border-radius:6px;background:transparent;transition:all .2s}}
.header .logout:hover{{border-color:#818cf8;color:#818cf8}}

/* Layout */
.container{{max-width:920px;margin:0 auto;padding:20px 16px 40px}}

/* Tabs */
.tabs{{display:flex;gap:2px;background:#111827;border-radius:10px;padding:3px;margin-bottom:24px;overflow-x:auto}}
.tab{{flex:1;padding:10px 8px;text-align:center;border-radius:8px;cursor:pointer;font-size:13px;font-weight:500;color:#64748b;transition:all .2s;white-space:nowrap;border:none;background:none}}
.tab:hover{{color:#94a3b8}}
.tab.active{{background:linear-gradient(135deg,#1e293b,#252f4a);color:#f1f3f9;box-shadow:0 1px 3px rgba(0,0,0,.3)}}
.tab-content{{display:none}}.tab-content.active{{display:block}}

/* Cards */
.card{{background:linear-gradient(180deg,#111827 0%,#0f1420 100%);border:1px solid #1e2a42;border-radius:12px;padding:20px;margin-bottom:16px}}
.card-title{{font-size:16px;font-weight:600;color:#e8ecf4;margin-bottom:14px;display:flex;justify-content:space-between;align-items:center}}
.card-title .add-btn{{font-size:12px;color:#818cf8;cursor:pointer;padding:4px 12px;border:1px solid #2a3352;border-radius:6px;background:transparent;transition:all .2s;font-weight:500}}
.card-title .add-btn:hover{{background:#818cf820;border-color:#818cf8}}

/* Items */
.item{{padding:10px 12px;border-radius:8px;margin-bottom:4px;transition:all .2s;position:relative}}
.item:hover{{background:#1a2236}}
.row{{display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px}}
.left{{display:flex;align-items:center;gap:10px;flex:1;cursor:pointer;min-width:0}}
.right{{display:flex;align-items:center;gap:8px;flex-shrink:0;flex-wrap:wrap}}
.item.done{{opacity:.5}}
.item.done .left span:last-child{{text-decoration:line-through}}

/* Task elements */
.circle{{width:18px;height:18px;border:2px solid #374151;border-radius:50%;flex-shrink:0;transition:all .2s}}
.circle:hover{{border-color:#818cf8}}
.check{{width:18px;height:18px;background:#22c55e;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:10px;color:#fff;flex-shrink:0;cursor:pointer}}
.check:hover{{background:#16a34a}}

/* Badges & Tags */
.badge{{color:#fff;padding:2px 8px;border-radius:10px;font-size:10px;font-weight:600;letter-spacing:.3px;white-space:nowrap}}
.tag{{background:#1e293b;color:#94a3b8;padding:2px 8px;border-radius:6px;font-size:11px;white-space:nowrap}}
.topic-tag{{background:#4338ca;color:#e0e7ff;padding:1px 7px;border-radius:6px;font-size:10px;font-weight:600;margin-left:6px}}
.sms-dot{{display:inline-block;width:8px;height:8px;background:#22c55e;border-radius:50%;margin-right:6px}}
.due{{color:#f97316;font-size:11px;white-space:nowrap}}
.ts{{color:#4b5563;font-size:11px;white-space:nowrap}}

/* Headers */
.group-hdr{{font-size:11px;font-weight:700;color:#6b7280;text-transform:uppercase;letter-spacing:1.2px;margin:16px 0 8px;padding-bottom:4px;border-bottom:1px solid #1a2236}}
.group-hdr:first-child{{margin-top:0}}
.day-hdr{{font-size:13px;font-weight:600;color:#818cf8;margin:16px 0 8px;padding-bottom:4px;border-bottom:1px solid #1a2236}}
.day-hdr:first-child{{margin-top:0}}
.atype{{font-size:11px;color:#4b5563;font-weight:600;margin:8px 0 4px 4px;text-transform:uppercase;letter-spacing:.5px}}

/* Calendar events */
.ev-card{{padding:12px 14px;border-left:3px solid #6366f1;margin-bottom:8px;border-radius:0 8px 8px 0;background:#111827}}
.ev-card.past{{border-left-color:#374151;opacity:.5}}
.ev-date{{font-size:12px;color:#818cf8;font-weight:600;margin-bottom:2px}}
.ev-title{{font-size:14px;font-weight:600;color:#e8ecf4;display:flex;align-items:center}}
.ev-meta{{font-size:12px;color:#6b7280;margin-top:2px}}

/* Remember */
.rem-content{{font-size:14px;color:#d1d5e0;line-height:1.5}}
.rem-meta{{display:flex;align-items:center;gap:6px;margin-top:6px;flex-wrap:wrap}}

/* Memos */
.memo-title{{font-size:14px;font-weight:600;color:#e8ecf4}}
.memo-body{{font-size:13px;color:#94a3b8;margin-top:4px;line-height:1.5}}

/* Topics */
.topic-row{{padding:8px 12px;border-radius:8px;margin-bottom:4px;display:flex;align-items:center;gap:12px}}
.topic-row:hover{{background:#1a2236}}
.topic-name{{font-weight:600;color:#e8ecf4;font-size:14px}}
.topic-count{{font-size:11px;color:#6b7280}}

/* Delete button */
.del{{background:none;border:none;color:#374151;cursor:pointer;font-size:16px;padding:2px 6px;border-radius:4px;transition:all .15s;line-height:1}}
.del:hover{{color:#ef4444;background:#ef444420}}

/* Forms */
.add-form{{display:none;padding:12px;background:#0d1117;border:1px solid #1e2a42;border-radius:8px;margin-bottom:12px}}
.add-form.show{{display:block}}
.add-form input,.add-form select{{width:100%;padding:8px 12px;background:#111827;border:1px solid #1e2a42;border-radius:6px;color:#e8ecf4;font-size:13px;font-family:inherit;margin-bottom:8px;outline:none}}
.add-form input:focus,.add-form select:focus{{border-color:#818cf8}}
.add-form .form-row{{display:flex;gap:8px}}
.add-form .form-row>*{{flex:1}}
.form-actions{{display:flex;gap:8px;justify-content:flex-end;margin-top:4px}}
.btn{{padding:6px 16px;border-radius:6px;font-size:12px;font-weight:600;cursor:pointer;border:none;transition:all .2s;font-family:inherit}}
.btn-primary{{background:#6366f1;color:#fff}}.btn-primary:hover{{background:#4f46e5}}
.btn-ghost{{background:transparent;color:#94a3b8;border:1px solid #1e2a42}}.btn-ghost:hover{{border-color:#6b7280}}

/* Empty state */
.empty{{color:#4b5563;font-style:italic;padding:16px 12px;text-align:center}}

/* Fade out animation */
.fade-out{{opacity:0;transform:translateX(20px);transition:all .3s}}

@media(max-width:640px){{
    .header{{padding:12px 16px}}
    .header h1{{font-size:18px}}
    .row{{flex-direction:column;align-items:flex-start}}
    .right{{margin-left:28px}}
    .add-form .form-row{{flex-direction:column;gap:4px}}
}}
</style>
</head>
<body>

<div class="header">
    <h1>Planner</h1>
    <div class="user-area">
        <span class="user-name">{_esc(user.name)}</span>
        <a href="/dashboard/logout" class="logout">Sign out</a>
    </div>
</div>

<div class="container">
    <div class="tabs">
        <button class="tab active" onclick="showTab('tasks',this)">Tasks</button>
        <button class="tab" onclick="showTab('calendar',this)">Calendar</button>
        <button class="tab" onclick="showTab('remember',this)">Remember</button>
        <button class="tab" onclick="showTab('journal',this)">Journal</button>
        <button class="tab" onclick="showTab('memos',this)">Memos</button>
    </div>

    <!-- TASKS -->
    <div id="tasks" class="tab-content active">
        <div class="card">
            <div class="card-title"><span>Open Tasks</span><button class="add-btn" onclick="toggleForm('task-form')">+ Add</button></div>
            <div class="add-form" id="task-form">
                <input type="text" id="task-desc" placeholder="What needs to be done?">
                <div class="form-row">
                    <select id="task-group" onchange="handleCustomSelect(this,'task-group-custom')">
                        {group_opts}
                    </select>
                    <input type="text" id="task-group-custom" placeholder="Group name" style="display:none">
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

    <!-- CALENDAR -->
    <div id="calendar" class="tab-content">
        <div class="card">
            <div class="card-title">Upcoming</div>
            {upcoming_html}
        </div>
        {f'<div class="card"><div class="card-title">Past</div>{past_html}</div>' if past_html else ''}
    </div>

    <!-- REMEMBER -->
    <div id="remember" class="tab-content">
        <div class="card">
            <div class="card-title"><span>Things to Remember</span><button class="add-btn" onclick="toggleForm('remember-form')">+ Add</button></div>
            <div class="add-form" id="remember-form">
                <input type="text" id="rem-content" placeholder="What do you want to remember?">
                <div class="form-row">
                    <select id="rem-category" onchange="handleCustomSelect(this,'rem-cat-custom')">
                        {cat_opts}
                    </select>
                    <input type="text" id="rem-cat-custom" placeholder="Category name" style="display:none">
                    <input type="text" id="rem-tags" placeholder="Tags (comma separated)">
                </div>
                <div class="form-actions">
                    <button class="btn btn-ghost" onclick="toggleForm('remember-form')">Cancel</button>
                    <button class="btn btn-primary" onclick="addRemember()">Save</button>
                </div>
            </div>
            {remember_html}
        </div>
    </div>

    <!-- JOURNAL -->
    <div id="journal" class="tab-content">
        <div class="card">
            <div class="card-title">Daily Journal</div>
            {journal_html}
        </div>
        {topics_html}
    </div>

    <!-- MEMOS -->
    <div id="memos" class="tab-content">
        <div class="card">
            <div class="card-title">Memos</div>
            {memos_html}
        </div>
    </div>
</div>

<script>
function showTab(name,el){{
    document.querySelectorAll('.tab-content').forEach(e=>e.classList.remove('active'));
    document.querySelectorAll('.tab').forEach(e=>e.classList.remove('active'));
    document.getElementById(name).classList.add('active');
    el.classList.add('active');
}}

function toggleForm(id){{
    document.getElementById(id).classList.toggle('show');
}}

function handleCustomSelect(sel,customId){{
    const custom=document.getElementById(customId);
    if(sel.value==='__custom'){{custom.style.display='block';custom.focus()}}
    else{{custom.style.display='none';custom.value=''}}
}}

async function api(method,url,body){{
    const opts={{method,headers:{{'Content-Type':'application/json'}}}};
    if(body)opts.body=JSON.stringify(body);
    const r=await fetch(url,opts);
    return r.json();
}}

function fadeAndRemove(id){{
    const el=document.getElementById(id);
    if(el){{el.classList.add('fade-out');setTimeout(()=>el.remove(),300)}}
}}

async function addTask(){{
    const desc=document.getElementById('task-desc').value.trim();
    if(!desc)return;
    let group=document.getElementById('task-group').value;
    const custom=document.getElementById('task-group-custom').value.trim();
    if(group==='__custom'&&custom)group=custom;
    const priority=document.getElementById('task-priority').value;
    await api('POST','/dashboard/api/tasks',{{description:desc,group,priority}});
    location.reload();
}}

async function addRemember(){{
    const content=document.getElementById('rem-content').value.trim();
    if(!content)return;
    let cat=document.getElementById('rem-category').value;
    const custom=document.getElementById('rem-cat-custom').value.trim();
    if(cat==='__custom'&&custom)cat=custom;
    const tags=document.getElementById('rem-tags').value.trim();
    await api('POST','/dashboard/api/remember',{{content,category:cat,tags}});
    location.reload();
}}

async function completeTask(id){{
    await api('POST',`/dashboard/api/tasks/${{id}}/complete`);
    fadeAndRemove('task-'+id);
    setTimeout(()=>location.reload(),500);
}}

async function reopenTask(id){{
    await api('POST',`/dashboard/api/tasks/${{id}}/reopen`);
    fadeAndRemove('task-'+id);
    setTimeout(()=>location.reload(),500);
}}

async function deleteItem(type,id){{
    if(!confirm('Delete this item?'))return;
    await api('DELETE',`/dashboard/api/${{type}}/${{id}}`);
    const prefix=type==='tasks'?'task':type==='memos'?'memo':type.replace(/s$/,'');
    fadeAndRemove(prefix+'-'+id);
}}
</script>

</body>
</html>'''


# ─── Login Page HTML ──────────────────────────────────────

LOGIN_HTML = '''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Planner — Sign In</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Inter',system-ui,sans-serif;background:#080c15;color:#d1d5e0;min-height:100vh;display:flex;align-items:center;justify-content:center}
.login-box{width:100%;max-width:380px;padding:40px;background:linear-gradient(180deg,#111827 0%,#0f1420 100%);border:1px solid #1e2a42;border-radius:16px;text-align:center}
h1{font-size:28px;font-weight:700;color:#f1f3f9;margin-bottom:8px;letter-spacing:-0.5px}
p{font-size:14px;color:#64748b;margin-bottom:28px}
input{width:100%;padding:12px 16px;background:#0d1117;border:1px solid #1e2a42;border-radius:8px;color:#e8ecf4;font-size:14px;font-family:inherit;outline:none;margin-bottom:16px;transition:border .2s}
input:focus{border-color:#818cf8}
button{width:100%;padding:12px;background:linear-gradient(135deg,#6366f1,#4f46e5);color:#fff;border:none;border-radius:8px;font-size:14px;font-weight:600;cursor:pointer;font-family:inherit;transition:all .2s}
button:hover{opacity:.9;transform:translateY(-1px)}
.error{background:#dc262620;border:1px solid #dc262640;color:#fca5a5;padding:10px;border-radius:8px;margin-bottom:16px;font-size:13px}
</style>
</head>
<body>
<div class="login-box">
    <h1>Planner</h1>
    <p>Enter your API key to sign in</p>
    <!--ERROR-->
    <form method="POST" action="/dashboard/login">
        <input type="password" name="api_key" placeholder="API Key" autofocus>
        <button type="submit">Sign In</button>
    </form>
</div>
</body>
</html>'''
