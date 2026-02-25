# Telegram Search Bot

Simple Telegram bot that indexes messages from multiple channels and lets users search them.

## Features

- User search with `/search keyword` or plain text.
- Case-insensitive search over text, caption, and file name.
- Forwards original matching channel messages.
- Admin commands to add/remove/list channels, sync old history, and show stats.
- Real-time indexing from `channel_post` updates.
- Old history backfill via Telethon in the same process.

## Project Structure

```text
telegram_search_bot/
    main.py
    config.py
    db.py
    models.py
    search.py
    admin.py
    indexer.py
    requirements.txt
    README.md
```

## Environment Variables

Create `.env` in `telegram_search_bot/`:

```env
BOT_TOKEN=
ADMIN_IDS=12345,67890
TG_API_ID=
TG_API_HASH=
TG_SESSION_NAME=session
```

Optional:

```env
DB_URL=sqlite+aiosqlite:///search_index.db
SEARCH_LIMIT=5
```

## Setup and Run

1. Create your bot with [@BotFather](https://t.me/BotFather).
2. Add the bot to your channels as admin.
3. Create Telegram API ID/HASH at https://my.telegram.org.
4. Install dependencies:

```bash
pip install -r requirements.txt
```

5. Run:

```bash
python main.py
```

## Admin Commands

- `/admin`
- `/add_channel <@username or -100id>`
- `/remove_channel <identifier>`
- `/channels`
- `/sync_channel <identifier>`
- `/stats`

## Notes

- `channel_post` indexing requires the bot to be admin in each channel.
- `/sync_channel` requires a Telethon user session:
  first run will ask for Telegram login code in terminal and save session as `TG_SESSION_NAME.session`.
