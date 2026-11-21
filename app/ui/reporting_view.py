"""Step 07 — Persistent corporate branding profile and auditable Excel/PDF report export."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import (
    QColorDialog,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.i18n.manager import get_i18n
from app.settings_manager import SettingsManager
from core.kpis import compute_kpis
from core.models import CorporateBrandingProfile, StepStatus

from .ui_common import (
    FlowLayout,
    InspectorPayload,
    ProjectState,
    apply_status_role,
    load_svg_icon,
    monospace_font,
    set_combo_by_data,
    ui_font,
)


class ReportingView(QWidget):
    """Step 07 view for corporate identity configuration and Excel/PDF report generation."""

    report_exported = Signal(str)
    inspector_requested = Signal(object)
    prev_step_requested = Signal()

    def __init__(
        self,
        state: ProjectState,
        settings: SettingsManager,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._state = state
        self._settings = settings
        self._i18n = get_i18n()
        self._theme = "light"
        self._syncing = False

        self._load_initial_branding()
        self._build_ui()
        self.retranslate_ui()
        self.refresh_from_state()

    def _load_initial_branding(self) -> None:
        profile = self._settings.load_branding()
        try:
            from reporting.branding import load_branding_profile

            disk_profile = load_branding_profile()
            if disk_profile:
                profile = disk_profile
        except Exception:
            pass
        self._state.branding = profile

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        self.header_title = QLabel(self)
        self.header_title.setFont(ui_font(11.0, bold=True))
        root.addWidget(self.header_title)

        # Branding Configuration Form
        self.branding_group = QGroupBox(self)
        form = QFormLayout(self.branding_group)
        form.setSpacing(6)

        self.company_edit = QLineEdit(self.branding_group)
        self.unit_edit = QLineEdit(self.branding_group)
        self.lead_edit = QLineEdit(self.branding_group)
        self.doc_code_edit = QLineEdit(self.branding_group)
        self.doc_code_edit.setFont(monospace_font(9.5))

        color_row = QHBoxLayout()
        self.color_preview = QLabel(self.branding_group)
        self.color_preview.setFixedSize(26, 22)
        self.color_edit = QLineEdit(self.branding_group)
        self.color_edit.setReadOnly(True)
        self.color_edit.setFont(monospace_font(9.5))
        self.pick_color_btn = QPushButton(self.branding_group)
        color_row.addWidget(self.color_preview)
        color_row.addWidget(self.color_edit)
        color_row.addWidget(self.pick_color_btn)

        logo_row = QHBoxLayout()
        self.logo_edit = QLineEdit(self.branding_group)
        self.logo_edit.setReadOnly(True)
        self.browse_logo_btn = QPushButton(self.branding_group)
        self.clear_logo_btn = QPushButton(self.branding_group)
        logo_row.addWidget(self.logo_edit, 1)
        logo_row.addWidget(self.browse_logo_btn)
        logo_row.addWidget(self.clear_logo_btn)

        self.logo_thumb = QLabel(self.branding_group)
        self.logo_thumb.setFixedSize(160, 56)
        self.logo_thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.logo_thumb.setFrameShape(QLabel.Shape.Box)

        self.save_branding_btn = QPushButton(self.branding_group)
        self.save_branding_btn.setProperty("accent", True)

        self.lbl_company = QLabel(self.branding_group)
        self.lbl_unit = QLabel(self.branding_group)
        self.lbl_lead = QLabel(self.branding_group)
        self.lbl_code = QLabel(self.branding_group)
        self.lbl_color = QLabel(self.branding_group)
        self.lbl_logo = QLabel(self.branding_group)

        form.addRow(self.lbl_company, self.company_edit)
        form.addRow(self.lbl_unit, self.unit_edit)
        form.addRow(self.lbl_lead, self.lead_edit)
        form.addRow(self.lbl_code, self.doc_code_edit)
        form.addRow(self.lbl_color, color_row)
        form.addRow(self.lbl_logo, logo_row)
        form.addRow("", self.logo_thumb)
        form.addRow("", self.save_branding_btn)
        root.addWidget(self.branding_group)

        # Export Group
        self.export_group = QGroupBox(self)
        exp_v = QVBoxLayout(self.export_group)
        exp_row = FlowLayout(margin=0, h_spacing=6, v_spacing=6)
        period_box = QWidget(self.export_group)
        period_box_layout = QHBoxLayout(period_box)
        period_box_layout.setContentsMargins(0, 0, 0, 0)
        period_box_layout.setSpacing(6)
        self.export_period_lbl = QLabel(period_box)
        self.export_period_combo = QComboBox(period_box)
        self.export_period_combo.setMinimumWidth(170)
        period_box_layout.addWidget(self.export_period_lbl)
        period_box_layout.addWidget(self.export_period_combo)

        self.export_excel_btn = QPushButton(self.export_group)
        self.export_excel_btn.setProperty("accent", True)
        self.export_excel_btn.setProperty("compact", True)
        self.export_pdf_btn = QPushButton(self.export_group)
        self.export_pdf_btn.setProperty("accent", True)
        self.export_pdf_btn.setProperty("compact", True)

        exp_row.addWidget(period_box)
        exp_row.addWidget(self.export_excel_btn)
        exp_row.addWidget(self.export_pdf_btn)
        exp_v.addLayout(exp_row)
        root.addWidget(self.export_group)

        # Status banner
        self.status_banner = QLabel(self)
        self.status_banner.setWordWrap(True)
        self.status_banner.setContentsMargins(8, 6, 8, 6)
        apply_status_role(self.status_banner, "pending")
        root.addWidget(self.status_banner)
        root.addStretch(1)

        # Footer
        footer = QHBoxLayout()
        footer.addStretch(1)
        self.prev_btn = QPushButton(self)
        footer.addWidget(self.prev_btn)
        root.addLayout(footer)

        # Signals
        self.pick_color_btn.clicked.connect(self._on_pick_color)
        self.browse_logo_btn.clicked.connect(self._on_browse_logo)
        self.clear_logo_btn.clicked.connect(self._on_clear_logo)
        self.save_branding_btn.clicked.connect(self.save_branding)
        self.export_excel_btn.clicked.connect(self.export_excel_dialog)
        self.export_pdf_btn.clicked.connect(self.export_pdf_dialog)
        self.prev_btn.clicked.connect(self.prev_step_requested.emit)

    def set_theme(self, theme: str) -> None:
        self._theme = theme
        self._update_icons()

    def _update_icons(self) -> None:
        self.save_branding_btn.setIcon(
            load_svg_icon("check_circle.svg", self._theme, color_override="#FFFFFF", size=16)
        )
        self.export_excel_btn.setIcon(
            load_svg_icon("excel_doc.svg", self._theme, color_override="#FFFFFF", size=16)
        )
        self.export_pdf_btn.setIcon(
            load_svg_icon("pdf_doc.svg", self._theme, color_override="#FFFFFF", size=16)
        )

    def retranslate_ui(self) -> None:
        self._syncing = True
        try:
            self.header_title.setText(self._i18n.t("workflow.step_07_title"))
            self.branding_group.setTitle(self._i18n.t("step07.section_branding"))
            self.lbl_company.setText(self._i18n.t("step07.company_name"))
            self.lbl_unit.setText(self._i18n.t("step07.mining_unit"))
            self.lbl_lead.setText(self._i18n.t("step07.technical_lead"))
            self.lbl_code.setText(self._i18n.t("step07.document_code"))
            self.lbl_color.setText(self._i18n.t("step07.primary_color"))
            self.pick_color_btn.setText(self._i18n.t("step07.pick_color_btn"))
            self.lbl_logo.setText(self._i18n.t("step07.logo_path"))
            self.browse_logo_btn.setText(self._i18n.t("step07.browse_logo_btn"))
            self.clear_logo_btn.setText(self._i18n.t("step07.clear_logo_btn"))
            self.save_branding_btn.setText(self._i18n.t("step07.save_branding_btn"))

            self.export_group.setTitle(self._i18n.t("step07.section_export"))
            self.export_period_lbl.setText(self._i18n.t("step07.export_period_label"))
            self.export_excel_btn.setText(self._i18n.t("step07.export_excel_btn"))
            self.export_pdf_btn.setText(self._i18n.t("step07.export_pdf_btn"))
            self.prev_btn.setText(self._i18n.t("workflow.nav.prev_step"))
        finally:
            self._syncing = False

        self._update_icons()

    def refresh_from_state(self) -> None:
        b = self._state.branding
        self.company_edit.setText(b.company_name)
        self.unit_edit.setText(b.mining_unit)
        self.lead_edit.setText(b.technical_lead)
        self.doc_code_edit.setText(b.document_code)
        self.color_edit.setText(b.primary_color)
        self._update_color_swatch(b.primary_color)
        self.logo_edit.setText(b.logo_path or "")
        self._update_logo_thumb(b.logo_path)

        self._syncing = True
        try:
            self.export_period_combo.clear()
            batch = self._state.batch_result
            if batch and batch.results:
                for idx, res in enumerate(batch.results):
                    self.export_period_combo.addItem(res.period_id, idx)
                set_combo_by_data(self.export_period_combo, self._state.selected_period_idx)
            elif self._state.reconciliation_result is not None:
                self.export_period_combo.addItem(
                    self._state.reconciliation_result.period_id,
                    self._state.selected_period_idx,
                )
        finally:
            self._syncing = False

        can_export = (
            self._state.cleaned_dataset is not None
            and self._state.uncertainty_model is not None
            and self._state.reconciliation_result is not None
        )
        self.export_excel_btn.setEnabled(can_export)
        self.export_pdf_btn.setEnabled(can_export)

    def _update_color_swatch(self, hex_color: str) -> None:
        self.color_preview.setStyleSheet(
            f"background-color: {hex_color}; border: 1px solid #475569; border-radius: 2px;"
        )

    def _update_logo_thumb(self, logo_path: Optional[str]) -> None:
        if logo_path and Path(logo_path).exists():
            pix = QPixmap(logo_path)
            if not pix.isNull():
                self.logo_thumb.setPixmap(
                    pix.scaled(
                        152,
                        50,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                )
                return
        self.logo_thumb.clear()
        self.logo_thumb.setText(self._i18n.t("common.na"))

    def _on_pick_color(self) -> None:
        initial = QColor(self.color_edit.text().strip() or "#1B4965")
        color = QColorDialog.getColor(initial, self, self._i18n.t("step07.primary_color"))
        if color.isValid():
            hex_val = color.name().upper()
            self.color_edit.setText(hex_val)
            self._update_color_swatch(hex_val)

    def _on_browse_logo(self) -> None:
        start_dir = self._settings.get_last_directory() or ""
        path, _ = QFileDialog.getOpenFileName(
            self,
            self._i18n.t("step07.logo_path"),
            start_dir,
            "Images (*.png *.jpg *.jpeg)",
        )
        if path:
            self._settings.set_last_directory(str(Path(path).parent))
            self.logo_edit.setText(path)
            self._update_logo_thumb(path)

    def _on_clear_logo(self) -> None:
        self.logo_edit.clear()
        self._update_logo_thumb(None)

    def _collect_branding(self) -> CorporateBrandingProfile:
        logo = self.logo_edit.text().strip() or None
        return CorporateBrandingProfile(
            company_name=self.company_edit.text().strip() or "MetBalance Engineering",
            mining_unit=self.unit_edit.text().strip() or "Planta Concentradora",
            technical_lead=self.lead_edit.text().strip() or "Ingeniero Metalurgista",
            document_code=self.doc_code_edit.text().strip() or "MBP-BAL-001",
            primary_color=self.color_edit.text().strip() or "#1B4965",
            logo_path=logo,
        )

    def save_branding(self) -> CorporateBrandingProfile:
        """Persist corporate branding profile via SettingsManager and reporting.branding."""
        profile = self._collect_branding()
        self._state.branding = profile
        self._settings.save_branding(profile)
        try:
            from reporting.branding import save_branding_profile

            save_branding_profile(profile)
        except Exception:
            pass
        self.status_banner.setText(self._i18n.t("step07.branding_saved_msg"))
        apply_status_role(self.status_banner, "validated")
        self.inspector_requested.emit(self.build_inspector_payload())
        return profile

    def _resolve_export_context(self):
        cleaned = self._state.cleaned_dataset
        um = self._state.uncertainty_model
        if cleaned is None or um is None:
            raise ValueError("Cleaned dataset and uncertainty model are required.")

        idx = self.export_period_combo.currentData()
        if not isinstance(idx, int):
            idx = self._state.selected_period_idx

        batch = self._state.batch_result
        if batch and batch.results and 0 <= idx < len(batch.results):
            rec_res = batch.results[idx]
            point = cleaned.periods[idx] if idx < len(cleaned.periods) else cleaned.periods[0]
        elif self._state.reconciliation_result is not None:
            rec_res = self._state.reconciliation_result
            point = self._state.current_period() or cleaned.periods[0]
        else:
            raise ValueError("No reconciliation result available to export.")

        kpi_report = compute_kpis(self._state.circuit, point, rec_res)
        comp = (
            self._state.outlier_comparison
            if self._state.outlier_comparison
            and self._state.outlier_comparison.with_outlier.period_id == rec_res.period_id
            else None
        )
        return self._state.circuit, cleaned, um, rec_res, kpi_report, self.save_branding(), batch, comp

    def export_excel_dialog(self) -> Optional[Path]:
        """Prompt user for Excel save destination and export workbook."""
        start_dir = self._settings.get_last_directory() or ""
        default_name = str(Path(start_dir) / f"{self._state.branding.document_code}.xlsx")
        path_str, _ = QFileDialog.getSaveFileName(
            self,
            self._i18n.t("step07.export_excel_btn"),
            default_name,
            "Excel (*.xlsx)",
        )
        if not path_str:
            return None
        self._settings.set_last_directory(str(Path(path_str).parent))
        return self.export_excel_to(Path(path_str))

    def export_pdf_dialog(self) -> Optional[Path]:
        """Prompt user for PDF save destination and export ReportLab report."""
        start_dir = self._settings.get_last_directory() or ""
        default_name = str(Path(start_dir) / f"{self._state.branding.document_code}.pdf")
        path_str, _ = QFileDialog.getSaveFileName(
            self,
            self._i18n.t("step07.export_pdf_btn"),
            default_name,
            "PDF (*.pdf)",
        )
        if not path_str:
            return None
        self._settings.set_last_directory(str(Path(path_str).parent))
        return self.export_pdf_to(Path(path_str))

    def export_excel_to(self, output_path: Path | str) -> Optional[Path]:
        """Execute reporting.excel_exporter.export_to_excel and update status banner."""
        try:
            from reporting.excel_exporter import export_to_excel

            circuit, dataset, um, rec_res, kpi, branding, batch, comp = (
                self._resolve_export_context()
            )
            out = export_to_excel(
                output_path=output_path,
                circuit=circuit,
                dataset=dataset,
                uncertainty_model=um,
                reconciliation_result=rec_res,
                kpi_report=kpi,
                branding=branding,
                batch_result=batch,
                outlier_comparison=comp,
                language=self._i18n.language,
            )
        except Exception as exc:
            self.status_banner.setText(self._i18n.t("step07.export_error", error=str(exc)))
            apply_status_role(self.status_banner, "error")
            return None

        self._state.step_statuses[6] = StepStatus.VALIDATED
        self.status_banner.setText(self._i18n.t("step07.export_success", path=str(out)))
        apply_status_role(self.status_banner, "validated")
        self.report_exported.emit(str(out))
        return Path(out)

    def export_pdf_to(self, output_path: Path | str) -> Optional[Path]:
        """Execute reporting.pdf_exporter.export_to_pdf and update status banner."""
        try:
            from reporting.pdf_exporter import export_to_pdf

            circuit, dataset, um, rec_res, kpi, branding, batch, comp = (
                self._resolve_export_context()
            )
            out = export_to_pdf(
                output_path=output_path,
                circuit=circuit,
                dataset=dataset,
                uncertainty_model=um,
                reconciliation_result=rec_res,
                kpi_report=kpi,
                branding=branding,
                batch_result=batch,
                outlier_comparison=comp,
                language=self._i18n.language,
            )
        except Exception as exc:
            self.status_banner.setText(self._i18n.t("step07.export_error", error=str(exc)))
            apply_status_role(self.status_banner, "error")
            return None

        self._state.step_statuses[6] = StepStatus.VALIDATED
        self.status_banner.setText(self._i18n.t("step07.export_success", path=str(out)))
        apply_status_role(self.status_banner, "validated")
        self.report_exported.emit(str(out))
        return Path(out)

    def build_inspector_payload(self) -> InspectorPayload:
        b = self._state.branding
        return InspectorPayload(
            title=self._i18n.t("workflow.step_07_title"),
            properties=[
                (self._i18n.t("step07.company_name"), b.company_name),
                (self._i18n.t("step07.mining_unit"), b.mining_unit),
                (self._i18n.t("step07.document_code"), b.document_code),
                (self._i18n.t("step07.primary_color"), b.primary_color),
            ],
        )
