"""
main.py — Application Entry Point
====================================
Creates the FastAPI app, registers routers, sets up the database.
"""
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from app.config import settings
from app.database import engine, Base, get_db
from app.schemas import HealthResponse
from app.services.google_auth import is_google_connected
from app.services.sms import is_twilio_configured
from app.auth import hash_api_key
from app import models


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    print("Database tables created/verified")
    print(f"Google Calendar: {'connected' if is_google_connected() else 'not connected'}")
    print(f"Twilio SMS: {'configured' if is_twilio_configured() else 'not configured'}")
    yield
    print("Server shutting down")


app = FastAPI(
    title="Planner API",
    description="Voice-first personal planner. Speak, and things happen.",
    version="0.2.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

from app.routers import input, entries
app.include_router(input.router)
app.include_router(entries.router)


@app.get("/health", response_model=HealthResponse, tags=["system"])
def health_check():
    return HealthResponse(
        status="healthy",
        environment=settings.environment,
        version="0.2.0",
        google_connected=is_google_connected(),
        twilio_configured=is_twilio_configured(),
    )


@app.post("/setup-user", tags=["system"])
async def setup_user(db: Session = Depends(get_db)):
    """One-time user setup using PLANNER_API_KEY from environment."""
    existing = db.query(models.User).first()
    if existing:
        return {"message": "User already exists", "username": existing.username}

    api_key = os.environ.get("PLANNER_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="PLANNER_API_KEY not set")

    user = models.User(
        username="rob",
        email="robertmkleinman@gmail.com",
        full_name="Rob Kleinman",
        api_key_hash=hash_api_key(api_key),
        is_active=True,
    )
    db.add(user)
    db.commit()
    return {"message": "User created", "username": "rob"}