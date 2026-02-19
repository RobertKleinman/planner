"""
main.py — Application Entry Point
====================================
"""
import os
import secrets
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, HTTPException, Body
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from app.config import settings
from app.database import engine, Base, get_db
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

    print("Database tables created/verified")
    print(f"Google Calendar: {'connected' if is_google_connected() else 'not connected'}")
    print(f"Twilio SMS: {'configured' if is_twilio_configured() else 'not configured'}")
    yield
    print("Server shutting down")


app = FastAPI(
    title="Planner API",
    description="Voice-first personal planner. Speak, and things happen.",
    version="0.3.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

from app.routers import input, entries, dashboard
app.include_router(input.router)
app.include_router(entries.router)
app.include_router(dashboard.router)


@app.get("/health", response_model=HealthResponse, tags=["system"])
def health_check():
    return HealthResponse(
        status="healthy",
        environment=settings.environment,
        version="0.3.0",
        google_connected=is_google_connected(),
        twilio_configured=is_twilio_configured(),
    )


@app.post("/setup-user", tags=["system"])
async def setup_user(db: Session = Depends(get_db)):
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
async def create_user(
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
