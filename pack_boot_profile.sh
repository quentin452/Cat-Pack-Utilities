#!/usr/bin/env bash
# Boot the Biggess pack once, wait for LoadComplete, report per-mod boot times,
# then ALWAYS terminate the game. Guards against double instances.
# Usage: pack_boot_profile.sh [--jfr] [timeout_seconds=600]
set -u
INST="/home/iamacat/Documents/curseforge/minecraft/Instances/Biggess Pack Cat Edition V1 TEST"
ARGFILE="/home/iamacat/Documents/GitHub/Mod-Sandbox/docs/captures/client-relaunch.arg"
LOG="$INST/logs/fml-client-latest.log"
JAVA=/usr/lib/jvm/default-runtime/bin/java
TIMEOUT="${2:-600}"

if pgrep -f "client-relaunch.arg" >/dev/null; then
  echo "ERREUR: une instance du pack tourne déjà (pgrep client-relaunch.arg). Abandon." >&2
  exit 1
fi

JFR_ARGS=()
if [ "${1:-}" = "--jfr" ]; then
  JFR_ARGS=(-XX:StartFlightRecording=maxsize=400m,settings=profile)
  echo "JFR actif (chunks dans le repo /tmp/<date>_<pid> — analyser les chunks FERMÉS individuellement)"
fi

cd "$INST" || exit 1
"$JAVA" "${JFR_ARGS[@]}" "@$ARGFILE" >/dev/null 2>&1 &
PID=$!
echo "pack lancé (pid $PID), attente LoadComplete (max ${TIMEOUT}s)..."

START=$(date +%s)
while kill -0 $PID 2>/dev/null; do
  if grep -q "Bar Finished: LoadComplete" "$LOG" 2>/dev/null; then
    sleep 10  # laisser LoadComplete se finir proprement (saves de configs etc.)
    echo "=== LoadComplete atteint en $(( $(date +%s) - START ))s de wall clock ==="
    python3 "$(dirname "$0")/boot_profiler.py" "$LOG" --top 10
    break
  fi
  if [ $(( $(date +%s) - START )) -gt "$TIMEOUT" ]; then
    echo "TIMEOUT (${TIMEOUT}s) — le boot n'a pas fini" >&2
    break
  fi
  sleep 10
done

# Terminaison SYSTÉMATIQUE — jamais de zombie au menu.
kill $PID 2>/dev/null
for i in $(seq 1 12); do kill -0 $PID 2>/dev/null || { echo "jeu terminé proprement"; exit 0; }; sleep 5; done
kill -9 $PID 2>/dev/null
echo "jeu terminé (SIGKILL après grâce de 60s)"
