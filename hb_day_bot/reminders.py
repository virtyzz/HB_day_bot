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
            logger.exception("Ошибка в цикле проверки напоминаний")
        await asyncio.sleep(interval_seconds)


async def send_due_reminders(bot: Bot, storage: Storage) -> None:
    for record in await storage.iter_birthdays():
        now = datetime.now(ZoneInfo(record.remind_timezone))
        if not is_due(record, now):
            continue

        await bot.send_message(record.owner_telegram_id, format_reminder(record, now.year))
        await storage.mark_reminded(record.id, now.year)
        logger.info(
            "Отправлено напоминание: запись #%s, пользователь %s, дата %02d.%02d, часовой пояс %s",
            record.id,
            record.owner_telegram_id,
            record.day,
            record.month,
            record.remind_timezone,
        )
