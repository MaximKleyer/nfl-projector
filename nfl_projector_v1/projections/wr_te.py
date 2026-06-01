"""WR/TE projection: target share for one game.

We project ONLY target share here. Actual receiving yards are computed
in team.py when we allocate the QB's projected passing yards across
receivers based on target share.

Why this split:
  - Receiving yards have to sum to the QB's pass yards (the anchor).
  - If we projected each receiver's yards independently, they wouldn't
    sum correctly. Doing the allocation at the team level guarantees
    consistency.
  - The receiver's "skill" is captured in target share + their personal
    yards-per-target (also factored in by team.py).

Approach (same recipe as qb.py and rb.py):
  For target_share:
    own_estimate = blend(weighted_recent, season_baseline)
    projected = own_estimate * opponent_factor * injury_factor

  yards_per_target (ypt) is also projected because team.py uses it for
  weighted allocation. A high-ypt receiver gets a slightly bigger slice
  than their raw target share would suggest.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional
import pandas as pd

from ..config import (
    LEAGUE_AVG_RECV_YPT,
    LEAGUE_AVG_RECV_YPR,
    LEAGUE_AVG_RECV_CATCH_RATE,
)
from ..data.roster import Player
from .base import (
    weighted_recent_average,
    blend_with_baseline,
    opponent_factor,
    injury_factor,
    staleness_factor,
)


# Default target share baseline by depth_order. These are league averages
# observed in 2023-2025 data. WR1 ≈ 22%, WR2 ≈ 16%, WR3/slot ≈ 11%, etc.
# TEs run a bit lower than the equivalent depth WR.
DEFAULT_TARGET_SHARE_BY_DEPTH_POS = {
    ("WR", 1): 22.0,
    ("WR", 2): 16.0,
    ("WR", 3): 11.0,
    ("WR", 4): 6.0,
    ("TE", 1): 14.0,
    ("TE", 2): 5.0,
}


@dataclass
class ReceiverProjection:
    """A single receiver's projected target share for one game.

    Actual receiving yards filled later by team.py via allocation.
    """
    name: str
    team: str
    opponent: str
    position: str               # 'WR' or 'TE'
    target_share: float         # % of team targets (e.g. 22.5 means 22.5%)
    ypt: float                  # yards per target — used in allocation weight
    catch_rate: float           # percent (used for receptions later)

    # Filled later by team.py during allocation
    receiving_yards: float = 0.0
    receptions: float = 0.0

    # Diagnostic
    n_recent_games: int = 0
    health_multiplier: float = 1.0
    matchup_multiplier: float = 1.0


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _recv_history_up_to_week(
    name: str,
    season: int,
    week: int,
    recv_history: pd.DataFrame,
    position_filter: tuple[str, ...] = ("WR", "TE"),
) -> pd.DataFrame:
    """Receiver's game logs strictly before (season, week).

    Filters to specific positions (default WR/TE) so an RB pass-catcher
    with the same name doesn't accidentally pull RB rows when projecting
    their WR/TE role (and vice versa). Since RBs are now in the receiving
    table alongside WRs/TEs, this filter is what keeps the two cleanly
    separated.
    """
    if recv_history is None or recv_history.empty:
        return pd.DataFrame()
    mask = (recv_history["player_name"] == name) & (
        (recv_history["season"] < season)
        | ((recv_history["season"] == season) & (recv_history["week_num"] < week))
    )
    if "position" in recv_history.columns and position_filter:
        mask = mask & recv_history["position"].isin(position_filter)
    return recv_history[mask].sort_values(["season", "week_num"])


def _opp_recv_def_recent(
    opponent: str,
    season: int,
    week: int,
    recv_defense: pd.DataFrame,
    stat: str,
    n_recent: int = 4,
) -> Optional[float]:
    """Average of opponent's receiving-defense stat over recent games."""
    if recv_defense is None or recv_defense.empty or stat not in recv_defense.columns:
        return None
    mask = (recv_defense["team_norm"] == opponent) & (
        (recv_defense["season"] < season)
        | ((recv_defense["season"] == season) & (recv_defense["week_num"] < week))
    )
    games = recv_defense[mask].sort_values(["season", "week_num"]).tail(n_recent)
    values = games[stat].dropna()
    if values.empty:
        return None
    return float(values.mean())


def _league_recv_def_avg(
    recv_defense: pd.DataFrame,
    season: int,
    week: int,
    stat: str,
) -> Optional[float]:
    """League-wide avg of a receiving-defense stat as of this week."""
    if recv_defense is None or recv_defense.empty or stat not in recv_defense.columns:
        return None
    mask = (recv_defense["season"] < season) | (
        (recv_defense["season"] == season) & (recv_defense["week_num"] < week)
    )
    values = recv_defense[mask][stat].dropna()
    if values.empty:
        return None
    return float(values.mean())


# ---------------------------------------------------------------------------
# Main projection function
# ---------------------------------------------------------------------------

def project_receiver_line(
    receiver: Player,
    opponent: str,
    season: int,
    week: int,
    recv_history: pd.DataFrame,
    recv_defense: Optional[pd.DataFrame] = None,
    injuries_df: Optional[pd.DataFrame] = None,
) -> ReceiverProjection:
    """Project this receiver's target share + efficiency for the game.

    Returns a ReceiverProjection with target_share, ypt, catch_rate.
    receiving_yards and receptions remain 0.0 here; team.py fills them in
    when allocating the QB's pass yards across the receiver pool.
    """
    hist = _recv_history_up_to_week(receiver.name, season, week, recv_history)
    n_games = len(hist)
    staleness_r = staleness_factor(receiver.team_games_missed)

    # Default target share baseline by (position, depth_order)
    key = (receiver.position, receiver.depth_order)
    target_baseline = DEFAULT_TARGET_SHARE_BY_DEPTH_POS.get(key, 8.0)

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

    target_share = _project_stat("target_share", target_baseline)
    ypt = _project_stat("ypt", LEAGUE_AVG_RECV_YPT)
    catch_rate = _project_stat("catch_rate", LEAGUE_AVG_RECV_CATCH_RATE)

    # Opponent matchup — most directly relevant: yards per route run allowed.
    # We use a soft adjustment on target_share (some defenses funnel volume
    # to specific receivers) and a fuller one on ypt.
    opp_yprr = _opp_recv_def_recent(opponent, season, week, recv_defense, "def_yprr")
    league_yprr = _league_recv_def_avg(recv_defense, season, week, "def_yprr")
    matchup_ypt = opponent_factor(opp_yprr, league_yprr)
    # Target share isn't really a defensive function — keep neutral
    matchup_share = 1.0

    inj = injury_factor(receiver.name, receiver.team, season, week, injuries_df)

    # Combine. Return-game ramp applies to VOLUME only (target_share) — a just-
    # returned receiver gets a smaller share of targets, not worse efficiency.
    # Ramping target_share BEFORE team.py allocation shrinks his slice and
    # redistributes to teammates, preserving the pass-yards-sum invariant (§12.4).
    target_share = target_share * matchup_share * inj * receiver.return_ramp_factor
    ypt = ypt * matchup_ypt * inj
    # catch_rate has small matchup effect; just apply injury
    catch_rate = catch_rate * inj

    return ReceiverProjection(
        name=receiver.name,
        team=receiver.team,
        opponent=opponent,
        position=receiver.position,
        target_share=round(target_share, 2),
        ypt=round(ypt, 2),
        catch_rate=round(catch_rate, 1),
        receiving_yards=0.0,
        receptions=0.0,
        n_recent_games=min(n_games, 4),
        health_multiplier=round(inj, 3),
        matchup_multiplier=round(matchup_ypt, 3),
    )
