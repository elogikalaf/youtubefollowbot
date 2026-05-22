from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path

from dotenv import load_dotenv


def _parse_allowed_ids(raw: str | None) -> frozenset[int]:
    if not raw:
        return frozenset()
    ids: set[int] = set()
    for chunk in raw.split(","):
        value = chunk.strip()
        if not value:
            continue
        ids.add(int(value))
    return frozenset(ids)


@dataclass(slots=True, frozen=True)
class Settings:
    bot_token: str
    base_url: str
    database_path: Path
    webhook_secret: str
    allowed_user_ids: frozenset[int]
    log_level: str

    @property
    def webhook_callback_url(self) -> str:
        return f"{self.base_url.rstrip('/')}/youtube/webhook"


def load_settings() -> Settings:
    load_dotenv()

    bot_token = os.environ.get("BOT_TOKEN", "").strip()
    base_url = os.environ.get("BASE_URL", "").strip()
    database_path = Path(os.environ.get("DATABASE_PATH", "data/bot.sqlite3").strip())
    webhook_secret = os.environ.get("WEBHOOK_SECRET", "").strip()
    allowed_user_ids = _parse_allowed_ids(os.environ.get("ALLOWED_USER_IDS"))
    log_level = os.environ.get("LOG_LEVEL", "INFO").strip().upper()

    if not bot_token:
        raise RuntimeError("BOT_TOKEN is required")
    if not base_url:
        raise RuntimeError("BASE_URL is required")
    if not webhook_secret:
        raise RuntimeError("WEBHOOK_SECRET is required")

    database_path.parent.mkdir(parents=True, exist_ok=True)

    return Settings(
        bot_token=bot_token,
        base_url=base_url,
        database_path=database_path,
        webhook_secret=webhook_secret,
        allowed_user_ids=allowed_user_ids,
        log_level=log_level,
    )
