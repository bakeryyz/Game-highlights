import os
from flask import Flask, render_template, request, redirect, url_for, jsonify
from dotenv import load_dotenv

load_dotenv()

from core.highlight_detector import score_plays, is_highlight
from core.narrative import generate_narrative
from data_sources.mlb_client import find_games, load_game

app = Flask(__name__)


@app.route("/")
def index():
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


@app.route("/game/<int:game_id>")
def game(game_id):
    g = load_game(game_id)
    highlights = score_plays(g.plays)
    highlight_map = {h.play.index: h for h in highlights}

    # Group plays by inning label for the template
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

    return render_template("game.html", game=g, innings=innings)


@app.route("/narrative/<int:game_id>", methods=["POST"])
def narrative(game_id):
    g = load_game(game_id)
    text = generate_narrative(g)
    return jsonify({"text": text})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=False)
