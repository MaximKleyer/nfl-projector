# nfl_projector_v1 — Design Document

**Status:** v1 build complete (steps 1-11). Backtested on 2023-2025. CLI operational.
**Goal:** Predict the final score of every scheduled NFL game per week, with ATS and OU picks derived from that prediction. Bottom-up architecture using projected player stat lines aggregated to team production.

> **Naming note:** This project (`nfl_projector_v1`) is the current product going forward. The older codebase it replaced is considered **v0.5** — historical, parked, not used for comparison or development. References to "v2" in early sections of this document are an artifact of the rebuild conversation and refer to *this* project.

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

---

## 3. Directory structure

```
nfl_projector_v1/
├── README.md                  # User-facing docs
├── pyproject.toml             # Package metadata, dependencies
├── DESIGN.md                  # This document
├── nfl_projector_v1/
│   ├── __init__.py
│   ├── __main__.py            # Entry point: python -m nfl_projector_v1 ...
│   ├── cli.py                 # CLI commands wiring
│   ├── config.py              # League constants, paths, settings
│   ├── data/
│   │   ├── __init__.py
│   │   ├── loaders.py         # Load DataFrames from warehouse
│   │   ├── depth_charts.py    # Pull + cache nflverse depth charts
│   │   └── roster.py          # Determine active roster for a team-week
│   ├── projections/
│   │   ├── __init__.py
│   │   ├── base.py            # Shared helpers (weighted avg, matchup factor)
│   │   ├── qb.py              # project_qb_line(qb, opp, week, history)
│   │   ├── rb.py              # project_rb_line(rb, opp, week, history)
│   │   ├── wr_te.py           # project_receiver_line(...)
│   │   ├── team.py            # aggregate_to_team(...)
│   │   └── points.py          # production_to_points(...)
│   ├── game.py                # project_game(home, away, week, ...) → GamePrediction
│   ├── grade.py               # Grade predictions vs actuals + Vegas
│   ├── backtest.py            # Walk-forward backtest
│   └── compare.py             # NEW: side-by-side v1-vs-v2 comparison
└── scripts/
    └── fetch_depth_charts.py  # One-off pull of nflverse depth charts
```

**Total estimated size:** ~1500 lines across ~15 files. About a third of the current v1's size (which is ~4500 lines).

---

## 4. Module-by-module spec

### 4.1 `config.py`

Constants and league baselines. No logic, just values.

```python
# League constants (computed empirically from 2023-2025 in the warehouse;
# we'll verify these before hard-coding)
LEAGUE_PASS_TD_PER_YARD = 0.0085   # ~2.7 pass TDs per 320 yds
LEAGUE_RUSH_TD_PER_YARD = 0.012    # ~1.2 rush TDs per 100 yds
POINTS_PER_TD_WITH_PAT  = 6.95     # 6 + 0.95 PAT (league kicker accuracy ~95%)
POINTS_PER_FG           = 3.0
NFL_MARGIN_STD_DEV      = 13.5     # for win probability conversion

# League averages for fallback when player has no history
LEAGUE_AVG_QB_PASS_ATTEMPTS    = 32
LEAGUE_AVG_QB_YPA              = 7.2
LEAGUE_AVG_QB_COMP_PCT         = 64.0
LEAGUE_AVG_RB_CARRIES_STARTER  = 14
LEAGUE_AVG_RB_YPC              = 4.4
LEAGUE_AVG_TARGETS_WR1         = 8.5
LEAGUE_AVG_YPC_RECV            = 11.5
LEAGUE_AVG_TEAM_FGS_PER_GAME   = 1.8

# Model knobs
RECENT_GAMES_WINDOW = 4   # "last 4 games" heavy weight
RECENT_WEIGHT       = 0.7 # 70% recent, 30% season baseline
INJURY_MULTIPLIERS  = {"Questionable": 0.85, "Doubtful": 0.30,
                       "Out": 0.0, "IR": 0.0}

# Paths
DEFAULT_WAREHOUSE = "data/processed/warehouse.duckdb"  # reuses v1's warehouse
DEFAULT_OUTPUTS   = "data/processed/v2"
```

### 4.2 `data/loaders.py`

Pure data access. No logic. Functions return clean DataFrames from the existing warehouse.

```python
def load_schedule(con) -> pd.DataFrame: ...
def load_vegas(con) -> pd.DataFrame: ...
def load_injuries(con) -> pd.DataFrame: ...
def load_player_history(con, position: str) -> pd.DataFrame: ...
def load_defense_stats(con, side: str) -> pd.DataFrame: ...
   # side ∈ {"pass", "rush", "recv"}
```

### 4.3 `data/depth_charts.py`

Pulls weekly depth charts from nflverse. Caches per (season, week) to avoid re-downloading.

```python
def fetch_depth_chart(season: int, week: int) -> pd.DataFrame:
    """
    Returns: player_name, team, position, depth_order
    Cached at data/raw/depth_charts/{season}_{week}.csv
    """
    ...
```

### 4.4 `data/roster.py`

Combines depth chart with recent activity to identify the projection roster.

```python
def get_active_roster(
    team: str,
    season: int,
    week: int,
    depth_chart: pd.DataFrame,
    player_history: pd.DataFrame,
    injuries: pd.DataFrame,
) -> list[Player]:
    """
    Returns the list of players to project for this team-week.
    
    Logic:
      1. Start with depth chart for (team, season, week)
      2. Filter to QB / RB / WR / TE (offense only)
      3. Drop players marked 'Out' or 'IR' in injury data
      4. For each remaining player, verify they had touches in the
         last 4 games (drops players listed but inactive)
      5. Return list with each player tagged starter/backup based on
         depth order
    """
    ...
```

A `Player` is a lightweight dataclass: `{name, position, depth, status}`.

### 4.5 `projections/base.py`

Shared helpers used by all position-specific projection modules.

```python
def weighted_recent_average(
    games: pd.DataFrame,
    stat: str,
    n_recent: int = 4,
    decay: float = 1.0,
) -> float:
    """Weighted mean of `stat` over last n games. decay=1.0 means equal weight."""
    ...

def blend_with_baseline(
    recent_value: float,
    season_baseline: float,
    n_recent_games: int,
    recent_weight: float = 0.7,
) -> float:
    """Bayesian-style blend.
    If recent sample is small, season baseline dominates.
    If recent sample is full (4 games), recent_value gets the heavier weight.
    """
    ...

def opponent_factor(
    opponent_recent_allowed: float,
    league_avg_allowed: float,
    floor: float = 0.85,
    ceiling: float = 1.15,
) -> float:
    """Matchup multiplier from opponent vs-position stats."""
    ...

def injury_factor(player_name: str, team: str, season: int, week: int,
                  injuries_df: pd.DataFrame) -> float:
    """Look up player's injury status, return multiplier from INJURY_MULTIPLIERS."""
    ...
```

### 4.6 `projections/qb.py`

```python
@dataclass
class QBProjection:
    name: str
    team: str
    pass_attempts: float
    completion_pct: float
    pass_yards: float           # the team passing anchor
    pass_tds: float             # later computed via team conversion, but tracked here
    interceptions: float
    sack_count: float
    scramble_yards: float       # tracked SEPARATELY from team rushing (your call)


def project_qb_line(
    qb: Player,
    opponent: str,
    season: int,
    week: int,
    qb_history: pd.DataFrame,
    pass_defense: pd.DataFrame,
    injuries: pd.DataFrame,
) -> QBProjection:
    """Project the starting QB's stat line.
    
    For each stat (attempts, comp_pct, ypa, scrambles):
      1. weighted_recent = weighted_recent_average(qb's last 4 games)
      2. season_baseline = qb's season-to-date mean
      3. own_estimate = blend_with_baseline(recent, season, n_games)
      4. opp_factor for the appropriate defensive stat
      5. inj_factor = injury_factor(...)
      6. projected_stat = own_estimate × opp_factor × inj_factor
    
    pass_yards = pass_attempts × ypa (with comp_pct applied to attempts that complete)
    """
    ...
```

### 4.7 `projections/rb.py`

```python
@dataclass
class RBProjection:
    name: str
    team: str
    carries: float
    rush_yards: float
    targets: float                # for receiving allocation
    receiving_yards: float        # filled in by receiver step
    is_starter: bool              # for snap-share weighting in allocation


def project_rb_line(
    rb: Player,
    opponent: str,
    season: int,
    week: int,
    rb_history: pd.DataFrame,
    rush_defense: pd.DataFrame,
    injuries: pd.DataFrame,
) -> RBProjection:
    """Project an RB's stat line.
    
    carries: weighted_recent → blend → opp_factor → injury_factor
    rush_yards: carries × ypc (with same blend logic)
    targets: weighted_recent target share, for allocation
    """
    ...
```

### 4.8 `projections/wr_te.py`

```python
@dataclass
class ReceiverProjection:
    name: str
    team: str
    position: str            # 'WR' or 'TE'
    target_share: float      # fraction of team targets they get
    receptions: float
    receiving_yards: float   # filled in by team allocation step


def project_receiver_target_share(
    receiver: Player,
    opponent: str,
    season: int,
    week: int,
    recv_history: pd.DataFrame,
    recv_defense: pd.DataFrame,
    injuries: pd.DataFrame,
) -> ReceiverProjection:
    """Project receiver's TARGET SHARE only.
    
    Actual yards filled in later by team.py during allocation.
    
    target_share: weighted_recent → blend → minor opp adjustment
    """
    ...
```

### 4.9 `projections/team.py`

The aggregation logic. Brings together all player projections into team-level numbers.

```python
@dataclass
class TeamProduction:
    team: str
    pass_yards: float
    rush_yards: float
    pass_tds_implied: float     # from yards × league rate
    rush_tds_implied: float     # from yards × league rate
    field_goals: float
    receiver_lines: list[ReceiverProjection]  # for debugging / interp


def aggregate_to_team(
    qb_proj: QBProjection,
    rb_projs: list[RBProjection],
    recv_projs: list[ReceiverProjection],
    team_recent_fgs: float,
) -> TeamProduction:
    """Aggregate all player projections into team production.
    
    Steps:
      1. team_pass_yards = qb_proj.pass_yards  (the QB-anchored decision)
      2. allocate pass_yards across receivers (WR + TE + RB) by target share
         - normalize target shares so they sum to 1.0
         - each player's receiving_yards = team_pass_yards × normalized_share
         - cap receiver yards at reasonable maximum (e.g., 250 yards)
      3. team_rush_yards = sum(rb.rush_yards for rb in rb_projs) + qb_proj.scramble_yards
      4. compute implied TDs from yards × league rate
      5. field_goals = team_recent_fgs (passed in from rolling stat)
    
    Returns a TeamProduction summarizing the whole offensive output.
    """
    ...
```

### 4.10 `projections/points.py`

The TD-and-FG-to-points conversion.

```python
def production_to_points(prod: TeamProduction) -> float:
    """Convert team production into expected points.
    
    points = (pass_tds + rush_tds) × POINTS_PER_TD_WITH_PAT
           + field_goals × POINTS_PER_FG
    """
    ...
```

### 4.11 `game.py`

The orchestrator. One function call → one game prediction.

```python
@dataclass
class GamePrediction:
    home_team: str
    away_team: str
    season: int
    week: int
    
    # Core predictions
    predicted_home_score: float
    predicted_away_score: float
    predicted_margin: float       # home - away
    predicted_total: float        # home + away
    
    # Derived metrics (Vegas-based, only if line available)
    spread_close: Optional[float]
    total_close: Optional[float]
    ats_pick: Optional[str]
    ats_prob: Optional[float]
    ou_pick: Optional[str]
    ou_prob: Optional[float]
    su_pick: str
    win_prob_home: float
    
    # Full breakdown for interpretation
    home_production: TeamProduction
    away_production: TeamProduction


def project_game(
    home: str,
    away: str,
    season: int,
    week: int,
    # All needed data passed in as DataFrames:
    schedule, vegas, injuries, depth_charts,
    qb_history, rb_history, recv_history,
    pass_defense, rush_defense, recv_defense,
) -> GamePrediction:
    """Project a single game end-to-end.
    
    For each team:
      1. Get active roster
      2. Project QB, all RBs, all receivers
      3. Aggregate to team production
      4. Convert to points
    
    Then compute derived metrics and return GamePrediction.
    """
    ...
```

### 4.12 `grade.py`

Grades predictions against actuals. Used by both the live workflow and the backtest.

```python
def grade_prediction(pred: GamePrediction, actual_home: float, actual_away: float) -> dict:
    """Return per-game grade dict with margin_error, su_correct, ats_correct, ou_correct."""
    ...

def summarize_grades(grades: list[dict]) -> pd.DataFrame:
    """Aggregate to MAE, SU accuracy, ATS accuracy, OU accuracy."""
    ...
```

### 4.13 `backtest.py`

The walk-forward harness for v2.

```python
def walk_forward_backtest(
    test_seasons: list[int],
    min_week: int = 1,
    max_week: int = 18,
    warehouse_path: str = DEFAULT_WAREHOUSE,
    verbose: bool = True,
) -> BacktestResult:
    """Walk-forward backtest of v2 across given seasons.
    
    For each (season, week, game):
      1. Load all data filtered to STRICTLY before (season, week) — no leakage
      2. Run project_game(...)
      3. Grade against actual final scores
    
    Returns: residuals DataFrame, summary table, per-season breakdown.
    """
    ...
```

### 4.14 `compare.py`

The side-by-side v1 vs v2 comparison script.

```python
def compare_backtests(
    v1_residuals_path: str = "data/processed/team_backtest_residuals.parquet",
    v2_residuals_path: str = "data/processed/v2/backtest_residuals.parquet",
) -> pd.DataFrame:
    """Load both backtests, join on game_id, output a side-by-side comparison."""
    ...
```

Output:

```
                 v1_naive  v1_points_only  v1_fpd_aware  v2_bottom_up
margin_mae         11.09         10.43          10.52         <tbd>
su_accuracy        54.2%         62.5%          63.7%         <tbd>
ats_accuracy       48.8%         49.2%          50.8%         <tbd>
n_games            816           816            816            816
```

### 4.15 `cli.py`

The CLI commands. Mirrors v1's structure but with fewer commands (no calibrate, no season-sim, no team-backtest variants, no track-results for v1 — all deferred).

```
python -m nfl_projector_v1 predict-week --season 2026 --week 5
python -m nfl_projector_v1 backtest --test-seasons 2023 2024 2025
python -m nfl_projector_v1 compare    # runs compare_backtests
python -m nfl_projector_v1 fetch-depth-charts --seasons 2023 2024 2025
```

---

## 5. Key design choices, explicit

### Choices made and locked

1. **Bottom-up architecture** — players first, team is the sum
2. **QB-anchored passing yards** — receiver yards allocate from QB total
3. **TD-per-yard conversion** — league-average rates, not per-player TD projection
4. **Team rolling FGs/game** — kicker variance deferred to v2+
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
- DST / special teams TDs
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

Output to `data/processed/v2/backtest_residuals.parquet` and `data/processed/v2/backtest_summary.csv`.

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
