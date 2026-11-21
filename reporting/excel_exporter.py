"""Executive multi-sheet openpyxl workbook exporter for MetBalance Pro.

This module belongs to the headless reporting layer and MUST NEVER import PySide6.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import openpyxl
from openpyxl.chart import BarChart, LineChart, Reference
from openpyxl.drawing.image import Image as OpenpyxlImage
from openpyxl.formatting.rule import CellIsRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from app.i18n.manager import I18nManager
from core.models import (
    BatchReconciliationResult,
    CircuitSpec,
    CleanedDataset,
    CorporateBrandingProfile,
    GEDVerdict,
    KPIReport,
    OutlierComparisonResult,
    ReconciliationResult,
    UncertaintyModel,
)
from reporting.branding import resolve_corporate_logo

_BILINGUAL_LABELS: dict[str, dict[str, str]] = {
    "es": {
        "company": "Empresa / Compañía",
        "unit": "Unidad Minera / Planta",
        "lead": "Responsable Técnico",
        "doc_code": "Código de Documento",
        "circuit": "Circuito",
        "period": "Período / Guardia",
        "generated_at": "Emitido (UTC ISO)",
        "sha256": "SHA-256 Datos Entrada",
        "meta_header": "Metadatos Corporativos y de Trazabilidad",
        "ged_header": "Resumen Diagnóstico Estadístico (GED)",
        "kpi_header": "Resumen de Indicadores Metalúrgicos (Nominal vs. Reconciliado)",
        "stream": "Corriente",
        "role": "Rol",
        "species": "Especie",
        "var_key": "Clave Variable",
        "type_var": "Tipo",
        "mass_flow": "Flujo Másico",
        "assay": "Ley",
        "unit_col": "Unidad",
        "measured": "Medido",
        "reconciled": "Reconciliado",
        "abs_diff": "Dif. Abs.",
        "rel_pct": "Ajuste (%)",
        "sigma": "Sigma",
        "z_score": "z-score",
        "abs_z": "|z|",
        "outlier": "Outlier",
        "status": "Estado",
        "classification": "Clasificación",
        "mass_pull_nom": "Mass Pull Nom. (%)",
        "mass_pull_rec": "Mass Pull Rec. (%)",
        "conc_ratio_rec": "Razón Conc. Rec.",
        "fine_metal_rec": "Fino Reconciliado",
        "recovery_rec": "Recuperación Rec. (%)",
        "recovery_nom": "Recuperación Nom. (%)",
        "factor": "Factor Fracción",
        "totals": "Total Productos",
        "unexplained": "Diferencia No Explicada (Alim. - Prod.)",
        "balance_table_title": "Tabla 1. Balance Conciliado de Flujos Másicos y Leyes",
        "fine_table_title": "Tabla 2. Balance de Fino Contenido y Recuperación Metalúrgica (Fórmulas Nativas)",
        "outlier_cmp_title": "Comparación Con vs. Sin Outlier",
        "with_outlier_j": "J* Con Outlier",
        "without_outlier_j": "J* Sin Outlier",
        "j_reduction": "Reducción J*",
        "verdict_improved": "Mejora de Veredicto",
        "excluded_vars": "Variables Excluidas",
        "converged": "Convergido",
        "solver": "Solver",
        "iterations": "Iteraciones",
        "max_mass_res": "Residual Máx. Masa",
        "max_sp_res": "Residual Máx. Especies",
        "dof": "Grados de Libertad (nu)",
        "alpha": "Nivel alpha",
        "verdict": "Veredicto",
        "max_z_var": "Variable Máx. |z|",
        "max_z_val": "Máx. |z|",
        "mean_j": "Promedio J*",
        "count_pass": "Aprobados (PASS)",
        "count_warn": "Advertencias (WARNING)",
        "count_fail": "Fallos (FAIL)",
        "audit_title": "Registro Auditable de Limpieza ETL",
        "source_file": "Archivo Fuente",
        "raw_rows": "Filas Crudas",
        "clean_rows": "Filas Validadas",
        "ts_iso": "Timestamp ISO",
        "action": "Acción",
        "column": "Columna",
        "affected_rows": "Filas Afectadas",
        "detail": "Detalle",
        "sigma_title": "Matriz de Incertidumbres Aplicada (sigma)",
        "mode": "Modo",
        "scale": "Escala",
        "value": "Valor",
        "yes": "Sí",
        "no": "No",
        "chart_rec_title": "Recuperación Metalúrgica Reconciliada (%)",
        "chart_chi2_title": "Control Estadístico Chi-Cuadrado (J* vs. Crítico)",
        "feed_mass_rec": "Masa Alimentación Reconciliada",
    },
    "en": {
        "company": "Company / Organization",
        "unit": "Mining Unit / Plant",
        "lead": "Technical Lead",
        "doc_code": "Document Code",
        "circuit": "Circuit",
        "period": "Period / Shift",
        "generated_at": "Issued (UTC ISO)",
        "sha256": "Input File SHA-256",
        "meta_header": "Corporate and Traceability Metadata",
        "ged_header": "Global Error Detection Summary (GED)",
        "kpi_header": "Metallurgical KPI Summary (Nominal vs. Reconciled)",
        "stream": "Stream",
        "role": "Role",
        "species": "Species",
        "var_key": "Variable Key",
        "type_var": "Type",
        "mass_flow": "Mass Flow",
        "assay": "Assay",
        "unit_col": "Unit",
        "measured": "Measured",
        "reconciled": "Reconciled",
        "abs_diff": "Abs. Diff.",
        "rel_pct": "Adj. (%)",
        "sigma": "Sigma",
        "z_score": "z-score",
        "abs_z": "|z|",
        "outlier": "Outlier",
        "status": "Status",
        "classification": "Classification",
        "mass_pull_nom": "Nominal Mass Pull (%)",
        "mass_pull_rec": "Reconciled Mass Pull (%)",
        "conc_ratio_rec": "Reconciled Conc. Ratio",
        "fine_metal_rec": "Reconciled Fine Metal",
        "recovery_rec": "Reconciled Recovery (%)",
        "recovery_nom": "Nominal Recovery (%)",
        "factor": "Fraction Factor",
        "totals": "Products Total",
        "unexplained": "Unexplained Difference (Feed - Prod.)",
        "balance_table_title": "Table 1. Reconciled Mass Flow and Assay Balance",
        "fine_table_title": "Table 2. Contained Fine Metal and Metallurgical Recovery (Native Formulas)",
        "outlier_cmp_title": "With vs. Without Outlier Comparison",
        "with_outlier_j": "J* With Outlier",
        "without_outlier_j": "J* Without Outlier",
        "j_reduction": "J* Reduction",
        "verdict_improved": "Verdict Improved",
        "excluded_vars": "Excluded Variables",
        "converged": "Converged",
        "solver": "Solver",
        "iterations": "Iterations",
        "max_mass_res": "Max Mass Residual",
        "max_sp_res": "Max Species Residual",
        "dof": "Degrees of Freedom (nu)",
        "alpha": "Alpha Level",
        "verdict": "Verdict",
        "max_z_var": "Max |z| Variable",
        "max_z_val": "Max |z|",
        "mean_j": "Mean J*",
        "count_pass": "Passed (PASS)",
        "count_warn": "Warnings (WARNING)",
        "count_fail": "Fails (FAIL)",
        "audit_title": "Auditable ETL Cleaning Log",
        "source_file": "Source File",
        "raw_rows": "Raw Rows",
        "clean_rows": "Validated Rows",
        "ts_iso": "Timestamp ISO",
        "action": "Action",
        "column": "Column",
        "affected_rows": "Affected Rows",
        "detail": "Detail",
        "sigma_title": "Applied Uncertainty Matrix (sigma)",
        "mode": "Mode",
        "scale": "Scale",
        "value": "Value",
        "yes": "Yes",
        "no": "No",
        "chart_rec_title": "Reconciled Metallurgical Recovery (%)",
        "chart_chi2_title": "Chi-Square Statistical Control Chart (J* vs. Critical)",
        "feed_mass_rec": "Reconciled Feed Mass",
    },
}


def _clean_hex(hex_color: str, fallback: str = "1B4965") -> str:
    """Return uppercase 6-char hex RGB color without leading '#'."""
    cleaned = (hex_color or "").strip().lstrip("#").upper()
    if len(cleaned) == 3:
        cleaned = "".join(ch * 2 for ch in cleaned)
    if len(cleaned) != 6 or any(ch not in "0123456789ABCDEF" for ch in cleaned):
        return fallback
    return cleaned


def _thin_border() -> Border:
    side = Side(style="thin", color="CBD5E1")
    return Border(left=side, right=side, top=side, bottom=side)


def _header_styles(primary_hex: str) -> dict[str, Any]:
    return {
        "banner_fill": PatternFill("solid", fgColor=primary_hex),
        "banner_font": Font(name="Segoe UI", size=13, bold=True, color="FFFFFF"),
        "subbanner_font": Font(name="Segoe UI", size=9, italic=True, color="E2E8F0"),
        "section_fill": PatternFill("solid", fgColor="E9EEF2"),
        "section_font": Font(name="Segoe UI", size=10, bold=True, color="1E293B"),
        "tbl_header_fill": PatternFill("solid", fgColor=primary_hex),
        "tbl_header_font": Font(name="Segoe UI", size=9, bold=True, color="FFFFFF"),
        "text_font": Font(name="Segoe UI", size=9, color="1E293B"),
        "bold_font": Font(name="Segoe UI", size=9, bold=True, color="1E293B"),
        "mono_font": Font(name="Consolas", size=9, color="1E293B"),
        "mono_bold": Font(name="Consolas", size=9, bold=True, color="1E293B"),
        "zebra_fill": PatternFill("solid", fgColor="F8FAFC"),
        "total_fill": PatternFill("solid", fgColor="E9EEF2"),
        "pass_fill": PatternFill("solid", fgColor="DCFCE7"),
        "pass_font": Font(name="Segoe UI", size=9, bold=True, color="15803D"),
        "warn_fill": PatternFill("solid", fgColor="FEF3C7"),
        "warn_font": Font(name="Segoe UI", size=9, bold=True, color="B45309"),
        "fail_fill": PatternFill("solid", fgColor="FEE2E2"),
        "fail_font": Font(name="Segoe UI", size=9, bold=True, color="B91C1C"),
        "border": _thin_border(),
    }


def _apply_verdict_style(cell: Any, verdict: GEDVerdict, styles: dict[str, Any]) -> None:
    if verdict == GEDVerdict.PASS:
        cell.fill = styles["pass_fill"]
        cell.font = styles["pass_font"]
    elif verdict == GEDVerdict.WARNING:
        cell.fill = styles["warn_fill"]
        cell.font = styles["warn_font"]
    else:
        cell.fill = styles["fail_fill"]
        cell.font = styles["fail_font"]


def _autofit_columns(ws: Any, min_width: float = 10.0, max_width: float = 44.0) -> None:
    for col_cells in ws.columns:
        col_letter = get_column_letter(col_cells[0].column)
        longest = min_width
        for cell in col_cells:
            val = cell.value
            if val is None:
                continue
            text = str(val)
            if text.startswith("="):
                longest = max(longest, 13.0)
            else:
                longest = max(longest, min(len(text) + 3.0, max_width))
        ws.column_dimensions[col_letter].width = min(max(longest, min_width), max_width)


def _safe_sheet_title(title: str) -> str:
    cleaned = "".join("_" if ch in '[]:*?/\\' else ch for ch in title).strip()
    return (cleaned or "Sheet")[:31]


def _style_table_header(ws: Any, row: int, headers: list[str], styles: dict[str, Any]) -> None:
    for idx, text in enumerate(headers, start=1):
        cell = ws.cell(row=row, column=idx, value=text)
        cell.fill = styles["tbl_header_fill"]
        cell.font = styles["tbl_header_font"]
        cell.border = styles["border"]
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    ws.row_dimensions[row].height = 24


def export_to_excel(
    output_path: str | Path,
    circuit: CircuitSpec,
    dataset: CleanedDataset,
    uncertainty_model: UncertaintyModel,
    reconciliation_result: ReconciliationResult,
    kpi_report: KPIReport,
    branding: CorporateBrandingProfile,
    batch_result: Optional[BatchReconciliationResult] = None,
    outlier_comparison: Optional[OutlierComparisonResult] = None,
    language: str = "es",
) -> Path:
    """Export a multi-sheet executive Excel workbook with native formulas, charts, and logo."""
    lang = language if language in ("es", "en") else "es"
    i18n = I18nManager(default_language=lang)
    lbl = _BILINGUAL_LABELS[lang]
    primary_hex = _clean_hex(branding.primary_color)
    styles = _header_styles(primary_hex)

    target_path = Path(output_path)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    logo_path = resolve_corporate_logo(branding)

    wb = openpyxl.Workbook()

    # ------------------------------------------------------------------
    # Sheet 1: Resumen_Ejecutivo / Executive_Summary
    # ------------------------------------------------------------------
    ws_exec = wb.active
    ws_exec.title = _safe_sheet_title(i18n.t("report.sheet_exec_summary"))
    ws_exec.views.sheetView[0].showGridLines = True

    # Corporate header banner
    ws_exec.merge_cells("C1:I1")
    ws_exec["C1"] = i18n.t("report.pdf_title")
    ws_exec.merge_cells("C2:I2")
    ws_exec["C2"] = f"{branding.company_name} — {branding.mining_unit} | {i18n.t('report.pdf_subtitle')}"
    for r_idx in (1, 2, 3):
        ws_exec.row_dimensions[r_idx].height = 22
        for c_idx in range(1, 10):
            cell = ws_exec.cell(row=r_idx, column=c_idx)
            cell.fill = styles["banner_fill"]
    ws_exec["C1"].font = styles["banner_font"]
    ws_exec["C1"].alignment = Alignment(horizontal="left", vertical="center")
    ws_exec["C2"].font = styles["subbanner_font"]
    ws_exec["C2"].alignment = Alignment(horizontal="left", vertical="center")

    xl_logo = OpenpyxlImage(str(logo_path))
    xl_logo.width = 165
    xl_logo.height = 50
    ws_exec.add_image(xl_logo, "A1")

    # Section: Corporate & Traceability Metadata
    row = 5
    ws_exec.merge_cells(start_row=row, start_column=1, end_row=row, end_column=4)
    ws_exec.cell(row=row, column=1, value=lbl["meta_header"]).fill = styles["section_fill"]
    ws_exec.cell(row=row, column=1).font = styles["section_font"]

    ws_exec.merge_cells(start_row=row, start_column=6, end_row=row, end_column=9)
    ws_exec.cell(row=row, column=6, value=lbl["ged_header"]).fill = styles["section_fill"]
    ws_exec.cell(row=row, column=6).font = styles["section_font"]

    meta_pairs = [
        (lbl["company"], branding.company_name),
        (lbl["unit"], branding.mining_unit),
        (lbl["lead"], branding.technical_lead),
        (lbl["doc_code"], branding.document_code),
        (lbl["circuit"], circuit.name),
        (lbl["period"], reconciliation_result.period_id),
        (lbl["generated_at"], datetime.now(timezone.utc).isoformat()),
        (lbl["sha256"], dataset.file_sha256),
    ]
    ged = reconciliation_result.ged
    ged_pairs = [
        (i18n.t("step05.objective_j"), ged.objective_j, "0.0000"),
        (i18n.t("step05.chi2_critical"), ged.chi2_critical, "0.0000"),
        (lbl["dof"], ged.degrees_of_freedom, "0"),
        (i18n.t("step05.p_value"), ged.p_value, "0.0000"),
        (i18n.t("step05.z_critical"), ged.z_critical, "0.0000"),
        (lbl["verdict"], i18n.t(f"enums.ged_verdict.{ged.verdict.value}"), None),
        (lbl["max_z_var"], ged.max_abs_z_variable or "-", None),
        (lbl["max_z_val"], ged.max_abs_z_value, "0.0000"),
    ]

    for idx in range(len(meta_pairs)):
        r = row + 1 + idx
        k_lbl, v_val = meta_pairs[idx]
        c_k = ws_exec.cell(row=r, column=1, value=k_lbl)
        c_k.font = styles["bold_font"]
        c_k.border = styles["border"]
        ws_exec.merge_cells(start_row=r, start_column=2, end_row=r, end_column=4)
        c_v = ws_exec.cell(row=r, column=2, value=v_val)
        c_v.font = styles["mono_font"] if idx >= 6 else styles["text_font"]
        for c_i in range(2, 5):
            ws_exec.cell(row=r, column=c_i).border = styles["border"]

        gk_lbl, gv_val, g_fmt = ged_pairs[idx]
        ws_exec.merge_cells(start_row=r, start_column=6, end_row=r, end_column=7)
        c_gk = ws_exec.cell(row=r, column=6, value=gk_lbl)
        c_gk.font = styles["bold_font"]
        ws_exec.cell(row=r, column=6).border = styles["border"]
        ws_exec.cell(row=r, column=7).border = styles["border"]
        ws_exec.merge_cells(start_row=r, start_column=8, end_row=r, end_column=9)
        c_gv = ws_exec.cell(row=r, column=8, value=gv_val)
        c_gv.font = styles["mono_font"] if g_fmt else styles["bold_font"]
        if g_fmt:
            c_gv.number_format = g_fmt
            c_gv.alignment = Alignment(horizontal="right", vertical="center")
        if gk_lbl == lbl["verdict"]:
            _apply_verdict_style(c_gv, ged.verdict, styles)
        for c_i in range(8, 10):
            ws_exec.cell(row=r, column=c_i).border = styles["border"]

    # KPI Summary Table on Resumen_Ejecutivo (cross-sheet / native formulas)
    kpi_title_row = 16
    ws_exec.merge_cells(start_row=kpi_title_row, start_column=1, end_row=kpi_title_row, end_column=9)
    ws_exec.cell(row=kpi_title_row, column=1, value=lbl["kpi_header"]).fill = styles["section_fill"]
    ws_exec.cell(row=kpi_title_row, column=1).font = styles["section_font"]

    ws_exec.cell(row=17, column=1, value=lbl["feed_mass_rec"]).font = styles["bold_font"]
    c_feed_val = ws_exec.cell(row=17, column=2, value=kpi_report.feed_mass_rec)
    c_feed_val.font = styles["mono_bold"]
    c_feed_val.number_format = "0.00"
    ws_exec.cell(row=17, column=3, value=circuit.mass_unit.value).font = styles["text_font"]

    kpi_hdr_row = 19
    primary_sp = circuit.species[0]
    kpi_headers = [
        lbl["stream"],
        lbl["role"],
        lbl["mass_pull_nom"],
        lbl["mass_pull_rec"],
        lbl["conc_ratio_rec"],
        f"{lbl['fine_metal_rec']} ({primary_sp.id})",
        f"{lbl['recovery_nom']} ({primary_sp.id})",
        f"{lbl['recovery_rec']} ({primary_sp.id})",
    ]
    _style_table_header(ws_exec, kpi_hdr_row, kpi_headers, styles)

    kpi_start = kpi_hdr_row + 1
    for idx, prod in enumerate(kpi_report.products):
        r = kpi_start + idx
        fill = styles["zebra_fill"] if idx % 2 == 1 else PatternFill(fill_type=None)
        vals = [
            prod.stream_name,
            i18n.t(f"enums.stream_role.{prod.role.value}"),
            prod.mass_pull_nom_pct,
            prod.mass_pull_rec_pct,
            prod.conc_ratio_rec,
            prod.fine_metal_rec.get(primary_sp.id, 0.0),
            prod.recovery_nom_pct.get(primary_sp.id),
            prod.recovery_rec_pct.get(primary_sp.id, 0.0),
        ]
        fmts = [None, None, "0.00", "0.00", "0.00", "0.0000", "0.00", "0.00"]
        for c_i, (val, fmt) in enumerate(zip(vals, fmts), start=1):
            cell = ws_exec.cell(row=r, column=c_i, value=val)
            cell.border = styles["border"]
            if fill.fill_type:
                cell.fill = fill
            if fmt is not None:
                cell.font = styles["mono_font"]
                cell.number_format = fmt
                cell.alignment = Alignment(horizontal="right", vertical="center")
            else:
                cell.font = styles["text_font"]

    kpi_end = kpi_start + len(kpi_report.products) - 1
    kpi_tot_row = kpi_end + 1
    ws_exec.cell(row=kpi_tot_row, column=1, value=lbl["totals"]).font = styles["bold_font"]
    ws_exec.cell(row=kpi_tot_row, column=4, value=f"=SUM(D{kpi_start}:D{kpi_end})").number_format = "0.00"
    ws_exec.cell(row=kpi_tot_row, column=6, value=f"=SUM(F{kpi_start}:F{kpi_end})").number_format = "0.0000"
    ws_exec.cell(row=kpi_tot_row, column=8, value=f"=SUM(H{kpi_start}:H{kpi_end})").number_format = "0.00"
    for c_i in range(1, 9):
        c = ws_exec.cell(row=kpi_tot_row, column=c_i)
        c.fill = styles["total_fill"]
        c.border = styles["border"]
        if c_i in (4, 6, 8):
            c.font = styles["mono_bold"]
            c.alignment = Alignment(horizontal="right", vertical="center")

    # Native Excel BarChart on Resumen_Ejecutivo
    if kpi_report.products:
        bar = BarChart()
        bar.type = "col"
        bar.style = 10
        bar.title = f"{lbl['chart_rec_title']} — {primary_sp.id}"
        bar.y_axis.title = "%"
        data_ref = Reference(ws_exec, min_col=8, min_row=kpi_hdr_row, max_row=kpi_end)
        cats_ref = Reference(ws_exec, min_col=1, min_row=kpi_start, max_row=kpi_end)
        bar.add_data(data_ref, titles_from_data=True)
        bar.set_categories(cats_ref)
        bar.legend = None
        bar.width = 16
        bar.height = 9
        ws_exec.add_chart(bar, f"A{kpi_tot_row + 2}")

    # ------------------------------------------------------------------
    # Sheet 2: Balance_Conciliado / Reconciled_Balance (Native Formulas)
    # ------------------------------------------------------------------
    ws_bal = wb.create_sheet(title=_safe_sheet_title(i18n.t("report.sheet_balance")))
    ws_bal.merge_cells("A1:J1")
    ws_bal["A1"] = f"{lbl['balance_table_title']} — {reconciliation_result.period_id}"
    ws_bal["A1"].fill = styles["section_fill"]
    ws_bal["A1"].font = styles["section_font"]
    ws_bal.row_dimensions[1].height = 22

    bal_headers = [
        lbl["var_key"],
        lbl["stream"],
        lbl["species"],
        lbl["measured"],
        lbl["reconciled"],
        lbl["abs_diff"],
        lbl["rel_pct"],
        lbl["unit_col"],
        lbl["sigma"],
        lbl["classification"],
    ]
    _style_table_header(ws_bal, 3, bal_headers, styles)
    ws_bal.freeze_panes = "A4"

    bal_row = 4
    for idx, adj in enumerate(reconciliation_result.adjustments):
        fill = styles["zebra_fill"] if idx % 2 == 1 else PatternFill(fill_type=None)
        ws_bal.cell(row=bal_row, column=1, value=adj.variable_key).font = styles["mono_font"]
        ws_bal.cell(row=bal_row, column=2, value=adj.stream_id).font = styles["text_font"]
        ws_bal.cell(row=bal_row, column=3, value=adj.species_id or lbl["mass_flow"]).font = styles["text_font"]

        c_meas = ws_bal.cell(row=bal_row, column=4, value=adj.measured)
        c_rec = ws_bal.cell(row=bal_row, column=5, value=adj.reconciled)
        c_diff = ws_bal.cell(
            row=bal_row,
            column=6,
            value=f'=IF(ISNUMBER(D{bal_row}),E{bal_row}-D{bal_row},"")',
        )
        c_pct = ws_bal.cell(
            row=bal_row,
            column=7,
            value=f'=IF(AND(ISNUMBER(D{bal_row}),D{bal_row}<>0),(E{bal_row}-D{bal_row})/D{bal_row}*100,"")',
        )
        c_unit = ws_bal.cell(row=bal_row, column=8, value=adj.unit)
        c_sig = ws_bal.cell(row=bal_row, column=9, value=adj.sigma)
        c_cls = ws_bal.cell(
            row=bal_row,
            column=10,
            value=i18n.t(f"enums.variable_class.{adj.classification.value}"),
        )

        for col_i, fmt in [(4, "0.0000"), (5, "0.0000"), (6, "0.0000"), (7, "0.00"), (9, "0.0000")]:
            cell = ws_bal.cell(row=bal_row, column=col_i)
            cell.font = styles["mono_font"]
            cell.number_format = fmt
            cell.alignment = Alignment(horizontal="right", vertical="center")
        c_unit.font = styles["text_font"]
        c_unit.alignment = Alignment(horizontal="center", vertical="center")
        c_cls.font = styles["text_font"]

        for c_i in range(1, 11):
            cell = ws_bal.cell(row=bal_row, column=c_i)
            cell.border = styles["border"]
            if fill.fill_type:
                cell.fill = fill
        bal_row += 1

    # Table 2 on Sheet 2: Fine Metal and Metallurgical Recovery with Native Formulas
    fine_title_row = bal_row + 2
    ws_bal.merge_cells(start_row=fine_title_row, start_column=1, end_row=fine_title_row, end_column=10)
    ws_bal.cell(row=fine_title_row, column=1, value=lbl["fine_table_title"]).fill = styles["section_fill"]
    ws_bal.cell(row=fine_title_row, column=1).font = styles["section_font"]
    ws_bal.row_dimensions[fine_title_row].height = 22

    fine_hdr_row = fine_title_row + 1
    fine_headers = [
        lbl["stream"],
        lbl["role"],
        lbl["species"],
        lbl["unit_col"],
        f"{lbl['mass_flow']} ({circuit.mass_unit.value})",
        lbl["assay"],
        lbl["factor"],
        lbl["fine_metal_rec"],
        lbl["mass_pull_rec"],
        lbl["recovery_rec"],
    ]
    _style_table_header(ws_bal, fine_hdr_row, fine_headers, styles)

    feed_streams = [s for s in circuit.streams if s.role.value == "feed"] or [circuit.streams[0]]
    product_streams = [s for s in circuit.streams if s.role.value in ("concentrate", "tailings")]

    cur_row = fine_hdr_row + 1
    chart_start_row = None
    chart_end_row = None
    for sp_idx, sp in enumerate(circuit.species):
        feed_stream = feed_streams[0]
        feed_row = cur_row
        m_feed = float(reconciliation_result.reconciled_mass_flows[feed_stream.id])
        x_feed = float(reconciliation_result.reconciled_assays[feed_stream.id][sp.id])

        ws_bal.cell(row=feed_row, column=1, value=feed_stream.name).font = styles["bold_font"]
        ws_bal.cell(row=feed_row, column=2, value=i18n.t(f"enums.stream_role.{feed_stream.role.value}")).font = styles["text_font"]
        ws_bal.cell(row=feed_row, column=3, value=sp.id).font = styles["bold_font"]
        ws_bal.cell(row=feed_row, column=4, value=sp.unit.value).font = styles["text_font"]
        ws_bal.cell(row=feed_row, column=5, value=m_feed).number_format = "0.000"
        ws_bal.cell(row=feed_row, column=6, value=x_feed).number_format = "0.0000"
        ws_bal.cell(row=feed_row, column=7, value=sp.to_fraction_factor()).number_format = "0.00E+00"
        ws_bal.cell(row=feed_row, column=8, value=f"=E{feed_row}*F{feed_row}*G{feed_row}").number_format = "0.0000"
        ws_bal.cell(row=feed_row, column=9, value=f"=E{feed_row}/$E${feed_row}*100").number_format = "0.00"
        ws_bal.cell(row=feed_row, column=10, value=f"=IF($H${feed_row}=0,0,H{feed_row}/$H${feed_row}*100)").number_format = "0.00"
        for c_i in range(1, 11):
            cell = ws_bal.cell(row=feed_row, column=c_i)
            cell.border = styles["border"]
            if c_i >= 5:
                cell.font = styles["mono_bold"]
                cell.alignment = Alignment(horizontal="right", vertical="center")
        cur_row += 1

        prod_start = cur_row
        for prod in product_streams:
            m_prod = float(reconciliation_result.reconciled_mass_flows[prod.id])
            x_prod = float(reconciliation_result.reconciled_assays[prod.id][sp.id])
            ws_bal.cell(row=cur_row, column=1, value=prod.name).font = styles["text_font"]
            ws_bal.cell(row=cur_row, column=2, value=i18n.t(f"enums.stream_role.{prod.role.value}")).font = styles["text_font"]
            ws_bal.cell(row=cur_row, column=3, value=sp.id).font = styles["text_font"]
            ws_bal.cell(row=cur_row, column=4, value=sp.unit.value).font = styles["text_font"]
            ws_bal.cell(row=cur_row, column=5, value=m_prod).number_format = "0.000"
            ws_bal.cell(row=cur_row, column=6, value=x_prod).number_format = "0.0000"
            ws_bal.cell(row=cur_row, column=7, value=sp.to_fraction_factor()).number_format = "0.00E+00"
            ws_bal.cell(row=cur_row, column=8, value=f"=E{cur_row}*F{cur_row}*G{cur_row}").number_format = "0.0000"
            ws_bal.cell(row=cur_row, column=9, value=f"=E{cur_row}/$E${feed_row}*100").number_format = "0.00"
            ws_bal.cell(row=cur_row, column=10, value=f"=IF($H${feed_row}=0,0,H{cur_row}/$H${feed_row}*100)").number_format = "0.00"
            for c_i in range(1, 11):
                cell = ws_bal.cell(row=cur_row, column=c_i)
                cell.border = styles["border"]
                if c_i >= 5:
                    cell.font = styles["mono_font"]
                    cell.alignment = Alignment(horizontal="right", vertical="center")
            cur_row += 1
        prod_end = cur_row - 1
        if sp_idx == 0 and prod_end >= prod_start:
            chart_start_row = prod_start
            chart_end_row = prod_end

        # Totals row using =SUM(...)
        tot_row = cur_row
        ws_bal.cell(row=tot_row, column=1, value=f"{lbl['totals']} ({sp.id})").font = styles["bold_font"]
        ws_bal.cell(row=tot_row, column=5, value=f"=SUM(E{prod_start}:E{prod_end})").number_format = "0.000"
        ws_bal.cell(row=tot_row, column=8, value=f"=SUM(H{prod_start}:H{prod_end})").number_format = "0.0000"
        ws_bal.cell(row=tot_row, column=9, value=f"=SUM(I{prod_start}:I{prod_end})").number_format = "0.00"
        ws_bal.cell(row=tot_row, column=10, value=f"=SUM(J{prod_start}:J{prod_end})").number_format = "0.00"
        for c_i in range(1, 11):
            c = ws_bal.cell(row=tot_row, column=c_i)
            c.fill = styles["total_fill"]
            c.border = styles["border"]
            if c_i >= 5:
                c.font = styles["mono_bold"]
                c.alignment = Alignment(horizontal="right", vertical="center")
        cur_row += 1

        # Unexplained difference row
        unexp_row = cur_row
        ws_bal.cell(row=unexp_row, column=1, value=f"{lbl['unexplained']} ({sp.id})").font = styles["text_font"]
        ws_bal.cell(row=unexp_row, column=5, value=f"=E{feed_row}-E{tot_row}").number_format = "0.000000"
        ws_bal.cell(row=unexp_row, column=8, value=f"=H{feed_row}-H{tot_row}").number_format = "0.000000"
        for c_i in range(1, 11):
            c = ws_bal.cell(row=unexp_row, column=c_i)
            c.border = styles["border"]
            if c_i in (5, 8):
                c.font = styles["mono_font"]
                c.alignment = Alignment(horizontal="right", vertical="center")
        cur_row += 1

    if chart_start_row is not None and chart_end_row is not None:
        bal_chart = BarChart()
        bal_chart.type = "col"
        bal_chart.style = 10
        bal_chart.title = f"{lbl['chart_rec_title']} ({circuit.species[0].id})"
        bal_chart.y_axis.title = "%"
        data_ref = Reference(ws_bal, min_col=10, min_row=chart_start_row, max_row=chart_end_row)
        cats_ref = Reference(ws_bal, min_col=1, min_row=chart_start_row, max_row=chart_end_row)
        bal_chart.add_data(data_ref, titles_from_data=False)
        bal_chart.set_categories(cats_ref)
        bal_chart.legend = None
        bal_chart.width = 15
        bal_chart.height = 8
        ws_bal.add_chart(bal_chart, "L3")

    # ------------------------------------------------------------------
    # Sheet 3: Diagnostico_GED / GED_Diagnostics (Conditional Formatting)
    # ------------------------------------------------------------------
    ws_ged = wb.create_sheet(title=_safe_sheet_title(i18n.t("report.sheet_ged")))
    ws_ged.merge_cells("A1:K1")
    ws_ged["A1"] = f"{lbl['ged_header']} — {reconciliation_result.period_id}"
    ws_ged["A1"].fill = styles["section_fill"]
    ws_ged["A1"].font = styles["section_font"]
    ws_ged.row_dimensions[1].height = 22

    ged_summary = [
        (i18n.t("step05.objective_j"), ged.objective_j, "0.0000"),
        (i18n.t("step05.chi2_critical"), ged.chi2_critical, "0.0000"),
        (lbl["dof"], ged.degrees_of_freedom, "0"),
        (lbl["alpha"], ged.alpha, "0.000"),
        (i18n.t("step05.p_value"), ged.p_value, "0.0000"),
        (i18n.t("step05.z_critical"), ged.z_critical, "0.0000"),
        (lbl["verdict"], i18n.t(f"enums.ged_verdict.{ged.verdict.value}"), None),
        (lbl["max_z_var"], ged.max_abs_z_variable or "-", None),
        (lbl["max_z_val"], ged.max_abs_z_value, "0.0000"),
    ]
    for idx, (k, v, fmt) in enumerate(ged_summary, start=3):
        ws_ged.cell(row=idx, column=1, value=k).font = styles["bold_font"]
        ws_ged.cell(row=idx, column=1).border = styles["border"]
        c_v = ws_ged.cell(row=idx, column=2, value=v)
        c_v.border = styles["border"]
        if fmt:
            c_v.font = styles["mono_font"]
            c_v.number_format = fmt
            c_v.alignment = Alignment(horizontal="right", vertical="center")
        else:
            c_v.font = styles["bold_font"]
        if k == lbl["verdict"]:
            _apply_verdict_style(c_v, ged.verdict, styles)

    # Outlier comparison block if present
    z_hdr_row = 14
    if outlier_comparison is not None:
        ws_ged.cell(row=3, column=4, value=lbl["outlier_cmp_title"]).font = styles["section_font"]
        cmp_rows = [
            (lbl["with_outlier_j"], outlier_comparison.with_outlier.ged.objective_j, "0.0000"),
            (lbl["without_outlier_j"], outlier_comparison.without_outlier.ged.objective_j, "0.0000"),
            (lbl["j_reduction"], "=E4-E5", "0.0000"),
            (lbl["verdict_improved"], lbl["yes"] if outlier_comparison.verdict_improved else lbl["no"], None),
            (lbl["excluded_vars"], ", ".join(outlier_comparison.excluded_variables), None),
        ]
        for idx, (k, v, fmt) in enumerate(cmp_rows, start=4):
            ws_ged.cell(row=idx, column=4, value=k).font = styles["bold_font"]
            ws_ged.cell(row=idx, column=4).border = styles["border"]
            cv = ws_ged.cell(row=idx, column=5, value=v)
            cv.border = styles["border"]
            if fmt:
                cv.font = styles["mono_font"]
                cv.number_format = fmt
                cv.alignment = Alignment(horizontal="right", vertical="center")
            else:
                cv.font = styles["text_font"]

    ged_headers = [
        lbl["var_key"],
        lbl["stream"],
        lbl["species"],
        lbl["unit_col"],
        lbl["measured"],
        lbl["reconciled"],
        lbl["sigma"],
        lbl["z_score"],
        lbl["abs_z"],
        lbl["outlier"],
        lbl["status"],
    ]
    _style_table_header(ws_ged, z_hdr_row, ged_headers, styles)
    ws_ged.freeze_panes = f"A{z_hdr_row + 1}"

    z_start = z_hdr_row + 1
    for idx, adj in enumerate(reconciliation_result.adjustments):
        r = z_start + idx
        fill = styles["zebra_fill"] if idx % 2 == 1 else PatternFill(fill_type=None)
        ws_ged.cell(row=r, column=1, value=adj.variable_key).font = styles["mono_font"]
        ws_ged.cell(row=r, column=2, value=adj.stream_id).font = styles["text_font"]
        ws_ged.cell(row=r, column=3, value=adj.species_id or lbl["mass_flow"]).font = styles["text_font"]
        ws_ged.cell(row=r, column=4, value=adj.unit).font = styles["text_font"]
        ws_ged.cell(row=r, column=5, value=adj.measured).number_format = "0.0000"
        ws_ged.cell(row=r, column=6, value=adj.reconciled).number_format = "0.0000"
        ws_ged.cell(row=r, column=7, value=adj.sigma).number_format = "0.0000"
        ws_ged.cell(row=r, column=8, value=adj.z_score).number_format = "0.0000"
        ws_ged.cell(row=r, column=9, value=f'=IF(ISNUMBER(H{r}),ABS(H{r}),"")').number_format = "0.0000"
        ws_ged.cell(row=r, column=10, value=lbl["yes"] if adj.is_outlier else lbl["no"]).font = styles["text_font"]
        ws_ged.cell(
            row=r,
            column=11,
            value=f'=IF(NOT(ISNUMBER(I{r})),"UNMEASURED",IF(I{r}>{ged.z_critical:.6f},"FAIL",IF(I{r}>2.0,"WARNING","PASS")))',
        ).font = styles["bold_font"]

        for c_i in range(1, 12):
            cell = ws_ged.cell(row=r, column=c_i)
            cell.border = styles["border"]
            if fill.fill_type:
                cell.fill = fill
            if c_i in (5, 6, 7, 8, 9):
                cell.font = styles["mono_font"]
                cell.alignment = Alignment(horizontal="right", vertical="center")

    z_end = z_start + max(len(reconciliation_result.adjustments) - 1, 0)
    abs_z_range = f"I{z_start}:I{z_end}"
    ws_ged.conditional_formatting.add(
        abs_z_range,
        CellIsRule(operator="greaterThan", formula=[f"{ged.z_critical:.6f}"], fill=styles["fail_fill"], font=styles["fail_font"]),
    )
    ws_ged.conditional_formatting.add(
        abs_z_range,
        CellIsRule(operator="between", formula=["2.000001", f"{ged.z_critical:.6f}"], fill=styles["warn_fill"], font=styles["warn_font"]),
    )
    ws_ged.conditional_formatting.add(
        abs_z_range,
        CellIsRule(operator="between", formula=["0", "2.0"], fill=styles["pass_fill"], font=styles["pass_font"]),
    )

    # ------------------------------------------------------------------
    # Sheet 4: Serie_Historica / Historical_Series
    # ------------------------------------------------------------------
    ws_hist = wb.create_sheet(title=_safe_sheet_title(i18n.t("report.sheet_history")))
    ws_hist.merge_cells("A1:L1")
    ws_hist["A1"] = i18n.t("step05.section_batch")
    ws_hist["A1"].fill = styles["section_fill"]
    ws_hist["A1"].font = styles["section_font"]
    ws_hist.row_dimensions[1].height = 22

    hist_headers = [
        lbl["period"],
        lbl["converged"],
        lbl["solver"],
        lbl["iterations"],
        lbl["max_mass_res"],
        lbl["max_sp_res"],
        i18n.t("step05.objective_j"),
        i18n.t("step05.chi2_critical"),
        i18n.t("step05.p_value"),
        lbl["verdict"],
        lbl["max_z_var"],
        lbl["max_z_val"],
    ]
    _style_table_header(ws_hist, 3, hist_headers, styles)
    ws_hist.freeze_panes = "A4"

    series_results = batch_result.results if batch_result is not None else [reconciliation_result]
    h_start = 4
    for idx, res in enumerate(series_results):
        r = h_start + idx
        fill = styles["zebra_fill"] if idx % 2 == 1 else PatternFill(fill_type=None)
        ws_hist.cell(row=r, column=1, value=res.period_id).font = styles["bold_font"]
        ws_hist.cell(row=r, column=2, value=lbl["yes"] if res.converged else lbl["no"]).font = styles["text_font"]
        ws_hist.cell(row=r, column=3, value=res.solver_used).font = styles["text_font"]
        ws_hist.cell(row=r, column=4, value=res.iterations).number_format = "0"
        ws_hist.cell(row=r, column=5, value=res.max_mass_residual).number_format = "0.00E+00"
        ws_hist.cell(row=r, column=6, value=res.max_species_residual).number_format = "0.00E+00"
        ws_hist.cell(row=r, column=7, value=res.ged.objective_j).number_format = "0.0000"
        ws_hist.cell(row=r, column=8, value=res.ged.chi2_critical).number_format = "0.0000"
        ws_hist.cell(row=r, column=9, value=res.ged.p_value).number_format = "0.0000"
        c_verd = ws_hist.cell(row=r, column=10, value=res.ged.verdict.value.upper())
        ws_hist.cell(row=r, column=11, value=res.ged.max_abs_z_variable or "-").font = styles["mono_font"]
        ws_hist.cell(row=r, column=12, value=res.ged.max_abs_z_value).number_format = "0.0000"

        for c_i in range(1, 13):
            cell = ws_hist.cell(row=r, column=c_i)
            cell.border = styles["border"]
            if fill.fill_type and c_i != 10:
                cell.fill = fill
            if c_i in (4, 5, 6, 7, 8, 9, 12):
                cell.font = styles["mono_font"]
                cell.alignment = Alignment(horizontal="right", vertical="center")
        _apply_verdict_style(c_verd, res.ged.verdict, styles)

    h_end = h_start + len(series_results) - 1
    mean_row = h_end + 1
    ws_hist.cell(row=mean_row, column=1, value=lbl["mean_j"]).font = styles["bold_font"]
    ws_hist.cell(row=mean_row, column=7, value=f"=AVERAGE(G{h_start}:G{h_end})").number_format = "0.0000"
    ws_hist.cell(row=mean_row, column=8, value=f"=AVERAGE(H{h_start}:H{h_end})").number_format = "0.0000"
    for c_i in range(1, 13):
        c = ws_hist.cell(row=mean_row, column=c_i)
        c.fill = styles["total_fill"]
        c.border = styles["border"]
        if c_i in (7, 8):
            c.font = styles["mono_bold"]
            c.alignment = Alignment(horizontal="right", vertical="center")

    # Summary counts using COUNTIF
    for offset, (label_txt, status_code) in enumerate(
        [(lbl["count_pass"], "PASS"), (lbl["count_warn"], "WARNING"), (lbl["count_fail"], "FAIL")],
        start=2,
    ):
        sr = mean_row + offset
        ws_hist.cell(row=sr, column=1, value=label_txt).font = styles["bold_font"]
        ws_hist.cell(row=sr, column=1).border = styles["border"]
        c_cnt = ws_hist.cell(row=sr, column=2, value=f'=COUNTIF(J{h_start}:J{h_end},"{status_code}")')
        c_cnt.font = styles["mono_bold"]
        c_cnt.border = styles["border"]
        c_cnt.number_format = "0"

    # LineChart on Serie_Historica
    line = LineChart()
    line.title = lbl["chart_chi2_title"]
    line.style = 10
    line.y_axis.title = "Chi2"
    line.x_axis.title = lbl["period"]
    data_ref = Reference(ws_hist, min_col=7, max_col=8, min_row=3, max_row=h_end)
    cats_ref = Reference(ws_hist, min_col=1, min_row=h_start, max_row=h_end)
    line.add_data(data_ref, titles_from_data=True)
    line.set_categories(cats_ref)
    line.width = 18
    line.height = 9
    ws_hist.add_chart(line, f"D{mean_row + 2}")

    # ------------------------------------------------------------------
    # Sheet 5: Datos_Auditados / Audited_Data
    # ------------------------------------------------------------------
    ws_aud = wb.create_sheet(title=_safe_sheet_title(i18n.t("report.sheet_audit")))
    ws_aud.merge_cells("A1:E1")
    ws_aud["A1"] = lbl["audit_title"]
    ws_aud["A1"].fill = styles["section_fill"]
    ws_aud["A1"].font = styles["section_font"]
    ws_aud.row_dimensions[1].height = 22

    audit_meta = [
        (lbl["source_file"], dataset.source_path),
        (lbl["sha256"], dataset.file_sha256),
        (lbl["raw_rows"], dataset.raw_row_count),
        (lbl["clean_rows"], dataset.clean_row_count),
    ]
    for idx, (k, v) in enumerate(audit_meta, start=3):
        ws_aud.cell(row=idx, column=1, value=k).font = styles["bold_font"]
        ws_aud.cell(row=idx, column=1).border = styles["border"]
        ws_aud.merge_cells(start_row=idx, start_column=2, end_row=idx, end_column=5)
        cv = ws_aud.cell(row=idx, column=2, value=v)
        cv.font = styles["mono_font"]
        for c_i in range(2, 6):
            ws_aud.cell(row=idx, column=c_i).border = styles["border"]

    aud_hdr_row = 8
    _style_table_header(
        ws_aud,
        aud_hdr_row,
        [lbl["ts_iso"], lbl["action"], lbl["column"], lbl["affected_rows"], lbl["detail"]],
        styles,
    )
    ws_aud.freeze_panes = "A9"

    cur_aud = aud_hdr_row + 1
    for idx, entry in enumerate(dataset.audit_log):
        fill = styles["zebra_fill"] if idx % 2 == 1 else PatternFill(fill_type=None)
        ws_aud.cell(row=cur_aud, column=1, value=entry.timestamp_iso).font = styles["mono_font"]
        ws_aud.cell(row=cur_aud, column=2, value=entry.action).font = styles["bold_font"]
        ws_aud.cell(row=cur_aud, column=3, value=entry.column or "-").font = styles["text_font"]
        c_rows = ws_aud.cell(row=cur_aud, column=4, value=entry.affected_rows)
        c_rows.font = styles["mono_font"]
        c_rows.number_format = "0"
        c_rows.alignment = Alignment(horizontal="right", vertical="center")
        ws_aud.cell(row=cur_aud, column=5, value=entry.detail).font = styles["text_font"]
        for c_i in range(1, 6):
            cell = ws_aud.cell(row=cur_aud, column=c_i)
            cell.border = styles["border"]
            if fill.fill_type:
                cell.fill = fill
        cur_aud += 1

    # Applied Uncertainty Model Matrix below Audit Log
    sig_title_row = cur_aud + 2
    ws_aud.merge_cells(start_row=sig_title_row, start_column=1, end_row=sig_title_row, end_column=6)
    ws_aud.cell(row=sig_title_row, column=1, value=lbl["sigma_title"]).fill = styles["section_fill"]
    ws_aud.cell(row=sig_title_row, column=1).font = styles["section_font"]

    sig_hdr_row = sig_title_row + 1
    _style_table_header(
        ws_aud,
        sig_hdr_row,
        [lbl["var_key"], lbl["stream"], lbl["species"], lbl["mode"], lbl["scale"], lbl["value"]],
        styles,
    )
    cur_sig = sig_hdr_row + 1
    for key in sorted(uncertainty_model.specs):
        sp_obj = uncertainty_model.specs[key]
        ws_aud.cell(row=cur_sig, column=1, value=sp_obj.variable_key).font = styles["mono_font"]
        ws_aud.cell(row=cur_sig, column=2, value=sp_obj.stream_id).font = styles["text_font"]
        ws_aud.cell(row=cur_sig, column=3, value=sp_obj.species_id or lbl["mass_flow"]).font = styles["text_font"]
        ws_aud.cell(row=cur_sig, column=4, value=i18n.t(f"enums.uncertainty_mode.{sp_obj.mode.value}")).font = styles["text_font"]
        ws_aud.cell(row=cur_sig, column=5, value=i18n.t(f"enums.uncertainty_scale.{sp_obj.scale.value}")).font = styles["text_font"]
        c_val = ws_aud.cell(row=cur_sig, column=6, value=sp_obj.value)
        c_val.font = styles["mono_font"]
        c_val.number_format = "0.0000"
        c_val.alignment = Alignment(horizontal="right", vertical="center")
        for c_i in range(1, 7):
            ws_aud.cell(row=cur_sig, column=c_i).border = styles["border"]
        cur_sig += 1

    for ws in (ws_exec, ws_bal, ws_ged, ws_hist, ws_aud):
        _autofit_columns(ws)

    wb.save(target_path)
    return target_path
