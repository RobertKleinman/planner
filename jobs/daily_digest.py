"""
jobs/daily_digest.py — Daily Summary Email
============================================
Run this as a cron job every evening. It:
1. Queries all entries from today
2. Sends them to Claude for summarization
3. Emails you a nicely formatted digest

CRON SETUP (on your VPS):
  crontab -e
  # Run at 9pm Toronto time every day:
  0 21 * * * cd /path/to/planner && /path/to/venv/bin/python -m jobs.daily_digest

HOW CRON WORKS:
Cron is a built-in Linux scheduler. The format is:
  minute hour day-of-month month day-of-week command
  0      21   *             *     *            → 9:00 PM every day
"""

import sys
import os
import json
import asyncio
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import logging
from app.config import settings
from app.database import SessionLocal
from app.models import Entry, User, Task, CalendarEvent
from app.services.email_service import send_daily_digest
from anthropic import Anthropic

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("planner.digest")

client = Anthropic(api_key=settings.anthropic_api_key)


def get_todays_entries(db, user_id: int) -> list[Entry]:
    """Get all entries from the last 24 hours."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    return (
        db.query(Entry)
        .filter(Entry.user_id == user_id, Entry.created_at >= cutoff, Entry.deleted_at.is_(None))
        .order_by(Entry.created_at.asc())
        .all()
    )


def get_open_tasks(db, user_id: int) -> list[Task]:
    """Get all open tasks."""
    return (
        db.query(Task).join(Entry)
        .filter(Entry.user_id == user_id, Entry.deleted_at.is_(None), Task.status == "open")
        .order_by(Task.priority.asc())
        .all()
    )


def get_upcoming_events(db, user_id: int) -> list[CalendarEvent]:
    """Get calendar events for the next 3 days."""
    now = datetime.now(timezone.utc)
    future = now + timedelta(days=3)
    return (
        db.query(CalendarEvent).join(Entry)
        .filter(Entry.user_id == user_id, Entry.deleted_at.is_(None),
                CalendarEvent.start_time >= now, CalendarEvent.start_time <= future)
        .order_by(CalendarEvent.start_time.asc())
        .all()
    )


def format_entries_for_llm(entries: list[Entry], open_tasks: list[Task], upcoming: list[CalendarEvent]) -> str:
    """Convert entries + context to a text block for Claude to summarize."""
    lines = []

    # Today's entries
    lines.append("== TODAY'S ENTRIES ==")
    for e in entries:
        time_str = e.created_at.strftime("%I:%M %p")
        content = e.processed_content or e.raw_transcript or "No content"
        lines.append(f"[{time_str}] [{e.module}] {e.title or 'Untitled'}: {content}")

    # Tasks completed today
    completed_today = [e for e in entries if e.module == "task" and e.task and e.task.status == "done"]
    if completed_today:
        lines.append("\n== TASKS COMPLETED TODAY ==")
        for e in completed_today:
            lines.append(f"  ✓ {e.task.description} [{e.task.group}]")

    # Open tasks
    if open_tasks:
        lines.append(f"\n== OPEN TASKS ({len(open_tasks)} total) ==")
        priority_labels = {"urgent": "URGENT", "do_today": "Today", "this_week": "This Week", "keep_in_mind": "Someday"}
        for t in open_tasks:
            lines.append(f"  [{priority_labels.get(t.priority, t.priority)}] {t.description} — {t.group}")

    # Upcoming events
    if upcoming:
        lines.append("\n== UPCOMING EVENTS (next 3 days) ==")
        for ev in upcoming:
            time_str = ev.start_time.strftime("%A %I:%M %p") if ev.start_time else "TBD"
            loc = f" @ {ev.location}" if ev.location else ""
            lines.append(f"  {time_str}: {ev.title}{loc}")

    return "\n".join(lines)


def generate_summary(entries_text: str) -> str:
    """Ask Claude to create a nice daily summary."""
    response = client.messages.create(
        model=settings.intent_model,
        max_tokens=2048,
        system="""You write concise, well-organized daily summary emails. Format in HTML with clean, inline styling.

Structure the digest as:
1. A brief 1-2 sentence overview of the day
2. "What You Did" — grouped by category (calendar events, journal, memos)
3. "Tasks Completed" — what got done today
4. "Still Open" — open tasks, grouped by priority (show counts)
5. "Coming Up" — next 3 days of events

Use a warm, personal tone. Keep it scannable with headers and short bullets.
Use a clean dark theme (background: #111820, text: #cdd5e0, accent: #6366f1) with inline CSS for email compatibility.""",
        messages=[{
            "role": "user",
            "content": f"Here's my planner data. Create my daily digest email:\n\n{entries_text}"
        }],
    )
    return response.content[0].text


async def run_digest():
    """Main digest function."""
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.is_active == True).first()
        if not user:
            logger.warning("No active users found.")
            return

        entries = get_todays_entries(db, user.id)
        open_tasks = get_open_tasks(db, user.id)
        upcoming = get_upcoming_events(db, user.id)

        if not entries and not open_tasks and not upcoming:
            logger.info("No entries, tasks, or events. Skipping digest.")
            return

        logger.info(f"Generating digest: {len(entries)} entries, {len(open_tasks)} open tasks, {len(upcoming)} upcoming events")

        entries_text = format_entries_for_llm(entries, open_tasks, upcoming)
        summary_html = generate_summary(entries_text)

        today = datetime.now().strftime("%A, %B %d")
        subject = f"Your Day — {today}"

        success = await send_daily_digest(subject=subject, body_html=summary_html)

        if success:
            logger.info(f"Daily digest sent for {today}")
        else:
            logger.error("Failed to send digest")

    finally:
        db.close()


if __name__ == "__main__":
    asyncio.run(run_digest())
