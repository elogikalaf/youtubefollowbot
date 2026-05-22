from __future__ import annotations

import logging
from datetime import datetime, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from telegram.ext import Application

from app.models.youtube_channel import YouTubeChannel
from app.services.notifications import format_notification, send_with_retry
from app.services.subscriptions import (
    get_subscribed_user_ids,
    has_sent_video,
    record_sent_video,
)
from app.services.youtube import fetch_channel_feed

logger = logging.getLogger(__name__)


async def reconcile_all_channels(
    session_factory: async_sessionmaker[AsyncSession],
    client: httpx.AsyncClient,
    telegram_app: Application,
) -> None:
    async with session_factory() as session:
        rows = await session.execute(select(YouTubeChannel.channel_id, YouTubeChannel.channel_name))
        channels = list(rows.all())

    for row in channels:
        channel_id, channel_name = row
        try:
            entries = await fetch_channel_feed(client, channel_id)
        except Exception:
            logger.exception("Failed to fetch feed for channel_id=%s", channel_id)
            continue

        async with session_factory() as session:
            for entry in sorted(entries, key=lambda item: item.published or datetime.min.replace(tzinfo=timezone.utc)):
                if await has_sent_video(session, video_id=entry.video_id):
                    continue
                created = await record_sent_video(
                    session,
                    video_id=entry.video_id,
                    channel_id=entry.channel_id,
                    title=entry.title,
                    published_at=entry.published,
                    source="reconcile",
                )
                if not created:
                    continue
                await session.commit()
                user_ids = await get_subscribed_user_ids(session, channel_id=channel_id)
                text = format_notification(channel_name, entry.title, entry.video_id)
                for user_id in user_ids:
                    await send_with_retry(telegram_app, user_id=user_id, text=text)
