"""Depth charts: fetch from nflverse, cache locally.

nflverse publishes a single CSV with all depth charts for all teams,
all weeks, all seasons. URL is stable and free.

Source:
    https://github.com/nflverse/nflverse-data/releases/tag/depth_charts

We download the full CSV once, cache it locally, and slice it per (season,
week) on demand. Avoids re-downloading during backtests.

This module does NOT decide who plays — that's roster.py's job. Here we
just pull and cache.
"""
from __future__ import annotations
from pathlib import Path
import pandas as pd
import requests

from ..config import DEFAULT_DEPTH_CHART_DIR


# nflverse stores one CSV per season at this URL pattern. Updated weekly
# during the season by nflverse maintainers.
DEPTH_CHART_URL_PATTERN = (
    "https://github.com/nflverse/nflverse-data/releases/download/"
    "depth_charts/depth_charts_{season}.csv"
)

# Cache: one file per season locally too. Re-download per-season when refresh=True.
CACHE_FILENAME_PATTERN = "depth_charts_{season}.csv"


def _cache_path(season: int, cache_dir: Path | None = None) -> Path:
    cache_dir = Path(cache_dir) if cache_dir else DEFAULT_DEPTH_CHART_DIR
    return cache_dir / CACHE_FILENAME_PATTERN.format(season=season)


def fetch_depth_charts_season(
    season: int,
    cache_dir: Path | None = None,
    refresh: bool = False,
    verbose: bool = True,
) -> pd.DataFrame:
    """Fetch the depth-chart CSV for ONE season from nflverse.

    Parameters
    ----------
    season : NFL season (e.g. 2024)
    cache_dir : where to store the cached CSV. Default: DEFAULT_DEPTH_CHART_DIR.
    refresh : if True, re-download even if cached. Use weekly during the
              season to pick up the latest depth charts.
    verbose : print progress.

    Returns
    -------
    Raw DataFrame as downloaded. Column normalization happens in
    get_depth_chart().
    """
    cache_path = _cache_path(season, cache_dir)
    cache_path.parent.mkdir(parents=True, exist_ok=True)

    if cache_path.exists() and not refresh:
        if verbose:
            print(f"  Using cached depth charts ({season}): {cache_path}")
        return pd.read_csv(cache_path, low_memory=False)

    url = DEPTH_CHART_URL_PATTERN.format(season=season)
    if verbose:
        print(f"  Downloading depth charts for {season}...")
    response = requests.get(url, timeout=60)
    response.raise_for_status()
    cache_path.write_bytes(response.content)
    if verbose:
        print(f"  Cached to {cache_path}")
    return pd.read_csv(cache_path, low_memory=False)


def fetch_depth_charts_seasons(
    seasons: list[int],
    cache_dir: Path | None = None,
    refresh: bool = False,
    verbose: bool = True,
) -> pd.DataFrame:
    """Fetch depth charts for multiple seasons, concatenated."""
    frames = []
    for s in seasons:
        try:
            frames.append(fetch_depth_charts_season(
                s, cache_dir=cache_dir, refresh=refresh, verbose=verbose,
            ))
        except requests.HTTPError as e:
            if verbose:
                print(f"  [warn] could not fetch depth charts for {s}: {e}")
            continue
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def get_depth_chart(
    season: int,
    week: int,
    cache_dir: Path | None = None,
    refresh: bool = False,
    schedule: "pd.DataFrame | None" = None,
) -> pd.DataFrame:
    """Get the depth chart for ONE specific (season, week).

    Returns a normalized DataFrame with these columns:
        season, week, team, player_name, position, depth_order

    `depth_order` is integer 1+ — 1 = starter, 2 = backup, etc.
    `position` is normalized to {QB, RB, WR, TE} (others dropped here
    since v1 only projects offense).

    Handles two nflverse schemas:
      - Legacy (2023-2024): club_code, full_name, position, depth_team, week
      - New (2025+):        team, player_name, pos_abb, pos_rank, dt
                            (no week column — derived from dt by snapping
                            each snapshot date to the NFL week from the
                            schedule. Without a schedule, falls back to a
                            cruder "i-th unique date = week i" heuristic.)

    Performance: the full-season normalized DataFrame is cached at module
    level, so calls for additional weeks of the same season are cheap
    (just a filter). Use refresh=True to bypass the cache.

    If no snapshot maps to (season, week) — the offseason, or a season with no
    games played yet — falls back to get_latest_depth_chart() (the current
    projected chart), so future-season predictions can be bootstrapped.
    """
    normalized_full = _get_normalized_full_season(
        season, cache_dir=cache_dir, refresh=refresh, schedule=schedule,
    )
    if not normalized_full.empty:
        df = normalized_full[normalized_full["week"] == week].copy()
        if not df.empty:
            return df.sort_values(["team", "position", "depth_order"]).reset_index(drop=True)

    # Fallback: no in-season snapshot maps to this (season, week) — the offseason
    # or preseason, or a new season with no games played yet (the in-season path
    # drops pre-September snapshots). Use the latest available PROJECTED depth
    # chart so a season can be bootstrapped before kickoff. nflverse refreshes the
    # underlying feed daily through the offseason, so this sharpens as the season
    # nears. See get_latest_depth_chart.
    return get_latest_depth_chart(season, cache_dir=cache_dir, refresh=refresh)


def get_latest_depth_chart(
    season: int,
    cache_dir: Path | None = None,
    refresh: bool = False,
) -> pd.DataFrame:
    """The CURRENT projected depth chart: each team's most recent snapshot,
    ignoring the in-season Sept-1 cutoff and week-snapping that get_depth_chart
    applies. This is what bootstraps a season before any games are played
    (offseason / preseason / week 1 of a new season).

    Returns the same normalized columns as get_depth_chart
    (`season, week, team, player_name, position, depth_order`), with `week` set
    to 0 to mark it as a projected/preseason chart. Because nflverse updates the
    feed daily, re-pulling closer to kickoff yields a sharper chart.
    """
    if not refresh and season in _LATEST_DC_CACHE:
        return _LATEST_DC_CACHE[season]

    out_cols = ["season", "week", "team", "player_name", "position", "depth_order"]
    raw = fetch_depth_charts_season(season, cache_dir=cache_dir, refresh=refresh, verbose=False)
    if raw.empty:
        empty = pd.DataFrame(columns=out_cols)
        _LATEST_DC_CACHE[season] = empty
        return empty

    is_new_schema = "week" not in raw.columns and "dt" in raw.columns
    if is_new_schema:
        df = raw.copy()
        df["dt_parsed"] = pd.to_datetime(df["dt"], errors="coerce", utc=True).dt.tz_localize(None)
        df = df.dropna(subset=["dt_parsed"])
        rename_map = {}
        if "pos_abb" in df.columns:
            rename_map["pos_abb"] = "position"
        elif "pos_grp" in df.columns:
            rename_map["pos_grp"] = "position"
        if "pos_rank" in df.columns:
            rename_map["pos_rank"] = "depth_order"
        df = df.rename(columns=rename_map)
        df["season"] = season
        # Keep only each team's most recent snapshot (the current projection).
        latest = df.groupby("team")["dt_parsed"].transform("max")
        df = df[df["dt_parsed"] == latest].copy()
    else:
        df = _normalize_legacy_schema_full(raw)
        df["season"] = season
        # Legacy schema carries a week column; "latest" = the final week present.
        wk = pd.to_numeric(df.get("week"), errors="coerce")
        if wk.notna().any():
            df = df[wk == wk.max()].copy()

    if df.empty or "position" not in df.columns:
        empty = pd.DataFrame(columns=out_cols)
        _LATEST_DC_CACHE[season] = empty
        return empty

    df["week"] = 0
    df["depth_order"] = pd.to_numeric(df["depth_order"], errors="coerce").fillna(99).astype(int)
    offensive = {"QB", "RB", "WR", "TE", "FB", "HB"}
    df = df[df["position"].isin(offensive)].copy()
    df.loc[df["position"].isin(["FB", "HB"]), "position"] = "RB"
    df = (
        df.sort_values(["team", "player_name", "position", "depth_order"])
          .drop_duplicates(subset=["team", "player_name", "position"], keep="first")
    )
    result = df[out_cols].sort_values(["team", "position", "depth_order"]).reset_index(drop=True)
    _LATEST_DC_CACHE[season] = result
    return result


# Module-level caches (populated lazily; survive the process, cleared via refresh=True).
_NORMALIZED_DC_CACHE: dict[int, pd.DataFrame] = {}   # season -> in-season normalized full season
_LATEST_DC_CACHE: dict[int, pd.DataFrame] = {}        # season -> latest projected snapshot


def _get_normalized_full_season(
    season: int,
    cache_dir: Path | None = None,
    refresh: bool = False,
    schedule: "pd.DataFrame | None" = None,
) -> pd.DataFrame:
    """Return the fully-normalized depth chart DataFrame for ALL weeks of
    a season. Reads from cache; populates if missing.

    This is where the per-call work (CSV read, date parsing, week
    derivation, deduplication) actually happens — once per season per
    process — so per-week calls in get_depth_chart() are cheap.

    `schedule` (optional) is used for the 2025+ schema to snap snapshot
    dates to real NFL weeks. Strongly recommended for 2025+ — without it,
    week numbering falls back to a crude heuristic that is wrong when
    nflverse publishes daily (rather than weekly) snapshots.
    """
    if not refresh and season in _NORMALIZED_DC_CACHE:
        return _NORMALIZED_DC_CACHE[season]

    raw = fetch_depth_charts_season(season, cache_dir=cache_dir, refresh=refresh, verbose=False)
    if raw.empty:
        empty = pd.DataFrame(columns=[
            "season", "week", "team", "player_name", "position", "depth_order"
        ])
        _NORMALIZED_DC_CACHE[season] = empty
        return empty

    # Detect schema version
    is_new_schema = "week" not in raw.columns and "dt" in raw.columns

    if is_new_schema:
        df = _normalize_new_schema_full(raw, season, schedule=schedule)
    else:
        df = _normalize_legacy_schema_full(raw)

    if df.empty:
        empty = pd.DataFrame(columns=[
            "season", "week", "team", "player_name", "position", "depth_order"
        ])
        _NORMALIZED_DC_CACHE[season] = empty
        return empty

    # Common cleanup: integer depth_order, filter to offensive positions
    df["depth_order"] = pd.to_numeric(df["depth_order"], errors="coerce").fillna(99).astype(int)

    offensive_positions = {"QB", "RB", "WR", "TE", "FB", "HB"}
    df = df[df["position"].isin(offensive_positions)].copy()
    df.loc[df["position"].isin(["FB", "HB"]), "position"] = "RB"

    # For the new schema, multiple snapshot dates can map to the same week
    # (nflverse publishes ~daily). Keep ONLY the latest snapshot per
    # (season, week, team) so we use the freshest roster going into the week.
    if is_new_schema and "dt_parsed" in df.columns:
        latest = (
            df.groupby(["season", "week", "team"])["dt_parsed"]
              .transform("max")
        )
        df = df[df["dt_parsed"] == latest].copy()

    # De-duplicate (legacy schema has formation duplicates; new schema usually
    # doesn't, but apply everywhere for safety). Per-week dedup.
    df = (
        df.sort_values(["season", "week", "team", "player_name", "position", "depth_order"])
          .drop_duplicates(subset=["season", "week", "team", "player_name", "position"], keep="first")
    )

    out_cols = ["season", "week", "team", "player_name", "position", "depth_order"]
    result = df[out_cols].reset_index(drop=True)
    _NORMALIZED_DC_CACHE[season] = result
    return result


def _normalize_legacy_schema_full(raw: pd.DataFrame) -> pd.DataFrame:
    """Normalize 2023-2024 nflverse depth chart schema for ALL weeks.

    Returns a DataFrame with all weeks present in the input file. The
    per-week filter happens in get_depth_chart.
    """
    df = raw.copy()

    rename_map = {}
    if "club_code" in df.columns:
        rename_map["club_code"] = "team"
    elif "team" not in df.columns and "recent_team" in df.columns:
        rename_map["recent_team"] = "team"
    if "full_name" in df.columns:
        rename_map["full_name"] = "player_name"
    elif "football_name" in df.columns:
        rename_map["football_name"] = "player_name"
    if "depth_team" in df.columns:
        rename_map["depth_team"] = "depth_order"
    df = df.rename(columns=rename_map)

    # Build player_name from first + last if missing
    if "player_name" not in df.columns:
        if "first_name" in df.columns and "last_name" in df.columns:
            df["player_name"] = (
                df["first_name"].fillna("").astype(str) + " "
                + df["last_name"].fillna("").astype(str)
            ).str.strip()
        else:
            raise ValueError(
                "Legacy depth chart CSV missing player name columns. "
                f"Found: {sorted(df.columns.tolist())}"
            )

    if "position" not in df.columns:
        raise ValueError(
            "Legacy depth chart CSV missing 'position' column. "
            f"Found: {sorted(df.columns.tolist())}"
        )

    return df


def _normalize_new_schema_full(
    raw: pd.DataFrame,
    season: int,
    schedule: "pd.DataFrame | None" = None,
) -> pd.DataFrame:
    """Normalize 2025+ nflverse depth chart schema for ALL weeks.

    There's no `week` or `season` — we derive both from `dt`.

    nflverse publishes depth chart snapshots frequently (often daily) in
    2025. To assign each snapshot to the correct NFL week, we snap each
    snapshot date to the week whose games it precedes, using the schedule's
    per-week start dates.

    If no schedule is provided, fall back to a cruder heuristic:
    "the i-th unique date is week i+1" — which is WRONG when snapshots are
    daily, but kept as a last resort so the function still runs.

    For a given week N, we want the snapshot taken most recently BEFORE
    that week's first game (the roster as set going into the week).
    """
    df = raw.copy()
    df["dt_parsed"] = pd.to_datetime(df["dt"], errors="coerce", utc=True).dt.tz_localize(None)

    # Drop preseason snapshots (training camps in July-August)
    season_start = pd.Timestamp(year=season, month=9, day=1)
    df = df[df["dt_parsed"] >= season_start]
    if df.empty:
        return df

    # Column normalization (do this before week assignment)
    rename_map = {}
    if "pos_abb" in df.columns:
        rename_map["pos_abb"] = "position"
    elif "pos_grp" in df.columns:
        rename_map["pos_grp"] = "position"
    if "pos_rank" in df.columns:
        rename_map["pos_rank"] = "depth_order"
    df = df.rename(columns=rename_map)
    df["season"] = season

    if schedule is not None and not schedule.empty:
        df["week"] = _assign_weeks_from_schedule(df, season, schedule)
        # Drop rows that couldn't be assigned to a week (before week 1, etc.)
        df = df[df["week"].notna()].copy()
        df["week"] = df["week"].astype(int)
    else:
        # Fallback heuristic (wrong for daily snapshots, but better than crashing)
        unique_dates = sorted(df["dt_parsed"].dropna().unique())
        if not unique_dates:
            return pd.DataFrame()
        date_to_week = {d: i + 1 for i, d in enumerate(unique_dates)}
        df["week"] = df["dt_parsed"].map(date_to_week)

    return df


def _assign_weeks_from_schedule(
    df: pd.DataFrame,
    season: int,
    schedule: pd.DataFrame,
) -> pd.Series:
    """Map each depth-chart snapshot date to an NFL week number.

    For week N, the relevant snapshot is the latest one taken ON OR BEFORE
    that week's first kickoff. Equivalently: a snapshot taken on date D
    belongs to the week whose first game is the next kickoff strictly
    after D (so a Tue/Wed snapshot maps to the upcoming Sunday's week).

    We implement this by building week-start dates from the schedule and
    using a searchsorted-style bucketing: a snapshot belongs to the latest
    week whose start date is >= the snapshot date... actually we want the
    NEXT week's games. We assign snapshot date D to week N where
    week_start[N] is the smallest week-start strictly greater than D, OR
    the snapshot falls within [week_start[N], week_start[N+1]).

    Simpler framing actually used here: snapshot date D belongs to the week
    currently "in progress or upcoming" — find the week whose start date is
    the largest one <= D + a few days. We use: assign D to the week whose
    start is the greatest week_start <= D; if D is before week 1 start but
    after Sept 1, assign to week 1.
    """
    # Build week → start date from the schedule
    sched = schedule[schedule["season"] == season]
    if sched.empty or "kickoff_dt" not in sched.columns:
        # No usable schedule; return NaNs so caller falls through
        return pd.Series([pd.NA] * len(df), index=df.index)

    week_starts = (
        sched.groupby("week")["kickoff_dt"]
        .min()
        .sort_index()
    )
    week_starts = pd.to_datetime(week_starts)

    # Build arrays for bucketing
    weeks = week_starts.index.to_numpy()
    starts = week_starts.to_numpy().astype("datetime64[ns]")

    snapshot_dates = df["dt_parsed"].to_numpy().astype("datetime64[ns]")

    # For each snapshot date D, assign it to the week whose games are the
    # NEXT to be played after D. A snapshot taken Tue-Sat before a Sunday
    # slate is the roster going INTO that week, so it should map to that
    # upcoming week — not the week that just finished.
    #
    # We bucket by: week N "owns" snapshot dates in the window
    #   [week_start[N-1] + 1 day  ...  week_start[N]]
    # i.e. the days leading up to week N's first game. Implementation:
    # find the smallest week_start that is >= snapshot date (side="left").
    import numpy as np
    idx = np.searchsorted(starts, snapshot_dates, side="left")

    # idx == len(weeks) means the snapshot is after the last week's start
    # (late-season snapshot for the final week, or postseason). Clamp to
    # the last regular week.
    result_weeks = []
    n_weeks = len(weeks)
    for i in idx:
        if i >= n_weeks:
            result_weeks.append(int(weeks[-1]))    # after last start → last week
        else:
            result_weeks.append(int(weeks[i]))
    return pd.Series(result_weeks, index=df.index)

    # De-duplicate: a player may appear multiple times per (team, week) because
    # depth charts have multiple formations (base, nickel, dime, goal-line, etc.).
    # For each (team, player_name, position) keep the row with the LOWEST
    # depth_order — that's their best/most-prominent role.
    df = (
        df.sort_values(["team", "player_name", "position", "depth_order"])
          .drop_duplicates(subset=["team", "player_name", "position"], keep="first")
    )

    out_cols = ["season", "week", "team", "player_name", "position", "depth_order"]
    return df[out_cols].sort_values(["team", "position", "depth_order"]).reset_index(drop=True)
