"""
clients.py — Shared API Client Instances
==========================================
Anthropic for live chat, OpenAI for background memory tasks.
"""

from anthropic import Anthropic
from openai import OpenAI
from app.config import settings

anthropic_client = Anthropic(api_key=settings.anthropic_api_key)
openai_client = OpenAI(api_key=settings.openai_api_key)

# Background task model — cheap, used for consolidation/compaction/summarization
BACKGROUND_MODEL = "gpt-4.1-nano"
