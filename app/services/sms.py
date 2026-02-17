"""
sms.py — Send SMS via Twilio
==============================
Sends a text message to Johnny (or anyone) when a calendar event is created
that involves them.

WHAT IS TWILIO?
A service that lets your server send text messages programmatically.
You get a phone number from them, and your code sends SMS through their API.
Costs about $0.0079 per message — essentially free for personal use.

Setup:
1. Create a free Twilio account at https://console.twilio.com
2. Get your Account SID and Auth Token from the dashboard
3. Buy a phone number ($1/month) or use the free trial number
4. Put the values in your .env file
"""

from twilio.rest import Client
from app.config import settings


def is_twilio_configured() -> bool:
    """Check if Twilio credentials are set."""
    return bool(
        settings.twilio_account_sid
        and settings.twilio_auth_token
        and settings.twilio_phone_number
    )


async def send_sms(to_number: str, message: str) -> bool:
    """
    Send an SMS message.

    Args:
        to_number: Phone number to text (e.g., "+14165551234")
        message: The text message body

    Returns:
        True if sent successfully, False otherwise.
    """
    if not is_twilio_configured():
        print("⚠ Twilio not configured. SMS not sent.")
        return False

    try:
        client = Client(settings.twilio_account_sid, settings.twilio_auth_token)
        msg = client.messages.create(
            body=message,
            from_=settings.twilio_phone_number,
            to=to_number,
        )
        print(f"✓ SMS sent to {to_number}: {msg.sid}")
        return True
    except Exception as e:
        print(f"✗ SMS failed: {e}")
        return False


async def notify_johnny(event_title: str, event_time: str, location: str = None):
    """
    Convenience function: text Johnny about a calendar event.
    """
    if not settings.johnny_phone_number:
        print("⚠ Johnny's phone number not configured.")
        return False

    message = f"📅 New event: {event_title}\n🕐 {event_time}"
    if location:
        message += f"\n📍 {location}"

    return await send_sms(settings.johnny_phone_number, message)
