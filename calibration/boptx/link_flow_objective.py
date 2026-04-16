"""
Custom FlowObjective that reads MATSim's output_links.csv.gz (vol_car)
instead of eqasim_counts.csv. Works with Bavaria RunSimulation which
doesn't support --count-links.
"""

import pandas as pd
import numpy as np
import logging

logger = logging.getLogger(__name__)


class LinkFlowObjective:
    def __init__(self, reference_path, minimum_count=10, objective="L1"):
        self.objective = objective
        self.minimum_count = minimum_count
        self.df_reference = pd.read_csv(reference_path, sep=";")
        # Expects columns: link_id, flow

    def get_state_count(self):
        return 0

    def calculate(self, simulation_path):
        # Read MATSim link volumes (may have run-ID prefix)
        import glob as _glob
        matches = _glob.glob("{}/*output_links.csv.gz".format(simulation_path))
        links_path = matches[0] if matches else "{}/output_links.csv.gz".format(simulation_path)
        try:
            df_sim = pd.read_csv(links_path, sep=";", usecols=["link", "vol_car"],
                                 dtype={"link": str})
            df_sim = df_sim.rename(columns={"link": "link_id", "vol_car": "simulation_flow"})
        except Exception as e:
            logger.warning("Could not read link volumes: %s", e)
            return {"objective": 1.0, "states": [], "type": "link_flow",
                    "configuration": {"matched": 0, "error": str(e)}}

        # Merge with reference
        df_ref = self.df_reference.rename(columns={"flow": "reference_flow"})
        df_ref["link_id"] = df_ref["link_id"].astype(str)
        df_merged = pd.merge(df_ref, df_sim, on="link_id", how="inner")

        # Filter by minimum count
        df_merged = df_merged[df_merged["reference_flow"] >= self.minimum_count]

        if len(df_merged) == 0:
            logger.warning("No matching links found for flow comparison")
            return {"objective": 1.0, "states": [], "type": "link_flow",
                    "configuration": {"matched": 0}}

        # Relative error
        df_merged["relative_error"] = (
            (df_merged["simulation_flow"] - df_merged["reference_flow"]).abs()
            / df_merged["reference_flow"]
        )

        if self.objective.upper() == "L1":
            obj = df_merged["relative_error"].mean()
        elif self.objective.upper() == "L2":
            obj = np.sqrt((df_merged["relative_error"] ** 2).mean())
        else:
            obj = df_merged["relative_error"].max()

        return {
            "objective": float(obj),
            "states": [],
            "type": "link_flow",
            "configuration": {
                "matched": len(df_merged),
                "mean_relative_error": float(df_merged["relative_error"].mean()),
                "median_relative_error": float(df_merged["relative_error"].median()),
            },
        }
