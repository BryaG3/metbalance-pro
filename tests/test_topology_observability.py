"""Unit tests for headless core imports, topology incidence/validation, and observability."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from core.models import (
    CircuitSpec,
    GradeUnit,
    MassUnit,
    MeasurementPoint,
    NodeSpec,
    SpeciesSpec,
    StreamRole,
    StreamSpec,
    UnitType,
    VariableClass,
)
from core.observability import analyze_observability
from core.topology import (
    build_incidence_matrix,
    get_circuit_template,
    list_circuit_templates,
    validate_topology,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _make_single_species_2prod_circuit() -> CircuitSpec:
    """Create a minimal 1-node, 2-product, 1-species concentrator circuit."""
    return CircuitSpec(
        name="SingleSpecies2Prod",
        description="Minimal 1-node 2-product Cu circuit",
        mass_unit=MassUnit.T_H,
        nodes=[
            NodeSpec(id="CONC", name="Concentrator", unit_type=UnitType.CONCENTRATOR),
        ],
        streams=[
            StreamSpec(id="Feed", name="Feed", role=StreamRole.FEED, from_node=None, to_node="CONC"),
            StreamSpec(
                id="Concentrate",
                name="Concentrate",
                role=StreamRole.CONCENTRATE,
                from_node="CONC",
                to_node=None,
            ),
            StreamSpec(
                id="Tailings",
                name="Tailings",
                role=StreamRole.TAILINGS,
                from_node="CONC",
                to_node=None,
            ),
        ],
        species=[SpeciesSpec(id="Cu", name="Copper", unit=GradeUnit.PERCENT)],
    )


def _status_map(report) -> dict[str, VariableClass]:
    """Index variable observability classifications by canonical variable key."""
    return {item.variable_key: item.classification for item in report.variable_statuses}


def test_core_modules_import_without_pyside6() -> None:
    """Verify the entire core package imports cleanly in a headless subprocess without PySide6."""
    script = (
        "import sys; "
        "import core.models, core.topology, core.observability, core.reconciliation, "
        "core.statistics_ged, core.kpis, core.etl, core.sample_generator; "
        "pyside = [m for m in sys.modules if m == 'PySide6' or m.startswith('PySide6.')]; "
        "assert not pyside, f'PySide6 unexpectedly imported: {pyside}'"
    )
    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, f"Subprocess failed:\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"


def test_circuit_templates_present_and_valid() -> None:
    """Verify all four required templates exist, carry metadata, and pass topology validation."""
    templates = list_circuit_templates()
    ids = {entry["id"] for entry in templates}
    expected_ids = {
        "concentrator_2prod",
        "concentrator_3prod",
        "polymetallic_4prod",
        "multinode_rsc",
    }
    assert expected_ids.issubset(ids)
    for entry in templates:
        assert entry.get("id")
        assert entry.get("name")
        assert entry.get("description")

    for tid in sorted(expected_ids):
        circuit = get_circuit_template(tid)
        assert isinstance(circuit, CircuitSpec)
        errors = validate_topology(circuit)
        assert errors == [], f"Template {tid} failed validation: {errors}"

    with pytest.raises((KeyError, ValueError)):
        get_circuit_template("nonexistent_template_xyz")


@pytest.mark.parametrize(
    "template_id",
    [
        "concentrator_2prod",
        "concentrator_3prod",
        "polymetallic_4prod",
        "multinode_rsc",
    ],
)
def test_incidence_matrix_exact_all_templates(template_id: str) -> None:
    """Verify incidence matrix A has +1 inflow, -1 outflow, and 0 disconnected for all templates."""
    circuit = get_circuit_template(template_id)
    matrix_a, node_ids, stream_ids = build_incidence_matrix(circuit)

    assert matrix_a.shape == (len(circuit.nodes), len(circuit.streams))
    assert node_ids == [n.id for n in circuit.nodes]
    assert stream_ids == [s.id for s in circuit.streams]

    node_idx = {nid: i for i, nid in enumerate(node_ids)}
    expected = np.zeros_like(matrix_a, dtype=float)
    for j, stream in enumerate(circuit.streams):
        if stream.to_node is not None:
            expected[node_idx[stream.to_node], j] += 1.0
        if stream.from_node is not None:
            expected[node_idx[stream.from_node], j] -= 1.0

    np.testing.assert_array_equal(matrix_a, expected)


def test_incidence_matrix_multinode_rsc_structure() -> None:
    """Check exact node-stream connections and column sums in the 4-node RSC circuit."""
    circuit = get_circuit_template("multinode_rsc")
    matrix_a, node_ids, stream_ids = build_incidence_matrix(circuit)
    n_idx = {nid: i for i, nid in enumerate(node_ids)}
    s_idx = {sid: j for j, sid in enumerate(stream_ids)}

    assert set(node_ids) == {"MIX", "RO", "SC", "CL"}
    assert set(stream_ids) == {
        "F_fresh",
        "RO_feed",
        "RO_conc",
        "RO_tail",
        "SC_conc",
        "FINAL_tail",
        "FINAL_conc",
        "CL_tail",
    }

    # External feed column sums to +1; external outputs sum to -1; internal streams sum to 0.
    assert matrix_a[n_idx["MIX"], s_idx["F_fresh"]] == 1
    assert np.sum(matrix_a[:, s_idx["F_fresh"]]) == 1
    assert matrix_a[n_idx["SC"], s_idx["FINAL_tail"]] == -1
    assert np.sum(matrix_a[:, s_idx["FINAL_tail"]]) == -1
    assert matrix_a[n_idx["CL"], s_idx["FINAL_conc"]] == -1
    assert np.sum(matrix_a[:, s_idx["FINAL_conc"]]) == -1

    for internal_id, src, dst in [
        ("RO_feed", "MIX", "RO"),
        ("RO_conc", "RO", "CL"),
        ("RO_tail", "RO", "SC"),
        ("SC_conc", "SC", "MIX"),
        ("CL_tail", "CL", "MIX"),
    ]:
        col = matrix_a[:, s_idx[internal_id]]
        assert col[n_idx[src]] == -1
        assert col[n_idx[dst]] == 1
        assert np.sum(col) == 0


def test_validate_topology_catches_structural_defects() -> None:
    """Verify validate_topology catches orphan streams, self-loops, missing IO, and disconnected graphs."""
    base = get_circuit_template("concentrator_2prod")

    # 1. Orphan stream (from_node=None and to_node=None)
    orphan_circuit = base.model_copy(
        update={
            "streams": [
                *base.streams,
                StreamSpec(id="Orphan", name="Orphan", role=StreamRole.INTERNAL, from_node=None, to_node=None),
            ]
        }
    )
    assert validate_topology(orphan_circuit), "Expected error for orphan stream"

    # 2. Self-loop (from_node == to_node)
    loop_circuit = base.model_copy(
        update={
            "streams": [
                *base.streams,
                StreamSpec(id="Loop", name="Loop", role=StreamRole.INTERNAL, from_node="CONC", to_node="CONC"),
            ]
        }
    )
    assert validate_topology(loop_circuit), "Expected error for self-loop"

    # 3. Missing inflow (only outgoing streams from node)
    no_inflow_circuit = base.model_copy(
        update={
            "streams": [s for s in base.streams if s.to_node != "CONC"],
        }
    )
    assert validate_topology(no_inflow_circuit), "Expected error for node missing inflow"

    # 4. Missing outflow (only incoming stream to node)
    no_outflow_circuit = base.model_copy(
        update={
            "streams": [s for s in base.streams if s.from_node != "CONC"],
        }
    )
    assert validate_topology(no_outflow_circuit), "Expected error for node missing outflow"

    # 5. Disconnected graph (two separate nodes, each with its own feed and tailings)
    disconnected_circuit = CircuitSpec(
        name="Disconnected",
        description="Two unconnected units",
        mass_unit=MassUnit.T_H,
        nodes=[
            NodeSpec(id="U1", name="Unit 1", unit_type=UnitType.CONCENTRATOR),
            NodeSpec(id="U2", name="Unit 2", unit_type=UnitType.CONCENTRATOR),
        ],
        streams=[
            StreamSpec(id="F1", name="F1", role=StreamRole.FEED, from_node=None, to_node="U1"),
            StreamSpec(id="T1", name="T1", role=StreamRole.TAILINGS, from_node="U1", to_node=None),
            StreamSpec(id="F2", name="F2", role=StreamRole.FEED, from_node=None, to_node="U2"),
            StreamSpec(id="T2", name="T2", role=StreamRole.TAILINGS, from_node="U2", to_node=None),
        ],
        species=[SpeciesSpec(id="Cu", name="Copper", unit=GradeUnit.PERCENT)],
    )
    assert validate_topology(disconnected_circuit), "Expected error for disconnected graph"

    # 6. Invalid node reference
    bad_ref_circuit = base.model_copy(
        update={
            "streams": [
                StreamSpec(id="Feed", name="Feed", role=StreamRole.FEED, from_node=None, to_node="GHOST"),
                *base.streams[1:],
            ]
        }
    )
    assert validate_topology(bad_ref_circuit), "Expected error for invalid node reference"


def test_observability_deterministic_minimal_2prod() -> None:
    """Minimal 1-flow + 3-assay single-species 2-prod case must yield nu=0 deterministic."""
    circuit = _make_single_species_2prod_circuit()
    point = MeasurementPoint(
        period_id="DET-01",
        mass_flows={"Feed": 100.0, "Concentrate": None, "Tailings": None},
        assays={
            "Feed": {"Cu": 1.20},
            "Concentrate": {"Cu": 28.0},
            "Tailings": {"Cu": 0.14},
        },
    )

    report = analyze_observability(circuit, point)
    assert report.degrees_of_freedom == 0
    assert report.redundancy_dof == 0
    assert report.is_deterministic is True
    assert report.can_reconcile is True
    assert report.unobservable_variables == []

    classes = _status_map(report)
    assert classes["M:Concentrate"] == VariableClass.UNMEASURED_OBSERVABLE
    assert classes["M:Tailings"] == VariableClass.UNMEASURED_OBSERVABLE
    assert classes["M:Feed"] == VariableClass.MEASURED_NON_REDUNDANT
    assert classes["X:Feed:Cu"] == VariableClass.MEASURED_NON_REDUNDANT
    assert classes["X:Concentrate:Cu"] == VariableClass.MEASURED_NON_REDUNDANT
    assert classes["X:Tailings:Cu"] == VariableClass.MEASURED_NON_REDUNDANT


def test_observability_deterministic_two_species_2prod() -> None:
    """2-species 2-prod with 1 flow + 3 Cu assays + 2 Au assays has 3 eqs, 3 unknowns -> nu=0."""
    circuit = get_circuit_template("concentrator_2prod")
    point = MeasurementPoint(
        period_id="DET-02",
        mass_flows={"Feed": 100.0, "Concentrate": None, "Tailings": None},
        assays={
            "Feed": {"Cu": 1.20, "Au": 0.90},
            "Concentrate": {"Cu": 28.0, "Au": 18.0},
            "Tailings": {"Cu": 0.14, "Au": None},
        },
    )

    report = analyze_observability(circuit, point)
    assert report.degrees_of_freedom == 0
    assert report.is_deterministic is True
    assert report.can_reconcile is True
    assert report.unobservable_variables == []

    classes = _status_map(report)
    assert classes["M:Concentrate"] == VariableClass.UNMEASURED_OBSERVABLE
    assert classes["M:Tailings"] == VariableClass.UNMEASURED_OBSERVABLE
    assert classes["X:Tailings:Au"] == VariableClass.UNMEASURED_OBSERVABLE


def test_observability_underdetermined_unobservable() -> None:
    """Missing one assay in minimal 2-prod makes system underdetermined and non-reconcilable."""
    circuit = _make_single_species_2prod_circuit()
    point = MeasurementPoint(
        period_id="UNDET-01",
        mass_flows={"Feed": 100.0, "Concentrate": None, "Tailings": None},
        assays={
            "Feed": {"Cu": 1.20},
            "Concentrate": {"Cu": 28.0},
            "Tailings": {"Cu": None},
        },
    )

    report = analyze_observability(circuit, point)
    assert report.can_reconcile is False
    assert report.is_deterministic is False
    assert report.degrees_of_freedom < 0
    assert len(report.unobservable_variables) > 0
    assert len(report.missing_measurements_hint) > 0

    classes = _status_map(report)
    assert VariableClass.UNMEASURED_UNOBSERVABLE in set(classes.values())


def test_observability_unobservable_species_blocks_reconciliation() -> None:
    """Even with redundant flows, missing two Au assays in 2-prod leaves Au unobservable."""
    circuit = get_circuit_template("concentrator_2prod")
    point = MeasurementPoint(
        period_id="UNOBS-AU",
        mass_flows={"Feed": 100.0, "Concentrate": 4.0, "Tailings": 96.0},
        assays={
            "Feed": {"Cu": 1.20, "Au": 0.90},
            "Concentrate": {"Cu": 28.0, "Au": None},
            "Tailings": {"Cu": 0.14, "Au": None},
        },
    )

    report = analyze_observability(circuit, point)
    assert report.can_reconcile is False
    assert "X:Concentrate:Au" in report.unobservable_variables
    assert "X:Tailings:Au" in report.unobservable_variables


def test_observability_fully_measured_redundant_and_imbalance() -> None:
    """Fully measured 2-prod circuit is redundant with DOF=3 and detects raw nodal imbalances."""
    circuit = get_circuit_template("concentrator_2prod")
    point = MeasurementPoint(
        period_id="RED-01",
        mass_flows={"Feed": 100.0, "Concentrate": 4.2, "Tailings": 94.8},
        assays={
            "Feed": {"Cu": 1.20, "Au": 0.90},
            "Concentrate": {"Cu": 27.5, "Au": 18.5},
            "Tailings": {"Cu": 0.14, "Au": 0.18},
        },
    )

    report = analyze_observability(circuit, point)
    assert report.can_reconcile is True
    assert report.is_deterministic is False
    assert report.degrees_of_freedom == 3
    assert report.redundancy_dof == 3
    assert report.unobservable_variables == []
    assert all(
        st.classification == VariableClass.MEASURED_REDUNDANT
        for st in report.variable_statuses
    )

    mass_imbalances = [ni for ni in report.nodal_imbalances if ni.node_id == "CONC" and ni.species_id is None]
    assert len(mass_imbalances) == 1
    assert mass_imbalances[0].difference == pytest.approx(100.0 - (4.2 + 94.8), abs=1e-9)


def test_observability_multinode_rsc_external_flows_only() -> None:
    """In multinode_rsc with only external flows + all assays, all 5 internal flows are observable."""
    circuit = get_circuit_template("multinode_rsc")
    point = MeasurementPoint(
        period_id="RSC-OBS",
        mass_flows={
            "F_fresh": 100.0,
            "RO_feed": None,
            "RO_conc": None,
            "RO_tail": None,
            "SC_conc": None,
            "FINAL_tail": 96.8,
            "FINAL_conc": 3.2,
            "CL_tail": None,
        },
        assays={
            "F_fresh": {"Cu": 1.00, "Mo": 0.040, "Fe": 4.50},
            "RO_feed": {"Cu": 1.21, "Mo": 0.047, "Fe": 5.31},
            "RO_conc": {"Cu": 6.02, "Mo": 0.207, "Fe": 11.57},
            "RO_tail": {"Cu": 0.27, "Mo": 0.015, "Fe": 4.09},
            "SC_conc": {"Cu": 1.45, "Mo": 0.055, "Fe": 6.50},
            "FINAL_tail": {"Cu": 0.124, "Mo": 0.0099, "Fe": 3.79},
            "FINAL_conc": {"Cu": 27.5, "Mo": 0.950, "Fe": 26.0},
            "CL_tail": {"Cu": 2.20, "Mo": 0.075, "Fe": 9.00},
        },
    )

    report = analyze_observability(circuit, point)
    assert report.can_reconcile is True
    assert report.is_deterministic is False
    assert report.degrees_of_freedom > 0
    assert report.degrees_of_freedom == 11
    assert report.unobservable_variables == []

    classes = _status_map(report)
    for internal_stream in ("RO_feed", "RO_conc", "RO_tail", "SC_conc", "CL_tail"):
        assert classes[f"M:{internal_stream}"] == VariableClass.UNMEASURED_OBSERVABLE
