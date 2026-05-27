"""Points conversion: turn TeamProduction into expected points.

The math:
    points = (total_tds_implied × POINTS_PER_TD_WITH_PAT)
           + (field_goals × POINTS_PER_FG)

That's it. The complexity is upstream (in team.py — how do TDs and FGs
get estimated). Here we just multiply by constants.

POINTS_PER_TD_WITH_PAT is set to 6.95 (6 TD + 0.95 PAT make rate) which
empirically accounts for missed XPs and the small contribution from
2-pt conversions averaging out.

DST and special-teams scores are NOT included in v1 — they're ~4% of
team scoring and add complexity without much accuracy gain. v2 may
include them as a flat league-average bump.
"""
from __future__ import annotations

from ..config import POINTS_PER_TD_WITH_PAT, POINTS_PER_FG
from .team import TeamProduction


def production_to_points(production: TeamProduction) -> float:
    """Convert team production into expected points scored.

    Parameters
    ----------
    production : TeamProduction from aggregate_to_team()

    Returns
    -------
    Expected points for the team in this game. Returned as a float;
    callers can round for display.
    """
    td_points = production.total_tds_implied * POINTS_PER_TD_WITH_PAT
    fg_points = production.field_goals * POINTS_PER_FG
    return td_points + fg_points
