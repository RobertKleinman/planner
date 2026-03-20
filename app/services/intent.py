"""
intent.py — LLM Intent Classification & Data Extraction (Multimodal)
=====================================================================
Supports multi-intent: a single recording can produce multiple actions
across different modules.
"""

import json
import re
import logging
import base64
from datetime import datetime, timezone
from app.config import settings
from app.services.clients import anthropic_client

logger = logging.getLogger("planner.intent")

INTENT_SYSTEM_PROMPT = """You are the brain of a personal planner. You receive voice transcripts, text, or image descriptions and classify them into intents.

1. Identify ALL distinct actions in the input — there may be MULTIPLE.
2. For each, classify the MODULE, extract structured data, generate a brief spoken confirmation.
3. Return a JSON array of intents.

## Modules (5 only)

- **memo** — Catch-all for notes, thoughts, observations, reflections, feelings, ideas, expenses, food, workouts, moods, screenshots. Anything that's "save this" without a specific action. DEFAULT if unsure.
- **journal** — Recording what they DID — activities, experiences, how they spent their time.
- **calendar** — Events with specific date/time. Extract title, start, end, location.
- **task** — Action items, to-dos, things to do. Also task completion ("I finished X").
- **remember** — Facts to recall later: passwords, preferences, references, people's info. Key phrases: "remember that", "don't forget", "the password is".

## Multi-Intent Detection

Split distinct actions. Examples:

"Schedule dinner at 7 and buy dog food and remember the wifi password is bluemoon42"
→ calendar (dinner), task (dog food), remember (wifi password)

"Today I worked on the planner and I need to call the vet"
→ journal (planner work), task (call vet)

One action = array with one item.

## Classification Tips

- "I need to..." / "gotta..." = **task**
- "Remember that..." / "The password is..." = **remember**
- "Today I..." / "Just finished..." = **journal**
- Specific time + event = **calendar**
- Everything else (feelings, ideas, expenses, food, workouts, reflections) = **memo**

## Task Rules

Creating: split into separate tasks in "tasks" array.
- GROUP: "Errands", "House", "Work", "Health", "Dogs", "Personal", "Finance", "Shopping", etc.
- PRIORITY: "urgent", "do_today", "this_week", "keep_in_mind" (default "this_week")
- Extract due dates when mentioned.

Completing: set action to "complete", list what was completed.

## Remember Rules

- Split multiple items into "items" array
- CATEGORY: "People", "Passwords", "Health", "Finance", "Home", "Work", "Travel", "Food", "Reference", "Personal"
- Extract tags as keywords

## Journal Rules

- Keep entire entry as ONE piece (don't split activities)
- MINIMAL grammar cleanup: fix errors, remove filler words, keep their voice
- activity_type: "work", "social", "health", "errands", "creative", "learning", "household", "leisure", "travel", "mixed"
- Extract 2-5 keyword tags

## General Rules

- Current date/time: {current_datetime}
- Timezone: {timezone}
- Resolve relative dates to ISO 8601. If no end time for calendar, assume 1 hour.
- spoken_response: brief, conversational.

## Response Format

ONLY valid JSON (no markdown, no backticks):

{{
  "intents": [
    {{
      "module": "calendar",
      "title": "Dinner with Johnny",
      "spoken_response": "Scheduled dinner with Johnny at 7pm.",
      "data": {{"title": "Dinner with Johnny", "start": "ISO", "end": "ISO", "location": null, "notify_partner": false}}
    }},
    {{
      "module": "task",
      "title": "Buy dog food",
      "spoken_response": "Added task: buy dog food.",
      "data": {{"action": "create", "tasks": [{{"description": "Buy dog food", "group": "Errands", "priority": "this_week", "due": null}}]}}
    }}
  ]
}}

Data schemas:
- memo: {{"content": "..."}}
- journal: {{"content": "cleaned up text", "activity_type": "work|mixed|etc", "tags": ["tag1", "tag2"]}}
- calendar: {{"title": "...", "start": "ISO", "end": "ISO", "location": null, "notify_partner": true|false}}
- task (create): {{"action": "create", "tasks": [{{"description": "...", "group": "...", "priority": "...", "due": null}}]}}
- task (complete): {{"action": "complete", "completed": ["..."]}}
- remember: {{"items": [{{"content": "...", "category": "...", "tags": ["..."]}}]}}

{memo_topics_section}"""


def _build_memo_topics_section(memo_topics) -> str:
    """Build the memo topics prompt section from user's active topics."""
    if not memo_topics:
        return ""
    lines = [
        "## Memo Topic Matching",
        "",
        "The user has defined memo topics below. For EACH intent you return, check if it relates to any of these topics.",
        "If it does, include a \"memo_topics\" array in that intent object with the matched topic names and a brief excerpt.",
        "",
        "Active topics:",
    ]
    for t in memo_topics:
        desc = f": {t.description}" if t.description else ""
        lines.append(f"- \"{t.name}\"{desc}")
    lines += [
        "",
        "For each matched topic, add to the intent:",
        "  \"memo_topics\": [{\"name\": \"Topic Name\", \"excerpt\": \"Brief 1-2 sentence summary of what's relevant\"}]",
        "",
        "Only match when there's a genuine connection. Don't force matches. Omit memo_topics if nothing matches.",
    ]
    return "\n".join(lines)


def classify_intent(
    transcript: str = None,
    image_bytes: bytes = None,
    image_media_type: str = "image/jpeg",
    memo_topics: list = None,
) -> list:
    """
    Returns a LIST of intent dicts — one per detected action.
    memo_topics: list of MemoTopic objects (user's active topics for matching).
    """
    current_dt = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S%z")
    system = INTENT_SYSTEM_PROMPT.format(
        current_datetime=current_dt,
        timezone=settings.timezone,
        memo_topics_section=_build_memo_topics_section(memo_topics),
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
        return [{
            "module": "memo",
            "title": "Empty Input",
            "spoken_response": "I didn't catch anything. Could you try again?",
            "data": {"content": ""},
        }]

    if image_bytes and not transcript:
        content.append({
            "type": "text",
            "text": "Analyze this image and classify what type of information it contains."
        })

    response = anthropic_client.messages.create(
        model=settings.intent_model,
        max_tokens=2048,
        system=system,
        messages=[{"role": "user", "content": content}],
    )

    raw_text = response.content[0].text.strip()

    # Strip markdown code fences if LLM wraps response in ```json ... ```
    if raw_text.startswith("```"):
        raw_text = re.sub(r"^```(?:json)?\s*\n?", "", raw_text)
        raw_text = re.sub(r"\n?```\s*$", "", raw_text)

    try:
        result = json.loads(raw_text)
    except json.JSONDecodeError:
        # Try fixing common LLM JSON issues: trailing commas
        cleaned = re.sub(r",\s*([}\]])", r"\1", raw_text)
        try:
            result = json.loads(cleaned)
            logger.warning("Fixed malformed JSON from LLM (trailing commas)")
        except json.JSONDecodeError:
            logger.error(f"Failed to parse intent JSON: {raw_text[:200]}")
            return [{
                "module": "memo",
                "title": "Voice Memo",
                "spoken_response": "Got it — saved your memo.",
                "data": {"content": transcript or "Image processed"},
            }]

    # Handle various response formats
    if isinstance(result, dict) and "intents" in result:
        intents = result["intents"]
    elif isinstance(result, list):
        intents = result
    else:
        # Old single-intent format — wrap in list
        intents = [result]

    # Validate each intent has required fields
    validated = []
    for intent in intents:
        if not isinstance(intent, dict):
            logger.warning(f"Skipping non-dict intent: {intent}")
            continue
        if "module" not in intent:
            intent["module"] = "memo"
        if "data" not in intent:
            intent["data"] = {"content": transcript or ""}
        if "spoken_response" not in intent:
            intent["spoken_response"] = "Saved."
        if "title" not in intent:
            intent["title"] = intent.get("data", {}).get("content", "Entry")[:80] or "Entry"
        validated.append(intent)

    return validated if validated else [{
        "module": "memo",
        "title": "Voice Memo",
        "spoken_response": "Got it — saved your memo.",
        "data": {"content": transcript or "Image processed"},
    }]
