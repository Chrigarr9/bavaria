"""
ModeShareObjective that reads MATSim's output_trips.csv.gz instead of
eqasim_trips.csv. Computes mode shares by distance bin, compares to MiD
reference trips using L1 norm.

Compatible with boptx objective interface.
"""

import pandas as pd
import numpy as np
import gzip
import logging

logger = logging.getLogger(__name__)

# Map MATSim mode names to reference data mode names
MATSIM_TO_REFERENCE = {
    "bike": "bicycle",
    "car": "car",
    "pt": "pt",
    "walk": "walk",
    "ride": "car_passenger",
}


def calculate_distance_bins(df_reference, modes, max_bins=20):
    """Calculate distance bin boundaries from reference data (quantile-based)."""
    values = df_reference["euclidean_distance"].values
    weights = df_reference["weight"].values

    sorter = np.argsort(values)
    values = values[sorter]
    cdf = np.cumsum(weights[sorter])
    cdf = cdf / cdf[-1]

    probabilities = np.linspace(0.0, 1.0, max_bins + 1)
    quantiles = np.unique([
        values[np.argmin(cdf <= p)] for p in probabilities
    ])

    bins = list(zip(range(len(quantiles) - 1), quantiles[:-1], quantiles[1:]))
    return {mode: bins for mode in modes}, "euclidean_distance"


def calculate_shares(df_trips, bins_info):
    """Calculate mode shares per distance bin."""
    bins, dist_col = bins_info
    records = []

    df_trips = df_trips[df_trips["mode"].isin(bins.keys())]

    for mode in bins:
        df_mode = df_trips[df_trips["mode"] == mode]
        for idx, lower, upper in bins[mode]:
            mask_all = df_trips[dist_col].between(lower, upper, inclusive="left")
            mask_mode = df_mode[dist_col].between(lower, upper, inclusive="left")
            total = df_trips.loc[mask_all, "weight"].sum()
            mode_count = df_mode.loc[mask_mode, "weight"].sum()
            share = mode_count / total if total > 0 else 0.0
            records.append({
                "mode": mode, "bin_index": idx,
                "lower_bound": lower, "upper_bound": upper,
                "share": share,
            })

    return pd.DataFrame.from_records(records)


class MATSimModeShareObjective:
    """
    Computes L1 deviation of MATSim simulation mode shares (by distance bin)
    from MiD 2017 reference data.

    Reads MATSim's output_trips.csv.gz (columns: main_mode, euclidean_distance).
    """

    def __init__(self, reference_path, modes=None, max_bins=20, objective="L1"):
        self.objective = objective
        self.modes = modes or ["car", "bicycle", "pt", "walk"]

        # Load reference
        self.df_reference = pd.read_csv(reference_path, sep=";")
        if "weight" not in self.df_reference and "trip_weight" in self.df_reference:
            self.df_reference["weight"] = self.df_reference["trip_weight"]

        self.bins = calculate_distance_bins(
            self.df_reference, self.modes, max_bins
        )

        # Pre-compute reference shares
        self.df_ref_shares = calculate_shares(self.df_reference, self.bins)

        n_bins = len(list(self.bins[0].values())[0])
        logger.info(
            "MATSimModeShareObjective: %d modes × %d bins = %d cells",
            len(self.modes), n_bins, len(self.modes) * n_bins,
        )

    def get_state_count(self):
        return sum(len(b) for b in self.bins[0].values())

    def calculate(self, simulation_path):
        """Read output_trips.csv.gz and compare to reference."""
        # Find the trips file (may have run ID prefix)
        import glob
        patterns = [
            f"{simulation_path}/*output_trips.csv.gz",
            f"{simulation_path}/output_trips.csv.gz",
        ]
        trips_file = None
        for pat in patterns:
            matches = glob.glob(pat)
            if matches:
                trips_file = matches[0]
                break

        if trips_file is None:
            logger.error("No output_trips.csv.gz found in %s", simulation_path)
            return {
                "objective": 100.0,
                "states": np.zeros(self.get_state_count()),
                "type": "mode_share",
            }

        # Read simulation trips
        df_sim = pd.read_csv(trips_file, sep=";",
                             usecols=["main_mode", "euclidean_distance"])

        # Map MATSim mode names to reference names
        df_sim = df_sim.rename(columns={"main_mode": "mode"})
        df_sim["mode"] = df_sim["mode"].map(
            lambda m: MATSIM_TO_REFERENCE.get(m, m)
        )
        df_sim["weight"] = 1.0
        df_sim["euclidean_distance"] = pd.to_numeric(
            df_sim["euclidean_distance"], errors="coerce"
        )
        df_sim = df_sim.dropna(subset=["euclidean_distance"])

        # Calculate simulation shares
        df_sim_shares = calculate_shares(df_sim, self.bins)

        # Merge and compute difference
        df_diff = pd.merge(
            self.df_ref_shares.rename(columns={"share": "reference_share"}),
            df_sim_shares.rename(columns={"share": "simulation_share"})[
                ["simulation_share", "mode", "bin_index"]
            ],
            on=["mode", "bin_index"],
            how="left",
        ).fillna(0.0)

        df_diff["difference"] = (
            df_diff["simulation_share"] - df_diff["reference_share"]
        )

        # Compute objective
        errors = np.abs(df_diff["difference"].values)
        if self.objective.upper() == "L1":
            obj = np.mean(errors)
        elif self.objective.upper() == "L2":
            obj = np.sqrt(np.mean(errors ** 2))
        else:
            obj = np.max(errors)

        # Log summary
        for mode in self.modes:
            dm = df_diff[df_diff["mode"] == mode]
            ref_avg = dm["reference_share"].mean()
            sim_avg = dm["simulation_share"].mean()
            logger.info("  %s: ref=%.3f sim=%.3f", mode, ref_avg, sim_avg)

        states = df_diff.sort_values(
            ["mode", "bin_index"]
        )["simulation_share"].values

        return {
            "objective": float(obj),
            "type": "mode_share",
            "states": states,
            "configuration": {
                "data": df_diff,
                "bins": self.bins,
            },
        }
