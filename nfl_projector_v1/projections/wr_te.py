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


# NOTE: there is intentionally NO receiver-level (coverage) defense matchup here.
# A diagnostic (2026-06-04) showed the receiving-defense table is redundant: its
# per-target metric (def_ypt_allowed) correlates 0.965 with the QB's def_ypa
# matchup and adds +0.0000 R2 of pass-yards-residual on top of it, and there is no
# positional signal (TE-funnel proxy correlates +0.02 with TE yard share). It also
# CANCELS structurally — a uniform per-receiver factor washes out in team.py's
# allocation, and the team pass total is the QB anchor anyway. Opposing pass
# defense is applied once, where it belongs: the QB's YPA (qb.py). DESIGN.md §5.


# ---------------------------------------------------------------------------
# Main projection function
# ---------------------------------------------------------------------------

def project_receiver_line(
    receiver: Player,
    opponent: str,
    season: int,
    week: int,
    recv_history: pd.DataFrame,
    injuries_df: Optional[pd.DataFrame] = None,
) -> ReceiverProjection:
    """Project this receiver's target share + efficiency for the game.

    Returns a ReceiverProjection with target_share, ypt, catch_rate.
    receiving_yards and receptions remain 0.0 here; team.py fills them in
    when allocating the QB's pass yards across the receiver pool.

    No coverage-defense matchup is applied — it's redundant with the QB's
    pass-defense matchup and cancels in allocation (see module note / DESIGN §5).
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

    inj = injury_factor(receiver.name, receiver.team, season, week, injuries_df)

    # Combine (injury only; pass-defense is applied once, on the QB's YPA — see
    # module note). Return-game ramp applies to VOLUME only (target_share) — a
    # just-returned receiver gets a smaller share of targets, not worse efficiency.
    # Ramping target_share BEFORE team.py allocation shrinks his slice and
    # redistributes to teammates, preserving the pass-yards-sum invariant (§12.4).
    target_share = target_share * inj * receiver.return_ramp_factor
    ypt = ypt * inj
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
        matchup_multiplier=1.0,
    )
