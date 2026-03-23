# Distance Distribution Calibration & Mode Choice — Session Log

> **Date:** 2026-03-18 to 2026-03-20
> **Author:** Christoph Garritsen, assisted by Claude Code
> **Context:** Continuation of scenario generation. Focuses on matching ENTD/MiD distance distributions in the synthetic population, fixing the ENTD bicycle bug, and running boptx ASC calibration.

---

## 1. Objective

Ensure the synthetic population's trip distance distributions match the ENTD 2008 input data (which is close to MiD 2017 Bayern), and calibrate mode choice ASCs against MiD 2017 mode shares using boptx.

The previous session (2026-03-17) fixed the gravity model and commute distances but left secondary activity distances (shop, leisure) uncalibrated, and mode choice ASCs were manually set.

---

## 2. Distance Distribution Analysis

### 2.1 Three-Way Comparison: ENTD → Pipeline → MiD

We established a systematic comparison framework between:
- **ENTD 2008** (French HTS) — the pipeline's behavioral input
- **Pipeline output** — what the synthetic population produces
- **MiD 2017 Bayern** — the calibration target

**Key insight:** The pipeline internally converts ENTD routed distances to euclidean via `/1.3` (in `data/hts/entd/reweighted.py`, line 28: `df_trips["euclidean_distance"] = df_trips["routed_distance"] / 1.3`). The spatial assignment then places activities at these euclidean distances. So comparisons must be done consistently:
- Pipeline euclidean ↔ ENTD euclidean (= ENTD routed / 1.3)
- Pipeline euclidean × 1.3 ↔ ENTD/MiD routed

### 2.2 ENTD vs MiD — How Close Are They?

| Purpose | ENTD routed mean | MiD routed mean | Difference |
|---------|:----------------:|:---------------:|:----------:|
| Work | 15.0 km | 15.4 km | -3% |
| Education | 6.1 km | 7.8 km | -22% |
| Shop | 6.3 km | 5.3 km | +19% |
| Leisure | 11.6 km | 15.3 km | -24% |

**Decision:** ENTD and MiD are close enough for work/shop. Education and leisure differ more, but within acceptable range for a rural scenario. We target **ENTD distributions** for the spatial assignment (since ENTD is the pipeline's input) and use **MiD mode shares** for mode choice calibration.

### 2.3 Pipeline Distortions Identified

The pipeline (with `secondary_distance_scale: 1.4`) systematically shifted mass from short trips to medium trips compared to ENTD:

| Purpose | ENTD 0-2km | Pipeline 0-2km | Gap |
|---------|:----------:|:--------------:|:---:|
| Shop | 39.6% | 24.5% | -15.1pp |
| Leisure | 36.8% | 18.3% | -18.5pp |

**Root cause:** The `secondary_distance_scale: 1.4` inflated all secondary activity distances uniformly, and the `leisure_correction_factor: 2.0` (total 2.8× for leisure) destroyed the short-trip distribution. Combined with the fact that ENTD shop distances were already *above* MiD targets, scaling them up made things worse.

---

## 3. Fixes Applied — Secondary Distance Assignment

### 3.1 Purpose-Specific Distance Scaling (Fix 1)

**Problem:** A single `secondary_distance_scale` parameter scaled all secondary activities (shop, leisure, other) identically, but each purpose has different distance characteristics.

**Fix:** Added per-purpose correction factors to `CustomDistanceSampler`:
- `shop_correction_factor` (config, default 1.0)
- `leisure_correction_factor` (config, existing, default 2.0)
- `other_correction_factor` (config, default 1.0)

Applied in `sample_distances()` via a dict lookup instead of the hardcoded `if purpose == "leisure"` check.

**Files changed:**
- `synthesis/population/spatial/secondary/components.py`: `CustomDistanceSampler` constructor and `sample_distances()`
- `synthesis/population/spatial/secondary/locations.py`: `configure()` and `process()` — new config params wired through

### 3.2 Distance-Aware Discretization (Fix 3)

**Problem:** The `CustomDiscretizationSolver` snapped each activity to the single nearest facility, regardless of whether that facility preserved the target distance from the previous chain point. This could introduce large distance errors, especially when facilities are sparse.

**Fix:** Added K-nearest candidate selection. When K>1 and chain anchor is known, the solver queries K nearest facilities and picks the one minimizing `|actual_distance - target_distance|` from the previous chain point.

**Files changed:**
- `synthesis/population/spatial/secondary/components.py`:
  - `CandidateIndex.query_k()` — new method returning K nearest candidates
  - `CustomDiscretizationSolver.__init__()` — accepts `k_candidates` parameter
  - `CustomDiscretizationSolver.solve()` — accepts `target_distances`, implements distance-aware selection
- `synthesis/population/spatial/secondary/rda.py`:
  - `DiscretizationSolver.solve()` — added `target_distances` parameter
  - `AssignmentSolver.solve()` — passes distances to discretization solver
- `synthesis/population/spatial/secondary/locations.py`: new config param `secloc_k_candidates` (default 5)

### 3.3 Grid Search Optimization

Ran a Monte Carlo simulation to find optimal correction factors. For each parameter combination, simulated 5,000 home→purpose→home round trips using:
- ENTD distance CDFs (euclidean) as sampling source
- Actual facility locations from the 10% sample
- K-nearest discretization

**Results:**

| Parameter | Old | Optimized | Rationale |
|-----------|:---:|:---------:|-----------|
| `secondary_distance_scale` | 1.4 | **1.0** | Pipeline already converts ENTD routed→euclidean |
| `shop_correction_factor` | N/A | **0.8** | ENTD shop already above MiD; pipeline inflates further |
| `leisure_correction_factor` | 2.0 | **1.3** | 2.0 destroyed short leisure trips |
| `other_correction_factor` | N/A | **1.1** | Minor adjustment |
| `secloc_k_candidates` | N/A | **5** | Distance-aware facility selection |

### 3.4 Pipeline Run Results (v7)

Regenerated the 10% sample with new parameters. Improvement in short-trip recovery:

| Purpose | Old 0-2km | New 0-2km | ENTD target | Gap reduction |
|---------|:---------:|:---------:|:-----------:|:-------------:|
| Shop | 24.5% | 41.9% | 45.7% | 21pp → 4pp |
| Leisure | 18.3% | 37.8% | 41.6% | 23pp → 4pp |

The remaining ~4pp gap is structural — facility discretization cannot produce trips shorter than the nearest facility distance.

### 3.5 replacement.py Fix

The Bavaria-specific `bavaria/locations/synthesis/replacement.py` overrides the base primary locations stage but had a copy-paste bug: its `configure()` didn't declare the `shared_facility_assignment` and `education_location_source` config options that `base.execute()` reads. This caused `PipelineError: Config option shared_facility_assignment is not requested` on clean pipeline runs.

**Fix:** Added `context.config("education_location_source", "bpe")` and `context.config("shared_facility_assignment", True)` to `replacement.py`'s `configure()`.

---

## 4. ENTD Bicycle Mode Mapping Bug

### 4.1 Discovery

The 10% v7 scenario produced **0% bicycle trips** despite bicycle availability being correctly set in the population (80% of adults). Investigation revealed:

- ENTD uses code `V2_MTP = "2.2"` for bicycle
- The pipeline's `MODES_MAP` in `data/hts/entd/cleaned.py` used prefix `"2.20"` to match bicycle
- Python's `str.startswith("2.20")` does **not** match `"2.2"` → all 1,349 bicycle trips were misclassified as `"car"` (matched by the earlier `("2", "car")` rule)

### 4.2 Impact

- No bicycle trips in ENTD data → no bicycle distance CDFs → no bicycle plans in synthetic population
- HTS template matching never assigned bicycle mode to synthetic persons
- DMC could never choose bicycle because no initial bicycle plans existed
- All ENTD bicycle trips inflated the car distance distribution

### 4.3 Fix

Changed `data/hts/entd/cleaned.py` line 31:
```python
# Before (never matched "2.2"):
("2.20", "bicycle"),
# After:
("2.2", "bicycle"),
```

**Result:** After pipeline rerun, bicycle mode share went from 0% to **5.4%** (MiD target: 7%).

### 4.4 Scope of Impact

This bug affects **all eqasim scenarios** using ENTD as the HTS source (not just Bavaria). It has been present since the original Île-de-France implementation. Any scenario using ENTD 2008 with the `"2.20"` prefix for bicycle will have zero bicycle trips in the ENTD processing.

---

## 5. Mode Choice Calibration with boptx

### 5.1 Setup

**boptx** (github.com/sebhoerl/boptx) is a black-box optimization framework for calibrating eqasim/MATSim scenarios. It:
1. Samples parameter values (ASCs, betas, capacity factors)
2. Runs MATSim simulations with those parameters via `--mode-choice-parameter:X` CLI overrides
3. Evaluates objectives (mode shares by distance band, traffic counts, stuck agents)
4. Optimizes using Differential Evolution, CMA-ES, or Opdyts

**Installation:** boptx is not a pip package. Cloned from GitHub and used via PYTHONPATH:
```
PYTHONPATH=boptx-upstream/src:boptx-upstream/src/boptx/eqasim:boptx-upstream/src/boptx/matsim
```

**Bug fixes required in boptx** (for DE + MATSim integration):
- `matsim.py:72`: `information` can be `None` when DE doesn't pass information dicts → added `if information is not None` guard
- `matsim.py:307`: same `None` issue for `simulation["information"]` → added `or {}` fallback
- `matsim.py:~290`: Bavaria's RunSimulation doesn't produce `urban.csv` → added auto-generation of dummy `urban.csv` from `eqasim_trips.csv`

### 5.2 Reference Data

Created `calibration/boptx/data/reference_trips.csv`: ENTD trip-level data with corrected bicycle mapping, reweighted per mode to match MiD 2017 Kelheim-area targets. boptx's `ModeShareObjective` uses this to compute mode shares per distance band (19 bins, 4 modes).

### 5.3 Traffic Counts Integration

Created `calibration/boptx/data/daily_flow.csv`: BASt SVZ 2021 counting stations mapped to MATSim link IDs via KDTree nearest-neighbor matching (186 stations → 358 directional links). Flows scaled to 1% sample and to 70% coverage (accounting for missing through-traffic, freight, and commercial vehicles that the synthetic population cannot represent).

Bavaria's `RunSimulation` doesn't support `--count-links`, so we wrote `link_flow_objective.py` — a custom objective that reads `output_links.csv.gz` (produced by standard MATSim) instead of `eqasim_counts.csv`.

### 5.4 Calibration Strategy

**1% sample** used for fast iteration (~5 min per MATSim eval instead of ~30 min for 10%).

**Config:** `config_kelheim_30km_1pct.yml` — 12,850 agents, fresh cache at `C:/matsim_cache_1pct`.

**Approach:** ASC-only calibration first. Beta (travel time coefficient) calibration was explored but rejected because:
- Betas have behavioral meaning from the mode choice study — they represent actual preference structures estimated via maximum likelihood
- Changing walk.beta from -0.162 to -0.50 to compensate for structural issues in the synthetic population undermines behavioral validity
- The walk-at-distance problem is better addressed by a walk distance cap (Section 6)

### 5.5 Calibration Run 1: ASC-Only (DE, 124 evaluations)

**Parameters:** 4 ASCs (car, bike, walk, pt)
**Objective:** ModeShareObjective only (L1 norm across 19 distance bins × 4 modes)
**Algorithm:** Differential Evolution

**Results — converged by eval ~30:**

| Parameter | Default | Calibrated |
|-----------|:-------:|:----------:|
| car.alpha_u | 0.4 | **4.44** |
| bike.alpha_u | -0.5 | **1.50** |
| walk.alpha_u | 1.8 | **3.72** |
| pt.alpha_u | 0.0 | **-2.08** |

**Mode shares (calibrated, 1% sample):**

| Mode | Calibrated | MiD target | Delta |
|------|:----------:|:----------:|:-----:|
| Walk | 29.1% | 17% | +12.1% |
| Bicycle | 6.8% | 7% | -0.2% |
| Car | 58.2% | 54% | +4.2% |
| Car passenger | — | 16% | — |
| PT | 5.9% | 7% | -1.1% |

Bicycle and PT are well calibrated. **Walk remains +12pp too high** — ASC calibration cannot fix this because the problem is distance-dependent: walk gets 23% of 2-5 km trips (target 3%) and 11% of 5-10 km trips (target 0.1%). The ASC shifts the entire walk curve up/down but cannot change its shape vs distance.

### 5.6 Calibration Run 2: ASCs + Betas + Flow (DE, 120 evaluations)

Added 3 travel time betas and flow objective to see if they help.

**Additional parameters:**
- `car.betaTravelTime_u_min` (bounds: -0.15 to -0.01, default: -0.042)
- `walk.betaTravelTime_u_min` (bounds: -0.30 to -0.10, default: -0.162)
- `bike.betaTravelTime_u_min` (bounds: -0.20 to -0.03, default: -0.093)

**Results:**
- `walk.betaTravelTime` hit its lower bound at -0.300 → wants even steeper decay
- Mode share objective: 0.0947 (essentially same as ASC-only)
- Flow objective: 0.505 mean relative error (structural — missing through-traffic)
- **Conclusion:** Beta calibration was rejected as it overrides behaviorally estimated parameters without fundamentally solving the walk problem

### 5.7 Walk Mode Share Analysis

Detailed analysis of why walk is over-represented:

| Distance band | Walk trips (sim) | Walk trips (ref) | Walk pkm contribution |
|---------------|:----------------:|:----------------:|:---------------------:|
| 0-0.5 km | 53% | ~80% | 3.4% of walk pkm |
| 0.5-1 km | 54% | ~50% | 6.0% |
| 1-2 km | 32% | ~15% | 10.6% |
| 2-5 km | 20% | ~3% | 23.5% |
| 5-10 km | 10.5% | ~0.1% | 16.7% |
| 10+ km | 6.9% | ~0% | **39.7%** |

**Key finding:** 28.5% of walk trips are >2 km, and these account for **80% of walk's person-kilometers**. The problem is worse in pkm than in trip share:
- Walk trip share: 26.9% vs 17% target (+10pp)
- Walk pkm share: 10.5% vs 2.7% target (+7.8pp)

This is a **mode choice model issue**, not a distance distribution issue. The eqasim walk utility function (`alpha + beta * travelTime`) has no distance-based cutoff — people can "walk" any distance if the utility happens to be higher than alternatives.

---

## 6. Walk Distance Cap

### 6.1 Rationale

Rather than distorting the behaviorally estimated travel time coefficients, we add a distance-based constraint that makes walk unavailable for unrealistically long trips. This is standard practice in discrete mode choice models (e.g., the Swiss national model caps walk at 3 km).

### 6.2 Implementation

Created `BavariaWalkUtilityEstimator.java` extending the core `WalkUtilityEstimator`:
- Computes euclidean trip distance via `PredictorUtils.calculateEuclideanDistance_km(trip)`
- Adds a -100 utility penalty for trips exceeding `MAX_WALK_DISTANCE_KM = 5.0`
- Registered in `BavariaModeChoiceModule` as `BavariaWalkUtilityEstimator`
- Config updated: `<param name="estimator" value="BavariaWalkUtilityEstimator" />`

The 5 km threshold (euclidean) corresponds to ~6.5 km routed, which is generous — MiD shows virtually no walk trips beyond 5 km routed. This only eliminates clearly unrealistic walk assignments without affecting legitimate short-distance walking.

### 6.3 Status

Java code created and compiled. Jar rebuilt. Config updated. **Not yet validated** — needs a calibration rerun with the walk cap active to measure improvement.

---

## 7. Pipeline and Scenario Inventory

### Generated Scenarios

| Directory | Sample | Distance fixes | Bike fix | Walk cap | ASC calibrated |
|-----------|:------:|:--------------:|:--------:|:--------:|:--------------:|
| `output/kelheim_30km_100pct/` | 100% | No | No | No | No |
| `output/kelheim_30km_10pct/` | 10% | No | No | No | No |
| `output/kelheim_30km_10pct_v7/` | 10% | Yes | No | No | No |
| `output/kelheim_30km_1pct/` | 1% | Yes | Yes | No | No |

### Configuration Files

| File | Sample | Purpose |
|------|:------:|---------|
| `config_kelheim_30km_100pct.yml` | 100% | Production (needs update) |
| `config_kelheim_30km_10pct.yml` | 10% | Intermediate calibration |
| `config_kelheim_30km_1pct.yml` | 1% | Fast boptx calibration |

### Calibration Artifacts

| File | Description |
|------|-------------|
| `calibration/boptx/calibrate.py` | boptx calibration script |
| `calibration/boptx/link_flow_objective.py` | Custom flow objective using output_links.csv.gz |
| `calibration/boptx/data/reference_trips.csv` | ENTD trips reweighted to MiD mode shares |
| `calibration/boptx/data/daily_flow.csv` | BASt counts mapped to MATSim links (1%, 70%) |
| `calibration/boptx/optimization_mode_share_DE_run1.p` | DE run 1 results (ASC-only, 124 evals) |
| `calibration/boptx/optimization_mode_share_v2_backup.p` | DE run 2 results (ASCs+betas, 120 evals) |

---

## 8. Files Created/Modified This Session

### New files

| File | Purpose |
|------|---------|
| `config_kelheim_30km_1pct.yml` | 1% pipeline config for fast calibration |
| `calibration/boptx/calibrate.py` | boptx calibration orchestration |
| `calibration/boptx/link_flow_objective.py` | Custom traffic count objective |
| `calibration/boptx/data/reference_trips.csv` | MiD-reweighted ENTD reference |
| `calibration/boptx/data/daily_flow.csv` | BASt-to-MATSim link mapping |
| `BavariaWalkUtilityEstimator.java` | Walk distance cap (5 km) |

### Modified files

| File | Change |
|------|--------|
| `data/hts/entd/cleaned.py` | Bicycle mode mapping `"2.20"` → `"2.2"` |
| `synthesis/population/spatial/secondary/components.py` | Purpose-specific scaling + K-nearest discretization |
| `synthesis/population/spatial/secondary/rda.py` | Pass target distances to discretization solver |
| `synthesis/population/spatial/secondary/locations.py` | New config params (shop/other factors, k_candidates) |
| `bavaria/locations/synthesis/replacement.py` | Added missing config declarations |
| `BavariaModeChoiceModule.java` | Registered BavariaWalkUtilityEstimator |
| `boptx-upstream/src/boptx/matsim/matsim.py` | 3 bug fixes for DE+MATSim integration |

---

## 9. Next Steps

1. **Rerun ASC calibration** with walk distance cap active — expect significant walk improvement
2. **Validate calibrated scenario** against BASt traffic counts and MiD distance distributions
3. **Regenerate 100% scenario** with all fixes (ENTD bike, distance scaling, walk cap, calibrated ASCs)
4. **DRT demand extraction** on the final calibrated 100% scenario

---

## 10. Key Learnings

### On Distance Calibration
- The eqasim pipeline's ENTD-to-euclidean conversion (`/1.3`) means the `secondary_distance_scale` should be ~1.0, not 1.4. The previous value of 1.4 was compensating for issues elsewhere.
- Purpose-specific distance scaling is essential: ENTD shop distances are already above MiD targets, while ENTD leisure distances need upward correction. A single scaling factor cannot serve both.
- The K-nearest discretization (Fix 3) provides marginal improvement (~1-2pp) over nearest-neighbor. The main gains come from correct scaling parameters (Fix 1).
- Grid search simulation on facility locations is an effective and fast way to optimize distance parameters without running the full pipeline.

### On Mode Choice Calibration
- ASC calibration alone cannot fix distance-dependent mode share errors. If walk gets 20% of 5 km trips, no ASC value can fix this while also preserving walk's share at 0.5 km trips.
- Calibrating travel time betas is tempting but questionable — it overrides behaviorally estimated parameters to mask structural issues in the synthetic population.
- A walk distance cap is the clean solution: it removes walk from unrealistic trip lengths without distorting the behavioral model's response to time/cost.

### On the ENTD Bicycle Bug
- String prefix matching (`str.startswith("2.20")`) is fragile. The ENTD data stores `"2.2"` not `"2.20"`, causing silent misclassification. This bug affects all eqasim ENTD pipelines, not just Bavaria.
- Always verify that HTS mode mappings produce the expected mode frequencies before trusting downstream results.

### On boptx Integration
- boptx is designed as a research tool with bare imports, not a pip package. Use PYTHONPATH, don't try to install it.
- The DE algorithm doesn't pass `information` dicts, causing NoneType errors in the MATSim evaluator. Three guard fixes needed.
- Bavaria's `RunSimulation` doesn't produce `eqasim_counts.csv` or `urban.csv`. Custom objectives and dummy file generation bridge the gap.
- 1% sample is practical for calibration (~5 min/eval). 10% is 6× slower with minimal accuracy gain for ASC calibration.
- Traffic count calibration is limited by missing through-traffic, freight, and commercial vehicles. Scaling reference counts to ~70% accounts for this.
