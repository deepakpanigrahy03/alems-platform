"""
energy_rule_checks.py - Three kinds of energy chain checks (GB10 / arm_spbm).

Why: one "validation" label hid three different claims. They are kept apart:

  expected containment  a labelled child channel should not exceed its
                        labelled parent: cpu_gpu <= soc_pkg; gpu, cpu_p,
                        cpu_e <= cpu_gpu. Passing shows consistency with the
                        expected hierarchy, not an established physical
                        boundary.
  observed ordering     package <= dc_input. The dc_input boundary is not
                        documented, so this is an observed ordering only.
  additivity residual   a declared disjoint set against its parent, residual
                        reported: gpu + cpu_p + cpu_e vs cpu_gpu;
                        cpu_p + cpu_e + gpu + dla vs package.
                        VCORE and PREREG are never part of a sum.

sys_total vs dc_input is a known conflict, reported by agreement_validator
(top_pair) as WARN with its distribution, never FAIL.

Comparisons are per energy collector tick: all SPBM domains of one tick share
a sample_id, so parent and child cover the same interval. A window with
missing samples or shorter than 10 s gives N/A with the reason, never OK.
Emits facts and status only (OK / WARN / N/A). Never raises.
"""
import logging

logger = logging.getLogger(__name__)

MIN_WINDOW_S = 10.0           # checks need at least 10 s of complete ticks
MAX_MISSING_SHARE = 0.01      # more incomplete ticks than this share: N/A
CONTAIN_MAX_VIOLATION = 0.01  # violating tick share above this: WARN
ADDITIVE_TOL = 0.02           # children may exceed parent by 2 % before WARN
NEVER_SUMMED = ('VCORE', 'PREREG')   # not additive with CPU rails (log: vcore > cpu_p + cpu_e)

# (result key, parent domain, child domain)
CONTAINMENT = (('cpu_gpu_in_soc_pkg', 'SOC_PKG', 'CPU_GPU'),
               ('gpu_in_cpu_gpu', 'CPU_GPU', 'GPU'),
               ('cpu_p_in_cpu_gpu', 'CPU_GPU', 'CPU_P'),
               ('cpu_e_in_cpu_gpu', 'CPU_GPU', 'CPU_E'))
ORDERING = (('package_le_dc_input', 'DC_INPUT', 'PACKAGE'),)
# (result key, parent domain, disjoint children)
ADDITIVE = (('cpu_gpu_additivity', 'CPU_GPU', ('GPU', 'CPU_P', 'CPU_E')),
            ('package_additivity', 'PACKAGE', ('CPU_P', 'CPU_E', 'GPU', 'DLA')))


def _ticks(conn, run_id):
    """Per tick domain energies of one run: list of (interval_ns, {name: uJ})."""
    rows = conn.execute(
        "SELECT s.sample_id, s.interval_ns, ed.name, d.energy_uj "
        "FROM energy_samples_v2 s "
        "JOIN energy_sample_domains d ON d.sample_id = s.sample_id "
        "JOIN energy_domains ed ON ed.domain_id = d.domain_id "
        "WHERE s.run_id = ? ORDER BY s.sample_id", (run_id,)).fetchall()
    ticks = {}
    for sample_id, interval_ns, name, energy_uj in rows:
        # one entry per tick; every domain of the tick lands in the same dict
        ticks.setdefault(sample_id, (interval_ns, {}))[1][name] = energy_uj
    return list(ticks.values())


def _quantile(values, q):
    """Nearest rank quantile of a non empty list."""
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(q * len(ordered)))]


def _usable(ticks, names):
    """
    Complete ticks for the named domains.

    Returns:
        (ticks, None) when the window qualifies, else (None, reason).
    """
    if not ticks:
        return None, 'no samples'
    full = [(iv, e) for iv, e in ticks
            if iv and all(e.get(n) is not None for n in names)]
    missing = len(ticks) - len(full)
    if missing > MAX_MISSING_SHARE * len(ticks):
        return None, 'missing samples: %d of %d ticks incomplete' % (missing, len(ticks))
    span_s = sum(iv for iv, _ in full) / 1e9
    if span_s < MIN_WINDOW_S:
        return None, 'window %.1f s shorter than %.0f s' % (span_s, MIN_WINDOW_S)
    return full, None


def _pairwise(ticks, parent, child, kind):
    """
    Violating tick share and the distribution of parent minus child power.

    Power per tick in W = energy uJ / interval ns * 1e3. A tick violates
    when the child exceeds the parent. Never FAIL: WARN above the limit.
    """
    full, reason = _usable(ticks, (parent, child))
    if full is None:
        return {'status': 'N/A', 'kind': kind, 'reason': reason}
    diffs_w = [(e[parent] - e[child]) / iv * 1e3 for iv, e in full]
    share = sum(1 for d in diffs_w if d < 0) / float(len(diffs_w))
    return {'status': 'OK' if share <= CONTAIN_MAX_VIOLATION else 'WARN',
            'kind': kind, 'parent': parent, 'child': child, 'n_ticks': len(full),
            'violating_share': round(share, 5),
            'diff_w_p05': round(_quantile(diffs_w, 0.05), 3),
            'diff_w_p50': round(_quantile(diffs_w, 0.50), 3),
            'diff_w_p95': round(_quantile(diffs_w, 0.95), 3)}


def _additive(ticks, parent, children):
    """Residual of a declared disjoint set against its parent, over the window."""
    names = (parent,) + tuple(children)
    if any(n in NEVER_SUMMED for n in names):
        # guard against a future table edit: these channels are never summed
        return {'status': 'N/A', 'kind': 'additivity_residual',
                'reason': 'VCORE and PREREG are never part of a sum'}
    full, reason = _usable(ticks, names)
    if full is None:
        return {'status': 'N/A', 'kind': 'additivity_residual', 'reason': reason}
    p_uj = float(sum(e[parent] for _, e in full))
    c_uj = float(sum(sum(e[n] for n in children) for _, e in full))
    share = (p_uj - c_uj) / p_uj if p_uj > 0 else None
    ok = share is not None and share >= -ADDITIVE_TOL
    return {'status': 'OK' if ok else 'WARN', 'kind': 'additivity_residual',
            'parent': parent, 'children': ' + '.join(children), 'n_ticks': len(full),
            'parent_j': round(p_uj / 1e6, 4), 'children_j': round(c_uj / 1e6, 4),
            'residual_j': round((p_uj - c_uj) / 1e6, 4),
            'residual_share': round(share, 4) if share is not None else None}


def validate_rules(conn, run_id, platform):
    """Return {check_name: result}. Empty dict on non GB10 platforms."""
    if platform != 'arm_spbm':
        return {}
    try:
        ticks = _ticks(conn, run_id)
    except Exception as exc:   # never crash the validator
        logger.warning("rule checks: sample read failed for run %s: %s", run_id, exc)
        return {'rule_checks': {'status': 'N/A', 'reason': 'error: %s' % exc}}
    out = {}
    for key, parent, child in CONTAINMENT:
        out[key] = _pairwise(ticks, parent, child, 'expected_containment')
    for key, parent, child in ORDERING:
        out[key] = _pairwise(ticks, parent, child, 'observed_ordering')
    for key, parent, children in ADDITIVE:
        out[key] = _additive(ticks, parent, children)
    return out
