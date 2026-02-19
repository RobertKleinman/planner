"""
models.py — Database Table Definitions
========================================
Soft delete: Entry.deleted_at is set instead of actually removing rows.
Trash auto-purges after 10 days.
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
    deleted_at = Column(DateTime, nullable=True, index=True)

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
    __tablename__ = "remember_items"

    id = Column(Integer, primary_key=True, index=True)
    entry_id = Column(Integer, ForeignKey("entries.id"), nullable=False)

    content = Column(Text, nullable=False)
    category = Column(String, nullable=False, default="General", index=True)
    tags = Column(String, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    entry = relationship("Entry", back_populates="remember_item")


class JournalEntry(Base):
    __tablename__ = "journal_entries"

    id = Column(Integer, primary_key=True, index=True)
    entry_id = Column(Integer, ForeignKey("entries.id"), nullable=False)

    content = Column(Text, nullable=False)
    activity_type = Column(String, nullable=True)
    topic = Column(String, nullable=True, index=True)
    date = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc), index=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    entry = relationship("Entry", back_populates="journal_entry")
