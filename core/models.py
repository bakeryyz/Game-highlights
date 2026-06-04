from dataclasses import dataclass


@dataclass
class Play:
    index: int
    inning: str
    description: str
    home_score: int
    away_score: int
    is_scoring_play: bool
    video_url: str | None


@dataclass
class Highlight:
    play: Play
    score: float
    reasons: list[str]


@dataclass
class Game:
    game_id: str
    home_team: str
    away_team: str
    date: str
    final_score: tuple[int, int]
    plays: list[Play]
