"""
intent.py — LLM Intent Classification & Data Extraction (Multimodal)
=====================================================================
WHAT CHANGED FROM V1:
- Now handles BOTH text and images (Claude's vision capability)
- Module list expanded to cover all planned modules
- classify_intent() accepts optional image_bytes for screenshot analysis
- The system prompt defines all module types and their data schemas

This is still the brain of the system. The quality of everything downstream
depends on how well this prompt works. We'll iterate on it.
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

- **memo**: General note, thought, idea, or reminder to self. DEFAULT if nothing else fits.
- **calendar**: They want to create a calendar event. Extract title, start time, end time, location, whether to notify their partner.
- **diary**: A personal diary/journal entry about their day, feelings, experiences. Usually longer and reflective.
- **task**: A work task or to-do item. Extract description, due date, project name if mentioned.
- **screenshot_note**: Input is from an image/screenshot. Extract and organize the visible information.
- **expense**: Money spent. Extract amount, currency (default CAD), category, vendor/store.
- **food**: Food eaten. Extract meal type (breakfast/lunch/dinner/snack), items, approximate calories if obvious.
- **mood**: How they're feeling. Extract mood rating (1-10), any triggers or context mentioned.
- **idea**: A business idea, project idea, or creative concept. Extract the core concept and any action steps.
- **gym**: Exercise or workout. Extract exercise names, sets, reps, weight, duration, type (cardio/strength).
- **work**: Work accomplishment, project update, or professional note. Extract project name, what was done, any metrics.

## Rules

- Current date and time: {current_datetime}
- Timezone: {timezone}
- Resolve relative dates: "tomorrow", "next Monday", "in 2 hours" → ISO 8601 datetime strings.
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
    // task: {{"description": "...", "due": "ISO datetime"|null, "project": "project name"|null}}
    // screenshot_note: {{"content": "extracted/organized information from the image", "source_type": "receipt|schedule|notes|message|other"}}
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

    Args:
        transcript: Text input (from transcription or direct text).
        image_bytes: Raw image bytes (from screenshot or photo upload).
        image_media_type: MIME type of the image.

    Returns:
        Dict with: module, title, spoken_response, data
    """
    current_dt = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S%z")
    system = INTENT_SYSTEM_PROMPT.format(
        current_datetime=current_dt,
        timezone=settings.timezone,
    )

    # Build the message content — can be text, image, or both.
    # Claude's API accepts a list of content blocks for multimodal input.
    content = []

    if image_bytes:
        # Encode image as base64 for Claude's vision API
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
        # No input at all — shouldn't happen but handle gracefully
        return {
            "module": "memo",
            "title": "Empty Input",
            "spoken_response": "I didn't catch anything. Could you try again?",
            "data": {"content": ""},
        }

    # If only image, no text, add a prompt asking Claude to analyze it
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
        # Fallback: treat as a plain memo
        result = {
            "module": "memo",
            "title": "Voice Memo",
            "spoken_response": "Got it — saved your memo.",
            "data": {"content": transcript or "Image processed"},
        }

    return result
