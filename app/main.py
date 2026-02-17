"""
main.py — Application Entry Point
====================================
Creates the FastAPI app, registers routers, sets up the database.
"""

from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.database import engine, Base
from app.schemas import HealthResponse
from app.services.google_auth import is_google_connected
from app.services.sms import is_twilio_configured

from app import models  # noqa: F401 — registers models with SQLAlchemy

from app.routers import input, entries


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    print("✓ Database tables created/verified")
    print(f"✓ Google Calendar: {'connected' if is_google_connected() else 'not connected'}")
    print(f"✓ Twilio SMS: {'configured' if is_twilio_configured() else 'not configured'}")
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
    allow_origins=["*"],  # TODO: Restrict in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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
