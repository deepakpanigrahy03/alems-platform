# Contributing Documentation

A-LEMS documentation is maintained alongside the codebase. This guide
covers how to add, update, and validate documentation so that every
change stays consistent with the measurement platform it describes.

**Last updated:** 2026-09-12

---

## Before Writing Anything

Check whether a file already covers the topic:

```bash
grep -r "your-topic" docs-src/mkdocs/source/ --include="*.md" -l
```

Read the existing file fully before changing anything. Verify every
command, path, and value against the actual codebase. Documentation
that describes commands which do not work is worse than no documentation.

---

## Document Structure

Every `.md` file in `docs-src/mkdocs/source/` begins with a status block:

```markdown
---
**Status:** CURRENT | DRAFT | STALE
**Platforms verified:** <comma-separated platform_class list>
**Last updated:** YYYY-MM-DD
---
```

Prose rules that apply to every document:

No section titled "Overview", "Introduction", "Summary", or "Conclusion"
that restates what the document already covers.

No bullet points for narrative content. Bullets are for commands, file
listings, parameter tables, checklists, and enumerations.

No em dashes or en dashes in prose. No hedging phrases such as
"It should be noted" or "It is important to understand."

Every sentence gives the reader new information or is cut.

---

## Creating a New Document

```
□ Path: docs-src/mkdocs/source/<section>/<name>.md
□ Filename: descriptive, no numbers, no spaces, hyphens only
□ Status block present
□ Added to mkdocs.yml nav in the correct section
□ If research doc: all 7 sections from the methodology template below
□ If contains methodology sections: { #anchor } tag on each heading
□ config/methodology_docs.yaml updated for every methodology section
□ python3 scripts/tools/validate_methodology_refs.py passes clean
□ cd docs-src/mkdocs && mkdocs build --strict passes clean
```

---

## Editing an Existing Document

```
□ Read the full file before changing anything
□ Verify every command by checking the actual source file it describes
□ Do not change section headings that carry { #anchor } tags —
  the anchor is a permanent identifier stored in the database
□ If a heading must change: add the new anchor tag, update
  methodology_docs.yaml, keep the old method_anchor value unchanged
□ Run validate_methodology_refs.py after any heading change
□ Update Status and Last updated in the status block
```

---

## Renaming or Moving a Document

```bash
# Use git mv to preserve history
git mv docs-src/mkdocs/source/old-path/old-name.md \
       docs-src/mkdocs/source/new-path/new-name.md

# Update methodology_docs.yaml — the only place filenames appear as identifiers
# Change: doc: "old-name.md"  →  doc: "new-name.md"
# method_anchor values never change

# Update mkdocs.yml nav entry

# Validate
python3 scripts/tools/validate_methodology_refs.py
cd docs-src/mkdocs && mkdocs build --strict
```

---

## Adding a New Platform

When a new `platform_class` is added to A-LEMS:

```
□ Add row to reference/platform-matrix.md
□ Add row to reference/provider-registry.md if new serving engine
□ Update getting-started/installation.md prerequisites table
□ Update concepts/platform-detection.md detector class table
□ Add platform methodology sections to research/energy-readers.md
  with { #anchor } tags on each new section heading
□ Add entries to config/methodology_docs.yaml for each new method
□ Add entries to scripts/seed_methodology.py via methodology_loader
□ Run seed_methodology.py explicitly:
  python3 scripts/seed_methodology.py
□ Run validate_methodology_refs.py
□ Run mkdocs build --strict
```

---

## Methodology Section Anchors

Every section in a research document that documents a measurement method
carries an explicit anchor tag:

```markdown
## RAPL Package Energy { #rapl-pkg-energy-v1 }
```

The anchor is a permanent identifier. It is stored in the database
alongside every run record that used this method. Once an anchor is
assigned and the data is seeded to any machine's database, it must
not change. If a section heading text changes, the anchor tag stays.

Every anchor must be registered in `config/methodology_docs.yaml`:

```yaml
rapl_msr_pkg_energy:
  doc: "energy-readers.md"
  method_anchor: "rapl-pkg-energy-v1"
  section: "RAPL Package Energy"
```

The validator checks that every registered anchor exists as a proper
heading anchor in the referenced file:

```bash
python3 scripts/tools/validate_methodology_refs.py
```

---

## Diagrams

```
□ New diagram: create YAML instance in config/diagrams/instances/
  numbered sequentially after the last existing instance
□ Use components from config/diagrams/components.yaml where they exist
□ Use boundaries from config/diagrams/boundaries.yaml for clustering
□ Generate and verify:
  python3 scripts/tools/generate_diagrams.py --name <diagram-name>
□ Reference in the doc:
  ![Description](../assets/diagrams/<name>.svg)
□ generate_diagrams.py copies SVGs to the MkDocs assets path automatically
```

---

## What Belongs in Public Documentation

Public documentation in `docs-src/mkdocs/source/` covers:

- Architecture concepts and design decisions
- Installation and configuration for all supported platforms
- Developer how-to guides for extending the platform
- Research methodology with full provenance chains
- Platform coverage tables with verified status
- SQL queries tested against a real database

Documentation must not contain: internal development tracking references,
informal machine nicknames, personal names as platform identifiers, or
SQL that has not been tested against a real A-LEMS database.

Every platform reference uses the canonical `platform_class` identifier
from the platform matrix. Every hardware reference uses the canonical
form: "NVIDIA Grace GB10 (GN100, aarch64)" not informal shorthand.

---

## Build Verification

Run these before closing any documentation work:

```bash
# Step 1: Validate all methodology references
python3 scripts/tools/validate_methodology_refs.py

# Step 2: Full docs build — must show 0 warnings
cd docs-src/mkdocs && mkdocs build 2>&1 | grep -c "WARNING"

# Step 3: If diagrams changed, regenerate
python3 scripts/tools/generate_diagrams.py

# Step 4: Commit all changes
cd ~/mydrive/alems-platform
git add -A
git commit -m "docs: <brief description of what changed>"

# Step 5: Deploy to GitHub Pages
cd docs-src/mkdocs && mkdocs gh-deploy --force
```

Step 5 is mandatory after every documentation session. The public site
at `https://deepakpanigrahy03.github.io/alems-platform/` must always
reflect the current state of the repository. A committed doc that is
not deployed is invisible to researchers and developers using the platform.

`mkdocs gh-deploy` builds the site and pushes to the `gh-pages` branch.
It takes approximately 2-3 minutes for GitHub to make the update live.

---

## Methodology Document Template

Every new research methodology document follows this structure:

```markdown
---
**Method ID:** <method_id>
**Schema version:** <version when this method was introduced>
**Platforms verified:** <comma-separated platform_class list>
**Status:** DRAFT | REVIEW | PRODUCTION | DEPRECATED
**Last updated:** YYYY-MM-DD
---

# <Method Name>

<One paragraph: what this method measures, what hardware counter or OS
interface backs it, and why this approach was chosen. No restatement
of the title.>

## Overview { #<method-id>-overview }

<What the method captures, the physical or computational quantity being
measured, and why this measurement approach was chosen over alternatives.>

## Platform Coverage { #<method-id>-platform-coverage }

| Platform | Architecture | Source | Canonical Role | Confidence | Status |
|---|---|---|---|---|---|
| NVIDIA Grace GB10 | aarch64 | <source> | <role> | <score> | VERIFIED |

Status values: VERIFIED (tested on real hardware), PENDING (implemented,
not yet tested), PLANNED (designed, not implemented), NOT_SUPPORTED.

## Schema { #<method-id>-schema }

| Column | Table | Type | Semantics |
|---|---|---|---|
| <column_name> | <table_name> | <SQLite type> | <what it means> |

## Method Provenance { #<method-id>-provenance }

| Field | Value |
|---|---|
| method_id | <id> |
| layer | silicon / os / application / orchestration |
| provenance | MEASURED / CALCULATED / INFERRED |
| confidence | <score> |
| formula | <LaTeX> |
| justification | <why confidence is not 1.0, if applicable> |

## Query Reference { #<method-id>-queries }

Every query includes a plain-English description, the exact SQL tested
against a real database, expected output format, and which platforms
it applies to.

## Verification { #<method-id>-verification }

Step-by-step commands to confirm correct operation on real hardware.

## Known Limitations { #<method-id>-limitations }

Format:
- **<Limitation name>**: <what cannot be measured and why>.
  Workaround: <alternative, or "None — accept NULL">.
```
