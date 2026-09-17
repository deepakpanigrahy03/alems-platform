#!/usr/bin/env bash
# A-LEMS platform provisioning: Generic ARM Linux (aarch64, non-Grace)
# Covers: AWS Graviton, Raspberry Pi, Oracle Cloud ARM, generic aarch64.
# Called by install.sh with a subcommand: deps, permissions, models
#
# Energy stack: cpuidle, ARM PMU via perf. No SPBM, no DCGM, no RAPL.
# Energy measurement capability depends on platform-specific hwmon drivers.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
SUBCOMMAND="${1:-all}"

case "$SUBCOMMAND" in
    deps)
        echo "  Generic ARM: installing system dependencies..."
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
        echo "  Setting up permissions for generic ARM..."
        if [ -f "${PROJECT_ROOT}/scripts/fix_permissions.sh" ]; then
            sudo bash "${PROJECT_ROOT}/scripts/fix_permissions.sh" || true
            echo "  Permissions attempted"
        else
            echo "  WARNING: fix_permissions.sh not found"
        fi
        ;;

    models)
        echo "  Model setup for generic ARM Linux..."
        echo ""
        echo "  Cloud inference (nvidia_nim) recommended."
        echo "  Set API key in core/.env:"
        echo "    NVIDIA_NIM_API_KEY=your-key-here"
        echo ""
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
