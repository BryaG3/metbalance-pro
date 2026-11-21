"""Global Error Detection (chi-square), adjustment covariance, and z-score diagnostics.

This module belongs to the headless core layer and MUST NEVER import PySide6.
"""

from __future__ import annotations

import math
from typing import Optional

import numpy as np
import scipy.linalg
import scipy.stats

from .models import (
    CircuitSpec,
    GEDReport,
    GEDVerdict,
    MeasurementPoint,
    ObservabilityReport,
    UncertaintyModel,
    UncertaintyScale,
    VariableAdjustment,
    VariableClass,
)
from .observability import (
    analyze_observability,
    build_bilinear_jacobian,
    build_variable_catalog,
    mask_excluded_measurements,
)
from .topology import build_incidence_matrix

DEFAULT_FALLBACK_REL_PCT = 5.0


def resolve_variable_sigma(
    var_key: str,
    meas_native: float,
    fraction_factor: float,
    uncertainty_model: UncertaintyModel,
) -> tuple[float, float]:
    """Resolve (sigma_native, sigma_internal_fraction) for a measured variable.

    Guarantees strictly positive finite standard deviations even when a variable
    lacks an explicit entry in `uncertainty_model.specs`.
    """
    spec = uncertainty_model.specs.get(var_key)
    meas_internal = meas_native * fraction_factor

    if spec is None:
        sigma_internal = max(abs(meas_internal) * (DEFAULT_FALLBACK_REL_PCT / 100.0), 1e-12)
        if abs(meas_internal) == 0.0:
            sigma_internal = max(1e-6 * fraction_factor, 1e-12)
        sigma_native = sigma_internal / fraction_factor
        return float(sigma_native), float(sigma_internal)

    if spec.scale == UncertaintyScale.RELATIVE_PCT:
        if abs(meas_internal) > 0.0:
            sigma_internal = max(abs(meas_internal) * (float(spec.value) / 100.0), 1e-12)
        else:
            sigma_internal = max(1e-6 * fraction_factor * (float(spec.value) / 100.0), 1e-12)
        sigma_native = sigma_internal / fraction_factor
        return float(sigma_native), float(sigma_internal)

    # UncertaintyScale.ABSOLUTE is provided in the variable's native unit
    sigma_native = max(float(spec.value), 1e-12)
    sigma_internal = max(sigma_native * fraction_factor, 1e-15)
    return float(sigma_native), float(sigma_internal)


def _svd_rank_tol(matrix: np.ndarray, singular_values: np.ndarray) -> float:
    if singular_values.size == 0:
        return 1e-9
    scale = max(matrix.shape) * np.finfo(np.float64).eps * float(singular_values[0])
    return max(1e-9, scale)


def compute_ged_and_zscores(
    circuit: CircuitSpec,
    point: MeasurementPoint,
    uncertainty_model: UncertaintyModel,
    obs_report: ObservabilityReport,
    rec_mass: dict[str, float],
    rec_assays_frac: dict[str, dict[str, float]],
    objective_j: float,
    excluded_outliers: Optional[list[str]] = None,
) -> tuple[GEDReport, list[VariableAdjustment]]:
    """Compute Global Error Detection chi-square test and standardized z-scores."""
    excluded_set = set(excluded_outliers or [])
    active_point = mask_excluded_measurements(point, excluded_outliers)

    # Ensure obs_report reflects excluded_outliers treated as unmeasured
    if excluded_set and any(
        st.is_measured and st.variable_key in excluded_set
        for st in obs_report.variable_statuses
    ):
        obs_active = analyze_observability(circuit, active_point)
    else:
        obs_active = obs_report

    n_u = len(circuit.nodes)
    n_s = len(circuit.streams)
    n_c = len(circuit.species)
    incidence_a, _, _ = build_incidence_matrix(circuit)
    catalog = build_variable_catalog(circuit)
    species_map = {sp.id: sp for sp in circuit.species}
    status_by_key = {st.variable_key: st for st in obs_active.variable_statuses}

    rec_m_vec = np.array(
        [float(rec_mass[stream.id]) for stream in circuit.streams],
        dtype=np.float64,
    )
    rec_x_frac = np.zeros((n_s, n_c), dtype=np.float64)
    for j, stream in enumerate(circuit.streams):
        stream_rec_assays = rec_assays_frac.get(stream.id, {})
        for i, sp in enumerate(circuit.species):
            rec_x_frac[j, i] = float(stream_rec_assays.get(sp.id, 0.0))

    m_ref = max(float(np.max(np.abs(rec_m_vec))), 1.0)
    x_ref = np.maximum(np.mean(np.maximum(rec_x_frac, 0.0), axis=0), 1e-9)

    m_tilde = rec_m_vec / m_ref
    x_tilde = rec_x_frac / x_ref[np.newaxis, :]
    b_tilde = build_bilinear_jacobian(incidence_a, m_tilde, x_tilde)

    meas_entries: list[dict[str, float | int | str | Optional[str]]] = []
    unmeas_idx: list[int] = []

    for global_idx, (var_key, stream_id, species_id) in enumerate(catalog):
        if species_id is None:
            raw_val = active_point.mass_flows.get(stream_id)
            factor = 1.0
            j_idx = circuit.streams.index(
                next(s for s in circuit.streams if s.id == stream_id)
            )
            rec_internal = float(rec_m_vec[j_idx])
            scale_d = m_ref
        else:
            raw_val = active_point.assays.get(stream_id, {}).get(species_id)
            sp = species_map[species_id]
            factor = sp.to_fraction_factor()
            j_idx = next(idx for idx, s in enumerate(circuit.streams) if s.id == stream_id)
            i_idx = next(idx for idx, s in enumerate(circuit.species) if s.id == species_id)
            rec_internal = float(rec_x_frac[j_idx, i_idx])
            scale_d = float(x_ref[i_idx])

        is_meas = raw_val is not None and math.isfinite(float(raw_val))
        if is_meas:
            meas_native = float(raw_val)  # type: ignore[arg-type]
            meas_internal = meas_native * factor
            sigma_native, sigma_internal = resolve_variable_sigma(
                var_key, meas_native, factor, uncertainty_model
            )
            meas_entries.append(
                {
                    "global_idx": global_idx,
                    "var_key": var_key,
                    "meas_native": meas_native,
                    "rec_native": rec_internal / factor,
                    "sigma_native": sigma_native,
                    "meas_tilde": meas_internal / scale_d,
                    "rec_tilde": rec_internal / scale_d,
                    "sigma_tilde": sigma_internal / scale_d,
                }
            )
        else:
            unmeas_idx.append(global_idx)

    meas_idx = [int(item["global_idx"]) for item in meas_entries]
    b_m_tilde = b_tilde[:, meas_idx]
    b_u_tilde = b_tilde[:, unmeas_idx]
    m_eq = b_tilde.shape[0]

    if len(unmeas_idx) == 0:
        q2 = np.eye(m_eq, dtype=np.float64)
    else:
        u_u, s_u, _ = np.linalg.svd(b_u_tilde, full_matrices=True)
        tol_u = _svd_rank_tol(b_u_tilde, s_u)
        r_u = int(np.sum(s_u > tol_u))
        q2 = u_u[:, r_u:]

    dof = int(obs_active.redundancy_dof) if obs_active.can_reconcile else 0
    z_scores_by_key: dict[str, Optional[float]] = {}
    meas_info_by_key: dict[str, dict[str, float | int | str | Optional[str]]] = {
        str(item["var_key"]): item for item in meas_entries
    }

    if dof > 0 and q2.shape[1] > 0 and len(meas_entries) > 0:
        r_m_tilde = q2.T @ b_m_tilde
        _, s_r, vt_r = np.linalg.svd(r_m_tilde, full_matrices=False)
        tol_r = _svd_rank_tol(r_m_tilde, s_r)
        r_indep = int(np.sum(s_r > tol_r))

        if r_indep > 0:
            r_indep_mat = vt_r[:r_indep, :]
            sigma_tilde_vec = np.array(
                [float(item["sigma_tilde"]) for item in meas_entries],
                dtype=np.float64,
            )
            sigma_m_tilde = np.diag(sigma_tilde_vec ** 2)
            s_r_cov = r_indep_mat @ sigma_m_tilde @ r_indep_mat.T
            rhs = r_indep_mat @ sigma_m_tilde
            try:
                solved = np.linalg.solve(s_r_cov, rhs)
            except np.linalg.LinAlgError:
                solved = np.linalg.pinv(s_r_cov, rcond=1e-15) @ rhs
            v_a_tilde = rhs.T @ solved

            z_max = math.sqrt(max(float(objective_j), 0.0))
            for local_k, item in enumerate(meas_entries):
                var_key = str(item["var_key"])
                v_class = status_by_key[var_key].classification
                if v_class == VariableClass.MEASURED_REDUNDANT:
                    col_norm = float(np.linalg.norm(r_indep_mat[:, local_k]))
                    v_kk = float(v_a_tilde[local_k, local_k])
                    v_meas_kk = float(sigma_tilde_vec[local_k] ** 2)
                    if col_norm > 1e-10 and v_meas_kk > 0.0:
                        v_reg_kk = max(v_kk, 1e-6 * v_meas_kk)
                        diff_tilde = float(item["meas_tilde"]) - float(item["rec_tilde"])
                        z_val = float(diff_tilde / math.sqrt(v_reg_kk))
                        if abs(z_val) > z_max:
                            z_val = math.copysign(z_max, z_val) if z_max > 0.0 else 0.0
                        z_scores_by_key[var_key] = z_val
                    else:
                        z_scores_by_key[var_key] = 0.0
                else:
                    z_scores_by_key[var_key] = 0.0
        else:
            for item in meas_entries:
                z_scores_by_key[str(item["var_key"])] = 0.0
    else:
        for item in meas_entries:
            z_scores_by_key[str(item["var_key"])] = 0.0

    alpha = float(uncertainty_model.alpha)
    z_critical = float(scipy.stats.norm.ppf(1.0 - alpha / 2.0))

    if dof > 0:
        chi2_critical = float(scipy.stats.chi2.ppf(1.0 - alpha, dof))
        p_value = float(scipy.stats.chi2.sf(max(float(objective_j), 0.0), dof))
    else:
        chi2_critical = 0.0
        p_value = 1.0

    redundant_z: list[tuple[str, float]] = []
    for item in meas_entries:
        var_key = str(item["var_key"])
        v_class = status_by_key[var_key].classification
        z_val = z_scores_by_key.get(var_key)
        if v_class == VariableClass.MEASURED_REDUNDANT and z_val is not None:
            redundant_z.append((var_key, abs(z_val)))

    suspect_pairs = [
        (key, mag) for key, mag in redundant_z if dof > 0 and mag > z_critical
    ]
    suspect_pairs.sort(key=lambda pair: pair[1], reverse=True)
    suspect_variables = [key for key, _ in suspect_pairs]
    suspect_set = set(suspect_variables)

    if redundant_z and dof > 0:
        max_key, max_val = max(redundant_z, key=lambda pair: pair[1])
        max_abs_z_variable: Optional[str] = max_key
        max_abs_z_value = float(max_val)
    else:
        max_abs_z_variable = None
        max_abs_z_value = 0.0

    if dof == 0:
        verdict = GEDVerdict.WARNING
    elif float(objective_j) > chi2_critical or len(suspect_variables) > 0:
        verdict = GEDVerdict.FAIL
    elif float(objective_j) > 0.85 * chi2_critical:
        verdict = GEDVerdict.WARNING
    else:
        verdict = GEDVerdict.PASS

    ged_report = GEDReport(
        objective_j=float(objective_j),
        degrees_of_freedom=dof,
        alpha=alpha,
        chi2_critical=chi2_critical,
        p_value=p_value,
        z_critical=z_critical,
        verdict=verdict,
        suspect_variables=suspect_variables,
        max_abs_z_variable=max_abs_z_variable,
        max_abs_z_value=max_abs_z_value,
    )

    stream_idx_map = {s.id: idx for idx, s in enumerate(circuit.streams)}
    species_idx_map = {sp.id: idx for idx, sp in enumerate(circuit.species)}

    adjustments: list[VariableAdjustment] = []
    for var_key, stream_id, species_id in catalog:
        v_class = status_by_key[var_key].classification
        j_idx = stream_idx_map[stream_id]
        if species_id is None:
            unit_str = circuit.mass_unit.value
            rec_native = float(rec_m_vec[j_idx])
        else:
            sp = species_map[species_id]
            unit_str = sp.unit.value
            i_idx = species_idx_map[species_id]
            rec_native = float(rec_x_frac[j_idx, i_idx] / sp.to_fraction_factor())

        if var_key in meas_info_by_key:
            info = meas_info_by_key[var_key]
            meas_native = float(info["meas_native"])  # type: ignore[arg-type]
            sigma_native = float(info["sigma_native"])  # type: ignore[arg-type]
            abs_diff = rec_native - meas_native
            rel_pct = (
                (100.0 * abs_diff / meas_native)
                if abs(meas_native) > 1e-12
                else (0.0 if abs(abs_diff) <= 1e-12 else None)
            )
            z_val = z_scores_by_key[var_key]
            is_outlier = var_key in suspect_set
        else:
            meas_native = None
            sigma_native = None
            abs_diff = None
            rel_pct = None
            z_val = None
            is_outlier = False

        adjustments.append(
            VariableAdjustment(
                variable_key=var_key,
                stream_id=stream_id,
                species_id=species_id,
                unit=unit_str,
                measured=meas_native,
                reconciled=rec_native,
                sigma=sigma_native,
                absolute_diff=abs_diff,
                relative_pct=rel_pct,
                z_score=z_val,
                is_outlier=is_outlier,
                classification=v_class,
            )
        )

    return ged_report, adjustments
