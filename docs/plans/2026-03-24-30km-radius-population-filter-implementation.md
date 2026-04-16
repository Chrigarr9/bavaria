# 30km Radius Population Filter — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a spatial radius filter that keeps only agents with at least one activity within 30km of Kelheim center, implemented as a Java in-memory filter in `RunBavaria30kmDemandExtraction` and a Python script for analysis CSVs.

**Architecture:** Java filter runs after population load + freight filtering, before downsampling. Python script operates on the existing eqasim CSV/GPKG output for notebook analysis. Both use Euclidean distance in EPSG:25832 (projected meters).

**Tech Stack:** Java 17 (MATSim API), Python 3 (geopandas, shapely, pandas)

**Design doc:** `docs/plans/2026-03-24-30km-radius-population-filter-design.md`

---

## File Paths

**Java** (relative to `matsim-libs/contribs/drt-demand-extraction/`):
- **Modify:** `src/main/java/org/matsim/contrib/demand_extraction/run/RunBavaria30kmDemandExtraction.java`

**Python** (relative to `matsim_scenarios/bavaria/`):
- **Create:** `scripts/filter_population_by_radius.py`

**Build command:** `cd matsim-libs/contribs/drt-demand-extraction && mvn compile -DskipTests`

---

### Task 1: Add CLI args and `filterByRadius` method to Java runner

**Files:**
- Modify: `matsim-libs/contribs/drt-demand-extraction/src/main/java/org/matsim/contrib/demand_extraction/run/RunBavaria30kmDemandExtraction.java`

**Step 1: Add CLI argument variables**

In the `main` method, after the existing variable declarations (line ~83), add:

```java
		double filterRadius = 30.0; // km, 0 = disabled
		double filterCenterX = 709000.0; // Kelheim center EPSG:25832
		double filterCenterY = 5423000.0;
```

**Step 2: Add CLI argument parsing cases**

In the switch block (line ~86-99), add these cases before the `default`:

```java
				case "--filter-radius" -> filterRadius = Double.parseDouble(args[++i]);
				case "--filter-center" -> {
					String[] parts = args[++i].split(",");
					filterCenterX = Double.parseDouble(parts[0]);
					filterCenterY = Double.parseDouble(parts[1]);
				}
```

**Step 3: Add radius filter logging**

After the existing logging block (line ~118), add:

```java
		if (filterRadius > 0) {
			log.info("Radius filter: {}km around ({}, {})", filterRadius, filterCenterX, filterCenterY);
		}
```

**Step 4: Add radius filter call in the execution flow**

After `filterUnwantedAgents(scenario)` and its logging (line ~161), BEFORE `downsamplePopulation` (line ~165), add:

```java
		// Spatial radius filter: keep only agents with any activity within radius of center
		if (filterRadius > 0) {
			filterByRadius(scenario, filterCenterX, filterCenterY, filterRadius * 1000.0);
		}
```

**Step 5: Add the `filterByRadius` method**

Add this method in the "Population filtering" section (after `filterUnwantedAgents`, around line ~717):

```java
	/**
	 * Remove agents who have no activity within the specified radius of the center point.
	 * Uses Euclidean distance (valid for projected CRS like EPSG:25832 at this scale).
	 *
	 * @param scenario the MATSim scenario
	 * @param centerX center X coordinate (EPSG:25832)
	 * @param centerY center Y coordinate (EPSG:25832)
	 * @param radiusMeters radius in meters
	 */
	private static void filterByRadius(Scenario scenario, double centerX,
			double centerY, double radiusMeters) {
		int before = scenario.getPopulation().getPersons().size();
		double radiusSq = radiusMeters * radiusMeters;

		scenario.getPopulation().getPersons().values().removeIf(person -> {
			if (person.getSelectedPlan() == null) return true;
			return person.getSelectedPlan().getPlanElements().stream()
					.filter(Activity.class::isInstance)
					.map(Activity.class::cast)
					.filter(act -> act.getCoord() != null)
					.noneMatch(act -> {
						double dx = act.getCoord().getX() - centerX;
						double dy = act.getCoord().getY() - centerY;
						return (dx * dx + dy * dy) <= radiusSq;
					});
		});

		int after = scenario.getPopulation().getPersons().size();
		log.info("Radius filter ({}km): {} -> {} agents ({} removed)",
				radiusMeters / 1000.0, before, after, before - after);
	}
```

Note: Uses `radiusSq` to avoid `Math.sqrt` per activity — minor optimization for 1M+ agents.

**Step 6: Update usage string**

In the usage error message (line ~103-108), append the new args:

```java
			System.err.println("Usage: RunBavaria30kmDemandExtraction "
					+ "--scenario-path <path> --population <path> "
					+ "[--sample <1|10|25|100>] [--iterations <N>] "
					+ "[--dmc-start-rate <0.0-1.0>] [--dmc-end-rate <0.0-1.0>] "
					+ "[--output-dir <path>] [--deterministic] "
					+ "[--filter-radius <km>] [--filter-center <x,y>]");
```

**Step 7: Compile**

Run: `cd matsim-libs/contribs/drt-demand-extraction && mvn compile -DskipTests`
Expected: BUILD SUCCESS

**Step 8: Commit**

```bash
cd matsim-libs/contribs/drt-demand-extraction
git add src/main/java/org/matsim/contrib/demand_extraction/run/RunBavaria30kmDemandExtraction.java
git commit -m "feat: add --filter-radius spatial filter to Bavaria demand extraction runner"
```

---

### Task 2: Create Python analysis filter script

**Files:**
- Create: `matsim_scenarios/bavaria/scripts/filter_population_by_radius.py`

**Step 1: Write the script**

```python
"""Filter Bavaria eqasim population to agents with activities within a radius of a center point.

Produces filtered CSVs and GPKGs for analysis notebooks.
Does NOT produce population XML — the Java runner handles that at runtime.

Usage:
    python scripts/filter_population_by_radius.py \
        --input output/kelheim_30km_100pct \
        --prefix kelheim_30km_100pct_ \
        --radius 30 \
        --center 709000,5423000
"""
import argparse
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd


def main():
    parser = argparse.ArgumentParser(description="Filter population by radius around center point")
    parser.add_argument("--input", required=True, help="Input directory with eqasim output")
    parser.add_argument("--prefix", required=True, help="File prefix (e.g. kelheim_30km_100pct_)")
    parser.add_argument("--radius", type=float, default=30.0, help="Radius in km (default: 30)")
    parser.add_argument("--center", default="709000,5423000",
                        help="Center point x,y in EPSG:25832 (default: Kelheim)")
    parser.add_argument("--output", default=None, help="Output directory (default: <input>/filtered_<radius>km)")
    args = parser.parse_args()

    input_dir = Path(args.input)
    prefix = args.prefix
    radius_m = args.radius * 1000.0
    cx, cy = [float(v) for v in args.center.split(",")]

    output_dir = Path(args.output) if args.output else input_dir / f"filtered_{int(args.radius)}km"
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"Input: {input_dir}")
    print(f"Center: ({cx}, {cy}), Radius: {args.radius}km")
    print(f"Output: {output_dir}")

    # --- Step 1: Load activities with geometry and find persons within radius ---
    activities_gpkg = input_dir / f"{prefix}activities.gpkg"
    print(f"\nLoading {activities_gpkg.name}...")
    gdf_activities = gpd.read_file(activities_gpkg)

    # Compute distance from each activity to center
    gdf_activities["dist_to_center"] = np.sqrt(
        (gdf_activities.geometry.x - cx) ** 2 +
        (gdf_activities.geometry.y - cy) ** 2
    )

    # Find person_ids with ANY activity within radius
    within = gdf_activities[gdf_activities["dist_to_center"] <= radius_m]
    keep_persons = set(within["person_id"].unique())
    total_persons = gdf_activities["person_id"].nunique()

    print(f"Persons within {args.radius}km: {len(keep_persons):,} / {total_persons:,} "
          f"({len(keep_persons)/total_persons:.1%})")

    # --- Step 2: Filter and write activities GPKG ---
    gdf_filtered = gdf_activities[gdf_activities["person_id"].isin(keep_persons)].copy()
    gdf_filtered = gdf_filtered.drop(columns=["dist_to_center"])
    out_path = output_dir / f"{prefix}activities.gpkg"
    gdf_filtered.to_file(out_path, driver="GPKG")
    print(f"  Wrote {out_path.name}: {len(gdf_filtered):,} activities")

    # --- Step 3: Filter CSVs ---
    for name in ["persons", "activities", "trips", "households"]:
        csv_path = input_dir / f"{prefix}{name}.csv"
        if not csv_path.exists():
            print(f"  Skipping {csv_path.name} (not found)")
            continue

        df = pd.read_csv(csv_path, sep=";")

        if name == "households":
            # Filter by household_ids of kept persons
            persons_csv = input_dir / f"{prefix}persons.csv"
            df_persons = pd.read_csv(persons_csv, sep=";")
            keep_hh = set(df_persons[df_persons["person_id"].isin(keep_persons)]["household_id"])
            df_out = df[df["household_id"].isin(keep_hh)]
        else:
            df_out = df[df["person_id"].isin(keep_persons)]

        out_path = output_dir / f"{prefix}{name}.csv"
        df_out.to_csv(out_path, sep=";", index=False)
        print(f"  Wrote {out_path.name}: {len(df_out):,} rows")

    # --- Step 4: Filter spatial files ---
    for name in ["homes", "trips", "commutes"]:
        gpkg_path = input_dir / f"{prefix}{name}.gpkg"
        if not gpkg_path.exists():
            print(f"  Skipping {gpkg_path.name} (not found)")
            continue

        gdf = gpd.read_file(gpkg_path)
        if "person_id" in gdf.columns:
            gdf_out = gdf[gdf["person_id"].isin(keep_persons)]
        elif "household_id" in gdf.columns:
            gdf_out = gdf[gdf["household_id"].isin(keep_hh)]
        else:
            print(f"  Skipping {gpkg_path.name} (no person_id or household_id)")
            continue

        out_path = output_dir / f"{prefix}{name}.gpkg"
        gdf_out.to_file(out_path, driver="GPKG")
        print(f"  Wrote {out_path.name}: {len(gdf_out):,} rows")

    # --- Summary ---
    removed = total_persons - len(keep_persons)
    print(f"\nDone. Kept {len(keep_persons):,} persons, removed {removed:,} "
          f"({removed/total_persons:.1%} outside {args.radius}km radius)")


if __name__ == "__main__":
    main()
```

**Step 2: Test the script**

Run:
```bash
cd matsim_scenarios/bavaria
python scripts/filter_population_by_radius.py \
    --input output/kelheim_30km_100pct \
    --prefix kelheim_30km_100pct_ \
    --radius 30
```

Expected output:
- Prints person count within 30km (expect ~60-80% retention)
- Creates `output/kelheim_30km_100pct/filtered_30km/` with filtered files
- All filtered CSVs have fewer rows than originals

**Step 3: Verify filtered output**

```bash
wc -l output/kelheim_30km_100pct/kelheim_30km_100pct_persons.csv
wc -l output/kelheim_30km_100pct/filtered_30km/kelheim_30km_100pct_persons.csv
```

Expected: filtered file has fewer lines (60-80% of original).

**Step 4: Commit**

```bash
cd matsim_scenarios/bavaria
git add scripts/filter_population_by_radius.py
git commit -m "feat: add Python script to filter population by radius for analysis"
```

---

## Summary

| Task | Description | Files |
|------|-------------|-------|
| 1 | Java in-memory radius filter in demand extraction runner | `RunBavaria30kmDemandExtraction.java` |
| 2 | Python filter script for analysis CSVs/GPKGs | `scripts/filter_population_by_radius.py` |

Total: ~2 files modified/created. The Java change is ~40 lines, the Python script is ~100 lines.
