# Game Highlights

A web app that tells the full story of an MLB game through stats — a scrollable timeline of every play, with the biggest moments automatically surfaced and real highlight clips playable inline.

## Tech Stack

- **Python** + **Streamlit** — frontend
- **MLB Stats API** — free, keyless; provides play-by-play and highlight clip links
- **Claude API** — writes the game narrative recap
- **Rules-based highlight detector** — deterministic, no AI, fully unit-tested

## Project Structure

```
game-highlights/
├── data_sources/       # API clients and caching
├── core/               # Data models, highlight detector, narrative generator
├── tests/              # Unit tests and saved API fixtures
├── app.py              # Streamlit UI
└── data/game_cache/    # Cached raw API responses (gitignored)
```

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then add your ANTHROPIC_API_KEY
streamlit run app.py
```

## Status

Work in progress — baseball (MLB) first.
