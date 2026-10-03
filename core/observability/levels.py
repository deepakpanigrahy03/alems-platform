"""
Modes, levels, component filters, and precedence (design 39.5.2 section 4).

Precedence lowest to highest follows design 7.12: machine config, sandbox
override, profile, environment, command line. Each layer is a small dict with
optional keys mode, level, components. Later layers override earlier ones key
by key; components merge with the later layer winning per component.
"""

import hashlib
import json
import logging
import os
from dataclasses import dataclass, field
from typing import Dict, Iterable, Mapping, Optional

# mode -> (console level, file level). Normal keeps console at WARNING so the
# visible stderr output equals Python's last resort handler today (plan A2).
MODES = {
    "quiet": (logging.ERROR, logging.INFO),
    "normal": (logging.WARNING, logging.INFO),
    "verbose": (logging.INFO, logging.DEBUG),
    "debug": (logging.DEBUG, logging.DEBUG),
}

# Design examples name components alems.*; real loggers are core.* because
# they use __name__. The alias lets both spellings work without renames.
_ALIAS_FROM = "alems"
_ALIAS_TO = "core"


@dataclass
class LogConfig:
    """Effective logging configuration after precedence resolution."""

    mode: str = "normal"
    console_level: int = logging.WARNING
    file_level: int = logging.INFO
    components: Dict[str, int] = field(default_factory=dict)

    def lowest_level(self) -> int:
        """Lowest level any handler or component needs (root threshold)."""
        levels = [self.console_level, self.file_level]
        levels.extend(self.components.values())
        return min(levels)


def parse_level(value: object) -> int:
    """
    Convert a level name or number to a logging level.

    Args:
        value: "DEBUG", "info", 10, and so on.

    Returns:
        Integer level.

    Raises:
        ValueError: unknown level name.
    """
    if isinstance(value, int):
        return value
    lvl = logging.getLevelName(str(value).strip().upper())
    if not isinstance(lvl, int):
        raise ValueError("unknown log level: %r" % value)
    return lvl


def normalize_component(name: str) -> str:
    """Map the alems alias prefix to the real core logger prefix."""
    name = name.strip()
    if name == _ALIAS_FROM or name.startswith(_ALIAS_FROM + "."):
        return _ALIAS_TO + name[len(_ALIAS_FROM):]
    return name


def parse_components(spec: object) -> Dict[str, int]:
    """
    Parse a component spec.

    Args:
        spec: "a=DEBUG,b=INFO" string or a mapping name -> level.

    Returns:
        Dict normalized logger prefix -> level.
    """
    if not spec:
        return {}
    if isinstance(spec, Mapping):
        items = list(spec.items())
    else:
        pairs = [p for p in str(spec).split(",") if p.strip()]
        items = [tuple(p.split("=", 1)) for p in pairs]
    out = {}
    for item in items:
        if len(item) != 2:
            raise ValueError("component spec needs name=LEVEL: %r" % (item,))
        out[normalize_component(item[0])] = parse_level(item[1])
    return out


def env_layer(environ: Optional[Mapping[str, str]] = None) -> Dict[str, object]:
    """
    Read the environment layer (ALEMS_LOG_LEVEL, ALEMS_LOG_COMPONENTS, ALEMS_LOG_MODE).

    Args:
        environ: Mapping to read; defaults to os.environ (injectable for tests).

    Returns:
        Layer dict with only the keys that are set.
    """
    env = os.environ if environ is None else environ
    layer = {}
    if env.get("ALEMS_LOG_MODE"):
        layer["mode"] = env["ALEMS_LOG_MODE"]
    if env.get("ALEMS_LOG_LEVEL"):
        layer["level"] = env["ALEMS_LOG_LEVEL"]
    if env.get("ALEMS_LOG_COMPONENTS"):
        layer["components"] = env["ALEMS_LOG_COMPONENTS"]
    return layer


def resolve_config(layers: Iterable[Optional[Mapping[str, object]]]) -> LogConfig:
    """
    Apply layers lowest first and return the effective configuration.

    Args:
        layers: Layer dicts in precedence order; None entries are skipped.

    Returns:
        LogConfig. A level key overrides the console level only; the file
        level comes from the mode unless the level is lower (more verbose).
    """
    mode, level, comps = "normal", None, {}
    for layer in layers:
        if not layer:
            continue
        # None means "not set in this layer", never "clear".
        if layer.get("mode") is not None:
            mode = str(layer["mode"])
        if layer.get("level") is not None:
            level = layer["level"]
        comps.update(parse_components(layer.get("components")))
    if mode not in MODES:
        raise ValueError("unknown log mode: %r" % mode)
    console, file_lvl = MODES[mode]
    if level is not None:
        console = parse_level(level)
        file_lvl = min(file_lvl, console)
    return LogConfig(mode, console, file_lvl, comps)


def config_hash(cfg: LogConfig) -> str:
    """
    SHA-256 of the canonical effective configuration.

    Used for measurement_log_config_hash in 2b; defined here so the hash has
    one definition.
    """
    payload = {
        "mode": cfg.mode,
        "console_level": cfg.console_level,
        "file_level": cfg.file_level,
        "components": dict(sorted(cfg.components.items())),
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


class ComponentFilter(logging.Filter):
    """
    Per handler threshold with per component overrides.

    The handler itself is set to the lowest level it could ever need; this
    filter then applies the longest matching component prefix, else the
    handler's base level.
    """

    def __init__(self, base_level: int, components: Mapping[str, int]):
        super().__init__()
        self.base_level = base_level
        # Longest prefix first so core.readers.rapl beats core.readers.
        self.rules = sorted(components.items(), key=lambda kv: -len(kv[0]))

    def threshold(self, name: str) -> int:
        """Effective threshold for a logger name."""
        for prefix, lvl in self.rules:
            if name == prefix or name.startswith(prefix + "."):
                return lvl
        return self.base_level

    def filter(self, record: logging.LogRecord) -> bool:
        """Keep the record if it meets its component threshold."""
        return record.levelno >= self.threshold(record.name)
