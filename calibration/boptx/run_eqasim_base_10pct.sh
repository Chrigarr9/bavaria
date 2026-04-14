#!/usr/bin/env bash
# Run eqasim-bavaria base simulation at 10% with calibrated ASCs and DMC replanning.
#
# Strategy config: the 100pct config already defines DiscreteModeChoice (weight 0.05)
# + KeepLastSelected (weight 0.95) with performReroute=false. No strategy overrides
# needed — just population, capacity, iterations, output, and ASC overrides.
#
# Usage:
#   bash run_eqasim_base_10pct.sh [iterations]
#
# Output: outputs/eqasim-base-10pct/
set -euo pipefail

ITERATIONS="${1:-100}"

# Use pwd -W (git-bash) to get Windows-native path "C:/..." so child Python
# and Java processes (native Windows binaries) can resolve the paths.
REPO="$(cd "$(dirname "$0")/../../../.." && pwd -W)"
SCENARIO_DIR="$REPO/matsim_scenarios/bavaria/output/kelheim_30km_100pct"
CONFIG_PATH="$SCENARIO_DIR/kelheim_30km_100pct_config.xml"
POPULATION_PATH="$REPO/matsim_scenarios/bavaria/output/populations_eqasim/population_10pct_kelheim30km.xml.gz"
OUTPUT_DIR="$REPO/outputs/eqasim-base-10pct"
ASC_YAML="$REPO/matsim_scenarios/bavaria/calibration/boptx/calibrated_asc.yml"
JAR_PATH="C:/matsim_cache_1pct/matsim.runtime.eqasim__83b63e4525913877d1368702e12255ef.cache/eqasim-java/bavaria/target/bavaria-1.5.0.jar"
JAVA_BINARY="C:/Users/VWAUCCY/dev/msf/.jdk/jdk-22.0.2+9/bin/java.exe"

# Read calibrated ASCs from YAML and build --mode-choice-parameter flags
ASC_ARGS=$(python -c "
import yaml, sys
with open('$ASC_YAML') as f:
    d = yaml.safe_load(f)
for k, v in d.items():
    if k.startswith('#') or not isinstance(v, (int, float)):
        continue
    print(f'--mode-choice-parameter:{k}={v:+.6f}')
" | tr '\n' ' ')

mkdir -p "$OUTPUT_DIR"

echo "=== Eqasim 10% base simulation with DMC replanning ==="
echo "  Scenario:    $SCENARIO_DIR"
echo "  Population:  $POPULATION_PATH"
echo "  Output:      $OUTPUT_DIR"
echo "  Iterations:  $ITERATIONS"
echo "  Calibrated ASCs from: $ASC_YAML"
echo "  ASC args:    $ASC_ARGS"

"$JAVA_BINARY" -Xmx40g -Djava.awt.headless=true \
  -cp "$JAR_PATH" org.eqasim.bavaria.RunSimulation \
  --config-path "$CONFIG_PATH" \
  --config:plans.inputPlansFile "$POPULATION_PATH" \
  --config:controler.outputDirectory "$OUTPUT_DIR" \
  --config:controler.lastIteration "$ITERATIONS" \
  --config:controler.overwriteFiles deleteDirectoryIfExists \
  --config:qsim.flowCapacityFactor 0.10 \
  --config:qsim.storageCapacityFactor 0.10 \
  --config:controler.createGraphsInterval 0 \
  $ASC_ARGS

echo "=== Done ==="
echo "Events file: $OUTPUT_DIR/output_events.xml.gz"
echo "Trips file:  $OUTPUT_DIR/output_trips.csv.gz"
