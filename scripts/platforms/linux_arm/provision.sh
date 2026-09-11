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
        echo "  Generic ARM: installing system build dependencies..."
        sudo apt install -y libjpeg-dev zlib1g-dev libfreetype-dev \
            liblcms2-dev libwebp-dev libxml2-dev libxslt1-dev \
            python3-dev build-essential sqlite3 2>/dev/null || true

        echo "  Installing Python dependencies..."
        pip install --upgrade pip
        pip install -r "${PROJECT_ROOT}/requirements.txt"
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
