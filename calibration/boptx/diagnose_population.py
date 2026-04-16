"""
Population-level diagnostics for the Bavaria walk-explosion bug.

Two questions:
  (a) Does the simulated trip-length distribution match MiD reference, or is the
      input population over-represented at 1.2-1.8 km?
  (b) Is mode availability (car/bike/license) too restrictive, forcing car-less
      and bike-less agents to walk in the 1-2 km band?

Reads:
  - work_base/<eval>/output/*.output_trips.csv.gz
  - work_base/<eval>/output/*.output_persons.csv.gz
  - data/reference_trips.csv

Defaults to the leftover eval directory but accepts --eval-dir.
"""
import argparse
import glob
import os

import numpy as np
import pandas as pd


def load_trips(eval_dir):
    pat = os.path.join(eval_dir, "*output_trips.csv.gz")
    f = glob.glob(pat)[0]
    return pd.read_csv(f, sep=";", usecols=[
        "person", "main_mode", "euclidean_distance",
    ])


def load_persons(eval_dir):
    pat = os.path.join(eval_dir, "*output_persons.csv.gz")
    f = glob.glob(pat)[0]
    return pd.read_csv(f, sep=";", usecols=[
        "person", "age", "carAvail", "carAvailability", "bicycleAvailability",
        "income", "subpopulation",
    ])


def quantile_bins(values, weights, n_bins=20):
    sorter = np.argsort(values)
    v = values[sorter]
    cdf = np.cumsum(weights[sorter])
    cdf = cdf / cdf[-1]
    probs = np.linspace(0.0, 1.0, n_bins + 1)
    edges = np.unique([v[np.argmin(cdf <= p)] for p in probs])
    return edges


def bin_counts(distances, weights, edges):
    counts = np.zeros(len(edges) - 1)
    for i in range(len(edges) - 1):
        lo, hi = edges[i], edges[i + 1]
        mask = (distances >= lo) & (distances < hi)
        counts[i] = weights[mask].sum()
    return counts


def trip_length_distribution(trips, ref):
    edges = quantile_bins(
        ref["euclidean_distance"].values, ref["weight"].values, n_bins=20
    )

    sim_d = trips["euclidean_distance"].dropna().astype(float).values
    sim_w = np.ones_like(sim_d)
    ref_d = ref["euclidean_distance"].astype(float).values
    ref_w = ref["weight"].astype(float).values

    sim_counts = bin_counts(sim_d, sim_w, edges)
    ref_counts = bin_counts(ref_d, ref_w, edges)
    sim_pdf = sim_counts / sim_counts.sum()
    ref_pdf = ref_counts / ref_counts.sum()

    rows = []
    for i, (lo, hi) in enumerate(zip(edges[:-1], edges[1:])):
        rows.append({
            "bin_index": i,
            "lower_m": int(lo),
            "upper_m": int(hi),
            "ref_pdf": ref_pdf[i],
            "sim_pdf": sim_pdf[i],
            "diff": sim_pdf[i] - ref_pdf[i],
            "ratio": sim_pdf[i] / ref_pdf[i] if ref_pdf[i] > 0 else float("nan"),
        })
    return pd.DataFrame(rows), edges


def mode_availability_stats(persons):
    print("Mode availability (persons):")
    print(f"  total: {len(persons)}")
    for col in ["carAvail", "carAvailability", "bicycleAvailability"]:
        print(f"  {col}:")
        for v, n in persons[col].value_counts(dropna=False).items():
            print(f"    {v!r}: {n}  ({n/len(persons):.1%})")


def cross_tab_per_band(trips, persons, edges):
    df = trips.merge(persons, on="person", how="left")

    car_less_keys = {"carAvail": ["never"], "carAvailability": ["none"]}
    bike_less_keys = {"bicycleAvailability": ["none"]}

    df["car_less"] = (
        df["carAvail"].isin(car_less_keys["carAvail"])
        | df["carAvailability"].isin(car_less_keys["carAvailability"])
    )
    df["bike_less"] = df["bicycleAvailability"].isin(bike_less_keys["bicycleAvailability"])
    df["both_less"] = df["car_less"] & df["bike_less"]

    df["dist"] = pd.to_numeric(df["euclidean_distance"], errors="coerce")
    df = df.dropna(subset=["dist"])

    rows = []
    for i in range(len(edges) - 1):
        lo, hi = edges[i], edges[i + 1]
        m = (df["dist"] >= lo) & (df["dist"] < hi)
        sub = df[m]
        if len(sub) == 0:
            continue
        rows.append({
            "bin_index": i,
            "lower_m": int(lo),
            "upper_m": int(hi),
            "n_trips": len(sub),
            "pct_car_less": sub["car_less"].mean(),
            "pct_bike_less": sub["bike_less"].mean(),
            "pct_both_less": sub["both_less"].mean(),
            "pct_walk_chosen": (sub["main_mode"] == "walk").mean(),
            "pct_bike_chosen": (sub["main_mode"] == "bike").mean(),
            "pct_car_chosen": (sub["main_mode"] == "car").mean(),
        })
    return pd.DataFrame(rows)


def bike_availability_violations(trips, persons):
    df = trips.merge(persons, on="person", how="left")
    bike_trips = df[df["main_mode"] == "bike"]
    if len(bike_trips) == 0:
        return None
    no_bike = bike_trips[bike_trips["bicycleAvailability"] == "none"]
    car_trips = df[df["main_mode"] == "car"]
    no_car = car_trips[
        (car_trips["carAvail"] == "never")
        | (car_trips["carAvailability"] == "none")
    ]
    return {
        "bike_trips_total": len(bike_trips),
        "bike_trips_by_no_bike_agents": len(no_bike),
        "pct": len(no_bike) / len(bike_trips) if len(bike_trips) else 0,
        "car_trips_total": len(car_trips),
        "car_trips_by_no_car_agents": len(no_car),
        "car_pct": len(no_car) / len(car_trips) if len(car_trips) else 0,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--eval-dir", default="work_base/42cb509117f7f2ddd559829479961e77/output")
    ap.add_argument("--reference", default="data/reference_trips.csv")
    ap.add_argument("--out-dir", default="diagnostics")
    args = ap.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)
    trips = load_trips(args.eval_dir)
    persons = load_persons(args.eval_dir)
    ref = pd.read_csv(args.reference, sep=";")

    print(f"Loaded {len(trips):,} sim trips, {len(persons):,} persons, {len(ref):,} ref trips")
    print()

    print("=" * 70)
    print("(1) TRIP-LENGTH DISTRIBUTION: sim input vs MiD reference")
    print("=" * 70)
    dist_df, edges = trip_length_distribution(trips, ref)
    fmt = dist_df.copy()
    fmt["ref_pdf"] = (fmt["ref_pdf"] * 100).round(2)
    fmt["sim_pdf"] = (fmt["sim_pdf"] * 100).round(2)
    fmt["diff"] = (fmt["diff"] * 100).round(2)
    fmt["ratio"] = fmt["ratio"].round(2)
    fmt.columns = ["bin", "lo_m", "hi_m", "ref_%", "sim_%", "diff_pp", "ratio"]
    print(fmt.to_string(index=False))
    dist_df.to_csv(os.path.join(args.out_dir, "trip_length_distribution.csv"), index=False)
    print()

    print("=" * 70)
    print("(2) MODE AVAILABILITY")
    print("=" * 70)
    mode_availability_stats(persons)
    print()

    print("=" * 70)
    print("(3) CROSS-TAB: per distance bin, who can choose what")
    print("=" * 70)
    cross_df = cross_tab_per_band(trips, persons, edges)
    fmt = cross_df.copy()
    for col in ["pct_car_less", "pct_bike_less", "pct_both_less",
                "pct_walk_chosen", "pct_bike_chosen", "pct_car_chosen"]:
        fmt[col] = (fmt[col] * 100).round(1)
    fmt.columns = ["bin", "lo_m", "hi_m", "n_trips",
                   "no_car_%", "no_bike_%", "no_either_%",
                   "walk_%", "bike_%", "car_%"]
    print(fmt.to_string(index=False))
    cross_df.to_csv(os.path.join(args.out_dir, "availability_per_band.csv"), index=False)
    print()

    print("=" * 70)
    print("(4) AVAILABILITY-CONSTRAINT VIOLATIONS")
    print("=" * 70)
    v = bike_availability_violations(trips, persons)
    if v:
        print(f"  bike trips total: {v['bike_trips_total']:,}")
        print(f"  ... by agents with bicycleAvailability=none: {v['bike_trips_by_no_bike_agents']:,} ({v['pct']:.1%})")
        print(f"  car  trips total: {v['car_trips_total']:,}")
        print(f"  ... by agents with no car availability:      {v['car_trips_by_no_car_agents']:,} ({v['car_pct']:.1%})")
    print()

    print(f"Wrote diagnostics to {args.out_dir}/")


if __name__ == "__main__":
    main()
