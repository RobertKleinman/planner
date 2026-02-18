"""
routers/dashboard.py — Web Dashboard
======================================
Serves an HTML dashboard showing tasks, calendar events, and memos.
No authentication on the page itself (protected by obscurity for now).
API calls from the page use the API key.
"""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session
from sqlalchemy import desc
from datetime import datetime, timezone

from app.database import get_db
from app.models import Entry, Task, CalendarEvent

router = APIRouter(tags=["dashboard"])


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(db: Session = Depends(get_db)):
    """Serve the main dashboard page."""

    # Fetch open tasks grouped by category
    open_tasks = (
        db.query(Task)
        .filter(Task.status == "open")
        .order_by(
            # Priority ordering: urgent first, keep_in_mind last
            Task.priority.asc(),
            Task.created_at.desc(),
        )
        .all()
    )

    # Fetch recently completed tasks
    done_tasks = (
        db.query(Task)
        .filter(Task.status == "done")
        .order_by(Task.completed_at.desc())
        .limit(20)
        .all()
    )

    # Fetch upcoming calendar events
    calendar_events = (
        db.query(CalendarEvent)
        .filter(CalendarEvent.start_time >= datetime.now(timezone.utc))
        .order_by(CalendarEvent.start_time.asc())
        .limit(20)
        .all()
    )

    # Fetch past calendar events
    past_events = (
        db.query(CalendarEvent)
        .filter(CalendarEvent.start_time < datetime.now(timezone.utc))
        .order_by(CalendarEvent.start_time.desc())
        .limit(10)
        .all()
    )

    # Fetch recent memos
    memos = (
        db.query(Entry)
        .filter(Entry.module == "memo")
        .order_by(Entry.created_at.desc())
        .limit(20)
        .all()
    )

    # Group open tasks by category
    task_groups = {}
    for task in open_tasks:
        if task.group not in task_groups:
            task_groups[task.group] = []
        task_groups[task.group].append(task)

    # Build HTML
    html = _render_dashboard(task_groups, done_tasks, calendar_events, past_events, memos)
    return HTMLResponse(content=html)


def _priority_badge(priority: str) -> str:
    colors = {
        "urgent": "#ef4444",
        "do_today": "#f97316",
        "this_week": "#eab308",
        "keep_in_mind": "#3b82f6",
    }
    labels = {
        "urgent": "Urgent",
        "do_today": "Do Today",
        "this_week": "This Week",
        "keep_in_mind": "Keep in Mind",
    }
    color = colors.get(priority, "#6b7280")
    label = labels.get(priority, priority)
    return f'<span style="background:{color};color:#fff;padding:2px 8px;border-radius:12px;font-size:11px;font-weight:600;letter-spacing:0.3px;">{label}</span>'


def _format_dt(dt) -> str:
    if not dt:
        return ""
    if isinstance(dt, str):
        try:
            dt = datetime.fromisoformat(dt)
        except ValueError:
            return dt
    return dt.strftime("%b %d, %Y %I:%M %p")


def _format_date(dt) -> str:
    if not dt:
        return ""
    if isinstance(dt, str):
        try:
            dt = datetime.fromisoformat(dt)
        except ValueError:
            return dt
    return dt.strftime("%b %d, %Y")


def _render_dashboard(task_groups, done_tasks, calendar_events, past_events, memos) -> str:
    # --- Task sections ---
    tasks_html = ""
    if task_groups:
        for group, tasks in sorted(task_groups.items()):
            tasks_html += f'<div class="group-header">{group}</div>'
            for t in tasks:
                due = f' <span class="due">Due: {_format_date(t.due_date)}</span>' if t.due_date else ""
                tasks_html += f'''
                <div class="task-item">
                    <div class="task-left">
                        <span class="task-circle"></span>
                        <span class="task-desc">{t.description}</span>
                    </div>
                    <div class="task-right">
                        {_priority_badge(t.priority)}{due}
                        <span class="timestamp">{_format_dt(t.created_at)}</span>
                    </div>
                </div>'''
    else:
        tasks_html = '<div class="empty">No open tasks. Nice work!</div>'

    # --- Done tasks ---
    done_html = ""
    if done_tasks:
        for t in done_tasks:
            done_html += f'''
            <div class="task-item done">
                <div class="task-left">
                    <span class="task-check">&#10003;</span>
                    <span class="task-desc">{t.description}</span>
                </div>
                <div class="task-right">
                    <span class="task-group-tag">{t.group}</span>
                    <span class="timestamp">Completed {_format_dt(t.completed_at)}</span>
                </div>
            </div>'''
    else:
        done_html = '<div class="empty">No completed tasks yet.</div>'

    # --- Calendar events ---
    upcoming_html = ""
    if calendar_events:
        for ev in calendar_events:
            loc = f' &mdash; {ev.location}' if ev.location else ""
            sms = ' <span class="sms-badge">SMS sent</span>' if ev.sms_sent else ""
            upcoming_html += f'''
            <div class="event-item">
                <div class="event-title">{ev.title}{sms}</div>
                <div class="event-meta">{_format_dt(ev.start_time)}{loc}</div>
            </div>'''
    else:
        upcoming_html = '<div class="empty">No upcoming events.</div>'

    past_html = ""
    if past_events:
        for ev in past_events:
            loc = f' &mdash; {ev.location}' if ev.location else ""
            past_html += f'''
            <div class="event-item past">
                <div class="event-title">{ev.title}</div>
                <div class="event-meta">{_format_dt(ev.start_time)}{loc}</div>
            </div>'''

    # --- Memos ---
    memos_html = ""
    if memos:
        for m in memos:
            content = m.processed_content or m.raw_transcript or ""
            if len(content) > 200:
                content = content[:200] + "..."
            memos_html += f'''
            <div class="memo-item">
                <div class="memo-title">{m.title or "Memo"}</div>
                <div class="memo-content">{content}</div>
                <div class="timestamp">{_format_dt(m.created_at)}</div>
            </div>'''
    else:
        memos_html = '<div class="empty">No memos yet.</div>'

    return f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Planner Dashboard</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            background: #0f172a;
            color: #e2e8f0;
            min-height: 100vh;
            padding: 20px;
        }}
        .container {{
            max-width: 900px;
            margin: 0 auto;
        }}
        h1 {{
            font-size: 28px;
            font-weight: 700;
            margin-bottom: 8px;
            color: #f8fafc;
        }}
        .subtitle {{
            color: #94a3b8;
            font-size: 14px;
            margin-bottom: 32px;
        }}
        .section {{
            background: #1e293b;
            border-radius: 12px;
            padding: 24px;
            margin-bottom: 20px;
        }}
        .section-title {{
            font-size: 18px;
            font-weight: 600;
            color: #f8fafc;
            margin-bottom: 16px;
            display: flex;
            align-items: center;
            gap: 8px;
        }}
        .section-title .icon {{
            font-size: 20px;
        }}
        .group-header {{
            font-size: 13px;
            font-weight: 700;
            color: #94a3b8;
            text-transform: uppercase;
            letter-spacing: 1px;
            margin-top: 16px;
            margin-bottom: 8px;
            padding-bottom: 4px;
            border-bottom: 1px solid #334155;
        }}
        .group-header:first-child {{
            margin-top: 0;
        }}
        .task-item {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 10px 12px;
            border-radius: 8px;
            margin-bottom: 4px;
            transition: background 0.15s;
        }}
        .task-item:hover {{
            background: #334155;
        }}
        .task-item.done {{
            opacity: 0.6;
        }}
        .task-left {{
            display: flex;
            align-items: center;
            gap: 10px;
            flex: 1;
        }}
        .task-circle {{
            width: 18px;
            height: 18px;
            border: 2px solid #475569;
            border-radius: 50%;
            flex-shrink: 0;
        }}
        .task-check {{
            width: 18px;
            height: 18px;
            background: #22c55e;
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 11px;
            color: #fff;
            flex-shrink: 0;
        }}
        .task-desc {{
            font-size: 14px;
        }}
        .task-right {{
            display: flex;
            align-items: center;
            gap: 10px;
            flex-shrink: 0;
        }}
        .task-group-tag {{
            background: #334155;
            color: #94a3b8;
            padding: 2px 8px;
            border-radius: 8px;
            font-size: 11px;
        }}
        .due {{
            color: #f97316;
            font-size: 12px;
        }}
        .timestamp {{
            color: #64748b;
            font-size: 11px;
            white-space: nowrap;
        }}
        .event-item {{
            padding: 12px;
            border-left: 3px solid #6366f1;
            margin-bottom: 8px;
            border-radius: 0 8px 8px 0;
            background: #1a2332;
        }}
        .event-item.past {{
            border-left-color: #475569;
            opacity: 0.6;
        }}
        .event-title {{
            font-size: 14px;
            font-weight: 600;
            color: #f8fafc;
        }}
        .event-meta {{
            font-size: 12px;
            color: #94a3b8;
            margin-top: 4px;
        }}
        .sms-badge {{
            background: #22c55e;
            color: #fff;
            padding: 1px 6px;
            border-radius: 8px;
            font-size: 10px;
            font-weight: 600;
            margin-left: 6px;
        }}
        .memo-item {{
            padding: 12px;
            border-radius: 8px;
            background: #1a2332;
            margin-bottom: 8px;
        }}
        .memo-title {{
            font-size: 14px;
            font-weight: 600;
            color: #f8fafc;
        }}
        .memo-content {{
            font-size: 13px;
            color: #94a3b8;
            margin-top: 4px;
            line-height: 1.5;
        }}
        .empty {{
            color: #64748b;
            font-style: italic;
            padding: 12px;
        }}
        .tabs {{
            display: flex;
            gap: 4px;
            margin-bottom: 20px;
            background: #1e293b;
            border-radius: 10px;
            padding: 4px;
        }}
        .tab {{
            flex: 1;
            padding: 10px;
            text-align: center;
            border-radius: 8px;
            cursor: pointer;
            font-size: 14px;
            font-weight: 500;
            color: #94a3b8;
            transition: all 0.15s;
        }}
        .tab.active {{
            background: #334155;
            color: #f8fafc;
        }}
        .tab:hover:not(.active) {{
            color: #e2e8f0;
        }}
        .tab-content {{
            display: none;
        }}
        .tab-content.active {{
            display: block;
        }}
        @media (max-width: 600px) {{
            body {{ padding: 12px; }}
            .task-item {{ flex-direction: column; align-items: flex-start; gap: 6px; }}
            .task-right {{ margin-left: 28px; }}
        }}
    </style>
</head>
<body>
    <div class="container">
        <h1>Planner</h1>
        <div class="subtitle">Your voice-powered command center</div>

        <div class="tabs">
            <div class="tab active" onclick="showTab('tasks')">Tasks</div>
            <div class="tab" onclick="showTab('calendar')">Calendar</div>
            <div class="tab" onclick="showTab('memos')">Memos</div>
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
                <div class="section-title"><span class="icon">&#128197;</span> Upcoming Events</div>
                {upcoming_html}
            </div>
            {f'<div class="section"><div class="section-title"><span class="icon">&#128336;</span> Past Events</div>{past_html}</div>' if past_html else ''}
        </div>

        <div id="memos" class="tab-content">
            <div class="section">
                <div class="section-title"><span class="icon">&#128221;</span> Recent Memos</div>
                {memos_html}
            </div>
        </div>
    </div>

    <script>
        function showTab(name) {{
            document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
            document.querySelectorAll('.tab').forEach(el => el.classList.remove('active'));
            document.getElementById(name).classList.add('active');
            event.target.classList.add('active');
        }}
    </script>
</body>
</html>'''
