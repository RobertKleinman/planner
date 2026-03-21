"""
jobs/send_reminders.py — Send Due Reminders via Telegram
=========================================================
Runs every few minutes via cron. Checks for reminders that are due
and sends them as Telegram messages from Zeph.

Usage:
    python -m jobs.send_reminders

Cron (every 5 minutes):
    */5 * * * * python -m jobs.send_reminders
"""

import sys
import os
import asyncio
import logging
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import settings
from app.database import SessionLocal
from app.models import Reminder, User

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("planner.reminders")


async def send_due_reminders():
    """Find and send all due reminders."""
    if not settings.telegram_bot_token:
        logger.warning("Telegram bot token not configured, skipping reminders")
        return

    from aiogram import Bot
    bot = Bot(token=settings.telegram_bot_token)

    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc).replace(tzinfo=None)

        # Find all unsent reminders that are due
        due_reminders = (
            db.query(Reminder)
            .filter(
                Reminder.sent == False,
                Reminder.remind_at <= now,
            )
            .all()
        )

        if not due_reminders:
            logger.info("No due reminders")
            return

        for reminder in due_reminders:
            user = db.query(User).filter(User.id == reminder.user_id).first()
            if not user or not user.telegram_chat_id:
                logger.warning(f"Reminder {reminder.id}: user has no Telegram chat_id, skipping")
                reminder.sent = True
                continue

            # Send as Zeph
            message = f"\u2728 Hey — {reminder.message}"
            try:
                await bot.send_message(chat_id=user.telegram_chat_id, text=message)
                logger.info(f"Reminder {reminder.id} sent to {user.name}: {reminder.message[:60]}")
            except Exception as e:
                logger.error(f"Failed to send reminder {reminder.id}: {e}")
                continue

            if reminder.recurring:
                # Schedule next occurrence
                if reminder.recurring == "daily":
                    reminder.remind_at = reminder.remind_at + timedelta(days=1)
                elif reminder.recurring == "weekly":
                    reminder.remind_at = reminder.remind_at + timedelta(weeks=1)
                elif reminder.recurring == "weekdays":
                    next_day = reminder.remind_at + timedelta(days=1)
                    while next_day.weekday() >= 5:  # skip Saturday (5) and Sunday (6)
                        next_day += timedelta(days=1)
                    reminder.remind_at = next_day
                # Keep sent=False for recurring reminders
            else:
                reminder.sent = True

        db.commit()
        logger.info(f"Processed {len(due_reminders)} reminder(s)")

    finally:
        db.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(send_due_reminders())
