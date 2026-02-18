"""
routers/dashboard.py — Web Dashboard
======================================
"""

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session
from sqlalchemy import desc, func
from datetime import datetime, timezone
from collections import defaultdict

from app.database import get_db
from app.models import Entry, Task, CalendarEvent, RememberItem, JournalEntry

router = APIRouter(tags=["dashboard"])


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(db: Session = Depends(get_db)):

    # Tasks
    open_tasks = db.query(Task).filter(Task.status == "open").order_by(Task.priority.asc(), Task.created_at.desc()).all()
    done_tasks = db.query(Task).filter(Task.status == "done").order_by(Task.completed_at.desc()).limit(20).all()

    # Calendar
    now = datetime.now(timezone.utc)
    upcoming_events = db.query(CalendarEvent).filter(CalendarEvent.start_time >= now).order_by(CalendarEvent.start_time.asc()).limit(20).all()
    past_events = db.query(CalendarEvent).filter(CalendarEvent.start_time < now).order_by(CalendarEvent.start_time.desc()).limit(10).all()

    # Memos
    memos = db.query(Entry).filter(Entry.module == "memo").order_by(Entry.created_at.desc()).limit(20).all()

    # Remember items
    remember_items = db.query(RememberItem).order_by(RememberItem.created_at.desc()).all()

    # Journal entries
    journal_entries = db.query(JournalEntry).order_by(JournalEntry.date.desc()).limit(50).all()

    html = _render(open_tasks, done_tasks, upcoming_events, past_events, memos, remember_items, journal_entries)
    return HTMLResponse(content=html)


def _badge(priority):
    c = {"urgent": "#ef4444", "do_today": "#f97316", "this_week": "#eab308", "keep_in_mind": "#3b82f6"}.get(priority, "#6b7280")
    l = {"urgent": "Urgent", "do_today": "Do Today", "this_week": "This Week", "keep_in_mind": "Keep in Mind"}.get(priority, priority)
    return f'<span style="background:{c};color:#fff;padding:2px 8px;border-radius:12px;font-size:11px;font-weight:600;">{l}</span>'


def _fmt(dt):
    if not dt: return ""
    if isinstance(dt, str):
        try: dt = datetime.fromisoformat(dt)
        except: return dt
    return dt.strftime("%b %d, %Y %I:%M %p")


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
    return dt.strftime("%A, %B %d, %Y")


def _render(open_tasks, done_tasks, upcoming_events, past_events, memos, remember_items, journal_entries):
    # --- Tasks ---
    task_groups = {}
    for t in open_tasks:
        task_groups.setdefault(t.group, []).append(t)

    tasks_html = ""
    if task_groups:
        for group in sorted(task_groups.keys()):
            tasks_html += f'<div class="group-header">{group}</div>'
            for t in task_groups[group]:
                due = f' <span class="due">Due: {_fdate(t.due_date)}</span>' if t.due_date else ""
                tasks_html += f'<div class="task-item"><div class="task-left"><span class="task-circle"></span><span class="task-desc">{t.description}</span></div><div class="task-right">{_badge(t.priority)}{due}<span class="ts">{_fmt(t.created_at)}</span></div></div>'
    else:
        tasks_html = '<div class="empty">No open tasks. Nice work!</div>'

    done_html = ""
    if done_tasks:
        for t in done_tasks:
            done_html += f'<div class="task-item done"><div class="task-left"><span class="task-check">&#10003;</span><span class="task-desc">{t.description}</span></div><div class="task-right"><span class="tag">{t.group}</span><span class="ts">Completed {_fmt(t.completed_at)}</span></div></div>'
    else:
        done_html = '<div class="empty">No completed tasks yet.</div>'

    # --- Calendar ---
    upcoming_html = ""
    if upcoming_events:
        for ev in upcoming_events:
            loc = f' &mdash; {ev.location}' if ev.location else ""
            sms = ' <span class="sms">SMS</span>' if ev.sms_sent else ""
            upcoming_html += f'<div class="ev-item"><div class="ev-title">{ev.title}{sms}</div><div class="ev-meta">{_fmt(ev.start_time)}{loc}</div></div>'
    else:
        upcoming_html = '<div class="empty">No upcoming events.</div>'

    past_html = ""
    for ev in past_events:
        loc = f' &mdash; {ev.location}' if ev.location else ""
        past_html += f'<div class="ev-item past"><div class="ev-title">{ev.title}</div><div class="ev-meta">{_fmt(ev.start_time)}{loc}</div></div>'

    # --- Memos ---
    memos_html = ""
    if memos:
        for m in memos:
            c = (m.processed_content or m.raw_transcript or "")[:200]
            memos_html += f'<div class="memo-item"><div class="memo-title">{m.title or "Memo"}</div><div class="memo-content">{c}</div><div class="ts">{_fmt(m.created_at)}</div></div>'
    else:
        memos_html = '<div class="empty">No memos yet.</div>'

    # --- Remember ---
    remember_groups = {}
    for r in remember_items:
        remember_groups.setdefault(r.category, []).append(r)

    remember_html = ""
    if remember_groups:
        for cat in sorted(remember_groups.keys()):
            remember_html += f'<div class="group-header">{cat}</div>'
            for r in remember_groups[cat]:
                tags = ""
                if r.tags:
                    tags = " ".join(f'<span class="tag">{t.strip()}</span>' for t in r.tags.split(",") if t.strip())
                remember_html += f'<div class="remember-item"><div class="remember-content">{r.content}</div><div class="remember-meta">{tags}<span class="ts">{_fmt(r.created_at)}</span></div></div>'
    else:
        remember_html = '<div class="empty">Nothing saved yet. Say "remember that..." to add items.</div>'

    # --- Journal ---
    # Group by day
    journal_by_day = defaultdict(list)
    for j in journal_entries:
        journal_by_day[_day_key(j.date)].append(j)

    # Collect all topics
    all_topics = defaultdict(list)
    for j in journal_entries:
        if j.topic:
            all_topics[j.topic].append(j)

    journal_html = ""
    if journal_by_day:
        for day in journal_by_day:
            journal_html += f'<div class="day-header">{day}</div>'
            # Group by activity type within the day
            by_type = defaultdict(list)
            for j in journal_by_day[day]:
                by_type[j.activity_type or "general"].append(j)
            for atype in sorted(by_type.keys()):
                type_label = atype.replace("_", " ").title()
                journal_html += f'<div class="activity-type">{type_label}</div>'
                for j in by_type[atype]:
                    topic_tag = f' <span class="topic-tag">{j.topic}</span>' if j.topic else ""
                    journal_html += f'<div class="journal-item"><div class="journal-content">{j.content}{topic_tag}</div><div class="ts">{_fmt(j.created_at)}</div></div>'
    else:
        journal_html = '<div class="empty">No journal entries yet. Say what you did today.</div>'

    topics_html = ""
    if all_topics:
        topics_html = '<div class="section"><div class="section-title"><span class="icon">&#128278;</span> Topics &amp; Projects</div>'
        for topic in sorted(all_topics.keys()):
            count = len(all_topics[topic])
            latest = all_topics[topic][0]
            topics_html += f'<div class="topic-item"><div class="topic-name">{topic} <span class="topic-count">({count} entries)</span></div><div class="topic-latest">Latest: {latest.content[:80]}</div></div>'
        topics_html += '</div>'

    return f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Planner Dashboard</title>
    <style>
        * {{ margin:0; padding:0; box-sizing:border-box; }}
        body {{ font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif; background:#0f172a; color:#e2e8f0; min-height:100vh; padding:20px; }}
        .container {{ max-width:900px; margin:0 auto; }}
        h1 {{ font-size:28px; font-weight:700; margin-bottom:8px; color:#f8fafc; }}
        .subtitle {{ color:#94a3b8; font-size:14px; margin-bottom:32px; }}
        .section {{ background:#1e293b; border-radius:12px; padding:24px; margin-bottom:20px; }}
        .section-title {{ font-size:18px; font-weight:600; color:#f8fafc; margin-bottom:16px; display:flex; align-items:center; gap:8px; }}
        .icon {{ font-size:20px; }}
        .group-header,.day-header {{ font-size:13px; font-weight:700; color:#94a3b8; text-transform:uppercase; letter-spacing:1px; margin-top:16px; margin-bottom:8px; padding-bottom:4px; border-bottom:1px solid #334155; }}
        .group-header:first-child,.day-header:first-child {{ margin-top:0; }}
        .day-header {{ color:#818cf8; font-size:14px; text-transform:none; letter-spacing:0; }}
        .activity-type {{ font-size:12px; color:#64748b; font-weight:600; margin-top:8px; margin-bottom:4px; margin-left:8px; }}
        .task-item {{ display:flex; justify-content:space-between; align-items:center; padding:10px 12px; border-radius:8px; margin-bottom:4px; }}
        .task-item:hover {{ background:#334155; }}
        .task-item.done {{ opacity:0.6; }}
        .task-left {{ display:flex; align-items:center; gap:10px; flex:1; }}
        .task-circle {{ width:18px; height:18px; border:2px solid #475569; border-radius:50%; flex-shrink:0; }}
        .task-check {{ width:18px; height:18px; background:#22c55e; border-radius:50%; display:flex; align-items:center; justify-content:center; font-size:11px; color:#fff; flex-shrink:0; }}
        .task-desc {{ font-size:14px; }}
        .task-right {{ display:flex; align-items:center; gap:10px; flex-shrink:0; }}
        .tag {{ background:#334155; color:#94a3b8; padding:2px 8px; border-radius:8px; font-size:11px; }}
        .due {{ color:#f97316; font-size:12px; }}
        .ts {{ color:#64748b; font-size:11px; white-space:nowrap; }}
        .ev-item {{ padding:12px; border-left:3px solid #6366f1; margin-bottom:8px; border-radius:0 8px 8px 0; background:#1a2332; }}
        .ev-item.past {{ border-left-color:#475569; opacity:0.6; }}
        .ev-title {{ font-size:14px; font-weight:600; color:#f8fafc; }}
        .ev-meta {{ font-size:12px; color:#94a3b8; margin-top:4px; }}
        .sms {{ background:#22c55e; color:#fff; padding:1px 6px; border-radius:8px; font-size:10px; font-weight:600; margin-left:6px; }}
        .memo-item,.remember-item,.journal-item {{ padding:12px; border-radius:8px; background:#1a2332; margin-bottom:8px; }}
        .memo-title {{ font-size:14px; font-weight:600; color:#f8fafc; }}
        .memo-content,.remember-content,.journal-content {{ font-size:13px; color:#94a3b8; margin-top:4px; line-height:1.5; }}
        .remember-meta {{ display:flex; align-items:center; gap:6px; margin-top:6px; flex-wrap:wrap; }}
        .topic-tag {{ background:#4f46e5; color:#fff; padding:1px 6px; border-radius:8px; font-size:10px; font-weight:600; margin-left:6px; }}
        .topic-item {{ padding:12px; border-radius:8px; background:#1a2332; margin-bottom:8px; }}
        .topic-name {{ font-size:14px; font-weight:600; color:#f8fafc; }}
        .topic-count {{ font-size:12px; color:#64748b; font-weight:400; }}
        .topic-latest {{ font-size:12px; color:#94a3b8; margin-top:4px; }}
        .empty {{ color:#64748b; font-style:italic; padding:12px; }}
        .tabs {{ display:flex; gap:4px; margin-bottom:20px; background:#1e293b; border-radius:10px; padding:4px; flex-wrap:wrap; }}
        .tab {{ flex:1; padding:10px; text-align:center; border-radius:8px; cursor:pointer; font-size:13px; font-weight:500; color:#94a3b8; transition:all .15s; min-width:80px; }}
        .tab.active {{ background:#334155; color:#f8fafc; }}
        .tab:hover:not(.active) {{ color:#e2e8f0; }}
        .tab-content {{ display:none; }}
        .tab-content.active {{ display:block; }}
        @media(max-width:600px) {{ body{{padding:12px;}} .task-item{{flex-direction:column;align-items:flex-start;gap:6px;}} .task-right{{margin-left:28px;}} }}
    </style>
</head>
<body>
<div class="container">
    <h1>Planner</h1>
    <div class="subtitle">Your voice-powered command center</div>

    <div class="tabs">
        <div class="tab active" onclick="showTab('tasks',this)">Tasks</div>
        <div class="tab" onclick="showTab('calendar',this)">Calendar</div>
        <div class="tab" onclick="showTab('remember',this)">Remember</div>
        <div class="tab" onclick="showTab('journal',this)">Journal</div>
        <div class="tab" onclick="showTab('memos',this)">Memos</div>
    </div>

    <div id="tasks" class="tab-content active">
        <div class="section">
            <div class="section-title"><span class="icon">&#9744;</span> Open Tasks</div>
            {tasks_html}
        </div>
        <div class="section">
            <div class="section-title"><span class="icon">&#10003;</span> Completed</div>
            {done_html}
        </div>
    </div>

    <div id="calendar" class="tab-content">
        <div class="section">
            <div class="section-title"><span class="icon">&#128197;</span> Upcoming</div>
            {upcoming_html}
        </div>
        {f'<div class="section"><div class="section-title"><span class="icon">&#128336;</span> Past</div>{past_html}</div>' if past_html else ''}
    </div>

    <div id="remember" class="tab-content">
        <div class="section">
            <div class="section-title"><span class="icon">&#128161;</span> Things to Remember</div>
            {remember_html}
        </div>
    </div>

    <div id="journal" class="tab-content">
        <div class="section">
            <div class="section-title"><span class="icon">&#128214;</span> Daily Journal</div>
            {journal_html}
        </div>
        {topics_html}
    </div>

    <div id="memos" class="tab-content">
        <div class="section">
            <div class="section-title"><span class="icon">&#128221;</span> Recent Memos</div>
            {memos_html}
        </div>
    </div>
</div>
<script>
function showTab(name,el) {{
    document.querySelectorAll('.tab-content').forEach(e=>e.classList.remove('active'));
    document.querySelectorAll('.tab').forEach(e=>e.classList.remove('active'));
    document.getElementById(name).classList.add('active');
    el.classList.add('active');
}}
</script>
</body>
</html>'''
