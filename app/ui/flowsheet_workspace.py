"""Permanent canvas-first central workspace hosting the interactive canvas and 7 engineering views."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QObject, QSize, Qt, SIGNAL, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QTabBar,
    QTabWidget,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from app.i18n.manager import get_i18n
from app.settings_manager import SettingsManager
from core.topology import build_incidence_matrix

from .charts_view import ChartsView
from .diagnostics_view import DiagnosticsView
from .etl_wizard_view import ETLWizardView
from .flowsheet_canvas import FlowsheetCanvas
from .project_topology_view import ProjectTopologyView
from .reporting_view import ReportingView
from .solver_results_view import SolverResultsView
from .ui_common import (
    FlowLayout,
    ProjectState,
    apply_status_role,
    configure_dense_table,
    get_theme_palette,
    load_svg_icon,
    make_text_item,
    monospace_font,
    ui_font,
)
from .uncertainty_view import UncertaintyView

_VIEW_MODES: tuple[str, ...] = ("canvas", "data", "charts", "reports")


class _ShrinkableStackedWidget(QStackedWidget):
    """Stacked widget with bounded minimumSizeHint so off-mode views cannot inflate docks."""

    def minimumSizeHint(self) -> QSize:
        return QSize(360, 240)


class _ShrinkableTabWidget(QTabWidget):
    """Tab widget with bounded minimumSizeHint so long tab bars shrink cleanly."""

    def minimumSizeHint(self) -> QSize:
        return QSize(340, 220)


class StepStackProxy(QObject):
    """Compatibility proxy tracking the active logical workflow step (0..6) for MainWindow & tests."""

    currentChanged = Signal(int)

    def __init__(self, workspace: "FlowsheetWorkspace") -> None:
        super().__init__(workspace)
        self._workspace = workspace
        self._current_idx = 0

    def count(self) -> int:
        return len(self._workspace._step_views)

    def widget(self, index: int) -> Optional[QWidget]:
        views = self._workspace._step_views
        if 0 <= index < len(views):
            return views[index]
        return None

    def currentWidget(self) -> Optional[QWidget]:
        return self.widget(self._current_idx)

    def currentIndex(self) -> int:
        return self._current_idx

    def setCurrentIndex(self, index: int) -> None:
        clamped = max(0, min(int(index), self.count() - 1))
        changed = clamped != self._current_idx
        self._current_idx = clamped
        self._workspace._sync_step_overlay(clamped)
        if changed:
            self.currentChanged.emit(clamped)


class FlowsheetWorkspace(QWidget):
    """Canvas-first central workspace uniting Excalidraw flowsheet, data tables, charts, and reports."""

    view_mode_changed = Signal(str)
    run_diagnostics_requested = Signal()
    run_reconcile_requested = Signal()
    run_batch_requested = Signal()

    def __init__(
        self,
        state: ProjectState,
        settings: SettingsManager,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("FlowsheetWorkspace")
        self.setMinimumWidth(380)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._state = state
        self._settings = settings
        self._i18n = get_i18n()
        self._theme = "light"
        self._active_mode = "canvas"
        self._syncing_topology = False

        # Instantiate all 7 sequential views
        self.step_01_view = ProjectTopologyView(self._state, self)
        self.step_02_view = ETLWizardView(self._state, self._settings, self)
        self.step_03_view = UncertaintyView(self._state, self)
        self.step_04_view = DiagnosticsView(self._state, self)
        self.step_05_view = SolverResultsView(self._state, self)
        self.step_06_view = ChartsView(self._state, self)
        self.step_07_view = ReportingView(self._state, self._settings, self)
        self._step_views: list[QWidget] = [
            self.step_01_view,
            self.step_02_view,
            self.step_03_view,
            self.step_04_view,
            self.step_05_view,
            self.step_06_view,
            self.step_07_view,
        ]
        self.step_stack = StepStackProxy(self)

        self._build_ui()
        self._wire_internal_signals()
        self.retranslate_ui()
        self.canvas.load_circuit(self._state.circuit)
        self.canvas.auto_layout()
        getattr(self.canvas, "fit_to_view", self.canvas.zoom_fit)()
        self.refresh_incidence_matrix()
        self.refresh_status_strip()
        self.set_active_view("canvas")

    def minimumSizeHint(self) -> QSize:
        return QSize(380, 320)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Top Switcher Bar
        self.switcher_bar = QFrame(self)
        self.switcher_bar.setObjectName("WorkspaceSwitcherBar")
        self.switcher_bar.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        sw_layout = QHBoxLayout(self.switcher_bar)
        sw_layout.setContentsMargins(8, 0, 8, 0)
        sw_layout.setSpacing(2)

        self.workspace_tab_bar = QTabBar(self.switcher_bar)
        self.workspace_tab_bar.setObjectName("WorkspaceModeTabBar")
        self.workspace_tab_bar.setExpanding(False)
        self.workspace_tab_bar.setElideMode(Qt.TextElideMode.ElideNone)
        self.workspace_tab_bar.setUsesScrollButtons(True)
        self.workspace_tab_bar.setDrawBase(False)
        for _mode in _VIEW_MODES:
            self.workspace_tab_bar.addTab("")
        self.workspace_tab_bar.currentChanged.connect(self._on_workspace_tab_changed)
        sw_layout.addWidget(self.workspace_tab_bar, 1)
        root.addWidget(self.switcher_bar)

        # Central 4-Mode Stack
        self.mode_stack = _ShrinkableStackedWidget(self)
        self.mode_stack.setObjectName("WorkspaceModeStack")

        # Mode 0: Canvas-First View
        self.canvas_page = QWidget(self.mode_stack)
        canvas_layout = QVBoxLayout(self.canvas_page)
        canvas_layout.setContentsMargins(0, 0, 0, 0)
        canvas_layout.setSpacing(0)

        self.canvas = FlowsheetCanvas(self.canvas_page, interactive=True)
        self.canvas.setObjectName("PrimaryFlowsheetCanvas")
        canvas_layout.addWidget(self.canvas, 1)

        # Toggleable Matrix A Drawer (hidden by default to maximize canvas space)
        self.matrix_drawer = QFrame(self.canvas_page)
        self.matrix_drawer.setObjectName("MatrixPreviewDrawer")
        self.matrix_drawer.setMinimumHeight(120)
        self.matrix_drawer.setMaximumHeight(200)
        drawer_layout = QVBoxLayout(self.matrix_drawer)
        drawer_layout.setContentsMargins(8, 6, 8, 6)
        drawer_layout.setSpacing(4)

        self.matrix_title_lbl = QLabel(self.matrix_drawer)
        self.matrix_title_lbl.setFont(ui_font(9.0, bold=True))
        drawer_layout.addWidget(self.matrix_title_lbl)

        self.matrix_table = QTableWidget(0, 0, self.matrix_drawer)
        self.matrix_table.setObjectName("WorkspaceIncidenceMatrixTable")
        configure_dense_table(self.matrix_table, stretch_last=False, editable=False)
        self.matrix_table.verticalHeader().setVisible(True)
        self.matrix_table.setFont(monospace_font(9.0))
        drawer_layout.addWidget(self.matrix_table, 1)

        self.matrix_drawer.setVisible(False)
        canvas_layout.addWidget(self.matrix_drawer)

        # Bottom CAD Strip
        self.bottom_strip = QFrame(self.canvas_page)
        self.bottom_strip.setObjectName("CanvasBottomStrip")
        self.bottom_strip.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        strip_layout = FlowLayout(self.bottom_strip, margin=6, h_spacing=8, v_spacing=4)

        self.summary_label = QLabel(self.bottom_strip)
        self.summary_label.setFont(monospace_font(8.6, bold=True))
        self.summary_label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        strip_layout.addWidget(self.summary_label)

        # Observability Legend Pills (visible in Step 04)
        self.obs_legend_widget = QWidget(self.bottom_strip)
        self.obs_legend_widget.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        obs_layout = QHBoxLayout(self.obs_legend_widget)
        obs_layout.setContentsMargins(0, 0, 0, 0)
        obs_layout.setSpacing(4)
        self.obs_pill_redundant = self._make_legend_pill("validated")
        self.obs_pill_non_redundant = self._make_legend_pill("pending")
        self.obs_pill_observable = self._make_legend_pill("warning")
        self.obs_pill_unobservable = self._make_legend_pill("error")
        for pill in (
            self.obs_pill_redundant,
            self.obs_pill_non_redundant,
            self.obs_pill_observable,
            self.obs_pill_unobservable,
        ):
            obs_layout.addWidget(pill)
        self.obs_legend_widget.setVisible(False)
        strip_layout.addWidget(self.obs_legend_widget)

        self.quick_diag_btn = QPushButton(self.bottom_strip)
        self.quick_diag_btn.setProperty("compact", True)
        self.quick_solve_btn = QPushButton(self.bottom_strip)
        self.quick_solve_btn.setProperty("compact", True)
        self.quick_solve_btn.setProperty("accent", True)
        self.quick_batch_btn = QPushButton(self.bottom_strip)
        self.quick_batch_btn.setProperty("compact", True)
        self.toggle_matrix_btn = QPushButton(self.bottom_strip)
        self.toggle_matrix_btn.setProperty("compact", True)
        self.toggle_matrix_btn.setCheckable(True)

        for btn in (
            self.quick_diag_btn,
            self.quick_solve_btn,
            self.quick_batch_btn,
            self.toggle_matrix_btn,
        ):
            btn.setIconSize(QSize(15, 15))
            strip_layout.addWidget(btn)

        canvas_layout.addWidget(self.bottom_strip)
        self.mode_stack.addWidget(self.canvas_page)

        # Mode 1: Tabbed Data / ETL / Sigma / Diagnostics / Solver / Matrix
        self.data_tabs = _ShrinkableTabWidget(self.mode_stack)
        self.data_tabs.setObjectName("WorkspaceDataTabs")
        data_tb = self.data_tabs.tabBar()
        data_tb.setExpanding(False)
        data_tb.setElideMode(Qt.TextElideMode.ElideNone)
        data_tb.setUsesScrollButtons(True)
        self.data_tabs.addTab(self.step_02_view, "")
        self.data_tabs.addTab(self.step_03_view, "")
        self.data_tabs.addTab(self.step_04_view, "")
        self.data_tabs.addTab(self.step_05_view, "")
        self.data_tabs.addTab(self.step_01_view, "")
        self.mode_stack.addWidget(self.data_tabs)

        # Mode 2: Charts & KPIs
        self.mode_stack.addWidget(self.step_06_view)

        # Mode 3: Corporate Reporting
        self.mode_stack.addWidget(self.step_07_view)

        root.addWidget(self.mode_stack, 1)

    def _make_legend_pill(self, role: str) -> QLabel:
        lbl = QLabel(self.bottom_strip)
        lbl.setContentsMargins(6, 2, 6, 2)
        lbl.setFont(ui_font(7.8, bold=True))
        apply_status_role(lbl, role)
        return lbl

    def _wire_internal_signals(self) -> None:
        self.toggle_matrix_btn.toggled.connect(self.toggle_matrix_drawer)
        self.quick_diag_btn.clicked.connect(self._on_quick_diag_clicked)
        self.quick_solve_btn.clicked.connect(self._on_quick_solve_clicked)
        self.quick_batch_btn.clicked.connect(self._on_quick_batch_clicked)

        self.canvas.circuit_changed.connect(self._on_canvas_circuit_changed)
        self.step_01_view.topology_modified.connect(self._on_step01_topology_modified)
        self.data_tabs.currentChanged.connect(self._on_data_tab_changed)

    def _on_quick_diag_clicked(self) -> None:
        if self.receivers(SIGNAL("run_diagnostics_requested()")) > 0:
            self.run_diagnostics_requested.emit()
        else:
            self.step_04_view.run_diagnostics()
            self._sync_step_overlay(3)

    def _on_quick_solve_clicked(self) -> None:
        if self.receivers(SIGNAL("run_reconcile_requested()")) > 0:
            self.run_reconcile_requested.emit()
        else:
            self.step_05_view.run_single()

    def _on_quick_batch_clicked(self) -> None:
        if self.receivers(SIGNAL("run_batch_requested()")) > 0:
            self.run_batch_requested.emit()
        else:
            self.step_05_view.run_batch()

    def _on_workspace_tab_changed(self, idx: int) -> None:
        if 0 <= idx < len(_VIEW_MODES):
            self.set_active_view(_VIEW_MODES[idx])

    def _on_canvas_circuit_changed(self, circuit) -> None:
        self._state.circuit = circuit
        self._syncing_topology = True
        try:
            self.refresh_incidence_matrix()
            self.refresh_status_strip()
        finally:
            self._syncing_topology = False

    def _on_step01_topology_modified(self) -> None:
        if self._syncing_topology:
            return
        self.canvas.load_circuit(self._state.circuit)
        self.refresh_incidence_matrix()
        self.refresh_status_strip()

    def _on_data_tab_changed(self, idx: int) -> None:
        if idx == 4:
            self.step_01_view.sync_from_state()
        else:
            w = self.data_tabs.widget(idx)
            if w is not None and hasattr(w, "refresh_from_state"):
                w.refresh_from_state()

    def _sync_step_overlay(self, step_idx: int) -> None:
        self.canvas.set_overlay_state(
            cleaned_dataset=self._state.cleaned_dataset,
            observability_report=self._state.observability_report,
            reconciliation_result=self._state.reconciliation_result,
            active_step_idx=step_idx,
            active_period_idx=self._state.selected_period_idx,
        )
        self.obs_legend_widget.setVisible(step_idx == 3)
        self.refresh_status_strip()

    # --- Public Workspace API ---

    def active_view(self) -> str:
        return self._active_mode

    def set_active_view(self, mode: str, subtab_idx: Optional[int] = None) -> None:
        """Switch central workspace between 'canvas', 'data', 'charts', and 'reports'."""
        if mode not in _VIEW_MODES:
            raise ValueError(f"Unknown workspace mode: '{mode}'.")
        self._active_mode = mode
        mode_idx = _VIEW_MODES.index(mode)
        self.mode_stack.setCurrentIndex(mode_idx)
        if self.workspace_tab_bar.currentIndex() != mode_idx:
            self.workspace_tab_bar.blockSignals(True)
            self.workspace_tab_bar.setCurrentIndex(mode_idx)
            self.workspace_tab_bar.blockSignals(False)

        if mode == "canvas":
            self._sync_step_overlay(self.step_stack.currentIndex())
            self.refresh_incidence_matrix()
            getattr(self.canvas, "fit_to_view", self.canvas.zoom_fit)()
        elif mode == "data":
            if subtab_idx is not None and 0 <= subtab_idx < self.data_tabs.count():
                self.data_tabs.setCurrentIndex(subtab_idx)
            self._on_data_tab_changed(self.data_tabs.currentIndex())
        elif mode == "charts":
            self.step_06_view.refresh_from_state()
        elif mode == "reports":
            self.step_07_view.refresh_from_state()

        self.view_mode_changed.emit(mode)

    def set_current_step_index(self, step_idx: int) -> None:
        """Update logical workflow step index and refresh canvas overlays."""
        self.step_stack.setCurrentIndex(step_idx)

    def toggle_matrix_drawer(self, visible: Optional[bool] = None) -> None:
        """Show or hide the bottom Incidence Matrix A drawer inside the Canvas mode."""
        target = (not self.matrix_drawer.isVisible()) if visible is None else bool(visible)
        self.matrix_drawer.setVisible(target)
        if self.toggle_matrix_btn.isChecked() != target:
            self.toggle_matrix_btn.blockSignals(True)
            self.toggle_matrix_btn.setChecked(target)
            self.toggle_matrix_btn.blockSignals(False)
        if target:
            self.refresh_incidence_matrix()

    def refresh_incidence_matrix(self) -> None:
        """Rebuild the live Incidence Matrix A preview table from ProjectState.circuit."""
        mat, node_ids, stream_ids = build_incidence_matrix(self._state.circuit)
        palette = get_theme_palette(self._theme)
        self.matrix_table.setRowCount(len(node_ids))
        self.matrix_table.setColumnCount(len(stream_ids))
        self.matrix_table.setVerticalHeaderLabels(node_ids)
        self.matrix_table.setHorizontalHeaderLabels(stream_ids)

        for r in range(len(node_ids)):
            for c in range(len(stream_ids)):
                val = int(round(float(mat[r, c])))
                if val > 0:
                    text, color, bold = "+1", palette["validated"], True
                elif val < 0:
                    text, color, bold = "-1", palette["error"], True
                else:
                    text, color, bold = "0", palette["text_secondary"], False
                self.matrix_table.setItem(
                    r,
                    c,
                    make_text_item(
                        text,
                        align=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                        color_hex=color,
                        mono=True,
                        bold=bold,
                    ),
                )
        self.matrix_table.resizeColumnsToContents()

    def refresh_status_strip(self) -> None:
        """Refresh bottom summary counters, observability legend visibility, and quick actions."""
        c = self._state.circuit
        obs = self._state.observability_report
        dof_str = str(obs.degrees_of_freedom) if obs is not None else self._i18n.t("common.na")
        summary_text = self._i18n.t(
            "workspace.status_summary_fmt",
            nodes=len(c.nodes),
            streams=len(c.streams),
            species=len(c.species),
            dof=dof_str,
        )
        self.summary_label.setText(summary_text)
        self.summary_label.setToolTip(summary_text)
        self.bottom_strip.updateGeometry()
        diag_ok = self._state.is_step_unlocked(3) or self._state.cleaned_dataset is not None
        solve_ok = self._state.is_step_unlocked(4) or (
            obs is not None and obs.can_reconcile and self._state.cleaned_dataset is not None
        )
        self.quick_diag_btn.setEnabled(diag_ok)
        self.quick_solve_btn.setEnabled(solve_ok)
        self.quick_batch_btn.setEnabled(
            solve_ok and self._state.cleaned_dataset is not None and len(self._state.cleaned_dataset.periods) > 0
        )

    def set_theme(self, theme: str) -> None:
        """Propagate Light/Dark theme to canvas, bottom strip, matrix drawer, and all 7 views."""
        self._theme = "dark" if theme == "dark" else "light"
        self.canvas.set_theme(self._theme)
        for view in self._step_views:
            if hasattr(view, "set_theme"):
                view.set_theme(self._theme)
        self._update_icons()
        self.refresh_incidence_matrix()

    def _update_icons(self) -> None:
        t = self._theme
        self.quick_diag_btn.setIcon(load_svg_icon("matrix.svg", t, size=15))
        self.quick_solve_btn.setIcon(
            load_svg_icon("play.svg", t, color_override="#FFFFFF", size=15)
        )
        self.quick_batch_btn.setIcon(load_svg_icon("scatter_plot.svg", t, size=15))
        self.toggle_matrix_btn.setIcon(load_svg_icon("matrix.svg", t, size=15))

    def retranslate_ui(self) -> None:
        """Retranslate switcher tabs, data subtabs, bottom strip, matrix drawer, canvas, and 7 views."""
        t = self._i18n.t
        self.workspace_tab_bar.setTabText(0, t("workspace.tab_flowsheet"))
        self.workspace_tab_bar.setTabText(1, t("workspace.tab_data_excel"))
        self.workspace_tab_bar.setTabText(2, t("workspace.tab_charts_kpis"))
        self.workspace_tab_bar.setTabText(3, t("workspace.tab_reports"))
        self.workspace_tab_bar.updateGeometry()

        self.data_tabs.setTabText(0, t("workspace.subtab_etl"))
        self.data_tabs.setTabText(1, t("workspace.subtab_sigma"))
        self.data_tabs.setTabText(2, t("workspace.subtab_diagnostics"))
        self.data_tabs.setTabText(3, t("workspace.subtab_solver"))
        self.data_tabs.setTabText(4, t("workspace.subtab_matrix"))

        self.matrix_title_lbl.setText(t("step01.incidence_matrix_title"))
        self.toggle_matrix_btn.setText(t("workspace.toggle_matrix_btn"))
        self.quick_diag_btn.setText(t("workspace.quick_diag_btn"))
        self.quick_solve_btn.setText(t("workspace.quick_solve_btn"))
        self.quick_batch_btn.setText(t("workspace.quick_batch_btn"))

        self.obs_pill_redundant.setText(t("workspace.obs_legend_redundant"))
        self.obs_pill_non_redundant.setText(t("workspace.obs_legend_non_redundant"))
        self.obs_pill_observable.setText(t("workspace.obs_legend_observable"))
        self.obs_pill_unobservable.setText(t("workspace.obs_legend_unobservable"))

        self.canvas.retranslate_ui()
        for view in self._step_views:
            if hasattr(view, "retranslate_ui"):
                view.retranslate_ui()

        self._update_icons()
        self.refresh_status_strip()
