"""Shared utilities: name normalization, team-code mapping, week labels."""

from __future__ import annotations
import re
import unicodedata


# ---------------------------------------------------------------------------
# Team codes
# ---------------------------------------------------------------------------

# FPD uses some non-standard codes. This map normalizes everything to the
# nflverse standard (32 codes, all 2-3 letters).
TEAM_CODE_MAP = {
    # FPD-isms
    "ARZ": "ARI",
    "BLT": "BAL",
    "CLV": "CLE",
    "HST": "HOU",
    "JAX": "JAX",
    "JAC": "JAX",
    "LA":  "LAR",
    "LAR": "LAR",
    "LV":  "LV",
    "LVR": "LV",
    "OAK": "LV",   # historical
    "SD":  "LAC",  # historical
    "STL": "LAR",  # historical
    "WAS": "WAS",
    "WSH": "WAS",
    # Rest are identity
}

# All 32 valid normalized codes, for sanity checking
VALID_TEAMS = {
    "ARI", "ATL", "BAL", "BUF", "CAR", "CHI", "CIN", "CLE",
    "DAL", "DEN", "DET", "GB",  "HOU", "IND", "JAX", "KC",
    "LAC", "LAR", "LV",  "MIA", "MIN", "NE",  "NO",  "NYG",
    "NYJ", "PHI", "PIT", "SEA", "SF",  "TB",  "TEN", "WAS",
}

# Team-name → code (handles full names from FPA / Coverage Matrix files)
TEAM_NAME_MAP = {
    "Arizona Cardinals": "ARI", "Cardinals": "ARI",
    "Atlanta Falcons": "ATL",   "Falcons": "ATL",
    "Baltimore Ravens": "BAL",  "Ravens": "BAL",
    "Buffalo Bills": "BUF",     "Bills": "BUF",
    "Carolina Panthers": "CAR", "Panthers": "CAR",
    "Chicago Bears": "CHI",     "Bears": "CHI",
    "Cincinnati Bengals": "CIN","Bengals": "CIN",
    "Cleveland Browns": "CLE",  "Browns": "CLE",
    "Dallas Cowboys": "DAL",    "Cowboys": "DAL",
    "Denver Broncos": "DEN",    "Broncos": "DEN",
    "Detroit Lions": "DET",     "Lions": "DET",
    "Green Bay Packers": "GB",  "Packers": "GB",
    "Houston Texans": "HOU",    "Texans": "HOU",
    "Indianapolis Colts": "IND","Colts": "IND",
    "Jacksonville Jaguars": "JAX", "Jaguars": "JAX",
    "Kansas City Chiefs": "KC", "Chiefs": "KC",
    "Los Angeles Chargers": "LAC", "Chargers": "LAC",
    "Los Angeles Rams": "LAR",  "Rams": "LAR",
    "Las Vegas Raiders": "LV",  "Raiders": "LV",
    "Miami Dolphins": "MIA",    "Dolphins": "MIA",
    "Minnesota Vikings": "MIN", "Vikings": "MIN",
    "New England Patriots": "NE", "Patriots": "NE",
    "New Orleans Saints": "NO", "Saints": "NO",
    "New York Giants": "NYG",   "Giants": "NYG",
    "New York Jets": "NYJ",     "Jets": "NYJ",
    "Philadelphia Eagles": "PHI", "Eagles": "PHI",
    "Pittsburgh Steelers": "PIT", "Steelers": "PIT",
    "Seattle Seahawks": "SEA",  "Seahawks": "SEA",
    "San Francisco 49ers": "SF","49ers": "SF",
    "Tampa Bay Buccaneers": "TB", "Buccaneers": "TB",
    "Tennessee Titans": "TEN",  "Titans": "TEN",
    "Washington Commanders": "WAS", "Commanders": "WAS",
    # 2020-2021: the franchise played as the "Washington Football Team"
    # before rebranding to the Commanders in 2022. FPD's 2021 defensive
    # exports use Location="Washington" + Team Name="Football Team".
    "Washington Football Team": "WAS", "Football Team": "WAS",
}


def normalize_team(code_or_name: str) -> str:
    """Map any team identifier (code OR full name OR nickname) to canonical code."""
    if code_or_name is None:
        return ""
    s = str(code_or_name).strip()
    if not s:
        return ""
    # Try as code first
    upper = s.upper()
    if upper in VALID_TEAMS:
        return upper
    if upper in TEAM_CODE_MAP:
        return TEAM_CODE_MAP[upper]
    # Try as full / partial name
    if s in TEAM_NAME_MAP:
        return TEAM_NAME_MAP[s]
    # Last attempt: nickname-only
    for name, code in TEAM_NAME_MAP.items():
        if s.lower() == name.lower():
            return code
    raise ValueError(f"Unknown team identifier: {code_or_name!r}")


# ---------------------------------------------------------------------------
# Player names
# ---------------------------------------------------------------------------

_NAME_STRIP_RE = re.compile(r"\b(jr|sr|ii|iii|iv|v)\.?$", re.IGNORECASE)
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


def normalize_name(name: str) -> str:
    """Normalize a player name for joining across sources.

    - lowercase
    - strip accents
    - strip Jr/Sr/II/III/IV
    - strip non-alphanumerics
    """
    if name is None:
        return ""
    s = str(name).strip()
    if not s:
        return ""
    # Strip accents
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower()
    # Strip suffixes
    s = _NAME_STRIP_RE.sub("", s).strip()
    # Strip non-alphanumerics
    s = _NON_ALNUM_RE.sub("", s)
    return s


def player_key(name: str, team: str) -> str:
    """Composite key (name_normalized + team) for player rows.

    We use this as our primary join key because FPD doesn't provide a
    persistent player_id. Trades break this; the data/raw/external/
    player_id_overrides.csv file lets you fix specific cases manually.
    """
    return f"{normalize_name(name)}__{normalize_team(team)}"


# ---------------------------------------------------------------------------
# Week labels (mapping FPD's 22 file ordering to season+week)
# ---------------------------------------------------------------------------

# Maxim's FPD exports come as 22 files per season:
#   passingAdvancedExport.csv         → week 1 (regular season)
#   passingAdvancedExport (1).csv     → week 2
#   ...
#   passingAdvancedExport (17).csv    → week 18 (regular season finale)
#   passingAdvancedExport (18).csv    → wildcard
#   passingAdvancedExport (19).csv    → divisional
#   passingAdvancedExport (20).csv    → conference
#   passingAdvancedExport (21).csv    → super_bowl

WEEK_LABELS = (
    [f"week_{i:02d}" for i in range(1, 19)]
    + ["wildcard", "divisional", "conference", "super_bowl"]
)
assert len(WEEK_LABELS) == 22


def fpd_index_to_label(index: int) -> str:
    """Map FPD's 0..21 file ordering to a season-week label."""
    if not 0 <= index < 22:
        raise ValueError(f"FPD index out of range: {index}")
    return WEEK_LABELS[index]


def label_to_week_number(label: str) -> tuple[int, str]:
    """Map a label like 'week_03' → (3, 'regular'), or 'wildcard' → (19, 'wildcard')."""
    if label.startswith("week_"):
        return int(label.split("_")[1]), "regular"
    playoff_week_offset = {
        "wildcard": 19,
        "divisional": 20,
        "conference": 21,
        "super_bowl": 22,
    }
    if label in playoff_week_offset:
        return playoff_week_offset[label], label
    raise ValueError(f"Unknown week label: {label!r}")
