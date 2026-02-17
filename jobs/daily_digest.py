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

from app.config import settings
from app.database import SessionLocal
from app.models import Entry, User
from app.services.email_service import send_daily_digest
from anthropic import Anthropic


client = Anthropic(api_key=settings.anthropic_api_key)


def get_todays_entries(db, user_id: int) -> list[Entry]:
    """Get all entries from the last 24 hours."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    return (
        db.query(Entry)
        .filter(Entry.user_id == user_id, Entry.created_at >= cutoff)
        .order_by(Entry.created_at.asc())
        .all()
    )


def format_entries_for_llm(entries: list[Entry]) -> str:
    """Convert entries to a text block for Claude to summarize."""
    lines = []
    for e in entries:
        time_str = e.created_at.strftime("%I:%M %p")
        content = e.processed_content or e.raw_transcript or "No content"
        lines.append(f"[{time_str}] [{e.module}] {e.title or 'Untitled'}: {content}")
    return "\n".join(lines)


def generate_summary(entries_text: str) -> str:
    """Ask Claude to create a nice daily summary."""
    response = client.messages.create(
        model=settings.intent_model,
        max_tokens=2048,
        system="You write concise, well-organized daily summary emails. Format in HTML with clean styling. Group by category (calendar events, tasks, memos, mood, etc). Keep it brief but informative. Use a warm, personal tone.",
        messages=[{
            "role": "user",
            "content": f"Here are all my planner entries from today. Create a daily digest email summarizing my day:\n\n{entries_text}"
        }],
    )
    return response.content[0].text


async def run_digest():
    """Main digest function."""
    db = SessionLocal()
    try:
        # Get the first active user (single-user for now)
        user = db.query(User).filter(User.is_active == True).first()
        if not user:
            print("No active users found.")
            return

        entries = get_todays_entries(db, user.id)
        if not entries:
            print("No entries today. Skipping digest.")
            return

        print(f"Found {len(entries)} entries. Generating summary...")

        entries_text = format_entries_for_llm(entries)
        summary_html = generate_summary(entries_text)

        today = datetime.now().strftime("%A, %B %d")
        subject = f"📋 Your Day — {today}"

        success = await send_daily_digest(subject=subject, body_html=summary_html)

        if success:
            print(f"✓ Daily digest sent for {today}")
        else:
            print("✗ Failed to send digest")

    finally:
        db.close()


if __name__ == "__main__":
    asyncio.run(run_digest())
