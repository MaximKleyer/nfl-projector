"""Walk-forward backtest: project every completed game, grade vs actuals.

For each game in the test window:
  1. Call project_game(home, away, season, week, data)
     (the projection modules internally filter history to strictly before
     this game — walk-forward correctness is enforced there)
  2. Look up the actual scores from the schedule
  3. Compare predicted vs actual:
     - margin error, total error
     - SU correct (did we pick the right winner)
     - ATS correct (did our pick cover, vs Vegas spread)
     - O/U correct (did we get the right side of the total)
  4. Aggregate into summary tables

This is the harness. The projection logic itself lives in game.py and
the projections/ modules. This module just iterates and grades.
"""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
import numpy as np
import pandas as pd

from .config import (
    DEFAULT_OUTPUT_DIR, DEFAULT_ROSTER_MODE, DEFAULT_TD_RATES, DEFAULT_FG_RATES,
    POINTS_CALIBRATION_PER_TEAM, DEFAULT_CALIBRATE, DEFAULT_HOME_FIELD,
    DEFAULT_ENVIRONMENT,
)
from .data.loaders import load_all
from .game import project_game, GamePrediction


@dataclass
class BacktestResult:
    """Result of a walk-forward backtest run."""
    residuals: pd.DataFrame      # one row per game with prediction + actual + grades
    summary: pd.DataFrame        # aggregate metrics for the whole run
    summary_by_season: pd.DataFrame   # breakdown by season


# ---------------------------------------------------------------------------
# Grading helpers
# ---------------------------------------------------------------------------

def _grade_prediction(
    pred: GamePrediction,
    actual_home_score: float,
    actual_away_score: float,
) -> dict:
    """Compare one GamePrediction to one game's actual scores.

    Returns a dict suitable for one row of the residuals DataFrame.
    """
    actual_margin = actual_home_score - actual_away_score
    actual_total = actual_home_score + actual_away_score
    actual_winner = pred.home_team if actual_margin > 0 else pred.away_team

    margin_error = actual_margin - pred.predicted_margin
    total_error = actual_total - pred.predicted_total

    # SU: did we pick the right winner? (Ties = neither side credit/blame)
    su_correct = None
    if actual_margin != 0:
        su_correct = int(pred.su_pick == actual_winner)

    # ATS: did our cover pick win, with push detection
    # spread_close convention: negative = home favored. Home covers when
    # actual_margin > -spread_close (i.e. when actual_margin + spread_close > 0)
    ats_correct = None
    ats_push = 0
    if pred.spread_close is not None and pred.ats_pick is not None:
        adjusted_margin = actual_margin + pred.spread_close
        if adjusted_margin == 0:
            ats_push = 1
        else:
            home_covered = adjusted_margin > 0
            ats_correct = int(
                (pred.ats_pick == pred.home_team and home_covered)
                or (pred.ats_pick == pred.away_team and not home_covered)
            )

    # Situational ATS overlay: grade the lean the same way as ATS (independent
    # of the model's pick). None when no signal fired or no line.
    sit_correct = None
    sit_push = 0
    if pred.spread_close is not None and pred.situational_ats_pick is not None:
        adjusted_margin = actual_margin + pred.spread_close
        if adjusted_margin == 0:
            sit_push = 1
        else:
            home_covered = adjusted_margin > 0
            sit_correct = int(
                (pred.situational_ats_pick == pred.home_team and home_covered)
                or (pred.situational_ats_pick == pred.away_team and not home_covered)
            )

    # O/U: did we get the right side
    ou_correct = None
    ou_push = 0
    if pred.total_close is not None and pred.ou_pick is not None:
        diff = actual_total - pred.total_close
        if diff == 0:
            ou_push = 1
        else:
            went_over = diff > 0
            ou_correct = int(
                (pred.ou_pick == "OVER" and went_over)
                or (pred.ou_pick == "UNDER" and not went_over)
            )

    return {
        "season": pred.season,
        "week": pred.week,
        "home_team": pred.home_team,
        "away_team": pred.away_team,
        "predicted_home_score": pred.predicted_home_score,
        "predicted_away_score": pred.predicted_away_score,
        "predicted_margin": pred.predicted_margin,
        "predicted_total": pred.predicted_total,
        "actual_home_score": actual_home_score,
        "actual_away_score": actual_away_score,
        "actual_margin": actual_margin,
        "actual_total": actual_total,
        "margin_error": margin_error,
        "total_error": total_error,
        "su_pick": pred.su_pick,
        "actual_winner": actual_winner,
        "su_correct": su_correct,
        "spread_close": pred.spread_close,
        "total_close": pred.total_close,
        "ats_pick": pred.ats_pick,
        "ats_correct": ats_correct,
        "ats_push": ats_push,
        "situational_ats_pick": pred.situational_ats_pick,
        "situational_ats_reason": pred.situational_ats_reason,
        "situational_ats_correct": sit_correct,
        "situational_ats_push": sit_push,
        "ou_pick": pred.ou_pick,
        "ou_correct": ou_correct,
        "ou_push": ou_push,
    }


# ---------------------------------------------------------------------------
# Summary aggregation
# ---------------------------------------------------------------------------

def _summarize(residuals: pd.DataFrame, label: str = "all") -> dict:
    """One row of aggregate metrics from a residuals DataFrame."""
    n_games = len(residuals)
    if n_games == 0:
        return {"label": label, "n_games": 0}

    margin_mae = float(residuals["margin_error"].abs().mean())
    margin_rmse = float(np.sqrt((residuals["margin_error"] ** 2).mean()))
    total_mae = float(residuals["total_error"].abs().mean())
    total_rmse = float(np.sqrt((residuals["total_error"] ** 2).mean()))

    # SU accuracy (exclude ties)
    su_played = residuals[residuals["su_correct"].notna()]
    su_accuracy = float(su_played["su_correct"].mean()) if len(su_played) > 0 else None

    # ATS accuracy (exclude pushes and missing lines)
    ats_played = residuals[
        residuals["ats_correct"].notna() & (residuals["ats_push"] == 0)
    ]
    ats_accuracy = float(ats_played["ats_correct"].mean()) if len(ats_played) > 0 else None
    ats_n = len(ats_played)

    # O/U accuracy
    ou_played = residuals[
        residuals["ou_correct"].notna() & (residuals["ou_push"] == 0)
    ]
    ou_accuracy = float(ou_played["ou_correct"].mean()) if len(ou_played) > 0 else None
    ou_n = len(ou_played)

    # Situational ATS overlay accuracy (only the games where a lean fired)
    sit_acc = None
    sit_n = 0
    if "situational_ats_correct" in residuals.columns:
        sit_played = residuals[
            residuals["situational_ats_correct"].notna()
            & (residuals.get("situational_ats_push", 0) == 0)
        ]
        sit_n = len(sit_played)
        sit_acc = float(sit_played["situational_ats_correct"].mean()) if sit_n > 0 else None

    return {
        "label": label,
        "n_games": n_games,
        "margin_mae": round(margin_mae, 3),
        "margin_rmse": round(margin_rmse, 3),
        "total_mae": round(total_mae, 3),
        "total_rmse": round(total_rmse, 3),
        "su_n": len(su_played),
        "su_accuracy": round(su_accuracy, 4) if su_accuracy is not None else None,
        "ats_n": ats_n,
        "ats_accuracy": round(ats_accuracy, 4) if ats_accuracy is not None else None,
        "ou_n": ou_n,
        "ou_accuracy": round(ou_accuracy, 4) if ou_accuracy is not None else None,
        "sit_ats_n": sit_n,
        "sit_ats_accuracy": round(sit_acc, 4) if sit_acc is not None else None,
    }


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def walk_forward_backtest(
    seasons: list[int],
    min_week: int = 1,
    max_week: int = 18,
    data: Optional[dict] = None,
    warehouse_path: Optional[Path] = None,
    verbose: bool = True,
    roster_mode: str = DEFAULT_ROSTER_MODE,
    td_rates: str = DEFAULT_TD_RATES,
    calibrate: bool = DEFAULT_CALIBRATE,
    home_field: str = DEFAULT_HOME_FIELD,
    fg_rates: str = DEFAULT_FG_RATES,
    environment: str = DEFAULT_ENVIRONMENT,
) -> BacktestResult:
    """Run a walk-forward backtest over the specified seasons and week range.

    Parameters
    ----------
    seasons : list of seasons to test (e.g. [2023, 2024, 2025])
    min_week, max_week : inclusive week range to evaluate
    data : optional pre-loaded data dict (from load_all). If None, loads it.
    warehouse_path : passed to load_all() if data not provided
    verbose : print progress

    Returns
    -------
    BacktestResult with residuals + summary tables.
    """
    if data is None:
        if verbose:
            print(f"Loading warehouse...")
        data = load_all(warehouse_path)
    data["roster_mode"] = roster_mode
    data["td_rates"] = td_rates
    data["points_calibration"] = POINTS_CALIBRATION_PER_TEAM if calibrate else 0.0
    data["home_field"] = home_field
    data["fg_rates"] = fg_rates
    data["environment"] = environment
    data.pop("_team_hfa_cache", None)   # recompute per-season HFA for this run's mode

    schedule = data["schedule"]
    # Filter to test seasons + week range, must have actual scores
    games_to_test = schedule[
        (schedule["season"].isin(seasons))
        & (schedule["week"] >= min_week)
        & (schedule["week"] <= max_week)
        & schedule["home_score"].notna()
        & schedule["away_score"].notna()
    ].copy()

    if games_to_test.empty:
        if verbose:
            print("No games found in the specified window.")
        return BacktestResult(
            residuals=pd.DataFrame(),
            summary=pd.DataFrame(),
            summary_by_season=pd.DataFrame(),
        )

    if verbose:
        print(f"Backtesting {len(games_to_test)} games "
              f"({seasons} weeks {min_week}-{max_week})...")

    rows = []
    last_progress_week = None
    for _, game in games_to_test.iterrows():
        season = int(game["season"])
        week = int(game["week"])
        home = game["home_team"]
        away = game["away_team"]

        # Print progress one line per (season, week)
        if verbose and (season, week) != last_progress_week:
            print(f"  Projecting season={season} week={week}...")
            last_progress_week = (season, week)

        try:
            pred = project_game(home, away, season, week, data)
        except Exception as e:
            if verbose:
                print(f"    [warn] failed on {away} @ {home}: {e}")
            continue

        row = _grade_prediction(
            pred=pred,
            actual_home_score=float(game["home_score"]),
            actual_away_score=float(game["away_score"]),
        )
        rows.append(row)

    residuals = pd.DataFrame(rows)

    # Aggregate summaries
    overall = pd.DataFrame([_summarize(residuals, label="all")])
    by_season = pd.DataFrame([
        _summarize(residuals[residuals["season"] == s], label=f"{s}")
        for s in sorted(residuals["season"].unique())
    ])

    return BacktestResult(
        residuals=residuals,
        summary=overall,
        summary_by_season=by_season,
    )


def write_backtest_outputs(
    result: BacktestResult,
    out_dir: Optional[Path] = None,
    suffix: str = "",
) -> dict[str, Path]:
    """Write residuals + summaries to CSV. Returns dict of paths written."""
    out_dir = Path(out_dir) if out_dir else DEFAULT_OUTPUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    paths = {}
    if not result.residuals.empty:
        p = out_dir / f"backtest_residuals{suffix}.csv"
        result.residuals.to_csv(p, index=False)
        paths["residuals"] = p
    if not result.summary.empty:
        p = out_dir / f"backtest_summary{suffix}.csv"
        result.summary.to_csv(p, index=False)
        paths["summary"] = p
    if not result.summary_by_season.empty:
        p = out_dir / f"backtest_summary_by_season{suffix}.csv"
        result.summary_by_season.to_csv(p, index=False)
        paths["summary_by_season"] = p

    return paths
