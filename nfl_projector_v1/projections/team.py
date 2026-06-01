"""Team aggregation: combine player projections into team-level production.

This is where the bottom-up architecture pays off. We take:
  - QB's projected stat line (pass_attempts, pass_yards, scramble_yards, ...)
  - RBs' projected stat lines (carries, rush_yards, target_share, ypt)
  - Receivers' projected target shares (WR, TE)

And produce a TeamProduction summary:
  - team_pass_yards = QB's projected pass_yards (the anchor)
  - team_rush_yards = sum of RBs' rush_yards + QB scramble_yards
  - allocated receiving_yards on each receiver, summing to team_pass_yards
  - implied pass_tds and rush_tds via league-average yards-per-TD

The receiver allocation is the trickiest part:
  1. Pool together WR + TE + RB receivers with target_share > 0
  2. Normalize their target_shares so the pool sums to 100%
  3. Compute each receiver's weight = target_share × ypt (efficiency-weighted)
  4. Distribute team_pass_yards proportionally to weights
  5. Mutate each receiver's receiving_yards in place
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional
import pandas as pd

from ..config import (
    LEAGUE_PASS_TD_PER_YARD,
    LEAGUE_RUSH_TD_PER_YARD,
    LEAGUE_AVG_TEAM_FGS_PER_GAME,
    RECEIVER_YARDS_CAP,
    RUSH_VOLUME_CEILING_MULT,
    DEFAULT_TD_RATES,
    TD_RATE_PRIOR_PASS_YDS,
    TD_RATE_PRIOR_RUSH_YDS,
    TD_RATE_CLAMP,
)
from .qb import QBProjection
from .rb import RBProjection
from .wr_te import ReceiverProjection


@dataclass
class TeamProduction:
    """Aggregate offensive production for one team in one game."""
    team: str
    opponent: str

    # Yardage totals
    pass_yards: float          # = QB pass_yards (anchor)
    rush_yards: float          # = sum of RB rush + QB scrambles
    total_yards: float         # pass + rush

    # Implied TDs from yardage × league-avg conversion
    pass_tds_implied: float
    rush_tds_implied: float
    total_tds_implied: float

    # FG projection (team-level, not per-player)
    field_goals: float

    # Player projections retained for interpretability
    qb_projection: Optional[QBProjection] = None
    rb_projections: list[RBProjection] = field(default_factory=list)
    receiver_projections: list[ReceiverProjection] = field(default_factory=list)

    # Diagnostic
    receiver_pool_raw_share_sum: float = 0.0  # sum of shares before normalize
    n_receivers: int = 0
    rush_volume_scaling: float = 1.0           # if backups got scaled up, ratio applied


def _team_recent_fgs_per_game(
    team: str,
    season: int,
    week: int,
    schedule_df: pd.DataFrame,
    n_recent: int = 4,
) -> float:
    """Estimate the team's expected FGs per game.

    Walk-forward correct (only games STRICTLY before this week).

    For v1 we don't have a clean per-team FGs/game stat in the warehouse
    (the schedule has scores, not FGs separately). Use league average for
    now — a known v1 simplification documented in DESIGN.md.

    If/when we wire kicker data or scoring-type breakdowns, replace this
    function. Everything else in team.py is independent of the FG model.
    """
    # Placeholder: league average.
    # TODO v2: compute team-specific rolling FGs/game from a FG data source.
    _ = (team, season, week, schedule_df, n_recent)  # silence linters
    return LEAGUE_AVG_TEAM_FGS_PER_GAME


def _team_recent_rush_attempts(
    team: str,
    season: int,
    week: int,
    rb_history: pd.DataFrame,
    n_recent: int = 4,
) -> Optional[float]:
    """Team's recent total RB carries per game.

    Used as a volume baseline. When backups step into starting roles
    because of injuries, their personal projection is based on their thin
    historical sample (low usage), which produces an unrealistically low
    team-level carry projection. Comparing the projection against this
    baseline lets us scale up volume to match team-level reality.

    Walk-forward correct — only includes games STRICTLY before (season, week).
    Returns None if there's not enough history.
    """
    if rb_history is None or rb_history.empty:
        return None
    if "team_norm" not in rb_history.columns:
        return None

    # All this team's RB-only games strictly before (season, week)
    mask = (
        (rb_history["team_norm"] == team)
        & (rb_history.get("position", "RB") == "RB")
        & (
            (rb_history["season"] < season)
            | ((rb_history["season"] == season) & (rb_history["week_num"] < week))
        )
    )
    hist = rb_history[mask]
    if hist.empty or "rush_att" not in hist.columns:
        return None

    # Sum carries per team-game, then average over recent games
    per_game = (
        hist.groupby(["season", "week_num"])["rush_att"]
        .sum()
        .sort_index()
    )
    if per_game.empty:
        return None
    return float(per_game.tail(n_recent).mean())


def _team_td_rate(
    history: Optional[pd.DataFrame],
    td_col: str,
    yds_col: str,
    team: str,
    season: int,
    week: int,
    league_rate: float,
    prior_yds: float,
    clamp: tuple = TD_RATE_CLAMP,
) -> float:
    """A team's TD-per-yard, computed walk-forward (games STRICTLY before
    (season, week)) and empirical-Bayes shrunk toward the league rate:

        rate = (team_TDs + league_rate * prior_yds) / (team_yds + prior_yds)

    Shrinkage keeps small early-season samples sane; once a team has lots of
    yardage history the prior washes out and we trust its own rate. The result
    is clamped to [lo, hi] x league_rate as a safety rail, and falls back to the
    league rate when there's no usable history.
    """
    if (history is None or history.empty
            or td_col not in history.columns or yds_col not in history.columns):
        return league_rate
    mask = (history["team_norm"] == team) & (
        (history["season"] < season)
        | ((history["season"] == season) & (history["week_num"] < week))
    )
    h = history[mask]
    team_yds = float(h[yds_col].sum())
    if team_yds <= 0:
        return league_rate
    team_td = float(h[td_col].sum())
    rate = (team_td + league_rate * prior_yds) / (team_yds + prior_yds)
    lo, hi = clamp
    return max(league_rate * lo, min(league_rate * hi, rate))


def aggregate_to_team(
    qb_proj: QBProjection,
    rb_projs: list[RBProjection],
    receiver_projs: list[ReceiverProjection],
    team: str,
    opponent: str,
    season: int,
    week: int,
    schedule_df: Optional[pd.DataFrame] = None,
    rb_history: Optional[pd.DataFrame] = None,
    qb_history: Optional[pd.DataFrame] = None,
    td_rates: str = DEFAULT_TD_RATES,
) -> TeamProduction:
    """Aggregate player projections into TeamProduction.

    Side effect: this function MUTATES the receiving_yards and receptions
    fields on each receiver/RB projection passed in. That's intentional —
    after this call, callers can read the populated values from the
    same objects they passed in.

    Parameters
    ----------
    qb_proj : the team's QB projection
    rb_projs : list of RB projections (rushing + receiving)
    receiver_projs : list of WR/TE projections
    team, opponent : team abbreviations
    season, week : the game we're projecting
    schedule_df : optional, used for FG estimation (currently a placeholder)
    rb_history : optional, used for team rush volume baseline. When the
                 sum of projected RB carries falls short of the team's
                 recent average, scale up to match team-level reality.
    qb_history : optional, passing game logs used for the team TD-per-yard rate.
    td_rates : "league" (flat LEAGUE_*_TD_PER_YARD) or "team" (per-team shrunk
               rate via _team_td_rate). See config DEFAULT_TD_RATES.

    Returns
    -------
    TeamProduction with everything populated.
    """
    # ---- Passing yards: the QB anchor ----
    team_pass_yards = qb_proj.pass_yards

    # ---- Allocate passing yards to receivers (WR + TE + RB pool) ----
    # Build the receiver pool: every player with target_share > 0.
    # RBs come from rb_projs, WRs/TEs from receiver_projs.
    pool: list = []  # mixed list of ReceiverProjection and RBProjection
    for r in receiver_projs:
        if r.target_share > 0:
            pool.append(r)
    for rb in rb_projs:
        if rb.target_share > 0:
            pool.append(rb)

    raw_share_sum = sum(p.target_share for p in pool)

    if pool and raw_share_sum > 0:
        # Normalize shares so the pool sums to 100% (then we can distribute
        # 100% of the QB's pass yards across them).
        # Efficiency-weighted allocation: weight = normalized_share × ypt.
        # This gives higher-efficiency receivers a slightly bigger slice
        # of yards than their raw target share would suggest.
        total_weight = 0.0
        weights = []
        for p in pool:
            norm_share = p.target_share / raw_share_sum   # 0-1 fraction
            ypt = p.ypt if p.ypt > 0 else 7.0             # safe fallback
            w = norm_share * ypt
            weights.append(w)
            total_weight += w

        # Distribute pass_yards proportionally to weights
        for p, w in zip(pool, weights):
            if total_weight > 0:
                allocated_yards = team_pass_yards * (w / total_weight)
            else:
                allocated_yards = 0.0
            # Cap at sanity ceiling (~280 yards in a single game is the league
            # max — a few outliers beyond this exist, but capping prevents
            # absurd outputs from compounding bad inputs)
            allocated_yards = min(allocated_yards, RECEIVER_YARDS_CAP)

            # Mutate the projection in place
            p.receiving_yards = round(allocated_yards, 1)
            # Derive receptions from yards / ypr (≈ catch_rate × targets)
            if p.ypt > 0:
                implied_targets = allocated_yards / p.ypt
                p.receptions = round(implied_targets * (p.catch_rate / 100.0), 1)
            else:
                p.receptions = 0.0

    # ---- Rushing: apply team-volume floor with per-RB depth caps ----
    # When a starting RB is missing (e.g., on IR but not yet on the injury
    # report), the remaining backups have low personal histories that
    # under-project team carries. We do two things:
    #
    # (1) Volume: redistribute up to the team's recent total carries, but
    #     no individual RB exceeds a realistic cap for their depth tier.
    #       d1 cap: 22 carries (workhorse load)
    #       d2 cap: 16 carries (committee starter)
    #       d3 cap: 12 carries (clear backup, promoted)
    #       d4+:    8  carries
    #
    # (2) YPC floor: when a backup is forced into starter volume, their
    #     historical garbage-time YPC (often 2-3) understates what they'd
    #     produce as the lead back. Apply a YPC floor of 3.5 (typical
    #     low-end NFL starter) for RBs whose carries got scaled up.
    #     Players already above the floor are unchanged.
    rush_volume_scaling = 1.0
    if rb_history is not None and not rb_history.empty:
        projected_carries = sum(rb.carries for rb in rb_projs)
        baseline_carries = _team_recent_rush_attempts(team, season, week, rb_history)

        if (
            baseline_carries is not None
            and baseline_carries > 0
            and projected_carries > 0
            and projected_carries < baseline_carries * 0.90
        ):
            # Carry caps by depth tier
            CARRY_CAPS = {1: 22, 2: 16, 3: 12}
            YPC_FLOOR_WHEN_PROMOTED = 3.5

            # The deficit is how many more carries to distribute
            deficit = baseline_carries - projected_carries
            # Distribute proportionally to current projected carries
            # (so the player already getting more carries gets more of
            # the boost), but never above each player's depth cap.
            total_assigned = 0.0
            remaining_capacity = []
            for rb in rb_projs:
                cap = CARRY_CAPS.get(rb.depth_order or 4, 8)
                room = max(0.0, cap - rb.carries)
                remaining_capacity.append(room)
            total_room = sum(remaining_capacity)

            if total_room > 0:
                # Allocate the deficit proportionally to each player's
                # remaining room. Players already at their cap get nothing;
                # the lead remaining back absorbs the most.
                allocatable = min(deficit, total_room)
                for rb, room in zip(rb_projs, remaining_capacity):
                    if total_room > 0:
                        added = allocatable * (room / total_room)
                    else:
                        added = 0.0
                    new_carries = rb.carries + added
                    # YPC floor applies when we promoted this player's
                    # volume meaningfully (added > 2 carries) AND their
                    # current YPC is below the floor
                    new_ypc = rb.ypc
                    if added > 2.0 and rb.ypc < YPC_FLOOR_WHEN_PROMOTED:
                        new_ypc = YPC_FLOOR_WHEN_PROMOTED
                    rb.carries = round(new_carries, 1)
                    rb.ypc = round(new_ypc, 2)
                    rb.rush_yards = round(rb.carries * rb.ypc, 1)
                    total_assigned += added

                # Diagnostic: effective scaling vs baseline
                new_total = sum(r.carries for r in rb_projs)
                rush_volume_scaling = round(new_total / projected_carries, 3)

        elif (
            baseline_carries is not None
            and baseline_carries > 0
            and projected_carries > baseline_carries * RUSH_VOLUME_CEILING_MULT
        ):
            # Team-volume CEILING (the mirror of the floor above). Summing each
            # back's solo-average carries overshoots a team's real volume when
            # the roster is inclusive — extra backs, or a committee where snap
            # share makes two RBs both look like starters. Without a ceiling this
            # inflated team rush yards (e.g. a 4th back pushing a team to 250+
            # rush yards) and over-projected total points. Scale every RB's
            # carries down proportionally so the team total caps at
            # RUSH_VOLUME_CEILING_MULT x its recent baseline (headroom for
            # run-heavy scripts); the relative split among backs is preserved.
            cap = baseline_carries * RUSH_VOLUME_CEILING_MULT
            scale = cap / projected_carries
            for rb in rb_projs:
                rb.carries = round(rb.carries * scale, 1)
                rb.rush_yards = round(rb.carries * rb.ypc, 1)
            rush_volume_scaling = round(scale, 3)

    # ---- Rushing yards: sum of (possibly-scaled) RBs + QB scrambles ----
    rb_rush_total = sum(rb.rush_yards for rb in rb_projs)
    team_rush_yards = rb_rush_total + qb_proj.scramble_yards

    # ---- Total yards ----
    team_total_yards = team_pass_yards + team_rush_yards

    # ---- Implied TDs from yardage × TD-per-yard conversion ----
    # "league" = flat rate for all; "team" = each team's own walk-forward,
    # shrunk TD-per-yard (fixes the flat rate under-/over-projecting efficient/
    # inefficient offenses — DESIGN.md §11 #5).
    if td_rates == "team":
        pass_rate = _team_td_rate(
            qb_history, "pass_td", "pass_yds", team, season, week,
            LEAGUE_PASS_TD_PER_YARD, TD_RATE_PRIOR_PASS_YDS,
        )
        rush_rate = _team_td_rate(
            rb_history, "rush_td", "rush_yds", team, season, week,
            LEAGUE_RUSH_TD_PER_YARD, TD_RATE_PRIOR_RUSH_YDS,
        )
    else:
        pass_rate = LEAGUE_PASS_TD_PER_YARD
        rush_rate = LEAGUE_RUSH_TD_PER_YARD
    pass_tds = team_pass_yards * pass_rate
    rush_tds = team_rush_yards * rush_rate
    total_tds = pass_tds + rush_tds

    # ---- FGs (placeholder league average for v1) ----
    fgs = _team_recent_fgs_per_game(team, season, week, schedule_df)

    return TeamProduction(
        team=team,
        opponent=opponent,
        pass_yards=round(team_pass_yards, 1),
        rush_yards=round(team_rush_yards, 1),
        total_yards=round(team_total_yards, 1),
        pass_tds_implied=round(pass_tds, 2),
        rush_tds_implied=round(rush_tds, 2),
        total_tds_implied=round(total_tds, 2),
        field_goals=round(fgs, 2),
        qb_projection=qb_proj,
        rb_projections=rb_projs,
        receiver_projections=receiver_projs,
        receiver_pool_raw_share_sum=round(raw_share_sum, 2),
        n_receivers=len(pool),
        rush_volume_scaling=round(rush_volume_scaling, 3),
    )
