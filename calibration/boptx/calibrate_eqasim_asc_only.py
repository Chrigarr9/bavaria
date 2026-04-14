"""
boptx ASC-only calibration for Bavaria 30 km eqasim pipeline.

Forked from calibrate_eqasim_v2.py. Differences:
  - 4 parameters only (car.alpha_u, bike.alpha_u, walk.alpha_u, pt.alpha_u).
    betaTravelTime is FROZEN at BavariaModeParameters.buildDefault() values.
  - Scenario population: raw eqasim synthesis (output/populations_eqasim/), NOT
    the Kelheim-adapted permanent populations in output/populations/.
  - Capacity factor bumped from 0.01 to 0.02 (minimum stable value for 1 % sample).
  - Reference: reference_trips.csv (verified to match MiD 2017 Niederbayern within
    1 pp on every mode, see docs/plans/2026-04-14-eqasim-drt-pipeline-design.md §3.3
    and PDF Abbildung 22 in data/bavaria/42_mid2017_regionalbericht_bayern.pdf).

Why ASC-only: betaTravelTime values come from survey estimation and should not float
during field calibration. Letting ASCs and betaTT vary simultaneously (as v2 did)
risks masking bias by trading one against the other.

Initial values: v1 best (eval #188, obj=0.073419) — widest plausible ASC region.
Bounds: ±0.75 around v1 best (wider than v2's ±0.5 because there are 4 fewer
free parameters, so the optimizer has more room to explore ASC space).

Usage:
    cd matsim_scenarios/bavaria/calibration/boptx
    python calibrate_eqasim_asc_only.py [parallelism] [threads]
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
from boptx.tracker import Tracker, PickleTracker

# === Parse arguments ===
PARALLELISM = int(sys.argv[1]) if len(sys.argv) > 1 else 1
THREADS = int(sys.argv[2]) if len(sys.argv) > 2 else 8

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
mode_share_objective = ModeShareObjective(
    "data/reference_trips.csv",
    dict(
        modes=["car", "pt", "bicycle", "walk", "car_passenger"],
        maximum_bin_count=20,
    ),
    objective="L1",
)

penalty = LinearPenaltyCalculator(100.0, 10.0)

WORK_DIR = "work_asc_only"
os.makedirs(WORK_DIR, exist_ok=True)

# === Parameters ===
# Bavaria survey defaults (BavariaModeParameters.buildDefault(), FROZEN here):
#   car.betaTravelTime_u_min    = -0.042431
#   bike.betaTravelTime_u_min   = -0.093485
#   walk.betaTravelTime_u_min   = -0.162285
#   pt.betaInVehicleTime_u_min  = -0.025501
#
# v1 best (eval #188, obj=0.073419):
#   car.alpha_u=-0.9415  bike.alpha_u=-1.3368
#   walk.alpha_u=+1.5911  pt.alpha_u=-3.0000

parameters = [
    # --- ASCs: ±0.75 around v1 best ---
    # (v1 best: car=-0.9415, bike=-1.3368, walk=+1.5911, pt=-3.0000)
    ModeParameter("car.alpha_u",  bounds=(-1.69, -0.19), initial_value=-0.9415),
    ModeParameter("bike.alpha_u", bounds=(-2.09, -0.59), initial_value=-1.3368),
    ModeParameter("walk.alpha_u", bounds=( 0.84,  2.34), initial_value= 1.5911),
    ModeParameter("pt.alpha_u",   bounds=(-3.75, -2.25), initial_value=-3.0000),
    # betaTravelTime FROZEN at BavariaModeParameters.buildDefault() — NOT calibrated
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
            # Override population: use the RAW eqasim 1 % population, not the adapted one
            "--config:plans.inputPlansFile",
                os.path.realpath("../../output/populations_eqasim/population_1pct_kelheim30km.xml.gz"),
            # Capacity factor: minimum stable for 1 % sample (existing config has 0.01 → too low)
            "--config:qsim.flowCapacityFactor",    "0.02",
            "--config:qsim.storageCapacityFactor", "0.02",
        ],
    ),
)


# === Logging tracker with per-eval mode share summary ===
# MiD Niederbayern aggregate targets for sanity checking
_MID = {"car": 0.535, "car_passenger": 0.158, "walk": 0.168, "bicycle": 0.069, "pt": 0.069}
_MODES_ORDERED = ["car", "walk", "bicycle", "pt", "car_passenger"]


class LoggingTracker(Tracker):
    """
    Wraps PickleTracker and after each round prints:
      - objective value + NEW BEST marker
      - parameter values
      - per-mode simulation share vs MiD reference (unweighted bin average)
    """

    def __init__(self, pickle_path):
        self._pickle = PickleTracker(pickle_path)
        self._best_obj = float("inf")

    def track(self, state, finished):
        self._pickle.track(state, finished)
        round_no = self._pickle.round - 1  # incremented inside track()

        non_transitional = [e for e in finished if not e.is_transitional()]
        if not non_transitional:
            return

        for ev in non_transitional:
            obj = ev.get_objective()
            vals = ev.get_values()
            marker = ""
            if obj < self._best_obj:
                self._best_obj = obj
                marker = "  *** NEW BEST ***"

            shares = self._mode_shares(ev.get_information())

            param_str = "  ".join(
                f"{p.parameter.split('.')[-1]}={v:+.4f}"
                for p, v in zip(parameters, vals)
            )
            share_str = "  ".join(
                f"{m[:4]}={shares.get(m, 0.0):.3f}(ref={_MID.get(m, 0.0):.3f})"
                for m in _MODES_ORDERED
            )
            print(f"[R{round_no:03d}] obj={obj:.6f}{marker}")
            print(f"  params: {param_str}")
            print(f"  shares: {share_str}")

        if round_no > 0 and round_no % 20 == 0:
            print(f"\n--- Best after round {round_no}: {self._best_obj:.6f} ---\n")

    @staticmethod
    def _mode_shares(info):
        """Unweighted per-bin average simulation share per mode (quick sanity check)."""
        try:
            df = info["matsim"]["objective"]["configuration"]["data"]
            return {
                m: float(df[df["mode"] == m]["simulation_share"].mean())
                for m in _MODES_ORDERED
            }
        except Exception:
            return {}


# === Run ===
print("=" * 70)
print("Bavaria 30km eqasim ASC-only Calibration (MiD Niederbayern)")
print("=" * 70)
print(f"  Parallelism:  {PARALLELISM}")
print(f"  Threads/eval: {THREADS}")
print(f"  Hot-start:    v1 best (eval #188, obj=0.073419)")
print(f"  Parameters ({len(parameters)}):")
for p in parameters:
    print(f"    {p.parameter:45s}  init={p.initial_value:+.6f}  bounds={p.bounds}")
print(f"  Objective: MiD 2017 Niederbayern mode shares by distance band (L1, 5 modes)")
print(f"  Equilibrium: deterministic (trip-based MNL, 1 iter, no reroute)")
print("=" * 70)

tracker = LoggingTracker("optimization_asc_only.p")

Loop(
    algorithm=algorithm,
    evaluator=evaluator,
    maximum_evaluations=300,
).advance(callback=tracker)
