#!/usr/bin/env bash
# rename_research_docs.sh
# Renames all numbered research docs to descriptive names.
# Run from alems-platform repo root.
# Uses git mv to preserve history.

set -euo pipefail

R=docs-src/mkdocs/source/research

echo "Resolving duplicates..."

# 01-orchestration-tax.md and 03-orchestration-tax.md — keep 03, remove 01
git rm "$R/01-orchestration-tax.md"

# 08-thermal-subsystem.md (bare) and 08-thermal-subsystem-methodology.md — keep methodology
git rm "$R/08-thermal-subsystem.md"

# SPEC_SPBM_FULL_TELEMETRY.md — internal spec, move to internaldocs
mkdir -p ~/mydrive/alems-internaldocs
git mv "$R/SPEC_SPBM_FULL_TELEMETRY.md" \
    ~/mydrive/alems-internaldocs/SPEC_SPBM_FULL_TELEMETRY.md

echo "Renaming research docs..."

git mv "$R/01-measurement-methodology.md"              "$R/measurement-methodology.md"
git mv "$R/02-mathematical-derivations.md"             "$R/mathematical-derivations.md"
git mv "$R/03-orchestration-tax.md"                    "$R/orchestration-tax.md"
git mv "$R/04-publications.md"                         "$R/publications.md"
git mv "$R/05-llm-measurement-methodology.md"          "$R/llm-measurement.md"
git mv "$R/06-reader-methodology.md"                   "$R/reader-methodology.md"
git mv "$R/07-energy-readers-methodology.md"           "$R/energy-readers.md"
git mv "$R/08-system-measurement-methodology.md"       "$R/system-measurement.md"
git mv "$R/08-thermal-subsystem-methodology.md"        "$R/thermal-subsystem.md"
git mv "$R/09-derived-metrics-methodology.md"          "$R/derived-metrics.md"
git mv "$R/10-provenance-research-value.md"            "$R/provenance.md"
git mv "$R/11-phase-attribution-developer-guide.md"    "$R/phase-attribution.md"
git mv "$R/12-energy-attribution-methodology.md"       "$R/energy-attribution.md"
git mv "$R/13-normalization-factors-methodology.md"    "$R/normalization.md"
git mv "$R/14-measurement-boundary-methodology.md"     "$R/measurement-boundary.md"
git mv "$R/15-llm-wait-energy-finding.md"              "$R/llm-wait-energy.md"
git mv "$R/16-run-quality-methodology.md"              "$R/run-quality.md"
git mv "$R/17-experiment-classification-methodology.md" "$R/experiment-classification.md"
git mv "$R/18-goal-execution-methodology.md"           "$R/goal-execution.md"
git mv "$R/19-hallucination-output-quality-methodology.md" "$R/output-quality.md"
git mv "$R/20-tool-failure-methodology.md"             "$R/tool-failure.md"
git mv "$R/21-goal-tracking-runtime.md"                "$R/goal-tracking.md"
git mv "$R/22-retry-tool-failure-methodology.md"       "$R/retry-methodology.md"
git mv "$R/23-researcher-guide.md"                     "$R/researcher-guide.md"
git mv "$R/24-gpu-energy-methodology.md"               "$R/gpu-energy.md"
git mv "$R/24-tool-instrumentation-methodology.md"     "$R/tool-instrumentation.md"
git mv "$R/25-energy-attribution-guide.md"             "$R/energy-attribution-guide.md"
git mv "$R/26-network-wait-energy-methodology.md"      "$R/network-wait-energy.md"
git mv "$R/26-unified-energy-schema.md"                "$R/unified-energy-schema.md"
git mv "$R/27-llm-energy-sample-methodology.md"        "$R/llm-energy-sample.md"
git mv "$R/27-power-rail-schema.md"                    "$R/power-rail-schema.md"
git mv "$R/28-cpu-idle-states.md"                      "$R/cpu-idle-states.md"
git mv "$R/29-cooling-subsystem.md"                    "$R/cooling-subsystem.md"
git mv "$R/30-arm-cpu-samples.md"                      "$R/arm-pmu-metrics.md"
git mv "$R/31-network-energy-cross-platform-methodology.md" "$R/network-energy-cross-platform.md"
git mv "$R/32-outlier-detection-methodology.md"        "$R/outlier-detection.md"
git mv "$R/33-energy-chain-researcher-guide.md"        "$R/energy-chain-guide.md"

echo "Creating stub files for new docs..."
touch "$R/nic-observability.md"
touch "$R/normalization.md" 2>/dev/null || true

echo "Done. Run: python3 scripts/tools/validate_methodology_refs.py"
