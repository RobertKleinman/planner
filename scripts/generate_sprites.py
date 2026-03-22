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
    # Zeph poses
    "zeph/reading": {
        "prompt": "pixel art, side view, single male elf character sitting at desk reading a book, pointy ears, tousled dark hair, loose white shirt, dark pants, barefoot, warm candlelight, solid bright green background, 32x32 sprite style, clean pixel edges, dark fantasy",
        "size": (48, 48),
    },
    "zeph/writing": {
        "prompt": "pixel art, side view, single male elf character sitting at desk writing with quill pen, pointy ears, tousled dark hair, loose white shirt, dark pants, barefoot, warm candlelight, solid bright green background, 32x32 sprite style, clean pixel edges, dark fantasy",
        "size": (48, 48),
    },
    "zeph/sleeping": {
        "prompt": "pixel art, side view, single male elf character sleeping curled up, pointy ears, tousled dark hair, loose white shirt, dark pants, barefoot, peaceful, solid bright green background, 32x32 sprite style, clean pixel edges, dark fantasy",
        "size": (48, 48),
    },
    "zeph/eating": {
        "prompt": "pixel art, side view, single male elf character sitting at table eating from a bowl, pointy ears, tousled dark hair, loose white shirt, dark pants, barefoot, solid bright green background, 32x32 sprite style, clean pixel edges, dark fantasy",
        "size": (48, 48),
    },
    "zeph/magic": {
        "prompt": "pixel art, side view, single male elf character standing with hands raised casting magic, glowing sparks, pointy ears, tousled dark hair, loose white shirt, dark pants, barefoot, solid bright green background, 32x32 sprite style, clean pixel edges, dark fantasy",
        "size": (48, 48),
    },
    "zeph/walking": {
        "prompt": "pixel art, side view, single male elf character walking, mid-stride, pointy ears, tousled dark hair, loose white shirt, dark pants, barefoot, solid bright green background, 32x32 sprite style, clean pixel edges, dark fantasy",
        "size": (48, 48),
    },
    "zeph/sitting": {
        "prompt": "pixel art, side view, single male elf character sitting on the ground relaxed, legs crossed, pointy ears, tousled dark hair, loose white shirt, dark pants, barefoot, solid bright green background, 32x32 sprite style, clean pixel edges, dark fantasy",
        "size": (48, 48),
    },
    # Briar poses
    "briar/sleeping": {
        "prompt": "pixel art, side view, single large scruffy wolfhound dog sleeping curled up, grey-brown fur, one torn ear, peaceful, solid bright green background, 32x32 sprite style, clean pixel edges, dark fantasy",
        "size": (48, 32),
    },
    "briar/sitting": {
        "prompt": "pixel art, side view, single large scruffy wolfhound dog sitting upright alert, grey-brown fur, one torn ear, solid bright green background, 32x32 sprite style, clean pixel edges, dark fantasy",
        "size": (48, 32),
    },
    "briar/following": {
        "prompt": "pixel art, side view, single large scruffy wolfhound dog walking trotting, grey-brown fur, one torn ear, tail up, solid bright green background, 32x32 sprite style, clean pixel edges, dark fantasy",
        "size": (48, 32),
    },
}

ROOM_SPRITES = {
    "rooms/study": {
        "prompt": "pixel art, interior scene, dark stone tower study room, wooden desk covered in books and scrolls, melting candles with warm glow, tall bookshelf against wall, arched window showing grey sea and clouds, ink bottles, quill pen, stone walls with cracks, cozy and cluttered, dark fantasy aesthetic, no characters, side view",
        "size": (320, 240),
    },
    "rooms/kitchen": {
        "prompt": "pixel art, interior scene, medieval stone tower kitchen, wooden table with plates and bread, stone fireplace with crackling fire, warm orange glow, hanging pots, wooden chair, shelves with jars, stone floor, cozy and simple, dark fantasy aesthetic, no characters, side view",
        "size": (320, 240),
    },
    "rooms/outside-day": {
        "prompt": "pixel art, outdoor scene, cliff edge overlooking grey restless sea, stone tower base visible on left, winding dirt path, single wind-bent tree, wild grass, overcast sky with clouds, seabirds in distance, coastal dark fantasy aesthetic, no characters, side view, daytime",
        "size": (320, 240),
    },
    "rooms/outside-night": {
        "prompt": "pixel art, outdoor scene, cliff edge overlooking dark sea at night, stone tower base visible on left with lit window, winding dirt path, single tree silhouette, stars and moon, gentle waves below, coastal dark fantasy aesthetic, no characters, side view, nighttime, dark blue palette",
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
        "negative_prompt": "blurry, smooth, photorealistic, 3d render, realistic, photograph, text, watermark, signature, multiple characters, crowd",
        "checkpoint": CHECKPOINT,
        "lora": LORA,
        "lora_strength": LORA_STRENGTH,
        "quality": "balanced",
        "width": 512,
        "height": 512 if not is_room else 384,
        "seed": -1,
        "skip_enhance": True,  # use our prompts directly, don't run through Grok
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
    """Remove bright green background via chroma key."""
    data = img.getdata()
    new_data = []
    for pixel in data:
        r, g, b, a = pixel
        # Check if pixel is close to bright green
        if (abs(r - BG_COLOR[0]) < BG_TOLERANCE and
            abs(g - BG_COLOR[1]) < BG_TOLERANCE and
            abs(b - BG_COLOR[2]) < BG_TOLERANCE):
            new_data.append((0, 0, 0, 0))  # transparent
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
