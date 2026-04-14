"""Check mode shares in output_trips.csv.gz against MiD 2017 Niederbayern targets.

Reads a MATSim output directory, loads output_trips.csv.gz, computes aggregate mode
shares, compares to MiD 2017 Niederbayern (PDF Abbildung 22), and prints pass/fail.

Usage:
    python check_mode_shares.py --output-dir path/to/matsim/output [--tol 0.02]
"""
import argparse
import glob
import os
import sys

import pandas as pd


MID_NIEDERBAYERN = {
    "car":           0.54,
    "car_passenger": 0.16,
    "walk":          0.16,
    "bike":          0.07,
    "pt":            0.07,
}

MATSIM_TO_CANON = {
    "car": "car",
    "pt": "pt",
    "walk": "walk",
    "bike": "bike",
    "bicycle": "bike",
    "ride": "car_passenger",
    "car_passenger": "car_passenger",
}


def load_trips(output_dir):
    patterns = [
        os.path.join(output_dir, "*output_trips.csv.gz"),
        os.path.join(output_dir, "output_trips.csv.gz"),
    ]
    for pat in patterns:
        matches = glob.glob(pat)
        if matches:
            return pd.read_csv(matches[0], sep=";", usecols=["main_mode"])
    raise SystemExit(f"No output_trips.csv.gz in {output_dir}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--output-dir", required=True)
    ap.add_argument("--tol", type=float, default=0.02,
                    help="Tolerance for each mode share (default: 0.02 = 2 pp)")
    args = ap.parse_args()

    df = load_trips(args.output_dir)
    df["canon"] = df["main_mode"].map(MATSIM_TO_CANON)
    if df["canon"].isnull().any():
        unknown = df.loc[df["canon"].isnull(), "main_mode"].unique()
        raise SystemExit(f"Unknown modes in output_trips.csv.gz: {list(unknown)}")

    counts = df["canon"].value_counts()
    total = counts.sum()
    shares = (counts / total).to_dict()

    print(f"Total trips: {total}")
    print(f"{'mode':16s}  {'sim':>7s}  {'ref':>7s}  {'Δpp':>7s}  {'pass':>5s}")
    print("-" * 46)
    failed = []
    for mode, ref in MID_NIEDERBAYERN.items():
        sim = shares.get(mode, 0.0)
        delta = sim - ref
        ok = abs(delta) <= args.tol
        mark = "PASS" if ok else "FAIL"
        print(f"{mode:16s}  {sim:7.3f}  {ref:7.3f}  {100*delta:+7.2f}  {mark:>5s}")
        if not ok:
            failed.append(mode)

    if failed:
        print()
        print(f"GATE FAILED: modes outside ±{100*args.tol:.1f} pp: {failed}")
        sys.exit(1)
    print()
    print(f"GATE PASSED: all modes within ±{100*args.tol:.1f} pp of MiD Niederbayern")


if __name__ == "__main__":
    main()
