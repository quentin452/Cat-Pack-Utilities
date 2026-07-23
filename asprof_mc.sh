#!/bin/bash
# asprof_mc.sh [duration_s] [event] [pid] — flamegraph HTML du MC java vivant.
# event: wall (defaut, blocking-aware, sans perf) | cpu (perf_event_paranoid<=1) | alloc | lock
# Meilleur que `jfr view` : wall-clock + frames natives + HTML flamegraph lisible.
DUR=${1:-30}; EVENT=${2:-wall}
ASPROF="$HOME/.local/async-profiler/bin/asprof"
PID=${3:-$(pgrep -x java | head -1)}
[ -z "$PID" ] && { echo "no java pid"; exit 1; }
OUT="/tmp/asprof-mc-$PID-$EVENT.html"
echo "profiling pid=$PID ${DUR}s event=$EVENT ..."
"$ASPROF" -d "$DUR" -e "$EVENT" -f "$OUT" "$PID" && echo "FLAMEGRAPH: $OUT"
