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


class ProfileMemory(Base):
    """Stable facts about the user: identity, preferences, relationships, projects."""
    __tablename__ = "profile_memories"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)

    content = Column(Text, nullable=False)                          # 20-80 tokens
    category = Column(String, nullable=False, default="General", index=True)  # identity, preference, relationship, project, etc.
    confidence = Column(Float, nullable=False, default=1.0)         # 1.0 = user-stated, lower = inferred
    source = Column(String, nullable=False, default="explicit")     # explicit (user said it) or inferred
    tags = Column(String, nullable=True)                            # comma-separated
    source_session_id = Column(String, nullable=True)               # session that created this
    status = Column(String, nullable=False, default="active", index=True)  # active, outdated, deleted
    last_confirmed = Column(DateTime, nullable=True)                # last time evidence supported this
    superseded_by_id = Column(Integer, nullable=True)               # FK to newer ProfileMemory
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc))

    user = relationship("User")


class EpisodicMemory(Base):
    """Notable events or recurring situations, clustered from conversation history."""
    __tablename__ = "episodic_memories"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)

    summary = Column(Text, nullable=False)                          # 50-120 tokens
    time_start = Column(DateTime, nullable=True)                    # when the episode began
    time_end = Column(DateTime, nullable=True)                      # when it ended (ongoing if null)
    recurrence_count = Column(Integer, nullable=False, default=1)   # how many times this has come up
    importance = Column(Float, nullable=False, default=0.5)         # 0-1, decays over time
    tags = Column(String, nullable=True)                            # comma-separated
    source_session_ids = Column(Text, nullable=True)                # JSON array of session_ids
    status = Column(String, nullable=False, default="active", index=True)  # active, decayed, archived
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc))

    user = relationship("User")


class HypothesisMemory(Base):
    """Inferred patterns about user behavior with uncertainty tracking."""
    __tablename__ = "hypothesis_memories"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)

    short_summary = Column(Text, nullable=False)                    # 30-50 tokens, for ranking/injection
    long_summary = Column(Text, nullable=False)                     # 80-140 tokens, for full context
    confidence = Column(Float, nullable=False, default=0.3)         # starts low
    evidence_for = Column(Integer, nullable=False, default=1)       # supporting instances
    evidence_against = Column(Integer, nullable=False, default=0)   # contradicting instances
    category = Column(String, nullable=True)                        # behavioral, emotional, preference, relational
    tags = Column(String, nullable=True)                            # comma-separated
    source_session_ids = Column(Text, nullable=True)                # JSON array of session_ids
    status = Column(String, nullable=False, default="provisional", index=True)  # provisional, active, challenged, superseded
    superseded_by_id = Column(Integer, nullable=True)               # FK to newer HypothesisMemory
    last_confirmed = Column(DateTime, nullable=True)                # last time evidence supported this
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc))

    user = relationship("User")


class SessionSummary(Base):
    """Compressed summary of a conversation window for medium-term context."""
    __tablename__ = "session_summaries"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    session_id = Column(String, nullable=False, index=True)

    summary = Column(Text, nullable=False)                          # 200-300 tokens
    message_id_start = Column(Integer, nullable=False)              # first message ID covered
    message_id_end = Column(Integer, nullable=False)                # last message ID covered
    message_count = Column(Integer, nullable=False)                 # how many messages summarized
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    user = relationship("User")


class ConsolidationCheckpoint(Base):
    """Tracks consolidation progress per user/session to enable delta processing."""
    __tablename__ = "consolidation_checkpoints"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    session_id = Column(String, nullable=False, index=True)

    last_consolidated_message_id = Column(Integer, nullable=False, default=0)
    last_consolidated_at = Column(DateTime, nullable=True)
    last_decay_at = Column(DateTime, nullable=True)
    is_consolidating = Column(Boolean, nullable=False, default=False)  # simple lock flag
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc))

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
