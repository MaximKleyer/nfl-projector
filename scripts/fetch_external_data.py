"""Fetch schedule + Vegas closing lines from nflverse (free, public).

nflverse publishes weekly NFL data including game schedules with closing
spreads, totals, and weather, in CSV format hosted on GitHub releases.

Source: https://github.com/nflverse/nflverse-data

Outputs:
    data/raw/external/schedule.csv
    data/raw/external/vegas.csv

USAGE:
    python scripts/fetch_external_data.py --seasons 2023 2024 2025
"""

from __future__ import annotations
import argparse
from pathlib import Path
import sys
import requests
import pandas as pd


# nflverse maintains a single 'games' table that has both schedule and Vegas.
# The CSV URL pattern. (You can also use the Parquet URL if you prefer.)
GAMES_URL = "https://github.com/nflverse/nfldata/raw/master/data/games.csv"

# nflverse injuries data. One CSV per season at:
#   https://github.com/nflverse/nflverse-data/releases/download/injuries/injuries_{YEAR}.csv
# Contains weekly injury report data: status (Q/D/O/IR), body part,
# practice participation, and game-day designations.
INJURIES_URL = (
    "https://github.com/nflverse/nflverse-data/releases/download/"
    "injuries/injuries_{season}.csv"
)


# Map nflverse columns → our schema
SCHEDULE_COLS = {
    "season":      "season",
    "week":        "week",
    "game_id":     "game_id",
    "home_team":   "home_team",
    "away_team":   "away_team",
    "home_score":  "home_score",
    "away_score":  "away_score",
    "gameday":     "kickoff_dt",
    "roof":        "_roof",          # 'dome' / 'closed' / 'outdoors' / 'open'
    "surface":     "surface",
    "temp":        "weather_temp",
    "wind":        "weather_wind",
}

VEGAS_COLS = {
    "season":          "season",
    "week":            "week",
    "game_id":         "game_id",
    "spread_line":     "spread_close",     # negative = home favored (matches our convention)
    "total_line":      "total_close",
    "home_moneyline":  "moneyline_home",
    "away_moneyline":  "moneyline_away",
}


def _download_games() -> pd.DataFrame:
    print(f"  Downloading {GAMES_URL}...")
    r = requests.get(GAMES_URL, timeout=60)
    r.raise_for_status()
    from io import StringIO
    return pd.read_csv(StringIO(r.text))


# Map nflverse injury columns to our schema. nflverse uses verbose
# descriptions; we standardize and shorten.
INJURY_COLS = {
    "season":            "season",
    "week":              "week",
    "team":              "team",
    "gsis_id":           "gsis_id",       # NFL stable player ID
    "full_name":         "player_name",
    "position":          "position",
    "report_status":     "report_status",  # 'Questionable' / 'Doubtful' / 'Out' / 'IR' / blank
    "practice_status":   "practice_status",  # 'DNP' / 'Limited' / 'Full' / blank
    "report_primary":    "injury_primary",
    "report_secondary":  "injury_secondary",
    "date_modified":     "date_modified",
}


def _download_injuries(season: int) -> pd.DataFrame | None:
    """Download nflverse injury data for one season.

    Returns None if data isn't available (e.g. a future season or
    nflverse hasn't published yet). Returns DataFrame on success.
    """
    url = INJURIES_URL.format(season=season)
    print(f"  Downloading injuries for {season}: {url}")
    try:
        r = requests.get(url, timeout=60)
        if r.status_code == 404:
            print(f"  [skip] no injuries data published yet for {season}")
            return None
        r.raise_for_status()
    except Exception as e:
        print(f"  [warn] injuries fetch failed for {season}: {e}")
        return None
    from io import StringIO
    return pd.read_csv(StringIO(r.text))


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--seasons", type=int, nargs="+", required=True)
    p.add_argument("--out-dir", default="data/raw/external")
    args = p.parse_args(argv)

    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        games = _download_games()
    except Exception as e:
        print(f"ERROR: failed to download games data: {e}", file=sys.stderr)
        print("If this is a network issue, you can manually download the file from:",
              file=sys.stderr)
        print(f"  {GAMES_URL}", file=sys.stderr)
        print(f"and save to {out_dir}/games_raw.csv, then re-run.", file=sys.stderr)
        return 1

    games = games[games["season"].isin(args.seasons)].copy()
    print(f"  Got {len(games)} games across seasons {args.seasons}")

    # Schedule
    sched_cols_present = {k: v for k, v in SCHEDULE_COLS.items() if k in games.columns}
    sched = games[list(sched_cols_present.keys())].rename(columns=sched_cols_present)
    if "_roof" in sched.columns:
        sched["dome"] = sched["_roof"].isin(["dome", "closed"]).astype(int)
        sched = sched.drop(columns=["_roof"])
    else:
        sched["dome"] = 0
    sched_path = out_dir / "schedule.csv"
    sched.to_csv(sched_path, index=False)
    print(f"  Wrote {sched_path} ({len(sched)} rows)")

    # Vegas
    vegas_cols_present = {k: v for k, v in VEGAS_COLS.items() if k in games.columns}
    vegas = games[list(vegas_cols_present.keys())].rename(columns=vegas_cols_present)
    # Convention conversion: nflverse uses spread_line as POSITIVE when home
    # is favored. The rest of this codebase (matchups.yaml, team outcome
    # model, ATS calculations) uses NEGATIVE when home is favored. Flip the
    # sign here so everything downstream uses one consistent convention.
    if "spread_close" in vegas.columns:
        vegas["spread_close"] = -vegas["spread_close"].astype(float)
    vegas_path = out_dir / "vegas.csv"
    vegas.to_csv(vegas_path, index=False)
    print(f"  Wrote {vegas_path} ({len(vegas)} rows)")
    print(f"  Note: spread_close uses 'negative = home favored' convention.")

    # Injuries (per-season, optional)
    print()
    inj_frames = []
    for season in args.seasons:
        df = _download_injuries(season)
        if df is None or df.empty:
            continue
        # Subset to known columns and rename
        cols_present = {k: v for k, v in INJURY_COLS.items() if k in df.columns}
        df = df[list(cols_present.keys())].rename(columns=cols_present)
        # Drop rows with no status — those are noise (no injury reported)
        if "report_status" in df.columns:
            df = df[df["report_status"].notna() & (df["report_status"] != "")]
        inj_frames.append(df)
    if inj_frames:
        injuries = pd.concat(inj_frames, ignore_index=True)
        inj_path = out_dir / "injuries.csv"
        injuries.to_csv(inj_path, index=False)
        print(f"  Wrote {inj_path} ({len(injuries)} rows of injury reports)")
        # Quick sanity check: status distribution
        if "report_status" in injuries.columns:
            print(f"  Status counts: "
                  f"{dict(injuries['report_status'].value_counts().head(6))}")
    else:
        print("  No injury data was retrieved. Continuing without it.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
