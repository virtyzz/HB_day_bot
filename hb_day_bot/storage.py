from __future__ import annotations

from pathlib import Path

import aiosqlite

from .models import BirthdayRecord


class Storage:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path

    async def init(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.database_path) as db:
            await db.executescript(
                """
                PRAGMA journal_mode=WAL;

                CREATE TABLE IF NOT EXISTS users (
                    telegram_id INTEGER PRIMARY KEY,
                    timezone TEXT NOT NULL,
                    is_whitelisted INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS birthdays (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    owner_telegram_id INTEGER NOT NULL,
                    full_name TEXT NOT NULL,
                    day INTEGER NOT NULL,
                    month INTEGER NOT NULL,
                    year INTEGER,
                    remind_time TEXT NOT NULL,
                    remind_timezone TEXT NOT NULL,
                    note TEXT,
                    last_reminded_year INTEGER,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(owner_telegram_id) REFERENCES users(telegram_id)
                );

                CREATE INDEX IF NOT EXISTS idx_birthdays_owner
                    ON birthdays(owner_telegram_id);
                CREATE INDEX IF NOT EXISTS idx_birthdays_reminder
                    ON birthdays(month, day, remind_timezone, remind_time);
                """
            )
            await db.commit()

    async def ensure_user(self, telegram_id: int, timezone: str, *, whitelist: bool = False) -> None:
        async with aiosqlite.connect(self.database_path) as db:
            await db.execute(
                """
                INSERT INTO users (telegram_id, timezone, is_whitelisted)
                VALUES (?, ?, ?)
                ON CONFLICT(telegram_id) DO UPDATE SET
                    is_whitelisted = MAX(users.is_whitelisted, excluded.is_whitelisted)
                """,
                (telegram_id, timezone, int(whitelist)),
            )
            await db.commit()

    async def set_user_timezone(self, telegram_id: int, timezone: str) -> None:
        async with aiosqlite.connect(self.database_path) as db:
            await db.execute(
                """
                INSERT INTO users (telegram_id, timezone, is_whitelisted)
                VALUES (?, ?, 0)
                ON CONFLICT(telegram_id) DO UPDATE SET timezone = excluded.timezone
                """,
                (telegram_id, timezone),
            )
            await db.commit()

    async def get_user_timezone(self, telegram_id: int, fallback: str) -> str:
        async with aiosqlite.connect(self.database_path) as db:
            async with db.execute(
                "SELECT timezone FROM users WHERE telegram_id = ?",
                (telegram_id,),
            ) as cursor:
                row = await cursor.fetchone()
                return str(row[0]) if row else fallback

    async def is_whitelisted(self, telegram_id: int) -> bool:
        async with aiosqlite.connect(self.database_path) as db:
            async with db.execute(
                "SELECT is_whitelisted FROM users WHERE telegram_id = ?",
                (telegram_id,),
            ) as cursor:
                row = await cursor.fetchone()
                return bool(row and row[0])

    async def set_whitelist(self, telegram_id: int, timezone: str, allowed: bool) -> None:
        async with aiosqlite.connect(self.database_path) as db:
            await db.execute(
                """
                INSERT INTO users (telegram_id, timezone, is_whitelisted)
                VALUES (?, ?, ?)
                ON CONFLICT(telegram_id) DO UPDATE SET
                    is_whitelisted = excluded.is_whitelisted
                """,
                (telegram_id, timezone, int(allowed)),
            )
            await db.commit()

    async def list_whitelisted(self) -> list[int]:
        async with aiosqlite.connect(self.database_path) as db:
            async with db.execute(
                "SELECT telegram_id FROM users WHERE is_whitelisted = 1 ORDER BY telegram_id"
            ) as cursor:
                rows = await cursor.fetchall()
                return [int(row[0]) for row in rows]

    async def add_birthday(
        self,
        *,
        owner_telegram_id: int,
        full_name: str,
        day: int,
        month: int,
        year: int | None,
        remind_time: str,
        remind_timezone: str,
        note: str | None,
    ) -> int:
        async with aiosqlite.connect(self.database_path) as db:
            cursor = await db.execute(
                """
                INSERT INTO birthdays (
                    owner_telegram_id, full_name, day, month, year,
                    remind_time, remind_timezone, note
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    owner_telegram_id,
                    full_name,
                    day,
                    month,
                    year,
                    remind_time,
                    remind_timezone,
                    note,
                ),
            )
            await db.commit()
            return int(cursor.lastrowid)

    async def list_birthdays(self, owner_telegram_id: int) -> list[BirthdayRecord]:
        async with aiosqlite.connect(self.database_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT * FROM birthdays
                WHERE owner_telegram_id = ?
                ORDER BY month, day, full_name
                """,
                (owner_telegram_id,),
            ) as cursor:
                return [_record_from_row(row) for row in await cursor.fetchall()]

    async def get_birthday(
        self,
        owner_telegram_id: int,
        record_id: int,
    ) -> BirthdayRecord | None:
        async with aiosqlite.connect(self.database_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT * FROM birthdays
                WHERE owner_telegram_id = ? AND id = ?
                """,
                (owner_telegram_id, record_id),
            ) as cursor:
                row = await cursor.fetchone()
                return _record_from_row(row) if row else None

    async def update_birthday(
        self,
        *,
        owner_telegram_id: int,
        record_id: int,
        full_name: str,
        day: int,
        month: int,
        year: int | None,
        remind_time: str,
        remind_timezone: str,
        note: str | None,
        reset_last_reminded: bool = False,
    ) -> bool:
        async with aiosqlite.connect(self.database_path) as db:
            cursor = await db.execute(
                """
                UPDATE birthdays
                SET full_name = ?,
                    day = ?,
                    month = ?,
                    year = ?,
                    remind_time = ?,
                    remind_timezone = ?,
                    note = ?,
                    last_reminded_year = CASE WHEN ? THEN NULL ELSE last_reminded_year END
                WHERE owner_telegram_id = ? AND id = ?
                """,
                (
                    full_name,
                    day,
                    month,
                    year,
                    remind_time,
                    remind_timezone,
                    note,
                    int(reset_last_reminded),
                    owner_telegram_id,
                    record_id,
                ),
            )
            await db.commit()
            return cursor.rowcount > 0

    async def delete_birthday(self, owner_telegram_id: int, record_id: int) -> bool:
        async with aiosqlite.connect(self.database_path) as db:
            cursor = await db.execute(
                "DELETE FROM birthdays WHERE owner_telegram_id = ? AND id = ?",
                (owner_telegram_id, record_id),
            )
            await db.commit()
            return cursor.rowcount > 0

    async def clear_birthdays(self, owner_telegram_id: int) -> int:
        async with aiosqlite.connect(self.database_path) as db:
            cursor = await db.execute(
                "DELETE FROM birthdays WHERE owner_telegram_id = ?",
                (owner_telegram_id,),
            )
            await db.commit()
            return cursor.rowcount

    async def iter_birthdays(self) -> list[BirthdayRecord]:
        async with aiosqlite.connect(self.database_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM birthdays") as cursor:
                return [_record_from_row(row) for row in await cursor.fetchall()]

    async def mark_reminded(self, record_id: int, year: int) -> None:
        async with aiosqlite.connect(self.database_path) as db:
            await db.execute(
                "UPDATE birthdays SET last_reminded_year = ? WHERE id = ?",
                (year, record_id),
            )
            await db.commit()


def _record_from_row(row: aiosqlite.Row) -> BirthdayRecord:
    return BirthdayRecord(
        id=int(row["id"]),
        owner_telegram_id=int(row["owner_telegram_id"]),
        full_name=str(row["full_name"]),
        day=int(row["day"]),
        month=int(row["month"]),
        year=int(row["year"]) if row["year"] is not None else None,
        remind_time=str(row["remind_time"]),
        remind_timezone=str(row["remind_timezone"]),
        note=str(row["note"]) if row["note"] else None,
        last_reminded_year=int(row["last_reminded_year"])
        if row["last_reminded_year"] is not None
        else None,
    )
