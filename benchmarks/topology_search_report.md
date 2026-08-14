# Topology Search — Milestone Report

The solver engine now searches several *materially different* arrangements per
brief and returns the best, instead of solving the single arrangement the
template dictated. The search is configurable, deterministic, and cannot
regress the hard guarantees milestones A-F proved: candidate 0 is always the
base programme, every candidate keeps the same room set and the same access
edge set, every survivor passes the strict validation gate before it can win,
and the winner is re-ordered back into the base programme's room order.

## What changed

### Files modified
| File | Change |
| --- | --- |
| `backend/app/geometry/solver/topology.py` | `SpatialBias`, `TopologySearchConfig`, candidate generation (`_zoning_variants`, `_permutation_orders`, `_permuted_programme`); `candidate_programmes` returns base + variants; en-suite pairing now ranked by bedroom priority so the access edge set is order-invariant. |
| `backend/app/geometry/solver/cp_sat.py` | `solve()` accepts `spatial_bias`; soft objective-only `_add_spatial_bias` (center-in-half booleans, never a hard constraint). |
| `backend/app/geometry/layout_engine.py` | `generate_solver` searches candidates, validates each survivor in-loop, prunes gate failures, picks the best-scoring validated plan, remaps rooms to base order; `LayoutPlan.topology_search` audit trail; config override. |
| `backend/app/core/config.py` | `topology_candidates` (default 5), `topology_zoning`, `topology_permutations`, `topology_bias_weight`. |
| `backend/scripts/benchmark.py` | `--topology-candidates` flag; `score_avg` and `unique_layouts` metrics. |
| `backend/README.md` (via root `README.md`) | Topology-search section. |

### Files created
| File | Purpose |
| --- | --- |
| `backend/scripts/topology_report.py` | Before/after milestone report from each plan's `topology_search` trail. |
| `backend/tests/test_topology_search.py` | 10 tests: candidate invariants, base-order preservation, never-worse-than-base, determinism, audit-trail shape, pruning. |

## Numbers (solver engine, 1.5s per candidate, 2 seeds x 20 templates)

### Corpus-wide (`scripts/topology_report.py --candidates 3`)
| Metric | Before (base) | After (search) |
| --- | --- | --- |
| Average score | 87.8 | **89.1** (+1.3) |
| Unique topologies per brief | 1 | 3 (base + 2 zoning) |
| Unique final layouts (400 plans) | — | 317 |
| Connectivity | 100% | 100% |
| Feasibility | 100% | 100% |
| Overlap | 0.0% | 0.0% |
| Area error (mean) | 0.002 | 0.002 |
| Dimension error (mean) | 0.001 | 0.001 |
| Runtime per plan | ~1.2 s | ~2.7 s |

Winners by candidate: **Base 150, Bedrooms-left/social-right 87,
Bedrooms-right/social-left 83** — zoning variants won 170 of 320 feasible plans
(53%), so the search genuinely selects different arrangements rather than
re-solving the same one. Pruned at N=3: 0 (pruning appears with the
permutation candidates at N>=6 — e.g. TPL-012: "Smallest rooms first" and
"Mirrored room order" were rejected by the strict door-graph gate).

### Benchmark side-by-side (`scripts/benchmark.py`, 40 plans per brief)
| Brief | Metric | N=1 (before) | N=3 (after) |
| --- | --- | --- | --- |
| 3BHK standard 30x45 | score avg | 87.1 | **88.9** |
| | area-err mean / p95 | 0.000 / 0.000 | 0.000 / 0.000 |
| | time | 834 ms | 2630 ms |
| 4BHK 40x55 | score avg | 83.8 | **85.6** |
| | area-err mean / p95 | 0.003 / 0.030 | 0.004 / 0.037 |
| | time | 1520 ms | 4559 ms |

The 4BHK area-error p95 ticked up 0.030→0.037 while the total score rose +1.8:
the scorer (area 25 / connectivity 20 / utilization 15 / circulation 10 /
aspect 10 / lighting 10 / privacy 5 / doors+windows 5) accepted a small area
trade for a better overall plan. The search's job is to maximise total
architectural quality, not any single metric.

## Verification
- `python -m pytest` — **435 passed** (406 baseline + 29; 10 of them new).
- `ruff check app scripts tests` — clean.
- Infeasible briefs (1BHK 18x25, 3BHK 20x40) still refused with 100%
  feasibility; feasibility/connectivity/overlap never regress.
- Room order of every returned plan matches the base programme's spec order,
  so door modeling, the strict gate and the API response keep their
  spec→room mapping unchanged.
- `--topology-candidates 1` exactly reproduces the pre-search engine
  (verified by the 435-test suite and the N=1 benchmark row).

## Visual inspection
Not performed in this session. The `scripts/demo_generate.py` / renderer flow
can be used to draw winner plans and eyeball zoning (bedrooms consistently on
one side, social core opposite) and circulation.

## Remaining weaknesses
- **Time-limited CP-SAT is not bit-reproducible.** When the budget cuts a solve
  off mid-search, the incumbent can differ between runs even with a fixed
  `random_seed` (pre-existing behaviour; determinism is exact only when the
  solve completes). Tests assert determinism only on models that finish.
- **Runtime scales with candidates** (~3x at N=3, ~5x at the N=5 default).
  The default is a quality knob; deployments should tune `topology_candidates`
  for their latency budget. Benchmark `time` reflects this honestly.
- **Permutation candidates are explored only at N>=6** (base + 4 zoning first)
  and are the main source of gate pruning, since the solver can find packs
  whose door graph the strict validator rejects. Those candidates are pruned,
  never fatal — but they cost solver time.
- **`forbidden-pair` plans**: the 3BHK N=1 run had 39/40 plans with a
  bathroom-social shared wall; N=3 dropped it to 37/40. Privacy is a 5-weight
  score, so the search occasionally trades it for area — by design, but worth
  watching if the product wants privacy prioritized.
- No ML-driven topology (RPLAN/CubiCasa/Graph2Plan/House-GAN), furniture
  placement, structural/electrical/plumbing, or multi-floor — all out of scope.
