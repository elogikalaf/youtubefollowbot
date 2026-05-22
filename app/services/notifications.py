from __future__ import annotations

import asyncio
import html
import logging

from telegram.constants import ParseMode
from telegram.error import NetworkError, RetryAfter, TelegramError
from telegram.ext import Application

logger = logging.getLogger(__name__)


def format_notification(channel_name: str, title: str, video_id: str) -> str:
    channel = html.escape(channel_name)
    video_title = html.escape(title)
    url = f"https://www.youtube.com/watch?v={video_id}"
    return (
        f"📺 <b>New upload from {channel}</b>\n\n"
        f"{video_title}\n\n"
        f"{url}"
    )


async def send_with_retry(
    application: Application,
    *,
    user_id: int,
    text: str,
) -> None:
    delay = 1.0
    for attempt in range(3):
        try:
            await application.bot.send_message(
                chat_id=user_id,
                text=text,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=False,
            )
            return
        except RetryAfter as exc:
            delay = float(exc.retry_after)
        except (NetworkError, TelegramError):
            logger.exception("Telegram send failed for user_id=%s attempt=%s", user_id, attempt + 1)
        await asyncio.sleep(delay)
        delay *= 2
