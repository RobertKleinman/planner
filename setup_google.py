"""
setup_google.py — One-Time Google OAuth Authorization
======================================================
Run this ONCE to authorize your server to access your Google Calendar,
Tasks, and Gmail.

BEFORE RUNNING THIS:
1. Go to https://console.cloud.google.com
2. Create a new project (or use existing)
3. Enable these APIs:
   - Google Calendar API
   - Google Tasks API
   - Gmail API
4. Go to "Credentials" → "Create Credentials" → "OAuth 2.0 Client ID"
   - Application type: "Desktop app" (even though it's a server — this is
     for the initial auth flow that opens a browser)
   - Download the JSON file
   - Save it as credentials.json in this project directory

5. Run: python setup_google.py
   - A browser window opens asking you to log in and grant permissions
   - After you approve, a token.json file is created
   - Your server uses this token for all future Google API calls

You only do this once. The token auto-refreshes indefinitely.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from google_auth_oauthlib.flow import InstalledAppFlow
from app.config import settings

SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/tasks",
    "https://www.googleapis.com/auth/gmail.send",
]


def setup():
    if not os.path.exists(settings.google_credentials_file):
        print(f"✗ {settings.google_credentials_file} not found.")
        print("  Download it from Google Cloud Console → Credentials → OAuth 2.0 Client ID")
        return

    if os.path.exists(settings.google_token_file):
        print(f"Token file already exists: {settings.google_token_file}")
        answer = input("Re-authorize? (y/N): ").strip().lower()
        if answer != "y":
            return

    flow = InstalledAppFlow.from_client_secrets_file(
        settings.google_credentials_file, SCOPES
    )
    creds = flow.run_local_server(port=0)

    with open(settings.google_token_file, "w") as token:
        token.write(creds.to_json())

    print(f"\n✓ Google OAuth complete! Token saved to {settings.google_token_file}")
    print("  Your server can now access Google Calendar, Tasks, and Gmail.")


if __name__ == "__main__":
    setup()
