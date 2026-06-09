from abc import ABC, abstractmethod

from core.fantasy_models import FantasyTeam, Matchup
from core.scoring import ScoringSettings


class ProviderNotConfigured(Exception):
    """Raised when the fantasy provider cannot be initialized due to missing config."""
    pass


class FantasyProvider(ABC):

    @abstractmethod
    def get_scoring_settings(self) -> ScoringSettings:
        """Return the league's scoring rules."""

    @abstractmethod
    def get_team(self) -> FantasyTeam:
        """Return the user's team with today's stat lines, points, and clips."""

    @abstractmethod
    def get_matchup(self) -> Matchup:
        """Return the current head-to-head matchup."""

    @property
    def attribution(self) -> str:
        """Human-readable attribution string."""
        return ""


def get_provider(platform: str | None = None) -> FantasyProvider:
    """
    Factory. Returns the configured FantasyProvider.
    Raises ProviderNotConfigured with setup instructions if not ready.
    """
    import os
    plat = platform or os.getenv("FANTASY_PLATFORM", "yahoo")

    if plat == "yahoo":
        from data_sources.fantasy.yahoo_provider import YahooProvider
        return YahooProvider()

    raise ProviderNotConfigured(
        f"Unknown fantasy platform '{plat}'. "
        "Set FANTASY_PLATFORM=yahoo in your .env file."
    )
