from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Index, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class UserSubscription(Base):
    __tablename__ = "user_subscriptions"
    __table_args__ = (
        UniqueConstraint("telegram_user_id", "channel_id", name="uq_user_channel_subscription"),
        Index("ix_user_subscriptions_user_id", "telegram_user_id"),
        Index("ix_user_subscriptions_channel_id", "channel_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    telegram_user_id: Mapped[int] = mapped_column(ForeignKey("users.telegram_user_id", ondelete="CASCADE"), nullable=False)
    channel_id: Mapped[str] = mapped_column(ForeignKey("youtube_channels.channel_id", ondelete="CASCADE"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

