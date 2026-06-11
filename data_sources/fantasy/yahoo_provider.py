import datetime
import os
from pathlib import Path

from core.fantasy_models import FantasyPlayer, FantasyTeam, Matchup
from core.scoring import ScoringSettings
from data_sources.fantasy.base import FantasyProvider, ProviderNotConfigured

ATTRIBUTION = "Fantasy data provided by Yahoo Fantasy"
ATTRIBUTION_URL = "https://sports.yahoo.com/fantasy/"

# Yahoo stat_id (string) → today_stat_line key used by stat_summary()
_STAT_ID_TO_STAT_LINE: dict[str, str] = {
    "7":  "runs",
    "9":  "singles",
    "10": "doubles",
    "11": "triples",
    "12": "homeRuns",
    "13": "rbi",
    "16": "stolenBases",
    "33": "outs_pitched",   # converted to inningsPitched below
    "42": "strikeouts_pitched",
    "37": "earnedRuns",
    "34": "hits_allowed",
    "39": "walks_allowed",
    "28": "wins",
    "32": "saves",
}


def _parse_stat_line(raw: dict) -> dict | None:
    """Convert Yahoo {stat_id: value} dict to normalized stat line keys."""
    result = {}
    for sid, val in raw.items():
        if val in ('-', '', None):
            continue
        key = _STAT_ID_TO_STAT_LINE.get(str(sid))
        if not key:
            continue
        try:
            result[key] = float(val)
        except (ValueError, TypeError):
            continue
    if not result:
        return None
    if "outs_pitched" in result:
        result["inningsPitched"] = result.pop("outs_pitched") / 3
    return result


class YahooProvider(FantasyProvider):
    """
    Read-only Yahoo Fantasy Baseball provider.
    NEVER calls any mutating Yahoo method (add/drop/trade/set-lineup).
    """

    def __init__(self) -> None:
        self._oauth = None
        self._lg = None
        self._tm = None
        self._scoring: ScoringSettings | None = None
        self._setup()

    def _setup(self) -> None:
        token_file = os.getenv("YAHOO_TOKEN_FILE")
        league_id = os.getenv("YAHOO_LEAGUE_ID")
        team_key = os.getenv("YAHOO_TEAM_KEY")

        missing = [k for k, v in [
            ("YAHOO_TOKEN_FILE", token_file),
            ("YAHOO_LEAGUE_ID", league_id),
            ("YAHOO_TEAM_KEY", team_key),
        ] if not v]
        if missing:
            raise ProviderNotConfigured(
                f"Missing env vars: {', '.join(missing)}. "
                "See .env.example and run: python scripts/yahoo_auth.py"
            )

        if not Path(token_file).exists():
            raise ProviderNotConfigured(
                f"Token file not found: {token_file}. "
                "Run: python scripts/yahoo_auth.py"
            )

        try:
            from yahoo_oauth import OAuth2
            import yahoo_fantasy_api as yfa
            self._oauth = OAuth2(None, None, from_file=token_file)
            if not self._oauth.token_is_valid():
                self._oauth.refresh_access_token()
            self._lg = yfa.Game(self._oauth, "mlb").to_league(league_id)
            self._tm = self._lg.to_team(team_key)
        except ProviderNotConfigured:
            raise
        except Exception as e:
            raise ProviderNotConfigured(
                f"Yahoo auth failed: {e}. "
                "Run: python scripts/yahoo_auth.py"
            )

    def get_scoring_settings(self) -> ScoringSettings:
        return ScoringSettings.default()

    def _build_team(self, team_key: str) -> FantasyTeam:
        from data_sources.player_crosswalk import resolve_mlbam_id
        from data_sources.mlb_client import highlights_for_player
        from data_sources.mlb_live import all_player_stats_today

        today = datetime.date.today().isoformat()
        resp = self._lg.sc.session.get(
            f"https://fantasysports.yahooapis.com/fantasy/v2/team/{team_key}/roster/players/stats;type=date;date={today}",
            params={"format": "json"},
        )
        data = resp.json()["fantasy_content"]["team"]

        team_info = data[0]
        team_name = next((x["name"] for x in team_info if isinstance(x, dict) and "name" in x), team_key)

        players_data = data[1]["roster"]["0"]["players"]
        count = players_data["count"]

        stats_index = all_player_stats_today()

        players: list[FantasyPlayer] = []
        for i in range(count):
            p = players_data[str(i)]["player"]
            info = p[0]

            name = next((x["name"]["full"] for x in info if isinstance(x, dict) and "name" in x), "?")
            yahoo_id = str(next((x["player_id"] for x in info if isinstance(x, dict) and "player_id" in x), ""))
            pro_team = next((x["editorial_team_abbr"] for x in info if isinstance(x, dict) and "editorial_team_abbr" in x), "")

            slot_data = p[1]["selected_position"]
            slot = next((x["position"] for x in slot_data if isinstance(x, dict) and "position" in x), "")

            today_points = 0.0
            today_stat_line = None
            if len(p) > 4:
                pts_section = p[4]
                today_points = float(pts_section["player_points"]["total"])
                raw_stats = {s["stat"]["stat_id"]: s["stat"]["value"] for s in pts_section["player_stats"]["stats"]}
                today_stat_line = _parse_stat_line(raw_stats)

            mlbam_id = resolve_mlbam_id(name, yahoo_id=yahoo_id, pro_team=pro_team)
            game_pk: int | None = None
            urls: list[str] = []
            if mlbam_id and mlbam_id in stats_index:
                game_pk = stats_index[mlbam_id][0]
                try:
                    urls = highlights_for_player(game_pk, mlbam_id)
                except Exception:
                    urls = []

            players.append(FantasyPlayer(
                name=name,
                platform="yahoo",
                platform_id=yahoo_id,
                mlbam_id=mlbam_id,
                lineup_slot=slot,
                pro_team=pro_team,
                today_stat_line=today_stat_line,
                today_points=today_points,
                game_pk=game_pk,
                video_urls=urls,
            ))

        return FantasyTeam(team_id=team_key, name=team_name, players=players)

    def _get_week_points(self, week: int, my_key: str, opp_key: str) -> tuple[float, float]:
        """Return (my_pts, opp_pts) weekly totals from Yahoo's scoreboard."""
        try:
            raw = self._lg.matchups(week)
            matchups = raw["fantasy_content"]["league"][1]["scoreboard"]["0"]["matchups"]
            for k, v in matchups.items():
                if k == "count":
                    continue
                teams = v.get("matchup", {}).get("0", {}).get("teams", {})
                pts_by_key: dict[str, float] = {}
                for tk, tv in teams.items():
                    if tk == "count":
                        continue
                    t = tv.get("team", [])
                    if isinstance(t, list) and len(t) > 1:
                        t_key = next((x["team_key"] for x in t[0] if isinstance(x, dict) and "team_key" in x), None)
                        t_pts = float(t[1].get("team_points", {}).get("total", 0))
                        if t_key:
                            pts_by_key[t_key] = t_pts
                if my_key in pts_by_key and opp_key in pts_by_key:
                    return pts_by_key[my_key], pts_by_key[opp_key]
        except Exception:
            pass
        return 0.0, 0.0

    def get_team(self) -> FantasyTeam:
        team_key = os.getenv("YAHOO_TEAM_KEY", "")
        return self._build_team(team_key)

    def get_matchup(self) -> Matchup:
        week = self._lg.current_week()
        my_key = os.getenv("YAHOO_TEAM_KEY", "")
        opp_key = self._tm.matchup(week)

        me = self._build_team(my_key)
        opp = self._build_team(opp_key)

        my_pts, opp_pts = self._get_week_points(week, my_key, opp_key)
        me.week_points = my_pts
        opp.week_points = opp_pts

        return Matchup(me=me, opponent=opp, period=f"Week {week}")

    @property
    def attribution(self) -> str:
        return ATTRIBUTION
