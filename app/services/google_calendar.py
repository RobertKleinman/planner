"""
google_calendar.py — Create Events in Google Calendar
=======================================================
This is what eliminates most of the Shortcuts complexity. Instead of
returning calendar data for Shortcuts to create the event, the SERVER
creates it directly in your Google Calendar via the API.

The event immediately syncs to every device where you're logged into
Google Calendar — your iPhone, Mac, browser, Johnny's phone if he's
an attendee. No Shortcuts action blocks needed.
"""

from datetime import datetime
from typing import Optional
from app.services.google_auth import get_calendar_service
from app.config import settings


async def create_calendar_event(
    title: str,
    start_time: str,
    end_time: str = None,
    location: str = None,
    description: str = None,
    attendee_email: str = None,
    timezone: str = None,
) -> Optional[dict]:
    """
    Create an event in the user's primary Google Calendar.

    Args:
        title: Event title ("Dentist", "Meeting with Sarah")
        start_time: ISO datetime string ("2026-02-18T14:00:00")
        end_time: ISO datetime string (defaults to 1 hour after start)
        location: Optional location string
        description: Optional description/notes
        attendee_email: If provided, adds this person as an attendee
                        (they get a Google Calendar invite notification)
        timezone: Timezone string (defaults to config timezone)

    Returns:
        The created event dict from Google (includes the event ID), or None if failed.
    """
    service = get_calendar_service()
    if not service:
        print("⚠ Google Calendar not connected. Run setup_google.py first.")
        return None

    tz = timezone or settings.timezone

    event_body = {
        "summary": title,
        "start": {
            "dateTime": start_time,
            "timeZone": tz,
        },
        "end": {
            "dateTime": end_time or start_time,  # Fallback; caller should provide
            "timeZone": tz,
        },
    }

    if location:
        event_body["location"] = location
    if description:
        event_body["description"] = description
    if attendee_email:
        event_body["attendees"] = [{"email": attendee_email}]

    try:
        event = service.events().insert(
            calendarId="primary",
            body=event_body,
            # sendUpdates="all" sends email notifications to attendees
            sendUpdates="all" if attendee_email else "none",
        ).execute()

        print(f"✓ Calendar event created: {event.get('htmlLink')}")
        return event

    except Exception as e:
        print(f"✗ Failed to create calendar event: {e}")
        return None
