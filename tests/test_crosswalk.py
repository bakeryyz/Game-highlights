import sys
from pathlib import Path
from unittest.mock import patch, MagicMock
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from data_sources.player_crosswalk import normalize_name, resolve_mlbam_id


# ---------------------------------------------------------------------------
# normalize_name
# ---------------------------------------------------------------------------

def test_normalize_accents():
    assert normalize_name("Ronald Acuña Jr.") == "Ronald Acuna"

def test_normalize_initials():
    result = normalize_name("A.J. Pollock")
    assert result == "aj Pollock"

def test_normalize_accented_vowel():
    assert normalize_name("José Ramírez") == "Jose Ramirez"

def test_normalize_sr_suffix():
    assert normalize_name("Ken Griffey Jr.") == "Ken Griffey"

def test_normalize_no_change_needed():
    assert normalize_name("Aaron Judge") == "Aaron Judge"


# ---------------------------------------------------------------------------
# resolve_mlbam_id — no network, everything monkeypatched
# ---------------------------------------------------------------------------

def _make_lookup_df(mlb_id):
    return pd.DataFrame({"mlb_id": [mlb_id]})


def test_resolve_prefers_baseball_id():
    """baseball_id returns a result → use it, don't call statsapi."""
    mock_lookup = MagicMock()
    mock_lookup.from_yahoo_ids.return_value = _make_lookup_df(660271)

    with patch.dict("sys.modules", {"baseball_id": MagicMock(Lookup=mock_lookup)}):
        with patch("data_sources.player_crosswalk._load_cache", return_value={}):
            with patch("data_sources.player_crosswalk._save_cache"):
                result = resolve_mlbam_id("Shohei Ohtani", yahoo_id=9999)

    assert result == 660271


def test_resolve_fallback_to_statsapi():
    """baseball_id returns empty → fall back to statsapi name lookup."""
    mock_lookup = MagicMock()
    mock_lookup.from_yahoo_ids.return_value = pd.DataFrame({"mlb_id": []})

    mock_hits = [{"id": "592450", "fullName": "Mookie Betts",
                  "currentTeam": {"abbreviation": "LAD"}}]

    with patch.dict("sys.modules", {"baseball_id": MagicMock(Lookup=mock_lookup)}):
        with patch("data_sources.player_crosswalk._load_cache", return_value={}):
            with patch("data_sources.player_crosswalk._save_cache"):
                with patch("statsapi.lookup_player", return_value=mock_hits):
                    result = resolve_mlbam_id("Mookie Betts", yahoo_id=1234)

    assert result == 592450


def test_resolve_returns_none_when_both_fail():
    """Both strategies fail → return None (never guess)."""
    mock_lookup = MagicMock()
    mock_lookup.from_yahoo_ids.return_value = pd.DataFrame({"mlb_id": []})

    with patch.dict("sys.modules", {"baseball_id": MagicMock(Lookup=mock_lookup)}):
        with patch("data_sources.player_crosswalk._load_cache", return_value={}):
            with patch("data_sources.player_crosswalk._save_cache"):
                with patch("statsapi.lookup_player", return_value=[]):
                    result = resolve_mlbam_id("Unknown Player", yahoo_id=0)

    assert result is None


def test_resolve_ambiguous_uses_team():
    """Multiple statsapi hits → disambiguate by pro_team."""
    mock_lookup = MagicMock()
    mock_lookup.from_yahoo_ids.return_value = pd.DataFrame({"mlb_id": []})

    hits = [
        {"id": "111", "fullName": "John Smith", "currentTeam": {"abbreviation": "NYY"}},
        {"id": "222", "fullName": "John Smith", "currentTeam": {"abbreviation": "BOS"}},
    ]

    with patch.dict("sys.modules", {"baseball_id": MagicMock(Lookup=mock_lookup)}):
        with patch("data_sources.player_crosswalk._load_cache", return_value={}):
            with patch("data_sources.player_crosswalk._save_cache"):
                with patch("statsapi.lookup_player", return_value=hits):
                    result = resolve_mlbam_id("John Smith", yahoo_id=0, pro_team="BOS")

    assert result == 222


def test_resolve_uses_disk_cache():
    """If the result is already in disk cache, don't call any external lib."""
    with patch("data_sources.player_crosswalk._load_cache", return_value={"yahoo:42": 660271}):
        result = resolve_mlbam_id("Anyone", yahoo_id=42)
    assert result == 660271


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    tests = [
        test_normalize_accents,
        test_normalize_initials,
        test_normalize_accented_vowel,
        test_normalize_sr_suffix,
        test_normalize_no_change_needed,
        test_resolve_prefers_baseball_id,
        test_resolve_fallback_to_statsapi,
        test_resolve_returns_none_when_both_fail,
        test_resolve_ambiguous_uses_team,
        test_resolve_uses_disk_cache,
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
