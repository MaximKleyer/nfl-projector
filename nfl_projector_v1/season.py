"""Season-wide projection: project every game in a season and aggregate to
per-team expected wins, projected standings, and (via a Monte Carlo sim of the
remaining schedule) division-title and playoff odds.

This runs the SAME game engine as `predict`/`backtest` (project_game), so it
inherits the production config (snap-share rosters + per-team TD rates +
calibration). For a not-yet-played season it's a preseason projection: every
game uses static prior-season strength + the current projected depth chart, so
treat it as an early baseline that sharpens as real in-season data arrives.
"""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
import numpy as np
import pandas as pd

from .config import DEFAULT_HOME_FIELD
from .data.loaders import load_all
from .game import project_game


# NFL conferences / divisions (standard nflverse team codes).
CONF_DIV = {
    "AFC": {
        "East": ["BUF", "MIA", "NE", "NYJ"], "North": ["BAL", "CIN", "CLE", "PIT"],
        "South": ["HOU", "IND", "JAX", "TEN"], "West": ["DEN", "KC", "LV", "LAC"],
    },
    "NFC": {
        "East": ["DAL", "NYG", "PHI", "WAS"], "North": ["CHI", "DET", "GB", "MIN"],
        "South": ["ATL", "CAR", "NO", "TB"], "West": ["ARI", "LAR", "SF", "SEA"],
    },
}
_TEAM_CONF = {t: conf for conf, divs in CONF_DIV.items() for ts in divs.values() for t in ts}
_TEAM_DIV = {t: f"{conf} {d}" for conf, divs in CONF_DIV.items() for d, ts in divs.items() for t in ts}


@dataclass
class SeasonProjection:
    standings: pd.DataFrame   # one row per team: exp wins/losses, SU record, div/playoff odds
    games: pd.DataFrame       # one row per game: scores, win_prob_home, su_pick


def project_season(
    season: int,
    data: Optional[dict] = None,
    min_week: int = 1,
    max_week: int = 18,
    warehouse_path: Optional[Path] = None,
    n_sims: int = 10000,
    seed: int = 0,
    verbose: bool = True,
    home_field: str = DEFAULT_HOME_FIELD,
) -> SeasonProjection:
    """Project a full season and return standings (with division/playoff odds) +
    per-game projections. Uses the production config via project_game."""
    if data is None:
        if verbose:
            print("Loading warehouse...")
        data = load_all(warehouse_path)
    data["home_field"] = home_field

    sched = data["schedule"]
    gms = sched[
        (sched["season"] == season)
        & (sched["week"] >= min_week) & (sched["week"] <= max_week)
        & sched["home_team"].notna() & sched["away_team"].notna()
    ].copy()
    if gms.empty:
        raise ValueError(f"No games found for season {season} weeks {min_week}-{max_week}. "
                         f"Has the {season} schedule been ingested?")

    if verbose:
        print(f"Projecting {len(gms)} games for season {season} (weeks {min_week}-{max_week})...")

    rows, last_wk = [], None
    for _, g in gms.iterrows():
        home, away, wk = g["home_team"], g["away_team"], int(g["week"])
        if verbose and wk != last_wk:
            print(f"  week {wk}...")
            last_wk = wk
        try:
            p = project_game(home, away, season, wk, data)
        except Exception as e:
            if verbose:
                print(f"    [warn] {away} @ {home}: {e}")
            continue
        rows.append({
            "season": season, "week": wk, "home_team": home, "away_team": away,
            "predicted_home_score": p.predicted_home_score,
            "predicted_away_score": p.predicted_away_score,
            "predicted_total": p.predicted_total,
            "win_prob_home": p.win_prob_home, "su_pick": p.su_pick,
        })
    games = pd.DataFrame(rows)

    teams = sorted(set(games["home_team"]) | set(games["away_team"]))
    idx = {t: i for i, t in enumerate(teams)}
    n = len(teams)

    # ---- Expected wins (analytic, from win probabilities) + SU favored counts ----
    exp = np.zeros(n); su = np.zeros(n); gp = np.zeros(n)
    for r in games.itertuples():
        hi, ai = idx[r.home_team], idx[r.away_team]
        exp[hi] += r.win_prob_home; exp[ai] += 1.0 - r.win_prob_home
        su[hi] += int(r.su_pick == r.home_team); su[ai] += int(r.su_pick == r.away_team)
        gp[hi] += 1; gp[ai] += 1

    # ---- Monte Carlo: each game ~ Bernoulli(win_prob_home); tally div titles + playoffs ----
    div_title, playoff = _simulate(games, idx, n, n_sims, seed)

    st = pd.DataFrame({
        "team": teams,
        "conf": [_TEAM_CONF.get(t, "?") for t in teams],
        "division": [_TEAM_DIV.get(t, "?") for t in teams],
        "games": gp.astype(int),
        "exp_wins": exp.round(1),
        "exp_losses": (gp - exp).round(1),
        "su_wins": su.astype(int),
        "su_losses": (gp - su).astype(int),
        "div_title_pct": (div_title * 100).round(1),
        "playoff_pct": (playoff * 100).round(1),
    }).sort_values(["conf", "division", "exp_wins"], ascending=[True, True, False]).reset_index(drop=True)

    return SeasonProjection(standings=st, games=games)


def _simulate(games: pd.DataFrame, idx: dict, n: int, n_sims: int, seed: int):
    """Monte Carlo the schedule → (div_title_freq, playoff_freq) per team index."""
    rng = np.random.default_rng(seed)
    hp = games["win_prob_home"].to_numpy()
    hidx = games["home_team"].map(idx).to_numpy()
    aidx = games["away_team"].map(idx).to_numpy()
    G = len(games)

    home_win = rng.random((n_sims, G)) < hp           # (sims, games)
    wins = np.zeros((n_sims, n))
    for gi in range(G):
        hw = home_win[:, gi]
        wins[hw, hidx[gi]] += 1
        wins[~hw, aidx[gi]] += 1
    wins = wins + rng.random((n_sims, n)) * 1e-6       # jitter to break ties

    div_winner = np.zeros((n_sims, n), dtype=bool)
    made = np.zeros((n_sims, n), dtype=bool)
    for conf, divs in CONF_DIV.items():
        # Division winners: top team in each 4-team division per sim.
        for ts in divs.values():
            cols = [idx[t] for t in ts if t in idx]
            if not cols:
                continue
            wcol = np.array(cols)[wins[:, cols].argmax(axis=1)]
            div_winner[np.arange(n_sims), wcol] = True
        # Playoffs: 4 division winners + top-3 non-winners (wildcards) per conference.
        conf_cols = [idx[t] for ts in divs.values() for t in ts if t in idx]
        sub_w = wins[:, conf_cols]
        sub_dw = div_winner[:, conf_cols]
        masked = np.where(sub_dw, -np.inf, sub_w)
        thresh = np.sort(masked, axis=1)[:, -3][:, None]   # 3rd-best non-winner
        made_conf = sub_dw | ((~sub_dw) & (masked >= thresh))
        for li, c in enumerate(conf_cols):
            made[:, c] |= made_conf[:, li]

    return div_winner.mean(axis=0), made.mean(axis=0)
