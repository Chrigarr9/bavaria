# Commuter OD Matrix Calibration — Session Log

> **Date:** 2026-03-23
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
| BA Pendlerverflechtungen | `krpend-k-0-202306-xlsx.xlsx` | Kreis→Kreis | 100% of SV-pflichtig cross-Kreis flows |
| Pendlerrechnung Eckzahlen | `19321-001r.xlsx` | Per Gemeinde | Total Auspendler + IOP per Gemeinde |
| Pendlerrechnung Verfl top-10 | `2024_Verfl_L09.csv` | Gemeinde→Gemeinde | Top 10 destinations per Gemeinde (~73% of Auspendler) |
| Pendlerrechnung IOP | `2024_IOP_Karte_L00.csv` | Per Gemeinde | Exact internal (within-Gemeinde) commuter counts |
| Employee counts (a6502c) | `a6502c_202200.xlsx` | Per Gemeinde | Beschaeftigte am Arbeitsort + Wohnort |

The BA data covers only SV-pflichtig Beschaeftigte. The Pendlerrechnung der Laender (tables 19321-*) is broader (includes Beamte and Selbstaendige) but produces slightly different totals. Both are valid for our purpose.

### 2.1 ARS vs AGS Code Systems

A critical implementation detail: the pipeline uses 12-digit ARS (Amtlicher Regionalschluessel) as `commune_id`, while some data sources use 8-digit AGS (Amtlicher Gemeindeschluessel). The mapping:

- ARS: Bundesland(2) + RegBez(1) + Kreis(2) + **Verband(4)** + Gemeinde(3) = 12 digits
- AGS: Bundesland(2) + RegBez(1) + Kreis(2) + Gemeinde(3) = 8 digits
- Conversion: `ags = ars[:5] + ars[9:]`

This caused initial matching failures until the code mapping was understood.

---

## 3. Design: Three-Level Approach

### 3.1 Brainstorming Phase

We explored three approaches:

**Approach A: Gemeinde OD where known, gravity for remainder** — Use IOP + top-10 Verfl directly, gravity fill for the ~27% tail. Simple, uses most precise data available.

**Approach B: Full Gemeinde OD, no gravity** — Same as A but distribute remainder evenly by employee count. Simplest but loses distance sensitivity for the tail.

**Approach C: Three-level hierarchy (Gemeinde → Kreis → gravity)** — Gemeinde flows first, Kreis Pendler for structure, gravity for fill. Most complex.

**Decision:** Approach A, enhanced with Kreis-level Pendler data for the outside fraction of the remaining tail. This became the "three-layer data fusion" approach.

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

### 3.3 Handling Outside Commuters

A key design decision from the brainstorming phase: workers whose destination falls outside the study area are **dropped from the population entirely**, rather than having their commute redistributed within the study area.

This is implemented across four pipeline stages:
1. `candidates.py` — randomly flags outside commuters based on `outside_fraction` per municipality, sets `has_work_trip=False`
2. `locations.py` — detects persons with work activities but no work location, removes all their activities
3. `output.py` — filters persons, activities, and trips to `valid_persons` only
4. `matsim/scenario/population.py` — same filter for MATSim XML output

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

**Result:** Kreis-level Pearson r = 1.0000 (exact match by construction). But Gemeinde-level r = 0.50 — the gravity sub-distribution within each Kreis was mediocre.

### 4.2 Phase 2: Outside Commuter Dropping (commits 951c5f5–e6db92e)

Initially, the `_outside` share was renormalized away (redistributed to within-study destinations). This inflated within-study flows and distorted Kreis shares when compared against the full official distribution.

**Fix:** Drop outside commuters from the population entirely. This required changes across the pipeline:
- 5 bug-fix commits to handle NaN geometries, missing persons in output stages, and consistency issues
- Key learning: the pipeline has many stages that load persons/activities independently, and all need consistent filtering

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

**Iterations on the gravity fill logic:**

| Approach | All r | Internal r | Cross r | Issue |
|----------|-------|-----------|---------|-------|
| Fixed 0.37 fill ratio | 0.93 | 0.55 | 0.99 | Heuristic, no principled basis |
| Exact Eckzahlen, all remaining within-study | 0.90 | 0.34 | 0.97 | Remaining includes outside flows, inflates cross-Gemeinde |
| Exact Eckzahlen, proportional split | 0.93 | 0.52 | 0.99 | Within/outside ratio from top-10 not accurate |
| Exact Eckzahlen, Kreis Pendler outside split | 0.93 | 0.51 | 0.99 | Best principled approach |

The heuristic and the principled approach produce nearly identical results. The Kreis Pendler outside fraction is the authoritative source for the tail's outside/within split.

---

## 5. Results

### 5.1 Progression Summary

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
**Internal r measured against Eckzahlen-based reference (IOP / (IOP + total Auspendler)), not the earlier biased reference.

### 5.2 Spatial Quality Pattern

The model quality is **highest in the core study area** (Kelheim, Regensburg Stadt/LK, Neumarkt, Schwandorf) with per-Kreis r > 0.90. Quality is lower in peripheral Kreise (Eichstaett, Pfaffenhofen, Landshut LK) because:

1. These Kreise have 50-85% of commuters going outside the study area (to Muenchen, Ingolstadt, Landshut Stadt)
2. The top-10 Verfl data captures fewer within-study flows for these peripheral origins
3. The gravity fill for the small within-study remainder overestimates internal shares

This is acceptable because:
- Peripheral commuters leaving the study area are dropped from the population (correct by design)
- The few remaining within-study commuters from peripheral Kreise are a small fraction of total DRT demand
- The DRT service area is centered on Kelheim, where the model is most accurate

### 5.3 Internal Share Analysis

The model systematically overestimates internal shares by ~15pp on average (model 36.7% vs official 21.2%). This is because:

1. IOP counts are exact (correct)
2. But the gravity fill for the ~27% Auspendler tail only distributes to cross-Gemeinde destinations
3. Auspendler by definition leave the Gemeinde — so gravity fill correctly excludes internal
4. The overestimate comes from the total denominator: IOP + within-study Auspendler is smaller than IOP + total Auspendler, because most Auspendler go outside the study area and are dropped

This is a study area boundary effect, not a model error.

---

## 6. Files Changed (All on `commuter-matrix` branch)

### New files
- `bavaria/gravity/pendler_data.py` — Pendler data parsers (4 functions)
- `tests/test_pendler_data.py` — 4 tests
- `tests/test_pendler_gravity.py` — 3 tests
- `scripts/validate_pendler_od.py` — Kreis-level validation script
- `scripts/plot_od_comparison.py` — scatter/heatmap/bar comparison plots
- `scripts/plot_od_map.py` — spatial quality choropleth maps

### Modified files
- `bavaria/gravity/model.py` — three-way config switch, three builder functions
- `synthesis/population/spatial/primary/candidates.py` — outside commuter flagging
- `synthesis/population/spatial/locations.py` — outside commuter population drop
- `synthesis/population/spatial/secondary/locations.py` — home-as-proxy for work geometry
- `synthesis/output.py` — consistent person/trip/activity filtering
- `matsim/scenario/population.py` — consistent filtering for MATSim XML
- `config_kelheim_30km_{1,10,100}pct.yml` — new config keys

### Data files used (not committed — in .gitignore)
- `data/germany/krpend-k-0-202306-xlsx.xlsx` — BA Pendlerverflechtungen
- `data/bavaria/a6502c_202200.xlsx` — Employee statistics
- `data/bavaria/2024_Verfl_L09.csv` — Pendlerrechnung top-10 Auspendler
- `data/bavaria/2024_IOP_Karte_L00.csv` — Internal commuters
- `data/bavaria/19321-001r.xlsx` — Pendlerrechnung Eckzahlen

---

## 7. Remaining Work

1. **100% population run** — ready to execute, all code tested with 1% sample
2. **Full Gemeinde OD data** — the Regionaldatenbank (table 19321-Z-21) has complete Gemeinde→Gemeinde flows (not just top-10). This would eliminate the gravity fill entirely and likely push overall r > 0.95. Requires free registration at regionalstatistik.de or download via BA interactive Pendler tool at Gemeinde level.
3. **Java ClassNotFoundException** — `matsim.simulation.prepare` fails with a missing class in the eqasim JAR. This is a pre-existing issue unrelated to the OD work. Needs the eqasim-java Bavaria module rebuilt.

---

## 8. Key Learnings

1. **Official data beats models.** Replacing gravity with Pendlerrechnung data improved overall r from 0.50 to 0.93. The gravity model is useful only for the small tail not covered by official data.

2. **Study area boundaries create systematic artifacts.** Peripheral Kreise with high outside fractions (Landshut LK: 85%) have poor within-study OD data coverage. The solution — dropping outside commuters — is correct but reduces the population significantly for those Kreise.

3. **Pipeline integration is the hard part.** The three-layer OD model itself was straightforward (3 functions). But making it work with the synpp pipeline's caching, stage dependencies, and output formatting required 5 bug-fix commits across 6 files. Each downstream stage that loads persons/activities needed consistent outside-commuter filtering.

4. **Multiple data sources with different code systems.** ARS (12-digit) vs AGS (8-digit) caused matching failures. The Verfl CSV uses ARS, the Eckzahlen Excel uses AGS, the pipeline uses ARS. Always verify code formats before building joins.
