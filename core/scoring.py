from dataclasses import dataclass

DEFAULT_WEIGHTS: dict[str, float] = {
    "singles": 1.0,
    "doubles": 2.0,
    "triples": 3.0,
    "homeRuns": 4.0,
    "rbi": 1.0,
    "runs": 1.0,
    "walks": 0.5,
    "hbp": 0.5,
    "stolenBases": 2.0,
    "strikeouts_batter": -0.5,
    "inningsPitched": 3.0,
    "strikeouts_pitched": 1.0,
    "earnedRuns": -1.0,
    "hits_allowed": -0.5,
    "walks_allowed": -0.5,
    "wins": 5.0,
    "saves": 5.0,
}


@dataclass
class ScoringSettings:
    weights: dict[str, float]
    name: str

    @classmethod
    def default(cls) -> "ScoringSettings":
        return cls(weights=dict(DEFAULT_WEIGHTS), name="default")

    def weight(self, key: str) -> float:
        return self.weights.get(key, 0.0)


def score_stat_line(line: dict, settings: ScoringSettings) -> float:
    """
    Pure function. Multiply each stat value by its scoring weight and sum.
    Unknown keys are ignored. Missing stats default to 0.
    Supports negative weights (e.g. earned runs).
    """
    total = 0.0
    for key, value in line.items():
        w = settings.weight(key)
        if w != 0.0:
            total += float(value) * w
    return round(total, 2)
