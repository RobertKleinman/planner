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
    telegram_chat_id = Column(String, nullable=True, unique=True, index=True)
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
    memo_topic_links = relationship("MemoTopicEntry", back_populates="entry", cascade="all, delete-orphan")


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
    topic = Column(String, nullable=True, index=True)  # legacy — use tags for new entries
    tags = Column(String, nullable=True)  # comma-separated: "gym,social,sarah"
    date = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc), index=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    entry = relationship("Entry", back_populates="journal_entry")


class MemoTopic(Base):
    __tablename__ = "memo_topics"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)

    name = Column(String, nullable=False)           # "House Renovation"
    description = Column(Text, nullable=True)       # optional context for LLM matching
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    user = relationship("User")
    linked_entries = relationship("MemoTopicEntry", back_populates="memo_topic", cascade="all, delete-orphan")


class MemoTopicEntry(Base):
    __tablename__ = "memo_topic_entries"

    id = Column(Integer, primary_key=True, index=True)
    memo_topic_id = Column(Integer, ForeignKey("memo_topics.id", ondelete="CASCADE"), nullable=False)
    entry_id = Column(Integer, ForeignKey("entries.id", ondelete="CASCADE"), nullable=False)

    excerpt = Column(Text, nullable=True)           # LLM-generated excerpt of what's relevant
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    memo_topic = relationship("MemoTopic", back_populates="linked_entries")
    entry = relationship("Entry", back_populates="memo_topic_links")


class ConversationMessage(Base):
    __tablename__ = "conversation_messages"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    session_id = Column(String, nullable=False, index=True)  # "telegram:<chat_id>" or "api:<uuid>"
    role = Column(String, nullable=False)  # "user" or "assistant"
    content = Column(Text, nullable=False)  # JSON-serialized Anthropic content blocks
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)

    user = relationship("User")


class Reminder(Base):
    __tablename__ = "reminders"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)

    message = Column(Text, nullable=False)
    remind_at = Column(DateTime, nullable=False, index=True)  # UTC
    recurring = Column(String, nullable=True)  # null=one-time, "daily", "weekly", "weekdays"
    sent = Column(Boolean, default=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    user = relationship("User")


class NotificationContact(Base):
    __tablename__ = "notification_contacts"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)

    name = Column(String, nullable=False)
    phone = Column(String, nullable=False)
    notify_mode = Column(String, nullable=False, default="always")  # always, mentioned, never
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    user = relationship("User")
