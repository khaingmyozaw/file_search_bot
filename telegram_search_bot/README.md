# Telegram Search Bot

Simple Telegram bot that indexes messages from multiple channels and lets users search them.

## Features

- User search with `/search keyword` or plain text.
- Optional media filtering with `/search media:<type> keyword`.
- Quick media commands: `/photos`, `/videos`, `/documents`, `/audio`.
- Case-insensitive search over text, caption, and file name.
- Forwards original matching channel messages.
- Inline pagination with `Prev`/`Next` result navigation.
- Admin commands to add/remove/list channels, sync old history, and show stats.
- Safe channel removal flow with optional history purge when a channel is deleted/inaccessible.
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
- `/purge_channel <identifier>`
- `/channels`
- `/sync_channel <identifier>`
- `/stats`

`/remove_channel` behavior:

- If the channel is still accessible, it is removed from tracking and its indexed history is kept.
- If the channel is deleted/inaccessible, the bot shows admin buttons:
  - `Keep history only` (stop tracking but keep searchable history)
  - `Delete all history` (remove channel and all indexed messages)

`/purge_channel` behavior:

- Admin-only hard delete for channel + all indexed messages.

## User Commands

- `/help`
- `/search <keyword>`
- `/search media:<photo|video|document|audio> <keyword>`
- `/photos <keyword>`
- `/videos <keyword>`
- `/documents <keyword>`
- `/audio <keyword>`
- `/channels` (view searchable channel list)

## User Search Examples

- `/search invoice`
- `/search media:photo launch`
- `/documents report`
- `error code 500` (plain text search)

## Notes

- `channel_post` indexing requires the bot to be admin in each channel.
- Channels marked as history-only are not indexed in real time until re-added.
- `/sync_channel` requires a Telethon user session:
  first run will ask for Telegram login code in terminal and save session as `TG_SESSION_NAME.session`.
