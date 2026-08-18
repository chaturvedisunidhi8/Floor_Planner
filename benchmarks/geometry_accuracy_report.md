# Geometry Accuracy — Milestone Report

Phase 2 of the Accuracy & Architectural Precision milestone: root-cause fixes
for the geometry axes measured by the precision metrics — door clearance at
wall corners, corridor width, bathroom usability, room proportions and unused
land. All five targets moved decisively; no feasible brief was made infeasible
and the strict validation gate was never weakened.

## What changed

### Files modified
| File | Change |
| --- | --- |
| `backend/app/geometry/solver/cp_sat.py` | Corrected access-run constraint with "self terms" (`starts_y[u] <= ends_y[u] - overlap`, and X variants) so a run is a genuine shared-run property of both rooms, not a weak nesting artefact. Hard/soft run ladder in `solve()`: try the hard tier `WALLS.door_clear_run` (6 ft), fall back to `WALLS.min_opening` (2.5 ft) only if the brief has access requirements and the hard tier is infeasible. Soft corner-clear bonus term (`_CORNER_CLEAR_WEIGHT` 80 cells²/edge) rewards edges whose shared run reaches `access_run_ft` (default 6 ft). New objective terms: natural-short aspect steering (`_ASPECT_STEER_WEIGHT` 30) and bounded uncovered-excess (`_UNCOVERED_ALLOWANCE` 0.15, `_UNCOVERED_WEIGHT` 2). |
| `backend/app/geometry/doors.py` | `_reposition_doors` ladder re-ordered to prioritise corner clearance `(3,1.5) -> (1.5,1.5) -> (0,1.5) -> (3,0) -> (0,0)`. `_doors_on_access_edges` two-pass: prefer candidates with run >= `max(min_opening, WALLS.door_clear_run)`, fall back to `min_opening` — matching the run the solver actually guarantees. |
| `backend/app/geometry/units.py` | `MIN_SIDE` raised to 4.5 for attached/common bathrooms, passage and foyer (gated by the feasibility probe: 96/96 corpus briefs stay feasible; the two infeasible controls stay infeasible). |
| `backend/app/geometry/solver/topology.py` | `RoomSpec.natural_short` + `_natural_short()`: sized rooms steer toward `snap(target.short_side)`, furniture rooms toward `snap(min(*FURNITURE[type]))`, corridor/outdoor/non-furniture rooms are not steered. |
| `backend/app/geometry/layout_engine.py` | `generate_solver` gains `access_run_ft` passthrough to `cp_sat.solve` (soft target, defaults to `WALLS.door_clear_run`). |

### Files created
| File | Purpose |
| --- | --- |
| `backend/scripts/probe_access_run.py` | Corpus probe: per hard-run value, how many feasible briefs x templates stay feasible (used to gate every hard raise). |
| `benchmarks/geometry_accuracy_report.md` / `.json` | This before/after report. Raw runs: `backend/benchmarks/arch_accuracy_{before,after}.json`, `backend/benchmarks/geo_accuracy_{before,after}.json`. |

## Numbers

Settings identical before/after. Architectural corpus: 12 templates
(TPL-001..012) x 2 seeds x 3 candidates at 1.5 s per solve, all 10 briefs.
Geo benchmark: 20 templates x 1 seed at 1.0 s per solve, both engines.

### Architectural corpus (192 feasible plans)
| Metric | Before | After |
| --- | --- | --- |
| Average quality score | 82.86 | **85.03** |
| Door-corner violations / plan | 4.77 | **0.083** |
| Door-spacing violations / plan | 0.000 | 0.000 |
| Opposing-door pairs / plan | 0.229 | 0.135 |
| Slender bathrooms | 63.4% | **0.0%** |
| Corridor min width (ft) | 4.06 | **5.69** |
| Uncovered land | 14.5% | 10.8% |
| Corridor fragmentation | 0.69 | 0.75 |
| Balconies without habitable room | 36 | 31 |
| Common-bath ↔ bedroom adjacency | 49.5% | 59.6% |
| Attached-bath ↔ bedroom adjacency | 76.3% | 85.1% |
| Bathroom-social shared walls | 92.8% | 78.6% |
| Bedrooms from circulation | 100% | 100% |
| Cross-ventilated rooms | 13.2% | 16.0% |
| Window corner / window-door violations | 0 / 0 | 0 / 0 |
| Geometry score | — | 97.9 |
| Furniture shortfalls | — | 532/1726 (30.8%) |
| Stray / off-grid edges, ledger, labels | — | 0 / 0, 192/192, 0 |

### Geo benchmark (200 plans)
| Metric | Before | After |
| --- | --- | --- |
| Average score | 82.99 | **84.40** |
| Area error (mean / p95) | 0.004 / 0.018 | 0.014 / 0.109 |
| Dimension error | 0.002 | 0.007 |
| Coverage (land used) | 85.7% | **89.2%** |
| Corridor fraction | 2.8% | 3.8% |
| Connectivity / feasibility / overlap | 100% / 100% / 0% | 100% / 100% / 0% |
| Forbidden-wall plans | 159 | 152 |
| Unique layouts | 159 | 159 |
| Runtime / plan | 2376 ms | 2513 ms |

### Geo benchmark per-brief
| Brief | Before | After |
| --- | --- | --- |
| 1BHK narrow 20x30 | 86.87 | **87.09** |
| 2BHK deep 25x50 | 80.98 | **85.48** |
| 2BHK irregular 26x38 | 83.93 | **85.43** |
| 2BHK wide 40x20 | 86.99 | 84.58 |
| 3BHK square 35x35 | 85.00 | 84.82 |
| 3BHK standard 30x45 | 84.00 | **84.42** |
| 4BHK 40x55 | 78.15 | **81.79** |
| 4BHK deep 30x65 | 78.04 | **81.59** |

## Verification
- `python -m pytest` — **486 passed** (308 s).
- `ruff check app scripts tests` — clean.
- Feasibility: the probe ran 96/96 feasible briefs x templates green with the
  corrected constraint, raised `MIN_SIDE` and the hard 6-ft run tier; the
  infeasible controls (1BHK 18x25, 3BHK 20x40) are still refused (100%
  feasibility across the benchmark). No constraint was weakened.
- The run ladder only drops from 6 ft to 2.5 ft on briefs where 6 ft is
  genuinely infeasible (a single corner-case brief, TPL-012 2BHK wide), and the
  fallback stays above the previous `MIN_OPENING` guarantee.

## Trade-offs
- **Area-accuracy traded for coverage.** Area error rose (0.004->0.014 mean,
  0.018->0.109 p95) while coverage rose 85.7%->89.2%. The uncovered term
  (weight 2, equal to the sized-area weight) makes brief-sized rooms neutral
  between their exact area and absorbing spare land, so on big plots the solver
  fills land at a small area cost. Per-brief scores still rose on 6 of 8 briefs
  and the big-plot briefs gained the most (4BHK +3.6, 2BHK deep +4.5), so the
  coverage gain outweighs the area cost; the p95 tail is concentrated in the
  4BHK briefs.
- **Runtime +5.7%** (2376 -> 2513 ms/plan) from the extra objective terms and
  the two-tier ladder; still well inside the per-candidate budget.
- **Corridor fragmentation** ticked up 0.69 -> 0.75 (a planner prefers width
  5.7 ft over fewer segments); the 2BHK wide and 3BHK square briefs lost ~1.5
  points as the wider corridor reshapes them.
- Balconies without a habitable room remain (31/192) — a residual quality gap,
  not a precision regression.

## Remaining weaknesses
- Time-limited CP-SAT is not bit-reproducible when a solve is cut off; tests
  assert determinism only on models that complete.
- The 6-ft hard run + 4.5-ft `MIN_SIDE` genuinely cannot fit TPL-012's wide
  edge brief; that plan currently runs the 2.5-ft fallback tier, which is
  within the old guarantee but below the new target.
- No structural/electrical/plumbing, furniture placement or multi-floor work in
  this milestone — out of scope.
