from dataclasses import dataclass, field


@dataclass
class Play:
    index: int
    inning: int
    half: str               # "top" or "bottom"
    description: str
    event: str              # "Home Run", "Single", "Strikeout", etc.
    event_type: str         # "home_run", "single", "strikeout", etc.
    away_score: int
    home_score: int
    is_scoring_play: bool
    is_out: bool
    rbi: int
    batter: str
    pitcher: str
    batter_id: int | None = None
    pitcher_id: int | None = None
    captivating_index: int = 0
    video_url: str | None = None
    # Statcast fields (populated for completed games)
    launch_speed: float | None = None
    launch_angle: float | None = None
    hit_distance: int | None = None
    pitch_speed: float | None = None
    pitch_type: str | None = None

    @property
    def inning_label(self) -> str:
        half = "Top" if self.half == "top" else "Bot"
        return f"{half} {self.inning}"

    @property
    def score_label(self) -> str:
        return f"{self.away_score}-{self.home_score}"


@dataclass
class Highlight:
    play: Play
    score: float
    reasons: list[str]


@dataclass
class Clip:
    title: str
    description: str
    url: str
    player_ids: list[int] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)


@dataclass
class Game:
    game_id: str
    home_team: str
    away_team: str
    date: str
    plays: list[Play] = field(default_factory=list)
    clips: list[Clip] = field(default_factory=list)  # unmatched highlight clips

    @property
    def final_home_score(self) -> int:
        return self.plays[-1].home_score if self.plays else 0

    @property
    def final_away_score(self) -> int:
        return self.plays[-1].away_score if self.plays else 0

    @property
    def final_score_label(self) -> str:
        return f"{self.away_team} {self.final_away_score}, {self.home_team} {self.final_home_score}"


@dataclass
class GameCandidate:
    game_id: int
    away_name: str
    home_name: str
    date: str
    away_score: int
    home_score: int
    status: str
    game_num: int  # 1 or 2 for doubleheaders

    @property
    def label(self) -> str:
        score = f"{self.away_score}-{self.home_score}" if self.status == "Final" else self.status
        suffix = f" (Game {self.game_num})" if self.game_num > 1 else ""
        return f"{self.away_name} @ {self.home_name} — {self.date} [{score}]{suffix}"
