"""
Validate the Pendler-constrained OD matrix against official BA data.
Run after generating a new population with pendler_od_path enabled.

Usage:
    python scripts/validate_pendler_od.py [--population-dir PATH]

If --population-dir is not given, uses the default 1% output path.
"""
import argparse
import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd

# Add parent so we can import bavaria modules
sys.path.insert(0, str(Path(__file__).parent.parent))


def load_commutes(population_dir):
    """Load work commutes from the generated commutes.gpkg."""
    commutes_path = Path(population_dir) / "commutes.gpkg"
    if not commutes_path.exists():
        # Try alternative names
        for alt in ["commute_flows.csv", "work_commutes.csv"]:
            alt_path = Path(population_dir) / alt
            if alt_path.exists():
                return pd.read_csv(alt_path)
        raise FileNotFoundError(f"No commute data found in {population_dir}")

    gdf = gpd.read_file(commutes_path)
    return gdf


def compute_kreis_flows(df, origin_col="home_commune_id", dest_col="work_commune_id"):
    """Aggregate Gemeinde-level flows to Kreis level."""
    df = df.copy()
    df["origin_kreis"] = df[origin_col].astype(str).str[:5]
    df["dest_kreis"] = df[dest_col].astype(str).str[:5]

    flows = df.groupby(["origin_kreis", "dest_kreis"]).size().reset_index(name="count")

    # Compute shares per origin
    totals = flows.groupby("origin_kreis")["count"].transform("sum")
    flows["share"] = flows["count"] / totals

    return flows


def load_official_pendler(pendler_path, a6502c_path, study_kreise):
    """Load official Pendler shares for comparison."""
    from bavaria.gravity.pendler_data import parse_pendler_matrix, load_employed_at_wohnort

    wohnort = load_employed_at_wohnort(a6502c_path, study_kreise)
    pendler = parse_pendler_matrix(pendler_path, study_kreise, wohnort)
    return pendler


def compare_shares(model_flows, official_shares, study_kreise):
    """Compare model Kreis-level flows against official Pendler shares."""
    print("\n=== Kreis-level Flow Comparison ===\n")

    # Build comparison table
    rows = []
    for origin in sorted(study_kreise):
        model_origin = model_flows[model_flows["origin_kreis"] == origin]
        official_origin = official_shares[official_shares["origin_kreis"] == origin]

        for dest in sorted(study_kreise):
            model_share = model_origin[model_origin["dest_kreis"] == dest]["share"].sum()
            official_share = official_origin[official_origin["destination_kreis"] == dest]["share"].sum()
            if model_share > 0 or official_share > 0:
                rows.append({
                    "origin": origin,
                    "destination": dest,
                    "model_share": model_share,
                    "official_share": official_share,
                    "diff": model_share - official_share,
                })

    df_compare = pd.DataFrame(rows)

    # Pearson correlation
    valid = df_compare[(df_compare["model_share"] > 0) | (df_compare["official_share"] > 0)]
    if len(valid) > 1:
        pearson_r = valid["model_share"].corr(valid["official_share"])
        print(f"Pearson r (Kreis shares): {pearson_r:.4f}")
    else:
        pearson_r = float("nan")
        print("Pearson r: insufficient data")

    # Key flows
    print("\nKey OD pairs:")
    for _, row in df_compare.sort_values("official_share", ascending=False).head(10).iterrows():
        print(f"  {row['origin']} -> {row['destination']}: "
              f"model={row['model_share']:.3f}  official={row['official_share']:.3f}  "
              f"diff={row['diff']:+.3f}")

    return pearson_r, df_compare


def main():
    parser = argparse.ArgumentParser(description="Validate Pendler-constrained OD matrix")
    parser.add_argument("--population-dir", type=str, default=None,
                        help="Path to population output directory")
    parser.add_argument("--data-path", type=str, default=None,
                        help="Path to data directory")
    args = parser.parse_args()

    base = Path(__file__).parent.parent
    data_path = Path(args.data_path) if args.data_path else base / "data"
    population_dir = Path(args.population_dir) if args.population_dir else base / "output" / "kelheim_30km_1pct"

    pendler_path = str(data_path / "germany" / "krpend-k-0-202306-xlsx.xlsx")
    a6502c_path = str(data_path / "bavaria" / "a6502c_202200.xlsx")

    study_kreise = {"09273", "09375", "09176", "09362", "09373", "09186", "09274", "09278", "09376"}

    print(f"Population dir: {population_dir}")
    print(f"Data path: {data_path}")

    # Load model output
    df_commutes = load_commutes(population_dir)
    print(f"Loaded {len(df_commutes)} commutes")

    # Compute model Kreis flows
    model_flows = compute_kreis_flows(df_commutes)

    # Load official data
    official = load_official_pendler(pendler_path, a6502c_path, study_kreise)

    # Compare
    pearson_r, df_compare = compare_shares(model_flows, official, study_kreise)

    # Summary
    print("\n=== Summary ===")
    print(f"Pearson r: {pearson_r:.4f}")
    if pearson_r > 0.95:
        print("PASS: Kreis-level shares closely match official data")
    elif pearson_r > 0.85:
        print("WARN: Moderate match -- check individual OD pairs")
    else:
        print("FAIL: Poor match -- investigate model or data issues")


if __name__ == "__main__":
    main()
