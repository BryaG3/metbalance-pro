"""Deterministic generator for realistic MetBalance Pro sample datasets.

Generates:
  - dataset_a_polimetalico_4prod.xlsx and .csv (60 shifts, 4-product polymetallic plant)
  - dataset_b_multinodo_rsc_cu_mo.xlsx and .csv (30 days, 4-node Cu-Mo-Fe RSC circuit)

This module belongs to the headless core layer and MUST NEVER import PySide6.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

DEFAULT_SAMPLE_DIR = (
    Path(__file__).resolve().parent.parent / "app" / "assets" / "sample_data"
)


def _generate_dataset_a_dataframe(seed: int = 42) -> pd.DataFrame:
    """Build 60 shifts for polymetallic_4prod with realistic noise, dirty cells, and 3 gross errors."""
    rng = np.random.default_rng(seed)
    n_shifts = 60
    start_dt = datetime(2025, 1, 1, 7, 0)

    rows: list[dict[str, object]] = []
    for k in range(n_shifts):
        shift_id = f"G-{k + 1:03d}"
        shift_dt = start_dt + timedelta(hours=12 * k)

        # True plant mass flows (TMS/guardia)
        m_cab_true = 1600.0 + 75.0 * np.sin(2.0 * np.pi * k / 14.0) + rng.normal(0.0, 28.0)
        y_cu = float(np.clip(0.031 + rng.normal(0.0, 0.0018), 0.024, 0.040))
        y_pb = float(np.clip(0.039 + rng.normal(0.0, 0.0022), 0.030, 0.048))
        y_zn = float(np.clip(0.074 + rng.normal(0.0, 0.0035), 0.060, 0.090))

        m_con_cu_true = m_cab_true * y_cu
        m_con_pb_true = m_cab_true * y_pb
        m_con_zn_true = m_cab_true * y_zn
        m_rel_true = m_cab_true - (m_con_cu_true + m_con_pb_true + m_con_zn_true)

        # True product assays (ConCu, ConPb, ConZn, Relave) -> exact Cabeza closure
        ag_cu = float(np.clip(1140.0 + rng.normal(0.0, 45.0), 920.0, 1400.0))
        cu_cu = float(np.clip(26.4 + rng.normal(0.0, 0.65), 24.0, 29.5))
        pb_cu = float(np.clip(2.75 + rng.normal(0.0, 0.18), 1.8, 3.8))
        zn_cu = float(np.clip(3.40 + rng.normal(0.0, 0.22), 2.3, 4.5))

        ag_pb = float(np.clip(1620.0 + rng.normal(0.0, 55.0), 1350.0, 1950.0))
        cu_pb = float(np.clip(1.60 + rng.normal(0.0, 0.12), 0.9, 2.3))
        pb_pb = float(np.clip(58.2 + rng.normal(0.0, 1.1), 54.0, 63.0))
        zn_pb = float(np.clip(4.15 + rng.normal(0.0, 0.24), 3.0, 5.5))

        ag_zn = float(np.clip(215.0 + rng.normal(0.0, 12.0), 160.0, 280.0))
        cu_zn = float(np.clip(0.85 + rng.normal(0.0, 0.06), 0.55, 1.20))
        pb_zn = float(np.clip(1.35 + rng.normal(0.0, 0.10), 0.85, 1.95))
        zn_zn = float(np.clip(52.4 + rng.normal(0.0, 0.9), 49.0, 56.0))

        ag_rlv = float(np.clip(16.2 + rng.normal(0.0, 1.1), 11.0, 23.0))
        cu_rlv = float(np.clip(0.095 + rng.normal(0.0, 0.008), 0.065, 0.135))
        pb_rlv = float(np.clip(0.185 + rng.normal(0.0, 0.014), 0.12, 0.26))
        zn_rlv = float(np.clip(0.820 + rng.normal(0.0, 0.045), 0.62, 1.05))

        ag_cbz_true = (
            m_con_cu_true * ag_cu
            + m_con_pb_true * ag_pb
            + m_con_zn_true * ag_zn
            + m_rel_true * ag_rlv
        ) / m_cab_true
        cu_cbz_true = (
            m_con_cu_true * cu_cu
            + m_con_pb_true * cu_pb
            + m_con_zn_true * cu_zn
            + m_rel_true * cu_rlv
        ) / m_cab_true
        pb_cbz_true = (
            m_con_cu_true * pb_cu
            + m_con_pb_true * pb_pb
            + m_con_zn_true * pb_zn
            + m_rel_true * pb_rlv
        ) / m_cab_true
        zn_cbz_true = (
            m_con_cu_true * zn_cu
            + m_con_pb_true * zn_pb
            + m_con_zn_true * zn_zn
            + m_rel_true * zn_rlv
        ) / m_cab_true

        # Add realistic sampling/analytical noise (~0.50x AMIRA P754 RSD)
        def _noisy(val: float, rsd_pct: float) -> float:
            return float(val * (1.0 + rng.normal(0.0, rsd_pct / 100.0)))

        row: dict[str, object] = {
            "Guardia": shift_id,
            "Fecha": shift_dt.strftime("%Y-%m-%d %H:%M"),
            "Cabeza_TMS/guardia": round(_noisy(m_cab_true, 1.0), 2),
            "ConCu_TMS/guardia": round(_noisy(m_con_cu_true, 1.5), 2),
            "ConPb_TMS/guardia": round(_noisy(m_con_pb_true, 1.5), 2),
            "ConZn_TMS/guardia": round(_noisy(m_con_zn_true, 1.5), 2),
            "Relave_TMS/guardia": round(_noisy(m_rel_true, 1.5), 2),
            "Cabeza_Ag_g/t": round(_noisy(ag_cbz_true, 5.5), 2),
            "En_Cu_cbz_%": round(_noisy(cu_cbz_true, 2.0), 4),
            "Cabeza_Pb_%": round(_noisy(pb_cbz_true, 2.0), 4),
            "Cabeza_Zn_%": round(_noisy(zn_cbz_true, 2.0), 4),
            "ConCu_Ag_g/t": round(_noisy(ag_cu, 5.5), 2),
            "ConCu_Cu_%": round(_noisy(cu_cu, 1.25), 3),
            "ConCu_Pb_%": round(_noisy(pb_cu, 1.25), 3),
            "ConCu_Zn_%": round(_noisy(zn_cu, 1.25), 3),
            "ConPb_Ag_g/t": round(_noisy(ag_pb, 5.5), 2),
            "ConPb_Cu_%": round(_noisy(cu_pb, 1.25), 3),
            "ConPb_Pb_%": round(_noisy(pb_pb, 1.25), 3),
            "ConPb_Zn_%": round(_noisy(zn_pb, 1.25), 3),
            "ConZn_Ag_g/t": round(_noisy(ag_zn, 5.5), 2),
            "ConZn_Cu_%": round(_noisy(cu_zn, 1.25), 3),
            "ConZn_Pb_%": round(_noisy(pb_zn, 1.25), 3),
            "ConZn_Zn_%": round(_noisy(zn_zn, 1.25), 3),
            "Rlv_Ag_g/t": round(_noisy(ag_rlv, 5.5), 2),
            "Rlv_Cu_%": round(_noisy(cu_rlv, 5.0), 4),
            "Rlv_Pb_%": round(_noisy(pb_rlv, 5.0), 4),
            "Rlv_Zn_%": round(_noisy(zn_rlv, 5.0), 4),
            "F80_um": round(float(11500.0 + rng.normal(0.0, 650.0)), 1),
            "P80_um": round(float(118.0 + rng.normal(0.0, 6.5)), 1),
            "Pct_Mas_65M": round(float(14.5 + rng.normal(0.0, 1.2)), 2),
            "Pct_Menos_200M": round(float(64.2 + rng.normal(0.0, 1.8)), 2),
        }

        # Inject 3 controlled gross errors on G-015, G-034, G-051
        if shift_id == "G-015":
            # +35% assay error on feed Cu
            row["En_Cu_cbz_%"] = round(float(row["En_Cu_cbz_%"]) * 1.35, 4)
        elif shift_id == "G-034":
            # +45% assay error on Zn concentrate grade
            row["ConZn_Zn_%"] = round(float(row["ConZn_Zn_%"]) * 1.45, 3)
        elif shift_id == "G-051":
            # +180% gross contamination spike in Relave Zn assay
            row["Rlv_Zn_%"] = round(float(row["Rlv_Zn_%"]) * 2.80, 4)

        rows.append(row)

    df = pd.DataFrame(rows)

    # Inject controlled ETL data-quality anomalies on non-gross-error shifts
    # Format actual measured values with decimal commas (2 decimals to avoid 3-digit comma ambiguity)
    df["En_Cu_cbz_%"] = df["En_Cu_cbz_%"].astype(object)
    df.loc[3, "En_Cu_cbz_%"] = f"{float(df.loc[3, 'En_Cu_cbz_%']):.2f}".replace(".", ",")
    df["ConCu_Cu_%"] = df["ConCu_Cu_%"].astype(object)
    df.loc[21, "ConCu_Cu_%"] = f"{float(df.loc[21, 'ConCu_Cu_%']):.2f}".replace(".", ",")
    df["ConZn_Zn_%"] = df["ConZn_Zn_%"].astype(object)
    df.loc[40, "ConZn_Zn_%"] = f"{float(df.loc[40, 'ConZn_Zn_%']):.2f}".replace(".", ",")

    # Missing / blank cells (keeps shift observable)
    df.loc[8, "ConPb_TMS/guardia"] = np.nan
    df.loc[27, "Rlv_Ag_g/t"] = np.nan
    df.loc[46, "ConZn_TMS/guardia"] = np.nan

    # Corrupt strings
    df["Rlv_Pb_%"] = df["Rlv_Pb_%"].astype(object)
    df.loc[10, "Rlv_Pb_%"] = "N/D"
    df["ConCu_Ag_g/t"] = df["ConCu_Ag_g/t"].astype(object)
    df.loc[38, "ConCu_Ag_g/t"] = "ERR"

    # Negative assay value
    df.loc[18, "Rlv_Pb_%"] = -0.08

    # Physically out-of-range assay (>100%)
    df.loc[43, "ConPb_Pb_%"] = 105.4

    return df


def _generate_dataset_b_dataframe(seed: int = 42) -> pd.DataFrame:
    """Build 30 days for multinode_rsc with unmeasured circulating loads and sampler bias."""
    rng = np.random.default_rng(seed + 100)
    n_days = 30
    start_date = date(2025, 2, 1)

    rows: list[dict[str, object]] = []
    for d in range(n_days):
        day_id = f"D-{d + 1:02d}"
        cur_date = start_date + timedelta(days=d)

        # True steady-state flows around the 4-node RSC circuit
        f_fresh = 1180.0 + 45.0 * np.sin(2.0 * np.pi * d / 10.0) + rng.normal(0.0, 18.0)
        final_conc = f_fresh * float(np.clip(0.031 + rng.normal(0.0, 0.0015), 0.026, 0.036))
        final_tail = f_fresh - final_conc

        # Internal circulating loads (unmeasured by flowmeters)
        cl_tail = final_conc * float(np.clip(3.6 + rng.normal(0.0, 0.20), 2.8, 4.4))
        sc_conc = f_fresh * float(np.clip(0.145 + rng.normal(0.0, 0.010), 0.11, 0.18))
        ro_conc = final_conc + cl_tail
        ro_tail = final_tail + sc_conc
        ro_feed = f_fresh + sc_conc + cl_tail

        # True grades (%) satisfying exact nodal balances
        # Leaf streams: FINAL_conc, FINAL_tail, SC_conc, CL_tail determine all internal streams
        x_fconc = {
            "Cu": float(np.clip(28.4 + rng.normal(0.0, 0.55), 26.5, 30.5)),
            "Mo": float(np.clip(0.86 + rng.normal(0.0, 0.035), 0.72, 1.00)),
            "Fe": float(np.clip(26.8 + rng.normal(0.0, 0.45), 25.0, 28.5)),
        }
        x_ftail = {
            "Cu": float(np.clip(0.075 + rng.normal(0.0, 0.006), 0.055, 0.095)),
            "Mo": float(np.clip(0.0065 + rng.normal(0.0, 0.0005), 0.0045, 0.0085)),
            "Fe": float(np.clip(3.45 + rng.normal(0.0, 0.12), 3.0, 3.9)),
        }
        x_scconc = {
            "Cu": float(np.clip(1.25 + rng.normal(0.0, 0.08), 0.95, 1.55)),
            "Mo": float(np.clip(0.042 + rng.normal(0.0, 0.003), 0.032, 0.052)),
            "Fe": float(np.clip(8.60 + rng.normal(0.0, 0.35), 7.5, 9.8)),
        }
        x_cltail = {
            "Cu": float(np.clip(3.35 + rng.normal(0.0, 0.18), 2.7, 4.1)),
            "Mo": float(np.clip(0.115 + rng.normal(0.0, 0.007), 0.09, 0.14)),
            "Fe": float(np.clip(11.4 + rng.normal(0.0, 0.40), 10.0, 12.8)),
        }

        x_roconc: dict[str, float] = {}
        x_rotail: dict[str, float] = {}
        x_rofeed: dict[str, float] = {}
        x_ffresh: dict[str, float] = {}

        for sp in ("Cu", "Mo", "Fe"):
            x_roconc[sp] = (final_conc * x_fconc[sp] + cl_tail * x_cltail[sp]) / ro_conc
            x_rotail[sp] = (final_tail * x_ftail[sp] + sc_conc * x_scconc[sp]) / ro_tail
            x_ffresh[sp] = (final_conc * x_fconc[sp] + final_tail * x_ftail[sp]) / f_fresh
            x_rofeed[sp] = (
                f_fresh * x_ffresh[sp] + sc_conc * x_scconc[sp] + cl_tail * x_cltail[sp]
            ) / ro_feed

        def _noisy(val: float, rsd_pct: float) -> float:
            return float(val * (1.0 + rng.normal(0.0, rsd_pct / 100.0)))

        # Days 18..25 (0-indexed 17..24): systematic sampler bias on RO_tail Cu (+75%)
        ro_tail_cu_meas = _noisy(x_rotail["Cu"], 2.4)
        if 17 <= d <= 24:
            ro_tail_cu_meas *= 1.75

        row: dict[str, object] = {
            "Dia": day_id,
            "Fecha": cur_date.isoformat(),
            "F_fresh_t/h": round(_noisy(f_fresh, 1.0), 2),
            "RO_feed_t/h": round(_noisy(ro_feed, 2.4), 2),
            "FINAL_conc_t/h": round(_noisy(final_conc, 1.5), 2),
            "FINAL_tail_t/h": round(_noisy(final_tail, 1.5), 2),
            "F_fresh_Cu_%": round(_noisy(x_ffresh["Cu"], 2.0), 4),
            "F_fresh_Mo_%": round(_noisy(x_ffresh["Mo"], 2.0), 4),
            "F_fresh_Fe_%": round(_noisy(x_ffresh["Fe"], 2.0), 3),
            "RO_feed_Cu_%": round(_noisy(x_rofeed["Cu"], 2.4), 4),
            "RO_feed_Mo_%": round(_noisy(x_rofeed["Mo"], 2.4), 4),
            "RO_feed_Fe_%": round(_noisy(x_rofeed["Fe"], 2.4), 3),
            "RO_conc_Cu_%": round(_noisy(x_roconc["Cu"], 2.4), 3),
            "RO_conc_Mo_%": round(_noisy(x_roconc["Mo"], 2.4), 4),
            "RO_conc_Fe_%": round(_noisy(x_roconc["Fe"], 2.4), 3),
            "RO_tail_Cu_%": round(ro_tail_cu_meas, 4),
            "RO_tail_Mo_%": round(_noisy(x_rotail["Mo"], 2.4), 4),
            "RO_tail_Fe_%": round(_noisy(x_rotail["Fe"], 2.4), 3),
            "SC_conc_Cu_%": round(_noisy(x_scconc["Cu"], 2.4), 4),
            "SC_conc_Mo_%": round(_noisy(x_scconc["Mo"], 2.4), 4),
            "SC_conc_Fe_%": round(_noisy(x_scconc["Fe"], 2.4), 3),
            "FINAL_tail_Cu_%": round(_noisy(x_ftail["Cu"], 4.8), 4),
            "FINAL_tail_Mo_%": round(_noisy(x_ftail["Mo"], 4.8), 5),
            "FINAL_tail_Fe_%": round(_noisy(x_ftail["Fe"], 4.8), 3),
            "FINAL_conc_Cu_%": round(_noisy(x_fconc["Cu"], 1.25), 3),
            "FINAL_conc_Mo_%": round(_noisy(x_fconc["Mo"], 1.25), 4),
            "FINAL_conc_Fe_%": round(_noisy(x_fconc["Fe"], 1.25), 3),
            "CL_tail_Cu_%": round(_noisy(x_cltail["Cu"], 2.4), 4),
            "CL_tail_Mo_%": round(_noisy(x_cltail["Mo"], 2.4), 4),
            "CL_tail_Fe_%": round(_noisy(x_cltail["Fe"], 2.4), 3),
        }
        rows.append(row)

    return pd.DataFrame(rows)


def generate_sample_datasets(
    output_dir: Optional[Path] = None,
    seed: int = 42,
) -> dict[str, Path]:
    """Generate and persist Dataset A and Dataset B in both .xlsx and .csv formats."""
    target_dir = Path(output_dir) if output_dir is not None else DEFAULT_SAMPLE_DIR
    target_dir.mkdir(parents=True, exist_ok=True)

    df_a = _generate_dataset_a_dataframe(seed=seed)
    df_b = _generate_dataset_b_dataframe(seed=seed)

    paths = {
        "dataset_a_csv": target_dir / "dataset_a_polimetalico_4prod.csv",
        "dataset_a_xlsx": target_dir / "dataset_a_polimetalico_4prod.xlsx",
        "dataset_b_csv": target_dir / "dataset_b_multinodo_rsc_cu_mo.csv",
        "dataset_b_xlsx": target_dir / "dataset_b_multinodo_rsc_cu_mo.xlsx",
    }

    df_a.to_csv(paths["dataset_a_csv"], index=False, encoding="utf-8")
    with pd.ExcelWriter(paths["dataset_a_xlsx"], engine="openpyxl") as writer:
        df_a.to_excel(writer, sheet_name="Guardias_Polimetalico", index=False)

    df_b.to_csv(paths["dataset_b_csv"], index=False, encoding="utf-8")
    with pd.ExcelWriter(paths["dataset_b_xlsx"], engine="openpyxl") as writer:
        df_b.to_excel(writer, sheet_name="Circuito_RSC_CuMo", index=False)

    return paths


if __name__ == "__main__":
    generated = generate_sample_datasets()
    for name, path in generated.items():
        print(f"Generated {name}: {path}")
