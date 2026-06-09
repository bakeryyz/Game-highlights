import os
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from flask import Flask, render_template, request, redirect, url_for, jsonify
from dotenv import load_dotenv

load_dotenv()

import statsapi
from core.highlight_detector import score_plays, is_highlight
from core.narrative import generate_narrative
from data_sources.mlb_client import find_games, load_game

app = Flask(__name__)


@app.route("/")
def index():
    return redirect(url_for("scoreboard"))


@app.route("/scoreboard")
def scoreboard():
    date_str = request.args.get("date", date.today().strftime("%Y-%m-%d"))
    try:
        selected = date.fromisoformat(date_str)
    except ValueError:
        selected = date.today()

    # Build a 7-day strip centred on today
    today = date.today()
    strip = [today + timedelta(days=i) for i in range(-3, 4)]

    games = statsapi.schedule(date=selected.strftime("%Y-%m-%d"), sportId=1)

    # Convert UTC game times to local system timezone
    local_tz = datetime.now().astimezone().tzinfo
    tz_name = datetime.now().astimezone().strftime("%Z")
    for g in games:
        try:
            utc_dt = datetime.fromisoformat(g['game_datetime'].replace("Z", "+00:00"))
            local_dt = utc_dt.astimezone(local_tz)
            g['local_time'] = local_dt.strftime("%-I:%M %p")
        except Exception:
            g['local_time'] = g.get('game_datetime', '')[:16]

    return render_template(
        "scoreboard.html",
        games=games,
        selected=selected,
        strip=strip,
        today=today,
        timedelta=timedelta,
        tz_name=tz_name,
    )


@app.route("/search-page")
def search_page():
    return render_template("index.html")


@app.route("/search")
def search():
    query = request.args.get("q", "").strip()
    if not query:
        return redirect(url_for("index"))

    candidates = find_games(query)

    if not candidates:
        return render_template("index.html", error="No games found. Try adding a date, e.g. 'Braves June 3 2026'.")

    if len(candidates) == 1:
        return redirect(url_for("game", game_id=candidates[0].game_id))

    return render_template("candidates.html", candidates=candidates, query=query)


def _build_innings(g):
    highlights = score_plays(g.plays)
    highlight_map = {h.play.index: h for h in highlights}
    innings = {}
    for play in g.plays:
        label = play.inning_label
        if label not in innings:
            innings[label] = []
        innings[label].append({
            "play": play,
            "highlight": highlight_map.get(play.index),
            "is_flagged": (h := highlight_map.get(play.index)) is not None and is_highlight(h),
        })
    return innings


def _game_status(game_id):
    schedule = statsapi.schedule(game_id=int(game_id))
    return schedule[0] if schedule else {}


@app.route("/game/<int:game_id>")
def game(game_id):
    g = load_game(game_id)
    innings = _build_innings(g)
    meta = _game_status(game_id)
    is_live = meta.get('status') == 'In Progress'
    current_inning = meta.get('current_inning', '')
    inning_state = meta.get('inning_state', '')

    return render_template(
        "game.html",
        game=g,
        innings=innings,
        is_live=is_live,
        current_inning=current_inning,
        inning_state=inning_state,
    )


@app.route("/api/game/<int:game_id>/state")
def game_state(game_id):
    """JSON endpoint polled by the frontend during live games."""
    from data_sources import cache as game_cache
    # Always bypass cache for live state
    game_cache.clear(str(game_id), 'pbp')
    g = load_game(game_id)
    innings = _build_innings(g)
    meta = _game_status(game_id)
    is_live = meta.get('status') == 'In Progress'

    return jsonify({
        "is_live": is_live,
        "away_score": g.final_away_score,
        "home_score": g.final_home_score,
        "current_inning": meta.get('current_inning', ''),
        "inning_state": meta.get('inning_state', ''),
        "plays": [
            {
                "index": item["play"].index,
                "inning_label": item["play"].inning_label,
                "description": item["play"].description,
                "score_label": item["play"].score_label,
                "is_flagged": item["is_flagged"],
                "reasons": item["highlight"].reasons if item["highlight"] else [],
                "video_url": item["play"].video_url,
            }
            for inning_plays in innings.values()
            for item in inning_plays
        ],
    })


@app.route("/narrative/<int:game_id>", methods=["POST"])
def narrative(game_id):
    g = load_game(game_id)
    text = generate_narrative(g)
    return jsonify({"text": text})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=False)
