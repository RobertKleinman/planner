# Planner v2 — Voice-First Personal OS

## What This Is

A personal planner where you speak into your phone and things happen:
calendar events get created in Google Calendar, Johnny gets a text,
memos are saved, and you get a daily digest email. Your phone is just
a microphone — the server is the brain.

## Architecture

```
┌──────────────┐      ┌───────────────────────────────────────────────────┐
│  iPhone       │      │  Your Server                                     │
│  (Shortcuts)  │─────▶│                                                   │
│               │      │  POST /api/v1/input  (audio, image, video, text) │
│  Records      │      │    │                                              │
│  audio,       │      │    ├── Transcribe (gpt-4o-mini-transcribe)       │
│  sends it,    │      │    ├── Classify intent (Claude + Vision)         │
│  shows a      │      │    ├── Route to module                           │
│  notification │      │    │    ├── memo → save to DB                    │
│               │◀─────│    │    ├── calendar → Google Calendar + SMS     │
│  (3 blocks    │      │    │    ├── mood → save to DB                    │
│   in total)   │      │    │    ├── expense → save to DB                 │
│               │      │    │    └── ... (11 modules planned)             │
└──────────────┘      │    └── Return confirmation string                 │
                       │                                                   │
┌──────────────┐      │  Nightly cron job:                                │
│  Dashboard    │      │    └── Summarize day → email digest (Gmail API)  │
│  (React PWA)  │─────▶│                                                   │
│  Phase 3      │      │  SQLite database (stores everything)             │
└──────────────┘      └───────────────────────────────────────────────────┘
                                    │           │           │
                              Google Calendar  Twilio    Gmail
                              (events sync     (SMS to   (daily
                               to all devices)  Johnny)   digest)
```

## Project Structure

```
planner/
├── app/
│   ├── main.py              ← Entry point. Starts server.
│   ├── config.py             ← All settings from .env
│   ├── database.py           ← SQLite connection
│   ├── models.py             ← DB tables: User, Entry, CalendarEvent
│   ├── schemas.py            ← API request/response shapes
│   ├── auth.py               ← API key authentication
│   ├── routers/
│   │   ├── input.py          ← Universal endpoint (audio/image/video/text)
│   │   └── entries.py        ← CRUD for dashboard
│   ├── services/
│   │   ├── transcription.py  ← OpenAI gpt-4o-mini-transcribe
│   │   ├── intent.py         ← Claude intent classification + vision
│   │   ├── google_auth.py    ← Google OAuth token management
│   │   ├── google_calendar.py← Create events in Google Calendar
│   │   ├── sms.py            ← Twilio SMS (text Johnny)
│   │   └── email_service.py  ← Gmail API (daily digest)
│   └── modules/
│       ├── base.py           ← Module interface definition
│       ├── memo.py           ← Generic handler (memo, diary, mood, etc.)
│       └── calendar.py       ← Calendar handler (Google Cal + SMS)
├── jobs/
│   └── daily_digest.py       ← Cron job: summarize day → email
├── setup_user.py             ← Run once: create your account
├── setup_google.py           ← Run once: Google OAuth authorization
├── .env.example              ← Template for secrets
├── requirements.txt
└── Dockerfile
```

## What Changed from v1

| Aspect | v1 | v2 |
|--------|----|----|
| Transcription model | whisper-1 ($0.006/min) | gpt-4o-mini-transcribe ($0.003/min, better accuracy) |
| Input types | Audio only | Audio, image, video, text |
| Calendar events | Shortcuts creates them | Server creates via Google Calendar API |
| SMS to Johnny | Not possible | Server sends via Twilio |
| Daily digest | Not implemented | Cron job → Claude summary → Gmail |
| Shortcuts complexity | ~10 action blocks with loops | 3 blocks total |
| Data model | Memo table | Universal Entry table (supports all 11 modules) |
| Multi-user ready | Basic | User-scoped everything + Google OAuth per user |

## Planned Modules (11 total)

| # | Module | Status | Description |
|---|--------|--------|-------------|
| 1 | Calendar | ✅ Phase 1 | Events → Google Calendar + SMS |
| 2 | Memos | ✅ Phase 1 | General notes and thoughts |
| 3 | Screenshot → Notes | 🔲 Phase 2 | Image analysis via Claude Vision |
| 4 | Work Tasks | 🔲 Phase 2 | Tasks → Google Tasks |
| 5 | Memo Updates | 🔲 Phase 2 | Update existing entries by reference |
| 6 | Expense Tracking | 🔲 Phase 4 | Money spent, receipt photos |
| 7 | Food Tracking | 🔲 Phase 4 | Meals and nutrition |
| 8 | Mood Tracking | 🔲 Phase 3 | Emotional check-ins |
| 9 | Ideas → Action | 🔲 Phase 3 | Creative/business ideas pipeline |
| 10 | Gym & Exercise | 🔲 Phase 4 | Workout logging |
| 11 | Daily Diary | 🔲 Phase 2 | Reflective journal entries |

## Getting Started

### 1. Setup
```bash
cd planner
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # Edit with your actual keys
```

### 2. Create your account
```bash
python setup_user.py
```

### 3. Setup Google (optional but recommended)
```bash
# First: create Google Cloud project, enable APIs, download credentials.json
python setup_google.py   # Opens browser for one-time authorization
```

### 4. Run
```bash
uvicorn app.main:app --reload --port 8000
# Visit http://localhost:8000/docs for interactive API docs
```

### 5. Setup cron for daily digest
```bash
crontab -e
# Add: 0 21 * * * cd /path/to/planner && /path/to/venv/bin/python -m jobs.daily_digest
```

### 6. Build the Shortcut
See SHORTCUTS_SETUP.md — it's 3 blocks.
