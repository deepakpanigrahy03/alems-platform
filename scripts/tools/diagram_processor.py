#!/usr/bin/env python3
"""
Diagram Processor for A-LEMS
--------------------------------
Handles loading, validating, and processing diagram definitions from YAML
files. Each class has one clear responsibility.

Classes:
    DiagramLoader    — reads YAML configuration files
    DiagramValidator — validates diagram definitions
    ComponentResolver — resolves wildcards and merges component data
    DotBuilder       — produces Graphviz DOT format strings
    SvgRenderer      — renders DOT to SVG via Graphviz
"""

import re
import subprocess
import yaml
from pathlib import Path
from typing import Any, Dict, List, Optional


# ============================================================================
# LOADER
# ============================================================================

class DiagramLoader:
    """
    Loads all YAML files from the diagrams configuration directory.

    Reads:
        components.yaml  — reusable component definitions
        templates.yaml   — visual styling rules per template type
        boundaries.yaml  — system boundary (cluster) definitions
        validation.yaml  — validation rules
        instances/*.yaml — individual diagram definitions
    """

    def __init__(self, config_dir: Path):
        self.config_dir = config_dir
        self.components: Dict = {}
        self.templates: Dict = {}
        self.boundaries: List = []
        self.validation: Dict = {}
        self.instances: List = []

    def load_all(self) -> Dict[str, Any]:
        """Load all configuration files and return as a single dictionary."""
        components_file = self.config_dir / "components.yaml"
        if components_file.exists():
            with open(components_file) as f:
                data = yaml.safe_load(f) or {}
                self.components = data.get("components", {})

        templates_file = self.config_dir / "templates.yaml"
        with open(templates_file) as f:
            data = yaml.safe_load(f) or {}
            self.templates = data.get("templates", {})

        boundaries_file = self.config_dir / "boundaries.yaml"
        with open(boundaries_file) as f:
            data = yaml.safe_load(f) or {}
            self.boundaries = data.get("boundaries", [])

        validation_file = self.config_dir / "validation.yaml"
        with open(validation_file) as f:
            data = yaml.safe_load(f) or {}
            self.validation = data.get("validation", {})

        instances_dir = self.config_dir / "instances"
        for inst_file in sorted(instances_dir.glob("*.yaml")):
            with open(inst_file) as f:
                instance = yaml.safe_load(f)
                instance["_file"] = inst_file.name
                self.instances.append(instance)

        return {
            "components": self.components,
            "templates": self.templates,
            "boundaries": self.boundaries,
            "validation": self.validation,
            "instances": self.instances,
        }


# ============================================================================
# VALIDATOR
# ============================================================================

class DiagramValidator:
    """
    Validates diagram definitions against rules from validation.yaml.

    Checks:
        node ID format (namespace.name, lowercase)
        component existence for string-referenced nodes
        duplicate node IDs
        edge endpoint existence
        inline node layer presence
    """

    def __init__(self, validation_rules: Dict):
        self.rules = validation_rules
        self.strict = validation_rules.get("strict", True)
        self.errors: List[str] = []
        self.warnings: List[str] = []

    def validate_node_id(self, node_id: str) -> bool:
        pattern = self.rules.get("node_id_format", {}).get("pattern")
        if not pattern:
            return True
        return bool(re.compile(pattern).match(node_id))

    def validate_instance(self, instance: Dict, all_components: Dict) -> bool:
        name = instance.get("name", "unknown")
        node_ids: set = set()

        for node in instance.get("nodes", []):
            node_id = None

            if isinstance(node, str):
                node_id = node
                if node_id not in all_components:
                    self.errors.append(f"[{name}] Component not found: {node_id}")

            elif isinstance(node, dict):
                node_id = node.get("id")
                if not node_id:
                    self.errors.append(f"[{name}] Node missing 'id' field: {node}")
                    continue
                if not node.get("layer"):
                    self.errors.append(f"[{name}] Inline node missing layer: {node_id}")

            if node_id and not self.validate_node_id(node_id):
                self.errors.append(f"[{name}] Invalid node ID format: {node_id}")

            if node_id in node_ids:
                self.errors.append(f"[{name}] Duplicate node ID: {node_id}")
            node_ids.add(node_id)

        for edge in instance.get("edges", []):
            for endpoint in ("from", "to"):
                if edge.get(endpoint) not in node_ids:
                    self.errors.append(
                        f"[{name}] Edge {endpoint} not found: {edge.get(endpoint)}"
                    )

        return len(self.errors) == 0

    def get_report(self) -> Dict:
        return {
            "errors": self.errors,
            "warnings": self.warnings,
            "valid": len(self.errors) == 0,
        }


# ============================================================================
# RESOLVER
# ============================================================================

class ComponentResolver:
    """
    Resolves component references, expands wildcards, and merges node data.

    Wildcard expansion: 'hw.*' expands to all nodes with prefix 'hw.'
    Component reference: string node ID looks up components.yaml entry
    Inline node: dict with 'id' field is used as-is
    """

    def __init__(self, components: Dict, templates: Dict, boundaries: List):
        self.components = components
        self.templates = templates
        self.boundaries = boundaries

    def expand_wildcard(self, pattern: str, all_nodes: List[str]) -> List[str]:
        if pattern.endswith("*"):
            prefix = pattern[:-1]
            return [n for n in all_nodes if n.startswith(prefix)]
        return [pattern] if pattern in all_nodes else []

    def resolve_node(self, node_def: Any, all_nodes: List[str]) -> List[Dict]:
        results = []

        if isinstance(node_def, str):
            if node_def.endswith("*"):
                for node_id in self.expand_wildcard(node_def, all_nodes):
                    if node_id in self.components:
                        node_data = self.components[node_id].copy()
                        node_data["id"] = node_id
                        results.append(node_data)
            else:
                if node_def in self.components:
                    node_data = self.components[node_def].copy()
                    node_data["id"] = node_def
                    results.append(node_data)

        elif isinstance(node_def, dict):
            node_id = node_def.get("id")
            if node_id:
                node_data = node_def.copy()
                results.append(node_data)

        return results


# ============================================================================
# DOT BUILDER
# ============================================================================

class DotBuilder:
    """
    Builds Graphviz DOT format strings from resolved graph data.

    Responsibilities:
        graph header with template attributes
        node definitions with attributes
        edge definitions with styles from template
        cluster subgraphs for system boundaries

    Boundaries from boundaries.yaml are rendered as named clusters.
    Each boundary specifies which node namespaces it contains.
    """

    DOT_NODE_ATTRS = {
        "label", "shape", "color", "style", "fontname", "fontsize",
        "fillcolor", "width", "height",
    }

    def __init__(self, template_name: str, templates: Dict, boundaries: List):
        self.template = templates.get(template_name, {})
        self.boundaries = boundaries
        self.template_name = template_name

    def _quote(self, node_id: str) -> str:
        """Quote node IDs containing dots — Graphviz requires this."""
        return f'"{node_id}"' if "." in node_id else node_id

    def _escape(self, text: str) -> str:
        return text.replace('"', '\\"')

    def build_graph_header(self) -> str:
        lines = ["digraph {"]
        for key, value in self.template.get("graph", {}).items():
            if isinstance(value, str) and value.startswith("#"):
                lines.append(f'  {key}="{value}";')
            else:
                lines.append(f"  {key}={value};")

        node_defaults = self.template.get("node", {})
        if node_defaults:
            attrs = ", ".join(
                f'{k}="{v}"' if isinstance(v, str) else f"{k}={v}"
                for k, v in node_defaults.items()
            )
            lines.append(f"  node [{attrs}];")

        edge_defaults = self.template.get("edge", {})
        if edge_defaults:
            attrs = ", ".join(
                f'{k}="{v}"' if isinstance(v, str) else f"{k}={v}"
                for k, v in edge_defaults.items()
            )
            lines.append(f"  edge [{attrs}];")

        return "\n".join(lines)

    def build_node(self, node_data: Dict) -> str:
        node_id = node_data["id"]
        quoted_id = self._quote(node_id)

        attrs = []
        for key, value in node_data.items():
            if key not in self.DOT_NODE_ATTRS:
                continue
            if key == "label" and isinstance(value, str):
                if value.strip().startswith("<"):
                    attrs.append(f"label={value}")
                else:
                    attrs.append(f'label="{self._escape(value)}"')
            elif isinstance(value, str) and value.startswith("#"):
                attrs.append(f'{key}="{value}"')
            else:
                attrs.append(f'{key}="{value}"')

        return f"  {quoted_id} [{', '.join(attrs)}];" if attrs else f"  {quoted_id};"

    def build_edge(self, edge_data: Dict, edge_styles: Dict) -> str:
        from_node = self._quote(edge_data["from"])
        to_node = self._quote(edge_data["to"])
        edge_type = edge_data.get("type", "flow")
        style = edge_styles.get(edge_type, {})

        attrs = []
        for key, value in style.items():
            if isinstance(value, str) and value.startswith("#"):
                attrs.append(f'{key}="{value}"')
            else:
                attrs.append(f"{key}={value}")

        if "label" in edge_data:
            attrs.append(f'label="{self._escape(str(edge_data["label"]))}"')

        return (
            f"  {from_node} -> {to_node} [{', '.join(attrs)}];"
            if attrs
            else f"  {from_node} -> {to_node};"
        )

    def build_clusters(self, resolved_nodes: List[Dict]) -> List[str]:
        """
        Build Graphviz cluster subgraphs from boundaries.yaml definitions.

        Each boundary entry specifies:
            name   — cluster label shown in diagram
            nodes  — list of node namespace patterns (e.g. 'hw.*', 'exec.*')
            style  — optional visual overrides (color, style, fontsize)

        Nodes are matched by namespace prefix against resolved node IDs.
        A node not matched by any boundary renders outside all clusters.
        """
        node_ids = {n["id"] for n in resolved_nodes}
        lines = []

        for i, boundary in enumerate(self.boundaries):
            bname = boundary.get("name", f"boundary_{i}")
            bstyle = boundary.get("style", {})
            bnode_patterns = boundary.get("nodes", [])

            matched: List[str] = []
            for pattern in bnode_patterns:
                if pattern.endswith("*"):
                    prefix = pattern[:-1]
                    matched.extend(n for n in node_ids if n.startswith(prefix))
                elif pattern in node_ids:
                    matched.append(pattern)

            if not matched:
                continue

            cluster_key = bname.lower().replace(" ", "_").replace("/", "_")
            lines.append(f"  subgraph cluster_{cluster_key} {{")

            label = bstyle.get("label", bname)
            color = bstyle.get("color", "gray")
            style = bstyle.get("style", "dashed")
            fontsize = bstyle.get("fontsize", 11)

            lines.append(f'    label="{self._escape(label)}";')
            lines.append(f"    color={color};")
            lines.append(f"    style={style};")
            lines.append(f"    fontsize={fontsize};")
            lines.append(f'    fontname="Helvetica";')

            cluster_style = self.template.get("cluster", {})
            for key, value in cluster_style.items():
                if key not in ("label", "color", "style", "fontsize"):
                    lines.append(f"    {key}={value};")

            for node_id in matched:
                lines.append(f"    {self._quote(node_id)};")

            lines.append("  }")

        return lines

    def build(self, resolved_nodes: List[Dict], edges: List[Dict]) -> str:
        """
        Build the complete DOT string for a diagram.

        Order: header → clusters → nodes → edges → closing brace.
        Clusters must be declared before nodes for Graphviz to group correctly.
        """
        edge_styles = self.template.get("edge_styles", {})

        parts = [self.build_graph_header()]
        parts.extend(self.build_clusters(resolved_nodes))
        parts.extend(self.build_node(n) for n in resolved_nodes)
        parts.extend(self.build_edge(e, edge_styles) for e in edges)
        parts.append("}")

        return "\n".join(parts)


# ============================================================================
# SVG RENDERER
# ============================================================================

class SvgRenderer:
    """
    Renders DOT strings to SVG files using the Graphviz 'dot' command.

    The temporary DOT file is written alongside the output SVG and removed
    after rendering. On failure the DOT file is preserved for debugging.
    """

    def render(self, dot_string: str, output_path: Path) -> bool:
        temp_dot = output_path.with_suffix(".dot")
        try:
            temp_dot.write_text(dot_string)
            result = subprocess.run(
                ["dot", "-Tsvg", "-o", str(output_path), str(temp_dot)],
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                print(f"  Graphviz error: {result.stderr.strip()}")
                return False
            temp_dot.unlink(missing_ok=True)
            return True
        except FileNotFoundError:
            print("  Error: 'dot' command not found. Install Graphviz.")
            return False
        except Exception as exc:
            print(f"  Render error: {exc}")
            return False
