"""
email_service.py — Send Emails via Gmail API
==============================================
Used for the daily digest: every evening, the server summarizes your
day's entries and emails you a nice digest.

Uses the same Google OAuth credentials as the Calendar API — no
additional authentication needed.
"""

import base64
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from app.services.google_auth import get_gmail_service
from app.config import settings


async def send_email(to_email: str, subject: str, body_html: str) -> bool:
    """
    Send an email via Gmail API.

    Args:
        to_email: Recipient email address
        subject: Email subject line
        body_html: HTML body content

    Returns:
        True if sent, False if failed.
    """
    service = get_gmail_service()
    if not service:
        print("⚠ Gmail not connected. Run setup_google.py first.")
        return False

    try:
        message = MIMEMultipart("alternative")
        message["to"] = to_email
        message["subject"] = subject

        # Add HTML body
        html_part = MIMEText(body_html, "html")
        message.attach(html_part)

        # Gmail API expects base64url-encoded message
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode()

        service.users().messages().send(
            userId="me",
            body={"raw": raw}
        ).execute()

        print(f"✓ Email sent to {to_email}: {subject}")
        return True

    except Exception as e:
        print(f"✗ Email failed: {e}")
        return False


async def send_daily_digest(subject: str, body_html: str) -> bool:
    """Convenience: send the daily digest to the configured recipient."""
    if not settings.digest_recipient_email:
        print("⚠ Digest recipient email not configured.")
        return False
    return await send_email(settings.digest_recipient_email, subject, body_html)
