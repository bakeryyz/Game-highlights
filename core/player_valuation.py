"""
Advanced Statcast-based player valuation system.

METHODOLOGY
-----------
1. Project stats from Statcast rate metrics (xwOBA, barrel%, K%, BB%,
   sprint_speed, whiff%) — not from box-score actuals. Statcast rates
   stabilize in ~50 PA and are immune to BABIP luck, strand rate variance,
   and defensive positioning. Counting stats need 400+ PA to stabilize.

2. Blend that Statcast projection with actual season stats, weighted by
   sample size. Early season (few PA): trust Statcast. Late season (many
   PA): trust actuals more, but never fully (sample noise always exists).

3. Project the blended rates to a common baseline (600 PA for batters,
   180 IP for SP, 65 IP for RP) to make players comparable.

4. Multiply projected stats by YOUR league's exact scoring weights → PPG.

5. Break down value by component (power, speed, contact, run_production,
   discipline for batters; K, era_quality, role for pitchers) so you see
   exactly what drives each player's value.

Statcast inputs ranked by predictive value:
  Batters:  xwOBA >> barrel% > K%/BB% > sprint_speed > hard_hit% > whiff%
  Pitchers: xERA  >> K%/whiff% > barrel_against > BB%

External API (unchanged so app.py / fantasy_context.py don't break):
  value_batter(season_stats, statcast, settings) -> ValuationResult | None
  value_pitcher(season_stats, statcast, settings, is_sp) -> ValuationResult | None
  scoring_settings_from_yahoo(modifiers) -> ScoringSettings
  assign_percentiles([(name, ValuationResult)]) -> None
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# Yahoo stat_id -> scoring key (used by scoring_settings_from_yahoo)
# ---------------------------------------------------------------------------

YAHOO_STAT_ID_TO_KEY: dict[str, str] = {
    "7":  "runs",
    "9":  "singles",
    "10": "doubles",
    "11": "triples",
    "12": "homeRuns",
    "13": "rbi",
    "16": "stolenBases",
    "33": "inningsPitched",    # Yahoo = pts per out; we multiply ×3 → per IP
    "42": "strikeouts_pitched",
    "37": "earnedRuns",
    "34": "hits_allowed",
    "39": "walks_allowed",
    "28": "wins",
    "32": "saves",
}

# ---------------------------------------------------------------------------
# League-average baselines (2024 MLB)
# ---------------------------------------------------------------------------

_MLB_AVG = {
    "xwoba":    0.315,
    "xba":      0.248,
    "xslg":     0.400,
    "barrel":   6.5,     # barrel% (per BIP)
    "k_pct":    22.5,    # K%
    "bb_pct":   8.5,     # BB%
    "hard_hit": 38.0,    # hard hit%
    "whiff":    23.0,    # whiff%
    "sprint":   27.0,    # ft/s
    "xera":     4.20,
}

# Full-season baselines per position (600 PA equiv / 180 IP equiv)
_BASELINE_R_PER_600   = 75.0
_BASELINE_RBI_PER_600 = 75.0


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class ValueBreakdown:
    """What drives a batter's or pitcher's PPG."""
    # Batters
    power:          float = 0.0   # HR + XBH scoring contribution
    speed:          float = 0.0   # SB contribution
    contact:        float = 0.0   # 1B + hit-dependent stats
    run_production: float = 0.0   # R + RBI contribution
    discipline:     float = 0.0   # BB bonus - K penalty combined
    # Pitchers
    k_value:        float = 0.0   # K contribution
    era_quality:    float = 0.0   # ERA/WHIP quality (ER, H, BB allowed)
    role_value:     float = 0.0   # W + SV + IP contribution
    # Total
    total_ppg:      float = 0.0


@dataclass
class ValuationResult:
    ppg: float
    breakdown: ValueBreakdown = field(default_factory=ValueBreakdown)

    # Regression signal — drives buy/sell advice
    regression_pct: float = 0.0    # +% = underperforming true talent (buy), -% = lucky (sell)

    # Key Statcast inputs that drove this valuation
    xwoba:       Optional[float] = None
    woba:        Optional[float] = None
    xera:        Optional[float] = None
    era:         Optional[float] = None
    barrel_pct:  Optional[float] = None
    sprint_speed: Optional[float] = None
    k_pct:       Optional[float] = None
    bb_pct:      Optional[float] = None

    # Meta
    statcast_weight: float = 0.5    # 0=all actuals, 1=all Statcast
    confidence: str = "MEDIUM"      # HIGH / MEDIUM / LOW — based on PA sample
    percentile: Optional[int] = None

    # Compatibility shim — old callers use val.pts_per_pa / val.pts_per_ip
    pts_per_pa: Optional[float] = None
    pts_per_ip: Optional[float] = None

    def signal_label(self) -> str:
        if abs(self.regression_pct) < 3:
            return "Neutral"
        if self.regression_pct > 0:
            return f"+{self.regression_pct:.1f}% upside (buy-low)"
        return f"{self.regression_pct:.1f}% downside (sell-high)"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def scoring_settings_from_yahoo(modifiers: list):
    """Build a ScoringSettings from Yahoo /league/settings stat_modifiers."""
    from core.scoring import ScoringSettings, DEFAULT_WEIGHTS

    weights = dict(DEFAULT_WEIGHTS)
    for item in modifiers:
        try:
            sid = str(item["stat"]["stat_id"])
            val = float(item["stat"]["value"])
            key = YAHOO_STAT_ID_TO_KEY.get(sid)
            if key:
                weights[key] = val * 3 if key == "inningsPitched" else val
        except Exception:
            continue
    return ScoringSettings(weights=weights, name="yahoo_live")


def value_batter(
    season_stats: dict,
    statcast: dict | None,
    settings,
    pa_projection: int = 600,
) -> ValuationResult | None:
    """
    Project a batter's fantasy PPG from Statcast metrics + actual stats.

    season_stats: dict from statsapi (homeRuns, hits, doubles, etc.)
    statcast:     dict from get_batter_expected_stats() or get_player_statcast()
                  Keys used: xwoba, woba, xba, xslg, barrel_pct, hard_hit,
                             sweet_spot, k_pct, bb_pct, whiff, sprint_speed
    pa_projection: baseline PA to project to (default 600 = full season)
    """
    sc = statcast or {}
    actual_pa = _safe_i(season_stats.get("plateAppearances") or season_stats.get("pa"))

    if actual_pa < 10 and not sc:
        return None

    # ── Sample size confidence & blend weight ──────────────────────
    sc_weight = _statcast_weight(actual_pa)
    confidence = "HIGH" if actual_pa >= 300 else ("MEDIUM" if actual_pa >= 100 else "LOW")

    # ── Statcast rate projection ────────────────────────────────────
    sc_rates = _project_batter_rates_from_statcast(sc)

    # ── Actual season rates ─────────────────────────────────────────
    if actual_pa >= 10:
        actual_rates = _batter_rates_from_actuals(season_stats, actual_pa)
        w = sc_weight
        rates = {k: w * sc_rates[k] + (1 - w) * actual_rates.get(k, sc_rates[k])
                 for k in sc_rates}
    else:
        rates = sc_rates

    # ── Regression signal (xwOBA vs actual wOBA) ───────────────────
    xwoba = sc.get("xwoba")
    woba  = sc.get("woba")
    regression_pct = 0.0
    if xwoba and woba and woba > 0.05:
        regression_pct = round((xwoba - woba) / woba * 50, 1)

    # ── Score each projected per-PA rate against league weights ────
    pts_per_pa = sum(rates.get(k, 0.0) * settings.weight(k) for k in rates)
    ppg = pts_per_pa * 3.8   # MLB avg ~3.8 PA/game

    breakdown = _batter_value_breakdown(rates, settings, pa_projection)
    breakdown.total_ppg = round(ppg, 2)

    return ValuationResult(
        ppg=round(ppg, 2),
        breakdown=breakdown,
        regression_pct=regression_pct,
        xwoba=xwoba,
        woba=woba,
        xera=None,
        era=None,
        barrel_pct=_safe_f(sc.get("barrel_pct")),
        sprint_speed=_safe_f(sc.get("sprint_speed")),
        k_pct=_safe_f(sc.get("k_pct")),
        bb_pct=_safe_f(sc.get("bb_pct")),
        statcast_weight=sc_weight,
        confidence=confidence,
        pts_per_pa=round(pts_per_pa, 4),
        pts_per_ip=None,
    )


def value_pitcher(
    season_stats: dict,
    statcast: dict | None,
    settings,
    is_sp: bool = True,
    ip_projection: Optional[float] = None,
) -> ValuationResult | None:
    """
    Project a pitcher's fantasy PPG from Statcast metrics + actual stats.

    season_stats: dict from statsapi (inningsPitched, strikeOuts, era, etc.)
    statcast:     dict from get_pitcher_expected_stats() or get_player_statcast()
                  Keys used: xera, era, barrel_pct, hard_hit, whiff, k_pct, bb_pct
    is_sp:        True = starting pitcher (more IP/game)
    ip_projection: baseline IP to project to (default: 180 SP, 65 RP)
    """
    sc = statcast or {}
    actual_ip = _parse_ip(season_stats.get("inningsPitched", 0))

    if actual_ip < 3 and not sc:
        return None

    if ip_projection is None:
        ip_projection = 180.0 if is_sp else 65.0

    # ── Sample size confidence & blend weight ──────────────────────
    sc_weight = _statcast_weight_ip(actual_ip)
    confidence = "HIGH" if actual_ip >= 80 else ("MEDIUM" if actual_ip >= 30 else "LOW")

    # ── Statcast rate projection ────────────────────────────────────
    sc_rates = _project_pitcher_rates_from_statcast(sc, is_sp)

    # ── Actual season rates ─────────────────────────────────────────
    if actual_ip >= 3:
        actual_rates = _pitcher_rates_from_actuals(season_stats, actual_ip)
        w = sc_weight
        rates = {k: w * sc_rates[k] + (1 - w) * actual_rates.get(k, sc_rates[k])
                 for k in sc_rates}
    else:
        rates = sc_rates

    # ── Regression signal (ERA vs xERA) ────────────────────────────
    era_raw = season_stats.get("era")
    try:
        era = float(str(era_raw)) if era_raw and str(era_raw) not in ("-.--", "") else None
    except Exception:
        era = None
    xera = _safe_f(sc.get("xera")) or None
    regression_pct = 0.0
    if xera and era and era > 0:
        regression_pct = round((era - xera) / era * 50, 1)  # ERA>xERA = unlucky (buy)

    # ── Score each projected per-IP rate against league weights ────
    pts_per_ip = sum(rates.get(k, 0.0) * settings.weight(k) for k in rates)
    ip_per_game = 1.1 if is_sp else 0.67
    ppg = pts_per_ip * ip_per_game

    breakdown = _pitcher_value_breakdown(rates, settings, ip_projection)
    breakdown.total_ppg = round(ppg, 2)

    return ValuationResult(
        ppg=round(ppg, 2),
        breakdown=breakdown,
        regression_pct=regression_pct,
        xwoba=None,
        woba=None,
        xera=xera,
        era=era,
        barrel_pct=_safe_f(sc.get("barrel_pct")),
        sprint_speed=None,
        k_pct=_safe_f(sc.get("k_pct")),
        bb_pct=_safe_f(sc.get("bb_pct")),
        statcast_weight=sc_weight,
        confidence=confidence,
        pts_per_pa=None,
        pts_per_ip=round(pts_per_ip, 4),
    )


def assign_percentiles(player_results: list[tuple[str, ValuationResult]]) -> None:
    """In-place: set ValuationResult.percentile (0–100) by PPG rank within group."""
    vals = sorted(r.ppg for _, r in player_results if r is not None)
    n = len(vals)
    if not n:
        return
    for _, result in player_results:
        if result is None:
            continue
        rank = sum(1 for v in vals if v < result.ppg)
        result.percentile = round(rank / n * 100)


# ---------------------------------------------------------------------------
# Batter projection internals
# ---------------------------------------------------------------------------

def _project_batter_rates_from_statcast(sc: dict) -> dict[str, float]:
    """
    Build per-PA rate projections using only Statcast metrics.
    Falls back to league averages for missing inputs.
    """
    # Use wOBA as xwOBA proxy when xwOBA is unavailable (e.g. early 2026 Savant data gap)
    xwoba_raw = sc.get("xwoba") or sc.get("woba")
    xwoba  = _coerce(xwoba_raw,  _MLB_AVG["xwoba"])
    xba    = _coerce(sc.get("xba") or sc.get("ba"),    _MLB_AVG["xba"])
    xslg   = _coerce(sc.get("xslg") or sc.get("slg"),   _MLB_AVG["xslg"])
    barrel = _coerce(sc.get("barrel_pct"), _MLB_AVG["barrel"])
    sprint = _coerce(sc.get("sprint_speed"), _MLB_AVG["sprint"])
    whiff  = _coerce(sc.get("whiff"),  _MLB_AVG["whiff"])

    # K%: use direct stat if available (from percentile data), else estimate from whiff%
    k_pct_raw = sc.get("k_pct")
    if k_pct_raw:
        k_rate = _coerce(k_pct_raw, _MLB_AVG["k_pct"]) / 100
    else:
        # whiff% × 0.82 approximates K% (empirical from 2021-2024 MLB)
        k_rate = min(0.40, whiff / 100 * 0.82)

    # BB%: use direct stat if available, else league average
    bb_pct_raw = sc.get("bb_pct")
    bb_rate = _coerce(bb_pct_raw, _MLB_AVG["bb_pct"]) / 100 if bb_pct_raw else _MLB_AVG["bb_pct"] / 100

    # HBP: fairly constant ~1.2% of PA
    hbp_rate = 0.012

    # Balls-in-play rate (everything that doesn't end in K or walk)
    bip_rate = max(0.25, 1.0 - k_rate - bb_rate - hbp_rate)

    # AB rate (plate appearances that become official at-bats)
    ab_rate = 1.0 - bb_rate - hbp_rate

    # ── HR: barrel% is the best predictor of HR rate ──
    # ~62% of barreled balls result in HR (2022-2024 MLB average)
    hr_rate = (barrel / 100) * bip_rate * 0.62

    # ── XBH decomposition from xBA and xSLG ──
    # xSLG = (1B + 2×2B + 3×3B + 4×HR) / AB
    # Total extra-base surplus above singles: (xSLG - xBA) × AB = 2B + 2×3B + 3×HR
    total_hits_pa  = xba * ab_rate
    xbh_surplus_pa = max(0.0, (xslg - xba) * ab_rate - 3 * hr_rate)
    # xbh_surplus = (2B) + 2×(3B); MLB: 3B ≈ 7% of 2B → surplus = 2.14 × 2B
    double_rate = max(0.0, xbh_surplus_pa / 2.14)
    triple_rate = double_rate * 0.07
    single_rate = max(0.0, total_hits_pa - hr_rate - double_rate - triple_rate)

    # ── R and RBI: scale from xwOBA relative to league average ──
    # League baseline: .315 xwOBA → 75 R / 75 RBI per 600 PA
    xwoba_ratio = xwoba / _MLB_AVG["xwoba"]
    run_rate = xwoba_ratio * (_BASELINE_R_PER_600 / 600)
    rbi_rate = xwoba_ratio * (_BASELINE_RBI_PER_600 / 600)

    # ── SB: sprint speed → stolen base projection ──
    sb_rate = _sprint_to_sb_per_pa(sprint)

    return {
        "singles":           single_rate,
        "doubles":           double_rate,
        "triples":           triple_rate,
        "homeRuns":          hr_rate,
        "rbi":               rbi_rate,
        "runs":              run_rate,
        "stolenBases":       sb_rate,
        "walks":             bb_rate,
        "hbp":               hbp_rate,
        "strikeouts_batter": k_rate,
    }


def _batter_rates_from_actuals(season_stats: dict, pa: int) -> dict[str, float]:
    """Convert actual season counting stats to per-PA rates."""
    hits    = _safe_f(season_stats.get("hits", 0))
    hr      = _safe_f(season_stats.get("homeRuns", 0))
    doubles = _safe_f(season_stats.get("doubles", 0))
    triples = _safe_f(season_stats.get("triples", 0))
    singles = max(0.0, hits - hr - doubles - triples)
    return {
        "singles":           singles / pa,
        "doubles":           doubles / pa,
        "triples":           triples / pa,
        "homeRuns":          hr / pa,
        "rbi":               _safe_f(season_stats.get("rbi", 0)) / pa,
        "runs":              _safe_f(season_stats.get("runs", 0)) / pa,
        "stolenBases":       _safe_f(season_stats.get("stolenBases", 0)) / pa,
        "walks":             _safe_f(season_stats.get("baseOnBalls", 0)) / pa,
        "hbp":               _safe_f(season_stats.get("hitByPitch", 0)) / pa,
        "strikeouts_batter": _safe_f(season_stats.get("strikeOuts", 0)) / pa,
    }


def _batter_value_breakdown(rates: dict, settings, pa: int) -> ValueBreakdown:
    """Attribute PPG to each value component for transparency."""
    def pts(key): return rates.get(key, 0.0) * settings.weight(key) * 3.8

    power          = pts("homeRuns") + pts("doubles") + pts("triples")
    speed          = pts("stolenBases")
    contact        = pts("singles")
    run_production = pts("runs") + pts("rbi")
    discipline     = pts("walks") + pts("hbp") + pts("strikeouts_batter")  # K is negative

    return ValueBreakdown(
        power=round(power, 2),
        speed=round(speed, 2),
        contact=round(contact, 2),
        run_production=round(run_production, 2),
        discipline=round(discipline, 2),
    )


# ---------------------------------------------------------------------------
# Pitcher projection internals
# ---------------------------------------------------------------------------

def _project_pitcher_rates_from_statcast(sc: dict, is_sp: bool) -> dict[str, float]:
    """
    Build per-IP rate projections using only Statcast metrics.
    Falls back to league averages for missing inputs.
    """
    xera    = _coerce(sc.get("xera"),       _MLB_AVG["xera"])
    whiff   = _coerce(sc.get("whiff"),      _MLB_AVG["whiff"])
    barrel  = _coerce(sc.get("barrel_pct"), _MLB_AVG["barrel"])
    hard_hit = _coerce(sc.get("hard_hit"),  _MLB_AVG["hard_hit"])

    # K/9 from whiff% and/or k_pct
    k_pct_raw = sc.get("k_pct")
    if k_pct_raw:
        k_per_9 = _coerce(k_pct_raw, _MLB_AVG["k_pct"]) / 100 * 27  # 27 BF per 9 IP
    else:
        # Empirical: K/9 ≈ whiff% × 0.38 (2022-2024 MLB)
        k_per_9 = min(14.0, whiff * 0.38)
    k_per_ip = k_per_9 / 9

    # BB/9 from bb_pct
    bb_pct_raw = sc.get("bb_pct")
    if bb_pct_raw:
        bb_per_9 = _coerce(bb_pct_raw, _MLB_AVG["bb_pct"]) / 100 * 27
    else:
        bb_per_9 = 3.0  # MLB average
    bb_per_ip = bb_per_9 / 9

    # ER and H from xERA
    # xERA encodes expected earned runs per 9 IP, so:
    er_per_ip = xera / 9
    # H/9 estimated from xERA and contact quality
    # League avg: ~8.5 H/9 at 4.20 ERA; better pitchers allow fewer
    h_per_9_baseline = 8.5 * (xera / _MLB_AVG["xera"])
    # Adjust for barrel% and hard_hit% (high barrel% → more XBH, fewer but harder hits)
    contact_adj = 1.0 + (barrel - _MLB_AVG["barrel"]) / _MLB_AVG["barrel"] * 0.15
    h_per_ip = (h_per_9_baseline / 9) * contact_adj

    # W and SV: context-dependent, use actual rates via blend
    # Statcast baseline: avg SP gets ~0.045 W/IP; avg closer gets ~0.45 SV/IP
    w_per_ip  = 0.050 if is_sp else 0.010
    sv_per_ip = 0.010 if is_sp else 0.450

    return {
        "inningsPitched":     1.0,          # pts for throwing IP
        "strikeouts_pitched": k_per_ip,
        "earnedRuns":         er_per_ip,
        "hits_allowed":       h_per_ip,
        "walks_allowed":      bb_per_ip,
        "wins":               w_per_ip,
        "saves":              sv_per_ip,
    }


def _pitcher_rates_from_actuals(season_stats: dict, ip: float) -> dict[str, float]:
    """Convert actual season pitching stats to per-IP rates."""
    er = _safe_f(season_stats.get("earnedRuns"))
    if er == 0:
        try:
            era_val = float(str(season_stats.get("era", "0")).replace("-.--", "0"))
            er = era_val * ip / 9
        except Exception:
            er = 0.0

    return {
        "inningsPitched":     1.0,
        "strikeouts_pitched": _safe_f(season_stats.get("strikeOuts", 0)) / ip,
        "earnedRuns":         er / ip,
        "hits_allowed":       _safe_f(season_stats.get("hits", 0)) / ip,
        "walks_allowed":      _safe_f(season_stats.get("baseOnBalls", 0)) / ip,
        "wins":               _safe_f(season_stats.get("wins", 0)) / ip,
        "saves":              _safe_f(season_stats.get("saves", 0)) / ip,
    }


def _pitcher_value_breakdown(rates: dict, settings, ip: float) -> ValueBreakdown:
    """Attribute PPG to each pitcher value component."""
    ip_per_game = ip / 162  # rough games/IP ratio for breakdown only
    def pts(key): return rates.get(key, 0.0) * settings.weight(key)

    k_value    = pts("strikeouts_pitched")
    era_quality = pts("earnedRuns") + pts("hits_allowed") + pts("walks_allowed")
    role_value  = pts("wins") + pts("saves") + pts("inningsPitched")

    return ValueBreakdown(
        k_value=round(k_value, 2),
        era_quality=round(era_quality, 2),
        role_value=round(role_value, 2),
    )


# ---------------------------------------------------------------------------
# Sprint speed → SB projection
# ---------------------------------------------------------------------------

def _sprint_to_sb_per_pa(sprint_speed: float) -> float:
    """
    Project stolen bases per PA from sprint speed.

    Calibrated to 2023-2024 MLB (post larger-base rule change).
    The relationship is exponential: elite runners attempt and succeed
    far more than average; slow runners are negligible.

    Approximate per-600 PA benchmarks:
      30.0 ft/s → ~45 SB   (elite: Jackson, Hamilton tier)
      29.0 ft/s → ~30 SB
      28.0 ft/s → ~18 SB
      27.0 ft/s → ~10 SB   (league average speed)
      26.0 ft/s → ~4 SB
      25.0 ft/s → ~1 SB
      < 24.5    → ~0 SB
    """
    if not sprint_speed or sprint_speed < 24.5:
        return 0.0
    # Exponential growth above 24.5 ft/s baseline
    sb_per_600 = max(0.0, (sprint_speed - 24.5) ** 2.4 * 0.55)
    return sb_per_600 / 600


# ---------------------------------------------------------------------------
# Sample-size blend weights
# ---------------------------------------------------------------------------

def _statcast_weight(pa: int) -> float:
    """
    How much weight to give Statcast projection vs actual stats.
    Returns value 0.0 (all actuals) to 1.0 (all Statcast).

    At 0 PA:   1.00 (trust Statcast entirely — no actuals)
    At 100 PA: 0.65 (Statcast rates are more stable than 100-PA actuals)
    At 250 PA: 0.40 (roughly equal weight — both meaningful)
    At 450 PA: 0.20 (actuals dominate — large enough sample)
    At 600 PA: 0.10 (full season — actuals nearly definitive)
    """
    if pa <= 0:
        return 1.00
    return max(0.10, min(1.00, 1.0 - (pa / 600) ** 0.65))


def _statcast_weight_ip(ip: float) -> float:
    """IP-equivalent of _statcast_weight for pitchers. 60 IP ≈ 250 PA reliability."""
    if ip <= 0:
        return 1.00
    return max(0.10, min(1.00, 1.0 - (ip / 180) ** 0.65))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_ip(ip_str) -> float:
    """Parse statsapi '73.2' → 73.667 (the digit after '.' is outs, not tenths)."""
    try:
        s = str(ip_str)
        if "." in s:
            full_s, frac_s = s.split(".", 1)
            outs = int(frac_s[0]) if frac_s else 0
            return int(full_s) + outs / 3.0
        return float(s)
    except Exception:
        return 0.0


def _safe_f(v) -> float:
    try:
        return float(v) if v is not None else 0.0
    except (ValueError, TypeError):
        return 0.0


def _safe_i(v) -> int:
    try:
        return int(v) if v is not None else 0
    except (ValueError, TypeError):
        return 0


def _coerce(v, default: float) -> float:
    """Return float(v) if valid and positive, else default."""
    try:
        result = float(v)
        return result if result > 0 else default
    except (TypeError, ValueError):
        return default
