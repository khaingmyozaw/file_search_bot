# Telegram Channel Search Bot

A Telegram bot that indexes channel posts and lets users search with plain text.

## Features

- Auto-indexes new posts from channels where the bot is admin.
- Supports admin-triggered historical backfill with `/sync_channel`.
- Searches indexed posts with keyword queries.

## Setup

1. Create a bot with [@BotFather](https://t.me/BotFather) and copy token.
2. Add the bot as **admin** to channels you want indexed.
3. Get `api_id` and `api_hash` from https://my.telegram.org (for MTProto history sync).
4. Fill `.env`:
   - `TELEGRAM_TOKEN`
   - `OWNER_USER_ID` (only this user can run `/sync_channel`)
   - `TELETHON_API_ID`
   - `TELETHON_API_HASH`
   - `TELETHON_STRING_SESSION` (user account session; required for history sync)
5. Install and run:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python bot.py
```

Generate `TELETHON_STRING_SESSION` once (interactive login as your Telegram user):

```bash
source .venv/bin/activate
export TELETHON_API_ID=your_api_id
export TELETHON_API_HASH=your_api_hash
python scripts/generate_telethon_string_session.py
```

Copy the printed value into `.env` as `TELETHON_STRING_SESSION=...`.

## Commands

- `/start`
- `/help`
- `/channels`
- `/sync_channel <channel_username_or_id> [limit]`
- `/purge_channel <channel_username_or_id>` (owner-only)
- `/delete_channel <channel_username_or_id>` (owner-only)

Example:

```text
/sync_channel my_public_channel 2000
```

This imports up to 2000 older messages from that channel into `search_index.db`.

## Notes

- `/sync_channel` is owner-only (`OWNER_USER_ID`).
- `/purge_channel` is owner-only (`OWNER_USER_ID`) and permanently deletes indexed history for that channel.
- `/delete_channel` is owner-only (`OWNER_USER_ID`) and permanently deletes indexed history for that channel.
- `/sync_channel` uses a user-authorized Telethon session, not bot auth.
- For private channels, that user account must have access.

## Admin Channel Delete Flow (telegram_search_bot)

If you are using the aiogram admin bot inside `telegram_search_bot/`, `/remove_channel <identifier>` now supports safe deletion behavior:

- Accessible channel: removed from active tracking, history kept for search.
- Deleted/inaccessible channel: bot shows admin options to either keep history or delete all indexed history.
