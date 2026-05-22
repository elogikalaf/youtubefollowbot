from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass

import httpx
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from telegram.ext import Application

from app.api.webhook import router as webhook_router
from app.bot.handlers import register_handlers
from app.db.base import Base
from app.db.session import create_engine, create_session_factory
from app.models import *  # noqa: F401,F403
from app.tasks.scheduler import BackgroundScheduler
from app.utils.logging import configure_logging
from app.utils.settings import Settings, load_settings

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class Runtime:
    settings: Settings
    engine: AsyncEngine
    session_factory: async_sessionmaker[AsyncSession]
    http_client: httpx.AsyncClient
    telegram_app: Application
    scheduler: BackgroundScheduler


async def _init_db(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = load_settings()
    configure_logging(settings.log_level)
    engine = create_engine(f"sqlite+aiosqlite:///{settings.database_path}")
    session_factory = create_session_factory(engine)
    http_client = httpx.AsyncClient(headers={"User-Agent": "yoututubefollowbot/1.0"})
    telegram_app = Application.builder().token(settings.bot_token).build()
    telegram_app.bot_data["allowed_user_ids"] = settings.allowed_user_ids
    telegram_app.bot_data["session_factory"] = session_factory
    telegram_app.bot_data["http_client"] = http_client
    telegram_app.bot_data["settings"] = settings
    register_handlers(telegram_app)

    await _init_db(engine)
    await telegram_app.initialize()
    await telegram_app.start()
    await telegram_app.updater.start_polling(drop_pending_updates=True)

    scheduler = BackgroundScheduler(
        session_factory=session_factory,
        http_client=http_client,
        telegram_app=telegram_app,
        callback_url=settings.webhook_callback_url,
        webhook_secret=settings.webhook_secret,
    )
    runtime = Runtime(
        settings=settings,
        engine=engine,
        session_factory=session_factory,
        http_client=http_client,
        telegram_app=telegram_app,
        scheduler=scheduler,
    )
    app.state.settings = settings
    app.state.runtime = runtime
    scheduler.start()
    try:
        yield
    finally:
        await scheduler.stop()
        await telegram_app.updater.stop()
        await telegram_app.stop()
        await telegram_app.shutdown()
        await http_client.aclose()
        await engine.dispose()


app = FastAPI(lifespan=lifespan)
app.include_router(webhook_router)
