"""
schemas.py — API Request/Response Shapes
==========================================
WHAT CHANGED FROM V1:
- VoiceResponse is now InputResponse (handles any input type)
- Actions are simplified — most work happens server-side now via Google APIs
- The response to Shortcuts is minimal: just a confirmation message
- Added schemas for entries (the universal record) for dashboard use
"""

from pydantic import BaseModel
from datetime import datetime
from typing import Optional, Any


# ============================================================
# Input Endpoint Response — What Shortcuts receives
# ============================================================

class InputResponse(BaseModel):
    """
    What the /input endpoint returns to your Shortcut.

    MUCH simpler than v1 because the server now handles everything
    (creates Google Calendar events, sends SMS, etc.). Shortcuts just
    needs to show a confirmation.
    """
    spoken_response: str              # "Got it — dentist tomorrow at 2pm, texted Johnny."
    entry_id: Optional[int] = None    # DB record ID
    module: str = "memo"              # Which module handled it


# ============================================================
# Entry Schemas — For the dashboard
# ============================================================

class EntryResponse(BaseModel):
    """What an entry looks like when retrieved from the API."""
    id: int
    input_type: str
    raw_transcript: Optional[str]
    raw_image_description: Optional[str]
    processed_content: Optional[str]
    title: Optional[str]
    module: str
    module_data: Optional[str]   # JSON string — frontend can parse it
    created_at: datetime

    class Config:
        from_attributes = True


class EntryListResponse(BaseModel):
    """Paginated list of entries, optionally filtered by module."""
    entries: list[EntryResponse]
    total: int
    page: int
    per_page: int


class CalendarEventResponse(BaseModel):
    """Calendar-specific data, returned alongside its parent entry."""
    id: int
    google_event_id: Optional[str]
    title: str
    start_time: datetime
    end_time: Optional[datetime]
    location: Optional[str]
    attendee_email: Optional[str]
    sms_sent: bool

    class Config:
        from_attributes = True


# ============================================================
# Health Check
# ============================================================

class HealthResponse(BaseModel):
    status: str
    environment: str
    version: str
    google_connected: bool
    twilio_configured: bool
