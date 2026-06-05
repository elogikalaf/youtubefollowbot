from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse
import re


@dataclass(slots=True, frozen=True)
class ChannelInfo:
    channel_id: str
    channel_name: str
    source_url: str


CHANNEL_ID_RE = re.compile(r"UC[a-zA-Z0-9_-]{22}")
HANDLE_RE = re.compile(r"@[A-Za-z0-9._-]{3,30}")


def normalize_youtube_reference(value: str) -> str | None:
    value = value.strip()

    if not value:
        return None

    if looks_like_youtube_url(value):
        return value

    if CHANNEL_ID_RE.fullmatch(value):
        return f"https://www.youtube.com/channel/{value}"

    if HANDLE_RE.fullmatch(value):
        return f"https://www.youtube.com/{value}"

    return None


def looks_like_youtube_reference(value: str) -> bool:
    return normalize_youtube_reference(value) is not None


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
