import os
from datetime import date, datetime, timedelta
from flask import Flask, render_template, request, redirect, url_for, jsonify
from dotenv import load_dotenv

load_dotenv()

import statsapi
from core.highlight_detector import score_plays, is_highlight
from core.narrative import generate_narrative
from data_sources.mlb_client import find_games, get_game_raw, load_game
from data_sources import statcast as sc_module

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


def _get_linescore(game_id):
    """Return a structured linescore dict ready for the template.

    Reuses the cached full game payload (populated by load_game) instead of
    making a second API call for the same data.
    """
    raw = get_game_raw(game_id)
    ls = raw['liveData']['linescore']
    innings = [
        {
            'num': inn['num'],
            'away': inn.get('away', {}).get('runs', '-'),
            'home': inn.get('home', {}).get('runs', '-'),
        }
        for inn in ls.get('innings', [])
    ]
    # Pad to at least 9 innings for display
    played = len(innings)
    for n in range(played + 1, 10):
        innings.append({'num': n, 'away': '-', 'home': '-'})

    teams = ls.get('teams', {})
    return {
        'innings': innings,
        'away': {
            'r': teams.get('away', {}).get('runs', 0),
            'h': teams.get('away', {}).get('hits', 0),
            'e': teams.get('away', {}).get('errors', 0),
        },
        'home': {
            'r': teams.get('home', {}).get('runs', 0),
            'h': teams.get('home', {}).get('hits', 0),
            'e': teams.get('home', {}).get('errors', 0),
        },
        'outs': ls.get('outs', 0),
    }


@app.route("/game/<int:game_id>")
def game(game_id):
    g = load_game(game_id)
    meta = _game_status(game_id)
    is_live = meta.get('status') == 'In Progress'

    # Enrich completed (or final) games with Statcast metrics
    if not is_live and g.plays:
        sc_module.enrich_plays(game_id, g.plays)

    innings = _build_innings(g)
    current_inning = meta.get('current_inning', '')
    inning_state = meta.get('inning_state', '')
    linescore = _get_linescore(game_id)

    return render_template(
        "game.html",
        game=g,
        innings=innings,
        is_live=is_live,
        current_inning=current_inning,
        inning_state=inning_state,
        linescore=linescore,
    )


@app.route("/api/game/<int:game_id>/state")
def game_state(game_id):
    """JSON endpoint polled by the frontend during live games."""
    # Bypass the cache for live state: refetches the full payload once and
    # re-caches it, so the _get_linescore call below reuses it (no second hit).
    g = load_game(game_id, bypass_cache=True)
    innings = _build_innings(g)
    meta = _game_status(game_id)
    is_live = meta.get('status') == 'In Progress'

    linescore = _get_linescore(game_id)

    return jsonify({
        "is_live": is_live,
        "away_score": g.final_away_score,
        "home_score": g.final_home_score,
        "current_inning": meta.get('current_inning', ''),
        "inning_state": meta.get('inning_state', ''),
        "linescore": linescore,
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


@app.route("/fantasy")
def fantasy():
    from data_sources.fantasy.base import get_provider, ProviderNotConfigured
    try:
        provider = get_provider()
        matchup = provider.get_matchup()
        return render_template(
            "fantasy.html",
            matchup=matchup,
            attribution=provider.attribution,
            attribution_url="https://sports.yahoo.com/fantasy/",
            error=None,
        )
    except ProviderNotConfigured as e:
        return render_template("fantasy.html", matchup=None, error=str(e),
                               attribution="Fantasy data provided by Yahoo Fantasy",
                               attribution_url="https://sports.yahoo.com/fantasy/")
    except Exception as e:
        return render_template("fantasy.html", matchup=None,
                               error=f"Unexpected error: {e}",
                               attribution="Fantasy data provided by Yahoo Fantasy",
                               attribution_url="https://sports.yahoo.com/fantasy/")


@app.route("/chat")
def chat():
    return render_template("chat.html")


@app.route("/api/chat/warm", methods=["POST"])
def chat_warm():
    """Pre-build and cache the fantasy context. Called when the chat page loads."""
    from data_sources import fantasy_context
    from data_sources.fantasy.base import get_provider, ProviderNotConfigured
    try:
        provider = get_provider()
        ctx = fantasy_context.build(provider)
        return jsonify({"ok": True, "chars": len(ctx)})
    except ProviderNotConfigured:
        return jsonify({"ok": False, "reason": "fantasy_not_configured"})
    except Exception as e:
        return jsonify({"ok": False, "reason": str(e)})


@app.route("/api/chat", methods=["POST"])
def api_chat():
    import json as _json
    from flask import Response, stream_with_context

    data = request.get_json(force=True)
    messages = data.get("messages", [])
    if not messages:
        return jsonify({"error": "No messages provided"}), 400

    # Trim to last 20 messages to avoid token overflow
    if len(messages) > 20:
        messages = messages[-20:]

    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        return jsonify({"error": "GROQ_API_KEY not set — add it to .env"}), 503

    # Build context (cached 15 min)
    from data_sources import fantasy_context
    from data_sources.fantasy.base import get_provider, ProviderNotConfigured
    system_prompt = fantasy_context.SYSTEM_PREAMBLE + (
        "Note: Fantasy league data is unavailable right now. "
        "Answer using general MLB and fantasy baseball knowledge."
    )
    try:
        provider = get_provider()
        system_prompt = fantasy_context.build(provider)
    except ProviderNotConfigured:
        pass
    except Exception:
        pass

    def generate():
        try:
            from groq import Groq
            client = Groq(api_key=api_key)
            stream = client.chat.completions.create(
                model=os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile"),
                max_tokens=1024,
                messages=[{"role": "system", "content": system_prompt}, *messages],
                stream=True,
            )
            for chunk in stream:
                text = chunk.choices[0].delta.content or ""
                if text:
                    yield f"data: {_json.dumps({'t': text})}\n\n"
            yield f"data: {_json.dumps({'done': True})}\n\n"
        except Exception as e:
            yield f"data: {_json.dumps({'error': str(e)})}\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.route("/statcast")
def statcast_page():
    return render_template("statcast.html", current_year=sc_module._CURRENT_YEAR)


@app.route("/api/statcast/leaderboard")
def statcast_leaderboard_api():
    ptype = request.args.get("type", "batter")
    year = int(request.args.get("year", sc_module._CURRENT_YEAR))

    dispatch = {
        "batter":       sc_module.get_batter_leaderboard,
        "pitcher":      sc_module.get_pitcher_leaderboard,
        "speed":        sc_module.get_speed_leaderboard,
        "batter_xstats": sc_module.get_batter_expected_stats,
        "pitcher_xstats": sc_module.get_pitcher_expected_stats,
        "batter_pct":   sc_module.get_batter_percentiles,
        "pitcher_pct":  sc_module.get_pitcher_percentiles,
    }
    fn = dispatch.get(ptype, sc_module.get_batter_leaderboard)
    data = fn(year)
    return jsonify({"data": data, "year": year, "type": ptype})


@app.route("/player/<int:mlbam_id>")
def player(mlbam_id):
    player_info = statsapi.player_stat_data(
        mlbam_id, group="hitting,pitching", type="season"
    )
    sc_metrics = sc_module.get_player_statcast(mlbam_id)
    percentiles = sc_module.compute_percentiles(mlbam_id, sc_metrics) if sc_metrics else {}

    return render_template(
        "player.html",
        player=player_info,
        mlbam_id=mlbam_id,
        sc_metrics=sc_metrics,
        percentiles=percentiles,
        pitch_type_name=sc_module.pitch_type_name,
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=False)
