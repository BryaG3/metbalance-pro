"""ReportLab A4 auditable technical PDF report exporter for MetBalance Pro.

This module belongs to the headless reporting layer and MUST NEVER import PySide6.
All diagnostic charts are rendered at 300 DPI using Matplotlib's headless Agg backend.
"""

from __future__ import annotations

import io
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from xml.sax.saxutils import escape as xml_escape

import numpy as np
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from PIL import Image as PILImage
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.platypus import (
    Image as RLImage,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

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
from core.topology import build_incidence_matrix
from reporting.branding import resolve_corporate_logo

_PDF_TEXT: dict[str, dict[str, str]] = {
    "es": {
        "company": "Empresa",
        "unit": "Unidad Minera",
        "lead": "Responsable Técnico",
        "doc_code": "Código Documento",
        "circuit": "Circuito",
        "period": "Período / Guardia",
        "mass_unit": "Unidad Másica",
        "nodes_streams_species": "Nodos / Corrientes / Especies",
        "node": "Nodo",
        "stream": "Corriente",
        "role": "Rol",
        "species": "Especie",
        "var": "Variable",
        "unit_col": "Unidad",
        "measured": "Medido",
        "reconciled": "Reconciliado",
        "abs_diff": "Dif. Abs.",
        "rel_pct": "Ajuste (%)",
        "sigma": "Sigma",
        "z_score": "z-score",
        "classification": "Clasificación",
        "mass_pull_nom": "Pull Nom (%)",
        "mass_pull_rec": "Pull Rec (%)",
        "conc_ratio": "Razón Conc.",
        "fine_rec": "Fino Rec.",
        "rec_nom": "Rec. Nom (%)",
        "rec_rec": "Rec. Rec (%)",
        "feed_mass": "Alimentación (Nom. / Rec.)",
        "unexplained": "Diferencia No Explicada (Nom. / Rec.)",
        "verdict": "Veredicto Global",
        "suspects": "Variables Sospechosas",
        "no_suspects": "No se detectaron errores groseros estadísticamente significativos.",
        "outlier_table": "Comparación Con vs. Sin Outlier",
        "metric": "Métrica",
        "value": "Valor",
        "with_outlier": "Con Outlier",
        "without_outlier": "Sin Outlier",
        "j_reduction": "Reducción en J*",
        "verdict_improved": "Mejora de Veredicto",
        "excluded": "Variables Excluidas",
        "solver": "Solver / Iteraciones",
        "residuals": "Residuales Máx. (Masa / Especies)",
        "etl_rows": "Registros ETL (Crudos / Limpios)",
        "mode": "Modo",
        "scale": "Escala",
        "page_of": "Página {page} de {total}",
        "yes": "Sí",
        "no": "No",
        "mass_flow_lbl": "Flujo Másico",
        "none_txt": "Ninguna",
        "description": "Descripción",
        "verdict_skipped": "Omitido (Sin Flujo)",
    },
    "en": {
        "company": "Company",
        "unit": "Mining Unit",
        "lead": "Technical Lead",
        "doc_code": "Document Code",
        "circuit": "Circuit",
        "period": "Period / Shift",
        "mass_unit": "Mass Unit",
        "nodes_streams_species": "Nodes / Streams / Species",
        "node": "Node",
        "stream": "Stream",
        "role": "Role",
        "species": "Species",
        "var": "Variable",
        "unit_col": "Unit",
        "measured": "Measured",
        "reconciled": "Reconciled",
        "abs_diff": "Abs. Diff.",
        "rel_pct": "Adj. (%)",
        "sigma": "Sigma",
        "z_score": "z-score",
        "classification": "Classification",
        "mass_pull_nom": "Nom. Pull (%)",
        "mass_pull_rec": "Rec. Pull (%)",
        "conc_ratio": "Conc. Ratio",
        "fine_rec": "Rec. Fine",
        "rec_nom": "Nom. Rec (%)",
        "rec_rec": "Rec. Rec (%)",
        "feed_mass": "Feed Mass (Nom. / Rec.)",
        "unexplained": "Unexplained Mass (Nom. / Rec.)",
        "verdict": "Global Verdict",
        "suspects": "Suspect Variables",
        "no_suspects": "No statistically significant gross errors detected.",
        "outlier_table": "With vs. Without Outlier Comparison",
        "metric": "Metric",
        "value": "Value",
        "with_outlier": "With Outlier",
        "without_outlier": "Without Outlier",
        "j_reduction": "Reduction in J*",
        "verdict_improved": "Verdict Improved",
        "excluded": "Excluded Variables",
        "solver": "Solver / Iterations",
        "residuals": "Max Residuals (Mass / Species)",
        "etl_rows": "ETL Rows (Raw / Validated)",
        "mode": "Mode",
        "scale": "Scale",
        "page_of": "Page {page} of {total}",
        "yes": "Yes",
        "no": "No",
        "mass_flow_lbl": "Mass Flow",
        "none_txt": "None",
        "description": "Description",
        "verdict_skipped": "Skipped (No Flow)",
    },
}


def _rl_color(hex_color: str, fallback: str = "#1B4965") -> colors.Color:
    cleaned = (hex_color or "").strip()
    if not cleaned.startswith("#"):
        cleaned = f"#{cleaned}"
    try:
        return colors.HexColor(cleaned)
    except Exception:
        return colors.HexColor(fallback)


class _NumberedCanvas(rl_canvas.Canvas):
    """Canvas subclass that renders running Page X of Y corporate footers."""

    def __init__(self, *args: Any, footer_callback: Any = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._saved_page_states: list[dict[str, Any]] = []
        self._footer_callback = footer_callback

    def showPage(self) -> None:
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self) -> None:
        total_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            if self._footer_callback is not None:
                self._footer_callback(self, self._pageNumber, total_pages)
            super().showPage()
        super().save()


def _fmt_num(val: Optional[float], decimals: int = 4) -> str:
    if val is None:
        return "-"
    return f"{val:.{decimals}f}"


def _is_skipped_result(res: ReconciliationResult) -> bool:
    """Return True if a reconciliation result was skipped due to zero/no flow."""
    status = str(getattr(res, "status", "OK") or "OK").upper()
    if status == "SKIPPED_NO_FLOW":
        return True
    verdict_val = getattr(res.ged.verdict, "value", str(res.ged.verdict))
    return str(verdict_val).lower() == "skipped"


def _verdict_display_text(
    i18n: I18nManager,
    verdict: Any,
    txt: dict[str, str],
    is_skipped: bool = False,
) -> str:
    """Return localized verdict text, handling SKIPPED defensively."""
    verdict_val = "skipped" if is_skipped else str(getattr(verdict, "value", verdict)).lower()
    key = f"enums.ged_verdict.{verdict_val}"
    translated = i18n.t(key)
    if translated == key and verdict_val == "skipped":
        return txt["verdict_skipped"]
    return translated


def _p(text: Any, style: ParagraphStyle) -> Paragraph:
    """Wrap arbitrary text safely in a ReportLab Paragraph for clean column wrapping."""
    return Paragraph(xml_escape(str(text)), style)


def _rl_image_from_png_buf(buf: io.BytesIO, target_width: float, max_height: float) -> RLImage:
    """Create a ReportLab Image preserving the exact rendered PNG aspect ratio."""
    buf.seek(0)
    with PILImage.open(buf) as pil_img:
        px_w, px_h = pil_img.size
    buf.seek(0)
    if px_w > 0 and px_h > 0:
        height = target_width * (float(px_h) / float(px_w))
        if height > max_height:
            scale = min(target_width / float(px_w), max_height / float(px_h))
            width = px_w * scale
            height = px_h * scale
        else:
            width = target_width
    else:
        width, height = target_width, max_height
    return RLImage(buf, width=width, height=height, hAlign="CENTER")


def _render_parity_png(
    result: ReconciliationResult,
    i18n: I18nManager,
    primary_hex: str,
) -> io.BytesIO:
    """Render 300 DPI Parity Plot (Measured vs Reconciled normalized by sigma) via Agg."""
    fig = Figure(figsize=(6.4, 3.6), dpi=300, facecolor="white")
    FigureCanvasAgg(fig)
    ax = fig.add_subplot(111)
    ax.set_facecolor("white")

    measured_norm: list[float] = []
    reconciled_norm: list[float] = []
    is_out: list[bool] = []
    for adj in result.adjustments:
        if adj.measured is None or adj.sigma is None or adj.sigma <= 0:
            continue
        measured_norm.append(adj.measured / adj.sigma)
        reconciled_norm.append(adj.reconciled / adj.sigma)
        is_out.append(bool(adj.is_outlier))

    if measured_norm:
        m_arr = np.array(measured_norm, dtype=float)
        r_arr = np.array(reconciled_norm, dtype=float)
        out_mask = np.array(is_out, dtype=bool)
        lo = float(min(np.min(m_arr), np.min(r_arr)))
        hi = float(max(np.max(m_arr), np.max(r_arr)))
        span = max(hi - lo, 2.0)
        grid = np.linspace(lo - 0.05 * span, hi + 0.05 * span, 50)

        ax.plot(grid, grid, color="#334155", linewidth=1.2, linestyle="-", label=i18n.t("step06.charts.parity_line_1_1"))
        ax.plot(grid, grid + 2.0, color="#B45309", linewidth=0.9, linestyle="--", label=i18n.t("step06.charts.parity_band_2sigma"))
        ax.plot(grid, grid - 2.0, color="#B45309", linewidth=0.9, linestyle="--")

        if np.any(~out_mask):
            ax.scatter(
                m_arr[~out_mask],
                r_arr[~out_mask],
                s=24,
                color=primary_hex,
                edgecolors="#FFFFFF",
                linewidths=0.4,
                label=i18n.t("step06.charts.parity_points"),
                zorder=3,
            )
        if np.any(out_mask):
            ax.scatter(
                m_arr[out_mask],
                r_arr[out_mask],
                s=38,
                color="#B91C1C",
                marker="^",
                label=i18n.t("step06.charts.parity_outliers"),
                zorder=4,
            )

    ax.set_title(
        i18n.t("step06.charts.parity_title", period=result.period_id),
        fontsize=9.5,
        fontweight="bold",
        color="#1E293B",
        pad=12,
    )
    ax.set_xlabel(i18n.t("step06.charts.parity_x"), fontsize=8, color="#334155", labelpad=8)
    ax.set_ylabel(i18n.t("step06.charts.parity_y"), fontsize=8, color="#334155", labelpad=6)
    ax.tick_params(labelsize=7.5, colors="#475569")
    ax.grid(True, linestyle=":", alpha=0.5, color="#CBD5E1")
    ax.legend(fontsize=7, frameon=True, facecolor="#FFFFFF", edgecolor="#CBD5E1")
    fig.subplots_adjust(left=0.12, right=0.97, top=0.88, bottom=0.18)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=300, bbox_inches="tight", pad_inches=0.25, facecolor="white")
    buf.seek(0)
    return buf


def _render_adjustments_png(
    result: ReconciliationResult,
    i18n: I18nManager,
) -> io.BytesIO:
    """Render 300 DPI Relative Adjustments (%) bar chart colored by |z| severity."""
    fig = Figure(figsize=(6.4, 3.8), dpi=300, facecolor="white")
    FigureCanvasAgg(fig)
    ax = fig.add_subplot(111)
    ax.set_facecolor("white")

    labels: list[str] = []
    pcts: list[float] = []
    bar_colors: list[str] = []
    z_crit = float(result.ged.z_critical) if result.ged.z_critical > 0 else 1.959964
    z_warn = 1.644854 if z_crit > 1.644854 else min(1.644854, z_crit * 0.85)

    for adj in result.adjustments:
        if adj.measured is None or adj.relative_pct is None:
            continue
        labels.append(adj.variable_key)
        pcts.append(float(adj.relative_pct))
        abs_z = abs(adj.z_score) if adj.z_score is not None else 0.0
        if abs_z > z_crit:
            bar_colors.append("#B91C1C")
        elif abs_z > z_warn:
            bar_colors.append("#B45309")
        else:
            bar_colors.append("#1B4965")

    if labels:
        x_pos = np.arange(len(labels))
        ax.bar(x_pos, pcts, color=bar_colors, width=0.68, edgecolor="#334155", linewidth=0.3)
        ax.axhline(0.0, color="#334155", linewidth=0.8)
        ax.set_xticks(x_pos)
        ax.set_xticklabels(labels, rotation=55, ha="right", fontsize=6.2, color="#334155")

    ax.set_title(
        i18n.t("step06.charts.adj_title", period=result.period_id),
        fontsize=9.5,
        fontweight="bold",
        color="#1E293B",
        pad=12,
    )
    ax.set_ylabel(i18n.t("step06.charts.adj_y"), fontsize=8, color="#334155", labelpad=6)
    ax.tick_params(axis="y", labelsize=7.5, colors="#475569")
    ax.grid(True, axis="y", linestyle=":", alpha=0.5, color="#CBD5E1")
    fig.subplots_adjust(left=0.12, right=0.97, top=0.88, bottom=0.28)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=300, bbox_inches="tight", pad_inches=0.25, facecolor="white")
    buf.seek(0)
    return buf


def _render_chi2_control_png(
    batch_result: BatchReconciliationResult,
    i18n: I18nManager,
    primary_hex: str,
) -> io.BytesIO:
    """Render 300 DPI Chi2 Statistical Control Chart across valid batch periods (excluding NO_FLOW)."""
    fig = Figure(figsize=(6.4, 3.5), dpi=300, facecolor="white")
    FigureCanvasAgg(fig)
    ax = fig.add_subplot(111)
    ax.set_facecolor("white")

    valid_results = [r for r in batch_result.results if not _is_skipped_result(r)]
    periods = [r.period_id for r in valid_results]
    j_vals = [r.ged.objective_j for r in valid_results]
    chi_crit = valid_results[0].ged.chi2_critical if valid_results else 0.0
    alpha = valid_results[0].ged.alpha if valid_results else 0.05

    if periods:
        x_pos = np.arange(len(periods))
        ax.plot(x_pos, j_vals, color=primary_hex, marker="o", markersize=3.2, linewidth=1.2, label=i18n.t("step06.charts.trends_chi2_series"))
        ax.axhline(
            chi_crit,
            color="#B91C1C",
            linestyle="--",
            linewidth=1.1,
            label=i18n.t("step06.charts.trends_chi2_crit", alpha=alpha),
        )
        step = max(1, len(periods) // 12)
        ax.set_xticks(x_pos[::step])
        ax.set_xticklabels(periods[::step], rotation=40, ha="right", fontsize=6.8, color="#334155")
        ax.legend(fontsize=7, frameon=True, facecolor="#FFFFFF", edgecolor="#CBD5E1")

    ax.set_title(
        i18n.t("step06.charts.trends_chi2_title"),
        fontsize=9.5,
        fontweight="bold",
        color="#1E293B",
        pad=12,
    )
    ax.set_xlabel(i18n.t("step06.charts.trends_chi2_x"), fontsize=8, color="#334155", labelpad=8)
    ax.set_ylabel(i18n.t("step06.charts.trends_chi2_y"), fontsize=8, color="#334155", labelpad=6)
    ax.tick_params(axis="y", labelsize=7.5, colors="#475569")
    ax.grid(True, linestyle=":", alpha=0.5, color="#CBD5E1")
    fig.subplots_adjust(left=0.12, right=0.97, top=0.88, bottom=0.24)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=300, bbox_inches="tight", pad_inches=0.25, facecolor="white")
    buf.seek(0)
    return buf


def _styled_table(
    data: list[list[Any]],
    col_widths: list[float],
    primary_color: colors.Color,
    mono_cols: Optional[set[int]] = None,
    right_cols: Optional[set[int]] = None,
) -> Table:
    tbl = Table(data, colWidths=col_widths, repeatRows=1)
    cmds: list[Any] = [
        ("BACKGROUND", (0, 0), (-1, 0), primary_color),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 7.5),
        ("ALIGN", (0, 0), (-1, 0), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 1), (-1, -1), 7.2),
        ("TEXTCOLOR", (0, 1), (-1, -1), colors.HexColor("#1E293B")),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#CBD5E1")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F8FAFC")]),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ]
    for col_i in sorted(mono_cols or set()):
        cmds.append(("FONTNAME", (col_i, 1), (col_i, -1), "Courier"))
    for col_i in sorted(right_cols or set()):
        cmds.append(("ALIGN", (col_i, 1), (col_i, -1), "RIGHT"))
    tbl.setStyle(TableStyle(cmds))
    return tbl


def export_to_pdf(
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
    """Export an auditable AMIRA P754 style A4 technical PDF report via ReportLab."""
    lang = language if language in ("es", "en") else "es"
    i18n = I18nManager(default_language=lang)
    txt = _PDF_TEXT[lang]
    primary_color = _rl_color(branding.primary_color)
    primary_hex = branding.primary_color if (branding.primary_color or "").startswith("#") else "#1B4965"

    target_path = Path(output_path)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    logo_path = resolve_corporate_logo(branding)
    issued_iso = datetime.now(timezone.utc).isoformat()

    page_w, page_h = A4
    left_m = 15 * mm
    right_m = 15 * mm
    usable_w = page_w - left_m - right_m

    def _draw_header(canv: rl_canvas.Canvas, _doc: Any) -> None:
        canv.saveState()
        # Top accent stripe
        canv.setFillColor(primary_color)
        canv.rect(left_m, page_h - 10 * mm, usable_w, 2.2 * mm, stroke=0, fill=1)

        # Corporate logo on every page
        try:
            canv.drawImage(
                str(logo_path),
                left_m,
                page_h - 24 * mm,
                width=42 * mm,
                height=12.5 * mm,
                preserveAspectRatio=True,
                mask="auto",
            )
        except Exception:
            pass

        canv.setFillColor(colors.HexColor("#1E293B"))
        canv.setFont("Helvetica-Bold", 9.5)
        canv.drawRightString(page_w - right_m, page_h - 15.5 * mm, branding.company_name)
        canv.setFillColor(colors.HexColor("#475569"))
        canv.setFont("Helvetica", 7.8)
        canv.drawRightString(
            page_w - right_m,
            page_h - 19.5 * mm,
            f"{branding.mining_unit} | {txt['doc_code']}: {branding.document_code}",
        )
        hdr_circuit = circuit.name if len(circuit.name) <= 48 else f"{circuit.name[:45]}..."
        canv.drawRightString(
            page_w - right_m,
            page_h - 23.2 * mm,
            f"{txt['circuit']}: {hdr_circuit} | {txt['period']}: {reconciliation_result.period_id}",
        )

        canv.setStrokeColor(colors.HexColor("#CBD5E1"))
        canv.setLineWidth(0.6)
        canv.line(left_m, page_h - 25.5 * mm, page_w - right_m, page_h - 25.5 * mm)
        canv.restoreState()

    def _draw_footer(canv: rl_canvas.Canvas, page_num: int, total_pages: int) -> None:
        canv.saveState()
        canv.setStrokeColor(colors.HexColor("#CBD5E1"))
        canv.setLineWidth(0.5)
        canv.line(left_m, 14 * mm, page_w - right_m, 14 * mm)
        canv.setFillColor(colors.HexColor("#475569"))
        canv.setFont("Helvetica", 7.3)
        left_footer = i18n.t(
            "report.pdf_page_footer",
            code=branding.document_code,
            company=branding.company_name,
            unit=branding.mining_unit,
            page=page_num,
        )
        canv.drawString(left_m, 9.5 * mm, left_footer)
        canv.drawRightString(
            page_w - right_m,
            9.5 * mm,
            txt["page_of"].format(page=page_num, total=total_pages),
        )
        canv.restoreState()

    base_styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "MBPTitle",
        parent=base_styles["Heading1"],
        fontName="Helvetica-Bold",
        fontSize=13,
        leading=16,
        textColor=primary_color,
        spaceAfter=2,
    )
    subtitle_style = ParagraphStyle(
        "MBPSubtitle",
        parent=base_styles["Normal"],
        fontName="Helvetica",
        fontSize=8.5,
        leading=11,
        textColor=colors.HexColor("#475569"),
        spaceAfter=8,
    )
    section_style = ParagraphStyle(
        "MBPSection",
        parent=base_styles["Heading2"],
        fontName="Helvetica-Bold",
        fontSize=10,
        leading=13,
        textColor=primary_color,
        spaceBefore=8,
        spaceAfter=4,
    )
    body_style = ParagraphStyle(
        "MBPBody",
        parent=base_styles["Normal"],
        fontName="Helvetica",
        fontSize=8,
        leading=11,
        textColor=colors.HexColor("#1E293B"),
        spaceAfter=4,
    )
    mono_style = ParagraphStyle(
        "MBPMono",
        parent=base_styles["Normal"],
        fontName="Courier",
        fontSize=7.2,
        leading=10,
        textColor=colors.HexColor("#1E293B"),
    )
    meta_val_style = ParagraphStyle(
        "MBPMetaVal",
        parent=base_styles["Normal"],
        fontName="Helvetica",
        fontSize=7.6,
        leading=9.6,
        textColor=colors.HexColor("#1E293B"),
    )
    cell_style = ParagraphStyle(
        "MBPCell",
        parent=base_styles["Normal"],
        fontName="Helvetica",
        fontSize=7.2,
        leading=9.2,
        textColor=colors.HexColor("#1E293B"),
    )
    cell_header_style = ParagraphStyle(
        "MBPCellHeader",
        parent=base_styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=7.2,
        leading=9.0,
        textColor=colors.white,
        alignment=TA_CENTER,
    )

    story: list[Any] = []
    story.append(Paragraph(i18n.t("report.pdf_title"), title_style))
    story.append(Paragraph(i18n.t("report.pdf_subtitle"), subtitle_style))

    # Executive corporate summary metadata table (wrapped Paragraphs prevent overflow)
    exec_meta = [
        [txt["company"], _p(branding.company_name, meta_val_style), txt["doc_code"], _p(branding.document_code, meta_val_style)],
        [txt["unit"], _p(branding.mining_unit, meta_val_style), txt["lead"], _p(branding.technical_lead, meta_val_style)],
        [txt["circuit"], _p(circuit.name, meta_val_style), txt["period"], _p(reconciliation_result.period_id, meta_val_style)],
    ]
    meta_tbl = Table(
        exec_meta,
        colWidths=[usable_w * 0.18, usable_w * 0.32, usable_w * 0.18, usable_w * 0.32],
    )
    meta_tbl.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#E9EEF2")),
                ("BACKGROUND", (2, 0), (2, -1), colors.HexColor("#E9EEF2")),
                ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                ("FONTNAME", (2, 0), (2, -1), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 7.8),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TEXTCOLOR", (0, 0), (-1, -1), colors.HexColor("#1E293B")),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#CBD5E1")),
                ("TOPPADDING", (0, 0), (-1, -1), 3.5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
            ]
        )
    )
    story.append(meta_tbl)
    story.append(Spacer(1, 5))

    # Section 1: Topology & Incidence Matrix A (wrapped text cells prevent overflow)
    story.append(Paragraph(i18n.t("report.pdf_topology_section"), section_style))
    matrix_a, node_ids, stream_ids = build_incidence_matrix(circuit)
    species_summary = ", ".join(f"{sp.id} ({sp.unit.value})" for sp in circuit.species)
    story.append(
        _styled_table(
            [
                [
                    _p(txt["circuit"], cell_header_style),
                    _p(txt["mass_unit"], cell_header_style),
                    _p(txt["nodes_streams_species"], cell_header_style),
                    _p(txt["species"], cell_header_style),
                ],
                [
                    _p(circuit.name, cell_style),
                    _p(circuit.mass_unit.value, cell_style),
                    f"{len(node_ids)} / {len(stream_ids)} / {len(circuit.species)}",
                    _p(species_summary, cell_style),
                ],
            ],
            [usable_w * 0.28, usable_w * 0.17, usable_w * 0.23, usable_w * 0.32],
            primary_color,
        )
    )
    if (circuit.description or "").strip():
        story.append(Spacer(1, 3))
        story.append(Paragraph(f"<b>{xml_escape(txt['description'])}:</b> {xml_escape(circuit.description)}", body_style))
    story.append(Spacer(1, 4))

    inc_header = [_p(txt["node"], cell_header_style)] + [_p(sid, cell_header_style) for sid in stream_ids]
    inc_data: list[list[Any]] = [inc_header]
    for r_idx, nid in enumerate(node_ids):
        inc_data.append([_p(nid, cell_style)] + [f"{int(round(matrix_a[r_idx, c_idx])):d}" for c_idx in range(len(stream_ids))])
    n_cols = len(inc_header)
    first_w = min(28 * mm, usable_w * 0.22)
    rem_w = (usable_w - first_w) / max(n_cols - 1, 1)
    story.append(
        _styled_table(
            inc_data,
            [first_w] + [rem_w] * (n_cols - 1),
            primary_color,
            mono_cols=set(range(1, n_cols)),
            right_cols=set(range(1, n_cols)),
        )
    )

    # Section 2: Metallurgical KPIs (explicit mass unit in fine metal header)
    story.append(Paragraph(i18n.t("report.pdf_kpi_section"), section_style))
    feed_str = f"{_fmt_num(kpi_report.feed_mass_nom, 2)} / {_fmt_num(kpi_report.feed_mass_rec, 2)} {circuit.mass_unit.value}"
    unexp_str = f"{_fmt_num(kpi_report.unexplained_mass_nom, 3)} / {_fmt_num(kpi_report.unexplained_mass_rec, 6)} {circuit.mass_unit.value}"
    story.append(Paragraph(f"<b>{txt['feed_mass']}:</b> {feed_str} &nbsp;&nbsp;|&nbsp;&nbsp; <b>{txt['unexplained']}:</b> {unexp_str}", body_style))

    fine_rec_header = f"{txt['fine_rec']} ({circuit.mass_unit.value})"
    kpi_rows: list[list[Any]] = [[
        _p(txt["stream"], cell_header_style),
        _p(txt["species"], cell_header_style),
        _p(txt["mass_pull_nom"], cell_header_style),
        _p(txt["mass_pull_rec"], cell_header_style),
        _p(txt["conc_ratio"], cell_header_style),
        _p(fine_rec_header, cell_header_style),
        _p(txt["rec_nom"], cell_header_style),
        _p(txt["rec_rec"], cell_header_style),
    ]]
    for prod in kpi_report.products:
        for sp in circuit.species:
            kpi_rows.append([
                _p(prod.stream_name, cell_style),
                _p(f"{sp.id} ({sp.unit.value})", cell_style),
                _fmt_num(prod.mass_pull_nom_pct, 2),
                _fmt_num(prod.mass_pull_rec_pct, 2),
                _fmt_num(prod.conc_ratio_rec, 2),
                _fmt_num(prod.fine_metal_rec.get(sp.id), 4),
                _fmt_num(prod.recovery_nom_pct.get(sp.id), 2),
                _fmt_num(prod.recovery_rec_pct.get(sp.id), 2),
            ])
    story.append(
        _styled_table(
            kpi_rows,
            [usable_w * 0.18, usable_w * 0.13, usable_w * 0.105, usable_w * 0.105, usable_w * 0.105, usable_w * 0.145, usable_w * 0.115, usable_w * 0.115],
            primary_color,
            mono_cols={2, 3, 4, 5, 6, 7},
            right_cols={2, 3, 4, 5, 6, 7},
        )
    )

    # Section 3: Reconciled Mass & Assay Balance Table
    story.append(Paragraph(i18n.t("report.pdf_balance_section"), section_style))
    bal_rows: list[list[Any]] = [[
        txt["var"],
        txt["stream"],
        txt["species"],
        txt["unit_col"],
        txt["measured"],
        txt["reconciled"],
        txt["abs_diff"],
        txt["rel_pct"],
    ]]
    for adj in reconciliation_result.adjustments:
        bal_rows.append([
            adj.variable_key,
            adj.stream_id,
            adj.species_id or txt["mass_flow_lbl"],
            adj.unit,
            _fmt_num(adj.measured, 4),
            _fmt_num(adj.reconciled, 4),
            _fmt_num(adj.absolute_diff, 4),
            _fmt_num(adj.relative_pct, 2),
        ])
    story.append(
        _styled_table(
            bal_rows,
            [usable_w * 0.21, usable_w * 0.15, usable_w * 0.12, usable_w * 0.10, usable_w * 0.11, usable_w * 0.11, usable_w * 0.10, usable_w * 0.10],
            primary_color,
            mono_cols={0, 4, 5, 6, 7},
            right_cols={4, 5, 6, 7},
        )
    )

    # Section 4: GED Diagnostics
    story.append(Paragraph(i18n.t("report.pdf_ged_section"), section_style))
    ged = reconciliation_result.ged
    res_is_skipped = _is_skipped_result(reconciliation_result)
    verdict_text = _verdict_display_text(i18n, ged.verdict, txt, is_skipped=res_is_skipped)
    ged_summary_rows = [
        [txt["metric"], txt["value"], txt["metric"], txt["value"]],
        [i18n.t("step05.objective_j"), _fmt_num(ged.objective_j, 4), i18n.t("step05.chi2_critical"), _fmt_num(ged.chi2_critical, 4)],
        [i18n.t("step04.dof"), str(ged.degrees_of_freedom), i18n.t("step05.p_value"), _fmt_num(ged.p_value, 4)],
        [i18n.t("step05.z_critical"), _fmt_num(ged.z_critical, 4), txt["verdict"], verdict_text],
        [i18n.t("step05.max_z_var"), f"{ged.max_abs_z_variable or '-'} ({_fmt_num(ged.max_abs_z_value, 3)})", txt["solver"], f"{reconciliation_result.solver_used} ({reconciliation_result.iterations})"],
    ]
    ged_tbl = _styled_table(
        ged_summary_rows,
        [usable_w * 0.24, usable_w * 0.26, usable_w * 0.24, usable_w * 0.26],
        primary_color,
        mono_cols={1, 3},
        right_cols={1, 3},
    )
    verdict_str = "skipped" if res_is_skipped else str(getattr(ged.verdict, "value", ged.verdict)).lower()
    if verdict_str == "skipped":
        verdict_bg = colors.HexColor("#E2E8F0")
    elif verdict_str == "pass":
        verdict_bg = colors.HexColor("#DCFCE7")
    elif verdict_str == "warning":
        verdict_bg = colors.HexColor("#FEF3C7")
    else:
        verdict_bg = colors.HexColor("#FEE2E2")
    ged_tbl.setStyle(TableStyle([("BACKGROUND", (3, 3), (3, 3), verdict_bg)]))
    story.append(ged_tbl)
    story.append(Spacer(1, 4))

    z_rows: list[list[Any]] = [[
        txt["var"],
        txt["measured"],
        txt["reconciled"],
        txt["sigma"],
        txt["z_score"],
        txt["classification"],
    ]]
    for adj in reconciliation_result.adjustments:
        z_rows.append([
            adj.variable_key,
            _fmt_num(adj.measured, 4),
            _fmt_num(adj.reconciled, 4),
            _fmt_num(adj.sigma, 4),
            _fmt_num(adj.z_score, 3),
            i18n.t(f"enums.variable_class.{adj.classification.value}"),
        ])
    story.append(
        _styled_table(
            z_rows,
            [usable_w * 0.22, usable_w * 0.13, usable_w * 0.13, usable_w * 0.13, usable_w * 0.12, usable_w * 0.27],
            primary_color,
            mono_cols={0, 1, 2, 3, 4},
            right_cols={1, 2, 3, 4},
        )
    )

    # Section 5: Gross Error Alerts & Outlier Comparison
    story.append(Paragraph(i18n.t("report.pdf_outlier_alerts"), section_style))
    suspect_str = ", ".join(ged.suspect_variables) if ged.suspect_variables else txt["no_suspects"]
    story.append(Paragraph(f"<b>{txt['suspects']}:</b> {suspect_str}", body_style))

    if outlier_comparison is not None:
        cmp_rows = [
            [txt["metric"], txt["with_outlier"], txt["without_outlier"]],
            [i18n.t("step05.objective_j"), _fmt_num(outlier_comparison.with_outlier.ged.objective_j, 4), _fmt_num(outlier_comparison.without_outlier.ged.objective_j, 4)],
            [txt["verdict"], i18n.t(f"enums.ged_verdict.{outlier_comparison.with_outlier.ged.verdict.value}"), i18n.t(f"enums.ged_verdict.{outlier_comparison.without_outlier.ged.verdict.value}")],
            [txt["j_reduction"], _fmt_num(outlier_comparison.j_reduction, 4), txt["yes"] if outlier_comparison.verdict_improved else txt["no"]],
            [txt["excluded"], ", ".join(outlier_comparison.excluded_variables) or txt["none_txt"], "-"],
        ]
        story.append(
            _styled_table(
                cmp_rows,
                [usable_w * 0.34, usable_w * 0.33, usable_w * 0.33],
                primary_color,
                mono_cols={1, 2},
                right_cols={1, 2},
            )
        )

    # Section 6: 300 DPI Matplotlib Technical Charts (new page for clean presentation)
    story.append(PageBreak())
    story.append(Paragraph(i18n.t("report.pdf_charts_section"), section_style))

    chart_target_w = usable_w * 0.94
    parity_buf = _render_parity_png(reconciliation_result, i18n, primary_hex)
    adj_buf = _render_adjustments_png(reconciliation_result, i18n)
    story.append(_rl_image_from_png_buf(parity_buf, chart_target_w, 108 * mm))
    story.append(Spacer(1, 6))
    story.append(_rl_image_from_png_buf(adj_buf, chart_target_w, 118 * mm))

    if batch_result is not None and any(not _is_skipped_result(r) for r in batch_result.results):
        story.append(Spacer(1, 6))
        chi_buf = _render_chi2_control_png(batch_result, i18n, primary_hex)
        story.append(_rl_image_from_png_buf(chi_buf, chart_target_w, 112 * mm))

    # Section 7: AMIRA P754 Traceability & Audit Seal
    story.append(Paragraph(i18n.t("report.pdf_traceability_section"), section_style))
    seal_rows = [
        [i18n.t("report.pdf_amira_seal"), ""],
        [i18n.t("report.pdf_timestamp"), issued_iso],
        [i18n.t("report.pdf_input_sha256"), dataset.file_sha256],
        [txt["residuals"], f"{reconciliation_result.max_mass_residual:.3e} / {reconciliation_result.max_species_residual:.3e}"],
        [txt["etl_rows"], f"{dataset.raw_row_count} / {dataset.clean_row_count} (audit entries: {len(dataset.audit_log)})"],
    ]
    seal_tbl = Table(seal_rows, colWidths=[usable_w * 0.34, usable_w * 0.66])
    seal_tbl.setStyle(
        TableStyle(
            [
                ("SPAN", (0, 0), (1, 0)),
                ("BACKGROUND", (0, 0), (1, 0), primary_color),
                ("TEXTCOLOR", (0, 0), (1, 0), colors.white),
                ("FONTNAME", (0, 0), (1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (1, 0), 8.2),
                ("FONTNAME", (0, 1), (0, -1), "Helvetica-Bold"),
                ("FONTNAME", (1, 1), (1, -1), "Courier"),
                ("FONTSIZE", (0, 1), (-1, -1), 7.2),
                ("GRID", (0, 0), (-1, -1), 0.5, primary_color),
                ("BACKGROUND", (0, 1), (-1, -1), colors.HexColor("#F8FAFC")),
                ("TOPPADDING", (0, 0), (-1, -1), 3.5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
            ]
        )
    )
    story.append(seal_tbl)
    story.append(Spacer(1, 5))

    story.append(Paragraph(f"<b>{i18n.t('report.pdf_sigma_matrix')}</b>", body_style))
    sig_rows: list[list[Any]] = [[txt["var"], txt["stream"], txt["species"], txt["mode"], txt["scale"], txt["value"]]]
    for key in sorted(uncertainty_model.specs):
        sp_obj = uncertainty_model.specs[key]
        sig_rows.append([
            sp_obj.variable_key,
            sp_obj.stream_id,
            sp_obj.species_id or txt["mass_flow_lbl"],
            i18n.t(f"enums.uncertainty_mode.{sp_obj.mode.value}"),
            i18n.t(f"enums.uncertainty_scale.{sp_obj.scale.value}"),
            f"{sp_obj.value:.4f}",
        ])
    story.append(
        _styled_table(
            sig_rows,
            [usable_w * 0.22, usable_w * 0.16, usable_w * 0.12, usable_w * 0.20, usable_w * 0.18, usable_w * 0.12],
            primary_color,
            mono_cols={0, 5},
            right_cols={5},
        )
    )

    doc = SimpleDocTemplate(
        str(target_path),
        pagesize=A4,
        leftMargin=left_m,
        rightMargin=right_m,
        topMargin=28 * mm,
        bottomMargin=17 * mm,
        title=i18n.t("report.pdf_title"),
        author=branding.technical_lead,
    )
    doc.build(
        story,
        onFirstPage=_draw_header,
        onLaterPages=_draw_header,
        canvasmaker=lambda *args, **kwargs: _NumberedCanvas(*args, footer_callback=_draw_footer, **kwargs),
    )
    return target_path
