#!/bin/bash
# Full Bavaria pipeline: eqasim → adapt → permanent populations → base simulation
# Run from: matsim_scenarios/bavaria/
# Usage: nohup bash run_full_pipeline.sh > pipeline.log 2>&1 &
set -e

BAVARIA_PYTHON="C:/Users/VWAUCCY/AppData/Roaming/mamba/envs/bavaria/python.exe"
DRT_DIR="C:/Users/VWAUCCY/dev/msf/projects/Dissertation/matsim-libs/contribs/drt-demand-extraction"
SCENARIO_DIR="C:/Users/VWAUCCY/dev/msf/projects/Dissertation/matsim_scenarios/bavaria"
OUTPUT_DIR="$SCENARIO_DIR/output/kelheim_30km_100pct"
PERM_DIR="$SCENARIO_DIR/output/populations"

export MAVEN_OPTS="-Xmx100g"

echo "============================================================"
echo "FULL BAVARIA PIPELINE — $(date)"
echo "============================================================"
echo ""

# ============================================================
# PHASE 1: Re-run eqasim 100% scenario
# ============================================================
echo "=== PHASE 1: eqasim 100% scenario generation ==="
echo "Started: $(date)"
cd "$SCENARIO_DIR"

"$BAVARIA_PYTHON" -m synpp config_kelheim_30km_100pct.yml

echo "Phase 1 complete: $(date)"
echo ""

if [ ! -f "$OUTPUT_DIR/kelheim_30km_100pct_population.xml.gz" ]; then
    echo "ERROR: eqasim output not found!"
    exit 1
fi
echo "Phase 1 verified."

# ============================================================
# PHASE 2: Adapt eqasim attributes to Kelheim format
# ============================================================
echo "=== PHASE 2: Adapt eqasim attributes ==="
echo "Started: $(date)"
cd "$DRT_DIR"

mvn exec:java -o \
  -Dexec.mainClass="org.matsim.contrib.demand_extraction.upsampling.RunAdaptEqasimPopulation" \
  -Dexec.args="--population $OUTPUT_DIR/kelheim_30km_100pct_population.xml.gz --households $OUTPUT_DIR/kelheim_30km_100pct_households.csv --output $OUTPUT_DIR/kelheim_30km_100pct_population_adapted.xml.gz" \
  -Denforcer.skip=true

echo "Phase 2 complete: $(date)"
echo ""

if [ ! -f "$OUTPUT_DIR/kelheim_30km_100pct_population_adapted.xml.gz" ]; then
    echo "ERROR: Adapted population not found!"
    exit 1
fi
echo "Phase 2 verified."

# ============================================================
# PHASE 3: Create permanent pre-filtered populations
# ============================================================
echo "=== PHASE 3: Create permanent populations (1%, 10%, 25%, 100%) ==="
echo "Started: $(date)"
cd "$DRT_DIR"

mkdir -p "$PERM_DIR"

mvn exec:java -o \
  -Dexec.mainClass="org.matsim.contrib.demand_extraction.upsampling.RunCreatePermanentPopulations" \
  -Dexec.args="--population $OUTPUT_DIR/kelheim_30km_100pct_population_adapted.xml.gz --output-dir $PERM_DIR --center-x 709432.34 --center-y 5421450.16 --radius 30000 --samples 1,10,25,100" \
  -Denforcer.skip=true

echo "Phase 3 complete: $(date)"
echo ""

for pct in 1 10 25 100; do
    if [ ! -f "$PERM_DIR/population_${pct}pct_kelheim30km.xml.gz" ]; then
        echo "ERROR: ${pct}% population not found!"
        exit 1
    fi
done
echo "Phase 3 verified: all 4 population files exist."

# ============================================================
# PHASE 4: Base simulation (25%, 100 iterations)
# ============================================================
echo "=== PHASE 4: Base simulation 25%, 100 iterations ==="
echo "Started: $(date)"
cd "$DRT_DIR"

mvn exec:java -o \
  -Dexec.mainClass="org.matsim.contrib.demand_extraction.run.RunBavariaBaseSimulation" \
  -Dexec.args="--scenario-path $OUTPUT_DIR --population $PERM_DIR/population_25pct_kelheim30km.xml.gz --sample 100 --capacity 25 --iterations 100 --output-dir $SCENARIO_DIR/output/base-simulation-25pct" \
  -Denforcer.skip=true

echo "Phase 4 complete: $(date)"
echo ""

if [ -f "$SCENARIO_DIR/output/base-simulation-25pct/travel_times.tsv" ]; then
    echo "Travel times exported successfully."
else
    echo "WARNING: travel_times.tsv not found — check simulation output"
fi

echo "============================================================"
echo "FULL PIPELINE COMPLETE — $(date)"
echo "============================================================"
echo ""
echo "Outputs:"
echo "  eqasim 100%:   $OUTPUT_DIR/"
echo "  Adapted pop:   $OUTPUT_DIR/kelheim_30km_100pct_population_adapted.xml.gz"
echo "  Populations:   $PERM_DIR/population_{1,10,25,100}pct_kelheim30km.xml.gz"
echo "  Base sim:      $SCENARIO_DIR/output/base-simulation-25pct/"
echo "  Travel times:  $SCENARIO_DIR/output/base-simulation-25pct/travel_times.tsv"
