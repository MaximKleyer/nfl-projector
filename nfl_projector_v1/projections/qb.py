"""QB projection: turn a QB's history into a projected stat line for one game.

Approach:
  For each base stat (pass_attempts, cmp_pct, ypa, sack_pct, ints, scrambles):
    own_estimate = blend(weighted_recent, season_baseline)
    projected_stat = own_estimate * opponent_factor * injury_factor

Then derive composite stats:
    pass_yards = pass_attempts * ypa
    completions = pass_attempts * cmp_pct/100

Note: pass_TDs are NOT projected per-QB. Per our design, TDs are derived
at the team level via league-average TD-per-yard conversion (see points.py).
This is intentional — per-player TD projections are too noisy.

scramble_yards is tracked SEPARATELY (not folded into team rushing here).
team.py will combine QB scrambles with RB rushing when aggregating.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional
import pandas as pd

from ..config import (
    LEAGUE_AVG_QB_PASS_ATTEMPTS,
    LEAGUE_AVG_QB_COMP_PCT,
    LEAGUE_AVG_QB_YPA,
    LEAGUE_AVG_QB_SCRAMBLES,
)
from ..data.roster import Player
from .base import (
    weighted_recent_average,
    blend_with_baseline,
    opponent_factor,
    injury_factor,
    staleness_factor,
)


@dataclass
class QBProjection:
    """A single QB's projected stat line for one game."""
    name: str
    team: str
    opponent: str

    # Base projected stats
    pass_attempts: float
    completions: float
    completion_pct: float
    ypa: float                  # yards per attempt
    pass_yards: float           # pass_attempts × ypa — the team passing anchor
    interceptions: float
    sack_count: float
    scramble_yards: float       # tracked SEPARATELY from team rushing

    # Diagnostic / interpretability
    n_recent_games: int
    health_multiplier: float
    matchup_multiplier_yards: float


# ---------------------------------------------------------------------------
# Internal helpers (filter player history, get opponent defensive avg)
# ---------------------------------------------------------------------------

def _qb_history_up_to_week(
    qb_name: str,
    season: int,
    week: int,
    qb_history: pd.DataFrame,
) -> pd.DataFrame:
    """Return all of this QB's game logs STRICTLY before (season, week).

    Used for both 'recent 4 games' and 'season-to-date baseline'.
    Walk-forward correct — no future-game leakage.
    """
    if qb_history is None or qb_history.empty:
        return pd.DataFrame()
    mask = (qb_history["player_name"] == qb_name) & (
        (qb_history["season"] < season)
        | ((qb_history["season"] == season) & (qb_history["week_num"] < week))
    )
    return qb_history[mask].sort_values(["season", "week_num"])


def _opponent_recent_def_avg(
    opponent: str,
    season: int,
    week: int,
    pass_defense: pd.DataFrame,
    stat: str,
    n_recent: int = 4,
) -> Optional[float]:
    """Average of opponent's defensive stat over their recent games.

    Used as the numerator in opponent_factor (comparing this defense to
    league average for the same stat).
    """
    if pass_defense is None or pass_defense.empty or stat not in pass_defense.columns:
        return None
    mask = (pass_defense["team_norm"] == opponent) & (
        (pass_defense["season"] < season)
        | ((pass_defense["season"] == season) & (pass_defense["week_num"] < week))
    )
    games = pass_defense[mask].sort_values(["season", "week_num"]).tail(n_recent)
    values = games[stat].dropna()
    if values.empty:
        return None
    return float(values.mean())


def _league_def_avg(
    pass_defense: pd.DataFrame,
    season: int,
    week: int,
    stat: str,
) -> Optional[float]:
    """League-wide average of a defensive stat AS OF this week (no leakage).

    Used as the denominator in opponent_factor. This is the league baseline
    for "what does an average defense allow at this stat."
    """
    if pass_defense is None or pass_defense.empty or stat not in pass_defense.columns:
        return None
    mask = (pass_defense["season"] < season) | (
        (pass_defense["season"] == season) & (pass_defense["week_num"] < week)
    )
    values = pass_defense[mask][stat].dropna()
    if values.empty:
        return None
    return float(values.mean())


# ---------------------------------------------------------------------------
# The main projection function
# ---------------------------------------------------------------------------

def project_qb_line(
    qb: Player,
    opponent: str,
    season: int,
    week: int,
    qb_history: pd.DataFrame,
    pass_defense: Optional[pd.DataFrame] = None,
    injuries_df: Optional[pd.DataFrame] = None,
) -> QBProjection:
    """Project this QB's stat line for the given game.

    Parameters
    ----------
    qb : Player object (output of roster.get_active_roster)
    opponent : team abbreviation of the opposing defense
    season, week : the game we're projecting
    qb_history : full qb_history DataFrame (will be filtered to this QB)
    pass_defense : optional defensive stats for matchup adjustment
    injuries_df : optional injury report for the health multiplier

    Returns
    -------
    QBProjection with all stats filled.
    """
    # Get this QB's prior games (walk-forward correct)
    hist = _qb_history_up_to_week(qb.name, season, week, qb_history)
    n_games = len(hist)
    staleness_r = staleness_factor(qb.team_games_missed)

    # ---- Project each base stat using the standard recipe ----
    # blend(weighted_recent, season) → league fallback, with staleness regression
    # toward the positional (league) baseline (§12.5). Typically a no-op for QB,
    # which resolves via depth chart/override rather than snaps.
    def _project_stat(stat: str, league_fallback: float) -> float:
        recent = weighted_recent_average(hist, stat, n_recent=4)
        season_avg = float(hist[stat].mean()) if (n_games > 0 and stat in hist.columns) else None
        return blend_with_baseline(
            recent_value=recent,
            season_baseline=season_avg,
            n_recent_games=min(n_games, 4),
            league_baseline=league_fallback,
            positional_baseline=league_fallback,
            staleness_r=staleness_r,
        )

    # Volume stats (will be hit by matchup multiplier)
    pass_attempts = _project_stat("pass_att", LEAGUE_AVG_QB_PASS_ATTEMPTS)
    # Efficiency stats
    cmp_pct = _project_stat("cmp_pct", LEAGUE_AVG_QB_COMP_PCT)
    ypa = _project_stat("ypa", LEAGUE_AVG_QB_YPA)
    # QB-rushing
    scrambles = _project_stat("scrambles", LEAGUE_AVG_QB_SCRAMBLES)
    # INT and sack rates (modest, used for diagnostic / interpretability)
    int_rate = _project_stat("ints", 0.7)  # league average ~0.7 INTs/game
    sack_rate = _project_stat("sacks_taken", 2.3)  # league avg ~2.3 sacks/game

    # ---- Opponent (matchup) factors ----
    # Compare this defense's recent stat-allowed to the league average of
    # the same stat. Floor/ceiling caps live inside opponent_factor.
    def _matchup(def_stat: str) -> float:
        opp_recent = _opponent_recent_def_avg(opponent, season, week, pass_defense, def_stat)
        league = _league_def_avg(pass_defense, season, week, def_stat)
        return opponent_factor(opp_recent, league)

    matchup_ypa = _matchup("def_ypa")
    matchup_attempts = _matchup("def_pass_att")
    matchup_cmp = _matchup("def_cmp_pct")
    matchup_sacks = _matchup("def_sack_pct")
    # For yards, the multiplier is BOTH attempts AND ypa — but applying both
    # would double-count the defensive effect. Use ypa-allowed only, since
    # passing attempts are mostly driven by GAME SCRIPT, not by defense.
    matchup_yards = matchup_ypa

    # ---- Injury factor ----
    inj = injury_factor(qb.name, qb.team, season, week, injuries_df)

    # ---- Combine ----
    # Volume stat: attempts get a small matchup boost (some defenses force
    # check-downs and short passes that inflate attempts). Return-game ramp
    # applies to VOLUME only (§12.4); a no-op for QB as currently resolved.
    pass_attempts = pass_attempts * matchup_attempts * inj * qb.return_ramp_factor
    # Efficiency stats: completion % and YPA get full matchup treatment
    cmp_pct = cmp_pct * matchup_cmp * inj
    ypa = ypa * matchup_ypa * inj
    # QB rushing: light matchup effect (good rush defenses also limit scrambles)
    # Reusing the rush adjustment is overkill; just apply injury.
    scrambles = scrambles * inj
    # INT/sack: opponent factor in opposite direction (good defense → more INTs)
    interceptions = int_rate * (2.0 - matchup_ypa) * inj  # invert: bad defense = lower INTs
    sack_count = sack_rate * matchup_sacks * inj

    # ---- Derive composite stats ----
    pass_yards = pass_attempts * ypa
    completions = pass_attempts * (cmp_pct / 100.0)

    # Estimated scramble yards: ~6 yards per scramble league avg (QB scrambles
    # are short — not designed runs). Lamar/Hurts/Daniels would have higher
    # individual avg but we don't break that out per-QB in v1.
    LEAGUE_AVG_YARDS_PER_SCRAMBLE = 6.0
    scramble_yards = scrambles * LEAGUE_AVG_YARDS_PER_SCRAMBLE

    return QBProjection(
        name=qb.name,
        team=qb.team,
        opponent=opponent,
        pass_attempts=round(pass_attempts, 1),
        completions=round(completions, 1),
        completion_pct=round(cmp_pct, 1),
        ypa=round(ypa, 2),
        pass_yards=round(pass_yards, 1),
        interceptions=round(interceptions, 2),
        sack_count=round(sack_count, 1),
        scramble_yards=round(scramble_yards, 1),
        n_recent_games=min(n_games, 4),
        health_multiplier=round(inj, 3),
        matchup_multiplier_yards=round(matchup_yards, 3),
    )
