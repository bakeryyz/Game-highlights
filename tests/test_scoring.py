import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.scoring import ScoringSettings, score_stat_line
from data_sources.mlb_live import _innings_to_float, _normalize_batting, _normalize_pitching


# ---------------------------------------------------------------------------
# _innings_to_float
# ---------------------------------------------------------------------------

def test_innings_to_float_thirds():
    assert abs(_innings_to_float("6.2") - (6 + 2/3)) < 1e-9

def test_innings_to_float_zero_frac():
    assert _innings_to_float("7.0") == 7.0

def test_innings_to_float_no_dot():
    assert _innings_to_float("9") == 9.0

def test_innings_to_float_one_third():
    assert abs(_innings_to_float("0.1") - (1/3)) < 1e-9

def test_innings_to_float_int_input():
    assert _innings_to_float(6) == 6.0


# ---------------------------------------------------------------------------
# _normalize_batting
# ---------------------------------------------------------------------------

def test_normalize_batting_singles_derived():
    raw = {
        "hits": 4, "doubles": 1, "triples": 0, "homeRuns": 1,
        "rbi": 2, "runs": 1, "baseOnBalls": 1, "hitByPitch": 0,
        "stolenBases": 1, "strikeOuts": 2,
    }
    line = _normalize_batting(raw)
    # singles = 4 - 1 - 0 - 1 = 2
    assert line["singles"] == 2
    assert line["doubles"] == 1
    assert line["homeRuns"] == 1
    assert line["rbi"] == 2
    assert line["walks"] == 1
    assert line["strikeouts_batter"] == 2

def test_normalize_batting_no_negative_singles():
    # Edge case: avoid negative singles if data is inconsistent
    raw = {"hits": 1, "doubles": 0, "triples": 0, "homeRuns": 2}
    line = _normalize_batting(raw)
    assert line["singles"] == 0


# ---------------------------------------------------------------------------
# score_stat_line — hitter
# ---------------------------------------------------------------------------

def test_score_hitter_basic():
    settings = ScoringSettings(weights={
        "singles": 1.0, "doubles": 2.0, "homeRuns": 4.0,
        "rbi": 1.0, "runs": 1.0, "stolenBases": 2.0,
    }, name="test")
    line = {"singles": 1, "doubles": 1, "homeRuns": 1, "rbi": 3, "runs": 2, "stolenBases": 0}
    pts = score_stat_line(line, settings)
    # 1*1 + 1*2 + 1*4 + 3*1 + 2*1 = 12.0
    assert pts == 12.0

def test_score_pitcher_negative_earned_runs():
    settings = ScoringSettings(weights={
        "inningsPitched": 3.0,
        "strikeouts_pitched": 1.0,
        "earnedRuns": -2.0,
    }, name="test")
    line = {"inningsPitched": 6.0, "strikeouts_pitched": 7, "earnedRuns": 3}
    pts = score_stat_line(line, settings)
    # 6*3 + 7*1 + 3*(-2) = 18 + 7 - 6 = 19.0
    assert pts == 19.0

def test_score_empty_line():
    settings = ScoringSettings.default()
    assert score_stat_line({}, settings) == 0.0

def test_score_unknown_keys_ignored():
    settings = ScoringSettings.default()
    line = {"unknownStat": 999, "anotherUnknown": 42}
    assert score_stat_line(line, settings) == 0.0

def test_score_missing_stats_default_zero():
    settings = ScoringSettings(weights={"homeRuns": 4.0, "rbi": 1.0}, name="test")
    line = {"homeRuns": 2}  # rbi missing
    assert score_stat_line(line, settings) == 8.0


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    tests = [
        test_innings_to_float_thirds,
        test_innings_to_float_zero_frac,
        test_innings_to_float_no_dot,
        test_innings_to_float_one_third,
        test_innings_to_float_int_input,
        test_normalize_batting_singles_derived,
        test_normalize_batting_no_negative_singles,
        test_score_hitter_basic,
        test_score_pitcher_negative_earned_runs,
        test_score_empty_line,
        test_score_unknown_keys_ignored,
        test_score_missing_stats_default_zero,
    ]
    passed = failed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
            passed += 1
        except Exception as e:
            print(f"  FAIL  {t.__name__}: {e}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
