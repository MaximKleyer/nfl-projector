"""Update the advanced_receiving_player CSVs for 2023, 2024, 2025 with
new data that includes RB receiving stats.

INPUT  layout (what you have after unzipping):
    <in_dir>/advanced_receiving_player_2023/receivingAdvancedExport.csv
    <in_dir>/advanced_receiving_player_2023/receivingAdvancedExport (1).csv
    ...
    <in_dir>/advanced_receiving_player_2023/receivingAdvancedExport (21).csv
    <in_dir>/advanced_receiving_player_2024/receivingAdvancedExport.csv
    ...
    <in_dir>/advanced_receiving_player_2025/receivingAdvancedExport.csv
    ...

OUTPUT layout (what the warehouse expects):
    data/raw/2023/week_01/advanced_receiving_player.csv
    data/raw/2023/week_02/advanced_receiving_player.csv
    ...
    data/raw/2023/super_bowl/advanced_receiving_player.csv
    (same for 2024 and 2025)

After running this, run build_database.py to re-ingest the warehouse.

USAGE:
    python scripts/update_receiving_with_rb.py --in-dir <unzipped_folder> --out-dir data/raw
"""
from __future__ import annotations
import argparse
import shutil
from pathlib import Path


# FPD numeric suffix → week label, matching the rest of the ingest pipeline.
# File without a suffix = week 1; (1) = week 2; ... (21) = super_bowl
WEEK_LABELS = (
    [f"week_{i:02d}" for i in range(1, 19)]
    + ["wildcard", "divisional", "conference", "super_bowl"]
)


def parse_week_index_from_filename(filename: str) -> int | None:
    """Extract the 0-indexed week number from an FPD export filename.

    'receivingAdvancedExport.csv'     → 0  (week 1)
    'receivingAdvancedExport (1).csv' → 1  (week 2)
    ...
    'receivingAdvancedExport (21).csv' → 21 (super_bowl)
    """
    name = filename.replace("receivingAdvancedExport", "").replace(".csv", "").strip()
    if name == "":
        return 0
    # Strip parentheses
    if name.startswith("(") and name.endswith(")"):
        try:
            return int(name[1:-1])
        except ValueError:
            return None
    return None


def update_season(
    season: int,
    in_dir: Path,
    out_dir: Path,
    verbose: bool = True,
) -> int:
    """Copy each weekly CSV for one season into the warehouse-expected layout.

    Returns the number of files successfully copied.
    """
    season_in = in_dir / f"advanced_receiving_player_{season}"
    if not season_in.exists():
        if verbose:
            print(f"  [skip] {season_in} not found")
        return 0

    if verbose:
        print(f"  Processing {season_in}...")

    copied = 0
    csvs = sorted(season_in.glob("receivingAdvancedExport*.csv"))
    for src in csvs:
        idx = parse_week_index_from_filename(src.name)
        if idx is None or idx >= len(WEEK_LABELS):
            if verbose:
                print(f"    [skip] could not parse week from: {src.name}")
            continue
        week_label = WEEK_LABELS[idx]
        dest_dir = out_dir / str(season) / week_label
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / "advanced_receiving_player.csv"
        shutil.copy2(src, dest)
        copied += 1

    if verbose:
        print(f"    Copied {copied} files for season {season}")
    return copied


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--in-dir", required=True,
                        help="Folder containing advanced_receiving_player_<season>/ subdirs")
    parser.add_argument("--out-dir", default="data/raw",
                        help="Warehouse raw-data root (default: data/raw)")
    parser.add_argument("--seasons", type=int, nargs="+",
                        default=[2023, 2024, 2025],
                        help="Seasons to update (default: 2023 2024 2025)")
    args = parser.parse_args()

    in_dir = Path(args.in_dir)
    out_dir = Path(args.out_dir)
    if not in_dir.exists():
        print(f"ERROR: input directory not found: {in_dir}")
        return 1
    out_dir.mkdir(parents=True, exist_ok=True)

    total = 0
    for season in args.seasons:
        total += update_season(season, in_dir, out_dir)

    print(f"\nDone. Updated {total} CSV files.")
    print("Next step:")
    print(f"  python scripts/build_database.py --seasons {' '.join(str(s) for s in args.seasons)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
