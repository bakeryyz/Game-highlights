import json
import sys
from pathlib import Path

# Allow running as: python tests/test_mlb_client_cache.py
sys.path.insert(0, str(Path(__file__).parent.parent))

import statsapi

from data_sources import cache
from data_sources import mlb_client


def _full_payload():
    """Build a realistic statsapi 'game' payload around the saved fixture."""
    all_plays = json.loads(
        (Path(__file__).parent / "fixtures" / "sample_pbp.json").read_text()
    )
    return {
        "liveData": {
            "plays": {"allPlays": all_plays},
            "linescore": {"innings": [], "teams": {}, "outs": 0},
        }
    }


def _patch(monkeypatch, tmp_path, game_payload=None):
    """Point the cache at a temp dir and stub statsapi, counting only the
    'game' (full-payload) endpoint calls — the ones the dedup is about."""
    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path)
    calls = {"n": 0}
    payload = _full_payload() if game_payload is None else game_payload

    def fake_get(endpoint, params):
        if endpoint == "game":
            calls["n"] += 1
            return payload
        if endpoint == "game_content":
            return {}  # no highlight clips in this fixture
        raise AssertionError(f"unexpected statsapi.get endpoint: {endpoint}")

    monkeypatch.setattr(statsapi, "get", fake_get)
    monkeypatch.setattr(statsapi, "schedule", lambda **kw: [])  # no meta
    return calls


def test_load_game_then_linescore_share_one_fetch(monkeypatch, tmp_path):
    """load_game warms the 'full' cache; a later get_game_raw must hit it,
    so the pair costs a single 'game' API call (the live-poll dedup)."""
    calls = _patch(monkeypatch, tmp_path)

    game = mlb_client.load_game(824829)
    assert len(game.plays) == 7

    raw = mlb_client.get_game_raw(824829)  # mimics _get_linescore
    assert "linescore" in raw["liveData"]

    assert calls["n"] == 1, f"expected 1 'game' API call, got {calls['n']}"


def test_bypass_cache_forces_refetch(monkeypatch, tmp_path):
    calls = _patch(monkeypatch, tmp_path)

    mlb_client.get_game_raw(824829)                     # fetch + cache
    mlb_client.get_game_raw(824829)                     # cache hit
    assert calls["n"] == 1

    mlb_client.get_game_raw(824829, bypass_cache=True)  # forced refetch
    assert calls["n"] == 2


def test_load_game_survives_missing_livedata(monkeypatch, tmp_path):
    _patch(monkeypatch, tmp_path, game_payload={})  # no liveData key

    game = mlb_client.load_game(999999)
    assert game.plays == []
