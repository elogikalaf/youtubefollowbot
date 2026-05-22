from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.user_subscription import UserSubscription
from app.models.youtube_channel import YouTubeChannel

logger = logging.getLogger(__name__)

WEBSUB_HUB_URL = "https://pubsubhubbub.appspot.com/subscribe"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def topic_url(channel_id: str) -> str:
    return f"https://www.youtube.com/xml/feeds/videos.xml?channel_id={channel_id}"


def _subscription_payload(mode: str, callback_url: str, channel_id: str, secret: str) -> dict[str, str]:
    return {
        "hub.mode": mode,
        "hub.callback": callback_url,
        "hub.topic": topic_url(channel_id),
        "hub.verify": "async",
        "hub.verify_token": secret,
        "hub.secret": secret,
        "hub.lease_seconds": "43200",
    }


async def request_websub_subscription(
    client: httpx.AsyncClient,
    *,
    callback_url: str,
    channel_id: str,
    secret: str,
) -> None:
    payload = _subscription_payload("subscribe", callback_url, channel_id, secret)
    response = await client.post(WEBSUB_HUB_URL, data=payload, timeout=httpx.Timeout(20.0))
    response.raise_for_status()


async def request_websub_unsubscribe(
    client: httpx.AsyncClient,
    *,
    callback_url: str,
    channel_id: str,
    secret: str,
) -> None:
    payload = _subscription_payload("unsubscribe", callback_url, channel_id, secret)
    response = await client.post(WEBSUB_HUB_URL, data=payload, timeout=httpx.Timeout(20.0))
    response.raise_for_status()


async def ensure_global_websub_subscription(
    session_factory: async_sessionmaker[AsyncSession],
    client: httpx.AsyncClient,
    *,
    callback_url: str,
    secret: str,
    channel_id: str,
    channel_name: str,
    source_url: str,
) -> None:
    async with session_factory() as session:
        channel = await session.scalar(select(YouTubeChannel).where(YouTubeChannel.channel_id == channel_id))
        if channel is None:
            channel = YouTubeChannel(channel_id=channel_id, channel_name=channel_name, source_url=source_url)
            session.add(channel)
        else:
            channel.channel_name = channel_name
            channel.source_url = source_url
        channel.last_websub_checked_at = _utcnow()
        channel.updated_at = _utcnow()
        await session.commit()

    for attempt in range(3):
        try:
            await request_websub_subscription(
                client,
                callback_url=callback_url,
                channel_id=channel_id,
                secret=secret,
            )
            async with session_factory() as session:
                channel = await session.scalar(select(YouTubeChannel).where(YouTubeChannel.channel_id == channel_id))
                if channel is not None:
                    channel.last_websub_subscribed_at = _utcnow()
                    channel.last_websub_checked_at = _utcnow()
                    channel.updated_at = _utcnow()
                    await session.commit()
            return
        except Exception:
            logger.exception("WebSub subscribe failed for channel_id=%s attempt=%s", channel_id, attempt + 1)
            await asyncio.sleep(2**attempt)


async def maybe_unsubscribe_global_websub(
    session_factory: async_sessionmaker[AsyncSession],
    client: httpx.AsyncClient,
    *,
    callback_url: str,
    secret: str,
    channel_id: str,
) -> None:
    async with session_factory() as session:
        count = await session.scalar(
            select(UserSubscription.id).where(UserSubscription.channel_id == channel_id).limit(1)
        )
    if count is not None:
        return
    try:
        await request_websub_unsubscribe(
            client,
            callback_url=callback_url,
            channel_id=channel_id,
            secret=secret,
        )
    except Exception:
        logger.exception("WebSub unsubscribe failed for channel_id=%s", channel_id)


async def resubscribe_all_active_channels(
    session_factory: async_sessionmaker[AsyncSession],
    client: httpx.AsyncClient,
    *,
    callback_url: str,
    secret: str,
) -> None:
    async with session_factory() as session:
        result = await session.execute(
            select(YouTubeChannel.channel_id, YouTubeChannel.channel_name, YouTubeChannel.source_url)
        )
        channels = list(result.all())
    for channel_id, channel_name, source_url in channels:
        await ensure_global_websub_subscription(
            session_factory,
            client,
            callback_url=callback_url,
            secret=secret,
            channel_id=channel_id,
            channel_name=channel_name,
            source_url=source_url,
        )
