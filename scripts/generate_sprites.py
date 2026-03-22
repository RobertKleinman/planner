"""
generate_sprites.py — Generate tamagotchi sprites via anime-gen API
====================================================================
Calls the local anime-gen ComfyUI backend to generate pixel art sprites
for Zeph, Briar, and room backgrounds.

Prerequisites:
  - anime-gen backend running on localhost:8001
  - ComfyUI running on localhost:8000
  - novaPixelsXL_v30.safetensors checkpoint installed
  - [Qwen.Image]PixelArt_Redmond.safetensors LoRA installed

Usage:
    python scripts/generate_sprites.py
    python scripts/generate_sprites.py --only zeph-reading
    python scripts/generate_sprites.py --only rooms
"""

import sys
import os
import argparse
import base64
import requests
import time
from io import BytesIO
from PIL import Image

API_BASE = "http://localhost:8001/api"
CHECKPOINT = "novaPixelsXL_v30.safetensors"
LORA = "[Qwen.Image]PixelArt_Redmond.safetensors"
LORA_STRENGTH = 0.75

OUTPUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static", "sprites")

# Chroma key background color for removal
BG_COLOR = (0, 255, 0)  # bright green
BG_TOLERANCE = 80


# ─── Sprite Definitions ──────────────────────────────────────

CHARACTER_SPRITES = {
    # Zeph poses — generate at 1024x1024, output at 128x128
    "zeph/reading": {
        "prompt": "pixel art character sprite, male elf sitting reading a large book, pointy ears, messy dark hair, white loose medieval shirt, dark fitted pants, barefoot, warm lighting, centered in frame, full body visible, solid bright green background",
        "size": (128, 128),
    },
    "zeph/writing": {
        "prompt": "pixel art character sprite, male elf sitting at desk writing with feather quill, pointy ears, messy dark hair, white loose medieval shirt, dark fitted pants, barefoot, inkwell nearby, centered in frame, full body visible, solid bright green background",
        "size": (128, 128),
    },
    "zeph/sleeping": {
        "prompt": "pixel art character sprite, male elf sleeping peacefully lying down on side, pointy ears, messy dark hair, white loose medieval shirt, dark fitted pants, barefoot, eyes closed, centered in frame, full body visible, solid bright green background",
        "size": (128, 128),
    },
    "zeph/eating": {
        "prompt": "pixel art character sprite, male elf sitting at small table eating from bowl with spoon, pointy ears, messy dark hair, white loose medieval shirt, dark fitted pants, barefoot, centered in frame, full body visible, solid bright green background",
        "size": (128, 128),
    },
    "zeph/magic": {
        "prompt": "pixel art character sprite, male elf standing casting magic spell, glowing magical particles around hands, pointy ears, messy dark hair, white loose medieval shirt, dark fitted pants, barefoot, magical aura, centered in frame, full body visible, solid bright green background",
        "size": (128, 128),
    },
    "zeph/walking": {
        "prompt": "pixel art character sprite, male elf walking mid-stride to the right, pointy ears, messy dark hair, white loose medieval shirt, dark fitted pants, barefoot, natural walking pose, centered in frame, full body visible, solid bright green background",
        "size": (128, 128),
    },
    "zeph/sitting": {
        "prompt": "pixel art character sprite, male elf sitting cross-legged on ground relaxed, pointy ears, messy dark hair, white loose medieval shirt, dark fitted pants, barefoot, peaceful expression, centered in frame, full body visible, solid bright green background",
        "size": (128, 128),
    },
    # Briar poses
    "briar/sleeping": {
        "prompt": "pixel art character sprite, large scruffy wolfhound dog sleeping curled up in a ball, shaggy grey-brown fur, one torn ear, peaceful, centered in frame, solid bright green background",
        "size": (128, 80),
    },
    "briar/sitting": {
        "prompt": "pixel art character sprite, large scruffy wolfhound dog sitting upright looking alert, shaggy grey-brown fur, one torn ear, tongue slightly out, centered in frame, solid bright green background",
        "size": (128, 80),
    },
    "briar/following": {
        "prompt": "pixel art character sprite, large scruffy wolfhound dog trotting walking to the right, shaggy grey-brown fur, one torn ear, tail wagging, happy, centered in frame, solid bright green background",
        "size": (128, 80),
    },
}

ROOM_SPRITES = {
    "rooms/study": {
        "prompt": "pixel art game background, dark stone tower study interior, side view platformer style, wooden desk with open books and scrolls, melting candles with warm orange glow, tall bookshelf packed with colorful books, arched stone window showing grey stormy sea, ink bottles and quill pen on desk, cracked stone walls with ivy, cozy cluttered atmosphere, dark fantasy rpg aesthetic, detailed pixel art, no characters, moody warm lighting",
        "size": (320, 240),
    },
    "rooms/kitchen": {
        "prompt": "pixel art game background, medieval stone tower kitchen interior, side view platformer style, heavy wooden table with bread and bowls, large stone fireplace with bright crackling fire, warm orange glow filling room, hanging copper pots on wall, wooden chair, shelves with glass jars and pottery, stone tile floor, cozy rustic atmosphere, dark fantasy rpg aesthetic, detailed pixel art, no characters",
        "size": (320, 240),
    },
    "rooms/outside-day": {
        "prompt": "pixel art game background, dramatic cliff edge overlooking grey restless ocean, side view platformer style, old stone tower visible on left side, winding dirt path along cliff, single wind-bent gnarled tree, wild grass and wildflowers, overcast dramatic sky with heavy clouds, seabirds flying, waves crashing below, coastal dark fantasy rpg aesthetic, detailed pixel art, no characters, daytime",
        "size": (320, 240),
    },
    "rooms/outside-night": {
        "prompt": "pixel art game background, cliff edge overlooking dark moonlit ocean at night, side view platformer style, old stone tower on left with warm glowing window, winding path, tree silhouette against starry sky, bright moon, twinkling stars, gentle silver waves below, dark blue and purple palette, coastal dark fantasy rpg aesthetic, detailed pixel art, no characters, nighttime",
        "size": (320, 240),
    },
}


def check_api():
    """Verify anime-gen API is running."""
    try:
        r = requests.get(f"{API_BASE}/status", timeout=5)
        data = r.json()
        if data.get("connected"):
            print("[OK] anime-gen API connected to ComfyUI")
            return True
        else:
            print("[FAIL] anime-gen API running but ComfyUI not connected")
            return False
    except Exception as e:
        print(f"[FAIL] anime-gen API not reachable: {e}")
        print("  Start it with: cd C:\\Users\\rober\\Projects\\imagegenera\\anime-gen && start.bat")
        return False


def generate_sprite(name: str, config: dict, is_room: bool = False) -> bool:
    """Generate a single sprite via the anime-gen API."""
    print(f"\n  Generating {name}...", end=" ", flush=True)

    payload = {
        "prompt": config["prompt"],
        "negative_prompt": "blurry, smooth, photorealistic, 3d render, realistic, photograph, text, watermark, signature, multiple characters, crowd, low quality, jpeg artifacts, gradient, anti-aliased, soft edges",
        "checkpoint": CHECKPOINT,
        "lora": LORA,
        "lora_strength": LORA_STRENGTH,
        "quality": "quality",
        "width": 1024,
        "height": 1024 if not is_room else 768,
        "seed": -1,
        "skip_enhance": True,
        "regions_enabled": False,
        "img2img_enabled": False,
        "image_mode": "anime",
        "style": "pixel_art",
    }

    try:
        r = requests.post(f"{API_BASE}/generate", json=payload, timeout=120)
        if r.status_code != 200:
            print(f"FAILED (HTTP {r.status_code})")
            try:
                print(f"    Error: {r.json().get('detail', r.text[:200])}")
            except Exception:
                print(f"    Response: {r.text[:200]}")
            return False

        data = r.json()
        image_b64 = data.get("image")
        if not image_b64:
            print("FAILED (no image in response)")
            return False

        # Decode image
        image_bytes = base64.b64decode(image_b64)
        img = Image.open(BytesIO(image_bytes)).convert("RGBA")

        target_w, target_h = config["size"]

        if not is_room:
            # Character sprite: remove green background
            img = remove_bg_chroma(img)

        # Downscale with nearest-neighbor (keeps pixels sharp)
        img = img.resize((target_w, target_h), Image.NEAREST)

        # Save
        output_path = os.path.join(OUTPUT_DIR, f"{name}.png")
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        img.save(output_path, "PNG")

        print(f"OK -> {output_path} ({target_w}x{target_h})")
        return True

    except requests.Timeout:
        print("FAILED (timeout)")
        return False
    except Exception as e:
        print(f"FAILED ({e})")
        return False


def remove_bg_chroma(img: Image.Image) -> Image.Image:
    """Remove green background using HSV color space for better coverage."""
    import colorsys

    data = img.getdata()
    new_data = []
    for pixel in data:
        r, g, b, a = pixel
        # Convert to HSV
        h, s, v = colorsys.rgb_to_hsv(r / 255.0, g / 255.0, b / 255.0)
        hue_deg = h * 360

        # Green hue range: roughly 70-170 degrees
        # Also catch desaturated greens and very bright/dark greens
        is_green = (70 < hue_deg < 170) and (s > 0.15) and (v > 0.15)

        # Also catch near-white and near-grey pixels at edges (anti-aliasing artifacts)
        is_edge = (s < 0.1) and (v > 0.85)

        if is_green or is_edge:
            new_data.append((0, 0, 0, 0))
        else:
            new_data.append(pixel)

    result = Image.new("RGBA", img.size)
    result.putdata(new_data)
    return result


def main():
    parser = argparse.ArgumentParser(description="Generate tamagotchi sprites")
    parser.add_argument("--only", help="Generate only this sprite (e.g., 'zeph-reading', 'rooms', 'briar')")
    parser.add_argument("--list", action="store_true", help="List all sprite names")
    args = parser.parse_args()

    all_sprites = {**CHARACTER_SPRITES, **ROOM_SPRITES}

    if args.list:
        print("Available sprites:")
        for name in all_sprites:
            print(f"  {name}")
        return

    if not check_api():
        sys.exit(1)

    # Filter sprites if --only specified
    if args.only:
        if args.only == "rooms":
            sprites = {k: v for k, v in ROOM_SPRITES.items()}
        elif args.only == "briar":
            sprites = {k: v for k, v in CHARACTER_SPRITES.items() if k.startswith("briar/")}
        elif args.only == "zeph":
            sprites = {k: v for k, v in CHARACTER_SPRITES.items() if k.startswith("zeph/")}
        else:
            # Try exact match or partial
            name = args.only.replace("-", "/")
            if name in all_sprites:
                sprites = {name: all_sprites[name]}
            else:
                print(f"Unknown sprite: {args.only}")
                print("Use --list to see available sprites")
                sys.exit(1)
    else:
        sprites = all_sprites

    print(f"Generating {len(sprites)} sprites using {CHECKPOINT} + {LORA}")
    print(f"Output: {OUTPUT_DIR}")

    success = 0
    failed = 0

    for name, config in sprites.items():
        is_room = name.startswith("rooms/")
        if generate_sprite(name, config, is_room):
            success += 1
        else:
            failed += 1
        # Small delay between requests
        time.sleep(1)

    print(f"\n{'='*40}")
    print(f"Done: {success} succeeded, {failed} failed")
    if success > 0:
        print(f"Sprites saved to: {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
