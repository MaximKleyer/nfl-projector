"""Entry point: `python -m nfl_projector_v1 <command> [options]`.

Delegates to the CLI in cli.py. Run with no arguments (or -h) to see
available sub-commands: predict, backtest, refresh-depth-charts, status.
"""
import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
