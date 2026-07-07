from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BirthdayRecord:
    id: int
    owner_telegram_id: int
    full_name: str
    day: int
    month: int
    year: int | None
    remind_time: str
    remind_timezone: str
    note: str | None
    last_reminded_year: int | None
