# Telegram Channel Search Bot

A simple Telegram bot that saves new posts from channels where it is admin, then lets users search those posts with normal text.

## What it does

- Indexes new channel posts automatically.
- Searches across all indexed channels using keywords.
- Sends user-friendly search results with links (for public channels).

## Setup

1. Create a bot with [@BotFather](https://t.me/BotFather) and copy the token.
2. Add this bot as **admin** in each channel you want to search.
3. Install dependencies and run:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# edit .env and set TELEGRAM_TOKEN
export $(grep -v '^#' .env | xargs)
python bot.py
```

## How normal users use it

1. Open a DM with the bot.
2. Send `/start` for quick instructions.
3. Type any words to search, e.g.:
   - `invoice march`
   - `meeting notes`

The bot replies with top matching posts in a clean, readable list.

## Notes

- Telegram bots only index messages they can receive. If the bot was added later, old channel history is not auto-imported.
- Private channels do not provide public message links, but the bot still shows where the message came from.
