"""
agreement_validator.py - Cross-source agreement checks (GB10 / arm_spbm).

Why: the DAG checks test only parent >= sum(children). They passed while
rail-derived domains in energy_sample_domains held mW instead of uJ
(bug B1, fixed Nov 4, 2026). These checks compare independent sources:

  agree_rail  stored rail-domain energy vs integral of power_rail_samples
  agree_pkg   package energy counter vs soc_pkg rail integral
  plaus_wall  dc_input / package ratio inside a plausible band
  top_pair    sys_total vs dc_input (known conflict; reported, never FAIL)

Emits facts and status only (OK / WARN / FAIL / N/A). Never raises.
"""
import logging

logger = logging.getLogger(__name__)

RAIL_DOMAINS = ('DC_INPUT', 'SOC_PKG', 'CPU_GPU', 'VCORE', 'PREREG', 'DLA')
MIN_SAMPLES = 20          # about 2 s at 10 Hz; shorter runs are N/A
AGREE_TOL = 0.05          # stored vs rail integral, relative
PKG_WARN_TOL = 0.10       # package counter vs soc_pkg rail, relative
WALL_BAND = (1.05, 2.5)   # dc_input / package
UNIT_ERR_RATIO = 0.05     # stored/integral below this: power stored as energy


def _stored_uj(conn, run_id, name):
    row = conn.execute(
        "SELECT SUM(e.energy_uj), COUNT(*) FROM energy_sample_domains e "
        "JOIN energy_domains d ON d.domain_id = e.domain_id "
        "WHERE e.run_id = ? AND d.name = ?", (run_id, name)).fetchone()
    return (row[0], row[1]) if row else (None, 0)


def _rail_uj(conn, run_id, rail_name):
    """Rectangular integral of a rail (same rule as the sampler): uJ, n."""
    rows = conn.execute(
        "SELECT s.timestamp_ns, s.power_mw FROM power_rail_samples s "
        "JOIN power_rails r ON r.rail_id = s.rail_id "
        "WHERE s.run_id = ? AND r.rail_name = ? AND s.power_mw IS NOT NULL "
        "ORDER BY s.timestamp_ns", (run_id, rail_name)).fetchall()
    n = len(rows)
    if n < 2:
        return None, n
    dts = [t2 - t1 for (t1, _), (t2, _) in zip(rows, rows[1:])]
    dts.append(sorted(dts)[len(dts) // 2])        # last sample: median interval
    e = sum(p * dt for (_, p), dt in zip(rows, dts)) / 1e6   # mW*ns/1e6 = uJ
    return e, n


def _agree_rail(conn, run_id):
    details, worst, unit_err = {}, 0.0, False
    for name in RAIL_DOMAINS:
        stored, n_st = _stored_uj(conn, run_id, name)
        rail, n_r = _rail_uj(conn, run_id, name.lower())
        if stored is None or rail is None or rail <= 0 or n_r < MIN_SAMPLES:
            continue
        ratio = stored / rail
        details[name] = {'stored_j': round(stored / 1e6, 4),
                         'rail_j': round(rail / 1e6, 4),
                         'ratio': round(ratio, 4)}
        worst = max(worst, abs(1 - ratio))
        if ratio < UNIT_ERR_RATIO:
            unit_err = True
    if not details:
        return {'status': 'N/A', 'reason': 'no rail domains or run too short'}
    out = {'details': details, 'worst_rel_gap': round(worst, 4)}
    if unit_err:
        out.update(status='FAIL',
                   reason='stored rail energy far below rail integral: '
                          'likely power (mW) stored as energy (uJ)')
    elif worst > AGREE_TOL:
        out.update(status='FAIL', reason='stored vs rail integral beyond tolerance')
    else:
        out['status'] = 'OK'
    return out


def _agree_pkg(conn, run_id):
    pkg, _ = _stored_uj(conn, run_id, 'PACKAGE')
    soc, n = _rail_uj(conn, run_id, 'soc_pkg')
    if not pkg or not soc or n < MIN_SAMPLES:
        return {'status': 'N/A', 'reason': 'missing data or run too short'}
    ratio = soc / pkg
    return {'status': 'OK' if abs(1 - ratio) <= PKG_WARN_TOL else 'WARN',
            'package_counter_j': round(pkg / 1e6, 4),
            'soc_pkg_rail_j': round(soc / 1e6, 4),
            'ratio_rail_to_counter': round(ratio, 4)}


def _plaus_wall(conn, run_id):
    pkg, _ = _stored_uj(conn, run_id, 'PACKAGE')
    wall, n = _rail_uj(conn, run_id, 'dc_input')
    if not pkg or not wall or n < MIN_SAMPLES:
        return {'status': 'N/A', 'reason': 'missing data or run too short'}
    ratio = wall / pkg
    lo, hi = WALL_BAND
    return {'status': 'OK' if lo <= ratio <= hi else 'FAIL',
            'ratio_wall_to_package': round(ratio, 4), 'band': [lo, hi]}


def _top_pair(conn, run_id):
    rows = conn.execute(
        "SELECT MAX(CASE r.rail_name WHEN 'sys_total' THEN s.power_mw END), "
        "       MAX(CASE r.rail_name WHEN 'dc_input'  THEN s.power_mw END) "
        "FROM power_rail_samples s JOIN power_rails r ON r.rail_id = s.rail_id "
        "WHERE s.run_id = ? GROUP BY s.timestamp_ns", (run_id,)).fetchall()
    pairs = [(a, b) for a, b in rows if a is not None and b is not None]
    if len(pairs) < MIN_SAMPLES:
        return {'status': 'N/A', 'reason': 'missing data or run too short'}
    above = sum(1 for a, b in pairs if a > b)
    gap_w = sum(a - b for a, b in pairs) / len(pairs) / 1000.0
    frac = above / len(pairs)
    return {'status': 'WARN' if frac > 0.5 else 'OK',
            'frac_sys_total_above_dc_input': round(frac, 4),
            'mean_gap_w': round(gap_w, 3), 'n': len(pairs),
            'note': 'child above parent; label or calibration conflict, not a code error'}


def validate_agreement(conn, run_id, platform):
    """Return {check_name: result}. Empty dict on non-GB10 platforms."""
    if platform != 'arm_spbm':
        return {}
    out = {}
    for key, fn in (('agree_rail', _agree_rail), ('agree_pkg', _agree_pkg),
                    ('plaus_wall', _plaus_wall), ('top_pair', _top_pair)):
        try:
            out[key] = fn(conn, run_id)
        except Exception as exc:   # never crash the validator
            logger.warning("agreement check %s failed: %s", key, exc)
            out[key] = {'status': 'N/A', 'reason': 'error: %s' % exc}
    return out
