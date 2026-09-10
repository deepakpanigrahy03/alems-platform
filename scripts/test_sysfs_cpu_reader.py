#!/usr/bin/env python3
"""
Unit test for SysfsCPUReader — no database, no experiment, no root.

Run:  python3 scripts/test_sysfs_cpu_reader.py
Pass: prints ALL UNIT TESTS PASSED and exits 0.

Covers: discovery, availability, 2 second live sampling, key contract
against cpu_samples columns, value range sanity, idempotent start,
stop without start, and PAC-4 never raise behavior under a broken
source path.
"""

import json
import sys
import time

sys.path.insert(0, ".")  # run from repo root

from core.readers.sysfs_cpu_reader import SysfsCPUReader  # noqa: E402

# Keys every sample must carry (schema aligned, always present)
REQUIRED_KEYS = {
    "timestamp_ns", "sample_start_ns", "sample_end_ns", "interval_ns",
    "cpu_util_percent", "cpu_busy_mhz", "cpu_avg_mhz",
    "package_power", "package_temp", "extra_metrics_json",
}

FAILURES = []


def check(name, condition, detail=""):
    """Record one assertion; keep going so a run reports ALL failures."""
    status = "PASS" if condition else "FAIL"
    print("  [%s] %s %s" % (status, name, detail))
    if not condition:
        FAILURES.append(name)


def main():
    print("== T1: construction and discovery ==")
    r = SysfsCPUReader({})
    check("is_available", r.is_available() is True,
          "(this test requires a Linux box with cpuidle)")
    check("get_name", r.get_name() == "cpu_sysfs_sampler")

    print("== T2: stop before start is safe (PAC-4) ==")
    out = r.stop_monitoring()
    check("stop_without_start", out == {"cpu_samples": []}, str(out)[:60])

    print("== T3: 2 second live sampling ==")
    r.start_monitoring()
    r.start_monitoring()  # idempotent second call must not break anything
    time.sleep(2.0)
    out = r.stop_monitoring()
    samples = out.get("cpu_samples", [])
    check("sample_count_10hz", 12 <= len(samples) <= 28,
          "got %d (expected ~20 at 10 Hz over 2 s)" % len(samples))

    if samples:
        s = samples[0]
        print("== T4: key contract ==")
        missing = REQUIRED_KEYS - set(s.keys())
        check("required_keys_present", not missing, "missing: %s" % missing)
        has_c_state = any(k.endswith("_residency") for k in s)
        check("at_least_one_c_state_column", has_c_state, str(sorted(s.keys())))

        print("== T5: value ranges ==")
        utils = [x["cpu_util_percent"] for x in samples
                 if x["cpu_util_percent"] is not None]
        check("util_in_0_100", utils and all(0 <= u <= 100 for u in utils),
              "min=%.1f max=%.1f" % (min(utils), max(utils)) if utils else "no values")
        ivals = [x["interval_ns"] for x in samples]
        check("interval_near_100ms",
              all(6e7 < i < 3e8 for i in ivals),
              "min=%dms max=%dms" % (min(ivals) / 1e6, max(ivals) / 1e6))
        powers = [x["package_power"] for x in samples
                  if x["package_power"] is not None]
        if powers:  # RAPL may be permission blocked; range only if present
            check("power_plausible_watts",
                  all(0 < p < 500 for p in powers),
                  "avg=%.1fW" % (sum(powers) / len(powers)))
        temps = [x["package_temp"] for x in samples
                 if x["package_temp"] is not None]
        if temps:
            check("temp_plausible_celsius",
                  all(10 < t < 110 for t in temps),
                  "avg=%.1fC" % (sum(temps) / len(temps)))

        print("== T6: extra_metrics_json is valid JSON with source tag ==")
        try:
            extra = json.loads(s["extra_metrics_json"])
            check("extra_json_source",
                  extra.get("measurement_source") == "cpuidle_sysfs",
                  str(extra)[:80])
        except (ValueError, KeyError) as e:
            check("extra_json_source", False, str(e))

        print("== T7: residency shares bounded by RESIDENCY_SCALE ==")
        from core.readers.sysfs_cpu_reader import RESIDENCY_SCALE
        shares = [v for x in samples for k, v in x.items()
                  if k.endswith("_residency") and v is not None]
        # 1.10 headroom: counter granularity can nudge a share past 1.0
        check("residency_bounded",
              all(0 <= v <= RESIDENCY_SCALE * 1.10 for v in shares),
              "max=%.3f scale=%s" % (max(shares), RESIDENCY_SCALE) if shares else "none")

    print("== T8: broken source never raises (PAC-4) ==")
    rb = SysfsCPUReader({})
    rb._freq_paths = ["/nonexistent/path"]  # sabotage one source
    rb._temp_path = "/nonexistent/path"
    rb.start_monitoring()
    time.sleep(0.5)
    out = rb.stop_monitoring()
    ok = all(x["cpu_busy_mhz"] is None for x in out["cpu_samples"])
    check("broken_source_yields_none_not_crash",
          ok and len(out["cpu_samples"]) >= 2)

    print()
    if FAILURES:
        print("FAILED: %s" % ", ".join(FAILURES))
        sys.exit(1)
    print("ALL UNIT TESTS PASSED (%d samples in T3)" % len(samples))
    sys.exit(0)


if __name__ == "__main__":
    main()
