"""
jobs/daily_digest.py — Daily Morning + Weekly Sunday Digest
============================================================
Run via cron:
  Daily  7am:  python -m jobs.daily_digest --daily
  Sunday 9am:  python -m jobs.daily_digest --weekly

Adaptive skip: won't send if there's nothing useful to show.
"""

import sys
import os
import argparse
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from app.config import settings
from app.database import SessionLocal
from app.models import Entry, User, Task, CalendarEvent, RememberItem, JournalEntry
from app.services.email_service import send_email
from app.services.clients import anthropic_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("planner.digest")

LOCAL_TZ = ZoneInfo(settings.timezone)


# ── Queries ──────────────────────────────────────────────

def _entries_in_range(db, user_id: int, start, end):
    return db.query(Entry).filter(
        Entry.user_id == user_id,
        Entry.deleted_at.is_(None),
        Entry.created_at >= start,
        Entry.created_at < end,
    ).order_by(Entry.created_at.asc()).all()


def _events_on_date(db, user_id: int, start, end):
    return db.query(CalendarEvent).join(Entry).filter(
        Entry.user_id == user_id,
        Entry.deleted_at.is_(None),
        CalendarEvent.start_time >= start,
        CalendarEvent.start_time < end,
    ).order_by(CalendarEvent.start_time.asc()).all()


def _open_tasks(db, user_id: int):
    return db.query(Task).join(Entry).filter(
        Entry.user_id == user_id,
        Entry.deleted_at.is_(None),
        Task.status == "open",
    ).order_by(Task.priority.asc(), Task.created_at.desc()).all()


def _random_remember_item(db, user_id: int):
    from sqlalchemy.sql.expression import func
    return db.query(RememberItem).join(Entry).filter(
        Entry.user_id == user_id,
        Entry.deleted_at.is_(None),
    ).order_by(func.random()).first()


def _journal_entries_in_range(db, user_id: int, start, end):
    return db.query(JournalEntry).join(Entry).filter(
        Entry.user_id == user_id,
        Entry.deleted_at.is_(None),
        JournalEntry.date >= start,
        JournalEntry.date < end,
    ).order_by(JournalEntry.date.asc()).all()


def _tasks_completed_in_range(db, user_id: int, start, end):
    return db.query(Task).join(Entry).filter(
        Entry.user_id == user_id,
        Entry.deleted_at.is_(None),
        Task.status == "done",
        Task.completed_at >= start,
        Task.completed_at < end,
    ).all()


# ── Daily ────────────────────────────────────────────────

def _build_daily_context(db, user_id: int) -> str | None:
    now_local = datetime.now(LOCAL_TZ)
    today_start = datetime.combine(now_local.date(), datetime.min.time(), tzinfo=LOCAL_TZ)
    today_end = today_start + timedelta(days=1)
    yesterday_start = today_start - timedelta(days=1)

    yesterday_entries = _entries_in_range(db, user_id, yesterday_start, today_start)
    today_events = _events_on_date(db, user_id, today_start, today_end)
    open_tasks = _open_tasks(db, user_id)
    remember = _random_remember_item(db, user_id)

    urgent_tasks = [t for t in open_tasks if t.priority in ("urgent", "do_today")]

    # Adaptive skip: nothing to show
    if not yesterday_entries and not today_events and not urgent_tasks:
        return None

    lines = []

    if today_events:
        lines.append("== TODAY'S EVENTS ==")
        for ev in today_events:
            time_str = ev.start_time.astimezone(LOCAL_TZ).strftime("%I:%M %p") if ev.start_time else "TBD"
            loc = f" @ {ev.location}" if ev.location else ""
            lines.append(f"  {time_str}: {ev.title}{loc}")

    if yesterday_entries:
        lines.append("\n== YESTERDAY'S CAPTURES ==")
        for e in yesterday_entries:
            time_str = e.created_at.astimezone(LOCAL_TZ).strftime("%I:%M %p") if e.created_at.tzinfo else e.created_at.strftime("%I:%M %p")
            content = e.processed_content or e.raw_transcript or "No content"
            lines.append(f"  [{time_str}] [{e.module}] {e.title or 'Untitled'}: {content[:120]}")

    if urgent_tasks:
        lines.append(f"\n== TASKS NEEDING ATTENTION ({len(urgent_tasks)}) ==")
        priority_labels = {"urgent": "URGENT", "do_today": "Today"}
        for t in urgent_tasks:
            lines.append(f"  [{priority_labels.get(t.priority, t.priority)}] {t.description} — {t.group}")

    if remember:
        lines.append(f"\n== WORTH REVISITING ==")
        lines.append(f"  [{remember.category}] {remember.content[:150]}")

    return "\n".join(lines)


def _generate_daily_html(context: str) -> str:
    response = anthropic_client.messages.create(
        model=settings.intent_model,
        max_tokens=2048,
        system="""You write concise morning briefing emails. Format in HTML with inline CSS.

Tone: forward-looking, scannable, 30-second read. Brief and punchy.

Structure:
1. One-sentence "good morning" opener referencing the day
2. Today's events (if any) — times and titles
3. Yesterday's captures — cleaned up, grouped logically
4. Tasks needing attention — only urgent/today priority
5. One "worth revisiting" item (if provided)

Use a clean dark theme (background: #111820, text: #cdd5e0, accent: #6366f1, headers: #e2e8f0) with inline CSS for email compatibility.
Keep it SHORT. The user is a skimmer.""",
        messages=[{"role": "user", "content": f"Create my morning briefing:\n\n{context}"}],
    )
    return response.content[0].text


# ── Weekly ───────────────────────────────────────────────

def _build_weekly_context(db, user_id: int) -> str | None:
    now_local = datetime.now(LOCAL_TZ)
    week_start = datetime.combine(now_local.date() - timedelta(days=7), datetime.min.time(), tzinfo=LOCAL_TZ)
    week_end = datetime.combine(now_local.date(), datetime.min.time(), tzinfo=LOCAL_TZ)

    entries = _entries_in_range(db, user_id, week_start, week_end)
    journals = _journal_entries_in_range(db, user_id, week_start, week_end)
    completed = _tasks_completed_in_range(db, user_id, week_start, week_end)
    open_tasks = _open_tasks(db, user_id)

    if not entries and not journals and not completed:
        return None

    lines = []

    if journals:
        lines.append("== JOURNAL ENTRIES THIS WEEK ==")
        for j in journals:
            date_str = j.date.astimezone(LOCAL_TZ).strftime("%A") if j.date.tzinfo else j.date.strftime("%A")
            tags = f" [{j.tags}]" if j.tags else ""
            lines.append(f"  {date_str}: {j.content[:200]}{tags}")

    # Memos/ideas from the week
    idea_entries = [e for e in entries if e.module == "memo"]
    if idea_entries:
        lines.append(f"\n== MEMOS & IDEAS THIS WEEK ({len(idea_entries)}) ==")
        for e in idea_entries[:15]:
            lines.append(f"  - {e.title or 'Untitled'}: {(e.processed_content or '')[:120]}")

    if completed:
        lines.append(f"\n== TASKS COMPLETED ({len(completed)}) ==")
        for t in completed:
            lines.append(f"  ✓ {t.description} [{t.group}]")

    if open_tasks:
        lines.append(f"\n== STILL OPEN ({len(open_tasks)}) ==")
        priority_labels = {"urgent": "URGENT", "do_today": "Today", "this_week": "This Week", "keep_in_mind": "Someday"}
        for t in open_tasks[:20]:
            lines.append(f"  [{priority_labels.get(t.priority, t.priority)}] {t.description} — {t.group}")

    return "\n".join(lines)


def _generate_weekly_html(context: str) -> str:
    response = anthropic_client.messages.create(
        model=settings.intent_model,
        max_tokens=3000,
        system="""You write weekly reflection/synthesis emails. Format in HTML with inline CSS.

Tone: reflective, synthesizing patterns, insightful. Not just a list — find connections.

Structure:
1. One-sentence "week in review" opener
2. Journal entries — cleaned up, grouped by THEME (not chronologically)
3. Ideas captured — highlight anything interesting
4. Tasks: completed vs still open (brief counts + highlights)
5. Patterns/observations — what themes emerge across the week's entries?

Use a clean dark theme (background: #111820, text: #cdd5e0, accent: #6366f1, headers: #e2e8f0) with inline CSS.
Be concise but thoughtful. Group by theme, not by day.""",
        messages=[{"role": "user", "content": f"Create my weekly synthesis:\n\n{context}"}],
    )
    return response.content[0].text


# ── Main ─────────────────────────────────────────────────

def run_daily():
    db = SessionLocal()
    try:
        users = db.query(User).filter(User.is_active == True).all()
        for user in users:
            if not user.email:
                continue
            context = _build_daily_context(db, user.id)
            if not context:
                logger.info(f"Skipping daily for {user.name} — nothing to report")
                continue

            logger.info(f"Generating daily digest for {user.name}")
            html = _generate_daily_html(context)
            today = datetime.now(LOCAL_TZ).strftime("%A, %B %d")
            send_email(user.email, f"Good morning — {today}", html)
            logger.info(f"Daily digest sent to {user.email}")
    finally:
        db.close()


def run_weekly():
    db = SessionLocal()
    try:
        users = db.query(User).filter(User.is_active == True).all()
        for user in users:
            if not user.email:
                continue
            context = _build_weekly_context(db, user.id)
            if not context:
                logger.info(f"Skipping weekly for {user.name} — nothing to report")
                continue

            logger.info(f"Generating weekly digest for {user.name}")
            html = _generate_weekly_html(context)
            week_of = datetime.now(LOCAL_TZ).strftime("%B %d")
            send_email(user.email, f"Your week — {week_of}", html)
            logger.info(f"Weekly digest sent to {user.email}")
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Planner digest emails")
    parser.add_argument("--daily", action="store_true", help="Send daily morning digest")
    parser.add_argument("--weekly", action="store_true", help="Send weekly synthesis")
    args = parser.parse_args()

    if not args.daily and not args.weekly:
        print("Usage: python -m jobs.daily_digest --daily|--weekly")
        sys.exit(1)

    if args.daily:
        run_daily()
    if args.weekly:
        run_weekly()
