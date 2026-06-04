from data_sources.mlb_client import find_game, get_plays, get_highlights

# Test find_game
print("=== find_game ===")
games = find_game("2026-06-03", "Braves")
for g in games:
    print(g['summary'])

# Test get_plays
print("\n=== get_plays (first 5 plays) ===")
plays = get_plays(824917)
for play in plays[:5]:
    print(play)

# Test get_highlights
print("\n=== get_highlights ===")
highlights = get_highlights(824917)
for title, url in highlights.items():
    print(f"{title}: {url}")