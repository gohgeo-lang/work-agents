from pathlib import Path
import re
import subprocess


def build_shorts_job(
    script: str,
    cuts: list[dict],
    image_paths: list[str] | None = None,
) -> dict:
    return {
        "status": "pending",
        "script": script,
        "cuts": cuts,
        "image_paths": image_paths or [],
    }


def _format_srt_time(seconds: float) -> str:
    millis = int(seconds * 1000)
    hours = millis // 3_600_000
    minutes = (millis % 3_600_000) // 60_000
    secs = (millis % 60_000) // 1000
    ms = millis % 1000
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"


def _split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+|(?<=[가-힣][.!?])\s+|(?<=[가-힣])\s*(?=[가-힣].{0,2}\?)", text)
    return [part.strip() for part in parts if part and part.strip()]


def build_srt_from_segments(segments: list[dict], output_path: Path) -> Path:
    blocks = []
    for idx, seg in enumerate(segments, start=1):
        start = float(seg.get("start", 0))
        end = float(seg.get("end", 0))
        text = (seg.get("text") or "").replace("\n", " ").strip()
        if not text:
            continue
        text = re.sub(r"\s+", " ", text)
        blocks.append(
            f"{idx}\n{_format_srt_time(start)} --> {_format_srt_time(end)}\n{text}\n"
        )
    output_path.write_text("\n".join(blocks), encoding="utf-8")
    return output_path


def build_srt(lines: list[str], total_seconds: float, output_path: Path) -> Path:
    cleaned = [line.strip() for line in lines if line.strip()]
    if len(cleaned) <= 1 and cleaned:
        cleaned = _split_sentences(cleaned[0])
    if not cleaned:
        output_path.write_text("", encoding="utf-8")
        return output_path
    segment = total_seconds / len(cleaned)
    blocks = []
    for idx, line in enumerate(cleaned, start=1):
        start = (idx - 1) * segment
        end = idx * segment
        blocks.append(
            f"{idx}\n{_format_srt_time(start)} --> {_format_srt_time(end)}\n{line}\n"
        )
    output_path.write_text("\n".join(blocks), encoding="utf-8")
    return output_path


def build_short_video(
    image_paths: list[Path],
    audio_path: Path,
    script: str,
    title: str,
    output_path: Path,
    total_seconds: float = 60.0,
    srt_path: Path | None = None,
    font_path: Path | None = None,
) -> Path:
    if not image_paths:
        raise RuntimeError("이미지 경로가 비어 있습니다.")
    if not audio_path.exists() or audio_path.stat().st_size == 0:
        raise RuntimeError("나레이션 오디오가 비어 있습니다.")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if srt_path is None:
        srt_path = output_path.with_suffix(".srt")
        lines = [line for line in script.splitlines() if line.strip()]
        build_srt(lines, total_seconds, srt_path)
    xfade_d = 0.35
    per_cut = (total_seconds + (len(image_paths) - 1) * xfade_d) / len(image_paths)
    inputs = []
    for path in image_paths:
        inputs.extend(["-loop", "1", "-t", f"{per_cut:.2f}", "-i", str(path)])
    frames = max(1, int(per_cut * 30))
    segments = []
    for idx in range(len(image_paths)):
        segments.append(
            f"color=c=black:s=1080x1920[bg{idx}];"
            f"[{idx}:v]scale=1080:1080:force_original_aspect_ratio=decrease,"
            f"pad=1080:1080:(ow-iw)/2:(oh-ih)/2:color=black@0,"
            f"zoompan=z='min(zoom+0.0008,1.03)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:s=1080x1080[fgz{idx}];"
            f"[bg{idx}][fgz{idx}]overlay=(W-w)/2:(H-h)/2,format=yuv420p[v{idx}]"
        )
    filter_complex = ";".join(segments)
    if len(image_paths) == 1:
        filter_complex += ";[v0]eq=saturation=1.05:contrast=1.02,noise=alls=6:allf=t"
        filter_complex += f",drawtext=text='{_escape_drawtext(title)}':x=(w-text_w)/2:y=90:fontsize=64:fontcolor=white:shadowx=2:shadowy=2"
        filter_complex += f",subtitles={srt_path}:force_style='FontName=Helvetica,Fontsize=48,Outline=2,Shadow=1,Alignment=2,MarginV=180'[v]"
    else:
        xfade_parts = []
        prev = "v0"
        for idx in range(1, len(image_paths)):
            offset = (idx * per_cut) - (idx * xfade_d)
            out = f"vxf{idx}"
            xfade_parts.append(
                f"[{prev}][v{idx}]xfade=transition=fade:duration={xfade_d}:offset={offset:.2f}[{out}]"
            )
            prev = out
        filter_complex += ";" + ";".join(xfade_parts)
        filter_complex += f";[{prev}]eq=saturation=1.05:contrast=1.02,noise=alls=6:allf=t"
        filter_complex += f",drawtext=text='{_escape_drawtext(title)}':x=(w-text_w)/2:y=90:fontsize=64:fontcolor=white:shadowx=2:shadowy=2"
        filter_complex += f",subtitles={srt_path}:force_style='FontName=Helvetica,Fontsize=48,Outline=2,Shadow=1,Alignment=2,MarginV=180'[v]"
    cmd = [
        "ffmpeg",
        "-y",
        *inputs,
        "-i",
        str(audio_path),
        "-filter_complex",
        filter_complex,
        "-map",
        "[v]",
        "-map",
        f"{len(image_paths)}:a",
        "-c:v",
        "libx264",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-shortest",
        "-r",
        "30",
        str(output_path),
    ]
    subprocess.run(cmd, check=True)
    return output_path


def _escape_drawtext(text: str) -> str:
    return (
        text.replace("\\", "\\\\")
        .replace(":", "\\:")
        .replace("'", "\\'")
        .replace("%", "\\%")
    )
