"""
Builds the AI chat context string for the fantasy advisor.

Gathers: matchup/roster data, league scoring, today's game schedule,
opposing starting pitchers + their season stats, and player season stats.
Uses threading to parallelize the many statsapi calls.
"""

import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date

import statsapi

_cache: dict[str, tuple[float, str]] = {}  # key → (timestamp, context_str)
_CONTEXT_TTL = 900  # 15 minutes

_ABBREV = {
    "ARI": "Arizona Diamondbacks", "ATL": "Atlanta Braves",
    "BAL": "Baltimore Orioles",     "BOS": "Boston Red Sox",
    "CHC": "Chicago Cubs",          "CWS": "Chicago White Sox",
    "CIN": "Cincinnati Reds",       "CLE": "Cleveland Guardians",
    "COL": "Colorado Rockies",      "DET": "Detroit Tigers",
    "HOU": "Houston Astros",        "KC":  "Kansas City Royals",
    "LAA": "Los Angeles Angels",    "LAD": "Los Angeles Dodgers",
    "MIA": "Miami Marlins",         "MIL": "Milwaukee Brewers",
    "MIN": "Minnesota Twins",       "NYM": "New York Mets",
    "NYY": "New York Yankees",      "OAK": "Oakland Athletics",
    "PHI": "Philadelphia Phillies", "PIT": "Pittsburgh Pirates",
    "SD":  "San Diego Padres",      "SF":  "San Francisco Giants",
    "SEA": "Seattle Mariners",      "STL": "St. Louis Cardinals",
    "TB":  "Tampa Bay Rays",        "TEX": "Texas Rangers",
    "TOR": "Toronto Blue Jays",     "WSH": "Washington Nationals",
}

SYSTEM_PREAMBLE = """You are an expert fantasy baseball AI advisor embedded in a personal stats dashboard.

You have deep knowledge of:
- MLB statistics and sabermetrics (wOBA, FIP, xFIP, barrel%, exit velocity, etc.)
- Fantasy baseball strategy in points leagues, H2H categories, and roto
- Ballpark factors (e.g. Coors Field benefits hitters, Oracle Park suppresses HRs)
- Matchup analysis: batter vs pitcher tendencies, platoon advantages, recent form
- Trade valuation, waiver wire targeting, and start/sit optimization

## CRITICAL — READ BEFORE RESPONDING
- The data below is **live, real data** pulled directly from the user's Yahoo Fantasy Baseball account
- You **DO have** the user's roster, matchup score, today's games, opposing pitchers, scoring settings, and waiver wire — it is all in the context below
- **Never say** "I don't have access to that data" or "I can't see your league" — you can, it is all below
- When asked about waiver wire pickups, use the WAIVER WIRE section — those are the actual available players in their specific league right now
- Reference specific player names and real stats. Be direct: "Start X because..." or "Pick up Y — they face Z today (ERA 4.21, soft matchup)"
- Keep responses scannable: bold the key names, use bullet points, give a clear recommendation first

Below is the live snapshot of the user's Yahoo Fantasy league:\n\n"""


def build(provider, force_refresh: bool = False) -> str:
    """Return the full system prompt string. Cached 15 minutes."""
    key = date.today().isoformat()
    if not force_refresh and key in _cache:
        ts, text = _cache[key]
        if time.time() - ts < _CONTEXT_TTL:
            return text

    context = _assemble(provider)
    full = SYSTEM_PREAMBLE + context
    _cache[key] = (time.time(), full)
    return full


def _assemble(provider) -> str:
    parts = []

    # ── Matchup + Roster ──────────────────────────────────────────
    try:
        matchup = provider.get_matchup()
        me = matchup.me
        opp = matchup.opponent
        parts.append(_fmt_matchup(matchup))
        all_my_players = me.players
    except Exception as e:
        parts.append(f"## FANTASY DATA\nUnavailable: {e}")
        return "\n\n".join(parts)

    # ── Scoring settings ──────────────────────────────────────────
    try:
        parts.append(_fmt_scoring(provider))
    except Exception:
        parts.append("## SCORING SETTINGS\nStandard points-based fantasy baseball")

    # ── Today's games with opposing SP + player stats ─────────────
    try:
        parts.append(_fmt_today_games(all_my_players))
    except Exception as e:
        parts.append(f"## TODAY'S GAMES\nUnavailable: {e}")

    # ── Waiver wire / free agents ─────────────────────────────────
    try:
        parts.append(_get_free_agents_section(provider))
    except Exception as e:
        parts.append(f"## WAIVER WIRE\nUnavailable: {e}")

    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Matchup + roster block
# ---------------------------------------------------------------------------

def _fmt_matchup(matchup) -> str:
    me = matchup.me
    opp = matchup.opponent
    lines = [
        f"## CURRENT MATCHUP — {matchup.period}",
        f"My team: **{me.name}** — {me.total_points:.1f} pts",
        f"Opponent: **{opp.name}** — {opp.total_points:.1f} pts",
        f"Status: {matchup.status_label}",
        "",
        "### MY ACTIVE STARTERS",
        "| Slot | Player | MLB Team | Today Pts | Today Stats |",
        "|------|--------|----------|-----------|-------------|",
    ]
    for p in me.starters:
        lines.append(f"| {p.lineup_slot} | {p.name} | {p.pro_team} | {p.today_points:.1f} | {p.stat_summary()} |")

    if me.bench:
        lines += [
            "",
            "### MY BENCH",
            "| Slot | Player | MLB Team | Today Pts |",
            "|------|--------|----------|-----------|",
        ]
        for p in me.bench:
            lines.append(f"| {p.lineup_slot} | {p.name} | {p.pro_team} | {p.today_points:.1f} |")

    lines += [
        "",
        f"### OPPONENT STARTERS — {opp.name}",
        "| Slot | Player | MLB Team | Today Pts | Today Stats |",
        "|------|--------|----------|-----------|-------------|",
    ]
    for p in opp.starters:
        lines.append(f"| {p.lineup_slot} | {p.name} | {p.pro_team} | {p.today_points:.1f} | {p.stat_summary()} |")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Scoring settings block
# ---------------------------------------------------------------------------

_STAT_NAMES = {
    "7":  "Runs (R)",           "9":  "Singles (1B)",
    "10": "Doubles (2B)",        "11": "Triples (3B)",
    "12": "Home Runs (HR)",      "13": "RBI",
    "16": "Stolen Bases (SB)",   "33": "Outs Pitched (÷3 = IP)",
    "42": "Pitcher Strikeouts",  "37": "Earned Runs (ER)",
    "34": "Hits Allowed",        "39": "Walks Allowed",
    "28": "Wins (W)",            "32": "Saves (SV)",
}


def _fmt_scoring(provider) -> str:
    try:
        league_id = os.getenv("YAHOO_LEAGUE_ID", "")
        resp = provider._lg.sc.session.get(
            f"https://fantasysports.yahooapis.com/fantasy/v2/league/{league_id}/settings",
            params={"format": "json"},
        )
        data = resp.json()["fantasy_content"]["league"][1]["settings"]
        if isinstance(data, list):
            data = data[0]
        modifiers = data.get("stat_modifiers", {}).get("stats", [])

        batting, pitching = [], []
        for item in modifiers:
            try:
                sid = str(item["stat"]["stat_id"])
                val = float(item["stat"]["value"])
                name = _STAT_NAMES.get(sid, f"Stat #{sid}")
                sign = "+" if val >= 0 else ""
                line = f"  {name}: {sign}{val}"
                (pitching if sid in ("33", "42", "37", "34", "39", "28", "32") else batting).append(line)
            except Exception:
                pass

        lines = ["## SCORING SETTINGS"]
        if batting:
            lines.append("**Batting:**")
            lines.extend(batting)
        if pitching:
            lines.append("**Pitching:**")
            lines.extend(pitching)
        return "\n".join(lines)
    except Exception:
        return "## SCORING SETTINGS\nPoints-based fantasy baseball (standard)"


# ---------------------------------------------------------------------------
# Today's games block — parallelized
# ---------------------------------------------------------------------------

def _fmt_today_games(players: list) -> str:
    today_str = date.today().strftime("%Y-%m-%d")
    games = statsapi.schedule(date=today_str, sportId=1)

    # Build lookup: lowercase full team name → game dict
    game_by_team: dict[str, dict] = {}
    for g in games:
        game_by_team[g["away_name"].lower()] = g
        game_by_team[g["home_name"].lower()] = g

    def find_game(abbrev: str) -> dict | None:
        full = _ABBREV.get(abbrev.upper(), "")
        if full and full.lower() in game_by_team:
            return game_by_team[full.lower()]
        # Fuzzy fallback: any game mentioning the abbreviation in a team name
        for tname, g in game_by_team.items():
            if abbrev.upper() in tname.upper():
                return g
        return None

    playing: list[tuple] = []
    not_playing: list = []

    for p in players:
        g = find_game(p.pro_team)
        if g:
            playing.append((p, g))
        else:
            not_playing.append(p)

    # Parallelize: season stats for my players + opposing SP stats
    stat_tasks: dict[str, tuple] = {}  # key → (type, id_or_name, slot)
    pitcher_lookup: dict[str, str] = {}  # player_name → opposing_pitcher_name

    for p, g in playing:
        full = _ABBREV.get(p.pro_team.upper(), p.pro_team)
        is_away = full.lower() in g["away_name"].lower()
        opp_sp_name = g["home_pitcher"] if is_away else g["away_pitcher"]
        pitcher_lookup[p.name] = opp_sp_name or ""

        if p.mlbam_id:
            stat_tasks[f"player_{p.mlbam_id}"] = ("player", p.mlbam_id, p.lineup_slot)
        if opp_sp_name and opp_sp_name.lower() not in ("unknown", "tbd", ""):
            stat_tasks[f"pitcher_{opp_sp_name}"] = ("pitcher_lookup", opp_sp_name, "SP")

    results: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=10) as ex:
        futures = {}
        for k, (kind, id_or_name, slot) in stat_tasks.items():
            if kind == "player":
                fut = ex.submit(_player_stats, id_or_name, slot)
            else:
                fut = ex.submit(_pitcher_stats_by_name, id_or_name)
            futures[fut] = k

        for fut in as_completed(futures):
            k = futures[fut]
            try:
                results[k] = fut.result(timeout=8) or ""
            except Exception:
                results[k] = ""

    # Assemble the text
    lines = [f"## TODAY'S GAMES ({today_str}) — MY PLAYERS"]

    for p, g in playing:
        full = _ABBREV.get(p.pro_team.upper(), p.pro_team)
        is_away = full.lower() in g["away_name"].lower()
        opp_team = g["home_name"] if is_away else g["away_name"]
        venue = g.get("venue_name", "")
        opp_sp = pitcher_lookup.get(p.name, "")
        game_status = g.get("status", "")

        lines.append(f"\n### {p.name} ({p.lineup_slot}, {p.pro_team})")
        matchup_str = f"{full} @ {opp_team}" if is_away else f"{opp_team} @ {full}"
        lines.append(f"Game: {matchup_str}")
        if venue:
            lines.append(f"Venue: {venue}")
        if game_status:
            lines.append(f"Status: {game_status}")

        if opp_sp and opp_sp.lower() not in ("unknown", "tbd", ""):
            sp_stats = results.get(f"pitcher_{opp_sp}", "")
            lines.append(f"Opposing SP: **{opp_sp}**" + (f" — {sp_stats}" if sp_stats else ""))

        p_stats = results.get(f"player_{p.mlbam_id}", "") if p.mlbam_id else ""
        if p_stats:
            lines.append(f"2026 Season: {p_stats}")

    if not_playing:
        lines.append("\n## NOT PLAYING TODAY")
        for p in not_playing:
            line = f"  - {p.name} ({p.lineup_slot}, {p.pro_team})"
            if p.mlbam_id:
                stats = _player_stats(p.mlbam_id, p.lineup_slot)
                if stats:
                    line += f" — {stats}"
            lines.append(line)

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Individual stat helpers (called in threads)
# ---------------------------------------------------------------------------

def _player_stats(mlbam_id: int, slot: str) -> str:
    try:
        is_pitcher = slot in ("SP", "RP", "P")
        group = "pitching" if is_pitcher else "hitting"
        data = statsapi.player_stat_data(mlbam_id, group=group, type="season")
        for sg in data.get("stats", []):
            if sg.get("group") == group and sg.get("stats"):
                s = sg["stats"]
                if is_pitcher:
                    return (
                        f"ERA {s.get('era','—')}, WHIP {s.get('whip','—')}, "
                        f"{s.get('inningsPitched','—')} IP, "
                        f"{s.get('strikeOuts','—')} K, {s.get('baseOnBalls','—')} BB, "
                        f"{s.get('homeRuns','—')} HR, W-L {s.get('wins','—')}-{s.get('losses','—')}"
                    )
                else:
                    avg = s.get("avg", "—")
                    obp = s.get("obp", "—")
                    slg = s.get("slg", "—")
                    ops = s.get("ops", "—")
                    hr  = s.get("homeRuns", "—")
                    rbi = s.get("rbi", "—")
                    sb  = s.get("stolenBases", "—")
                    so  = s.get("strikeOuts", "—")
                    pa  = s.get("plateAppearances", "—")
                    return f"{avg}/{obp}/{slg} ({ops} OPS), {hr} HR, {rbi} RBI, {sb} SB, {so} K, {pa} PA"
    except Exception:
        pass
    return ""


def _pitcher_stats_by_name(pitcher_name: str) -> str:
    try:
        results = statsapi.lookup_player(pitcher_name, sportId=1)
        if not results:
            return ""
        pid = results[0]["id"]
        data = statsapi.player_stat_data(pid, group="pitching", type="season")
        for sg in data.get("stats", []):
            if sg.get("group") == "pitching" and sg.get("stats"):
                s = sg["stats"]
                return (
                    f"ERA {s.get('era','—')}, WHIP {s.get('whip','—')}, "
                    f"{s.get('inningsPitched','—')} IP, "
                    f"{s.get('strikeOuts','—')} K, {s.get('baseOnBalls','—')} BB, "
                    f"{s.get('homeRuns','—')} HR"
                )
    except Exception:
        pass
    return ""


# ---------------------------------------------------------------------------
# Waiver wire / free agents block
# ---------------------------------------------------------------------------

def _parse_yahoo_player_stats(pdata: list) -> dict:
    """Extract stat_id → float from a Yahoo player API response list."""
    stats: dict[str, float] = {}
    for section in pdata[1:]:
        if not isinstance(section, dict):
            continue
        raw_stats = section.get("player_stats", {}).get("stats", [])
        for item in raw_stats:
            try:
                sid = str(item["stat"]["stat_id"])
                val = item["stat"]["value"]
                if val not in ("-", "", None):
                    stats[sid] = float(val)
            except Exception:
                pass
        # Also capture total fantasy points if present
        pts = section.get("player_points", {}).get("total")
        if pts is not None:
            try:
                stats["_pts"] = float(pts)
            except Exception:
                pass
    return stats


def _fmt_pitcher_stats(stats: dict) -> str:
    outs = stats.get("33")
    ip = outs / 3 if outs else None
    ip_str = f"{int(ip)}.{int(round((ip - int(ip)) * 3))}" if ip else "—"

    era = stats.get("38")
    if era is None and ip and "37" in stats:
        era = round(stats["37"] / ip * 9, 2)

    whip = stats.get("50")
    if whip is None and ip and "36" in stats and "39" in stats:
        whip = round((stats["36"] + stats["39"]) / ip, 2)

    era_s  = f"{era:.2f}"  if era  is not None else "—"
    whip_s = f"{whip:.2f}" if whip is not None else "—"
    k  = int(stats["42"]) if "42" in stats else "—"
    w  = int(stats["28"]) if "28" in stats else "—"
    sv = int(stats["32"]) if "32" in stats else "—"
    return f"ERA {era_s} | WHIP {whip_s} | {ip_str} IP | {k} K | {w} W | {sv} SV"


def _fmt_batter_stats(stats: dict) -> str:
    avg = stats.get("26")
    if avg is None and "8" in stats and "14" in stats and stats["14"] > 0:
        avg = round(stats["8"] / stats["14"], 3)
    avg_s = f".{int(float(avg) * 1000):03d}" if avg is not None else "—"
    hr  = int(stats["12"]) if "12" in stats else "—"
    rbi = int(stats["13"]) if "13" in stats else "—"
    r   = int(stats["7"])  if "7"  in stats else "—"
    sb  = int(stats["16"]) if "16" in stats else "—"
    return f"AVG {avg_s} | {hr} HR | {rbi} RBI | {r} R | {sb} SB"


def _get_free_agents_section(provider) -> str:
    """Fetch top available pitchers and hitters from Yahoo and format for AI context."""
    league_id = os.getenv("YAHOO_LEAGUE_ID", "")

    def fetch(position: str, count: int = 25) -> list[dict]:
        # status=A means available (free agents + waivers); status=FW is not valid
        url = (
            f"https://fantasysports.yahooapis.com/fantasy/v2/"
            f"league/{league_id}/players;status=A;position={position};"
            f"count={count};sort=AR/stats;type=season"
        )
        resp = provider._lg.sc.session.get(url, params={"format": "json"})
        players_raw = resp.json()["fantasy_content"]["league"][1]["players"]
        n = players_raw.get("count", 0)
        out = []
        for i in range(n):
            try:
                pdata = players_raw[str(i)]["player"]
                info = pdata[0]
                name = next((x["name"]["full"] for x in info if isinstance(x, dict) and "name" in x), "?")
                team = next((x["editorial_team_abbr"] for x in info if isinstance(x, dict) and "editorial_team_abbr" in x), "?")
                pos  = next((x["display_position"] for x in info if isinstance(x, dict) and "display_position" in x), "?")
                # Stats are in pdata[1] as player_stats + player_points
                stats = _parse_yahoo_player_stats(pdata)
                out.append({"name": name, "team": team, "pos": pos, "stats": stats})
            except Exception:
                continue
        return out

    lines = ["## WAIVER WIRE / FREE AGENTS (Available in Your League)"]

    # ── Pitchers ──────────────────────────────────────────────────
    try:
        pitchers = fetch("SP,RP", 25)
        lines += [
            "\n### Available Pitchers",
            "| Player | Team | Pos | ERA | WHIP | IP | K | W | SV |",
            "|--------|------|-----|-----|------|----|---|---|----|",
        ]
        for p in pitchers:
            s = p["stats"]
            outs = s.get("33")
            ip = outs / 3 if outs else None
            ip_str = f"{int(ip)}.{int(round((ip - int(ip)) * 3))}" if ip else "—"
            era = s.get("38")
            if era is None and ip and "37" in s:
                era = round(s["37"] / ip * 9, 2)
            whip = s.get("50")
            if whip is None and ip and "36" in s and "39" in s:
                whip = round((s["36"] + s["39"]) / ip, 2)
            lines.append(
                f"| {p['name']} | {p['team']} | {p['pos']} "
                f"| {f'{era:.2f}' if era is not None else '—'} "
                f"| {f'{whip:.2f}' if whip is not None else '—'} "
                f"| {ip_str} "
                f"| {int(s['42']) if '42' in s else '—'} "
                f"| {int(s['28']) if '28' in s else '—'} "
                f"| {int(s['32']) if '32' in s else '—'} |"
            )
    except Exception as e:
        lines.append(f"Pitchers: unavailable ({e})")

    # ── Position players ──────────────────────────────────────────
    try:
        batters = fetch("1B,2B,3B,SS,OF,C", 20)
        lines += [
            "\n### Available Position Players",
            "| Player | Team | Pos | AVG | HR | RBI | R | SB |",
            "|--------|------|-----|-----|----|-----|---|----|",
        ]
        for p in batters:
            s = p["stats"]
            avg = s.get("26")
            if avg is None and "8" in s and "14" in s and s.get("14", 0) > 0:
                avg = round(s["8"] / s["14"], 3)
            avg_s = f".{int(float(avg) * 1000):03d}" if avg is not None else "—"
            lines.append(
                f"| {p['name']} | {p['team']} | {p['pos']} "
                f"| {avg_s} "
                f"| {int(s['12']) if '12' in s else '—'} "
                f"| {int(s['13']) if '13' in s else '—'} "
                f"| {int(s['7'])  if '7'  in s else '—'} "
                f"| {int(s['16']) if '16' in s else '—'} |"
            )
    except Exception as e:
        lines.append(f"Position players: unavailable ({e})")

    return "\n".join(lines)
