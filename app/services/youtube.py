from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx
from yt_dlp import YoutubeDL

from app.utils.youtube import ChannelInfo

logger = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class FeedEntry:
    video_id: str
    channel_id: str
    title: str
    published: datetime | None


def _pick_text(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, list):
        for item in value:
            picked = _pick_text(item)
            if picked:
                return picked
    if isinstance(value, dict):
        for key in ("channel_id", "channelId", "uploader_id", "uploaderId", "id", "channel_url", "channelUrl"):
            picked = _pick_text(value.get(key))
            if picked:
                return picked
        for nested in value.values():
            picked = _pick_text(nested)
            if picked:
                return picked
    return None


def _extract_channel_id(info: dict[str, Any]) -> str | None:
    for key in ("channel_id", "channelId", "uploader_id", "uploaderId"):
        value = info.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    nested = info.get("channel")
    if isinstance(nested, dict):
        value = _pick_text(nested)
        if value:
            return value
    return None


def _extract_channel_name(info: dict[str, Any]) -> str:
    for key in ("channel", "channel_title", "channel_title", "uploader", "uploader_id", "title"):
        value = info.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "YouTube channel"


async def extract_channel_info(url: str) -> ChannelInfo:
    def _extract() -> ChannelInfo:
        options = {
            "quiet": True,
            "no_warnings": True,
            "extract_flat": False,
            "skip_download": True,
        }
        with YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=False)
        if not isinstance(info, dict):
            raise ValueError("yt-dlp returned an unexpected response")
        channel_id = _extract_channel_id(info)
        if not channel_id:
            raise ValueError("Could not determine the canonical YouTube channel_id")
        channel_name = _extract_channel_name(info)
        source_url = str(info.get("channel_url") or info.get("webpage_url") or url)
        return ChannelInfo(channel_id=channel_id, channel_name=channel_name, source_url=source_url)

    return await asyncio.to_thread(_extract)


def parse_published(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = value.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def parse_atom_feed(xml_text: str) -> list[FeedEntry]:
    import xml.etree.ElementTree as ET

    if not xml_text.strip():
        return []
    root = ET.fromstring(xml_text)
    ns = {
        "atom": "http://www.w3.org/2005/Atom",
        "yt": "http://www.youtube.com/xml/schemas/2015",
    }
    entries: list[FeedEntry] = []
    for entry in root.findall("atom:entry", ns):
        video_id = entry.findtext("yt:videoId", default="", namespaces=ns).strip()
        channel_id = entry.findtext("yt:channelId", default="", namespaces=ns).strip()
        title = entry.findtext("atom:title", default="", namespaces=ns).strip()
        published = parse_published(entry.findtext("atom:published", default="", namespaces=ns))
        if not video_id or not channel_id or not title:
            continue
        entries.append(FeedEntry(video_id=video_id, channel_id=channel_id, title=title, published=published))
    return entries


async def fetch_channel_feed(client: httpx.AsyncClient, channel_id: str) -> list[FeedEntry]:
    url = f"https://www.youtube.com/xml/feeds/videos.xml?channel_id={channel_id}"
    response = await client.get(url, timeout=httpx.Timeout(20.0))
    response.raise_for_status()
    return parse_atom_feed(response.text)

