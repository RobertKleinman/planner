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

- **memo**: General note, thought, or reminder to self. DEFAULT if nothing else fits.
- **calendar**: They want to create a calendar event. Extract title, start time, end time, location.
- **diary**: A personal diary/journal entry about their day, feelings, experiences. Usually longer and reflective.
- **task**: Action items, to-do items, things they need to do, errands, things to remember to do. This includes:
  - Creating new tasks: "I need to pick up dry cleaning and buy dog food"
  - Completing tasks: "I finished picking up the dry cleaning" or "done with the dry cleaning"
  - Any list of things that need doing
  - Errands, chores, action items, follow-ups
- **screenshot_note**: Input is from an image/screenshot. Extract and organize the visible information.
- **expense**: Money spent. Extract amount, currency (default CAD), category, vendor/store.
- **food**: Food eaten. Extract meal type, items, approximate calories if obvious.
- **mood**: How they're feeling. Extract mood rating (1-10), any triggers or context mentioned.
- **idea**: A business idea, project idea, or creative concept.
- **gym**: Exercise or workout. Extract exercise names, sets, reps, weight, duration.
- **work**: Work accomplishment, project update, or professional note.

## Task Classification Rules

Be generous with task classification. If someone says they "need to", "should", "have to", "gotta", "want to" do something — that's a task, not a memo. If they list multiple things — that's tasks.

When creating tasks, you MUST:
- Split multiple items into separate tasks in the "tasks" array
- Assign a GROUP (category) to organize them. Use short labels like: "Errands", "House", "Work", "Health", "Dogs", "Personal", "Finance", "Shopping", etc. If the user says "for the house:" or "work stuff:", use that as the group. Otherwise infer the best group.
- Assign a PRIORITY based on urgency cues:
  - "urgent" — words like "urgent", "ASAP", "emergency", "critical", "the leak is getting worse"
  - "do_today" — words like "today", "need to", "gotta", "should really", "before tonight"
  - "this_week" — words like "this week", "soon", "before Friday", "need to get around to"
  - "keep_in_mind" — words like "at some point", "eventually", "whenever", "would be nice", "sometime"
  - Default to "this_week" if unclear
- Extract due dates when mentioned (as ISO 8601 datetime strings)

When completing tasks, set action to "complete" and list what was completed in the "completed" array. Matching is fuzzy so just extract the key description words.

## General Rules

- Current date and time: {current_datetime}
- Timezone: {timezone}
- Resolve relative dates: "tomorrow", "next Monday", "in 2 hours" -> ISO 8601 datetime strings.
- If no end time for calendar events, assume 1 hour duration.
- Be generous: "dentist at 2" = calendar event, not a memo.
- "I feel..." or emotional content = mood or diary depending on length.
- Money amounts = expense. Food mentions with meal context = food.
- The spoken_response should be brief and conversational.
- If the input mentions notifying or telling their partner/husband about a calendar event, set notify_partner to true.

## Response Format

Respond with ONLY valid JSON (no markdown, no backticks). Use this structure:

{{
  "module": "memo|calendar|diary|task|screenshot_note|expense|food|mood|idea|gym|work",
  "title": "Short title summarizing the input",
  "spoken_response": "Brief conversational confirmation",
  "data": {{
    // Module-specific fields — include only relevant ones:

    // calendar: {{"title": "...", "start": "ISO datetime", "end": "ISO datetime", "location": null, "notify_partner": true|false}}
    // memo: {{"content": "the memo text"}}
    // diary: {{"content": "diary entry text", "highlights": ["key moment 1", "key moment 2"]}}

    // task (creating): {{"action": "create", "tasks": [{{"description": "pick up dry cleaning", "group": "Errands", "priority": "do_today", "due": null}}, {{"description": "buy dog food", "group": "Errands", "priority": "this_week", "due": null}}] }}
    // task (completing): {{"action": "complete", "completed": ["dry cleaning", "dog food"]}}

    // screenshot_note: {{"content": "extracted info", "source_type": "receipt|schedule|notes|message|other"}}
    // expense: {{"amount": 42.50, "currency": "CAD", "category": "groceries|dining|transport|health|entertainment|shopping|bills|other", "vendor": "store name"|null, "items": ["item1"]|null}}
    // food: {{"meal": "breakfast|lunch|dinner|snack", "items": ["item1", "item2"], "calories_estimate": null}}
    // mood: {{"rating": 7, "triggers": ["trigger1"], "notes": "additional context"}}
    // idea: {{"concept": "the core idea", "category": "business|creative|project|other", "action_steps": ["step1"]|null}}
    // gym: {{"exercises": [{{"name": "...", "sets": 3, "reps": 10, "weight_lbs": 135}}], "duration_minutes": null, "type": "strength|cardio|flexibility|mixed"}}
    // work: {{"project": "project name"|null, "description": "what was done", "metrics": "any numbers or achievements"|null}}
  }}
}}"""


async def classify_intent(
    transcript: str = None,
    image_bytes: bytes = None,
    image_media_type: str = "image/jpeg",
) -> dict:
    """
    Send text and/or image to Claude for intent classification.
    """
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
