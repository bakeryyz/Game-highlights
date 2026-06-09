import json
from pathlib import Path

CACHE_DIR = Path("data/game_cache")


def _path(game_id: str, resource: str) -> Path:
    return CACHE_DIR / f"{game_id}_{resource}.json"


def load(game_id: str, resource: str = "game") -> dict | None:
    try:
        p = _path(game_id, resource)
        if p.exists():
            return json.loads(p.read_text())
    except Exception:
        pass
    return None


def save(game_id: str, data: dict, resource: str = "game") -> None:
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _path(game_id, resource).write_text(json.dumps(data))
    except Exception:
        pass


def clear(game_id: str, resource: str = "game") -> None:
    try:
        _path(game_id, resource).unlink(missing_ok=True)
    except Exception:
        pass
