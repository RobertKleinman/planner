"""
generate_sprites.py — Generate tamagotchi character art and backgrounds
========================================================================
Uses anime-gen ComfyUI backend. Illustrated style (not pixel art).

Generates:
  - Zeph poses (7) + 3 idle variation frames each = 28 character images
  - Briar poses (3) + 3 idle variations each = 12 character images
  - Room backgrounds (3 rooms x multiple weather variants) = ~12 backgrounds

Usage:
    python scripts/generate_sprites.py
    python scripts/generate_sprites.py --only zeph
    python scripts/generate_sprites.py --only backgrounds
    python scripts/generate_sprites.py --only zeph/reading
    python scripts/generate_sprites.py --list
"""

import sys
import os
import argparse
import base64
import requests
import time
import json
import colorsys
from io import BytesIO
from PIL import Image

API_BASE = "http://localhost:8001/api"
CHECKPOINT = "novaAnimeXL_ilV170.safetensors"
LORA = "none"
LORA_STRENGTH = 0.0

OUTPUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static", "sprites")

# Character base prompt elements
ZEPH_BASE = "anime illustration, single male elf, pointy ears, messy dark hair, pale skin, handsome, slim build, loose white medieval shirt, dark fitted pants, barefoot, dark fantasy aesthetic"
BRIAR_BASE = "anime illustration, single large scruffy wolfhound dog, shaggy grey-brown fur, one torn ear, loyal gentle expression, dark fantasy aesthetic"
NEGATIVE = "blurry, low quality, jpeg artifacts, watermark, text, signature, multiple characters, crowd, chibi, deformed, bad anatomy, extra limbs, pixel art, pixelated"

# ─── Character Definitions ───────────────────────────────────

CHARACTER_SPRITES = {
    "zeph/reading": {
        "prompt": f"{ZEPH_BASE}, sitting cross-legged reading a large old book, warm candlelight, focused expression, cozy atmosphere, solid green background, full body visible, centered",
        "size": (256, 256),
    },
    "zeph/writing": {
        "prompt": f"{ZEPH_BASE}, sitting at wooden desk writing with feather quill pen, inkwell, parchment, concentrated expression, warm lighting, solid green background, full body visible, centered",
        "size": (256, 256),
    },
    "zeph/sleeping": {
        "prompt": f"{ZEPH_BASE}, sleeping peacefully curled up on his side, eyes closed, relaxed expression, soft lighting, solid green background, full body visible, centered",
        "size": (256, 256),
    },
    "zeph/eating": {
        "prompt": f"{ZEPH_BASE}, sitting at small wooden table eating from a bowl, simple meal, relaxed, warm firelight, solid green background, full body visible, centered",
        "size": (256, 256),
    },
    "zeph/magic": {
        "prompt": f"{ZEPH_BASE}, standing with hands raised casting magic spell, glowing magical energy around hands, mystical aura, dramatic lighting, solid green background, full body visible, centered",
        "size": (256, 256),
    },
    "zeph/walking": {
        "prompt": f"{ZEPH_BASE}, walking naturally mid-stride to the right, relaxed posture, windswept hair, solid green background, full body visible, centered",
        "size": (256, 256),
    },
    "zeph/sitting": {
        "prompt": f"{ZEPH_BASE}, sitting on the ground relaxed, one knee up, looking at the sky, peaceful contemplative expression, solid green background, full body visible, centered",
        "size": (256, 256),
    },
    "briar/sleeping": {
        "prompt": f"{BRIAR_BASE}, sleeping curled up in a ball, peaceful, warm lighting, solid green background, centered",
        "size": (256, 160),
    },
    "briar/sitting": {
        "prompt": f"{BRIAR_BASE}, sitting upright looking alert, tongue slightly out, tail relaxed, solid green background, centered",
        "size": (256, 160),
    },
    "briar/following": {
        "prompt": f"{BRIAR_BASE}, trotting walking to the right, happy expression, tail wagging, solid green background, centered",
        "size": (256, 160),
    },
}

# ─── Background Definitions ─────────────────────────────────

BACKGROUND_SPRITES = {
    # Study variants
    "rooms/study-day": {
        "prompt": "anime illustration background, dark stone tower study interior, wooden desk covered in open books and scrolls, melting candles, tall bookshelf packed with colorful books, arched stone window showing daylight and grey sea, ink bottles and quill pen, cracked stone walls with ivy, cozy cluttered atmosphere, warm natural light from window, dark fantasy aesthetic, detailed, no characters",
        "size": (640, 480),
    },
    "rooms/study-night": {
        "prompt": "anime illustration background, dark stone tower study interior, wooden desk covered in open books and scrolls, glowing melting candles as only light source, tall bookshelf, arched stone window showing dark night sky with stars, ink bottles and quill pen, stone walls, warm candlelight atmosphere, dark moody, dark fantasy aesthetic, detailed, no characters",
        "size": (640, 480),
    },
    # Kitchen variants
    "rooms/kitchen-day": {
        "prompt": "anime illustration background, medieval stone tower kitchen interior, heavy wooden table with bread and bowls, large stone fireplace with low embers, daylight coming through small window, hanging copper pots, wooden chair, shelves with jars, stone floor, cozy rustic morning atmosphere, dark fantasy aesthetic, detailed, no characters",
        "size": (640, 480),
    },
    "rooms/kitchen-night": {
        "prompt": "anime illustration background, medieval stone tower kitchen interior, heavy wooden table with bread and bowls, large stone fireplace with bright crackling fire as main light, warm orange glow filling room, hanging copper pots, wooden chair, shelves with glass jars, stone floor, cozy evening atmosphere, dark fantasy aesthetic, detailed, no characters",
        "size": (640, 480),
    },
    # Outside variants
    "rooms/outside-sunny": {
        "prompt": "anime illustration background, cliff edge overlooking bright blue ocean, old stone tower visible on right, winding dirt path along cliff, green grass and wildflowers, single wind-bent tree, clear sky with scattered clouds, seabirds flying, bright sunny day, coastal fantasy aesthetic, detailed, beautiful, no characters",
        "size": (640, 480),
    },
    "rooms/outside-overcast": {
        "prompt": "anime illustration background, cliff edge overlooking grey restless ocean, old stone tower visible on right, winding dirt path, wild grass, dramatic overcast sky with heavy clouds, moody atmosphere, waves crashing below, coastal dark fantasy aesthetic, detailed, no characters",
        "size": (640, 480),
    },
    "rooms/outside-rain": {
        "prompt": "anime illustration background, cliff edge in heavy rain, ocean grey and rough, old stone tower visible on right with warm glowing window, dirt path turned muddy, rain streaks visible, dark storm clouds, dramatic moody atmosphere, wet surfaces reflecting light, coastal dark fantasy aesthetic, detailed, no characters",
        "size": (640, 480),
    },
    "rooms/outside-night": {
        "prompt": "anime illustration background, cliff edge overlooking dark moonlit ocean at night, old stone tower on right with warm glowing window, winding path, tree silhouette, bright moon, twinkling stars, silver waves below, dark blue and purple palette, serene nighttime, coastal fantasy aesthetic, detailed, no characters",
        "size": (640, 480),
    },
    "rooms/outside-sunset": {
        "prompt": "anime illustration background, cliff edge overlooking ocean at golden sunset, old stone tower silhouette on right, dramatic orange and purple sky, sun low on horizon, golden light on grass and path, long shadows, seabirds, breathtaking view, coastal fantasy aesthetic, detailed, no characters",
        "size": (640, 480),
    },
    "rooms/outside-fog": {
        "prompt": "anime illustration background, cliff edge shrouded in thick morning fog, ocean barely visible below, old stone tower fading into mist on right, path disappearing into fog, mysterious ethereal atmosphere, muted colors, soft diffused light, coastal dark fantasy aesthetic, detailed, no characters",
        "size": (640, 480),
    },
}


def check_api():
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
        return False


def generate_image(name, prompt, size, is_background=False, gen_width=1024, gen_height=None):
    """Generate a single image via the anime-gen API."""
    if gen_height is None:
        gen_height = 768 if is_background else 1024

    print(f"  Generating {name}...", end=" ", flush=True)

    payload = {
        "prompt": prompt,
        "negative_prompt": NEGATIVE,
        "checkpoint": CHECKPOINT,
        "lora": LORA,
        "lora_strength": LORA_STRENGTH,
        "quality": "quality",
        "width": gen_width,
        "height": gen_height,
        "seed": -1,
        "skip_enhance": True,
        "regions_enabled": False,
        "img2img_enabled": False,
        "image_mode": "anime",
        "style": "anime",
    }

    try:
        r = requests.post(f"{API_BASE}/generate", json=payload, timeout=180)
        if r.status_code != 200:
            print(f"FAILED (HTTP {r.status_code})")
            return None

        data = r.json()
        image_b64 = data.get("image")
        if not image_b64:
            print("FAILED (no image)")
            return None

        image_bytes = base64.b64decode(image_b64)
        img = Image.open(BytesIO(image_bytes)).convert("RGBA")

        target_w, target_h = size

        if not is_background:
            img = remove_bg(img)

        # High-quality downscale
        img = img.resize((target_w, target_h), Image.LANCZOS)

        output_path = os.path.join(OUTPUT_DIR, f"{name}.png")
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        img.save(output_path, "PNG")

        print(f"OK -> {output_path} ({target_w}x{target_h})")
        return img

    except requests.Timeout:
        print("FAILED (timeout)")
        return None
    except Exception as e:
        print(f"FAILED ({e})")
        return None


def generate_idle_frames(name, base_img, prompt, size, num_frames=3):
    """Generate subtle idle animation variations using img2img at low denoise."""
    if base_img is None:
        return 0

    print(f"  Generating {num_frames} idle frames for {name}...", end=" ", flush=True)

    # Save base image as frame 0 (already saved as the main sprite)
    # Generate frames 1, 2, 3 as subtle variations

    # Upload the base image for img2img
    base_full = base_img.resize((1024, 1024), Image.LANCZOS).convert("RGB")
    buf = BytesIO()
    base_full.save(buf, format="PNG")
    buf.seek(0)

    try:
        upload_resp = requests.post(
            f"{API_BASE}/upload-img2img",
            files={"file": ("base.png", buf, "image/png")},
            timeout=30,
        )
        if upload_resp.status_code != 200:
            print(f"FAILED (upload: {upload_resp.status_code})")
            return 0

        img2img_filename = upload_resp.json().get("filename")
        if not img2img_filename:
            print("FAILED (no filename)")
            return 0
    except Exception as e:
        print(f"FAILED (upload: {e})")
        return 0

    generated = 0
    for i in range(1, num_frames + 1):
        payload = {
            "prompt": prompt,
            "negative_prompt": NEGATIVE,
            "checkpoint": CHECKPOINT,
            "lora": LORA,
            "lora_strength": LORA_STRENGTH,
            "quality": "balanced",
            "width": 1024,
            "height": 1024,
            "seed": -1,
            "skip_enhance": True,
            "regions_enabled": False,
            "img2img_enabled": True,
            "img2img_image": img2img_filename,
            "img2img_denoise": 0.25,
            "image_mode": "anime",
            "style": "anime",
        }

        try:
            r = requests.post(f"{API_BASE}/generate", json=payload, timeout=180)
            if r.status_code != 200:
                continue

            data = r.json()
            image_b64 = data.get("image")
            if not image_b64:
                continue

            image_bytes = base64.b64decode(image_b64)
            img = Image.open(BytesIO(image_bytes)).convert("RGBA")

            img = remove_bg(img)
            target_w, target_h = size
            img = img.resize((target_w, target_h), Image.LANCZOS)

            frame_path = os.path.join(OUTPUT_DIR, f"{name}-f{i}.png")
            img.save(frame_path, "PNG")
            generated += 1

        except Exception:
            continue

        time.sleep(1)

    print(f"{generated} frames OK")
    return generated


def remove_bg(img):
    """Remove green background using HSV color space."""
    data = img.getdata()
    new_data = []
    for pixel in data:
        r, g, b, a = pixel
        h, s, v = colorsys.rgb_to_hsv(r / 255.0, g / 255.0, b / 255.0)
        hue_deg = h * 360
        is_green = (70 < hue_deg < 170) and (s > 0.15) and (v > 0.15)
        is_edge = (s < 0.08) and (v > 0.88)
        if is_green or is_edge:
            new_data.append((0, 0, 0, 0))
        else:
            new_data.append(pixel)

    result = Image.new("RGBA", img.size)
    result.putdata(new_data)
    return result


def main():
    parser = argparse.ArgumentParser(description="Generate tamagotchi sprites and backgrounds")
    parser.add_argument("--only", help="Generate subset: 'zeph', 'briar', 'backgrounds', or specific like 'zeph/reading'")
    parser.add_argument("--no-frames", action="store_true", help="Skip idle animation frame generation")
    parser.add_argument("--list", action="store_true", help="List all sprite names")
    args = parser.parse_args()

    all_chars = dict(CHARACTER_SPRITES)
    all_bgs = dict(BACKGROUND_SPRITES)

    if args.list:
        print("Characters:")
        for name in all_chars:
            print(f"  {name}")
        print("Backgrounds:")
        for name in all_bgs:
            print(f"  {name}")
        return

    if not check_api():
        sys.exit(1)

    # Determine what to generate
    chars_to_gen = {}
    bgs_to_gen = {}

    if args.only:
        if args.only == "backgrounds":
            bgs_to_gen = all_bgs
        elif args.only == "zeph":
            chars_to_gen = {k: v for k, v in all_chars.items() if k.startswith("zeph/")}
        elif args.only == "briar":
            chars_to_gen = {k: v for k, v in all_chars.items() if k.startswith("briar/")}
        elif args.only in all_chars:
            chars_to_gen = {args.only: all_chars[args.only]}
        elif args.only.replace("-", "/") in all_chars:
            key = args.only.replace("-", "/")
            chars_to_gen = {key: all_chars[key]}
        elif args.only in all_bgs:
            bgs_to_gen = {args.only: all_bgs[args.only]}
        else:
            print(f"Unknown: {args.only}. Use --list")
            sys.exit(1)
    else:
        chars_to_gen = all_chars
        bgs_to_gen = all_bgs

    total = len(chars_to_gen) + len(bgs_to_gen)
    print(f"\nGenerating {len(chars_to_gen)} characters + {len(bgs_to_gen)} backgrounds")
    print(f"Checkpoint: {CHECKPOINT}")
    print(f"Output: {OUTPUT_DIR}\n")

    success = 0
    failed = 0

    # Generate characters
    for name, config in chars_to_gen.items():
        base_img = generate_image(name, config["prompt"], config["size"], is_background=False)
        if base_img:
            success += 1
            if not args.no_frames:
                generate_idle_frames(name, base_img, config["prompt"], config["size"])
        else:
            failed += 1
        time.sleep(1)

    # Generate backgrounds
    for name, config in bgs_to_gen.items():
        result = generate_image(name, config["prompt"], config["size"], is_background=True, gen_width=1024, gen_height=768)
        if result:
            success += 1
        else:
            failed += 1
        time.sleep(1)

    print(f"\n{'='*40}")
    print(f"Done: {success} succeeded, {failed} failed")


if __name__ == "__main__":
    main()
