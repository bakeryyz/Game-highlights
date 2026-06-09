import re
from datetime import datetime

import statsapi

from core.models import Clip, Game, GameCandidate, Play
from data_sources import cache

# ---------------------------------------------------------------------------
# Date extraction
# ---------------------------------------------------------------------------

_MONTHS = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3,
    "april": 4, "apr": 4, "may": 5, "june": 6, "jun": 6,
    "july": 7, "jul": 7, "august": 8, "aug": 8, "september": 9, "sep": 9,
    "october": 10, "oct": 10, "november": 11, "nov": 11, "december": 12, "dec": 12,
}


def _extract_date(query: str) -> str | None:
    """Return a YYYY-MM-DD string extracted from a natural-language query, or None."""
    q = query.lower().strip()

    # YYYY-MM-DD
    m = re.search(r'\b(\d{4})-(\d{1,2})-(\d{1,2})\b', q)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"

    # M/D/YY or M/D/YYYY
    m = re.search(r'\b(\d{1,2})/(\d{1,2})/(\d{2,4})\b', q)
    if m:
        year = int(m.group(3))
        if year < 100:
            year += 2000
        return f"{year}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"

    # "June 3rd 2024" / "June 3 2024" / "June 3, 2024"
    pattern = r'\b(' + '|'.join(_MONTHS.keys()) + r')\s+(\d{1,2})(?:st|nd|rd|th)?[,\s]+(\d{4})\b'
    m = re.search(pattern, q)
    if m:
        month = _MONTHS[m.group(1)]
        return f"{m.group(3)}-{month:02d}-{int(m.group(2)):02d}"

    # "June 3rd" / "June 3" — no year, assume current year
    pattern = r'\b(' + '|'.join(_MONTHS.keys()) + r')\s+(\d{1,2})(?:st|nd|rd|th)?\b'
    m = re.search(pattern, q)
    if m:
        month = _MONTHS[m.group(1)]
        year = datetime.now().year
        return f"{year}-{month:02d}-{int(m.group(2)):02d}"

    return None


# ---------------------------------------------------------------------------
# Game lookup
# ---------------------------------------------------------------------------

def find_games(query: str) -> list[GameCandidate]:
    """Parse a natural-language query and return matching GameCandidates."""
    date = _extract_date(query)
    if not date:
        return []

    all_games = statsapi.schedule(date=date, sportId=1)

    # Try to filter by team name if any word in the query matches
    words = re.sub(r'[^a-z\s]', '', query.lower()).split()
    filtered = [
        g for g in all_games
        if any(w in g['home_name'].lower() or w in g['away_name'].lower() for w in words
               if len(w) > 3)
    ]
    games_to_use = filtered if filtered else all_games

    return [
        GameCandidate(
            game_id=g['game_id'],
            away_name=g['away_name'],
            home_name=g['home_name'],
            date=g['game_date'],
            away_score=g.get('away_score', 0),
            home_score=g.get('home_score', 0),
            status=g['status'],
            game_num=g.get('game_num', 1),
        )
        for g in sorted(games_to_use, key=lambda x: x['game_datetime'], reverse=True)
    ]


def find_game(date: str, team: str) -> list[dict]:
    """Simple lookup by date + team name string (kept for backward compat)."""
    games = statsapi.schedule(date=date, sportId=1)
    return [
        g for g in games
        if team.lower() in g['home_name'].lower() or team.lower() in g['away_name'].lower()
    ]


# ---------------------------------------------------------------------------
# Play parsing
# ---------------------------------------------------------------------------

def _parse_plays(all_plays: list[dict]) -> list[Play]:
    """Convert raw allPlays list into Play objects. Pure function — testable offline."""
    plays = []
    for p in all_plays:
        if not p.get('about', {}).get('isComplete', True):
            continue
        result = p.get('result', {})
        about = p.get('about', {})
        matchup = p.get('matchup', {})
        plays.append(Play(
            index=about.get('atBatIndex', len(plays)),
            inning=about.get('inning', 1),
            half=about.get('halfInning', 'top'),
            description=result.get('description', ''),
            event=result.get('event', ''),
            event_type=result.get('eventType', ''),
            away_score=result.get('awayScore', 0),
            home_score=result.get('homeScore', 0),
            is_scoring_play=about.get('isScoringPlay', False),
            is_out=result.get('isOut', False),
            rbi=result.get('rbi', 0),
            batter=matchup.get('batter', {}).get('fullName', ''),
            pitcher=matchup.get('pitcher', {}).get('fullName', ''),
            captivating_index=about.get('captivatingIndex', 0),
            video_url=None,
        ))
    return plays


# ---------------------------------------------------------------------------
# Clip parsing
# ---------------------------------------------------------------------------

def _parse_clips(items: list[dict]) -> list[Clip]:
    """Convert raw highlight items into Clip objects, filtering non-play content."""
    clips = []
    skip_prefixes = ('condensed game', 'field view', 'japanese highlights')
    for item in items:
        title = item.get('title', '')
        if ' on ' in title.lower():
            continue
        url = None
        for pb in item.get('playbacks', []):
            if pb.get('name') == 'mp4Avc':
                url = pb['url']
                break
        if not url or 'darkroom-clips' in url:
            continue
        if title.lower().startswith(skip_prefixes):
            continue

        player_ids = [
            int(kw['value']) for kw in item.get('keywordsAll', [])
            if kw.get('type') == 'player_id' and kw.get('value', '').isdigit()
        ]
        keywords = [
            kw['value'] for kw in item.get('keywordsAll', [])
            if kw.get('type') == 'taxonomy'
        ]
        clips.append(Clip(
            title=title,
            description=item.get('description', ''),
            url=url,
            player_ids=player_ids,
            keywords=keywords,
        ))
    return clips


# ---------------------------------------------------------------------------
# Clip-to-play matching
# ---------------------------------------------------------------------------

def _match_clips_to_plays(plays: list[Play], clips: list[Clip]) -> list[Clip]:
    """
    Attach video URLs to plays where confident. Returns unmatched clips.
    A clip matches a play only when it names the batter AND shares an event keyword.
    """
    SCORING_KEYWORDS = {'home_run', 'single', 'double', 'triple', 'walk', 'sac_fly'}
    unmatched = []

    for clip in clips:
        matched = False
        for play in plays:
            if play.video_url:
                continue
            # Must name the batter
            if play.batter and play.batter.split()[0].lower() not in clip.title.lower():
                if play.batter and play.batter.split()[-1].lower() not in clip.title.lower():
                    continue
            # Must share an event type or be a scoring play
            event_match = (
                play.event_type in SCORING_KEYWORDS
                or play.is_scoring_play
                or play.event_type in clip.title.lower().replace(' ', '_')
            )
            if event_match:
                play.video_url = clip.url
                matched = True
                break
        if not matched:
            unmatched.append(clip)

    return unmatched


# ---------------------------------------------------------------------------
# Main load function
# ---------------------------------------------------------------------------

def load_game(game_id: int | str) -> Game:
    """Fetch (with caching) and return a fully parsed Game object."""
    gid = str(game_id)

    # --- play-by-play ---
    pbp = cache.load(gid, 'pbp')
    if pbp is None:
        raw = statsapi.get('game', {'gamePk': gid})
        pbp = raw['liveData']['plays']
        cache.save(gid, pbp, 'pbp')

    plays = _parse_plays(pbp.get('allPlays', []))

    # --- highlights ---
    hl_raw = cache.load(gid, 'highlights')
    if hl_raw is None:
        content = statsapi.get('game_content', {'gamePk': gid})
        hl_raw = content.get('highlights', {}).get('highlights', {}).get('items', [])
        cache.save(gid, hl_raw, 'highlights')

    clips = _parse_clips(hl_raw)
    unmatched = _match_clips_to_plays(plays, clips)

    # --- game metadata ---
    meta = cache.load(gid, 'meta')
    if meta is None:
        schedule = statsapi.schedule(game_id=int(gid))
        meta = schedule[0] if schedule else {}
        cache.save(gid, meta, 'meta')

    return Game(
        game_id=gid,
        home_team=meta.get('home_name', 'Home'),
        away_team=meta.get('away_name', 'Away'),
        date=meta.get('game_date', ''),
        plays=plays,
        clips=unmatched,
    )


def highlights_for_player(game_pk: int, mlbam_id: int) -> list[str]:
    """
    Return clip URLs from a game that feature a specific player (by MLBAM id).
    Reuses the existing highlight pipeline; only returns high-confidence matches
    where the player's id appears in the clip's keywordsAll player_id entries.
    """
    gid = str(game_pk)
    hl_raw = cache.load(gid, 'highlights')
    if hl_raw is None:
        content = statsapi.get('game_content', {'gamePk': gid})
        hl_raw = content.get('highlights', {}).get('highlights', {}).get('items', [])
        cache.save(gid, hl_raw, 'highlights')

    clips = _parse_clips(hl_raw)
    return [c.url for c in clips if mlbam_id in c.player_ids]
