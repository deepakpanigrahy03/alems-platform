#!/bin/bash
# Registers the DCGM Python bindings in the active venv via a .pth file.
# Run on NVIDIA GB10 (or any DCGM host) after activating the venv. Idempotent.
#
# Why a search: DCGM 4 moved the bindings from /usr/local/dcgm/bindings/python3
# (DCGM 3) to /usr/share/datacenter-gpu-manager-4/bindings/python3. A fixed path
# broke GPU energy silently after the upgrade (B24). Override with
# ALEMS_DCGM_BINDINGS=/path/to/bindings/python3.

CANDIDATES=(
    "${ALEMS_DCGM_BINDINGS:-}"
    "/usr/share/datacenter-gpu-manager-4/bindings/python3"
    "/usr/local/dcgm/bindings/python3"
)

DCGM_PATH=""
for c in "${CANDIDATES[@]}"; do
    # Accept only a folder that actually holds pydcgm.py
    if [ -n "$c" ] && [ -f "$c/pydcgm.py" ]; then
        DCGM_PATH="$c"
        break
    fi
done

if [ -z "$DCGM_PATH" ]; then
    if command -v nv-hostengine >/dev/null 2>&1; then
        # DCGM is installed but its bindings were not found: this is an error
        echo "ERROR: DCGM is installed (nv-hostengine found) but pydcgm.py was not found in:" >&2
        printf '  %s\n' "${CANDIDATES[@]}" >&2
        exit 1
    fi
    echo "DCGM not installed on this machine — skipping"
    exit 0
fi

PTH_FILE="$(python3 -c 'import site; print(site.getsitepackages()[0])')/dcgm_bindings.pth"
echo "$DCGM_PATH" > "$PTH_FILE"
echo "DCGM bindings registered: $DCGM_PATH -> $PTH_FILE"
python3 -c "import pydcgm; print('pydcgm import OK')"
