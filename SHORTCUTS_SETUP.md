# Apple Shortcuts Setup — 3 Blocks Total

## The Shortcut

Since the server handles everything (calendar events, SMS, storage),
your Shortcut is dead simple:

### Block 1: Record Audio
- Action: **Record Audio**
- Quality: Normal
- Start: Immediately

### Block 2: Send to Server
- Action: **Get Contents of URL**
- URL: `https://your-server.com/api/v1/input`
- Method: **POST**
- Headers:
  - Key: `X-API-Key`  Value: your API key from .env
- Request Body: **Form**
  - Add field → **File**
    - Key: `file`
    - Value: (Recording from Block 1)

### Block 3: Show Confirmation
- Action: **Get Dictionary Value**
  - Key: `spoken_response` from Contents of URL
- Action: **Show Notification**
  - Title: "Planner"
  - Body: (Dictionary Value from above)

**That's it. 3 blocks. You never touch this Shortcut again.**

All new functionality is added on the server. New modules, new intents,
new integrations — none of it changes the Shortcut.

## How to Trigger It

- **Back Tap**: Settings → Accessibility → Touch → Back Tap → Double Tap → your shortcut
  (double-tap the back of your phone to start recording)
- **Action Button** (iPhone 15 Pro+): Settings → Action Button → your shortcut
- **Home Screen Widget**: Long-press → Add Widget → Shortcuts → select this one
- **Lock Screen Widget**: Settings → Lock Screen → Add Widget → Shortcuts

## Why It's So Simple Now

In v1, the Shortcut had to parse the response, loop through actions,
create calendar events, set reminders, etc. Now the server does all of that
via Google Calendar API and Twilio SMS. The phone is just a microphone.

## Optional: Screenshot Shortcut

If you also want to send screenshots/images to the planner:

1. **Receive Input**: Set to accept images from Share Sheet
2. **Get Contents of URL**: Same as above but send the image as the `file` field
3. **Show Notification**: Same as above

This lets you share any screenshot to the planner from the iOS Share Sheet.
