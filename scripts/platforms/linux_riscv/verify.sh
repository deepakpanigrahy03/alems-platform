#!/usr/bin/env bash
# A-LEMS platform verification: RISC-V Linux
# Called by install.sh with DB_PATH as $1
set -euo pipefail

DB_PATH="${1:-data/experiments.db}"
PASS=0
FAIL=0

check() {
    local label="$1"; local expected="$2"; local actual="$3"
    if [ "$actual" = "$expected" ]; then
        echo "  OK  $label ($actual)"; PASS=$((PASS + 1))
    else
        echo "  FAIL $label (expected $expected, got $actual)"; FAIL=$((FAIL + 1))
    fi
}

echo "A-LEMS Verification: RISC-V Linux"
echo "  DB: ${DB_PATH}"
echo ""

echo "Seed data:"
check "energy_sources" "9" "$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM energy_sources;")"
check "energy_domains" "29" "$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM energy_domains;")"
check "retry_policy"   "3"  "$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM retry_policy;")"

echo ""
echo "Detection:"
HW_CONFIG=$(python3 -c "
import json, os
p = 'config/hw_config.json'
d = json.load(open(p)) if os.path.exists(p) else {}
print(d.get('cpu_architecture','MISSING'))
" 2>/dev/null)
check "hw_config.json cpu_architecture" "riscv64" "$HW_CONFIG"

echo ""
echo "Results: ${PASS} passed, ${FAIL} failed"
echo "NOTE: RISC-V runs in observation-only mode."
[ "$FAIL" -gt 0 ] && { echo "VERIFICATION FAILED"; exit 1; }
echo "VERIFICATION PASSED"
