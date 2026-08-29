#!/usr/bin/env bash
# Thin wrapper: the real driver is loop.py (standard-library Python). All flags pass through.
set -euo pipefail
cd "$(dirname "$0")"
exec python3 loop.py "$@"
