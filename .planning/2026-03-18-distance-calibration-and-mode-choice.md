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

---

## 11. MiD 2017 Bayern Distance Calibration (2026-03-27)

> **Date:** 2026-03-27
> **Context:** Regenerated 100% Bavaria population still had 15-35pp too many short trips (<1km) across all purposes compared to MiD 2017 Bayern. The per-purpose correction factors from Section 3 improved medium distances but couldn't fix the distribution shape — they scale linearly but the ENTD-to-MiD mismatch is non-linear.

### 11.1 Problem Statement

Population comparison notebook (`population_comparison.ipynb`) showed systematic short-trip overrepresentation vs MiD 2017 Bayern (routed km):

| Purpose | Pipeline <1km | MiD <1km | Gap |
|---------|:------------:|:--------:|:---:|
| Work | 20.6% | 5.0% | +15.6pp |
| Education | 35.9% | 22.0% | +13.9pp |
| Shop | 30.2% | 25.0% | +5.2pp |
| Leisure | 33.1% | 18.0% | +15.1pp |

Root cause: the pipeline samples distances from **French ENTD** survey data. French travel patterns (shorter trips, different chain structures) don't match rural Bavaria.

### 11.2 Approach Evolution — Three Iterations

#### Iteration 1: Independent MiD CDF Sampling + Ring Search

**Idea:** Replace ENTD CDFs entirely with MiD 2017 Bayern CDFs. Sample distances directly by purpose. Add ring-based facility search (KDTree `query_radius`) instead of K-nearest to find facilities at the correct distance.

**Implementation:**
- `MiDDistanceSampler`: inverse CDF sampling via `np.interp(u, mid_cdf, mid_distances)` per purpose
- `CandidateIndex.query_ring()`: search facilities in `[target*(1-tol), target*(1+tol)]` from chain anchor
- Progressive fallback: tolerance 0.3 → 0.6 → 1.0 → K-nearest
- `commute_distance.py`: MiD work/education CDFs for primary commute targets

**Results:**
- Ring query: 93% hit rate, median error 109-235m — excellent facility matching
- But: **74% chain feasibility** — independent sampling breaks chain geometry
- For round-trip chains (home→shop→home), independently sampled d1 and d2 are rarely equal (required by triangle inequality with direct_distance≈0). The feasibility loop biases toward short distances because short pairs are more likely to be similar.

**Decision:** Ring-based search works great (keep it). Independent sampling doesn't (replace it).

#### Iteration 2: Quantile Mapping (ENTD Chains + MiD CDFs)

**Key insight from user:** The ENTD survey data contains real observed trip chains that are inherently feasible. Keep the ENTD chain structure and apply a CDF-to-CDF transform to calibrate marginal distances to MiD.

**Algorithm per leg:**
1. Sample `d_entd` from ENTD CDF (mode + travel_time band, preserving chain coordination)
2. Find quantile: `p = ENTD_CDF(d_entd)` — "this is the 30th percentile shop trip in ENTD"
3. Map to MiD: `d_mid = MiD_CDF_inverse(p)` — "the 30th percentile shop trip in Bavaria is 2.8km"

**Implementation:** `QuantileMappedDistanceSampler` in `components.py`

**Results:** Chain feasibility restored (~100%), but distances ~5-10pp off MiD targets. Investigation revealed the ENTD CDF per band has only **37 unique distance values** (from 648 survey records). The quantile mapping is lossy — many different `u` draws map to the same ENTD distance, which maps to the same quantile, collapsing the MiD target distribution. The ENTD CDF acts as a 37-step bottleneck.

**Decision:** Accept the imprecision. The alternative (bypassing ENTD) breaks chain feasibility. The quantile mapping is the best achievable with ENTD chain data and aggregated MiD CDFs.

#### Iteration 3: What Actually Matters — DRT Demand Filter

**Key insight from user:** The DRT demand extraction (`RunBavaria30kmDemandExtraction`) uses `CommuteFilter.COMMUTES_AND_EDUCATION` — it only extracts home↔work and home↔education trips. Intermediate chain trips (work→shop→work) are filtered out.

The "all consecutive trips" metric was distorted by French chain patterns (more midday errands), not by distance calibration errors. These sandwich trips are naturally short (you don't drive 15km for a lunch-break errand) and are excluded from DRT demand.

**Final validation — DRT-relevant trips only:**

| | <1km | MiD | <2km | MiD | <5km | MiD | <10km | MiD |
|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| **Commute** | 4.5% | 5.0% | 11.8% | 13.0% | 29.6% | 32.0% | 54.4% | 53.0% |
| **Education** | 17.7% | 22.0% | 36.5% | 38.0% | 65.1% | 58.0% | 76.5% | 77.0% |

**Commute trips match MiD almost exactly.** Education is within 4pp across all bands.

### 11.3 Final Implementation

| File | Change |
|------|--------|
| `components.py` | Added `QuantileMappedDistanceSampler` (ENTD chain + MiD quantile mapping) |
| `components.py` | Added `CandidateIndex.query_ring()` (KDTree radius search with progressive fallback) |
| `components.py` | Updated `CustomDiscretizationSolver` with `use_ring_query` parameter |
| `locations.py` | Config `use_mid_distances` switches between legacy ENTD and quantile-mapped mode |
| `commute_distance.py` | MiD work/education CDFs for primary commute distance targets |
| `config_kelheim_30km_*.yml` | `use_mid_distances: true`, correction factors reset to 1.0 |

**MiD CDF reference data** (hardcoded, from MiD 2017 Kurzreport Bayern, routed km / 1.3 * 1000 → euclidean meters):

| Euclidean m | 0 | 769 | 1538 | 3846 | 7692 | 15385 | 38462 |
|-------------|---|-----|------|------|------|-------|-------|
| Work | 0 | .05 | .13 | .32 | .53 | .76 | .95 |
| Education | 0 | .22 | .38 | .58 | .77 | .93 | .99 |
| Shop | 0 | .25 | .44 | .72 | .89 | .97 | 1.0 |
| Leisure | 0 | .18 | .30 | .50 | .66 | .82 | .95 |
| Other | 0 | .28 | .41 | .60 | .76 | .88 | .97 |

### 11.4 Key Learnings

**On distance calibration approaches:**
- Linear correction factors (Section 3) scale the mean but can't fix distribution shape. Quantile mapping can, but requires sufficient CDF resolution.
- The ENTD CDF has only ~37 unique values per mode/travel_time band — too coarse for precise quantile mapping. This is a fundamental data limitation, not an algorithmic one.
- Chain feasibility is non-negotiable. Independent per-leg sampling (even from perfect CDFs) produces 26% infeasible chains because the triangle inequality requires correlated distances within a chain.

**On what matters for DRT demand:**
- The "all consecutive trips" metric mixes primary trips (home→work) with chain-internal trips (work→shop→work). These have fundamentally different distance characteristics.
- Chain-internal trips are naturally short (quick errands near anchor points) and are correctly short — a 15km shop trip between two work stints is unrealistic.
- The DRT demand extraction already filters for commute and education trips only. The French chain structure issue (more midday errands than Bavarians) doesn't affect DRT demand.
- **Primary (home↔work, home↔education) distances are the only ones that matter for DRT, and they match MiD within 1-4pp.**

**On the ring-based facility search:**
- 93% hit rate with median error 109-235m — KDTree `query_radius` with progressive tolerance widening is very effective for facility matching in rural Bavaria.
- The 7% fallback to K-nearest occurs in sparse areas where no facility exists within ±100% of the target distance.
- Ring search from chain anchor (not relaxed location) is the correct approach — it directly matches the target distance rather than depending on the relaxation solver's intermediate placement.

### 11.5 Updated Scenario Inventory

| Directory | Sample | Distance fixes | MiD calibration | Bike fix | Walk cap |
|-----------|:------:|:--------------:|:---------------:|:--------:|:--------:|
| `output/kelheim_30km_100pct/` | 100% | No | No | No | No |
| `output/kelheim_30km_10pct/` | 10% | No | No | No | No |
| `output/kelheim_30km_10pct_v7/` | 10% | v7 factors | No | No | No |
| `output/kelheim_30km_1pct/` | 1% | v7 + quantile map | **Yes** | Yes | No |
| `output/populations/` | 1-100% | Pre-calibration XMLs | No | No | No |
