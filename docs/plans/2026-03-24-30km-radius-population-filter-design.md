# Design: 30km Radius Population Filter

**Date:** 2026-03-24
**Author:** Christoph Garritsen + Claude
**Status:** Approved

## Problem

The Bavaria 30km eqasim scenario (`config_kelheim_30km_100pct.yml`) generates a synthetic
population for 9 complete Landkreise (~1.17M agents). For DRT demand extraction and analysis,
we only need agents who have at least one activity within a 30km radius of Kelheim center.
Filtering reduces the population to the relevant subset without re-running the synthesis pipeline.

## Approach: Hybrid (Java runtime + Python analysis)

### Why not filter before sampling?

Random per-person sampling and deterministic spatial filtering are independent operations —
they commute. Filtering after sampling gives statistically identical results and is faster
(operates on the smaller sampled population).

### Component 1: Java in-memory filter in `RunBavaria30kmDemandExtraction`

Filters the population after loading and before demand extraction. No intermediate XML
files needed.

**New CLI arguments:**

| Argument | Default | Description |
|----------|---------|-------------|
| `--filter-radius` | `30` | Radius in km around center |
| `--filter-center` | `709000,5423000` | Center point in EPSG:25832 (Kelheim) |

**Execution order:**

```
1. Load sampled population XML (e.g. 10% = ~80MB)
2. filterUnwantedAgents()          — existing (freight, outside, truck)
3. filterByRadius()                — NEW
4. DemandExtractionModule runs on reduced population
```

**Filter logic:**

- For each person, check if ANY activity in their selected plan has a coordinate
  within `radiusMeters` of the center point (Euclidean distance)
- Remove persons where NO activity falls within the radius
- Euclidean distance is valid: EPSG:25832 is projected in meters, 30km is small
  enough that projection distortion is negligible (~0.01% error)

```java
private static void filterByRadius(Scenario scenario, double centerX,
        double centerY, double radiusMeters) {
    int before = scenario.getPopulation().getPersons().size();

    scenario.getPopulation().getPersons().values().removeIf(person -> {
        if (person.getSelectedPlan() == null) return true;
        return person.getSelectedPlan().getPlanElements().stream()
            .filter(Activity.class::isInstance)
            .map(Activity.class::cast)
            .filter(act -> act.getCoord() != null)
            .noneMatch(act -> {
                double dx = act.getCoord().getX() - centerX;
                double dy = act.getCoord().getY() - centerY;
                return Math.sqrt(dx*dx + dy*dy) <= radiusMeters;
            });
    });

    int after = scenario.getPopulation().getPersons().size();
    log.info("Radius filter: {} -> {} agents ({} removed, outside {}km radius)",
            before, after, before - after, radiusMeters / 1000.0);
}
```

### Component 2: Python script for filtered analysis CSVs/GPKGs

**Script:** `matsim_scenarios/bavaria/scripts/filter_population_by_radius.py`

**Purpose:** Produce filtered flat files for analysis notebooks (population comparison,
mode share analysis, etc.) without needing Java.

**Logic:**

1. Load `activities.gpkg` (has geometry in EPSG:25832)
2. Compute Euclidean distance from each activity to Kelheim center `(709000, 5423000)`
3. Collect `person_id`s where any activity is within 30km
4. Filter `persons.csv`, `activities.csv`, `trips.csv`, `households.csv` to those person_ids
5. Filter `activities.gpkg`, `trips.gpkg`, `homes.gpkg` to matching records
6. Write to `output/kelheim_30km_100pct/filtered_30km/`

**CLI:**

```bash
python scripts/filter_population_by_radius.py \
    --input output/kelheim_30km_100pct \
    --prefix kelheim_30km_100pct_ \
    --radius 30 \
    --center 709000,5423000
```

## Data Flow

```
Bavaria 9-Kreise Pipeline (already run)
  -> output/kelheim_30km_100pct/ (1.17M agents, all files)

Python filter script (for analysis)
  -> output/kelheim_30km_100pct/filtered_30km/ (filtered CSVs/GPKGs)

RunBavaria30kmDemandExtraction --filter-radius 30 (for MATSim)
  -> loads population XML
  -> filters in memory (no file I/O)
  -> runs demand extraction on filtered subset
```

## Expected Impact

Based on the 9 Kreise and their border distances from Kelheim:
- Kelheim (0km), Regensburg LK (6km), Eichstatt (9km) — fully within 30km
- Regensburg Stadt (16km), Neumarkt (17km), Pfaffenhofen (18km) — mostly within 30km
- Landshut (22km), Straubing-Bogen (26km), Schwandorf (28km) — partially within 30km

Estimate: ~60-80% of agents retained (those with activities near the Kelheim core),
~20-40% filtered out (agents in outer Kreise with no activities reaching the 30km zone).

## What This Does NOT Do

- Does not truncate activity chains (agents keep their full plans)
- Does not modify the network, transit, or facilities
- Does not rewrite population XML files (Java filters at runtime)
- Does not affect the synthesis pipeline itself
