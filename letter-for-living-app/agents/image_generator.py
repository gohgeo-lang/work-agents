import base64
import os
from pathlib import Path

import requests


def generate_images(
    prompts: list[str],
    output_dir: Path,
    size: str = "1024x1024",
    model: str = "gpt-image-1",
    start_index: int = 1,
) -> list[Path]:
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set")
    if not prompts:
        raise RuntimeError("Image prompts are empty")

    output_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    timeout_seconds = 180
    for idx, prompt in enumerate(prompts, start=start_index):
        payload = {
            "model": model,
            "prompt": prompt,
            "size": size,
        }
        for attempt in range(2):
            try:
                resp = requests.post(
                    "https://api.openai.com/v1/images/generations",
                    headers={"Authorization": f"Bearer {api_key}"},
                    json=payload,
                    timeout=timeout_seconds,
                )
                break
            except requests.exceptions.Timeout as exc:
                if attempt == 1:
                    raise RuntimeError("OpenAI image request timed out") from exc
        if resp.status_code >= 400:
            raise RuntimeError(f"OpenAI image error {resp.status_code}: {resp.text}")
        data = resp.json()
        image_info = data.get("data", [{}])[0]
        b64_data = image_info.get("b64_json")
        if b64_data:
            image_bytes = base64.b64decode(b64_data)
        else:
            image_url = image_info.get("url")
            if not image_url:
                raise RuntimeError("OpenAI image response is missing image data")
            image_resp = requests.get(image_url, timeout=timeout_seconds)
            if image_resp.status_code >= 400:
                raise RuntimeError(
                    f"OpenAI image download error {image_resp.status_code}: {image_resp.text}"
                )
            image_bytes = image_resp.content
        path = output_dir / f"image_{idx:02d}.png"
        path.write_bytes(image_bytes)
        paths.append(path)
    return paths
