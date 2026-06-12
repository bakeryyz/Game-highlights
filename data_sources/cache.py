import json
import logging
from pathlib import Path

CACHE_DIR = Path("data/game_cache")

log = logging.getLogger(__name__)


def _path(game_id: str, resource: str) -> Path:
    return CACHE_DIR / f"{game_id}_{resource}.json"


def load(game_id: str, resource: str = "game") -> dict | None:
    try:
        p = _path(game_id, resource)
        if p.exists():
            return json.loads(p.read_text())
    except Exception as e:
        log.warning(f"Cache read failed {game_id}/{resource}: {e}")
    return None


def save(game_id: str, data: dict, resource: str = "game") -> None:
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _path(game_id, resource).write_text(json.dumps(data))
    except Exception as e:
        log.warning(f"Cache write failed {game_id}/{resource}: {e}")


def clear(game_id: str, resource: str = "game") -> None:
    try:
        _path(game_id, resource).unlink(missing_ok=True)
    except Exception as e:
        log.warning(f"Cache clear failed {game_id}/{resource}: {e}")
