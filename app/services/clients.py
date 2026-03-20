"""
clients.py — Shared API Client Instances
==========================================
Single Anthropic and OpenAI client instances, imported everywhere.
"""

from anthropic import Anthropic
from openai import OpenAI
from app.config import settings

anthropic_client = Anthropic(api_key=settings.anthropic_api_key)
openai_client = OpenAI(api_key=settings.openai_api_key)
