from __future__ import annotations

from dataclasses import dataclass
from datetime import date, time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


@dataclass(frozen=True)
class BirthdayDate:
    day: int
    month: int
    year: int | None


def parse_birthday_date(raw: str) -> BirthdayDate:
    parts = raw.strip().split(".")
    if len(parts) not in (2, 3):
        raise ValueError("Use DD.MM or DD.MM.YYYY")

    try:
        day = int(parts[0])
        month = int(parts[1])
        year = int(parts[2]) if len(parts) == 3 and parts[2] else None
    except ValueError as exc:
        raise ValueError("Birthday date must contain numbers") from exc

    validation_year = year or 2000
    try:
        date(validation_year, month, day)
    except ValueError as exc:
        raise ValueError("Birthday date does not exist") from exc

    return BirthdayDate(day=day, month=month, year=year)


def parse_reminder_time(raw: str) -> time:
    parts = raw.strip().split(":")
    if len(parts) != 2:
        raise ValueError("Use HH:MM")

    try:
        hour = int(parts[0])
        minute = int(parts[1])
        return time(hour=hour, minute=minute)
    except ValueError as exc:
        raise ValueError("Reminder time must be valid HH:MM") from exc


def normalize_timezone(raw: str) -> str:
    timezone_name = raw.strip()
    try:
        ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError("Unknown timezone") from exc
    return timezone_name
