"""End-to-end Phase 3 integration audit for MetBalance Pro.

Exercises the complete headless engineering pipeline across Dataset A (Polymetallic
4-Product, 60 shifts) and Dataset B (Multinode Rougher-Scavenger-Cleaner, 30 days),
generates `reporte_prueba.xlsx` and `reporte_prueba.pdf`, and programmatically audits
both exported deliverables.
"""

from __future__ import annotations

import base64
import re
import sys
import tempfile
import zlib
from pathlib import Path

import openpyxl

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.etl import (
    apply_cleaning_plan,
    build_amira_p754_presets,
    build_default_cleaning_plan,
    profile_file,
)
from core.kpis import compute_kpis
from core.models import CorporateBrandingProfile, GEDVerdict, VariableClass
from core.observability import analyze_observability
from core.reconciliation import compare_with_without_outlier, reconcile_batch
from core.topology import get_circuit_template
from reporting.branding import generate_default_corporate_logo
from reporting.excel_exporter import export_to_excel
from reporting.pdf_exporter import export_to_pdf

SAMPLE_DATA_DIR = PROJECT_ROOT / "app" / "assets" / "sample_data"


def _audit_pdf_deliverable(
    pdf_path: Path,
    expected_sha256: str,
    expected_j: float,
    expected_feed_mass: float,
) -> tuple[int, int, int]:
    """Audit generated PDF via PyMuPDF (if installed) or stdlib PDF stream decoder."""
    raw = pdf_path.read_bytes()
    assert raw.startswith(b"%PDF-"), "PDF missing %PDF- header"
    assert len(raw) > 20000, f"PDF unexpectedly small: {len(raw)} bytes"

    image_xobjects = len(re.findall(rb"/Subtype\s*/Image", raw))
    assert image_xobjects >= 3, f"Expected embedded logo + chart images, found {image_xobjects}"

    try:
        import fitz  # type: ignore

        doc = fitz.open(str(pdf_path))
        page_count = len(doc)
        extracted_text = "\n".join(page.get_text() for page in doc)
        doc.close()
    except ImportError:
        page_count = len(re.findall(rb"/Type\s*/Page(?![sA-Za-z])", raw))
        chunks: list[str] = []
        for match in re.finditer(rb"(<<.*?>>)\s*stream\r?\n(.*?)endstream", raw, flags=re.DOTALL):
            hdr, body = match.group(1), match.group(2).strip()
            if b"/Subtype /Image" in hdr:
                continue
            decoded = body
            if decoded.endswith(b"~>"):
                decoded = base64.a85decode(decoded[:-2], ignorechars=b" \r\n\t")
            try:
                decoded = zlib.decompress(decoded)
            except zlib.error:
                pass
            txt = decoded.decode("latin-1", errors="ignore")
            for lit in re.findall(r"\(([^()\\]*(?:\\.[^()\\]*)*)\)", txt):
                chunks.append(lit.replace(r"\(", "(").replace(r"\)", ")").replace(r"\\", "\\"))
        extracted_text = " ".join(chunks)

    assert page_count >= 2, f"Expected multi-page PDF, got {page_count}"
    assert "AMIRA P754" in extracted_text, "AMIRA P754 traceability seal missing in PDF"
    assert expected_sha256 in extracted_text, "Input SHA-256 missing in PDF"
    assert f"{expected_j:.4f}" in extracted_text, "GED J* metric missing in PDF"
    assert f"{expected_feed_mass:.2f}" in extracted_text, "KPI feed mass missing in PDF"
    return page_count, image_xobjects, len(extracted_text)


def main() -> int:
    """Run complete end-to-end audit across Dataset A & Dataset B and verify outputs."""
    assert "PySide6" not in sys.modules, "PySide6 must not be loaded in headless audit"

    # ------------------------------------------------------------------
    # 1. Dataset A — Polymetallic 4-Product (60 shifts)
    # ------------------------------------------------------------------
    circuit_a = get_circuit_template("polymetallic_4prod")
    path_a = SAMPLE_DATA_DIR / "dataset_a_polimetalico_4prod.xlsx"
    raw_df_a, prof_a = profile_file(path_a, circuit_a)
    assert prof_a.row_count == 60
    assert {"F80_um", "P80_um", "Pct_Mas_65M", "Pct_Menos_200M"}.issubset(
        set(prof_a.non_metallurgical_columns)
    )
    assert len(prof_a.anomalies) > 0

    plan_a = build_default_cleaning_plan(prof_a)
    plan_a.confirmed = True
    cleaned_a = apply_cleaning_plan(raw_df_a, prof_a, circuit_a, plan_a)
    assert len(cleaned_a.periods) == 60

    um_a = build_amira_p754_presets(circuit_a, alpha=0.05)
    obs_a0 = analyze_observability(circuit_a, cleaned_a.periods[0])
    assert obs_a0.can_reconcile is True

    batch_a = reconcile_batch(circuit_a, cleaned_a.periods, um_a)
    assert len(batch_a.results) == 60
    assert all(r.converged for r in batch_a.results)
    max_mass_a = max(r.max_mass_residual for r in batch_a.results)
    max_spec_a = max(r.max_species_residual for r in batch_a.results)
    assert max_mass_a < 1e-8 and max_spec_a < 1e-8

    idx_g15 = next(i for i, p in enumerate(cleaned_a.periods) if p.period_id == "G-015")
    shift_g15 = cleaned_a.periods[idx_g15]
    res_g15 = batch_a.results[idx_g15]
    assert res_g15.ged.verdict == GEDVerdict.FAIL
    cmp_a = compare_with_without_outlier(circuit_a, shift_g15, um_a, ["X:Cabeza:Cu"])
    assert cmp_a.j_reduction > 0.0 and cmp_a.verdict_improved is True
    kpi_g15 = compute_kpis(circuit_a, shift_g15, res_g15)
    assert abs(kpi_g15.unexplained_mass_rec) < 1e-8

    print(
        f"[OK] Dataset A (60 shifts): converged=60/60, max_mass_res={max_mass_a:.3e}, "
        f"max_sp_res={max_spec_a:.3e}, pass={batch_a.summary_pass_count}, "
        f"warn={batch_a.summary_warning_count}, fail={batch_a.summary_fail_count}, "
        f"G-015 J* {cmp_a.with_outlier.ged.objective_j:.2f} -> {cmp_a.without_outlier.ged.objective_j:.2f}"
    )

    # ------------------------------------------------------------------
    # 2. Dataset B — Multinode RSC Cu-Mo-Fe (30 days)
    # ------------------------------------------------------------------
    circuit_b = get_circuit_template("multinode_rsc")
    path_b = SAMPLE_DATA_DIR / "dataset_b_multinodo_rsc_cu_mo.xlsx"
    raw_df_b, prof_b = profile_file(path_b, circuit_b)
    assert prof_b.row_count == 30

    plan_b = build_default_cleaning_plan(prof_b)
    plan_b.confirmed = True
    cleaned_b = apply_cleaning_plan(raw_df_b, prof_b, circuit_b, plan_b)
    assert len(cleaned_b.periods) == 30

    um_b = build_amira_p754_presets(circuit_b, alpha=0.05)
    obs_b0 = analyze_observability(circuit_b, cleaned_b.periods[0])
    assert obs_b0.can_reconcile is True
    unmeas_obs = [
        st.variable_key
        for st in obs_b0.variable_statuses
        if st.classification == VariableClass.UNMEASURED_OBSERVABLE
    ]
    assert set(unmeas_obs) == {"M:RO_conc", "M:RO_tail", "M:SC_conc", "M:CL_tail"}

    batch_b = reconcile_batch(circuit_b, cleaned_b.periods, um_b)
    assert len(batch_b.results) == 30
    assert all(r.converged for r in batch_b.results)
    max_mass_b = max(r.max_mass_residual for r in batch_b.results)
    max_spec_b = max(r.max_species_residual for r in batch_b.results)
    assert max_mass_b < 1e-8 and max_spec_b < 1e-8

    idx_d18 = next(i for i, p in enumerate(cleaned_b.periods) if p.period_id == "D-18")
    day_d18 = cleaned_b.periods[idx_d18]
    res_d18 = batch_b.results[idx_d18]
    assert res_d18.ged.verdict == GEDVerdict.FAIL
    assert res_d18.ged.max_abs_z_variable == "X:RO_tail:Cu"
    cmp_b = compare_with_without_outlier(circuit_b, day_d18, um_b, ["X:RO_tail:Cu"])
    assert cmp_b.j_reduction > 0.0 and cmp_b.verdict_improved is True
    kpi_d18 = compute_kpis(circuit_b, day_d18, res_d18)
    assert abs(kpi_d18.unexplained_mass_rec) < 1e-8

    print(
        f"[OK] Dataset B (30 days): converged=30/30, max_mass_res={max_mass_b:.3e}, "
        f"max_sp_res={max_spec_b:.3e}, pass={batch_b.summary_pass_count}, "
        f"warn={batch_b.summary_warning_count}, fail={batch_b.summary_fail_count}, "
        f"D-18 max|z|={res_d18.ged.max_abs_z_variable}, "
        f"J* {cmp_b.with_outlier.ged.objective_j:.2f} -> {cmp_b.without_outlier.ged.objective_j:.2f}"
    )

    # ------------------------------------------------------------------
    # 3. Generate & Audit reporte_prueba.xlsx and reporte_prueba.pdf
    # ------------------------------------------------------------------
    with tempfile.TemporaryDirectory() as tmp_dir:
        logo_path = generate_default_corporate_logo(Path(tmp_dir) / "audit_logo.png", primary_color="#1B4965")
        branding = CorporateBrandingProfile(
            company_name="MetBalance Engineering S.A.",
            mining_unit="Planta Concentradora Polimetálica",
            technical_lead="Ing. Metalurgista Responsable",
            document_code="MBP-E2E-001",
            primary_color="#1B4965",
            logo_path=str(logo_path),
        )

        xlsx_out = export_to_excel(
            PROJECT_ROOT / "reporte_prueba.xlsx",
            circuit_a,
            cleaned_a,
            um_a,
            res_g15,
            kpi_g15,
            branding,
            batch_result=batch_a,
            outlier_comparison=cmp_a,
            language="es",
        )
        pdf_out = export_to_pdf(
            PROJECT_ROOT / "reporte_prueba.pdf",
            circuit_a,
            cleaned_a,
            um_a,
            res_g15,
            kpi_g15,
            branding,
            batch_result=batch_a,
            outlier_comparison=cmp_a,
            language="es",
        )

    # Audit Excel deliverable
    wb = openpyxl.load_workbook(xlsx_out, data_only=False)
    expected_sheets = [
        "Resumen_Ejecutivo",
        "Balance_Conciliado",
        "Diagnostico_GED",
        "Serie_Historica",
        "Datos_Auditados",
    ]
    assert wb.sheetnames == expected_sheets
    formula_count = sum(
        1
        for ws in wb.worksheets
        for row in ws.iter_rows()
        for cell in row
        if isinstance(cell.value, str) and cell.value.startswith("=")
    )
    assert formula_count > 30, f"Expected native Excel formulas, found {formula_count}"
    assert len(wb["Resumen_Ejecutivo"]._images) >= 1
    assert len(wb["Diagnostico_GED"].conditional_formatting) >= 1
    chart_count = sum(len(ws._charts) for ws in wb.worksheets)
    assert chart_count >= 3
    print(
        f"[OK] Excel audited ({xlsx_out.name}, {xlsx_out.stat().st_size} bytes): "
        f"sheets={len(wb.sheetnames)}, formulas={formula_count}, "
        f"images={len(wb['Resumen_Ejecutivo']._images)}, charts={chart_count}, "
        f"cf_rules={len(wb['Diagnostico_GED'].conditional_formatting)}"
    )

    # Audit PDF deliverable
    pdf_pages, pdf_imgs, pdf_text_len = _audit_pdf_deliverable(
        pdf_out,
        expected_sha256=cleaned_a.file_sha256,
        expected_j=res_g15.ged.objective_j,
        expected_feed_mass=kpi_g15.feed_mass_rec,
    )
    print(
        f"[OK] PDF audited ({pdf_out.name}, {pdf_out.stat().st_size} bytes): "
        f"pages={pdf_pages}, embedded_images={pdf_imgs}, extracted_chars={pdf_text_len}"
    )

    assert "PySide6" not in sys.modules
    print("[OK] E2E AUDIT PASSED 100% (PySide6 absent from sys.modules)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
