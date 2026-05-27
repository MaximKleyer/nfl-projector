"""RB projection: rushing AND receiving stat line for one game.

The RB now has BOTH rushing and receiving projections, because the
advanced_receiving_player table was updated to include RBs alongside
WRs and TEs.

Approach (matches qb.py and wr_te.py):
  For each base stat (carries, ypc, target_share, ypt, catch_rate):
    own_estimate = blend(weighted_recent, season_baseline)
    projected_stat = own_estimate * opponent_factor * injury_factor

Then derive:
    rush_yards = carries * ypc
    (receiving_yards filled by team.py during allocation)
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional
import pandas as pd

from ..config import (
    LEAGUE_AVG_RB_CARRIES_STARTER,
    LEAGUE_AVG_RB_YPC,
    LEAGUE_AVG_RECV_YPT,
    LEAGUE_AVG_RECV_CATCH_RATE,
)
from ..data.roster import Player
from .base import (
    weighted_recent_average,
    blend_with_baseline,
    opponent_factor,
    injury_factor,
)


@dataclass
class RBProjection:
    """A single RB's projected stat line for one game.

    Rushing stats are computed here. Receiving target_share and ypt are
    also computed here (from the RB's receiving history). Receiving yards
    themselves are filled by team.py during the allocation step.
    """
    name: str
    team: str
    opponent: str

    # Rushing stats
    carries: float
    ypc: float                  # yards per carry
    rush_yards: float           # carries × ypc

    # Receiving stats (target_share + efficiency, populated for RBs with
    # receiving history; receiving_yards filled by team.py via allocation)
    target_share: float = 0.0
    ypt: float = 0.0
    catch_rate: float = 0.0
    receiving_yards: float = 0.0
    receptions: float = 0.0

    # Depth chart position (1 = starter, 2 = primary backup, etc.).
    # Used by team.py for depth-aware volume scaling when starters are out.
    depth_order: int = 1

    # Diagnostic
    n_recent_games: int = 0
    health_multiplier: float = 1.0
    matchup_multiplier: float = 1.0


# ---------------------------------------------------------------------------
# Internal helpers — same shape as qb.py
# ---------------------------------------------------------------------------

def _rb_history_up_to_week(
    rb_name: str,
    season: int,
    week: int,
    rb_history: pd.DataFrame,
) -> pd.DataFrame:
    """Return all of this RB's game logs STRICTLY before (season, week).

    Walk-forward correct — no future-game leakage.
    Filters to position='RB' to avoid picking up WR/TE end-around rushes
    that also live in this table.
    """
    if rb_history is None or rb_history.empty:
        return pd.DataFrame()
    mask = (
        (rb_history["player_name"] == rb_name)
        & (rb_history.get("position", "RB") == "RB")
        & (
            (rb_history["season"] < season)
            | ((rb_history["season"] == season) & (rb_history["week_num"] < week))
        )
    )
    return rb_history[mask].sort_values(["season", "week_num"])


def _opp_rush_def_recent(
    opponent: str,
    season: int,
    week: int,
    rush_defense: pd.DataFrame,
    stat: str,
    n_recent: int = 4,
) -> Optional[float]:
    """Avg of opponent's rush-defense stat over their recent games."""
    if rush_defense is None or rush_defense.empty or stat not in rush_defense.columns:
        return None
    mask = (rush_defense["team_norm"] == opponent) & (
        (rush_defense["season"] < season)
        | ((rush_defense["season"] == season) & (rush_defense["week_num"] < week))
    )
    games = rush_defense[mask].sort_values(["season", "week_num"]).tail(n_recent)
    values = games[stat].dropna()
    if values.empty:
        return None
    return float(values.mean())


def _league_rush_def_avg(
    rush_defense: pd.DataFrame,
    season: int,
    week: int,
    stat: str,
) -> Optional[float]:
    """League-wide avg of a rush-defense stat as of this week (no leakage)."""
    if rush_defense is None or rush_defense.empty or stat not in rush_defense.columns:
        return None
    mask = (rush_defense["season"] < season) | (
        (rush_defense["season"] == season) & (rush_defense["week_num"] < week)
    )
    values = rush_defense[mask][stat].dropna()
    if values.empty:
        return None
    return float(values.mean())


# ---------------------------------------------------------------------------
# Main projection function
# ---------------------------------------------------------------------------

def project_rb_line(
    rb: Player,
    opponent: str,
    season: int,
    week: int,
    rb_history: pd.DataFrame,
    rush_defense: Optional[pd.DataFrame] = None,
    injuries_df: Optional[pd.DataFrame] = None,
    recv_history: Optional[pd.DataFrame] = None,
) -> RBProjection:
    """Project this RB's rushing AND receiving stat line for the given game.

    Parameters
    ----------
    rb : Player object (from roster.get_active_roster)
    opponent : team abbreviation of the opposing defense
    season, week : the game we're projecting
    rb_history : full rb_history DataFrame (will be filtered)
    rush_defense : optional defensive stats for rushing matchup
    injuries_df : optional injury report for the health multiplier
    recv_history : optional receiving table; if provided, the RB's receiving
                   stats (target_share, ypt, catch_rate) are also projected.
                   receiving_yards itself is filled by team.py during allocation.

    Returns
    -------
    RBProjection with rushing stats filled. If recv_history is provided AND
    the RB has receiving history, target_share/ypt/catch_rate are populated;
    otherwise those default to 0.0.
    """
    # Get this RB's prior games (walk-forward correct)
    hist = _rb_history_up_to_week(rb.name, season, week, rb_history)
    n_games = len(hist)

    # Backups in the depth chart get scaled-down expectations even with
    # full league baseline. Adjust the carries baseline based on depth.
    # Starter (d1): full ~16 carries; backup (d2): ~7; third-string: ~3
    depth = rb.depth_order if rb.depth_order else 1
    carries_baseline = LEAGUE_AVG_RB_CARRIES_STARTER
    if depth >= 2:
        carries_baseline *= 0.45  # backups get ~45% of a starter's load
    if depth >= 3:
        carries_baseline *= 0.40  # third-string gets ~18% of a starter's load

    def _project_stat(stat: str, league_fallback: float, src=hist) -> float:
        recent = weighted_recent_average(src, stat, n_recent=4)
        n_src = len(src)
        season_avg = float(src[stat].mean()) if (n_src > 0 and stat in src.columns) else None
        return blend_with_baseline(
            recent_value=recent,
            season_baseline=season_avg,
            n_recent_games=min(n_src, 4),
            league_baseline=league_fallback,
        )

    # ----- Rushing -----
    carries = _project_stat("rush_att", carries_baseline)
    ypc = _project_stat("ypc", LEAGUE_AVG_RB_YPC)

    # Opponent (matchup) factors — apply to YPC primarily
    opp_ypc_allowed = _opp_rush_def_recent(opponent, season, week, rush_defense, "def_ypc")
    league_ypc_allowed = _league_rush_def_avg(rush_defense, season, week, "def_ypc")
    matchup_ypc = opponent_factor(opp_ypc_allowed, league_ypc_allowed)

    # Injury factor
    inj = injury_factor(rb.name, rb.team, season, week, injuries_df)

    # Combine rushing
    carries = carries * inj
    ypc = ypc * matchup_ypc * inj
    rush_yards = carries * ypc

    # ----- Receiving (only if recv_history provided) -----
    target_share = 0.0
    ypt = 0.0
    catch_rate = 0.0
    if recv_history is not None and not recv_history.empty:
        # Filter to this RB's RB-only receiving rows (avoid name collisions
        # with WR/TE rows for a different player or a different role).
        rmask = (recv_history["player_name"] == rb.name) & (
            (recv_history["season"] < season)
            | ((recv_history["season"] == season) & (recv_history["week_num"] < week))
        )
        if "position" in recv_history.columns:
            rmask = rmask & (recv_history["position"] == "RB")
        rhist = recv_history[rmask].sort_values(["season", "week_num"])

        if not rhist.empty:
            # League-fallback target share for RBs by depth_order. RBs typically
            # get fewer targets than WR/TE: starter ~10%, backup ~5%.
            rb_target_baseline = 10.0 if depth == 1 else 5.0
            target_share = _project_stat("target_share", rb_target_baseline, src=rhist)
            ypt = _project_stat("ypt", LEAGUE_AVG_RECV_YPT, src=rhist)
            catch_rate = _project_stat("catch_rate", LEAGUE_AVG_RECV_CATCH_RATE, src=rhist)
            # Apply injury to receiving stats too (no separate matchup adjustment
            # for RB receiving — small effect, kept simple)
            target_share = target_share * inj
            ypt = ypt * inj
            catch_rate = catch_rate * inj

    return RBProjection(
        name=rb.name,
        team=rb.team,
        opponent=opponent,
        carries=round(carries, 1),
        ypc=round(ypc, 2),
        rush_yards=round(rush_yards, 1),
        target_share=round(target_share, 2),
        ypt=round(ypt, 2),
        catch_rate=round(catch_rate, 1),
        receiving_yards=0.0,       # filled by team.py during allocation
        receptions=0.0,
        depth_order=depth,
        n_recent_games=min(n_games, 4),
        health_multiplier=round(inj, 3),
        matchup_multiplier=round(matchup_ypc, 3),
    )
