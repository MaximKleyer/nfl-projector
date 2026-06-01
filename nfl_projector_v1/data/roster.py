"""Roster identification: who do we project for team X in week N?

Two modes (DESIGN.md §12), chosen via `roster_mode`:

  "snaps"        — NEW (design target). Skill positions (WR/RB/FB->RB/TE) are
                   selected from FPD snap share over each player's last N games
                   ACTUALLY PLAYED — injury-aware, never calendar weeks. QB is
                   resolved separately: manual override -> depth-chart QB1 ->
                   injury fallthrough to QB2. Players returning from an absence
                   carry a return-game ramp factor and a team-games-missed count
                   (for staleness regression).
  "depth_chart"  — LEGACY. nflverse depth-chart top-N per position + an Out/IR
                   drop + a calendar-based recent-activity filter. Kept so we can
                   A/B backtest the snap-share path against it.

The snap path fixes the omission bug where a player returning from a 4+ week
injury was dropped by the calendar activity filter (§12.3): with snaps, "recent"
means last N games played, so his pre-injury role is recognized immediately.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional
import pandas as pd

from ..config import (
    RECENT_GAMES_WINDOW,
    SNAP_SHARE_MIN_PCT,
    SNAP_ROSTER_CAPS,
    RETURN_TRIGGER_GAMES_MISSED,
    RETURN_RAMP_FACTORS,
    QB_WONT_START_STATUSES,
)
from ..utils import normalize_name


@dataclass
class Player:
    """A player we plan to project for one specific team-week."""
    name: str
    team: str
    position: str          # 'QB', 'RB', 'WR', 'TE'
    depth_order: int       # 1 = starter, 2 = backup, etc.
    injury_status: Optional[str] = None

    # Snap-share path extras (DESIGN.md §12.4/§12.5). Defaults keep the legacy
    # depth-chart path and any downstream code that ignores them working unchanged.
    team_games_missed: int = 0        # team games between last active game and this week
    return_ramp_factor: float = 1.0   # volume multiplier for a just-returned player
    player_key: Optional[str] = None  # snaps/stat join key when known


# How many players to keep per position on the LEGACY depth-chart path. The snap
# path uses SNAP_ROSTER_CAPS instead but reuses these limits for its fallback.
ROSTER_DEPTH_LIMITS = {
    "QB": 1,   # one starter only — backups don't get projections in v1
    "RB": 3,
    "WR": 4,
    "TE": 2,
}


# Statuses that mean "won't play this week"
WONT_PLAY_STATUSES = {"Out", "IR"}


# ---------------------------------------------------------------------------
# Injury lookup (shared by both paths)
# ---------------------------------------------------------------------------

def _get_injury_status(
    player_name: str,
    team: str,
    season: int,
    week: int,
    injuries_df: Optional[pd.DataFrame],
) -> Optional[str]:
    """Return the player's injury report_status, or None if not listed."""
    if injuries_df is None or injuries_df.empty:
        return None
    if "player_name" not in injuries_df.columns:
        return None
    matches = injuries_df[
        (injuries_df["player_name"] == player_name)
        & (injuries_df["team"] == team)
        & (injuries_df["season"] == season)
        & (injuries_df["week"] == week)
    ]
    if matches.empty:
        return None
    return str(matches["report_status"].iloc[-1]).strip()


# ---------------------------------------------------------------------------
# Snap-share selection (roster_mode="snaps")
# ---------------------------------------------------------------------------

def _team_game_weeks(team: str, season: int,
                     schedule_df: Optional[pd.DataFrame]) -> list[int]:
    """Week numbers the team has a scheduled game in this season (ascending)."""
    if schedule_df is None or schedule_df.empty:
        return []
    m = (schedule_df["season"] == season) & (
        (schedule_df["home_team"] == team) | (schedule_df["away_team"] == team)
    )
    return sorted(int(w) for w in schedule_df.loc[m, "week"].unique())


def _games_back_index(active_weeks: set[int], team_weeks_before: list[int],
                      trigger: int) -> Optional[int]:
    """How many games 'back from injury' the upcoming game is.

    1 = first game back, 2 = second, ... None = no qualifying recent absence.
    `team_weeks_before` is the team's game-weeks strictly before the week we're
    projecting (ascending); `active_weeks` is the weeks the player actually played.
    Walks back from the end: counts the current played stint, then the missed run
    just before it; if that run is >= trigger, the upcoming game is the
    (stint + 1)-th game back.
    """
    seq = [(w in active_weeks) for w in team_weeks_before]
    i = len(seq) - 1
    stint = 0
    while i >= 0 and seq[i]:
        stint += 1
        i -= 1
    missed_run = 0
    while i >= 0 and not seq[i]:
        missed_run += 1
        i -= 1
    if missed_run >= trigger:
        return stint + 1
    return None


def _return_ramp_factor(games_back: Optional[int]) -> float:
    if games_back is None:
        return 1.0
    return RETURN_RAMP_FACTORS.get(games_back, 1.0)


def select_roster_from_snaps(
    team: str,
    season: int,
    week: int,
    snaps_df: Optional[pd.DataFrame],
    injuries_df: Optional[pd.DataFrame],
    schedule_df: Optional[pd.DataFrame],
    n: int = RECENT_GAMES_WINDOW,
) -> list[Player]:
    """Select WR/RB/TE (FB folded into RB) from snap share.

    For each player on the team with snap history strictly before (season, week):
      - average total_snap_pct over their last n ACTIVE games (games played),
      - drop if below SNAP_SHARE_MIN_PCT or if Out/IR on the injury report,
      - rank within position by snap% -> depth_order, capped by SNAP_ROSTER_CAPS,
      - attach team_games_missed (for staleness) + return_ramp_factor.

    NOTE: snaps stores standard team codes in `team_norm` (`team` holds raw FPD
    codes), so we filter on team_norm.
    """
    if snaps_df is None or snaps_df.empty:
        return []
    mask = (snaps_df["team_norm"] == team) & (
        (snaps_df["season"] < season)
        | ((snaps_df["season"] == season) & (snaps_df["week_num"] < week))
    )
    df = snaps_df.loc[mask, ["player_key", "player_name", "position",
                             "season", "week_num", "total_snap_pct"]].copy()
    if df.empty:
        return []
    df["position"] = df["position"].replace({"FB": "RB"})
    df = df[df["position"].isin(["WR", "RB", "TE"])]
    if df.empty:
        return []

    team_weeks_before = [w for w in _team_game_weeks(team, season, schedule_df)
                         if w < week]

    candidates: dict[str, list] = {"WR": [], "RB": [], "TE": []}
    for player_key, g in df.groupby("player_key", sort=False):
        g = g.sort_values(["season", "week_num"])
        avg_snap = float(g.tail(n)["total_snap_pct"].mean())
        if avg_snap < SNAP_SHARE_MIN_PCT:
            continue
        name = str(g["player_name"].iloc[-1]).strip()
        pos = str(g["position"].iloc[-1])
        if pos not in candidates:
            continue
        inj = _get_injury_status(name, team, season, week, injuries_df)
        if inj in WONT_PLAY_STATUSES:
            continue

        # Injury-gap signals — this season only (cross-season gaps are not injuries)
        this_season = g[g["season"] == season]
        if this_season.empty:
            team_games_missed = 0
            games_back = None
        else:
            active_weeks = {int(w) for w in this_season["week_num"]}
            last_active = max(active_weeks)
            team_games_missed = sum(1 for w in team_weeks_before if w > last_active)
            games_back = _games_back_index(active_weeks, team_weeks_before,
                                           RETURN_TRIGGER_GAMES_MISSED)

        candidates[pos].append({
            "name": name,
            "player_key": player_key,
            "avg_snap": avg_snap,
            "injury_status": inj,
            "team_games_missed": team_games_missed,
            "ramp": _return_ramp_factor(games_back),
        })

    out: list[Player] = []
    for pos, plist in candidates.items():
        plist.sort(key=lambda p: p["avg_snap"], reverse=True)
        cap = SNAP_ROSTER_CAPS.get(pos, len(plist))
        for depth, p in enumerate(plist[:cap], start=1):
            out.append(Player(
                name=p["name"], team=team, position=pos, depth_order=depth,
                injury_status=p["injury_status"],
                team_games_missed=p["team_games_missed"],
                return_ramp_factor=p["ramp"], player_key=p["player_key"],
            ))
    return out


def _depth_chart_fallback(
    team: str,
    season: int,
    week: int,
    depth_chart: Optional[pd.DataFrame],
    injuries_df: Optional[pd.DataFrame],
    snap_norm_names: set[str],
    by_pos_base_depth: dict[str, int],
) -> list[Player]:
    """Add depth-chart skill players with NO snap history at all (rookie debuts,
    fresh call-ups). Players who appear in snaps but fell below the threshold are
    intentionally excluded — their low snap share is the signal to leave them out.
    """
    out: list[Player] = []
    if depth_chart is None or depth_chart.empty:
        return out
    team_dc = depth_chart[depth_chart["team"] == team]
    if team_dc.empty:
        return out
    for pos in ("WR", "RB", "TE"):
        limit = ROSTER_DEPTH_LIMITS.get(pos, 0)
        pos_dc = (
            team_dc[team_dc["position"] == pos]
            .sort_values("depth_order")
            .head(limit)
        )
        extra = 0
        base = by_pos_base_depth.get(pos, 0)
        for _, row in pos_dc.iterrows():
            name = str(row["player_name"]).strip()
            if not name or normalize_name(name) in snap_norm_names:
                continue
            inj = _get_injury_status(name, team, season, week, injuries_df)
            if inj in WONT_PLAY_STATUSES:
                continue
            extra += 1
            out.append(Player(
                name=name, team=team, position=pos,
                depth_order=base + extra, injury_status=inj,
            ))
    return out


# ---------------------------------------------------------------------------
# QB resolution (roster_mode="snaps"; FPD snaps carry no QB rows)
# ---------------------------------------------------------------------------

def _qb_override(qb_starters: Optional[dict], season: int, week: int,
                 team: str) -> Optional[str]:
    """Look up a manual starter override (season -> week -> team -> name)."""
    if not qb_starters:
        return None
    by_week = qb_starters.get(season) or {}
    by_team = by_week.get(week) or {}
    name = by_team.get(team)
    return str(name).strip() if name else None


def resolve_qb(
    team: str,
    season: int,
    week: int,
    depth_chart: Optional[pd.DataFrame],
    injuries_df: Optional[pd.DataFrame],
    qb_starters: Optional[dict],
) -> Optional[Player]:
    """Resolve the starting QB (DESIGN.md §12.2):
    manual override -> depth-chart QB1 -> if Out/IR/Doubtful, next QB on the chart.
    Returns None if no depth chart is available (game.py synthesizes a league-avg QB).
    """
    override = _qb_override(qb_starters, season, week, team)
    if override:
        return Player(
            name=override, team=team, position="QB", depth_order=1,
            injury_status=_get_injury_status(override, team, season, week, injuries_df),
        )
    if depth_chart is None or depth_chart.empty:
        return None
    qbs = depth_chart[
        (depth_chart["team"] == team) & (depth_chart["position"] == "QB")
    ].sort_values("depth_order")
    if qbs.empty:
        return None
    for _, row in qbs.iterrows():
        name = str(row["player_name"]).strip()
        if not name:
            continue
        inj = _get_injury_status(name, team, season, week, injuries_df)
        if inj in QB_WONT_START_STATUSES:
            continue  # this QB won't start — try the next on the depth chart
        return Player(name=name, team=team, position="QB", depth_order=1,
                      injury_status=inj)
    # Every listed QB is Out/Doubtful — fall back to QB1 anyway (someone starts).
    row = qbs.iloc[0]
    name = str(row["player_name"]).strip()
    return Player(name=name, team=team, position="QB", depth_order=1,
                  injury_status=_get_injury_status(name, team, season, week, injuries_df))


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

def get_active_roster(
    team: str,
    season: int,
    week: int,
    *,
    depth_chart: pd.DataFrame,
    qb_history: Optional[pd.DataFrame] = None,
    rb_history: Optional[pd.DataFrame] = None,
    recv_history: Optional[pd.DataFrame] = None,
    snaps_df: Optional[pd.DataFrame] = None,
    injuries_df: Optional[pd.DataFrame] = None,
    schedule_df: Optional[pd.DataFrame] = None,
    qb_starters: Optional[dict] = None,
    roster_mode: str = "depth_chart",
    enforce_activity_filter: bool = True,
) -> list[Player]:
    """The list of players to project for this team-week.

    roster_mode="snaps": WR/RB/TE from snap share + a depth-chart fallback for
    players with no snap history, plus a QB from resolve_qb().
    roster_mode="depth_chart": the legacy nflverse-depth-chart selection.
    """
    if roster_mode == "snaps":
        skill = select_roster_from_snaps(
            team, season, week, snaps_df, injuries_df, schedule_df
        )
        # Normalized snap names for the team (for the "no snap history" fallback test)
        snap_norm_names: set[str] = set()
        if snaps_df is not None and not snaps_df.empty:
            m = (snaps_df["team_norm"] == team) & (
                (snaps_df["season"] < season)
                | ((snaps_df["season"] == season) & (snaps_df["week_num"] < week))
            )
            snap_norm_names = set(snaps_df.loc[m, "player_name_norm"])
        by_pos_base = {}
        for p in skill:
            by_pos_base[p.position] = max(by_pos_base.get(p.position, 0), p.depth_order)
        skill += _depth_chart_fallback(
            team, season, week, depth_chart, injuries_df, snap_norm_names, by_pos_base
        )
        qb = resolve_qb(team, season, week, depth_chart, injuries_df, qb_starters)
        return ([qb] if qb is not None else []) + skill

    return _select_roster_depth_chart(
        team, season, week, depth_chart,
        qb_history, rb_history, recv_history, injuries_df, enforce_activity_filter,
    )


# ---------------------------------------------------------------------------
# Legacy depth-chart selection (roster_mode="depth_chart")
# ---------------------------------------------------------------------------

def _player_had_recent_touches(
    player_name: str,
    team: str,
    season: int,
    week: int,
    qb_history: pd.DataFrame,
    rb_history: pd.DataFrame,
    recv_history: pd.DataFrame,
    n_weeks_lookback: int = 4,
) -> bool:
    """True if the player appears in any stat table recently.

    CALENDAR-based lookback (prior n_weeks_lookback weeks of this season; also the
    prior season before week 5). This is the filter the snap path replaces — it
    drops players returning from a 4+ week injury.
    """
    min_week_current = max(1, week - n_weeks_lookback)
    use_prior_season = week <= 4

    for df in (qb_history, rb_history, recv_history):
        if df is None or df.empty:
            continue
        if "player_name" not in df.columns:
            continue
        mask = (
            (df["player_name"] == player_name)
            & (df["team_norm"] == team)
            & (df["season"] == season)
            & (df["week_num"] >= min_week_current)
            & (df["week_num"] < week)
        )
        if mask.any():
            return True
        if use_prior_season:
            mask_prior = (
                (df["player_name"] == player_name)
                & (df["season"] == season - 1)
            )
            if mask_prior.any():
                return True
    return False


def _select_roster_depth_chart(
    team: str,
    season: int,
    week: int,
    depth_chart: pd.DataFrame,
    qb_history: pd.DataFrame,
    rb_history: pd.DataFrame,
    recv_history: pd.DataFrame,
    injuries_df: Optional[pd.DataFrame] = None,
    enforce_activity_filter: bool = True,
) -> list[Player]:
    """The original depth-chart + injury + recent-activity roster selection."""
    if depth_chart is None or depth_chart.empty:
        return []

    team_dc = depth_chart[depth_chart["team"] == team].copy()
    if team_dc.empty:
        return []

    out: list[Player] = []

    for pos, limit in ROSTER_DEPTH_LIMITS.items():
        pos_dc = (
            team_dc[team_dc["position"] == pos]
            .sort_values("depth_order")
            .head(limit)
        )

        for _, row in pos_dc.iterrows():
            player_name = str(row["player_name"]).strip()
            if not player_name:
                continue

            inj_status = _get_injury_status(
                player_name, team, season, week, injuries_df
            )
            if inj_status in WONT_PLAY_STATUSES:
                continue

            if enforce_activity_filter:
                had_touches = _player_had_recent_touches(
                    player_name, team, season, week,
                    qb_history, rb_history, recv_history,
                )
                if not had_touches:
                    continue

            out.append(Player(
                name=player_name,
                team=team,
                position=pos,
                depth_order=int(row.get("depth_order", 99)),
                injury_status=inj_status,
            ))

    return out
