"""Build a DuckDB warehouse from raw FPD CSVs + external (schedule, vegas).

Expected directory layout:

    data/raw/
      2023/
        week_01/
          advanced_passing_player.csv
          advanced_passing_def.csv
          advanced_rushing_player.csv
          advanced_receiving_player.csv
          snaps.csv
          routes_run.csv
          run_pass_report.csv
          man_vs_zone.csv
          passing_depth.csv
          fpa_qb.csv  fpa_rb.csv  fpa_wr.csv  fpa_te.csv
        week_02/
          ...
        season/
          coverage_matrix_off.csv
          coverage_matrix_def.csv
          line_matchups.csv
      2024/ ...
      2025/ ...
      external/
        schedule.csv
        vegas.csv

Missing files are skipped with a warning, not an error — the model degrades
gracefully when a particular report wasn't pulled for a given week.
"""

from __future__ import annotations
from pathlib import Path
import warnings
import duckdb
import pandas as pd

from .schemas import REPORTS
from .readers import read_report


def _discover_weeks(season_dir: Path) -> list[str]:
    """List the week labels present in a season folder."""
    if not season_dir.exists():
        return []
    return sorted(
        d.name for d in season_dir.iterdir()
        if d.is_dir() and d.name != "season"
    )


def _ingest_season(season: int, raw_dir: Path) -> dict[str, pd.DataFrame]:
    """Read every available report for a single season and concatenate by report.

    Returns a dict: report_name → DataFrame stacking all weeks for that report.
    """
    season_dir = raw_dir / str(season)
    accumulators: dict[str, list[pd.DataFrame]] = {name: [] for name in REPORTS}

    # --- Weekly reports ---
    for week_label in _discover_weeks(season_dir):
        week_dir = season_dir / week_label
        for report_name, schema in REPORTS.items():
            if not schema.weekly:
                continue
            csv_path = week_dir / schema.filename_pattern
            if not csv_path.exists():
                continue
            try:
                df = read_report(csv_path, report_name, season, week_label)
                accumulators[report_name].append(df)
            except Exception as e:
                warnings.warn(f"Failed to read {csv_path}: {e}")

    # --- Season-aggregate reports ---
    season_subdir = season_dir / "season"
    if season_subdir.exists():
        for report_name, schema in REPORTS.items():
            if schema.weekly:
                continue
            csv_path = season_subdir / schema.filename_pattern
            if not csv_path.exists():
                continue
            try:
                df = read_report(csv_path, report_name, season, "season")
                accumulators[report_name].append(df)
            except Exception as e:
                warnings.warn(f"Failed to read {csv_path}: {e}")

    # Concat each report's frames
    out = {}
    for report_name, frames in accumulators.items():
        if frames:
            out[report_name] = pd.concat(frames, ignore_index=True)
    return out


def _ingest_external(raw_dir: Path) -> dict[str, pd.DataFrame]:
    """Read schedule.csv, vegas.csv, injuries.csv, kicking.csv from data/raw/external/."""
    out = {}
    ext_dir = raw_dir / "external"
    for report_name in ("schedule", "vegas", "injuries", "kicking"):
        path = ext_dir / REPORTS[report_name].filename_pattern
        if not path.exists():
            warnings.warn(f"External file missing: {path}")
            continue
        # External CSVs aren't season-or-weekly-tagged the same way
        df = pd.read_csv(path, encoding="utf-8-sig")
        # Normalize team codes
        from ..utils import normalize_team
        if "home_team" in df.columns:
            df["home_team"] = df["home_team"].apply(
                lambda x: normalize_team(x) if pd.notna(x) and x else ""
            )
            df["away_team"] = df["away_team"].apply(
                lambda x: normalize_team(x) if pd.notna(x) and x else ""
            )
        if "team" in df.columns:
            df["team"] = df["team"].apply(
                lambda x: normalize_team(x) if pd.notna(x) and x else ""
            )
        out[report_name] = df
    return out


def build_warehouse(
    raw_dir: str | Path,
    db_path: str | Path,
    seasons: list[int],
) -> None:
    """Build the DuckDB warehouse.

    Parameters
    ----------
    raw_dir : root directory containing data/raw/<season>/<week>/...
    db_path : output DuckDB file (will be overwritten)
    seasons : seasons to ingest
    """
    raw_dir = Path(raw_dir)
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    # Collect all data first, then write atomically
    by_report: dict[str, list[pd.DataFrame]] = {name: [] for name in REPORTS}

    for season in seasons:
        print(f"  Ingesting season {season}...")
        season_data = _ingest_season(season, raw_dir)
        for name, df in season_data.items():
            by_report[name].append(df)

    print("  Ingesting external data...")
    external = _ingest_external(raw_dir)
    for name, df in external.items():
        by_report[name] = [df]  # external isn't seasonal in the same way

    # Write to DuckDB
    if db_path.exists():
        db_path.unlink()
    con = duckdb.connect(str(db_path))
    try:
        for name, frames in by_report.items():
            if not frames:
                print(f"  [skip] {name} (no files found)")
                continue
            df = pd.concat(frames, ignore_index=True)
            con.register("__tmp", df)
            con.execute(f'CREATE TABLE "{name}" AS SELECT * FROM __tmp')
            con.unregister("__tmp")
            print(f"  [ok]  {name:40s} rows={len(df):>7d}")
        # Useful indices via VIEWs (DuckDB doesn't need traditional indices for analytical scans)
        _create_views(con)
    finally:
        con.close()


def _create_views(con: duckdb.DuckDBPyConnection) -> None:
    """Create convenience views on top of the raw tables."""
    # Players table — distinct (player_key, name, team) seen across all sources
    sources = [
        ("advanced_passing_player", "QB"),
        ("advanced_receiving_player", None),
        ("advanced_rushing_player", None),
        ("snaps", None),
    ]
    selects = []
    for tbl, _ in sources:
        try:
            con.execute(f'SELECT 1 FROM "{tbl}" LIMIT 0')
            selects.append(
                f'SELECT DISTINCT player_key, player_name, team_norm AS team, '
                f'position FROM "{tbl}" WHERE player_key IS NOT NULL AND player_key <> \'\''
            )
        except duckdb.CatalogException:
            continue
    if selects:
        union = "\nUNION\n".join(selects)
        con.execute(f"CREATE OR REPLACE VIEW players AS {union}")

    # Player-game canonical view: combines passing/receiving/rushing weekly rows
    # for downstream feature joins
    try:
        con.execute("""
        CREATE OR REPLACE VIEW player_games AS
        SELECT season, week_num, week_label, week_type, player_key, player_name,
               team_norm AS team, position, total_fp AS fp, fp_per_g, games_played
        FROM advanced_receiving_player
        UNION ALL
        SELECT season, week_num, week_label, week_type, player_key, player_name,
               team_norm AS team, position, total_fp AS fp, fp_per_g, games_played
        FROM advanced_rushing_player
        UNION ALL
        SELECT season, week_num, week_label, week_type, player_key, player_name,
               team_norm AS team, position, total_fp AS fp, fp_per_g, games_played
        FROM advanced_passing_player
        """)
    except duckdb.CatalogException:
        pass


def open_warehouse(db_path: str | Path) -> duckdb.DuckDBPyConnection:
    """Open the warehouse for read access. Use a context manager:

        with open_warehouse('warehouse.duckdb') as con:
            df = con.execute("SELECT ...").df()
    """
    return duckdb.connect(str(db_path), read_only=True)
