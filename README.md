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
snap share + injuries + depth  →  who plays this week (active roster)
player game logs (rolling)     →  each player's projected stat line
  · QB: attempts, YPA, pass yards (the team passing anchor), scrambles, sacks
  · RB: carries, YPC, rush yards, plus receiving target share
  · WR/TE: target share, yards per target
matchup + injury adjustments   →  each stat × opponent factor × health factor
aggregate to team              →  pass yards (QB anchor) allocated across
                                  receivers by efficiency-weighted target share;
                                  rush yards = RBs + QB scrambles
production → points             →  TDs via per-team TD-per-yard, FGs, DST/ST
                                  baseline, global total calibration
two teams → game               →  per-team home-field shift (margin) +
                                  dome total nudge → margin, total, win prob,
                                  ATS/OU picks
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

Walk-forward backtest on **2023-2025 (816 games)** with the production model
(snap-share rosters + per-team TD conversion + total calibration + per-team
home-field advantage + dome total adjustment):

| Metric | Value |
|--------|-------|
| SU accuracy | 63.8% |
| ATS accuracy | 49.3% |
| O/U accuracy | 49.9% |
| Margin MAE | 10.40 |
| Total MAE | 10.61 |
| Total bias | ~0 (calibrated) |
| Home-margin bias | +0.26 (HFA-corrected, was +2.30) |

The model's edge is straight-up winners and margin (64% SU). O/U stays near
coin-flip — totals are the hardest market — but the systematic under-bias is
calibrated out and efficient offenses are no longer under-projected. The
per-team home-field term lifted SU +1.6 pts and nearly zeroed the home-margin
bias the bottom-up engine carried (it had ~zero built-in HFA). ATS sits just
under break-even — never the model's edge (Vegas prices HFA). Build history:
snap-share roster (`DESIGN.md` §12), per-team TD conversion + calibration (§13),
per-team home-field advantage (§14).

**Known limitation:** the model needs ~1 full prior season of data to perform.
The first season of the data window — now **2021** (no 2019-2020 priors) — sits near
coin-flip on SU, so the backtest only grades from 2023 onward. See `DESIGN.md` §10.

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
python scripts/build_database.py --seasons 2021 2022 2023 2024 2025
```

## Usage

```bash
# Predict every game in a week (prints + writes a CSV)
python -m nfl_projector_v1 predict --season 2025 --week 16

# Project a full season: per-team expected wins + division/playoff odds
python -m nfl_projector_v1 predict-season --season 2026

# Walk-forward backtest
python -m nfl_projector_v1 backtest --seasons 2024 2025

# Refresh depth charts from nflverse
python -m nfl_projector_v1 refresh-depth-charts --seasons 2025

# Check data availability
python -m nfl_projector_v1 status
```

`predict` and `predict-season` work for future weeks/seasons too — they don't
need actual scores, only the scheduled matchups and current depth charts. Run
`refresh-depth-charts` first to pull the latest rosters. The A/B knobs
(`--roster-mode`, `--td-rates`, `--calibrate/--no-calibrate`, `--home-field`)
default to the production config; override them on any command to compare.

## Project layout

```
nfl_projector_v1/
  config.py              league constants, paths
  cli.py                 command-line interface
  game.py                game orchestrator → GamePrediction (+ home-field shift)
  season.py              full-season projection → standings + div/playoff odds
  backtest.py            walk-forward backtest harness
  data/
    loaders.py           warehouse → DataFrames
    depth_charts.py      nflverse depth charts (QB path + no-snap fallback)
    roster.py            active-roster identification (FPD snap share)
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

v1 is complete and operational, with **2021-2025 ingested**. Shipped since the
initial build (see `DESIGN.md`): the 2021-2022 backfill that fixed the 2023
cold-start (§10/§11), FPD snap-share rosters (§12), per-team TD conversion +
total calibration (§13), per-team home-field advantage (§14), a dome total
adjustment (§15), and the `predict-season` full-season projection.

Remaining planned enhancements (`DESIGN.md` §11): game-script awareness for
backup-QB over-projection, a cross-time team comparison command, and QB
cross-team history weighting. Per-team FG rates were investigated and didn't beat
flat (§11 #5); a **wind** total penalty is promising (~−2 to −4.7 pts in 10+ mph
games) but needs a weather-forecast feed to be usable live (§15).
