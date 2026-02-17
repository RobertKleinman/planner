"""
modules/calendar.py — Calendar Module
========================================
This is the module with real side effects. When you say "dentist tomorrow
at 2pm", this module:

1. Saves the entry to our database (for dashboard/digest)
2. Creates the event in your Google Calendar (syncs to all devices)
3. Optionally adds Johnny as an attendee (he gets a Google invite)
4. Optionally texts Johnny via Twilio (immediate SMS notification)
5. Returns a confirmation to your phone

All of that from one voice memo. This is why moving the logic server-side
is so powerful — Shortcuts could never do steps 2-4.
"""

import json
from datetime import datetime, timedelta
from sqlalchemy.orm import Session

from app.models import User, Entry, CalendarEvent
from app.schemas import InputResponse
from app.services.google_calendar import create_calendar_event
from app.services.sms import notify_johnny
from app.config import settings


async def handle_calendar(
    user: User,
    raw_input: str,
    intent_data: dict,
    db: Session,
    input_type: str = "audio",
    image_description: str = None,
) -> InputResponse:
    """
    Process a calendar event: save to DB, create in Google Calendar,
    notify Johnny if requested.
    """
    data = intent_data.get("data", {})
    event_title = data.get("title", intent_data.get("title", "Event"))
    start_time = data.get("start")
    end_time = data.get("end")
    location = data.get("location")
    should_notify_partner = data.get("notify_partner", False)

    # --- Step 1: Save to our database ---
    entry = Entry(
        user_id=user.id,
        input_type=input_type,
        raw_transcript=raw_input if input_type != "image" else None,
        raw_image_description=image_description,
        processed_content=intent_data.get("spoken_response", raw_input),
        title=event_title,
        module="calendar",
        module_data=json.dumps(data),
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)

    # --- Step 2: Create in Google Calendar ---
    # Determine attendee email (Johnny) if we should notify partner
    attendee = None
    if should_notify_partner and settings.digest_recipient_email:
        # For now, we'll use a configurable partner email.
        # TODO: Add partner_email to config once Johnny's email is known.
        attendee = None  # Will be set when we configure Johnny's Google email

    google_event = await create_calendar_event(
        title=event_title,
        start_time=start_time,
        end_time=end_time,
        location=location,
        description=f"Created by voice: {raw_input}",
        attendee_email=attendee,
    )

    # --- Step 3: Save calendar-specific data ---
    cal_event = CalendarEvent(
        entry_id=entry.id,
        google_event_id=google_event.get("id") if google_event else None,
        title=event_title,
        start_time=datetime.fromisoformat(start_time) if start_time else datetime.now(),
        end_time=datetime.fromisoformat(end_time) if end_time else None,
        location=location,
        attendee_email=attendee,
    )
    db.add(cal_event)
    db.commit()

    # --- Step 4: SMS to Johnny if requested ---
    sms_sent = False
    if should_notify_partner:
        sms_sent = await notify_johnny(
            event_title=event_title,
            event_time=start_time or "TBD",
            location=location,
        )
        cal_event.sms_sent = sms_sent
        db.commit()

    # --- Step 5: Build response ---
    response_parts = [intent_data.get("spoken_response", f"Created: {event_title}")]
    if google_event:
        response_parts.append("Added to your calendar.")
    if sms_sent:
        response_parts.append("Texted Johnny.")

    return InputResponse(
        spoken_response=" ".join(response_parts),
        entry_id=entry.id,
        module="calendar",
    )
