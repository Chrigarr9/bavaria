"""
Build the Niederbayern per-distance-band mode-share calibration target.

Pipeline:
  1. Load Bayern W10.5 Wegelaenge x Hauptverkehrsmittel cross-table
     (extracted from MiD 2017 Wegetabellen Bayern, Tabelle A W10.5, Seite 37).
  2. Apply iterative proportional fitting (IPF) to rescale the Bayern joint
     distribution to Niederbayern marginals:
       - aggregate mode split from MiD 2017 Kurzreport Bayern p. 13 Regierungsbezirk row
       - aggregate distance distribution from MiD 2017 Regionalbericht Bayern Tabelle 14 p. 122
  3. Write a boptx shares pickle consumed by ModeShareObjective via `shares_path`.

Provenance:
  - Source table (Bayern): matsim_scenarios/bavaria/data/bavaria/mid2017_wegetabellen_bayern.pdf
  - Niederbayern aggregate mode share: 16/7/16/54/7 (walk/bike/cp/car/pt) from Kurzreport
  - Niederbayern distance distribution: 9/10/12/23/16/15/10/3/2 from Regionalbericht Tabelle 14

The output is a pickle of (bounds, distance_col_name, shares) where
  bounds: dict[mode] -> list of (bin_idx, lower_m, upper_m)
  shares: dict[mode] -> list of fraction-per-bin
which is what boptx ModeShareObjective(shares_path=...) expects.
"""
import csv
import os
import pickle
import pathlib

import numpy as np


# ---------- 1. Bayern W10.5 seed (MiD 2017 Wegetabellen Bayern, Tabelle A W10.5) ----------
# Row % within each distance band. Source: Tabelle A W10.5, Seite 37.
# Columns are in the order of MODES_CSV below.
#
# The Bayern-wide table has 9 distance bands including >100 km. Our Kelheim
# 30 km buffer scenario has zero trips beyond ~60 km (physically impossible),
# so we drop the >100 km and 50-100 km bands from the calibration target —
# they are unreachable and would waste optimizer effort on bins the sim
# cannot possibly populate. Truncating requires also truncating the
# Niederbayern distance marginal and renormalising.
MODES_CSV = ["walk", "bike", "car_passenger", "car_driver", "pt"]
BAYERN_TABLE = [
    # band,              weighted_k_trips,  walk  bike  cp  car  pt
    ("<0.5 km",          14843,             75,   12,   3,  10,  1),
    ("0.5-1 km",         17027,             44,   21,   8,  25,  2),
    ("1-2 km",           20571,             27,   19,  12,  37,  5),
    ("2-5 km",           34034,             13,   14,  16,  46, 11),
    ("5-10 km",          24214,              5,    8,  17,  55, 15),
    ("10-20 km",         20295,              2,    4,  18,  63, 14),
    ("20-50 km",         13490,              1,    3,  18,  64, 15),
    # Dropped from target (scenario buffer = 30 km, trips > 50 km are sparse):
    # ("50-100 km",         3768,              0,    1,  19,  59, 21),
    # (">100 km",           2450,              0,    0,  27,  51, 23),
]

# Bin edges in METERS. Must match the CSV band labels above (1-to-1 order).
BIN_EDGES_M = [
    (0,       500),
    (500,    1000),
    (1000,   2000),
    (2000,   5000),
    (5000,  10000),
    (10000, 20000),
    (20000, 50000),
]

# ---------- 2. Niederbayern marginals (truncated to match BAYERN_TABLE bins) ----------
# Aggregate mode split (Kurzreport p. 13 Regierungsbezirk row for Niederbayern).
# NOTE: This is the full-Niederbayern aggregate including trips >50 km. Since
# we truncated the target to <=50 km, these marginals become a *model of
# within-scenario shares*, not the true Niederbayern aggregate. In practice
# the long-distance bins are a small fraction of trips (5% total) so the
# truncation error on column marginals is minor.
NB_AGG_MODE = {"walk": 16, "bike": 7, "car_passenger": 16, "car_driver": 54, "pt": 7}
# Aggregate distance distribution (Regionalbericht Tabelle 14, p. 122).
# Original Niederbayern: <0.5:9 / 0.5-1:10 / 1-2:12 / 2-5:23 / 5-10:16 / 10-20:15 /
# 20-50:10 / 50-100:3 / >100:2 = 100
# Truncated to 7 bins and renormalised to 100:
NB_AGG_DIST_RAW = [9, 10, 12, 23, 16, 15, 10]
_total = sum(NB_AGG_DIST_RAW)  # 95
NB_AGG_DIST = [100 * x / _total for x in NB_AGG_DIST_RAW]

assert sum(NB_AGG_MODE.values()) == 100
assert abs(sum(NB_AGG_DIST) - 100) < 1e-9

# ---------- 3. Mode name mapping: proxy CSV -> simulation trip mode names ----------
# The simulation's eqasim_trips.csv uses:
#   walk, bicycle, car_passenger, car, pt
# The boptx calibration script references them as:
#   modes=["car","pt","bicycle","walk","car_passenger"]
CSV_TO_SIM = {
    "walk":          "walk",
    "bike":          "bicycle",
    "car_passenger": "car_passenger",
    "car_driver":    "car",
    "pt":            "pt",
}


def run_ipf(seed_joint, row_marg, col_marg, iterations=100):
    """Iterative Proportional Fitting to bend a seed joint distribution to given marginals."""
    M = seed_joint.astype(float).copy()
    M = M / M.sum() * row_marg.sum()  # normalise to marginal total
    for _ in range(iterations):
        row_sums = M.sum(axis=1, keepdims=True)
        M = M * (row_marg.reshape(-1, 1) / row_sums)
        col_sums = M.sum(axis=0, keepdims=True)
        M = M * (col_marg.reshape(1, -1) / col_sums)
    return M


def main():
    # Build Bayern joint matrix from row%-x-trip-count
    n_bands = len(BAYERN_TABLE)
    n_modes = len(MODES_CSV)
    row_pct = np.array([row[2:] for row in BAYERN_TABLE], dtype=float) / 100.0
    trip_counts = np.array([row[1] for row in BAYERN_TABLE], dtype=float)
    bayern_joint = row_pct * trip_counts[:, None]

    # Sanity: Bayern column aggregate should reproduce published 20/11/14/45/10
    bayern_agg = bayern_joint.sum(axis=0) / bayern_joint.sum() * 100
    print("Bayern reconstructed aggregate (should be ~20/11/14/45/10):")
    for m, v in zip(MODES_CSV, bayern_agg):
        print(f"  {m:14s} {v:5.1f}%")

    # IPF to Niederbayern marginals
    nb_dist_arr = np.array(NB_AGG_DIST, dtype=float)
    nb_mode_arr = np.array([NB_AGG_MODE[m] for m in MODES_CSV], dtype=float)
    M = run_ipf(bayern_joint, nb_dist_arr, nb_mode_arr)

    # Verify IPF convergence
    print("\nIPF check — row sums (should equal NB_AGG_DIST):")
    print(f"  target: {NB_AGG_DIST}")
    print(f"  got:    {[round(x, 2) for x in M.sum(axis=1)]}")
    print("IPF check — column sums (should equal NB_AGG_MODE):")
    print(f"  target: {[NB_AGG_MODE[m] for m in MODES_CSV]}")
    print(f"  got:    {[round(x, 2) for x in M.sum(axis=0)]}")

    # Row-normalise to get 'share of trips in this distance band that use mode m'
    nb_row_pct = M / M.sum(axis=1, keepdims=True)  # fractions (not %)

    print("\nNiederbayern proxy — mode share within each distance band (%):")
    print(f"  {'band':12s}  " + "  ".join(f"{m[:5]:>5s}" for m in MODES_CSV))
    for (band, *_), row in zip(BAYERN_TABLE, nb_row_pct):
        print(f"  {band:12s}  " + "  ".join(f"{100*x:5.1f}" for x in row))

    # Build boptx pickle payload
    # bounds[mode] is a list of (bin_idx, lower_m, upper_m) tuples
    # shares[mode] is a list of fraction-per-bin (same order)
    bounds = {}
    shares = {}
    for csv_mode, sim_mode in CSV_TO_SIM.items():
        mode_idx = MODES_CSV.index(csv_mode)
        bounds[sim_mode] = [(i, lo, hi) for i, (lo, hi) in enumerate(BIN_EDGES_M)]
        shares[sim_mode] = [float(nb_row_pct[i, mode_idx]) for i in range(n_bands)]

    payload = (bounds, "euclidean_distance", shares)

    # Write pickle next to the calibration data dir
    script_dir = pathlib.Path(__file__).resolve().parent
    out_path = script_dir / "data" / "niederbayern_proxy_shares.pkl"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "wb") as f:
        pickle.dump(payload, f)
    print(f"\nWrote {out_path}")
    print(f"  shapes: {n_modes} modes x {n_bands} bins = {n_modes*n_bands} target cells")

    # Also dump a CSV next to it for easy inspection
    csv_path = script_dir / "data" / "niederbayern_proxy_shares.csv"
    with open(csv_path, "w", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["mode", "bin_idx", "lower_m", "upper_m", "share"])
        for sim_mode in bounds:
            for (idx, lo, hi), share in zip(bounds[sim_mode], shares[sim_mode]):
                w.writerow([sim_mode, idx, lo, hi, f"{share:.6f}"])
    print(f"Wrote {csv_path}")


if __name__ == "__main__":
    main()
