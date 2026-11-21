"""Offscreen pytest-qt smoke tests for MainWindow, docks, themes, i18n, locking, and workflow."""

from __future__ import annotations

from pathlib import Path
import re
import xml.etree.ElementTree as ET

import numpy as np
import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, QSettings, QSize, Qt
from PySide6.QtGui import QFontMetrics, QMouseEvent, QPalette
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDockWidget,
    QDoubleSpinBox,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QWidget,
)

from app.i18n.manager import reset_i18n
from app.main import main as app_main
from app.settings_manager import SettingsManager
from app.ui.flowsheet_canvas import CanvasBlockKind
from app.ui.main_window import MainToolBar, MainWindow
from app.ui.ui_common import (
    ICONS_DIR,
    TableInspectorDialog,
    build_palette,
    load_stylesheet,
    load_svg_icon,
    ui_font,
)
from core.models import StepStatus, StreamRole
from core.topology import build_incidence_matrix, get_circuit_template


def _isolated_settings(tmp_path: Path, name: str = "settings.ini") -> SettingsManager:
    """Create a SettingsManager backed by an isolated INI file under tmp_path."""
    qs = QSettings(str(tmp_path / name), QSettings.Format.IniFormat)
    qs.clear()
    qs.sync()
    return SettingsManager(qsettings=qs)


@pytest.fixture(autouse=True)
def _clean_i18n_singleton():
    """Ensure each UI test starts and ends with a clean i18n singleton."""
    reset_i18n()
    yield
    reset_i18n()


def test_mainwindow_construction_docks_theme_and_reset_layout(qtbot, tmp_path: Path) -> None:
    """Verify MainWindow constructs cleanly, docks move/float/reset, and light/dark themes switch."""
    settings = _isolated_settings(tmp_path)
    win = MainWindow(settings_manager=settings)
    qtbot.addWidget(win)
    win.show()

    # Movable and floatable QDockWidgets
    assert isinstance(win.project_tree_dock, QDockWidget)
    assert isinstance(win.inspector_dock, QDockWidget)
    assert win.project_tree_dock.features() & QDockWidget.DockWidgetFeature.DockWidgetMovable
    assert win.project_tree_dock.features() & QDockWidget.DockWidgetFeature.DockWidgetFloatable
    assert win.dockWidgetArea(win.project_tree_dock) == Qt.DockWidgetArea.LeftDockWidgetArea
    assert win.dockWidgetArea(win.inspector_dock) == Qt.DockWidgetArea.RightDockWidgetArea

    # Move/float/hide docks, then verify View -> Reset Layout restores defaults
    win.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, win.project_tree_dock)
    win.inspector_dock.setFloating(True)
    win.project_tree_dock.setVisible(False)
    assert win.inspector_dock.isFloating() is True
    assert win.project_tree_dock.isVisible() is False

    win.reset_layout()
    assert win.project_tree_dock.isVisible() is True
    assert win.inspector_dock.isVisible() is True
    assert win.project_tree_dock.isFloating() is False
    assert win.inspector_dock.isFloating() is False
    assert win.dockWidgetArea(win.project_tree_dock) == Qt.DockWidgetArea.LeftDockWidgetArea
    assert win.dockWidgetArea(win.inspector_dock) == Qt.DockWidgetArea.RightDockWidgetArea

    # Light <-> Dark theme toggle
    assert win.act_theme_light.isChecked() is True
    win.set_theme("dark")
    assert win.act_theme_dark.isChecked() is True
    assert settings.get_theme() == "dark"
    win.set_theme("light")
    assert win.act_theme_light.isChecked() is True
    assert settings.get_theme() == "light"

    win.close()


def test_hot_language_switching_es_en(qtbot, tmp_path: Path) -> None:
    """Verify live ES <-> EN switching updates menus, docks, workflow tree, and views."""
    settings = _isolated_settings(tmp_path, "lang.ini")
    win = MainWindow(settings_manager=settings)
    qtbot.addWidget(win)
    win.show()

    assert win.menu_file.title() == "Archivo"
    assert win.project_tree_dock.windowTitle() == "Árbol de Proyecto Secuencial"
    assert win.project_tree_dock.step_item(0).text(0).startswith("01. Proyecto")

    win.set_language("en")
    assert win.i18n.language == "en"
    assert win.menu_file.title() == "File"
    assert win.project_tree_dock.windowTitle() == "Sequential Project Tree"
    assert win.inspector_dock.windowTitle() == "Properties and Alerts Inspector"
    assert win.project_tree_dock.step_item(0).text(0).startswith("01. Project")
    assert win.act_lang_en.isChecked() is True

    win.toggle_language()
    assert win.i18n.language == "es"
    assert win.menu_file.title() == "Archivo"
    assert win.act_lang_es.isChecked() is True

    win.close()


def test_sequential_step_locking(qtbot, tmp_path: Path) -> None:
    """Verify sequential Leapfrog step preconditions prevent jumping ahead of incomplete steps."""
    settings = _isolated_settings(tmp_path, "lock.ini")
    win = MainWindow(settings_manager=settings)
    qtbot.addWidget(win)
    win.show()

    assert win.step_stack.currentIndex() == 0
    assert win.state.is_step_unlocked(0) is True
    for locked_idx in range(1, 7):
        assert win.state.is_step_unlocked(locked_idx) is False
        assert win.go_to_step(locked_idx) is False
        assert win.step_stack.currentIndex() == 0

    # Validating Step 01 unlocks Step 02 (only), leaving Step 03+ locked
    assert win.step_01_view.validate_step() is True
    assert win.state.step_statuses[0] == StepStatus.VALIDATED
    assert win.go_to_step(1) is True
    assert win.step_stack.currentIndex() == 1
    assert win.go_to_step(2) is False

    win.close()


def test_sample_a_end_to_end_ui_workflow_and_export(qtbot, tmp_path: Path, monkeypatch) -> None:
    """Verify full 7-step UI workflow with Sample Dataset A including async WLS and exports."""
    monkeypatch.setattr("reporting.branding.DEFAULT_BRANDING_PATH", tmp_path / "branding.json")
    monkeypatch.setattr("reporting.branding.DEFAULT_LOGO_PATH", tmp_path / "default_logo.png")

    settings = _isolated_settings(tmp_path, "workflow.ini")
    win = MainWindow(settings_manager=settings)
    qtbot.addWidget(win)
    win.show()

    # Step 01 -> Load Sample A (Step 02 profiled)
    win.load_sample_a()
    assert win.step_stack.currentIndex() == 1
    assert win.state.profile_report is not None
    assert win.state.profile_report.row_count == 60

    # Step 02 -> Apply confirmed cleaning
    win.step_02_view.apply_and_validate()
    assert win.state.cleaned_dataset is not None
    assert win.state.cleaned_dataset.clean_row_count == 60
    assert win.go_to_step(2) is True

    # Step 03 -> AMIRA P754 presets & validation
    win.step_03_view.apply_amira_presets()
    assert win.step_03_view.validate_uncertainties() is True
    assert win.state.uncertainty_model is not None
    assert win.go_to_step(3) is True

    # Step 04 -> Observability & Nodal diagnostics
    win.step_04_view.run_diagnostics()
    assert win.state.observability_report is not None
    assert win.state.observability_report.can_reconcile is True
    assert win.state.observability_report.degrees_of_freedom > 0
    assert win.go_to_step(4) is True

    # Step 05 -> Async QThread WLS Reconciliation
    with qtbot.waitSignal(win.step_05_view.reconciliation_completed, timeout=15000) as sig:
        win.step_05_view.run_single()
    assert sig.args == [True]
    res = win.state.reconciliation_result
    assert res is not None
    assert res.converged is True
    assert res.max_mass_residual < 1e-8
    assert res.max_species_residual < 1e-8

    # Step 06 -> KPIs & Charts + hot retranslation on charts
    assert win.go_to_step(5) is True
    assert win.step_06_view.kpi_table.rowCount() > 0
    win.set_language("en")
    assert win.step_06_view.tabs.tabText(1) == "Parity Plot"

    # Step 07 -> Corporate Branding & Excel/PDF Exports
    assert win.go_to_step(6) is True
    win.step_07_view.company_edit.setText("Andes Mining Test")
    out_xlsx = win.step_07_view.export_excel_to(tmp_path / "ui_smoke.xlsx")
    out_pdf = win.step_07_view.export_pdf_to(tmp_path / "ui_smoke.pdf")
    assert out_xlsx is not None and out_xlsx.exists() and out_xlsx.stat().st_size > 5000
    assert out_pdf is not None and out_pdf.exists() and out_pdf.stat().st_size > 15000

    win.close()


def test_app_main_entrypoint_with_existing_qapp(qtbot, tmp_path: Path, monkeypatch) -> None:
    """Verify app.main.main() constructs and displays MainWindow cleanly without blocking."""
    monkeypatch.setattr("app.main.SettingsManager", lambda: _isolated_settings(tmp_path, "main.ini"))
    rc = app_main([])
    assert rc == 0
    from PySide6.QtWidgets import QApplication

    for w in QApplication.topLevelWidgets():
        if isinstance(w, MainWindow):
            qtbot.addWidget(w)
            w.close()


def test_explicit_fusion_palette_light_and_dark_dialogs_and_docks(qtbot, tmp_path: Path) -> None:
    """Verify Fusion style and explicit high-contrast light/dark QPalette across dialogs and docks."""
    settings = _isolated_settings(tmp_path, "palette.ini")
    win = MainWindow(settings_manager=settings)
    qtbot.addWidget(win)
    win.show()

    app = QApplication.instance()
    assert isinstance(app, QApplication)
    assert app.property("metbalance_fusion_applied") is True

    # Light mode: QDialog, QMessageBox, and QDockWidget inner container must have light background & dark text
    dlg = QDialog(win)
    msg = QMessageBox(win)
    qtbot.addWidget(dlg)
    qtbot.addWidget(msg)

    for w in (win, dlg, msg, win.inspector_dock.widget()):
        assert w is not None
        pal = w.palette()
        assert pal.color(QPalette.ColorRole.Window).lightness() > 210
        assert pal.color(QPalette.ColorRole.WindowText).lightness() < 80
        assert pal.color(QPalette.ColorRole.Base).lightness() > 235
        assert pal.color(QPalette.ColorRole.Text).lightness() < 80

    # Switch to Dark mode and verify explicit dark palette takes effect
    win.set_theme("dark")
    dark_pal = build_palette("dark")
    assert app.palette().color(QPalette.ColorRole.Window) == dark_pal.color(
        QPalette.ColorRole.Window
    )
    assert app.palette().color(QPalette.ColorRole.Window).lightness() < 50
    assert app.palette().color(QPalette.ColorRole.WindowText).lightness() > 200
    assert dlg.palette().color(QPalette.ColorRole.Window).lightness() < 50
    assert dlg.palette().color(QPalette.ColorRole.WindowText).lightness() > 200

    # Restore light mode cleanly
    win.set_theme("light")
    assert app.palette().color(QPalette.ColorRole.Window).lightness() > 210
    win.close()


def test_svg_icons_viewbox_chevron_and_non_clipped_dpi_render(qtbot) -> None:
    """Verify all SVG viewBoxes/bounds, chevron assets in QSS, and full 4-quadrant DPI icon rendering."""
    svg_files = sorted(ICONS_DIR.glob("*.svg"))
    assert len(svg_files) >= 27

    for svg_path in svg_files:
        root = ET.fromstring(svg_path.read_text(encoding="utf-8"))
        assert root.tag == "{http://www.w3.org/2000/svg}svg"
        is_chevron = svg_path.name.startswith("chevron_down")
        expected_dim = "12" if is_chevron else "24"
        expected_vb = f"0 0 {expected_dim} {expected_dim}"
        assert root.attrib.get("width") == expected_dim, svg_path.name
        assert root.attrib.get("height") == expected_dim, svg_path.name
        assert root.attrib.get("viewBox") == expected_vb, svg_path.name
        assert root.attrib.get("stroke-width") == "1.5", svg_path.name

        lo, hi = (2.0, 10.0) if is_chevron else (2.0, 22.0)
        for elem in root.iter():
            if elem is root:
                continue
            for attr in ("x1", "y1", "x2", "y2", "cx", "cy"):
                if attr in elem.attrib:
                    val = float(elem.attrib[attr])
                    assert lo <= val <= hi, f"{svg_path.name} {attr}={val} out of [{lo}, {hi}]"
            if "points" in elem.attrib:
                coords = [float(v) for v in re.findall(r"-?\d+(?:\.\d+)?", elem.attrib["points"])]
                for val in coords:
                    assert lo <= val <= hi, f"{svg_path.name} point={val} out of [{lo}, {hi}]"

    # Verify chevron icons exist and are referenced in both resolved stylesheets
    assert (ICONS_DIR / "chevron_down.svg").exists()
    assert (ICONS_DIR / "chevron_down_dark.svg").exists()
    light_qss = load_stylesheet("light")
    dark_qss = load_stylesheet("dark")
    assert "__ICONS_DIR__" not in light_qss and "chevron_down.svg" in light_qss
    assert "__ICONS_DIR__" not in dark_qss and "chevron_down_dark.svg" in dark_qss

    # Render an icon at 150% DPI scaling and verify non-transparent pixels in all 4 quadrants
    icon = load_svg_icon("check_circle.svg", theme="light", size=24, dpr=1.5)
    assert not icon.isNull()
    pix = icon.pixmap(QSize(24, 24), 1.5)
    img = pix.toImage()
    w, h = img.width(), img.height()
    assert w == 36 and h == 36
    hw, hh = w // 2, h // 2
    quadrants = [
        (0, hw, 0, hh),
        (hw, w, 0, hh),
        (0, hw, hh, h),
        (hw, w, hh, h),
    ]
    for q_idx, (x0, x1, y0, y1) in enumerate(quadrants):
        has_ink = any(
            img.pixelColor(x, y).alpha() > 0 for y in range(y0, y1) for x in range(x0, x1)
        )
        assert has_ink, f"Quadrant {q_idx} has no rendered pixels — SVG clipped!"


def test_toolbar_top_only_horizontal_and_reset_layout(qtbot, tmp_path: Path) -> None:
    """Verify ArcMap-style top-only horizontal toolbar, reset_layout, and QSettings recovery."""
    settings = _isolated_settings(tmp_path, "toolbar.ini")
    win = MainWindow(settings_manager=settings)
    qtbot.addWidget(win)
    win.show()

    tb = win.toolbar
    assert tb.allowedAreas() == Qt.ToolBarArea.TopToolBarArea
    assert tb.isMovable() is True
    assert tb.isFloatable() is True
    assert tb.orientation() == Qt.Orientation.Horizontal

    # Forcing vertical orientation must snap right back to horizontal
    tb.setOrientation(Qt.Orientation.Vertical)
    QApplication.processEvents()
    assert tb.orientation() == Qt.Orientation.Horizontal

    # Float toolbar, then reset_layout must re-dock into TopToolBarArea
    win.removeToolBar(tb)
    tb.setParent(None)
    tb.setWindowFlag(Qt.WindowType.Tool, True)
    tb.show()
    QApplication.processEvents()
    assert tb.isFloating() is True
    assert tb.orientation() == Qt.Orientation.Horizontal

    win.reset_layout()
    QApplication.processEvents()
    assert tb.isFloating() is False
    assert tb.isVisible() is True
    assert win.toolBarArea(tb) == Qt.ToolBarArea.TopToolBarArea

    # Persist detached toolbar state and verify fresh MainWindow sanitizes it back onto TopToolBarArea
    tb.setWindowFlag(Qt.WindowType.Tool, True)
    tb.show()
    QApplication.processEvents()
    win.close()

    win2 = MainWindow(settings_manager=settings)
    qtbot.addWidget(win2)
    win2.show()
    assert win2.toolbar.isFloating() is False
    assert win2.toolBarArea(win2.toolbar) == Qt.ToolBarArea.TopToolBarArea
    assert win2.toolbar.orientation() == Qt.Orientation.Horizontal
    win2.close()


def test_step01_splitter_bidirectional_resize_and_flow_toolbar(qtbot, tmp_path: Path) -> None:
    """Verify Step 01 splitter moves freely in both directions and FlowLayout wraps without clipping."""
    settings = _isolated_settings(tmp_path, "splitter.ini")
    win = MainWindow(settings_manager=settings)
    qtbot.addWidget(win)
    win.resize(1600, 900)
    win.show()
    QApplication.processEvents()

    view = win.step_01_view
    splitter = view.main_splitter
    assert view.left_panel.minimumWidth() == 260
    assert splitter.childrenCollapsible() is False

    splitter.setSizes([300, 700])
    QApplication.processEvents()
    assert splitter.sizes()[0] <= 340

    splitter.setSizes([550, 450])
    QApplication.processEvents()
    assert splitter.sizes()[0] >= 500

    splitter.setSizes([280, 720])
    QApplication.processEvents()
    assert splitter.sizes()[0] <= 340

    # FlowLayout wraps toolbar buttons onto multiple rows when narrowed
    h_wide = view.fs_toolbar.heightForWidth(1200)
    h_narrow = view.fs_toolbar.heightForWidth(360)
    assert h_narrow > h_wide
    view.fs_toolbar.setGeometry(QRect(0, 0, 360, h_narrow))
    for i in range(view.fs_toolbar.count()):
        item = view.fs_toolbar.itemAt(i)
        assert item is not None
        assert item.geometry().right() <= 360
    win.close()


def test_embedded_table_row_alignment_inspector_headers_and_tree_width(
    qtbot, tmp_path: Path
) -> None:
    """Verify fixed 32px row height on combo tables, inspector headers, and ProjectTreeDock sizing."""
    settings = _isolated_settings(tmp_path, "polish.ini")
    win = MainWindow(settings_manager=settings)
    qtbot.addWidget(win)
    win.show()

    # Tables with embedded QComboBoxes use fixed 32px row height and fitting editors
    for table in (
        win.step_01_view.species_table,
        win.step_02_view.mapping_table,
        win.step_03_view.sigma_table,
    ):
        vh = table.verticalHeader()
        assert vh.defaultSectionSize() == 32
        assert vh.minimumSectionSize() == 32
        assert vh.sectionResizeMode(0) == QHeaderView.ResizeMode.Fixed

    for r in range(win.step_01_view.species_table.rowCount()):
        cb = win.step_01_view.species_table.cellWidget(r, 2)
        assert isinstance(cb, QComboBox)
        assert cb.sizeHint().height() <= 32

    # Inspector property table headers must read Propiedad / Valor (and Property / Value in EN)
    h0 = win.inspector_dock.props_table.horizontalHeaderItem(0)
    h1 = win.inspector_dock.props_table.horizontalHeaderItem(1)
    assert h0 is not None and h0.text() == "Propiedad"
    assert h1 is not None and h1.text() == "Valor"

    win.set_language("en")
    h0_en = win.inspector_dock.props_table.horizontalHeaderItem(0)
    h1_en = win.inspector_dock.props_table.horizontalHeaderItem(1)
    assert h0_en is not None and h0_en.text() == "Property"
    assert h1_en is not None and h1_en.text() == "Value"

    # Project tree dock comfortable width + full visible step titles
    assert win.project_tree_dock.minimumWidth() == 210
    assert win.project_tree_dock.sizeHint().width() >= 260
    assert win.project_tree_dock.tree.columnCount() == 1
    assert win.project_tree_dock.step_item(0).text(0) == "01. Project and Topology"
    win.close()


def test_canvas_programmatic_circuit_syncs_incidence_matrix(qtbot, tmp_path: Path) -> None:
    """Verify programmatic Excalidraw canvas authoring maps PROCESS->nodes and terminals->streams in Matrix A."""
    settings = _isolated_settings(tmp_path, "canvas_matrix.ini")
    win = MainWindow(settings_manager=settings)
    qtbot.addWidget(win)
    win.show()
    QApplication.processEvents()

    # Clear and draw a fresh 1-node 3-stream circuit on win.canvas
    win.canvas.clear_circuit(keep_species=True)
    feed_id = win.canvas.add_block(CanvasBlockKind.FEED, "Alimentación", (60.0, 200.0))
    proc_id = win.canvas.add_block(CanvasBlockKind.PROCESS, "Flotación", (320.0, 200.0))
    conc_id = win.canvas.add_block(
        CanvasBlockKind.PRODUCT, "Concentrado", (580.0, 140.0), is_tailings=False
    )
    tail_id = win.canvas.add_block(
        CanvasBlockKind.PRODUCT, "Relave", (580.0, 260.0), is_tailings=True
    )

    s_feed = win.canvas.connect_blocks(feed_id, proc_id, stream_name="Alimentación")
    s_conc = win.canvas.connect_blocks(proc_id, conc_id, stream_name="Concentrado")
    s_tail = win.canvas.connect_blocks(proc_id, tail_id, stream_name="Relave")

    circuit = win.state.circuit
    assert len(circuit.nodes) == 1
    assert circuit.nodes[0].id == proc_id
    assert len(circuit.streams) == 3
    roles = [s.role for s in circuit.streams]
    assert roles.count(StreamRole.FEED) == 1
    assert roles.count(StreamRole.CONCENTRATE) == 1
    assert roles.count(StreamRole.TAILINGS) == 1

    A, node_ids, stream_ids = build_incidence_matrix(circuit)
    assert A.shape == (1, 3)
    assert node_ids == [proc_id]
    assert stream_ids == [s_feed, s_conc, s_tail]
    assert np.allclose(A, np.array([[1.0, -1.0, -1.0]]))

    # Inline rename of block and stream updates CircuitSpec cleanly + Undo/Redo restores & re-applies
    win.canvas.rename_block(proc_id, "Flotación Rougher", use_undo=True)
    assert win.state.circuit.nodes[0].name == "Flotación Rougher"
    win.canvas.undo_stack.undo()
    assert win.state.circuit.nodes[0].name == "Flotación"
    win.canvas.undo_stack.redo()
    assert win.state.circuit.nodes[0].name == "Flotación Rougher"

    win.canvas.rename_stream(s_conc, "Concentrado Cu", use_undo=True)
    conc_spec = next(s for s in win.state.circuit.streams if s.id == s_conc)
    assert conc_spec.name == "Concentrado Cu"
    win.canvas.undo_stack.undo()
    conc_spec_undo = next(s for s in win.state.circuit.streams if s.id == s_conc)
    assert conc_spec_undo.name == "Concentrado"
    win.canvas.undo_stack.redo()
    conc_spec_redo = next(s for s in win.state.circuit.streams if s.id == s_conc)
    assert conc_spec_redo.name == "Concentrado Cu"

    # Workspace top switcher transitions cleanly across canvas, data, charts, reports and back
    for mode in ("canvas", "data", "charts", "reports", "canvas"):
        win.workspace.set_active_view(mode)
        QApplication.processEvents()
        assert win.workspace.active_view() == mode

    win.close()


def test_canvas_selection_updates_contextual_inspector_and_manual_entry(
    qtbot, tmp_path: Path
) -> None:
    """Verify canvas block/stream selection auto-expands inspector, switches context, and writes manual data."""
    settings = _isolated_settings(tmp_path, "inspector_ctx.ini")
    win = MainWindow(settings_manager=settings)
    qtbot.addWidget(win)
    win.show()
    QApplication.processEvents()

    # Collapse inspector dock first
    win.collapse_dock(win.inspector_dock)
    QApplication.processEvents()
    assert win.is_dock_collapsed(win.inspector_dock) is True
    assert win.inspector_dock.isVisible() is False

    # Find Feed terminal block on canvas and select it -> auto-expands inspector into "stream" mode
    feed_stream = next(s for s in win.state.circuit.streams if s.role == StreamRole.FEED)
    feed_block_id = f"TERM_IN__{feed_stream.id}"
    win.canvas.select_block(feed_block_id)
    QApplication.processEvents()

    assert win.is_dock_collapsed(win.inspector_dock) is False
    assert win.inspector_dock.isVisible() is True
    assert win.contextual_inspector_dock.current_context_mode() == "stream"
    assert win.contextual_inspector_dock.selected_stream_id == feed_stream.id

    # Edit manual mass flow value in ContextualInspectorDock -> updates state.current_period().mass_flows
    win.contextual_inspector_dock.manual_mass_chk.setChecked(True)
    win.contextual_inspector_dock.manual_mass_spin.setValue(150.0)
    QApplication.processEvents()

    period = win.state.current_period()
    assert period is not None
    assert period.mass_flows.get(feed_stream.id) == pytest.approx(150.0)

    # Select PROCESS block on canvas -> inspector switches to "stage" mode with connected inflows & outflows
    proc_node = win.state.circuit.nodes[0]
    win.canvas.select_block(proc_node.id)
    QApplication.processEvents()

    assert win.contextual_inspector_dock.current_context_mode() == "stage"
    assert win.contextual_inspector_dock.selected_node_id == proc_node.id
    assert win.contextual_inspector_dock.inflows_flow.count() >= 1
    assert win.contextual_inspector_dock.outflows_flow.count() >= 2

    # Clear selection -> inspector returns to "project" overview mode
    win.canvas._scene.clearSelection()
    QApplication.processEvents()
    assert win.contextual_inspector_dock.current_context_mode() == "project"

    win.close()


def test_collapsible_docks_left_and_right_auto_hide_tabs_and_reset(
    qtbot, tmp_path: Path
) -> None:
    """Verify ArcMap auto-hide pin/unpin, 28px side tab strips, left<->right dock swapping, and reset_layout."""
    settings = _isolated_settings(tmp_path, "collapsible_docks.ini")
    win = MainWindow(settings_manager=settings)
    qtbot.addWidget(win)
    win.show()
    QApplication.processEvents()

    # Side tab strips start hidden when docks are expanded
    assert win.left_tab_strip.isVisible() is False
    assert win.right_tab_strip.isVisible() is False

    # Collapse project_tree_dock via its title-bar pin button
    win.project_tree_dock.pin_button.click()
    QApplication.processEvents()
    assert win.is_dock_collapsed(win.project_tree_dock) is True
    assert win.project_tree_dock.isVisible() is False
    assert win.left_tab_strip.isVisible() is True
    assert win.left_tab_strip.width() == 28
    tree_tab = win.dock_side_tab(win.project_tree_dock)
    assert tree_tab is not None and tree_tab.isVisible() is True

    # Click vertical tab button to expand project_tree_dock
    tree_tab.click()
    QApplication.processEvents()
    assert win.project_tree_dock.isVisible() is True
    assert win.is_dock_collapsed(win.project_tree_dock) is False
    assert win.left_tab_strip.isVisible() is False

    # Collapse inspector_dock via its title-bar pin button -> right_tab_strip appears at 28px width
    win.inspector_dock.pin_button.click()
    QApplication.processEvents()
    assert win.is_dock_collapsed(win.inspector_dock) is True
    assert win.inspector_dock.isVisible() is False
    assert win.right_tab_strip.isVisible() is True
    assert win.right_tab_strip.width() == 28
    insp_tab = win.dock_side_tab(win.inspector_dock)
    assert insp_tab is not None and insp_tab.isVisible() is True

    # Click vertical tab button to restore inspector_dock
    insp_tab.click()
    QApplication.processEvents()
    assert win.inspector_dock.isVisible() is True
    assert win.is_dock_collapsed(win.inspector_dock) is False
    assert win.right_tab_strip.isVisible() is False

    # Swap dock sides: project_tree_dock -> RightDockWidgetArea, inspector_dock -> LeftDockWidgetArea
    win.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, win.project_tree_dock)
    win.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, win.inspector_dock)
    QApplication.processEvents()

    # Collapse BOTH and verify each dock's vertical tab appears on its new side strip
    win.project_tree_dock.pin_button.click()
    win.inspector_dock.pin_button.click()
    QApplication.processEvents()

    assert win.right_tab_strip.isVisible() is True
    assert win.left_tab_strip.isVisible() is True
    right_tree_tab = win.right_tab_strip.tab_button(win.project_tree_dock)
    left_insp_tab = win.left_tab_strip.tab_button(win.inspector_dock)
    assert right_tree_tab is not None and right_tree_tab.isVisible() is True
    assert left_insp_tab is not None and left_insp_tab.isVisible() is True

    # reset_layout() restores both docks uncollapsed to Left/Right, hides both strips, keeps toolbar top-horizontal
    win.reset_layout()
    QApplication.processEvents()
    assert win.is_dock_collapsed(win.project_tree_dock) is False
    assert win.is_dock_collapsed(win.inspector_dock) is False
    assert win.project_tree_dock.isVisible() is True
    assert win.inspector_dock.isVisible() is True
    assert win.dockWidgetArea(win.project_tree_dock) == Qt.DockWidgetArea.LeftDockWidgetArea
    assert win.dockWidgetArea(win.inspector_dock) == Qt.DockWidgetArea.RightDockWidgetArea
    assert win.left_tab_strip.isVisible() is False
    assert win.right_tab_strip.isVisible() is False
    assert win.toolBarArea(win.toolbar) == Qt.ToolBarArea.TopToolBarArea
    assert win.toolbar.orientation() == Qt.Orientation.Horizontal

    win.close()


def test_canvas_quick_templates_and_initial_sync(qtbot, tmp_path: Path) -> None:
    """Verify initial MainWindow canvas/inspector sync and all 4 canvas quick template actions."""
    settings = _isolated_settings(tmp_path, "quick_templates.ini")
    win = MainWindow(settings_manager=settings)
    qtbot.addWidget(win)
    win.show()
    QApplication.processEvents()

    # Initial startup sync between state.circuit, canvas items, and contextual inspector
    init_circuit = win.state.circuit
    init_proc_blocks = [
        b for b in win.canvas._block_items.values() if b.kind == CanvasBlockKind.PROCESS
    ]
    assert len(init_proc_blocks) == len(init_circuit.nodes)
    assert {b.block_id for b in init_proc_blocks} == {n.id for n in init_circuit.nodes}
    assert set(win.canvas._stream_items.keys()) == {s.id for s in init_circuit.streams}
    assert win.contextual_inspector_dock.species_table.rowCount() == len(init_circuit.species)

    # Trigger each quick template QAction from canvas floating toolbar
    expected_ids = (
        "concentrator_2prod",
        "concentrator_3prod",
        "polymetallic_4prod",
        "multinode_rsc",
    )
    actions_by_id = {act.data(): act for act in win.canvas._toolbar.templates_menu.actions()}
    assert set(actions_by_id.keys()) == set(expected_ids)

    for tpl_id in expected_ids:
        actions_by_id[tpl_id].trigger()
        QApplication.processEvents()

        expected = get_circuit_template(tpl_id)
        circuit = win.state.circuit
        assert circuit.name == expected.name
        assert len(circuit.nodes) == len(expected.nodes)
        assert len(circuit.streams) == len(expected.streams)
        assert [sp.id for sp in circuit.species] == [sp.id for sp in expected.species]

        proc_ids = {
            b.block_id
            for b in win.canvas._block_items.values()
            if b.kind == CanvasBlockKind.PROCESS
        }
        assert proc_ids == {n.id for n in expected.nodes}
        assert set(win.canvas._stream_items.keys()) == {s.id for s in expected.streams}

        expected_summary = win.i18n.t(
            "workspace.status_summary_fmt",
            nodes=len(expected.nodes),
            streams=len(expected.streams),
            species=len(expected.species),
            dof=win.i18n.t("common.na"),
        )
        assert win.workspace.summary_label.text() == expected_summary
        assert win.workspace.matrix_table.rowCount() == len(circuit.nodes)
        assert win.workspace.matrix_table.columnCount() == len(circuit.streams)
        assert win.contextual_inspector_dock.project_name_edit.text() == circuit.name
        assert win.contextual_inspector_dock.species_table.rowCount() == len(circuit.species)

    win.close()


def test_dock_resizing_constraints_and_tab_text_no_elision(qtbot, tmp_path: Path) -> None:
    """Verify dock shrink bounds, bidirectional resizeDocks, non-elided tab bars, and full vertical tab heights."""
    settings = _isolated_settings(tmp_path, "dock_resize_tabs.ini")
    win = MainWindow(settings_manager=settings)
    qtbot.addWidget(win)
    win.resize(1380, 860)
    win.show()
    QApplication.processEvents()

    for dock in (win.project_tree_dock, win.inspector_dock):
        assert dock.minimumWidth() == 210
        assert dock.maximumWidth() >= 524287
        assert dock.minimumSizeHint().width() <= 220
        assert dock._title_bar.title_label.wordWrap() is True

    assert 360 <= win._central_shell.minimumWidth() <= 420
    assert 360 <= win.workspace.minimumWidth() <= 420
    assert win.workspace.minimumSizeHint().width() <= 420

    # Bidirectional dock resizing on 1380px window without crushing docks or collapsing center
    win.resizeDocks(
        [win.project_tree_dock, win.inspector_dock],
        [210, 500],
        Qt.Orientation.Horizontal,
    )
    QApplication.processEvents()
    assert 210 <= win.project_tree_dock.width() <= 240
    assert win.inspector_dock.width() >= 450
    assert win._central_shell.width() >= 380

    win.resizeDocks(
        [win.project_tree_dock, win.inspector_dock],
        [480, 210],
        Qt.Orientation.Horizontal,
    )
    QApplication.processEvents()
    assert win.project_tree_dock.width() >= 430
    assert 210 <= win.inspector_dock.width() <= 240
    assert win._central_shell.width() >= 380

    # Non-expanding, non-eliding tab bars across workspace & charts
    for tab_bar in (
        win.workspace.workspace_tab_bar,
        win.workspace.data_tabs.tabBar(),
        win.step_06_view.tabs.tabBar(),
    ):
        assert tab_bar.expanding() is False
        assert tab_bar.elideMode() == Qt.TextElideMode.ElideNone

    # VerticalTabButton height must fit full title text + icon/padding (no 220px truncation)
    fm = QFontMetrics(ui_font(8.8, bold=True))
    for dock in (win.project_tree_dock, win.inspector_dock):
        tab_btn = win.dock_side_tab(dock)
        assert tab_btn is not None
        icon_extra = (8 + 14 + 6) if dock.dock_icon_name else 8
        expected_min_h = fm.horizontalAdvance(dock.windowTitle()) + icon_extra + 24
        assert tab_btn.sizeHint().height() >= expected_min_h
    assert win.dock_side_tab(win.inspector_dock).sizeHint().height() > 220

    win.close()


def test_main_toolbar_magnetic_and_double_click_redock(qtbot, tmp_path: Path) -> None:
    """Verify MainToolBar double-click and magnetic top-zone re-docking behavior."""
    settings = _isolated_settings(tmp_path, "toolbar_magnetic.ini")
    win = MainWindow(settings_manager=settings)
    qtbot.addWidget(win)
    win.move(80, 80)
    win.show()
    QApplication.processEvents()

    tb = win.toolbar
    assert isinstance(tb, MainToolBar)
    assert tb.allowedAreas() == Qt.ToolBarArea.TopToolBarArea
    assert tb.isMovable() is True
    assert tb.isFloatable() is True

    def _float_toolbar() -> None:
        win.removeToolBar(tb)
        tb.setParent(None)
        tb.setWindowFlag(Qt.WindowType.Tool, True)
        tb.show()
        QApplication.processEvents()
        assert tb.isFloating() is True

    # 1. Double-click while floating re-docks horizontally into TopToolBarArea
    _float_toolbar()
    dbl_ev = QMouseEvent(
        QEvent.Type.MouseButtonDblClick,
        QPointF(8.0, 8.0),
        QPointF(tb.mapToGlobal(QPoint(8, 8))),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(tb, dbl_ev)
    QApplication.processEvents()
    assert tb.isFloating() is False
    assert win.toolBarArea(tb) == Qt.ToolBarArea.TopToolBarArea
    assert tb.orientation() == Qt.Orientation.Horizontal

    # 2. Magnetic re-docking: stay floating outside zone, snap back inside 60px band below menuBar
    menu_bar = win.menuBar()
    anchor_y = win.mapToGlobal(QPoint(0, menu_bar.height() if menu_bar is not None else 0)).y()
    target_x = win.frameGeometry().center().x() - max(100, tb.width()) // 2

    _float_toolbar()
    tb.move(target_x, anchor_y + 220)
    QApplication.processEvents()
    assert tb.is_in_magnetic_dock_zone() is False
    assert tb.try_magnetic_redock() is False
    assert tb.isFloating() is True

    tb.move(target_x, anchor_y + 20)
    assert tb.is_in_magnetic_dock_zone() is True
    assert tb.try_magnetic_redock() is True
    QApplication.processEvents()
    assert tb.isFloating() is False
    assert win.toolBarArea(tb) == Qt.ToolBarArea.TopToolBarArea
    assert tb.orientation() == Qt.Orientation.Horizontal

    # 3. Also verify mouseReleaseEvent triggers magnetic re-dock after leaving & re-entering zone
    _float_toolbar()
    tb.move(target_x, anchor_y + 220)
    QApplication.processEvents()
    assert tb.isFloating() is True
    tb.move(target_x, anchor_y + 20)
    rel_ev = QMouseEvent(
        QEvent.Type.MouseButtonRelease,
        QPointF(8.0, 8.0),
        QPointF(tb.mapToGlobal(QPoint(8, 8))),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(tb, rel_ev)
    QApplication.processEvents()
    assert tb.isFloating() is False
    assert win.toolBarArea(tb) == Qt.ToolBarArea.TopToolBarArea
    assert tb.orientation() == Qt.Orientation.Horizontal

    win.close()


def test_nested_dock_vertical_stacking_and_multi_tab_side_strip(qtbot, tmp_path: Path) -> None:
    """Verify AllowNestedDocks vertical stacking, multi-tab SideTabStrip, and reset_layout."""
    settings = _isolated_settings(tmp_path, "nested_docks.ini")
    win = MainWindow(settings_manager=settings)
    qtbot.addWidget(win)
    win.resize(1380, 860)
    win.show()
    QApplication.processEvents()

    assert bool(win.dockOptions() & QMainWindow.DockOption.AllowNestedDocks) is True

    # 1. Direct splitDockWidget vertical stacking in LeftDockWidgetArea
    win.splitDockWidget(win.project_tree_dock, win.inspector_dock, Qt.Orientation.Vertical)
    QApplication.processEvents()
    assert win.dockWidgetArea(win.project_tree_dock) == Qt.DockWidgetArea.LeftDockWidgetArea
    assert win.dockWidgetArea(win.inspector_dock) == Qt.DockWidgetArea.LeftDockWidgetArea
    assert win.project_tree_dock.isFloating() is False
    assert win.inspector_dock.isFloating() is False
    assert win.inspector_dock.y() > win.project_tree_dock.y()

    # Restore, then verify stack_below title-menu action & stack_dock_below_other
    win.reset_layout()
    QApplication.processEvents()
    assert win.dockWidgetArea(win.inspector_dock) == Qt.DockWidgetArea.RightDockWidgetArea

    _, _, act_stack_below = win.inspector_dock.dock_actions
    act_stack_below.trigger()
    QApplication.processEvents()
    assert win.dockWidgetArea(win.project_tree_dock) == Qt.DockWidgetArea.LeftDockWidgetArea
    assert win.dockWidgetArea(win.inspector_dock) == Qt.DockWidgetArea.LeftDockWidgetArea
    assert win.project_tree_dock.isFloating() is False
    assert win.inspector_dock.isFloating() is False
    assert win.inspector_dock.y() > win.project_tree_dock.y()

    # 2. Collapse BOTH stacked docks into left_tab_strip
    win.project_tree_dock.pin_button.click()
    win.inspector_dock.pin_button.click()
    QApplication.processEvents()

    assert win.left_tab_strip.isVisible() is True
    assert win.right_tab_strip.isVisible() is False
    tree_tab = win.left_tab_strip.tab_button(win.project_tree_dock)
    insp_tab = win.left_tab_strip.tab_button(win.inspector_dock)
    assert tree_tab is not None and tree_tab.isVisible() is True
    assert insp_tab is not None and insp_tab.isVisible() is True

    # 3. Expand each dock via its vertical tab button
    tree_tab.click()
    QApplication.processEvents()
    assert win.project_tree_dock.isVisible() is True
    assert win.is_dock_collapsed(win.project_tree_dock) is False
    assert win.left_tab_strip.isVisible() is True
    assert insp_tab.isVisible() is True

    insp_tab.click()
    QApplication.processEvents()
    assert win.inspector_dock.isVisible() is True
    assert win.is_dock_collapsed(win.inspector_dock) is False
    assert win.left_tab_strip.isVisible() is False
    assert win.right_tab_strip.isVisible() is False

    # 4. reset_layout() restores default Left / Right layout
    win.reset_layout()
    QApplication.processEvents()
    assert win.dockWidgetArea(win.project_tree_dock) == Qt.DockWidgetArea.LeftDockWidgetArea
    assert win.dockWidgetArea(win.inspector_dock) == Qt.DockWidgetArea.RightDockWidgetArea
    assert win.project_tree_dock.isVisible() is True
    assert win.inspector_dock.isVisible() is True
    assert win.left_tab_strip.isVisible() is False
    assert win.right_tab_strip.isVisible() is False

    win.close()


def test_contextual_inspector_no_widget_overlaps_across_widths(qtbot, tmp_path: Path) -> None:
    """Verify ContextualInspectorDock has zero sibling overlaps or clipped buttons across widths and contexts."""
    settings = _isolated_settings(tmp_path, "inspector_overlaps.ini")
    win = MainWindow(settings_manager=settings)
    qtbot.addWidget(win)
    win.resize(1380, 860)
    win.show()
    qtbot.wait(50)

    insp = win.contextual_inspector_dock
    feed_stream_id = next(
        s.id for s in win.state.circuit.streams if s.role == StreamRole.FEED
    )
    proc_node_id = win.state.circuit.nodes[0].id

    control_types = (
        QLabel,
        QLineEdit,
        QComboBox,
        QPushButton,
        QDoubleSpinBox,
        QCheckBox,
    )
    internal_parents = (QDoubleSpinBox, QComboBox, QTableWidget, QListWidget)
    table_or_list = (QTableWidget, QListWidget)

    def _assert_no_overlaps_or_clipped_buttons(ctx_label: str, target_w: int) -> None:
        QApplication.processEvents()
        by_parent: dict[int, tuple[QWidget, list[QWidget]]] = {}
        for wid in insp.findChildren(QWidget):
            if not isinstance(wid, control_types) or not wid.isVisible():
                continue
            parent = wid.parentWidget()
            if parent is None or isinstance(parent, internal_parents):
                continue
            grandparent = parent.parentWidget()
            if grandparent is not None and isinstance(grandparent, table_or_list):
                continue

            pid = id(parent)
            if pid not in by_parent:
                by_parent[pid] = (parent, [])
            by_parent[pid][1].append(wid)

            if isinstance(wid, QPushButton):
                min_w = wid.minimumSizeHint().width()
                assert wid.width() >= min_w, (
                    f"[{ctx_label} w={target_w}] Button {wid.text()!r} clipped: "
                    f"width={wid.width()} < minimumSizeHint={min_w}"
                )

        assert by_parent, f"[{ctx_label} w={target_w}] Expected visible controls in inspector"
        for parent, siblings in by_parent.values():
            for idx, a in enumerate(siblings):
                for b in siblings[idx + 1 :]:
                    assert not a.geometry().intersects(b.geometry()), (
                        f"[{ctx_label} w={target_w}] Sibling overlap under "
                        f"{type(parent).__name__}: {type(a).__name__}({a.geometry()}) "
                        f"vs {type(b).__name__}({b.geometry()})"
                    )

    width_bounds = (
        (230, 210, 255),
        (340, 300, 380),
    )
    for target_w, min_w, max_w in width_bounds:
        win.resizeDocks([insp], [target_w], Qt.Orientation.Horizontal)
        qtbot.wait(50)
        assert min_w <= insp.width() <= max_w, (
            f"Expected inspector dock width in [{min_w}, {max_w}] for target={target_w}, "
            f"got {insp.width()}"
        )

        # 1. Project context
        insp.show_project_context()
        qtbot.wait(50)
        assert insp.current_context_mode() == "project"
        _assert_no_overlaps_or_clipped_buttons("project", target_w)

        # 2. Stream context (manual mode + historical mode, then restore manual)
        insp.show_stream_context(feed_stream_id)
        qtbot.wait(50)
        assert insp.current_context_mode() == "stream"
        _assert_no_overlaps_or_clipped_buttons("stream_manual", target_w)

        insp._on_data_mode_switched(1)
        qtbot.wait(50)
        _assert_no_overlaps_or_clipped_buttons("stream_historical", target_w)

        insp._on_data_mode_switched(0)
        qtbot.wait(50)
        _assert_no_overlaps_or_clipped_buttons("stream_manual_restored", target_w)

        # 3. Stage context
        insp.show_stage_context(proc_node_id)
        qtbot.wait(50)
        assert insp.current_context_mode() == "stage"
        _assert_no_overlaps_or_clipped_buttons("stage", target_w)

    assert win.close() is True


def test_table_inspector_dialog_filter_copy_and_expand_buttons(qtbot, tmp_path: Path) -> None:
    """Verify TableInspectorDialog cloning/filtering/TSV copy/palettes and expand buttons in ES & EN."""
    settings = _isolated_settings(tmp_path, "table_inspector.ini")
    win = MainWindow(settings_manager=settings)
    qtbot.addWidget(win)
    win.show()
    QApplication.processEvents()

    # 1. Verify expand-table buttons exist across Step 02, 04, 05, 06 with ES labels
    expand_buttons = [
        win.step_02_view.expand_anomalies_btn,
        win.step_02_view.expand_mapping_btn,
        win.step_04_view.expand_obs_btn,
        win.step_04_view.expand_nodal_btn,
        win.step_05_view.expand_adjustments_btn,
        win.step_05_view.expand_batch_btn,
        win.step_06_view.expand_kpi_btn,
    ]
    for btn in expand_buttons:
        assert isinstance(btn, QPushButton)
        assert btn.text() == "Expandir Tabla"
    assert win.step_02_view.chk_drop_zero_flow.isChecked() is True
    assert "flujo" in win.step_02_view.chk_drop_zero_flow.text().lower()

    # Verify table headers are sized to fit their full header text (no clipping)
    for table in (
        win.step_02_view.anomalies_table,
        win.step_02_view.mapping_table,
        win.step_04_view.nodal_table,
        win.step_06_view.kpi_table,
    ):
        fm = QFontMetrics(table.horizontalHeader().font())
        for c in range(table.columnCount()):
            h_item = table.horizontalHeaderItem(c)
            if h_item is not None and h_item.text():
                assert table.columnWidth(c) >= fm.horizontalAdvance(h_item.text()) + 20

    # 2. Open TableInspectorDialog on populated species_table (contains embedded QComboBox)
    src_table = win.step_01_view.species_table
    assert src_table.rowCount() >= 4
    dlg_light = TableInspectorDialog(src_table, "Especies", theme="light", parent=win)
    qtbot.addWidget(dlg_light)
    dlg_light.show()
    QApplication.processEvents()

    assert dlg_light.width() >= 720 and dlg_light.height() >= 420
    assert "Especies" in dlg_light.windowTitle()
    assert dlg_light.table.rowCount() == src_table.rowCount()
    assert dlg_light.table.columnCount() == src_table.columnCount()
    for c in range(src_table.columnCount()):
        assert (
            dlg_light.table.horizontalHeaderItem(c).text()
            == src_table.horizontalHeaderItem(c).text()
        )
    # Embedded QComboBox value in column 2 must be cloned as text
    first_unit_combo = src_table.cellWidget(0, 2)
    assert isinstance(first_unit_combo, QComboBox)
    assert dlg_light.table.item(0, 2).text() == first_unit_combo.currentText()

    # Light palette check
    assert dlg_light.palette().color(QPalette.ColorRole.Window).lightness() > 210
    assert dlg_light.palette().color(QPalette.ColorRole.WindowText).lightness() < 80

    # Live filter by species token "Cu"
    dlg_light.filter_edit.setText("Cu")
    QApplication.processEvents()
    visible_rows = [
        r for r in range(dlg_light.table.rowCount()) if not dlg_light.table.isRowHidden(r)
    ]
    assert len(visible_rows) == 1
    assert dlg_light.row_count_label.text() == f"1 de {src_table.rowCount()} filas"

    # Copy filtered rows to clipboard as TSV
    tsv = dlg_light.copy_to_clipboard_tsv()
    tsv_lines = tsv.splitlines()
    assert len(tsv_lines) == 2  # header + 1 visible row
    assert "\t" in tsv_lines[0]
    assert "Cu" in tsv_lines[1]
    assert QApplication.clipboard().text() == tsv
    assert "1" in dlg_light.status_label.text()

    # Clear filter restores all rows
    dlg_light.filter_edit.setText("")
    QApplication.processEvents()
    assert all(not dlg_light.table.isRowHidden(r) for r in range(dlg_light.table.rowCount()))
    dlg_light.close()

    # 3. Dark theme palette check + EN language switching on expand buttons
    win.set_language("en")
    for btn in expand_buttons:
        assert btn.text() == "Expand Table"
    assert "mass flow" in win.step_02_view.chk_drop_zero_flow.text().lower()

    dlg_dark = TableInspectorDialog(src_table, "Species", theme="dark", parent=win)
    qtbot.addWidget(dlg_dark)
    assert "Expanded Table Viewer" in dlg_dark.windowTitle()
    assert dlg_dark.palette().color(QPalette.ColorRole.Window).lightness() < 50
    assert dlg_dark.palette().color(QPalette.ColorRole.WindowText).lightness() > 200
    dlg_dark.close()

    assert win.close() is True



