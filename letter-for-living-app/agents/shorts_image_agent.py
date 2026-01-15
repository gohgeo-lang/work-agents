from typing import Iterable
from pathlib import Path
import base64
import os
import requests


def summarize_image_prompts(image_prompts: Iterable[str]) -> str:
    prompts = [prompt.strip() for prompt in image_prompts if prompt and prompt.strip()]
    if not prompts:
        return ""
    return "\n\n".join(prompts)


def build_image_outputs(image_prompts: Iterable[str]) -> list[dict[str, str]]:
    prompts = [prompt.strip() for prompt in image_prompts if prompt and prompt.strip()]
    outputs = []
    for idx, prompt in enumerate(prompts, start=1):
        outputs.append(
            {
                "label": f"컷 이미지 {idx}",
                "prompt": prompt,
            }
        )
    return outputs


def generate_images(
    image_prompts: Iterable[str],
    output_dir: Path,
    model: str = "gpt-image-1-mini",
    size: str = "1024x1024",
) -> list[Path]:
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set")
    output_dir.mkdir(parents=True, exist_ok=True)
    prompts = [prompt.strip() for prompt in image_prompts if prompt and prompt.strip()]
    paths: list[Path] = []
    for idx, prompt in enumerate(prompts, start=1):
        payload = {
            "model": model,
            "prompt": prompt,
            "size": size,
            "quality": "low",
        }
        resp = requests.post(
            "https://api.openai.com/v1/images/generations",
            headers={"Authorization": f"Bearer {api_key}"},
            json=payload,
            timeout=120,
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"OpenAI image error {resp.status_code}: {resp.text}")
        data = resp.json()
        image_item = data["data"][0]
        if "b64_json" in image_item:
            image_bytes = base64.b64decode(image_item["b64_json"])
        elif "url" in image_item:
            image_resp = requests.get(image_item["url"], timeout=60)
            image_resp.raise_for_status()
            image_bytes = image_resp.content
        else:
            raise RuntimeError("Image response does not include b64_json or url.")
        out_path = output_dir / f"shorts_cut_{idx}.png"
        out_path.write_bytes(image_bytes)
        paths.append(out_path)
    return paths
