#!/usr/bin/env bash
# scripts/build-docs.sh
# =============================================================================
# A-LEMS Documentation Build Pipeline
#
# Usage:
#   bash scripts/build-docs.sh                  full build
#   bash scripts/build-docs.sh --validate-only  validate only, no build
#   bash scripts/build-docs.sh --deploy         build + deploy to GitHub Pages
#   bash scripts/build-docs.sh --strict         warnings treated as failures
#
# Every documentation change must pass this script before committing.
# Every committed change must be deployed with --deploy before closing.
# =============================================================================

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="$REPO_ROOT/venv"
MKDOCS_DIR="$REPO_ROOT/docs-src/mkdocs"

VALIDATE_ONLY=false
DEPLOY=false
STRICT=false

for arg in "$@"; do
    case $arg in
        --validate-only) VALIDATE_ONLY=true ;;
        --deploy)        DEPLOY=true ;;
        --strict)        STRICT=true ;;
    esac
done

# Activate venv
if [ -f "$VENV/bin/activate" ]; then
    source "$VENV/bin/activate"
else
    echo "ERROR: venv not found at $VENV"
    echo "Run: bash scripts/install.sh"
    exit 1
fi

cd "$REPO_ROOT"

echo "=================================================="
echo " A-LEMS Documentation Build"
echo "=================================================="
echo ""

# =============================================================================
# Step 1: Validate methodology references
# =============================================================================
echo "Step 1: Validating methodology references..."

STRICT_FLAG=""
if $STRICT; then
    STRICT_FLAG="--strict"
fi

python3 scripts/tools/validate_methodology_refs.py $STRICT_FLAG
echo ""

if $VALIDATE_ONLY; then
    echo "Validate-only mode. Done."
    exit 0
fi

# =============================================================================
# Step 2: Generate diagrams
# =============================================================================
echo "Step 2: Generating diagrams..."

if command -v dot &>/dev/null; then
    python3 scripts/tools/generate_diagrams.py
else
    echo "  WARNING: Graphviz 'dot' not found — skipping diagram generation."
    echo "  Install with: sudo apt install graphviz"
fi
echo ""

# =============================================================================
# Step 3: Build MkDocs
# =============================================================================
echo "Step 3: Building MkDocs site..."

cd "$MKDOCS_DIR"

WARNING_COUNT=$(mkdocs build 2>&1 | tee /dev/stderr | grep -c "WARNING" || true)

if [ "$WARNING_COUNT" -gt 0 ]; then
    echo ""
    echo "ERROR: $WARNING_COUNT warning(s) found in mkdocs build."
    echo "Fix all warnings before deploying."
    exit 1
fi

echo "  Build complete. 0 warnings."
echo ""

# =============================================================================
# Step 4: Deploy to GitHub Pages (optional)
# =============================================================================
if $DEPLOY; then
    echo "Step 4: Pushing source to main branch..."
    git push origin main
    echo ""
    echo "Step 5: Deploying to GitHub Pages..."
    mkdocs gh-deploy --force
    echo ""
    echo "  Deployed. Live in ~2 minutes at:"
    echo "  https://deepakpanigrahy03.github.io/alems-platform/"
fi

cd "$REPO_ROOT"

echo "=================================================="
echo " Build complete."
if $DEPLOY; then
    echo " Deployed to GitHub Pages."
else
    echo " Run with --deploy to publish to GitHub Pages."
fi
echo "=================================================="
