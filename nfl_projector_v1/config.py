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

LEAGUE_AVG_NON_OFFENSIVE_POINTS_PER_TEAM = 1.0  # DST TDs + ST TDs + safeties

LEAGUE_AVG_TEAM_FGS_PER_GAME = 2.0

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

# Team rush-volume ceiling (team.py). Summing per-RB projected carries can
# overshoot a team's real volume when the roster is inclusive (committee backs /
# an extra RB), inflating rush yards and total points. team.py caps the team's
# total carries at this multiple of its recent RB-carry baseline. 1.20 leaves
# headroom for run-heavy game scripts while clipping the egregious overshoots;
# capping tighter (toward 1.0) over-corrects and pushes totals to under-project.
RUSH_VOLUME_CEILING_MULT = 1.20

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

# ---------------------------------------------------------------------------
# Snap-share roster & injury-aware recency (DESIGN.md §12)
# ---------------------------------------------------------------------------
# Skill-position rosters (WR/RB/FB/TE) are selected from FPD snap share instead
# of the static depth chart. QB is handled separately (depth chart + injury +
# manual override). See DESIGN.md §12 for the full design.

# Minimum average snap% (over a player's last RECENT_GAMES_WINDOW *active* games,
# i.e. games actually played) for a skill player to be projected. ~15% reproduces
# the old WR4/RB3/TE2 roster sizes from actual playing time while keeping marginal
# slot WRs / committee RBs.
SNAP_SHARE_MIN_PCT = 15.0

# Hard cap on how many players to keep per position after the snap-share filter
# (FB folds into RB). Prevents a noisy 6th WR from inflating team production.
SNAP_ROSTER_CAPS = {"WR": 5, "RB": 4, "TE": 3}

# Positions selected via snap share. FB folds into RB downstream.
SNAP_SELECTION_POSITIONS = ("WR", "RB", "TE", "FB")

# --- Return-game ramp discount (§12.4) -------------------------------------
# A player back from a real absence (>= RETURN_TRIGGER_GAMES_MISSED of his TEAM's
# games) is eased in: his projected VOLUME is multiplied by the games-back factor
# (efficiency is untouched). Games-back beyond the table -> 1.0 (full).
RETURN_TRIGGER_GAMES_MISSED = 2
RETURN_RAMP_FACTORS = {1: 0.80, 2: 0.90}

# --- Staleness regression (§12.5) ------------------------------------------
# The longer a player has been gone, the more his (stale) recent form is regressed
# toward the positional/league baseline:
#   r = 1 - STALENESS_DECAY ** max(0, team_games_missed - STALENESS_GRACE_GAMES)
#   final = (1 - r) * player_form + r * positional_baseline
STALENESS_DECAY = 0.85
STALENESS_GRACE_GAMES = 1

# --- QB starter resolution (§12.2) -----------------------------------------
# Manual override file (tracked YAML, keyed season -> week -> team -> QB name).
# Wins over the depth chart; for healthy benchings the depth chart/injury report
# can't capture. Lives in the package (the gitignored tree is data/, not here).
QB_STARTERS_YAML = Path(__file__).resolve().parent / "qb_starters.yaml"

# QB injury statuses that trigger fallthrough from depth-chart QB1 to QB2.
# (Questionable still starts, with the existing INJURY_MULTIPLIERS drag.)
QB_WONT_START_STATUSES = {"Out", "IR", "Doubtful"}

# Default roster selection mode: "depth_chart" (legacy) or "snaps" (§12).
# Flipped to "snaps" (2026-05-31) after the A/B backtest: snap-share beat the
# depth chart on SU (+2.5), ATS (+1.3), O/U (+0.8), and margin/total MAE over
# 2023-2025. Set back to "depth_chart" (or `backtest --roster-mode depth_chart`)
# to run the legacy path for comparison.
DEFAULT_ROSTER_MODE = "snaps"
