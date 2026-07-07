from __future__ import annotations

import asyncio
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from .config import load_config
from .handlers import register_handlers
from .logging_setup import setup_logging
from .reminders import run_reminder_loop
from .storage import Storage


async def run() -> None:
    setup_logging()
    config = load_config()
    storage = Storage(config.database_path)
    await storage.init()

    bot = Bot(token=config.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dispatcher = Dispatcher(storage=MemoryStorage())
    dispatcher.include_router(register_handlers(storage, config))

    reminder_task = asyncio.create_task(
        run_reminder_loop(
            bot,
            storage,
            interval_seconds=config.check_interval_seconds,
        )
    )
    try:
        await dispatcher.start_polling(bot)
    finally:
        reminder_task.cancel()
        await bot.session.close()


def main() -> None:
    asyncio.run(run())
