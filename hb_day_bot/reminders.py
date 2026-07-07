from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import Bot

from .reminder_logic import format_reminder, is_due
from .storage import Storage

logger = logging.getLogger(__name__)


async def run_reminder_loop(
    bot: Bot,
    storage: Storage,
    *,
    interval_seconds: int,
) -> None:
    while True:
        try:
            await send_due_reminders(bot, storage)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Reminder loop failed")
        await asyncio.sleep(interval_seconds)


async def send_due_reminders(bot: Bot, storage: Storage) -> None:
    for record in await storage.iter_birthdays():
        now = datetime.now(ZoneInfo(record.remind_timezone))
        if not is_due(record, now):
            continue

        await bot.send_message(record.owner_telegram_id, format_reminder(record, now.year))
        await storage.mark_reminded(record.id, now.year)
