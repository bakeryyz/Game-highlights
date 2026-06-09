import json
import sys
from pathlib import Path

# Allow running as: python tests/test_detector.py
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.highlight_detector import is_highlight, score_plays, top_moments
from core.models import Play
from data_sources.mlb_client import _extract_date, _parse_plays


def load_fixture() -> list[Play]:
    fixture_path = Path(__file__).parent / "fixtures" / "sample_pbp.json"
    raw = json.loads(fixture_path.read_text())
    return _parse_plays(raw)


# ---------------------------------------------------------------------------
# Parse tests
# ---------------------------------------------------------------------------

def test_parse_count():
    plays = load_fixture()
    assert len(plays) == 7, f"Expected 7 plays, got {len(plays)}"


def test_parse_fields():
    plays = load_fixture()
    hr = plays[1]
    assert hr.event_type == 'home_run'
    assert hr.inning == 3
    assert hr.half == 'top'
    assert hr.batter == 'Shohei Ohtani'
    assert hr.away_score == 2
    assert hr.home_score == 0
    assert hr.rbi == 2
    assert hr.captivating_index == 82


# ---------------------------------------------------------------------------
# Detector tests
# ---------------------------------------------------------------------------

def test_leadoff_strikeout_not_flagged():
    plays = load_fixture()
    highlights = score_plays(plays)
    flagged_indices = {h.play.index for h in highlights}
    assert 0 not in flagged_indices, "Leadoff strikeout should NOT be flagged"


def test_tying_run_fires():
    plays = load_fixture()
    highlights = score_plays(plays)
    tying_play_index = 3  # Jarren Duran double ties it 2-2
    tying = next((h for h in highlights if h.play.index == tying_play_index), None)
    assert tying is not None, "Tying run play should be flagged"
    assert 'tying run' in tying.reasons


def test_lead_change_fires():
    plays = load_fixture()
    highlights = score_plays(plays)
    # Play 5: Ohtani 8th-inning HR — away was trailing (2-3), now leads (4-3)
    lead_change = next((h for h in highlights if h.play.index == 5), None)
    assert lead_change is not None, "Lead change play should be flagged"
    assert 'lead change' in lead_change.reasons


def test_8th_inning_hr_is_top_moment():
    plays = load_fixture()
    best = top_moments(plays, n=1)
    assert len(best) == 1
    assert best[0].play.index == 5, "8th-inning Ohtani HR should be the top moment"


def test_9th_inning_strikeout_flagged():
    plays = load_fixture()
    highlights = score_plays(plays)
    final_k = next((h for h in highlights if h.play.index == 6), None)
    assert final_k is not None, "9th-inning game-ending strikeout should be flagged"
    assert 'late game' in final_k.reasons


def test_go_ahead_run_fires():
    plays = load_fixture()
    highlights = score_plays(plays)
    # Play 4: home team goes from tie to lead
    go_ahead = next((h for h in highlights if h.play.index == 4), None)
    assert go_ahead is not None
    assert 'go-ahead run' in go_ahead.reasons


# ---------------------------------------------------------------------------
# Date extraction tests
# ---------------------------------------------------------------------------

def test_extract_date_iso():
    assert _extract_date("2024-06-01") == "2024-06-01"


def test_extract_date_slash():
    assert _extract_date("6/1/24") == "2024-06-01"
    assert _extract_date("6/1/2024") == "2024-06-01"


def test_extract_date_natural():
    assert _extract_date("June 1st 2024") == "2024-06-01"
    assert _extract_date("June 1, 2024") == "2024-06-01"
    assert _extract_date("Dodgers vs Red Sox June 1 2024") == "2024-06-01"


def test_extract_date_none():
    assert _extract_date("Dodgers vs Red Sox") is None


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    tests = [
        test_parse_count,
        test_parse_fields,
        test_leadoff_strikeout_not_flagged,
        test_tying_run_fires,
        test_lead_change_fires,
        test_8th_inning_hr_is_top_moment,
        test_9th_inning_strikeout_flagged,
        test_go_ahead_run_fires,
        test_extract_date_iso,
        test_extract_date_slash,
        test_extract_date_natural,
        test_extract_date_none,
    ]
    passed = 0
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"  FAIL  {t.__name__}: {e}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
