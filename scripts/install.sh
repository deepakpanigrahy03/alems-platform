#!/usr/bin/env bash
# A-LEMS Unified Installer
# One script, all platforms. Platform-specific logic in scripts/platforms/.
#
# Usage:
#   bash scripts/install.sh
#
# Sequencing is critical. Do not reorder steps.
# schema_version must exist before detect_environment.py reads it.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

# ── Step 0: Detect platform ──────────────────────────────────────────
# Run a fast hardware probe to get platform_class from detect_hardware.py.
# This is the single source of truth for platform identity across the
# entire install system. All downstream steps key off platform_class,
# not OS/ARCH strings, so adding a new platform to detect_hardware.py
# automatically makes it work here without any install.sh changes.
OS="$(uname -s)"
ARCH="$(uname -m)"

echo "A-LEMS Installer"
echo "  OS/Arch:  ${OS} ${ARCH}"
echo "  Project:  ${PROJECT_ROOT}"
echo ""

# Pass 0: fast detection to get platform_class before venv/deps are set up.
# Uses --stdout so no file is written yet; full detection runs in Step 4.
echo "[0/12] Platform identification..."
PLATFORM=$(python3 scripts/detect_hardware.py --stdout 2>/dev/null \
    | python3 -c "import json,sys; print(json.load(sys.stdin).get('platform_class','unknown'))" \
    2>/dev/null || echo "unknown")

if [ "$PLATFORM" = "unknown" ]; then
    # Fallback: derive from OS/ARCH if detect_hardware.py not yet available
    case "${OS}_${ARCH}" in
        Linux_x86_64)
            if grep -q "AuthenticAMD" /proc/cpuinfo 2>/dev/null; then
                PLATFORM="amd_x86"
            elif grep -q "GenuineIntel" /proc/cpuinfo 2>/dev/null; then
                PLATFORM="intel_x86"
            else
                PLATFORM="linux_x86_unknown"
            fi
            ;;
        Linux_aarch64)  PLATFORM="linux_arm"     ;;
        Darwin_arm64)   PLATFORM="apple_silicon"  ;;
        Darwin_x86_64)  PLATFORM="intel_mac"      ;;
        *)              PLATFORM="linux_x86_unknown" ;;
    esac
    echo "  WARNING: detect_hardware.py unavailable, derived platform: ${PLATFORM}"
fi

PLATFORM_DIR="${SCRIPT_DIR}/platforms/${PLATFORM}"
if [ ! -d "$PLATFORM_DIR" ]; then
    echo "  WARNING: No platform dir for ${PLATFORM}, using linux_x86_unknown fallback"
    PLATFORM_DIR="${SCRIPT_DIR}/platforms/linux_x86_unknown"
fi

echo "  Platform: ${PLATFORM}"
echo "  Platform dir: ${PLATFORM_DIR}"
echo ""

# ── Step 1: Python venv ──────────────────────────────────────────────
echo "[1/12] Python virtual environment..."
if [ ! -d "venv" ]; then
    python3 -m venv venv
    echo "  Created venv/"
else
    echo "  venv/ already exists, reusing"
fi
# shellcheck disable=SC1091
source venv/bin/activate

# ── Step 2: System deps + Python packages (platform handles both) ────
echo "[2/12] System and Python dependencies..."
if [ -f "${PLATFORM_DIR}/provision.sh" ]; then
    bash "${PLATFORM_DIR}/provision.sh" deps
else
    echo "  No platform deps script, installing base requirements..."
    pip install --upgrade pip --quiet
    pip install -r requirements.txt --quiet
fi

# ── Step 3: Permissions ──────────────────────────────────────────────
echo "[3/12] Permissions..."
if [ -f "${PLATFORM_DIR}/provision.sh" ]; then
    bash "${PLATFORM_DIR}/provision.sh" permissions
else
    echo "  No platform permissions script, skipping"
fi

# kperf PMU helper (Darwin arm64) is handled inside fix_permissions.sh Mac branch.
# Removed from install.sh to avoid duplication.

# ── Step 4: Hardware detection pass 1 (baseline, pre-permissions) ────
echo "[4/12] Hardware detection (pass 1)..."
python3 scripts/detect_hardware.py
echo "  hw_config.json written (baseline)"

# ── Step 4b: Hardware detection pass 2 (post-permissions, merge) ─────
# Re-run after fix_permissions.sh so MSR devices, turbostat caps,
# and setcap rdmsr are in place. Merge preserves custom fields.
echo "[4b/12] Hardware detection (pass 2, post-permissions)..."
python3 scripts/detect_hardware.py
echo "  hw_config.json updated (post-permissions)"

# ── Step 4c: Hardware verification ───────────────────────────────────
# Confirms every path in hw_config.json is actually readable.
# Halts install if any required check fails.
echo "[4c/12] Hardware verification..."
python3 scripts/verify_hardware.py || {
    echo "  ❌ Hardware verification failed — fix issues above before continuing"
    exit 1
}

# ── Step 5: ~/.alemsrc setup ─────────────────────────────────────────
echo "[5/12] Data directory setup..."
ALEMSRC="$HOME/.alemsrc"
HOSTNAME_LOWER="$(hostname | tr '[:upper:]' '[:lower:]')"

if [ -f "$ALEMSRC" ] && grep -q "ALEMS_DATA_ROOT" "$ALEMSRC"; then
    echo "  ~/.alemsrc already configured"
    # shellcheck disable=SC1090
    source "$ALEMSRC"
else
    # Set platform-appropriate default data root
    # Darwin: /mnt is read-only — use home directory instead
    # Linux: /mnt/alems-data is standard (external mount or NFS)
    if [ "${OS}" = "Darwin" ]; then
        DEFAULT_DATA_ROOT="${HOME}/alems-data"
    else
        DEFAULT_DATA_ROOT="/mnt/alems-data"
    fi
    echo ""
    echo "  A-LEMS stores experiment data outside the repo."
    echo "  Default: ${DEFAULT_DATA_ROOT}"
    echo "  (Press Enter to accept, or type a different path)"
    echo ""
    read -rp "  Data root [${DEFAULT_DATA_ROOT}]: " DATA_ROOT
    DATA_ROOT="${DATA_ROOT:-${DEFAULT_DATA_ROOT}}"

    MACHINE_DIR="${DATA_ROOT}/${HOSTNAME_LOWER}"
    mkdir -p "$MACHINE_DIR"

    # Write or append to ~/.alemsrc
    if [ ! -f "$ALEMSRC" ]; then
        echo "# A-LEMS environment (sourced by path_loader.py)" > "$ALEMSRC"
    fi
    echo "export ALEMS_DATA_ROOT=${DATA_ROOT}" >> "$ALEMSRC"
    export ALEMS_DATA_ROOT="${DATA_ROOT}"
    echo "  ~/.alemsrc written: ALEMS_DATA_ROOT=${DATA_ROOT}"
    echo "  Machine data dir:   ${MACHINE_DIR}/"
fi

# Resolve actual DB path via path_loader
DB_PATH=$(python3 -c "from scripts.tools.path_loader import get_alems_db_path; print(get_alems_db_path())")
DB_DIR="$(dirname "$DB_PATH")"
mkdir -p "$DB_DIR"
echo "  DB path: ${DB_PATH}"

# ── Step 6: Database init ────────────────────────────────────────────
echo "[6/12] Database initialization..."
python3 -c "
from core.database.sqlite_adapter import SQLiteAdapter
db = SQLiteAdapter({'path': '${DB_PATH}'})
db.create_tables()
print('  Tables created')
"

# ── Step 7: Universal seed data ──────────────────────────────────────
echo "[7/12] Universal seed data..."
SEED_DIR="migrations/seed"
if [ -d "$SEED_DIR" ]; then
    for f in "$SEED_DIR"/s*.sql; do
        [ -f "$f" ] || continue
        sqlite3 "$DB_PATH" < "$f"
        echo "  Applied $(basename "$f")"
    done
else
    echo "  WARNING: ${SEED_DIR}/ not found, skipping seed data"
fi

# ── Step 8: Schema migrations ────────────────────────────────────────
echo "[8/12] Schema migrations..."
python3 scripts/tools/alems_migrate.py
echo "  Migrations applied"

# ── Step 9: Platform-specific seed data ──────────────────────────────
echo "[9/12] Platform seed data..."
PLATFORM_SEED="migrations/platform/${PLATFORM}"
if [ -d "$PLATFORM_SEED" ]; then
    for f in "$PLATFORM_SEED"/*.sql; do
        [ -f "$f" ] || continue
        sqlite3 "$DB_PATH" < "$f"
        echo "  Applied $(basename "$f")"
    done
else
    echo "  No platform-specific seed data for ${PLATFORM}"
fi

# ── Step 10: Environment detection ───────────────────────────────────
echo "[10/12] Environment detection..."
python3 scripts/detect_environment.py
echo "  Environment detected"

# ── Step 11: Methodology seeding ─────────────────────────────────────
echo "[11/12] Methodology seeding..."
python3 scripts/seed_methodology.py
echo "  Methodology registry populated"

# ── Step 12: Model/API setup ─────────────────────────────────────────
echo "[12/12] Model and API setup..."
if [ -f "${PLATFORM_DIR}/provision.sh" ]; then
    bash "${PLATFORM_DIR}/provision.sh" models
else
    echo "  No platform model setup, skipping"
fi

# ── Verification ─────────────────────────────────────────────────────
echo ""
echo "Running verification..."
if [ -f "${PLATFORM_DIR}/verify.sh" ]; then
    bash "${PLATFORM_DIR}/verify.sh" "$DB_PATH"
fi

echo ""
echo "A-LEMS installation complete."
echo "  Platform: ${PLATFORM}"
echo "  Database: ${DB_PATH}"
echo ""
echo "Next steps:"
echo "  source venv/bin/activate"
echo "  python -m core.execution.tests.test_llm_setup --provider all --verbose"
echo "  python -m core.execution.tests.test_harness --task-id gsm8k_basic --repetitions 1 --save-db"
