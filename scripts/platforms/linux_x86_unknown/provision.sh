#!/usr/bin/env bash
# A-LEMS platform provisioning: Generic Linux x86_64
# Covers: VMs (KVM, VMware, Hyper-V), CloudLab, Hygon, VIA, unknown vendors.
# Called by install.sh with a subcommand: deps, permissions, models
#
# Energy stack: RAPL if available (may be absent in VMs), no MSR guarantee,
# no turbostat. Graceful degradation — A-LEMS runs in observation-only mode
# if no energy counters are accessible.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
SUBCOMMAND="${1:-all}"

case "$SUBCOMMAND" in
    deps)
        echo "  Generic x86: installing system dependencies..."
        sudo apt install -y \
            $(grep -v '^#' "${PROJECT_ROOT}/system-requirements-linux-common.txt" | grep -v '^$' | tr '\n' ' ') \
            $(grep -v '^#' "${SCRIPT_DIR}/system-requirements.txt" | grep -v '^$' | tr '\n' ' ') \
            2>/dev/null || true

        echo "  Installing Python dependencies..."
        REQS_HASH=$(cat "${PROJECT_ROOT}/requirements.txt" "${SCRIPT_DIR}/requirements.txt" 2>/dev/null \
            | md5sum | awk '{print $1}')
        HASH_FILE="${PROJECT_ROOT}/venv/.reqs_hash"
        if [ -f "${HASH_FILE}" ] && [ "$(cat "${HASH_FILE}")" = "${REQS_HASH}" ]; then
            echo "  Requirements unchanged, skipping pip install"
        else
            pip install --upgrade pip
            pip install -r "${PROJECT_ROOT}/requirements.txt"
            [ -s "${SCRIPT_DIR}/requirements.txt" ] && pip install -r "${SCRIPT_DIR}/requirements.txt"
            echo "${REQS_HASH}" > "${HASH_FILE}"
        fi
        ;;
    permissions)
        # fix_permissions.sh handles RAPL, MSR, perf_event, turbostat.
        # In VMs many of these will warn rather than fail — that is expected.
        echo "  Setting up permissions (best-effort on VM/unknown x86)..."
        if [ -f "${PROJECT_ROOT}/scripts/fix_permissions.sh" ]; then
            sudo bash "${PROJECT_ROOT}/scripts/fix_permissions.sh" || true
            echo "  Permissions attempted (some steps may warn in VM environments)"
        else
            echo "  WARNING: fix_permissions.sh not found"
        fi
        ;;

    models)
        echo "  Model setup for generic x86..."
        echo ""
        echo "  Cloud inference (nvidia_nim) is the safest option for VMs."
        echo "  Set API key in core/.env:"
        echo "    NVIDIA_NIM_API_KEY=your-key-here"
        echo ""
        echo "  For local inference, ensure sufficient RAM and optionally a GPU."
        echo "  Test with:"
        echo "    python -m core.execution.tests.test_llm_setup --provider all --verbose"
        ;;

    all)
        bash "$0" deps
        bash "$0" permissions
        bash "$0" models
        ;;

    *)
        echo "Usage: provision.sh {deps|permissions|models|all}"
        exit 1
        ;;
esac
