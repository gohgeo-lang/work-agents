def build_upload_payload(
    title: str,
    description: str,
    video_path: str,
    platform: str,
) -> dict:
    return {
        "title": title,
        "description": description,
        "video_path": video_path,
        "platform": platform,
    }
