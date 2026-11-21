"""Flowsheet topology, incidence matrix, validation, and circuit templates.

This module belongs to the headless core layer and MUST NEVER import PySide6.
"""

from __future__ import annotations

from collections import Counter, deque

import numpy as np

from .models import (
    CircuitSpec,
    GradeUnit,
    MassUnit,
    NodeSpec,
    SpeciesSpec,
    StreamRole,
    StreamSpec,
    UnitType,
)


def build_incidence_matrix(
    circuit: CircuitSpec,
) -> tuple[np.ndarray, list[str], list[str]]:
    """Build the directed node-stream incidence matrix A of shape (n_nodes, n_streams).

    A[k, j] = +1.0 if stream j enters node k (to_node == node_k),
              -1.0 if stream j leaves node k (from_node == node_k),
               0.0 otherwise.
    """
    node_ids = [node.id for node in circuit.nodes]
    stream_ids = [stream.id for stream in circuit.streams]
    node_idx = {node_id: idx for idx, node_id in enumerate(node_ids)}

    matrix = np.zeros((len(node_ids), len(stream_ids)), dtype=np.float64)
    for j, stream in enumerate(circuit.streams):
        if stream.to_node is not None and stream.to_node in node_idx:
            matrix[node_idx[stream.to_node], j] += 1.0
        if stream.from_node is not None and stream.from_node in node_idx:
            matrix[node_idx[stream.from_node], j] -= 1.0

    return matrix, node_ids, stream_ids


def _find_duplicates(ids: list[str]) -> list[str]:
    counts = Counter(ids)
    return [item for item, count in counts.items() if count > 1]


def validate_topology(circuit: CircuitSpec) -> list[str]:
    """Validate structural, naming, reference, and connectivity rules for a circuit.

    Returns an empty list when the circuit topology is valid.
    """
    errors: list[str] = []

    if len(circuit.nodes) < 1:
        errors.append("Circuit must contain at least 1 process node.")
    if len(circuit.streams) < 2:
        errors.append("Circuit must contain at least 2 streams.")
    if len(circuit.species) < 1:
        errors.append("Circuit must contain at least 1 assayed species.")

    node_ids = [node.id for node in circuit.nodes]
    stream_ids = [stream.id for stream in circuit.streams]
    species_ids = [sp.id for sp in circuit.species]

    if any(not nid.strip() for nid in node_ids):
        errors.append("Node IDs must be non-empty.")
    if any(not sid.strip() for sid in stream_ids):
        errors.append("Stream IDs must be non-empty.")
    if any(not sp_id.strip() for sp_id in species_ids):
        errors.append("Species IDs must be non-empty.")

    dup_nodes = _find_duplicates(node_ids)
    if dup_nodes:
        errors.append(f"Duplicate node IDs detected: {', '.join(dup_nodes)}.")

    dup_streams = _find_duplicates(stream_ids)
    if dup_streams:
        errors.append(f"Duplicate stream IDs detected: {', '.join(dup_streams)}.")

    dup_species = _find_duplicates(species_ids)
    if dup_species:
        errors.append(f"Duplicate species IDs detected: {', '.join(dup_species)}.")

    node_set = set(node_ids)
    inflow_counts: dict[str, int] = {nid: 0 for nid in node_set}
    outflow_counts: dict[str, int] = {nid: 0 for nid in node_set}
    adjacency: dict[str, set[str]] = {nid: set() for nid in node_set}

    for stream in circuit.streams:
        if stream.from_node is None and stream.to_node is None:
            errors.append(
                f"Stream '{stream.id}' is an orphan stream (both from_node and to_node are None)."
            )
            continue

        if stream.from_node is not None and stream.from_node not in node_set:
            errors.append(
                f"Stream '{stream.id}' references unknown from_node '{stream.from_node}'."
            )
        if stream.to_node is not None and stream.to_node not in node_set:
            errors.append(
                f"Stream '{stream.id}' references unknown to_node '{stream.to_node}'."
            )

        if (
            stream.from_node is not None
            and stream.to_node is not None
            and stream.from_node == stream.to_node
        ):
            errors.append(
                f"Stream '{stream.id}' forms a self-loop on node '{stream.from_node}'."
            )
            continue

        if stream.to_node is not None and stream.to_node in inflow_counts:
            inflow_counts[stream.to_node] += 1
        if stream.from_node is not None and stream.from_node in outflow_counts:
            outflow_counts[stream.from_node] += 1

        if (
            stream.from_node is not None
            and stream.to_node is not None
            and stream.from_node in node_set
            and stream.to_node in node_set
        ):
            adjacency[stream.from_node].add(stream.to_node)
            adjacency[stream.to_node].add(stream.from_node)

    for node in circuit.nodes:
        nid = node.id
        if nid not in inflow_counts:
            continue
        if inflow_counts[nid] < 1:
            errors.append(f"Node '{nid}' must have at least 1 inflow stream.")
        if outflow_counts[nid] < 1:
            errors.append(f"Node '{nid}' must have at least 1 outflow stream.")

    if len(node_set) > 1:
        start = circuit.nodes[0].id
        visited: set[str] = {start}
        queue: deque[str] = deque([start])
        while queue:
            current = queue.popleft()
            for neighbor in adjacency.get(current, ()):
                if neighbor not in visited:
                    visited.add(neighbor)
                    queue.append(neighbor)
        if len(visited) != len(node_set):
            disconnected = sorted(node_set - visited)
            errors.append(
                f"Circuit flowsheet graph is disconnected; unreachable node(s): {', '.join(disconnected)}."
            )

    return errors


_TEMPLATE_CATALOG: list[dict[str, str]] = [
    {
        "id": "concentrator_2prod",
        "name": "2-Product Concentrator (Cu-Au)",
        "description": "Single-node concentrator splitting Feed into Concentrate and Tailings (Cu %, Au g/t).",
    },
    {
        "id": "concentrator_3prod",
        "name": "3-Product Concentrator (Cu-Zn-Ag)",
        "description": "Single-node sequential concentrator splitting Feed into ConCu, ConZn, and Tailings.",
    },
    {
        "id": "polymetallic_4prod",
        "name": "4-Product Polymetallic Plant (Ag-Cu-Pb-Zn)",
        "description": "Single-node polymetallic plant splitting Cabeza into ConCu, ConPb, ConZn, and Relave.",
    },
    {
        "id": "multinode_rsc",
        "name": "Multinode Rougher-Scavenger-Cleaner (Cu-Mo-Fe)",
        "description": "Four-node flotation circuit (MIX, RO, SC, CL) with internal circulating loads.",
    },
]


def list_circuit_templates() -> list[dict[str, str]]:
    """Return metadata descriptors for all built-in metallurgical circuit templates."""
    return [dict(item) for item in _TEMPLATE_CATALOG]


def get_circuit_template(template_id: str) -> CircuitSpec:
    """Instantiate a built-in CircuitSpec template by ID."""
    if template_id == "concentrator_2prod":
        return CircuitSpec(
            name="Concentrator 2-Product (Cu-Au)",
            description="Single-node concentrator: Feed -> Concentrate + Tailings.",
            mass_unit=MassUnit.T_H,
            nodes=[
                NodeSpec(
                    id="CONC",
                    name="Concentrator",
                    unit_type=UnitType.CONCENTRATOR,
                    pos_x=260.0,
                    pos_y=180.0,
                )
            ],
            streams=[
                StreamSpec(
                    id="Feed",
                    name="Feed",
                    role=StreamRole.FEED,
                    from_node=None,
                    to_node="CONC",
                ),
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
            species=[
                SpeciesSpec(id="Cu", name="Copper", unit=GradeUnit.PERCENT),
                SpeciesSpec(id="Au", name="Gold", unit=GradeUnit.G_T),
            ],
        )

    if template_id == "concentrator_3prod":
        return CircuitSpec(
            name="Concentrator 3-Product (Cu-Zn-Ag)",
            description="Single-node concentrator: Feed -> ConCu + ConZn + Tailings.",
            mass_unit=MassUnit.T_H,
            nodes=[
                NodeSpec(
                    id="CONC",
                    name="Concentrator",
                    unit_type=UnitType.CONCENTRATOR,
                    pos_x=260.0,
                    pos_y=180.0,
                )
            ],
            streams=[
                StreamSpec(
                    id="Feed",
                    name="Feed",
                    role=StreamRole.FEED,
                    from_node=None,
                    to_node="CONC",
                ),
                StreamSpec(
                    id="ConCu",
                    name="Copper Concentrate",
                    role=StreamRole.CONCENTRATE,
                    from_node="CONC",
                    to_node=None,
                ),
                StreamSpec(
                    id="ConZn",
                    name="Zinc Concentrate",
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
            species=[
                SpeciesSpec(id="Cu", name="Copper", unit=GradeUnit.PERCENT),
                SpeciesSpec(id="Zn", name="Zinc", unit=GradeUnit.PERCENT),
                SpeciesSpec(id="Ag", name="Silver", unit=GradeUnit.G_T),
            ],
        )

    if template_id == "polymetallic_4prod":
        return CircuitSpec(
            name="Polymetallic 4-Product Plant (Ag-Cu-Pb-Zn)",
            description="Polymetallic plant: Cabeza -> ConCu + ConPb + ConZn + Relave.",
            mass_unit=MassUnit.TMS_GUARDIA,
            nodes=[
                NodeSpec(
                    id="PLANT",
                    name="Polymetallic Plant",
                    unit_type=UnitType.CONCENTRATOR,
                    pos_x=260.0,
                    pos_y=200.0,
                )
            ],
            streams=[
                StreamSpec(
                    id="Cabeza",
                    name="Cabeza",
                    role=StreamRole.FEED,
                    from_node=None,
                    to_node="PLANT",
                ),
                StreamSpec(
                    id="ConCu",
                    name="Concentrado Cu",
                    role=StreamRole.CONCENTRATE,
                    from_node="PLANT",
                    to_node=None,
                ),
                StreamSpec(
                    id="ConPb",
                    name="Concentrado Pb",
                    role=StreamRole.CONCENTRATE,
                    from_node="PLANT",
                    to_node=None,
                ),
                StreamSpec(
                    id="ConZn",
                    name="Concentrado Zn",
                    role=StreamRole.CONCENTRATE,
                    from_node="PLANT",
                    to_node=None,
                ),
                StreamSpec(
                    id="Relave",
                    name="Relave Final",
                    role=StreamRole.TAILINGS,
                    from_node="PLANT",
                    to_node=None,
                ),
            ],
            species=[
                SpeciesSpec(id="Ag", name="Silver", unit=GradeUnit.G_T),
                SpeciesSpec(id="Cu", name="Copper", unit=GradeUnit.PERCENT),
                SpeciesSpec(id="Pb", name="Lead", unit=GradeUnit.PERCENT),
                SpeciesSpec(id="Zn", name="Zinc", unit=GradeUnit.PERCENT),
            ],
        )

    if template_id == "multinode_rsc":
        return CircuitSpec(
            name="Multinode Rougher-Scavenger-Cleaner (Cu-Mo-Fe)",
            description="Four-node flotation circuit with MIX, RO, SC, and CL units and circulating loads.",
            mass_unit=MassUnit.T_H,
            nodes=[
                NodeSpec(
                    id="MIX",
                    name="Conditioner Mixer",
                    unit_type=UnitType.MIXER_SPLITTER,
                    pos_x=120.0,
                    pos_y=200.0,
                ),
                NodeSpec(
                    id="RO",
                    name="Rougher Bank",
                    unit_type=UnitType.ROUGHER,
                    pos_x=340.0,
                    pos_y=200.0,
                ),
                NodeSpec(
                    id="SC",
                    name="Scavenger Bank",
                    unit_type=UnitType.SCAVENGER,
                    pos_x=580.0,
                    pos_y=310.0,
                ),
                NodeSpec(
                    id="CL",
                    name="Cleaner Bank",
                    unit_type=UnitType.CLEANER,
                    pos_x=580.0,
                    pos_y=90.0,
                ),
            ],
            streams=[
                StreamSpec(
                    id="F_fresh",
                    name="Fresh Feed",
                    role=StreamRole.FEED,
                    from_node=None,
                    to_node="MIX",
                ),
                StreamSpec(
                    id="RO_feed",
                    name="Rougher Feed",
                    role=StreamRole.INTERNAL,
                    from_node="MIX",
                    to_node="RO",
                ),
                StreamSpec(
                    id="RO_conc",
                    name="Rougher Concentrate",
                    role=StreamRole.INTERNAL,
                    from_node="RO",
                    to_node="CL",
                ),
                StreamSpec(
                    id="RO_tail",
                    name="Rougher Tailings",
                    role=StreamRole.INTERNAL,
                    from_node="RO",
                    to_node="SC",
                ),
                StreamSpec(
                    id="SC_conc",
                    name="Scavenger Concentrate",
                    role=StreamRole.INTERNAL,
                    from_node="SC",
                    to_node="MIX",
                ),
                StreamSpec(
                    id="FINAL_tail",
                    name="Final Tailings",
                    role=StreamRole.TAILINGS,
                    from_node="SC",
                    to_node=None,
                ),
                StreamSpec(
                    id="FINAL_conc",
                    name="Final Concentrate",
                    role=StreamRole.CONCENTRATE,
                    from_node="CL",
                    to_node=None,
                ),
                StreamSpec(
                    id="CL_tail",
                    name="Cleaner Tailings",
                    role=StreamRole.INTERNAL,
                    from_node="CL",
                    to_node="MIX",
                ),
            ],
            species=[
                SpeciesSpec(id="Cu", name="Copper", unit=GradeUnit.PERCENT),
                SpeciesSpec(id="Mo", name="Molybdenum", unit=GradeUnit.PERCENT),
                SpeciesSpec(id="Fe", name="Iron", unit=GradeUnit.PERCENT),
            ],
        )

    raise ValueError(f"Unknown circuit template: '{template_id}'.")
