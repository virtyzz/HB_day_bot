from __future__ import annotations

import logging
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
logger = logging.getLogger(__name__)

ADD_BUTTON = "Добавить ДР"
LIST_BUTTON = "Посмотреть список"
CLEAR_BUTTON = "Очистить список"
ADMIN_BUTTON = "Админка"
TIMEZONE_PAGE_SIZE = 10
TIMEZONE_CHOICES = [
    ("UTC-12", "Etc/GMT+12"),
    ("UTC-11", "Etc/GMT+11"),
    ("UTC-10", "Etc/GMT+10"),
    ("UTC-09", "Etc/GMT+9"),
    ("UTC-08", "Etc/GMT+8"),
    ("UTC-07", "Etc/GMT+7"),
    ("UTC-06", "Etc/GMT+6"),
    ("UTC-05", "Etc/GMT+5"),
    ("UTC-04", "Etc/GMT+4"),
    ("UTC-03", "Etc/GMT+3"),
    ("UTC-02", "Etc/GMT+2"),
    ("UTC-01", "Etc/GMT+1"),
    ("UTC+00", "UTC"),
    ("UTC+01", "Etc/GMT-1"),
    ("UTC+02", "Etc/GMT-2"),
    ("UTC+03", "Etc/GMT-3"),
    ("UTC+04", "Etc/GMT-4"),
    ("UTC+05", "Etc/GMT-5"),
    ("UTC+06", "Etc/GMT-6"),
    ("UTC+07", "Etc/GMT-7"),
    ("UTC+08", "Etc/GMT-8"),
    ("UTC+09", "Etc/GMT-9"),
    ("UTC+10", "Etc/GMT-10"),
    ("UTC+11", "Etc/GMT-11"),
    ("UTC+12", "Etc/GMT-12"),
    ("UTC+13", "Etc/GMT-13"),
    ("UTC+14", "Etc/GMT-14"),
]


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
        logger.info("Пользователь открыл главное меню: %s", _format_user(message.from_user))
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
            "или отправьте '-' чтобы взять ваш сохраненный часовой пояс:",
            reply_markup=timezone_keyboard("add", 0, include_default=True),
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
        logger.info(
            "Добавлена запись ДР #%s пользователем %s: %s",
            record_id,
            _format_user(message.from_user),
            data["full_name"],
        )
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
        await show_birthdays_list(callback.message, storage, callback.from_user.id, edit=True)

    @router.callback_query(F.data.startswith("record:"))
    async def show_record(callback: CallbackQuery, state: FSMContext) -> None:
        if not callback.from_user or not callback.message or not callback.data:
            return
        await state.clear()
        record_id = int(callback.data.split(":", 1)[1])
        record = await storage.get_birthday(callback.from_user.id, record_id)
        await callback.answer()
        if not record:
            await show_menu(callback.message, "Запись не найдена.", edit=True)
            return
        await show_menu(
            callback.message,
            _format_record(record),
            record_keyboard(record.id),
            edit=True,
        )

    @router.callback_query(F.data.startswith("edit:"))
    async def edit_record_menu(callback: CallbackQuery, state: FSMContext) -> None:
        if not callback.data or not callback.message:
            return
        await state.clear()
        record_id = int(callback.data.split(":", 1)[1])
        await callback.answer()
        await show_menu(
            callback.message,
            "Что изменить?",
            edit_keyboard(record_id),
            edit=True,
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
        if field == "remind_timezone":
            await show_menu(
                callback.message,
                "Выберите новый часовой пояс или отправьте его текстом, например Asia/Novosibirsk.",
                timezone_keyboard(f"edit:{record_id}", 0),
                edit=True,
            )
            return
        await show_menu(callback.message, _edit_prompt(field, record), edit=True)

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
            reset_last_reminded=_reminder_schedule_changed(record, updated),
        )
        await state.clear()
        logger.info(
            "Изменена запись ДР #%s пользователем %s: поле %s",
            record.id,
            _format_user(message.from_user),
            _field_name(field),
        )
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
        if deleted:
            logger.info("Удалена запись ДР #%s пользователем %s", record_id, _format_user(callback.from_user))
        else:
            logger.warning("Попытка удалить несуществующую запись #%s: %s", record_id, _format_user(callback.from_user))
        await callback.answer("Удалено" if deleted else "Запись не найдена")
        await show_birthdays_list(callback.message, storage, callback.from_user.id, edit=True)

    @router.message(F.text == CLEAR_BUTTON)
    async def clear_list_warning(message: Message) -> None:
        await message.answer(
            "Вы точно хотите полностью очистить свой список ДР?",
            reply_markup=clear_confirm_keyboard(),
        )

    @router.callback_query(F.data == "clear_cancel")
    async def clear_cancel(callback: CallbackQuery) -> None:
        await callback.answer()
        if callback.from_user and callback.message:
            await show_birthdays_list(callback.message, storage, callback.from_user.id, edit=True)

    @router.callback_query(F.data == "clear_confirm")
    async def clear_confirm(callback: CallbackQuery) -> None:
        if not callback.from_user or not callback.message:
            return
        count = await storage.clear_birthdays(callback.from_user.id)
        logger.warning("Пользователь очистил список ДР: %s; удалено записей: %s", _format_user(callback.from_user), count)
        await callback.answer("Список очищен")
        await show_menu(callback.message, f"Список очищен. Удалено записей: {count}.", edit=True)

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
        await show_menu(callback.message, text, admin_whitelist_keyboard(), edit=True)

    @router.callback_query(F.data == "admin_back")
    async def admin_back(callback: CallbackQuery, state: FSMContext) -> None:
        if not _is_admin_callback(callback, config) or not callback.message:
            return
        await state.clear()
        await callback.answer()
        await show_menu(callback.message, "Админка", admin_keyboard(), edit=True)

    @router.callback_query(F.data == "admin_add_user")
    async def admin_add_user(callback: CallbackQuery, state: FSMContext) -> None:
        if not _is_admin_callback(callback, config) or not callback.message:
            return
        await state.set_state(AdminWhitelist.add_user)
        await callback.answer()
        await show_menu(callback.message, "Введите Telegram ID пользователя для добавления:", edit=True)

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
        logger.info("Администратор %s добавил пользователя %s в whitelist", _format_user(message.from_user), user_id)
        await message.answer(f"Пользователь {user_id} добавлен в белый список.", reply_markup=admin_keyboard())

    @router.callback_query(F.data == "admin_remove_user")
    async def admin_remove_user(callback: CallbackQuery, state: FSMContext) -> None:
        if not _is_admin_callback(callback, config) or not callback.message:
            return
        await state.set_state(AdminWhitelist.remove_user)
        await callback.answer()
        await show_menu(callback.message, "Введите Telegram ID пользователя для удаления:", edit=True)

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
        logger.warning("Администратор %s удалил пользователя %s из whitelist", _format_user(message.from_user), user_id)
        await message.answer(f"Пользователь {user_id} удален из белого списка.", reply_markup=admin_keyboard())

    @router.message(Command("timezone"))
    async def set_timezone(message: Message) -> None:
        if not message.from_user:
            return
        raw = _command_tail(message.text)
        if not raw:
            await message.answer(
                "Выберите часовой пояс или укажите его вручную, например: /timezone Asia/Novosibirsk",
                reply_markup=timezone_keyboard("user", 0),
            )
            return
        try:
            timezone = normalize_timezone(raw)
        except ValueError:
            await message.answer("Не знаю такой часовой пояс. Пример: Asia/Novosibirsk")
            return
        await storage.set_user_timezone(message.from_user.id, timezone)
        logger.info("Пользователь %s установил часовой пояс: %s", _format_user(message.from_user), timezone)
        await message.answer(f"Готово. Ваш часовой пояс по умолчанию: {timezone}")

    @router.callback_query(F.data.startswith("tzpage:"))
    async def timezone_page(callback: CallbackQuery) -> None:
        if not callback.message or not callback.data:
            return
        prefix, page_raw = callback.data.rsplit(":", 1)
        context = prefix.split(":", 1)[1]
        await callback.answer()
        await show_menu(
            callback.message,
            _timezone_menu_text(context),
            timezone_keyboard(context, int(page_raw), include_default=context == "add"),
            edit=True,
        )

    @router.callback_query(F.data.startswith("tzdefault:"))
    async def timezone_default(callback: CallbackQuery, state: FSMContext) -> None:
        if not callback.from_user or not callback.message or not callback.data:
            return
        _, context = callback.data.split(":", 1)
        timezone = await storage.get_user_timezone(callback.from_user.id, config.default_user_timezone)
        await callback.answer()
        if context == "add":
            await state.update_data(remind_timezone=timezone)
            await state.set_state(AddBirthday.note)
            await show_menu(
                callback.message,
                f"Часовой пояс: {_timezone_display(timezone)}\n\nВведите примечание или отправьте '-' чтобы оставить пустым:",
                edit=True,
            )

    @router.callback_query(F.data.startswith("tzset:"))
    async def timezone_selected(callback: CallbackQuery, state: FSMContext) -> None:
        if not callback.from_user or not callback.message or not callback.data:
            return
        prefix, index_raw = callback.data.rsplit(":", 1)
        context = prefix.split(":", 1)[1]
        timezone = TIMEZONE_CHOICES[int(index_raw)][1]
        await callback.answer("Выбрано")
        if context == "user":
            await storage.set_user_timezone(callback.from_user.id, timezone)
            logger.info("Пользователь %s выбрал часовой пояс кнопкой: %s", _format_user(callback.from_user), timezone)
            await show_menu(
                callback.message,
                f"Готово. Ваш часовой пояс по умолчанию: {_timezone_display(timezone)}",
                edit=True,
            )
            return
        if context == "add":
            await state.update_data(remind_timezone=timezone)
            await state.set_state(AddBirthday.note)
            await show_menu(
                callback.message,
                f"Часовой пояс: {_timezone_display(timezone)}\n\nВведите примечание или отправьте '-' чтобы оставить пустым:",
                edit=True,
            )
            return
        if context.startswith("edit:"):
            record_id = int(context.split(":", 1)[1])
            record = await storage.get_birthday(callback.from_user.id, record_id)
            if not record:
                await state.clear()
                await show_menu(callback.message, "Запись не найдена.", edit=True)
                return
            updated = _record_to_dict(record)
            updated["remind_timezone"] = timezone
            await storage.update_birthday(
                owner_telegram_id=callback.from_user.id,
                record_id=record.id,
                full_name=updated["full_name"],
                day=updated["day"],
                month=updated["month"],
                year=updated["year"],
                remind_time=updated["remind_time"],
                remind_timezone=updated["remind_timezone"],
                note=updated["note"],
                reset_last_reminded=_reminder_schedule_changed(record, updated),
            )
            await state.clear()
            logger.info(
                "Пользователь %s изменил часовой пояс записи #%s: %s",
                _format_user(callback.from_user),
                record.id,
                timezone,
            )
            refreshed = await storage.get_birthday(callback.from_user.id, record.id)
            await show_menu(
                callback.message,
                "Изменено.\n\n" + _format_record(refreshed or record),
                record_keyboard(record.id),
                edit=True,
            )

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
            logger.info("Доступ разрешен администратору: %s; событие: %s", _format_user(event.from_user), _event_name(event))
            return await handler(event, data)
        if await self.storage.is_whitelisted(user_id):
            await self.storage.ensure_user(user_id, self.config.default_user_timezone)
            logger.info("Доступ разрешен пользователю из whitelist: %s; событие: %s", _format_user(event.from_user), _event_name(event))
            return await handler(event, data)
        logger.warning(
            "Доступ запрещен: пользователь не в whitelist: %s; событие: %s",
            _format_user(event.from_user),
            _event_name(event),
        )
        return None


async def send_birthdays_list(message: Message, storage: Storage, user_id: int) -> None:
    await show_birthdays_list(message, storage, user_id)


async def show_birthdays_list(message: Message, storage: Storage, user_id: int, *, edit: bool = False) -> None:
    records = await storage.list_birthdays(user_id)
    if not records:
        await show_menu(message, "У вас пока нет записей.", edit=edit)
        return
    await show_menu(message, "Ваш список ДР:", records_keyboard(records), edit=edit)


async def show_menu(
    message: Message,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
    *,
    edit: bool = False,
) -> None:
    if edit:
        try:
            await message.edit_text(text, reply_markup=reply_markup)
            return
        except Exception:
            pass
    await message.answer(text, reply_markup=reply_markup)


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
            [InlineKeyboardButton(text="Назад", callback_data="admin_back")],
        ]
    )


def timezone_keyboard(context: str, page: int, *, include_default: bool = False) -> InlineKeyboardMarkup:
    total_pages = (len(TIMEZONE_CHOICES) + TIMEZONE_PAGE_SIZE - 1) // TIMEZONE_PAGE_SIZE
    page = max(0, min(page, total_pages - 1))
    start = page * TIMEZONE_PAGE_SIZE
    rows = [
        [InlineKeyboardButton(text=label, callback_data=f"tzset:{context}:{start + index}")]
        for index, (label, _) in enumerate(TIMEZONE_CHOICES[start : start + TIMEZONE_PAGE_SIZE])
    ]
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="Назад", callback_data=f"tzpage:{context}:{page - 1}"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton(text="Вперед", callback_data=f"tzpage:{context}:{page + 1}"))
    if nav:
        rows.append(nav)
    if include_default:
        rows.append([InlineKeyboardButton(text="Мой часовой пояс", callback_data=f"tzdefault:{context}")])
    if context.startswith("edit:"):
        record_id = context.split(":", 1)[1]
        rows.append([InlineKeyboardButton(text="Назад", callback_data=f"edit:{record_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _timezone_menu_text(context: str) -> str:
    if context == "user":
        return "Выберите часовой пояс или укажите его вручную, например: /timezone Asia/Novosibirsk"
    if context == "add":
        return (
            "Выберите часовой пояс напоминания или отправьте его текстом, например Asia/Novosibirsk.\n"
            "Можно отправить '-' чтобы взять ваш сохраненный часовой пояс."
        )
    return "Выберите новый часовой пояс или отправьте его текстом, например Asia/Novosibirsk."


def _timezone_display(timezone: str) -> str:
    for label, name in TIMEZONE_CHOICES:
        if name == timezone:
            return f"{label} ({name})"
    return timezone


def _format_user(user: Any) -> str:
    username = f"@{user.username}" if getattr(user, "username", None) else "без username"
    full_name = getattr(user, "full_name", "") or "без имени"
    return f"id={user.id}, {username}, имя='{full_name}'"


def _event_name(event: Message | CallbackQuery) -> str:
    if isinstance(event, CallbackQuery):
        return f"кнопка {event.data or '<без data>'}"
    if event.text:
        command = event.text.split(maxsplit=1)[0]
        return f"сообщение {command}"
    return "сообщение без текста"


def _field_name(field: str) -> str:
    names = {
        "full_name": "ФИО",
        "birthday": "дата рождения",
        "remind_time": "время напоминания",
        "remind_timezone": "часовой пояс",
        "note": "примечание",
    }
    return names.get(field, field)


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


def _reminder_schedule_changed(record: BirthdayRecord, updated: dict[str, Any]) -> bool:
    return (
        record.day != updated["day"]
        or record.month != updated["month"]
        or record.year != updated["year"]
        or record.remind_time != updated["remind_time"]
        or record.remind_timezone != updated["remind_timezone"]
    )


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
