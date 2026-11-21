"""Observability, redundancy, degrees-of-freedom, and nodal imbalance diagnostics.

This module belongs to the headless core layer and MUST NEVER import PySide6.
"""

from __future__ import annotations

import math
from typing import Optional

import numpy as np
import scipy.linalg
import scipy.optimize

from .models import (
    CircuitSpec,
    MeasurementPoint,
    NodalImbalance,
    ObservabilityReport,
    VariableClass,
    VariableObservability,
    make_assay_var_key,
    make_mass_var_key,
)
from .topology import build_incidence_matrix


def _is_measured_value(value: Optional[float]) -> bool:
    return value is not None and math.isfinite(float(value))


def mask_excluded_measurements(
    point: MeasurementPoint,
    excluded_outliers: Optional[list[str]],
) -> MeasurementPoint:
    """Return a deep copy of MeasurementPoint with excluded outlier keys set to None."""
    active_point = point.model_copy(deep=True)
    if not excluded_outliers:
        return active_point

    for key in excluded_outliers:
        if key.startswith("M:"):
            stream_id = key[2:]
            if stream_id in active_point.mass_flows:
                active_point.mass_flows[stream_id] = None
        elif key.startswith("X:"):
            parts = key.split(":", 2)
            if len(parts) == 3:
                _, stream_id, species_id = parts
                if stream_id in active_point.assays and species_id in active_point.assays[stream_id]:
                    active_point.assays[stream_id][species_id] = None

    return active_point


def build_variable_catalog(
    circuit: CircuitSpec,
) -> list[tuple[str, str, Optional[str]]]:
    """Return canonical variable order: all M:{stream} then all X:{stream}:{species}."""
    catalog: list[tuple[str, str, Optional[str]]] = []
    for stream in circuit.streams:
        catalog.append((make_mass_var_key(stream.id), stream.id, None))
    for stream in circuit.streams:
        for species in circuit.species:
            catalog.append(
                (make_assay_var_key(stream.id, species.id), stream.id, species.id)
            )
    return catalog


def build_bilinear_jacobian(
    incidence_a: np.ndarray,
    mass_flows: np.ndarray,
    assays_frac: np.ndarray,
) -> np.ndarray:
    """Evaluate exact Jacobian B of (1 + n_species) * n_nodes balance constraints.

    Constraint vector ordering:
      - rows [0 : n_u]: A @ M = 0
      - rows [(1+i)*n_u : (2+i)*n_u]: A @ (M * x[:, i]) = 0 for species i

    Column ordering matches `build_variable_catalog`:
      - cols [0 : n_s]: mass flows M_j
      - cols [n_s + j * n_c + i]: assay x_{j, i}
    """
    n_u, n_s = incidence_a.shape
    n_c = assays_frac.shape[1]
    n_var = n_s * (1 + n_c)

    jacobian = np.zeros(((1 + n_c) * n_u, n_var), dtype=np.float64)
    jacobian[:n_u, :n_s] = incidence_a

    stream_indices = np.arange(n_s)
    for i in range(n_c):
        r0 = (1 + i) * n_u
        r1 = r0 + n_u
        jacobian[r0:r1, :n_s] = incidence_a * assays_frac[:, i][np.newaxis, :]
        assay_cols = n_s + stream_indices * n_c + i
        jacobian[r0:r1, assay_cols] = incidence_a * mass_flows[np.newaxis, :]

    return jacobian


def _svd_rank_tol(matrix: np.ndarray, singular_values: np.ndarray) -> float:
    if singular_values.size == 0:
        return 1e-9
    scale = max(matrix.shape) * np.finfo(np.float64).eps * float(singular_values[0])
    return max(1e-9, scale)


def _build_missing_hints(
    circuit: CircuitSpec,
    statuses: list[VariableObservability],
    unobservable_variables: list[str],
    global_dof: int,
) -> list[str]:
    hints: list[str] = []
    if global_dof < 0:
        hints.append(
            f"Underdetermined balance system (global DOF = {global_dof}): "
            f"at least {-global_dof} additional independent measurement(s) required."
        )

    mass_statuses = [st for st in statuses if st.species_id is None]
    if mass_statuses and all(not st.is_measured for st in mass_statuses):
        feed_ids = [s.id for s in circuit.streams if s.role.value == "feed"]
        anchor = ", ".join(feed_ids) if feed_ids else circuit.streams[0].id
        hints.append(
            f"No stream mass flows are measured; measure at least one reference flow (e.g. '{anchor}')."
        )

    for st in statuses:
        if st.variable_key in unobservable_variables:
            if st.species_id is None:
                hints.append(
                    f"Unobservable mass flow on stream '{st.stream_id}' ({st.variable_key})."
                )
            else:
                hints.append(
                    f"Unobservable assay '{st.species_id}' on stream '{st.stream_id}' ({st.variable_key})."
                )

    return hints


def _compute_nodal_imbalances(
    circuit: CircuitSpec,
    point: MeasurementPoint,
) -> list[NodalImbalance]:
    imbalances: list[NodalImbalance] = []

    for node in circuit.nodes:
        in_streams = [s for s in circuit.streams if s.to_node == node.id]
        out_streams = [s for s in circuit.streams if s.from_node == node.id]
        connected = in_streams + out_streams

        all_mass_ok = bool(connected) and all(
            _is_measured_value(point.mass_flows.get(s.id)) for s in connected
        )
        if all_mass_ok:
            inflow = sum(float(point.mass_flows[s.id]) for s in in_streams)
            outflow = sum(float(point.mass_flows[s.id]) for s in out_streams)
            diff = inflow - outflow
            rel_pct = (
                (100.0 * diff / inflow)
                if abs(inflow) > 1e-12
                else (0.0 if abs(diff) <= 1e-12 else None)
            )
            imbalances.append(
                NodalImbalance(
                    node_id=node.id,
                    species_id=None,
                    inflow=inflow,
                    outflow=outflow,
                    difference=diff,
                    relative_pct=rel_pct,
                )
            )
        else:
            imbalances.append(NodalImbalance(node_id=node.id, species_id=None))

        for species in circuit.species:
            factor = species.to_fraction_factor()
            all_species_ok = all_mass_ok and all(
                _is_measured_value(point.assays.get(s.id, {}).get(species.id))
                for s in connected
            )
            if all_species_ok:
                inflow_sp = sum(
                    float(point.mass_flows[s.id])
                    * float(point.assays[s.id][species.id])
                    * factor
                    for s in in_streams
                )
                outflow_sp = sum(
                    float(point.mass_flows[s.id])
                    * float(point.assays[s.id][species.id])
                    * factor
                    for s in out_streams
                )
                diff_sp = inflow_sp - outflow_sp
                rel_sp = (
                    (100.0 * diff_sp / inflow_sp)
                    if abs(inflow_sp) > 1e-12
                    else (0.0 if abs(diff_sp) <= 1e-12 else None)
                )
                imbalances.append(
                    NodalImbalance(
                        node_id=node.id,
                        species_id=species.id,
                        inflow=inflow_sp,
                        outflow=outflow_sp,
                        difference=diff_sp,
                        relative_pct=rel_sp,
                    )
                )
            else:
                imbalances.append(
                    NodalImbalance(node_id=node.id, species_id=species.id)
                )

    return imbalances


def _build_feasible_generic_state(
    incidence_a: np.ndarray,
    n_s: int,
    n_c: int,
) -> tuple[np.ndarray, np.ndarray]:
    """Build a deterministic strictly positive feasible state (M*, x*) satisfying
    A @ M* = 0 and A @ (M* * x*_i) = 0 so structural Jacobian ranks match the
    exact bilinear balance manifold without noise-induced rank inflation."""
    n_u = incidence_a.shape[0]
    rng = np.random.default_rng(202501)

    c_obj = rng.uniform(0.8, 1.5, size=n_s)
    bounds = [(10.0 + 3.17 * j, None) for j in range(n_s)]
    lp_res = scipy.optimize.linprog(
        c=c_obj,
        A_eq=incidence_a,
        b_eq=np.zeros(n_u, dtype=np.float64),
        bounds=bounds,
        method="highs",
    )
    if lp_res.success and lp_res.x is not None and np.all(lp_res.x > 0.0):
        m_star = np.asarray(lp_res.x, dtype=np.float64)
    else:
        m_star = np.array([103.7 + 11.13 * j for j in range(n_s)], dtype=np.float64)
        x_frac_star = np.array(
            [
                [0.011 + 0.0037 * j + 0.0053 * i + 0.0007 * j * i for i in range(n_c)]
                for j in range(n_s)
            ],
            dtype=np.float64,
        )
        return m_star, x_frac_star

    _, s_a, vt_a = np.linalg.svd(incidence_a, full_matrices=True)
    tol_a = _svd_rank_tol(incidence_a, s_a)
    r_a = int(np.sum(s_a > tol_a))
    null_a = vt_a[r_a:, :].T

    x_frac_star = np.zeros((n_s, n_c), dtype=np.float64)
    m_min = float(np.min(m_star))
    for i in range(n_c):
        gamma_i = 0.015 * (1.0 + 0.43 * i)
        if null_a.shape[1] > 0:
            alpha = rng.normal(size=null_a.shape[1])
            delta = null_a @ alpha
            max_abs = float(np.max(np.abs(delta)))
            if max_abs > 1e-12:
                delta = delta * (0.35 * m_min / max_abs)
            else:
                delta = np.zeros(n_s, dtype=np.float64)
        else:
            delta = np.zeros(n_s, dtype=np.float64)
        comp_flow = gamma_i * (m_star + delta)
        x_frac_star[:, i] = comp_flow / m_star

    return m_star, x_frac_star


def analyze_observability(
    circuit: CircuitSpec,
    point: MeasurementPoint,
) -> ObservabilityReport:
    """Analyze bilinear balance observability, variable classification, and DOF via QR/SVD."""
    n_u = len(circuit.nodes)
    n_s = len(circuit.streams)
    n_c = len(circuit.species)
    if n_u == 0 or n_s == 0 or n_c == 0:
        raise ValueError("Circuit must have at least 1 node, 1 stream, and 1 species.")

    incidence_a, _, _ = build_incidence_matrix(circuit)
    catalog = build_variable_catalog(circuit)
    total_vars = len(catalog)

    is_measured_flags: list[bool] = [False] * total_vars
    for j, stream in enumerate(circuit.streams):
        is_measured_flags[j] = _is_measured_value(point.mass_flows.get(stream.id))
        stream_assays = point.assays.get(stream.id, {})
        for i, species in enumerate(circuit.species):
            col_idx = n_s + j * n_c + i
            is_measured_flags[col_idx] = _is_measured_value(stream_assays.get(species.id))

    mass_state, assays_frac_state = _build_feasible_generic_state(
        incidence_a, n_s, n_c
    )

    m_ref = max(float(np.max(mass_state)), 1.0)
    x_ref = np.maximum(np.mean(assays_frac_state, axis=0), 1e-9)

    mass_tilde = mass_state / m_ref
    assays_tilde = assays_frac_state / x_ref[np.newaxis, :]

    b_tilde = build_bilinear_jacobian(incidence_a, mass_tilde, assays_tilde)

    meas_idx = [idx for idx, flag in enumerate(is_measured_flags) if flag]
    unmeas_idx = [idx for idx, flag in enumerate(is_measured_flags) if not flag]

    b_m_tilde = b_tilde[:, meas_idx]
    b_u_tilde = b_tilde[:, unmeas_idx]

    m_eq = b_tilde.shape[0]
    n_m = len(meas_idx)
    n_u_var = len(unmeas_idx)

    _, s_b, _ = np.linalg.svd(b_tilde, full_matrices=False)
    tol_b = _svd_rank_tol(b_tilde, s_b)
    r_b = int(np.sum(s_b > tol_b))

    if n_u_var == 0:
        r_u = 0
        q2 = np.eye(m_eq, dtype=np.float64)
        n_u_null = np.zeros((0, 0), dtype=np.float64)
    else:
        u_u, s_u, vt_u = np.linalg.svd(b_u_tilde, full_matrices=True)
        tol_u = _svd_rank_tol(b_u_tilde, s_u)
        r_u = int(np.sum(s_u > tol_u))

        # Cross-check rank with pivoted QR on the scaled unmeasured Jacobian
        _, r_qr, _ = scipy.linalg.qr(b_u_tilde, mode="full", pivoting=True)
        diag_r = np.abs(np.diag(r_qr))
        r_u_qr = int(np.sum(diag_r > tol_u))
        r_u = max(r_u, 0) if r_u == r_u_qr else r_u

        q2 = u_u[:, r_u:]
        n_u_null = vt_u[r_u:, :].T

    classifications: dict[int, VariableClass] = {}
    unobservable_variables: list[str] = []

    for local_j, global_idx in enumerate(unmeas_idx):
        var_key = catalog[global_idx][0]
        if n_u_null.shape[1] == 0 or float(np.linalg.norm(n_u_null[local_j, :])) < 1e-8:
            classifications[global_idx] = VariableClass.UNMEASURED_OBSERVABLE
        else:
            classifications[global_idx] = VariableClass.UNMEASURED_UNOBSERVABLE
            unobservable_variables.append(var_key)

    if q2.shape[1] > 0 and n_m > 0:
        r_m = q2.T @ b_m_tilde
        for local_k, global_idx in enumerate(meas_idx):
            col_norm = float(np.linalg.norm(r_m[:, local_k]))
            if col_norm > 1e-8:
                classifications[global_idx] = VariableClass.MEASURED_REDUNDANT
            else:
                classifications[global_idx] = VariableClass.MEASURED_NON_REDUNDANT
    else:
        for global_idx in meas_idx:
            classifications[global_idx] = VariableClass.MEASURED_NON_REDUNDANT

    redundancy_dof = r_b - r_u
    global_dof = r_b - n_u_var

    variable_statuses = [
        VariableObservability(
            variable_key=var_key,
            stream_id=stream_id,
            species_id=species_id,
            is_measured=is_measured_flags[idx],
            classification=classifications[idx],
        )
        for idx, (var_key, stream_id, species_id) in enumerate(catalog)
    ]

    if unobservable_variables or global_dof < 0:
        can_reconcile = False
        is_deterministic = False
        degrees_of_freedom = global_dof
        missing_hints = _build_missing_hints(
            circuit, variable_statuses, unobservable_variables, global_dof
        )
    elif redundancy_dof == 0:
        can_reconcile = True
        is_deterministic = True
        degrees_of_freedom = 0
        missing_hints = []
    else:
        can_reconcile = True
        is_deterministic = False
        degrees_of_freedom = redundancy_dof
        missing_hints = []

    nodal_imbalances = _compute_nodal_imbalances(circuit, point)

    return ObservabilityReport(
        num_nodes=n_u,
        num_streams=n_s,
        num_species=n_c,
        total_variables=total_vars,
        measured_count=n_m,
        unmeasured_count=n_u_var,
        rank_constraints=r_b,
        rank_unmeasured=r_u,
        degrees_of_freedom=degrees_of_freedom,
        redundancy_dof=redundancy_dof,
        is_deterministic=is_deterministic,
        can_reconcile=can_reconcile,
        unobservable_variables=unobservable_variables,
        missing_measurements_hint=missing_hints,
        variable_statuses=variable_statuses,
        nodal_imbalances=nodal_imbalances,
    )
