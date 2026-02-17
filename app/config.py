"""
config.py — Centralized Settings
=================================
Every setting the app needs, in one place. Reads from .env file.
If a required value is missing, the app crashes at startup with a clear error.

WHAT CHANGED FROM V1:
- Added Google, Twilio, email, and timezone settings
- Changed default transcription model to gpt-4o-mini-transcribe
"""

from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    # --- AI Services ---
    openai_api_key: str = Field(description="OpenAI API key for transcription")
    anthropic_api_key: str = Field(description="Anthropic API key for Claude")

    # --- Authentication ---
    planner_api_key: str = Field(description="Secret key your phone sends")

    # --- Database ---
    database_url: str = Field(default="sqlite:///./planner.db")

    # --- Google APIs ---
    google_credentials_file: str = Field(default="credentials.json")
    google_token_file: str = Field(default="token.json")

    # --- Twilio (SMS) ---
    twilio_account_sid: str = Field(default="")
    twilio_auth_token: str = Field(default="")
    twilio_phone_number: str = Field(default="")
    johnny_phone_number: str = Field(default="")

    # --- Email ---
    digest_recipient_email: str = Field(default="")

    # --- Server ---
    environment: str = Field(default="development")
    timezone: str = Field(default="America/Toronto")

    # --- AI Model Preferences ---
    # gpt-4o-mini-transcribe: OpenAI's current recommended model.
    # Half the cost of whisper-1 ($0.003/min vs $0.006/min) with better accuracy.
    transcription_model: str = Field(default="gpt-4o-mini-transcribe")
    intent_model: str = Field(default="claude-sonnet-4-20250514")

    class Config:
        env_file = ".env"
        case_sensitive = False


settings = Settings()
