"""
Extract per-distance-band mode share diagnostics from a boptx PickleTracker file.

The MATSimModeShareObjective stores its full per-bin df_diff inside each
evaluation's information dict, and PickleTracker pickles it. So all per-band
deltas for every past evaluation are already on disk in optimization_*.p — we
just need to surface them.

Usage:
    python extract_diagnostics.py optimization_base_sim.p
    python extract_diagnostics.py optimization_base_sim.p --parameters car bike ride
"""
import argparse
import os
import pickle
import sys

import pandas as pd


def extract(pickle_path, parameter_names):
    with open(pickle_path, "rb") as f:
        d = pickle.load(f)

    long_rows = []
    summary_rows = []

    for eval_idx, e in enumerate(d["evaluations"]):
        info = e.get("information") or {}
        ms = info.get("mode_share", {})
        cfg = ms.get("configuration", {}) if hasattr(ms, "get") else {}
        data = cfg.get("data") if hasattr(cfg, "get") else None
        if data is None or not hasattr(data, "columns"):
            continue

        df = data.copy()
        df["eval_idx"] = eval_idx
        df["round"] = e.get("round")
        df["objective"] = e.get("objective")
        for k, name in enumerate(parameter_names):
            df[f"asc_{name}"] = e["values"][k]
        long_rows.append(df)

        df_abs = df.assign(abs_diff=df["difference"].abs())
        worst = df_abs.nlargest(5, "abs_diff")
        summary_rows.append({
            "eval_idx": eval_idx,
            "round": e.get("round"),
            "objective": e.get("objective"),
            **{f"asc_{n}": e["values"][k] for k, n in enumerate(parameter_names)},
            "worst_bins": "; ".join(
                f"{r['mode']}@{r['lower_bound']:.0f}-{r['upper_bound']:.0f}m:{r['difference']:+.3f}"
                for _, r in worst.iterrows()
            ),
        })

    if not long_rows:
        raise RuntimeError(f"No mode_share diagnostics found in {pickle_path}")

    return pd.concat(long_rows, ignore_index=True), pd.DataFrame(summary_rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pickle", help="Path to optimization_*.p")
    ap.add_argument("--parameters", nargs="+", default=["car", "bike", "ride"],
                    help="Parameter names in the order they appear in evaluation values")
    ap.add_argument("--out-dir", default="diagnostics")
    args = ap.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)
    long_df, summary_df = extract(args.pickle, args.parameters)

    long_path = os.path.join(args.out_dir, "diagnostics_per_band.csv")
    summary_path = os.path.join(args.out_dir, "diagnostics_summary.csv")
    long_df.to_csv(long_path, index=False)
    summary_df.to_csv(summary_path, index=False)

    best = summary_df.loc[summary_df["objective"].idxmin()]
    print(f"Source: {args.pickle}")
    print(f"Evaluations: {len(summary_df)}  Bins per eval: {len(long_df) // len(summary_df)}")
    print()
    print(f"Best eval: #{int(best['eval_idx'])} (round {int(best['round'])})  obj={best['objective']:.5f}")
    for n in args.parameters:
        print(f"  ASC({n:<5s}) = {best[f'asc_{n}']:+.4f}")

    best_data = long_df[long_df["eval_idx"] == best["eval_idx"]].copy()
    best_data["abs_diff"] = best_data["difference"].abs()

    print()
    print("Top 15 worst bins (best eval):")
    cols = ["mode", "bin_index", "lower_bound", "upper_bound",
            "reference_share", "simulation_share", "difference"]
    fmt = best_data.nlargest(15, "abs_diff")[cols].copy()
    fmt["lower_bound"] = fmt["lower_bound"].round(0).astype(int)
    fmt["upper_bound"] = fmt["upper_bound"].round(0).astype(int)
    fmt["reference_share"] = fmt["reference_share"].round(3)
    fmt["simulation_share"] = fmt["simulation_share"].round(3)
    fmt["difference"] = fmt["difference"].round(3)
    print(fmt.to_string(index=False))

    print()
    print("Mode share by distance band (best eval):")
    pivot = best_data.pivot_table(
        index=["bin_index", "lower_bound", "upper_bound"],
        columns="mode",
        values="difference",
        aggfunc="first",
    ).round(3)
    pivot.index = [f"{int(lo)}-{int(hi)}m" for _, lo, hi in pivot.index]
    print(pivot.to_string())

    print()
    print(f"Written:")
    print(f"  {long_path} ({len(long_df)} rows)")
    print(f"  {summary_path} ({len(summary_df)} rows)")


if __name__ == "__main__":
    main()
