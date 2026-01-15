from pathlib import Path
import os
import requests


def build_voiceover(
    script: str,
    output_dir: Path,
    voice: str = "alloy",
    model: str = "gpt-4o-mini-tts",
) -> Path:
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set")
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "shorts_voiceover.mp3"
    payload = {
        "model": model,
        "voice": voice,
        "input": script,
        "format": "mp3",
    }
    resp = requests.post(
        "https://api.openai.com/v1/audio/speech",
        headers={"Authorization": f"Bearer {api_key}"},
        json=payload,
        timeout=60,
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"OpenAI TTS error {resp.status_code}: {resp.text}")
    output_path.write_bytes(resp.content)
    return output_path
