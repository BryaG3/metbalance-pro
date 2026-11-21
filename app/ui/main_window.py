"""Main CAD-Industrial PySide6 shell with Canvas-First workspace and ArcMap collapsible docks."""

from __future__ import annotations

import ctypes
import sys
from typing import Optional

if sys.platform == "win32":
    import ctypes.wintypes

from PySide6.QtCore import QByteArray, QEvent, QPoint, QSize, QTimer, Qt
from PySide6.QtGui import QAction, QActionGroup, QCloseEvent, QMouseEvent
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QSizePolicy,
    QToolBar,
    QWidget,
)

from app.i18n.manager import get_i18n
from app.settings_manager import SettingsManager
from core.models import CircuitSpec, StepStatus
from core.observability import analyze_observability
from core.topology import validate_topology

from .collapsible_dock import CollapsibleDockWidget, SideTabStrip, VerticalTabButton
from .contextual_inspector_dock import ContextualInspectorDock
from .flowsheet_canvas import CanvasBlockKind
from .flowsheet_workspace import FlowsheetWorkspace
from .project_tree_dock import ProjectTreeDock
from .ui_common import (
    InspectorPayload,
    ProjectState,
    build_palette,
    load_stylesheet,
    load_svg_icon,
)


class MainToolBar(QToolBar):
    """ArcMap-style horizontal toolbar with magnetic top-zone and double-click re-dock."""

    def __init__(self, main_window: "MainWindow") -> None:
        super().__init__(main_window)
        self._main_window = main_window
        self._snap_band_px = 60
        self._has_left_dock_zone = False
        self._redocking = False

        self._snap_timer = QTimer(self)
        self._snap_timer.setSingleShot(True)
        self._snap_timer.timeout.connect(self._on_snap_timer_timeout)

        self.setAllowedAreas(Qt.ToolBarArea.TopToolBarArea)
        self.setOrientation(Qt.Orientation.Horizontal)
        self.setMovable(True)
        self.setFloatable(True)

        self.topLevelChanged.connect(self._on_top_level_changed)
        self.orientationChanged.connect(self._on_orientation_changed)

    def _on_top_level_changed(self, floating: bool) -> None:
        self._snap_timer.stop()
        if not self._redocking:
            self.setOrientation(Qt.Orientation.Horizontal)
        self._has_left_dock_zone = (not self.is_in_magnetic_dock_zone()) if floating else False

    def _on_orientation_changed(self, orientation: Qt.Orientation) -> None:
        if orientation != Qt.Orientation.Horizontal and not self._redocking:
            self.setOrientation(Qt.Orientation.Horizontal)

    def redock_top(self) -> None:
        """Re-dock a floating toolbar cleanly back into TopToolBarArea as horizontal."""
        self._snap_timer.stop()
        self._redocking = True
        self._has_left_dock_zone = False
        try:
            if self.isFloating() and hasattr(self, "setFloating"):
                self.setFloating(False)
            self.setOrientation(Qt.Orientation.Horizontal)
            self._main_window.addToolBar(Qt.ToolBarArea.TopToolBarArea, self)
            self.setOrientation(Qt.Orientation.Horizontal)
            self.show()
        finally:
            self._redocking = False

    def is_in_magnetic_dock_zone(self) -> bool:
        """Return True when floating toolbar overlaps the magnetic TopToolBarArea zone."""
        if not self.isFloating() or not self._main_window.isVisible():
            return False
        win_geo = self._main_window.frameGeometry()
        bar_geo = self.frameGeometry()
        menu_bar = self._main_window.menuBar()
        menu_h = menu_bar.height() if menu_bar is not None else 0
        anchor_y = self._main_window.mapToGlobal(QPoint(0, menu_h)).y()
        horiz_ok = (win_geo.left() - 40) <= bar_geo.center().x() <= (win_geo.right() + 40)
        vert_ok = (anchor_y - 36) <= bar_geo.top() <= (anchor_y + self._snap_band_px)
        return horiz_ok and vert_ok

    def try_magnetic_redock(self, *, force: bool = False) -> bool:
        """Re-dock floating toolbar when inside the magnetic top zone and eligible."""
        if self.isFloating() and not self._redocking:
            if self.is_in_magnetic_dock_zone():
                if force or self._has_left_dock_zone:
                    self.redock_top()
                    return True
            else:
                self._has_left_dock_zone = True
        return False

    def moveEvent(self, event) -> None:
        super().moveEvent(event)
        if not self.isFloating() or self._redocking:
            self._snap_timer.stop()
            return
        if not self.is_in_magnetic_dock_zone():
            self._has_left_dock_zone = True
            self._snap_timer.stop()
        elif self._has_left_dock_zone:
            self._snap_timer.start(120)

    def _on_snap_timer_timeout(self) -> None:
        if (
            self.isFloating()
            and self.isVisible()
            and self._main_window.isVisible()
            and not (QApplication.mouseButtons() & Qt.MouseButton.LeftButton)
        ):
            self.try_magnetic_redock(force=True)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        super().mouseReleaseEvent(event)
        if self.isFloating():
            self.try_magnetic_redock(force=True)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if self.isFloating():
            self.redock_top()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def event(self, ev: QEvent) -> bool:
        ev_type = ev.type()
        if ev_type == QEvent.Type.NonClientAreaMouseButtonDblClick and self.isFloating():
            self.redock_top()
            ev.accept()
            return True
        if ev_type == QEvent.Type.NonClientAreaMouseButtonRelease and self.isFloating():
            QTimer.singleShot(0, lambda: self.try_magnetic_redock(force=True))
        return super().event(ev)

    def nativeEvent(self, eventType, message):
        if (
            sys.platform == "win32"
            and bytes(eventType) in (b"windows_generic_MSG", b"windows_dispatcher_MSG")
            and self.isFloating()
        ):
            try:
                msg = ctypes.wintypes.MSG.from_address(int(message))
                if msg.message == 0x0232:
                    QTimer.singleShot(0, lambda: self.try_magnetic_redock(force=True))
            except Exception:
                pass
        try:
            return super().nativeEvent(eventType, message)
        except ValueError:
            return (False, 0)


_TopHorizontalToolBar = MainToolBar


class _CentralWorkspaceShell(QWidget):
    """Central QMainWindow shell container with bounded minimumSizeHint for dock splitting."""

    def minimumSizeHint(self) -> QSize:
        return QSize(380, 320)


class MainWindow(QMainWindow):
    """Primary desktop shell hosting Canvas-First workspace, auto-hide docks, menus, and toolbar."""

    def __init__(
        self,
        settings_manager: Optional[SettingsManager] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("MetBalanceMainWindow")
        self.resize(1380, 860)
        self.setMinimumSize(1024, 680)
        self.setDockOptions(
            QMainWindow.DockOption.AnimatedDocks
            | QMainWindow.DockOption.AllowNestedDocks
            | QMainWindow.DockOption.AllowTabbedDocks
        )

        app = QApplication.instance()
        if isinstance(app, QApplication) and not app.property("metbalance_fusion_applied"):
            app.setStyle("Fusion")
            app.setProperty("metbalance_fusion_applied", True)

        self.settings = settings_manager or SettingsManager()
        self.i18n = get_i18n()
        self.state = ProjectState()
        self._theme = self.settings.get_theme()
        self._default_geometry: Optional[QByteArray] = None
        self._default_state: Optional[QByteArray] = None

        self._syncing_canvas = False
        self._syncing_layout = False
        self._last_topology_sig = self._topology_signature(self.state.circuit)
        self._dock_last_area: dict[CollapsibleDockWidget, Qt.DockWidgetArea] = {}

        # Sync initial i18n language from persisted settings
        saved_lang = self.settings.get_language()
        if self.i18n.language != saved_lang:
            self.i18n.set_language(saved_lang)

        self._build_central_views()
        self._build_docks()
        self._build_menus_and_toolbar()
        self._build_status_bar()
        self._wire_signals()

        # Capture pristine default layout snapshot before restoring user layout
        self._default_geometry = self.saveGeometry()
        self._default_state = self.saveState()
        if not self.settings.has_default_layout():
            self.settings.save_default_layout(self._default_geometry, self._default_state)

        saved_geo = self.settings.load_window_geometry()
        saved_state = self.settings.load_window_state()
        if saved_geo is not None:
            self.restoreGeometry(saved_geo)
        if saved_state is not None:
            self.restoreState(saved_state)
            self._sanitize_restored_toolbar()
            self._sync_restored_docks()

        self.i18n.subscribe(self._on_language_changed)
        self.set_theme(self._theme, persist=False)
        self.retranslate_ui()
        self.go_to_step(0)
        self.canvas.load_circuit(self.state.circuit)
        self.canvas.auto_layout()
        self.canvas.zoom_fit()
        self.workspace.refresh_incidence_matrix()
        self.workspace.refresh_status_strip()
        self.contextual_inspector_dock.show_project_context()
        self.project_tree_dock.refresh_steps(self.state, 0)

    def _build_central_views(self) -> None:
        self._central_shell = _CentralWorkspaceShell(self)
        self._central_shell.setObjectName("CentralWorkspaceShell")
        self._central_shell.setMinimumWidth(380)
        self._central_shell.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        shell_layout = QHBoxLayout(self._central_shell)
        shell_layout.setContentsMargins(0, 0, 0, 0)
        shell_layout.setSpacing(0)

        self.left_tab_strip = SideTabStrip("left", self._central_shell)
        self.workspace = FlowsheetWorkspace(self.state, self.settings, self._central_shell)
        self.right_tab_strip = SideTabStrip("right", self._central_shell)

        shell_layout.addWidget(self.left_tab_strip)
        shell_layout.addWidget(self.workspace, 1)
        shell_layout.addWidget(self.right_tab_strip)
        self.setCentralWidget(self._central_shell)

        # Compatibility aliases for direct step/canvas access
        self.canvas = self.workspace.canvas
        self.step_stack = self.workspace.step_stack
        self.step_01_view = self.workspace.step_01_view
        self.step_02_view = self.workspace.step_02_view
        self.step_03_view = self.workspace.step_03_view
        self.step_04_view = self.workspace.step_04_view
        self.step_05_view = self.workspace.step_05_view
        self.step_06_view = self.workspace.step_06_view
        self.step_07_view = self.workspace.step_07_view
        self._step_views = [
            self.step_01_view,
            self.step_02_view,
            self.step_03_view,
            self.step_04_view,
            self.step_05_view,
            self.step_06_view,
            self.step_07_view,
        ]
        self._sync_unshown_step_geometries()

    def _sync_unshown_step_geometries(self) -> None:
        """Keep off-tab Step 01 splitter sized to workspace bounds before tab activation."""
        target_w = max(1000, self.width() - 580)
        target_h = max(600, self.height() - 160)
        if not self.step_01_view.isVisible():
            self.step_01_view.resize(target_w, target_h)
            self.step_01_view.main_splitter.resize(max(1000, target_w - 16), max(500, target_h - 80))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._sync_unshown_step_geometries()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._sync_unshown_step_geometries()

    def _build_docks(self) -> None:
        self.project_tree_dock = ProjectTreeDock(self)
        self.contextual_inspector_dock = ContextualInspectorDock(self.state, self)
        self.inspector_dock = self.contextual_inspector_dock

        self._dock_last_area = {
            self.project_tree_dock: Qt.DockWidgetArea.LeftDockWidgetArea,
            self.contextual_inspector_dock: Qt.DockWidgetArea.RightDockWidgetArea,
        }

        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.project_tree_dock)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.contextual_inspector_dock)
        self.resizeDocks(
            [self.project_tree_dock, self.contextual_inspector_dock],
            [260, 320],
            Qt.Orientation.Horizontal,
        )

        self._sync_dock_tab_registration(
            self.project_tree_dock, Qt.DockWidgetArea.LeftDockWidgetArea
        )
        self._sync_dock_tab_registration(
            self.contextual_inspector_dock, Qt.DockWidgetArea.RightDockWidgetArea
        )

    def _build_menus_and_toolbar(self) -> None:
        menu_bar = self.menuBar()

        # File menu
        self.menu_file: QMenu = menu_bar.addMenu("")
        self.act_file_new = QAction(self)
        self.act_file_open = QAction(self)
        self.act_file_sample_a = QAction(self)
        self.act_file_sample_b = QAction(self)
        self.act_file_export_excel = QAction(self)
        self.act_file_export_pdf = QAction(self)
        self.act_file_exit = QAction(self)

        self.menu_file.addAction(self.act_file_new)
        self.menu_file.addAction(self.act_file_open)
        self.menu_file.addSeparator()
        self.menu_file.addAction(self.act_file_sample_a)
        self.menu_file.addAction(self.act_file_sample_b)
        self.menu_file.addSeparator()
        self.menu_file.addAction(self.act_file_export_excel)
        self.menu_file.addAction(self.act_file_export_pdf)
        self.menu_file.addSeparator()
        self.menu_file.addAction(self.act_file_exit)

        # View menu
        self.menu_view: QMenu = menu_bar.addMenu("")
        self.act_theme_light = QAction(self)
        self.act_theme_light.setCheckable(True)
        self.act_theme_dark = QAction(self)
        self.act_theme_dark.setCheckable(True)
        theme_group = QActionGroup(self)
        theme_group.addAction(self.act_theme_light)
        theme_group.addAction(self.act_theme_dark)

        self.menu_docks = QMenu(self.menu_view)
        self.act_dock_tree = self.project_tree_dock.toggleViewAction()
        self.act_dock_inspector = self.contextual_inspector_dock.toggleViewAction()
        self.menu_docks.addAction(self.act_dock_tree)
        self.menu_docks.addAction(self.act_dock_inspector)

        self.act_reset_layout = QAction(self)

        self.menu_view.addAction(self.act_theme_light)
        self.menu_view.addAction(self.act_theme_dark)
        self.menu_view.addSeparator()
        self.menu_view.addMenu(self.menu_docks)
        self.menu_view.addAction(self.act_reset_layout)

        # Language menu
        self.menu_language: QMenu = menu_bar.addMenu("")
        self.act_lang_es = QAction(self)
        self.act_lang_es.setCheckable(True)
        self.act_lang_en = QAction(self)
        self.act_lang_en.setCheckable(True)
        lang_group = QActionGroup(self)
        lang_group.addAction(self.act_lang_es)
        lang_group.addAction(self.act_lang_en)
        self.menu_language.addAction(self.act_lang_es)
        self.menu_language.addAction(self.act_lang_en)

        # Tools menu
        self.menu_tools: QMenu = menu_bar.addMenu("")
        self.act_tools_diag = QAction(self)
        self.act_tools_reconcile = QAction(self)
        self.act_tools_batch = QAction(self)
        self.act_tools_branding = QAction(self)
        self.menu_tools.addAction(self.act_tools_diag)
        self.menu_tools.addAction(self.act_tools_reconcile)
        self.menu_tools.addAction(self.act_tools_batch)
        self.menu_tools.addSeparator()
        self.menu_tools.addAction(self.act_tools_branding)

        # Help menu
        self.menu_help: QMenu = menu_bar.addMenu("")
        self.act_help_amira = QAction(self)
        self.act_help_about = QAction(self)
        self.menu_help.addAction(self.act_help_amira)
        self.menu_help.addAction(self.act_help_about)

        # Concise Engineering Toolbar (ArcMap-style horizontal top-only dock)
        self.toolbar = MainToolBar(self)
        self.toolbar.setObjectName("MainEngineeringToolBar")
        self.toolbar.setAllowedAreas(Qt.ToolBarArea.TopToolBarArea)
        self.toolbar.setOrientation(Qt.Orientation.Horizontal)
        self.toolbar.setMovable(True)
        self.toolbar.setFloatable(True)
        self.toolbar.setIconSize(QSize(18, 18))
        self.toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, self.toolbar)

        self.tb_new = QAction(self)
        self.tb_open = QAction(self)
        self.tb_diag = QAction(self)
        self.tb_solver = QAction(self)
        self.tb_batch = QAction(self)
        self.tb_excel = QAction(self)
        self.tb_pdf = QAction(self)
        self.tb_theme = QAction(self)
        self.tb_lang = QAction(self)
        self.tb_reset_layout = QAction(self)

        self.toolbar.addAction(self.tb_new)
        self.toolbar.addAction(self.tb_open)
        self.toolbar.addSeparator()
        self.toolbar.addAction(self.tb_diag)
        self.toolbar.addAction(self.tb_solver)
        self.toolbar.addAction(self.tb_batch)
        self.toolbar.addSeparator()
        self.toolbar.addAction(self.tb_excel)
        self.toolbar.addAction(self.tb_pdf)
        self.toolbar.addSeparator()
        self.toolbar.addAction(self.tb_theme)
        self.toolbar.addAction(self.tb_lang)
        self.toolbar.addAction(self.tb_reset_layout)

    def _build_status_bar(self) -> None:
        sb = self.statusBar()
        self.status_context_label = QLabel(self)
        sb.addPermanentWidget(self.status_context_label)

    def _wire_signals(self) -> None:
        # Menu & toolbar actions
        self.act_file_new.triggered.connect(self.new_project)
        self.tb_new.triggered.connect(self.new_project)
        self.act_file_open.triggered.connect(self._open_data_file)
        self.tb_open.triggered.connect(self._open_data_file)
        self.act_file_sample_a.triggered.connect(self.load_sample_a)
        self.act_file_sample_b.triggered.connect(self.load_sample_b)
        self.act_file_export_excel.triggered.connect(self._trigger_export_excel)
        self.tb_excel.triggered.connect(self._trigger_export_excel)
        self.act_file_export_pdf.triggered.connect(self._trigger_export_pdf)
        self.tb_pdf.triggered.connect(self._trigger_export_pdf)
        self.act_file_exit.triggered.connect(self.close)

        self.act_theme_light.triggered.connect(lambda: self.set_theme("light"))
        self.act_theme_dark.triggered.connect(lambda: self.set_theme("dark"))
        self.tb_theme.triggered.connect(self.toggle_theme)
        self.act_reset_layout.triggered.connect(self.reset_layout)
        self.tb_reset_layout.triggered.connect(self.reset_layout)

        self.act_lang_es.triggered.connect(lambda: self.set_language("es"))
        self.act_lang_en.triggered.connect(lambda: self.set_language("en"))
        self.tb_lang.triggered.connect(self.toggle_language)

        self.act_tools_diag.triggered.connect(self._run_tools_diagnostics)
        self.tb_diag.triggered.connect(self._run_tools_diagnostics)
        self.act_tools_reconcile.triggered.connect(self._run_tools_reconcile)
        self.tb_solver.triggered.connect(self._run_tools_reconcile)
        self.act_tools_batch.triggered.connect(self._run_tools_batch)
        self.tb_batch.triggered.connect(self._run_tools_batch)
        self.act_tools_branding.triggered.connect(self._open_branding_step)

        self.act_help_about.triggered.connect(self._show_about_dialog)
        self.act_help_amira.triggered.connect(self._show_amira_dialog)

        # Collapsible dock & side strip signals
        for dock in (self.project_tree_dock, self.contextual_inspector_dock):
            dock.collapse_requested.connect(self.collapse_dock)
            dock.dockLocationChanged.connect(
                lambda area, d=dock: self._on_dock_location_changed(d, area)
            )
            dock.visibilityChanged.connect(
                lambda visible, d=dock: self._on_dock_visibility_changed(d, visible)
            )
            if hasattr(dock, "dock_left_requested"):
                dock.dock_left_requested.connect(
                    lambda target: self.dock_to_side(
                        target, Qt.DockWidgetArea.LeftDockWidgetArea
                    )
                )
            if hasattr(dock, "dock_right_requested"):
                dock.dock_right_requested.connect(
                    lambda target: self.dock_to_side(
                        target, Qt.DockWidgetArea.RightDockWidgetArea
                    )
                )
            if hasattr(dock, "stack_below_requested"):
                dock.stack_below_requested.connect(self.stack_dock_below_other)
        self.left_tab_strip.tab_clicked.connect(self.expand_dock)
        self.right_tab_strip.tab_clicked.connect(self.expand_dock)

        # Project tree & step navigation
        self.project_tree_dock.step_selected.connect(
            lambda idx: self.go_to_step(idx, show_canvas=idx < 5)
        )
        self.project_tree_dock.locked_step_clicked.connect(self._on_locked_step_clicked)

        # Canvas <-> Workspace <-> Contextual Inspector synchronization
        self.canvas.circuit_changed.connect(self._on_canvas_circuit_changed)
        self.canvas.node_moved.connect(self._on_canvas_node_moved)
        self.canvas.block_selected.connect(self._on_canvas_block_selected)
        self.canvas.stream_selected.connect(self._on_canvas_stream_selected)
        self.canvas.selection_cleared.connect(self._on_canvas_selection_cleared)
        self.canvas.template_requested.connect(self._on_canvas_template_requested)

        self.workspace.view_mode_changed.connect(self._on_workspace_mode_changed)
        self.workspace.run_diagnostics_requested.connect(self._on_quick_diagnostics)
        self.workspace.run_reconcile_requested.connect(self._on_quick_reconcile)
        self.workspace.run_batch_requested.connect(self._on_quick_batch)

        self.contextual_inspector_dock.circuit_edited.connect(self._on_inspector_circuit_edited)
        self.contextual_inspector_dock.stream_renamed.connect(self._on_inspector_stream_renamed)
        self.contextual_inspector_dock.stage_renamed.connect(self._on_inspector_stage_renamed)
        self.contextual_inspector_dock.manual_data_changed.connect(
            self._on_inspector_manual_data_changed
        )
        self.contextual_inspector_dock.uncertainty_changed.connect(
            self._on_inspector_uncertainty_changed
        )
        self.contextual_inspector_dock.import_data_requested.connect(self._open_data_file)
        self.contextual_inspector_dock.open_etl_requested.connect(self._on_open_etl_requested)
        self.contextual_inspector_dock.open_full_table_requested.connect(
            self._on_open_full_table_requested
        )
        self.contextual_inspector_dock.outlier_compare_requested.connect(
            self._on_inspector_outlier_compare
        )
        self.contextual_inspector_dock.stream_jump_requested.connect(self.canvas.select_stream)
        self.contextual_inspector_dock.run_diagnostics_requested.connect(self._on_quick_diagnostics)
        self.contextual_inspector_dock.run_reconcile_requested.connect(self._on_quick_reconcile)

        # Step view signals
        self.step_01_view.topology_modified.connect(self._on_step01_topology_modified)
        self.step_01_view.step_validated.connect(lambda _ok: self._on_workflow_state_changed())
        self.step_01_view.next_step_requested.connect(lambda: self.go_to_step(1))
        self.step_01_view.inspector_requested.connect(self.contextual_inspector_dock.set_payload)

        self.step_02_view.dataset_profiled.connect(self._on_dataset_profiled)
        self.step_02_view.dataset_cleaned.connect(self._on_dataset_cleaned)
        self.step_02_view.prev_step_requested.connect(lambda: self.go_to_step(0))
        self.step_02_view.next_step_requested.connect(lambda: self.go_to_step(2))
        self.step_02_view.inspector_requested.connect(self.contextual_inspector_dock.set_payload)

        self.step_03_view.uncertainty_validated.connect(self._on_step03_uncertainty_validated)
        self.step_03_view.prev_step_requested.connect(lambda: self.go_to_step(1))
        self.step_03_view.next_step_requested.connect(lambda: self.go_to_step(3))
        self.step_03_view.inspector_requested.connect(self.contextual_inspector_dock.set_payload)

        self.step_04_view.diagnostics_completed.connect(self._on_diagnostics_completed)
        self.step_04_view.prev_step_requested.connect(lambda: self.go_to_step(2))
        self.step_04_view.next_step_requested.connect(lambda: self.go_to_step(4))
        self.step_04_view.inspector_requested.connect(self.contextual_inspector_dock.set_payload)

        self.step_05_view.reconciliation_completed.connect(self._on_reconciliation_completed)
        self.step_05_view.prev_step_requested.connect(lambda: self.go_to_step(3))
        self.step_05_view.next_step_requested.connect(lambda: self.go_to_step(5))
        self.step_05_view.inspector_requested.connect(self.contextual_inspector_dock.set_payload)

        self.step_06_view.prev_step_requested.connect(lambda: self.go_to_step(4))
        self.step_06_view.next_step_requested.connect(lambda: self.go_to_step(6))
        self.step_06_view.inspector_requested.connect(self.contextual_inspector_dock.set_payload)

        self.step_07_view.prev_step_requested.connect(lambda: self.go_to_step(5))
        self.step_07_view.inspector_requested.connect(self.contextual_inspector_dock.set_payload)

    # --- ArcMap Auto-Hide Collapsible Dock Management ---

    def _strip_for_area(self, area: Qt.DockWidgetArea) -> SideTabStrip:
        return (
            self.right_tab_strip
            if area == Qt.DockWidgetArea.RightDockWidgetArea
            else self.left_tab_strip
        )

    def _other_strip_for_area(self, area: Qt.DockWidgetArea) -> SideTabStrip:
        return (
            self.left_tab_strip
            if area == Qt.DockWidgetArea.RightDockWidgetArea
            else self.right_tab_strip
        )

    def _resolve_dock_area(self, dock: CollapsibleDockWidget) -> Qt.DockWidgetArea:
        area = self.dockWidgetArea(dock)
        if area in (
            Qt.DockWidgetArea.LeftDockWidgetArea,
            Qt.DockWidgetArea.RightDockWidgetArea,
        ):
            self._dock_last_area[dock] = area
            return area
        return self._dock_last_area.get(dock, Qt.DockWidgetArea.LeftDockWidgetArea)

    def _sync_dock_tab_registration(
        self, dock: CollapsibleDockWidget, area: Qt.DockWidgetArea
    ) -> VerticalTabButton:
        target_strip = self._strip_for_area(area)
        other_strip = self._other_strip_for_area(area)

        if other_strip.tab_button(dock) is not None:
            old_btn = other_strip.unregister_tab(dock)
            if old_btn is not None:
                old_btn.deleteLater()

        btn = target_strip.tab_button(dock)
        if btn is None:
            btn = target_strip.register_tab(dock)
        target_strip.set_tab_visible(dock, dock.is_collapsed())
        other_strip.refresh_visibility()
        return btn

    def _activate_central_layout(self) -> None:
        layout = self._central_shell.layout()
        if layout is not None:
            layout.activate()

    def collapse_dock(self, dock: CollapsibleDockWidget) -> None:
        """Collapse a dock into a 28px vertical side tab on its current edge."""
        area = self._resolve_dock_area(dock)
        self._syncing_layout = True
        try:
            if dock.isFloating():
                dock.setFloating(False)
                self.addDockWidget(area, dock)
            dock.set_collapsed_state(True)
            dock.hide()
            self._sync_dock_tab_registration(dock, area)
            self._activate_central_layout()
        finally:
            self._syncing_layout = False

    def _prepare_dock_for_area(self, dock: CollapsibleDockWidget) -> None:
        self.left_tab_strip.set_tab_visible(dock, False)
        self.right_tab_strip.set_tab_visible(dock, False)
        dock.set_collapsed_state(False)
        if dock.isFloating():
            dock.setFloating(False)

    def dock_to_side(self, dock: CollapsibleDockWidget, area: Qt.DockWidgetArea) -> None:
        """Uncollapse, unfloat, and dock the panel onto LeftDockWidgetArea or RightDockWidgetArea."""
        target_area = (
            area
            if area
            in (Qt.DockWidgetArea.LeftDockWidgetArea, Qt.DockWidgetArea.RightDockWidgetArea)
            else Qt.DockWidgetArea.LeftDockWidgetArea
        )
        self._syncing_layout = True
        try:
            self._prepare_dock_for_area(dock)
            self.addDockWidget(target_area, dock)
            dock.show()
            dock.raise_()
            self._dock_last_area[dock] = target_area
            self._sync_dock_tab_registration(dock, target_area)
            self._activate_central_layout()
        finally:
            self._syncing_layout = False

    def stack_dock_below_other(self, dock: CollapsibleDockWidget) -> None:
        """Stack dock vertically below its sibling dock in the sibling's active edge area."""
        other = (
            self.contextual_inspector_dock
            if dock is self.project_tree_dock
            else self.project_tree_dock
        )
        target_area = self._resolve_dock_area(other)
        self._syncing_layout = True
        try:
            self._prepare_dock_for_area(other)
            self._prepare_dock_for_area(dock)
            if self.dockWidgetArea(other) != target_area:
                self.addDockWidget(target_area, other)
            other.show()

            self.addDockWidget(target_area, dock)
            dock.show()
            self.splitDockWidget(other, dock, Qt.Orientation.Vertical)
            half_h = max(200, self.height() // 2)
            self.resizeDocks([other, dock], [half_h, half_h], Qt.Orientation.Vertical)

            for member in (other, dock):
                self._dock_last_area[member] = target_area
                self._sync_dock_tab_registration(member, target_area)
            other.raise_()
            dock.raise_()
            self._activate_central_layout()
        finally:
            self._syncing_layout = False

    def expand_dock(self, dock: CollapsibleDockWidget) -> None:
        """Expand a collapsed dock back into its docked panel and hide its side tab."""
        area = self._resolve_dock_area(dock)
        self._syncing_layout = True
        try:
            self.left_tab_strip.set_tab_visible(dock, False)
            self.right_tab_strip.set_tab_visible(dock, False)
            dock.set_collapsed_state(False)
            if (
                not dock.isFloating()
                and self.dockWidgetArea(dock)
                not in (
                    Qt.DockWidgetArea.LeftDockWidgetArea,
                    Qt.DockWidgetArea.RightDockWidgetArea,
                )
            ):
                self.addDockWidget(area, dock)
            dock.show()
            dock.raise_()
            self._activate_central_layout()
        finally:
            self._syncing_layout = False

    def toggle_dock_collapsed(self, dock: CollapsibleDockWidget) -> None:
        """Toggle collapsed auto-hide state for dock."""
        if dock.is_collapsed():
            self.expand_dock(dock)
        else:
            self.collapse_dock(dock)

    def is_dock_collapsed(self, dock: CollapsibleDockWidget) -> bool:
        """Return True if dock is currently collapsed into its edge side tab strip."""
        return dock.is_collapsed()

    def dock_side_tab(self, dock: CollapsibleDockWidget) -> Optional[VerticalTabButton]:
        """Return the VerticalTabButton registered for dock on left or right strip."""
        return self.left_tab_strip.tab_button(dock) or self.right_tab_strip.tab_button(dock)

    def _on_dock_location_changed(
        self, dock: CollapsibleDockWidget, area: Qt.DockWidgetArea
    ) -> None:
        if area not in (
            Qt.DockWidgetArea.LeftDockWidgetArea,
            Qt.DockWidgetArea.RightDockWidgetArea,
        ):
            return
        self._dock_last_area[dock] = area
        self._sync_dock_tab_registration(dock, area)
        self._activate_central_layout()

    def _on_dock_visibility_changed(self, dock: CollapsibleDockWidget, visible: bool) -> None:
        if self._syncing_layout:
            return
        if visible and dock.is_collapsed():
            self.expand_dock(dock)

    def _sync_restored_docks(self) -> None:
        for dock in (self.project_tree_dock, self.contextual_inspector_dock):
            area = self._resolve_dock_area(dock)
            if not dock.isFloating() and dock.isHidden():
                dock.set_collapsed_state(True)
            else:
                dock.set_collapsed_state(False)
            self._sync_dock_tab_registration(dock, area)
        self._activate_central_layout()

    # --- Canvas <-> Contextual Inspector <-> 7-Step Synchronization ---

    @staticmethod
    def _topology_signature(circuit: CircuitSpec) -> tuple:
        return (
            tuple(n.id for n in circuit.nodes),
            tuple((s.id, s.from_node, s.to_node, s.role.value) for s in circuit.streams),
            tuple((sp.id, sp.unit.value) for sp in circuit.species),
        )

    def _refresh_live_observability(self) -> None:
        """Recompute ObservabilityReport silently when a dataset and valid circuit exist."""
        point = self.state.current_period()
        if point is None or validate_topology(self.state.circuit):
            self.state.observability_report = None
            return
        try:
            self.state.observability_report = analyze_observability(self.state.circuit, point)
        except Exception:
            self.state.observability_report = None

    def _sync_canvas_overlays(self) -> None:
        self.canvas.set_overlay_state(
            cleaned_dataset=self.state.cleaned_dataset,
            observability_report=self.state.observability_report,
            reconciliation_result=self.state.reconciliation_result,
            active_step_idx=self.step_stack.currentIndex(),
            active_period_idx=self.state.selected_period_idx,
        )

    def _sync_topology_change_downstream(self, circuit: CircuitSpec) -> None:
        new_sig = self._topology_signature(circuit)
        if new_sig != self._last_topology_sig:
            self._last_topology_sig = new_sig
            self.state.step_statuses[0] = StepStatus.PENDING
            self.state.step_block_reasons.pop(0, None)
            self.state.clear_from_step(1)

    def _on_canvas_circuit_changed(self, circuit: CircuitSpec) -> None:
        if self._syncing_canvas:
            return
        self._syncing_canvas = True
        try:
            self.state.circuit = circuit
            self._sync_topology_change_downstream(circuit)
            self._refresh_live_observability()
            self.step_01_view.sync_from_state()
            self._sync_canvas_overlays()
            self.workspace.refresh_incidence_matrix()
            self.workspace.refresh_status_strip()
            self.contextual_inspector_dock.refresh_from_state()
            self._on_workflow_state_changed()
        finally:
            self._syncing_canvas = False

    def _on_canvas_node_moved(self, _block_id: str, _x: float, _y: float) -> None:
        if self._syncing_canvas:
            return
        self.state.circuit = self.canvas.sync_circuit_from_canvas()

    def _on_canvas_block_selected(self, block_id: str, kind_val: str) -> None:
        if self.is_dock_collapsed(self.contextual_inspector_dock):
            self.expand_dock(self.contextual_inspector_dock)
        if kind_val == CanvasBlockKind.PROCESS.value:
            self.contextual_inspector_dock.show_stage_context(block_id)
        else:
            stream_id = self.canvas.stream_for_terminal(block_id)
            if stream_id:
                self.contextual_inspector_dock.show_stream_context(stream_id)
            else:
                self.contextual_inspector_dock.show_project_context()

    def _on_canvas_stream_selected(self, stream_id: str) -> None:
        if self.is_dock_collapsed(self.contextual_inspector_dock):
            self.expand_dock(self.contextual_inspector_dock)
        self.contextual_inspector_dock.show_stream_context(stream_id)

    def _on_canvas_selection_cleared(self) -> None:
        self.contextual_inspector_dock.show_project_context()

    def _sync_canvas_after_template(self) -> None:
        self._last_topology_sig = self._topology_signature(self.state.circuit)
        self.canvas.load_circuit(self.state.circuit)
        self.canvas.auto_layout()
        getattr(self.canvas, "fit_to_view", self.canvas.zoom_fit)()
        self.workspace.refresh_incidence_matrix()
        self.workspace.refresh_status_strip()

    def _on_canvas_template_requested(self, tpl_id: str) -> None:
        self._syncing_canvas = True
        try:
            self.step_01_view.apply_template(tpl_id)
            self._sync_canvas_after_template()
            self._sync_canvas_overlays()
            self.workspace.set_current_step_index(0)
            self.contextual_inspector_dock.set_payload(self.step_01_view.build_inspector_payload())
            self.contextual_inspector_dock.show_project_context()
            self._on_workflow_state_changed()
        finally:
            self._syncing_canvas = False

    def _on_step01_topology_modified(self) -> None:
        if self._syncing_canvas:
            return
        self._last_topology_sig = self._topology_signature(self.state.circuit)
        self.contextual_inspector_dock.refresh_from_state()
        self._on_workflow_state_changed()

    def _on_inspector_stream_renamed(self, stream_id: str, new_name: str) -> None:
        if self._syncing_canvas:
            return
        self._syncing_canvas = True
        try:
            self.canvas.rename_stream(stream_id, new_name, use_undo=False)
            self.step_01_view.canvas.set_circuit_ref(self.state.circuit)
            self.workspace.refresh_status_strip()
            self._update_status_bar()
        finally:
            self._syncing_canvas = False

    def _on_inspector_stage_renamed(self, node_id: str, new_name: str) -> None:
        if self._syncing_canvas:
            return
        self._syncing_canvas = True
        try:
            self.canvas.rename_block(node_id, new_name, use_undo=False)
            self.step_01_view.canvas.set_circuit_ref(self.state.circuit)
            self.workspace.refresh_status_strip()
            self._update_status_bar()
        finally:
            self._syncing_canvas = False

    def _on_inspector_circuit_edited(self) -> None:
        if self._syncing_canvas:
            return
        self._syncing_canvas = True
        try:
            self._sync_topology_change_downstream(self.state.circuit)
            self.canvas.set_circuit_ref(self.state.circuit)
            self._refresh_live_observability()
            self.step_01_view.sync_from_state()
            self._sync_canvas_overlays()
            self.workspace.refresh_incidence_matrix()
            self.workspace.refresh_status_strip()
            self._on_workflow_state_changed()
        finally:
            self._syncing_canvas = False

    def _on_inspector_manual_data_changed(self) -> None:
        if not validate_topology(self.state.circuit):
            self.state.step_statuses[0] = StepStatus.VALIDATED
            self.state.step_block_reasons.pop(0, None)
        if self.state.cleaned_dataset is not None and self.state.cleaned_dataset.clean_row_count > 0:
            self.state.step_statuses[1] = StepStatus.VALIDATED
            self.state.step_block_reasons.pop(1, None)
        self.state.clear_from_step(3)
        self._refresh_live_observability()
        self.step_02_view.refresh_from_state()
        self.step_04_view.refresh_from_state()
        self._sync_canvas_overlays()
        self.workspace.refresh_status_strip()
        self._on_workflow_state_changed()

    def _on_inspector_uncertainty_changed(self) -> None:
        if not validate_topology(self.state.circuit):
            self.state.step_statuses[0] = StepStatus.VALIDATED
            self.state.step_block_reasons.pop(0, None)
        if self.state.cleaned_dataset is not None and self.state.cleaned_dataset.clean_row_count > 0:
            self.state.step_statuses[1] = StepStatus.VALIDATED
            self.state.step_block_reasons.pop(1, None)
        um = self.state.uncertainty_model
        if um is not None and um.specs and all(spec.value > 0 for spec in um.specs.values()):
            self.state.step_statuses[2] = StepStatus.VALIDATED
            self.state.step_block_reasons.pop(2, None)
        self.state.clear_from_step(4)
        self.step_03_view.refresh_from_state()
        self._sync_canvas_overlays()
        self.workspace.refresh_status_strip()
        self._on_workflow_state_changed()

    def _on_dataset_profiled(self) -> None:
        self._sync_canvas_overlays()
        self.contextual_inspector_dock.refresh_from_state()
        self.workspace.refresh_status_strip()
        self._on_workflow_state_changed()

    def _on_dataset_cleaned(self) -> None:
        self._refresh_live_observability()
        self._sync_canvas_overlays()
        self.contextual_inspector_dock.refresh_from_state()
        self.workspace.refresh_status_strip()
        self._on_workflow_state_changed()

    def _on_step03_uncertainty_validated(self, _ok: bool) -> None:
        self._sync_canvas_overlays()
        self.contextual_inspector_dock.refresh_from_state()
        self.workspace.refresh_status_strip()
        self._on_workflow_state_changed()

    def _on_diagnostics_completed(self, _ok: bool) -> None:
        self._sync_canvas_overlays()
        self.contextual_inspector_dock.refresh_from_state()
        self.workspace.refresh_status_strip()
        self._on_workflow_state_changed()

    def _on_reconciliation_completed(self, ok: bool) -> None:
        if ok:
            self.step_06_view.refresh_from_state()
            self.step_07_view.refresh_from_state()
        self._sync_canvas_overlays()
        self.contextual_inspector_dock.refresh_from_state()
        self.workspace.refresh_status_strip()
        self._on_workflow_state_changed()

    def _on_workspace_mode_changed(self, mode: str) -> None:
        if mode == "canvas":
            self._refresh_live_observability()
            self._sync_canvas_overlays()
            self.contextual_inspector_dock.refresh_from_state()
            self.workspace.refresh_status_strip()

    def _ensure_ready_for_diagnostics(self) -> bool:
        if validate_topology(self.state.circuit):
            self.step_01_view.validate_step()
            return False
        if not self.state.is_step_completed(0):
            self.state.step_statuses[0] = StepStatus.VALIDATED
            self.state.step_block_reasons.pop(0, None)
        if self.state.current_period() is None:
            self.state.ensure_manual_dataset()
        if not self.state.is_step_completed(1):
            self.state.step_statuses[1] = StepStatus.VALIDATED
            self.state.step_block_reasons.pop(1, None)
        if self.state.uncertainty_model is None:
            self.state.ensure_uncertainty_model()
        if not self.state.is_step_completed(2):
            self.state.step_statuses[2] = StepStatus.VALIDATED
            self.state.step_block_reasons.pop(2, None)
        return True

    def _on_quick_diagnostics(self) -> None:
        if not self._ensure_ready_for_diagnostics():
            return
        self.workspace.set_current_step_index(3)
        self.step_04_view.refresh_from_state()
        self.step_04_view.run_diagnostics()
        self.workspace.set_current_step_index(3)
        self.project_tree_dock.refresh_steps(self.state, 3)
        self._update_action_states()
        self._update_status_bar()

    def _on_quick_reconcile(self) -> None:
        if not self._ensure_ready_for_diagnostics():
            return
        obs = self.state.observability_report
        if obs is None:
            obs = self.step_04_view.run_diagnostics()
        else:
            if not self.state.is_step_completed(3):
                obs = self.step_04_view.run_diagnostics()
        if obs is None or not obs.can_reconcile:
            self.workspace.set_current_step_index(3)
            self.project_tree_dock.refresh_steps(self.state, 3)
            return
        self.workspace.set_current_step_index(4)
        self.step_05_view.refresh_from_state()
        self.step_05_view.run_single()
        self.project_tree_dock.refresh_steps(self.state, 4)
        self._update_action_states()
        self._update_status_bar()

    def _on_quick_batch(self) -> None:
        if not self._ensure_ready_for_diagnostics():
            return
        obs = self.state.observability_report
        if obs is None or not self.state.is_step_completed(3):
            obs = self.step_04_view.run_diagnostics()
        if obs is None or not obs.can_reconcile:
            self.workspace.set_current_step_index(3)
            self.project_tree_dock.refresh_steps(self.state, 3)
            return
        self.workspace.set_current_step_index(4)
        self.step_05_view.refresh_from_state()
        self.step_05_view.run_batch()
        self.project_tree_dock.refresh_steps(self.state, 4)
        self._update_action_states()
        self._update_status_bar()

    def _on_open_etl_requested(self) -> None:
        if not self.state.is_step_unlocked(1):
            if not self.step_01_view.validate_step():
                return
        self.go_to_step(1, show_canvas=False)

    def _on_open_full_table_requested(self) -> None:
        if not self.state.is_step_unlocked(1):
            if not self.step_01_view.validate_step():
                return
        subtab = 3 if self.state.reconciliation_result is not None else 0
        self.workspace.set_active_view("data", subtab_idx=subtab)

    def _on_inspector_outlier_compare(self, var_keys: list) -> None:
        if not var_keys:
            return
        self.state.excluded_outliers = [str(v) for v in var_keys if v]
        if not self._ensure_ready_for_diagnostics():
            return
        if self.state.observability_report is None or not self.state.is_step_completed(3):
            obs = self.step_04_view.run_diagnostics()
            if obs is None or not obs.can_reconcile:
                return
        self.workspace.set_current_step_index(4)
        self.step_05_view.run_outlier_comparison()

    def _on_workflow_state_changed(self) -> None:
        cur = self.step_stack.currentIndex()
        self.project_tree_dock.refresh_steps(self.state, cur)
        self.workspace.refresh_status_strip()
        self._update_action_states()
        self._update_status_bar()

    def _update_action_states(self) -> None:
        diag_ok = self.state.is_step_unlocked(3)
        solve_ok = self.state.is_step_unlocked(4)
        export_ok = (
            self.state.cleaned_dataset is not None
            and self.state.uncertainty_model is not None
            and self.state.reconciliation_result is not None
        )
        self.act_tools_diag.setEnabled(diag_ok)
        self.tb_diag.setEnabled(diag_ok)
        self.act_tools_reconcile.setEnabled(solve_ok)
        self.tb_solver.setEnabled(solve_ok)
        self.act_tools_batch.setEnabled(solve_ok)
        self.tb_batch.setEnabled(solve_ok)
        self.act_file_export_excel.setEnabled(export_ok)
        self.tb_excel.setEnabled(export_ok)
        self.act_file_export_pdf.setEnabled(export_ok)
        self.tb_pdf.setEnabled(export_ok)

    def _update_status_bar(self) -> None:
        idx = self.step_stack.currentIndex()
        step_title = self.i18n.t(f"workflow.step_{idx + 1:02d}_title")
        self.status_context_label.setText(
            self.i18n.t(
                "app.status_bar_format",
                project=self.state.circuit.name,
                step=step_title,
                lang=self.i18n.language.upper(),
            )
        )

    def go_to_step(self, step_idx: int, show_canvas: bool = False) -> bool:
        """Navigate to sequential workflow step_idx if preconditions are satisfied."""
        if not (0 <= step_idx < len(self._step_views)):
            return False
        if not self.state.is_step_unlocked(step_idx):
            prev_title = self.i18n.t(f"workflow.step_{step_idx:02d}_title")
            self._on_locked_step_clicked(step_idx, prev_title)
            return False

        self.workspace.set_current_step_index(step_idx)
        view = self._step_views[step_idx]

        if step_idx == 0:
            self.workspace.set_active_view("canvas")
            self.canvas.zoom_fit()
        elif step_idx in (1, 2, 3, 4):
            if hasattr(view, "refresh_from_state"):
                view.refresh_from_state()
            if show_canvas:
                self.workspace.data_tabs.setCurrentIndex(step_idx - 1)
                self.workspace.set_active_view("canvas")
            else:
                self.workspace.set_active_view("data", subtab_idx=step_idx - 1)
        elif step_idx == 5:
            self.workspace.set_active_view("charts")
        elif step_idx == 6:
            self.workspace.set_active_view("reports")

        self.workspace.set_current_step_index(step_idx)

        if hasattr(view, "build_inspector_payload"):
            self.contextual_inspector_dock.set_payload(view.build_inspector_payload())
        self.contextual_inspector_dock.refresh_from_state()

        self.project_tree_dock.refresh_steps(self.state, step_idx)
        self._update_action_states()
        self._update_status_bar()
        return True

    def _on_locked_step_clicked(self, step_idx: int, prev_step_title: str) -> None:
        msg = self.i18n.t("workflow.nav.step_locked_msg", prev_step=prev_step_title)
        self.statusBar().showMessage(msg, 6000)
        step_title = self.i18n.t(f"workflow.step_{step_idx + 1:02d}_title")
        self.contextual_inspector_dock.set_payload(
            InspectorPayload(
                title=step_title,
                alerts=[("warning", msg)],
                blocked_reason=msg,
            )
        )

    # --- Theme & Layout ---

    def _sanitize_restored_toolbar(self) -> None:
        """Guarantee toolbar stays top-docked horizontal and never occludes central widgets."""
        self.toolbar.setAllowedAreas(Qt.ToolBarArea.TopToolBarArea)
        self.toolbar.setOrientation(Qt.Orientation.Horizontal)
        if (
            self.toolbar.isFloating()
            or self.toolBarArea(self.toolbar) != Qt.ToolBarArea.TopToolBarArea
            or not self.toolbar.isVisible()
        ):
            self.toolbar.redock_top()

    def set_theme(self, theme: str, persist: bool = True) -> None:
        """Switch active CAD QPalette, stylesheet, and icons between 'light' and 'dark'."""
        resolved = "dark" if theme == "dark" else "light"
        self._theme = resolved
        if persist:
            self.settings.set_theme(resolved)

        pal = build_palette(resolved)
        qss = load_stylesheet(resolved)
        app = QApplication.instance()
        if isinstance(app, QApplication):
            app.setPalette(pal)
            app.setStyleSheet(qss)
            self.setPalette(pal)
        else:
            self.setPalette(pal)
            self.setStyleSheet(qss)

        self.act_theme_light.setChecked(resolved == "light")
        self.act_theme_dark.setChecked(resolved == "dark")

        self.left_tab_strip.set_theme(resolved)
        self.right_tab_strip.set_theme(resolved)
        self.project_tree_dock.set_theme(resolved)
        self.contextual_inspector_dock.set_theme(resolved)
        self.workspace.set_theme(resolved)
        self._refresh_icons()

    def toggle_theme(self) -> None:
        """Toggle between Light Technical Engineering Mode and Dark Engineering Mode."""
        self.set_theme("dark" if self._theme == "light" else "light")

    def reset_layout(self) -> None:
        """Restore default toolbar, dock locations, visibility, and window geometry."""
        self._syncing_layout = True
        try:
            default_geo = self.settings.load_default_geometry() or self._default_geometry
            default_state = self.settings.load_default_state() or self._default_state
            if default_geo is not None:
                self.restoreGeometry(default_geo)
            if default_state is not None:
                self.restoreState(default_state)

            self.toolbar.redock_top()

            self.project_tree_dock.setFloating(False)
            self.contextual_inspector_dock.setFloating(False)
            self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.project_tree_dock)
            self.addDockWidget(
                Qt.DockWidgetArea.RightDockWidgetArea, self.contextual_inspector_dock
            )
            self._dock_last_area[self.project_tree_dock] = Qt.DockWidgetArea.LeftDockWidgetArea
            self._dock_last_area[
                self.contextual_inspector_dock
            ] = Qt.DockWidgetArea.RightDockWidgetArea

            self.expand_dock(self.project_tree_dock)
            self.expand_dock(self.contextual_inspector_dock)
            self._sync_dock_tab_registration(
                self.project_tree_dock, Qt.DockWidgetArea.LeftDockWidgetArea
            )
            self._sync_dock_tab_registration(
                self.contextual_inspector_dock, Qt.DockWidgetArea.RightDockWidgetArea
            )
            self.left_tab_strip.refresh_visibility()
            self.right_tab_strip.refresh_visibility()

            self.resizeDocks(
                [self.project_tree_dock, self.contextual_inspector_dock],
                [260, 320],
                Qt.Orientation.Horizontal,
            )
            self._activate_central_layout()
        finally:
            self._syncing_layout = False
        self.statusBar().showMessage(self.i18n.t("app.ready"), 3000)

    def _refresh_icons(self) -> None:
        t = self._theme
        self.act_file_new.setIcon(load_svg_icon("folder.svg", t, size=18))
        self.act_file_open.setIcon(load_svg_icon("import_table.svg", t, size=18))
        self.act_file_sample_a.setIcon(load_svg_icon("import_table.svg", t, size=18))
        self.act_file_sample_b.setIcon(load_svg_icon("import_table.svg", t, size=18))
        self.act_file_export_excel.setIcon(load_svg_icon("excel_doc.svg", t, size=18))
        self.act_file_export_pdf.setIcon(load_svg_icon("pdf_doc.svg", t, size=18))
        self.act_reset_layout.setIcon(load_svg_icon("reset_layout.svg", t, size=18))
        self.act_tools_diag.setIcon(load_svg_icon("matrix.svg", t, size=18))
        self.act_tools_reconcile.setIcon(load_svg_icon("play.svg", t, size=18))
        self.act_tools_batch.setIcon(load_svg_icon("scatter_plot.svg", t, size=18))
        self.act_tools_branding.setIcon(load_svg_icon("gear.svg", t, size=18))

        self.tb_new.setIcon(load_svg_icon("folder.svg", t, size=18))
        self.tb_open.setIcon(load_svg_icon("import_table.svg", t, size=18))
        self.tb_diag.setIcon(load_svg_icon("matrix.svg", t, size=18))
        self.tb_solver.setIcon(load_svg_icon("play.svg", t, size=18))
        self.tb_batch.setIcon(load_svg_icon("scatter_plot.svg", t, size=18))
        self.tb_excel.setIcon(load_svg_icon("excel_doc.svg", t, size=18))
        self.tb_pdf.setIcon(load_svg_icon("pdf_doc.svg", t, size=18))
        self.tb_theme.setIcon(load_svg_icon("theme.svg", t, size=18))
        self.tb_lang.setIcon(load_svg_icon("language.svg", t, size=18))
        self.tb_reset_layout.setIcon(load_svg_icon("reset_layout.svg", t, size=18))

    # --- Language & Hot Retranslation ---

    def set_language(self, lang: str) -> None:
        """Persist language and trigger reactive ES/EN retranslation across all widgets."""
        self.settings.set_language(lang)
        if self.i18n.language != lang:
            self.i18n.set_language(lang)
        else:
            self.retranslate_ui()

    def toggle_language(self) -> None:
        """Switch between Spanish ('es') and English ('en')."""
        self.set_language("en" if self.i18n.language == "es" else "es")

    def _on_language_changed(self, _lang: str) -> None:
        self.retranslate_ui()

    def retranslate_ui(self) -> None:
        """Retranslate MainWindow, menus, toolbar, docks, workspace, and all 7 step views."""
        t = self.i18n.t
        self.setWindowTitle(f"{t('app.title')} — {t('app.subtitle')}")

        self.menu_file.setTitle(t("menu.file"))
        self.act_file_new.setText(t("menu.file_new"))
        self.act_file_open.setText(t("menu.file_open"))
        self.act_file_sample_a.setText(t("menu.file_load_sample_a"))
        self.act_file_sample_b.setText(t("menu.file_load_sample_b"))
        self.act_file_export_excel.setText(t("menu.file_export_excel"))
        self.act_file_export_pdf.setText(t("menu.file_export_pdf"))
        self.act_file_exit.setText(t("menu.file_exit"))

        self.menu_view.setTitle(t("menu.view"))
        self.act_theme_light.setText(t("menu.view_theme_light"))
        self.act_theme_dark.setText(t("menu.view_theme_dark"))
        self.menu_docks.setTitle(t("menu.view_docks"))
        self.act_dock_tree.setText(t("menu.view_dock_project_tree"))
        self.act_dock_inspector.setText(t("menu.view_dock_inspector"))
        self.act_reset_layout.setText(t("menu.view_reset_layout"))

        self.menu_language.setTitle(t("menu.language"))
        self.act_lang_es.setText(t("menu.language_es"))
        self.act_lang_en.setText(t("menu.language_en"))
        self.act_lang_es.setChecked(self.i18n.language == "es")
        self.act_lang_en.setChecked(self.i18n.language == "en")

        self.menu_tools.setTitle(t("menu.tools"))
        self.act_tools_diag.setText(t("menu.tools_run_diagnostics"))
        self.act_tools_reconcile.setText(t("menu.tools_run_reconciliation"))
        self.act_tools_batch.setText(t("menu.tools_run_batch"))
        self.act_tools_branding.setText(t("menu.tools_branding"))

        self.menu_help.setTitle(t("menu.help"))
        self.act_help_about.setText(t("menu.help_about"))
        self.act_help_amira.setText(t("menu.help_amira_guide"))

        self.tb_new.setText(t("toolbar.new_project"))
        self.tb_open.setText(t("toolbar.open_data"))
        self.tb_diag.setText(t("toolbar.run_diagnostics"))
        self.tb_solver.setText(t("toolbar.run_solver"))
        self.tb_batch.setText(t("toolbar.run_batch"))
        self.tb_excel.setText(t("toolbar.export_excel"))
        self.tb_pdf.setText(t("toolbar.export_pdf"))
        self.tb_theme.setText(t("toolbar.toggle_theme"))
        self.tb_lang.setText(t("toolbar.toggle_language"))
        self.tb_reset_layout.setText(t("toolbar.reset_layout"))

        self.project_tree_dock.retranslate_ui()
        self.contextual_inspector_dock.retranslate_ui()
        self.workspace.retranslate_ui()

        cur_idx = self.step_stack.currentIndex()
        cur_view = self._step_views[cur_idx]
        if hasattr(cur_view, "build_inspector_payload"):
            self.contextual_inspector_dock.set_payload(cur_view.build_inspector_payload())

        self.statusBar().showMessage(t("app.ready"))
        self._update_status_bar()

    # --- Workflow & Menu Actions ---

    def new_project(self) -> None:
        """Reset circuit to default template and clear downstream steps."""
        self.step_01_view.apply_template("polymetallic_4prod")
        self._sync_canvas_after_template()
        self.go_to_step(0)

    def _open_data_file(self) -> None:
        if not self.state.is_step_unlocked(1):
            if not self.step_01_view.validate_step():
                return
        if self.go_to_step(1, show_canvas=False):
            self.step_02_view.open_file_dialog()

    def load_sample_a(self) -> None:
        """Load Polymetallic 4-product template, validate Step 01, and profile Sample Dataset A."""
        self.step_01_view.apply_template("polymetallic_4prod")
        self._sync_canvas_after_template()
        if self.step_01_view.validate_step() and self.go_to_step(1, show_canvas=False):
            self.step_02_view.load_sample_dataset("a")

    def load_sample_b(self) -> None:
        """Load Multinode RSC template, validate Step 01, and profile Sample Dataset B."""
        self.step_01_view.apply_template("multinode_rsc")
        self._sync_canvas_after_template()
        if self.step_01_view.validate_step() and self.go_to_step(1, show_canvas=False):
            self.step_02_view.load_sample_dataset("b")

    def _run_tools_diagnostics(self) -> None:
        if self.go_to_step(3):
            self.step_04_view.run_diagnostics()

    def _run_tools_reconcile(self) -> None:
        if self.go_to_step(4):
            self.step_05_view.run_single()

    def _run_tools_batch(self) -> None:
        if self.go_to_step(4):
            self.step_05_view.run_batch()

    def _open_branding_step(self) -> None:
        self.go_to_step(6)

    def _trigger_export_excel(self) -> None:
        if self.go_to_step(6):
            self.step_07_view.export_excel_dialog()

    def _trigger_export_pdf(self) -> None:
        if self.go_to_step(6):
            self.step_07_view.export_pdf_dialog()

    def _show_about_dialog(self) -> None:
        t = self.i18n.t
        lines = [
            t("app.title"),
            t("app.subtitle"),
            t("report.pdf_subtitle"),
            t("report.pdf_amira_seal"),
        ]
        QMessageBox.information(self, t("menu.help_about"), "\n\n".join(lines))

    def _show_amira_dialog(self) -> None:
        t = self.i18n.t
        lines = [
            t("report.pdf_subtitle"),
            t("step03.preset_group"),
            f"- {t('step03.preset_belt_weigher')}",
            f"- {t('step03.preset_pulp_flow')}",
            f"- {t('step03.preset_feed_assay')}",
            f"- {t('step03.preset_conc_assay')}",
            f"- {t('step03.preset_tail_assay')}",
            f"- {t('step03.preset_precious_assay')}",
        ]
        QMessageBox.information(self, t("menu.help_amira_guide"), "\n".join(lines))

    def closeEvent(self, event: QCloseEvent) -> None:
        """Persist geometry/dock state and stop background QThreads cleanly on close."""
        self.step_05_view.shutdown_workers()
        self.settings.save_window_geometry(self.saveGeometry())
        self.settings.save_window_state(self.saveState())
        self.i18n.unsubscribe(self._on_language_changed)
        super().closeEvent(event)
