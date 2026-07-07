from __future__ import annotations

import logging
import os

RED = "\033[31m"
RESET = "\033[0m"


class RussianColorFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        message = super().format(record)
        if record.levelno >= logging.WARNING and not os.getenv("NO_COLOR"):
            return f"{RED}{message}{RESET}"
        return message


def setup_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(
        RussianColorFormatter(
            fmt="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(logging.INFO)

    logging.getLogger("aiogram.event").setLevel(logging.WARNING)
