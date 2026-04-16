"""
Per-distance-band, per-mode utility calculation using the Kelheim v3.0/v3.1
scoring parameters (including DRT from the KEXI config). Predicts MNL mode
shares from those utilities and compares to MiD reference.

Goal: sanity-check whether the Kelheim scoring framework can in principle
reproduce the MiD reference shares — or whether it's structurally biased and
no amount of ASC tuning can fix it.

All assumptions are listed at the top in plain constants so they can be tweaked.

Sources for parameters:
  - kelheim-v3.1-config.xml (base scoring, no DRT)
  - kelheim-v3.0-25pct.kexi.config.xml (DRT modeParams: const=+2.45, margDist=-2.5E-4)
  - bavaria/calibration/boptx/data/reference_trips.csv (MiD trip distances + modes)
  - empirical median speeds from a 1pct base sim run (output_trips.csv.gz)
"""
import argparse
import os

import numpy as np
import pandas as pd

# -----------------------------------------------------------------------------
# Global scoring constants (Kelheim v3.0/v3.1 defaults)
# -----------------------------------------------------------------------------
PERFORMING_UTILS_HR = 6.0       # MATSim default; sets opportunity cost of travel time
MARG_UTIL_OF_MONEY = 1.0        # Default; income-dependent in real run, use 1 for back-of-envelope
WAITING_PT_UTILS_HR = -1.6      # Kelheim line 168
LINE_SWITCH_UTIL = -1.0         # MATSim default

BRAIN_EXP_BETA = 1.0            # MNL scale parameter

BEELINE_FACTOR = 1.3            # Same for all teleported modes in Kelheim

# -----------------------------------------------------------------------------
# Mode parameters (Kelheim v3.0/v3.1 base + KEXI DRT)
# -----------------------------------------------------------------------------
# constant       — ASC
# marg_time_hr   — additional time disutility on top of foregone performing
# marg_dist_m    — additional distance disutility (utils/m)
# mon_dist_rate  — monetary distance rate (€/m), multiplied by marg_util_of_money
# daily_money    — daily monetary constant (€/day), amortized
# speed_kmh      — empirical median from output_trips.csv.gz of a real Bavaria 1pct run
MODE_PARAMS = {
    "car": dict(
        constant=0.10908902922956654, marg_time_hr=0.0, marg_dist_m=0.0,
        mon_dist_rate=-2.0e-4, daily_money=-5.3, speed_kmh=31.0),
    "ride": dict(   # eqasim car_passenger
        constant=-0.44874536876610344, marg_time_hr=-12.0, marg_dist_m=0.0,
        mon_dist_rate=-2.0e-4, daily_money=0.0, speed_kmh=30.0),
    "pt": dict(
        constant=0.0449751479497542, marg_time_hr=0.0, marg_dist_m=0.0,
        mon_dist_rate=0.0, daily_money=0.0, speed_kmh=10.7),
    "bike": dict(
        constant=-0.9059637590522914, marg_time_hr=-3.0, marg_dist_m=0.0,
        mon_dist_rate=0.0, daily_money=0.0, speed_kmh=11.3),
    "walk": dict(
        constant=0.0, marg_time_hr=0.0, marg_dist_m=0.0,
        mon_dist_rate=0.0, daily_money=0.0, speed_kmh=3.8),
    "drt": dict(
        constant=2.45, marg_time_hr=0.0, marg_dist_m=-2.5e-4,
        mon_dist_rate=0.0, daily_money=0.0, speed_kmh=25.0),
}

# How daily costs are amortized: assumed number of trips per day for an agent
# whose primary mode is the given one. Affects only car's -5.3 €/day.
TRIPS_PER_DAY = 3.5

# PT-specific extras
PT_WAIT_FRACTION = 0.20    # Fraction of pt journey time spent waiting at stops
PT_LINE_SWITCHES = 0.5     # Average transfers per pt trip (rural → low)

# DRT-specific extras
DRT_WAIT_HOURS = 5.0 / 60.0   # 5 min average wait (Kelheim KEXI typical)


# -----------------------------------------------------------------------------
# MiD reference quantile bins (matches MATSimModeShareObjective)
# -----------------------------------------------------------------------------
def quantile_bins(values, weights, n_bins=20):
    sorter = np.argsort(values)
    v = values[sorter]
    cdf = np.cumsum(weights[sorter])
    cdf = cdf / cdf[-1]
    probs = np.linspace(0.0, 1.0, n_bins + 1)
    edges = np.unique([v[np.argmin(cdf <= p)] for p in probs])
    return edges


# -----------------------------------------------------------------------------
# Per-mode utility at a given beeline distance
# -----------------------------------------------------------------------------
def mode_utility(mode, beeline_m):
    p = MODE_PARAMS[mode]
    network_m = beeline_m * BEELINE_FACTOR
    network_km = network_m / 1000.0
    speed_kmh = p["speed_kmh"]
    in_vehicle_hr = network_km / speed_kmh

    constant = p["constant"]
    money_util = p["mon_dist_rate"] * network_m * MARG_UTIL_OF_MONEY
    distance_util = p["marg_dist_m"] * network_m
    daily_util = p["daily_money"] * MARG_UTIL_OF_MONEY / TRIPS_PER_DAY

    if mode == "pt":
        wait_hr = in_vehicle_hr * PT_WAIT_FRACTION
        ride_hr = in_vehicle_hr * (1 - PT_WAIT_FRACTION)
        time_util = (
            ride_hr * (p["marg_time_hr"] - PERFORMING_UTILS_HR)
            + wait_hr * (WAITING_PT_UTILS_HR - PERFORMING_UTILS_HR)
            + PT_LINE_SWITCHES * LINE_SWITCH_UTIL
        )
        total_time_min = in_vehicle_hr * 60
    elif mode == "drt":
        wait_hr = DRT_WAIT_HOURS
        ride_hr = in_vehicle_hr
        time_util = (
            ride_hr * (p["marg_time_hr"] - PERFORMING_UTILS_HR)
            + wait_hr * (-PERFORMING_UTILS_HR)  # opportunity cost of waiting
        )
        total_time_min = (ride_hr + wait_hr) * 60
    else:
        time_util = in_vehicle_hr * (p["marg_time_hr"] - PERFORMING_UTILS_HR)
        total_time_min = in_vehicle_hr * 60

    total = constant + time_util + money_util + distance_util + daily_util
    return total, total_time_min


def mnl_shares(utilities):
    u = np.array(utilities)
    e = np.exp(BRAIN_EXP_BETA * (u - u.max()))  # stabilized
    return e / e.sum()


# -----------------------------------------------------------------------------
# Reference shares per band (from MiD reference_trips.csv)
# -----------------------------------------------------------------------------
def reference_shares_per_band(ref, edges, modes_to_show):
    rows = []
    for i in range(len(edges) - 1):
        lo, hi = edges[i], edges[i + 1]
        sub = ref[(ref["euclidean_distance"] >= lo) & (ref["euclidean_distance"] < hi)]
        total_w = sub["weight"].sum()
        row = {"bin": i, "lo_m": int(lo), "hi_m": int(hi), "n_ref": int(sub["weight"].sum())}
        for m in modes_to_show:
            ref_mode_name = m
            if m == "ride":
                ref_mode_name = "car_passenger"
            elif m == "bike":
                ref_mode_name = "bicycle"
            w = sub[sub["mode"] == ref_mode_name]["weight"].sum()
            row[f"ref_{m}"] = w / total_w if total_w > 0 else 0.0
        rows.append(row)
    return pd.DataFrame(rows)


# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reference", default="data/reference_trips.csv")
    ap.add_argument("--out-dir", default="diagnostics")
    ap.add_argument("--exclude-drt", action="store_true",
                    help="Exclude DRT from the choice set (Kelheim base run has no DRT)")
    args = ap.parse_args()

    ref = pd.read_csv(args.reference, sep=";")
    edges = quantile_bins(ref["euclidean_distance"].values, ref["weight"].values, n_bins=20)

    modes = ["car", "bike", "pt", "walk", "ride", "drt"]
    if args.exclude_drt:
        modes = [m for m in modes if m != "drt"]

    rows = []
    for i in range(len(edges) - 1):
        lo, hi = edges[i], edges[i + 1]
        midpoint = (lo + hi) / 2
        utils = []
        times = []
        for m in modes:
            u, t = mode_utility(m, midpoint)
            utils.append(u)
            times.append(t)
        shares = mnl_shares(utils)
        row = {"bin": i, "lo_m": int(lo), "hi_m": int(hi), "mid_m": int(midpoint)}
        for m, u, t, s in zip(modes, utils, times, shares):
            row[f"u_{m}"] = round(u, 2)
            row[f"t_{m}"] = round(t, 1)
            row[f"sim_{m}"] = round(s, 3)
        winner = modes[int(np.argmax(utils))]
        row["winner"] = winner
        rows.append(row)
    pred = pd.DataFrame(rows)

    ref_shares = reference_shares_per_band(ref, edges, modes)
    merged = pred.merge(ref_shares[["bin"] + [f"ref_{m}" for m in modes]], on="bin")

    print("=" * 100)
    print("MODE UTILITIES per distance band (midpoint), Kelheim params, including DRT" if not args.exclude_drt else "Kelheim params, NO DRT")
    print("=" * 100)
    print()
    util_cols = ["bin", "lo_m", "hi_m", "mid_m"] + [f"u_{m}" for m in modes] + ["winner"]
    print(merged[util_cols].to_string(index=False))
    print()

    print("=" * 100)
    print("PREDICTED MNL SHARES vs MiD REFERENCE")
    print("=" * 100)
    print()
    for m in modes:
        merged[f"d_{m}"] = (merged[f"sim_{m}"] - merged[f"ref_{m}"]).round(3)
    show_cols = ["bin", "lo_m", "hi_m"]
    for m in modes:
        show_cols += [f"sim_{m}", f"ref_{m}", f"d_{m}"]
    fmt = merged[show_cols].copy()
    for m in modes:
        fmt[f"sim_{m}"] = (fmt[f"sim_{m}"] * 100).round(1)
        fmt[f"ref_{m}"] = (fmt[f"ref_{m}"] * 100).round(1)
        fmt[f"d_{m}"] = (fmt[f"d_{m}"] * 100).round(1)
    print(fmt.to_string(index=False))
    print()

    print("=" * 100)
    print("AGGREGATE MODE SHARES (averaged over bins; equal weight)")
    print("=" * 100)
    agg = pd.DataFrame({
        "mode": modes,
        "predicted_pct": [round(merged[f"sim_{m}"].mean() * 100, 2) for m in modes],
        "reference_pct": [round(merged[f"ref_{m}"].mean() * 100, 2) for m in modes],
    })
    agg["delta_pp"] = (agg["predicted_pct"] - agg["reference_pct"]).round(2)
    print(agg.to_string(index=False))
    print()

    os.makedirs(args.out_dir, exist_ok=True)
    out = os.path.join(args.out_dir, "scoring_per_band.csv")
    merged.to_csv(out, index=False)
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
