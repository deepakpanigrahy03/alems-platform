#!/usr/bin/env bash
# A-LEMS platform provisioning: RISC-V Linux (riscv64)
# Stub — RISC-V support is detected by detect_hardware.py (RISCVLinuxDetector)
# but energy measurement capability is minimal (no RAPL, no MSR, no PMU standard).
# Called by install.sh with a subcommand: deps, permissions, models
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
SUBCOMMAND="${1:-all}"

case "$SUBCOMMAND" in
    deps)
        echo "  RISC-V Linux: installing system dependencies..."
        sudo apt install -y \
            $(grep -v '^#' "${PROJECT_ROOT}/system-requirements-linux-common.txt" | grep -v '^$' | tr '\n' ' ') \
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
            echo "${REQS_HASH}" > "${HASH_FILE}"
        fi
        ;;

    permissions)
        echo "  RISC-V: minimal permissions needed (no MSR, no RAPL)."
        # perf_event_paranoid still useful if perf is available
        if [ -f /etc/sysctl.d/99-a-lems.conf ]; then
            echo "  perf_event_paranoid already configured"
        else
            echo 'kernel.perf_event_paranoid = -1' \
                | sudo tee /etc/sysctl.d/99-a-lems.conf > /dev/null
            sudo sysctl -p /etc/sysctl.d/99-a-lems.conf > /dev/null
            echo "  perf_event_paranoid set to -1"
        fi
        ;;

    models)
        echo "  Model setup for RISC-V Linux..."
        echo "  Cloud inference recommended: set NVIDIA_NIM_API_KEY in core/.env"
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
