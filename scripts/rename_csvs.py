"""Rename FPD zip-export files into the clean week-numbered structure
this package expects.

INPUT: a directory containing FPD zip files like:
    Advanced_Passing_2023.zip
    Advanced_Passing_2024.zip
    Advanced_Receiving_2023.zip
    Snaps_2024.zip
    ...

Each zip contains 22 files per season:
    passingAdvancedExport.csv         → week 1
    passingAdvancedExport (1).csv     → week 2
    ...
    passingAdvancedExport (17).csv    → week 18
    passingAdvancedExport (18).csv    → wildcard
    passingAdvancedExport (19).csv    → divisional
    passingAdvancedExport (20).csv    → conference
    passingAdvancedExport (21).csv    → super_bowl

OUTPUT: data/raw/<season>/<week_label>/<canonical_name>.csv

Canonical names match the schemas.py filename_pattern.

USAGE:
    python scripts/rename_csvs.py --in-dir ~/Downloads/fpd_zips --out-dir data/raw

Each zip's filename is parsed to determine:
- The report type (Advanced_Passing → advanced_passing_player vs. _def)
- The season

REPORT_PATTERN below maps zip filename prefixes to canonical CSV names.
Edit it if FPD names something differently.
"""

from __future__ import annotations
import argparse
import re
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

# Map zip filename prefix → canonical output filename
# Add new entries here as you pull more report types.
REPORT_PATTERN = {
    "advanced_passing":          "advanced_passing_player.csv",
    "advanced_passing_def":      "advanced_passing_def.csv",
    "advanced_passing_player":   "advanced_passing_player.csv",
    "advanced_receiving":        "advanced_receiving_player.csv",
    "advanced_receiving_player": "advanced_receiving_player.csv",
    "advanced_receiving_def":      "advanced_receiving_def.csv",
    "advanced_rushing":          "advanced_rushing_player.csv",
    "advanced_rushing_player":   "advanced_rushing_player.csv",
    "advanced_rushing_def":      "advanced_rushing_def.csv",
    "snaps":                     "snaps.csv",
    "routes_run":                "routes_run.csv",
    "run_pass_report":           "run_pass_report.csv",
    "man_vs_zone":               "man_vs_zone.csv",
    "passing_depth":             "passing_depth.csv",
    "fpa_qb":                    "fpa_qb.csv",
    "fpa_rb":                    "fpa_rb.csv",
    "fpa_wr":                    "fpa_wr.csv",
    "fpa_te":                    "fpa_te.csv",
    # Season-aggregate reports — go in <season>/season/ instead of weekly
    "coverage_matrix_off":       ("coverage_matrix_off.csv",       "season"),
    "coverage_matrix_def":       ("coverage_matrix_def.csv",       "season"),
    "line_matchups":             ("line_matchups.csv",             "season"),
}

WEEK_LABELS = (
    [f"week_{i:02d}" for i in range(1, 19)]
    + ["wildcard", "divisional", "conference", "super_bowl"]
)


def _parse_zip_filename(name: str) -> tuple[str, int] | None:
    """Parse 'Advanced_Passing_2024.zip' or 'snaps_2023.zip' → ('advanced_passing', 2024).

    Robust to spaces, mixed case, and 'Advanced Passing 2024.zip'.
    """
    base = Path(name).stem.lower().replace(" ", "_").replace("-", "_")
    # Pull off trailing 4-digit year
    m = re.match(r"^(.*?)_?(\d{4})$", base)
    if not m:
        return None
    prefix, year = m.group(1), int(m.group(2))
    return prefix, year


def _natural_sort_key(p: Path) -> tuple:
    """Sort 'foo.csv' before 'foo (1).csv' before 'foo (2).csv' etc."""
    name = p.name
    m = re.match(r".*\((\d+)\)\.csv$", name)
    return (0,) if not m else (1, int(m.group(1)))


def process_zip(zip_path: Path, out_root: Path) -> int:
    """Extract a single FPD zip and rename its 22 files into the target tree."""
    parsed = _parse_zip_filename(zip_path.name)
    if not parsed:
        print(f"  [skip] cannot parse name: {zip_path.name}")
        return 0
    prefix, year = parsed

    # Find report mapping
    if prefix not in REPORT_PATTERN:
        print(f"  [skip] unknown report prefix: {prefix} (from {zip_path.name})")
        print(f"          add it to REPORT_PATTERN in this script.")
        return 0
    target = REPORT_PATTERN[prefix]
    if isinstance(target, tuple):
        canonical_name, target_subdir = target
        is_season_aggregate = True
    else:
        canonical_name = target
        target_subdir = None
        is_season_aggregate = False

    # Extract zip to temp, then move CSVs
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(td)
        csvs = sorted(td.rglob("*.csv"), key=_natural_sort_key)
        if not csvs:
            print(f"  [skip] no CSVs in {zip_path.name}")
            return 0

        if is_season_aggregate:
            # Season-aggregate: just one file goes to <season>/season/
            if len(csvs) > 1:
                print(f"  [warn] {len(csvs)} CSVs in {zip_path.name} (expected 1 for season-aggregate); using first.")
            dst = out_root / str(year) / "season" / canonical_name
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(csvs[0], dst)
            print(f"  [ok] {zip_path.name} → {dst.relative_to(out_root)}")
            return 1

        # Weekly: 22 files map to WEEK_LABELS in order
        if len(csvs) != 22:
            print(f"  [warn] {zip_path.name} has {len(csvs)} CSVs (expected 22). "
                  f"Mapping the first {min(len(csvs), 22)}.")
        n = 0
        for i, src in enumerate(csvs[:22]):
            label = WEEK_LABELS[i]
            dst = out_root / str(year) / label / canonical_name
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            n += 1
        print(f"  [ok] {zip_path.name} → {n} files into {year}/<week>/{canonical_name}")
        return n


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--in-dir", required=True, help="Directory containing FPD zips")
    p.add_argument("--out-dir", default="data/raw",
                   help="Output root (default: data/raw)")
    args = p.parse_args(argv)

    in_dir = Path(args.in_dir).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    if not in_dir.exists():
        print(f"ERROR: {in_dir} does not exist", file=sys.stderr)
        return 1

    zips = sorted(in_dir.glob("*.zip"))
    if not zips:
        print(f"No .zip files found in {in_dir}", file=sys.stderr)
        return 1

    print(f"Found {len(zips)} zip file(s)")
    print(f"Output root: {out_dir}\n")

    total_files = 0
    for z in zips:
        total_files += process_zip(z, out_dir)

    print(f"\nDone. {total_files} CSVs written under {out_dir}")
    print("\nNext steps:")
    print("  1. python scripts/fetch_external_data.py --seasons 2023 2024 2025")
    print("  2. python scripts/build_database.py --seasons 2023 2024 2025")
    return 0


if __name__ == "__main__":
    sys.exit(main())
