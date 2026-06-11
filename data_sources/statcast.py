"""
Baseball Savant / Statcast data layer.

All heavy pybaseball calls go through a local JSON cache so repeated requests
are fast and pybaseball's own per-call cache is also enabled.
"""

import json
import logging
import time
from pathlib import Path

import pandas as pd

CACHE_DIR = Path("data/statcast_cache")
_SEASON_START = "2026-03-20"
_SEASON_END = "2026-11-01"
_CURRENT_YEAR = 2026

log = logging.getLogger(__name__)

_PITCH_TYPE_NAMES = {
    "FF": "4-Seam Fastball", "SI": "Sinker", "FC": "Cutter",
    "SL": "Slider", "CH": "Changeup", "CU": "Curveball",
    "KC": "Knuckle Curve", "ST": "Sweeper", "SV": "Slurve",
    "FS": "Splitter", "FO": "Forkball", "KN": "Knuckleball",
    "CS": "Slow Curve", "SC": "Screwball", "EP": "Eephus",
}


def pitch_type_name(code: str) -> str:
    return _PITCH_TYPE_NAMES.get(code, code)


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------

def _cache_path(key: str) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR / f"{key}.json"


def _cache_get(key: str, ttl: int):
    p = _cache_path(key)
    if p.exists():
        try:
            raw = json.loads(p.read_text())
            if time.time() - raw.get("_at", 0) < ttl:
                return raw["data"]
        except Exception:
            pass
    return None


def _cache_set(key: str, data) -> None:
    try:
        _cache_path(key).write_text(json.dumps({"_at": time.time(), "data": data}))
    except Exception as e:
        log.warning(f"Cache write failed {key}: {e}")


def _safe_records(df: pd.DataFrame) -> list[dict]:
    """DataFrame → JSON-serializable list of dicts."""
    return json.loads(df.to_json(orient="records"))


# ---------------------------------------------------------------------------
# Game-level Statcast enrichment
# ---------------------------------------------------------------------------

def get_game_statcast(game_pk: int) -> dict:
    """
    Fetch Statcast data for a single game.
    Returns a dict keyed by str((batter_id, inning, half, event_type)) with
    batted-ball / pitch metrics for each terminal play.
    """
    cache_key = f"game_{game_pk}"
    cached = _cache_get(cache_key, 300)
    if cached is not None:
        return cached

    try:
        import pybaseball as pb
        pb.cache.enable()
        df = pb.statcast_single_game(game_pk)

        if df is None or df.empty:
            return {}

        terminal = df[df["events"].notna() & (df["events"] != "")]
        result = {}

        for _, row in terminal.iterrows():
            bid = row.get("batter")
            if pd.isna(bid):
                continue
            inning = int(row["inning"]) if pd.notna(row.get("inning")) else 0
            half = "top" if str(row.get("inning_topbot", "Top")).startswith("T") else "bottom"
            event = str(row.get("events", "")).lower().replace(" ", "_")
            key = str((int(bid), inning, half, event))
            result[key] = {
                "launch_speed": float(row["launch_speed"]) if pd.notna(row.get("launch_speed")) else None,
                "launch_angle": float(row["launch_angle"]) if pd.notna(row.get("launch_angle")) else None,
                "hit_distance": int(row["hit_distance_sc"]) if pd.notna(row.get("hit_distance_sc")) else None,
                "pitch_speed": float(row["release_speed"]) if pd.notna(row.get("release_speed")) else None,
                "pitch_type": str(row["pitch_type"]) if pd.notna(row.get("pitch_type")) else None,
            }

        _cache_set(cache_key, result)
        return result

    except Exception as e:
        log.warning(f"Statcast game {game_pk} failed: {e}")
        return {}


def enrich_plays(game_pk: int, plays: list) -> None:
    """Attach Statcast metrics to Play objects in-place. Silent on failure."""
    sc = get_game_statcast(int(game_pk))
    if not sc:
        return
    for play in plays:
        if not play.batter_id:
            continue
        key = str((play.batter_id, play.inning, play.half, play.event_type))
        m = sc.get(key)
        if m:
            play.launch_speed = m.get("launch_speed")
            play.launch_angle = m.get("launch_angle")
            play.hit_distance = m.get("hit_distance")
            play.pitch_speed = m.get("pitch_speed")
            play.pitch_type = m.get("pitch_type")


# ---------------------------------------------------------------------------
# Player season Statcast
# ---------------------------------------------------------------------------

def _agg_batter(df: pd.DataFrame) -> dict:
    batted = df[df["launch_speed"].notna()]
    n_bb = len(batted)
    if n_bb < 5:
        return {}

    n_pa = int(df["events"].notna().sum())
    barrels = batted[
        (batted["launch_speed"] >= 98) & (batted["launch_angle"].between(26, 30))
    ]
    hard_hit = batted[batted["launch_speed"] >= 95]

    return {
        "position_type": "B",
        "avg_exit_velo": round(float(batted["launch_speed"].mean()), 1),
        "max_exit_velo": round(float(batted["launch_speed"].max()), 1),
        "avg_launch_angle": round(float(batted["launch_angle"].mean()), 1),
        "barrel_rate": round(len(barrels) / n_bb * 100, 1),
        "hard_hit_rate": round(len(hard_hit) / n_bb * 100, 1),
        "n_batted": n_bb,
        "n_pa": n_pa,
    }


def _agg_pitcher(df: pd.DataFrame) -> dict:
    total = len(df)
    if total < 10:
        return {}

    arsenal = []
    if "pitch_type" in df.columns and "release_speed" in df.columns:
        for pt, grp in df.groupby("pitch_type"):
            if not pt or str(pt).lower() in ("nan", "null", ""):
                continue
            pct = round(len(grp) / total * 100, 1)
            if pct < 1:
                continue
            avg_velo = round(float(grp["release_speed"].mean()), 1) if grp["release_speed"].notna().any() else None
            spin_col = "release_spin_rate"
            avg_spin = (
                int(round(float(grp[spin_col].mean())))
                if spin_col in grp.columns and grp[spin_col].notna().any()
                else None
            )
            arsenal.append({
                "code": str(pt),
                "name": pitch_type_name(str(pt)),
                "pct": pct,
                "avg_velo": avg_velo,
                "avg_spin": avg_spin,
            })
        arsenal.sort(key=lambda x: x["pct"], reverse=True)

    batted = df[df["launch_speed"].notna()]
    avg_ev = round(float(batted["launch_speed"].mean()), 1) if len(batted) > 0 else None

    return {
        "position_type": "P",
        "arsenal": arsenal,
        "avg_ev_against": avg_ev,
        "total_pitches": total,
    }


def get_player_statcast(mlbam_id: int, year: int = _CURRENT_YEAR) -> dict:
    """Return aggregated Statcast season metrics for a player. Tries batter then pitcher."""
    cache_key = f"player_{mlbam_id}_{year}"
    cached = _cache_get(cache_key, 3600)
    if cached is not None:
        return cached

    try:
        import pybaseball as pb
        pb.cache.enable()

        df = pb.statcast_batter(_SEASON_START, _SEASON_END, player_id=mlbam_id)
        if df is not None and not df.empty:
            result = _agg_batter(df)
            if result:
                _cache_set(cache_key, result)
                return result

        df = pb.statcast_pitcher(_SEASON_START, _SEASON_END, player_id=mlbam_id)
        if df is not None and not df.empty:
            result = _agg_pitcher(df)
            if result:
                _cache_set(cache_key, result)
                return result

        return {}

    except Exception as e:
        log.warning(f"Statcast player {mlbam_id} failed: {e}")
        return {}


# ---------------------------------------------------------------------------
# Percentile rankings
# ---------------------------------------------------------------------------

def _get_leaderboard(year: int, position_type: str) -> list[dict]:
    cache_key = f"lb_{position_type}_{year}"
    cached = _cache_get(cache_key, 86400)
    if cached is not None:
        return cached

    try:
        import pybaseball as pb
        pb.cache.enable()
        if position_type == "B":
            df = pb.statcast_batter_exitvelo_barrels(year, minBBE=10)
        else:
            df = pb.statcast_pitcher_exitvelo_barrels(year, minBBE=10)

        if df is None or df.empty:
            return []
        records = _safe_records(df)
        _cache_set(cache_key, records)
        return records
    except Exception as e:
        log.warning(f"Leaderboard {position_type} {year} failed: {e}")
        return []


def _pct_rank(values: list, value: float, lower_is_better: bool = False) -> int | None:
    if value is None or not values:
        return None
    n_below = sum(1 for v in values if v < value)
    rank = int(round(n_below / len(values) * 100))
    return (100 - rank) if lower_is_better else rank


def compute_percentiles(mlbam_id: int, metrics: dict, year: int = _CURRENT_YEAR) -> dict:
    """Return dict of metric_name → percentile int (0–100)."""
    ptype = metrics.get("position_type", "B")
    leaders = _get_leaderboard(year, ptype)
    if not leaders:
        return {}

    result = {}
    if ptype == "B":
        ev_vals = [r["avg_hit_speed"] for r in leaders if r.get("avg_hit_speed") is not None]
        brl_vals = [r["brl_percent"] for r in leaders if r.get("brl_percent") is not None]
        hh_vals = [r["ev95percent"] for r in leaders if r.get("ev95percent") is not None]
        result["exit_velocity"] = _pct_rank(ev_vals, metrics.get("avg_exit_velo"))
        result["barrel_rate"] = _pct_rank(brl_vals, metrics.get("barrel_rate"))
        result["hard_hit_rate"] = _pct_rank(hh_vals, metrics.get("hard_hit_rate"))
    else:
        ev_vals = [r["avg_hit_speed"] for r in leaders if r.get("avg_hit_speed") is not None]
        result["ev_against"] = _pct_rank(ev_vals, metrics.get("avg_ev_against"), lower_is_better=True)

    return result


# ---------------------------------------------------------------------------
# Leaderboard pages
# ---------------------------------------------------------------------------

def _parse_player_name(raw) -> str:
    """Parse 'Last, First' → 'First Last'."""
    raw = str(raw).strip()
    if "," in raw:
        last, first = raw.split(",", 1)
        return f"{first.strip()} {last.strip()}"
    return raw


def _find_name_col(row: dict) -> str:
    for key in row:
        if "last_name" in str(key).lower():
            return key
    return ""


def get_batter_leaderboard(year: int = _CURRENT_YEAR) -> list[dict]:
    raw = _get_leaderboard(year, "B")
    result = []
    for r in raw:
        name_key = _find_name_col(r)
        result.append({
            "player_id": r.get("player_id"),
            "name": _parse_player_name(r.get(name_key, "")),
            "attempts": r.get("attempts"),
            "avg_ev": r.get("avg_hit_speed"),
            "max_ev": r.get("max_hit_speed"),
            "hard_hit": r.get("ev95percent"),
            "barrel_pct": r.get("brl_percent"),
            "avg_dist": r.get("avg_distance"),
        })
    result.sort(key=lambda x: x.get("avg_ev") or 0, reverse=True)
    return result


def get_pitcher_leaderboard(year: int = _CURRENT_YEAR) -> list[dict]:
    raw = _get_leaderboard(year, "P")
    result = []
    for r in raw:
        name_key = _find_name_col(r)
        result.append({
            "player_id": r.get("player_id"),
            "name": _parse_player_name(r.get(name_key, "")),
            "attempts": r.get("attempts"),
            "avg_ev": r.get("avg_hit_speed"),
            "max_ev": r.get("max_hit_speed"),
            "hard_hit": r.get("ev95percent"),
            "barrel_pct": r.get("brl_percent"),
        })
    result.sort(key=lambda x: x.get("avg_ev") or 999)
    return result


def get_speed_leaderboard(year: int = _CURRENT_YEAR) -> list[dict]:
    cache_key = f"lb_speed_{year}"
    cached = _cache_get(cache_key, 86400)
    if cached is not None:
        return cached

    try:
        import pybaseball as pb
        pb.cache.enable()
        df = pb.statcast_sprint_speed(year, minOF=5)
        if df is None or df.empty:
            return []

        name_col = next((c for c in df.columns if "last_name" in c.lower()), None)
        result = []
        for _, row in df.iterrows():
            name = _parse_player_name(row[name_col]) if name_col else "Unknown"
            result.append({
                "player_id": int(row["player_id"]) if pd.notna(row.get("player_id")) else None,
                "name": name,
                "sprint_speed": round(float(row["sprint_speed"]), 1) if pd.notna(row.get("sprint_speed")) else None,
                "hp_to_1b": round(float(row["hp_to_1b"]), 2) if pd.notna(row.get("hp_to_1b")) else None,
                "n": int(row["n"]) if pd.notna(row.get("n")) else None,
            })

        result.sort(key=lambda x: x.get("sprint_speed") or 0, reverse=True)
        _cache_set(cache_key, result)
        return result
    except Exception as e:
        log.warning(f"Speed leaderboard {year} failed: {e}")
        return []
