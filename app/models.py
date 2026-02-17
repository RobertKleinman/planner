"""
models.py — Database Table Definitions
========================================
WHAT CHANGED FROM V1:
The big insight from our module planning is that most modules (memo, diary,
mood, gym, expenses, etc.) are really *specialized entry types* with different
metadata. So instead of a "Memo" table, we have a universal "Entry" table.

Every input — voice memo, calendar event, mood check-in, gym log — becomes
an Entry. What makes them different is:
  - The `module` field ("memo", "calendar", "mood", "gym", etc.)
  - The `module_data` JSON field (holds module-specific structured data)
  - How the dashboard displays them

This means adding a new module NEVER requires a database migration. You just
start storing entries with a new module name and new JSON structure in
module_data. The database doesn't care — it's just another row.

For modules that need extra relational data (like calendar events needing
a Google Calendar event ID for updates/deletes), we add small linked tables.
"""

from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, Boolean, Float
from sqlalchemy.orm import relationship
from datetime import datetime, timezone

from app.database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    name = Column(String, nullable=False)
    api_key_hash = Column(String, nullable=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    entries = relationship("Entry", back_populates="user")


class Entry(Base):
    """
    The universal record. Every input becomes an Entry.

    Think of this as a filing system where every document goes into one cabinet,
    but each document has a colored tab (module) and a detailed label (module_data).
    """
    __tablename__ = "entries"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)

    # --- What type of input was this? ---
    input_type = Column(String, nullable=False, default="audio")  # "audio", "image", "video", "text"

    # --- Raw input ---
    raw_transcript = Column(Text, nullable=True)        # For audio/video: Whisper transcription
    raw_image_description = Column(Text, nullable=True)  # For images: what Claude saw

    # --- Processed output ---
    processed_content = Column(Text, nullable=True)  # LLM's summary/extraction
    title = Column(String, nullable=True)             # Short title from LLM

    # --- Module routing ---
    module = Column(String, nullable=False, default="memo", index=True)
    # One of: "memo", "calendar", "diary", "task", "screenshot_note",
    #         "expense", "mood", "idea", "gym", "work", "food"

    # --- Module-specific structured data (stored as JSON string) ---
    # For calendar: {"title": "Dentist", "start": "...", "end": "...", "location": "..."}
    # For mood: {"rating": 7, "triggers": ["work stress"], "notes": "..."}
    # For gym: {"exercise": "bench press", "sets": 3, "reps": 10, "weight_lbs": 135}
    # For expense: {"amount": 42.50, "currency": "CAD", "category": "groceries", "vendor": "Loblaws"}
    module_data = Column(Text, nullable=True)

    # --- Metadata ---
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)

    # --- Relationships ---
    user = relationship("User", back_populates="entries")
    calendar_event = relationship("CalendarEvent", back_populates="entry", uselist=False)


class CalendarEvent(Base):
    """
    Extra data for calendar entries. Links back to the Entry table.

    Why a separate table? Because calendar events have a lifecycle beyond
    the initial creation — they can be updated, cancelled, moved. We need
    to store the Google Calendar event ID so our server can modify the
    event later. This is "relational" data that doesn't fit in a JSON blob.
    """
    __tablename__ = "calendar_events"

    id = Column(Integer, primary_key=True, index=True)
    entry_id = Column(Integer, ForeignKey("entries.id"), nullable=False)

    google_event_id = Column(String, nullable=True)   # From Google Calendar API
    title = Column(String, nullable=False)
    start_time = Column(DateTime, nullable=False)
    end_time = Column(DateTime, nullable=True)
    location = Column(String, nullable=True)
    attendee_email = Column(String, nullable=True)     # Johnny's email for shared events
    sms_sent = Column(Boolean, default=False)          # Did we text Johnny?

    entry = relationship("Entry", back_populates="calendar_event")
