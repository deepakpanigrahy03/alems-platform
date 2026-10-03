#!/usr/bin/env bash
# A-LEMS system package resolution (G146).
#
# Sourced by scripts/install.sh and every scripts/platforms/*/provision.sh.
# Requirement files list generic tool names (perf, msr, python3-dev, ...).
# scripts/platforms/packages.map turns a generic name into the real package
# for this OS, so adding an OS is one line in one file, never a code change.
#
# Lookup order: /etc/os-release ID, then each ID_LIKE entry (Mint finds
# ubuntu, Raspberry Pi OS finds debian). No line found: the generic name is
# the package name. A map value of "-" means nothing to install on that OS.
# KERNEL inside a value expands to the running kernel (uname -r).
#
# Errors are never hidden: a failed install names the packages and returns 1.

# Map lives next to the platform folders; resolved from this file's location
# so callers in any working directory find it.
_ALEMS_PKG_MAP="$(cd "$(dirname "${BASH_SOURCE[0]}")/../platforms" && pwd)/packages.map"

pkg_detect() {
    # Computed once per shell; later calls return immediately.
    if [ -n "${ALEMS_PKG_MGR:-}" ]; then
        return 0
    fi
    # macOS has no os-release; brew names equal the generic names.
    if [ "$(uname -s)" = "Darwin" ]; then
        ALEMS_PKG_MGR="brew"
        ALEMS_PKG_INSTALL="brew install"
        ALEMS_OS_IDS="macos"
        return 0
    fi
    if command -v apt-get >/dev/null 2>&1; then
        ALEMS_PKG_MGR="apt"
        ALEMS_PKG_INSTALL="sudo apt install -y"
    elif command -v dnf >/dev/null 2>&1; then
        ALEMS_PKG_MGR="dnf"
        ALEMS_PKG_INSTALL="sudo dnf install -y"
    elif command -v pacman >/dev/null 2>&1; then
        ALEMS_PKG_MGR="pacman"
        ALEMS_PKG_INSTALL="sudo pacman -S --noconfirm"
    else
        ALEMS_PKG_MGR="unknown"
        ALEMS_PKG_INSTALL="<your-package-manager> install"
    fi
    # Subshell so os-release variables do not leak into the installer.
    ALEMS_OS_IDS=""
    if [ -r /etc/os-release ]; then
        ALEMS_OS_IDS="$(. /etc/os-release; echo "${ID:-} ${ID_LIKE:-}")"
    fi
}

pkg_name() {
    # Print the real package(s) for one generic name; empty for "-".
    local generic="$1" os value
    pkg_detect
    for os in ${ALEMS_OS_IDS}; do
        value="$(awk -v o="$os" -v g="$generic" \
            '$1 == o && $2 == g { $1 = ""; $2 = ""; sub(/^ +/, ""); print; exit }' \
            "${_ALEMS_PKG_MAP}" 2>/dev/null)"
        if [ -n "$value" ]; then
            # "-" marks a tool this OS does not package; install nothing.
            if [ "$value" = "-" ]; then
                echo ""
            else
                echo "${value//KERNEL/$(uname -r)}"
            fi
            return 0
        fi
    done
    echo "$generic"
}

pkg_install_names() {
    # Map generic names and install them; failure is reported, not hidden.
    local name pkgs=""
    pkg_detect
    for name in "$@"; do
        pkgs="$pkgs $(pkg_name "$name")"
    done
    # Collapse whitespace left by names that map to nothing.
    pkgs="$(echo $pkgs)"
    if [ -z "$pkgs" ]; then
        echo "  No system packages to install"
        return 0
    fi
    echo "  Installing (${ALEMS_PKG_MGR}): $pkgs"
    if ${ALEMS_PKG_INSTALL} $pkgs; then
        return 0
    fi
    # A stale apt index gives 404 on old package versions; refresh once, retry.
    if [ "${ALEMS_PKG_MGR}" = "apt" ]; then
        echo "  Install failed; refreshing package index and retrying once..."
        if sudo apt-get update -qq && ${ALEMS_PKG_INSTALL} $pkgs; then
            return 0
        fi
    fi
    echo "  ERROR: system package install failed: $pkgs"
    echo "  See the package manager error above. If a name is unknown for this OS"
    echo "  (ids: ${ALEMS_OS_IDS:-none}), add a line to scripts/platforms/packages.map"
    return 1
}

pkg_install_files() {
    # Read generic names (one per line, # comments) from files, then install.
    local f name names=""
    for f in "$@"; do
        [ -f "$f" ] || continue
        # "|| [ -n ... ]" keeps a last line that has no trailing newline.
        while read -r name _ || [ -n "${name:-}" ]; do
            case "$name" in
                ""|\#*) continue ;;
            esac
            names="$names $name"
        done < "$f"
    done
    pkg_install_names $names
}
