"""Incorporate the 2021 + 2022 FPD exports into the warehouse raw-data layout.

INPUT layout (what's inside the 2021_2022.zip after unzipping):
    <in_dir>/2021/advanced_passing_player_2021/passingAdvancedExport.csv
    <in_dir>/2021/advanced_passing_player_2021/passingAdvancedExport (1).csv
    ...
    <in_dir>/2021/advanced_passing_def_2021/passingAdvancedExport.csv
    ...
    <in_dir>/2022/advanced_rushing def_2022/rushingAdvancedExport (3).csv   # note: space, not underscore
    ...

  Each category folder holds 22 weekly files. The FPD export base name does
  NOT distinguish player vs def or stat group — that information is in the
  FOLDER name. So we map by folder, not by filename.

OUTPUT layout (what build_database.py expects):
    data/raw/2021/week_01/advanced_passing_player.csv
    data/raw/2021/week_02/advanced_passing_player.csv
    ...
    data/raw/2021/super_bowl/advanced_passing_player.csv
    (same for all 6 categories, both seasons)

After running this, rebuild the warehouse:
    python scripts/build_database.py --seasons 2021 2022 2023 2024 2025

Note: fpa_qb is intentionally NOT included — the v1 model never reads it.
Ingestion will print "[skip] fpa_qb (no files found)" for 2021/2022, which
is expected and harmless.

USAGE:
    python scripts/incorporate_2021_2022.py --in-dir <unzipped_folder> --out-dir data/raw
"""
from __future__ import annotations
import argparse
import re
import shutil
from pathlib import Path


# FPD numeric suffix → week label (matches the rest of the ingest pipeline).
# File with no suffix = week 1; (1) = week 2; ... (21) = super_bowl.
WEEK_LABELS = (
    [f"week_{i:02d}" for i in range(1, 19)]
    + ["wildcard", "divisional", "conference", "super_bowl"]
)

# Map a normalized source-folder stem → canonical output filename.
# We normalize the folder name first (lowercase, spaces→underscores, strip the
# trailing _YYYY) so that the stray "advanced_rushing def_2022" folder (which
# has a space instead of an underscore) still matches.
FOLDER_TO_CANONICAL = {
    "advanced_passing_player": "advanced_passing_player.csv",
    "advanced_passing_def":    "advanced_passing_def.csv",
    "advanced_rushing_player": "advanced_rushing_player.csv",
    "advanced_rushing_def":    "advanced_rushing_def.csv",
    "advanced_receiving_player": "advanced_receiving_player.csv",
    "advanced_receiving_def":    "advanced_receiving_def.csv",
}


def _normalize_folder_stem(folder_name: str, season: int) -> str | None:
    """Turn a source-folder name into a canonical key.

    'advanced_passing_player_2021'  -> 'advanced_passing_player'
    'advanced_rushing def_2022'     -> 'advanced_rushing_def'  (space → underscore)

    Returns None if the folder doesn't look like one of our categories.
    """
    stem = folder_name.lower().strip()
    # Replace any whitespace with underscores (handles "rushing def")
    stem = re.sub(r"\s+", "_", stem)
    # Strip a trailing _<season>
    stem = re.sub(rf"_{season}$", "", stem)
    # Collapse any doubled underscores that might result
    stem = re.sub(r"_+", "_", stem)
    return stem if stem in FOLDER_TO_CANONICAL else None


def _week_index_from_filename(filename: str) -> int | None:
    """Extract the 0-indexed week number from an FPD export filename.

    'passingAdvancedExport.csv'     -> 0  (week 1)
    'passingAdvancedExport (1).csv' -> 1  (week 2)
    ...
    'passingAdvancedExport (21).csv' -> 21 (super_bowl)
    """
    m = re.search(r"\((\d+)\)", filename)
    if m:
        return int(m.group(1))
    # No parenthetical suffix → the base file → week 1 (index 0)
    if filename.lower().endswith(".csv"):
        return 0
    return None


def process_season(season: int, in_dir: Path, out_dir: Path, verbose: bool = True) -> int:
    """Copy all category folders for one season into the warehouse layout.

    Returns the number of files copied.
    """
    season_dir = in_dir / str(season)
    if not season_dir.exists():
        if verbose:
            print(f"  [skip] {season_dir} not found")
        return 0

    copied = 0
    for folder in sorted(season_dir.iterdir()):
        if not folder.is_dir():
            continue
        key = _normalize_folder_stem(folder.name, season)
        if key is None:
            if verbose:
                print(f"  [skip] unrecognized folder: {folder.name}")
            continue
        canonical = FOLDER_TO_CANONICAL[key]

        if verbose:
            print(f"  {folder.name}  →  {canonical}")

        for src in sorted(folder.glob("*.csv")):
            idx = _week_index_from_filename(src.name)
            if idx is None or idx >= len(WEEK_LABELS):
                if verbose:
                    print(f"    [skip] could not parse week from: {src.name}")
                continue
            week_label = WEEK_LABELS[idx]
            dest_dir = out_dir / str(season) / week_label
            dest_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest_dir / canonical)
            copied += 1

    if verbose:
        print(f"  Season {season}: copied {copied} files\n")
    return copied


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--in-dir", required=True,
                        help="Folder containing 2021/ and 2022/ subdirs of category folders")
    parser.add_argument("--out-dir", default="data/raw",
                        help="Warehouse raw-data root (default: data/raw)")
    parser.add_argument("--seasons", type=int, nargs="+", default=[2021, 2022],
                        help="Seasons to process (default: 2021 2022)")
    args = parser.parse_args(argv)

    in_dir = Path(args.in_dir).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    if not in_dir.exists():
        print(f"ERROR: input directory not found: {in_dir}")
        return 1
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Incorporating FPD data from {in_dir}")
    print(f"Output to {out_dir}\n")

    total = 0
    for season in args.seasons:
        total += process_season(season, in_dir, out_dir)

    print(f"Done. Copied {total} files total.")
    print("\nExpected: 6 categories × 22 weeks × 2 seasons = 264 files")
    print("\nNext step — rebuild the warehouse:")
    print(f"  python scripts/build_database.py --seasons 2021 2022 2023 2024 2025")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
