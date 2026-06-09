import os

from core.highlight_detector import is_highlight, score_plays
from core.models import Game, Highlight
from data_sources import cache

CLAUDE_MODEL = os.getenv('CLAUDE_MODEL', 'claude-sonnet-4-6')


def _stats_recap(game: Game, highlights: list[Highlight]) -> str:
    """Deterministic fallback recap — no LLM required."""
    top = [h for h in highlights if is_highlight(h)][:5]
    lines = [f"{game.away_team} vs {game.home_team} — {game.date}",
             f"Final: {game.final_score_label}", ""]
    if top:
        lines.append("Key moments:")
        for h in top:
            lines.append(f"  • {h.play.inning_label}: {h.play.description} ({', '.join(h.reasons)})")
    return "\n".join(lines)


def generate_narrative(game: Game) -> str:
    """
    Build a short prose recap using Claude. Falls back to a stats-only recap
    if CLAUDE_API_KEY is missing or the API call fails.
    """
    cached = cache.load(game.game_id, 'narrative')
    if cached and isinstance(cached, dict) and 'text' in cached:
        return cached['text']

    highlights = score_plays(game.plays)
    top = [h for h in highlights if is_highlight(h)][:6]

    api_key = os.getenv('CLAUDE_API_KEY')
    if not api_key:
        return _stats_recap(game, highlights)

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)

        moments_text = "\n".join(
            f"- {h.play.inning_label}: {h.play.description} (reasons: {', '.join(h.reasons)})"
            for h in top
        )
        prompt = (
            f"Write a short, punchy 3-4 sentence recap of this MLB game. "
            f"Lead with the 2-3 biggest moments. Be specific about players and plays.\n\n"
            f"Game: {game.away_team} at {game.home_team}, {game.date}\n"
            f"Final score: {game.final_score_label}\n\n"
            f"Top moments:\n{moments_text}"
        )

        message = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}],
        )
        text = message.content[0].text
        cache.save(game.game_id, {'text': text}, 'narrative')
        return text

    except Exception:
        return _stats_recap(game, highlights)
