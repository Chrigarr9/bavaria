#!/bin/bash
# Monitor boptx calibration, restart on failure, log progress
# Run with: nohup bash monitor_calibration.sh > monitor.log 2>&1 &

BOPTX_DIR="C:/Users/VWAUCCY/dev/msf/projects/Dissertation/matsim_scenarios/bavaria/calibration/boptx"
LOG="$BOPTX_DIR/calibration_base.log"
MONITOR_LOG="$BOPTX_DIR/monitor.log"
CHECK_INTERVAL=1800  # 30 minutes

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" >> "$MONITOR_LOG"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1"
}

is_calibration_running() {
    # Check if python calibrate_base.py is running
    tasklist 2>/dev/null | grep -q "python.exe" && \
    wmic process where "name='python.exe'" get CommandLine 2>/dev/null | grep -q "calibrate_base"
    return $?
}

is_java_sim_running() {
    wmic process where "name='java.exe' and CommandLine like '%RunBavaria%'" get ProcessId 2>/dev/null | grep -qo '[0-9]'
    return $?
}

get_eval_count() {
    grep -c "^INFO:__main__:Eval " "$LOG" 2>/dev/null || echo "0"
}

get_best_objective() {
    grep "New best objective" "$LOG" 2>/dev/null | tail -1 | grep -o 'objective [0-9.]*' | grep -o '[0-9.]*'
}

get_last_mode_shares() {
    grep "car: ref=" "$LOG" 2>/dev/null | tail -1
    grep "bicycle: ref=" "$LOG" 2>/dev/null | tail -1
    grep "pt: ref=" "$LOG" 2>/dev/null | tail -1
    grep "walk: ref=" "$LOG" 2>/dev/null | tail -1
}

restart_calibration() {
    log "Restarting calibration..."

    # Kill any orphan Java sims
    wmic process where "name='java.exe' and CommandLine like '%RunBavaria%'" get ProcessId 2>/dev/null | grep -o '[0-9]*' | while read pid; do
        taskkill //PID $pid //F 2>/dev/null
        log "Killed Java PID $pid"
    done

    sleep 5

    cd "$BOPTX_DIR"
    nohup python calibrate_base.py 1 6 >> "$LOG" 2>&1 &
    disown
    log "Calibration restarted (PID: $!)"
}

check_for_errors() {
    # Check for common crash patterns in log
    if tail -20 "$LOG" 2>/dev/null | grep -q "Traceback\|PermissionError\|Error\|FAILED"; then
        local last_error=$(tail -30 "$LOG" | grep -A2 "Traceback\|Error" | tail -3)
        log "ERROR detected: $last_error"
        return 1
    fi
    return 0
}

log "=== Calibration monitor started ==="
log "Checking every ${CHECK_INTERVAL}s (30 min)"

while true; do
    sleep $CHECK_INTERVAL

    eval_count=$(get_eval_count)
    best_obj=$(get_best_objective)

    log "--- Status check ---"
    log "Completed evals: $eval_count"
    log "Best objective: ${best_obj:-none yet}"

    # Log latest mode shares
    last_shares=$(get_last_mode_shares)
    if [ -n "$last_shares" ]; then
        log "Latest mode shares:"
        echo "$last_shares" | while read line; do log "  $line"; done
    fi

    # Check if calibration python is running
    if ! is_calibration_running; then
        log "WARNING: Calibration python process not found!"

        # Check if it crashed
        if check_for_errors; then
            log "No obvious errors in log — may have finished or been killed"
        else
            log "Errors found in log"
        fi

        # Check if a Java sim is still running (orphan)
        if is_java_sim_running; then
            log "Orphan Java sim still running — waiting for it to finish"
        else
            log "No Java sim running either — restarting calibration"
            restart_calibration
        fi
    else
        log "Calibration is running normally"
    fi

    # Check if we've done enough evals and converged
    if [ "$eval_count" -gt 50 ] 2>/dev/null; then
        log "Over 50 evals completed — calibration has been running a while"
    fi
done
