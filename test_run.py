from data_sources.mlb_client import find_game, find_games, load_game
from core.highlight_detector import score_plays, top_moments

# Test find_game (original simple version)
print("=== find_game ===")
games = find_game("2026-06-03", "Braves")
for g in games:
    print(g['summary'])

# Test find_games (natural language)
print("\n=== find_games (natural language) ===")
candidates = find_games("Braves June 3 2026")
for c in candidates:
    print(c.label)

# Test load_game
print("\n=== load_game ===")
game = load_game(824917)
print(f"{game.away_team} @ {game.home_team} — {game.date}")
print(f"Final: {game.final_score_label}")
print(f"Plays: {len(game.plays)}")
print(f"Unmatched clips: {len(game.clips)}")

# Test highlight detector
print("\n=== top 5 highlights ===")
for h in top_moments(game.plays, n=5):
    print(f"  [{h.play.inning_label}] {h.play.description[:60]}...")
    print(f"    score={h.score:.1f}  reasons={h.reasons}")
