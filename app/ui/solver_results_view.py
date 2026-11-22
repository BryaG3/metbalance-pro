"""Step 05 — Asynchronous WLS solver execution, GED chi2 diagnostics, and Con/Sin outlier test."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QObject, QThread, Qt, Signal, Slot
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from app.i18n.manager import get_i18n
from core.models import (
    BatchReconciliationResult,
    CircuitSpec,
    GEDVerdict,
    MeasurementPoint,
    OutlierComparisonResult,
    ReconciliationResult,
    StepStatus,
    UncertaintyModel,
)
from core.reconciliation import (
    compare_with_without_outlier,
    reconcile_batch,
    reconcile_period,
)

from .ui_common import (
    FlowLayout,
    InspectorPayload,
    ProjectState,
    TableInspectorDialog,
    apply_status_role,
    autosize_table_headers,
    configure_dense_table,
    get_theme_palette,
    load_svg_icon,
    make_expand_table_button,
    make_numeric_item,
    make_text_item,
    monospace_font,
    set_combo_by_data,
    ui_font,
)


class _ReconcileWorker(QObject):
    """QObject worker executing WLS optimization inside a background QThread."""

    progress = Signal(int, int)
    single_finished = Signal(object)
    batch_finished = Signal(object)
    compare_finished = Signal(object)
    failed = Signal(str)

    def __init__(
        self,
        mode: str,
        circuit: CircuitSpec,
        uncertainty_model: UncertaintyModel,
        point: Optional[MeasurementPoint] = None,
        periods: Optional[list[MeasurementPoint]] = None,
        excluded_outliers: Optional[list[str]] = None,
    ) -> None:
        super().__init__()
        self._mode = mode
        self._circuit = circuit
        self._uncertainty = uncertainty_model
        self._point = point
        self._periods = periods or []
        self._excluded = excluded_outliers or []

    @Slot()
    def run(self) -> None:
        try:
            if self._mode == "single" and self._point is not None:
                res = reconcile_period(
                    self._circuit,
                    self._point,
                    self._uncertainty,
                    excluded_outliers=self._excluded or None,
                )
                self.single_finished.emit(res)
            elif self._mode == "batch":
                batch = reconcile_batch(
                    self._circuit,
                    self._periods,
                    self._uncertainty,
                    progress_callback=lambda cur, tot: self.progress.emit(cur, tot),
                )
                self.batch_finished.emit(batch)
            elif self._mode == "compare" and self._point is not None:
                comp = compare_with_without_outlier(
                    self._circuit,
                    self._point,
                    self._uncertainty,
                    outlier_keys=self._excluded,
                )
                self.compare_finished.emit(comp)
        except Exception as exc:
            self.failed.emit(str(exc))


class SolverResultsView(QWidget):
    """Step 05 view running WLS in QThread, displaying GED diagnostics and Con/Sin outlier analysis."""

    reconciliation_completed = Signal(bool)
    inspector_requested = Signal(object)
    prev_step_requested = Signal()
    next_step_requested = Signal()

    def __init__(
        self,
        state: ProjectState,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._state = state
        self._i18n = get_i18n()
        self._theme = "light"
        self._syncing = False

        self._worker_thread: Optional[QThread] = None
        self._worker: Optional[_ReconcileWorker] = None
        self._ged_cards: dict[str, tuple[QLabel, QLabel]] = {}

        self._build_ui()
        self.retranslate_ui()
        self.refresh_from_state()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        # Fixed Header
        self.header_title = QLabel(self)
        self.header_title.setFont(ui_font(11.0, bold=True))
        self.header_title.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        root.addWidget(self.header_title)

        # Scrollable Central Body
        self.scroll_area = QScrollArea(self)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_area.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self.scroll_area.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self.scroll_area.viewport().setAutoFillBackground(False)

        scroll_content = QWidget(self.scroll_area)
        body_layout = QVBoxLayout(scroll_content)
        body_layout.setContentsMargins(0, 0, 4, 0)
        body_layout.setSpacing(8)

        # 1. Execution Controls Group
        self.ctrl_group = QGroupBox(scroll_content)
        self.ctrl_group.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        ctrl_v = QVBoxLayout(self.ctrl_group)
        ctrl_v.setSpacing(6)

        ctrl_btn_bar = QWidget(self.ctrl_group)
        ctrl_btn_bar.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        btn_row = FlowLayout(ctrl_btn_bar, margin=0, h_spacing=6, v_spacing=6)

        period_box = QWidget(ctrl_btn_bar)
        period_box.setAutoFillBackground(False)
        period_box.setSizePolicy(
            QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed
        )
        period_box_layout = QHBoxLayout(period_box)
        period_box_layout.setContentsMargins(0, 0, 0, 0)
        period_box_layout.setSpacing(6)
        self.period_lbl = QLabel(period_box)
        self.period_combo = QComboBox(period_box)
        self.period_combo.setMinimumWidth(160)
        self.period_combo.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed
        )
        period_box_layout.addWidget(self.period_lbl)
        period_box_layout.addWidget(self.period_combo)

        self.run_single_btn = QPushButton(ctrl_btn_bar)
        self.run_single_btn.setProperty("accent", True)
        self.run_single_btn.setProperty("compact", True)
        self.run_single_btn.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed
        )
        self.run_batch_btn = QPushButton(ctrl_btn_bar)
        self.run_batch_btn.setProperty("compact", True)
        self.run_batch_btn.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed
        )
        self.progress_bar = QProgressBar(ctrl_btn_bar)
        self.progress_bar.setMinimumWidth(160)
        self.progress_bar.setMinimumHeight(22)
        self.progress_bar.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed
        )
        self.progress_bar.setVisible(False)

        btn_row.addWidget(period_box)
        btn_row.addWidget(self.run_single_btn)
        btn_row.addWidget(self.run_batch_btn)
        btn_row.addWidget(self.progress_bar)
        ctrl_v.addWidget(ctrl_btn_bar)

        self.solver_status_lbl = QLabel(self.ctrl_group)
        self.solver_status_lbl.setWordWrap(True)
        self.solver_status_lbl.setContentsMargins(6, 4, 6, 4)
        self.solver_status_lbl.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum
        )
        apply_status_role(self.solver_status_lbl, "pending")
        ctrl_v.addWidget(self.solver_status_lbl)

        residuals_bar = QWidget(self.ctrl_group)
        residuals_bar.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        residuals_flow = FlowLayout(residuals_bar, margin=0, h_spacing=12, v_spacing=4)

        mass_res_box = QWidget(residuals_bar)
        mass_res_box.setAutoFillBackground(False)
        mass_res_box.setSizePolicy(
            QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed
        )
        mass_res_layout = QHBoxLayout(mass_res_box)
        mass_res_layout.setContentsMargins(0, 0, 0, 0)
        mass_res_layout.setSpacing(6)
        self.max_mass_res_lbl = QLabel(mass_res_box)
        self.max_mass_res_val = QLabel("-", mass_res_box)
        self.max_mass_res_val.setFont(monospace_font(9.0, bold=True))
        mass_res_layout.addWidget(self.max_mass_res_lbl)
        mass_res_layout.addWidget(self.max_mass_res_val)

        sp_res_box = QWidget(residuals_bar)
        sp_res_box.setAutoFillBackground(False)
        sp_res_box.setSizePolicy(
            QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed
        )
        sp_res_layout = QHBoxLayout(sp_res_box)
        sp_res_layout.setContentsMargins(0, 0, 0, 0)
        sp_res_layout.setSpacing(6)
        self.max_sp_res_lbl = QLabel(sp_res_box)
        self.max_sp_res_val = QLabel("-", sp_res_box)
        self.max_sp_res_val.setFont(monospace_font(9.0, bold=True))
        sp_res_layout.addWidget(self.max_sp_res_lbl)
        sp_res_layout.addWidget(self.max_sp_res_val)

        residuals_flow.addWidget(mass_res_box)
        residuals_flow.addWidget(sp_res_box)
        ctrl_v.addWidget(residuals_bar)
        body_layout.addWidget(self.ctrl_group)

        # 2. GED Diagnostics Group
        self.ged_group = QGroupBox(scroll_content)
        self.ged_group.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        ged_v = QVBoxLayout(self.ged_group)
        ged_v.setSpacing(6)
        ged_grid = QGridLayout()
        ged_grid.setSpacing(6)

        ged_keys = [
            "step05.objective_j",
            "step05.chi2_critical",
            "step05.p_value",
            "step05.z_critical",
            "step05.verdict",
            "step05.max_z_var",
            "step05.suspect_vars",
        ]
        for idx, key in enumerate(ged_keys):
            card = QFrame(self.ged_group)
            card.setProperty("card", True)
            card.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
            )
            cl = QVBoxLayout(card)
            cl.setContentsMargins(8, 4, 8, 4)
            cl.setSpacing(2)
            t_lbl = QLabel(card)
            t_lbl.setProperty("role", "secondary")
            t_lbl.setFont(ui_font(8.0))
            t_lbl.setWordWrap(True)
            v_lbl = QLabel("-", card)
            v_lbl.setFont(monospace_font(9.5, bold=True))
            v_lbl.setWordWrap(True)
            cl.addWidget(t_lbl)
            cl.addWidget(v_lbl)
            ged_grid.addWidget(card, idx // 4, idx % 4)
            self._ged_cards[key] = (t_lbl, v_lbl)

        ged_v.addLayout(ged_grid)

        self.ged_verdict_banner = QLabel(self.ged_group)
        self.ged_verdict_banner.setWordWrap(True)
        self.ged_verdict_banner.setContentsMargins(8, 5, 8, 5)
        self.ged_verdict_banner.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum
        )
        apply_status_role(self.ged_verdict_banner, "pending")
        ged_v.addWidget(self.ged_verdict_banner)
        body_layout.addWidget(self.ged_group)

        # 3. Con vs. Sin Outlier Analysis Group
        self.outlier_group = QGroupBox(scroll_content)
        self.outlier_group.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        out_v = QVBoxLayout(self.outlier_group)
        out_v.setSpacing(6)

        out_btn_bar = QWidget(self.outlier_group)
        out_btn_bar.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        out_btns = FlowLayout(out_btn_bar, margin=0, h_spacing=6, v_spacing=6)
        self.mark_outlier_btn = QPushButton(out_btn_bar)
        self.mark_outlier_btn.setProperty("compact", True)
        self.mark_outlier_btn.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed
        )
        self.clear_outliers_btn = QPushButton(out_btn_bar)
        self.clear_outliers_btn.setProperty("compact", True)
        self.clear_outliers_btn.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed
        )
        self.compare_outliers_btn = QPushButton(out_btn_bar)
        self.compare_outliers_btn.setProperty("accent", True)
        self.compare_outliers_btn.setProperty("compact", True)
        self.compare_outliers_btn.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed
        )

        out_btns.addWidget(self.mark_outlier_btn)
        out_btns.addWidget(self.clear_outliers_btn)
        out_btns.addWidget(self.compare_outliers_btn)
        out_v.addWidget(out_btn_bar)

        self.excluded_list_lbl = QLabel("-", self.outlier_group)
        self.excluded_list_lbl.setFont(monospace_font(8.5))
        self.excluded_list_lbl.setWordWrap(True)
        self.excluded_list_lbl.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum
        )
        out_v.addWidget(self.excluded_list_lbl)

        self.comparison_panel = QFrame(self.outlier_group)
        self.comparison_panel.setProperty("card", True)
        self.comparison_panel.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        comp_layout = QHBoxLayout(self.comparison_panel)
        comp_layout.setContentsMargins(8, 5, 8, 5)
        comp_layout.setSpacing(8)
        self.comp_with_lbl = QLabel(self.comparison_panel)
        self.comp_with_lbl.setFont(monospace_font(8.5))
        self.comp_with_lbl.setWordWrap(True)
        self.comp_without_lbl = QLabel(self.comparison_panel)
        self.comp_without_lbl.setFont(monospace_font(8.5))
        self.comp_without_lbl.setWordWrap(True)
        self.comp_delta_lbl = QLabel(self.comparison_panel)
        self.comp_delta_lbl.setFont(monospace_font(8.5, bold=True))
        self.comp_delta_lbl.setWordWrap(True)
        comp_layout.addWidget(self.comp_with_lbl, 1)
        comp_layout.addWidget(self.comp_without_lbl, 1)
        comp_layout.addWidget(self.comp_delta_lbl, 1)
        self.comparison_panel.setVisible(False)
        out_v.addWidget(self.comparison_panel)
        body_layout.addWidget(self.outlier_group)

        # 4. Variable Adjustments Table Group
        self.adj_group = QGroupBox(scroll_content)
        self.adj_group.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        adj_tbl_layout = QVBoxLayout(self.adj_group)
        adj_tbl_layout.setSpacing(6)

        adj_top_bar = QWidget(self.adj_group)
        adj_top_bar.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        adj_top = QHBoxLayout(adj_top_bar)
        adj_top.setContentsMargins(0, 0, 0, 0)
        adj_top.addStretch(1)
        self.expand_adjustments_btn = make_expand_table_button(
            adj_top_bar, self._theme
        )
        self.expand_adjustments_btn.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed
        )
        adj_top.addWidget(self.expand_adjustments_btn)
        adj_tbl_layout.addWidget(adj_top_bar)

        self.adjustments_table = QTableWidget(0, 9, self.adj_group)
        self.adjustments_table.setObjectName("WLSAdjustmentsTable")
        configure_dense_table(self.adjustments_table, stretch_last=True, editable=False)
        self.adjustments_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.adjustments_table.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self.adjustments_table.setMinimumHeight(240)
        self.adjustments_table.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        adj_tbl_layout.addWidget(self.adjustments_table, 1)
        body_layout.addWidget(self.adj_group, 3)

        # 5. Historical Batch Summary Group
        self.batch_group = QGroupBox(scroll_content)
        self.batch_group.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        batch_v = QVBoxLayout(self.batch_group)
        batch_v.setSpacing(6)

        batch_top_bar = QWidget(self.batch_group)
        batch_top_bar.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        batch_top_layout = QHBoxLayout(batch_top_bar)
        batch_top_layout.setContentsMargins(0, 0, 0, 0)
        batch_top_layout.setSpacing(8)

        batch_stats_bar = QWidget(batch_top_bar)
        batch_stats_bar.setAutoFillBackground(False)
        batch_stats_bar.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        batch_stats = FlowLayout(batch_stats_bar, margin=0, h_spacing=10, v_spacing=4)
        self.batch_pass_lbl = QLabel(batch_stats_bar)
        self.batch_warn_lbl = QLabel(batch_stats_bar)
        self.batch_fail_lbl = QLabel(batch_stats_bar)
        self.batch_skipped_lbl = QLabel(batch_stats_bar)
        self.batch_mean_lbl = QLabel(batch_stats_bar)
        for lbl in (
            self.batch_pass_lbl,
            self.batch_warn_lbl,
            self.batch_fail_lbl,
            self.batch_skipped_lbl,
            self.batch_mean_lbl,
        ):
            lbl.setFont(monospace_font(9.0, bold=True))
            lbl.setSizePolicy(
                QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed
            )
            batch_stats.addWidget(lbl)

        self.expand_batch_btn = make_expand_table_button(batch_top_bar, self._theme)
        self.expand_batch_btn.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed
        )
        batch_top_layout.addWidget(batch_stats_bar, 1)
        batch_top_layout.addWidget(
            self.expand_batch_btn,
            0,
            Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight,
        )
        batch_v.addWidget(batch_top_bar)

        self.batch_table = QTableWidget(0, 6, self.batch_group)
        self.batch_table.setObjectName("BatchSummaryTable")
        configure_dense_table(self.batch_table, stretch_last=True, editable=False)
        self.batch_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.batch_table.setMinimumHeight(220)
        self.batch_table.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        batch_v.addWidget(self.batch_table, 1)
        self.batch_group.setVisible(False)
        body_layout.addWidget(self.batch_group, 2)

        self.scroll_area.setWidget(scroll_content)
        root.addWidget(self.scroll_area, 1)

        # Fixed Footer Navigation
        footer = QHBoxLayout()
        footer.addStretch(1)
        self.prev_btn = QPushButton(self)
        self.prev_btn.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed
        )
        self.next_btn = QPushButton(self)
        self.next_btn.setProperty("accent", True)
        self.next_btn.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed
        )
        footer.addWidget(self.prev_btn)
        footer.addWidget(self.next_btn)
        root.addLayout(footer)

        # Signals
        self.period_combo.currentIndexChanged.connect(self._on_period_changed)
        self.run_single_btn.clicked.connect(self.run_single)
        self.run_batch_btn.clicked.connect(self.run_batch)
        self.mark_outlier_btn.clicked.connect(self.mark_selected_as_outlier)
        self.clear_outliers_btn.clicked.connect(self.clear_outliers)
        self.compare_outliers_btn.clicked.connect(self.run_outlier_comparison)
        self.batch_table.cellClicked.connect(self._on_batch_row_clicked)
        self.expand_adjustments_btn.clicked.connect(self._on_expand_adjustments)
        self.expand_batch_btn.clicked.connect(self._on_expand_batch)
        self.prev_btn.clicked.connect(self.prev_step_requested.emit)
        self.next_btn.clicked.connect(self.next_step_requested.emit)

    def _on_expand_adjustments(self) -> None:
        dlg = TableInspectorDialog(
            self.adjustments_table,
            self._i18n.t("step05.section_adjustments"),
            theme=self._theme,
            parent=self,
        )
        dlg.exec()

    def _on_expand_batch(self) -> None:
        dlg = TableInspectorDialog(
            self.batch_table,
            self._i18n.t("step05.section_batch"),
            theme=self._theme,
            parent=self,
        )
        dlg.exec()

    def is_busy(self) -> bool:
        """Return True if a background reconciliation thread is active."""
        return self._worker_thread is not None and self._worker_thread.isRunning()

    def shutdown_workers(self) -> None:
        """Gracefully terminate any active background QThread before window destruction."""
        if self._worker_thread is not None:
            if self._worker_thread.isRunning():
                self._worker_thread.quit()
                self._worker_thread.wait(3000)
            self._worker_thread.deleteLater()
            self._worker_thread = None
        if self._worker is not None:
            self._worker.deleteLater()
            self._worker = None

    def set_theme(self, theme: str) -> None:
        self._theme = theme
        self._update_icons()
        self._render_results()

    def _update_icons(self) -> None:
        self.run_single_btn.setIcon(
            load_svg_icon("play.svg", self._theme, color_override="#FFFFFF", size=16)
        )
        self.run_batch_btn.setIcon(load_svg_icon("play.svg", self._theme, size=16))
        self.mark_outlier_btn.setIcon(load_svg_icon("alert_triangle.svg", self._theme, size=16))
        self.compare_outliers_btn.setIcon(
            load_svg_icon("scatter_plot.svg", self._theme, color_override="#FFFFFF", size=16)
        )
        expand_icon = load_svg_icon("reset_layout.svg", self._theme, size=14)
        self.expand_adjustments_btn.setIcon(expand_icon)
        self.expand_batch_btn.setIcon(expand_icon)

    def retranslate_ui(self) -> None:
        self._syncing = True
        try:
            self.header_title.setText(self._i18n.t("workflow.step_05_title"))
            self.ctrl_group.setTitle(self._i18n.t("step05.section_controls"))
            self.period_lbl.setText(self._i18n.t("step05.period_label"))
            self.run_single_btn.setText(self._i18n.t("step05.run_single_btn"))
            self.run_batch_btn.setText(self._i18n.t("step05.run_batch_btn"))
            self.max_mass_res_lbl.setText(self._i18n.t("step05.max_mass_res"))
            self.max_sp_res_lbl.setText(self._i18n.t("step05.max_species_res"))

            self.ged_group.setTitle(self._i18n.t("step05.section_ged"))
            for key, (t_lbl, _) in self._ged_cards.items():
                t_lbl.setText(self._i18n.t(key))

            self.outlier_group.setTitle(self._i18n.t("step05.section_outliers"))
            self.mark_outlier_btn.setText(self._i18n.t("step05.mark_outlier_btn"))
            self.clear_outliers_btn.setText(self._i18n.t("step05.clear_outliers_btn"))
            self.compare_outliers_btn.setText(self._i18n.t("step05.compare_outliers_btn"))

            self.adj_group.setTitle(self._i18n.t("step05.section_adjustments"))
            self.adjustments_table.setHorizontalHeaderLabels(
                [
                    self._i18n.t("step05.col_var"),
                    self._i18n.t("step05.col_unit"),
                    self._i18n.t("step05.col_measured"),
                    self._i18n.t("step05.col_reconciled"),
                    self._i18n.t("step05.col_sigma"),
                    self._i18n.t("step05.col_abs_diff"),
                    self._i18n.t("step05.col_rel_pct"),
                    self._i18n.t("step05.col_z_score"),
                    self._i18n.t("step05.col_outlier"),
                ]
            )

            self.batch_group.setTitle(self._i18n.t("step05.section_batch"))
            self.batch_table.setHorizontalHeaderLabels(
                [
                    self._i18n.t("step05.period_label"),
                    self._i18n.t("step05.objective_j"),
                    self._i18n.t("step05.chi2_critical"),
                    self._i18n.t("step05.verdict"),
                    self._i18n.t("step05.max_z_var"),
                    self._i18n.t("step05.p_value"),
                ]
            )

            self.prev_btn.setText(self._i18n.t("workflow.nav.prev_step"))
            self.next_btn.setText(self._i18n.t("workflow.nav.next_step"))
            self.expand_adjustments_btn.setText(self._i18n.t("common.expand_table"))
            self.expand_batch_btn.setText(self._i18n.t("common.expand_table"))
        finally:
            self._syncing = False

        self._update_icons()
        self._render_results()
        autosize_table_headers(self.adjustments_table, stretch_last=True)
        autosize_table_headers(self.batch_table, stretch_last=True)

    def refresh_from_state(self) -> None:
        self._syncing = True
        try:
            self.period_combo.clear()
            cleaned = self._state.cleaned_dataset
            if cleaned and cleaned.periods:
                for idx, p in enumerate(cleaned.periods):
                    lbl = f"{p.period_id} ({p.timestamp})" if p.timestamp else p.period_id
                    self.period_combo.addItem(lbl, idx)
                set_combo_by_data(self.period_combo, self._state.selected_period_idx)
        finally:
            self._syncing = False
        self._render_results()

    def _on_period_changed(self, _idx: int) -> None:
        if self._syncing:
            return
        idx = self.period_combo.currentData()
        if isinstance(idx, int):
            self._state.selected_period_idx = idx
            if self._state.batch_result and idx < len(self._state.batch_result.results):
                self._state.reconciliation_result = self._state.batch_result.results[idx]
                self._render_results()

    def _on_batch_row_clicked(self, row: int, _col: int) -> None:
        if self._state.batch_result and 0 <= row < len(self._state.batch_result.results):
            self._state.selected_period_idx = row
            self._state.reconciliation_result = self._state.batch_result.results[row]
            self._syncing = True
            set_combo_by_data(self.period_combo, row)
            self._syncing = False
            self._render_results()
            self.reconciliation_completed.emit(True)

    def _set_running(self, running: bool, indeterminate: bool = True) -> None:
        self.run_single_btn.setEnabled(not running)
        self.run_batch_btn.setEnabled(not running)
        self.compare_outliers_btn.setEnabled(not running)
        self.progress_bar.setVisible(running)
        if running and indeterminate:
            self.progress_bar.setRange(0, 0)

    def _launch_worker(self, worker: _ReconcileWorker, indeterminate: bool = True) -> None:
        if self.is_busy():
            return
        self.shutdown_workers()
        self._set_running(True, indeterminate=indeterminate)

        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._on_batch_progress)
        worker.single_finished.connect(self._on_single_finished)
        worker.batch_finished.connect(self._on_batch_finished)
        worker.compare_finished.connect(self._on_compare_finished)
        worker.failed.connect(self._on_worker_failed)
        worker.single_finished.connect(thread.quit)
        worker.batch_finished.connect(thread.quit)
        worker.compare_finished.connect(thread.quit)
        worker.failed.connect(thread.quit)

        self._worker_thread = thread
        self._worker = worker
        thread.start()

    def run_single(self) -> None:
        """Execute single-period WLS reconciliation asynchronously in a QThread."""
        point = self._state.current_period()
        um = self._state.uncertainty_model
        if point is None or um is None:
            return
        worker = _ReconcileWorker(
            mode="single",
            circuit=self._state.circuit,
            uncertainty_model=um,
            point=point,
            excluded_outliers=list(self._state.excluded_outliers),
        )
        self._launch_worker(worker, indeterminate=True)

    def run_batch(self) -> None:
        """Execute batch historical WLS reconciliation asynchronously with QProgressBar."""
        cleaned = self._state.cleaned_dataset
        um = self._state.uncertainty_model
        if cleaned is None or not cleaned.periods or um is None:
            return
        self.progress_bar.setRange(0, len(cleaned.periods))
        self.progress_bar.setValue(0)
        worker = _ReconcileWorker(
            mode="batch",
            circuit=self._state.circuit,
            uncertainty_model=um,
            periods=list(cleaned.periods),
        )
        self._launch_worker(worker, indeterminate=False)

    @Slot(int, int)
    def _on_batch_progress(self, current: int, total: int) -> None:
        self.progress_bar.setRange(0, max(total, 1))
        self.progress_bar.setValue(current)
        self.progress_bar.setFormat(
            self._i18n.t("step05.running_progress", current=current, total=total)
        )

    @Slot(object)
    def _on_single_finished(self, result: ReconciliationResult) -> None:
        self._set_running(False)
        self._state.reconciliation_result = result
        self._state.outlier_comparison = None
        self._apply_step_status_from_result(result)
        self._render_results()
        self.inspector_requested.emit(self.build_inspector_payload())
        self.reconciliation_completed.emit(result.converged)

    @Slot(object)
    def _on_batch_finished(self, batch: BatchReconciliationResult) -> None:
        self._set_running(False)
        self._state.batch_result = batch
        idx = max(0, min(self._state.selected_period_idx, len(batch.results) - 1))
        if batch.results:
            self._state.selected_period_idx = idx
            self._state.reconciliation_result = batch.results[idx]
            self._apply_step_status_from_result(batch.results[idx])
        self._render_results()
        self.inspector_requested.emit(self.build_inspector_payload())
        self.reconciliation_completed.emit(bool(batch.results))

    @Slot(object)
    def _on_compare_finished(self, comp: OutlierComparisonResult) -> None:
        self._set_running(False)
        self._state.outlier_comparison = comp
        self._state.reconciliation_result = comp.without_outlier
        self._apply_step_status_from_result(comp.without_outlier)
        self._render_results()
        self.inspector_requested.emit(self.build_inspector_payload())
        self.reconciliation_completed.emit(comp.without_outlier.converged)

    @Slot(str)
    def _on_worker_failed(self, err_msg: str) -> None:
        self._set_running(False)
        self._state.step_statuses[4] = StepStatus.BLOCKED
        self._state.step_block_reasons[4] = err_msg
        self.solver_status_lbl.setText(err_msg)
        apply_status_role(self.solver_status_lbl, "error")
        self.inspector_requested.emit(self.build_inspector_payload())
        self.reconciliation_completed.emit(False)

    def _apply_step_status_from_result(self, result: ReconciliationResult) -> None:
        if not result.converged:
            self._state.step_statuses[4] = StepStatus.BLOCKED
            self._state.step_block_reasons[4] = self._i18n.t(
                "step05.solver_status_fail", solver=result.solver_used
            )
        elif result.ged.verdict in (GEDVerdict.FAIL, GEDVerdict.WARNING):
            self._state.step_statuses[4] = StepStatus.WARNINGS
            self._state.step_block_reasons.pop(4, None)
        else:
            self._state.step_statuses[4] = StepStatus.VALIDATED
            self._state.step_block_reasons.pop(4, None)

    def mark_selected_as_outlier(self) -> None:
        """Add selected measured variables (or top suspect variable) to excluded outlier list."""
        result = self._state.reconciliation_result
        if result is None:
            return
        selected_rows = sorted({idx.row() for idx in self.adjustments_table.selectedIndexes()})
        added: list[str] = []
        for r in selected_rows:
            var_item = self.adjustments_table.item(r, 0)
            if var_item is None:
                continue
            key = var_item.text()
            adj = next((a for a in result.adjustments if a.variable_key == key), None)
            if adj is not None and adj.measured is not None:
                if key not in self._state.excluded_outliers:
                    self._state.excluded_outliers.append(key)
                    added.append(key)
        if not added and result.ged.max_abs_z_variable:
            top = result.ged.max_abs_z_variable
            if top not in self._state.excluded_outliers:
                self._state.excluded_outliers.append(top)
        self._render_outlier_strip()

    def clear_outliers(self) -> None:
        """Clear excluded outlier list and comparison banner."""
        self._state.excluded_outliers.clear()
        self._state.outlier_comparison = None
        self._render_outlier_strip()
        self.comparison_panel.setVisible(False)

    def run_outlier_comparison(self) -> None:
        """Run compare_with_without_outlier for currently flagged outliers in QThread."""
        if not self._state.excluded_outliers:
            self.mark_selected_as_outlier()
        point = self._state.current_period()
        um = self._state.uncertainty_model
        if point is None or um is None or not self._state.excluded_outliers:
            return
        worker = _ReconcileWorker(
            mode="compare",
            circuit=self._state.circuit,
            uncertainty_model=um,
            point=point,
            excluded_outliers=list(self._state.excluded_outliers),
        )
        self._launch_worker(worker, indeterminate=True)

    def _render_outlier_strip(self) -> None:
        excl = self._state.excluded_outliers
        self.excluded_list_lbl.setText(", ".join(excl) if excl else "-")

    def _render_results(self) -> None:
        self._render_outlier_strip()
        result = self._state.reconciliation_result
        palette = get_theme_palette(self._theme)
        na_str = self._i18n.t("common.na")

        if result is None:
            self.solver_status_lbl.setText(self._i18n.t("workflow.step_05_desc"))
            apply_status_role(self.solver_status_lbl, "pending")
            self.max_mass_res_val.setText("-")
            self.max_sp_res_val.setText("-")
            for _, (_, v_lbl) in self._ged_cards.items():
                v_lbl.setText("-")
            self.ged_verdict_banner.setText(self._i18n.t("workflow.step_05_desc"))
            apply_status_role(self.ged_verdict_banner, "pending")
            self.adjustments_table.setRowCount(0)
            self.comparison_panel.setVisible(False)
            self.next_btn.setEnabled(False)
        else:
            if result.converged:
                self.solver_status_lbl.setText(
                    self._i18n.t(
                        "step05.solver_status_ok",
                        solver=result.solver_used,
                        iters=result.iterations,
                    )
                )
                apply_status_role(self.solver_status_lbl, "validated")
            else:
                self.solver_status_lbl.setText(
                    self._i18n.t("step05.solver_status_fail", solver=result.solver_used)
                )
                apply_status_role(self.solver_status_lbl, "error")

            self.max_mass_res_val.setText(f"{result.max_mass_residual:.3e}")
            self.max_sp_res_val.setText(f"{result.max_species_residual:.3e}")

            ged = result.ged
            self._ged_cards["step05.objective_j"][1].setText(f"{ged.objective_j:.4f}")
            self._ged_cards["step05.chi2_critical"][1].setText(f"{ged.chi2_critical:.4f}")
            self._ged_cards["step05.p_value"][1].setText(f"{ged.p_value:.4g}")
            self._ged_cards["step05.z_critical"][1].setText(f"{ged.z_critical:.3f}")
            self._ged_cards["step05.verdict"][1].setText(
                self._i18n.t(f"enums.ged_verdict.{ged.verdict.value}")
            )
            max_z_str = (
                f"{ged.max_abs_z_variable} ({ged.max_abs_z_value:.2f})"
                if ged.max_abs_z_variable
                else "-"
            )
            self._ged_cards["step05.max_z_var"][1].setText(max_z_str)
            self._ged_cards["step05.suspect_vars"][1].setText(
                ", ".join(ged.suspect_variables) if ged.suspect_variables else "-"
            )

            if ged.verdict == GEDVerdict.PASS:
                self.ged_verdict_banner.setText(self._i18n.t("step05.verdict_pass_msg"))
                apply_status_role(self.ged_verdict_banner, "validated")
            elif ged.verdict == GEDVerdict.WARNING:
                self.ged_verdict_banner.setText(self._i18n.t("step05.verdict_warning_msg"))
                apply_status_role(self.ged_verdict_banner, "warning")
            elif str(ged.verdict.value) == "skipped":
                self.ged_verdict_banner.setText(self._i18n.t("enums.ged_verdict.skipped"))
                apply_status_role(self.ged_verdict_banner, "pending")
            else:
                self.ged_verdict_banner.setText(self._i18n.t("step05.verdict_fail_msg"))
                apply_status_role(self.ged_verdict_banner, "error")

            # Adjustments table
            yes_str = self._i18n.t("step04.yes")
            no_str = self._i18n.t("step04.no")
            self.adjustments_table.setRowCount(len(result.adjustments))
            for r, adj in enumerate(result.adjustments):
                row_bg: Optional[str] = None
                z_color: Optional[str] = None
                if adj.is_outlier:
                    row_bg = palette["error_bg"]
                    z_color = palette["error"]
                elif adj.z_score is not None and abs(adj.z_score) > 2.0:
                    row_bg = palette["warning_bg"]
                    z_color = palette["warning"]

                self.adjustments_table.setItem(
                    r, 0, make_text_item(adj.variable_key, mono=True, bg_hex=row_bg)
                )
                self.adjustments_table.setItem(
                    r, 1, make_text_item(adj.unit, bg_hex=row_bg)
                )
                self.adjustments_table.setItem(
                    r, 2, make_numeric_item(adj.measured, fmt="{:.4f}", na_text=na_str, bg_hex=row_bg)
                )
                self.adjustments_table.setItem(
                    r, 3, make_numeric_item(adj.reconciled, fmt="{:.4f}", bold=True, bg_hex=row_bg)
                )
                self.adjustments_table.setItem(
                    r, 4, make_numeric_item(adj.sigma, fmt="{:.4f}", na_text=na_str, bg_hex=row_bg)
                )
                self.adjustments_table.setItem(
                    r,
                    5,
                    make_numeric_item(adj.absolute_diff, fmt="{:+.4f}", na_text=na_str, bg_hex=row_bg),
                )
                self.adjustments_table.setItem(
                    r,
                    6,
                    make_numeric_item(adj.relative_pct, fmt="{:+.2f}%", na_text=na_str, bg_hex=row_bg),
                )
                self.adjustments_table.setItem(
                    r,
                    7,
                    make_numeric_item(
                        adj.z_score,
                        fmt="{:+.3f}",
                        na_text=na_str,
                        color_hex=z_color,
                        bg_hex=row_bg,
                        bold=adj.is_outlier,
                    ),
                )
                self.adjustments_table.setItem(
                    r,
                    8,
                    make_text_item(
                        yes_str if adj.is_outlier else no_str,
                        align=Qt.AlignmentFlag.AlignCenter,
                        color_hex=z_color,
                        bg_hex=row_bg,
                        bold=adj.is_outlier,
                    ),
                )

            # Comparison panel
            comp = self._state.outlier_comparison
            if comp is not None:
                self.comp_with_lbl.setText(
                    f"{self._i18n.t('step05.with_outlier_label')}: "
                    f"J*={comp.with_outlier.ged.objective_j:.3f} | "
                    f"{self._i18n.t(f'enums.ged_verdict.{comp.with_outlier.ged.verdict.value}')}"
                )
                self.comp_without_lbl.setText(
                    f"{self._i18n.t('step05.without_outlier_label', vars=', '.join(comp.excluded_variables))}: "
                    f"J*={comp.without_outlier.ged.objective_j:.3f} | "
                    f"{self._i18n.t(f'enums.ged_verdict.{comp.without_outlier.ged.verdict.value}')}"
                )
                imp_key = (
                    "step05.verdict_improved_yes"
                    if comp.verdict_improved
                    else "step05.verdict_improved_no"
                )
                self.comp_delta_lbl.setText(
                    f"{self._i18n.t('step05.j_reduction_label', delta=f'{comp.j_reduction:.3f}')} — "
                    f"{self._i18n.t(imp_key)}"
                )
                self.comparison_panel.setVisible(True)
            else:
                self.comparison_panel.setVisible(False)

            self.next_btn.setEnabled(result.converged)

        # Batch summary
        batch = self._state.batch_result
        if batch is not None and batch.results:
            self.batch_group.setVisible(True)
            self.batch_pass_lbl.setText(
                f"{self._i18n.t('step05.batch_pass')}: {batch.summary_pass_count}"
            )
            self.batch_warn_lbl.setText(
                f"{self._i18n.t('step05.batch_warning')}: {batch.summary_warning_count}"
            )
            self.batch_fail_lbl.setText(
                f"{self._i18n.t('step05.batch_fail')}: {batch.summary_fail_count}"
            )
            skipped_count = getattr(batch, "summary_skipped_count", 0)
            self.batch_skipped_lbl.setText(
                f"{self._i18n.t('step05.batch_skipped')}: {skipped_count}"
            )
            self.batch_mean_lbl.setText(
                f"{self._i18n.t('step05.batch_mean_chi2')}: {batch.mean_chi2:.3f}"
            )
            self.batch_table.setRowCount(len(batch.results))
            for r, res in enumerate(batch.results):
                v = res.ged.verdict
                if v == GEDVerdict.PASS:
                    v_color = palette["validated"]
                elif v == GEDVerdict.WARNING:
                    v_color = palette["warning"]
                elif str(v.value) == "skipped":
                    v_color = palette["pending"]
                else:
                    v_color = palette["error"]
                self.batch_table.setItem(r, 0, make_text_item(res.period_id, mono=True))
                self.batch_table.setItem(
                    r, 1, make_numeric_item(res.ged.objective_j, fmt="{:.3f}")
                )
                self.batch_table.setItem(
                    r, 2, make_numeric_item(res.ged.chi2_critical, fmt="{:.3f}")
                )
                self.batch_table.setItem(
                    r,
                    3,
                    make_text_item(
                        self._i18n.t(f"enums.ged_verdict.{v.value}"),
                        color_hex=v_color,
                        bold=True,
                    ),
                )
                z_str = (
                    f"{res.ged.max_abs_z_variable} ({res.ged.max_abs_z_value:.2f})"
                    if res.ged.max_abs_z_variable
                    else "-"
                )
                self.batch_table.setItem(r, 4, make_text_item(z_str, mono=True))
                self.batch_table.setItem(
                    r, 5, make_numeric_item(res.ged.p_value, fmt="{:.4g}")
                )
        else:
            self.batch_group.setVisible(False)
        autosize_table_headers(self.adjustments_table, stretch_last=True)
        autosize_table_headers(self.batch_table, stretch_last=True)

    def build_inspector_payload(self) -> InspectorPayload:
        result = self._state.reconciliation_result
        if result is None:
            return InspectorPayload(title=self._i18n.t("workflow.step_05_title"))
        ged = result.ged
        alerts: list[tuple[str, str]] = []
        for var in ged.suspect_variables:
            alerts.append(("error", f"{var} (|z| > {ged.z_critical:.2f})"))
        if ged.verdict == GEDVerdict.WARNING:
            alerts.append(("warning", self._i18n.t("step05.verdict_warning_msg")))
        return InspectorPayload(
            title=f"{self._i18n.t('workflow.step_05_title')} ({result.period_id})",
            properties=[
                (self._i18n.t("step05.objective_j"), f"{ged.objective_j:.4f}"),
                (self._i18n.t("step05.chi2_critical"), f"{ged.chi2_critical:.4f}"),
                (self._i18n.t("step05.p_value"), f"{ged.p_value:.4g}"),
                (self._i18n.t("step05.max_mass_res"), f"{result.max_mass_residual:.3e}"),
                (self._i18n.t("step05.max_species_res"), f"{result.max_species_residual:.3e}"),
            ],
            alerts=alerts,
            blocked_reason=self._state.step_block_reasons.get(4),
        )
