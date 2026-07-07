import unittest
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

import hb_day_bot.handlers as handlers
from hb_day_bot.models import BirthdayRecord
from hb_day_bot.parsing import parse_birthday_date, parse_reminder_time
from hb_day_bot.reminder_logic import is_due


class ParsingTestCase(unittest.TestCase):
    def test_parse_birthday_without_year(self) -> None:
        parsed = parse_birthday_date("21.07")

        self.assertEqual(parsed.day, 21)
        self.assertEqual(parsed.month, 7)
        self.assertIsNone(parsed.year)

    def test_parse_birthday_with_year(self) -> None:
        parsed = parse_birthday_date("21.07.1994")

        self.assertEqual(parsed.day, 21)
        self.assertEqual(parsed.month, 7)
        self.assertEqual(parsed.year, 1994)

    def test_parse_reminder_time(self) -> None:
        self.assertEqual(parse_reminder_time("09:30").strftime("%H:%M"), "09:30")

    def test_parse_birthday_rejects_invalid_dates(self) -> None:
        for raw in ("31.02", "x.y", "2026-07-21"):
            with self.subTest(raw=raw):
                with self.assertRaises(ValueError):
                    parse_birthday_date(raw)


class ReminderLogicTestCase(unittest.TestCase):
    def test_reminder_is_due_once_per_record_timezone(self) -> None:
        record = BirthdayRecord(
            id=1,
            owner_telegram_id=10,
            full_name="Test User",
            day=8,
            month=7,
            year=None,
            remind_time="09:00",
            remind_timezone="Asia/Novosibirsk",
            note=None,
            last_reminded_year=None,
        )
        now = datetime(2026, 7, 8, 9, 1, tzinfo=ZoneInfo("Asia/Novosibirsk"))

        self.assertTrue(is_due(record, now))

    def test_reminder_is_not_due_after_sent_this_year(self) -> None:
        record = BirthdayRecord(
            id=1,
            owner_telegram_id=10,
            full_name="Test User",
            day=8,
            month=7,
            year=None,
            remind_time="09:00",
            remind_timezone="Asia/Novosibirsk",
            note=None,
            last_reminded_year=2026,
        )
        now = datetime(2026, 7, 8, 10, 0, tzinfo=ZoneInfo("Asia/Novosibirsk"))

        self.assertFalse(is_due(record, now))


class BirthdayFormattingTestCase(unittest.TestCase):
    def test_age_uses_current_date_not_only_year(self) -> None:
        record = BirthdayRecord(
            id=1,
            owner_telegram_id=10,
            full_name="Test User",
            day=12,
            month=12,
            year=2000,
            remind_time="09:00",
            remind_timezone="Asia/Novosibirsk",
            note=None,
            last_reminded_year=None,
        )

        class FixedDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                return cls(2026, 7, 8)

        with patch.object(handlers, "datetime", FixedDateTime):
            self.assertEqual(handlers._format_record_age(record), "25")


if __name__ == "__main__":
    unittest.main()
