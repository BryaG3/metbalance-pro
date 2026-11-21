"""Step 02 — Guided ETL profiling, anomaly inspection, mapping, and confirmed cleaning."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTabWidget,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from app.i18n.manager import get_i18n
from app.settings_manager import SettingsManager
from core.etl import (
    apply_cleaning_plan,
    build_default_cleaning_plan,
    list_excel_sheets,
    profile_file,
)
from core.models import GradeUnit, MassUnit, StepStatus

from .ui_common import (
    SAMPLE_DATA_DIR,
    FlowLayout,
    InspectorPayload,
    ProjectState,
    apply_status_role,
    configure_dense_table,
    get_theme_palette,
    load_svg_icon,
    make_numeric_item,
    make_text_item,
    monospace_font,
    set_combo_by_data,
    ui_font,
)


class ETLWizardView(QWidget):
    """Step 02 view for raw file ingestion, anomaly profiling, column mapping, and confirmed cleaning."""

    dataset_profiled = Signal()
    dataset_cleaned = Signal()
    inspector_requested = Signal(object)
    prev_step_requested = Signal()
    next_step_requested = Signal()

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
        self._current_file_path: Optional[Path] = None

        self._build_ui()
        self.retranslate_ui()
        self.refresh_from_state()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        self.header_title = QLabel(self)
        self.header_title.setFont(ui_font(11.0, bold=True))
        root.addWidget(self.header_title)

        # Source controls group
        self.source_group = QGroupBox(self)
        src_layout = QVBoxLayout(self.source_group)
        src_layout.setSpacing(5)

        btn_row = FlowLayout(margin=0, h_spacing=6, v_spacing=6)
        self.load_file_btn = QPushButton(self.source_group)
        self.load_file_btn.setProperty("accent", True)
        self.load_file_btn.setProperty("compact", True)
        self.sample_a_btn = QPushButton(self.source_group)
        self.sample_a_btn.setProperty("compact", True)
        self.sample_b_btn = QPushButton(self.source_group)
        self.sample_b_btn.setProperty("compact", True)
        btn_row.addWidget(self.load_file_btn)
        btn_row.addWidget(self.sample_a_btn)
        btn_row.addWidget(self.sample_b_btn)
        src_layout.addLayout(btn_row)

        meta_row = QHBoxLayout()
        self.sheet_lbl = QLabel(self.source_group)
        self.sheet_combo = QComboBox(self.source_group)
        self.sheet_combo.setMinimumWidth(160)
        self.sheet_combo.setEnabled(False)

        self.delimiter_lbl = QLabel(self.source_group)
        self.delimiter_val = QLabel("-", self.source_group)
        self.delimiter_val.setFont(monospace_font(9.0, bold=True))

        self.encoding_lbl = QLabel(self.source_group)
        self.encoding_val = QLabel("-", self.source_group)
        self.encoding_val.setFont(monospace_font(9.0, bold=True))

        self.row_count_val = QLabel(self.source_group)
        self.hash_val = QLabel(self.source_group)
        self.hash_val.setFont(monospace_font(8.5))

        meta_row.addWidget(self.sheet_lbl)
        meta_row.addWidget(self.sheet_combo)
        meta_row.addWidget(self.delimiter_lbl)
        meta_row.addWidget(self.delimiter_val)
        meta_row.addWidget(self.encoding_lbl)
        meta_row.addWidget(self.encoding_val)
        meta_row.addWidget(self.row_count_val)
        meta_row.addWidget(self.hash_val, 1)
        src_layout.addLayout(meta_row)

        self.non_met_banner = QLabel(self.source_group)
        self.non_met_banner.setWordWrap(True)
        self.non_met_banner.setContentsMargins(6, 4, 6, 4)
        apply_status_role(self.non_met_banner, "pending")
        src_layout.addWidget(self.non_met_banner)

        root.addWidget(self.source_group)

        # Workspace tabs: Anomalies, Column Mapping, Cleaning Plan + Audit Log
        self.tabs = QTabWidget(self)

        # Tab 1: Anomalies
        anom_tab = QWidget(self.tabs)
        anom_layout = QVBoxLayout(anom_tab)
        self.anomalies_banner = QLabel(anom_tab)
        self.anomalies_banner.setContentsMargins(6, 4, 6, 4)
        apply_status_role(self.anomalies_banner, "pending")
        anom_layout.addWidget(self.anomalies_banner)

        self.anomalies_table = QTableWidget(0, 7, anom_tab)
        self.anomalies_table.setObjectName("ETLAnomaliesTable")
        configure_dense_table(self.anomalies_table, stretch_last=True, editable=False)
        anom_layout.addWidget(self.anomalies_table, 1)
        self.tabs.addTab(anom_tab, "")

        # Tab 2: Assisted Column Mapping
        map_tab = QWidget(self.tabs)
        map_layout = QVBoxLayout(map_tab)
        self.mapping_table = QTableWidget(0, 6, map_tab)
        self.mapping_table.setObjectName("ETLMappingTable")
        configure_dense_table(
            self.mapping_table, stretch_last=True, editable=False, has_embedded_widgets=True
        )
        map_layout.addWidget(self.mapping_table, 1)
        self.tabs.addTab(map_tab, "")

        # Tab 3: Cleaning Plan & Audit Log
        plan_tab = QWidget(self.tabs)
        plan_tab_layout = QVBoxLayout(plan_tab)

        self.plan_group = QGroupBox(plan_tab)
        plan_grid = QVBoxLayout(self.plan_group)

        checks_row1 = QHBoxLayout()
        self.chk_decimal_commas = QCheckBox(self.plan_group)
        self.chk_corrupt_text = QCheckBox(self.plan_group)
        self.chk_negative = QCheckBox(self.plan_group)
        checks_row1.addWidget(self.chk_decimal_commas)
        checks_row1.addWidget(self.chk_corrupt_text)
        checks_row1.addWidget(self.chk_negative)
        plan_grid.addLayout(checks_row1)

        checks_row2 = QHBoxLayout()
        self.chk_out_of_range = QCheckBox(self.plan_group)
        self.chk_convert_units = QCheckBox(self.plan_group)
        self.chk_drop_non_met = QCheckBox(self.plan_group)
        checks_row2.addWidget(self.chk_out_of_range)
        checks_row2.addWidget(self.chk_convert_units)
        checks_row2.addWidget(self.chk_drop_non_met)
        plan_grid.addLayout(checks_row2)

        strat_row = QHBoxLayout()
        self.missing_strategy_lbl = QLabel(self.plan_group)
        self.missing_strategy_combo = QComboBox(self.plan_group)
        self.apply_cleaning_btn = QPushButton(self.plan_group)
        self.apply_cleaning_btn.setProperty("accent", True)
        strat_row.addWidget(self.missing_strategy_lbl)
        strat_row.addWidget(self.missing_strategy_combo, 1)
        strat_row.addWidget(self.apply_cleaning_btn)
        plan_grid.addLayout(strat_row)

        self.confirm_warning_lbl = QLabel(self.plan_group)
        self.confirm_warning_lbl.setWordWrap(True)
        self.confirm_warning_lbl.setContentsMargins(6, 4, 6, 4)
        apply_status_role(self.confirm_warning_lbl, "warning")
        plan_grid.addWidget(self.confirm_warning_lbl)

        plan_tab_layout.addWidget(self.plan_group)

        self.audit_group = QGroupBox(plan_tab)
        audit_layout = QVBoxLayout(self.audit_group)
        self.audit_table = QTableWidget(0, 5, self.audit_group)
        self.audit_table.setObjectName("ETLAuditLogTable")
        configure_dense_table(self.audit_table, stretch_last=True, editable=False)
        audit_layout.addWidget(self.audit_table, 1)
        plan_tab_layout.addWidget(self.audit_group, 1)

        self.tabs.addTab(plan_tab, "")
        root.addWidget(self.tabs, 1)

        # Footer banner + nav
        footer = QHBoxLayout()
        self.status_banner = QLabel(self)
        self.status_banner.setContentsMargins(8, 5, 8, 5)
        apply_status_role(self.status_banner, "pending")
        footer.addWidget(self.status_banner, 1)

        self.prev_btn = QPushButton(self)
        self.next_btn = QPushButton(self)
        footer.addWidget(self.prev_btn)
        footer.addWidget(self.next_btn)
        root.addLayout(footer)

        # Wire signals
        self.load_file_btn.clicked.connect(self._on_browse_file)
        self.sample_a_btn.clicked.connect(lambda: self.load_sample_dataset("a"))
        self.sample_b_btn.clicked.connect(lambda: self.load_sample_dataset("b"))
        self.sheet_combo.currentIndexChanged.connect(self._on_sheet_changed)
        self.apply_cleaning_btn.clicked.connect(self.apply_and_validate)
        self.prev_btn.clicked.connect(self.prev_step_requested.emit)
        self.next_btn.clicked.connect(self._on_next_clicked)

    def set_theme(self, theme: str) -> None:
        self._theme = theme
        self._update_icons()
        self._populate_anomalies_table()

    def _update_icons(self) -> None:
        self.load_file_btn.setIcon(
            load_svg_icon("folder.svg", self._theme, color_override="#FFFFFF", size=16)
        )
        self.sample_a_btn.setIcon(load_svg_icon("import_table.svg", self._theme, size=16))
        self.sample_b_btn.setIcon(load_svg_icon("import_table.svg", self._theme, size=16))
        self.apply_cleaning_btn.setIcon(
            load_svg_icon("check_circle.svg", self._theme, color_override="#FFFFFF", size=16)
        )

    def retranslate_ui(self) -> None:
        self._syncing = True
        try:
            self.header_title.setText(self._i18n.t("workflow.step_02_title"))
            self.source_group.setTitle(self._i18n.t("step02.section_source"))
            self.load_file_btn.setText(self._i18n.t("step02.load_file_btn"))
            self.sample_a_btn.setText(self._i18n.t("step02.sample_a_label"))
            self.sample_b_btn.setText(self._i18n.t("step02.sample_b_label"))
            self.sheet_lbl.setText(self._i18n.t("step02.sheet_label"))
            self.delimiter_lbl.setText(self._i18n.t("step02.delimiter_label"))
            self.encoding_lbl.setText(self._i18n.t("step02.encoding_label"))

            self.tabs.setTabText(0, self._i18n.t("step02.section_anomalies"))
            self.tabs.setTabText(1, self._i18n.t("step02.section_mapping"))
            self.tabs.setTabText(2, self._i18n.t("step02.section_plan"))

            self.anomalies_table.setHorizontalHeaderLabels(
                [
                    self._i18n.t("step02.col_row"),
                    self._i18n.t("step02.col_column"),
                    self._i18n.t("step02.col_raw_value"),
                    self._i18n.t("step02.col_type"),
                    self._i18n.t("step02.col_severity"),
                    self._i18n.t("step02.col_detail"),
                    self._i18n.t("step02.col_action"),
                ]
            )
            self.mapping_table.setHorizontalHeaderLabels(
                [
                    self._i18n.t("step02.source_column"),
                    self._i18n.t("step02.target_stream"),
                    self._i18n.t("step02.target_species"),
                    self._i18n.t("step02.is_mass_flow"),
                    self._i18n.t("step02.detected_unit"),
                    self._i18n.t("step02.confidence"),
                ]
            )

            self.plan_group.setTitle(self._i18n.t("step02.section_plan"))
            self.chk_decimal_commas.setText(self._i18n.t("step02.fix_decimal_commas"))
            self.chk_corrupt_text.setText(self._i18n.t("step02.coerce_corrupt_text"))
            self.chk_negative.setText(self._i18n.t("step02.nullify_negative"))
            self.chk_out_of_range.setText(self._i18n.t("step02.nullify_out_of_range"))
            self.chk_convert_units.setText(self._i18n.t("step02.convert_units"))
            self.chk_drop_non_met.setText(self._i18n.t("step02.drop_non_metallurgical"))
            self.missing_strategy_lbl.setText(self._i18n.t("step02.missing_strategy_label"))

            cur_strat = self.missing_strategy_combo.currentData() or "keep_as_unmeasured"
            self.missing_strategy_combo.clear()
            self.missing_strategy_combo.addItem(
                self._i18n.t("step02.strategy_keep"), "keep_as_unmeasured"
            )
            self.missing_strategy_combo.addItem(
                self._i18n.t("step02.strategy_drop"), "drop_row"
            )
            self.missing_strategy_combo.addItem(
                self._i18n.t("step02.strategy_interpolate"), "interpolate"
            )
            set_combo_by_data(self.missing_strategy_combo, cur_strat)

            self.apply_cleaning_btn.setText(self._i18n.t("step02.apply_cleaning_btn"))
            self.confirm_warning_lbl.setText(self._i18n.t("step02.confirm_warning"))
            self.audit_group.setTitle(self._i18n.t("step02.audit_log_title"))
            self.audit_table.setHorizontalHeaderLabels(
                [
                    self._i18n.t("report.pdf_timestamp"),
                    self._i18n.t("step02.col_action"),
                    self._i18n.t("step02.col_column"),
                    self._i18n.t("step02.col_row"),
                    self._i18n.t("step02.col_detail"),
                ]
            )

            self.prev_btn.setText(self._i18n.t("workflow.nav.prev_step"))
            self.next_btn.setText(self._i18n.t("workflow.nav.next_step"))
        finally:
            self._syncing = False

        self._update_icons()
        self.refresh_from_state()

    def _on_browse_file(self) -> None:
        start_dir = self._settings.get_last_directory() or str(SAMPLE_DATA_DIR)
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            self._i18n.t("step02.load_file_btn"),
            start_dir,
            "Excel / CSV (*.xlsx *.xlsm *.csv *.txt *.tsv)",
        )
        if not file_path:
            return
        self._settings.set_last_directory(str(Path(file_path).parent))
        self.load_file(Path(file_path))

    def open_file_dialog(self) -> None:
        """Public trigger for Menu -> Open Data."""
        self._on_browse_file()

    def load_sample_dataset(self, which: str) -> None:
        """Load built-in Sample Dataset A ('a') or Sample Dataset B ('b')."""
        filename = (
            "dataset_a_polimetalico_4prod.xlsx"
            if which.lower() == "a"
            else "dataset_b_multinodo_rsc_cu_mo.xlsx"
        )
        path = SAMPLE_DATA_DIR / filename
        if path.exists():
            self.load_file(path)

    def load_file(self, path: str | Path, sheet_name: Optional[str] = None) -> None:
        """Profile an Excel/CSV file without silent changes and update Step 02 UI."""
        file_path = Path(path)
        self._current_file_path = file_path

        self._syncing = True
        try:
            self.sheet_combo.clear()
            if file_path.suffix.lower() in {".xlsx", ".xlsm"}:
                sheets = list_excel_sheets(file_path)
                for s in sheets:
                    self.sheet_combo.addItem(s, s)
                self.sheet_combo.setEnabled(len(sheets) > 0)
                if sheet_name and sheet_name in sheets:
                    set_combo_by_data(self.sheet_combo, sheet_name)
                resolved_sheet = self.sheet_combo.currentData()
            else:
                self.sheet_combo.setEnabled(False)
                resolved_sheet = None
        finally:
            self._syncing = False

        self._profile_current_file(resolved_sheet)

    def _on_sheet_changed(self) -> None:
        if self._syncing or self._current_file_path is None:
            return
        sheet = self.sheet_combo.currentData()
        if sheet:
            self._profile_current_file(str(sheet))

    def _profile_current_file(self, sheet_name: Optional[str]) -> None:
        if self._current_file_path is None:
            return
        try:
            raw_df, report = profile_file(
                self._current_file_path,
                self._state.circuit,
                sheet_name=sheet_name,
            )
        except Exception as exc:
            self.status_banner.setText(str(exc))
            apply_status_role(self.status_banner, "error")
            self._state.step_statuses[1] = StepStatus.BLOCKED
            self._state.step_block_reasons[1] = str(exc)
            return

        self._state.clear_from_step(2)
        self._state.raw_df = raw_df
        self._state.profile_report = report
        self._state.cleaning_plan = build_default_cleaning_plan(report)
        self._state.cleaned_dataset = None
        self._state.step_statuses[1] = (
            StepStatus.WARNINGS if report.anomalies else StepStatus.PENDING
        )
        self._state.step_block_reasons.pop(1, None)

        self.refresh_from_state()
        self.dataset_profiled.emit()
        self.inspector_requested.emit(self.build_inspector_payload())

    def refresh_from_state(self) -> None:
        """Synchronize all Step 02 labels, anomaly/mapping tables, plan flags, and audit log."""
        report = self._state.profile_report
        na_str = self._i18n.t("common.na")

        if report is None:
            self.delimiter_val.setText(na_str)
            self.encoding_val.setText(na_str)
            self.row_count_val.setText(self._i18n.t("step02.row_count_label", count=0))
            self.hash_val.setText(self._i18n.t("step02.file_hash_label", sha=na_str))
            self.non_met_banner.setText(self._i18n.t("step02.non_met_label", cols=na_str))
            self.anomalies_banner.setText(self._i18n.t("step02.no_anomalies"))
            apply_status_role(self.anomalies_banner, "pending")
            self.anomalies_table.setRowCount(0)
            self.mapping_table.setRowCount(0)
            self.audit_table.setRowCount(0)
            self.apply_cleaning_btn.setEnabled(False)
            self.status_banner.setText(self._i18n.t("step02.confirm_warning"))
            apply_status_role(self.status_banner, "pending")
            return

        delim_display = "\\t" if report.delimiter == "\t" else (report.delimiter or na_str)
        self.delimiter_val.setText(delim_display)
        self.encoding_val.setText(report.encoding or na_str)
        self.row_count_val.setText(self._i18n.t("step02.row_count_label", count=report.row_count))
        self.hash_val.setText(
            self._i18n.t("step02.file_hash_label", sha=f"{report.file_sha256[:16]}...")
        )
        self.hash_val.setToolTip(report.file_sha256)

        non_met_str = ", ".join(report.non_metallurgical_columns) if report.non_metallurgical_columns else na_str
        self.non_met_banner.setText(self._i18n.t("step02.non_met_label", cols=non_met_str))
        apply_status_role(
            self.non_met_banner,
            "warning" if report.non_metallurgical_columns else "pending",
        )

        self.apply_cleaning_btn.setEnabled(True)
        self._populate_anomalies_table()
        self._populate_mapping_table()
        self._sync_plan_controls()
        self._populate_audit_table()

        cleaned = self._state.cleaned_dataset
        if cleaned is not None:
            self.status_banner.setText(
                self._i18n.t(
                    "step02.audit_summary",
                    clean_rows=cleaned.clean_row_count,
                    raw_rows=cleaned.raw_row_count,
                )
            )
            apply_status_role(self.status_banner, "validated")
            self.confirm_warning_lbl.setVisible(False)
        else:
            self.status_banner.setText(self._i18n.t("step02.confirm_warning"))
            apply_status_role(self.status_banner, "warning")
            self.confirm_warning_lbl.setVisible(True)

    def _populate_anomalies_table(self) -> None:
        report = self._state.profile_report
        if report is None:
            self.anomalies_table.setRowCount(0)
            return
        anomalies = report.anomalies
        if not anomalies:
            self.anomalies_banner.setText(self._i18n.t("step02.no_anomalies"))
            apply_status_role(self.anomalies_banner, "validated")
            self.anomalies_table.setRowCount(0)
            return

        self.anomalies_banner.setText(
            self._i18n.t("docks.inspector_alerts_count", count=len(anomalies))
        )
        apply_status_role(self.anomalies_banner, "warning")
        palette = get_theme_palette(self._theme)

        self.anomalies_table.setRowCount(len(anomalies))
        for r, anom in enumerate(anomalies):
            row_txt = str(anom.row_index + 1) if anom.row_index is not None else "-"
            sev_color = (
                palette["error"] if anom.severity.lower() == "error" else palette["warning"]
            )
            self.anomalies_table.setItem(
                r,
                0,
                make_text_item(
                    row_txt,
                    align=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                    mono=True,
                ),
            )
            self.anomalies_table.setItem(r, 1, make_text_item(anom.column, mono=True))
            self.anomalies_table.setItem(r, 2, make_text_item(str(anom.raw_value), mono=True))
            self.anomalies_table.setItem(r, 3, make_text_item(anom.anomaly_type))
            self.anomalies_table.setItem(
                r, 4, make_text_item(anom.severity, color_hex=sev_color, bold=True)
            )
            self.anomalies_table.setItem(r, 5, make_text_item(anom.detail))
            self.anomalies_table.setItem(r, 6, make_text_item(anom.suggested_action))

    def _populate_mapping_table(self) -> None:
        plan = self._state.cleaning_plan
        if plan is None:
            self.mapping_table.setRowCount(0)
            return

        self._syncing = True
        try:
            mappings = plan.mappings
            self.mapping_table.setRowCount(len(mappings))
            unmapped_lbl = self._i18n.t("step02.unmapped")

            for r, m in enumerate(mappings):
                self.mapping_table.setItem(r, 0, make_text_item(m.source_column, mono=True))

                stream_cb = QComboBox(self.mapping_table)
                stream_cb.addItem(unmapped_lbl, None)
                for s in self._state.circuit.streams:
                    stream_cb.addItem(s.id, s.id)
                set_combo_by_data(stream_cb, m.target_stream_id)
                stream_cb.currentIndexChanged.connect(
                    lambda _i, row=r, cb=stream_cb: self._on_map_stream_changed(row, cb)
                )
                self.mapping_table.setCellWidget(r, 1, stream_cb)

                sp_cb = QComboBox(self.mapping_table)
                sp_cb.addItem(unmapped_lbl, None)
                for sp in self._state.circuit.species:
                    sp_cb.addItem(f"{sp.id} ({sp.unit.value})", sp.id)
                set_combo_by_data(sp_cb, m.target_species_id)
                sp_cb.currentIndexChanged.connect(
                    lambda _i, row=r, cb=sp_cb: self._on_map_species_changed(row, cb)
                )
                self.mapping_table.setCellWidget(r, 2, sp_cb)

                mass_chk = QCheckBox(self.mapping_table)
                mass_chk.setChecked(m.is_mass_flow)
                mass_chk.toggled.connect(
                    lambda checked, row=r: self._on_map_mass_toggled(row, checked)
                )
                self.mapping_table.setCellWidget(r, 3, mass_chk)

                unit_cb = QComboBox(self.mapping_table)
                unit_cb.addItem(unmapped_lbl, None)
                for mu in MassUnit:
                    unit_cb.addItem(mu.value, mu.value)
                for gu in GradeUnit:
                    unit_cb.addItem(gu.value, gu.value)
                set_combo_by_data(unit_cb, m.detected_unit)
                unit_cb.currentIndexChanged.connect(
                    lambda _i, row=r, cb=unit_cb: self._on_map_unit_changed(row, cb)
                )
                self.mapping_table.setCellWidget(r, 4, unit_cb)

                self.mapping_table.setItem(
                    r,
                    5,
                    make_numeric_item(m.confidence * 100.0, fmt="{:.1f}%"),
                )
        finally:
            self._syncing = False

    def _on_map_stream_changed(self, row: int, cb: QComboBox) -> None:
        if self._syncing or self._state.cleaning_plan is None:
            return
        self._state.cleaning_plan.mappings[row].target_stream_id = cb.currentData()

    def _on_map_species_changed(self, row: int, cb: QComboBox) -> None:
        if self._syncing or self._state.cleaning_plan is None:
            return
        self._state.cleaning_plan.mappings[row].target_species_id = cb.currentData()

    def _on_map_mass_toggled(self, row: int, checked: bool) -> None:
        if self._syncing or self._state.cleaning_plan is None:
            return
        self._state.cleaning_plan.mappings[row].is_mass_flow = checked

    def _on_map_unit_changed(self, row: int, cb: QComboBox) -> None:
        if self._syncing or self._state.cleaning_plan is None:
            return
        self._state.cleaning_plan.mappings[row].detected_unit = cb.currentData()

    def _sync_plan_controls(self) -> None:
        plan = self._state.cleaning_plan
        if plan is None:
            return
        self._syncing = True
        try:
            self.chk_decimal_commas.setChecked(plan.fix_decimal_commas)
            self.chk_corrupt_text.setChecked(plan.coerce_corrupt_text_to_nan)
            self.chk_negative.setChecked(plan.nullify_negative)
            self.chk_out_of_range.setChecked(plan.nullify_out_of_range)
            self.chk_convert_units.setChecked(plan.convert_units)
            self.chk_drop_non_met.setChecked(plan.drop_non_metallurgical)
            set_combo_by_data(self.missing_strategy_combo, plan.missing_strategy)
        finally:
            self._syncing = False

    def _populate_audit_table(self) -> None:
        cleaned = self._state.cleaned_dataset
        if cleaned is None:
            self.audit_table.setRowCount(0)
            return
        logs = cleaned.audit_log
        self.audit_table.setRowCount(len(logs))
        for r, entry in enumerate(logs):
            self.audit_table.setItem(r, 0, make_text_item(entry.timestamp_iso, mono=True))
            self.audit_table.setItem(r, 1, make_text_item(entry.action, mono=True))
            self.audit_table.setItem(r, 2, make_text_item(entry.column or "-", mono=True))
            self.audit_table.setItem(
                r, 3, make_numeric_item(float(entry.affected_rows), fmt="{:.0f}")
            )
            self.audit_table.setItem(r, 4, make_text_item(entry.detail))

    def apply_and_validate(self) -> bool:
        """Confirm CleaningPlan explicitly and run core.etl.apply_cleaning_plan."""
        if (
            self._state.raw_df is None
            or self._state.profile_report is None
            or self._state.cleaning_plan is None
        ):
            self.status_banner.setText(self._i18n.t("step02.confirm_warning"))
            apply_status_role(self.status_banner, "error")
            return False

        plan = self._state.cleaning_plan
        plan.fix_decimal_commas = self.chk_decimal_commas.isChecked()
        plan.coerce_corrupt_text_to_nan = self.chk_corrupt_text.isChecked()
        plan.nullify_negative = self.chk_negative.isChecked()
        plan.nullify_out_of_range = self.chk_out_of_range.isChecked()
        plan.convert_units = self.chk_convert_units.isChecked()
        plan.drop_non_metallurgical = self.chk_drop_non_met.isChecked()
        plan.missing_strategy = self.missing_strategy_combo.currentData() or "keep_as_unmeasured"
        plan.confirmed = True

        try:
            cleaned = apply_cleaning_plan(
                self._state.raw_df,
                self._state.profile_report,
                self._state.circuit,
                plan,
            )
        except Exception as exc:
            self.status_banner.setText(str(exc))
            apply_status_role(self.status_banner, "error")
            self._state.step_statuses[1] = StepStatus.BLOCKED
            self._state.step_block_reasons[1] = str(exc)
            return False

        self._state.clear_from_step(2)
        self._state.cleaned_dataset = cleaned
        self._state.selected_period_idx = 0
        self._state.step_statuses[1] = (
            StepStatus.VALIDATED if cleaned.clean_row_count > 0 else StepStatus.BLOCKED
        )
        self._state.step_block_reasons.pop(1, None)

        self.refresh_from_state()
        self.dataset_cleaned.emit()
        self.inspector_requested.emit(self.build_inspector_payload())
        return cleaned.clean_row_count > 0

    def _on_next_clicked(self) -> None:
        if self._state.cleaned_dataset is None:
            if not self.apply_and_validate():
                return
        if self._state.is_step_completed(1):
            self.next_step_requested.emit()

    def build_inspector_payload(self) -> InspectorPayload:
        report = self._state.profile_report
        cleaned = self._state.cleaned_dataset
        if report is None:
            return InspectorPayload(title=self._i18n.t("workflow.step_02_title"))
        props = [
            (self._i18n.t("step02.row_count_label", count="").strip(": "), str(report.row_count)),
            (self._i18n.t("step02.encoding_label"), report.encoding or "-"),
            (self._i18n.t("step02.delimiter_label"), report.delimiter or "-"),
            ("SHA-256", report.file_sha256[:16] + "..."),
        ]
        if cleaned is not None:
            props.append(
                (self._i18n.t("step02.audit_log_title"), f"{cleaned.clean_row_count}/{cleaned.raw_row_count}")
            )
        alerts = [
            (anom.severity, f"{anom.column}: {anom.detail}")
            for anom in report.anomalies[:25]
        ]
        return InspectorPayload(
            title=self._i18n.t("workflow.step_02_title"),
            properties=props,
            alerts=alerts,
        )
