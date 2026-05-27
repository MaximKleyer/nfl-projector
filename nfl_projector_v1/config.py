"""Configuration: league constants, paths, model knobs.

All numerical constants in this file are LEAGUE AVERAGES verified empirically
against the 2023-2025 warehouse data (see DESIGN.md section 4.1 for the
verification queries).

When tuning the model, change knobs here rather than in projection logic.
Keeps the math reproducible and inspectable.
"""
from __future__ import annotations
from pathlib import Path

# ---------------------------------------------------------------------------
# League scoring conversions (empirically derived, 2023-2025 weeks 1-18)
# ---------------------------------------------------------------------------

# Passing TDs per passing yard. Computed: 2352 pass TDs / 377,786 pass yds.
LEAGUE_PASS_TD_PER_YARD = 0.0062

# Rushing TDs per rushing yard. Computed: 1098 rush TDs / 151,868 rush yds.
LEAGUE_RUSH_TD_PER_YARD = 0.0072

# Points per touchdown (6 TD + 0.95 PAT make rate). Slightly higher than 6
# but lower than 7. The remainder accounts for missed XPs and 2pt conversions
# averaging out roughly to 0.95.
POINTS_PER_TD_WITH_PAT = 6.95

POINTS_PER_FG = 3.0

# Standard deviation of (home_score - away_score) across all games 2023-2025.
# Used to convert predicted margin → win probability via normal distribution.
NFL_MARGIN_STD_DEV = 14.34

# Mean team points per game (for sanity checks; not used in projection itself)
LEAGUE_AVG_TEAM_PPG = 22.56

# ---------------------------------------------------------------------------
# League position baselines (used as fallback when a player has no history,
# e.g. preseason or a rookie's first game)
# ---------------------------------------------------------------------------

LEAGUE_AVG_QB_PASS_ATTEMPTS = 32.7
LEAGUE_AVG_QB_COMP_PCT      = 64.9
LEAGUE_AVG_QB_YPA           = 7.14
LEAGUE_AVG_QB_SCRAMBLES     = 1.9

LEAGUE_AVG_RB_CARRIES_STARTER = 15.7
LEAGUE_AVG_RB_YPC             = 4.35

LEAGUE_AVG_RECV_YPT          = 8.03   # yards per target
LEAGUE_AVG_RECV_YPR          = 12.10  # yards per reception
LEAGUE_AVG_RECV_CATCH_RATE   = 68.1   # percent

LEAGUE_AVG_TEAM_FGS_PER_GAME = 1.8

# ---------------------------------------------------------------------------
# Model knobs (tunable parameters of the projection logic itself)
# ---------------------------------------------------------------------------

# How many recent games count as "recent form". Used by blend_with_baseline.
RECENT_GAMES_WINDOW = 4

# Weight given to recent form vs season baseline.
# When player has at least RECENT_GAMES_WINDOW games of history:
#   blended = RECENT_WEIGHT * recent + (1 - RECENT_WEIGHT) * season_baseline
RECENT_WEIGHT = 0.7

# Hard caps for opponent matchup multipliers. Keeps a single bad-defense game
# from making projections fly off the rails. Past experiments showed wider
# caps caused overshoot at the extremes.
MATCHUP_FLOOR   = 0.85
MATCHUP_CEILING = 1.15

# Injury status multipliers. "Out" and "IR" zero out the player entirely.
# "Questionable" applies a small drag (15%). "Doubtful" applies a heavy drag.
INJURY_MULTIPLIERS = {
    "Out":          0.0,
    "IR":           0.0,
    "Doubtful":     0.30,
    "Questionable": 0.85,
    # Anything else (no listing, "Probable", etc.) → 1.0
}

# Cap on receiver yards (defensive sanity check on allocation step).
# League max single-game receiving in the dataset is around 250 yards;
# capping at 280 leaves room for outliers while preventing absurd outputs.
RECEIVER_YARDS_CAP = 280.0

# ---------------------------------------------------------------------------
# File paths
# ---------------------------------------------------------------------------
#
# Path resolution: walk up from this file's location, looking for a directory
# that contains data/processed/warehouse.duckdb. This makes v2 portable —
# you can drop the package into any layout (inside v1's repo, beside it,
# nested several levels down) and it'll find the warehouse automatically.
#
# Override at runtime via CLI flags or by passing an explicit `path` argument
# to open_warehouse() and similar functions.


def _find_warehouse() -> Path:
    """Search for data/processed/warehouse.duckdb.

    Looks in two patterns:
      1. Walking up from this file's parents (handles "nested" layout —
         v2 inside v1's repo).
      2. At each level, also checking sibling directories (handles "beside"
         layout — v2 next to v1's repo, both under a common parent).

    Returns the path if found. If not found, returns the canonical relative
    path — the caller will get a clean FileNotFoundError pointing at the
    expected location, which is more useful than a silent failure.
    """
    here = Path(__file__).resolve()
    target = Path("data") / "processed" / "warehouse.duckdb"

    for parent in [here, *here.parents]:
        # Direct check: does this directory contain data/processed/warehouse.duckdb?
        candidate = parent / target
        if candidate.exists():
            return candidate
        # Sibling check: any sibling directories that contain it?
        if parent.is_dir():
            try:
                for sibling in parent.iterdir():
                    if sibling.is_dir():
                        candidate = sibling / target
                        if candidate.exists():
                            return candidate
            except PermissionError:
                continue

    # Fallback — won't exist, but the path it points at tells the user
    # what we were looking for
    return Path("data/processed/warehouse.duckdb")


def _find_project_root() -> Path:
    """The directory that contains the warehouse (used as the anchor for
    other data paths like outputs and depth charts).
    """
    warehouse = _find_warehouse()
    if warehouse.exists():
        # data/processed/warehouse.duckdb → project root is 2 levels up
        return warehouse.parent.parent.parent
    # Fallback if no warehouse found — use the directory above the package
    return Path(__file__).resolve().parent.parent


PROJECT_ROOT = _find_project_root()
DEFAULT_WAREHOUSE_PATH  = _find_warehouse()
DEFAULT_OUTPUT_DIR      = PROJECT_ROOT / "data" / "processed" / "v2"
DEFAULT_DEPTH_CHART_DIR = PROJECT_ROOT / "data" / "raw" / "depth_charts"
