"""Fetch nflverse weekly kicking stats and land a per-team-game `kicking` table.

nflverse publishes a combined weekly player-stats CSV per season (which includes
kicking columns) at:
    https://github.com/nflverse/nflverse-data/releases/download/
        stats_player/stats_player_week_{season}.csv
(The older player_stats_kicking_{season}.csv release stops at 2024, so we use the
current `stats_player` release for a consistent schema across 2021-2025.)

We aggregate field goals made/attempted to one row per (season, week, team) —
summing across kickers so mid-season kicker changes don't split a team's total —
and:
  1. write data/raw/external/kicking.csv (RAW team codes) as the source of truth
     for a future `build_database` rebuild (_ingest_external normalizes it), and
  2. CREATE OR REPLACE the `kicking` table directly in the existing warehouse
     (normalized team codes) so no full FPD rebuild is needed right now.

USAGE:
  python scripts/fetch_kicking.py --seasons 2021 2022 2023 2024 2025
"""
from __future__ import annotations
import argparse
import io
import sys
from pathlib import Path

import duckdb
import pandas as pd
import requests

from nfl_projector_v1.config import DEFAULT_WAREHOUSE_PATH, PROJECT_ROOT
from nfl_projector_v1.utils import normalize_team

URL = ("https://github.com/nflverse/nflverse-data/releases/download/"
       "stats_player/stats_player_week_{season}.csv")


def fetch_season(season: int) -> pd.DataFrame:
    """Fetch + aggregate one season's kicking to (season, week, team, fg_made, fg_att)."""
    r = requests.get(URL.format(season=season), timeout=120)
    r.raise_for_status()
    df = pd.read_csv(io.BytesIO(r.content), low_memory=False)
    # Regular season only (matches the model's weeks 1-18 scope).
    if "season_type" in df.columns:
        df = df[df["season_type"] == "REG"]
    df["fg_made"] = pd.to_numeric(df.get("fg_made"), errors="coerce").fillna(0.0)
    df["fg_att"] = pd.to_numeric(df.get("fg_att"), errors="coerce").fillna(0.0)
    # Drop non-kicker rows (no FG activity) before aggregating — summing fg_made by
    # team-week folds multiple kickers (mid-season changes) into one team total.
    df = df[(df["fg_made"] > 0) | (df["fg_att"] > 0)]
    agg = (
        df.groupby(["season", "week", "team"], as_index=False)[["fg_made", "fg_att"]]
        .sum()
    )
    return agg


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--seasons", type=int, nargs="+",
                   default=[2021, 2022, 2023, 2024, 2025])
    p.add_argument("--db-path", default=str(DEFAULT_WAREHOUSE_PATH))
    args = p.parse_args(argv)

    frames = []
    for s in args.seasons:
        try:
            f = fetch_season(s)
            print(f"  {s}: {len(f)} team-weeks, {f['fg_made'].sum():.0f} FGs made")
            frames.append(f)
        except Exception as e:
            print(f"  [warn] {s}: {e}", file=sys.stderr)
    if not frames:
        print("No kicking data fetched.", file=sys.stderr)
        return 1
    raw = pd.concat(frames, ignore_index=True)

    # 1) Source-of-truth CSV (raw team codes) for future rebuilds.
    ext_dir = PROJECT_ROOT / "data" / "raw" / "external"
    ext_dir.mkdir(parents=True, exist_ok=True)
    csv_path = ext_dir / "kicking.csv"
    raw.to_csv(csv_path, index=False)
    print(f"\nWrote {csv_path} ({len(raw)} rows, raw team codes)")

    # 2) Normalized table directly into the existing warehouse (no rebuild).
    norm = raw.copy()
    bad = set()

    def _norm(code):
        try:
            return normalize_team(code)
        except ValueError:
            bad.add(code)
            return None

    norm["team"] = norm["team"].apply(_norm)
    if bad:
        print(f"  [warn] unmapped team codes dropped: {sorted(bad)}", file=sys.stderr)
    norm = norm[norm["team"].notna()].copy()

    con = duckdb.connect(args.db_path)
    try:
        con.register("__kick", norm)
        con.execute('CREATE OR REPLACE TABLE kicking AS SELECT * FROM __kick')
        con.unregister("__kick")
        n = con.execute("SELECT COUNT(*) FROM kicking").fetchone()[0]
        print(f"Wrote `kicking` table to {args.db_path} ({n} rows)\n")

        # Verification: league + per-team FGs/game over the seasons we ingested
        # (weeks 1-18 only, so playoff games don't skew the per-game denominator).
        seasons_csv = ",".join(str(s) for s in args.seasons)
        league = con.execute(f"""
            WITH gp AS (
                SELECT home_team t FROM schedule
                WHERE home_score IS NOT NULL AND week BETWEEN 1 AND 18 AND season IN ({seasons_csv})
                UNION ALL SELECT away_team t FROM schedule
                WHERE home_score IS NOT NULL AND week BETWEEN 1 AND 18 AND season IN ({seasons_csv})
            )
            SELECT SUM(k.fg_made) / (SELECT COUNT(*) FROM gp) AS fg_per_team_game
            FROM kicking k
        """).fetchone()[0]
        print(f"League FGs per team-game ({seasons_csv}, wk 1-18): {league:.3f}")
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
