"""
Machine level configuration loader: ~/.alemsrc.

Single implementation (G28). core.storage.resolver and
scripts/tools/path_loader.py both call load_alemsrc(), so every entry point
sees the same ALEMS_DATA_ROOT and API keys whatever its sys.path is. It
lives in core because core must never import scripts (CH39-3).

AUTHOR: Deepak Panigrahy
"""
import os

ALEMSRC = os.path.expanduser("~/.alemsrc")


def load_alemsrc(path: str = ALEMSRC) -> bool:
    """
    Export the variables of ~/.alemsrc into os.environ.

    Only lines of the form "export KEY=VALUE" are read; comments and blank
    lines are skipped. setdefault keeps the shell environment authoritative:
    a variable already exported in the shell always wins over the file.

    Args:
        path: rc file to read (tests pass a temporary file).

    Returns:
        True if the file existed and was read, False if it is absent.
    """
    if not os.path.exists(path):
        return False
    with open(path) as fh:
        for raw in fh:
            line = raw.strip()
            if not line.startswith("export "):
                continue
            key, _, val = line[7:].partition("=")
            os.environ.setdefault(key.strip(), val.strip())
    return True
