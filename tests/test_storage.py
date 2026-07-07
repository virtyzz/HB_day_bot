import tempfile
import unittest
from pathlib import Path

from hb_day_bot.storage import Storage


class StorageTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.storage = Storage(Path(self.temp_dir.name) / "birthdays.sqlite3")
        await self.storage.init()
        await self.storage.ensure_user(10, "Asia/Novosibirsk", whitelist=True)
        self.record_id = await self.storage.add_birthday(
            owner_telegram_id=10,
            full_name="Test User",
            day=8,
            month=7,
            year=None,
            remind_time="09:00",
            remind_timezone="Asia/Novosibirsk",
            note=None,
        )
        await self.storage.mark_reminded(self.record_id, 2026)

    async def asyncTearDown(self) -> None:
        self.temp_dir.cleanup()

    async def test_update_can_keep_last_reminded_year(self) -> None:
        updated = await self.storage.update_birthday(
            owner_telegram_id=10,
            record_id=self.record_id,
            full_name="Test User",
            day=8,
            month=7,
            year=None,
            remind_time="09:00",
            remind_timezone="Asia/Novosibirsk",
            note="note",
        )

        record = await self.storage.get_birthday(10, self.record_id)

        self.assertTrue(updated)
        self.assertIsNotNone(record)
        self.assertEqual(record.last_reminded_year, 2026)

    async def test_update_can_reset_last_reminded_year(self) -> None:
        updated = await self.storage.update_birthday(
            owner_telegram_id=10,
            record_id=self.record_id,
            full_name="Test User",
            day=8,
            month=7,
            year=None,
            remind_time="09:01",
            remind_timezone="Asia/Novosibirsk",
            note=None,
            reset_last_reminded=True,
        )

        record = await self.storage.get_birthday(10, self.record_id)

        self.assertTrue(updated)
        self.assertIsNotNone(record)
        self.assertIsNone(record.last_reminded_year)

    async def test_list_birthdays_keeps_insert_order(self) -> None:
        second_id = await self.storage.add_birthday(
            owner_telegram_id=10,
            full_name="Earlier Calendar Date",
            day=1,
            month=1,
            year=None,
            remind_time="09:00",
            remind_timezone="Asia/Novosibirsk",
            note=None,
        )
        third_id = await self.storage.add_birthday(
            owner_telegram_id=10,
            full_name="Middle Calendar Date",
            day=2,
            month=2,
            year=None,
            remind_time="09:00",
            remind_timezone="Asia/Novosibirsk",
            note=None,
        )

        records = await self.storage.list_birthdays(10)

        self.assertEqual([record.id for record in records], [self.record_id, second_id, third_id])


if __name__ == "__main__":
    unittest.main()
