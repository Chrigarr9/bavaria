"""
boptx ASC-only calibration for Bavaria 30 km eqasim pipeline.

v4 (2026-04-15): Car-reference + PT penalty pins + walk/bike betaTT
-------------------------------------------------------------------
Building on v3 (MiD Niederbayern proxy target, car as reference).

What v3 discovered (via mode_cache.csv analysis on the 100% demand-
extraction run and BavariaPtUtilityEstimator source reading):

  The Bavaria pt utility estimator applies TWO Munich-calibrated
  penalties that fire on ~100% of trips in rural Kelheim and are
  structurally incompatible with the target:
    - bavariaPt.onlyBus_u          = -1.416309  (trip has no rail leg)
    - bavariaPt.betaDrivingPermit_u = -0.531426  (license holder)
  Together they stack ~-1.95 utils onto every pt choice in the
  30 km Kelheim buffer, invisible to any pt.alpha_u tuning. v3 sat
  pt.alpha_u at -1.65 (not saturating bounds) because further
  increases didn't move pt share enough to justify hurting other
  modes — the gap wasn't closable with ASCs alone.

  Separately, the pt/car travel time distribution (SwissRailRaptor
  on the actual kelheim_30km_100pct transit schedule) shows median
  pt is 5.6x slower than car across the buffer; at 10-20 km it's
  6.3x slower. This is the real service-quality ceiling on pt share.

v4 changes:
  1. Pin onlyBus_u and betaDrivingPermit_u to 0.0 via CLI. These are
     Munich artifacts that do not apply to the bus-only rural network.
     Reclaims +1.95 utils on every license-holder pt choice without
     any Java rebuild (ParameterDefinition.applyCommandLine supports
     nested field paths reflectively).
  2. Add walk.betaTravelTime_u_min and bike.betaTravelTime_u_min as
     free parameters. v3 accepted walk shape and bike over-distance
     as "structural." In reality, betaTT controls the slope of the
     distance-vs-share curve. A steeper beta_walk sharpens the walk
     drop-off (fixing 0.5-2 km over), a steeper beta_bike kills the
     bike long-distance tail (fixing +10 pp at 5-50 km).
  3. Widen pt.alpha_u bounds from (-5, +1.5) to (-3, +2) now that
     the constant penalty stack is gone. v3's -1.65 optimum shifts
     by ~+2 under the new baseline; (+2 upper) gives headroom.
  4. Bump DE candidates 4 -> 8. 4 is too tight for the 6-dim problem;
     initial sampling in 6D with 4 points is structurally under-
     covered. 8 roughly doubles cold-start exploration.
  5. Bump maximum_evaluations 300 -> 400. More generations, still
     fits overnight (~5 min/eval x 400 = ~33 h ceiling, will usually
     converge well before that).

Known residuals the calibration STILL cannot fix (v4 scope):
  - PT fare model is hardcoded Munich MVV (BavariaPtCostModel.java,
    shortPrice=1.9, basePrice_h=8.0, zonal tables). Rural trips
    fall through to ~EUR 24 for a 132-min bus trip, invisible to
    CLI (not in BavariaCostParameters). Would need Java patch +
    JAR rebuild. Not done in v4.
  - Car fixed cost (0-500 m overshoot): same story, requires
    BavariaCarCostModel.java patch.
  - Single ASC still cannot fix car short-over AND long-under
    simultaneously; car at 0 normalises this rather than solves it.

DMC coverage: uses kelheim_30km_1pct_asc_only_config.xml with
DiscreteModeChoice strategy weight 1.0 (vs 0.05 in v1/v2). With
lastIteration=1 every agent gets fresh mode choice exactly once per eval
— no warm-start noise floor.

Usage:
    cd matsim_scenarios/bavaria/calibration/boptx
    # regenerate the target pickle after any MiD source change:
    python build_niederbayern_target.py
    # run the calibration:
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
# Use the ASC-only config variant with DiscreteModeChoice weight=1.0 so every
# agent is re-mode-chosen every eval (no 95% warm-start noise floor).
SCENARIO_DIR = os.path.realpath("../../output/kelheim_30km_1pct")
CONFIG_PATH = os.path.join(SCENARIO_DIR, "kelheim_30km_1pct_asc_only_config.xml")
JAR_PATH = os.path.realpath(
    "C:/matsim_cache_1pct/matsim.runtime.eqasim__83b63e4525913877d1368702e12255ef.cache"
    "/eqasim-java/bavaria/target/bavaria-1.5.0.jar"
)
JAVA_BINARY = "C:/Users/VWAUCCY/dev/msf/.jdk/jdk-22.0.2+9/bin/java.exe"

# === Objective ===
# Calibration target: MiD 2017 Niederbayern proxy, derived via IPF from the
# Bayern W10.5 cross-table (Wegelaenge x Hauptverkehrsmittel) reweighted to
# Niederbayern marginals (aggregate mode split from Kurzreport + aggregate
# distance distribution from Regionalbericht Tabelle 14).
#
# See build_niederbayern_target.py for provenance and regeneration.
#
# The shares_path pickle overrides the per-(mode, bin) reference shares used
# by ModeShareObjective. The bins it provides are FIXED at 7 MiD distance
# bands that are reachable by the Kelheim 30 km scenario:
#   <0.5, 0.5-1, 1-2, 2-5, 5-10, 10-20, 20-50 km.
# The 50-100 km and >100 km bands from the full MiD W12 set are dropped:
# the scenario geography physically cannot produce those trips.
# reference_trips.csv is still needed by ModeShareObjective for its initial
# self.bounds computation (immediately overridden by the pickle) and for
# filtering, but its per-bin shares are IGNORED in favour of the pickle.
mode_share_objective = ModeShareObjective(
    "data/reference_trips.csv",
    dict(
        modes=["car", "pt", "bicycle", "walk", "car_passenger"],
        maximum_bin_count=20,
    ),
    objective="L1",
    shares_path="data/niederbayern_proxy_shares.pkl",
)

penalty = LinearPenaltyCalculator(100.0, 10.0)

WORK_DIR = "work_asc_only"
os.makedirs(WORK_DIR, exist_ok=True)

# === Parameters ===
# Reference mode: car. car.alpha_u is FIXED AT 0 (not calibrated) and all
# other ASCs express utility *relative to car*.
#
# Bavaria survey defaults (BavariaModeParameters.buildDefault()):
#   car.betaTravelTime_u_min    = -0.042431   (FROZEN)
#   bike.betaTravelTime_u_min   = -0.093485   (now a FREE param in v4)
#   walk.betaTravelTime_u_min   = -0.162285   (now a FREE param in v4)
#   pt.betaInVehicleTime_u_min  = -0.025501   (FROZEN; rare to tune)
#
# Why free the walk/bike betaTT in v4: they directly shape the distance-
# vs-share curve. v3 with ASCs-only showed structural walk-over at
# 0.5-2 km and bike-over at 5-50 km; steeper betaTT sharpens the
# decay, flat betaTT extends it. Literature discrete-choice estimates
# for walk betaTT range -0.10 to -0.25; bike -0.07 to -0.18. Bounds
# straddle the Bavaria default generously in both directions.
#
# Widened pt.alpha_u bounds vs v3: (-5, +1.5) -> (-3, +2). With the
# constant penalty stack (onlyBus_u + betaDrivingPermit_u) pinned to
# zero, pt's baseline utility shifts up by ~1.95 utils, so v3's -1.65
# optimum should land near +0.30 under the new baseline. (+2 upper
# gives headroom; -3 lower retained for safety if the new baseline
# overcorrects.)
parameters = [
    ModeParameter("bike.alpha_u",              bounds=(-4.00,  1.00), initial_value=-1.50),
    ModeParameter("walk.alpha_u",              bounds=(-1.00,  4.00), initial_value= 1.50),
    ModeParameter("pt.alpha_u",                bounds=(-3.00,  2.00), initial_value= 0.00),
    ModeParameter("carPassenger.alpha_u",      bounds=(-4.00,  1.00), initial_value=-1.50),
    ModeParameter("walk.betaTravelTime_u_min", bounds=(-0.30, -0.08), initial_value=-0.162285),
    ModeParameter("bike.betaTravelTime_u_min", bounds=(-0.22, -0.05), initial_value=-0.093485),
    # car.alpha_u: FIXED at 0.0 via CLI flag in evaluator (below)
    # bavariaPt.onlyBus_u, bavariaPt.betaDrivingPermit_u: pinned to 0.0 via CLI
]

problem = CalibrationProblem(mode_share_objective, parameters=parameters, penalty=penalty)

# === Algorithm ===
# candidates=8 (up from DE default 4): with 6 free params, 4 initial
# samples in 6D is structurally undersampled. 8 roughly doubles the
# cold-start coverage without doubling per-generation cost (the DE
# loop only runs what's needed per generation anyway).
algorithm = DifferentialEvolutionAlgorithm(problem, candidates=8)

# === Evaluator ===
evaluator = MATSimEvaluator(
    working_directory=WORK_DIR,
    problem=problem,
    parallelism=PARALLELISM,
    settings=dict(
        class_path=JAR_PATH,
        main_class="org.eqasim.bavaria.RunSimulation",
        # Dropped from 20g -> 12g in v4b: 1pct sims have ~6k persons and
        # peak at ~4 GB heap. 20 GB was wildly oversized and inflated the
        # Windows process commit charge, which on this shared machine
        # (~130 GB RAM but bounded pagefile) triggered intermittent
        # OS-level kills every ~30-130 evals regardless of actual heap
        # usage. 12g leaves slack for GC and outliers without puffing
        # commit. Combined with the matsim.py _ping() retry patch, this
        # should make 400-eval overnight runs reliable.
        memory="12g",
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
            # Fix car ASC at 0 — car is the reference alternative, all other
            # calibrated ASCs are utility deltas vs car. Overrides the Bavaria
            # default of +0.40. Must be reapplied every eval since boptx
            # generates --mode-choice-parameter flags only for the free params.
            "--mode-choice-parameter:car.alpha_u", "0.0",
            # Pin the Bavaria-specific pt penalty stack to ZERO. These are
            # Munich-calibrated (bavariaPt.onlyBus_u fires when a pt trip
            # has no rail leg — always true in rural Kelheim; betaDrivingPermit_u
            # fires on all license-holding adults — ~95% of the population).
            # Together they stacked -1.95 utils on every pt choice in v3
            # and were invisible to pt.alpha_u. ParameterDefinition's
            # applyCommandLine walks nested field paths reflectively so
            # these work without a Java rebuild.
            "--mode-choice-parameter:bavariaPt.onlyBus_u", "0.0",
            "--mode-choice-parameter:bavariaPt.betaDrivingPermit_u", "0.0",
        ],
    ),
)


# === Logging tracker with per-eval mode share summary ===
# MiD 2017 Niederbayern aggregate targets (Kurzreport Bayern p.13, Regierungsbezirk row)
_MID = {"car": 0.54, "car_passenger": 0.16, "walk": 0.16, "bicycle": 0.07, "pt": 0.07}
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
                f"{p.parameter}={v:+.4f}"
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
print("Bavaria 30km eqasim ASC + walk/bike betaTT Calibration v4")
print("=" * 70)
print(f"  Parallelism:  {PARALLELISM}")
print(f"  Threads/eval: {THREADS}")
print(f"  DE candidates: 8   max_evals: 400")
print(f"  Pinned via CLI:")
print(f"    car.alpha_u                     = 0.0  (reference)")
print(f"    bavariaPt.onlyBus_u             = 0.0  (was -1.416, Munich MVV artifact)")
print(f"    bavariaPt.betaDrivingPermit_u   = 0.0  (was -0.531, Munich MVV artifact)")
print(f"  Free parameters ({len(parameters)}):")
for p in parameters:
    print(f"    {p.parameter:42s}  init={p.initial_value:+.6f}  bounds={p.bounds}")
print(f"  Objective: MiD 2017 Niederbayern mode shares by distance band (L1, 5 modes)")
print(f"  Equilibrium: deterministic (trip-based MNL, 1 iter, no reroute)")
print("=" * 70)

tracker = LoggingTracker("optimization_asc_only.p")

Loop(
    algorithm=algorithm,
    evaluator=evaluator,
    maximum_evaluations=400,
).advance(callback=tracker)
