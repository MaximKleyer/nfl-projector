"""Build the DuckDB warehouse from the renamed FPD CSVs + external data.

Prerequisites:
  1. Run scripts/rename_csvs.py to populate data/raw/<season>/<week>/...
  2. Run scripts/fetch_external_data.py to populate data/raw/external/

USAGE:
  python scripts/build_database.py --seasons 2023 2024 2025
"""

from __future__ import annotations
import argparse
from pathlib import Path
import sys

from nfl_projector_v1.ingest.database import build_warehouse


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--seasons", type=int, nargs="+", required=True)
    p.add_argument("--raw-dir", default="data/raw")
    p.add_argument("--db-path", default="data/processed/warehouse.duckdb")
    args = p.parse_args(argv)

    raw_dir = Path(args.raw_dir).expanduser().resolve()
    db_path = Path(args.db_path).expanduser().resolve()

    if not raw_dir.exists():
        print(f"ERROR: {raw_dir} does not exist. Run rename_csvs.py first.",
              file=sys.stderr)
        return 1

    print(f"Building warehouse from {raw_dir} → {db_path}")
    print(f"Seasons: {args.seasons}\n")
    build_warehouse(raw_dir=raw_dir, db_path=db_path, seasons=args.seasons)
    print(f"\nDone. Warehouse ready at {db_path}")
    print("\nNext steps:")
    print("  python -m nfl_projector_v1 backtest --seasons 2023 2024 2025")
    print("  python -m nfl_projector_v1 predict --season 2025 --week 1")
    return 0


if __name__ == "__main__":
    sys.exit(main())
