import base64
import datetime as dt
from pathlib import Path

import requests


def build_image_prompt(result: dict) -> str:
    theme = result.get("theme_display", "") or result.get("theme_en", "")
    verse = result.get("verse_reference", "")
    anchor = result.get("anchor_text", "")
    intent = result.get("one_line_intent", "")
    return (
        "Create a horizontal poster-style image inspired by a Bible verse. "
        "Minimal, contemplative, textured paper feel. "
        "No readable text, no typography, no logos. "
        f"Theme: {theme}. Verse: {verse}. Anchor idea: {anchor}. Intent: {intent}."
    )


def generate_image(prompt: str, image_dir: Path, api_key: str, size: str = "1536x1024") -> Path:
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set")
    payload = {
        "model": "gpt-image-1",
        "prompt": prompt,
        "size": size,
    }
    resp = requests.post(
        "https://api.openai.com/v1/images/generations",
        headers={"Authorization": f"Bearer {api_key}"},
        json=payload,
        timeout=120,
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"OpenAI image error {resp.status_code}: {resp.text}")
    data = resp.json().get("data", [])
    if not data or "b64_json" not in data[0]:
        raise RuntimeError("Image response missing data")
    image_dir.mkdir(parents=True, exist_ok=True)
    timestamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    img_path = image_dir / f"poster_{timestamp}.png"
    img_bytes = base64.b64decode(data[0]["b64_json"])
    img_path.write_bytes(img_bytes)
    return img_path
