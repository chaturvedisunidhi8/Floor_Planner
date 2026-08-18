# Adaptive Search + Architectural Quality — Milestone Report

Two milestones landed on top of topology search: an **architectural-quality
pass** that models doors and windows with realistic clearances (Phase 4/5) and
an **adaptive search** that biases each plan toward wet-room clustering,
daylight or a spine corridor (Phase 6), plus the render/quality tooling
(Phase 3e/6) and a runtime tradeoff study (Phase 7).

All numbers below are from the current codebase at `topology_candidates=3`,
`solver_budget=1.0s` unless stated otherwise.

## What changed

### Files modified
| File | Change |
| --- | --- |
| `backend/app/geometry/primitives.py` | Shared `clear_of_corners(lo, hi, width, clearance)` used by doors and windows. |
| `backend/app/geometry/doors.py` | `_door_between` returns `(Door, Wall)`; both door paths collect placed tuples and run `_reposition_doors` — a ladder `(door_spacing, corner_clearance)` → `(door_spacing, 0)` → `(0, 0)` that re-centres each wall's group while keeping `door_spacing` between doors and `door_corner_clearance` off corners; keeps original positions if no group position fits. |
| `backend/app/geometry/windows.py` | Windows placed via `clear_of_corners` then shifted to keep `window_door_spacing` from every door on the same wall line (`_door_blockers` / `_clear_of_doors`). |
| `backend/app/geometry/walls.py` | `WallConfig` constants: `door_corner_clearance=1.5`, `door_spacing=3.0`, `window_corner_clearance=1.0`, `window_door_spacing=3.0`. |
| `backend/app/geometry/quality.py` | Constants aliased to `WallConfig`; clearance/spacing quality metrics (`door_corner_violations`, `door_spacing_violations`, `window_corner_violations`, `window_door_violations`, `opposing_door_pairs`). |
| `backend/app/geometry/solver/topology.py` | Adaptive labels: `Spine corridor`, `Wet rooms near bedrooms`, `Daylight on the envelope` (part of the existing candidate set). |
| `backend/scripts/benchmark.py` | `--briefs`, `--report`, `--engine both` options; solver plans capped via budget. |

### Files created
| File | Purpose |
| --- | --- |
| `backend/scripts/sanity_check.py` | Phase 3e gate: end-to-end solve + search-log shape + no-overlap + strict-validation gate + scored quality; exits nonzero on any failure. |
| `backend/scripts/architectural_report.py` | Aggregates quality metrics per brief/axis into `benchmarks/architectural_report.json`; winner tracking. |
| `backend/scripts/render_winner.py` | Renders winning plans to `benchmarks/renders/*.png`. |
| `benchmarks/adaptive_search_trail.json` | Base-vs-best trail from `topology_report.py`. |
| `benchmarks/architectural_report.json` | Corpus-wide quality aggregation. |
| `benchmarks/renders/` | 4 winning-plan PNGs (3BHK TPL-001/TPL-007 × seeds 100/200). |
| `backend/tests/test_openings.py` (+3) | Door-spacing group placement, short-wall collapse, window-clear-of-door; 22 tests total. |

## Door & window placement (Phase 4/5)

Single centred openings always get symmetric corner clearance, and window width
(`min(MAX_WIDTH, run*0.45)` with `min_run=6`) guarantees ≥1.65 ≥ 1.0 by
construction, so `clear_of_corners` is defensive there. The spacing pass
matters when two openings share one wall: `_reposition_doors` re-centres the
whole group, and `_clear_of_doors` slides a window the least amount to keep
3.0ft off any door on the same wall line.

Measured across the corpus: **door-spacing violations 0**, **window-corner 0**,
**window-door 0** on every one of the 158 feasible plans. Door-corner
violations are still common (≈760/158 total — the 1.5ft clearance is a soft
quality metric, not a gate) and are the main remaining quality cost, driven by
short walls in narrow rooms (utility, corridor-side bath).

## Adaptive search (Phase 6)

`topology_report.py` on three representative briefs, N=3, 20 plans each:

| Brief | base-score | best-score | delta | unique topologies | unique layouts |
| --- | --- | --- | --- | --- | --- |
| 1BHK narrow 20x30 | 84.2 | **86.8** | +2.6 | 5 | 19/20 |
| 3BHK standard 30x45 | 79.8 | **82.5** | +2.6 | 4 | 20/20 |
| 4BHK deep 30x65 | 74.2 | **76.8** | +2.6 | 6 | 19/20 (1 refused) |
| **Totals** | 79.9 | **82.1** | +2.3 | 6 | 58/60 |

The +2.3–2.6 search delta is stable across plan sizes. Adaptive candidates won
**39/60 (65%)**: Daylight 21, Wet rooms near bedrooms 14, Spine corridor 4,
plus Bedrooms-left/right zoning 6; base won 14. Connectivity 100%, overlap 0%.

Corpus-wide (`architectural_report.py`, 8 briefs × 20 plans): **adaptive
winners 114/158 (72%)** — Daylight 70, Wet rooms near bedrooms 32, Spine
corridor 12; base 44. Scores per brief range ≈80.8 (2BHK deep 25x50) to 86.8
(1BHK narrow, 2BHK wide 40x20).

## Benchmark side-by-side (`scripts/benchmark.py --engine both`, 200 plans)

| Metric | Solver (N=3) | Legacy |
| --- | --- | --- |
| Connectivity | 100.00% | ~85% (3BHK) |
| Feasibility | 100.00% | 100% |
| Overlap | 0.0% | 0.0% |
| Access satisfied | 100.00% | — |
| Door-conn | 100.00% | — |
| Door satisfied | 100.00% | — |
| Score avg | **82.7** | — |
| Unique layouts | 159/200 | — |
| Time per plan | ~2459 ms | ~12–24 ms |
| Infeasible briefs | correctly refused | correctly refused |

`benchmarks/engine_comparison.json` was regenerated at reduced scale
(`--variants 1 --solver-budget 1.0`).

## Runtime tradeoff (Phase 7)

3BHK standard 30x45, `--solver-budget 1.0`, 20 plans, score avg:

| Candidates N | score avg | time per plan | Δ score vs N=1 |
| --- | --- | --- | --- |
| 1 | 80.0 | ~1000 ms | — |
| 3 | 82.9 | ~2866 ms | +2.9 |
| 5 | 83.5 | ~4660 ms | +3.5 |

Diminishing returns: the first two extra candidates buy +2.9 points for ~1.9s
more; the next two buy +0.6 for ~1.8s. **N=3 is the sweet spot** for a
~3s/plan budget; N=1 for latency-critical deployments. This is why the default
in the earlier topology report is now understood as a knob: at N=5 full-corpus
runs cost ~5x the N=1 wall-clock.

## Verification

- `python -m pytest` — **477 passed**.
- `ruff check app scripts tests` — clean.
- `scripts/sanity_check.py` — passes (feasible, search log complete with ≥1
  survivor, no room overlap, no stranded rooms, strict gate, scored).
- Feasibility/connectivity/overlap never regress vs the previous milestones.

## Remaining weaknesses

- **Door-corner clearance** is the biggest remaining soft-quality cost (~5
  violations per plan, concentrated on short walls). Fixing it fully needs
  door-reach constraints in the geometry pass, not the placement pass.
- **Time-limited CP-SAT is not bit-reproducible**: e.g. 4BHK deep refused 1 of
  20 plans at a 1.0s budget (feasible run timed out, no incumbent returned), and
  winner scores vary between runs (79.0 optimal vs 81.4 time-limited for the
  Daylight candidate). Determinism holds only when every candidate finishes.
- **Runtime scales with candidates** (~3x at N=3, ~5x at N=5); tune
  `topology_candidates` per deployment.
- Renders were generated but not visually eyeballed in this session (PNGs in
  `benchmarks/renders/`, ~140KB each).
- Legacy engine comparison is wall-clock only — the solver's architectural
  metrics (door/window, adaptive quality) have no legacy analogue.
