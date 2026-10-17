#!/bin/bash
# RAF R5 one command refit on HPRC: export from snapshot, fit, write params.
# Usage: bash run_recovery_controller.sh <data_dir> <snapshot.db> <tag>
#   ledgers: every *ledger*.jsonl in <data_dir> except smoke ones.
set -euo pipefail
D=$1; DB=$2; TAG=$3
HERE=$(cd "$(dirname "$0")" && pwd)
LED=""
for f in "$D"/*ledger*.jsonl; do
  case "$f" in *smoke*) continue;; esac
  LED="$LED --ledger $f"
done
echo "ledgers:$LED"
python "$HERE/export_recovery_store.py" --db "$DB" $LED --out "$D/export_$TAG"
python "$HERE/fit_controller_params.py" --export "$D/export_$TAG" --out "$D/r5_$TAG" --tag "$TAG"
echo "params: $D/r5_$TAG/controller_params.json"
