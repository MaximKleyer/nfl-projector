"""Data loaders: pull DataFrames from the warehouse.

These functions do NOTHING except SQL → DataFrame. No filtering, no
transformation, no business logic. That's the next layer's job
(projections/, game.py, etc.).

Keeping this pure makes the rest of the codebase trivially testable —
you can swap the warehouse for any DataFrame source and the projection
logic works unchanged.
"""
from __future__ import annotations
from pathlib import Path
from typing import Optional
import duckdb
import pandas as pd
import yaml

from ..config import DEFAULT_WAREHOUSE_PATH, QB_STARTERS_YAML


def open_warehouse(path: str | Path | None = None) -> duckdb.DuckDBPyConnection:
    """Open the DuckDB warehouse read-only. Caller is responsible for closing."""
    path = Path(path) if path else DEFAULT_WAREHOUSE_PATH
    if not path.exists():
        raise FileNotFoundError(f"Warehouse not found at {path}")
    return duckdb.connect(str(path), read_only=True)


# ---------------------------------------------------------------------------
# Schedule, Vegas, injuries (nflverse data)
# ---------------------------------------------------------------------------

def load_schedule(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """All scheduled games with scores where available.

    Key columns: season, week, home_team, away_team, home_score, away_score,
                 kickoff timestamp, weather conditions.
    """
    return con.execute("SELECT * FROM schedule").df()


def load_vegas(con: duckdb.DuckDBPyConnection) -> Optional[pd.DataFrame]:
    """Vegas closing lines. None if table doesn't exist."""
    try:
        return con.execute("SELECT * FROM vegas").df()
    except Exception:
        return None


def load_injuries(con: duckdb.DuckDBPyConnection) -> Optional[pd.DataFrame]:
    """Weekly injury reports. None if table doesn't exist."""
    try:
        return con.execute("SELECT * FROM injuries").df()
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Player game logs (FPD data, one row per player-game)
# ---------------------------------------------------------------------------

def load_qb_history(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """All QB game logs from FPD advanced_passing_player.

    Useful columns: player_name, team_norm, season, week_num,
                    pass_att, completions, cmp_pct, pass_yds, ypa,
                    pass_td, ints, sacks_taken, scrambles, total_fp.
    """
    return con.execute("SELECT * FROM advanced_passing_player").df()


def load_rb_history(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """All RB game logs from FPD advanced_rushing_player.

    Useful columns: player_name, team_norm, position, season, week_num,
                    rush_att, rush_yds, ypc, rush_td, fumbles, total_fp.
    Note: this table includes ALL rushers (QBs, WRs with end-arounds, etc.).
    Filter to position='RB' in downstream code if needed.
    """
    return con.execute("SELECT * FROM advanced_rushing_player").df()


def load_receiver_history(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """All receiver game logs from FPD advanced_receiving_player.

    Includes WR, TE, and RB receiving stats.

    Useful columns: player_name, team_norm, position, season, week_num,
                    routes_run, targets, target_share, receptions, catch_rate,
                    rec_yds, ypt, ypr, yac, rec_td, total_fp.
    """
    return con.execute("SELECT * FROM advanced_receiving_player").df()


# ---------------------------------------------------------------------------
# Defense game logs
# ---------------------------------------------------------------------------

def load_pass_defense(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Defensive stats vs the pass, per team-week."""
    return con.execute("SELECT * FROM advanced_passing_def").df()


def load_rush_defense(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Defensive stats vs the run, per team-week."""
    return con.execute("SELECT * FROM advanced_rushing_def").df()


def load_recv_defense(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Defensive stats vs receivers, per team-week.

    More granular than pass_defense (which is team-level passing allowed).
    This breaks it down vs receiver type / target depth.
    """
    return con.execute("SELECT * FROM advanced_receiving_def").df()


# ---------------------------------------------------------------------------
# Snap counts / snap share (FPD, one row per player-game)
# ---------------------------------------------------------------------------

def load_snaps(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Per-week offensive snap counts/shares (WR/TE/RB/FB only).

    Key columns: player_name, position, season, week_num, player_key, team_norm,
                 total_snap_pct (the headline snap share), plus run/pass and
                 red-zone tier splits (rush_/pass_/gl_/i10_/rz_). One row per
                 player-game (counts do NOT accumulate across weeks).
    """
    return con.execute("SELECT * FROM snaps").df()


# ---------------------------------------------------------------------------
# Kicking (nflverse, one row per team-game: field goals made/attempted)
# ---------------------------------------------------------------------------

def load_kicking(con: duckdb.DuckDBPyConnection) -> Optional[pd.DataFrame]:
    """Per-team-game field goals from nflverse (scripts/fetch_kicking.py).

    Columns: season, week, team (normalized), fg_made, fg_att. Used by
    team.py:_team_fg_rate for per-team FG conversion. None if not yet ingested.
    """
    try:
        return con.execute("SELECT * FROM kicking").df()
    except Exception:
        return None


# ---------------------------------------------------------------------------
# QB starter overrides (manual YAML, not from the warehouse)
# ---------------------------------------------------------------------------

def load_qb_starters(path: str | Path | None = None) -> dict:
    """Load the manual QB-starter override map (DESIGN.md §12.2).

    Returns a nested dict {season: {week: {team: qb_name}}}, or {} if the file
    is missing or empty. Entries here win over the depth chart in resolve_qb().
    """
    path = Path(path) if path else QB_STARTERS_YAML
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    return data or {}


# ---------------------------------------------------------------------------
# Convenience: one-shot loader for everything
# ---------------------------------------------------------------------------

def load_all(warehouse_path: str | Path | None = None) -> dict:
    """Load everything we need into one dict. Useful for backtests.

    Returns a dict with keys:
        schedule, vegas, injuries,
        qb_history, rb_history, recv_history,
        pass_defense, rush_defense, recv_defense,
        snaps, kicking, qb_starters
    """
    con = open_warehouse(warehouse_path)
    try:
        return {
            "schedule": load_schedule(con),
            "vegas": load_vegas(con),
            "injuries": load_injuries(con),
            "qb_history": load_qb_history(con),
            "rb_history": load_rb_history(con),
            "recv_history": load_receiver_history(con),
            "pass_defense": load_pass_defense(con),
            "rush_defense": load_rush_defense(con),
            "recv_defense": load_recv_defense(con),
            "snaps": load_snaps(con),
            "kicking": load_kicking(con),
            "qb_starters": load_qb_starters(),
        }
    finally:
        con.close()
