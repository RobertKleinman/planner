"""
main.py — Application Entry Point
====================================
"""
import os
import logging
import secrets
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, HTTPException, Body
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from app.config import settings

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("planner")
from app.database import engine, Base, get_db, database_url
from app.schemas import HealthResponse
from app.services.google_auth import is_google_connected
from app.services.sms import is_twilio_configured
from app.auth import hash_api_key, get_current_user
from app import models


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)

    # Migration: add deleted_at column if it doesn't exist
    from sqlalchemy import text, inspect
    with engine.connect() as conn:
        inspector = inspect(engine)
        columns = [c["name"] for c in inspector.get_columns("entries")]
        if "deleted_at" not in columns:
            conn.execute(text("ALTER TABLE entries ADD COLUMN deleted_at TIMESTAMP"))
            conn.commit()
            print("Migration: added deleted_at column to entries")

        # Migration: add tags column to journal_entries
        je_columns = [c["name"] for c in inspector.get_columns("journal_entries")]
        if "tags" not in je_columns:
            conn.execute(text("ALTER TABLE journal_entries ADD COLUMN tags VARCHAR"))
            conn.commit()
            logger.info("Migration: added tags column to journal_entries")

        # Migration: add telegram_chat_id to users
        user_columns = [c["name"] for c in inspector.get_columns("users")]
        if "telegram_chat_id" not in user_columns:
            conn.execute(text("ALTER TABLE users ADD COLUMN telegram_chat_id VARCHAR"))
            conn.commit()
            logger.info("Migration: added telegram_chat_id column to users")

        # Migration: fix memo_topic_entries.entry_id FK to CASCADE on delete
        # PostgreSQL: drop old FK, add new one with ON DELETE CASCADE
        # SQLite: no ALTER CONSTRAINT support, but new tables get it from model definition
        if not database_url.startswith("sqlite"):
            try:
                # Check if constraint already has CASCADE (idempotent)
                result = conn.execute(text("""
                    SELECT confdeltype FROM pg_constraint
                    WHERE conrelid = 'memo_topic_entries'::regclass
                    AND conname LIKE '%entry_id%'
                """))
                row = result.fetchone()
                if row and row[0] != 'c':  # 'c' = CASCADE
                    conn.execute(text("""
                        ALTER TABLE memo_topic_entries
                        DROP CONSTRAINT IF EXISTS memo_topic_entries_entry_id_fkey
                    """))
                    conn.execute(text("""
                        ALTER TABLE memo_topic_entries
                        ADD CONSTRAINT memo_topic_entries_entry_id_fkey
                        FOREIGN KEY (entry_id) REFERENCES entries(id) ON DELETE CASCADE
                    """))
                    conn.commit()
                    logger.info("Migration: added CASCADE to memo_topic_entries.entry_id FK")
            except Exception as e:
                logger.warning(f"FK migration skipped (table may not exist yet): {e}")

    logger.info("Database tables created/verified")
    logger.info(f"Google Calendar: {'connected' if is_google_connected() else 'not connected'}")
    logger.info(f"Twilio SMS: {'configured' if is_twilio_configured() else 'not configured'}")

    # Set up Telegram webhook if configured
    if settings.telegram_bot_token and settings.app_base_url:
        try:
            from aiogram import Bot
            bot = Bot(token=settings.telegram_bot_token)
            webhook_url = f"{settings.app_base_url.rstrip('/')}/telegram/webhook"
            # Only use secret if it contains valid characters (alphanumeric, -, _)
            secret = settings.telegram_webhook_secret or ""
            import re
            if secret and not re.match(r'^[A-Za-z0-9_-]+$', secret):
                logger.warning(f"Telegram webhook secret contains invalid characters, skipping secret")
                secret = ""
            await bot.set_webhook(
                url=webhook_url,
                secret_token=secret or None,
            )
            logger.info(f"Telegram webhook set: {webhook_url} (secret: {'yes' if secret else 'no'})")
            await bot.session.close()
        except Exception as e:
            logger.error(f"Failed to set Telegram webhook: {e}", exc_info=True)

    yield

    # Note: we intentionally do NOT delete the webhook on shutdown.
    # The webhook stays registered so Telegram queues messages during
    # redeploys. The startup code re-registers it anyway.

    logger.info("Server shutting down")


app = FastAPI(
    title="Planner API",
    description="Voice-first personal planner. Speak, and things happen.",
    version="0.3.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

from app.routers import input, entries, dashboard, search
app.include_router(input.router)
app.include_router(entries.router)
app.include_router(dashboard.router)
app.include_router(search.router)

if settings.telegram_bot_token:
    from app.routers import telegram
    app.include_router(telegram.router)
    logger.info("Telegram bot router enabled")


@app.get("/health", response_model=HealthResponse, tags=["system"])
def health_check():
    return HealthResponse(
        status="healthy",
        environment=settings.environment,
        version="0.3.0",
        google_connected=is_google_connected(),
        twilio_configured=is_twilio_configured(),
    )


@app.post("/admin/notification-contacts", tags=["system"])
def add_notification_contact(
    name: str = Body(...),
    phone: str = Body(...),
    notify_mode: str = Body("always"),
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """Add a notification contact for calendar event SMS alerts."""
    existing = db.query(models.NotificationContact).filter(
        models.NotificationContact.user_id == user.id,
        models.NotificationContact.phone == phone,
    ).first()
    if existing:
        return {"message": f"Contact {existing.name} already exists with this phone"}

    contact = models.NotificationContact(
        user_id=user.id,
        name=name,
        phone=phone,
        notify_mode=notify_mode,
    )
    db.add(contact)
    db.commit()
    return {"message": f"Contact '{name}' added", "phone": phone, "notify_mode": notify_mode}


@app.get("/admin/notification-contacts", tags=["system"])
def list_notification_contacts(
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """List all notification contacts."""
    contacts = db.query(models.NotificationContact).filter(
        models.NotificationContact.user_id == user.id,
    ).all()
    return [
        {"id": c.id, "name": c.name, "phone": c.phone, "notify_mode": c.notify_mode}
        for c in contacts
    ]


@app.post("/setup-user", tags=["system"])
def setup_user(db: Session = Depends(get_db)):
    """One-time setup for the primary user using PLANNER_API_KEY from environment."""
    existing = db.query(models.User).first()
    if existing:
        return {"message": "User already exists", "email": existing.email}

    api_key = os.environ.get("PLANNER_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="PLANNER_API_KEY not set")

    user = models.User(
        email="robertmkleinman@gmail.com",
        name="Rob",
        api_key_hash=hash_api_key(api_key),
        is_active=True,
    )
    db.add(user)
    db.commit()
    return {"message": "User created", "email": "robertmkleinman@gmail.com"}


@app.post("/admin/create-user", tags=["system"])
def create_user(
    name: str = Body(...),
    email: str = Body(...),
    phone: str = Body(None, description="Phone number for SMS notifications"),
    db: Session = Depends(get_db),
    admin: models.User = Depends(get_current_user),
):
    """
    Create a new user. Requires API key of an existing user.
    Returns the new user's API key — shown ONCE, save it!
    """
    existing = db.query(models.User).filter(models.User.email == email).first()
    if existing:
        raise HTTPException(status_code=400, detail="User with this email already exists")

    new_api_key = secrets.token_urlsafe(32)

    user = models.User(
        email=email,
        name=name,
        api_key_hash=hash_api_key(new_api_key),
        is_active=True,
    )
    db.add(user)
    db.commit()

    return {
        "message": f"User '{name}' created successfully",
        "name": name,
        "email": email,
        "api_key": new_api_key,
        "important": "Save this API key now — it cannot be retrieved later!",
    }
