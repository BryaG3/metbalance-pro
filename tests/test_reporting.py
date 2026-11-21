"""Unit tests for headless corporate branding, Excel formulas/charts export, and PDF export."""

from __future__ import annotations

import base64
import re
import subprocess
import sys
import zlib
from pathlib import Path

import openpyxl
import pytest
from PIL import Image

from core.etl import (
    apply_cleaning_plan,
    build_amira_p754_presets,
    build_default_cleaning_plan,
    profile_file,
)
from core.kpis import compute_kpis
from core.models import CorporateBrandingProfile
from core.reconciliation import compare_with_without_outlier, reconcile_batch, reconcile_period
from core.topology import get_circuit_template
from reporting.branding import (
    generate_default_corporate_logo,
    load_branding_profile,
    save_branding_profile,
)
from reporting.excel_exporter import export_to_excel
from reporting.pdf_exporter import export_to_pdf

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_DATA_DIR = PROJECT_ROOT / "app" / "assets" / "sample_data"


def _extract_pdf_text_and_page_count(pdf_path: Path) -> tuple[str, int]:
    """Extract literal text from ReportLab ASCII85/FlateDecode PDF streams via stdlib."""
    raw = pdf_path.read_bytes()
    assert raw.startswith(b"%PDF-"), "File does not start with %PDF- signature"

    chunks: list[str] = []
    for match in re.finditer(rb"(<<.*?>>)\s*stream\r?\n(.*?)endstream", raw, flags=re.DOTALL):
        header_bytes, stream_bytes = match.group(1), match.group(2).strip()
        if b"/Subtype /Image" in header_bytes:
            continue
        decoded = stream_bytes
        if decoded.endswith(b"~>"):
            decoded = base64.a85decode(decoded[:-2], ignorechars=b" \r\n\t")
        try:
            decoded = zlib.decompress(decoded)
        except zlib.error:
            pass
        text_block = decoded.decode("latin-1", errors="ignore")
        # Collect ReportLab text literal tokens inside parentheses (...)
        for lit in re.findall(r"\(([^()\\]*(?:\\.[^()\\]*)*)\)", text_block):
            unescaped = lit.replace(r"\(", "(").replace(r"\)", ")").replace(r"\\", "\\")
            chunks.append(unescaped)

    # Count /Type /Page objects (excluding /Type /Pages)
    page_count = len(re.findall(rb"/Type\s*/Page(?![sA-Za-z])", raw))
    return " ".join(chunks), page_count


def _build_sample_reporting_bundle():
    """Run real ETL + reconciliation + batch + outlier comparison on Dataset A."""
    circuit = get_circuit_template("polymetallic_4prod")
    raw_df, profile = profile_file(SAMPLE_DATA_DIR / "dataset_a_polimetalico_4prod.xlsx", circuit)
    plan = build_default_cleaning_plan(profile)
    plan.confirmed = True
    dataset = apply_cleaning_plan(raw_df, profile, circuit, plan)
    uncertainty = build_amira_p754_presets(circuit, alpha=0.05)

    shift_15 = next(p for p in dataset.periods if p.period_id == "G-015")
    result = reconcile_period(circuit, shift_15, uncertainty)
    kpi_report = compute_kpis(circuit, shift_15, result)
    batch_result = reconcile_batch(circuit, dataset.periods[:6], uncertainty)
    outlier_cmp = compare_with_without_outlier(circuit, shift_15, uncertainty, ["X:Cabeza:Cu"])

    branding = CorporateBrandingProfile(
        company_name="Andes Mineral Corp",
        mining_unit="Concentradora Sur",
        technical_lead="Ing. Elena Rojas",
        document_code="AMC-MET-2026",
        primary_color="#1B4965",
        logo_path=None,
    )
    return circuit, dataset, uncertainty, result, kpi_report, batch_result, outlier_cmp, branding


def test_reporting_imports_without_pyside6() -> None:
    """Verify reporting.* modules import cleanly without loading PySide6."""
    script = (
        "import sys; "
        "import reporting.branding, reporting.excel_exporter, reporting.pdf_exporter; "
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


def test_branding_save_load_roundtrip_and_logo_generation(tmp_path: Path) -> None:
    """Verify CorporateBrandingProfile JSON persistence and Pillow default logo generation."""
    logo_path = tmp_path / "corp_logo.png"
    out_logo = generate_default_corporate_logo(logo_path, primary_color="#234E70")
    assert out_logo.exists()
    with Image.open(out_logo) as img:
        assert img.size[0] >= 200 and img.size[1] >= 60

    profile_path = tmp_path / "branding.json"
    profile = CorporateBrandingProfile(
        company_name="Sierra Polymetallic S.A.",
        mining_unit="Planta Concentradora Norte",
        technical_lead="Ing. Marco Diaz",
        document_code="SP-BAL-009",
        primary_color="#234E70",
        logo_path=str(out_logo),
    )
    saved = save_branding_profile(profile, path=profile_path)
    assert saved.exists()
    loaded = load_branding_profile(path=profile_path)
    assert loaded == profile

    # Missing file falls back cleanly to default profile
    fallback = load_branding_profile(path=tmp_path / "does_not_exist.json")
    assert isinstance(fallback, CorporateBrandingProfile)


@pytest.mark.parametrize(
    "lang,expected_sheets",
    [
        ("es", ["Resumen_Ejecutivo", "Balance_Conciliado", "Diagnostico_GED", "Serie_Historica", "Datos_Auditados"]),
        ("en", ["Executive_Summary", "Reconciled_Balance", "GED_Diagnostics", "Historical_Series", "Audited_Data"]),
    ],
)
def test_export_to_excel_formulas_charts_logo_and_cf(
    tmp_path: Path,
    lang: str,
    expected_sheets: list[str],
) -> None:
    """Verify Excel export preserves native formulas, embedded logo, conditional formatting, and charts."""
    circuit, dataset, uncertainty, result, kpi_report, batch_result, outlier_cmp, branding = (
        _build_sample_reporting_bundle()
    )
    custom_logo = generate_default_corporate_logo(tmp_path / f"logo_{lang}.png", primary_color="#1B4965")
    branding = branding.model_copy(update={"logo_path": str(custom_logo)})

    xlsx_path = tmp_path / f"report_{lang}.xlsx"
    out = export_to_excel(
        xlsx_path,
        circuit,
        dataset,
        uncertainty,
        result,
        kpi_report,
        branding,
        batch_result=batch_result,
        outlier_comparison=outlier_cmp,
        language=lang,
    )
    assert out.exists() and out.stat().st_size > 5000

    wb = openpyxl.load_workbook(out, data_only=False)
    assert wb.sheetnames == expected_sheets

    ws_exec = wb[expected_sheets[0]]
    ws_bal = wb[expected_sheets[1]]
    ws_ged = wb[expected_sheets[2]]
    ws_hist = wb[expected_sheets[3]]
    ws_aud = wb[expected_sheets[4]]

    # Embedded corporate logo + native charts
    assert len(ws_exec._images) >= 1
    assert len(ws_exec._charts) >= 1
    assert len(ws_bal._charts) >= 1
    assert len(ws_hist._charts) >= 1

    # Conditional formatting on GED sheet
    assert len(ws_ged.conditional_formatting) >= 1

    # Native Excel formulas intact on Balance, Exec, GED, and Historical sheets
    bal_formulas = [
        str(cell.value)
        for row in ws_bal.iter_rows()
        for cell in row
        if isinstance(cell.value, str) and cell.value.startswith("=")
    ]
    assert any("=SUM(" in f for f in bal_formulas), "Expected =SUM formulas in Balance sheet"
    assert any("E" in f and "D" in f for f in bal_formulas), "Expected native diff/% formulas in Balance sheet"

    exec_formulas = [
        str(cell.value)
        for row in ws_exec.iter_rows()
        for cell in row
        if isinstance(cell.value, str) and cell.value.startswith("=")
    ]
    assert any("=SUM(" in f for f in exec_formulas)

    hist_formulas = [
        str(cell.value)
        for row in ws_hist.iter_rows()
        for cell in row
        if isinstance(cell.value, str) and cell.value.startswith("=")
    ]
    assert any("=AVERAGE(" in f for f in hist_formulas)
    assert any("=COUNTIF(" in f for f in hist_formulas)

    # Audit sheet contains input SHA-256 checksum
    audit_text = " ".join(
        str(cell.value)
        for row in ws_aud.iter_rows()
        for cell in row
        if cell.value is not None
    )
    assert dataset.file_sha256 in audit_text
    assert wb[expected_sheets[0]].freeze_panes is None or ws_bal.freeze_panes is not None
    assert ws_bal.freeze_panes is not None


@pytest.mark.parametrize("lang", ["es", "en"])
def test_export_to_pdf_multipage_content_and_traceability(tmp_path: Path, lang: str) -> None:
    """Verify ReportLab A4 PDF contains corporate metadata, SHA-256, AMIRA seal, KPIs, and GED."""
    circuit, dataset, uncertainty, result, kpi_report, batch_result, outlier_cmp, branding = (
        _build_sample_reporting_bundle()
    )
    pdf_path = tmp_path / f"report_{lang}.pdf"
    out = export_to_pdf(
        pdf_path,
        circuit,
        dataset,
        uncertainty,
        result,
        kpi_report,
        branding,
        batch_result=batch_result,
        outlier_comparison=outlier_cmp,
        language=lang,
    )
    assert out.exists() and out.stat().st_size > 15000

    extracted_text, page_count = _extract_pdf_text_and_page_count(out)
    assert page_count >= 2, f"Expected multi-page PDF, got {page_count}"

    assert branding.company_name in extracted_text
    assert branding.mining_unit in extracted_text
    assert branding.document_code in extracted_text
    assert dataset.file_sha256 in extracted_text
    assert "AMIRA P754" in extracted_text
    assert f"{result.ged.objective_j:.4f}" in extracted_text
    assert f"{result.ged.chi2_critical:.4f}" in extracted_text
    assert f"{kpi_report.feed_mass_rec:.2f}" in extracted_text
