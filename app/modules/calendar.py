"""
modules/calendar.py — Calendar Module
========================================
Creates Google Calendar events and notifies contacts via SMS.
Uses NotificationContact table for dynamic SMS routing:
- "always" = text on every calendar event
- "mentioned" = text only when their name appears in the voice input
- "never" = never text
"""

import json
from datetime import datetime, timedelta
from sqlalchemy.orm import Session

from app.models import User, Entry, CalendarEvent, NotificationContact
from app.schemas import InputResponse
from app.services.google_calendar import create_calendar_event
from app.services.sms import send_sms
from app.config import settings


async def handle_calendar(
    user: User,
    raw_input: str,
    intent_data: dict,
    db: Session,
    input_type: str = "audio",
    image_description: str = None,
) -> InputResponse:
    data = intent_data.get("data", {})
    event_title = data.get("title", intent_data.get("title", "Event"))
    start_time = data.get("start")
    end_time = data.get("end")
    location = data.get("location")

    # --- Save to our database ---
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

    # --- Create in Google Calendar ---
    google_event = await create_calendar_event(
        title=event_title,
        start_time=start_time,
        end_time=end_time,
        location=location,
        description=f"Created by voice: {raw_input}",
        attendee_email=None,
    )

    # --- Save calendar-specific data ---
    cal_event = CalendarEvent(
        entry_id=entry.id,
        google_event_id=google_event.get("id") if google_event else None,
        title=event_title,
        start_time=datetime.fromisoformat(start_time) if start_time else datetime.now(),
        end_time=datetime.fromisoformat(end_time) if end_time else None,
        location=location,
    )
    db.add(cal_event)
    db.commit()

    # --- Notify contacts via SMS ---
    contacts = db.query(NotificationContact).filter(
        NotificationContact.user_id == user.id,
        NotificationContact.notify_mode != "never",
    ).all()

    sms_sent_to = []
    raw_lower = (raw_input or "").lower()

    for contact in contacts:
        should_notify = False
        if contact.notify_mode == "always":
            should_notify = True
        elif contact.notify_mode == "mentioned":
            # Check if contact's name appears in the voice input
            name_parts = contact.name.lower().split()
            should_notify = any(part in raw_lower for part in name_parts)

        if should_notify:
            message = f"\U0001f4c5 New event: {event_title}\n\U0001f550 {start_time or 'TBD'}"
            if location:
                message += f"\n\U0001f4cd {location}"
            sent = await send_sms(contact.phone, message)
            if sent:
                sms_sent_to.append(contact.name)

    cal_event.sms_sent = len(sms_sent_to) > 0
    cal_event.attendee_email = None
    db.commit()

    # --- Build response ---
    response_parts = [intent_data.get("spoken_response", f"Created: {event_title}")]
    if google_event:
        response_parts.append("Added to your calendar.")
    if sms_sent_to:
        response_parts.append(f"Texted {', '.join(sms_sent_to)}.")

    return InputResponse(
        spoken_response=" ".join(response_parts),
        entry_id=entry.id,
        module="calendar",
    )
