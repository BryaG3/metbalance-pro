"""Unit tests for headless corporate branding, Excel formulas/charts export, and PDF export."""

from __future__ import annotations

import base64
import re
import subprocess
import sys
import zlib
from pathlib import Path

import numpy as np
import openpyxl
import pytest
from openpyxl.utils.units import EMU_to_cm, EMU_to_pixels
from PIL import Image

from app.i18n.manager import I18nManager
from core.etl import (
    apply_cleaning_plan,
    build_amira_p754_presets,
    build_default_cleaning_plan,
    profile_file,
)
from core.kpis import compute_kpis
from core.models import (
    BatchReconciliationResult,
    CorporateBrandingProfile,
    GEDReport,
    GEDVerdict,
    ReconciliationResult,
)
from core.reconciliation import compare_with_without_outlier, reconcile_batch, reconcile_period
from core.topology import get_circuit_template
from reporting.branding import (
    generate_default_corporate_logo,
    load_branding_profile,
    save_branding_profile,
)
from reporting.excel_exporter import _build_proportional_logo, export_to_excel
from reporting.pdf_exporter import (
    _render_adjustments_png,
    _render_chi2_control_png,
    _render_parity_png,
    _rl_image_from_png_buf,
    export_to_pdf,
)

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


def _extract_chart_title_text(title_obj) -> str:
    """Extract plain text from an openpyxl chart/axis Title object."""
    if title_obj is None or getattr(title_obj, "tx", None) is None:
        return ""
    rich = getattr(title_obj.tx, "rich", None)
    if rich is None:
        return ""
    parts: list[str] = []
    for para in getattr(rich, "p", []) or []:
        for run in getattr(para, "r", []) or []:
            if getattr(run, "t", None):
                parts.append(str(run.t))
    return "".join(parts).strip()


def _make_skipped_result(circuit, period_id: str = "G-NOFLOW") -> ReconciliationResult:
    """Create a SKIPPED_NO_FLOW ReconciliationResult compatible with BatchReconciliationResult."""
    return ReconciliationResult(
        period_id=period_id,
        converged=False,
        solver_used="skipped_no_flow",
        iterations=0,
        max_mass_residual=0.0,
        max_species_residual=0.0,
        reconciled_mass_flows={s.id: 0.0 for s in circuit.streams},
        reconciled_assays={
            s.id: {sp.id: 0.0 for sp in circuit.species} for s in circuit.streams
        },
        adjustments=[],
        ged=GEDReport(
            objective_j=0.0,
            degrees_of_freedom=0,
            alpha=0.05,
            chi2_critical=0.0,
            p_value=1.0,
            z_critical=1.959964,
            verdict=GEDVerdict.SKIPPED,
            suspect_variables=[],
            max_abs_z_variable=None,
            max_abs_z_value=0.0,
        ),
        excluded_outliers=[],
        status="SKIPPED_NO_FLOW",
    )


@pytest.mark.parametrize("lang", ["es", "en"])
def test_excel_logo_ratio_chart_axes_n3_anchor_skipped_and_ged_formula(
    tmp_path: Path,
    lang: str,
) -> None:
    """Verify proportional square/wide logos, chart axis metadata, N3 anchor, NO_FLOW handling, and GED WARNING formula."""
    circuit, dataset, uncertainty, result, kpi_report, batch_result, outlier_cmp, branding = (
        _build_sample_reporting_bundle()
    )

    # 1. Square 200x200 logo preserves 1:1 aspect ratio within 140x46 box
    square_logo = tmp_path / f"square_logo_{lang}.png"
    Image.new("RGB", (200, 200), "#1B4965").save(square_logo)
    sq_xl = _build_proportional_logo(square_logo)
    assert sq_xl.width == sq_xl.height == 46

    wide_logo = tmp_path / f"wide_logo_{lang}.png"
    Image.new("RGB", (320, 80), "#1B4965").save(wide_logo)
    wide_xl = _build_proportional_logo(wide_logo)
    assert wide_xl.width == 140
    assert wide_xl.height == 35

    branding = branding.model_copy(update={"logo_path": str(square_logo)})

    # Inject a SKIPPED_NO_FLOW shift into the batch
    skipped_res = _make_skipped_result(circuit, period_id="G-NOFLOW-01")
    batch_with_skipped = BatchReconciliationResult(
        results=[*batch_result.results, skipped_res],
        summary_pass_count=batch_result.summary_pass_count,
        summary_warning_count=batch_result.summary_warning_count,
        summary_fail_count=batch_result.summary_fail_count,
        summary_skipped_count=1,
        mean_chi2=batch_result.mean_chi2,
    )

    xlsx_path = tmp_path / f"polished_{lang}.xlsx"
    out = export_to_excel(
        xlsx_path,
        circuit,
        dataset,
        uncertainty,
        result,
        kpi_report,
        branding,
        batch_result=batch_with_skipped,
        outlier_comparison=outlier_cmp,
        language=lang,
    )

    wb = openpyxl.load_workbook(out, data_only=False)
    ws_exec = wb[wb.sheetnames[0]]
    ws_bal = wb[wb.sheetnames[1]]
    ws_ged = wb[wb.sheetnames[2]]
    ws_hist = wb[wb.sheetnames[3]]

    # Verify saved square logo dimensions in workbook XML (via anchor EMU extents)
    assert len(ws_exec._images) == 1
    saved_img = ws_exec._images[0]
    w_px = round(EMU_to_pixels(saved_img.anchor.ext.cx))
    h_px = round(EMU_to_pixels(saved_img.anchor.ext.cy))
    assert w_px == h_px <= 46

    # Verify all 3 charts have explicit axis visibility, positioning, titles, and legend.overlay = False
    for ws_chart_host in (ws_exec, ws_bal, ws_hist):
        assert len(ws_chart_host._charts) >= 1
        ch = ws_chart_host._charts[0]
        assert ch.x_axis.delete is False
        assert ch.y_axis.delete is False
        assert ch.x_axis.axPos == "b"
        assert ch.y_axis.axPos == "l"
        assert ch.x_axis.tickLblPos == "low"
        assert ch.legend is not None
        assert ch.legend.overlay is False
        assert _extract_chart_title_text(ch.x_axis.title) != ""
        assert _extract_chart_title_text(ch.y_axis.title) != ""

    # Verify Serie_Historica LineChart is anchored at N3 (col=13, row=2) with width=18, height=10
    hist_chart = ws_hist._charts[0]
    assert hist_chart.anchor._from.col == 13
    assert hist_chart.anchor._from.row == 2
    assert EMU_to_cm(hist_chart.anchor.ext.cx) == pytest.approx(18.0, abs=0.1)
    assert EMU_to_cm(hist_chart.anchor.ext.cy) == pytest.approx(10.0, abs=0.1)

    # Verify SKIPPED_NO_FLOW shift row in Serie_Historica has blank J* and clear skipped status
    skipped_row_idx = 4 + len(batch_result.results)
    assert ws_hist.cell(row=skipped_row_idx, column=1).value == "G-NOFLOW-01"
    assert ws_hist.cell(row=skipped_row_idx, column=7).value is None
    assert ws_hist.cell(row=skipped_row_idx, column=10).value == "SKIPPED / NO_FLOW"

    hist_formulas = [
        str(cell.value)
        for row in ws_hist.iter_rows()
        for cell in row
        if isinstance(cell.value, str) and cell.value.startswith("=")
    ]
    assert any("SKIPPED*" in f for f in hist_formulas)

    # Verify Diagnostico_GED WARNING threshold uses 1.644854 and has no dead >2.0 clause
    ged_status_formulas = [
        str(ws_ged.cell(row=r, column=11).value)
        for r in range(15, 15 + len(result.adjustments))
    ]
    assert ged_status_formulas
    for formula in ged_status_formulas:
        assert "1.644854" in formula
        assert '"WARNING"' in formula
        assert ">2.0," not in formula


@pytest.mark.parametrize("lang", ["es", "en"])
def test_pdf_long_circuit_name_fine_unit_header_and_skipped_batch(
    tmp_path: Path,
    lang: str,
) -> None:
    """Verify PDF handles long circuit names, shows mass unit in Section 2 fine header, and excludes NO_FLOW from Chi2 plot."""
    circuit, dataset, uncertainty, result, kpi_report, batch_result, outlier_cmp, branding = (
        _build_sample_reporting_bundle()
    )
    long_name = (
        "Planta Concentradora Polimetalica de Alta Complejidad Operativa "
        "Linea Primaria 4 Productos Ag-Cu-Pb-Zn"
    )
    long_desc = (
        "Circuito industrial de flotacion diferencial secuencial con molienda SAG "
        "y remolienda fina para recuperacion de plata, cobre, plomo y zinc."
    )
    long_circuit = circuit.model_copy(update={"name": long_name, "description": long_desc})

    skipped_res = _make_skipped_result(long_circuit, period_id="G-NOFLOW-02")
    batch_with_skipped = BatchReconciliationResult(
        results=[*batch_result.results, skipped_res],
        summary_pass_count=batch_result.summary_pass_count,
        summary_warning_count=batch_result.summary_warning_count,
        summary_fail_count=batch_result.summary_fail_count,
        summary_skipped_count=1,
        mean_chi2=batch_result.mean_chi2,
    )

    pdf_path = tmp_path / f"long_circuit_{lang}.pdf"
    out = export_to_pdf(
        pdf_path,
        long_circuit,
        dataset,
        uncertainty,
        result,
        kpi_report,
        branding,
        batch_result=batch_with_skipped,
        outlier_comparison=outlier_cmp,
        language=lang,
    )
    assert out.exists() and out.stat().st_size > 15000

    extracted_text, page_count = _extract_pdf_text_and_page_count(out)
    assert page_count >= 2
    assert "Planta Concentradora Polimetalica" in extracted_text
    assert long_circuit.mass_unit.value in extracted_text
    expected_fine_prefix = "Fino Rec." if lang == "es" else "Rec. Fine"
    assert expected_fine_prefix in extracted_text
    assert f"({long_circuit.mass_unit.value})" in extracted_text


@pytest.mark.parametrize("lang", ["es", "en"])
def test_pdf_chart_png_margins_and_proportional_sizing(lang: str) -> None:
    """Verify all 3 PDF Section 6 chart PNGs have unclipped white top/bottom margins and aspect-preserving RL sizing."""
    circuit, _dataset, _uncertainty, result, _kpi_report, batch_result, _outlier_cmp, branding = (
        _build_sample_reporting_bundle()
    )
    i18n = I18nManager(default_language=lang)

    chart_buffers = [
        ("parity", _render_parity_png(result, i18n, branding.primary_color)),
        ("adjustments", _render_adjustments_png(result, i18n)),
        ("chi2_control", _render_chi2_control_png(batch_result, i18n, branding.primary_color)),
    ]

    for chart_name, buf in chart_buffers:
        buf.seek(0)
        with Image.open(buf) as pil_img:
            rgb = pil_img.convert("RGB")
            px_w, px_h = rgb.size
            assert px_w > 0 and px_h > 0, f"{chart_name} rendered zero dimensions"
            assert px_w >= 1000 and px_h >= 600, f"{chart_name} unexpectedly small at 300 DPI: {px_w}x{px_h}"

            arr = np.asarray(rgb)
            # Top 4px and bottom 4px edges must be pure white (no clipped title or X-axis label ink)
            assert np.all(arr[:4, :, :] >= 250), f"{chart_name} has ink touching top 4px edge"
            assert np.all(arr[-4:, :, :] >= 250), f"{chart_name} has ink touching bottom 4px edge"

            # Verify real chart ink exists inside the interior (guard against blank render)
            non_white = np.any(arr < 250, axis=2)
            assert int(np.sum(non_white)) > 1000, f"{chart_name} rendered unexpectedly empty"

        # Verify ReportLab image preserves exact aspect ratio under both width-limited and height-limited budgets
        rl_fit = _rl_image_from_png_buf(buf, target_width=480.0, max_height=400.0)
        assert rl_fit.drawWidth == pytest.approx(480.0, rel=1e-4)
        assert rl_fit.drawHeight == pytest.approx(480.0 * (px_h / px_w), rel=1e-4)

        rl_capped = _rl_image_from_png_buf(buf, target_width=480.0, max_height=180.0)
        assert rl_capped.drawHeight == pytest.approx(180.0, rel=1e-4)
        assert rl_capped.drawWidth == pytest.approx(180.0 * (px_w / px_h), rel=1e-4)
