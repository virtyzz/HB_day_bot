from __future__ import annotations

from datetime import datetime

from .models import BirthdayRecord


def is_due(record: BirthdayRecord, now: datetime) -> bool:
    if record.last_reminded_year == now.year:
        return False
    if record.month != now.month or record.day != now.day:
        return False

    remind_hour, remind_minute = [int(part) for part in record.remind_time.split(":", 1)]
    return (now.hour, now.minute) >= (remind_hour, remind_minute)


def format_reminder(record: BirthdayRecord, current_year: int) -> str:
    age = ""
    if record.year:
        age = f" ({current_year - record.year})"

    note = f"\nПримечание: {record.note}" if record.note else ""
    return (
        f"Сегодня День Рождения: {record.full_name}{age}\n"
        f"Дата: {record.day:02d}.{record.month:02d}"
        f"{'.' + str(record.year) if record.year else ''}\n"
        f"Напоминание: {record.remind_time} {record.remind_timezone}"
        f"{note}"
    )
