import statsapi

from core.models import Game, Play


def find_game(date: str, team: str) -> list[dict]:
    # Fetch all MLB games on this date from the Stats API
    games = statsapi.schedule(date=date, sportId=1)
    
    # Keep only games where the team name appears in either home or away team
    # .lower() on both sides makes the match case-insensitive ("braves" matches "Atlanta Braves")
    results = [g for g in games if team.lower() in g['home_name'].lower() or team.lower() in g['away_name'].lower()]
    
    return results



def _match_highlight(description: str, highlights: dict) -> str | None:
    # Use the first two words of the description as the player's name to match against
    name = " ".join(description.split()[:2]).lower()
    for title, url in highlights.items():
        if name in title.lower():
            return url
    return None


def get_plays(game_id: str) -> list[Play]:
    # Fetch the full game data — contains play-by-play, linescore, boxscore, etc.
    game = statsapi.get('game', {'gamePk': game_id})

    # allPlays is a list of every at-bat in the game, in order
    raw_plays = game['liveData']['plays']['allPlays']

    # Fetch highlight clips so we can attach video URLs to matching plays
    highlights = get_highlights(game_id)

    plays = []
    for p in raw_plays:
        play = Play(
            # atBatIndex is the sequential number of this play in the game (0, 1, 2...)
            index=p['about']['atBatIndex'],
            # halfInning is "top" or "bottom", inning is the number — combine into "Top 1"
            inning=f"{p['about']['halfInning'].capitalize()} {p['about']['inning']}",
            # Human-readable description of what happened, e.g. "Aaron Judge homers..."
            description=p['result']['description'],
            # Scores reflect the state AFTER this play completed
            home_score=p['result']['homeScore'],
            away_score=p['result']['awayScore'],
            # True if a run scored on this play
            is_scoring_play=p['about']['isScoringPlay'],
            # Match this play's description against highlight titles to find a video clip
            video_url=_match_highlight(p['result']['description'], highlights)
        )
        plays.append(play)
    return plays


def get_highlights(game_id: str) -> dict:
    content = statsapi.get('game_content', {'gamePk': game_id})
    items = content['highlights']['highlights']['items']
    
    highlights = {}
    for item in items:
        title = item['title']
        for p in item['playbacks']:
            if p['name'] == 'mp4Avc':
                url = p['url']
                # Skip non-play content: darkroom clips are lineups/stats, " on " signals interviews
                if 'darkroom-clips' not in url and ' on ' not in title.lower():
                    highlights[title] = url

    return highlights
