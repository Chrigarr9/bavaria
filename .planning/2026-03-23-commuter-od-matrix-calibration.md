# Commuter OD Matrix Calibration — Session Log

> **Date:** 2026-03-23 to 2026-03-24
> **Author:** Christoph Garritsen, assisted by Claude Code
> **Context:** Replace the pure gravity model for work commute assignment with official commuter flow data. This is critical for the dissertation because incorrect commuter flows lead to incorrect DRT demand patterns and misleading pooling potential estimates.

---

## 1. The Problem

The eqasim Bavaria pipeline uses a doubly-constrained gravity model (Furness balancing with exponential distance decay) to distribute workers across destination Gemeinden. While this produces reasonable distance distributions, it systematically misallocates commuter flows:

- Regensburg Stadt receives 14.3% of Kelheim's cross-Kreis flows (gravity) vs 47.8% in official BA data
- Pearson r = 0.81 on Kreis-level flow shares — decent rank ordering but wrong magnitudes
- The gravity model over-localizes (too many short trips) and under-attracts major employment centers

This matters because the dissertation analyzes DRT ridepooling for Kelheim-area commuters. If the model sends workers to the wrong destinations, the demand patterns — and therefore the pooling potential — are wrong.

### 1.1 The Gravity Model (Baseline)

The original `bavaria.gravity.model` stage implements:

```
friction = exp(slope * distance + constant) + diagonal * I
flow = Furness_balance(population, employees, friction)
```

Parameters calibrated for rural Bavaria: `slope=-0.1` (flatter than IDF default -0.2), `diagonal=0.0` (no same-Gemeinde bonus), `constant=-2.4`. The model produces a full 246x246 Gemeinde probability matrix where weights sum to 1.0 per origin. Education and work commutes use the same matrix.

---

## 2. Data Sources Identified

| Data | Source | Granularity | Coverage |
|------|--------|-------------|----------|
| BA Pendlerverflechtungen | `krpend-k-0-202306-xlsx.xlsx` | Kreis->Kreis | 100% of SV-pflichtig cross-Kreis flows |
| Pendlerrechnung Eckzahlen | `19321-001r.xlsx` | Per Gemeinde | Total Auspendler + IOP per Gemeinde |
| Pendlerrechnung Verfl top-10 | `2024_Verfl_L09.csv` | Gemeinde->Gemeinde | Top 10 destinations per Gemeinde (~73% of Auspendler) |
| Pendlerrechnung IOP | `2024_IOP_Karte_L00.csv` | Per Gemeinde | Exact internal (within-Gemeinde) commuter counts |
| Employee counts (a6502c) | `a6502c_202200.xlsx` | Per Gemeinde | Beschaeftigte am Arbeitsort + Wohnort |

The BA data covers only SV-pflichtig Beschaeftigte. The Pendlerrechnung der Laender (tables 19321-*) is broader (includes Beamte and Selbstaendige) but produces slightly different totals. Both are valid for our purpose.

### 2.1 Data Source Selection Rationale

We explored multiple data sources:

- **BA Pendlerverflechtungen** — Only available at Kreis level. Comprehensive for cross-Kreis flows, but no within-Kreis Gemeinde resolution. This was our first data source.
- **Pendlerrechnung top-10 (Verfl)** — Available from the Pendleratlas (pendleratlas.statistikportal.de). Gives the 10 largest outbound destination Gemeinden per origin. Captures ~73% of total Auspendler (median). Downloaded as `2024_Verfl_L09.csv` for all of Bavaria.
- **Pendlerrechnung IOP** — Internal commuters per Gemeinde, exact count. Essential for getting internal shares right.
- **Pendlerrechnung Eckzahlen** — Per-Gemeinde totals (Auspendler, Einpendler, Bevoelkerung). Used to compute exact gravity fill amounts.
- **Full Gemeinde-to-Gemeinde OD** — Table 19321-Z-21 from Regionaldatenbank. Available but requires registration. Only top-10 was accessible without registration via the Pendleratlas. The full table would eliminate the gravity fill entirely.

**Decision:** Use the three-layer approach (IOP + top-10 Verfl + Kreis Pendler for outside fractions + gravity fill for the small tail). This achieves r=0.93 without requiring GENESIS registration.

### 2.2 ARS vs AGS Code Systems

A critical implementation detail: the pipeline uses 12-digit ARS (Amtlicher Regionalschluessel) as `commune_id`, while some data sources use 8-digit AGS (Amtlicher Gemeindeschluessel). The mapping:

- ARS: Bundesland(2) + RegBez(1) + Kreis(2) + **Verband(4)** + Gemeinde(3) = 12 digits
- AGS: Bundesland(2) + RegBez(1) + Kreis(2) + Gemeinde(3) = 8 digits
- Conversion: `ags = ars[:5] + ars[9:]`

This caused initial matching failures until the code mapping was understood. The Verfl and IOP files use 12-digit ARS (matching the pipeline), while the Eckzahlen Excel uses 8-digit AGS (requires conversion).

---

## 3. Design: Three-Level Approach

### 3.1 Approaches Considered

**Approach A: Gemeinde OD where known, gravity for remainder** (CHOSEN)
- Use IOP + top-10 Verfl directly, gravity fill for the ~27% tail
- Pros: Uses most precise data available, simple, three official data sources
- Cons: Top-10 only captures 73% of Auspendler; tail needs gravity

**Approach B: Full Gemeinde OD, no gravity**
- Same as A but distribute remainder evenly by employee count
- Pros: Simplest, no gravity model needed
- Cons: Loses distance sensitivity for the tail
- Rejected: distance sensitivity matters for correct trip lengths

**Approach C: Three-level hierarchy (Gemeinde -> Kreis -> gravity)**
- Gemeinde flows first, Kreis Pendler for remaining structure, gravity for fill
- Pros: Best of both worlds
- Cons: Most complex, diminishing returns over Approach A
- Rejected: added complexity for marginal improvement

**Decision:** Approach A with Kreis-level Pendler data for the outside fraction of the remaining tail.

### 3.2 Architecture: Three-Way Config Switch

The implementation supports three modes, selected by config:

```yaml
# Mode 1: pure gravity (backward compatible)
pendler_od_path: null
gemeinde_od_path: null

# Mode 2: Kreis-level Pendler + gravity within Kreis
pendler_od_path: germany/krpend-k-0-202306-xlsx.xlsx
gemeinde_od_path: null

# Mode 3: Gemeinde-level OD + gravity fill (most precise)
gemeinde_od_path: bavaria/2024_Verfl_L09.csv
gemeinde_iop_path: bavaria/2024_IOP_Karte_L00.csv
gemeinde_eckzahlen_path: bavaria/19321-001r.xlsx
pendler_od_path: germany/krpend-k-0-202306-xlsx.xlsx  # for outside fractions
```

All three modes return the same interface: `(df_work_matrix, df_education_matrix, df_outside_fractions)`.

**Rationale for keeping all three modes:** backward compatibility, ability to test each layer's contribution independently, and graceful fallback when data files are not available.

### 3.3 Handling Outside Commuters

**Problem:** Workers whose destination falls outside the 9-Kreis study area cannot be assigned a facility in the simulation. Simply redistributing their flows within the study area inflates cross-Kreis shares (we measured +15-45% inflation depending on Kreis).

**Alternatives considered:**
1. **Redistribute** — renormalize weights, dropping `_outside`. Simple but distorts shares.
2. **Strip work trips** — keep person, remove only work activities. Complex: breaks trip chain consistency.
3. **Drop from population** — remove person entirely. Clean, but reduces population.

**Decision:** Option 3 — drop from population. This is implemented across four pipeline stages:
1. `candidates.py` — randomly flags outside commuters based on `outside_fraction` per municipality, sets `has_work_trip=False`
2. `locations.py` — detects persons with work activities but no work location, removes all their activities
3. `output.py` — filters persons, activities, and trips to `valid_persons` only
4. `matsim/scenario/population.py` — same filter for MATSim XML output

**Impact:** 26.1% of workers (103,888 of 376,460) are dropped. The remaining population represents within-study-area commuters with undistorted OD shares. Non-working persons (children, retirees, students) are all retained.

The `secondary/locations.py` stage needed a special fix: outside commuters still have work-anchored trip chains from HTS matching, but no work geometry. The fix uses home location as a proxy for secondary activity chain anchoring (these persons are dropped from final output anyway).

---

## 4. Implementation Progression

### 4.1 Phase 1: Kreis-Level Pendler (commits 2232571–7a5355a)

**New files:**
- `bavaria/gravity/pendler_data.py` — `parse_pendler_matrix()` and `load_employed_at_wohnort()`
- `tests/test_pendler_data.py` — 4 tests against real BA data

**Modified files:**
- `bavaria/gravity/model.py` — added `build_pendler_constrained_matrix()`, two-way switch in `execute()`

**Logic:**
```
P(g_j | g_i) = P_pendler(K_d | K_o) * P_gravity(g_j | K_d, g_i)
```

Kreis shares from BA Pendlerverflechtungen. Within-Kreis distribution from gravity (employee x distance decay). Internal share computed from Beschaeftigte am Wohnort (a6502c) minus total Auspendler. Shares renormalized after removing `_outside` sentinel.

**Result:** Kreis-level Pearson r = 1.0000 (exact match by construction). But Gemeinde-level r = 0.50 — the gravity sub-distribution within each Kreis was mediocre. Main failures: (1) internal flows underestimated, (2) gravity over-attracts large employment centers within each Kreis.

### 4.2 Phase 2: Outside Commuter Dropping (commits 951c5f5–e6db92e)

Initially, the `_outside` share was renormalized away (redistributed to within-study destinations). This inflated within-study flows and distorted Kreis shares when compared against the full official distribution.

**Fix:** Drop outside commuters from the population entirely. This required changes across the pipeline — 5 bug-fix commits to handle NaN geometries, missing persons in output stages, and consistency issues. Key learning: the pipeline has many stages that load persons/activities independently, and all need consistent filtering.

**Outside fractions by Kreis (BA Pendler):**
- Kelheim: 18.7%, Reg. Stadt: 14.0%, Reg. LK: 16.5% (low — most flows stay within study area)
- Landshut LK: 84.7%, Straubing-Bogen: 79.3%, Eichstaett: 53.3% (high — peripheral Kreise)

### 4.3 Phase 3: Gemeinde-Level OD (commits 4bb06ef–4f68f2f)

**New functions:**
- `parse_gemeinde_od()` — reads Verfl CSV (top-10 Auspendler per Gemeinde) and IOP CSV (internal commuters)
- `load_total_auspendler()` — reads Eckzahlen for exact per-Gemeinde Auspendler totals
- `build_gemeinde_constrained_matrix()` — three-layer data fusion

**Logic for each origin Gemeinde:**
1. **Internal** = IOP count (exact from Pendlerrechnung)
2. **Cross-Gemeinde top-10** = Verfl counts for within-study destinations (exact)
3. **Remaining** = total Auspendler (Eckzahlen) - top-10 sum
4. **Remaining split:** Kreis-level BA Pendler outside fraction determines how much of the remaining goes outside (dropped) vs within-study (gravity fill)
5. **Gravity fill** for within-study remainder distributed by employee x exp(slope x distance) to non-top-10 destinations

### 4.4 Gravity Fill Iterations

The gravity fill logic went through several iterations:

| Approach | All r | Internal r | Cross r | Issue |
|----------|-------|-----------|---------|-------|
| Fixed 0.37 fill ratio | 0.93 | 0.55 | 0.99 | Heuristic, no principled basis |
| Exact Eckzahlen, all remaining within-study | 0.90 | 0.34 | 0.97 | Remaining includes outside flows, inflates cross-Gemeinde |
| Exact Eckzahlen, proportional split | 0.93 | 0.52 | 0.99 | Within/outside ratio from top-10 not accurate |
| Exact Eckzahlen, Kreis Pendler outside split | 0.93 | 0.51 | 0.99 | Best principled approach — CHOSEN |

**Decision:** Use Kreis-level Pendler outside fractions for the remaining tail's outside/within split. This is the most principled approach — the BA Pendler data is authoritative for outside fractions (covers all flows, not just top-10). The heuristic happened to produce similar results but has no principled basis.

### 4.5 Phase 4: Java Fix + 100% Run

**Java ClassNotFoundException** — The eqasim JAR (`bavaria-1.5.0.jar`) was only 47KB — a thin JAR missing all dependencies. The Maven shade plugin was configured but hadn't executed properly. Rebuilding with `mvn -Pstandalone package` produced the correct 127MB fat JAR with 40,454 classes including `RunPreparation`. The 100% cache already had correct JARs; only the 1% cache was affected.

**100% population run** completed successfully: 1,172,072 persons retained (103,888 outside commuters dropped). All 12 pipeline stages passed including MATSim simulation (1 iteration).

---

## 5. Results

### 5.1 OD Matrix Quality

| Metric | Pure Gravity | Kreis Pendler | Gemeinde OD (final) |
|--------|-------------|---------------|---------------------|
| Overall Pearson r | 0.50 | 0.50* | **0.93** |
| Internal r | 0.53 | 0.53* | 0.51** |
| Cross-Gemeinde r | 0.69 | 0.69* | **0.99** |
| Kreis-level r | 0.81 | 1.00 | **1.00** |
| Top-1 destination match | — | — | **97%** |
| Outside commuters dropped | 0% | 40.4% | 26.1% |
| Population retained | 100% | ~59.6% | ~73.9% |

*Kreis Pendler r at Gemeinde level is same as gravity because within-Kreis distribution still uses gravity.
**Internal r measured against Eckzahlen-based reference (IOP / (IOP + total Auspendler)).

### 5.2 Spatial Quality Pattern

The model quality is **highest in the core study area** (Kelheim, Regensburg Stadt/LK, Neumarkt, Schwandorf) with per-Kreis r > 0.90. Quality is lower in peripheral Kreise (Eichstaett, Pfaffenhofen, Landshut LK) because:

1. These Kreise have 50-85% of commuters going outside the study area (to Muenchen, Ingolstadt, Landshut Stadt)
2. The top-10 Verfl data captures fewer within-study flows for these peripheral origins
3. The gravity fill for the small within-study remainder overestimates internal shares

This is acceptable for the dissertation because:
- Peripheral commuters leaving the study area are dropped (correct by design)
- The few remaining within-study commuters from peripheral Kreise are a small fraction of total DRT demand
- The DRT service area is centered on Kelheim, where the model is most accurate

### 5.3 Internal Share Analysis

The model systematically overestimates internal shares by ~15pp on average (model 36.7% vs official 21.2% using Eckzahlen reference). This is a **study area boundary effect**, not a model error:

- IOP counts are exact (correct)
- But most Auspendler go outside the study area and are dropped
- The remaining within-study denominator (IOP + within-study Auspendler) is much smaller than the full denominator (IOP + total Auspendler)
- So internal / within-study appears inflated compared to internal / total

---

## 6. Population Comparison: Eqasim vs MATSim-Kelheim

### 6.1 Scenario Differences

| | MATSim-Kelheim 25% | Eqasim 100% (9 Kreise) |
|---|---|---|
| **Area** | Dilution area (~Kelheim LK + fringe) | 9 Kreise within 30km |
| **Population** | ~170K (scaled from 25%) | 1,172,072 |
| **Source** | Senozon/TU Berlin demand model | eqasim open-source pipeline |
| **HTS** | German MiD 2017 | French ENTD 2008 |
| **Work OD** | Calibrated with real data | Gemeinde-level Pendlerrechnung |
| **Mode choice** | Calibrated MATSim simulation | No mode choice (eqasim) |

### 6.2 Activity Timing Comparison

**Critical finding:** The initial comparison showed dramatic differences in activity start times. Investigation revealed this was an **interpretation bug**, not a real difference:

- MATSim-Kelheim XML plans have only `end_time` attributes (when you LEAVE an activity), not `start_time`
- Eqasim CSV has `start_time` (when you ARRIVE at an activity)
- The comparison code was plotting departure times for MATSim-Kelheim vs arrival times for eqasim

**Fix:** Reconstruct MATSim-Kelheim start times during XML parsing: `start_time = prev_leg.dep_time + prev_leg.trav_time`. After this fix, the profiles are much more comparable, though real differences remain due to ENTD vs MiD scheduling patterns.

**Remaining real differences:**
- Eqasim has a strong H-W-H-W-H (lunch break) pattern from ENTD — 25,469 persons with median 3.7km commute. This is realistic for short-distance commuters but more common in France.
- Eqasim shop trips are shorter (median 2.6km vs 5.4km) due to the secondary location assignment using only K=5 nearest candidates.

### 6.3 Income Distribution Fix

**Bug found:** The `AttributeAdapter.java` (population upsampling tool) had an incorrect mapping from eqasim household income bands to MiD income groups. The mapping was shifted up ~2 groups:

| Eqasim band | Wrong group | Correct group |
|-------------|:-:|:-:|
| 2500-3000 | 7 (4000-5000) | 5 (2000-3000) |
| 3500-4000 | 8 (5000-6000) | 6 (3000-4000) |
| 4000-5000 | 9 (6000-7000) | 7 (4000-5000) |

This inflated per-person income by ~30-50%, affecting mode choice budgets in the DRT demand extraction.

**Fix:** Corrected both the Java `AttributeAdapter.java` and the Python notebook port to align EUR/month ranges directly. Also added the Python `adapt_eqasim_to_kelheim()` function to the population comparison notebook for harmonized attribute comparison (car availability, PT subscription, income, household size).

### 6.4 Trip Distance Differences

| Purpose | MATSim-Kelheim median | Eqasim median | Explanation |
|---------|:-:|:-:|---|
| Work | 7.0 km | 5.5 km | Outside commuter drop removes long-distance workers |
| Home | 6.0 km | 2.3 km | H-W-H-W-H lunch trips inflate short home trips |
| Shop | 5.4 km | 2.6 km | K=5 nearest-candidate assignment |
| Leisure | 5.6 km | 3.2 km | Same secondary location bias |
| Education | 4.2 km | 1.9 km | Same |
| Other | 2.8 km | 2.7 km | Well-matched (both use similar methodology) |

---

## 7. Files Changed

### Bavaria repo (`commuter-matrix` branch)

**New files:**
- `bavaria/gravity/pendler_data.py` — 4 parser functions (Pendler, Gemeinde OD, IOP, Eckzahlen)
- `tests/test_pendler_data.py` — 5 tests
- `tests/test_pendler_gravity.py` — 3 tests
- `scripts/validate_pendler_od.py` — Kreis-level validation
- `scripts/plot_od_comparison.py` — scatter/heatmap/bar comparison plots
- `scripts/plot_od_map.py` — spatial quality choropleth maps
- `.planning/2026-03-23-commuter-od-matrix-calibration.md` — this document

**Modified files:**
- `bavaria/gravity/model.py` — three-way config switch, three builder functions
- `synthesis/population/spatial/primary/candidates.py` — outside commuter flagging
- `synthesis/population/spatial/locations.py` — outside commuter population drop
- `synthesis/population/spatial/secondary/locations.py` — home-as-proxy for work geometry
- `synthesis/output.py` — consistent person/trip/activity filtering
- `matsim/scenario/population.py` — consistent filtering for MATSim XML
- `config_kelheim_30km_{1,10,100}pct.yml` — new config keys

### Root Dissertation repo

- `matsim_scenarios/notebooks/population_comparison.ipynb` — full comparison notebook with harmonized attributes, reconstructed start times, spatial heatmaps
- `docs/plans/2026-03-23-pendler-constrained-od-implementation.md` — Kreis-level plan
- `docs/plans/2026-03-23-gemeinde-level-od-design.md` — Gemeinde-level design
- `docs/plans/2026-03-23-gemeinde-od-implementation.md` — Gemeinde-level plan

### matsim-libs (drt-demand-extraction)

- `AttributeAdapter.java` — fixed income band to MiD group mapping
- `AttributeAdapterTest.java` — updated test expectations

### Data files used (not committed — in .gitignore)
- `data/germany/krpend-k-0-202306-xlsx.xlsx` — BA Pendlerverflechtungen (Kreis)
- `data/bavaria/a6502c_202200.xlsx` — Employee statistics
- `data/bavaria/2024_Verfl_L09.csv` — Pendlerrechnung top-10 Auspendler (Gemeinde)
- `data/bavaria/2024_IOP_Karte_L00.csv` — Internal commuters (Gemeinde)
- `data/bavaria/19321-001r.xlsx` — Pendlerrechnung Eckzahlen (Gemeinde)

---

## 8. Remaining Work

1. **Full Gemeinde OD data** — Table 19321-Z-21 from Regionaldatenbank has complete Gemeinde->Gemeinde flows (not just top-10). Would eliminate gravity fill and push r > 0.95. Requires free registration at regionalstatistik.de or download via BA interactive Pendler tool at Gemeinde level.
2. **ENTD vs MiD scheduling** — The ENTD-derived activity timing produces more lunch-break patterns and shorter secondary trips than German reality. Could be improved by using MiD-based departure time distributions, but this is a deeper pipeline change.
3. **Secondary location distances** — The K=5 nearest-candidate approach produces shorter-than-reality shop/leisure distances. Increasing K or using a different distance-weighted sampling could help.

---

## 9. Key Learnings

1. **Official data beats models.** Replacing gravity with Pendlerrechnung data improved overall r from 0.50 to 0.93. The gravity model is useful only for the small tail not covered by official data.

2. **Study area boundaries create systematic artifacts.** Peripheral Kreise with high outside fractions (Landshut LK: 85%) have poor within-study OD data coverage. The solution — dropping outside commuters — is correct but creates apparent internal share inflation when comparing against full-population references.

3. **Pipeline integration is the hard part.** The three-layer OD model itself was straightforward (3 functions). But making it work with the synpp pipeline's caching, stage dependencies, and output formatting required 5 bug-fix commits across 6 files. Each downstream stage that loads persons/activities needed consistent outside-commuter filtering.

4. **Multiple data sources with different code systems.** ARS (12-digit) vs AGS (8-digit) caused matching failures. Always verify code formats before building joins.

5. **Always check what time fields actually contain.** The MATSim XML `end_time` vs eqasim `start_time` confusion produced a misleading comparison. Reconstructing arrival times from leg departure + travel time fixed this.

6. **Verify attribute mappings against their definitions.** The income band -> MiD group mapping was shifted by ~2 groups because the mapping was written without checking the actual MiD group EUR/month ranges. Always cross-reference both sides of a mapping.

7. **Three-layer data fusion is robust.** Using IOP (exact internal) + Verfl top-10 (exact cross-Gemeinde) + Kreis Pendler (outside fractions) + gravity (small tail) produces r=0.93 overall and r=0.99 for cross-Gemeinde flows. Each layer contributes something the others can't provide.
