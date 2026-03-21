"""
services/image_gen.py — Zeph Image Generation
================================================
Generates images of Zeph using OpenAI's gpt-image-1 model.
Uses a detailed character description for consistency.
"""

import base64
import logging

from app.services.clients import openai_client

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


def generate_zeph_image(scene_description: str, quality: str = "medium") -> bytes | None:
    """
    Generate an image of Zeph in a described scene.

    Args:
        scene_description: What Zeph is doing, the setting, his expression
        quality: "low", "medium", or "high" (affects cost and detail)

    Returns:
        Image bytes (PNG) or None if generation failed.
    """
    prompt = f"{ZEPH_CHARACTER}\n\nScene: {scene_description}"

    try:
        response = openai_client.images.generate(
            model="gpt-image-1-mini",
            prompt=prompt,
            n=1,
            size="1024x1024",
            quality=quality,
        )

        # gpt-image-1 returns base64
        image_data = response.data[0].b64_json
        image_bytes = base64.b64decode(image_data)

        logger.info(f"Zeph image generated ({quality}): {scene_description[:80]}")
        return image_bytes

    except Exception as e:
        logger.error(f"Image generation failed: {e}", exc_info=True)
        return None
