"""CSV readers that turn FPD's raw exports into clean long-format DataFrames.

The challenge with FPD CSVs is that several reports are "wide" — they repeat
column names across sections (e.g., man_vs_zone.csv has RTE, TPRR, YPRR, FP/RR
five times across TOTAL/MAN/ZONE/SHELL_1HI/SHELL_2HI sections).

`read_report` is the main entry point. It uses the schema (from schemas.py)
to figure out which reader strategy applies to a given file:

- Simple flat CSV: just rename columns and select.
- Wide CSV with sections: chunk the columns by section size, label each chunk,
  and unpack into per-section columns (e.g., man_yprr, zone_yprr).

All readers return a pandas DataFrame with:
- season, week_label, week_num, week_type as the temporal columns
- player_key (or team_key) as the primary entity key
- canonical column names from the schema
"""

from __future__ import annotations
from pathlib import Path
import pandas as pd

from .schemas import REPORTS, ReportSchema, RequiredColumn
from ..utils import normalize_name, normalize_team, player_key, label_to_week_number


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_raw_csv(path: str | Path) -> pd.DataFrame:
    """Read a FPD CSV with the BOM stripped and string-dtype-safe."""
    return pd.read_csv(path, encoding="utf-8-sig", dtype=str, keep_default_na=False)


def _coerce(s: pd.Series, dtype: str) -> pd.Series:
    """Coerce a string series to the target dtype, treating empty strings as NaN."""
    s = s.replace({"": None, "-": None, "—": None})
    if dtype == "int64":
        return pd.to_numeric(s, errors="coerce").astype("Int64")
    if dtype == "float64":
        return pd.to_numeric(s, errors="coerce")
    return s.astype("string")


def _add_temporal_keys(
    df: pd.DataFrame,
    season: int,
    week_label: str,
) -> pd.DataFrame:
    """Add season, week_label, week_num, week_type to a DataFrame."""
    week_num, week_type = label_to_week_number(week_label)
    df = df.copy()
    df["season"] = int(season)
    df["week_label"] = week_label
    df["week_num"] = int(week_num)
    df["week_type"] = week_type
    return df


# ---------------------------------------------------------------------------
# Strategy 1: simple flat CSV
# ---------------------------------------------------------------------------

def _read_flat(path: Path, schema: ReportSchema) -> pd.DataFrame:
    raw = _read_raw_csv(path)
    out = pd.DataFrame()
    for col in schema.columns:
        if col.raw not in raw.columns:
            # Column missing from this season's file; emit NaN.
            out[col.canonical] = pd.Series([None] * len(raw), dtype="object")
            continue
        # If the same raw name appears multiple times (it can in pandas as
        # raw_name, raw_name.1, ...), only take the first occurrence.
        first_col = raw[col.raw].iloc[:, 0] if isinstance(raw[col.raw], pd.DataFrame) else raw[col.raw]
        out[col.canonical] = _coerce(first_col, col.dtype)
    return out


# ---------------------------------------------------------------------------
# Strategy 2: wide CSV with repeated section blocks
# ---------------------------------------------------------------------------

def _read_wide_with_sections(path: Path, schema: ReportSchema) -> pd.DataFrame:
    """Parse files like man_vs_zone or routes_run that have repeated col blocks.

    Strategy:
    1. Read the raw row-by-row using the column header positions, NOT names
       (since pandas dedupes duplicate names by appending .1, .2, etc.)
    2. Pull off the identifier columns first.
    3. Partition the remaining columns into chunks of len(section_columns).
    4. Label each chunk with the corresponding section name.
    5. Build the output frame with columns named {section}_{canonical}.
    """
    if not schema.section_columns or not schema.section_layout:
        raise ValueError(f"{schema.name} declared wide but has no section layout")

    # Read with positional access — use header=None then promote first row
    raw = pd.read_csv(path, encoding="utf-8-sig", dtype=str, header=None, keep_default_na=False)
    header = raw.iloc[0].tolist()
    body = raw.iloc[1:].reset_index(drop=True)

    # Pull the identifier columns by matching header text (these are the
    # ones in schema.columns; section_columns will repeat after them)
    out = pd.DataFrame()
    used_idx: set[int] = set()
    for col in schema.columns:
        # Identifier columns (Name, Team, etc.) — first occurrence wins
        try:
            idx = header.index(col.raw)
        except ValueError:
            out[col.canonical] = pd.Series([None] * len(body), dtype="object")
            continue
        used_idx.add(idx)
        out[col.canonical] = _coerce(body.iloc[:, idx], col.dtype)

    # Now find positions of section start markers. The first section column
    # is the marker. We expect len(section_layout) sections after the IDs.
    first_section_marker = schema.section_columns[0].raw
    section_size = len(schema.section_columns)

    # Find all positions in the header where the first marker appears, in order
    marker_positions = [
        i for i, name in enumerate(header)
        if name == first_section_marker and i not in used_idx
    ]
    # Special-case routes_run, where the section's first column (RTE) DOES
    # appear in identifier columns too. We need to find positions AFTER the
    # last identifier column.
    if used_idx:
        last_id_pos = max(used_idx)
        marker_positions = [p for p in marker_positions if p > last_id_pos]

    # Special-case the routes_run "__ALIGN_PCT__" placeholder — replace with
    # whatever the alignment-percent column is named in this section.
    expected_n_sections = len(schema.section_layout)
    if len(marker_positions) < expected_n_sections:
        # Some files (e.g., separation_by_coverage) have abbreviated sections
        # where later sections drop columns. We still process what we can.
        pass

    for sec_i, sec_label in enumerate(schema.section_layout):
        if sec_i >= len(marker_positions):
            # Missing section in this file; emit NaN columns
            for col in schema.section_columns:
                cname = f"{sec_label}_{col.canonical}"
                out[cname] = pd.Series([None] * len(body), dtype="object")
            continue
        start = marker_positions[sec_i]
        end = marker_positions[sec_i + 1] if sec_i + 1 < len(marker_positions) else start + section_size
        # We expect `section_size` columns; if fewer, pad with NaN
        for col_i, col in enumerate(schema.section_columns):
            cname = f"{sec_label}_{col.canonical}"
            pos = start + col_i
            if pos >= end or pos >= len(header):
                out[cname] = pd.Series([None] * len(body), dtype="object")
            else:
                # Skip the __ALIGN_PCT__ placeholder gracefully
                if col.raw == "__ALIGN_PCT__":
                    # The actual column name in this section is the alignment-rate
                    out[cname] = _coerce(body.iloc[:, pos], col.dtype)
                else:
                    out[cname] = _coerce(body.iloc[:, pos], col.dtype)
    return out


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def read_report(
    path: str | Path,
    report_name: str,
    season: int,
    week_label: str = "season",
) -> pd.DataFrame:
    """Read a FPD CSV into a clean DataFrame.

    Parameters
    ----------
    path : path to the CSV file
    report_name : key into REPORTS (e.g. 'advanced_receiving_player')
    season : 4-digit season year
    week_label : 'week_01' .. 'week_18' or 'wildcard'/'divisional'/'conference'
                 /'super_bowl' or 'season' for season-aggregate reports
    """
    if report_name not in REPORTS:
        raise KeyError(f"Unknown report: {report_name}. Known: {sorted(REPORTS)}")
    schema = REPORTS[report_name]
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"{path} not found")

    if schema.section_layout:
        df = _read_wide_with_sections(path, schema)
    else:
        df = _read_flat(path, schema)

    # Add temporal keys (skip 'season' label for season-aggregate reports)
    if week_label != "season":
        df = _add_temporal_keys(df, season, week_label)
    else:
        df = df.copy()
        df["season"] = int(season)
        df["week_label"] = "season"
        df["week_num"] = pd.NA
        df["week_type"] = "season"

    # Add normalized keys
    if schema.granularity == "player":
        if "player_name" in df.columns and "team" in df.columns:
            df["player_name_norm"] = df["player_name"].apply(normalize_name)
            df["team_norm"] = df["team"].apply(
                lambda x: normalize_team(x) if pd.notna(x) and x else ""
            )
            df["player_key"] = df.apply(
                lambda r: player_key(r["player_name"], r["team"]) if r["player_name"] else "",
                axis=1,
            )
    else:
        # team_off / team_def: use full team name if present
        if "team_full_name" in df.columns:
            df["team_norm"] = df["team_full_name"].apply(
                lambda x: normalize_team(x) if pd.notna(x) and x else ""
            )

    return df
