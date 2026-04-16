"""
boptx calibration for RunBavariaBaseSimulation.

Calibrates MATSim scoring ASCs (car, bike, pt, walk) against:
- MiD 2017 mode shares by distance band (primary, weight=1.0)
- BASt 2021 traffic counts (secondary, weight=0.3)

Uses the 1% pre-filtered population for fast iteration (~3-5 min/eval).

Usage:
    cd matsim_scenarios/bavaria/calibration/boptx
    python calibrate_base.py [parallelism] [threads]
"""

import os
import sys
import json
import shutil
import subprocess as sp
import logging
import glob

import numpy as np
import pandas as pd

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Add boptx to path
BOPTX_SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "boptx-upstream", "src")
sys.path.insert(0, BOPTX_SRC)

from boptx.problem import Problem, ContinuousParameter
from boptx.evaluator import BaseEvaluator, DefaultEvaluation
from boptx.algorithms import DifferentialEvolutionAlgorithm
from boptx.loop import Loop
from boptx.tracker import PickleTracker

# Local objectives
from matsim_mode_share_objective import MATSimModeShareObjective
from link_flow_objective import LinkFlowObjective

# === Config ===
PARALLELISM = int(sys.argv[1]) if len(sys.argv) > 1 else 1
THREADS = int(sys.argv[2]) if len(sys.argv) > 2 else 6

# Paths
PROJECT_ROOT = os.path.realpath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
DRT_MODULE = os.path.join(PROJECT_ROOT, "matsim-libs", "contribs", "drt-demand-extraction")
SCENARIO_PATH = os.path.realpath(os.path.join(os.path.dirname(__file__), "..", "..", "output", "kelheim_30km_100pct"))
POPULATION = os.path.realpath(os.path.join(os.path.dirname(__file__), "..", "..", "output", "populations", "population_1pct_kelheim30km.xml.gz"))
WORKING_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "work_base")

ITERATIONS = 60  # Enough for 1% convergence with DMC annealing

os.makedirs(WORKING_DIR, exist_ok=True)


# === ASC Parameter ===
class AscParameter(ContinuousParameter):
    """A MATSim scoring ASC parameter."""

    def __init__(self, mode, bounds=(-5.0, 5.0), initial_value=0.0):
        super().__init__(f"ASC({mode})", bounds, initial_value)
        self.mode = mode


# === Objectives ===
mode_share_objective = MATSimModeShareObjective(
    "data/reference_trips.csv",
    modes=["car", "bicycle", "pt", "walk", "car_passenger"],
    max_bins=20,
    objective="L1",
)

flow_objective = LinkFlowObjective(
    "data/daily_flow.csv",
    minimum_count=10,
    objective="L1",
)


# === Problem ===
class BaseSimCalibrationProblem(Problem):

    def __init__(self, parameters):
        self.parameters = parameters

    def get_parameters(self):
        return self.parameters

    def get_settings(self):
        return {}

    def get_state_count(self):
        return mode_share_objective.get_state_count()


# Parameters: Calibrate car, ride, bike — keep pt, walk fixed at Kelheim values.
# Kelheim ASCs: car=0.109, bike=-0.906, pt=0.045, ride=-0.449, walk=0.0
# Initial values from best 1% result: car=0.68, ride=-1.46
parameters = [
    AscParameter("car", bounds=(-0.5, 1.5), initial_value=0.683),
    AscParameter("bike", bounds=(-2.5, -0.5), initial_value=-0.906),   # allow downward from Kelheim
    AscParameter("ride", bounds=(-2.5, 0.5), initial_value=-1.455),
]

# Fixed ASCs (not calibrated, always passed to simulation)
FIXED_ASCS = {"pt": 0.045, "walk": 0.0}

problem = BaseSimCalibrationProblem(parameters)


# === Evaluator ===
class BaseSimEvaluator(BaseEvaluator):
    """Runs RunBavariaBaseSimulation with ASC overrides for each candidate."""

    def __init__(self, working_directory, parallelism=1):
        super().__init__(parallelism=parallelism)
        self.working_directory = os.path.realpath(working_directory)
        self.simulations = {}

    def start_evaluation(self, identifier, values, information):
        sim_path = os.path.join(self.working_directory, str(identifier))
        if os.path.exists(sim_path):
            # Windows: files may be locked briefly after process exit — retry
            for attempt in range(5):
                try:
                    shutil.rmtree(sim_path)
                    break
                except PermissionError:
                    import time
                    time.sleep(2)
            else:
                logger.warning("Could not fully clean %s, recreating", sim_path)
        os.makedirs(sim_path, exist_ok=True)

        # Write ASC overrides file (calibrated + fixed)
        asc_values = dict(FIXED_ASCS)  # start with fixed Kelheim values
        for param, value in zip(parameters, values):
            asc_values[param.mode] = round(float(value), 6)

        overrides_file = os.path.join(sim_path, "asc_overrides.json")
        with open(overrides_file, "w") as f:
            json.dump(asc_values, f)

        output_dir = os.path.join(sim_path, "output")

        # Build exec args string for mvn exec:java
        exec_args = " ".join([
            "--scenario-path", SCENARIO_PATH,
            "--population", POPULATION,
            "--sample", "100",       # population is already 1%
            "--capacity", "3",       # 3x overscaling for 1% sample to avoid gridlock artifacts
            "--iterations", str(ITERATIONS),
            "--dmc-start-rate", "0.20",
            "--dmc-end-rate", "0.05",
            "--output-dir", output_dir,
            "--asc-overrides", overrides_file,
        ])

        cmd = [
            "mvn.cmd", "exec:java", "-o",
            f"-Dexec.mainClass=org.matsim.contrib.demand_extraction.run.RunBavariaBaseSimulation",
            f"-Dexec.args={exec_args}",
            "-Denforcer.skip=true",
        ]

        stdout = open(os.path.join(sim_path, "simulation_output.log"), "w+")
        stderr = open(os.path.join(sim_path, "simulation_error.log"), "w+")

        logger.info("Starting eval %s: ASCs=%s", identifier, asc_values)

        self.simulations[identifier] = {
            "process": sp.Popen(cmd, stdout=stdout, stderr=stderr, cwd=DRT_MODULE),
            "status": "running",
            "progress": -1,
            "values": values,
            "information": information,
            "sim_path": sim_path,
        }

    def _ping(self):
        for identifier, sim in list(self.simulations.items()):
            if sim["status"] == "running":
                rc = sim["process"].poll()
                if rc is None:
                    # Still running — check progress
                    iteration = self._get_iteration(identifier)
                    if iteration > sim["progress"]:
                        sim["progress"] = iteration
                        logger.info("  eval %s: iteration %d/%d",
                                    identifier, iteration, ITERATIONS)
                elif rc == 0:
                    logger.info("Finished eval %s", identifier)
                    sim["status"] = "done"
                else:
                    logger.error("FAILED eval %s (rc=%d)", identifier, rc)
                    sim["status"] = "done"  # Let objective return high penalty

    def _get_iteration(self, identifier):
        sw_pattern = os.path.join(
            self.working_directory, str(identifier), "output", "*stopwatch*"
        )
        matches = glob.glob(sw_pattern)
        if matches and os.path.isfile(matches[0]):
            try:
                df = pd.read_csv(matches[0], sep="\t")
                if len(df) > 0:
                    return int(df["Iteration"].max())
            except Exception:
                pass
        return -1

    def check_evaluation(self, identifier):
        self._ping()
        return self.simulations[identifier]["status"] == "done"

    def get_evaluation(self, identifier):
        if not self.check_evaluation(identifier):
            raise RuntimeError(f"Simulation {identifier} not ready")

        sim = self.simulations[identifier]
        output_path = os.path.join(sim["sim_path"], "output")

        # Calculate objectives
        mode_result = mode_share_objective.calculate(output_path)
        flow_result = flow_objective.calculate(output_path)

        # Mode shares only — flow is unreliable (missing through-traffic + commercial)
        obj = 1.0 * mode_result["objective"]

        logger.info(
            "Eval %s: mode_share=%.4f, flow=%.4f, total=%.4f",
            identifier, mode_result["objective"], flow_result["objective"], obj,
        )

        information = sim["information"] or {}
        information["mode_share"] = mode_result
        information["flow"] = flow_result

        return DefaultEvaluation(sim["values"], obj, information)

    def clean_evaluation(self, identifier):
        sim = self.simulations.pop(identifier, None)
        if sim:
            sim_path = sim["sim_path"]
            if os.path.exists(sim_path):
                shutil.rmtree(sim_path)



# === Run ===
if __name__ == "__main__":
    algorithm = DifferentialEvolutionAlgorithm(problem)
    evaluator = BaseSimEvaluator(WORKING_DIR, parallelism=PARALLELISM)
    tracker = PickleTracker("optimization_base_sim.p")

    print("=" * 60)
    print("Bavaria Base Simulation ASC Calibration")
    print("=" * 60)
    print(f"  Parallelism: {PARALLELISM}")
    print(f"  Threads: {THREADS}")
    print(f"  Iterations per eval: {ITERATIONS}")
    print(f"  Population: {POPULATION}")
    print(f"  Parameters ({len(parameters)}):")
    for p in parameters:
        print(f"    {p.name}: init={p.initial_value}, bounds={p.bounds}")
    print(f"  Objectives: mode_share(×1.0) + flow(×0.3)")
    print("=" * 60)

    Loop(
        algorithm=algorithm,
        evaluator=evaluator,
        maximum_evaluations=200,
    ).advance(callback=tracker)
