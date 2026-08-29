#!/usr/bin/env bash
# Thin wrapper: the real driver is loop.py (standard-library Python). All flags pass through.
# If the driver crashes (exit 3) it is restarted after 30 s, at most 3 times in a row — state is
# on disk and resumable, so a transient bug costs minutes instead of the rest of the night.
set -uo pipefail
cd "$(dirname "$0")"
attempts=0
while true; do
  python3 loop.py "$@"
  rc=$?
  if [ "$rc" -eq 3 ] && [ "$attempts" -lt 3 ]; then
    attempts=$((attempts + 1))
    echo "[loop.sh] driver crashed (exit 3); restarting in 30 s (attempt $attempts/3) — see logs/driver-crash.log"
    sleep 30
    continue
  fi
  exit "$rc"
done
