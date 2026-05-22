from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse


@dataclass(slots=True, frozen=True)
class ChannelInfo:
    channel_id: str
    channel_name: str
    source_url: str


def looks_like_youtube_url(value: str) -> bool:
    try:
        parsed = urlparse(value.strip())
    except ValueError:
        return False
    if parsed.scheme not in {"http", "https"}:
        return False
    host = (parsed.netloc or "").lower()
    return any(
        host == domain or host.endswith(f".{domain}")
        for domain in {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}
    )

