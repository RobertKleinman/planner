"""
google_auth.py — Google OAuth Token Management
================================================
Handles authenticating with Google APIs (Calendar, Tasks, Gmail).

HOW GOOGLE AUTH WORKS (simplified):
1. You create a "project" in Google Cloud Console and enable Calendar/Gmail APIs.
2. You download a credentials.json file (your app's identity).
3. First time: you open a URL in your browser, log into Google, and grant permissions.
4. Google gives you a "token" (stored in token.json) that lets your server
   act on your behalf — create calendar events, send emails, etc.
5. The token expires periodically, but includes a "refresh token" that
   automatically gets a new one. So you only do step 3 once.

The setup_google.py script handles the one-time authorization flow.
This file handles loading and refreshing the token for ongoing use.
"""

import os
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

from app.config import settings

# These are the permissions we're asking for.
# Each scope grants access to a specific API.
SCOPES = [
    "https://www.googleapis.com/auth/calendar",          # Create/modify calendar events
    "https://www.googleapis.com/auth/tasks",              # Create/modify tasks
    "https://www.googleapis.com/auth/gmail.send",         # Send emails (daily digest)
]


def get_google_credentials() -> Credentials:
    """
    Load Google OAuth credentials from the stored token file.
    Automatically refreshes if expired.

    Returns None if not yet authenticated (run setup_google.py first).
    """
    creds = None

    if os.path.exists(settings.google_token_file):
        creds = Credentials.from_authorized_user_file(
            settings.google_token_file, SCOPES
        )

    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        # Save the refreshed token
        with open(settings.google_token_file, "w") as token:
            token.write(creds.to_json())

    return creds


def get_calendar_service():
    """Get an authenticated Google Calendar API service object."""
    creds = get_google_credentials()
    if not creds or not creds.valid:
        return None
    return build("calendar", "v3", credentials=creds)


def get_gmail_service():
    """Get an authenticated Gmail API service object."""
    creds = get_google_credentials()
    if not creds or not creds.valid:
        return None
    return build("gmail", "v1", credentials=creds)


def is_google_connected() -> bool:
    """Check if Google OAuth is set up and valid."""
    creds = get_google_credentials()
    return creds is not None and creds.valid
