from __future__ import annotations

from typing import Any

from aiogram import BaseMiddleware, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)

from .config import Config
from .models import BirthdayRecord
from .parsing import normalize_timezone, parse_birthday_date, parse_reminder_time
from .storage import Storage

router = Router()

ADD_BUTTON = "Добавить ДР"
LIST_BUTTON = "Посмотреть список"
CLEAR_BUTTON = "Очистить список"
ADMIN_BUTTON = "Админка"


class AddBirthday(StatesGroup):
    full_name = State()
    birthday = State()
    remind_time = State()
    remind_timezone = State()
    note = State()


class EditBirthday(StatesGroup):
    value = State()


class AdminWhitelist(StatesGroup):
    add_user = State()
    remove_user = State()


def register_handlers(storage: Storage, config: Config) -> Router:
    access_middleware = AccessMiddleware(storage, config)
    router.message.middleware(access_middleware)
    router.callback_query.middleware(access_middleware)

    @router.message(Command("start", "help"))
    async def start(message: Message, state: FSMContext) -> None:
        if not message.from_user:
            return
        await state.clear()
        await storage.ensure_user(
            message.from_user.id,
            config.default_user_timezone,
            whitelist=message.from_user.id == config.admin_telegram_id,
        )
        await message.answer(
            "Выберите действие кнопками ниже.\n\n"
            "При добавлении ДР год можно не указывать. "
            "Время и часовой пояс напоминания тоже можно пропустить: "
            "тогда будет 09:00 по вашему часовому поясу.",
            reply_markup=main_menu(message.from_user.id == config.admin_telegram_id),
        )

    @router.message(Command("cancel"))
    async def cancel(message: Message, state: FSMContext) -> None:
        await state.clear()
        await message.answer(
            "Отменено.",
            reply_markup=main_menu(_is_admin(message, config)),
        )

    @router.message(F.text == ADD_BUTTON)
    @router.message(Command("add"))
    async def add_start(message: Message, state: FSMContext) -> None:
        await state.clear()
        await state.set_state(AddBirthday.full_name)
        await message.answer("Введите ФИО:")

    @router.message(AddBirthday.full_name, F.text)
    async def add_full_name(message: Message, state: FSMContext) -> None:
        full_name = message.text.strip()
        if not full_name:
            await message.answer("ФИО не должно быть пустым.")
            return
        await state.update_data(full_name=full_name)
        await state.set_state(AddBirthday.birthday)
        await message.answer(
            "Введите дату рождения: DD.MM или DD.MM.YYYY.\n"
            "Год можно не указывать, например: 21.07"
        )

    @router.message(AddBirthday.birthday, F.text)
    async def add_birthday_date(message: Message, state: FSMContext) -> None:
        try:
            birthday = parse_birthday_date(message.text)
        except ValueError as exc:
            await message.answer(f"Не получилось разобрать дату. {exc}")
            return
        await state.update_data(day=birthday.day, month=birthday.month, year=birthday.year)
        await state.set_state(AddBirthday.remind_time)
        await message.answer("Введите время напоминания HH:MM или отправьте '-' чтобы оставить 09:00:")

    @router.message(AddBirthday.remind_time, F.text)
    async def add_remind_time(message: Message, state: FSMContext) -> None:
        raw = message.text.strip()
        if raw in ("", "-"):
            remind_time = "09:00"
        else:
            try:
                remind_time = parse_reminder_time(raw).strftime("%H:%M")
            except ValueError as exc:
                await message.answer(f"Не получилось разобрать время. {exc}")
                return
        await state.update_data(remind_time=remind_time)
        await state.set_state(AddBirthday.remind_timezone)
        await message.answer(
            "Введите часовой пояс напоминания, например Asia/Novosibirsk, "
            "или отправьте '-' чтобы взять ваш сохраненный часовой пояс:"
        )

    @router.message(AddBirthday.remind_timezone, F.text)
    async def add_remind_timezone(message: Message, state: FSMContext) -> None:
        if not message.from_user:
            return
        raw = message.text.strip()
        if raw in ("", "-"):
            timezone = await storage.get_user_timezone(
                message.from_user.id,
                config.default_user_timezone,
            )
        else:
            try:
                timezone = normalize_timezone(raw)
            except ValueError:
                await message.answer("Не знаю такой часовой пояс. Пример: Asia/Novosibirsk")
                return
        await state.update_data(remind_timezone=timezone)
        await state.set_state(AddBirthday.note)
        await message.answer("Введите примечание или отправьте '-' чтобы оставить пустым:")

    @router.message(AddBirthday.note, F.text)
    async def add_note(message: Message, state: FSMContext) -> None:
        if not message.from_user:
            return
        data = await state.get_data()
        note = message.text.strip()
        record_id = await storage.add_birthday(
            owner_telegram_id=message.from_user.id,
            full_name=data["full_name"],
            day=data["day"],
            month=data["month"],
            year=data["year"],
            remind_time=data["remind_time"],
            remind_timezone=data["remind_timezone"],
            note=None if note in ("", "-") else note,
        )
        await state.clear()
        await message.answer(
            f"Запись #{record_id} сохранена.",
            reply_markup=main_menu(_is_admin(message, config)),
        )

    @router.message(F.text == LIST_BUTTON)
    @router.message(Command("list"))
    async def list_birthdays(message: Message) -> None:
        if not message.from_user:
            return
        await send_birthdays_list(message, storage, message.from_user.id)

    @router.callback_query(F.data == "list")
    async def list_birthdays_callback(callback: CallbackQuery) -> None:
        if not callback.from_user or not callback.message:
            return
        await callback.answer()
        await send_birthdays_list(callback.message, storage, callback.from_user.id)

    @router.callback_query(F.data.startswith("record:"))
    async def show_record(callback: CallbackQuery) -> None:
        if not callback.from_user or not callback.message or not callback.data:
            return
        record_id = int(callback.data.split(":", 1)[1])
        record = await storage.get_birthday(callback.from_user.id, record_id)
        await callback.answer()
        if not record:
            await callback.message.answer("Запись не найдена.")
            return
        await callback.message.answer(
            _format_record(record),
            reply_markup=record_keyboard(record.id),
        )

    @router.callback_query(F.data.startswith("edit:"))
    async def edit_record_menu(callback: CallbackQuery) -> None:
        if not callback.data or not callback.message:
            return
        record_id = int(callback.data.split(":", 1)[1])
        await callback.answer()
        await callback.message.answer(
            "Что изменить?",
            reply_markup=edit_keyboard(record_id),
        )

    @router.callback_query(F.data.startswith("edit_field:"))
    async def edit_field(callback: CallbackQuery, state: FSMContext) -> None:
        if not callback.from_user or not callback.message or not callback.data:
            return
        _, record_id_raw, field = callback.data.split(":", 2)
        record_id = int(record_id_raw)
        record = await storage.get_birthday(callback.from_user.id, record_id)
        await callback.answer()
        if not record:
            await callback.message.answer("Запись не найдена.")
            return
        await state.set_state(EditBirthday.value)
        await state.update_data(record_id=record_id, field=field)
        await callback.message.answer(_edit_prompt(field, record))

    @router.message(EditBirthday.value, F.text)
    async def save_edit_value(message: Message, state: FSMContext) -> None:
        if not message.from_user:
            return
        data = await state.get_data()
        record_id = int(data["record_id"])
        field = str(data["field"])
        record = await storage.get_birthday(message.from_user.id, record_id)
        if not record:
            await state.clear()
            await message.answer("Запись не найдена.")
            return

        updated = _record_to_dict(record)
        raw = message.text.strip()
        try:
            _apply_edit(updated, field, raw)
        except ValueError as exc:
            await message.answer(str(exc))
            return

        await storage.update_birthday(
            owner_telegram_id=message.from_user.id,
            record_id=record.id,
            full_name=updated["full_name"],
            day=updated["day"],
            month=updated["month"],
            year=updated["year"],
            remind_time=updated["remind_time"],
            remind_timezone=updated["remind_timezone"],
            note=updated["note"],
        )
        await state.clear()
        refreshed = await storage.get_birthday(message.from_user.id, record.id)
        await message.answer(
            "Изменено.\n\n" + _format_record(refreshed or record),
            reply_markup=record_keyboard(record.id),
        )

    @router.callback_query(F.data.startswith("delete:"))
    async def delete_record(callback: CallbackQuery) -> None:
        if not callback.from_user or not callback.message or not callback.data:
            return
        record_id = int(callback.data.split(":", 1)[1])
        deleted = await storage.delete_birthday(callback.from_user.id, record_id)
        await callback.answer("Удалено" if deleted else "Запись не найдена")
        await callback.message.answer("Удалено." if deleted else "Запись не найдена.")

    @router.message(F.text == CLEAR_BUTTON)
    async def clear_list_warning(message: Message) -> None:
        await message.answer(
            "Вы точно хотите полностью очистить свой список ДР?",
            reply_markup=clear_confirm_keyboard(),
        )

    @router.callback_query(F.data == "clear_cancel")
    async def clear_cancel(callback: CallbackQuery) -> None:
        if callback.message:
            await callback.message.answer("Очистка отменена.")
        await callback.answer()

    @router.callback_query(F.data == "clear_confirm")
    async def clear_confirm(callback: CallbackQuery) -> None:
        if not callback.from_user or not callback.message:
            return
        count = await storage.clear_birthdays(callback.from_user.id)
        await callback.answer("Список очищен")
        await callback.message.answer(f"Список очищен. Удалено записей: {count}.")

    @router.message(F.text == ADMIN_BUTTON)
    async def admin_panel(message: Message) -> None:
        if not _is_admin(message, config):
            return
        await message.answer("Админка", reply_markup=admin_keyboard())

    @router.callback_query(F.data == "admin_whitelist")
    async def admin_whitelist(callback: CallbackQuery) -> None:
        if not _is_admin_callback(callback, config) or not callback.message:
            return
        users = await storage.list_whitelisted()
        text = "Белый список пуст." if not users else "Белый список:\n" + "\n".join(str(user_id) for user_id in users)
        await callback.answer()
        await callback.message.answer(text, reply_markup=admin_whitelist_keyboard())

    @router.callback_query(F.data == "admin_add_user")
    async def admin_add_user(callback: CallbackQuery, state: FSMContext) -> None:
        if not _is_admin_callback(callback, config) or not callback.message:
            return
        await state.set_state(AdminWhitelist.add_user)
        await callback.answer()
        await callback.message.answer("Введите Telegram ID пользователя для добавления:")

    @router.message(AdminWhitelist.add_user, F.text)
    async def admin_add_user_save(message: Message, state: FSMContext) -> None:
        if not _is_admin(message, config):
            return
        if not message.text.strip().isdigit():
            await message.answer("Telegram ID должен быть числом.")
            return
        user_id = int(message.text.strip())
        await storage.set_whitelist(user_id, config.default_user_timezone, True)
        await state.clear()
        await message.answer(f"Пользователь {user_id} добавлен в белый список.", reply_markup=admin_keyboard())

    @router.callback_query(F.data == "admin_remove_user")
    async def admin_remove_user(callback: CallbackQuery, state: FSMContext) -> None:
        if not _is_admin_callback(callback, config) or not callback.message:
            return
        await state.set_state(AdminWhitelist.remove_user)
        await callback.answer()
        await callback.message.answer("Введите Telegram ID пользователя для удаления:")

    @router.message(AdminWhitelist.remove_user, F.text)
    async def admin_remove_user_save(message: Message, state: FSMContext) -> None:
        if not _is_admin(message, config):
            return
        if not message.text.strip().isdigit():
            await message.answer("Telegram ID должен быть числом.")
            return
        user_id = int(message.text.strip())
        await storage.set_whitelist(user_id, config.default_user_timezone, False)
        await state.clear()
        await message.answer(f"Пользователь {user_id} удален из белого списка.", reply_markup=admin_keyboard())

    @router.message(Command("timezone"))
    async def set_timezone(message: Message) -> None:
        if not message.from_user:
            return
        raw = _command_tail(message.text)
        if not raw:
            await message.answer("Укажите часовой пояс, например: /timezone Asia/Novosibirsk")
            return
        try:
            timezone = normalize_timezone(raw)
        except ValueError:
            await message.answer("Не знаю такой часовой пояс. Пример: Asia/Novosibirsk")
            return
        await storage.set_user_timezone(message.from_user.id, timezone)
        await message.answer(f"Готово. Ваш часовой пояс по умолчанию: {timezone}")

    return router


class AccessMiddleware(BaseMiddleware):
    def __init__(self, storage: Storage, config: Config) -> None:
        self.storage = storage
        self.config = config

    async def __call__(self, handler, event: Message | CallbackQuery, data: dict[str, Any]):
        if not event.from_user:
            return None
        user_id = event.from_user.id
        if user_id == self.config.admin_telegram_id:
            await self.storage.ensure_user(
                user_id,
                self.config.default_user_timezone,
                whitelist=True,
            )
            return await handler(event, data)
        if await self.storage.is_whitelisted(user_id):
            await self.storage.ensure_user(user_id, self.config.default_user_timezone)
            return await handler(event, data)
        return None


async def send_birthdays_list(message: Message, storage: Storage, user_id: int) -> None:
    records = await storage.list_birthdays(user_id)
    if not records:
        await message.answer("У вас пока нет записей.")
        return
    await message.answer("Ваш список ДР:", reply_markup=records_keyboard(records))


def main_menu(is_admin: bool) -> ReplyKeyboardMarkup:
    keyboard = [
        [KeyboardButton(text=ADD_BUTTON), KeyboardButton(text=LIST_BUTTON)],
        [KeyboardButton(text=CLEAR_BUTTON)],
    ]
    if is_admin:
        keyboard.append([KeyboardButton(text=ADMIN_BUTTON)])
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)


def records_keyboard(records: list[BirthdayRecord]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=record.full_name, callback_data=f"record:{record.id}")]
            for record in records
        ]
    )


def record_keyboard(record_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Изменить", callback_data=f"edit:{record_id}")],
            [InlineKeyboardButton(text="Удалить", callback_data=f"delete:{record_id}")],
            [InlineKeyboardButton(text="Назад", callback_data="list")],
        ]
    )


def edit_keyboard(record_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="ФИО", callback_data=f"edit_field:{record_id}:full_name")],
            [InlineKeyboardButton(text="Дата рождения", callback_data=f"edit_field:{record_id}:birthday")],
            [InlineKeyboardButton(text="Время", callback_data=f"edit_field:{record_id}:remind_time")],
            [InlineKeyboardButton(text="Часовой пояс", callback_data=f"edit_field:{record_id}:remind_timezone")],
            [InlineKeyboardButton(text="Примечание", callback_data=f"edit_field:{record_id}:note")],
            [InlineKeyboardButton(text="Назад", callback_data=f"record:{record_id}")],
        ]
    )


def clear_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Да, очистить", callback_data="clear_confirm")],
            [InlineKeyboardButton(text="Назад", callback_data="clear_cancel")],
        ]
    )


def admin_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Управление whitelist", callback_data="admin_whitelist")],
        ]
    )


def admin_whitelist_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Добавить пользователя", callback_data="admin_add_user")],
            [InlineKeyboardButton(text="Удалить пользователя", callback_data="admin_remove_user")],
        ]
    )


def _format_record(record: BirthdayRecord) -> str:
    year = f".{record.year}" if record.year else ""
    note = f"\nПримечание: {record.note}" if record.note else ""
    return (
        f"#{record.id} {record.full_name}\n"
        f"Дата: {record.day:02d}.{record.month:02d}{year}\n"
        f"Напоминание: {record.remind_time} {record.remind_timezone}"
        f"{note}"
    )


def _edit_prompt(field: str, record: BirthdayRecord) -> str:
    prompts = {
        "full_name": f"Введите новое ФИО.\nСейчас: {record.full_name}",
        "birthday": "Введите новую дату рождения: DD.MM или DD.MM.YYYY.",
        "remind_time": f"Введите новое время HH:MM или '-' для 09:00.\nСейчас: {record.remind_time}",
        "remind_timezone": (
            "Введите новый часовой пояс, например Asia/Novosibirsk.\n"
            f"Сейчас: {record.remind_timezone}"
        ),
        "note": "Введите новое примечание или '-' чтобы очистить его.",
    }
    return prompts[field]


def _record_to_dict(record: BirthdayRecord) -> dict[str, Any]:
    return {
        "full_name": record.full_name,
        "day": record.day,
        "month": record.month,
        "year": record.year,
        "remind_time": record.remind_time,
        "remind_timezone": record.remind_timezone,
        "note": record.note,
    }


def _apply_edit(updated: dict[str, Any], field: str, raw: str) -> None:
    if field == "full_name":
        if not raw:
            raise ValueError("ФИО не должно быть пустым.")
        updated["full_name"] = raw
        return
    if field == "birthday":
        birthday = parse_birthday_date(raw)
        updated["day"] = birthday.day
        updated["month"] = birthday.month
        updated["year"] = birthday.year
        return
    if field == "remind_time":
        updated["remind_time"] = "09:00" if raw in ("", "-") else parse_reminder_time(raw).strftime("%H:%M")
        return
    if field == "remind_timezone":
        updated["remind_timezone"] = normalize_timezone(raw)
        return
    if field == "note":
        updated["note"] = None if raw in ("", "-") else raw
        return
    raise ValueError("Неизвестное поле.")


def _command_tail(text: str | None) -> str:
    if not text:
        return ""
    return text.partition(" ")[2].strip()


def _is_admin(message: Message, config: Config) -> bool:
    return bool(message.from_user and message.from_user.id == config.admin_telegram_id)


def _is_admin_callback(callback: CallbackQuery, config: Config) -> bool:
    return callback.from_user.id == config.admin_telegram_id
