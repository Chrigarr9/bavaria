"""Filter Bavaria eqasim population to agents with activities within a radius of a center point.

Produces filtered CSVs and GPKGs for analysis notebooks.
Does NOT produce population XML — the Java runner handles that at runtime.

Usage:
    # By coordinates:
    python scripts/filter_population_by_radius.py \
        --input output/kelheim_30km_100pct \
        --prefix kelheim_30km_100pct_ \
        --radius 30 \
        --center 709000,5423000

    # By municipality name (looks up centroid from VG250 shapes):
    python scripts/filter_population_by_radius.py \
        --input output/kelheim_30km_100pct \
        --prefix kelheim_30km_100pct_ \
        --radius 30 \
        --municipality Kelheim
"""
import argparse
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd


VG250_LAYERS = ["vg250_gem", "vg250_krs", "vg250_lan"]  # Gemeinde, Kreis, Land


def resolve_municipality_center(name: str, shapes_path: str) -> tuple[float, float]:
    """Look up a municipality by name in VG250 shapes and return its centroid in EPSG:25832.

    Searches Gemeinde, then Kreis, then Land layers. Matches case-insensitively
    on the GEN (name) column. If multiple matches, prefers the one with the
    smallest area (most specific).
    """
    shapes_path = Path(shapes_path)
    if not shapes_path.exists():
        raise FileNotFoundError(f"VG250 shapes not found: {shapes_path}")

    matches = []
    for layer in VG250_LAYERS:
        gdf = gpd.read_file(shapes_path, layer=layer)
        # Filter to land polygons only (GF=4 = mit Wasser, most common)
        if "GF" in gdf.columns:
            gdf = gdf[gdf["GF"].isin([4, 2])]
        found = gdf[gdf["GEN"].str.lower() == name.lower()]
        for _, row in found.iterrows():
            matches.append((layer, row))

    if not matches:
        raise ValueError(
            f"Municipality '{name}' not found in VG250 layers {VG250_LAYERS}. "
            f"Check spelling (GEN column, case-insensitive)."
        )

    # Prefer smallest area (most specific match)
    layer, best = min(matches, key=lambda m: m[1].geometry.area)
    centroid = best.geometry.centroid
    print(f"Resolved '{name}' -> {best['GEN']} ({best['BEZ']}) in {layer}, "
          f"centroid=({centroid.x:.0f}, {centroid.y:.0f}), pop={best.get('EWZ', '?')}")
    return centroid.x, centroid.y


def main():
    parser = argparse.ArgumentParser(description="Filter population by radius around center point")
    parser.add_argument("--input", required=True, help="Input directory with eqasim output")
    parser.add_argument("--prefix", required=True, help="File prefix (e.g. kelheim_30km_100pct_)")
    parser.add_argument("--radius", type=float, default=30.0, help="Radius in km (default: 30)")
    parser.add_argument("--center", default="709000,5423000",
                        help="Center point x,y in EPSG:25832 (default: Kelheim)")
    parser.add_argument("--municipality", default=None,
                        help="Municipality name to use as center (looks up centroid from VG250 shapes)")
    parser.add_argument("--shapes", default="data/germany/tmp_vg250/vg250-ew_12-31.utm32s.gpkg.ebenen/vg250-ew_ebenen_1231/DE_VG250.gpkg",
                        help="Path to VG250 GeoPackage (default: data/germany/...)")
    parser.add_argument("--output", default=None, help="Output directory (default: <input>/filtered_<radius>km)")
    args = parser.parse_args()

    input_dir = Path(args.input)
    prefix = args.prefix
    radius_m = args.radius * 1000.0

    # Resolve center: municipality name or explicit coordinates
    if args.municipality:
        cx, cy = resolve_municipality_center(args.municipality, args.shapes)
    else:
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
