# Telegram Channel Search Bot

A Telegram bot that indexes channel posts and lets users search with plain text.

## Main goals

- **Easy for normal users**: they only type keywords in bot chat.
- **Safe for bot owner**: only trusted bot managers can approve channels.
- **Old data sync support**: managers can forward old channel posts to import history.

## Setup

1. Create a bot with [@BotFather](https://t.me/BotFather) and copy token.
2. Get your Telegram numeric user ID (this will be `OWNER_USER_ID`).
3. Install and run:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# set TELEGRAM_TOKEN and OWNER_USER_ID in .env
export $(grep -v '^#' .env | xargs)
python bot.py
```

## Manager workflow (security)

Only `OWNER_USER_ID` can add/remove bot managers.

- `/add_admin <user_id>`
- `/remove_admin <user_id>`
- `/list_admins`

Managers can approve/revoke channels:

- `/allow_channel <channel_id>`
- `/remove_channel <channel_id>`
- `/list_channels`

> This protects the bot from random people adding it as admin in unknown channels and polluting your index.

## Normal user workflow

1. Open bot private chat.
2. Send `/start`.
3. Search with simple words, for example:
   - `invoice march`
   - `meeting notes`

## Old data sync (history)

Telegram bots cannot auto-read old channel history via API.

To sync older posts:

1. Manager approves the channel with `/allow_channel <channel_id>`.
2. Manager forwards old posts from that channel to bot private chat.
3. Bot indexes each forwarded old post.

Use `/sync_help` for reminder inside bot.
