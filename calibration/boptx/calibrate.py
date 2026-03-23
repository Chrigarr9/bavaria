"""
boptx calibration for Kelheim 30km 1% scenario.

Calibrates ASCs + travel time betas against:
- MiD 2017 mode shares by distance band
- BASt 2021 traffic counts (via output_links.csv.gz)

Usage:
    cd matsim_scenarios/bavaria/calibration/boptx
    python calibrate.py mode_share [parallelism] [threads]
"""

import os, sys
import logging

logging.basicConfig(level=logging.INFO)

# Add unmodified boptx to path
BOPTX_SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "boptx-upstream", "src")
sys.path.insert(0, BOPTX_SRC)
sys.path.insert(0, os.path.join(BOPTX_SRC, "boptx", "eqasim"))
sys.path.insert(0, os.path.join(BOPTX_SRC, "boptx", "matsim"))

from objectives import ModeShareObjective, WeightedSumObjective, StuckAgentsObjective
from problem import ModeParameter, CalibrationProblem, LinearPenaltyCalculator
from boptx.algorithms import DifferentialEvolutionAlgorithm
from matsim import MATSimEvaluator

# Local custom objective
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from link_flow_objective import LinkFlowObjective

# === Parse arguments ===
selected_objective = sys.argv[1] if len(sys.argv) > 1 else "mode_share"
parallelism = int(sys.argv[2]) if len(sys.argv) > 2 else 1
threads = int(sys.argv[3]) if len(sys.argv) > 3 else 8

# === Paths ===
SCENARIO_DIR = os.path.realpath("../../output/kelheim_30km_1pct")
JAR_PATH = os.path.realpath("C:/matsim_cache_1pct/matsim.runtime.eqasim__83b63e4525913877d1368702e12255ef.cache/eqasim-java/bavaria/target/bavaria-1.5.0.jar")
CONFIG_PATH = os.path.join(SCENARIO_DIR, "kelheim_30km_1pct_config.xml")
JAVA_BINARY = "C:/Users/VWAUCCY/dev/msf/.jdk/jdk-22.0.2+9/bin/java.exe"

# === Objectives ===

# 1) Mode share by distance band (MiD 2017 targets)
mode_share_objective = ModeShareObjective(
    "data/reference_trips.csv",
    dict(
        modes=["car", "pt", "bicycle", "walk"],
        maximum_bin_count=20,
    ),
    objective="L1",
)

# 2) Traffic counts (BASt SVZ 2021, reads output_links.csv.gz)
flow_objective = LinkFlowObjective(
    "data/daily_flow.csv",
    minimum_count=10,
    objective="L1",
)

# 3) Stuck agents penalty
stuck_objective = StuckAgentsObjective()

# Combined objective
sum_objective = WeightedSumObjective()
sum_objective.add("mode_share", 1.0, mode_share_objective, True)
sum_objective.add("flow", 0.5, flow_objective)
sum_objective.add("stuck", 0.5, stuck_objective)

# === Parameters ===
# ASCs (wide bounds)
# Current defaults: car=0.4, bike=-0.5, walk=1.8, pt=0.0
# Previous calibration found: car=3.91, bike=1.07, walk=2.38, pt=-2.31
parameters = [
    # ASCs warm-started from previous calibration run
    ModeParameter("car.alpha_u", bounds=(-3.0, 5.0), initial_value=4.44),
    ModeParameter("bike.alpha_u", bounds=(-3.0, 5.0), initial_value=1.50),
    ModeParameter("walk.alpha_u", bounds=(-3.0, 5.0), initial_value=3.72),
    ModeParameter("pt.alpha_u", bounds=(-3.0, 5.0), initial_value=-2.08),
    # Travel time betas (wider bounds — previous run hit limits)
    # car: default -0.042, prev calibrated -0.010 (hit upper bound)
    ModeParameter("car.betaTravelTime_u_min", bounds=(-0.15, -0.005), initial_value=-0.010),
    # walk: default -0.162, prev calibrated -0.300 (hit lower bound)
    ModeParameter("walk.betaTravelTime_u_min", bounds=(-0.50, -0.10), initial_value=-0.300),
    # bike: default -0.093, prev calibrated -0.140
    ModeParameter("bike.betaTravelTime_u_min", bounds=(-0.25, -0.03), initial_value=-0.140),
]

penalty = LinearPenaltyCalculator(100.0, 10.0)
problem = CalibrationProblem(sum_objective, parameters, penalty)

# === Algorithm ===
algorithm = DifferentialEvolutionAlgorithm(problem)

# === Evaluator ===
evaluator = MATSimEvaluator(
    working_directory="work",
    problem=problem,
    parallelism=parallelism,
    settings=dict(
        class_path=JAR_PATH,
        main_class="org.eqasim.bavaria.RunSimulation",
        memory="20g",
        java=JAVA_BINARY,
        threads=threads,
        iterations=1,
        arguments=[
            "--config-path", CONFIG_PATH,
            "--config:controler.createGraphsInterval", "0",
        ],
    ),
)

# === Run ===
from boptx.loop import Loop
from boptx.tracker import PickleTracker

tracker = PickleTracker("optimization_{}.p".format(selected_objective))

print("Starting boptx calibration (DE, ASCs + betas + flow)...")
print(f"  Objective: {selected_objective}")
print(f"  Parallelism: {parallelism}")
print(f"  Threads per eval: {threads}")
print(f"  Parameters ({len(parameters)}):")
for p in parameters:
    print(f"    {p.name}: init={p.initial_value}, bounds={p.bounds}")
print(f"  Config: {CONFIG_PATH}")

Loop(
    algorithm=algorithm,
    evaluator=evaluator,
    maximum_evaluations=300,
).advance(callback=tracker)
