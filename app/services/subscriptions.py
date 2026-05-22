from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timezone
import logging

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.sent_video import SentVideo
from app.models.user import User
from app.models.user_subscription import UserSubscription
from app.models.youtube_channel import YouTubeChannel

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def upsert_user(
    session: AsyncSession,
    *,
    telegram_user_id: int,
    username: str | None,
    first_name: str | None,
) -> User:
    user = await session.scalar(select(User).where(User.telegram_user_id == telegram_user_id))
    if user is None:
        user = User(
            telegram_user_id=telegram_user_id,
            username=username,
            first_name=first_name,
        )
        session.add(user)
    else:
        user.username = username
        user.first_name = first_name
        user.last_seen_at = _utcnow()
    await session.flush()
    return user


async def save_channel(
    session: AsyncSession,
    *,
    channel_id: str,
    channel_name: str,
    source_url: str,
) -> YouTubeChannel:
    channel = await session.scalar(select(YouTubeChannel).where(YouTubeChannel.channel_id == channel_id))
    if channel is None:
        channel = YouTubeChannel(
            channel_id=channel_id,
            channel_name=channel_name,
            source_url=source_url,
        )
        session.add(channel)
    else:
        channel.channel_name = channel_name
        channel.source_url = source_url
        channel.updated_at = _utcnow()
    await session.flush()
    return channel


async def create_subscription(
    session: AsyncSession,
    *,
    telegram_user_id: int,
    channel_id: str,
) -> bool:
    existing = await session.scalar(
        select(UserSubscription).where(
            UserSubscription.telegram_user_id == telegram_user_id,
            UserSubscription.channel_id == channel_id,
        )
    )
    if existing is not None:
        return False
    session.add(UserSubscription(telegram_user_id=telegram_user_id, channel_id=channel_id))
    await session.flush()
    return True


async def delete_subscription(
    session: AsyncSession,
    *,
    telegram_user_id: int,
    channel_id: str,
) -> bool:
    result = await session.execute(
        delete(UserSubscription).where(
            UserSubscription.telegram_user_id == telegram_user_id,
            UserSubscription.channel_id == channel_id,
        )
    )
    return result.rowcount > 0


async def user_subscription_count(session: AsyncSession, *, telegram_user_id: int) -> int:
    result = await session.scalar(
        select(func.count()).select_from(UserSubscription).where(UserSubscription.telegram_user_id == telegram_user_id)
    )
    return int(result or 0)


async def channel_subscription_count(session: AsyncSession, *, channel_id: str) -> int:
    result = await session.scalar(
        select(func.count()).select_from(UserSubscription).where(UserSubscription.channel_id == channel_id)
    )
    return int(result or 0)


async def list_user_subscriptions(
    session: AsyncSession,
    *,
    telegram_user_id: int,
    offset: int,
    limit: int,
) -> list[tuple[str, str]]:
    result = await session.execute(
        select(YouTubeChannel.channel_id, YouTubeChannel.channel_name)
        .join(UserSubscription, UserSubscription.channel_id == YouTubeChannel.channel_id)
        .where(UserSubscription.telegram_user_id == telegram_user_id)
        .order_by(YouTubeChannel.channel_name.asc())
        .offset(offset)
        .limit(limit)
    )
    return [(row[0], row[1]) for row in result.all()]


async def count_user_subscriptions(session: AsyncSession, *, telegram_user_id: int) -> int:
    result = await session.scalar(
        select(func.count()).select_from(UserSubscription).where(UserSubscription.telegram_user_id == telegram_user_id)
    )
    return int(result or 0)


async def get_channel(session: AsyncSession, *, channel_id: str) -> YouTubeChannel | None:
    return await session.scalar(select(YouTubeChannel).where(YouTubeChannel.channel_id == channel_id))


async def get_channel_name(session: AsyncSession, *, channel_id: str) -> str | None:
    channel = await get_channel(session, channel_id=channel_id)
    return channel.channel_name if channel else None


async def get_subscribed_user_ids(session: AsyncSession, *, channel_id: str) -> list[int]:
    result = await session.scalars(
        select(User.telegram_user_id)
        .join(UserSubscription, UserSubscription.telegram_user_id == User.telegram_user_id)
        .where(UserSubscription.channel_id == channel_id)
        .order_by(User.telegram_user_id.asc())
    )
    return list(result)


async def record_sent_video(
    session: AsyncSession,
    *,
    video_id: str,
    channel_id: str,
    title: str,
    published_at: datetime | None,
    source: str,
) -> bool:
    session.add(
        SentVideo(
            video_id=video_id,
            channel_id=channel_id,
            title=title,
            published_at=published_at,
            source=source,
        )
    )
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        return False
    return True


async def has_sent_video(session: AsyncSession, *, video_id: str) -> bool:
    result = await session.scalar(select(SentVideo.id).where(SentVideo.video_id == video_id))
    return result is not None

