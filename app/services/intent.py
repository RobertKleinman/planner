"""
intent.py — LLM Intent Classification & Data Extraction (Multimodal)
=====================================================================
"""

import json
import base64
from datetime import datetime, timezone
from anthropic import Anthropic
from app.config import settings

client = Anthropic(api_key=settings.anthropic_api_key)

INTENT_SYSTEM_PROMPT = """You are the brain of a personal planner system. You receive input that has been transcribed from voice, typed as text, or described from an image/screenshot. Your job is to:

1. Classify which MODULE should handle this input.
2. Extract STRUCTURED DATA relevant to that module.
3. Generate a brief SPOKEN RESPONSE confirming what you understood.

## Available Modules

- **memo**: General note or thought. DEFAULT if nothing else fits.
- **calendar**: They want to create a calendar event. Extract title, start time, end time, location.
- **task**: Action items, to-do items, things they need to do, errands.
  - Creating: "I need to pick up dry cleaning and buy dog food"
  - Completing: "I finished picking up the dry cleaning"
- **remember**: Things they want to REMEMBER — facts, info, preferences, references, things to keep in mind for the future. Key phrases: "remember that", "don't forget", "keep in mind", "note that", "I should know".
  - "Remember that Johnny's mom's birthday is March 15"
  - "The wifi password at the cottage is bluemoon42"
  - "My insurance policy number is ABC123"
  - "Rob's favorite restaurant is Canoe"
- **journal**: Recording what they DID today — activities, experiences, accomplishments, how they spent their time. Past tense or present tense describing current/recent activity. Key phrases: "today I", "just finished", "spent time", "worked on", "went to", "had a meeting".
  - "Today I worked on the data governance policy, then had a meeting with Judy about the EDW"
  - "Just got back from walking Biscuit and Bentley, then made dinner"
  - "Spent 2 hours on the planner project, got the SMS working"
- **diary**: Deep personal reflection, feelings, emotional processing. Longer and more introspective than journal.
- **screenshot_note**: Input is from an image/screenshot.
- **expense**: Money spent. Extract amount, currency (default CAD), category, vendor.
- **food**: Food eaten. Extract meal type, items, calories.
- **mood**: How they're feeling. Extract rating (1-10), triggers.
- **idea**: A business/creative/project idea.
- **gym**: Exercise or workout.
- **work**: Work accomplishment or project update.

## Classification Tips

- "I need to..." / "I have to..." / "gotta..." = **task**
- "Remember that..." / "Don't forget..." / "The password is..." = **remember**
- "Today I..." / "Just finished..." / "Worked on..." / "Went to..." = **journal**
- "I feel..." / deep emotional reflection = **diary** or **mood**
- Specific time + event = **calendar**
- If it could be both remember and memo, prefer **remember** if it's factual info they'd want to look up later.
- If it could be both journal and memo, prefer **journal** if they're describing activities.

## Task Rules

When creating tasks:
- Split multiple items into separate tasks in the "tasks" array
- Assign a GROUP: "Errands", "House", "Work", "Health", "Dogs", "Personal", "Finance", "Shopping", etc.
- Assign PRIORITY: "urgent", "do_today", "this_week", "keep_in_mind" (default "this_week")
- Extract due dates when mentioned

When completing tasks: set action to "complete", list what was completed.

## Remember Rules

When storing things to remember:
- Split multiple items into separate items in the "items" array
- Assign a CATEGORY: "People", "Passwords", "Health", "Finance", "Home", "Work", "Travel", "Food", "Shopping", "Reference", "Personal", etc.
- Extract relevant tags as keywords

## Journal Rules

When logging journal activities:
- Split multiple activities into the "activities" array
- Assign activity_type: "work", "social", "health", "errands", "creative", "learning", "household", "leisure", "travel"
- Assign a topic if it relates to a specific project or recurring theme (e.g. "Planner Project", "Data Governance", "Dog Care")

## General Rules

- Current date and time: {current_datetime}
- Timezone: {timezone}
- Resolve relative dates to ISO 8601 datetime strings.
- If no end time for calendar events, assume 1 hour duration.
- The spoken_response should be brief and conversational.

## Response Format

Respond with ONLY valid JSON (no markdown, no backticks):

{{
  "module": "memo|calendar|task|remember|journal|diary|screenshot_note|expense|food|mood|idea|gym|work",
  "title": "Short title",
  "spoken_response": "Brief confirmation",
  "data": {{
    // calendar: {{"title": "...", "start": "ISO", "end": "ISO", "location": null, "notify_partner": true|false}}
    // memo: {{"content": "..."}}
    // task (create): {{"action": "create", "tasks": [{{"description": "...", "group": "Errands", "priority": "do_today", "due": null}}]}}
    // task (complete): {{"action": "complete", "completed": ["dry cleaning"]}}
    // remember: {{"items": [{{"content": "Johnny's mom birthday is March 15", "category": "People", "tags": ["johnny", "birthday"]}}]}}
    // journal: {{"activities": [{{"content": "Worked on data governance policy for 2 hours", "activity_type": "work", "topic": "Data Governance"}}]}}
    // diary: {{"content": "...", "highlights": ["..."]}}
    // expense: {{"amount": 42.50, "currency": "CAD", "category": "groceries", "vendor": null}}
    // mood: {{"rating": 7, "triggers": ["..."], "notes": "..."}}
    // idea: {{"concept": "...", "category": "business|creative|project|other", "action_steps": []}}
    // gym: {{"exercises": [{{"name": "...", "sets": 3, "reps": 10, "weight_lbs": 135}}], "type": "strength"}}
    // work: {{"project": null, "description": "...", "metrics": null}}
  }}
}}"""


async def classify_intent(
    transcript: str = None,
    image_bytes: bytes = None,
    image_media_type: str = "image/jpeg",
) -> dict:
    current_dt = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S%z")
    system = INTENT_SYSTEM_PROMPT.format(
        current_datetime=current_dt,
        timezone=settings.timezone,
    )

    content = []

    if image_bytes:
        b64_image = base64.b64encode(image_bytes).decode("utf-8")
        content.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": image_media_type,
                "data": b64_image,
            }
        })

    if transcript:
        content.append({"type": "text", "text": transcript})
    elif not image_bytes:
        return {
            "module": "memo",
            "title": "Empty Input",
            "spoken_response": "I didn't catch anything. Could you try again?",
            "data": {"content": ""},
        }

    if image_bytes and not transcript:
        content.append({
            "type": "text",
            "text": "Analyze this image and classify what type of information it contains."
        })

    response = client.messages.create(
        model=settings.intent_model,
        max_tokens=1024,
        system=system,
        messages=[{"role": "user", "content": content}],
    )

    raw_text = response.content[0].text.strip()

    try:
        result = json.loads(raw_text)
    except json.JSONDecodeError:
        result = {
            "module": "memo",
            "title": "Voice Memo",
            "spoken_response": "Got it — saved your memo.",
            "data": {"content": transcript or "Image processed"},
        }

    return result
