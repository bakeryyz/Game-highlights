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

# Pitch outcome classification
_SWING_DESCS = frozenset({
    "swinging_strike", "foul", "foul_tip", "hit_into_play",
    "hit_into_play_no_out", "hit_into_play_score",
    "foul_bunt", "missed_bunt", "swinging_strike_blocked",
    "bunt_foul_tip", "foul_pitchout",
})
_WHIFF_DESCS = frozenset({
    "swinging_strike", "swinging_strike_blocked", "missed_bunt",
})


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
    n_pitches = len(df)
    if n_pitches < 5:
        return {}

    n_pa = int(df["events"].notna().sum())
    batted = df[df["launch_speed"].notna()].copy()
    n_bb = len(batted)

    result: dict = {"position_type": "B", "n_pa": n_pa, "n_pitches": n_pitches, "n_batted": n_bb}

    if n_bb >= 5:
        # ── Exit velocity ──────────────────────────────────────────
        result["avg_exit_velo"] = round(float(batted["launch_speed"].mean()), 1)
        result["max_exit_velo"] = round(float(batted["launch_speed"].max()), 1)

        # ── Launch angle ───────────────────────────────────────────
        if "launch_angle" in batted.columns and batted["launch_angle"].notna().any():
            result["avg_launch_angle"] = round(float(batted["launch_angle"].mean()), 1)

            # ── Barrels (launch_speed_angle category 6 if available) ──
            if "launch_speed_angle" in batted.columns:
                barrel_cnt = int((batted["launch_speed_angle"] == 6).sum())
            else:
                barrel_cnt = int(
                    ((batted["launch_speed"] >= 98) & batted["launch_angle"].between(26, 30)).sum()
                )
            result["barrel_count"] = barrel_cnt
            result["barrel_rate"] = round(barrel_cnt / n_bb * 100, 1)

            # ── Sweet spot (8–32° LA) ──────────────────────────────
            sweet = int(batted["launch_angle"].between(8, 32).sum())
            result["sweet_spot_rate"] = round(sweet / n_bb * 100, 1)

            # ── GB / LD / FB / PU rates ────────────────────────────
            if "bb_type" in batted.columns:
                bbt = batted["bb_type"].value_counts()
                for code, key in [("ground_ball", "gb_rate"), ("line_drive", "ld_rate"),
                                   ("fly_ball", "fb_rate"), ("popup", "pu_rate")]:
                    result[key] = round(bbt.get(code, 0) / n_bb * 100, 1)

        # ── Hard hit ───────────────────────────────────────────────
        hh_cnt = int((batted["launch_speed"] >= 95).sum())
        result["hard_hit_rate"] = round(hh_cnt / n_bb * 100, 1)

        # ── Average distance ───────────────────────────────────────
        dist_col = next((c for c in ("hit_distance_sc", "hit_distance") if c in batted.columns), None)
        if dist_col:
            dist = batted[dist_col].dropna()
            if len(dist) > 0:
                result["avg_distance"] = round(float(dist.mean()), 0)

        # ── xBA / xwOBA per batted ball ────────────────────────────
        for src, key in [("estimated_ba_using_speedangle", "avg_xba"),
                         ("estimated_woba_using_speedangle", "avg_xwoba")]:
            if src in batted.columns:
                vals = batted[src].dropna()
                if len(vals) > 0:
                    result[key] = round(float(vals.mean()), 3)

        # ── Spray direction ────────────────────────────────────────
        if "hc_x" in batted.columns and "stand" in batted.columns:
            bip = batted[batted["hc_x"].notna() & batted["stand"].notna()]
            if len(bip) > 0:
                rhb = bip[bip["stand"] == "R"]
                lhb = bip[bip["stand"] == "L"]
                pull = int(((rhb["hc_x"] < 100).sum()) + ((lhb["hc_x"] > 155).sum()))
                oppo = int(((rhb["hc_x"] > 155).sum()) + ((lhb["hc_x"] < 100).sum()))
                cent = len(bip) - pull - oppo
                result["pull_rate"]   = round(pull / len(bip) * 100, 1)
                result["center_rate"] = round(cent / len(bip) * 100, 1)
                result["oppo_rate"]   = round(oppo / len(bip) * 100, 1)

    # ── Plate discipline ───────────────────────────────────────────
    descs = df["description"] if "description" in df.columns else pd.Series(dtype=str)
    n_swings = int(descs.isin(_SWING_DESCS).sum())
    n_whiffs  = int(descs.isin(_WHIFF_DESCS).sum())
    result["swing_pct"]  = round(n_swings / n_pitches * 100, 1) if n_pitches else None
    result["whiff_pct"]  = round(n_whiffs / n_swings * 100, 1) if n_swings else None
    result["swstr_pct"]  = round(n_whiffs / n_pitches * 100, 1) if n_pitches else None

    if "zone" in df.columns:
        zone_s = df["zone"]
        n_iz = int(zone_s.between(1, 9).sum())
        n_oz = int(zone_s.between(11, 14).sum())
        result["zone_pct"] = round(n_iz / n_pitches * 100, 1) if n_pitches else None

        if n_iz > 0:
            iz_sw = int(df[zone_s.between(1, 9)]["description"].isin(_SWING_DESCS).sum())
            result["z_swing_pct"] = round(iz_sw / n_iz * 100, 1)
            iz_con = int(descs[zone_s.between(1, 9)].isin(_SWING_DESCS - _WHIFF_DESCS).sum())
            result["z_contact_pct"] = round(iz_con / iz_sw * 100, 1) if iz_sw else None
        if n_oz > 0:
            oz_sw = int(df[zone_s.between(11, 14)]["description"].isin(_SWING_DESCS).sum())
            result["chase_pct"] = round(oz_sw / n_oz * 100, 1)
            oz_con = int(descs[zone_s.between(11, 14)].isin(_SWING_DESCS - _WHIFF_DESCS).sum())
            result["o_contact_pct"] = round(oz_con / oz_sw * 100, 1) if oz_sw else None

    # ── K% / BB% ──────────────────────────────────────────────────
    if n_pa > 0:
        result["k_pct"]  = round(int((df["events"] == "strikeout").sum()) / n_pa * 100, 1)
        result["bb_pct"] = round(int(df["events"].isin(["walk", "intent_walk"]).sum()) / n_pa * 100, 1)

    # ── Pitch type breakdown (how batter performs vs each pitch) ──
    breakdown = []
    if "pitch_type" in df.columns:
        for pt, grp in df.groupby("pitch_type"):
            if not pt or str(pt).lower() in ("nan", "null", ""):
                continue
            n_pt = len(grp)
            pct = round(n_pt / n_pitches * 100, 1)
            if pct < 1:
                continue
            g_sw = int(grp["description"].isin(_SWING_DESCS).sum())
            g_wh = int(grp["description"].isin(_WHIFF_DESCS).sum())
            g_bip = grp[grp["launch_speed"].notna()]
            entry = {
                "code": str(pt), "name": pitch_type_name(str(pt)),
                "pct": pct, "n": n_pt,
                "whiff_pct": round(g_wh / g_sw * 100, 1) if g_sw else None,
                "avg_ev": round(float(g_bip["launch_speed"].mean()), 1) if len(g_bip) > 0 else None,
            }
            if "estimated_woba_using_speedangle" in grp.columns:
                xw = grp["estimated_woba_using_speedangle"].dropna()
                if len(xw) > 0:
                    entry["xwoba"] = round(float(xw.mean()), 3)
            breakdown.append(entry)
        breakdown.sort(key=lambda x: x["pct"], reverse=True)
    result["pitch_breakdown"] = breakdown

    return result


def _agg_pitcher(df: pd.DataFrame) -> dict:
    n_pitches = len(df)
    if n_pitches < 10:
        return {}

    n_pa = int(df["events"].notna().sum())
    result: dict = {"position_type": "P", "n_pitches": n_pitches, "n_pa_faced": n_pa}

    # ── Contact quality allowed ────────────────────────────────────
    batted = df[df["launch_speed"].notna()].copy()
    n_bip = len(batted)
    if n_bip > 0:
        result["avg_ev_against"] = round(float(batted["launch_speed"].mean()), 1)
        result["max_ev_against"] = round(float(batted["launch_speed"].max()), 1)
        result["hard_hit_rate"]  = round(int((batted["launch_speed"] >= 95).sum()) / n_bip * 100, 1)

        if "launch_angle" in batted.columns and batted["launch_angle"].notna().any():
            result["avg_la_against"] = round(float(batted["launch_angle"].mean()), 1)
            if "launch_speed_angle" in batted.columns:
                brl = int((batted["launch_speed_angle"] == 6).sum())
            else:
                brl = int(
                    ((batted["launch_speed"] >= 98) & batted["launch_angle"].between(26, 30)).sum()
                )
            result["barrel_rate"] = round(brl / n_bip * 100, 1)

        if "bb_type" in batted.columns:
            bbt = batted["bb_type"].value_counts()
            for code, key in [("ground_ball", "gb_rate"), ("line_drive", "ld_rate"),
                               ("fly_ball", "fb_rate"), ("popup", "pu_rate")]:
                result[key] = round(bbt.get(code, 0) / n_bip * 100, 1)

        for src, key in [("estimated_ba_using_speedangle", "avg_xba_against"),
                         ("estimated_woba_using_speedangle", "avg_xwoba_against")]:
            if src in batted.columns:
                vals = batted[src].dropna()
                if len(vals) > 0:
                    result[key] = round(float(vals.mean()), 3)

    # ── Plate discipline ───────────────────────────────────────────
    descs = df["description"] if "description" in df.columns else pd.Series(dtype=str)
    n_swings = int(descs.isin(_SWING_DESCS).sum())
    n_whiffs  = int(descs.isin(_WHIFF_DESCS).sum())
    result["swing_pct"] = round(n_swings / n_pitches * 100, 1) if n_pitches else None
    result["whiff_pct"] = round(n_whiffs / n_swings  * 100, 1) if n_swings  else None
    result["swstr_pct"] = round(n_whiffs / n_pitches * 100, 1) if n_pitches else None

    if "zone" in df.columns:
        zone_s = df["zone"]
        n_iz = int(zone_s.between(1, 9).sum())
        n_oz = int(zone_s.between(11, 14).sum())
        result["zone_pct"] = round(n_iz / n_pitches * 100, 1) if n_pitches else None
        if n_oz > 0:
            oz_sw = int(df[zone_s.between(11, 14)]["description"].isin(_SWING_DESCS).sum())
            result["chase_pct"] = round(oz_sw / n_oz * 100, 1)
        if n_iz > 0:
            iz_sw = int(df[zone_s.between(1, 9)]["description"].isin(_SWING_DESCS).sum())
            result["z_swing_pct"] = round(iz_sw / n_iz * 100, 1)

    if n_pa > 0:
        result["k_pct"]  = round(int((df["events"] == "strikeout").sum()) / n_pa * 100, 1)
        result["bb_pct"] = round(int(df["events"].isin(["walk", "intent_walk"]).sum()) / n_pa * 100, 1)

    # ── Pitch arsenal ──────────────────────────────────────────────
    arsenal = []
    if "pitch_type" in df.columns:
        for pt, grp in df.groupby("pitch_type"):
            if not pt or str(pt).lower() in ("nan", "null", ""):
                continue
            n_pt = len(grp)
            pct = round(n_pt / n_pitches * 100, 1)
            if pct < 1:
                continue

            entry: dict = {
                "code": str(pt), "name": pitch_type_name(str(pt)),
                "pct": pct, "n": n_pt,
            }

            if "release_speed" in grp.columns and grp["release_speed"].notna().any():
                entry["avg_velo"] = round(float(grp["release_speed"].mean()), 1)
                entry["max_velo"] = round(float(grp["release_speed"].max()), 1)
            if "release_spin_rate" in grp.columns and grp["release_spin_rate"].notna().any():
                entry["avg_spin"] = int(round(float(grp["release_spin_rate"].mean())))
            if "pfx_x" in grp.columns and grp["pfx_x"].notna().any():
                entry["h_break"] = round(float(grp["pfx_x"].mean()) * 12, 1)
            if "pfx_z" in grp.columns and grp["pfx_z"].notna().any():
                entry["v_break"] = round(float(grp["pfx_z"].mean()) * 12, 1)
            if "release_extension" in grp.columns and grp["release_extension"].notna().any():
                entry["extension"] = round(float(grp["release_extension"].mean()), 1)

            g_sw = int(grp["description"].isin(_SWING_DESCS).sum())
            g_wh = int(grp["description"].isin(_WHIFF_DESCS).sum())
            entry["whiff_pct"] = round(g_wh / g_sw * 100, 1) if g_sw else None

            if "zone" in grp.columns:
                n_oz = int(grp["zone"].between(11, 14).sum())
                if n_oz > 0:
                    oz_sw = int(grp[grp["zone"].between(11, 14)]["description"].isin(_SWING_DESCS).sum())
                    entry["chase_pct"] = round(oz_sw / n_oz * 100, 1)

            g_bip = grp[grp["launch_speed"].notna()]
            if len(g_bip) > 0:
                entry["avg_ev_against"] = round(float(g_bip["launch_speed"].mean()), 1)
                entry["hard_hit_pct"] = round(
                    int((g_bip["launch_speed"] >= 95).sum()) / len(g_bip) * 100, 1
                )

            if "estimated_woba_using_speedangle" in grp.columns:
                xw = grp["estimated_woba_using_speedangle"].dropna()
                if len(xw) > 0:
                    entry["xwoba"] = round(float(xw.mean()), 3)

            arsenal.append(entry)
        arsenal.sort(key=lambda x: x["pct"], reverse=True)

    result["arsenal"] = arsenal
    return result


def get_player_statcast(mlbam_id: int, year: int = _CURRENT_YEAR) -> dict:
    """
    Return comprehensive Statcast season metrics for a player.
    Includes raw aggregation + official Baseball Savant xStats + percentile ranks.
    Cached 24 hours for daily refresh.
    """
    cache_key = f"player_full_{mlbam_id}_{year}"
    cached = _cache_get(cache_key, 86400)
    if cached is not None:
        return cached

    try:
        import pybaseball as pb
        pb.cache.enable()

        result: dict = {}

        # ── Try batter first ───────────────────────────────────────
        df = pb.statcast_batter(_SEASON_START, _SEASON_END, player_id=mlbam_id)
        if df is not None and not df.empty:
            result = _agg_batter(df)

        # ── Fall back to pitcher ───────────────────────────────────
        if not result:
            df = pb.statcast_pitcher(_SEASON_START, _SEASON_END, player_id=mlbam_id)
            if df is not None and not df.empty:
                result = _agg_pitcher(df)

        if not result:
            return {}

        ptype = result.get("position_type", "B")

        # ── Merge official xStats from annual leaderboard ──────────
        try:
            if ptype == "B":
                xstats_list = get_batter_expected_stats(year)
                xmap = {r["player_id"]: r for r in xstats_list if r.get("player_id")}
                if mlbam_id in xmap:
                    xs = xmap[mlbam_id]
                    result.update({
                        "xba":        xs.get("xba"),
                        "xslg":       xs.get("xslg"),
                        "xwoba":      xs.get("xwoba"),
                        "xwoba_diff": xs.get("xwoba_diff"),
                        "xwobacon":   xs.get("xwobacon"),
                        "xba_diff":   xs.get("xba_diff"),
                        "woba":       xs.get("woba"),
                        "ba_official": xs.get("ba"),
                        "sweet_spot_rate": xs.get("sweet_spot") or result.get("sweet_spot_rate"),
                    })
            else:
                xstats_list = get_pitcher_expected_stats(year)
                xmap = {r["player_id"]: r for r in xstats_list if r.get("player_id")}
                if mlbam_id in xmap:
                    xs = xmap[mlbam_id]
                    result.update({
                        "xera":       xs.get("xera"),
                        "era":        xs.get("era"),
                        "xera_diff":  xs.get("xera_diff"),
                        "xba":        xs.get("xba"),
                        "xba_against": xs.get("xba"),
                        "xwoba":      xs.get("xwoba"),
                        "xwoba_diff": xs.get("xwoba_diff"),
                        "babip":      xs.get("babip"),
                        "xbabip":     xs.get("xbabip"),
                    })
        except Exception as e:
            log.warning(f"xStats merge for {mlbam_id}: {e}")

        # ── Merge official Savant percentile ranks ─────────────────
        try:
            if ptype == "B":
                pct_list = get_batter_percentiles(year)
            else:
                pct_list = get_pitcher_percentiles(year)
            pct_map = {r["player_id"]: r for r in pct_list if r.get("player_id")}
            if mlbam_id in pct_map:
                result["percentiles"] = pct_map[mlbam_id]
        except Exception as e:
            log.warning(f"Percentile merge for {mlbam_id}: {e}")

        _cache_set(cache_key, result)
        return result

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
# Leaderboard pages — helpers
# ---------------------------------------------------------------------------

def _safe_float(val, decimals: int = 1):
    try:
        if pd.isna(val):
            return None
        return round(float(val), decimals)
    except Exception:
        return None


def _safe_int(val):
    try:
        if pd.isna(val):
            return None
        return int(val)
    except Exception:
        return None


def _find_name_col_df(df: pd.DataFrame) -> str:
    for c in df.columns:
        if "last_name" in str(c).lower():
            return c
    return df.columns[0] if len(df.columns) > 0 else ""


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


def get_batter_expected_stats(year: int = _CURRENT_YEAR) -> list[dict]:
    """xBA, xSLG, xwOBA vs actuals for batters — shows over/under performers."""
    cache_key = f"lb_batter_xstats_{year}"
    cached = _cache_get(cache_key, 86400)
    if cached is not None:
        return cached

    try:
        import pybaseball as pb
        pb.cache.enable()
        df = pb.statcast_batter_expected_stats(year, minPA=25)
        if df is None or df.empty:
            return []

        name_col = _find_name_col_df(df)
        result = []
        for _, row in df.iterrows():
            result.append({
                "player_id": _safe_int(row.get("player_id")),
                "name": _parse_player_name(row.get(name_col, "")),
                "pa": _safe_int(row.get("pa")),
                "ba": _safe_float(row.get("ba"), 3),
                "xba": _safe_float(row.get("xba"), 3),
                "xba_diff": _safe_float(row.get("xba_minus_ba_diff"), 3),
                "slg": _safe_float(row.get("slg"), 3),
                "xslg": _safe_float(row.get("xslg"), 3),
                "xslg_diff": _safe_float(row.get("xslg_minus_slg_diff"), 3),
                "woba": _safe_float(row.get("woba"), 3),
                "xwoba": _safe_float(row.get("xwoba"), 3),
                "xwoba_diff": _safe_float(row.get("xwoba_minus_woba_diff"), 3),
                "xwobacon": _safe_float(row.get("xwobacon"), 3),
                "barrel_pct": _safe_float(row.get("barrel_batted_rate"), 1),
                "hard_hit": _safe_float(row.get("hard_hit_percent"), 1),
                "sweet_spot": _safe_float(row.get("sweet_spot_percent"), 1),
                "avg_ev": _safe_float(row.get("exit_velocity_avg"), 1),
                "avg_la": _safe_float(row.get("launch_angle_avg"), 1),
                "whiff": _safe_float(row.get("whiff_percent"), 1),
                "swing": _safe_float(row.get("swing_percent"), 1),
            })

        result.sort(key=lambda x: x.get("xwoba") or 0, reverse=True)
        _cache_set(cache_key, result)
        return result
    except Exception as e:
        log.warning(f"Batter xstats {year} failed: {e}")
        return []


def get_pitcher_expected_stats(year: int = _CURRENT_YEAR) -> list[dict]:
    """xERA, xBA/xwOBA allowed vs actuals for pitchers — identifies regression candidates."""
    cache_key = f"lb_pitcher_xstats_{year}"
    cached = _cache_get(cache_key, 86400)
    if cached is not None:
        return cached

    try:
        import pybaseball as pb
        pb.cache.enable()
        df = pb.statcast_pitcher_expected_stats(year, minPA=25)
        if df is None or df.empty:
            return []

        name_col = _find_name_col_df(df)
        result = []
        for _, row in df.iterrows():
            result.append({
                "player_id": _safe_int(row.get("player_id")),
                "name": _parse_player_name(row.get(name_col, "")),
                "pa": _safe_int(row.get("pa")),
                "era": _safe_float(row.get("era"), 2),
                "xera": _safe_float(row.get("xera"), 2),
                "xera_diff": _safe_float(row.get("xera_minus_era_diff"), 2),
                "ba": _safe_float(row.get("ba"), 3),
                "xba": _safe_float(row.get("xba"), 3),
                "xba_diff": _safe_float(row.get("xba_minus_ba_diff"), 3),
                "woba": _safe_float(row.get("woba"), 3),
                "xwoba": _safe_float(row.get("xwoba"), 3),
                "xwoba_diff": _safe_float(row.get("xwoba_minus_woba_diff"), 3),
                "babip": _safe_float(row.get("babip"), 3),
                "xbabip": _safe_float(row.get("xbabip"), 3),
                "hard_hit": _safe_float(row.get("hard_hit_percent"), 1),
                "barrel_pct": _safe_float(row.get("barrel_batted_rate"), 1),
                "sweet_spot": _safe_float(row.get("sweet_spot_percent"), 1),
                "avg_ev": _safe_float(row.get("exit_velocity_avg"), 1),
                "whiff": _safe_float(row.get("whiff_percent"), 1),
            })

        result.sort(key=lambda x: x.get("xera") or 99)
        _cache_set(cache_key, result)
        return result
    except Exception as e:
        log.warning(f"Pitcher xstats {year} failed: {e}")
        return []


def get_batter_percentiles(year: int = _CURRENT_YEAR) -> list[dict]:
    """Baseball Savant-style percentile ranks (0–100) for batters. Higher = better for all metrics."""
    cache_key = f"lb_batter_pct_{year}"
    cached = _cache_get(cache_key, 86400)
    if cached is not None:
        return cached

    try:
        import pybaseball as pb
        pb.cache.enable()
        df = pb.statcast_batter_percentile_ranks(year)
        if df is None or df.empty:
            return []

        result = []
        for _, row in df.iterrows():
            result.append({
                "player_id": _safe_int(row.get("player_id")),
                "name": str(row.get("player_name", "")).strip(),
                "team": str(row.get("team", "")).strip(),
                "xba": _safe_int(row.get("xba")),
                "xslg": _safe_int(row.get("xslg")),
                "xwoba": _safe_int(row.get("xwoba")),
                "xwobacon": _safe_int(row.get("xwobacon")),
                "exit_velocity": _safe_int(row.get("exit_velocity")),
                "brl_percent": _safe_int(row.get("brl_percent")),
                "hard_hit": _safe_int(row.get("hard_hit_percent")),
                "sprint_speed": _safe_int(row.get("sprint_speed")),
                "k_pct": _safe_int(row.get("k_percent")),
                "bb_pct": _safe_int(row.get("bb_percent")),
                "whiff": _safe_int(row.get("whiff_percent")),
                "sweet_spot": _safe_int(row.get("sweet_spot_percent")),
            })

        result.sort(key=lambda x: x.get("xwoba") or 0, reverse=True)
        _cache_set(cache_key, result)
        return result
    except Exception as e:
        log.warning(f"Batter percentiles {year} failed: {e}")
        return []


def get_pitcher_percentiles(year: int = _CURRENT_YEAR) -> list[dict]:
    """Baseball Savant-style percentile ranks (0–100) for pitchers. Higher = better for all metrics."""
    cache_key = f"lb_pitcher_pct_{year}"
    cached = _cache_get(cache_key, 86400)
    if cached is not None:
        return cached

    try:
        import pybaseball as pb
        pb.cache.enable()
        df = pb.statcast_pitcher_percentile_ranks(year)
        if df is None or df.empty:
            return []

        result = []
        for _, row in df.iterrows():
            result.append({
                "player_id": _safe_int(row.get("player_id")),
                "name": str(row.get("player_name", "")).strip(),
                "team": str(row.get("team", "")).strip(),
                "xba": _safe_int(row.get("xba")),
                "xslg": _safe_int(row.get("xslg")),
                "xwoba": _safe_int(row.get("xwoba")),
                "xwobacon": _safe_int(row.get("xwobacon")),
                "exit_velocity": _safe_int(row.get("exit_velocity")),
                "brl_percent": _safe_int(row.get("brl_percent")),
                "hard_hit": _safe_int(row.get("hard_hit_percent")),
                "k_pct": _safe_int(row.get("k_percent")),
                "bb_pct": _safe_int(row.get("bb_percent")),
                "whiff": _safe_int(row.get("whiff_percent")),
                "meatball_swing": _safe_int(row.get("meatball_swing_percent")),
                "chase": _safe_int(row.get("oz_swing_percent")),
            })

        result.sort(key=lambda x: x.get("xwoba") or 0, reverse=True)
        _cache_set(cache_key, result)
        return result
    except Exception as e:
        log.warning(f"Pitcher percentiles {year} failed: {e}")
        return []


def build_batter_statcast_map(year: int = _CURRENT_YEAR) -> dict[int, dict]:
    """
    Merge all batter Statcast sources into a single lookup by player_id.

    Sources combined:
      get_batter_expected_stats() → wOBA, xwOBA (when available), BA, SLG
      get_batter_leaderboard()    → barrel_pct, avg_ev, hard_hit (actual values)
      get_speed_leaderboard()     → sprint_speed in ft/s
      get_batter_percentiles()    → k_pct/bb_pct/xwOBA as 0-100 Savant ranks
    """
    merged: dict[int, dict] = {}

    for r in get_batter_expected_stats(year):
        pid = r.get("player_id")
        if pid:
            merged[pid] = dict(r)

    for r in get_batter_leaderboard(year):
        pid = r.get("player_id")
        if pid:
            entry = merged.setdefault(pid, {"player_id": pid, "name": r.get("name")})
            for k in ("barrel_pct", "avg_ev", "hard_hit"):
                if r.get(k) is not None:
                    entry[k] = r[k]

    for r in get_speed_leaderboard(year):
        pid = r.get("player_id")
        if pid:
            entry = merged.setdefault(pid, {"player_id": pid, "name": r.get("name")})
            if r.get("sprint_speed") is not None:
                entry["sprint_speed"] = r["sprint_speed"]

    for r in get_batter_percentiles(year):
        pid = r.get("player_id")
        if pid:
            entry = merged.setdefault(pid, {"player_id": pid, "name": r.get("name", "")})
            # Savant percentile ranks (0-100) — store with _rank suffix
            for k in ("xwoba", "brl_percent", "hard_hit", "sprint_speed",
                      "k_pct", "bb_pct", "whiff", "exit_velocity"):
                if r.get(k) is not None:
                    entry[f"{k}_rank"] = r[k]
            # xwOBA rank can proxy for xwOBA when actual value is missing
            if r.get("xwoba") is not None and entry.get("xwoba") is None:
                # Convert percentile rank → approximate xwOBA
                # League range ~.250-.450; 50th pct ≈ .315 (avg)
                entry["xwoba"] = round(0.250 + (r["xwoba"] / 100) * 0.200, 3)

    return merged


def build_pitcher_statcast_map(year: int = _CURRENT_YEAR) -> dict[int, dict]:
    """
    Merge all pitcher Statcast sources into a single lookup by player_id.

    Sources combined:
      get_pitcher_expected_stats() → xERA, ERA, xwOBA allowed, barrel_pct
      get_pitcher_leaderboard()    → avg_ev against, hard_hit against
      get_pitcher_percentiles()    → K%, BB%, whiff% as Savant ranks
    """
    merged: dict[int, dict] = {}

    for r in get_pitcher_expected_stats(year):
        pid = r.get("player_id")
        if pid:
            merged[pid] = dict(r)

    for r in get_pitcher_leaderboard(year):
        pid = r.get("player_id")
        if pid:
            entry = merged.setdefault(pid, {"player_id": pid, "name": r.get("name")})
            for k in ("barrel_pct", "avg_ev", "hard_hit"):
                if r.get(k) is not None:
                    entry[k] = r[k]

    for r in get_pitcher_percentiles(year):
        pid = r.get("player_id")
        if pid:
            entry = merged.setdefault(pid, {"player_id": pid, "name": r.get("name", "")})
            for k in ("k_pct", "bb_pct", "whiff", "xwoba", "brl_percent",
                      "hard_hit", "exit_velocity"):
                if r.get(k) is not None:
                    entry[f"{k}_rank"] = r[k]

    return merged


def get_statcast_context_for_roster(players: list) -> str:
    """
    Build a Statcast section string for the AI context.
    players: list of objects with .name, .mlbam_id, .lineup_slot
    Returns a formatted markdown section with regression / luck analysis.
    """
    batter_map = {r["player_id"]: r for r in get_batter_expected_stats() if r.get("player_id")}
    pitcher_map = {r["player_id"]: r for r in get_pitcher_expected_stats() if r.get("player_id")}

    lines = [
        "## STATCAST DATA (Baseball Savant — Expected Stats)",
        "Use these to identify players likely to improve (unlucky) or regress (lucky):",
        "",
    ]

    found_any = False
    for p in players:
        if not p.mlbam_id:
            continue
        mid = int(p.mlbam_id)
        is_pitcher = p.lineup_slot in ("SP", "RP", "P")

        if is_pitcher and mid in pitcher_map:
            found_any = True
            d = pitcher_map[mid]
            era = d.get("era")
            xera = d.get("xera")
            diff = d.get("xera_diff")  # xERA minus ERA (positive = pitcher is outperforming)
            line = f"**{p.name}** (P): ERA {era if era is not None else '—'} / xERA {xera if xera is not None else '—'}"
            line += f" | wOBA against {d.get('woba','—')} / xwOBA {d.get('xwoba','—')}"
            line += f" | Hard Hit% {d.get('hard_hit','—')} | Barrel% {d.get('barrel_pct','—')}"
            lines.append(line)
            if diff is not None:
                if diff > 0.5:
                    lines.append(f"  ⚠️  xERA is {diff:.2f} HIGHER than ERA → pitcher is LUCKY, ERA likely to rise")
                elif diff < -0.5:
                    lines.append(f"  ✅ xERA is {abs(diff):.2f} LOWER than ERA → pitcher is UNLUCKY, ERA likely to drop")

        elif not is_pitcher and mid in batter_map:
            found_any = True
            d = batter_map[mid]
            diff = d.get("xwoba_diff")  # xwOBA minus wOBA (positive = batter is underperforming)
            line = f"**{p.name}** (B): BA {d.get('ba','—')} / xBA {d.get('xba','—')}"
            line += f" | wOBA {d.get('woba','—')} / xwOBA {d.get('xwoba','—')}"
            line += f" | Barrel% {d.get('barrel_pct','—')} | Hard Hit% {d.get('hard_hit','—')} | Sweet Spot% {d.get('sweet_spot','—')}"
            lines.append(line)
            if diff is not None:
                if diff > 0.020:
                    lines.append(f"  ✅ xwOBA is {diff:.3f} ABOVE actual wOBA → batter is UNLUCKY/underperforming, expect improvement")
                elif diff < -0.020:
                    lines.append(f"  ⚠️  xwOBA is {abs(diff):.3f} BELOW actual wOBA → batter is LUCKY/overperforming, may regress")

    if not found_any:
        lines.append("(No Statcast data found for current roster players)")

    return "\n".join(lines)
