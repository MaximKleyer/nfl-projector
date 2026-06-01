"""Command-line interface for nfl_projector_v1.

Sub-commands:
  predict              Project all games in a given week (print + CSV)
  backtest             Run the walk-forward backtest over seasons
  refresh-depth-charts Re-download depth chart CSVs from nflverse
  status               Sanity-check that warehouse + depth charts are present

Invoke via:  python -m nfl_projector_v1 <command> [options]
"""
from __future__ import annotations
import argparse
import sys
from pathlib import Path

from .config import (
    DEFAULT_WAREHOUSE_PATH,
    DEFAULT_OUTPUT_DIR,
    DEFAULT_DEPTH_CHART_DIR,
    DEFAULT_ROSTER_MODE,
)


# ---------------------------------------------------------------------------
# predict
# ---------------------------------------------------------------------------

def _format_game_line(pred) -> str:
    """Human-readable one-line summary of a GamePrediction.

    Format: "GB @ DET   DET 24.7 - GB 19.6   |  SU: DET (62%)  ATS: DET  O/U: UNDER"
    """
    away, home = pred.away_team, pred.home_team
    matchup = f"{away} @ {home}"
    score = f"{home} {pred.predicted_home_score:.1f} - {away} {pred.predicted_away_score:.1f}"

    su_prob = (
        pred.win_prob_home if pred.su_pick == home else pred.win_prob_away
    )
    su = f"SU: {pred.su_pick} ({su_prob*100:.0f}%)"

    ats = f"ATS: {pred.ats_pick}" if pred.ats_pick else "ATS: —"
    ou = f"O/U: {pred.ou_pick}" if pred.ou_pick else "O/U: —"

    return f"{matchup:14s} {score:24s} | {su:18s} {ats:10s} {ou}"


def cmd_predict(args: argparse.Namespace) -> int:
    """Predict all games in a given (season, week)."""
    from .data.loaders import load_all
    from .game import project_game
    import pandas as pd

    season = args.season
    week = args.week

    print(f"Loading warehouse...")
    data = load_all()

    schedule = data["schedule"]
    games = schedule[
        (schedule["season"] == season) & (schedule["week"] == week)
    ].copy()

    if games.empty:
        print(f"No games found for season {season} week {week}.")
        print("Check that the schedule table covers this week.")
        return 1

    print(f"\nProjecting {len(games)} games for season {season}, week {week}:\n")

    rows = []
    predictions = []
    for _, g in games.iterrows():
        home = g["home_team"]
        away = g["away_team"]
        try:
            pred = project_game(home, away, season, week, data)
        except Exception as e:
            print(f"  [warn] failed on {away} @ {home}: {e}")
            continue
        predictions.append(pred)
        print("  " + _format_game_line(pred))

        rows.append({
            "season": season,
            "week": week,
            "away_team": away,
            "home_team": home,
            "predicted_away_score": pred.predicted_away_score,
            "predicted_home_score": pred.predicted_home_score,
            "predicted_margin": pred.predicted_margin,
            "predicted_total": pred.predicted_total,
            "su_pick": pred.su_pick,
            "win_prob_home": pred.win_prob_home,
            "win_prob_away": pred.win_prob_away,
            "spread_close": pred.spread_close,
            "total_close": pred.total_close,
            "ats_pick": pred.ats_pick,
            "ats_prob": pred.ats_prob,
            "ou_pick": pred.ou_pick,
            "ou_prob": pred.ou_prob,
        })

    # Write CSV
    out_dir = Path(args.out_dir) if args.out_dir else DEFAULT_OUTPUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"predictions_{season}_week{week:02d}.csv"
    pd.DataFrame(rows).to_csv(out_path, index=False)
    print(f"\nWrote {len(rows)} predictions to:\n  {out_path}")

    return 0


# ---------------------------------------------------------------------------
# backtest
# ---------------------------------------------------------------------------

def cmd_backtest(args: argparse.Namespace) -> int:
    """Run the walk-forward backtest."""
    from .backtest import walk_forward_backtest, write_backtest_outputs

    result = walk_forward_backtest(
        seasons=args.seasons,
        min_week=args.min_week,
        max_week=args.max_week,
        verbose=True,
        roster_mode=args.roster_mode,
    )

    if result.residuals.empty:
        print("No games graded.")
        return 1

    print("\n=== Overall Summary ===")
    print(result.summary.to_string(index=False))
    print("\n=== By Season ===")
    print(result.summary_by_season.to_string(index=False))

    suffix = args.suffix if args.suffix else ""
    paths = write_backtest_outputs(result, suffix=suffix)
    print(f"\nWrote: {list(paths.values())}")
    return 0


# ---------------------------------------------------------------------------
# refresh-depth-charts
# ---------------------------------------------------------------------------

def cmd_refresh_depth_charts(args: argparse.Namespace) -> int:
    """Re-download depth chart CSVs from nflverse."""
    from .data.depth_charts import fetch_depth_charts_season

    seasons = args.seasons
    print(f"Refreshing depth charts for seasons: {seasons}")
    for season in seasons:
        try:
            df = fetch_depth_charts_season(season, refresh=True, verbose=True)
            print(f"  {season}: {len(df)} rows")
        except Exception as e:
            print(f"  [error] {season}: {e}")
            print(f"  (If this is a network error, check your connection or "
                  f"nflverse availability.)")
    print("Done.")
    return 0


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------

def cmd_status(args: argparse.Namespace) -> int:
    """Sanity-check that the warehouse and depth charts are present."""
    print("=== nfl_projector_v1 status ===\n")

    # Warehouse
    wh = DEFAULT_WAREHOUSE_PATH
    print(f"Warehouse path: {wh}")
    if wh.exists():
        size_mb = wh.stat().st_size / (1024 * 1024)
        print(f"  exists: YES  ({size_mb:.1f} MB)")
        # Try to read table row counts
        try:
            import duckdb
            con = duckdb.connect(str(wh), read_only=True)
            tables = [t[0] for t in con.execute("SHOW TABLES").fetchall()]
            print(f"  tables: {len(tables)}")
            for t in sorted(tables):
                n = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                print(f"    {t:32s} {n:>8,} rows")
            con.close()
        except Exception as e:
            print(f"  [warn] could not read tables: {e}")
    else:
        print(f"  exists: NO")
        print(f"  -> Build it with: python scripts/build_database.py --seasons 2023 2024 2025")

    # Depth charts
    print(f"\nDepth chart cache: {DEFAULT_DEPTH_CHART_DIR}")
    if DEFAULT_DEPTH_CHART_DIR.exists():
        files = sorted(DEFAULT_DEPTH_CHART_DIR.glob("depth_charts_*.csv"))
        if files:
            for f in files:
                size_kb = f.stat().st_size / 1024
                print(f"  {f.name}  ({size_kb:.0f} KB)")
        else:
            print(f"  (no cached depth charts)")
            print(f"  -> Fetch with: python -m nfl_projector_v1 refresh-depth-charts")
    else:
        print(f"  exists: NO")
        print(f"  -> Fetch with: python -m nfl_projector_v1 refresh-depth-charts")

    # Output dir
    print(f"\nOutput dir: {DEFAULT_OUTPUT_DIR}")
    print(f"  exists: {'YES' if DEFAULT_OUTPUT_DIR.exists() else 'NO (will be created on first write)'}")

    print("\nStatus check complete.")
    return 0


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nfl_projector_v1",
        description="NFL game projection model (v1). Bottom-up player→team→game.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # predict
    p_pred = sub.add_parser("predict", help="Project all games in a week")
    p_pred.add_argument("--season", type=int, required=True, help="Season year, e.g. 2025")
    p_pred.add_argument("--week", type=int, required=True, help="Week number, 1-18")
    p_pred.add_argument("--out-dir", type=str, default=None,
                        help="Where to write the predictions CSV (default: config output dir)")
    p_pred.set_defaults(func=cmd_predict)

    # backtest
    p_bt = sub.add_parser("backtest", help="Run walk-forward backtest")
    p_bt.add_argument("--seasons", type=int, nargs="+", required=True,
                      help="Seasons to test, e.g. --seasons 2024 2025")
    p_bt.add_argument("--min-week", type=int, default=1, help="First week (default 1)")
    p_bt.add_argument("--max-week", type=int, default=18, help="Last week (default 18)")
    p_bt.add_argument("--suffix", type=str, default="", help="Suffix for output CSV filenames")
    p_bt.add_argument("--roster-mode", choices=["depth_chart", "snaps"], default=DEFAULT_ROSTER_MODE,
                      help=f"Roster selection: FPD snap share or legacy nflverse depth chart (default {DEFAULT_ROSTER_MODE})")
    p_bt.set_defaults(func=cmd_backtest)

    # refresh-depth-charts
    p_dc = sub.add_parser("refresh-depth-charts", help="Re-download depth charts from nflverse")
    p_dc.add_argument("--seasons", type=int, nargs="+", default=[2023, 2024, 2025],
                      help="Seasons to refresh (default: 2023 2024 2025)")
    p_dc.set_defaults(func=cmd_refresh_depth_charts)

    # status
    p_st = sub.add_parser("status", help="Check warehouse + depth chart availability")
    p_st.set_defaults(func=cmd_status)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)
