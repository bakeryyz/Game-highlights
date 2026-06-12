# Game Highlights — Code Review & Improvement Plan

_Reviewed June 11, 2026. 34 unit tests pass; findings below are from reading the code and running the suite._

## TL;DR

The core is genuinely good — the highlight detector is clean, pure, and well-tested, and the MLB data layer is sensible. The problems are mostly at the edges: **the dependency list is broken** (a fresh `pip install -r requirements.txt` won't even boot the app), **the docs describe a different app than the one that exists** (README says Streamlit + Claude; it's actually Flask + Groq for chat), and **one config bug silently disables the Claude recap**. Fix those and you've got a solid, demoable product.

---

## 1. Bugs that break things (fix first)

### 1a. `requirements.txt` is missing 4 packages — the app won't start on a clean install
The code imports `pandas`, `pybaseball`, `groq`, and `requests`, none of which are listed. `pandas` is imported at the top of `data_sources/statcast.py`, which `app.py` imports at module load — so on a fresh environment **the app crashes on startup**, not just on a feature page.

Add to `requirements.txt`:
```
pandas
pybaseball
groq
requests
```
And remove `streamlit` and `streamlit-autorefresh` — they're leftovers from the old version and no longer imported anywhere.

### 1b. The Claude recap never runs — wrong env var name
`core/narrative.py` reads `os.getenv('CLAUDE_API_KEY')`, but `.env.example` (and the README) tell you to set `ANTHROPIC_API_KEY`. So `api_key` is always `None`, and `generate_narrative` silently falls back to the stats-only recap every time. The LLM path is effectively dead code right now.

Fix: read `ANTHROPIC_API_KEY` (the name the Anthropic SDK uses by default anyway):
```python
api_key = os.getenv('ANTHROPIC_API_KEY')
```

### 1c. `test_run.py` breaks the whole test suite
It's named like a test, so `pytest` collects it, but it makes a live `statsapi` network call at **import time** (module level). In any sandboxed or offline CI this raises during collection and takes down the entire run — `pytest` reports 0 tests instead of the 34 that actually pass. (`pytest tests/` works; bare `pytest` doesn't.)

Fix: it's a manual integration script, not a unit test. Either rename it (`scripts/smoke_check.py`) and move it out of the collection path, or guard the calls under `if __name__ == "__main__":`.

---

## 2. Reliability & performance

### 2a. Live polling makes 3 API calls per tick
For each poll of `/api/game/<id>/state`, the code: clears the pbp cache and refetches the full game (`load_game`), then `_get_linescore` fetches the **same** `statsapi.get('game', ...)` payload again, then `_game_status` calls `statsapi.schedule`. The linescore is already inside the game payload `load_game` just fetched — you're paying for it twice. Thread the already-fetched `raw`/`liveData` through instead of re-requesting. At a few-second poll interval this is the difference between polite and rate-limited.

### 2b. `cache.py` has no TTL or size bound
Game data is cached forever by file. Fine for finals, but there's no expiry for anything that might change, and no cleanup — `data/game_cache/` grows without limit. Consider a TTL (you already have the pattern in `statcast.py`'s `_cache_get`) and reuse it here so the two cache layers behave consistently.

### 2c. Broad `except Exception: pass` hides failures
`cache.py`, the Statcast layer, and the chat/fantasy routes swallow errors silently. Great for resilience, bad for debugging — when Statcast quietly returns `{}` because `pybaseball` isn't installed (see 1a), you get blank pages with no signal. At minimum log at `warning` everywhere you currently `pass` (statcast already does this well; copy that pattern into `cache.py` and `narrative.py`).

---

## 3. Docs accuracy (the README describes a different app)

The README has drifted significantly from the code:

- **"Python + Streamlit — frontend"** → it's **Flask** now (`app.py` is all Flask routes + Jinja templates). Setup says `streamlit run app.py`; the real command is `python app.py` (or `make run`), serving on **port 5001**.
- **Tech stack omits** the Statcast/Baseball Savant integration, the Yahoo fantasy advisor, and the **Groq-powered chat** (`llama-3.3-70b-versatile`) — three of the biggest features. The README still reads like a single-page MLB recap tool.
- **"Claude API — writes the game narrative"** is true (when 1b is fixed), but chat uses Groq, not Claude — worth saying so the two LLM dependencies are clear.
- `.env.example` lists `ANTHROPIC_API_KEY`, `CLAUDE_MODEL`, and Yahoo vars, but **not** `GROQ_API_KEY` / `GROQ_MODEL`, which `/chat` requires. Add them.

A 10-minute README pass to match reality will save the next person (or you, in three months) real confusion.

---

## 4. Code quality / cleanup

- **`spike.py`** is an exploratory scratch script (literally "STEP 1… STEP 6" comments). It documents how the MLB API works, which is useful — but move it to `docs/` or `scripts/` so it's not mistaken for app code in the repo root.
- **Dead code in `highlight_detector.py`**: `detect_highlights` is an unused backward-compat alias. Fine to keep, but tag it or drop it.
- **`scoring.example.json`** and the fantasy scoring system exist but I don't see them wired into the highlight detector — confirm they're actually used by the fantasy path and not orphaned.
- **Magic numbers in the detector** (HR = 3.0, late game ≥ 8, captivating ≥ 70, etc.) are reasonable but undocumented. A short comment block or a `SCORING` constants dict at the top would make tuning easier and self-documenting.
- **Timezone handling in `/scoreboard`** uses the server's local tz (`datetime.now().astimezone()`). Fine locally; if you ever deploy, game times will render in the server's zone, not the user's. Worth noting now.

---

## 5. Feature / UX ideas (once the above is solid)

- **Surface the highlight score visually.** You compute a numeric leverage score and reasons per play but the UI only flags a binary "is_highlight". A small intensity bar or a "win-probability-style" sparkline down the timeline would make the "story of the game" pitch land much harder.
- **Auto-generate a shareable recap card.** You already have the narrative + top moments + final score — render them to a single image/OG card so a game is one-click shareable. High demo value.
- **"Jump to the big moments" rail.** A sticky strip of the top 3–5 flagged plays at the top of the game page, each scrolling to its spot in the timeline. Turns the scroll into a highlight reel.
- **Graceful degradation messaging.** When Statcast or fantasy data is unavailable (no key, no pybaseball), show a small "data unavailable" note instead of a blank section — ties into the silent-failure issue in 2c.
- **Multi-sport groundwork.** README says "baseball first." The `core` models are MLB-shaped (innings, RBI). If multi-sport is real, that's the abstraction boundary to plan now rather than retrofit.

---

## Suggested order of attack

1. **Dependencies + env vars** (1a, 1b, 3's `.env`) — makes the app actually run for a new user. ~30 min.
2. **`test_run.py`** (1c) — gets CI/green checkmarks honest. ~10 min.
3. **README rewrite** (3) — cheap, high clarity payoff.
4. **Live-poll de-duplication** (2a) — the one real performance fix.
5. Then cleanup (4) and features (5) as appetite allows.

The foundation is strong — this is mostly drift cleanup, not rework.
