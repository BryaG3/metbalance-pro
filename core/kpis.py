"""Metallurgical Key Performance Indicators (KPIs) for nominal and reconciled balances.

Contained fine metal is computed consistently in `circuit.mass_unit` using:
    fine_metal = M * (x_native * species.to_fraction_factor())

This module belongs to the headless core layer and MUST NEVER import PySide6.
"""

from __future__ import annotations

import math
from typing import Optional

from .models import (
    CircuitSpec,
    KPIReport,
    MeasurementPoint,
    ProductKPI,
    ReconciliationResult,
    StreamRole,
)


def _finite_or_none(value: Optional[float]) -> Optional[float]:
    if value is None:
        return None
    f_val = float(value)
    return f_val if math.isfinite(f_val) else None


def compute_kpis(
    circuit: CircuitSpec,
    point: MeasurementPoint,
    result: ReconciliationResult,
) -> KPIReport:
    """Compute nominal vs. reconciled Mass Pull, Concentration Ratio, Fine Metal, and Recoveries."""
    feed_streams = [s for s in circuit.streams if s.role == StreamRole.FEED]
    if not feed_streams:
        feed_streams = [s for s in circuit.streams if s.from_node is None]

    product_streams = [
        s
        for s in circuit.streams
        if s.role in {StreamRole.CONCENTRATE, StreamRole.TAILINGS}
    ]
    if not product_streams:
        product_streams = [s for s in circuit.streams if s.to_node is None]

    feed_mass_rec = sum(
        float(result.reconciled_mass_flows[s.id]) for s in feed_streams
    )

    feed_nom_flows = [_finite_or_none(point.mass_flows.get(s.id)) for s in feed_streams]
    if feed_streams and all(v is not None for v in feed_nom_flows):
        feed_mass_nom: Optional[float] = sum(float(v) for v in feed_nom_flows if v is not None)
    else:
        feed_mass_nom = None

    feed_fine_rec: dict[str, float] = {}
    feed_fine_nom: dict[str, Optional[float]] = {}

    for sp in circuit.species:
        factor = sp.to_fraction_factor()
        feed_fine_rec[sp.id] = sum(
            float(result.reconciled_mass_flows[s.id])
            * float(result.reconciled_assays[s.id][sp.id])
            * factor
            for s in feed_streams
        )

        nom_terms: list[Optional[float]] = []
        for s in feed_streams:
            m_nom = _finite_or_none(point.mass_flows.get(s.id))
            x_nom = _finite_or_none(point.assays.get(s.id, {}).get(sp.id))
            if m_nom is not None and x_nom is not None:
                nom_terms.append(m_nom * x_nom * factor)
            else:
                nom_terms.append(None)

        if feed_streams and all(term is not None for term in nom_terms):
            feed_fine_nom[sp.id] = sum(float(t) for t in nom_terms if t is not None)
        else:
            feed_fine_nom[sp.id] = None

    products: list[ProductKPI] = []
    prod_nom_masses: list[Optional[float]] = []

    for prod in product_streams:
        m_rec = float(result.reconciled_mass_flows[prod.id])
        m_nom = _finite_or_none(point.mass_flows.get(prod.id))
        prod_nom_masses.append(m_nom)

        mass_pull_rec_pct = (
            100.0 * m_rec / feed_mass_rec if feed_mass_rec > 1e-12 else 0.0
        )
        mass_pull_nom_pct = (
            (100.0 * m_nom / feed_mass_nom)
            if (m_nom is not None and feed_mass_nom is not None and feed_mass_nom > 1e-12)
            else None
        )

        conc_ratio_rec = feed_mass_rec / m_rec if m_rec > 1e-12 else 0.0
        conc_ratio_nom = (
            (feed_mass_nom / m_nom)
            if (m_nom is not None and feed_mass_nom is not None and m_nom > 1e-12)
            else None
        )

        fine_metal_nom: dict[str, Optional[float]] = {}
        fine_metal_rec: dict[str, float] = {}
        recovery_nom_pct: dict[str, Optional[float]] = {}
        recovery_rec_pct: dict[str, float] = {}

        for sp in circuit.species:
            factor = sp.to_fraction_factor()
            x_rec = float(result.reconciled_assays[prod.id][sp.id])
            fm_rec = m_rec * x_rec * factor
            fine_metal_rec[sp.id] = fm_rec

            f_rec = feed_fine_rec[sp.id]
            recovery_rec_pct[sp.id] = (
                100.0 * fm_rec / f_rec if f_rec > 1e-15 else 0.0
            )

            x_nom = _finite_or_none(point.assays.get(prod.id, {}).get(sp.id))
            if m_nom is not None and x_nom is not None:
                fm_nom = m_nom * x_nom * factor
                fine_metal_nom[sp.id] = fm_nom
                f_nom = feed_fine_nom[sp.id]
                if f_nom is not None and f_nom > 1e-15:
                    recovery_nom_pct[sp.id] = 100.0 * fm_nom / f_nom
                else:
                    recovery_nom_pct[sp.id] = None
            else:
                fine_metal_nom[sp.id] = None
                recovery_nom_pct[sp.id] = None

        products.append(
            ProductKPI(
                stream_id=prod.id,
                stream_name=prod.name,
                role=prod.role,
                mass_pull_nom_pct=mass_pull_nom_pct,
                mass_pull_rec_pct=mass_pull_rec_pct,
                conc_ratio_nom=conc_ratio_nom,
                conc_ratio_rec=conc_ratio_rec,
                fine_metal_nom=fine_metal_nom,
                fine_metal_rec=fine_metal_rec,
                recovery_nom_pct=recovery_nom_pct,
                recovery_rec_pct=recovery_rec_pct,
            )
        )

    unexplained_mass_rec = feed_mass_rec - sum(
        float(result.reconciled_mass_flows[p.id]) for p in product_streams
    )

    if (
        feed_mass_nom is not None
        and product_streams
        and all(m is not None for m in prod_nom_masses)
    ):
        unexplained_mass_nom: Optional[float] = feed_mass_nom - sum(
            float(m) for m in prod_nom_masses if m is not None
        )
        unexplained_mass_nom_pct: Optional[float] = (
            (100.0 * unexplained_mass_nom / feed_mass_nom)
            if feed_mass_nom > 1e-12
            else None
        )
    else:
        unexplained_mass_nom = None
        unexplained_mass_nom_pct = None

    unexplained_fine_rec: dict[str, float] = {}
    unexplained_fine_nom: dict[str, Optional[float]] = {}
    unexplained_fine_nom_pct: dict[str, Optional[float]] = {}

    for sp in circuit.species:
        unexplained_fine_rec[sp.id] = feed_fine_rec[sp.id] - sum(
            p.fine_metal_rec[sp.id] for p in products
        )

        f_nom = feed_fine_nom[sp.id]
        prod_fms = [p.fine_metal_nom[sp.id] for p in products]
        if f_nom is not None and products and all(fm is not None for fm in prod_fms):
            unexp_nom = f_nom - sum(float(fm) for fm in prod_fms if fm is not None)
            unexplained_fine_nom[sp.id] = unexp_nom
            unexplained_fine_nom_pct[sp.id] = (
                (100.0 * unexp_nom / f_nom) if f_nom > 1e-15 else None
            )
        else:
            unexplained_fine_nom[sp.id] = None
            unexplained_fine_nom_pct[sp.id] = None

    return KPIReport(
        period_id=result.period_id,
        feed_mass_nom=feed_mass_nom,
        feed_mass_rec=feed_mass_rec,
        products=products,
        unexplained_mass_nom=unexplained_mass_nom,
        unexplained_mass_nom_pct=unexplained_mass_nom_pct,
        unexplained_mass_rec=unexplained_mass_rec,
        unexplained_fine_nom=unexplained_fine_nom,
        unexplained_fine_nom_pct=unexplained_fine_nom_pct,
        unexplained_fine_rec=unexplained_fine_rec,
    )
