"""Game prediction: orchestrates everything into one prediction per game.

The flow for ONE game:

  1. Look up the active rosters for both teams (data/roster.py)
  2. For each team:
       a. Project the QB                     (projections/qb.py)
       b. Project each RB                    (projections/rb.py)
       c. Project each WR/TE                 (projections/wr_te.py)
       d. Aggregate to team production       (projections/team.py)
       e. Convert production to points       (projections/points.py)
  3. Compute margin, total, win probability
  4. If Vegas line available, derive ATS pick + OU pick

This module is almost pure orchestration — the logic lives in the
projection modules. The point is that the orchestrator's signature
stays simple: (home, away, season, week, all_data) → GamePrediction.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional
import math
import pandas as pd

from .config import (
    NFL_MARGIN_STD_DEV, DEFAULT_ROSTER_MODE, DEFAULT_TD_RATES, DEFAULT_FG_RATES,
    POINTS_CALIBRATION_PER_TEAM, DEFAULT_CALIBRATE,
    DEFAULT_HOME_FIELD, LEAGUE_HFA, HFA_SHRINKAGE_GAMES, HFA_CLAMP,
    DEFAULT_ENVIRONMENT, DOME_TOTAL_ADJUST, OUTDOOR_TOTAL_ADJUST,
)
from .data.roster import Player, get_active_roster
from .projections.qb import project_qb_line, QBProjection
from .projections.rb import project_rb_line
from .projections.wr_te import project_receiver_line
from .projections.team import aggregate_to_team, TeamProduction
from .projections.points import production_to_points


@dataclass
class GamePrediction:
    """Full prediction for one NFL game."""
    home_team: str
    away_team: str
    season: int
    week: int

    # Core predictions
    predicted_home_score: float
    predicted_away_score: float
    predicted_margin: float        # home - away (positive = home favored)
    predicted_total: float         # home + away

    # SU pick + win probability
    su_pick: str                   # team that's predicted to win
    win_prob_home: float           # P(home wins) — 0 to 1
    win_prob_away: float           # P(away wins) — 0 to 1

    # Vegas-based derived metrics (None if no Vegas line found)
    spread_close: Optional[float] = None        # nflverse convention: negative = home favored
    total_close: Optional[float] = None
    ats_pick: Optional[str] = None              # which team covers
    ats_prob: Optional[float] = None            # probability of that pick
    ou_pick: Optional[str] = None               # "OVER" or "UNDER"
    ou_prob: Optional[float] = None             # probability of that pick

    # Situational ATS overlay (separate from the model; DESIGN.md §16)
    situational_ats_pick: Optional[str] = None   # team to bet ATS, or None
    situational_ats_reason: Optional[str] = None # which signal(s) fired

    # Full team production for interpretation
    home_production: Optional[TeamProduction] = None
    away_production: Optional[TeamProduction] = None


# ---------------------------------------------------------------------------
# Vegas line lookup
# ---------------------------------------------------------------------------

def _lookup_vegas_line(
    home: str,
    away: str,
    season: int,
    week: int,
    vegas_df: Optional[pd.DataFrame],
) -> tuple[Optional[float], Optional[float]]:
    """Return (spread_close, total_close) from the vegas table, or (None, None).

    nflverse vegas table is keyed by game_id, which has the format:
        '{season}_{week:02d}_{away_team}_{home_team}'  e.g. '2024_14_LAC_KC'
    """
    if vegas_df is None or vegas_df.empty:
        return None, None
    # Build the game_id key (away first, then home, per nflverse convention)
    game_id = f"{season}_{int(week):02d}_{away}_{home}"
    matches = vegas_df[vegas_df["game_id"] == game_id]
    if matches.empty:
        return None, None
    row = matches.iloc[0]
    spread = row.get("spread_close")
    total = row.get("total_close")
    spread = float(spread) if pd.notna(spread) else None
    total = float(total) if pd.notna(total) else None
    return spread, total


# ---------------------------------------------------------------------------
# Probability helpers
# ---------------------------------------------------------------------------

def _normal_cdf(x: float) -> float:
    """Standard normal CDF via erf. No scipy dependency for this one calc."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _win_probability(predicted_margin: float, std_dev: float = NFL_MARGIN_STD_DEV) -> float:
    """P(home wins) given predicted margin.

    Uses normal CDF: P(actual_margin > 0 | predicted_margin) = CDF(predicted/std).
    Empirically NFL margin std dev ≈ 14.34 (from 2023-2025 in warehouse).

    Examples:
       predicted_margin =  0 → 0.500 (true coin flip)
       predicted_margin =  3 → 0.583
       predicted_margin =  7 → 0.687
       predicted_margin = 14 → 0.835
    """
    if std_dev <= 0:
        return 0.5
    return _normal_cdf(predicted_margin / std_dev)


def _cover_probability(
    predicted_margin: float,
    spread_close: float,
    std_dev: float = NFL_MARGIN_STD_DEV,
) -> float:
    """P(home covers the spread) given predicted margin.

    Vegas convention (nflverse): negative spread_close = home favored.
    Home "covers" when: actual_margin > -spread_close
    So P(cover) = P(actual_margin + spread_close > 0)
              = P(actual_margin > -spread_close)
              = CDF((predicted_margin - (-spread_close)) / std)
              = CDF((predicted_margin + spread_close) / std)
    """
    if std_dev <= 0:
        return 0.5
    return _normal_cdf((predicted_margin + spread_close) / std_dev)


def _over_probability(
    predicted_total: float,
    total_close: float,
    std_dev: float = 9.93,   # ~stddev of team total points; approximate
) -> float:
    """P(actual total > total_close) given predicted total.

    Using 2x the team-PPG stddev as a rough total-stddev (the two team
    scores aren't independent — correlated by pace/script — but this
    works as a calibrated approximation).
    """
    total_std = std_dev * math.sqrt(2.0)
    if total_std <= 0:
        return 0.5
    return _normal_cdf((predicted_total - total_close) / total_std)


# ---------------------------------------------------------------------------
# Home-field advantage (DESIGN.md §14)
# ---------------------------------------------------------------------------

def compute_team_hfa(
    schedule_df: Optional[pd.DataFrame],
    before_season: int,
    league_hfa: float = LEAGUE_HFA,
    k: float = HFA_SHRINKAGE_GAMES,
    clamp: tuple[float, float] = HFA_CLAMP,
) -> dict[str, float]:
    """Per-team home-field advantage (margin points) from completed games
    STRICTLY BEFORE `before_season` — walk-forward safe.

    A team's raw HFA is (mean margin when home − mean margin when away) / 2; the
    construction cancels team quality and leaves the home/venue/travel effect.
    Each raw estimate is empirical-Bayes shrunk toward `league_hfa` (small samples
    are noisy — ~8-9 home games/season) and clamped. Returns {team: hfa}; teams
    with no prior history are omitted (caller falls back to league_hfa).
    """
    if schedule_df is None or schedule_df.empty:
        return {}
    g = schedule_df[
        schedule_df["home_score"].notna()
        & schedule_df["away_score"].notna()
        & (schedule_df["season"] < before_season)
    ]
    if g.empty:
        return {}
    margin = (g["home_score"] - g["away_score"]).astype(float)
    lo, hi = clamp
    out: dict[str, float] = {}
    for team in set(g["home_team"]) | set(g["away_team"]):
        home_m = margin[g["home_team"] == team]
        away_m = -margin[g["away_team"] == team]   # team's margin when away
        if home_m.empty or away_m.empty:
            continue
        raw = (home_m.mean() - away_m.mean()) / 2.0
        n = len(home_m) + len(away_m)
        shrunk = league_hfa + (raw - league_hfa) * (n / (n + k))
        out[team] = float(min(hi, max(lo, shrunk)))
    return out


def _home_field_points(home_team: str, season: int, data: dict) -> float:
    """Home-field margin (points) to add to the home team for this game, per
    data["home_field"] mode. "team" mode computes the walk-forward per-team map
    once per season and caches it on the data dict."""
    mode = data.get("home_field", DEFAULT_HOME_FIELD)
    if mode == "none":
        return 0.0
    if mode == "league":
        return LEAGUE_HFA
    # "team": lazily compute + cache the per-season HFA map (walk-forward).
    cache = data.setdefault("_team_hfa_cache", {})
    if season not in cache:
        cache[season] = compute_team_hfa(data.get("schedule"), before_season=season)
    return float(cache[season].get(home_team, LEAGUE_HFA))


def _environment_total_adjust(
    home_team: str, away_team: str, season: int, week: int, data: dict,
) -> float:
    """Total-points adjustment for this game's environment, per data["environment"].

    "dome" mode nudges the game TOTAL by the measured conditional residual: up in
    a dome/closed-roof game, slightly down outdoors (mean-zero at the league dome
    rate, so overall calibration is preserved). Returns points to ADD to the total;
    the caller splits it equally between the two teams so margin/SU/ATS are
    unchanged. 0 if the mode is off or the game's dome flag is unknown.
    """
    mode = data.get("environment", DEFAULT_ENVIRONMENT)
    if mode != "dome":
        return 0.0
    sched = data.get("schedule")
    if sched is None or sched.empty or "dome" not in sched.columns:
        return 0.0
    row = sched[
        (sched["season"] == season) & (sched["week"] == week)
        & (sched["home_team"] == home_team) & (sched["away_team"] == away_team)
    ]
    if row.empty:
        return 0.0
    dome = pd.to_numeric(row.iloc[0]["dome"], errors="coerce")
    if dome == 1:
        return DOME_TOTAL_ADJUST
    if dome == 0:
        return OUTDOOR_TOTAL_ADJUST
    return 0.0


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def _project_one_team(
    team: str,
    opponent: str,
    season: int,
    week: int,
    data: dict,
    enforce_activity_filter: bool = True,
) -> TeamProduction:
    """Project a single team's production for one game."""
    # 1. Active roster
    from .data.depth_charts import get_depth_chart
    dc = get_depth_chart(season, week, schedule=data.get("schedule"))
    roster: list[Player] = get_active_roster(
        team=team, season=season, week=week,
        depth_chart=dc,
        qb_history=data["qb_history"],
        rb_history=data["rb_history"],
        recv_history=data["recv_history"],
        snaps_df=data.get("snaps"),
        injuries_df=data.get("injuries"),
        schedule_df=data.get("schedule"),
        qb_starters=data.get("qb_starters"),
        roster_mode=data.get("roster_mode", DEFAULT_ROSTER_MODE),
        enforce_activity_filter=enforce_activity_filter,
    )

    # 2. Group by position
    qbs = [p for p in roster if p.position == "QB"]
    rbs = [p for p in roster if p.position == "RB"]
    receivers = [p for p in roster if p.position in ("WR", "TE")]

    # 3. Project the QB (first one on depth chart — already top-1 from roster filter)
    if not qbs:
        # No QB on roster — extremely unlikely but defensive. Synthesize a
        # league-average QB projection so we don't crash; this WILL produce
        # a weird projection, the caller should treat it as a data issue.
        qb_proj = QBProjection(
            name="(no QB found)", team=team, opponent=opponent,
            pass_attempts=32.7, completions=21.2, completion_pct=64.9,
            ypa=7.14, pass_yards=233.5, interceptions=0.7,
            sack_count=2.3, scramble_yards=11.4,
            n_recent_games=0, health_multiplier=1.0, matchup_multiplier_yards=1.0,
        )
    else:
        qb_proj = project_qb_line(
            qb=qbs[0], opponent=opponent, season=season, week=week,
            qb_history=data["qb_history"],
            pass_defense=data.get("pass_defense"),
            injuries_df=data.get("injuries"),
        )

    # 4. Project each RB (rushing + receiving)
    rb_projs = [
        project_rb_line(
            rb=rb, opponent=opponent, season=season, week=week,
            rb_history=data["rb_history"],
            rush_defense=data.get("rush_defense"),
            injuries_df=data.get("injuries"),
            recv_history=data.get("recv_history"),
        )
        for rb in rbs
    ]

    # 5. Project each WR/TE
    recv_projs = [
        project_receiver_line(
            receiver=r, opponent=opponent, season=season, week=week,
            recv_history=data["recv_history"],
            recv_defense=data.get("recv_defense"),
            injuries_df=data.get("injuries"),
        )
        for r in receivers
    ]

    # 6. Aggregate to team production (this also allocates receiver yards
    #    and applies team-level rush volume floor)
    return aggregate_to_team(
        qb_proj=qb_proj,
        rb_projs=rb_projs,
        receiver_projs=recv_projs,
        team=team, opponent=opponent, season=season, week=week,
        schedule_df=data.get("schedule"),
        rb_history=data.get("rb_history"),
        qb_history=data.get("qb_history"),
        td_rates=data.get("td_rates", DEFAULT_TD_RATES),
        kicking_df=data.get("kicking"),
        fg_rates=data.get("fg_rates", DEFAULT_FG_RATES),
    )


def project_game(
    home_team: str,
    away_team: str,
    season: int,
    week: int,
    data: dict,
    enforce_activity_filter: bool = True,
) -> GamePrediction:
    """Project one full game and return a GamePrediction.

    Parameters
    ----------
    home_team, away_team : team abbreviations
    season, week : the game to predict
    data : dict of DataFrames from data.loaders.load_all() — must include
           schedule, vegas, injuries, qb_history, rb_history, recv_history,
           pass_defense, rush_defense, recv_defense
    enforce_activity_filter : pass to roster filter. Default True; set
           False for weeks where there's no usable history.

    Returns
    -------
    GamePrediction with everything populated.
    """
    # Project each team's production
    home_prod = _project_one_team(home_team, away_team, season, week, data,
                                  enforce_activity_filter=enforce_activity_filter)
    away_prod = _project_one_team(away_team, home_team, season, week, data,
                                  enforce_activity_filter=enforce_activity_filter)

    # Points. Optional global calibration adds the same constant to both teams,
    # so it shifts the TOTAL (and O/U) but leaves margin/SU/ATS unchanged.
    calib = data.get("points_calibration",
                     POINTS_CALIBRATION_PER_TEAM if DEFAULT_CALIBRATE else 0.0)
    home_score = production_to_points(home_prod) + calib
    away_score = production_to_points(away_prod) + calib

    # Home-field advantage: total-preserving margin shift (home +h/2, away -h/2),
    # so it moves margin / SU / win-prob but leaves the calibrated total alone.
    hfa = _home_field_points(home_team, season, data)
    home_score += hfa / 2.0
    away_score -= hfa / 2.0

    # Environment (dome): total-only adjustment split equally between both teams,
    # so it moves the TOTAL / O-U but leaves margin / SU / ATS unchanged.
    env_adj = _environment_total_adjust(home_team, away_team, season, week, data)
    home_score += env_adj / 2.0
    away_score += env_adj / 2.0

    margin = home_score - away_score
    total = home_score + away_score

    # Win probability (home perspective)
    win_prob_home = _win_probability(margin)
    win_prob_away = 1.0 - win_prob_home

    # SU pick
    su_pick = home_team if margin > 0 else away_team

    # Vegas-derived metrics
    spread_close, total_close = _lookup_vegas_line(
        home_team, away_team, season, week, data.get("vegas"),
    )

    ats_pick = None
    ats_prob = None
    ou_pick = None
    ou_prob = None

    if spread_close is not None:
        home_cover_p = _cover_probability(margin, spread_close)
        if home_cover_p >= 0.5:
            ats_pick = home_team
            ats_prob = home_cover_p
        else:
            ats_pick = away_team
            ats_prob = 1.0 - home_cover_p

    if total_close is not None:
        over_p = _over_probability(total, total_close)
        if over_p >= 0.5:
            ou_pick = "OVER"
            ou_prob = over_p
        else:
            ou_pick = "UNDER"
            ou_prob = 1.0 - over_p

    # Situational ATS overlay — independent of the model's margin/SU; only an
    # against-the-spread lean from validated market biases (DESIGN.md §16).
    from .situational import situational_ats_lean
    sit_pick, sit_reason = situational_ats_lean(home_team, away_team, spread_close)

    return GamePrediction(
        home_team=home_team,
        away_team=away_team,
        season=season,
        week=week,
        predicted_home_score=round(home_score, 1),
        predicted_away_score=round(away_score, 1),
        predicted_margin=round(margin, 1),
        predicted_total=round(total, 1),
        su_pick=su_pick,
        win_prob_home=round(win_prob_home, 3),
        win_prob_away=round(win_prob_away, 3),
        spread_close=spread_close,
        total_close=total_close,
        ats_pick=ats_pick,
        ats_prob=round(ats_prob, 3) if ats_prob is not None else None,
        ou_pick=ou_pick,
        ou_prob=round(ou_prob, 3) if ou_prob is not None else None,
        situational_ats_pick=sit_pick,
        situational_ats_reason=sit_reason,
        home_production=home_prod,
        away_production=away_prod,
    )
