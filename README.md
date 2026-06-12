# Game Highlights

A web app that tells the full story of an MLB game through stats — a scrollable timeline of every play with the biggest moments automatically surfaced, real highlight clips playable inline, Statcast metrics, and a fantasy advisor chat.

## Features

- **Scoreboard** — browse games by date with a 7-day strip.
- **Game timeline** — every play, grouped by inning, with the biggest moments auto-flagged by a rules-based highlight detector. Live games poll for updates.
- **Highlight clips** — real MLB video matched to the plays that earned them.
- **Game recap** — a short prose narrative written by Claude (falls back to a stats-only recap if no key is set).
- **Statcast** — exit velocity / barrel / pitch-arsenal leaderboards and per-player percentile profiles (via pybaseball / Baseball Savant).
- **Fantasy advisor** — a streaming chat that answers questions about your Yahoo fantasy team (powered by Groq).

## Tech Stack

- **Python + Flask** — backend and server-rendered Jinja templates
- **MLB Stats API** — free, keyless; play-by-play and highlight clip links
- **pybaseball** — Statcast / Baseball Savant metrics
- **Claude API** — writes the game narrative recap
- **Groq** (Llama 3.3) — powers the fantasy advisor chat
- **Yahoo Fantasy API** — optional, for the fantasy pages
- **Rules-based highlight detector** — deterministic, no AI, fully unit-tested

## Project Structure

```
game-highlights/
├── data_sources/       # API clients, caching, Statcast, fantasy providers
├── core/               # Data models, highlight detector, narrative generator
├── templates/          # Jinja2 templates
├── static/             # CSS
├── tests/              # Unit tests and saved API fixtures
├── app.py              # Flask app (routes + JSON APIs)
└── data/               # Cached raw API responses (gitignored)
```

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then fill in the keys you want (all optional)
python app.py          # or: make run
```

The app serves at **http://localhost:5001**.

### Environment variables

All keys are optional — the app runs without them, degrading the relevant feature gracefully:

- `ANTHROPIC_API_KEY` — enables the Claude-written game recap. Without it you get a deterministic stats recap.
- `GROQ_API_KEY` — enables the `/chat` fantasy advisor.
- Yahoo (`YAHOO_*`) — enables the `/fantasy` pages. See `.env.example`.

## Tests

```bash
pytest tests/        # 34 unit tests, fully offline (uses saved fixtures)
```

`test_run.py` is a manual integration smoke check that hits the live MLB API —
run it directly with `python test_run.py`, not under pytest.

## Status

Work in progress — baseball (MLB) first.
