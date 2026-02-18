"""
models.py — Database Table Definitions
========================================
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
    __tablename__ = "entries"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)

    input_type = Column(String, nullable=False, default="audio")
    raw_transcript = Column(Text, nullable=True)
    raw_image_description = Column(Text, nullable=True)
    processed_content = Column(Text, nullable=True)
    title = Column(String, nullable=True)

    module = Column(String, nullable=False, default="memo", index=True)
    module_data = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)

    user = relationship("User", back_populates="entries")
    calendar_event = relationship("CalendarEvent", back_populates="entry", uselist=False)
    task = relationship("Task", back_populates="entry", uselist=False)
    remember_item = relationship("RememberItem", back_populates="entry", uselist=False)
    journal_entry = relationship("JournalEntry", back_populates="entry", uselist=False)


class CalendarEvent(Base):
    __tablename__ = "calendar_events"

    id = Column(Integer, primary_key=True, index=True)
    entry_id = Column(Integer, ForeignKey("entries.id"), nullable=False)

    google_event_id = Column(String, nullable=True)
    title = Column(String, nullable=False)
    start_time = Column(DateTime, nullable=False)
    end_time = Column(DateTime, nullable=True)
    location = Column(String, nullable=True)
    attendee_email = Column(String, nullable=True)
    sms_sent = Column(Boolean, default=False)

    entry = relationship("Entry", back_populates="calendar_event")


class Task(Base):
    __tablename__ = "tasks"

    id = Column(Integer, primary_key=True, index=True)
    entry_id = Column(Integer, ForeignKey("entries.id"), nullable=False)

    description = Column(String, nullable=False)
    group = Column(String, nullable=False, default="General", index=True)
    priority = Column(String, nullable=False, default="keep_in_mind")
    status = Column(String, nullable=False, default="open", index=True)
    due_date = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    entry = relationship("Entry", back_populates="task")


class RememberItem(Base):
    """
    Things to remember — facts, preferences, references, info to keep handy.
    Categorized and searchable from the dashboard.
    """
    __tablename__ = "remember_items"

    id = Column(Integer, primary_key=True, index=True)
    entry_id = Column(Integer, ForeignKey("entries.id"), nullable=False)

    content = Column(Text, nullable=False)
    category = Column(String, nullable=False, default="General", index=True)
    tags = Column(String, nullable=True)  # Comma-separated tags
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    entry = relationship("Entry", back_populates="remember_item")


class JournalEntry(Base):
    """
    Daily journal — activities grouped by day and by topic/project.
    """
    __tablename__ = "journal_entries"

    id = Column(Integer, primary_key=True, index=True)
    entry_id = Column(Integer, ForeignKey("entries.id"), nullable=False)

    content = Column(Text, nullable=False)
    activity_type = Column(String, nullable=True)  # "work", "social", "health", "errands", etc.
    topic = Column(String, nullable=True, index=True)  # Project or recurring topic
    date = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc), index=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    entry = relationship("Entry", back_populates="journal_entry")
