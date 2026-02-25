import asyncio
import os
import shlex
from pathlib import Path

from telethon import TelegramClient
from telethon.sessions import StringSession


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key or key in os.environ:
            continue

        if value:
            try:
                parsed = shlex.split(value, posix=True)
                if parsed:
                    value = parsed[0]
            except ValueError:
                pass
        os.environ[key] = value


async def main() -> None:
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    api_id_raw = os.getenv("TELETHON_API_ID")
    api_hash = os.getenv("TELETHON_API_HASH")

    if not api_id_raw or not api_hash:
        raise RuntimeError("Set TELETHON_API_ID and TELETHON_API_HASH in your environment first.")

    api_id = int(api_id_raw)
    client = TelegramClient(StringSession(), api_id, api_hash)
    await client.start()
    session_str = client.session.save()
    await client.disconnect()
    print(session_str)


if __name__ == "__main__":
    asyncio.run(main())
