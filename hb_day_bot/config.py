from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv


@dataclass(frozen=True)
class Config:
    bot_token: str
    admin_telegram_id: int
    database_path: Path
    default_user_timezone: str
    check_interval_seconds: int


def load_config() -> Config:
    load_dotenv()

    bot_token = _required("BOT_TOKEN")
    admin_telegram_id = int(_required("ADMIN_TELEGRAM_ID"))
    database_path = Path(os.getenv("DATABASE_PATH", "data/birthdays.sqlite3"))
    default_user_timezone = os.getenv("DEFAULT_USER_TIMEZONE", "UTC")
    check_interval_seconds = int(os.getenv("CHECK_INTERVAL_SECONDS", "60"))

    try:
        ZoneInfo(default_user_timezone)
    except ZoneInfoNotFoundError as exc:
        raise RuntimeError(
            f"DEFAULT_USER_TIMEZONE has unknown timezone: {default_user_timezone}"
        ) from exc

    return Config(
        bot_token=bot_token,
        admin_telegram_id=admin_telegram_id,
        database_path=database_path,
        default_user_timezone=default_user_timezone,
        check_interval_seconds=check_interval_seconds,
    )


def _required(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"{name} is required")
    return value
