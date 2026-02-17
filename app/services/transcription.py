"""
transcription.py — Audio → Text via gpt-4o-mini-transcribe
============================================================
WHAT CHANGED FROM V1:
- Model: whisper-1 → gpt-4o-mini-transcribe
  (OpenAI's current recommendation: better accuracy, half the cost)
- Response handling: gpt-4o-mini-transcribe returns JSON by default
  with a .text field, but the Python SDK abstracts this — you still
  just read response.text. So the code barely changes.
"""

from openai import OpenAI
from app.config import settings
import io

client = OpenAI(api_key=settings.openai_api_key)


async def transcribe_audio(audio_bytes: bytes, filename: str = "recording.m4a") -> str:
    """
    Convert audio bytes to text using OpenAI's recommended transcription model.

    Supports: flac, mp3, mp4, mpeg, mpga, m4a, ogg, wav, webm
    iPhone records .m4a by default, which works perfectly.
    """
    audio_file = io.BytesIO(audio_bytes)
    audio_file.name = filename

    response = client.audio.transcriptions.create(
        model=settings.transcription_model,  # "gpt-4o-mini-transcribe"
        file=audio_file,
        # Note: gpt-4o-mini-transcribe only supports "json" or "text" format.
        # Default is json which returns {"text": "..."}.
        # The SDK handles this — response.text works either way.
    )

    # The SDK returns an object with a .text attribute regardless of model.
    return response.text.strip()
