# nfl_projector_v1

A bottom-up NFL game projection model. Projects each player's stat line for an
upcoming week, aggregates those into team production, and converts production
into a predicted final score — then derives straight-up, against-the-spread,
and over/under picks from that score.

```
GB @ DET       DET 26.3 - GB 21.2       | SU: DET (64%)   ATS: DET   O/U: UNDER
LAC @ KC       KC 25.6 - LAC 21.6       | SU: KC (61%)    ATS: LAC   O/U: OVER
CIN @ DAL      DAL 19.2 - CIN 25.8      | SU: CIN (68%)   ATS: CIN   O/U: UNDER
```

## How it works

The model is **bottom-up**: it builds team scores from individual player
projections rather than predicting team scores directly.

```
depth charts + injuries        →  who plays this week (active roster)
player game logs (rolling)     →  each player's projected stat line
  · QB: attempts, YPA, pass yards (the team passing anchor), scrambles, sacks
  · RB: carries, YPC, rush yards, plus receiving target share
  · WR/TE: target share, yards per target
matchup + injury adjustments   →  each stat × opponent factor × health factor
aggregate to team              →  pass yards (QB anchor) allocated across
                                  receivers by efficiency-weighted target share;
                                  rush yards = RBs + QB scrambles
production → points             →  TDs via league TD-per-yard, FGs, DST/ST baseline
two teams → game               →  margin, total, win prob, ATS/OU picks
```

Each projected stat follows the same recipe:

```
estimate = blend(weighted_recent_4_games, season_baseline, league_fallback)
projected = estimate × opponent_matchup_factor × injury_factor
```

Recent form is weighted toward the last 4 games with Bayesian shrinkage toward
season and league baselines when the sample is thin. Vegas lines are used **only
for grading** ATS/OU — they never anchor the predictions.

## Performance

Walk-forward backtest on 2024-2025 (544 games, the "fair" sample with prior-season
history available):

| Metric | Value |
|--------|-------|
| Margin MAE | 10.79 |
| SU accuracy | 58.8% |
| ATS accuracy | 49.3% |
| O/U accuracy | 47.3% |
| Total MAE | 10.78 |

2025 alone (post roster-fix): SU 58.3%, ATS 53.9%.

**Known limitation:** the model needs ~1 full prior season of data to perform.
The first season of any data window (currently 2023) sits near coin-flip on SU
because player projections fall back to league averages. See `DESIGN.md` §10.

## Setup

Requires Python 3.14 and a built warehouse (`warehouse.duckdb`) containing the
FPD + nflverse tables. Data lives outside the repo (see `.gitignore`).

```bash
# from the repo root
python -m venv .venv
.venv\Scripts\Activate.ps1        # Windows
pip install -e .

# sanity-check the warehouse + depth chart cache are present
python -m nfl_projector_v1 status

# fetch depth charts from nflverse (first run, or to refresh)
python -m nfl_projector_v1 refresh-depth-charts --seasons 2023 2024 2025
```

If the warehouse is missing, build it from the raw FPD CSVs:

```bash
python scripts/build_database.py --seasons 2023 2024 2025
```

## Usage

```bash
# Predict every game in a week (prints + writes a CSV)
python -m nfl_projector_v1 predict --season 2025 --week 16

# Walk-forward backtest
python -m nfl_projector_v1 backtest --seasons 2024 2025

# Refresh depth charts from nflverse
python -m nfl_projector_v1 refresh-depth-charts --seasons 2025

# Check data availability
python -m nfl_projector_v1 status
```

`predict` works for future weeks too — it doesn't need actual scores, only the
scheduled matchups and current depth charts. Run `refresh-depth-charts` first to
pull the latest rosters.

## Project layout

```
nfl_projector_v1/
  config.py              league constants, paths
  cli.py                 command-line interface
  game.py                game orchestrator → GamePrediction
  backtest.py            walk-forward backtest harness
  data/
    loaders.py           warehouse → DataFrames
    depth_charts.py      nflverse depth charts (handles 2023-24 + 2025+ schemas)
    roster.py            active-roster identification
  projections/
    base.py              the 4 shared projection helpers
    qb.py                QB stat-line projection
    rb.py                RB rushing + receiving projection
    wr_te.py             WR/TE target-share projection
    team.py              aggregate players → team production
    points.py            production → expected points
scripts/
  build_database.py      ingest raw CSVs → warehouse
  update_receiving_with_rb.py   one-off RB-receiving data update
DESIGN.md                full design doc + results + future work
```

## Data sources

- **FPD** — player and defensive game logs (passing, rushing, receiving)
- **nflverse** — depth charts, schedule, Vegas lines, injuries

## Status & roadmap

v1 is complete and operational. Planned enhancements (see `DESIGN.md` §11):
ingest 2021-2022 to fix the cold-start season, game-script awareness for
backup-QB over-projection, team-specific FG and red-zone rates, and a
cross-time team comparison command.
