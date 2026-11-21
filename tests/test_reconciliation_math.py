"""Unit tests for WLS nonlinear reconciliation, GED statistics, outlier comparison, and KPIs."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from core.etl import apply_cleaning_plan, build_amira_p754_presets, build_default_cleaning_plan, profile_file
from core.kpis import compute_kpis
from core.models import (
    GEDVerdict,
    MeasurementPoint,
    UncertaintyModel,
    UncertaintyMode,
    UncertaintyScale,
    UncertaintySpec,
    make_assay_var_key,
    make_mass_var_key,
)
from core.reconciliation import (
    compare_with_without_outlier,
    reconcile_batch,
    reconcile_period,
)
from core.topology import build_incidence_matrix, get_circuit_template

SAMPLE_DATA_DIR = Path(__file__).resolve().parents[1] / "app" / "assets" / "sample_data"


def _assert_balance_closure(circuit, result, tol: float = 1e-8) -> None:
    """Independently verify convergence and < tol mass and species closure via incidence matrix A."""
    assert result.converged is True
    assert result.max_mass_residual < tol
    assert result.max_species_residual < tol

    matrix_a, _node_ids, stream_ids = build_incidence_matrix(circuit)
    m_hat = np.array([result.reconciled_mass_flows[sid] for sid in stream_ids], dtype=float)
    assert np.all(np.isfinite(m_hat))
    assert np.all(m_hat >= 0.0)

    mass_closure = np.max(np.abs(matrix_a @ m_hat))
    assert mass_closure < tol, f"Mass closure residual {mass_closure} >= {tol}"

    for sp in circuit.species:
        x_native = np.array([result.reconciled_assays[sid][sp.id] for sid in stream_ids], dtype=float)
        x_frac = x_native * sp.to_fraction_factor()
        assert np.all(np.isfinite(x_frac))
        assert np.all((x_frac >= 0.0) & (x_frac <= 1.0))

        species_closure = np.max(np.abs(matrix_a @ (m_hat * x_frac)))
        assert species_closure < tol, f"Species {sp.id} closure residual {species_closure} >= {tol}"


def _build_rsc_closed_truth() -> tuple[dict[str, float], dict[str, dict[str, float]]]:
    """Return an exact closed 4-node Rougher-Scavenger-Cleaner balance with circulating loads."""
    f_fresh = 100.0
    final_conc = 3.2
    final_tail = 96.8

    f_cu, f_mo, f_fe = 1.00, 0.040, 4.50
    fc_cu, fc_mo, fc_fe = 27.50, 0.950, 26.00

    ft_cu = (f_fresh * f_cu - final_conc * fc_cu) / final_tail
    ft_mo = (f_fresh * f_mo - final_conc * fc_mo) / final_tail
    ft_fe = (f_fresh * f_fe - final_conc * fc_fe) / final_tail

    cl_tail = 18.0
    cl_cu, cl_mo, cl_fe = 2.20, 0.075, 9.00
    ro_conc = final_conc + cl_tail
    rc_cu = (final_conc * fc_cu + cl_tail * cl_cu) / ro_conc
    rc_mo = (final_conc * fc_mo + cl_tail * cl_mo) / ro_conc
    rc_fe = (final_conc * fc_fe + cl_tail * cl_fe) / ro_conc

    sc_conc = 12.0
    sc_cu, sc_mo, sc_fe = 1.45, 0.055, 6.50
    ro_tail = sc_conc + final_tail
    rt_cu = (sc_conc * sc_cu + final_tail * ft_cu) / ro_tail
    rt_mo = (sc_conc * sc_mo + final_tail * ft_mo) / ro_tail
    rt_fe = (sc_conc * sc_fe + final_tail * ft_fe) / ro_tail

    ro_feed = f_fresh + sc_conc + cl_tail
    rf_cu = (f_fresh * f_cu + sc_conc * sc_cu + cl_tail * cl_cu) / ro_feed
    rf_mo = (f_fresh * f_mo + sc_conc * sc_mo + cl_tail * cl_mo) / ro_feed
    rf_fe = (f_fresh * f_fe + sc_conc * sc_fe + cl_tail * cl_fe) / ro_feed

    flows = {
        "F_fresh": f_fresh,
        "RO_feed": ro_feed,
        "RO_conc": ro_conc,
        "RO_tail": ro_tail,
        "SC_conc": sc_conc,
        "FINAL_tail": final_tail,
        "FINAL_conc": final_conc,
        "CL_tail": cl_tail,
    }
    assays = {
        "F_fresh": {"Cu": f_cu, "Mo": f_mo, "Fe": f_fe},
        "RO_feed": {"Cu": rf_cu, "Mo": rf_mo, "Fe": rf_fe},
        "RO_conc": {"Cu": rc_cu, "Mo": rc_mo, "Fe": rc_fe},
        "RO_tail": {"Cu": rt_cu, "Mo": rt_mo, "Fe": rt_fe},
        "SC_conc": {"Cu": sc_cu, "Mo": sc_mo, "Fe": sc_fe},
        "FINAL_tail": {"Cu": ft_cu, "Mo": ft_mo, "Fe": ft_fe},
        "FINAL_conc": {"Cu": fc_cu, "Mo": fc_mo, "Fe": fc_fe},
        "CL_tail": {"Cu": cl_cu, "Mo": cl_mo, "Fe": cl_fe},
    }
    return flows, assays


def test_benchmark1_concentrator_2prod() -> None:
    """Benchmark 1: 2-product multi-element concentrator closes below 1e-8 and passes GED."""
    circuit = get_circuit_template("concentrator_2prod")
    uncertainty = build_amira_p754_presets(circuit)

    # Exact truth: 120 = 4.2 + 115.8
    true_cu_t = (120.0 * 1.15 - 4.2 * 27.5) / 115.8
    true_au_t = (120.0 * 0.95 - 4.2 * 21.0) / 115.8

    point = MeasurementPoint(
        period_id="BM1",
        mass_flows={"Feed": 120.8, "Concentrate": 4.16, "Tailings": 114.9},
        assays={
            "Feed": {"Cu": 1.17, "Au": 0.92},
            "Concentrate": {"Cu": 27.2, "Au": 21.6},
            "Tailings": {"Cu": true_cu_t * 1.02, "Au": true_au_t * 0.97},
        },
    )

    result = reconcile_period(circuit, point, uncertainty)
    _assert_balance_closure(circuit, result, tol=1e-8)
    assert result.ged.degrees_of_freedom == 3
    assert result.ged.verdict == GEDVerdict.PASS


def test_benchmark2_concentrator_3prod() -> None:
    """Benchmark 2: 3-product Cu-Zn-Ag concentrator closes below 1e-8."""
    circuit = get_circuit_template("concentrator_3prod")
    uncertainty = build_amira_p754_presets(circuit)

    # Exact truth: Feed=150, ConCu=5.5, ConZn=8.0, Tailings=136.5
    true_cu_t = (150.0 * 1.10 - 5.5 * 26.0 - 8.0 * 0.65) / 136.5
    true_zn_t = (150.0 * 3.40 - 5.5 * 1.8 - 8.0 * 52.0) / 136.5
    true_ag_t = (150.0 * 48.0 - 5.5 * 680.0 - 8.0 * 180.0) / 136.5

    point = MeasurementPoint(
        period_id="BM2",
        mass_flows={"Feed": 151.2, "ConCu": 5.42, "ConZn": 8.12, "Tailings": 135.4},
        assays={
            "Feed": {"Cu": 1.12, "Zn": 3.35, "Ag": 49.5},
            "ConCu": {"Cu": 25.8, "Zn": 1.84, "Ag": 692.0},
            "ConZn": {"Cu": 0.66, "Zn": 51.6, "Ag": 176.0},
            "Tailings": {"Cu": true_cu_t * 1.03, "Zn": true_zn_t * 0.98, "Ag": true_ag_t * 1.04},
        },
    )

    result = reconcile_period(circuit, point, uncertainty)
    _assert_balance_closure(circuit, result, tol=1e-8)
    assert result.ged.degrees_of_freedom == 4
    assert result.ged.verdict in (GEDVerdict.PASS, GEDVerdict.WARNING)


def test_benchmark3_polymetallic_4prod_and_dataset_a() -> None:
    """Benchmark 3: 4-product polymetallic balance (synthetic + cleaned Dataset A shifts)."""
    circuit = get_circuit_template("polymetallic_4prod")
    uncertainty = build_amira_p754_presets(circuit)

    # Exact truth: Cabeza=1000, ConCu=28, ConPb=22, ConZn=65, Relave=885
    m_c, m_cu, m_pb, m_zn, m_r = 1000.0, 28.0, 22.0, 65.0, 885.0
    head = {"Ag": 95.0, "Cu": 0.92, "Pb": 1.35, "Zn": 3.85}
    ccu = {"Ag": 1450.0, "Cu": 26.5, "Pb": 2.4, "Zn": 3.1}
    cpb = {"Ag": 1820.0, "Cu": 1.2, "Pb": 54.0, "Zn": 4.2}
    czn = {"Ag": 110.0, "Cu": 0.85, "Pb": 1.1, "Zn": 52.5}
    rel = {
        sp: (m_c * head[sp] - m_cu * ccu[sp] - m_pb * cpb[sp] - m_zn * czn[sp]) / m_r
        for sp in head
    }

    point = MeasurementPoint(
        period_id="BM3-SYN",
        mass_flows={
            "Cabeza": 1008.0,
            "ConCu": 27.6,
            "ConPb": 22.3,
            "ConZn": 64.2,
            "Relave": 891.0,
        },
        assays={
            "Cabeza": {"Ag": 96.5, "Cu": 0.93, "Pb": 1.33, "Zn": 3.89},
            "ConCu": {"Ag": 1435.0, "Cu": 26.3, "Pb": 2.45, "Zn": 3.05},
            "ConPb": {"Ag": 1840.0, "Cu": 1.18, "Pb": 53.7, "Zn": 4.28},
            "ConZn": {"Ag": 112.0, "Cu": 0.84, "Pb": 1.08, "Zn": 52.2},
            "Relave": {
                "Ag": rel["Ag"] * 1.03,
                "Cu": rel["Cu"] * 0.97,
                "Pb": rel["Pb"] * 1.02,
                "Zn": rel["Zn"] * 0.98,
            },
        },
    )

    result = reconcile_period(circuit, point, uncertainty)
    _assert_balance_closure(circuit, result, tol=1e-8)

    # Dataset A end-to-end math check
    dataset_a_path = SAMPLE_DATA_DIR / "dataset_a_polimetalico_4prod.xlsx"
    assert dataset_a_path.exists(), f"Missing generated sample file: {dataset_a_path}"
    raw_df, profile = profile_file(dataset_a_path, circuit)
    plan = build_default_cleaning_plan(profile)
    plan.confirmed = True
    cleaned = apply_cleaning_plan(raw_df, profile, circuit, plan)
    assert len(cleaned.periods) == 60

    batch = reconcile_batch(circuit, cleaned.periods[:10], uncertainty)
    assert len(batch.results) == 10
    for shift_res in batch.results:
        _assert_balance_closure(circuit, shift_res, tol=1e-8)


def test_benchmark4_multinode_rsc_measured_and_unmeasured_internal() -> None:
    """Benchmark 4: 4-node RSC closes < 1e-8 with full measurements AND with unmeasured circulating flows."""
    circuit = get_circuit_template("multinode_rsc")
    uncertainty = build_amira_p754_presets(circuit)
    true_flows, true_assays = _build_rsc_closed_truth()

    rng = np.random.default_rng(123)
    noisy_flows = {k: float(v * (1.0 + rng.normal(0.0, 0.01))) for k, v in true_flows.items()}
    noisy_assays = {
        s: {sp: float(val * (1.0 + rng.normal(0.0, 0.015))) for sp, val in sp_map.items()}
        for s, sp_map in true_assays.items()
    }

    # Case A: all 8 flows + all 24 assays measured
    full_point = MeasurementPoint(period_id="RSC-FULL", mass_flows=noisy_flows, assays=noisy_assays)
    res_full = reconcile_period(circuit, full_point, uncertainty)
    _assert_balance_closure(circuit, res_full, tol=1e-8)

    # Case B: only external flows measured; all 5 internal circulating flows unmeasured (None)
    ext_flows = {
        k: (v if k in {"F_fresh", "FINAL_conc", "FINAL_tail"} else None)
        for k, v in noisy_flows.items()
    }
    ext_point = MeasurementPoint(period_id="RSC-EXT", mass_flows=ext_flows, assays=noisy_assays)
    res_ext = reconcile_period(circuit, ext_point, uncertainty)
    _assert_balance_closure(circuit, res_ext, tol=1e-8)

    # Verify recovered internal circulating flows are realistic and close to true circulating loads
    assert res_ext.reconciled_mass_flows["RO_feed"] == pytest.approx(true_flows["RO_feed"], rel=0.05)
    assert res_ext.reconciled_mass_flows["SC_conc"] == pytest.approx(true_flows["SC_conc"], rel=0.12)
    assert res_ext.reconciled_mass_flows["CL_tail"] == pytest.approx(true_flows["CL_tail"], rel=0.12)

    # Case C: Dataset B days check
    dataset_b_path = SAMPLE_DATA_DIR / "dataset_b_multinodo_rsc_cu_mo.xlsx"
    assert dataset_b_path.exists(), f"Missing generated sample file: {dataset_b_path}"
    raw_df_b, profile_b = profile_file(dataset_b_path, circuit)
    plan_b = build_default_cleaning_plan(profile_b)
    plan_b.confirmed = True
    cleaned_b = apply_cleaning_plan(raw_df_b, profile_b, circuit, plan_b)
    assert len(cleaned_b.periods) == 30

    batch_b = reconcile_batch(circuit, cleaned_b.periods[:5], uncertainty)
    for day_res in batch_b.results:
        _assert_balance_closure(circuit, day_res, tol=1e-8)


def test_ged_detects_synthetic_25pct_tailings_error_and_dataset_a_outliers() -> None:
    """Injected +25% tailings gross error triggers GED FAIL and outlier exclusion reduces J*."""
    # Use multinode_rsc where FINAL_tail_Cu sits on node SC independently of F_fresh/RO_conc,
    # giving unique z-score identifiability, AND verify Dataset A gross-error shifts G-015/G-034/G-051.
    rsc = get_circuit_template("multinode_rsc")
    true_flows, true_assays = _build_rsc_closed_truth()

    # Tight laboratory assay/flow precision (1.0% RSD) so a +25% tailings gross error
    # cleanly exceeds chi2_critical (26.30 at 16 DOF) and yields unique max |z| on FINAL_tail:Cu
    specs = {}
    for s in rsc.streams:
        mk = make_mass_var_key(s.id)
        specs[mk] = UncertaintySpec(
            variable_key=mk,
            stream_id=s.id,
            mode=UncertaintyMode.MANUAL,
            scale=UncertaintyScale.RELATIVE_PCT,
            value=1.0,
        )
        for sp in rsc.species:
            ak = make_assay_var_key(s.id, sp.id)
            specs[ak] = UncertaintySpec(
                variable_key=ak,
                stream_id=s.id,
                species_id=sp.id,
                mode=UncertaintyMode.MANUAL,
                scale=UncertaintyScale.RELATIVE_PCT,
                value=1.0,
            )
    u_model = UncertaintyModel(specs=specs, alpha=0.05)

    corrupted_assays = {s: dict(v) for s, v in true_assays.items()}
    corrupted_assays["FINAL_tail"]["Cu"] *= 1.25  # +25% synthetic gross error on tailings Cu

    corrupted_point = MeasurementPoint(
        period_id="GED-SYN",
        mass_flows=dict(true_flows),
        assays=corrupted_assays,
    )
    res_bad = reconcile_period(rsc, corrupted_point, u_model)
    _assert_balance_closure(rsc, res_bad, tol=1e-8)

    assert res_bad.ged.objective_j > res_bad.ged.chi2_critical
    assert res_bad.ged.verdict == GEDVerdict.FAIL
    assert res_bad.ged.max_abs_z_variable == "X:FINAL_tail:Cu"
    assert "X:FINAL_tail:Cu" in res_bad.ged.suspect_variables

    # Compare with vs without outlier
    comp = compare_with_without_outlier(rsc, corrupted_point, u_model, ["X:FINAL_tail:Cu"])
    _assert_balance_closure(rsc, comp.without_outlier, tol=1e-8)
    assert comp.j_reduction > 0.0
    assert comp.without_outlier.ged.objective_j < comp.with_outlier.ged.objective_j
    assert comp.verdict_improved is True
    assert "X:FINAL_tail:Cu" in comp.without_outlier.excluded_outliers

    # Also verify +25% on 2-prod tailings with tight 1.0% precision flags FAIL and suspects X:Tailings:Cu
    c2 = get_circuit_template("concentrator_2prod")
    u2_specs = {}
    for s in c2.streams:
        mk = make_mass_var_key(s.id)
        u2_specs[mk] = UncertaintySpec(
            variable_key=mk,
            stream_id=s.id,
            mode=UncertaintyMode.MANUAL,
            scale=UncertaintyScale.RELATIVE_PCT,
            value=1.0,
        )
        for sp in c2.species:
            ak = make_assay_var_key(s.id, sp.id)
            u2_specs[ak] = UncertaintySpec(
                variable_key=ak,
                stream_id=s.id,
                species_id=sp.id,
                mode=UncertaintyMode.MANUAL,
                scale=UncertaintyScale.RELATIVE_PCT,
                value=1.0,
            )
    u2 = UncertaintyModel(specs=u2_specs, alpha=0.05)
    true_cu_t = (100.0 * 1.10 - 3.0 * 22.0) / 97.0
    true_au_t = (100.0 * 0.95 - 3.0 * 21.0) / 97.0
    p2_bad = MeasurementPoint(
        period_id="BM1-GED",
        mass_flows={"Feed": 100.0, "Concentrate": 3.0, "Tailings": 97.0},
        assays={
            "Feed": {"Cu": 1.10, "Au": 0.95},
            "Concentrate": {"Cu": 22.0, "Au": 21.0},
            "Tailings": {"Cu": true_cu_t * 1.25, "Au": true_au_t},
        },
    )
    r2_bad = reconcile_period(c2, p2_bad, u2)
    _assert_balance_closure(c2, r2_bad, tol=1e-8)
    assert r2_bad.ged.objective_j > r2_bad.ged.chi2_critical
    assert r2_bad.ged.verdict == GEDVerdict.FAIL
    assert "X:Tailings:Cu" in r2_bad.ged.suspect_variables

    # Dataset A injected gross-error shifts G-015, G-034, G-051
    poly = get_circuit_template("polymetallic_4prod")
    raw_a, prof_a = profile_file(SAMPLE_DATA_DIR / "dataset_a_polimetalico_4prod.xlsx", poly)
    plan_a = build_default_cleaning_plan(prof_a)
    plan_a.confirmed = True
    cleaned_a = apply_cleaning_plan(raw_a, prof_a, poly, plan_a)
    u_a = build_amira_p754_presets(poly)

    periods_by_id = {p.period_id: p for p in cleaned_a.periods}
    for bad_shift in ("G-015", "G-034", "G-051"):
        assert bad_shift in periods_by_id
        res_shift = reconcile_period(poly, periods_by_id[bad_shift], u_a)
        _assert_balance_closure(poly, res_shift, tol=1e-8)
        assert res_shift.ged.objective_j > res_shift.ged.chi2_critical, f"{bad_shift} should exceed chi2_crit"
        assert res_shift.ged.verdict == GEDVerdict.FAIL

    # Dataset B sampler bias day D-18 identifies X:RO_tail:Cu and improves when excluded
    raw_b, prof_b = profile_file(SAMPLE_DATA_DIR / "dataset_b_multinodo_rsc_cu_mo.xlsx", rsc)
    plan_b = build_default_cleaning_plan(prof_b)
    plan_b.confirmed = True
    cleaned_b = apply_cleaning_plan(raw_b, prof_b, rsc, plan_b)
    u_b = build_amira_p754_presets(rsc)
    day_18 = next(p for p in cleaned_b.periods if p.period_id == "D-18")
    comp_b = compare_with_without_outlier(rsc, day_18, u_b, ["X:RO_tail:Cu"])
    _assert_balance_closure(rsc, comp_b.with_outlier, tol=1e-8)
    _assert_balance_closure(rsc, comp_b.without_outlier, tol=1e-8)
    assert comp_b.with_outlier.ged.verdict == GEDVerdict.FAIL
    assert comp_b.with_outlier.ged.max_abs_z_variable == "X:RO_tail:Cu"
    assert comp_b.j_reduction > 0.0
    assert comp_b.verdict_improved is True


def test_batch_reconciliation_and_kpis() -> None:
    """Verify reconcile_batch progress/summary and compute_kpis closure consistency."""
    circuit = get_circuit_template("concentrator_2prod")
    uncertainty = build_amira_p754_presets(circuit)

    true_cu_t = (100.0 * 1.20 - 4.0 * 27.0) / 96.0
    true_au_t = (100.0 * 1.00 - 4.0 * 20.0) / 96.0

    periods = [
        MeasurementPoint(
            period_id=f"P-{i:02d}",
            mass_flows={"Feed": 100.0 + 0.5 * i, "Concentrate": 4.0, "Tailings": 96.0},
            assays={
                "Feed": {"Cu": 1.20, "Au": 1.00},
                "Concentrate": {"Cu": 27.0, "Au": 20.0},
                "Tailings": {"Cu": true_cu_t, "Au": true_au_t},
            },
        )
        for i in range(1, 5)
    ]

    progress_calls: list[tuple[int, int]] = []
    batch = reconcile_batch(
        circuit,
        periods,
        uncertainty,
        progress_callback=lambda done, total: progress_calls.append((done, total)),
    )
    assert len(batch.results) == 4
    assert len(progress_calls) == 4
    assert progress_calls[-1] == (4, 4)
    assert (
        batch.summary_pass_count + batch.summary_warning_count + batch.summary_fail_count
        == len(batch.results)
    )
    assert batch.mean_chi2 == pytest.approx(
        float(np.mean([r.ged.objective_j for r in batch.results])),
        rel=1e-9,
    )

    kpis = compute_kpis(circuit, periods[0], batch.results[0])
    assert kpis.period_id == "P-01"
    assert len(kpis.products) == 2

    total_mass_pull = sum(p.mass_pull_rec_pct for p in kpis.products)
    assert total_mass_pull == pytest.approx(100.0, abs=1e-6)
    assert kpis.unexplained_mass_rec == pytest.approx(0.0, abs=1e-6)

    for sp in circuit.species:
        total_rec = sum(p.recovery_rec_pct[sp.id] for p in kpis.products)
        assert total_rec == pytest.approx(100.0, abs=1e-6)
        assert kpis.unexplained_fine_rec[sp.id] == pytest.approx(0.0, abs=1e-6)

    conc_kpi = next(p for p in kpis.products if p.stream_id == "Concentrate")
    assert conc_kpi.conc_ratio_rec == pytest.approx(
        kpis.feed_mass_rec / batch.results[0].reconciled_mass_flows["Concentrate"],
        rel=1e-6,
    )
