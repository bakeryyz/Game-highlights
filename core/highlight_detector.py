from core.models import Highlight, Play


def score_plays(plays: list[Play]) -> list[Highlight]:
    """
    Pure function — no I/O. Walks the play list and scores each play for
    highlight-worthiness. Returns a Highlight for every play that earns points.
    """
    highlights = []

    for i, play in enumerate(plays):
        pts = 0.0
        reasons: list[str] = []
        prev = plays[i - 1] if i > 0 else None

        # --- home run ---
        if play.event_type == 'home_run' or 'homers' in play.description.lower():
            pts += 3.0
            reasons.append('home run')

        # --- scoring play ---
        if play.is_scoring_play:
            pts += 1.0
            reasons.append('scoring play')

        # --- RBI value ---
        if play.rbi >= 2:
            pts += 1.0
            reasons.append(f'{play.rbi} RBI')

        # --- tying run ---
        if prev and play.is_scoring_play:
            if prev.away_score != prev.home_score and play.away_score == play.home_score:
                pts += 2.0
                reasons.append('tying run')

        # --- lead change (only when someone was actually leading before — tie→lead is go-ahead) ---
        if prev and prev.away_score != prev.home_score and play.away_score != play.home_score:
            was_away_leading = prev.away_score > prev.home_score
            is_away_leading = play.away_score > play.home_score
            if was_away_leading != is_away_leading:
                pts += 2.0
                reasons.append('lead change')

        # --- go-ahead run (takes lead from a tie) ---
        if prev and prev.away_score == prev.home_score and play.away_score != play.home_score:
            pts += 1.5
            reasons.append('go-ahead run')

        # --- late inning leverage ---
        if play.inning >= 8:
            pts += 1.0
            reasons.append('late game')

        # --- late-inning called/swinging strikeout ---
        if play.inning >= 8 and play.event_type in ('strikeout', 'strikeout_double_play'):
            pts += 0.5
            reasons.append('late K')

        # --- captivating index bonus (MLB's own excitement score) ---
        if play.captivating_index >= 70:
            pts += play.captivating_index / 100.0
            reasons.append(f'captivating ({play.captivating_index})')

        if pts > 0:
            highlights.append(Highlight(play=play, score=pts, reasons=reasons))

    return sorted(highlights, key=lambda h: h.score, reverse=True)


def detect_highlights(plays: list[Play]) -> list[Highlight]:
    """Alias kept for backward compatibility."""
    return score_plays(plays)


def is_highlight(h: Highlight, threshold: float = 3.0) -> bool:
    return h.score >= threshold


def top_moments(plays: list[Play], n: int = 5) -> list[Highlight]:
    return score_plays(plays)[:n]
