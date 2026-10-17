#!/usr/bin/env python3
"""
make_e3_tasks_yaml.py: config/tasks.yaml entries for the 28 E3 held out tasks,
generated with the template that produced the 30 E1 entries in e0aefb1.

Proof first: the 30 E1 entries are regenerated and compared byte for byte with
the block e0aefb1 added. Any difference: exit 1, nothing is written for E3.

Template: the pilot entry bfcl_multi_turn_base_7 (same source select_main_tasks
uses), with id, name, answer, tool_calls, description stratum and tags replaced.
tool_calls: tool count from sweep run 1. Stratum: from e1_gn100.yaml / e3_gn100.yaml.
Data check: every id must have BFCL turns and ground truth in the plugin data.

Read only on the repo; writes only to --out (default /tmp).
"""
import argparse
import os
import re
import subprocess
import sys
from typing import Dict, List

import yaml

ENGINE = "/home/dpani/mydrive/alems-platform"
SB = "/home/dpani/sandboxes/raf-mlsys-2027"
LINE = re.compile(r"^(?:r\d+\s+)?(multi_turn_base_\d+)\s+B0\s+.*?llm=(\d+)\s+tool=(\d+)")


def sweep_tools(path: str) -> Dict[str, int]:
    """Tool call count per task from a sweep file (first B0 line per task wins)."""
    out = {}
    with open(path) as fh:
        for line in fh:
            m = LINE.match(line.strip())
            if m and m.group(1) not in out:
                out[m.group(1)] = int(m.group(3))
    return out


def strata(cfg_path: str) -> Dict[str, str]:
    """Stratum per task (ids without the bfcl_ prefix) from an E1/E3 config."""
    tasks = yaml.safe_load(open(cfg_path))["tasks"]
    return {k.replace("bfcl_", ""): v["stratum"] for k, v in tasks.items()}


def template(text: str) -> str:
    """The pilot entry for base 7, without its trailing newline."""
    m = re.search(r"(  - id: bfcl_multi_turn_base_7\n.*?)(?=\n  - id: |\n\S|\Z)", text, re.S)
    if not m:
        sys.exit("STOP: pilot template bfcl_multi_turn_base_7 not found")
    return m.group(1).rstrip("\n")


def entry(tpl: str, t: str, tools: int, stratum: str, study: str, tag: str) -> str:
    """One tasks.yaml entry; same replacements for E1 and E3, only study text and tag vary."""
    n = t.rsplit("_", 1)[1]
    out = tpl.replace("multi_turn_base_7", t).replace("base 7", "base " + n)
    out = out.replace("RAF LH 01 pilot (short)", "%s (%s)" % (study, stratum))
    out = re.sub(r"tool_calls: \d+", "tool_calls: %d" % tools, out)
    return out.replace('"raf_lh_01", "short"', '"%s", "%s"' % (tag, stratum))


def block(tpl, ids, tools, st, study, tag) -> str:
    """Entries in numeric task order (the order e0aefb1 used)."""
    ids = sorted(ids, key=lambda t: int(t.rsplit("_", 1)[1]))
    missing = [t for t in ids if t not in tools or t not in st]
    if missing:
        sys.exit("STOP: no sweep count or stratum for %s" % missing)
    return "\n".join(entry(tpl, t, tools[t], st[t], study, tag) for t in ids) + "\n"


def e0aefb1_block() -> str:
    """The lines e0aefb1 added to config/tasks.yaml."""
    d = subprocess.run(["git", "-C", ENGINE, "show", "e0aefb1", "--", "config/tasks.yaml"],
                       capture_output=True, text=True, check=True).stdout
    return "".join(l[1:] + "\n" for l in d.splitlines() if l.startswith("+") and not l.startswith("+++"))


def data_ok(ids: List[str]) -> List[str]:
    """Ids lacking BFCL turns or ground truth in the plugin data."""
    sys.path.insert(0, os.path.join(ENGINE, "alems-plugin-bfcl"))
    from alems_plugin_bfcl.tasks import ground_truth, load_entry
    bad = []
    for t in ids:
        try:
            if not load_entry(t) or not ground_truth(t):
                bad.append(t)
        except Exception as e:  # report, never hide
            bad.append("%s (%s)" % (t, e))
    return bad


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sweep", default=SB + "/raf_sweep30/sweep_r1.txt")
    ap.add_argument("--e1-sweep", default=None, help="sweep for the E1 check if different")
    ap.add_argument("--study", default="RAF E1 main study")
    ap.add_argument("--tag", default="raf_e1")
    ap.add_argument("--out", default="/tmp/e3_tasks_yaml_block.txt")
    a = ap.parse_args()

    tpl = template(subprocess.run(["git", "-C", ENGINE, "show", "e0aefb1:config/tasks.yaml"],
                                  capture_output=True, text=True, check=True).stdout)
    e1 = [l.strip().replace("bfcl_", "") for l in open(SB + "/raf_e1/e1_tasks.txt") if l.strip()]
    held = [l.strip().replace("bfcl_", "") for l in open(SB + "/raf_e1/e3_heldout_tasks.txt") if l.strip()]

    # 1. proof: regenerate E1 exactly as e0aefb1 added it; ids already present in its
    # parent (pilot tasks 154, 193) were not re-added, so they are skipped here too
    parent = subprocess.run(["git", "-C", ENGINE, "show", "e0aefb1^:config/tasks.yaml"],
                            capture_output=True, text=True, check=True).stdout
    e1 = [t for t in e1 if "id: bfcl_%s\n" % t not in parent]
    got = block(tpl, e1, sweep_tools(a.e1_sweep or a.sweep), strata(ENGINE + "/scripts/raf/e1_gn100.yaml"),
                "RAF E1 main study", "raf_e1")
    want = e0aefb1_block()
    if got != want:
        open("/tmp/e1_regen.txt", "w").write(got)
        open("/tmp/e1_e0aefb1.txt", "w").write(want)
        print("STOP: E1 regeneration differs; diff /tmp/e1_e0aefb1.txt /tmp/e1_regen.txt")
        return 1
    print("E1 check: %d entries byte identical to e0aefb1" % len(e1))

    # 2. data: every held out id has turns and ground truth
    bad = data_ok(held)
    if bad:
        print("STOP: no BFCL data or ground truth: %s" % bad)
        return 1
    print("data check: %d of %d held out tasks have turns and ground truth" % (len(held), len(held)))

    # 3. E3 block, never duplicating an id already in tasks.yaml
    cur = open(ENGINE + "/config/tasks.yaml").read()
    dup = [t for t in held if "id: bfcl_%s\n" % t in cur]
    if dup:
        print("already in tasks.yaml (resolve as is, not regenerated): %s" % dup)
    held = [t for t in held if t not in dup]
    out = block(tpl, held, sweep_tools(a.sweep), strata(ENGINE + "/scripts/raf/e3_gn100.yaml"), a.study, a.tag)
    open(a.out, "w").write(out)
    print("wrote %d entries to %s" % (out.count("  - id: "), a.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
