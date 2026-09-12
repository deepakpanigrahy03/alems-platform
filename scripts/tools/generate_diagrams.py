#!/usr/bin/env python3
"""
A-LEMS Diagram Generator
--------------------------------
Generates SVG diagrams from YAML definitions and copies them to the
MkDocs source assets directory so they are available during mkdocs build.

Usage:
    python3 scripts/tools/generate_diagrams.py
    python3 scripts/tools/generate_diagrams.py --name platform-detection-flow
    python3 scripts/tools/generate_diagrams.py --output /custom/path
    python3 scripts/tools/generate_diagrams.py --no-copy
"""

import argparse
import shutil
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent))

from diagram_processor import (
    ComponentResolver,
    DiagramLoader,
    DiagramValidator,
    DotBuilder,
    SvgRenderer,
)
from path_loader import config


def get_project_root() -> Path:
    current = Path(__file__).resolve().parent
    while current != current.parent:
        if (current / ".git").exists():
            return current
        current = current.parent
    return Path.cwd()


def load_and_validate(config_dir: Path) -> dict:
    print("Loading diagram configurations...")
    loader = DiagramLoader(config_dir)
    data = loader.load_all()

    print("Validating...")
    validator = DiagramValidator(data["validation"])
    all_valid = True

    for instance in data["instances"]:
        name = instance.get("name", "unknown")
        valid = validator.validate_instance(instance, data["components"])
        if not valid:
            all_valid = False

    report = validator.get_report()
    if report["errors"]:
        print("\nValidation errors:")
        for error in report["errors"]:
            print(f"  {error}")

    if report["warnings"]:
        print("\nWarnings:")
        for warning in report["warnings"]:
            print(f"  {warning}")

    if not all_valid:
        print("\nFix validation errors and re-run.")
        sys.exit(1)

    print(f"  {len(data['instances'])} diagrams valid.")
    return data


def generate_diagram(instance: dict, data: dict, output_dir: Path) -> bool:
    name = instance.get("name", "unknown")
    template_name = instance.get("template", "layered")

    all_node_ids = []
    for node_def in instance.get("nodes", []):
        if isinstance(node_def, str):
            all_node_ids.append(node_def)
        elif isinstance(node_def, dict):
            node_id = node_def.get("id")
            if node_id:
                all_node_ids.append(node_id)

    resolver = ComponentResolver(
        data["components"],
        data["templates"],
        data["boundaries"],
    )

    resolved_nodes = []
    for node_def in instance.get("nodes", []):
        resolved_nodes.extend(resolver.resolve_node(node_def, all_node_ids))

    builder = DotBuilder(template_name, data["templates"], data["boundaries"])

    # Use instance-level boundary override if specified
    instance_boundaries = instance.get("boundaries")
    if instance_boundaries is not None:
        filtered = [b for b in data["boundaries"] if b.get("name") in instance_boundaries]
        builder.boundaries = filtered

    dot_string = builder.build(resolved_nodes, instance.get("edges", []))

    output_file = output_dir / f"{name}.svg"
    renderer = SvgRenderer()
    success = renderer.render(dot_string, output_file)

    if success:
        print(f"  {name}.svg")
    else:
        print(f"  FAILED: {name}")

    return success


def copy_to_mkdocs(output_dir: Path, mkdocs_assets: Path) -> None:
    """
    Copy generated SVGs to the MkDocs source assets directory.

    This makes diagrams available to mkdocs build without requiring
    generate_diagrams.py to know the MkDocs build output path.
    The source path (docs-src/mkdocs/source/assets/diagrams/) is what
    MkDocs reads; the generated path (docs/assets/diagrams/) is the
    intermediate store.
    """
    mkdocs_assets.mkdir(parents=True, exist_ok=True)
    copied = 0
    for svg in output_dir.glob("*.svg"):
        dest = mkdocs_assets / svg.name
        shutil.copy2(svg, dest)
        copied += 1
    print(f"  Copied {copied} SVGs to {mkdocs_assets}")


def main():
    parser = argparse.ArgumentParser(description="Generate A-LEMS diagrams")
    parser.add_argument("--name", help="Generate only this diagram (by name)")
    parser.add_argument("--output", help="Override output directory")
    parser.add_argument(
        "--no-copy",
        action="store_true",
        help="Skip copying SVGs to MkDocs source assets",
    )
    args = parser.parse_args()

    project_root = get_project_root()
    config_dir = project_root / "config" / "diagrams"

    output_dir = Path(args.output) if args.output else config.DIAGRAMS_OUTPUT
    output_dir.mkdir(parents=True, exist_ok=True)

    # MkDocs source assets path — where MkDocs reads from during build
    mkdocs_assets = project_root / "docs-src" / "mkdocs" / "source" / "assets" / "diagrams"

    print("=" * 50)
    print("A-LEMS Diagram Generator")
    print("=" * 50)
    print(f"Config:  {config_dir}")
    print(f"Output:  {output_dir}")
    if not args.no_copy:
        print(f"MkDocs:  {mkdocs_assets}")
    print()

    data = load_and_validate(config_dir)

    print("\nGenerating:")
    generated = 0
    failed = 0

    for instance in data["instances"]:
        name = instance.get("name", "unknown")
        if args.name and name != args.name:
            continue
        if generate_diagram(instance, data, output_dir):
            generated += 1
        else:
            failed += 1

    if not args.no_copy and generated > 0:
        print("\nCopying to MkDocs source:")
        copy_to_mkdocs(output_dir, mkdocs_assets)

    print()
    print("=" * 50)
    print(f"Generated: {generated}  Failed: {failed}")
    print("=" * 50)

    if failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
