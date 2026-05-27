# nfl_projector_v1

Bottom-up NFL game prediction model.

**Status:** Step 1 complete (foundation: directory, config, data loaders).
Still WIP — see DESIGN.md for the full plan.

## Setup

```bash
cd nfl_projector_v1
pip install -e .
```

## Architecture

Projects player stat lines first, aggregates to team production, derives game
score. See DESIGN.md for the full architecture.

## Build status

- [x] Step 1: Directory + config + data loaders
- [ ] Step 2: Projection helpers (base.py)
- [ ] Step 3: Roster identification (depth charts + recent activity filter)
- [ ] Step 4: QB projection
- [ ] Step 5: RB + WR/TE projection
- [ ] Step 6: Team aggregation + points conversion
- [ ] Step 7: Game prediction (end-to-end)
- [ ] Step 8: Backtest (small slice)
- [ ] Step 9: Full backtest (2023-2025)
- [ ] Step 10: Compare to v1
- [ ] Step 11: CLI + final README
