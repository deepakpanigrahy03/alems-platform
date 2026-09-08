#!/usr/bin/env bash
# A-LEMS platform verification: Generic Linux x86_64 (VMs, unknown vendors)
# Called by install.sh with DB_PATH as $1
# RAPL and MSR checks are soft (warn only) because VMs may lack them.
set -euo pipefail

DB_PATH="${1:-data/experiments.db}"
PASS=0
FAIL=0
WARN=0

check() {
    local label="$1"
    local expected="$2"
    local actual="$3"
    if [ "$actual" = "$expected" ]; then
        echo "  OK  $label ($actual)"
        PASS=$((PASS + 1))
    else
        echo "  FAIL $label (expected $expected, got $actual)"
        FAIL=$((FAIL + 1))
    fi
}

check_warn() {
    # Like check but counts as warning not failure when mismatched
    local label="$1"
    local expected="$2"
    local actual="$3"
    if [ "$actual" = "$expected" ]; then
        echo "  OK  $label ($actual)"
        PASS=$((PASS + 1))
    else
        echo "  WARN $label (expected $expected, got $actual) — optional on this platform"
        WARN=$((WARN + 1))
    fi
}

echo "A-LEMS Verification: Generic Linux x86_64"
echo "  DB: ${DB_PATH}"
echo ""

# ── Seed data row counts ─────────────────────────────────────────────
echo "Seed data:"
check "energy_sources"           "9"   "$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM energy_sources;")"
check "energy_domains"           "29"  "$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM energy_domains;")"
check "retry_policy"             "3"   "$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM retry_policy;")"
check "outlier_detection_config" "11"  "$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM outlier_detection_config;")"
check "analysis_domain_config"   "10"  "$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM analysis_domain_config;")"
check "analysis_view_config"     "8"   "$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM analysis_view_config;")"
check "metric_analysis_domains"  "132" "$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM metric_analysis_domains;")"
check "power_limits"             "4"   "$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM power_limits;")"

echo ""

# ── Platform-specific (soft checks) ─────────────────────────────────
echo "Energy hardware (soft — VMs may lack these):"

RAPL_OK="false"
if ls /sys/class/powercap/intel-rapl*/energy_uj > /dev/null 2>&1; then
    RAPL_OK="true"
fi
check_warn "RAPL energy_uj present" "true" "$RAPL_OK"

MSR_OK="false"
[ -e "/dev/cpu/0/msr" ] && MSR_OK="true"
check_warn "MSR device present"    "true" "$MSR_OK"

echo ""

# ── Detection ────────────────────────────────────────────────────────
echo "Detection:"
HW_CONFIG=$(python3 -c "
import json, os
p = 'config/hw_config.json'
if os.path.exists(p):
    d = json.load(open(p))
    print(d.get('platform_class','MISSING'))
else:
    print('NO_FILE')
" 2>/dev/null)
# Accept any linux x86 platform_class including linux_x86_unknown
PCLASS_OK="false"
case "$HW_CONFIG" in
    intel_x86|amd_x86|linux_x86_unknown) PCLASS_OK="true" ;;
esac
check "hw_config.json platform_class is x86" "true" "$PCLASS_OK"

echo ""

# ── Methodology ──────────────────────────────────────────────────────
echo "Methodology:"
MMR_COUNT=$(sqlite3 "$DB_PATH" \
    "SELECT COUNT(*) FROM measurement_method_registry;" 2>/dev/null || echo "0")
check "measurement_method_registry > 0" "true" \
    "$([ "$MMR_COUNT" -gt 0 ] && echo true || echo false)"

echo ""

# ── Schema completeness ──────────────────────────────────────────────
echo "Schema completeness:"
TABLE_COUNT=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM sqlite_master WHERE type='table';")
check "table count >= 70" "true" "$([ "$TABLE_COUNT" -ge 70 ] && echo true || echo false)"

RK_EXISTS=$(sqlite3 "$DB_PATH" "PRAGMA table_info(energy_domains);" \
    | grep -c "reader_keys" || echo "0")
check "energy_domains.reader_keys column" "1" "$RK_EXISTS"

echo ""
echo "Results: ${PASS} passed, ${FAIL} failed, ${WARN} warnings"
if [ "$FAIL" -gt 0 ]; then
    echo "VERIFICATION FAILED"
    exit 1
fi
echo "VERIFICATION PASSED"
