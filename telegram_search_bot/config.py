import os
from dataclasses import dataclass
from pathlib import Path


def load_dotenv(path: Path = Path('.env')) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding='utf-8').splitlines():
        line = raw_line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        key = key.strip()
        value = value.strip()
        if key and key not in os.environ:
            os.environ[key] = value


def parse_admin_ids(raw: str) -> list[int]:
    if not raw.strip():
        return []
    values: list[int] = []
    for part in raw.split(','):
        token = part.strip()
        if not token:
            continue
        values.append(int(token))
    return values


@dataclass(slots=True)
class Config:
    bot_token: str
    admin_ids: list[int]
    tg_api_id: int | None
    tg_api_hash: str
    tg_session_name: str
    db_url: str
    search_limit: int



def load_config() -> Config:
    load_dotenv()

    bot_token = os.getenv('BOT_TOKEN', '').strip()
    if not bot_token:
        raise RuntimeError('BOT_TOKEN is required.')

    admin_ids_raw = os.getenv('ADMIN_IDS', '')
    admin_ids = parse_admin_ids(admin_ids_raw)

    tg_api_id_raw = os.getenv('TG_API_ID', '').strip()
    tg_api_id = int(tg_api_id_raw) if tg_api_id_raw else None

    tg_api_hash = os.getenv('TG_API_HASH', '').strip()
    tg_session_name = os.getenv('TG_SESSION_NAME', 'session').strip() or 'session'

    db_url = os.getenv('DB_URL', 'sqlite+aiosqlite:///search_index.db').strip()
    search_limit = int(os.getenv('SEARCH_LIMIT', '5'))

    return Config(
        bot_token=bot_token,
        admin_ids=admin_ids,
        tg_api_id=tg_api_id,
        tg_api_hash=tg_api_hash,
        tg_session_name=tg_session_name,
        db_url=db_url,
        search_limit=search_limit,
    )
