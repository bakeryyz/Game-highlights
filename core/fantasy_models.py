from dataclasses import dataclass, field


@dataclass
class FantasyPlayer:
    name: str
    platform: str           # "yahoo"
    platform_id: str
    mlbam_id: int | None
    lineup_slot: str        # "C", "1B", "SP", "BN", "IL", "NA", etc.
    pro_team: str
    today_stat_line: dict | None = None
    today_points: float = 0.0
    game_pk: int | None = None
    video_urls: list[str] = field(default_factory=list)

    @property
    def is_starter(self) -> bool:
        return self.lineup_slot not in ('BN', 'IL', 'NA', '')

    @property
    def playing_today(self) -> bool:
        return self.game_pk is not None

    def stat_summary(self) -> str:
        """Short human-readable stat line for display."""
        if not self.today_stat_line:
            return '—'
        line = self.today_stat_line
        parts = []
        # Batting summary
        if 'singles' in line or 'homeRuns' in line:
            hits = sum(line.get(k, 0) for k in ('singles', 'doubles', 'triples', 'homeRuns'))
            if hits:
                parts.append(f"{hits}H")
            if line.get('homeRuns'):
                parts.append(f"{line['homeRuns']}HR")
            if line.get('rbi'):
                parts.append(f"{line['rbi']}RBI")
            if line.get('runs'):
                parts.append(f"{line['runs']}R")
            if line.get('stolenBases'):
                parts.append(f"{line['stolenBases']}SB")
        # Pitching summary
        if 'inningsPitched' in line:
            ip = line['inningsPitched']
            whole = int(ip)
            frac = round((ip - whole) * 3)
            ip_str = f"{whole}.{frac}" if frac else str(whole)
            parts.append(f"{ip_str}IP")
            if line.get('strikeouts_pitched'):
                parts.append(f"{line['strikeouts_pitched']}K")
            if line.get('earnedRuns'):
                parts.append(f"{line['earnedRuns']}ER")
        return ' · '.join(parts) if parts else '—'


@dataclass
class FantasyTeam:
    team_id: str
    name: str
    players: list[FantasyPlayer] = field(default_factory=list)
    week_points: float = 0.0  # Yahoo's official week-to-date total

    @property
    def starters(self) -> list[FantasyPlayer]:
        return [p for p in self.players if p.is_starter]

    @property
    def bench(self) -> list[FantasyPlayer]:
        return [p for p in self.players if not p.is_starter]

    @property
    def total_points(self) -> float:
        if self.week_points:
            return self.week_points
        return sum(p.today_points for p in self.starters)


@dataclass
class Matchup:
    me: FantasyTeam
    opponent: FantasyTeam
    period: str             # e.g. "Week 14"

    @property
    def margin(self) -> float:
        return self.me.total_points - self.opponent.total_points

    @property
    def status_label(self) -> str:
        m = self.margin
        if abs(m) < 0.01:
            return "Tied"
        elif m > 0:
            return f"You lead by {m:.1f} pts"
        else:
            return f"Trailing by {abs(m):.1f} pts"
