"""
boptx eqasim calibration for Bavaria 30km 1% scenario.

Calibrates eqasim mode-choice parameters against:
- MiD 2017 mode shares by distance band (primary, weight=1.0)

Scoring: eqasim trip-based MNL (not Charypar-Nagel plan scoring).
Population: warm-started from previous eqasim run (pre-routed plans).
Iterations: 1 (eqasim standard with pre-routed population + performReroute=false).

Parameters (Bavaria defaults ±2 on alpha_u only):
  car.alpha_u:  init=+0.4, bounds=(-1.6,  2.4)
  bike.alpha_u: init=-0.5, bounds=(-2.5,  1.5)
  walk.alpha_u: init=+1.8, bounds=(-0.2,  3.8)
  pt.alpha_u:   init=+0.0, bounds=(-2.0,  2.0)

Usage:
    cd matsim_scenarios/bavaria/calibration/boptx
    python calibrate_eqasim_base.py [parallelism] [threads]
"""

import os
import sys
import logging

logging.basicConfig(level=logging.INFO)

# Add boptx to path
BOPTX_SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "boptx-upstream", "src")
sys.path.insert(0, BOPTX_SRC)
sys.path.insert(0, os.path.join(BOPTX_SRC, "boptx", "eqasim"))
sys.path.insert(0, os.path.join(BOPTX_SRC, "boptx", "matsim"))

from objectives import ModeShareObjective
from problem import ModeParameter, CalibrationProblem, LinearPenaltyCalculator
from boptx.algorithms import DifferentialEvolutionAlgorithm
from matsim import MATSimEvaluator
from boptx.loop import Loop
from boptx.tracker import PickleTracker

# === Parse arguments ===
PARALLELISM = int(sys.argv[1]) if len(sys.argv) > 1 else 1
THREADS = int(sys.argv[2]) if len(sys.argv) > 2 else 8

# Headless mode: prevent MATSim JFreeChart listeners from crashing when no display is available.
os.environ["JAVA_TOOL_OPTIONS"] = "-Djava.awt.headless=true"

# === Paths ===
SCENARIO_DIR = os.path.realpath("../../output/kelheim_30km_1pct")
CONFIG_PATH = os.path.join(SCENARIO_DIR, "kelheim_30km_1pct_config.xml")
JAR_PATH = os.path.realpath(
    "C:/matsim_cache_1pct/matsim.runtime.eqasim__83b63e4525913877d1368702e12255ef.cache"
    "/eqasim-java/bavaria/target/bavaria-1.5.0.jar"
)
JAVA_BINARY = "C:/Users/VWAUCCY/dev/msf/.jdk/jdk-22.0.2+9/bin/java.exe"

# === Objective ===
# Distance-bucket-aware mode share (MiD 2017 reference trips by Euclidean distance band).
# Includes car_passenger — important for Bavaria rural mobility patterns.
mode_share_objective = ModeShareObjective(
    "data/reference_trips.csv",
    dict(
        modes=["car", "pt", "bicycle", "walk", "car_passenger"],
        maximum_bin_count=20,
    ),
    objective="L1",
)

penalty = LinearPenaltyCalculator(100.0, 10.0)

WORK_DIR = "work_eqasim_base"
os.makedirs(WORK_DIR, exist_ok=True)

# === Parameters ===
# Bavaria eqasim defaults (BavariaModeParameters.java) ± 2 on alpha_u only.
# Travel time betas fixed at Bavaria defaults — calibrate ASCs first to see
# how close we can get before introducing more degrees of freedom.
# Note: boptx field name is "bike" (not "bicycle") for the bicycle mode.
parameters = [
    # Warm-started from best eval #47 (objective 0.07373).
    # pt bound extended to -3.0: optimizer consistently hit -2.0 wall → allow further exploration.
    ModeParameter("car.alpha_u",  bounds=(-1.6,  2.4), initial_value= 0.827),
    ModeParameter("bike.alpha_u", bounds=(-2.5,  1.5), initial_value=-0.848),
    ModeParameter("walk.alpha_u", bounds=(-0.2,  3.8), initial_value= 2.083),
    ModeParameter("pt.alpha_u",   bounds=(-3.0,  2.0), initial_value=-2.0),
]

problem = CalibrationProblem(mode_share_objective, parameters=parameters, penalty=penalty)

# === Algorithm ===
algorithm = DifferentialEvolutionAlgorithm(problem)

# === Evaluator ===
evaluator = MATSimEvaluator(
    working_directory=WORK_DIR,
    problem=problem,
    parallelism=PARALLELISM,
    settings=dict(
        class_path=JAR_PATH,
        main_class="org.eqasim.bavaria.RunSimulation",
        memory="20g",
        java=JAVA_BINARY,
        threads=THREADS,
        iterations=1,
        arguments=[
            "--config-path", CONFIG_PATH,
            "--config:controler.createGraphsInterval", "0",
        ],
    ),
)

# === Run ===
print("=" * 60)
print("Bavaria 30km eqasim Calibration (trip-based MNL scoring)")
print("=" * 60)
print(f"  Parallelism:  {PARALLELISM}")
print(f"  Threads/eval: {THREADS}")
print(f"  Iterations:   1 (pre-routed population, performReroute=false)")
print(f"  Config:       {CONFIG_PATH}")
print(f"  Parameters ({len(parameters)}):")
for p in parameters:
    print(f"    {p.name}: init={p.initial_value}, bounds={p.bounds}")
print(f"  Objective: MiD 2017 mode shares by distance band (×1.0)")
print(f"             Modes: car, pt, bicycle, walk, car_passenger")
print(f"  Note: betaTravelTime fixed at Bavaria defaults")
print("=" * 60)

tracker = PickleTracker("optimization_eqasim_base.p")

Loop(
    algorithm=algorithm,
    evaluator=evaluator,
    maximum_evaluations=300,
).advance(callback=tracker)
