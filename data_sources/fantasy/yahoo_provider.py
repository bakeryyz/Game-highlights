import json
import os
from pathlib import Path

from core.fantasy_models import FantasyPlayer, FantasyTeam, Matchup
from core.scoring import ScoringSettings, score_stat_line
from data_sources.fantasy.base import FantasyProvider, ProviderNotConfigured

ATTRIBUTION = "Fantasy data provided by Yahoo Fantasy"
ATTRIBUTION_URL = "https://sports.yahoo.com/fantasy/"

# Yahoo display_name → normalized scoring key
_YAHOO_STAT_MAP: dict[str, str] = {
    "R": "runs", "1B": "singles", "2B": "doubles", "3B": "triples",
    "HR": "homeRuns", "RBI": "rbi", "BB": "walks", "HBP": "hbp",
    "SB": "stolenBases", "SO": "strikeouts_batter",
    "IP": "inningsPitched", "K": "strikeouts_pitched",
    "ER": "earnedRuns", "HA": "hits_allowed", "BBA": "walks_allowed",
    "W": "wins", "SV": "saves",
}


def _load_scoring_override(path: str | None) -> ScoringSettings | None:
    if not path:
        return None
    try:
        weights = json.loads(Path(path).read_text())
        return ScoringSettings(weights=weights, name="file_override")
    except Exception:
        return None


def _parse_yahoo_scoring(lg) -> ScoringSettings | None:
    """Best-effort parse of Yahoo league scoring. Returns None if it fails."""
    try:
        cats = lg.stat_categories()
        weights: dict[str, float] = {}
        for cat in cats:
            disp = cat.get('display_name', '')
            norm = _YAHOO_STAT_MAP.get(disp)
            if norm:
                # Yahoo multiplier; default 1.0 if not present
                weights[norm] = float(cat.get('multiplier', 1.0))
        return ScoringSettings(weights=weights, name="yahoo") if weights else None
    except Exception:
        return None


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
        if self._scoring is not None:
            return self._scoring
        # Priority: file override → Yahoo parse → default
        override = _load_scoring_override(os.getenv("SCORING_FILE"))
        if override:
            self._scoring = override
        else:
            parsed = _parse_yahoo_scoring(self._lg)
            self._scoring = parsed or ScoringSettings.default()
        return self._scoring

    def _build_team(self, team_obj, team_key: str) -> FantasyTeam:
        from data_sources.mlb_live import all_player_stats_today
        from data_sources.player_crosswalk import resolve_mlbam_id
        from data_sources.mlb_client import highlights_for_player

        scoring = self.get_scoring_settings()
        stats_index = all_player_stats_today()

        roster = team_obj.roster()
        team_name = getattr(team_obj, 'team_name', team_key)

        players: list[FantasyPlayer] = []
        for p in roster:
            yahoo_id = str(p.get('player_id', ''))
            name = p.get('name', '')
            slot = p.get('selected_position', '')
            pro_team = p.get('editorial_team_abbr', '')

            mlbam_id = resolve_mlbam_id(name, yahoo_id=yahoo_id, pro_team=pro_team)

            game_pk: int | None = None
            stat_line: dict | None = None
            pts = 0.0
            urls: list[str] = []

            if mlbam_id and mlbam_id in stats_index:
                game_pk, stat_line = stats_index[mlbam_id]
                pts = score_stat_line(stat_line, scoring)
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
                today_stat_line=stat_line,
                today_points=pts,
                game_pk=game_pk,
                video_urls=urls,
            ))

        return FantasyTeam(team_id=team_key, name=team_name, players=players)

    def get_team(self) -> FantasyTeam:
        team_key = os.getenv("YAHOO_TEAM_KEY", "")
        return self._build_team(self._tm, team_key)

    def get_matchup(self) -> Matchup:
        week = self._lg.current_week()
        opp_key = self._tm.matchup(week)
        opp_team_obj = self._lg.to_team(opp_key)

        me = self.get_team()
        opp = self._build_team(opp_team_obj, opp_key)

        return Matchup(me=me, opponent=opp, period=f"Week {week}")

    @property
    def attribution(self) -> str:
        return ATTRIBUTION
