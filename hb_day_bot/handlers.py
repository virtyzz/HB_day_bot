from __future__ import annotations

import logging
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

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
from aiogram.utils.formatting import Bold, Pre, Text

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
ADD_CANCEL_CALLBACK = "add_cancel"
ADD_BACK_CALLBACK = "add_back"
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
        data = await state.get_data()
        await _delete_add_menu(message, data)
        await _delete_message(message)
        await state.clear()
        await message.answer(
            "Отменено.",
            reply_markup=main_menu(_is_admin(message, config)),
        )

    @router.callback_query(F.data == ADD_CANCEL_CALLBACK)
    async def add_cancel(callback: CallbackQuery, state: FSMContext) -> None:
        if not callback.message:
            return
        await state.clear()
        await callback.answer("Отменено")
        await show_menu(
            callback.message,
            "Добавление ДР отменено. Данные не сохранены.",
            edit=True,
        )

    @router.callback_query(F.data == ADD_BACK_CALLBACK)
    async def add_back(callback: CallbackQuery, state: FSMContext) -> None:
        if not callback.message:
            return
        current_state = await state.get_state()
        previous_state = _previous_add_state(current_state)
        if not previous_state:
            await callback.answer()
            await state.set_state(AddBirthday.full_name)
            await _clear_add_fields_from(state, AddBirthday.full_name)
            await _show_add_menu_from_callback(
                callback,
                state,
                AddBirthday.full_name,
                config.default_user_timezone,
            )
            return
        await state.set_state(previous_state)
        await _clear_add_fields_from(state, previous_state)
        await callback.answer()
        await _show_add_menu_from_callback(
            callback,
            state,
            previous_state,
            config.default_user_timezone,
        )

    @router.message(F.text == ADD_BUTTON)
    @router.message(Command("add"))
    async def add_start(message: Message, state: FSMContext) -> None:
        await state.clear()
        await state.set_state(AddBirthday.full_name)
        summary = await message.answer(_add_summary_text({}, AddBirthday.full_name))
        prompt = await message.answer(
            _add_prompt(AddBirthday.full_name),
            reply_markup=add_step_keyboard(can_go_back=False),
        )
        await state.update_data(
            add_summary_message_id=summary.message_id,
            add_prompt_message_id=prompt.message_id,
        )
        await _delete_message(message)

    @router.message(AddBirthday.full_name, F.text)
    async def add_full_name(message: Message, state: FSMContext) -> None:
        full_name = message.text.strip()
        if not full_name:
            await _show_add_menu_from_message(
                message,
                state,
                AddBirthday.full_name,
                config.default_user_timezone,
                error="ФИО не должно быть пустым.",
            )
            return
        await state.update_data(full_name=full_name)
        await state.set_state(AddBirthday.birthday)
        await _show_add_menu_from_message(
            message,
            state,
            AddBirthday.birthday,
            config.default_user_timezone,
        )

    @router.message(AddBirthday.birthday, F.text)
    async def add_birthday_date(message: Message, state: FSMContext) -> None:
        try:
            birthday = parse_birthday_date(message.text)
        except ValueError as exc:
            await _show_add_menu_from_message(
                message,
                state,
                AddBirthday.birthday,
                config.default_user_timezone,
                error=f"Не получилось разобрать дату. {exc}",
            )
            return
        await state.update_data(day=birthday.day, month=birthday.month, year=birthday.year)
        await state.set_state(AddBirthday.remind_time)
        await _show_add_menu_from_message(
            message,
            state,
            AddBirthday.remind_time,
            config.default_user_timezone,
        )

    @router.message(AddBirthday.remind_time, F.text)
    async def add_remind_time(message: Message, state: FSMContext) -> None:
        raw = message.text.strip()
        if raw in ("", "-"):
            remind_time = "09:00"
        else:
            try:
                remind_time = parse_reminder_time(raw).strftime("%H:%M")
            except ValueError as exc:
                await _show_add_menu_from_message(
                    message,
                    state,
                    AddBirthday.remind_time,
                    config.default_user_timezone,
                    error=f"Не получилось разобрать время. {exc}",
                )
                return
        await state.update_data(remind_time=remind_time)
        await state.set_state(AddBirthday.remind_timezone)
        await _show_add_menu_from_message(
            message,
            state,
            AddBirthday.remind_timezone,
            config.default_user_timezone,
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
                await _show_add_menu_from_message(
                    message,
                    state,
                    AddBirthday.remind_timezone,
                    config.default_user_timezone,
                    error="Не знаю такой часовой пояс. Пример: Asia/Novosibirsk",
                )
                return
        await state.update_data(remind_timezone=timezone)
        await state.set_state(AddBirthday.note)
        await _show_add_menu_from_message(
            message,
            state,
            AddBirthday.note,
            config.default_user_timezone,
        )

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
        await _delete_message(message)
        logger.info(
            "Добавлена запись ДР #%s пользователем %s: %s",
            record_id,
            _format_user(message.from_user),
            data["full_name"],
        )
        summary_message_id = data.get("add_summary_message_id") or data.get("add_menu_message_id")
        prompt_message_id = data.get("add_prompt_message_id")
        if prompt_message_id:
            try:
                await message.bot.delete_message(message.chat.id, prompt_message_id)
            except Exception:
                pass
        if summary_message_id:
            try:
                await message.bot.edit_message_text(
                    chat_id=message.chat.id,
                    message_id=summary_message_id,
                    text=f"Запись #{record_id} сохранена.",
                )
                return
            except Exception:
                pass
        await message.answer(f"Запись #{record_id} сохранена.", reply_markup=main_menu(_is_admin(message, config)))

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

    @router.callback_query(F.data == "records_all")
    async def show_all_birthdays(callback: CallbackQuery) -> None:
        if not callback.from_user or not callback.message:
            return
        records = await storage.list_birthdays(callback.from_user.id)
        await callback.answer()
        if not records:
            await show_menu(callback.message, "У вас пока нет записей.", edit=True)
            return
        pages = _format_all_records_pages(records)
        await show_menu(callback.message, pages[0], all_records_keyboard(), edit=True)
        for page in pages[1:]:
            await callback.message.answer(page)

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
                timezone_keyboard(
                    f"edit:{record_id}",
                    0,
                    include_default=True,
                    default_timezone=config.default_user_timezone,
                ),
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
    async def timezone_page(callback: CallbackQuery, state: FSMContext) -> None:
        if not callback.message or not callback.data:
            return
        prefix, page_raw = callback.data.rsplit(":", 1)
        context = prefix.split(":", 1)[1]
        await callback.answer()
        if context == "add":
            await _show_add_menu_from_callback(
                callback,
                state,
                AddBirthday.remind_timezone,
                config.default_user_timezone,
                reply_markup=timezone_keyboard(
                    context,
                    int(page_raw),
                    include_default=True,
                    default_timezone=config.default_user_timezone,
                ),
            )
            return
        await show_menu(
            callback.message,
            _timezone_menu_text(context),
            timezone_keyboard(
                context,
                int(page_raw),
                include_default=context == "add",
                default_timezone=config.default_user_timezone,
            ),
            edit=True,
        )

    @router.callback_query(F.data.startswith("tzdefault:"))
    async def timezone_default(callback: CallbackQuery, state: FSMContext) -> None:
        if not callback.from_user or not callback.message or not callback.data:
            return
        _, context = callback.data.split(":", 1)
        timezone = config.default_user_timezone
        await callback.answer()
        if context == "add":
            await state.update_data(remind_timezone=timezone)
            await state.set_state(AddBirthday.note)
            await _show_add_menu_from_callback(
                callback,
                state,
                AddBirthday.note,
                config.default_user_timezone,
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
            await _show_add_menu_from_callback(
                callback,
                state,
                AddBirthday.note,
                config.default_user_timezone,
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


def add_step_keyboard(*, can_go_back: bool = True) -> InlineKeyboardMarkup:
    row = []
    if can_go_back:
        row.append(InlineKeyboardButton(text="Назад", callback_data=ADD_BACK_CALLBACK))
    row.append(InlineKeyboardButton(text="Отмена", callback_data=ADD_CANCEL_CALLBACK))
    return InlineKeyboardMarkup(inline_keyboard=[row])


def records_keyboard(records: list[BirthdayRecord]) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text="Показать все ДР", callback_data="records_all")]]
    rows.extend(
        [InlineKeyboardButton(text=record.full_name, callback_data=f"record:{record.id}")]
        for record in records
    )
    return InlineKeyboardMarkup(
        inline_keyboard=rows
    )


def all_records_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Назад", callback_data="list")],
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


def timezone_keyboard(
    context: str,
    page: int,
    *,
    include_default: bool = False,
    default_timezone: str = "UTC",
) -> InlineKeyboardMarkup:
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
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"По-умолчанию ({_timezone_utc_offset(default_timezone)})",
                    callback_data=f"tzdefault:{context}",
                )
            ]
        )
    if context == "add":
        rows.append(
            [
                InlineKeyboardButton(text="Назад", callback_data=ADD_BACK_CALLBACK),
                InlineKeyboardButton(text="Отмена", callback_data=ADD_CANCEL_CALLBACK),
            ]
        )
    if context.startswith("edit:"):
        record_id = context.split(":", 1)[1]
        rows.append([InlineKeyboardButton(text="Назад", callback_data=f"edit:{record_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _add_full_name_prompt() -> str:
    return "Введите ФИО:"


def _add_birthday_prompt() -> str:
    return (
        "Введите дату рождения: DD.MM или DD.MM.YYYY.\n"
        "Год можно не указывать, например: 21.07"
    )


def _add_remind_time_prompt() -> str:
    return "Введите время напоминания HH:MM или отправьте '-' чтобы оставить 09:00:"


def _add_remind_timezone_prompt() -> str:
    return (
        "Введите часовой пояс напоминания, например Asia/Novosibirsk, "
        "или отправьте '-' чтобы взять ваш сохраненный часовой пояс:"
    )


def _add_note_prompt() -> str:
    return "Введите примечание или отправьте '-' чтобы оставить пустым:"


async def _show_add_menu_from_message(
    message: Message,
    state: FSMContext,
    add_state: State,
    default_timezone: str,
    *,
    error: str | None = None,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    data = await state.get_data()
    await _render_add_menu(
        message,
        state,
        data,
        add_state,
        default_timezone,
        error=error,
        reply_markup=reply_markup,
    )
    await _delete_message(message)


async def _show_add_menu_from_callback(
    callback: CallbackQuery,
    state: FSMContext,
    add_state: State,
    default_timezone: str,
    *,
    error: str | None = None,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    if not callback.message:
        return
    await _render_add_menu(
        callback.message,
        state,
        await state.get_data(),
        add_state,
        default_timezone,
        error=error,
        reply_markup=reply_markup,
    )


async def _render_add_menu(
    message: Message,
    state: FSMContext,
    data: dict[str, Any],
    add_state: State,
    default_timezone: str,
    *,
    error: str | None = None,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    summary_text = _add_summary_text(data, add_state)
    prompt_text = _add_prompt_text(add_state, error=error)
    prompt_markup = reply_markup or _add_reply_markup(add_state, default_timezone)
    summary_message_id = data.get("add_summary_message_id")
    prompt_message_id = data.get("add_prompt_message_id") or data.get("add_menu_message_id")

    if summary_message_id:
        try:
            await message.bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=summary_message_id,
                text=summary_text,
            )
        except Exception:
            pass
    if prompt_message_id:
        try:
            await message.bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=prompt_message_id,
                text=prompt_text,
                reply_markup=prompt_markup,
            )
            return
        except Exception:
            pass

    if not summary_message_id:
        summary = await message.answer(summary_text)
        await state.update_data(add_summary_message_id=summary.message_id)
    prompt = await message.answer(prompt_text, reply_markup=prompt_markup)
    await state.update_data(add_prompt_message_id=prompt.message_id)


async def _delete_message(message: Message) -> None:
    try:
        await message.delete()
    except Exception:
        pass


async def _delete_add_menu(message: Message, data: dict[str, Any]) -> None:
    message_ids = (
        data.get("add_summary_message_id"),
        data.get("add_prompt_message_id"),
        data.get("add_menu_message_id"),
    )
    for message_id in dict.fromkeys(message_ids):
        if not message_id:
            continue
        try:
            await message.bot.delete_message(message.chat.id, message_id)
        except Exception:
            pass


async def _clear_add_fields_from(state: FSMContext, add_state: State) -> None:
    fields_by_state = {
        AddBirthday.full_name.state: {
            "full_name": None,
            "day": None,
            "month": None,
            "year": None,
            "remind_time": None,
            "remind_timezone": None,
            "note": None,
        },
        AddBirthday.birthday.state: {
            "day": None,
            "month": None,
            "year": None,
            "remind_time": None,
            "remind_timezone": None,
            "note": None,
        },
        AddBirthday.remind_time.state: {
            "remind_time": None,
            "remind_timezone": None,
            "note": None,
        },
        AddBirthday.remind_timezone.state: {
            "remind_timezone": None,
            "note": None,
        },
        AddBirthday.note.state: {
            "note": None,
        },
    }
    updates = fields_by_state.get(add_state.state)
    if updates:
        await state.update_data(**updates)


def _add_summary_text(data: dict[str, Any], add_state: State) -> str:
    return Text(
        Bold("Добавление ДР"),
        "\n",
        "Текущий этап: ",
        Bold(_add_step_title(add_state)),
        "\n\n",
        Bold("Заполнено"),
        "\n",
        _add_field_line("ФИО", data.get("full_name"), AddBirthday.full_name),
        "\n",
        _add_field_line("Дата рождения", _add_date_value(data), AddBirthday.birthday),
        "\n",
        _add_field_line("Время", data.get("remind_time"), AddBirthday.remind_time),
        "\n",
        _add_field_line("Часовой пояс", _add_timezone_value(data.get("remind_timezone")), AddBirthday.remind_timezone),
        "\n",
        _add_field_line("Примечание", data.get("note"), AddBirthday.note),
        "\n\n",
        Bold("Что осталось"),
        "\n",
        _add_remaining_text(data, add_state),
    ).as_html()


def _add_prompt_text(add_state: State, *, error: str | None = None) -> str:
    parts: list[str | Text] = []
    if error:
        parts.extend([Bold(f"Ошибка: {error}"), "\n\n"])
    parts.append(_add_prompt(add_state))
    return Text(*parts).as_html()


def _add_date_value(data: dict[str, Any]) -> str:
    day = data.get("day")
    month = data.get("month")
    if not day or not month:
        return ""
    year = data.get("year")
    suffix = f".{year}" if year else ""
    return f"{int(day):02d}.{int(month):02d}{suffix}"


def _add_timezone_value(value: Any) -> str:
    return _timezone_display(str(value)) if value else ""


def _add_field_line(label: str, value: Any, field_state: State) -> Text:
    if value:
        return Text("✓ ", Bold(label), ": ", str(value))
    if field_state == AddBirthday.note:
        return Text("○ ", Bold(label), ": можно оставить пустым")
    return Text("○ ", Bold(label), ": не заполнено")


def _add_remaining_text(data: dict[str, Any], add_state: State) -> Text:
    remaining = []
    for field_state, title, is_filled in _add_field_statuses(data):
        if field_state == AddBirthday.note:
            continue
        if not is_filled:
            remaining.append(title)
    if not remaining and add_state == AddBirthday.note:
        return Text("Осталось ввести примечание или отправить '-' для пустого поля.")
    if not remaining:
        return Text("Все обязательные поля заполнены.")
    return Text(*_join_rich_lines([f"○ {title}" for title in remaining]))


def _add_field_statuses(data: dict[str, Any]) -> list[tuple[State, str, bool]]:
    return [
        (AddBirthday.full_name, _add_step_title(AddBirthday.full_name), bool(data.get("full_name"))),
        (AddBirthday.birthday, _add_step_title(AddBirthday.birthday), bool(data.get("day") and data.get("month"))),
        (AddBirthday.remind_time, _add_step_title(AddBirthday.remind_time), bool(data.get("remind_time"))),
        (AddBirthday.remind_timezone, _add_step_title(AddBirthday.remind_timezone), bool(data.get("remind_timezone"))),
        (AddBirthday.note, _add_step_title(AddBirthday.note), bool(data.get("note"))),
    ]


def _add_step_title(state: State) -> str:
    titles = {
        AddBirthday.full_name.state: "ФИО",
        AddBirthday.birthday.state: "Дата рождения",
        AddBirthday.remind_time.state: "Время напоминания",
        AddBirthday.remind_timezone.state: "Часовой пояс",
        AddBirthday.note.state: "Примечание",
    }
    return titles.get(state.state, "Добавление")


def _previous_add_state(current_state: str | None) -> State | None:
    previous_states = {
        AddBirthday.birthday.state: AddBirthday.full_name,
        AddBirthday.remind_time.state: AddBirthday.birthday,
        AddBirthday.remind_timezone.state: AddBirthday.remind_time,
        AddBirthday.note.state: AddBirthday.remind_timezone,
    }
    return previous_states.get(current_state)


def _add_prompt(state: State) -> str:
    state_name = state.state
    prompts = {
        AddBirthday.full_name.state: _add_full_name_prompt(),
        AddBirthday.birthday.state: _add_birthday_prompt(),
        AddBirthday.remind_time.state: _add_remind_time_prompt(),
        AddBirthday.remind_timezone.state: _add_remind_timezone_prompt(),
        AddBirthday.note.state: _add_note_prompt(),
    }
    return prompts.get(state_name, _add_full_name_prompt())


def _add_reply_markup(state: State, default_timezone: str) -> InlineKeyboardMarkup:
    state_name = state.state
    if state_name == AddBirthday.full_name.state:
        return add_step_keyboard(can_go_back=False)
    if state_name == AddBirthday.remind_timezone.state:
        return timezone_keyboard(
            "add",
            0,
            include_default=True,
            default_timezone=default_timezone,
        )
    return add_step_keyboard()


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
    return f"{_timezone_utc_offset(timezone)} ({timezone})"


def _timezone_utc_offset(timezone: str) -> str:
    offset = datetime.now(ZoneInfo(timezone)).utcoffset()
    if offset is None:
        return "UTC+0"

    total_minutes = int(offset.total_seconds() // 60)
    sign = "+" if total_minutes >= 0 else "-"
    total_minutes = abs(total_minutes)
    hours, minutes = divmod(total_minutes, 60)
    if minutes:
        return f"UTC{sign}{hours}:{minutes:02d}"
    return f"UTC{sign}{hours}"


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
        f"Напоминание: {record.remind_time} {_timezone_display(record.remind_timezone)}"
        f"{note}"
    )


def _format_all_records_pages(records: list[BirthdayRecord]) -> list[str]:
    pages = []
    rows: list[tuple[int, BirthdayRecord]] = []
    for index, record in enumerate(records, start=1):
        candidate_rows = [*rows, (index, record)]
        candidate_page = _format_all_records_table_page(
            records_count=len(records),
            rows=candidate_rows,
            continuation=bool(pages),
        )
        if len(candidate_page) > 3900 and rows:
            pages.append(
                _format_all_records_table_page(
                    records_count=len(records),
                    rows=rows,
                    continuation=bool(pages),
                )
            )
            rows = [(index, record)]
        else:
            rows = candidate_rows
    if rows:
        pages.append(
            _format_all_records_table_page(
                records_count=len(records),
                rows=rows,
                continuation=bool(pages),
            )
        )
    return pages


def _format_all_records_table_page(
    *,
    records_count: int,
    rows: list[tuple[int, BirthdayRecord]],
    continuation: bool,
) -> str:
    return Text(
        Bold("Все ДР, продолжение" if continuation else "Все ДР"),
        "\n",
        f"Всего записей: {records_count}",
        "\n\n",
        Pre(_format_records_table(rows)),
    ).as_html()


def _format_records_table(rows: list[tuple[int, BirthdayRecord]]) -> str:
    widths = {
        "index": 3,
        "name": 22,
        "date": 10,
        "time": 5,
        "timezone": 18,
        "note": 18,
    }
    header = _format_table_row(
        ("№", "ФИО", "Дата", "Время", "Пояс", "Примечание"),
        widths,
    )
    separator = _format_table_row(
        (
            "-" * widths["index"],
            "-" * widths["name"],
            "-" * widths["date"],
            "-" * widths["time"],
            "-" * widths["timezone"],
            "-" * widths["note"],
        ),
        widths,
    )
    table_rows = [header, separator]
    table_rows.extend(_format_record_table_row(index, record, widths) for index, record in rows)
    return "\n".join(table_rows)


def _format_record_table_row(index: int, record: BirthdayRecord, widths: dict[str, int]) -> str:
    note = record.note or ""
    return _format_table_row(
        (
            str(index),
            record.full_name,
            _format_birthday_date(record),
            record.remind_time,
            _timezone_display(record.remind_timezone),
            note,
        ),
        widths,
    )


def _format_table_row(values: tuple[str, str, str, str, str, str], widths: dict[str, int]) -> str:
    index, name, birthday, remind_time, timezone, note = values
    return (
        f"{_fit_table_cell(index, widths['index'], align='right')} "
        f"{_fit_table_cell(name, widths['name'])} "
        f"{_fit_table_cell(birthday, widths['date'])} "
        f"{_fit_table_cell(remind_time, widths['time'])} "
        f"{_fit_table_cell(timezone, widths['timezone'])} "
        f"{_fit_table_cell(note, widths['note'])}"
    )


def _fit_table_cell(value: str, width: int, *, align: str = "left") -> str:
    value = " ".join(str(value).split())
    if len(value) > width:
        value = value[: max(0, width - 3)] + "..."
    if align == "right":
        return value.rjust(width)
    return value.ljust(width)


def _join_rich_lines(lines: list[str | Text]) -> list[str | Text]:
    joined: list[str | Text] = []
    for index, line in enumerate(lines):
        if index:
            joined.append("\n")
        joined.append(line)
    return joined


def _format_birthday_date(record: BirthdayRecord) -> str:
    year = f".{record.year}" if record.year else ""
    return f"{record.day:02d}.{record.month:02d}{year}"


def _edit_prompt(field: str, record: BirthdayRecord) -> str:
    prompts = {
        "full_name": f"Введите новое ФИО.\nСейчас: {record.full_name}",
        "birthday": "Введите новую дату рождения: DD.MM или DD.MM.YYYY.",
        "remind_time": f"Введите новое время HH:MM или '-' для 09:00.\nСейчас: {record.remind_time}",
        "remind_timezone": (
            "Введите новый часовой пояс, например Asia/Novosibirsk.\n"
            f"Сейчас: {_timezone_display(record.remind_timezone)}"
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
