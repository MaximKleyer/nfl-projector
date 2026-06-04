"""Situational ATS overlay (DESIGN.md §16).

A betting-market overlay kept SEPARATE from the model's score / margin / SU /
win-prob (those are unchanged). It flags an against-the-spread lean from the one
situational bias that survived every test: the KEY-NUMBER FAVORITE FADE — when
the favorite lays a touchdown to 9.5 (|spread| in [7, 9.5]), back the underdog.

Three signals were pre-specified and tested (division dog, fade-the-bye, mid-fav
fade). In the OOS experiment all three nominally cleared the 52.4% vig break-even,
but the per-signal backtest breakdown (2023-2025) dissolved two of them: division
dog ALONE hit 46.4% and fade-bye ALONE 46.3% — their apparent edge came from
overlap with the mid-fav fade plus small-sample noise. Only the mid-favorite fade
held across every cut: raw diagnostic 55.9%, OOS test 59.8%, full backtest 57.4%
(n=115, ~38 plays/season). Still a MODEST, small-sample edge (n is thin, market
biases decay) — worth betting selectively and continuing to validate, not a
guarantee. The model never sees the line, so this is the only ATS lean expressed.
See DESIGN.md §16.
"""
from __future__ import annotations
from typing import Optional


def situational_ats_lean(
    home_team: str,
    away_team: str,
    spread_close: Optional[float],
) -> tuple[Optional[str], Optional[str]]:
    """Return (ats_pick, reason) for a situational ATS lean, or (None, None) if no
    signal fires. The only validated signal: fade a favorite laying 7 to 9.5 (back
    the underdog). Independent of the model's projection."""
    if spread_close is None or spread_close == 0:
        return None, None
    if 7.0 <= abs(spread_close) <= 9.5:
        dog = away_team if spread_close < 0 else home_team   # neg spread = home favored
        return dog, "fade 7-9.5 favorite (key number)"
    return None, None
