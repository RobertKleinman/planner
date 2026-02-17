"""
google_auth.py — Google OAuth Token Management
================================================
Handles authenticating with Google APIs (Calendar, Tasks, Gmail).

Supports two modes:
1. LOCAL: Reads token.json and credentials.json from disk (normal setup)
2. CLOUD (Railway): Reads token/credentials JSON from environment variables
   and writes them to temporary files.
"""

import os
import json
import tempfile
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

from app.config import settings

SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/tasks",
    "https://www.googleapis.com/auth/gmail.send",
]


def _ensure_token_file() -> str:
    """
    Returns the path to token.json.
    If running on Railway (env var GOOGLE_TOKEN_JSON is set),
    writes the JSON content to a temp file and returns that path.
    Otherwise returns the local file path from settings.
    """
    # Check if token JSON is provided as an environment variable
    token_json = os.environ.get("GOOGLE_TOKEN_JSON")
    if token_json:
        path = os.path.join(tempfile.gettempdir(), "token.json")
        with open(path, "w") as f:
            f.write(token_json)
        return path

    # Fall back to local file
    return settings.google_token_file


def _ensure_credentials_file() -> str:
    """
    Returns the path to credentials.json.
    If running on Railway (env var GOOGLE_CREDENTIALS_JSON is set),
    writes the JSON content to a temp file and returns that path.
    Otherwise returns the local file path from settings.
    """
    creds_json = os.environ.get("GOOGLE_CREDENTIALS_JSON")
    if creds_json:
        path = os.path.join(tempfile.gettempdir(), "credentials.json")
        with open(path, "w") as f:
            f.write(creds_json)
        return path

    return settings.google_credentials_file


def get_google_credentials() -> Credentials:
    """
    Load Google OAuth credentials from the stored token file.
    Automatically refreshes if expired.

    Returns None if not yet authenticated (run setup_google.py first).
    """
    creds = None
    token_path = _ensure_token_file()
    
    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(
            token_path, SCOPES
        )

    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        # Save the refreshed token
        with open(token_path, "w") as token:
            token.write(creds.to_json())
        # Also update the environment variable if running on Railway
        if os.environ.get("GOOGLE_TOKEN_JSON"):
            os.environ["GOOGLE_TOKEN_JSON"] = creds.to_json()

    return creds


def get_calendar_service():
    """Get an authenticated Google Calendar API service."""
    creds = get_google_credentials()
    if not creds:
        return None
    return build("calendar", "v3", credentials=creds)


def is_google_connected() -> bool:
    """Check if Google OAuth credentials are available and valid."""
    try:
        creds = get_google_credentials()
        return creds is not None and creds.valid
    except Exception:
        return False
```


def get_gmail_service():
    """Get an authenticated Gmail API service."""
    creds = get_google_credentials()
    if not creds:
        return None
    return build("gmail", "v1", credentials=creds)
