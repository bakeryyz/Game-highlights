import statsapi

# STEP 1: Find games on a specific date
# schedule() returns a list of game dicts, each with a game_id, teams, scores, status, etc.
games = statsapi.schedule(date="2026-6-3", sportId=1)
print(games)

# STEP 2: Fetch full data for one game by its game_id (called gamePk in the API)
# This returns two main sections:
#   - gameData: teams, venue, weather, officials
#   - liveData: plays, linescore, boxscore
game = statsapi.get("game", {"gamePk": 824917})
print(game.keys())

# STEP 3: Drill into liveData -> plays -> allPlays to get every at-bat
print(game['liveData'].keys())           # plays, linescore, boxscore, decisions, leaders
print(game['liveData']['plays'].keys())  # allPlays, currentPlay, scoringPlays, playsByInning

# Each play has:
#   result.description  -> "Ozzie Albies homers on a fly ball to left field."
#   result.event        -> "Home Run", "Single", "Strikeout", etc.
#   about.inning        -> inning number
#   about.halfInning    -> "top" or "bottom"
#   about.isScoringPlay -> True/False
#   result.awayScore / result.homeScore -> score after the play
print(game['liveData']['plays']['allPlays'][0])

# STEP 4: Fetch the media/content for the same game (separate endpoint)
# This has highlights, editorial recaps, images — not play data
content = statsapi.get('game_content', {'gamePk': 824917})
print(content.keys())                              # editorial, media, highlights, summary, gameNotes

# STEP 5: Drill into highlights to find the video clips
print(content['highlights'].keys())                # scoreboard, gameCenter, milestone, highlights, live
print(content['highlights']['highlights'].keys())  # items

# Each item in 'items' is one highlight clip with a title, description, and playbacks list
print(content['highlights']['highlights']['items'][0])

# STEP 6: Extract the actual playable video URL
# 'playbacks' has multiple formats: hlsCloud (.m3u8 stream), mp4Avc (.mp4), highBit, etc.
# mp4Avc is the one that works with st.video() in Streamlit
item = content['highlights']['highlights']['items'][0]
for p in item['playbacks']:
    if p['name'] == 'mp4Avc':
        print(p['url'])
# Result: a real .mp4 URL hosted on mlb-cuts-diamond.mlb.com — confirmed playable in browser