"""Step 04 — Pre-reconciliation observability, QR rank/DOF diagnostics, and nodal closure."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPushButton,
    QSplitter,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from app.i18n.manager import get_i18n
from core.models import ObservabilityReport, StepStatus, VariableClass
from core.observability import analyze_observability

from .ui_common import (
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


class DiagnosticsView(QWidget):
    """Step 04 view for QR observability, DOF guardrails, and raw nodal closure."""

    diagnostics_completed = Signal(bool)
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

        self._metric_cards: dict[str, tuple[QLabel, QLabel]] = {}
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

        # Control bar
        ctrl_row = QHBoxLayout()
        self.period_lbl = QLabel(self)
        self.period_combo = QComboBox(self)
        self.period_combo.setMinimumWidth(180)
        self.run_diagnostics_btn = QPushButton(self)
        self.run_diagnostics_btn.setProperty("accent", True)

        ctrl_row.addWidget(self.period_lbl)
        ctrl_row.addWidget(self.period_combo)
        ctrl_row.addWidget(self.run_diagnostics_btn)
        ctrl_row.addStretch(1)
        root.addLayout(ctrl_row)

        # QR / DOF Metric Cards
        self.summary_group = QGroupBox(self)
        cards_grid = QGridLayout(self.summary_group)
        cards_grid.setSpacing(6)

        metric_keys = [
            "step04.num_nodes",
            "step04.num_streams",
            "step04.num_species",
            "step04.total_vars",
            "step04.measured_vars",
            "step04.unmeasured_vars",
            "step04.rank_constraints",
            "step04.rank_unmeasured",
            "step04.dof",
            "step04.redundancy_dof",
        ]
        for idx, key in enumerate(metric_keys):
            card = QFrame(self.summary_group)
            card.setProperty("card", True)
            c_layout = QVBoxLayout(card)
            c_layout.setContentsMargins(8, 5, 8, 5)
            c_layout.setSpacing(2)

            title_lbl = QLabel(card)
            title_lbl.setProperty("role", "secondary")
            title_lbl.setFont(ui_font(8.0))
            val_lbl = QLabel("-", card)
            val_lbl.setFont(monospace_font(11.0, bold=True))
            val_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            c_layout.addWidget(title_lbl)
            c_layout.addWidget(val_lbl)
            cards_grid.addWidget(card, idx // 5, idx % 5)
            self._metric_cards[key] = (title_lbl, val_lbl)

        root.addWidget(self.summary_group)

        # Explicit Guardrail Banner + Missing Hints
        self.guardrail_banner = QLabel(self)
        self.guardrail_banner.setWordWrap(True)
        self.guardrail_banner.setContentsMargins(8, 6, 8, 6)
        apply_status_role(self.guardrail_banner, "pending")
        root.addWidget(self.guardrail_banner)

        self.hints_group = QGroupBox(self)
        hints_layout = QVBoxLayout(self.hints_group)
        self.hints_list = QListWidget(self.hints_group)
        self.hints_list.setMaximumHeight(90)
        hints_layout.addWidget(self.hints_list)
        self.hints_group.setVisible(False)
        root.addWidget(self.hints_group)

        # Tables Splitter
        splitter = QSplitter(Qt.Orientation.Vertical, self)

        self.vars_group = QGroupBox(splitter)
        v_layout = QVBoxLayout(self.vars_group)
        self.vars_table = QTableWidget(0, 5, self.vars_group)
        self.vars_table.setObjectName("ObservabilityVarsTable")
        configure_dense_table(self.vars_table, stretch_last=True, editable=False)
        v_layout.addWidget(self.vars_table)
        splitter.addWidget(self.vars_group)

        self.nodal_group = QGroupBox(splitter)
        n_layout = QVBoxLayout(self.nodal_group)
        self.nodal_table = QTableWidget(0, 6, self.nodal_group)
        self.nodal_table.setObjectName("NodalImbalanceTable")
        configure_dense_table(self.nodal_table, stretch_last=True, editable=False)
        n_layout.addWidget(self.nodal_table)
        splitter.addWidget(self.nodal_group)

        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        root.addWidget(splitter, 1)

        # Footer Navigation
        footer = QHBoxLayout()
        footer.addStretch(1)
        self.prev_btn = QPushButton(self)
        self.next_btn = QPushButton(self)
        self.next_btn.setProperty("accent", True)
        footer.addWidget(self.prev_btn)
        footer.addWidget(self.next_btn)
        root.addLayout(footer)

        # Signals
        self.period_combo.currentIndexChanged.connect(self._on_period_changed)
        self.run_diagnostics_btn.clicked.connect(self.run_diagnostics)
        self.prev_btn.clicked.connect(self.prev_step_requested.emit)
        self.next_btn.clicked.connect(self._on_next_clicked)

    def set_theme(self, theme: str) -> None:
        self._theme = theme
        self._update_icons()
        self._render_report()

    def _update_icons(self) -> None:
        self.run_diagnostics_btn.setIcon(
            load_svg_icon("matrix.svg", self._theme, color_override="#FFFFFF", size=16)
        )

    def retranslate_ui(self) -> None:
        self._syncing = True
        try:
            self.header_title.setText(self._i18n.t("workflow.step_04_title"))
            self.period_lbl.setText(self._i18n.t("step05.period_label"))
            self.run_diagnostics_btn.setText(self._i18n.t("step04.run_diagnostics_btn"))
            self.summary_group.setTitle(self._i18n.t("step04.section_summary"))

            for key, (title_lbl, _) in self._metric_cards.items():
                title_lbl.setText(self._i18n.t(key))

            self.hints_group.setTitle(self._i18n.t("step04.missing_hint_title"))
            self.vars_group.setTitle(self._i18n.t("step04.section_variables"))
            self.vars_table.setHorizontalHeaderLabels(
                [
                    self._i18n.t("step04.col_var_key"),
                    self._i18n.t("step04.col_stream"),
                    self._i18n.t("step04.col_species"),
                    self._i18n.t("step04.col_measured"),
                    self._i18n.t("step04.col_classification"),
                ]
            )

            self.nodal_group.setTitle(self._i18n.t("step04.section_nodal"))
            self.nodal_table.setHorizontalHeaderLabels(
                [
                    self._i18n.t("step04.col_node"),
                    self._i18n.t("step04.col_species"),
                    self._i18n.t("step04.col_inflow"),
                    self._i18n.t("step04.col_outflow"),
                    self._i18n.t("step04.col_diff"),
                    self._i18n.t("step04.col_rel_pct"),
                ]
            )

            self.prev_btn.setText(self._i18n.t("workflow.nav.prev_step"))
            self.next_btn.setText(self._i18n.t("workflow.nav.next_step"))
        finally:
            self._syncing = False

        self._update_icons()
        self._render_report()

    def refresh_from_state(self) -> None:
        """Populate period combo from CleanedDataset and render current observability report."""
        self._syncing = True
        try:
            self.period_combo.clear()
            cleaned = self._state.cleaned_dataset
            if cleaned and cleaned.periods:
                for idx, p in enumerate(cleaned.periods):
                    label = f"{p.period_id} ({p.timestamp})" if p.timestamp else p.period_id
                    self.period_combo.addItem(label, idx)
                set_combo_by_data(self.period_combo, self._state.selected_period_idx)
        finally:
            self._syncing = False
        self._render_report()

    def _on_period_changed(self, _idx: int) -> None:
        if self._syncing:
            return
        data = self.period_combo.currentData()
        if isinstance(data, int):
            self._state.selected_period_idx = data
            self._state.observability_report = None
            self._state.step_statuses[3] = StepStatus.PENDING
            self._state.clear_from_step(4)
            self._render_report()
            self.diagnostics_completed.emit(False)

    def run_diagnostics(self) -> Optional[ObservabilityReport]:
        """Execute core.observability.analyze_observability on selected MeasurementPoint."""
        point = self._state.current_period()
        if point is None:
            return None

        report = analyze_observability(self._state.circuit, point)
        self._state.observability_report = report
        self._state.clear_from_step(4)

        if not report.can_reconcile:
            self._state.step_statuses[3] = StepStatus.BLOCKED
            reason = (
                report.missing_measurements_hint[0]
                if report.missing_measurements_hint
                else self._i18n.t(
                    "step04.guardrail_underdetermined", nu=report.degrees_of_freedom
                )
            )
            self._state.step_block_reasons[3] = reason
        elif report.is_deterministic:
            self._state.step_statuses[3] = StepStatus.WARNINGS
            self._state.step_block_reasons.pop(3, None)
        else:
            self._state.step_statuses[3] = StepStatus.VALIDATED
            self._state.step_block_reasons.pop(3, None)

        self._render_report()
        self.inspector_requested.emit(self.build_inspector_payload())
        self.diagnostics_completed.emit(report.can_reconcile)
        return report

    def _render_report(self) -> None:
        report = self._state.observability_report
        palette = get_theme_palette(self._theme)

        if report is None:
            for _, (_, val_lbl) in self._metric_cards.items():
                val_lbl.setText("-")
            self.guardrail_banner.setText(self._i18n.t("workflow.step_04_desc"))
            apply_status_role(self.guardrail_banner, "pending")
            self.hints_group.setVisible(False)
            self.vars_table.setRowCount(0)
            self.nodal_table.setRowCount(0)
            self.next_btn.setEnabled(False)
            return

        values = {
            "step04.num_nodes": report.num_nodes,
            "step04.num_streams": report.num_streams,
            "step04.num_species": report.num_species,
            "step04.total_vars": report.total_variables,
            "step04.measured_vars": report.measured_count,
            "step04.unmeasured_vars": report.unmeasured_count,
            "step04.rank_constraints": report.rank_constraints,
            "step04.rank_unmeasured": report.rank_unmeasured,
            "step04.dof": report.degrees_of_freedom,
            "step04.redundancy_dof": report.redundancy_dof,
        }
        for key, num in values.items():
            self._metric_cards[key][1].setText(str(num))

        if not report.can_reconcile:
            self.guardrail_banner.setText(
                self._i18n.t(
                    "step04.guardrail_underdetermined", nu=report.degrees_of_freedom
                )
            )
            apply_status_role(self.guardrail_banner, "error")
            self.hints_list.clear()
            for hint in report.missing_measurements_hint:
                self.hints_list.addItem(hint)
            self.hints_group.setVisible(bool(report.missing_measurements_hint))
            self.next_btn.setEnabled(False)
        elif report.is_deterministic:
            self.guardrail_banner.setText(self._i18n.t("step04.guardrail_deterministic"))
            apply_status_role(self.guardrail_banner, "warning")
            self.hints_group.setVisible(False)
            self.next_btn.setEnabled(True)
        else:
            self.guardrail_banner.setText(
                self._i18n.t(
                    "step04.guardrail_redundant", nu=report.degrees_of_freedom
                )
            )
            apply_status_role(self.guardrail_banner, "validated")
            self.hints_group.setVisible(False)
            self.next_btn.setEnabled(True)

        # Variable classification table
        yes_str = self._i18n.t("step04.yes")
        no_str = self._i18n.t("step04.no")
        self.vars_table.setRowCount(len(report.variable_statuses))
        for r, st in enumerate(report.variable_statuses):
            if st.classification == VariableClass.MEASURED_REDUNDANT:
                c_color = palette["validated"]
            elif st.classification == VariableClass.UNMEASURED_OBSERVABLE:
                c_color = palette["accent"]
            elif st.classification == VariableClass.UNMEASURED_UNOBSERVABLE:
                c_color = palette["error"]
            else:
                c_color = palette["text_secondary"]

            self.vars_table.setItem(r, 0, make_text_item(st.variable_key, mono=True))
            self.vars_table.setItem(r, 1, make_text_item(st.stream_id))
            self.vars_table.setItem(r, 2, make_text_item(st.species_id or "-"))
            self.vars_table.setItem(
                r,
                3,
                make_text_item(
                    yes_str if st.is_measured else no_str,
                    align=Qt.AlignmentFlag.AlignCenter,
                ),
            )
            self.vars_table.setItem(
                r,
                4,
                make_text_item(
                    self._i18n.t(f"enums.variable_class.{st.classification.value}"),
                    color_hex=c_color,
                    bold=True,
                ),
            )

        # Nodal Imbalances table
        na_str = self._i18n.t("common.na")
        mass_lbl = self._i18n.t("step04.mass_total_label")
        self.nodal_table.setRowCount(len(report.nodal_imbalances))
        for r, imb in enumerate(report.nodal_imbalances):
            self.nodal_table.setItem(r, 0, make_text_item(imb.node_id, mono=True))
            self.nodal_table.setItem(r, 1, make_text_item(imb.species_id or mass_lbl))
            self.nodal_table.setItem(
                r, 2, make_numeric_item(imb.inflow, fmt="{:.4f}", na_text=na_str)
            )
            self.nodal_table.setItem(
                r, 3, make_numeric_item(imb.outflow, fmt="{:.4f}", na_text=na_str)
            )
            self.nodal_table.setItem(
                r, 4, make_numeric_item(imb.difference, fmt="{:+.4f}", na_text=na_str)
            )
            rel_color = None
            if imb.relative_pct is not None and abs(imb.relative_pct) > 10.0:
                rel_color = palette["warning"]
            self.nodal_table.setItem(
                r,
                5,
                make_numeric_item(
                    imb.relative_pct, fmt="{:+.2f}%", na_text=na_str, color_hex=rel_color
                ),
            )

    def _on_next_clicked(self) -> None:
        if self._state.observability_report is None:
            self.run_diagnostics()
        if self._state.observability_report and self._state.observability_report.can_reconcile:
            self.next_step_requested.emit()

    def build_inspector_payload(self) -> InspectorPayload:
        report = self._state.observability_report
        if report is None:
            return InspectorPayload(title=self._i18n.t("workflow.step_04_title"))
        alerts = [("warning" if report.can_reconcile else "error", h) for h in report.missing_measurements_hint]
        if report.is_deterministic:
            alerts.append(("warning", self._i18n.t("step04.guardrail_deterministic")))
        return InspectorPayload(
            title=self._i18n.t("workflow.step_04_title"),
            properties=[
                (self._i18n.t("step04.dof"), str(report.degrees_of_freedom)),
                (self._i18n.t("step04.redundancy_dof"), str(report.redundancy_dof)),
                (self._i18n.t("step04.rank_constraints"), str(report.rank_constraints)),
                (self._i18n.t("step04.rank_unmeasured"), str(report.rank_unmeasured)),
            ],
            alerts=alerts,
            blocked_reason=self._state.step_block_reasons.get(3),
        )
