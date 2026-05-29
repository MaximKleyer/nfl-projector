# nfl_projector_v1 — Design Document

**Status:** v1 build complete (steps 1-11). Backtested on 2023-2025. CLI operational.
**Goal:** Predict the final score of every scheduled NFL game per week, with ATS and OU picks derived from that prediction. Bottom-up architecture using projected player stat lines aggregated to team production.

> **Naming note:** This project (`nfl_projector_v1`) is the current product going forward. The older codebase it replaced is considered **v0.5** — historical, parked, not used for comparison or development. References to "v2" in early sections of this document are an artifact of the rebuild conversation and refer to *this* project.

> **Code-reconciliation note (updated 2026-05-29):** Sections 3–4 below were rewritten to match the **shipped code**, which diverged from the original pre-build spec. Key corrections: the CLI commands are `predict` / `backtest` / `refresh-depth-charts` / `status` (not `predict-week` / `compare` / `fetch-depth-charts`); there is no standalone `grade.py` or `compare.py` (grading lives inside `backtest.py`); a build-time `ingest/` package (and `scripts/`) that the spec omitted does the warehouse build; and the league constants in §4.1 now reflect the values actually committed in `config.py`. Sections 5–11 are the original design rationale, risks, and post-build results — left intact.

---

## 1. What the model produces

**For each game in the upcoming week:**

```
home_team away_team  predicted_home_score predicted_away_score
KC        BUF        27.4                 24.1
```

Plus derived outputs (computed from the score prediction):

- **Predicted margin** = home_score - away_score
- **Predicted total** = home_score + away_score
- **SU pick** = whichever team's score is higher
- **ATS pick** = which side covers the closing spread (uses Vegas line for grading only)
- **OU pick** = whether predicted total goes over or under
- **Win probability** = derived from margin and NFL margin std dev (~13.5)

Output formats: console table for human reading, CSV for downstream use.

---

## 2. Architecture overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                     INPUTS (from warehouse)                         │
│  Player game logs · Defense game logs · Schedule · Vegas · Injuries │
│                          + nflverse depth charts                    │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│  STEP 1 — Identify the active roster for each team                  │
│           Depth chart filter × recent-activity filter (last 4 weeks)│
└──────────────────────────────┬──────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│  STEP 2 — Project each player's stat line for this week             │
│           QB: pass yards, scramble yards, sacks, INTs               │
│           RB: rush yards, target share                              │
│           WR/TE: target share                                       │
│           Each stat: weighted recent × matchup × injury             │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│  STEP 3 — Aggregate to team production                              │
│           Team pass yards = QB's projected pass yards (the anchor)  │
│           Allocate pass yards to receivers via target share         │
│           Team rush yards = sum of RB rushes + QB scrambles         │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│  STEP 4 — Convert team production to points                         │
│           TDs = (pass_yds × league pass TD/yd) + (rush_yds × ...)   │
│           FGs = team rolling FGs/game                               │
│           team_points = (TDs × 6.95) + (FGs × 3)                    │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│  STEP 5 — Game prediction                                           │
│           Run steps 1-4 for both teams                              │
│           Output (home_score, away_score) and derived metrics       │
└─────────────────────────────────────────────────────────────────────┘
```

> **Code note:** STEP 4 in the shipped code also adds a flat **+1.0 non-offensive
> points/team** (DST / ST) and uses a flat league-average FG rate (2.0/game). The
> `team_points` line above shows the conceptual flow; the exact formula is in §4.10.

---

## 3. Directory structure

```
nfl_projector_v1/                 # repo root (git, pyproject, .venv)
├── README.md
├── pyproject.toml
├── DESIGN.md                     # this document
├── check_dc.py                   # standalone depth-chart sanity script
├── nfl_projector_v1/             # the importable package
│   ├── __init__.py
│   ├── __main__.py               # entry point: python -m nfl_projector_v1 ...
│   ├── cli.py                    # 4 subcommands: predict / backtest /
│   │                             #   refresh-depth-charts / status
│   ├── config.py                 # league constants, model knobs, path discovery
│   ├── utils.py                  # name/team normalization, week-label parsing
│   ├── game.py                   # project_game(home, away, season, week, data)
│   ├── backtest.py               # walk_forward_backtest + grading + CSV output
│   ├── ingest/                   # raw CSV → DuckDB warehouse (BUILD-TIME)
│   │   ├── schemas.py            # REPORTS: per-file column schemas
│   │   ├── readers.py            # read_report(): flat + wide-section CSV readers
│   │   └── database.py           # build_warehouse(): discover weeks, load tables
│   ├── data/                     # warehouse → DataFrames (RUN-TIME)
│   │   ├── loaders.py            # load_all() and per-table loaders
│   │   ├── depth_charts.py       # fetch/cache nflverse + get_depth_chart()
│   │   └── roster.py             # get_active_roster() + Player dataclass
│   └── projections/
│       ├── base.py               # 4 shared helpers (weighted avg, blend,
│       │                         #   opponent factor, injury factor)
│       ├── qb.py                 # project_qb_line() → QBProjection
│       ├── rb.py                 # project_rb_line() → RBProjection (rush + recv)
│       ├── wr_te.py              # project_receiver_line() → ReceiverProjection
│       ├── team.py               # aggregate_to_team() → TeamProduction
│       └── points.py             # production_to_points()
└── scripts/
    ├── build_database.py         # CLI wrapper around ingest.build_warehouse
    ├── fetch_external_data.py    # pull schedule / vegas / injuries
    ├── rename_csvs.py            # normalize raw FPD CSV filenames
    ├── incorporate_2021_2022.py  # backfill older seasons (see §11 #1)
    └── update_receiving_with_rb.py  # one-off: add RBs to receiving table
```

The package is deliberately small and dependency-light (pandas / numpy / duckdb /
scipy / pyyaml). All model logic is plain functions — there is no ML training step.

---

## 4. Module-by-module spec

### 4.1 `config.py`

Constants and league baselines. No logic, just values.

```python
# League scoring conversions (empirically verified, 2023-2025 weeks 1-18)
LEAGUE_PASS_TD_PER_YARD = 0.0062   # 2352 pass TDs / 377,786 pass yds
LEAGUE_RUSH_TD_PER_YARD = 0.0072   # 1098 rush TDs / 151,868 rush yds
POINTS_PER_TD_WITH_PAT  = 6.95     # 6 + 0.95 PAT (missed XPs / 2pt average out)
POINTS_PER_FG           = 3.0
NFL_MARGIN_STD_DEV      = 14.34    # std of (home-away); → win prob via normal CDF
LEAGUE_AVG_TEAM_PPG     = 22.56    # sanity-check only, not used in projection

# League position baselines (fallback when a player has no usable history)
LEAGUE_AVG_QB_PASS_ATTEMPTS = 32.7
LEAGUE_AVG_QB_COMP_PCT      = 64.9
LEAGUE_AVG_QB_YPA           = 7.14
LEAGUE_AVG_QB_SCRAMBLES     = 1.9
LEAGUE_AVG_RB_CARRIES_STARTER = 15.7
LEAGUE_AVG_RB_YPC             = 4.35
LEAGUE_AVG_RECV_YPT         = 8.03    # yards per target
LEAGUE_AVG_RECV_YPR         = 12.10   # yards per reception
LEAGUE_AVG_RECV_CATCH_RATE  = 68.1    # percent
LEAGUE_AVG_NON_OFFENSIVE_POINTS_PER_TEAM = 1.0   # DST + ST TDs + safeties
LEAGUE_AVG_TEAM_FGS_PER_GAME = 2.0

# Model knobs
RECENT_GAMES_WINDOW = 4      # "recent form" window
RECENT_WEIGHT       = 0.7    # weight on recent vs season baseline (when sample full)
MATCHUP_FLOOR       = 0.85   # opponent-factor caps (tight on purpose — wider
MATCHUP_CEILING     = 1.15   #   caps overshot at the extremes in testing)
INJURY_MULTIPLIERS  = {"Out": 0.0, "IR": 0.0, "Doubtful": 0.30,
                       "Questionable": 0.85}   # anything else → 1.0
RECEIVER_YARDS_CAP  = 280.0  # sanity cap on allocated single-game receiving yds

# Paths — auto-discovered by walking up/sideways for data/processed/warehouse.duckdb
DEFAULT_WAREHOUSE_PATH  = <discovered>/data/processed/warehouse.duckdb
DEFAULT_OUTPUT_DIR      = <root>/data/processed/v2
DEFAULT_DEPTH_CHART_DIR = <root>/data/raw/depth_charts
```

### 4.2 `data/loaders.py`

Pure data access. No logic. Functions return clean DataFrames from the existing warehouse.

```python
def open_warehouse(path=None) -> duckdb connection   # read-only

# nflverse tables
def load_schedule(con) -> pd.DataFrame
def load_vegas(con)    -> pd.DataFrame | None
def load_injuries(con) -> pd.DataFrame | None

# FPD player game logs (one row per player-game)
def load_qb_history(con)       -> pd.DataFrame   # advanced_passing_player
def load_rb_history(con)       -> pd.DataFrame   # advanced_rushing_player (all rushers)
def load_receiver_history(con) -> pd.DataFrame   # advanced_receiving_player (WR/TE/RB)

# defense game logs
def load_pass_defense(con) -> pd.DataFrame   # advanced_passing_def
def load_rush_defense(con) -> pd.DataFrame   # advanced_rushing_def
def load_recv_defense(con) -> pd.DataFrame   # advanced_receiving_def

# One-shot convenience used by game.py / backtest.py:
def load_all(warehouse_path=None) -> dict
#   keys: schedule, vegas, injuries, qb_history, rb_history, recv_history,
#         pass_defense, rush_defense, recv_defense
```

### 4.3 `data/depth_charts.py`

Pulls weekly depth charts from nflverse. Caches per (season, week) to avoid re-downloading.

```python
def fetch_depth_charts_season(season, refresh=False) -> pd.DataFrame:
    """Download (or read cached) the full nflverse depth-chart CSV for a season.
    Cached at data/raw/depth_charts/depth_charts_{season}.csv."""

def get_depth_chart(season, week, schedule=None) -> pd.DataFrame:
    """Normalized depth chart for ONE (season, week):
        columns = season, week, team, player_name, position, depth_order
    Handles BOTH nflverse schemas:
      - legacy 2023-2024 (club_code / full_name / depth_team / week)
      - new 2025+ (team / player_name / pos_abb / pos_rank / dt, NO week column).
    For 2025+, snapshot dates are snapped to NFL weeks via `schedule`, keeping the
    LATEST snapshot per (season, week, team). Pass `schedule` for 2025+ — without
    it, week numbering falls back to a crude (often wrong) heuristic. The
    normalized full season is cached at module level for cheap per-week reads."""
```

### 4.4 `data/roster.py`

Combines depth chart with recent activity to identify the projection roster.

```python
@dataclass
class Player:
    name: str
    team: str
    position: str        # 'QB' | 'RB' | 'WR' | 'TE'
    depth_order: int     # 1 = starter, 2 = backup, ...
    injury_status: str | None = None

# Per-position depth limits actually projected (ROSTER_DEPTH_LIMITS):
#   QB 1, RB 3, WR 4, TE 2

def get_active_roster(
    team, season, week,
    depth_chart,                       # normalized output of get_depth_chart()
    qb_history, rb_history, recv_history,
    injuries_df=None,
    enforce_activity_filter=True,      # set False for week 1 / no history
) -> list[Player]:
    """1. Slice the depth chart to `team`; take top-N per position.
       2. Drop players whose injury report_status is Out/IR.
       3. If enforce_activity_filter: drop players with no touches in the last
          ~4 weeks (prior season also counts in weeks <= 4). This catches IR
          players who stopped appearing on weekly injury reports.
       Returns Player objects sorted by position then depth_order."""
    ...
```

The activity filter is what keeps depth-chart-listed-but-not-actually-playing
players (practice squad, quietly-injured) out of the projection.

### 4.5 `projections/base.py`

Shared helpers used by all position-specific projection modules.

```python
def weighted_recent_average(games, stat, n_recent=4, decay=1.0) -> float | None:
    """Mean of `stat` over the player's last n_recent games. Caller must pre-filter
    to ONE player and sort oldest→newest. decay>1 weights recent games more."""
    ...

def blend_with_baseline(recent_value, season_baseline, n_recent_games,
                        recent_weight=RECENT_WEIGHT, league_baseline=None,
                        min_recent_games=2) -> float | None:
    """Bayesian-style blend. The recent weight is scaled by sample completeness
    (n_recent_games / 4), so a 2-game sample leans toward the season baseline.
    Falls back recent → season → league → None."""
    ...

def opponent_factor(opp_recent_allowed, league_avg_allowed,
                    floor=MATCHUP_FLOOR, ceiling=MATCHUP_CEILING) -> float:
    """opp_recent_allowed / league_avg_allowed, clamped to [0.85, 1.15].
    Returns 1.0 if either input is missing."""
    ...

def injury_factor(player_name, team, season, week, injuries_df) -> float:
    """Look up report_status in the injuries table → INJURY_MULTIPLIERS.
    Unmatched / unknown status → 1.0 (treat as healthy — safer than zeroing)."""
    ...
```

### 4.6 `projections/qb.py`

```python
@dataclass
class QBProjection:
    name: str; team: str; opponent: str
    pass_attempts: float
    completions: float
    completion_pct: float
    ypa: float
    pass_yards: float            # pass_attempts × ypa — the team passing anchor
    interceptions: float
    sack_count: float
    scramble_yards: float        # tracked SEPARATELY; team.py folds it into rushing
    # diagnostics:
    n_recent_games: int
    health_multiplier: float
    matchup_multiplier_yards: float


def project_qb_line(qb: Player, opponent, season, week, qb_history,
                    pass_defense=None, injuries_df=None) -> QBProjection:
    """Project each base stat (pass_att, cmp_pct, ypa, scrambles, ints, sacks) via
    blend(weighted_recent, season) with league fallback, then apply the matchup +
    injury multipliers. History is filtered STRICTLY before (season, week) —
    walk-forward correct. pass_yards = attempts × ypa. Per design, the yards
    matchup uses ypa-allowed only (attempts are game-script driven, not defense).
    TDs are NOT projected here — derived at team level (see points.py)."""
    ...
```

### 4.7 `projections/rb.py`

```python
@dataclass
class RBProjection:
    name: str; team: str; opponent: str
    carries: float; ypc: float; rush_yards: float
    # receiving (populated when recv_history is passed AND the RB has rec history;
    #   receiving_yards/receptions are filled later by team.py allocation):
    target_share: float = 0.0; ypt: float = 0.0; catch_rate: float = 0.0
    receiving_yards: float = 0.0; receptions: float = 0.0
    depth_order: int = 1          # used by team.py for depth-aware volume scaling
    # diagnostics: n_recent_games, health_multiplier, matchup_multiplier


def project_rb_line(rb: Player, opponent, season, week, rb_history,
                    rush_defense=None, injuries_df=None, recv_history=None) -> RBProjection:
    """Rushing: carries (depth-scaled baseline — backups get ~45%, third-string
    ~18% of a starter's load) and ypc via the standard recipe; matchup hits ypc.
    Receiving: if recv_history is given and the RB has RB-position receiving rows,
    project target_share / ypt / catch_rate (receiving yards filled by team.py).
    The RB receiving fields exist because the receiving table was later updated to
    include RBs — see scripts/update_receiving_with_rb.py and §4.16."""
    ...
```

### 4.8 `projections/wr_te.py`

```python
@dataclass
class ReceiverProjection:
    name: str; team: str; opponent: str
    position: str               # 'WR' | 'TE'
    target_share: float         # PERCENT of team targets (e.g. 22.5)
    ypt: float                  # used as the allocation weight in team.py
    catch_rate: float
    receiving_yards: float = 0.0   # filled by team.py allocation
    receptions: float = 0.0
    # diagnostics: n_recent_games, health_multiplier, matchup_multiplier


def project_receiver_line(receiver: Player, opponent, season, week, recv_history,
                          recv_defense=None, injuries_df=None) -> ReceiverProjection:
    """Project target_share, ypt, catch_rate (NOT yards). Depth/position-based
    target-share fallbacks (WR1 22%, WR2 16%, slot 11%, TE1 14%, ...). Matchup
    adjusts ypt (vs def_yprr); target_share is treated as neutral to defense.
    Receiving YARDS are allocated later in team.py from the QB's pass-yard total,
    so receivers' yards always sum to the QB anchor."""
    ...
```

### 4.9 `projections/team.py`

The aggregation logic. Brings together all player projections into team-level numbers.

```python
@dataclass
class TeamProduction:
    team: str; opponent: str
    pass_yards: float            # = QB pass_yards (anchor)
    rush_yards: float            # = Σ RB rush_yards + QB scramble_yards
    total_yards: float
    pass_tds_implied: float; rush_tds_implied: float; total_tds_implied: float
    field_goals: float           # flat league avg in v1 (placeholder)
    # retained for interpretability: qb_projection, rb_projections,
    #   receiver_projections; plus diagnostics (n_receivers, rush_volume_scaling)


def aggregate_to_team(qb_proj, rb_projs, receiver_projs, team, opponent,
                      season, week, schedule_df=None, rb_history=None) -> TeamProduction:
    """MUTATES receiving_yards/receptions on the projections passed in.
      1. team_pass_yards = qb_proj.pass_yards.
      2. Allocate pass yards across the WR+TE+RB pool by efficiency-weighted
         share (normalized target_share × ypt), capped at RECEIVER_YARDS_CAP.
      3. Depth-aware rush-volume floor: if Σ projected carries < 90% of the team's
         recent carries/game, redistribute the deficit up to per-depth caps
         (d1 22 / d2 16 / d3 12 / d4+ 8) and apply a 3.5 YPC floor to any backup
         whose volume got promoted. (Fixes backup-RB under-projection — §10 #5.)
      4. team_rush_yards = Σ RB rush + QB scrambles.
      5. implied TDs = yards × league TD-per-yard; FGs = league avg (placeholder)."""
    ...
```

### 4.10 `projections/points.py`

The TD-and-FG-to-points conversion.

```python
def production_to_points(production: TeamProduction) -> float:
    """points = total_tds_implied × 6.95
             + field_goals × 3.0
             + 1.0   (LEAGUE_AVG_NON_OFFENSIVE_POINTS_PER_TEAM — DST/ST baseline)

    The flat +1.0 non-offensive baseline was added POST-BUILD to close a
    systematic total-points under-bias (originally this returned only TD + FG
    points). See §10 finding #4."""
    ...
```

### 4.11 `game.py`

The orchestrator. One function call → one game prediction.

```python
@dataclass
class GamePrediction:
    home_team: str; away_team: str; season: int; week: int
    predicted_home_score: float; predicted_away_score: float
    predicted_margin: float       # home - away
    predicted_total: float
    su_pick: str
    win_prob_home: float; win_prob_away: float
    # Vegas-derived (None if no line found for this game_id):
    spread_close: float | None; total_close: float | None
    ats_pick: str | None; ats_prob: float | None
    ou_pick: str | None;  ou_prob: float | None
    home_production: TeamProduction | None
    away_production: TeamProduction | None


def project_game(home_team, away_team, season, week, data: dict,
                 enforce_activity_filter=True) -> GamePrediction:
    """`data` is the dict from loaders.load_all(). For each team: build roster →
    project QB / RBs / receivers → aggregate_to_team → production_to_points. Then
    margin/total, win prob (normal CDF, σ≈14.34), SU pick. If a Vegas line is
    found (game_id '{season}_{week:02d}_{away}_{home}', negative spread = home
    favored), derive ATS + O/U picks — Vegas is used only here, never to anchor.

    NOTE: the signature is (home, away, season, week, data) — a single data dict,
    not ten DataFrame arguments. game.py is pure orchestration; the projection
    modules each filter `data` to the rows they need."""
    ...
```

### 4.12 Grading (lives in `backtest.py`, not a separate module)

There is no standalone `grade.py`. Grading is done by private helpers inside
`backtest.py`:

```python
def _grade_prediction(pred, actual_home_score, actual_away_score) -> dict:
    """One residuals row: margin/total error, su_correct, ats_correct (+ats_push),
    ou_correct (+ou_push). Ties and pushes are excluded from accuracy, not blamed."""
    ...

def _summarize(residuals, label="all") -> dict:
    """Aggregate to margin/total MAE + RMSE and SU / ATS / O-U accuracy (with
    per-metric game counts)."""
    ...
```

### 4.13 `backtest.py`

The walk-forward harness for v2.

```python
@dataclass
class BacktestResult:
    residuals: pd.DataFrame          # one row per graded game
    summary: pd.DataFrame            # overall metrics
    summary_by_season: pd.DataFrame  # per-season breakdown


def walk_forward_backtest(seasons, min_week=1, max_week=18, data=None,
                          warehouse_path=None, verbose=True) -> BacktestResult:
    """Iterate every COMPLETED game in the window and call project_game (which
    enforces walk-forward history filtering internally — no leakage), then grade
    vs the actual scores. Loads data via load_all() unless a pre-loaded `data`
    dict is passed (cheaper for repeated runs)."""
    ...


def write_backtest_outputs(result, out_dir=None, suffix="") -> dict[str, Path]:
    """Write CSVs to DEFAULT_OUTPUT_DIR (data/processed/v2/):
        backtest_residuals{suffix}.csv
        backtest_summary{suffix}.csv
        backtest_summary_by_season{suffix}.csv"""
    ...
```

### 4.14 `compare.py` — not implemented

The original spec planned a `compare.py` for a side-by-side table against the old
v0.5 model. It was never built, and there is no `compare` CLI command. The old
v0.5 backtest residuals still live at
`data/processed/team_backtest_residuals.parquet` if a manual comparison is ever
wanted; the cross-time team-comparison idea is tracked as deferred work in §11 #2.

### 4.15 `cli.py`

The CLI commands. Mirrors v1's structure but with fewer commands (no calibrate, no season-sim, no team-backtest variants, no track-results for v1 — all deferred).

```
python -m nfl_projector_v1 status
python -m nfl_projector_v1 predict --season 2025 --week 16
python -m nfl_projector_v1 backtest --seasons 2024 2025 [--min-week N --max-week N --suffix S]
python -m nfl_projector_v1 refresh-depth-charts --seasons 2025
```

---

### 4.16 Ingestion: `ingest/` package + `scripts/`

The original spec omitted the build-time ingestion layer. The warehouse is built
*offline* (not during prediction) from raw FPD CSVs:

- `ingest/schemas.py` — `REPORTS`: per-file column schemas (which raw columns map
  to canonical names; which files are flat vs "wide" multi-section reports).
- `ingest/readers.py` — `read_report()`: turns a raw FPD CSV into a clean
  long-format DataFrame. Handles wide reports that repeat column groups across
  TOTAL / MAN / ZONE / SHELL sections. Emits season, week_label, week_num,
  week_type and a player_key / team_key.
- `ingest/database.py` — `build_warehouse()`: discovers weeks under
  `data/raw/<season>/week_NN/`, reads each report, and writes the DuckDB tables
  the loaders read (schedule, vegas, injuries, advanced_passing_player,
  advanced_rushing_player, advanced_receiving_player,
  advanced_{passing,rushing,receiving}_def). Missing files are skipped with a
  warning, not an error — the model degrades gracefully.
- `utils.py` — `normalize_name`, `normalize_team`, `player_key`,
  `label_to_week_number` (shared by the readers).

Build pipeline (run in order, only when rebuilding the warehouse):

```
scripts/rename_csvs.py                                # normalize raw FPD filenames
scripts/fetch_external_data.py                        # pull schedule / vegas / injuries
scripts/build_database.py --seasons 2023 2024 2025    # → warehouse.duckdb
```

`scripts/incorporate_2021_2022.py` backfills the older seasons (§11 #1);
`scripts/update_receiving_with_rb.py` was the one-off that added RB rows to the
receiving table (which is why `RBProjection` now carries receiving stats).

---

## 5. Key design choices, explicit

### Choices made and locked

1. **Bottom-up architecture** — players first, team is the sum
2. **QB-anchored passing yards** — receiver yards allocate from QB total
3. **TD-per-yard conversion** — league-average rates, not per-player TD projection
4. **Team FGs** — shipped as a flat league average (2.0/game); the per-team rolling version is a stubbed placeholder in `team.py`, kicker variance deferred to v2+
5. **No multiplicative blocks** — each player stat: blended baseline × opp factor × injury factor (just two adjustments, no stacking of 4-5)
6. **No fantasy points in the model** — production is yards/TDs, never converted to FP internally
7. **Vegas used only for grading** — not for anchoring projections
8. **Walk-forward backtest with all weeks** — weeks 1-3 noisier but included
9. **Hybrid depth-chart roster** — nflverse depth chart + recent-activity filter
10. **2023-2025 only for v1** — 2021/2022 deferred

### Things explicitly NOT in v1

- Calibrator
- Season simulator
- Snap share data
- Kicker-level data
- DST / special teams TDs as modeled events (a flat **+1.0 non-offensive
  points/team** baseline *was* added post-build — see §10 finding #4)
- 2pt conversions / safeties (use league average constant)
- Coverage scheme adjustments
- SOS adjustment
- Multiple model variants
- Fantasy projections as a primary output

If/when any of these prove necessary after v1 backtests, they're easy to add.

---

## 6. Backtest protocol (explicit)

```
For each test_season in [2023, 2024, 2025]:
    For each week in 1..18:
        For each game with completed scores:
            # All data filtered to STRICTLY before (season, week)
            data = filter_history(season, week)
            
            # Run prediction
            prediction = project_game(home, away, season, week, **data)
            
            # Grade
            grade = grade_prediction(prediction, actual_home, actual_away)
            
            # Record
            residuals.append(grade)

Aggregate: residuals → summary table (MAE, SU acc, ATS acc, OU acc)
```

Output (CSV) to `data/processed/v2/`: `backtest_residuals.csv`, `backtest_summary.csv`,
and `backtest_summary_by_season.csv` (an optional `--suffix` is appended to each name).

---

## 7. Build order

If you approve this design, here's the order I'd build in. Each step is testable independently. After each step we pause, verify, then proceed.

1. **Set up directory + config + data loaders** (foundation, no logic)
2. **Build `projections/base.py` helpers** (small, easy to verify with synthetic input)
3. **Build `data/roster.py` + `data/depth_charts.py`** (verify roster selection makes sense on a known week)
4. **Build `projections/qb.py`** (verify on a known QB — Mahomes 2024 week 14)
5. **Build `projections/rb.py` and `projections/wr_te.py`** (same pattern)
6. **Build `projections/team.py` and `projections/points.py`** (verify aggregation math on a known team-game)
7. **Build `game.py`** (verify on a known game — KC vs LAC week 14 2024)
8. **Build `backtest.py`** (run on a small slice first, e.g., 2024 week 14-15)
9. **Run full backtest, 2023-2025**
10. **Build `compare.py`, run side-by-side vs v1**
11. **Wire `cli.py`, write README**

Estimated time across all steps: probably 5-8 messages of building + debugging. Each step is its own message with summary at the end.

---

## 8. Risks I want to flag

1. **Target share volatility.** WR target shares fluctuate week to week. Even with 4-game windows, the variance is high. This could cause receiver projections to be noisy. Mitigation: blend heavily toward season baseline if recent sample shows extreme values.

2. **QB injury cascades.** When Mahomes goes out and Carson Wentz starts, Wentz has limited recent data with KC. The model might fall back to league baseline and underpredict KC's offense (or overpredict if league baseline is generous). Mitigation: pull Wentz's stats from his prior team, document this is a known weakness.

3. **TD rate is league-average.** Teams in the red zone often differ in TD conversion rates. v1 ignores this. Will likely produce slight bias for high-RZ-conversion teams (PHI, BAL) being underpredicted.

4. **FG variance.** Some teams (those that drive but don't punch in) have outlier FG totals. Rolling avg smooths this but can lag for trending teams.

5. **Bottom-up models can underpredict offensive blowups.** When a team has a 35-point game it's often driven by 1-2 explosive plays. Player-level rolling averages don't capture that variance well.

6. **Will likely match (not beat) v1 on aggregate MAE.** Restated from our earlier conversation. The win is interpretability and personnel handling, not necessarily raw accuracy.

---

## 9. Working agreement (reaffirmed)

- **Explain → build → summarize** for every change
- I won't add features you didn't ask for
- After each step, you can ask "walk me through this" and I'll explain line-by-line
- If something doesn't match this design, I tell you first before deviating
- You can stop at any step if the direction isn't right

---

## 10. Results & findings (post-build)

Backtested walk-forward over 2023-2025 (816 games), and over 2024-2025 (544 games) as the "fair" benchmark with prior-season history available.

### Benchmark — 2024+2025 (the honest number)

| Metric | Value |
|--------|-------|
| Margin MAE | 10.79 |
| Margin RMSE | 14.07 |
| Total MAE | 10.78 |
| SU accuracy | 58.8% |
| ATS accuracy | 49.3% |
| O/U accuracy | 47.3% |
| Total bias | ~-1.8 points/game (slightly under) |

2025 alone, after the depth-chart week-mapping fix: SU 58.3%, **ATS 53.9%**, margin MAE 10.82.

### Key findings

1. **Cold-start problem (most important).** The model needs ~1 full prior season of data to differentiate teams. 2023 (our first data year, no 2022 priors) performs at ~52-53% SU — essentially coin flip — because early-season player projections fall back to league averages and every team looks alike. 2024 and 2025, which have prior-season history, perform much better (59% and 58% SU). **Implication:** never trust the first season of any data window. Ingesting 2021-2022 (see Future Enhancements) would fix 2023.

2. **Matches, doesn't beat, v0.5 on raw accuracy.** As predicted in Risk #6, margin MAE (~10.8) is in the same range as the old model (~10.5). The win was interpretability and personnel-change handling, not raw error reduction.

3. **TD-per-yard rates were correct.** Empirically verified: pass 0.00606 (we used 0.0062), rush 0.00722 (we used 0.0072). No tuning needed. The early systematic under-projection of totals (-5 pts/game) was NOT a TD-conversion problem.

4. **The -5 ppg total bias came from omissions, not conversion.** Adding a DST/ST scoring baseline (1.0 pt/team) and bumping the FG estimate (1.8 → 2.0) closed the bias from -5.0 to -1.8 ppg without touching margin or SU. The remaining ~1.8 is likely conservative yardage projection on backup-heavy rosters.

5. **Depth-aware rush volume floor matters.** When a starting RB is missing, naive per-player projection under-counts team rushing badly (backups have thin, garbage-time histories). The depth-capped volume floor + 3.5 YPC floor (team.py) corrects this — e.g. LAC W14 2024 went from a nonsensical 32 rush yards to a realistic 81, cutting that game's margin error from 4.5 to 2.0.

6. **nflverse changed depth-chart schemas in 2025.** The 2025 format dropped the `week` column and publishes near-daily snapshots (193 unique dates). Naively numbering them broke late-season roster lookups. Fixed by snapping each snapshot date to the correct NFL week via the schedule, keeping the latest snapshot per week. This fix alone bumped 2025 ATS from 50.4% to 53.9% — roster freshness matters most for spread picks.

---

## 11. Future enhancements (v2 / later)

In rough priority order:

1. **Ingest 2021-2022 data.** Highest-impact change. Would give 2023 a proper prior-season baseline and likely lift 2023 SU from ~53% to ~58%. Purely mechanical: same FPD CSV structure, re-run `build_database.py`. The 2022 depth charts use the legacy schema (no `dt` complication).

2. **Cross-time team comparison.** A `compare` command to pit any two team-weeks against each other (e.g. "Week 14 2024 KC vs Week 10 2025 CLE"), projecting each against a neutral/league-average defense, then matching the two production profiles. Pure novelty/analysis feature.

3. **Game-script awareness.** The biggest accuracy limitation. Backup QBs with inflated garbage-time stats (e.g. Winston 2024) over-project because the model can't tell "350 yards while trailing by 21" from "350 yards in a competitive game." Would need a way to down-weight stats accumulated in blowouts.

4. **Team-specific FG rates.** Currently a flat league average (2.0). Teams that drive-but-stall (kick more FGs) vs teams that punch it in differ meaningfully. Requires FG data not currently in the warehouse.

5. **Red-zone TD conversion rates.** Per-team RZ efficiency would replace the league-average TD-per-yard conversion, helping high-conversion teams (PHI, BAL) that we currently under-project.

6. **QB cross-team history.** When a QB changes teams (e.g. Wilson DEN → PIT), we currently use his recent games regardless of team context. Could weight by scheme/team fit.

## Ingestion notes

- **Washington 2021:** the v0.5 ingestion package (`nfl_projector/utils.py`,
  outside this repo) requires "Washington Football Team" and "Football Team"
  mapped to WAS. The 2021 franchise predates the Commanders rebrand. Applied
  manually to the v0.5 team-normalizer; re-apply if rebuilding ingestion from
  scratch.
- **2021-2022 backtest result:** ingesting 2021-2022 lifted 2023 SU 52.6%?54.4%
  and improved 2024/2025 modestly (+1-1.5 pts each) via more stable baselines.
  Overall SU 56.1%?58.2%, margin MAE ~unchanged (10.98?10.93).
