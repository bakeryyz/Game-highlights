from datetime import date as date_type

import statsapi

from data_sources import cache


def _innings_to_float(ip: str | float | int) -> float:
    """
    Convert MLB innings-pitched format to float.
    "6.2" means 6 and 2/3 innings — NOT 6.2.
    """
    s = str(ip)
    if '.' in s:
        whole, frac = s.split('.', 1)
        return int(whole) + int(frac) / 3
    return float(s)


def _normalize_batting(raw: dict) -> dict:
    """
    Convert a raw MLB boxscore batting dict to normalized scoring keys.
    Key gotcha: singles aren't a field — compute from other hits.
    """
    hits = int(raw.get('hits', 0))
    doubles = int(raw.get('doubles', 0))
    triples = int(raw.get('triples', 0))
    hrs = int(raw.get('homeRuns', 0))
    singles = max(0, hits - doubles - triples - hrs)
    return {
        'singles': singles,
        'doubles': doubles,
        'triples': triples,
        'homeRuns': hrs,
        'rbi': int(raw.get('rbi', 0)),
        'runs': int(raw.get('runs', 0)),
        'walks': int(raw.get('baseOnBalls', 0)),
        'hbp': int(raw.get('hitByPitch', 0)),
        'stolenBases': int(raw.get('stolenBases', 0)),
        'strikeouts_batter': int(raw.get('strikeOuts', 0)),
    }


def _normalize_pitching(raw: dict) -> dict:
    """
    Convert a raw MLB boxscore pitching dict to normalized scoring keys.
    Key gotcha: inningsPitched "6.2" = 6⅔, not 6.2.
    Wins/saves are game decisions — not in per-player box line; default 0.
    """
    return {
        'inningsPitched': _innings_to_float(raw.get('inningsPitched', '0')),
        'strikeouts_pitched': int(raw.get('strikeOuts', 0)),
        'earnedRuns': int(raw.get('earnedRuns', 0)),
        'hits_allowed': int(raw.get('hits', 0)),
        'walks_allowed': int(raw.get('baseOnBalls', 0)),
        # Decisions are not in per-player box lines — wire a source here later
        'wins': 0,
        'saves': 0,
    }


def todays_game_pks(day: date_type | None = None) -> list[int]:
    """Return all MLB game PKs scheduled for today (or a given date)."""
    d = day or date_type.today()
    games = statsapi.schedule(date=d.strftime('%Y-%m-%d'), sportId=1)
    return [g['game_id'] for g in games if g.get('status') in ('Final', 'In Progress')]


def player_stat_lines(game_pk: int) -> dict[int, dict]:
    """
    Return {mlbam_id: normalized_stat_line} for every player in a game.
    Caches the raw boxscore to avoid re-fetching on every call.
    """
    raw = cache.load(str(game_pk), 'boxscore')
    if raw is None:
        raw = statsapi.boxscore_data(game_pk)
        cache.save(str(game_pk), raw, 'boxscore')

    result: dict[int, dict] = {}
    for side in ('home', 'away'):
        players = raw.get(side, {}).get('players', {})
        for key, player_data in players.items():
            if not key.startswith('ID'):
                continue
            mlbam_id = int(key[2:])
            stats = player_data.get('stats', {})
            batting = stats.get('batting', {})
            pitching = stats.get('pitching', {})

            line: dict = {}
            if batting:
                line.update(_normalize_batting(batting))
            if pitching and pitching.get('inningsPitched', '0') != '0':
                line.update(_normalize_pitching(pitching))

            if line:
                result[mlbam_id] = line

    return result


def all_player_stats_today(day: date_type | None = None) -> dict[int, tuple[int, dict]]:
    """
    Return {mlbam_id: (game_pk, stat_line)} for all players with stats today.
    Indexes stats from every game in one pass so the caller can look up any player.
    """
    index: dict[int, tuple[int, dict]] = {}
    for gid in todays_game_pks(day):
        try:
            for mlbam_id, line in player_stat_lines(gid).items():
                index[mlbam_id] = (gid, line)
        except Exception:
            continue
    return index
