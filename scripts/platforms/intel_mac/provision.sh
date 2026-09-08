#!/usr/bin/env bash
# A-LEMS platform provisioning: Intel Mac (Darwin x86_64)
# Rare in the A-LEMS fleet. No Apple Silicon energy APIs available.
# Measurement capability: observation-only (no RAPL, no IOKit energy on Intel Mac).
# Called by install.sh with a subcommand: deps, permissions, models
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
SUBCOMMAND="${1:-all}"

case "$SUBCOMMAND" in
    deps)
        echo "  Intel Mac: installing system build dependencies..."
        if command -v brew &>/dev/null; then
            brew install libjpeg libxml2 libxslt freetype lcms2 webp 2>/dev/null || true
        else
            echo "  WARNING: Homebrew not found. Install from https://brew.sh"
            exit 1
        fi

        echo "  Installing Python dependencies..."
        pip install --upgrade pip --quiet
        pip install -r "${PROJECT_ROOT}/requirements.txt" --quiet
        ;;

    permissions)
        # powermetrics exists on Intel Mac but energy data is limited.
        # fix_permissions.sh Mac branch installs the sudoers rule anyway.
        echo "  Setting up powermetrics sudoers (best-effort on Intel Mac)..."
        if [ -f "${PROJECT_ROOT}/scripts/fix_permissions.sh" ]; then
            sudo bash "${PROJECT_ROOT}/scripts/fix_permissions.sh" || true
        else
            echo "  WARNING: fix_permissions.sh not found"
        fi
        ;;

    models)
        echo "  Model setup for Intel Mac..."
        echo ""
        echo "  Cloud inference (nvidia_nim) recommended — no local GPU acceleration."
        echo "  Set API key in core/.env: NVIDIA_NIM_API_KEY=your-key-here"
        echo ""
        echo "  Test with:"
        echo "    python -m core.execution.tests.test_llm_setup --provider cloud --verbose"
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
