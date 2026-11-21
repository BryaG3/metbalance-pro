"""Nonlinear Weighted Least Squares (WLS) metallurgical data reconciliation engine.

This module belongs to the headless core layer and MUST NEVER import PySide6.
"""

from __future__ import annotations

import math
from typing import Callable, Optional

import numpy as np
import scipy.optimize
import scipy.stats

from .models import (
    BatchReconciliationResult,
    CircuitSpec,
    GEDReport,
    GEDVerdict,
    MeasurementPoint,
    OutlierComparisonResult,
    ReconciliationResult,
    StreamRole,
    UncertaintyModel,
    make_assay_var_key,
    make_mass_var_key,
)
from .observability import (
    analyze_observability,
    build_bilinear_jacobian,
    mask_excluded_measurements,
)
from .statistics_ged import compute_ged_and_zscores, resolve_variable_sigma
from .topology import build_incidence_matrix


def _is_finite_meas(value: Optional[float]) -> bool:
    return value is not None and math.isfinite(float(value))


def _is_no_flow_period(circuit: CircuitSpec, point: MeasurementPoint) -> bool:
    """Return True when the period represents a non-operational / zero-flow shift."""
    feed_streams = [s for s in circuit.streams if s.role == StreamRole.FEED]
    if not feed_streams:
        feed_streams = [s for s in circuit.streams if s.from_node is None]

    measured_feed_flows: list[float] = []
    for s in feed_streams:
        raw = point.mass_flows.get(s.id)
        if _is_finite_meas(raw):
            val = float(raw)  # type: ignore[arg-type]
            if val <= 0.0:
                return True
            measured_feed_flows.append(val)

    if (
        feed_streams
        and len(measured_feed_flows) == len(feed_streams)
        and sum(measured_feed_flows) <= 0.0
    ):
        return True

    all_measured_flows: list[float] = []
    for s in circuit.streams:
        raw = point.mass_flows.get(s.id)
        if _is_finite_meas(raw):
            all_measured_flows.append(float(raw))  # type: ignore[arg-type]

    if all_measured_flows and all(v <= 0.0 for v in all_measured_flows):
        return True

    return False


def _compute_initial_guess(
    circuit: CircuitSpec,
    incidence_a: np.ndarray,
    meas_m: list[Optional[float]],
    sigma_m: list[Optional[float]],
    meas_x_frac: np.ndarray,
    sigma_x_frac: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, float, np.ndarray]:
    """Compute a nearly-feasible linear WLS initial guess (M_init, x_init, m_ref, x_ref)."""
    n_u, n_s = incidence_a.shape
    n_c = len(circuit.species)

    x_init = np.zeros((n_s, n_c), dtype=np.float64)
    x_ref = np.zeros(n_c, dtype=np.float64)

    for i in range(n_c):
        pos_vals = [
            float(meas_x_frac[j, i])
            for j in range(n_s)
            if not np.isnan(meas_x_frac[j, i]) and meas_x_frac[j, i] > 0.0
        ]
        mean_val = float(np.mean(pos_vals)) if pos_vals else 0.01
        x_ref[i] = max(mean_val, 1e-9)
        for j in range(n_s):
            val = meas_x_frac[j, i]
            if not np.isnan(val):
                x_init[j, i] = float(np.clip(val, 1e-10, 0.9999))
            else:
                x_init[j, i] = float(np.clip(mean_val, 1e-10, 0.9999))

    pos_m = [float(v) for v in meas_m if v is not None and v > 0.0]
    m_scale = max(pos_m) if pos_m else 100.0

    def _solve_m_given_x(x_cur: np.ndarray) -> np.ndarray:
        rows: list[np.ndarray] = []
        rhs: list[float] = []
        c_weight = 100.0

        rows.append(c_weight * incidence_a)
        rhs.extend([0.0] * n_u)
        for i in range(n_c):
            scaled_x = x_cur[:, i] / x_ref[i]
            rows.append(c_weight * (incidence_a * scaled_x[np.newaxis, :]))
            rhs.extend([0.0] * n_u)

        for j in range(n_s):
            if meas_m[j] is not None and sigma_m[j] is not None:
                row = np.zeros((1, n_s), dtype=np.float64)
                w = 1.0 / max(float(sigma_m[j]), 1e-6 * m_scale)
                row[0, j] = w
                rows.append(row)
                rhs.append(float(meas_m[j]) * w)

        h_mat = np.vstack(rows)
        h_rhs = np.array(rhs, dtype=np.float64)
        m_sol, _, _, _ = np.linalg.lstsq(h_mat, h_rhs, rcond=None)
        return np.maximum(m_sol, 1e-4 * m_scale)

    m_init = _solve_m_given_x(x_init)

    # Refine any unmeasured assays via linear component-mass balance A @ (M * x_i) = 0
    has_unmeasured_x = bool(np.any(np.isnan(meas_x_frac)))
    if has_unmeasured_x:
        for i in range(n_c):
            if not np.any(np.isnan(meas_x_frac[:, i])):
                continue
            rows_comp: list[np.ndarray] = [100.0 * incidence_a]
            rhs_comp: list[float] = [0.0] * n_u
            for j in range(n_s):
                if not np.isnan(meas_x_frac[j, i]):
                    row = np.zeros((1, n_s), dtype=np.float64)
                    sig_comp = max(float(m_init[j] * sigma_x_frac[j, i]), 1e-12 * m_scale * x_ref[i])
                    w = 1.0 / sig_comp
                    row[0, j] = w
                    rows_comp.append(row)
                    rhs_comp.append(float(m_init[j] * meas_x_frac[j, i]) * w)
            h_c = np.vstack(rows_comp)
            r_c = np.array(rhs_comp, dtype=np.float64)
            comp_sol, _, _, _ = np.linalg.lstsq(h_c, r_c, rcond=None)
            for j in range(n_s):
                if np.isnan(meas_x_frac[j, i]):
                    x_init[j, i] = float(np.clip(comp_sol[j] / m_init[j], 1e-10, 0.9999))
        m_init = _solve_m_given_x(x_init)

    m_ref = max(float(np.max(m_init)), m_scale, 1.0)
    return m_init, x_init, m_ref, x_ref


def reconcile_period(
    circuit: CircuitSpec,
    point: MeasurementPoint,
    uncertainty_model: UncertaintyModel,
    excluded_outliers: Optional[list[str]] = None,
) -> ReconciliationResult:
    """Reconcile a single MeasurementPoint using nonlinear WLS optimization."""
    excluded_list = list(excluded_outliers) if excluded_outliers else []
    active_point = mask_excluded_measurements(point, excluded_list)

    obs = analyze_observability(circuit, active_point)
    if not obs.can_reconcile:
        unobs_str = ", ".join(obs.unobservable_variables) or "underdetermined system"
        hints_str = " | ".join(obs.missing_measurements_hint)
        raise ValueError(
            f"Cannot reconcile period '{point.period_id}': system is unobservable or "
            f"underdetermined (DOF={obs.degrees_of_freedom}, unobservable=[{unobs_str}]). "
            f"{hints_str}"
        )

    incidence_a, _, _ = build_incidence_matrix(circuit)
    n_u, n_s = incidence_a.shape
    n_c = len(circuit.species)
    n_var = n_s * (1 + n_c)
    species_factors = [sp.to_fraction_factor() for sp in circuit.species]

    meas_m: list[Optional[float]] = [None] * n_s
    sigma_m: list[Optional[float]] = [None] * n_s
    meas_x_frac = np.full((n_s, n_c), np.nan, dtype=np.float64)
    sigma_x_frac = np.full((n_s, n_c), np.nan, dtype=np.float64)

    for j, stream in enumerate(circuit.streams):
        raw_m = active_point.mass_flows.get(stream.id)
        if _is_finite_meas(raw_m):
            m_val = float(raw_m)  # type: ignore[arg-type]
            meas_m[j] = m_val
            _, sig_m = resolve_variable_sigma(
                make_mass_var_key(stream.id), m_val, 1.0, uncertainty_model
            )
            sigma_m[j] = sig_m

        stream_assays = active_point.assays.get(stream.id, {})
        for i, sp in enumerate(circuit.species):
            raw_x = stream_assays.get(sp.id)
            if _is_finite_meas(raw_x):
                x_native = float(raw_x)  # type: ignore[arg-type]
                factor = species_factors[i]
                meas_x_frac[j, i] = x_native * factor
                _, sig_x = resolve_variable_sigma(
                    make_assay_var_key(stream.id, sp.id),
                    x_native,
                    factor,
                    uncertainty_model,
                )
                sigma_x_frac[j, i] = sig_x

    m_init, x_init, m_ref, x_ref = _compute_initial_guess(
        circuit, incidence_a, meas_m, sigma_m, meas_x_frac, sigma_x_frac
    )

    # Scale every variable by its sigma (or characteristic scale if unmeasured)
    # so the WLS objective Hessian is strictly the identity matrix on measured variables.
    var_scale = np.zeros(n_var, dtype=np.float64)
    meas_mask = np.zeros(n_var, dtype=bool)
    y_meas_z = np.zeros(n_var, dtype=np.float64)

    for j in range(n_s):
        if meas_m[j] is not None and sigma_m[j] is not None:
            meas_mask[j] = True
            var_scale[j] = max(float(sigma_m[j]), 1e-12)
            y_meas_z[j] = float(meas_m[j]) / var_scale[j]
        else:
            var_scale[j] = max(0.03 * float(m_init[j]), 1e-3 * m_ref)

        for i in range(n_c):
            col_idx = n_s + j * n_c + i
            if not np.isnan(meas_x_frac[j, i]):
                meas_mask[col_idx] = True
                var_scale[col_idx] = max(float(sigma_x_frac[j, i]), 1e-12)
                y_meas_z[col_idx] = float(meas_x_frac[j, i]) / var_scale[col_idx]
            else:
                var_scale[col_idx] = max(0.05 * float(x_init[j, i]), 1e-6 * float(x_ref[i]))

    y_init_internal = np.concatenate([m_init, x_init.ravel()]).astype(np.float64)
    z0 = y_init_internal / var_scale

    # Strictly positive lower bound on mass flows prevents collapse to the trivial M=0 root
    lb = np.zeros(n_var, dtype=np.float64)
    for j in range(n_s):
        if z0[j] > 1e-8:
            lb[j] = min(1e-3 * float(z0[j]), 0.05 * float(z0[j]))
            if m_init[j] > 1e-6 * m_ref:
                lb[j] = max(lb[j], (1e-4 * m_ref) / var_scale[j])
    ub = np.full(n_var, np.inf, dtype=np.float64)
    for j in range(n_s):
        for i in range(n_c):
            col_idx = n_s + j * n_c + i
            ub[col_idx] = 1.0 / var_scale[col_idx]
    z0 = np.clip(z0, lb, ub)

    # Row-normalize constraint Jacobian at z0 so every constraint row has O(1) norm
    b_z0 = build_bilinear_jacobian(incidence_a, m_init, x_init) * var_scale[np.newaxis, :]
    row_norms = np.linalg.norm(b_z0, axis=1)
    c_scale = np.where(row_norms > 1e-12, row_norms, 1.0)

    def _obj_and_grad(z: np.ndarray) -> tuple[float, np.ndarray]:
        diff = np.where(meas_mask, z - y_meas_z, 0.0)
        val = 0.5 * float(np.sum(diff * diff))
        return val, diff

    def _obj_hess(z: np.ndarray) -> np.ndarray:
        return np.diag(meas_mask.astype(np.float64))

    def _constraints(z: np.ndarray) -> np.ndarray:
        y_vec = z * var_scale
        m_vec = y_vec[:n_s]
        x_mat = y_vec[n_s:].reshape((n_s, n_c))
        blocks = [incidence_a @ m_vec]
        for i in range(n_c):
            blocks.append(incidence_a @ (m_vec * x_mat[:, i]))
        return np.concatenate(blocks) / c_scale

    def _constraints_jac(z: np.ndarray) -> np.ndarray:
        y_vec = z * var_scale
        m_vec = y_vec[:n_s]
        x_mat = y_vec[n_s:].reshape((n_s, n_c))
        b_raw = build_bilinear_jacobian(incidence_a, m_vec, x_mat)
        return (b_raw * var_scale[np.newaxis, :]) / c_scale[:, np.newaxis]

    def _constraints_hess(z: np.ndarray, v: np.ndarray) -> np.ndarray:
        hess = np.zeros((n_var, n_var), dtype=np.float64)
        v_scaled = v / c_scale
        for i in range(n_c):
            v_block = v_scaled[(1 + i) * n_u : (2 + i) * n_u]
            at_v = incidence_a.T @ v_block
            for j in range(n_s):
                col = n_s + j * n_c + i
                entry = at_v[j] * var_scale[j] * var_scale[col]
                hess[j, col] = entry
                hess[col, j] = entry
        return hess

    def _eval_residuals(z: np.ndarray) -> tuple[float, float, float]:
        y_vec = z * var_scale
        rec_m = y_vec[:n_s]
        rec_x_f = y_vec[n_s:].reshape((n_s, n_c))
        mass_res = float(np.max(np.abs(incidence_a @ rec_m)))
        sp_frac_res = 0.0
        sp_native_res = 0.0
        for i in range(n_c):
            r_frac = float(np.max(np.abs(incidence_a @ (rec_m * rec_x_f[:, i]))))
            sp_frac_res = max(sp_frac_res, r_frac)
            sp_native_res = max(sp_native_res, r_frac / species_factors[i])
        return mass_res, sp_frac_res, sp_native_res

    def _true_objective(z: np.ndarray) -> float:
        diff = z[meas_mask] - y_meas_z[meas_mask]
        return float(np.sum(diff * diff))

    slsqp_bounds = [(float(lb[k]), None if np.isinf(ub[k]) else float(ub[k])) for k in range(n_var)]

    res_slsqp = scipy.optimize.minimize(
        fun=_obj_and_grad,
        x0=z0,
        method="SLSQP",
        jac=True,
        bounds=slsqp_bounds,
        constraints=[{"type": "eq", "fun": _constraints, "jac": _constraints_jac}],
        options={"ftol": 1e-12, "maxiter": 800},
    )

    z_best = np.clip(np.asarray(res_slsqp.x, dtype=np.float64), lb, ub)
    mass_res, sp_frac_res, sp_native_res = _eval_residuals(z_best)
    solver_used = "SLSQP"
    total_iterations = int(getattr(res_slsqp, "nit", 0))
    j_init = _true_objective(z0)
    j_slsqp = _true_objective(z_best)
    slsqp_suspect_jump = j_slsqp > max(500.0, 25.0 * max(j_init, 1.0))

    converged = bool(
        res_slsqp.success
        and not slsqp_suspect_jump
        and mass_res <= 1e-9
        and sp_frac_res <= 1e-9
        and sp_native_res <= 1e-6
    )

    if not converged:
        x_start = z_best if (np.all(np.isfinite(z_best)) and not slsqp_suspect_jump) else z0
        nl_constraint = scipy.optimize.NonlinearConstraint(
            _constraints, 0.0, 0.0, jac=_constraints_jac, hess=_constraints_hess
        )
        tc_bounds = scipy.optimize.Bounds(lb, ub)
        res_tc = scipy.optimize.minimize(
            fun=_obj_and_grad,
            x0=x_start,
            method="trust-constr",
            jac=True,
            hess=_obj_hess,
            bounds=tc_bounds,
            constraints=[nl_constraint],
            options={"gtol": 1e-11, "xtol": 1e-12, "barrier_tol": 1e-12, "maxiter": 1200},
        )
        z_tc = np.clip(np.asarray(res_tc.x, dtype=np.float64), lb, ub)
        tc_mass_res, tc_sp_res, tc_nat_res = _eval_residuals(z_tc)
        tc_ok = tc_mass_res <= 1e-8 and tc_sp_res <= 1e-8
        slsqp_ok = mass_res <= 1e-8 and sp_frac_res <= 1e-8

        use_tc = False
        if np.all(np.isfinite(z_tc)):
            if tc_ok and not slsqp_ok:
                use_tc = True
            elif tc_ok and slsqp_ok:
                if _true_objective(z_tc) <= _true_objective(z_best) + 1e-9 or (
                    tc_nat_res < sp_native_res
                ):
                    use_tc = True
            elif (tc_mass_res + tc_sp_res) < (mass_res + sp_frac_res):
                use_tc = True

        if use_tc:
            z_best = z_tc
            mass_res, sp_frac_res, sp_native_res = tc_mass_res, tc_sp_res, tc_nat_res
            solver_used = "trust-constr"
            total_iterations += int(getattr(res_tc, "nit", 0))
            converged = bool((res_tc.success or tc_ok) and tc_ok)
        else:
            converged = bool(slsqp_ok)

    y_best = z_best * var_scale
    rec_m_vec = np.maximum(y_best[:n_s], 0.0)
    rec_x_frac_mat = np.clip(
        y_best[n_s:].reshape((n_s, n_c)), 0.0, 1.0
    )

    rec_mass_dict: dict[str, float] = {}
    rec_assays_frac_dict: dict[str, dict[str, float]] = {}
    rec_assays_native_dict: dict[str, dict[str, float]] = {}

    for j, stream in enumerate(circuit.streams):
        rec_mass_dict[stream.id] = float(rec_m_vec[j])
        rec_assays_frac_dict[stream.id] = {}
        rec_assays_native_dict[stream.id] = {}
        for i, sp in enumerate(circuit.species):
            x_f = float(rec_x_frac_mat[j, i])
            rec_assays_frac_dict[stream.id][sp.id] = x_f
            rec_assays_native_dict[stream.id][sp.id] = float(x_f / species_factors[i])

    objective_j = _true_objective(z_best)

    ged_report, adjustments = compute_ged_and_zscores(
        circuit=circuit,
        point=active_point,
        uncertainty_model=uncertainty_model,
        obs_report=obs,
        rec_mass=rec_mass_dict,
        rec_assays_frac=rec_assays_frac_dict,
        objective_j=objective_j,
        excluded_outliers=excluded_list,
    )

    return ReconciliationResult(
        period_id=point.period_id,
        converged=converged,
        solver_used=solver_used,
        iterations=total_iterations,
        max_mass_residual=mass_res,
        max_species_residual=sp_frac_res,
        reconciled_mass_flows=rec_mass_dict,
        reconciled_assays=rec_assays_native_dict,
        adjustments=adjustments,
        ged=ged_report,
        excluded_outliers=excluded_list,
        status="OK",
    )


def reconcile_batch(
    circuit: CircuitSpec,
    periods: list[MeasurementPoint],
    uncertainty_model: UncertaintyModel,
    progress_callback: Optional[Callable[[int, int], None]] = None,
) -> BatchReconciliationResult:
    """Reconcile a sequence of measurement periods and aggregate GED statistics."""
    results: list[ReconciliationResult] = []
    total = len(periods)
    alpha = float(uncertainty_model.alpha)
    z_crit = float(scipy.stats.norm.ppf(1.0 - alpha / 2.0))

    for idx, period in enumerate(periods):
        if _is_no_flow_period(circuit, period):
            res = ReconciliationResult(
                period_id=period.period_id,
                converged=False,
                solver_used="SKIPPED_NO_FLOW",
                iterations=0,
                max_mass_residual=0.0,
                max_species_residual=0.0,
                reconciled_mass_flows={s.id: 0.0 for s in circuit.streams},
                reconciled_assays={
                    s.id: {sp.id: 0.0 for sp in circuit.species}
                    for s in circuit.streams
                },
                adjustments=[],
                ged=GEDReport(
                    objective_j=0.0,
                    degrees_of_freedom=0,
                    alpha=alpha,
                    chi2_critical=0.0,
                    p_value=1.0,
                    z_critical=z_crit,
                    verdict=GEDVerdict.SKIPPED,
                    suspect_variables=[],
                    max_abs_z_variable=None,
                    max_abs_z_value=0.0,
                ),
                status="SKIPPED_NO_FLOW",
            )
        else:
            res = reconcile_period(circuit, period, uncertainty_model)
        results.append(res)
        if progress_callback is not None:
            progress_callback(idx + 1, total)

    pass_count = sum(1 for r in results if r.ged.verdict == GEDVerdict.PASS)
    warning_count = sum(1 for r in results if r.ged.verdict == GEDVerdict.WARNING)
    fail_count = sum(1 for r in results if r.ged.verdict == GEDVerdict.FAIL)
    skipped_count = sum(
        1
        for r in results
        if r.status == "SKIPPED_NO_FLOW" or r.ged.verdict == GEDVerdict.SKIPPED
    )
    valid = [
        r
        for r in results
        if r.status != "SKIPPED_NO_FLOW" and r.ged.verdict != GEDVerdict.SKIPPED
    ]
    mean_chi2 = (
        float(np.mean([r.ged.objective_j for r in valid])) if valid else 0.0
    )

    return BatchReconciliationResult(
        results=results,
        summary_pass_count=pass_count,
        summary_warning_count=warning_count,
        summary_fail_count=fail_count,
        summary_skipped_count=skipped_count,
        mean_chi2=mean_chi2,
    )


def compare_with_without_outlier(
    circuit: CircuitSpec,
    point: MeasurementPoint,
    uncertainty_model: UncertaintyModel,
    outlier_keys: list[str],
) -> OutlierComparisonResult:
    """Compare reconciliation results with and without suspect outlier measurements."""
    with_outlier = reconcile_period(
        circuit=circuit,
        point=point,
        uncertainty_model=uncertainty_model,
        excluded_outliers=None,
    )
    without_outlier = reconcile_period(
        circuit=circuit,
        point=point,
        uncertainty_model=uncertainty_model,
        excluded_outliers=outlier_keys,
    )

    j_reduction = float(with_outlier.ged.objective_j - without_outlier.ged.objective_j)
    severity_rank = {
        GEDVerdict.FAIL: 2,
        GEDVerdict.WARNING: 1,
        GEDVerdict.PASS: 0,
        GEDVerdict.SKIPPED: 0,
    }
    verdict_improved = bool(
        severity_rank[without_outlier.ged.verdict]
        < severity_rank[with_outlier.ged.verdict]
    )

    return OutlierComparisonResult(
        with_outlier=with_outlier,
        without_outlier=without_outlier,
        excluded_variables=list(outlier_keys),
        j_reduction=j_reduction,
        verdict_improved=verdict_improved,
    )
