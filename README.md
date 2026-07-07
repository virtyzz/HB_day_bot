# HB Day Bot

Telegram bot for private birthday reminders.

## Features

- Stores full name, birthday date, optional birth year, reminder time, reminder timezone, and note.
- Each user sees only records created by that user.
- Bot stays silent for everyone except the admin from `.env` and users added to the whitelist by admin.
- Admin can manage the whitelist from Telegram.
- Reminder time and timezone are set separately for every birthday record.
- If reminder time or timezone is skipped while adding a record, the bot uses `09:00` and the user's saved timezone.

## Setup

1. Create a bot via BotFather and copy the token.
2. Copy `.env.example` to `.env`.
3. Fill `BOT_TOKEN` and `ADMIN_TELEGRAM_ID`.
4. Install dependencies:

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

5. Run the bot:

```bash
python -m hb_day_bot
```

## Docker Compose

1. Copy `.env.example` to `.env`.
2. Fill `BOT_TOKEN` and `ADMIN_TELEGRAM_ID`.
3. Start the bot:

```bash
docker compose up -d --build
```

The SQLite database is stored in `./data` on the host and mounted to `/app/data` in the container.

Useful commands:

```bash
docker compose logs -f bot
docker compose down
```

## Tests

```bash
python -m unittest discover -s tests
```

## Bot Menu

- `Добавить ДР` - add a birthday record.
- `Посмотреть список` - show your records as buttons. Press a record to see details.
- `Очистить список` - ask for confirmation and then delete all your records.
- `Админка` - whitelist management menu, visible only to the admin from `.env`.

Record details have inline buttons:

- `Изменить`
- `Удалить`
- `Назад`

Admin whitelist actions are also available through buttons:

- `Добавить пользователя`
- `Удалить пользователя`

Technical commands:

- `/start` - show the main menu.
- `/timezone Asia/Novosibirsk` - set your default timezone.
- `/cancel` - cancel current input flow.

## Birthday Date Format

Use `DD.MM` when the birth year is unknown, for example `21.07`.
Use `DD.MM.YYYY` when the birth year is known, for example `21.07.1994`.

## Timezone Format

Use IANA timezone names, for example:

- `Asia/Novosibirsk`
- `Europe/Moscow`
- `UTC`

You can skip reminder time and timezone during `/add`. The bot will use `09:00` and your saved timezone.
