"""Command-line interface for nfl_projector_v1.

Sub-commands:
  predict              Project all games in a given week (print + CSV)
  predict-season       Project a full season: standings + division/playoff odds
  backtest             Run the walk-forward backtest over seasons
  refresh-depth-charts Re-download depth chart CSVs from nflverse
  status               Sanity-check that warehouse + depth charts are present

Invoke via:  python -m nfl_projector_v1 <command> [options]
"""
from __future__ import annotations
import argparse
import os
import sys
from pathlib import Path

from .config import (
    DEFAULT_WAREHOUSE_PATH,
    DEFAULT_OUTPUT_DIR,
    DEFAULT_DEPTH_CHART_DIR,
    DEFAULT_ROSTER_MODE,
    DEFAULT_TD_RATES,
    DEFAULT_CALIBRATE,
    DEFAULT_HOME_FIELD,
    DEFAULT_FG_RATES,
    DEFAULT_ENVIRONMENT,
)


# ---------------------------------------------------------------------------
# Terminal color for the predict table (winner score green, loser red)
# ---------------------------------------------------------------------------
# Only colorize on an interactive terminal (so redirecting to a file stays
# plain). FORCE_COLOR overrides; NO_COLOR disables.
_USE_COLOR = bool(
    (sys.stdout.isatty() or os.environ.get("FORCE_COLOR"))
    and not os.environ.get("NO_COLOR")
)
_GREEN, _RED, _RESET = "\033[92m", "\033[91m", "\033[0m"


def _enable_ansi_colors() -> None:
    """Enable ANSI escape processing on legacy Windows consoles (no-op elsewhere)."""
    if os.name == "nt":
        try:
            import ctypes
            k = ctypes.windll.kernel32
            # 0x0007 = PROCESSED_OUTPUT | WRAP_AT_EOL | VIRTUAL_TERMINAL_PROCESSING
            k.SetConsoleMode(k.GetStdHandle(-11), 0x0007)
        except Exception:
            pass


def _c(text: str, code: str) -> str:
    """Wrap text in an ANSI color (no-op when color is disabled)."""
    return f"{code}{text}{_RESET}" if _USE_COLOR else text


# ---------------------------------------------------------------------------
# predict
# ---------------------------------------------------------------------------

def _format_game_line(pred) -> str:
    """Human-readable one-line summary of a GamePrediction.

    Format: "GB @ DET   DET 24.7 - GB 19.6   |  SU: DET (62%)  ATS: DET  O/U: UNDER"
    """
    away, home = pred.away_team, pred.home_team
    matchup = f"{away} @ {home}"

    # Color the scores by projected winner: winner green, loser red (scores only).
    hs, as_ = pred.predicted_home_score, pred.predicted_away_score
    home_s, away_s = f"{hs:.1f}", f"{as_:.1f}"
    if hs > as_:
        home_s, away_s = _c(home_s, _GREEN), _c(away_s, _RED)
    elif as_ > hs:
        home_s, away_s = _c(home_s, _RED), _c(away_s, _GREEN)
    # Pad to width 24 on the VISIBLE length (ANSI codes are zero-width).
    plain = f"{home} {hs:.1f} - {away} {as_:.1f}"
    score = f"{home} {home_s} - {away} {away_s}" + " " * max(0, 24 - len(plain))

    su_prob = (
        pred.win_prob_home if pred.su_pick == home else pred.win_prob_away
    )
    su = f"SU: {pred.su_pick} ({su_prob*100:.0f}%)"

    ats = f"ATS: {pred.ats_pick}" if pred.ats_pick else "ATS: —"
    ou = f"O/U: {pred.ou_pick}" if pred.ou_pick else "O/U: —"

    return f"{matchup:14s} {score} | {su:18s} {ats:10s} {ou}"


# ---------------------------------------------------------------------------
# Per-player projection view (predict --players)
# ---------------------------------------------------------------------------

def _player_rows(pred) -> list[dict]:
    """Flatten a GamePrediction's per-player projections (both teams) into rows
    for a CSV. One row per projected player (QB / each RB / each WR-TE)."""
    out = []
    game = f"{pred.away_team}@{pred.home_team}"
    for prod, ha in ((pred.away_production, "away"), (pred.home_production, "home")):
        if prod is None:
            continue
        base = dict(season=pred.season, week=pred.week, game=game,
                    team=prod.team, opponent=prod.opponent, home_away=ha)
        qb = prod.qb_projection
        if qb is not None:
            out.append({**base, "position": "QB", "depth": 1, "player": qb.name,
                        "pass_att": round(qb.pass_attempts, 1),
                        "completions": round(qb.completions, 1),
                        "pass_yds": round(qb.pass_yards, 1),
                        "ypa": round(qb.ypa, 2),
                        "interceptions": round(qb.interceptions, 2),
                        "sacks": round(qb.sack_count, 1),
                        "scramble_yds": round(qb.scramble_yards, 1)})
        for rb in prod.rb_projections:
            out.append({**base, "position": "RB",
                        "depth": getattr(rb, "depth_order", None), "player": rb.name,
                        "carries": round(rb.carries, 1),
                        "ypc": round(rb.ypc, 2),
                        "rush_yds": round(rb.rush_yards, 1),
                        "target_share_pct": round(rb.target_share, 1),
                        "ypt": round(rb.ypt, 2),
                        "receptions": round(rb.receptions, 1),
                        "rec_yds": round(rb.receiving_yards, 1)})
        for r in prod.receiver_projections:
            out.append({**base, "position": r.position, "player": r.name,
                        "target_share_pct": round(r.target_share, 1),
                        "ypt": round(r.ypt, 2),
                        "catch_rate": round(r.catch_rate, 1),
                        "receptions": round(r.receptions, 1),
                        "rec_yds": round(r.receiving_yards, 1)})
    return out


def _print_player_block(pred) -> None:
    """Print a readable per-player breakdown for one game (both teams)."""
    print(f"\n  {pred.away_team} {pred.predicted_away_score:.1f} @ "
          f"{pred.home_team} {pred.predicted_home_score:.1f}   "
          f"(total {pred.predicted_total:.1f})")
    for prod in (pred.away_production, pred.home_production):
        if prod is None:
            continue
        print(f"    {prod.team} vs {prod.opponent}  "
              f"[pass {prod.pass_yards:.0f} / rush {prod.rush_yards:.0f} yds, "
              f"~{prod.total_tds_implied:.1f} TD, {prod.field_goals:.1f} FG]")
        qb = prod.qb_projection
        if qb is not None:
            print(f"      QB  {qb.name:20s} {qb.pass_attempts:5.1f} att "
                  f"{qb.completions:5.1f} cmp {qb.pass_yards:6.0f} yds {qb.ypa:5.1f} ypa "
                  f"{qb.interceptions:4.1f} INT {qb.scramble_yards:4.0f} scrm")
        for rb in prod.rb_projections:
            print(f"      RB  {rb.name:20s} {rb.carries:5.1f} car "
                  f"{rb.rush_yards:6.0f} yds {rb.ypc:5.1f} ypc | "
                  f"{rb.target_share:4.1f}% tgt {rb.receiving_yards:5.0f} rec "
                  f"{rb.receptions:4.1f} rec")
        for r in prod.receiver_projections:
            print(f"      {r.position:<2s}  {r.name:20s} {r.target_share:4.1f}% tgt "
                  f"{r.receiving_yards:6.0f} rec yds {r.receptions:5.1f} rec "
                  f"{r.ypt:5.1f} ypt")


def cmd_predict(args: argparse.Namespace) -> int:
    """Predict all games in a given (season, week)."""
    from .data.loaders import load_all
    from .game import project_game
    import pandas as pd

    season = args.season
    week = args.week

    _enable_ansi_colors()
    print(f"Loading warehouse...")
    data = load_all()
    data["home_field"] = args.home_field

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
        if args.players:
            _print_player_block(pred)

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

    # Per-player projections CSV (one row per projected player), when --players.
    if args.players:
        prows = [r for pred in predictions for r in _player_rows(pred)]
        if prows:
            cols = ["season", "week", "game", "team", "opponent", "home_away",
                    "position", "depth", "player", "pass_att", "completions",
                    "pass_yds", "ypa", "interceptions", "sacks", "scramble_yds",
                    "carries", "ypc", "rush_yds", "target_share_pct", "ypt",
                    "catch_rate", "receptions", "rec_yds"]
            pdf = pd.DataFrame(prows).reindex(columns=cols)
            p_path = out_dir / f"player_projections_{season}_week{week:02d}.csv"
            pdf.to_csv(p_path, index=False)
            print(f"Wrote {len(prows)} player projections to:\n  {p_path}")

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
        td_rates=args.td_rates,
        calibrate=args.calibrate,
        home_field=args.home_field,
        fg_rates=args.fg_rates,
        environment=args.environment,
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
# predict-season
# ---------------------------------------------------------------------------

def cmd_predict_season(args: argparse.Namespace) -> int:
    """Project a full season: per-team expected wins + division/playoff odds."""
    from .season import project_season

    result = project_season(
        args.season, min_week=args.min_week, max_week=args.max_week,
        n_sims=args.sims, verbose=True, home_field=args.home_field,
    )
    st = result.standings

    print(f"\n=== {args.season} projected standings "
          f"(expected W-L; {args.sims:,} sims for odds) ===")
    for div, grp in st.groupby("division", sort=False):
        print(f"\n{div}")
        for r in grp.itertuples():
            print(f"  {r.team:4s} {r.exp_wins:4.1f}-{r.exp_losses:<4.1f}  "
                  f"div {r.div_title_pct:5.1f}%   playoff {r.playoff_pct:5.1f}%   "
                  f"(SU {r.su_wins}-{r.su_losses})")

    out_dir = Path(args.out_dir) if args.out_dir else DEFAULT_OUTPUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    st_path = out_dir / f"season_standings_{args.season}.csv"
    st.to_csv(st_path, index=False)
    print(f"\nWrote standings: {st_path}")
    if not args.no_games_csv:
        g_path = out_dir / f"season_games_{args.season}.csv"
        result.games.to_csv(g_path, index=False)
        print(f"Wrote per-game projections: {g_path}")
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
    p_pred.add_argument("--home-field", choices=["none", "league", "team"], default=DEFAULT_HOME_FIELD,
                        help=f"Home-field advantage: off, flat league, or per-team (default {DEFAULT_HOME_FIELD})")
    p_pred.add_argument("--players", action="store_true",
                        help="Also print each game's per-player projections and write a player_projections CSV")
    p_pred.set_defaults(func=cmd_predict)

    # predict-season
    p_ps = sub.add_parser("predict-season",
                          help="Project a full season: standings + division/playoff odds")
    p_ps.add_argument("--season", type=int, required=True, help="Season year, e.g. 2026")
    p_ps.add_argument("--min-week", type=int, default=1, help="First week (default 1)")
    p_ps.add_argument("--max-week", type=int, default=18, help="Last week (default 18)")
    p_ps.add_argument("--sims", type=int, default=10000,
                      help="Monte Carlo sims for division/playoff odds (default 10000)")
    p_ps.add_argument("--out-dir", type=str, default=None,
                      help="Where to write the season CSVs (default: config output dir)")
    p_ps.add_argument("--no-games-csv", action="store_true",
                      help="Skip writing the per-game projections CSV")
    p_ps.add_argument("--home-field", choices=["none", "league", "team"], default=DEFAULT_HOME_FIELD,
                      help=f"Home-field advantage: off, flat league, or per-team (default {DEFAULT_HOME_FIELD})")
    p_ps.set_defaults(func=cmd_predict_season)

    # backtest
    p_bt = sub.add_parser("backtest", help="Run walk-forward backtest")
    p_bt.add_argument("--seasons", type=int, nargs="+", required=True,
                      help="Seasons to test, e.g. --seasons 2024 2025")
    p_bt.add_argument("--min-week", type=int, default=1, help="First week (default 1)")
    p_bt.add_argument("--max-week", type=int, default=18, help="Last week (default 18)")
    p_bt.add_argument("--suffix", type=str, default="", help="Suffix for output CSV filenames")
    p_bt.add_argument("--roster-mode", choices=["depth_chart", "snaps"], default=DEFAULT_ROSTER_MODE,
                      help=f"Roster selection: FPD snap share or legacy nflverse depth chart (default {DEFAULT_ROSTER_MODE})")
    p_bt.add_argument("--td-rates", choices=["league", "team"], default=DEFAULT_TD_RATES,
                      help=f"TD-per-yard conversion: flat league rate or per-team shrunk rate (default {DEFAULT_TD_RATES})")
    p_bt.add_argument("--calibrate", dest="calibrate", action="store_true", default=DEFAULT_CALIBRATE,
                      help=f"Apply the global total-points calibration (default {DEFAULT_CALIBRATE}; fixes total bias, leaves margin/SU/ATS unchanged)")
    p_bt.add_argument("--no-calibrate", dest="calibrate", action="store_false",
                      help="Disable the global total-points calibration (for A/B comparison)")
    p_bt.add_argument("--home-field", choices=["none", "league", "team"], default=DEFAULT_HOME_FIELD,
                      help=f"Home-field advantage: off, flat league (~2pt), or per-team shrunk (default {DEFAULT_HOME_FIELD})")
    p_bt.add_argument("--fg-rates", choices=["league", "team"], default=DEFAULT_FG_RATES,
                      help=f"FG conversion: flat league rate or per-team shrunk FGs/game (default {DEFAULT_FG_RATES})")
    p_bt.add_argument("--environment", choices=["none", "dome"], default=DEFAULT_ENVIRONMENT,
                      help=f"Environment total adjustment: off, or dome/outdoor nudge (default {DEFAULT_ENVIRONMENT})")
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
