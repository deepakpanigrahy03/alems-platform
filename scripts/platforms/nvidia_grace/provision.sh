#!/usr/bin/env bash
# A-LEMS platform provisioning: Linux ARM (GN100, NVIDIA Grace GB10)
# Called by install.sh with a subcommand: deps, permissions, models
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
SUBCOMMAND="${1:-all}"

case "$SUBCOMMAND" in
    deps)
        echo "  GN100 ARM: installing system dependencies..."
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

        echo "  GN100 ARM: checking CUDA/vLLM dependencies..."
        if python3 -c "import vllm" 2>/dev/null; then
            echo "  vLLM already installed"
        else
            echo "  NOTE: vLLM requires CUDA toolkit and GPU drivers."
            echo "  Install manually if needed: pip install vllm"
        fi
        ;;

    permissions)
        echo "  Setting up RAPL/hwmon permissions..."
        if [ -f "${PROJECT_ROOT}/scripts/fix_permissions.sh" ]; then
            sudo bash "${PROJECT_ROOT}/scripts/fix_permissions.sh"
            echo "  Permissions configured"
        else
            echo "  WARNING: fix_permissions.sh not found"
        fi
        ;;

    models)
        echo "  Model setup for GN100..."
        echo ""
        echo "  GN100 uses vllm_local as primary provider."
        echo "  Ensure a model is downloaded to the models/ directory."
        echo "  Cloud provider (nvidia_nim) needs API key in core/.env"
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
