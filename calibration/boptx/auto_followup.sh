#!/bin/bash
# Monitor 1% freeflow calibration, then auto-launch 10% calibration with found ASCs
# Run with: nohup bash auto_followup.sh > auto_followup.log 2>&1 &

BOPTX_DIR="C:/Users/VWAUCCY/dev/msf/projects/Dissertation/matsim_scenarios/bavaria/calibration/boptx"
LOG="$BOPTX_DIR/calibration_base.log"
CHECK_INTERVAL=300  # 5 minutes
STALE_THRESHOLD=3   # consider converged if no improvement for this many checks (15 min)
MIN_EVALS=20        # require at least this many evals before considering converged

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1"
}

get_best_objective() {
    grep "New best objective" "$LOG" 2>/dev/null | tail -1 | grep -o 'objective [0-9.]*' | grep -o '[0-9.]*'
}

get_eval_count() {
    grep -c "^INFO:__main__:Eval " "$LOG" 2>/dev/null || echo "0"
}

is_running() {
    tasklist 2>/dev/null | grep -q "python.exe" && \
    wmic process where "name='python.exe'" get CommandLine 2>/dev/null | grep -q "calibrate_base"
    return $?
}

log "=== Auto-followup monitor started ==="
log "Waiting for 1% freeflow calibration to converge..."

best_obj=""
stale_count=0

while true; do
    sleep $CHECK_INTERVAL

    current_obj=$(get_best_objective)
    eval_count=$(get_eval_count)

    log "Evals: $eval_count, Best: ${current_obj:-none}"

    # Check if calibration crashed
    if ! is_running; then
        log "Calibration process not running!"
        # If we have enough evals, consider it done
        if [ "$eval_count" -ge "$MIN_EVALS" ] 2>/dev/null; then
            log "Enough evals ($eval_count >= $MIN_EVALS), proceeding to followup"
            break
        else
            log "Not enough evals yet ($eval_count < $MIN_EVALS), waiting..."
            continue
        fi
    fi

    # Check convergence
    if [ "$current_obj" = "$best_obj" ]; then
        stale_count=$((stale_count + 1))
        log "No improvement for $stale_count checks"
        if [ "$stale_count" -ge "$STALE_THRESHOLD" ] && [ "$eval_count" -ge "$MIN_EVALS" ] 2>/dev/null; then
            log "Converged! No improvement for $((stale_count * CHECK_INTERVAL / 60)) minutes with $eval_count evals"
            break
        fi
    else
        best_obj="$current_obj"
        stale_count=0
        log "New best: $current_obj"
    fi
done

# Extract best ASCs from the log
log "=== Extracting best ASCs ==="
best_line=$(grep "New best objective" "$LOG" | sort -t' ' -k4 -n | head -1)
log "Best result: $best_line"

# Parse ASC values from the numpy array format: [car, bike, pt, walk]
best_ascs=$(echo "$best_line" | grep -o '\[.*\]')
log "Best ASC vector: $best_ascs"

# Kill the 1% calibration if still running
log "Stopping 1% calibration..."
wmic process where "name='python.exe' and CommandLine like '%calibrate_base%'" get ProcessId 2>/dev/null | grep -o '[0-9]*' | while read pid; do
    taskkill //PID $pid //F 2>/dev/null
    log "Killed python PID $pid"
done
wmic process where "name='java.exe' and CommandLine like '%RunBavaria%'" get ProcessId 2>/dev/null | grep -o '[0-9]*' | while read pid; do
    taskkill //PID $pid //F 2>/dev/null
    log "Killed java PID $pid"
done

sleep 5

# Create 10% calibration script with narrowed bounds
log "=== Setting up 10% calibration ==="

# Parse individual ASC values using python
cd "$BOPTX_DIR"
python -c "
import re

# Find the best objective line
best_obj = float('inf')
best_vals = None

with open('calibration_base.log') as f:
    for line in f:
        m = re.search(r'New best objective ([\d.]+) at \[(.*?)\]', line)
        if m:
            obj = float(m.group(1))
            if obj < best_obj:
                best_obj = obj
                vals = [float(x.strip()) for x in m.group(2).split()]
                best_vals = vals

if best_vals:
    car, bike, pt, walk = best_vals
    print(f'Best objective: {best_obj:.4f}')
    print(f'ASCs: car={car:.4f}, bike={bike:.4f}, pt={pt:.4f}, walk={walk:.4f}')

    # Write 10% calibration config with ±1.0 bounds around best
    with open('calibrate_10pct.py', 'w') as out:
        out.write(open('calibrate_base.py').read()
            .replace('population_1pct_kelheim30km.xml.gz', 'population_10pct_kelheim30km.xml.gz')
            .replace('\"--capacity\", \"100\"', '\"--capacity\", \"10\"')
            .replace('ITERATIONS = 60', 'ITERATIONS = 100')
            .replace('optimization_base_sim.p', 'optimization_10pct.p')
            .replace(
                'AscParameter(\"car\", bounds=(-1.9, 2.1), initial_value=0.1091)',
                f'AscParameter(\"car\", bounds=({car-1.0:.4f}, {car+1.0:.4f}), initial_value={car:.4f})')
            .replace(
                'AscParameter(\"bike\", bounds=(-2.9, 1.1), initial_value=-0.906)',
                f'AscParameter(\"bike\", bounds=({bike-1.0:.4f}, {bike+1.0:.4f}), initial_value={bike:.4f})')
            .replace(
                'AscParameter(\"pt\", bounds=(-2.0, 2.0), initial_value=0.045)',
                f'AscParameter(\"pt\", bounds=({pt-1.0:.4f}, {pt+1.0:.4f}), initial_value={pt:.4f})')
            .replace(
                'AscParameter(\"walk\", bounds=(-2.0, 2.0), initial_value=0.0)',
                f'AscParameter(\"walk\", bounds=({walk-1.0:.4f}, {walk+1.0:.4f}), initial_value={walk:.4f})')
            .replace('work_base', 'work_10pct')
        )
    print('Wrote calibrate_10pct.py')
else:
    print('ERROR: No best objective found!')
    exit(1)
"

if [ $? -ne 0 ]; then
    log "ERROR: Failed to create 10% calibration script"
    exit 1
fi

# Create work directory
mkdir -p "$BOPTX_DIR/work_10pct"

# Launch 10% calibration
log "Launching 10% calibration..."
cd "$BOPTX_DIR"
nohup python calibrate_10pct.py 1 6 > calibration_10pct.log 2>&1 &
disown
log "10% calibration started (PID: $!)"
log "=== Auto-followup complete ==="
