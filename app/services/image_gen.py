"""
services/image_gen.py — Zeph Image Generation
================================================
Generates images of Zeph using xAI's Grok Imagine API.
Uses OpenAI-compatible SDK with xAI base URL.
"""

import base64
import logging

from openai import OpenAI
from app.config import settings

logger = logging.getLogger("planner.image_gen")

# Zeph's core visual identity — included in every generation prompt
ZEPH_CHARACTER = (
    "Male elf character named Zephyr. Slim, lean, twinky build with delicate "
    "bishounen-inspired features. Pointed ears, tousled light brown hair, "
    "bare feet, mischievous and subtly seductive expression. "
    "Outfit: loose open white/cream shirt showing chest, fitted dark pants "
    "rolled at the ankles, nothing on the feet. "
    "Style: clean anime-influenced illustration with flat colors. "
    "Dark fantasy atmosphere — candlelight, ink swirls, weathered books — "
    "but still warm and inviting."
)

# Lazy-initialized xAI client
_xai_client = None


def _get_xai_client():
    global _xai_client
    if _xai_client is None:
        _xai_client = OpenAI(
            base_url="https://api.x.ai/v1",
            api_key=settings.xai_api_key,
        )
    return _xai_client


def generate_zeph_image(scene_description: str) -> bytes:
    """
    Generate an image of Zeph in a described scene.

    Args:
        scene_description: What Zeph is doing, the setting, his expression

    Returns:
        Image bytes (PNG).

    Raises:
        Exception if generation fails.
    """
    prompt = f"{ZEPH_CHARACTER}\n\nScene: {scene_description}"
    client = _get_xai_client()

    response = client.images.generate(
        model="grok-imagine-image",
        prompt=prompt,
    )

    # Grok returns a URL
    image_url = response.data[0].url
    if image_url:
        import httpx
        resp = httpx.get(image_url)
        resp.raise_for_status()
        logger.info(f"Zeph image generated: {scene_description[:80]}")
        return resp.content

    # Fallback: check for b64_json
    if response.data[0].b64_json:
        logger.info(f"Zeph image generated (b64): {scene_description[:80]}")
        return base64.b64decode(response.data[0].b64_json)

    raise ValueError("No image data in response")
