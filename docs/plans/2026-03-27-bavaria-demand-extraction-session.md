# Bavaria Demand Extraction — Session Documentation (2026-03-27/28/29)

## Overview

Multi-day session establishing the full pipeline from eqasim population synthesis to DRT demand extraction for the Bavaria 30km scenario. This document captures the decision process, issues encountered, and solutions for dissertation traceability.

---

## 1. Starting Point: Comparing Bavaria vs Kelheim Demand Extraction

### Context
- Bavaria eqasim 100% population: 1.17M agents, 30km around Kelheim (9 Kreise)
- Kelheim Senozon 25% population: calibrated matsim-kelheim scenario
- Both had 1% demand extraction outputs to compare

### Key Finding: Missing Income-Dependent Scoring
The Bavaria 1% extraction used the **raw eqasim population** without numeric `income` attributes. The `IncomeDependentUtilityOfMoneyPersonScoringParameters` requires a numeric `income` attribute via `PersonUtils.getIncome()`, but eqasim stores `householdIncome` as categorical strings ("2000-2500", "5000+").

**Impact:** Without income-dependent scoring, all agents used uniform `marginalUtilityOfMoney = 1.0`. This produced lower budgets (mean 1.08 vs Kelheim's 2.87) and fewer sharing opportunities.

**Solution:** Created `RunAdaptEqasimPopulation.java` — a standalone preprocessing tool that:
- Reads the eqasim households CSV for household sizes
- Maps categorical `householdIncome` bands → MiD income groups (1-10)
- Derives numeric per-person `income` using the PreparePopulation.java formula (income band midpoint / household size)
- Also adapts: carAvailability (all→always, none→never), ptSubscription (boolean→full/none), subpopulation (null→person), MiD:hhgr_gr

### Three-Way Comparison Results (after fix)

| Metric | Bavaria full | Bavaria 30km-Kel | Kelheim base |
|--------|-------------|-------------------|--------------|
| Requests | 6,178 | 3,405 | 341 |
| Budget mean | 3.42 | 3.71 | 7.91 |
| maxCostPerKm mean | 0.80 | 0.69 | 0.43 |
| Distance mean (km) | 13.0 | 15.2 | 20.9 |
| Rides/request | 7.7 | 9.2 | 22.0 |
| Base mode: car | 51% | 47% | 72% |
| Base mode: bike | 38% | 38% | 21% |

**Key insight:** Budget gap (3.71 vs 7.91) and mode split divergence (47% car vs 72%) are population-level differences between eqasim and Senozon, not config issues. Bavaria eqasim produces many short bike-competitive trips (32% < 5km) vs Kelheim (8%).

---

## 2. Scoring Gap: Daily Monetary Constants

### Problem
`PlanCalcScoreAdapter.scoreLeg()` excluded `dailyMonetaryConstant` (car: -5.3 EUR/day) from trip-level scoring. This made car artificially cheap per-trip, inflating car's share in base mode comparison.

### Decision
Approximate by amortizing daily constants across total daily distance:
```
effectiveRate = dailyMonetaryConstant / totalDailyDistance
```
Added per trip as an additional distance-based cost. Implemented as `amortizeDailyMonetaryConstants` config toggle + `getDailyMonetaryConstantUtils()` in scoring adapters.

**Status:** Implemented but uncommitted at session end. Deferred to separate commit.

---

## 3. Trip-Level Spatial Filter

### Problem
The agent-level radius filter (`--filter-radius 30`) keeps agents with ANY activity inside 30km. This includes agents who live far away but have one shop trip inside the area — their commute trips (outside) inflate request counts.

### Decision Process
Analyzed 1.17M agents at 100%:

| Filter | Commute trips | At 25% |
|--------|--------------|--------|
| No filter | 320,589 | 80,147 |
| Any activity inside (agent-level) | 153,562 | 38,390 |
| Home AND work inside (agent-level) | 122,085 | 30,521 |
| Trip O+D both inside (trip-level) | 122,060 | 30,515 |

For commute trips, "trip O+D both inside" ≈ "home AND work inside" because commutes go home↔work. But trip-level filter is more flexible for future non-commute trip purposes.

**Decision:** Implement trip-level O+D filter in `DrtRequestFactory` (not agent-level). Configured via `ExMasConfigGroup`: `tripFilterRadiusKm`, `tripFilterCenterX/Y`.

---

## 4. ExMAS Scalability — Beeline Pre-Filter

### Problem
Bavaria 25% (61k requests) OOM'd at 30GB during pair generation and ride extension.

### Design: Beeline Distance Check
Before network routing for a candidate pair (i,j), compute Euclidean shared path distance and compare against `directDistance × maxDetourFactor`.

**Key insight (from discussion):** Since `beeline ≤ network_distance` (always), comparing beeline shared path against the network-derived limit has **zero false negatives**. No safety margin needed — if the beeline already exceeds the limit, the network path certainly does.

```
FIFO passenger i: beeline(O_i, O_j) + beeline(O_j, D_i) > directDistance_i × 1.5 → REJECT
```

**Result at 1%:** Rejected 169,290 candidate pairs before routing. Pair generation 8x faster (26.3s → 3.2s).

---

## 5. ExMAS Scalability — Post-Graph Degree-2 Pruning

### Problem
Even with beeline filter, too many pair rides survive as extension bases.

### Decision Process: Distance Savings Threshold
Analyzed savings distribution of degree-2 pairs from 1% data:
- **72% of pairs have NEGATIVE savings** (shared ride longer than solo sum)
- Median pair: -8% savings

Discussed `scale` parameter for `requiredSaving = scale × log₂(degree)`:

| scale | Degree 2 | Degree 3 | Degree 4 |
|-------|----------|----------|----------|
| 0.10 | 10% | 15.8% | 20% |
| 0.15 | 15% | 23.8% | 30% |
| 0.20 | 20% | 31.7% | 40% |
| 0.25 | 25% | 39.6% | 50% |

**Decision: `scale = 0.25`** (same as existing degree 3+ setting, now applied from degree 2).

Rationale: Higher-degree rides should save proportionally more distance. A degree-4 ride saving only 20% means the geometry isn't great. 50% at degree 4 means passengers genuinely share route segments.

**Important:** Pruned pairs remain in `allRides` as pair support for `tryExtend()` validation. The triple (A,B,C) can still be discovered via alternate pair paths even if pair (A,B) is pruned as extension base.

### A/B Comparison (1%, scale=0.25 for both, with vs without degree-2 pruning)

| Degree | No pruning | With pruning | Change |
|--------|-----------|-------------|--------|
| 1 (solo) | 2,459 | 2,514 | +2% (noise) |
| 2 (pairs) | 13,268 | 11,893 | -10% |
| 3 | 455 | 420 | -8% |
| 4 | 135 | 132 | -2% |
| **Total** | 16,335 | 14,976 | **-8%** |

Higher-degree rides slightly reduced (-8% at degree 3) — acceptable cost for 93% fewer extension bases.

---

## 6. Full Pipeline Design

### Pipeline Phases
1. **eqasim 100%** — regenerate population with latest fixes (MiD distance CDFs, OD calibration)
2. **Adapt attributes** — `RunAdaptEqasimPopulation` (householdIncome → numeric income)
3. **Permanent populations** — `RunCreatePermanentPopulations` (1%, 10%, 25%, 100% with 30km agent filter)
4. **Base simulation** — `RunBavariaBaseSimulation` (100 iterations, DMC annealing, exports travel_times.tsv)
5. **Demand extraction** — uses pre-computed travel times at any sample rate

### Key Design Decisions

**Permanent populations (Phase 3):**
- Agent-level filter: any activity within 30km of Kelheim
- Deterministic hash-based downsampling (same hash as MATSim's `String.hashCode() % 100`)
- Written as MATSim population XML files for reproducibility

**Base simulation (Phase 4):**
- Reset all eqasim legs to walk (strip interaction activities, insert walk legs)
- SubtourModeChoice assigns proper modes (car, pt, bike, walk — NO ride)
- `--sample 100 --capacity 25` — no further downsampling, but QSim uses 25% capacity factors
- Income-dependent scoring active
- Travel times exported via `DvrpOfflineTravelTimes.saveLinkTravelTimes()`

**Why no "ride" mode:** In SubtourModeChoice, ride (car_passenger) has no chain constraint and is freely assignable. With no daily monetary cost and teleported at 30 km/h, it dominates car. In reality, ride requires a household driver — not freely available. First run with ride: 85% ride, 3.7% car. Removed ride from mode choice.

**Travel time reuse:** Link travel times exported as TSV (287k links × 145 time bins × 900s bins). Loaded for demand extraction via `DvrpOfflineTravelTimes.loadLinkTravelTimes()` + `asTravelTime()` with time clamping for activities beyond 36h.

---

## 7. Issues Encountered and Fixed

### Vehicle ID Mismatch
Eqasim embeds household vehicle IDs in person attributes (e.g., "368346:car"). `VehiclesSource.defaultVehicle` creates generic vehicles that don't match. Fix: `VehiclesSource.fromVehiclesData` to load actual vehicle definitions.

### Eqasim Mode Names
Eqasim uses "bicycle" and "car_passenger" instead of MATSim's "bike" and "ride". Fix: strip all legs from plans, rebuild as activity-walk-activity structure, let SubtourModeChoice assign standard modes.

### Interaction Activities
Eqasim plans have multi-leg trips with interaction activities (walk → car_interaction → walk). Stripping only legs left orphaned interaction activities. Fix: filter to real activities only (exclude `*interaction`), then insert walk legs between them.

### ReplanningAnnealer Module Conflict
`config.addModule(new ReplanningAnnealerConfigGroup())` throws "Module already exists". Fix: use `ConfigUtils.addOrGetModule()`.

### Travel Time Out of Range
`DvrpOfflineTravelTimes.asTravelTime()` throws when called with time > maxTime (36h). Eqasim plans can have activities beyond 36h. Fix: wrap travel time with clamping: `Math.min(time, endTime)`.

### Disk Space
synpp cache (`C:/matsim_cache`) grew to 58 GB with stale entries from old pipeline runs. Old `matsim.simulation.run` entries alone: 40 GB. Cleaned old entries, freed 22 GB.

---

## 8. Extension OOM — Per-Request-Set Processing (Planned)

### Problem
25% extraction (53k requests → 6M pairs → 473k extension bases) OOM'd at 100 GB during degree-3 extension. The `.collect(Collectors.toList())` in `RideExtender` accumulated 39.6M extension candidates.

### Root Cause Analysis
The post-hoc percentage pruning (`keepTopFraction=0.3`) requires seeing ALL candidates before pruning. But holding all 39.6M candidates in memory exceeds 100 GB.

### Solution Design
Restructure extension from "per base ride" to "per request set":

**Current:** iterate base rides → each produces candidates for many request sets → collect ALL → prune

**New:** enumerate request sets (graph triangles) → for each, generate all variants from all base rides → percentage prune immediately → release memory

Memory bounded to one request set's variants (~3-30 entries) instead of all candidates.

**Implementation plan:** `docs/plans/2026-03-29-exmas-extension-scalability.md`

---

## 9. Modal Split Comparison (Preliminary)

Base simulation at 10%, 100 iterations, no ride mode:

| Mode | MATSim | MiD 2017 Bayern | Diff |
|------|--------|-----------------|------|
| car | 34.3% | 57.0% | -22.7% |
| pt | 3.2% | 8.0% | -4.8% |
| bike | 55.5% | 11.0% | +44.5% |
| walk | 7.0% | 22.0% | -15.0% |

Bike dominates because Kelheim-calibrated ASCs make bike attractive for the many short eqasim trips. ASC re-calibration needed for Bavaria population. This is expected — the Kelheim ASCs were calibrated for the Senozon population with different trip length distributions.

**Note:** Despite incorrect mode shares, the travel times from car traffic are still useful for demand extraction routing. Mode shares only affect which trips become DRT-eligible — the network congestion pattern is driven by actual traffic volumes.

---

## 10. Files Created This Session

### Java Tools
| File | Purpose |
|------|---------|
| `RunAdaptEqasimPopulation.java` | Convert eqasim attributes to Kelheim format |
| `RunCreatePermanentPopulations.java` | Create filtered+downsampled population files |
| `RunBavariaBaseSimulation.java` | Standalone MATSim simulation with travel time export |
| `RunExportTravelTimes.java` | Extract travel times from events file |
| `RunBavariaKelheim30kmComparison.java` | Hardcoded demand extraction runner |
| `BeelineDetourFilterTest.java` | Unit tests for beeline pre-filter |

### Pipeline
| File | Purpose |
|------|---------|
| `run_full_pipeline.sh` | Orchestrates all 4 phases sequentially |

### Design Documents
| File | Purpose |
|------|---------|
| `2026-03-26-exmas-scalability-design.md` | Scalability strategy (beeline + pruning) |
| `2026-03-26-exmas-scalability-implementation.md` | Implementation plan for beeline + pruning |
| `2026-03-29-exmas-extension-scalability.md` | Per-request-set extension plan |

### Config Changes (committed)
- `ExMasConfigGroup`: trip-level spatial filter params (`tripFilterRadiusKm`, `tripFilterCenterX/Y`)
- `DrtRequestFactory`: trip-level O+D spatial filter implementation
- `PairGenerator`: beeline pre-filter before routing
- `RunBavaria30kmDemandExtraction`: degree-2 pruning enabled (`scale=0.25`, `minDegree=2`), travel times loading, vehicle source fix

### Outputs
| Path | Contents |
|------|----------|
| `bavaria/output/kelheim_30km_100pct/` | Fresh eqasim 100% output (MiD distances) |
| `bavaria/output/populations/population_{1,10,25,100}pct_kelheim30km.xml.gz` | Permanent pre-filtered populations |
| `bavaria/output/base-simulation-10pct/travel_times.tsv` | Converged link travel times (287k links × 145 bins) |

---

## 11. Next Steps

1. **Implement per-request-set extension** (plan ready) — fixes 25% OOM
2. **Run 25% demand extraction** with new extension logic + travel times
3. **ASC re-calibration** for Bavaria eqasim population (bike too high at 55%)
4. **Daily monetary constant amortization** — commit and validate
5. **100% demand extraction** — validate scalability at full scale
6. **Feed results to ExmasCommuters** for ridepooling optimization
