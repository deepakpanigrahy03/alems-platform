#!/usr/bin/env bash
# A-LEMS platform provisioning: AMD x86_64 (Ryzen + NVIDIA discrete GPU)
# Called by install.sh with a subcommand: deps, permissions, models
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
SUBCOMMAND="${1:-all}"

case "$SUBCOMMAND" in
    deps)
        echo "  AMD x86: installing system dependencies..."
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

        echo "  AMD x86: verifying pynvml..."
        python3 -c "import pynvml; pynvml.nvmlInit(); print('  pynvml OK')" || \
            echo "  ⚠️  pynvml unavailable — GPU energy will be unavailable"
        ;;

    permissions)
        echo "  Setting up RAPL permissions for AMD..."
        if [ -f "${PROJECT_ROOT}/scripts/fix_permissions.sh" ]; then
            sudo bash "${PROJECT_ROOT}/scripts/fix_permissions.sh"
            echo "  Permissions configured"
        else
            echo "  WARNING: fix_permissions.sh not found"
        fi

        # Verify RAPL readable after permissions fix
        if cat /sys/class/powercap/intel-rapl/intel-rapl:0/energy_uj > /dev/null 2>&1; then
            echo "  RAPL energy_uj readable: OK"
        else
            echo "  WARNING: RAPL energy_uj still not readable"
            echo "  Try: sudo chmod -R a+r /sys/class/powercap/intel-rapl/"
        fi
        ;;

    models)
        echo "  Model setup for AMD x86..."
        echo ""
        echo "  This machine has an RTX 2070 Super (8GB VRAM)."
        echo "  For local inference with GPU acceleration:"
        echo "    pip install llama-cpp-python  (CUDA build)"
        echo "    mkdir -p ~/models"
        echo "    # Download a GGUF model that fits in 8GB VRAM"
        echo ""
        echo "  For cloud inference (nvidia_nim):"
        echo "    Set API key in core/.env:"
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
