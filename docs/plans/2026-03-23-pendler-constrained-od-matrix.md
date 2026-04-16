# Pendler-Constrained OD Matrix for Work Location Assignment

**Date:** 2026-03-23
**Status:** Design approved, pending implementation plan
**Author:** Christoph Garritsen + Claude

## Problem

The eqasim Bavaria pipeline uses a gravity model to distribute workers across destination Gemeinden. The gravity model produces reasonable distance distributions but systematically under-attracts major employment centers:

- Regensburg Stadt receives 14.3% of cross-Kreis flows (gravity) vs 37.2% official (BA Pendlerverflechtungen)
- Pearson r = 0.81 on flow shares — decent rank ordering but wrong magnitudes
- The calibrated 25% Kelheim scenario gets Kelheim's own flows better (37.6% to Reg.St) because it was calibrated with real facility data

This matters because the dissertation is about commuter ridepooling. Incorrect commuter flows → incorrect demand patterns → misleading pooling potential estimates.

## Solution: Two-Level Hierarchical OD Matrix

Replace the single-level gravity model with a two-level hierarchy:

1. **Kreis level**: Official BA Pendlerverflechtungen determine the share of workers going from origin Kreis to each destination Kreis
2. **Gemeinde level**: Gravity model distributes workers within each destination Kreis (employee count × distance decay)

### Formula

For origin Gemeinde `g_i` in Kreis `K_o`, the probability of destination Gemeinde `g_j` in Kreis `K_d`:

```
P(g_j | g_i) = P_pendler(K_d | K_o) × P_gravity(g_j | K_d, g_i)
```

Where:
- `P_pendler(K_d | K_o)` = official Pendler share from BA data (including internal flows)
- `P_gravity(g_j | K_d, g_i)` = gravity-based weight within destination Kreis, normalized:
  `employees_j × exp(slope × distance(g_i, g_j)) / Σ_{g_k ∈ K_d} employees_k × exp(slope × distance(g_i, g_k))`

### Internal (same-Kreis) flows

The BA Pendlermatrix only records cross-Kreis flows. Internal share is computed as:

```
P_pendler(K_o | K_o) = 1 - Σ_{K_d ≠ K_o} P_pendler(K_d | K_o)
```

Where cross-Kreis outbound shares are derived from:
```
P_pendler(K_d | K_o) = auspendler(K_o → K_d) / total_employed_residents(K_o)
```

`total_employed_residents` comes from the existing employee-at-residence data (Beschäftigte am Wohnort column in the Pendlermatrix).

### Handling destinations outside study area

The official Pendlermatrix includes flows to all German Kreise. To keep shares undistorted:

1. Sample destination Kreis from **full** official distribution (including outside-study-area Kreise)
2. If destination is inside study area → assign Gemeinde + facility normally
3. If destination is outside study area → **drop this worker from the population**

This produces a population representing "100% of people commuting within the study area" with correct, undistorted Kreis-level shares. Workers who commute to Ingolstadt/München/etc. are correctly excluded rather than artificially redistributed.

### Education trips

No change — keep pure gravity model for education. The BA Pendlermatrix covers SV-pflichtig Beschäftigte only.

## Data Requirements

| Data | Source | Status |
|---|---|---|
| BA Pendlerverflechtungen (Kreis) | `krpend-k-0-202306-xlsx.xlsx` | Downloaded |
| Employee counts per Gemeinde | `a6502c_202200.xlsx` | Existing in pipeline |
| Gemeinde centroid distances | `bavaria.gravity.distance_matrix` stage | Existing in pipeline |
| Gemeinde→Kreis mapping | AGS code prefix (first 5 digits) | Trivial |

## Pipeline Integration

### What changes

Only **one stage**: `bavaria.gravity.model` (aliased as `data.od.weighted`)

Current signature (unchanged):
```python
def execute(context):
    ...
    return df_work_od, df_education_od  # both are Gemeinde×Gemeinde probability matrices
```

The stage returns the same DataFrame format: `[origin_id, destination_id, weight]` where `weight` is P(destination | origin). Downstream stages (`primary.candidates`, `primary.locations`) are untouched.

### New config

```yaml
# Path to BA Pendlerverflechtungen Excel (null = pure gravity, backward compatible)
pendler_od_path: bavaria/krpend-k-0-202306-xlsx.xlsx
```

### New stage dependencies

```python
def configure(context):
    context.stage("bavaria.gravity.distance_matrix")  # existing
    context.stage("bavaria.ipf.attributed")           # existing (population)
    context.stage("bavaria.data.census.employees")    # existing
    context.config("pendler_od_path", None)           # NEW: optional Pendlermatrix
    context.config("gravity_slope", DEFAULT_SLOPE)    # existing, used for within-Kreis
```

## Algorithm

```
1. Load Pendlermatrix → build P_pendler(K_d | K_o) for all Kreis pairs
   - Parse "Auspendler Kreise" sheet
   - For each origin Kreis: normalize by Beschäftigte am Wohnort
   - Compute internal share as 1 - Σ(cross-Kreis outbound)

2. Load existing gravity inputs: distances, employees, population

3. Build Gemeinde→Kreis lookup from AGS codes

4. For each origin Gemeinde g_i:
   a. Determine origin Kreis K_o
   b. For each destination Kreis K_d (including K_o):
      - Get P_pendler(K_d | K_o)
      - For each Gemeinde g_j in K_d:
        - Compute gravity weight: employees_j × exp(slope × dist(g_i, g_j))
      - Normalize within K_d
      - P(g_j | g_i) = P_pendler × P_gravity_normalized
   c. For destination Kreise outside study area:
      - Assign combined probability to a sentinel "outside" destination
      - Workers sampled to "outside" are dropped downstream

5. Output: same format as current gravity model
```

## Validation

After implementation, re-run the three-way comparison:

| Metric | Target |
|---|---|
| Kreis-level flow shares vs official | Should be near-exact (by construction) |
| Gemeinde-level distance distribution | Should remain close to MiD/ENTD reference |
| Kelheim→Regensburg Stadt share | ~47.8% (currently 21.5% eqasim, 37.6% K25) |
| Pearson r (cross-Kreis shares) | >0.95 (currently 0.81) |

## Scientific Justification

- Two-level OD models (macro-level constraints + micro-level distribution) are standard in transport planning (Ortúzar & Willumsen 2011, Ch. 5)
- IPF/Furness method for constrained matrix estimation has decades of theoretical backing
- Using observed Pendler data as constraints is strictly better than unconstrained gravity — it adds real information without losing the distance-based realism of gravity
- The within-Kreis gravity distribution is still needed because Pendler data is not available at Gemeinde level

## Backward Compatibility

- `pendler_od_path: null` (default) → pure gravity model, identical to current behavior
- Existing configs work unchanged
- Only the `bavaria.gravity.model` stage is modified
