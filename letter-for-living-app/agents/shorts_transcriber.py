from pathlib import Path
import os
import re
import requests


def transcribe_with_timestamps(audio_path: Path) -> list[dict]:
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set")
    if not audio_path.exists():
        raise RuntimeError("Audio file not found for transcription.")
    with audio_path.open("rb") as f:
        resp = requests.post(
            "https://api.openai.com/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {api_key}"},
            data=[
                ("model", "whisper-1"),
                ("response_format", "verbose_json"),
                ("timestamp_granularities[]", "segment"),
            ],
            files={"file": f},
            timeout=120,
        )
    if resp.status_code >= 400:
        raise RuntimeError(f"OpenAI STT error {resp.status_code}: {resp.text}")
    data = resp.json()
    return data.get("segments", [])


def merge_segments_by_sentence(segments: list[dict]) -> list[dict]:
    merged: list[dict] = []
    buffer = None
    for seg in segments:
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        if buffer is None:
            buffer = {
                "start": seg.get("start", 0),
                "end": seg.get("end", 0),
                "text": text,
            }
        else:
            buffer["end"] = seg.get("end", buffer["end"])
            buffer["text"] = f"{buffer['text']} {text}".strip()
        if text.endswith((".", "!", "?", "다.", "요.", "죠.", "네.")):
            merged.append(buffer)
            buffer = None
    if buffer:
        merged.append(buffer)
    return merged


def _split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+|(?<=[가-힣][.!?])\s+|(?<=[가-힣])\s*(?=[가-힣].{0,2}\?)", text)
    return [part.strip() for part in parts if part and part.strip()]


def split_long_segments(segments: list[dict]) -> list[dict]:
    if len(segments) != 1:
        return segments
    seg = segments[0]
    text = (seg.get("text") or "").strip()
    sentences = _split_sentences(text)
    if len(sentences) <= 1:
        return segments
    start = float(seg.get("start", 0))
    end = float(seg.get("end", 0))
    if end <= start:
        end = start + (0.8 * len(sentences))
    total = end - start
    per = total / len(sentences)
    split = []
    for idx, sentence in enumerate(sentences):
        s = start + (idx * per)
        e = start + ((idx + 1) * per)
        split.append({"start": s, "end": e, "text": sentence})
    return split
