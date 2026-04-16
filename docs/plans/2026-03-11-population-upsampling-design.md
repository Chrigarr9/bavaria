# Population Upsampling: 25% → 100% for DRT Demand Extraction

**Date:** 2026-03-11
**Updated:** 2026-03-13 (post-compatibility-analysis)
**Status:** Design approved, compatibility analysis complete, pending Java implementation

## Problem

The calibrated Kelheim MATSim scenario uses a 25% population sample. Ridepooling KPIs (pooling rate, detour factor, fleet efficiency) scale nonlinearly with demand density — a 25% sample underestimates pooling opportunities and overestimates cost-per-trip. Scientific literature (Bischoff & Maciejewski 2022, Ben-Dor et al. 2021) strongly recommends 100% population for on-demand service simulation.

## Solution

Upscale the population to 100% by merging:
1. **Calibrated Kelheim 25%** converged output plans (realistic travel behavior)
2. **Bavaria eqasim pipeline** 100% synthetic population for the Kelheim region (independent agents with realistic attributes + activity chains)

Then run DRT demand extraction on the merged 100% population using travel times from the calibrated 25% run.

## Why This Works

- **No artificial pooling inflation**: Bavaria pipeline generates each agent independently from census marginals + ENTD activity chains. No agent is a clone.
- **Travel times are representative**: MATSim's capacity scaling (flowCapacityFactor=0.25) means 25% traffic dynamics approximate 100% conditions. Kelheim is rural — congestion is minimal.
- **Agent attributes are realistic**: Bavaria pipeline uses same data sources (Zensus, MiD/KBA, OSM) as the original Kelheim scenario generation.
- **Activity chains are consistent**: Each synthetic agent has a complete, coherent daily plan (H-W-S-H etc.) from the pipeline, not jittered individual trips.

## Pipeline

```
STEP 0: Bavaria Pipeline  ✅ DONE (2026-03-12)
  Config: 7 Landkreise (09273, 09375, 09186, 09274, 09176, 09178, 09373)
  sampling_rate=1.0, java_memory=60G, processes=8
  Output: kelheim_100pct_population.xml.gz (~1.07M agents)
  Note: IPF convergence relaxed (structural tension, stable at min=0.731/max=1.368)

STEP 1: Compatibility Analysis  ✅ DONE (2026-03-13)
  Script: matsim_scenarios/scripts/compare_populations.py
  Notebook: matsim_scenarios/notebooks/population_comparison.ipynb
  Result: Populations are compatible for merging (see findings below)

STEP 2: Kelheim 25% MATSim Run (existing, unchanged)
  Output: output_plans.xml.gz (converged) + travel times

STEP 3: Population Merge (Java tool)  ⏳ PENDING
  Input: Kelheim 25% output plans + Bavaria 100% population
       + Bavaria 100% households CSV (for household_size)
       + VG250 shapefile
  Logic: municipality-stratified sampling + attribute adaptation
  Output: merged_population.xml.gz (~100%)

STEP 4: DRT Demand Extraction (existing tool)
  Input: merged population + connection_cache from 25% run
  Output: drt_requests.csv → ExMAS
```

### Compatibility Analysis Findings (Step 1)

**Confirmed compatible:**
- Trips per person: 3.61 (25%, filtered) vs 3.56 (eqasim) — match
- Chain patterns overlap well once interactions removed (H, H-W-H, H-L-H, etc.)
- Departure time profiles match (both peak at 7-9 and 16-18)
- Spatial centroids within 8-18 km per activity type (same CRS: EPSG:25832)
- Age distribution similar (max delta 3.6pp in 45-54 bracket)
- Sex split near-identical (~49% female)

**Differences requiring attribute adaptation:**
- Car availability encoding: `always/never` (25%) vs `all/none` (eqasim)
- Income format: continuous EUR/month (25%) vs categorical HH bands (eqasim)
- PT subscription rate: 10% (25%) vs 20% (eqasim) — use eqasim rate (census-derived)
- `householdSize`: only in households CSV, not in population XML (see AttributeAdapter)
- `sex` in XML is `m/f` (matches 25%), CSV has `male/female` — no mapping needed in Java

**25% Kelheim plan structure notes:**
- 42.7% of activities are "car interaction" (mode access/egress markers)
- Also has `pt interaction`, `ride interaction`, `freight interaction`
- Activity types use time suffixes: `home_600`, `home_86400`, `work_600`, etc.
- These are structural MATSim artifacts — irrelevant for chain compatibility
- Eqasim uses plain types: `home`, `work`, `education`, `leisure`, `shop`, `other`

## Component 1: Java Merge Tool — `RunPopulationUpsampling`

### Location

`matsim-libs/contribs/drt-demand-extraction/src/main/java/org/matsim/contrib/demand_extraction/upsampling/`

### CLI Arguments

| Argument | Description | Required |
|---|---|---|
| `--base-population` | Kelheim 25% converged output plans XML | yes |
| `--donor-population` | Bavaria 100% prepared population XML | yes |
| `--donor-households` | Bavaria 100% households CSV (for household_size) | yes |
| `--municipalities-shp` | VG250 administrative boundaries shapefile (with ARS codes) | yes |
| `--output-population` | Path for merged output XML | yes |
| `--random-seed` | Random seed for reproducibility | no (default: 4711) |

### Algorithm

```
1. Load base population (Kelheim 25% converged output)
2. Load donor population (Bavaria 100%, ~1.07M agents across 7 Kreise)
3. Load donor households CSV → build householdId→householdSize lookup
4. Load VG250 municipality shapefile → build spatial index (STRtree)

5. Municipality mapping (same method for both populations):
   For each person:
     home_coord = first activity with type starting with "home"
     municipality_code = point-in-polygon lookup → ARS code (12-digit)

6. Build targets:
   count_target[municipality] = count donor persons per municipality
   count_existing[municipality] = count base persons per municipality
   deficit[municipality] = target - existing

7. Build donor pool:
   Group donor persons by municipality

8. Stratified sampling:
   For each municipality with deficit > 0:
     pool = donor_persons[municipality]
     sample = random sample of min(deficit, pool.size()) persons
     For each sampled person:
       Look up householdSize from CSV lookup
       Call AttributeAdapter.adapt(person, householdSize, rnd)
     Assign new unique person IDs (offset to avoid collision)
     Add sampled persons to output population

9. Copy all base persons to output population (unchanged)

10. Write merged population XML
11. Log summary: per-municipality counts, total agents, warnings
```

### Edge Cases

- Municipality in base but not in donor (activity outside Bavaria pipeline extent) → skip, log warning
- Municipality in donor but not in base → sample full target count
- Deficit ≤ 0 (base has enough) → skip municipality, log info
- Donor pool smaller than deficit → sample all available, log warning
- Person ID collisions → prefix donor IDs with offset (e.g., +10,000,000)

### Home Activity Detection

Scan person's plan for first activity whose type starts with `"home"`. Works for both:
- Kelheim: `"home_77400"` (time suffix convention)
- Bavaria: `"home"`

### Classes

| Class | Responsibility |
|---|---|
| `RunPopulationUpsampling` | Main entry point, CLI parsing, orchestration, CSV loading |
| `MunicipalityMapper` | Shapefile loading, spatial index, point-in-polygon lookup |
| `StratifiedPopulationSampler` | Target calculation, stratified random sampling |
| `AttributeAdapter` | Harmonize eqasim→Kelheim person attributes (car avail, income, MiD groups, PT, subpopulation) |

## Component 2: Python Compatibility Analysis — ✅ DONE

### Location

- Script: `matsim_scenarios/scripts/compare_populations.py`
- Notebook: `matsim_scenarios/notebooks/population_comparison.ipynb`
- Output: `matsim_scenarios/bavaria/output/population_comparison.txt`

### What was compared

| Metric | Result |
|---|---|
| Age distribution | Similar (max 3.6pp delta) |
| Sex ratio | Identical (~49% female) |
| Car availability | Values differ (always/never vs all/none), rates match (82% vs 80%) |
| PT subscription | Differs: 10% (25%) vs 20% (eqasim) |
| Household size | Different distribution (25% skews larger) |
| Income | Incompatible formats (continuous vs categorical) |
| Trips per person (filtered) | Match: 3.61 vs 3.56 |
| Activity chain patterns (filtered) | Good overlap (H, H-W-H, H-L-H, H-E-H, H-S-H) |
| Activity purpose (normalized) | Compatible after type mapping |
| Departure time profile | Match (both peak 7-9 and 16-18) |
| Trip Euclidean distance | 25% longer (13.2 vs 6.9 km mean — external agents) |
| Spatial centroids per activity type | Within 8-18 km (same CRS) |
| Home location bounding box | 25% much wider (external agents), eqasim bounded to 7 Kreise |

## Data Requirements

### Bavaria Pipeline Configuration — ✅ DONE

```yaml
# config_kelheim_100pct.yml (actual working config)
config:
  processes: 8
  sampling_rate: 1.0
  java_memory: 60G    # 10G caused OOM with 909k agents
  bavaria.political_prefix:
    - "09273"  # Kelheim (LK) — core area (~126k)
    - "09375"  # Regensburg (LK) (~195k)
    - "09186"  # Pfaffenhofen a.d.Ilm (LK) (~130k)
    - "09274"  # Landshut (LK) (~165k)
    - "09176"  # Eichstatt (LK) (~133k)
    - "09178"  # Freising (LK) (~180k)
    - "09373"  # Neumarkt i.d.OPf (LK) (~135k)
```

**Pipeline fixes applied:**
- `osmosis.py`: added `encoding="latin-1"` (German umlauts in osmosis output)
- `ipf/model.py`: relaxed `assert converged` to warning (structural tension, stable at min=0.731/max=1.368)
- Full Kreise required for IPF convergence (employment data is Kreis-level)

**Output:** ~1.07M agents in `bavaria/output/kelheim_100pct/`
- `kelheim_100pct_population.xml.gz` (675MB)
- `kelheim_100pct_households.csv` (required by AttributeAdapter for householdSize)
- `kelheim_100pct_activities.gpkg` (spatial data)
- Plus: persons.csv, trips.csv, activities.csv, network, transit, facilities

After CSV generation, run MATSim preparation steps:
1. `RunPreparation` — snap activity coords to Kelheim network link IDs
2. (Skip `RunPopulationRouting` — not needed, DRT extraction does its own routing)

### VG250 Shapefile

Source: BKG (Bundesamt fuer Kartographie und Geodaesie)
Contains: municipality polygons with ARS (Amtlicher Regionalschluessel) codes
CRS: EPSG:25832 (same as Kelheim scenario)

## Scientific Justification

### Why upsampling is necessary
- Bischoff & Maciejewski (2022): "strongly recommended to use 100% population for on-demand services with ride-sharing"
- Pooling probability scales super-linearly with demand density
- 25% sample underestimates matching opportunities

### Why this method is valid
- Donor agents are independently generated (no cloning → no artificial correlation)
- Activity chains from ENTD survey matching (complete, consistent per agent)
- Municipality-stratified sampling preserves spatial distribution
- Travel times from calibrated 25% run are representative (capacity-scaled MATSim)

### Key references
- Bischoff et al. (2022) "Effects of population sampling on agent-based transport simulation of on-demand services"
- Bischoff & Maciejewski (2021) "The impact of trip density on the fleet size and pooling rate of ride-hailing services"
- Ben-Dor et al. (2021) "Population downscaling in multi-agent transportation simulations"
- Horl & Balac (2021) "Synthetic population and travel demand for Paris and Ile-de-France"
- Santi et al. (2014) "Quantifying the benefits of vehicle pooling with shareability networks" (PNAS)
- Fielbaum et al. (2023) "An analytical framework for modeling ride pooling efficiency and minimum fleet size"
