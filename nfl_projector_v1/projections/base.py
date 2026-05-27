"""Shared projection helpers.

These four functions are the building blocks every position-specific
projection (QB, RB, WR/TE) composes. Keeping them centralized means:

  1. Every position uses the same blending/matchup/injury logic. No drift.
  2. Changes to model behavior happen here in one place, not 4.
  3. Easy to unit-test in isolation (pure functions, no side effects).

The intentional shape of every position projection in v2:

    own_estimate = blend_with_baseline(
        weighted_recent_average(player_games, stat),
        season_baseline,
        n_recent_games,
    )
    projected = (
        own_estimate
        * opponent_factor(opp_recent_allowed, league_avg_allowed)
        * injury_factor(player, season, week, injuries)
    )

That's it. Two multipliers on a blended baseline. No stacking of five
adjustments. The v1 model's xfp × eff × matchup × env × injury chain is
exactly the pattern we're avoiding here — empirically, each multiplier
adds noise, and multiplying correlated signals amplifies that noise.
"""
from __future__ import annotations
import pandas as pd
from typing import Optional

from ..config import (
    MATCHUP_FLOOR,
    MATCHUP_CEILING,
    INJURY_MULTIPLIERS,
    RECENT_WEIGHT,
)


# ---------------------------------------------------------------------------
# weighted_recent_average
# ---------------------------------------------------------------------------

def weighted_recent_average(
    games: pd.DataFrame,
    stat: str,
    n_recent: int = 4,
    decay: float = 1.0,
) -> Optional[float]:
    """Weighted mean of `stat` over the player's most recent n_recent games.

    Parameters
    ----------
    games : DataFrame sorted oldest→newest. Must contain column `stat`.
            Caller is responsible for filtering to ONE player and
            sorting by (season, week_num) before passing in.
    stat : name of the column to average.
    n_recent : number of most recent games to include. If fewer games are
               available, uses all of them.
    decay : 1.0 = equal weight; >1 = newer games weighted more heavily.
            decay=1.5 means the most recent game gets ~1.5x the weight of
            the game 4 back. For v1 we default to 1.0 (equal weighting
            within the recent window) — simpler to interpret. Can tune
            later if backtests show recency-weighting helps.

    Returns
    -------
    Weighted mean, or None if the games DataFrame is empty or the stat
    column is all NaN.
    """
    if games.empty or stat not in games.columns:
        return None
    # Take last n_recent rows (caller guarantees sort order)
    recent = games.tail(n_recent)
    values = recent[stat].dropna()
    if values.empty:
        return None
    if decay == 1.0:
        return float(values.mean())
    # Geometric weights — most recent gets weight 1, then decreasing
    n = len(values)
    weights = [decay ** (n - 1 - i) for i in range(n)]
    weighted_sum = sum(v * w for v, w in zip(values, weights))
    total_weight = sum(weights)
    return float(weighted_sum / total_weight)


# ---------------------------------------------------------------------------
# blend_with_baseline
# ---------------------------------------------------------------------------

def blend_with_baseline(
    recent_value: Optional[float],
    season_baseline: Optional[float],
    n_recent_games: int,
    recent_weight: float = RECENT_WEIGHT,
    league_baseline: Optional[float] = None,
    min_recent_games: int = 2,
) -> Optional[float]:
    """Bayesian-style blend of recent form with season-then-league baseline.

    Logic:
      - If recent sample is full (>= 4 games), use the weighted blend:
            recent_weight * recent + (1 - recent_weight) * season
      - If recent sample is partial (2-3 games), the blend leans toward season:
            (n/4) * recent_weight scales down the recent contribution
      - If recent sample is very small (<2 games), fall back to season
      - If no season data either, fall back to league baseline
      - If none of those work, return None (caller decides what to do)

    Parameters
    ----------
    recent_value : output of weighted_recent_average, or None
    season_baseline : player's season-to-date mean, or None
    n_recent_games : how many games were actually used for recent_value
                     (affects how much weight to give recent)
    recent_weight : default weight for recent when sample is full (4+ games)
    league_baseline : fallback when player has zero season data
    min_recent_games : if fewer than this many games, recent is ignored entirely

    Returns
    -------
    Blended estimate, or None if no data available at any level.
    """
    has_recent = recent_value is not None and n_recent_games >= min_recent_games
    has_season = season_baseline is not None
    has_league = league_baseline is not None

    if has_recent and has_season:
        # Scale recent_weight by how full the sample is.
        # If we have 4+ games, use full recent_weight.
        # If we have 2 games out of 4 desired, use half of recent_weight.
        sample_completeness = min(n_recent_games / 4.0, 1.0)
        effective_weight = recent_weight * sample_completeness
        return (
            effective_weight * recent_value
            + (1.0 - effective_weight) * season_baseline
        )
    if has_recent:
        # No season data — just trust the recent
        return recent_value
    if has_season:
        return season_baseline
    if has_league:
        return league_baseline
    return None


# ---------------------------------------------------------------------------
# opponent_factor
# ---------------------------------------------------------------------------

def opponent_factor(
    opp_recent_allowed: Optional[float],
    league_avg_allowed: Optional[float],
    floor: float = MATCHUP_FLOOR,
    ceiling: float = MATCHUP_CEILING,
) -> float:
    """Matchup multiplier: how much better/worse than league average is this defense?

    Returns a number to multiply the player's projection by.

    Examples (assuming league_avg = 100):
        opp_recent_allowed=120 → 1.15 (capped) — bad defense, boost
        opp_recent_allowed=110 → 1.10        — slightly bad defense
        opp_recent_allowed=100 → 1.00        — average defense, no adjustment
        opp_recent_allowed= 90 → 0.90        — good defense, dampen
        opp_recent_allowed= 80 → 0.85 (capped) — elite defense

    The floor/ceiling caps are intentionally tight (default [0.85, 1.15]).
    Past experiments with wider caps (e.g. [0.70, 1.30]) caused projections
    to overshoot at the extremes — turning a 22-point team into a 32-point
    one against a weak defense, which empirically doesn't happen.

    Returns 1.0 (no adjustment) when either input is missing.
    """
    if opp_recent_allowed is None or league_avg_allowed is None:
        return 1.0
    if league_avg_allowed <= 0:
        return 1.0
    raw_factor = opp_recent_allowed / league_avg_allowed
    return max(floor, min(ceiling, raw_factor))


# ---------------------------------------------------------------------------
# injury_factor
# ---------------------------------------------------------------------------

def injury_factor(
    player_name: str,
    team: str,
    season: int,
    week: int,
    injuries_df: Optional[pd.DataFrame],
) -> float:
    """Look up player's injury designation; return the multiplier.

    Reads the warehouse's `injuries` table. Returns:
        Out / IR        → 0.0  (player projects to 0)
        Doubtful        → 0.30 (heavy dampener)
        Questionable    → 0.85 (small dampener)
        Probable / no listing → 1.0

    Player matching: uses player_name and team. Some injury listings have
    name variants (e.g. "DK Metcalf" vs "D.K. Metcalf") which is a known
    data-quality issue; in those cases we fall through to 1.0 (treated
    as healthy), which is the safer default — a small false-negative is
    better than zeroing out a healthy player by accident.

    Returns 1.0 (no adjustment) if no injuries data is available.
    """
    if injuries_df is None or injuries_df.empty:
        return 1.0
    # Standard injury columns from nflverse
    cols_expected = {"player_name", "team", "season", "week", "report_status"}
    if not cols_expected.issubset(injuries_df.columns):
        # Schema doesn't match — silently fall through (safer than crashing)
        return 1.0

    matches = injuries_df[
        (injuries_df["player_name"] == player_name)
        & (injuries_df["team"] == team)
        & (injuries_df["season"] == season)
        & (injuries_df["week"] == week)
    ]
    if matches.empty:
        return 1.0
    # Take the most-recent status if multiple rows (some weeks have multiple
    # reports — Wed/Thu/Fri practice reports). Take whichever appears last.
    status = str(matches["report_status"].iloc[-1]).strip()
    return INJURY_MULTIPLIERS.get(status, 1.0)
