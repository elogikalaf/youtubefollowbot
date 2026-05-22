from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

import httpx
from sqlalchemy.ext.asyncio import async_sessionmaker
from telegram.ext import Application

from app.services.reconciliation import reconcile_all_channels
from app.services.websub import resubscribe_all_active_channels

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class BackgroundScheduler:
    session_factory: async_sessionmaker
    http_client: httpx.AsyncClient
    telegram_app: Application
    callback_url: str
    webhook_secret: str
    tasks: list[asyncio.Task[None]] = field(default_factory=list)

    def start(self) -> None:
        self.tasks = [
            asyncio.create_task(self._loop("websub-renew", 12 * 60 * 60, self._renew_loop())),
            asyncio.create_task(self._loop("reconcile", 6 * 60 * 60, self._reconcile_loop())),
        ]

    async def stop(self) -> None:
        for task in self.tasks:
            task.cancel()
        for task in self.tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
        self.tasks.clear()

    async def _loop(self, name: str, interval_seconds: int, coro_factory: Callable[[], Awaitable[None]]) -> None:
        while True:
            try:
                await coro_factory()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("%s loop failed", name)
            await asyncio.sleep(interval_seconds)

    async def _renew_loop(self) -> None:
        await resubscribe_all_active_channels(
            self.session_factory,
            self.http_client,
            callback_url=self.callback_url,
            secret=self.webhook_secret,
        )

    async def _reconcile_loop(self) -> None:
        await reconcile_all_channels(
            self.session_factory,
            self.http_client,
            self.telegram_app,
        )
