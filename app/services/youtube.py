from __future__ import annotations

import html
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

import httpx

from app.utils.youtube import ChannelInfo

logger = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class FeedEntry:
    video_id: str
    channel_id: str
    title: str
    published: datetime | None
    author: str | None = None


CHANNEL_ID_RE = re.compile(r"UC[a-zA-Z0-9_-]{22}")

META_ITEMPROP_RE = re.compile(
    r'<meta\s+itemprop=["\']channelId["\']\s+content=["\']([^"\']+)["\']',
    re.I,
)

CANONICAL_CHANNEL_RE = re.compile(
    r'<link\s+rel=["\']canonical["\']\s+href=["\']https://www\.youtube\.com/channel/([^"\']+)["\']',
    re.I,
)

JSON_CHANNEL_ID_RE = re.compile(
    r'"channelId"\s*:\s*"(UC[a-zA-Z0-9_-]{22})"'
)

JSON_EXTERNAL_ID_RE = re.compile(
    r'"externalId"\s*:\s*"(UC[a-zA-Z0-9_-]{22})"'
)

OG_TITLE_RE = re.compile(
    r'<meta\s+property=["\']og:title["\']\s+content=["\']([^"\']+)["\']',
    re.I,
)

TITLE_RE = re.compile(
    r"<title>(.*?)</title>",
    re.I | re.S,
)


def _normalize_channel_id(value: str | None) -> str | None:
    if not value:
        return None

    value = value.strip()

    if CHANNEL_ID_RE.fullmatch(value):
        return value

    match = CHANNEL_ID_RE.search(value)

    if match:
        return match.group(0)

    return None


def _extract_direct_channel_id(url: str) -> str | None:
    parsed = urlparse(url)

    path_parts = [
        part
        for part in parsed.path.split("/")
        if part
    ]

    for part in path_parts:
        channel_id = _normalize_channel_id(part)

        if channel_id:
            return channel_id

    query_channel = parse_qs(parsed.query).get(
        "channel_id",
        [""],
    )[0]

    return _normalize_channel_id(query_channel)


def _extract_channel_id_from_html(page: str) -> str | None:
    for pattern in (
        META_ITEMPROP_RE,
        CANONICAL_CHANNEL_RE,
        JSON_EXTERNAL_ID_RE,
        JSON_CHANNEL_ID_RE,
    ):
        match = pattern.search(page)

        if match:
            channel_id = _normalize_channel_id(match.group(1))

            if channel_id:
                return channel_id

    return None


def _extract_channel_name_from_html(page: str) -> str | None:
    for pattern in (OG_TITLE_RE, TITLE_RE):
        match = pattern.search(page)

        if not match:
            continue

        value = html.unescape(match.group(1)).strip()

        value = re.sub(
            r"\s+-\s+YouTube\s*$",
            "",
            value,
        ).strip()

        if not value:
            continue

        lowered = value.lower()

        if lowered in {
            "youtube",
            "youtube feed",
        }:
            continue

        return value

    return None


async def _fetch_text(
    client: httpx.AsyncClient,
    url: str,
) -> str:
    response = await client.get(
        url,
        follow_redirects=True,
        timeout=httpx.Timeout(20.0),
        headers={
            "Accept-Language": "en-US,en;q=0.9",
            "User-Agent": (
                "Mozilla/5.0 "
                "compatible; youtubefollowbot/1.0"
            ),
        },
    )

    response.raise_for_status()

    return response.text


async def _fetch_oembed_name(
    client: httpx.AsyncClient,
    url: str,
) -> str | None:
    try:
        response = await client.get(
            "https://www.youtube.com/oembed",
            params={
                "url": url,
                "format": "json",
            },
            timeout=httpx.Timeout(10.0),
        )

        response.raise_for_status()

        data = response.json()

    except Exception:
        return None

    author = data.get("author_name")

    if not isinstance(author, str):
        return None

    author = author.strip()

    if not author:
        return None

    lowered = author.lower()

    if lowered in {
        "youtube",
        "youtube feed",
    }:
        return None

    return author


async def _resolve_channel_name(
    client: httpx.AsyncClient,
    channel_id: str,
    fallback_url: str,
    page: str | None,
) -> str:
    """
    Resolve canonical YouTube channel name.

    Priority:
    1. Atom feed author metadata
    2. YouTube oEmbed
    3. HTML metadata fallback
    4. Generic fallback
    """

    # Most reliable source:
    # YouTube Atom feed metadata.
    try:
        entries = await fetch_channel_feed(
            client,
            channel_id,
        )

        for entry in entries:
            if not entry.author:
                continue

            cleaned = entry.author.strip()

            if cleaned.lower() not in {
                "youtube",
                "youtube feed",
            }:
                return cleaned

    except Exception:
        logger.warning(
            "Could not resolve channel name from feed for %s",
            channel_id,
        )

    # Secondary fallback:
    # oEmbed metadata.
    try:
        name = await _fetch_oembed_name(
            client,
            fallback_url,
        )

        if name:
            cleaned = name.strip()

            if cleaned.lower() not in {
                "youtube",
                "youtube feed",
            }:
                return cleaned

    except Exception:
        logger.warning(
            "Could not resolve channel name from oEmbed for %s",
            fallback_url,
        )

    # Last-resort fallback:
    # HTML scraping.
    if page:
        name = _extract_channel_name_from_html(page)

        if name:
            cleaned = name.strip()

            if cleaned.lower() not in {
                "youtube",
                "youtube feed",
            }:
                return cleaned

    # Final fallback.
    return f"Channel {channel_id}"


async def extract_channel_info(
    url: str,
    client: httpx.AsyncClient | None = None,
) -> ChannelInfo:
    close_client = client is None

    http_client = client or httpx.AsyncClient()

    try:
        channel_id = _extract_direct_channel_id(url)

        page: str | None = None

        if channel_id is None:
            page = await _fetch_text(
                http_client,
                url,
            )

            channel_id = _extract_channel_id_from_html(
                page,
            )

        if channel_id is None:
            raise ValueError(
                "Could not determine the canonical "
                "YouTube channel_id from page metadata"
            )

        channel_url = (
            f"https://www.youtube.com/channel/{channel_id}"
        )

        channel_name = await _resolve_channel_name(
            http_client,
            channel_id,
            url,
            page,
        )

        return ChannelInfo(
            channel_id=channel_id,
            channel_name=channel_name,
            source_url=channel_url,
        )

    finally:
        if close_client:
            await http_client.aclose()


def parse_published(
    value: str | None,
) -> datetime | None:
    if not value:
        return None

    normalized = value.strip().replace(
        "Z",
        "+00:00",
    )

    try:
        dt = datetime.fromisoformat(normalized)

    except ValueError:
        return None

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    return dt.astimezone(timezone.utc)


def parse_atom_feed(
    xml_text: str,
) -> list[FeedEntry]:
    import xml.etree.ElementTree as ET

    if not xml_text.strip():
        return []

    root = ET.fromstring(xml_text)

    ns = {
        "atom": "http://www.w3.org/2005/Atom",
        "yt": "http://www.youtube.com/xml/schemas/2015",
    }

    entries: list[FeedEntry] = []

    for entry in root.findall(
        "atom:entry",
        ns,
    ):
        video_id = entry.findtext(
            "yt:videoId",
            default="",
            namespaces=ns,
        ).strip()

        channel_id = entry.findtext(
            "yt:channelId",
            default="",
            namespaces=ns,
        ).strip()

        title = entry.findtext(
            "atom:title",
            default="",
            namespaces=ns,
        ).strip()

        published = parse_published(
            entry.findtext(
                "atom:published",
                default="",
                namespaces=ns,
            )
        )

        author = entry.findtext(
            "atom:author/atom:name",
            default="",
            namespaces=ns,
        ).strip()

        if not video_id:
            continue

        if not channel_id:
            continue

        if not title:
            continue

        entries.append(
            FeedEntry(
                video_id=video_id,
                channel_id=channel_id,
                title=title,
                published=published,
                author=author or None,
            )
        )

    return entries


async def fetch_channel_feed(
    client: httpx.AsyncClient,
    channel_id: str,
) -> list[FeedEntry]:
    url = (
        "https://www.youtube.com/xml/feeds/videos.xml"
        f"?channel_id={channel_id}"
    )

    response = await client.get(
        url,
        timeout=httpx.Timeout(20.0),
    )

    response.raise_for_status()

    return parse_atom_feed(response.text)
