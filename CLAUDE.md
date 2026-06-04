# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository layout note

The git repository and project root is `nfl_projector_v1/` (the folder containing this
file, `pyproject.toml`, `README.md`, `DESIGN.md`). It sits one level below the editor
workspace root (`NFLProjectionModel/`). Inside it, the importable Python package is the
nested `nfl_projector_v1/nfl_projector_v1/`. Run all commands from the repo root.

## Environment & setup

- Python **3.14** (the committed `.venv` is cpython-3.14; `pyproject.toml` only floors at 3.10).
- Activate the venv before running anything: `.venv\Scripts\Activate.ps1` (Windows PowerShell).
- Install editable: `pip install -e .`
- Dependencies: pandas, numpy, duckdb, pyyaml, scipy.
- **Run `python -m nfl_projector_v1 …` from the repo root** (`nfl_projector_v1/`). Running from the
  workspace root makes Python's path include both the repo-root `data/` dir and the `data` code
  subpackage, which collide and break `import nfl_projector_v1.data.*`.
- **Windows console gotcha:** the CLI/scripts print Unicode (`→`, `×`) that cp1252 can't encode and
  crash on. Prefix runs with `$env:PYTHONIOENCODING='utf-8'`.

## Commands

```powershell
# Check data availability (warehouse tables + depth-chart cache). Run this first.
python -m nfl_projector_v1 status

# Predict every game in a week (prints a table + writes a CSV). Works for future weeks.
python -m nfl_projector_v1 predict --season 2025 --week 16
python -m nfl_projector_v1 predict --season 2025 --week 16 --players   # + per-player stat lines + player CSV

# Project a full season: per-team expected wins + Monte Carlo division/playoff odds
python -m nfl_projector_v1 predict-season --season 2026

# Walk-forward backtest over one or more seasons (this is the regression check — see below)
python -m nfl_projector_v1 backtest --seasons 2024 2025
python -m nfl_projector_v1 backtest --seasons 2024 2025 --min-week 14 --max-week 15   # quick slice
# A/B knobs (all default to production): --roster-mode, --td-rates, --calibrate/--no-calibrate, --home-field, --fg-rates, --environment

# Refresh depth charts from nflverse (needs network; do this before predicting a new week)
python -m nfl_projector_v1 refresh-depth-charts --seasons 2025

# Build the warehouse from raw FPD CSVs (only when warehouse.duckdb is missing/stale)
python scripts/build_database.py --seasons 2023 2024 2025
```

The five CLI subcommands are `predict`, `predict-season`, `backtest`, `refresh-depth-charts`,
`status` (wired in [cli.py](nfl_projector_v1/cli.py)). The entry point is `python -m nfl_projector_v1`.

### Tests

There is **no automated test suite** (the only `test_*.py` files are inside `.venv`). The
de-facto regression check is the **backtest** — run it before/after a change to a projection
module and compare the summary metrics (margin MAE, SU/ATS/OU accuracy) against the
benchmarks recorded in `README.md` / `DESIGN.md` §10. [check_dc.py](check_dc.py) at the repo
root is a small standalone depth-chart sanity script.

## Architecture: bottom-up player → team → game

The model never predicts team scores directly. It projects individual player stat lines,
aggregates them into team production (yards), converts production to points, then pairs two
teams into a game. Understanding any change requires following this pipeline, which is split
across [game.py](nfl_projector_v1/game.py) (orchestration) and the `projections/` modules
(logic). `game.py` is deliberately almost-pure orchestration — the modeling lives downstream.

```
load_all() (data/loaders.py)  →  dict of DataFrames from warehouse.duckdb
        │  keys: schedule, vegas, injuries, qb_history, rb_history, recv_history,
        │        pass_defense, rush_defense, recv_defense, snaps, qb_starters
        ▼
project_game(home, away, season, week, data)   game.py
        │  for each team → _project_one_team():
        │    1. get_active_roster()            data/roster.py
        │         roster_mode="snaps" (DEFAULT): WR/RB/FB→RB/TE by FPD snap share;
        │           QB via resolve_qb (qb_starters.yaml → depth chart → injury).
        │         roster_mode="depth_chart": legacy depth chart + activity filter.
        │    2. project QB / RBs / WR-TEs      projections/{qb,rb,wr_te}.py
        │    3. aggregate_to_team()            projections/team.py
        │    4. production_to_points()         projections/points.py
        ▼
GamePrediction  → margin, total, win prob (normal CDF), SU pick;
                  ATS/OU picks derived only if a Vegas line is found
```

### The core projection recipe (every stat goes through this)

Implemented as the shared helpers in [projections/base.py](nfl_projector_v1/projections/base.py),
called by all four position modules:

```
own_estimate = blend_with_baseline(weighted_recent_4_games, season_baseline, n_games)
projected    = own_estimate × opponent_matchup_factor × injury_factor
```

Recent form is the last `RECENT_GAMES_WINDOW` (4) games, weighted `RECENT_WEIGHT` (0.7) against
the season baseline, with shrinkage toward league averages when the sample is thin. Matchup
factors are clamped to `[MATCHUP_FLOOR, MATCHUP_CEILING]` = `[0.85, 1.15]`. Only **two**
adjustments are ever stacked (matchup, injury) — this is an explicit design rule, do not add
more multiplicative blocks (DESIGN.md §5).

### Aggregation decisions that aren't obvious from one file

- **QB-anchored passing.** Team pass yards = the QB's projected pass yards. Receiver yards are
  *allocated* from that total by efficiency-weighted target share (WR/TE projections produce a
  target share, not yards). See [projections/team.py](nfl_projector_v1/projections/team.py).
- **Team rush yards** = sum of RB rush yards + QB scramble yards. Scrambles are tracked on the
  QB projection but counted as team rushing here.
- **Depth-aware rush-volume floor AND ceiling** (team.py). The floor corrects under-counted
  rushing when a starting RB is out (backups have thin histories); the ceiling
  (`RUSH_VOLUME_CEILING_MULT` 1.20) caps summed RB carries at 1.2× the team's recent baseline
  so the more-inclusive snap-share roster doesn't over-project team rush yards. Both anchor team
  carries to the recent baseline — load-bearing (the ceiling fixed a +3pt total over-projection).
- **TDs are not projected per player.** Team yards × a TD-per-yard rate gives implied TDs.
  Default (`DEFAULT_TD_RATES="team"`) is each team's own walk-forward TD-per-yard,
  empirical-Bayes shrunk toward the league rate (`team.py:_team_td_rate`); `--td-rates
  league` is the flat-rate baseline. `points = TDs × 6.95 + FGs × 3 + non-offensive
  baseline + a global calibration` (`POINTS_CALIBRATION_PER_TEAM`, added equally to both
  teams to zero the total under-bias without touching margin/SU/ATS; `--no-calibrate` to
  disable). See DESIGN.md §13.
- **Home-field advantage** (`game.py`). The bottom-up engine has no home/away input, so it
  carried ~zero HFA (predicted home win 48% vs reality 54%). `compute_team_hfa` derives each
  team's walk-forward home margin (mean home-margin − mean away-margin, /2), empirical-Bayes
  shrunk toward `LEAGUE_HFA` (2.0) and clamped. Applied as a **total-preserving margin shift**
  (home `+h/2`, away `−h/2`) so it moves margin/SU/win-prob but leaves the calibrated total
  alone. `DEFAULT_HOME_FIELD="team"` (won the A/B: SU 62.2→63.8); `--home-field none|league`
  to compare. See DESIGN.md §14.
- **Environment / dome** (`game.py:_environment_total_adjust`). A **total-only** nudge from the
  schedule's `dome` flag (DOME +1.0 / OUTDOOR −0.5, mean-centered), split equally between teams
  so it moves O/U but not margin/SU/ATS. Corrects a measured conditional total bias (the model
  under-projects domes). `DEFAULT_ENVIRONMENT="dome"`; `--environment none` to disable. Wind/temp
  are deliberately unused (wind has no live forecast feed; temp is noise). See DESIGN.md §15.
- **Situational ATS overlay** (`situational.py`, DESIGN.md §16). The model never sees the line,
  so its own ATS picks have no edge (~49%). The overlay is a SEPARATE against-the-spread lean —
  it does NOT touch score/margin/SU. Only one signal survived OOS + full-backtest validation:
  the **key-number favorite fade** (favorite laying 7-9.5 → back the dog), ~57.6% on ~39
  plays/season. Stored on `GamePrediction.situational_ats_pick/reason`, shown in `predict`, and
  tracked as `sit_ats_accuracy` in the backtest summary. Division-dog and fade-bye were tested
  and dropped (~46% isolated) — don't re-add them naively.

### Vegas lines

Used **only for grading** ATS/OU picks — they never anchor a projection. `game.py` looks up the
line by nflverse `game_id` (`{season}_{week:02d}_{away}_{home}`); negative `spread_close` means
home is favored. If no line is found, ATS/OU are left `None` and only the SU pick is produced.

## Configuration

All league constants and model knobs live in [config.py](nfl_projector_v1/config.py) — tune the
model there, never inside projection logic. The numbers are empirically derived from the
2023-2025 warehouse (verification queries noted in DESIGN.md §4.1).

Paths auto-discover: `config.py` walks up/sideways from the package looking for
`data/processed/warehouse.duckdb`, so the package works regardless of where it's nested. Outputs
are written to `data/processed/v2/` and depth-chart cache to `data/raw/depth_charts/`.

## Data & known gotchas

- The warehouse (`warehouse.duckdb`) and the `data/` tree are **large and gitignored** — they
  live outside version control. A fresh clone has no data until `build_database.py` runs.
- **`v2` is a misnomer.** This project (`nfl_projector_v1`) *is* the current product; the older
  parked codebase is "v0.5". The `data/processed/v2/` output dir and any "v2" references in
  DESIGN.md are leftover naming from the rebuild — they refer to *this* project.
- **DESIGN.md §3–§4** were reconciled to the shipped code on 2026-05-29 (they now match the
  real modules, CLI commands, constants, and the `ingest/` layer). §5–§11 are the original
  design rationale, risks, and post-build results — accurate but historical (e.g. §5 lists
  design-time decisions; §10 records what actually changed, like the +1.0 non-offensive points
  baseline). There is no `grade.py` or `compare.py` — grading lives in `backtest.py`.
- **Cold-start limitation.** The model needs ~1 full prior season to differentiate teams. The
  first season of any data window performs near coin-flip on SU because projections fall back to
  league averages. Never trust the earliest season in the loaded window (DESIGN.md §10).
- **Roster selection is depth-chart-FIRST, snap-refined** (`DEFAULT_ROSTER_MODE="snaps"`, DESIGN.md
  §12). The **current depth chart sets membership** (who's on the team — fixes the bug where stale
  2021-2025 snap history resurrected departed players on future/opener rosters, e.g. Tyreek Hill on
  2026 KC); **snap%** over each player's last N *active* games sets the depth ORDER and feeds
  projections (a no-snap rookie/FA is seeded by depth-chart slot). Players in old snaps but absent
  from the current chart are dropped. Injury-aware recency + ramp discount + staleness regression
  still apply; the pure snaps-only path (`_roster_from_snaps_only`) is a fallback for when no chart
  exists. QB resolved separately: `qb_starters.yaml` override → depth-chart QB1 → injury fallthrough.
  Snap section columns are `total/rush/pass/gl/i10/rz` (only `total_snap_pct` feeds selection).
  Legacy depth-chart-only path: `backtest --roster-mode depth_chart`.
- **nflverse depth charts** drive roster membership + the QB path (`refresh-depth-charts` applies).
  [data/depth_charts.py](nfl_projector_v1/data/depth_charts.py) handles the 2025 schema change
  (dropped `week` column, near-daily snapshots) by snapping each snapshot to the NFL week and keeping
  the latest per week; for a future/offseason season it serves the latest projected chart — preserve
  that if you touch it.
