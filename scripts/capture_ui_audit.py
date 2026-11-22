"""Offscreen/GUI visual audit capture script for MetBalance Pro at 1366x768.

Executes the complete Dataset A workflow in Light Mode (1366x768) and saves:
- All 7 key application screens (+ scrolled views of Diagnostics and Solver/Batch)
- A test corporate PDF report
- The 3 300-DPI Matplotlib chart PNGs produced by reporting.pdf_exporter

Outputs default to a temporary directory outside the repository so git stays clean.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path
from typing import Callable

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _pump_events(app, rounds: int = 6, delay_ms: int = 25) -> None:
    """Process pending Qt events, layouts, and single-shot timers."""
    from PySide6.QtCore import QEventLoop, QTimer

    for _ in range(rounds):
        app.processEvents()
        loop = QEventLoop()
        QTimer.singleShot(delay_ms, loop.quit)
        loop.exec()
    app.processEvents()


def _run_async_solver_action(app, view, trigger: Callable[[], None], timeout_ms: int = 30000) -> bool:
    """Trigger an async QThread solver action on SolverResultsView and wait for completion."""
    from PySide6.QtCore import QEventLoop, QTimer

    loop = QEventLoop()
    result_flag = [False]
    timed_out = [False]

    def _on_done(ok: bool) -> None:
        result_flag[0] = bool(ok)
        if loop.isRunning():
            loop.quit()

    def _on_timeout() -> None:
        timed_out[0] = True
        if loop.isRunning():
            loop.quit()

    timer = QTimer()
    timer.setSingleShot(True)
    timer.timeout.connect(_on_timeout)
    view.reconciliation_completed.connect(_on_done)
    try:
        timer.start(timeout_ms)
        trigger()
        loop.exec()
    finally:
        timer.stop()
        try:
            view.reconciliation_completed.disconnect(_on_done)
        except (RuntimeError, TypeError):
            pass

    if timed_out[0]:
        raise TimeoutError(f"Solver action timed out after {timeout_ms} ms")
    _pump_events(app, rounds=4, delay_ms=20)
    return result_flag[0]


def _grab_window(app, widget, out_path: Path) -> tuple[int, int]:
    """Flush Qt events and save a PNG grab of widget to out_path."""
    _pump_events(app, rounds=4, delay_ms=25)
    pixmap = widget.grab()
    if pixmap.isNull():
        raise RuntimeError(f"Grabbed null QPixmap for {out_path.name}")
    ok = pixmap.save(str(out_path), "PNG")
    if not ok:
        raise RuntimeError(f"Failed to save screenshot to {out_path}")
    return pixmap.width(), pixmap.height()


def capture_visual_audit(out_dir: Path, *, width: int = 1366, height: int = 768) -> dict[str, Path]:
    """Run full Dataset A workflow at laptop resolution and capture all audit PNGs."""
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication

    from app.i18n.manager import get_i18n, reset_i18n
    from app.settings_manager import SettingsManager
    from app.ui.main_window import MainWindow
    from reporting.pdf_exporter import (
        _render_adjustments_png,
        _render_chi2_control_png,
        _render_parity_png,
    )

    out_dir.mkdir(parents=True, exist_ok=True)
    reset_i18n()

    app = QApplication.instance()
    if app is None:
        app = QApplication(["capture_ui_audit"])

    ini_path = out_dir / "_audit_settings.ini"
    qs = QSettings(str(ini_path), QSettings.Format.IniFormat)
    qs.clear()
    qs.sync()
    settings = SettingsManager(qsettings=qs)
    settings.set_theme("light")
    settings.set_language("es")

    win = MainWindow(settings_manager=settings)
    win.set_language("es")
    win.set_theme("light", persist=False)
    win.resize(width, height)
    win.show()
    _pump_events(app, rounds=6, delay_ms=30)

    captured: dict[str, Path] = {}

    try:
        # 1. Step 01: Flowsheet Canvas
        win.go_to_step(0)
        win.canvas.zoom_fit()
        p1 = out_dir / "01_flujograma.png"
        _grab_window(app, win, p1)
        captured["01_flujograma"] = p1

        # 2. Step 02: Load Sample A -> Anomalies sub-tab
        win.load_sample_a()
        win.step_02_view.tabs.setCurrentIndex(0)
        win.step_02_view.anomalies_scroll.verticalScrollBar().setValue(0)
        p2 = out_dir / "02_etl_anomalias.png"
        _grab_window(app, win, p2)
        captured["02_etl_anomalias"] = p2

        # 3. Step 02: Cleaning Plan sub-tab (apply cleaning so audit log is populated)
        win.step_02_view.tabs.setCurrentIndex(2)
        win.step_02_view.apply_and_validate()
        win.step_02_view.plan_scroll.verticalScrollBar().setValue(0)
        p3 = out_dir / "03_etl_plan_limpieza.png"
        _grab_window(app, win, p3)
        captured["03_etl_plan_limpieza"] = p3

        # 4. Step 03: Uncertainties (AMIRA P754 presets + validation)
        win.go_to_step(2, show_canvas=False)
        win.step_03_view.apply_amira_presets()
        ok_unc = win.step_03_view.validate_uncertainties()
        if not ok_unc:
            raise RuntimeError("Step 03 uncertainty validation failed on Sample A")
        win.step_03_view.scroll_area.verticalScrollBar().setValue(0)
        p4 = out_dir / "04_incertidumbres.png"
        _grab_window(app, win, p4)
        captured["04_incertidumbres"] = p4

        # 5. Step 04: Observability & Nodal Closure (top + scrolled bottom)
        win.go_to_step(3, show_canvas=False)
        obs = win.step_04_view.run_diagnostics()
        if obs is None or not obs.can_reconcile:
            raise RuntimeError("Step 04 observability diagnostics failed on Sample A")
        win.step_04_view.scroll_area.verticalScrollBar().setValue(0)
        p5 = out_dir / "05_observabilidad.png"
        _grab_window(app, win, p5)
        captured["05_observabilidad"] = p5

        vbar4 = win.step_04_view.scroll_area.verticalScrollBar()
        vbar4.setValue(vbar4.maximum())
        p5b = out_dir / "05b_observabilidad_scrolled.png"
        _grab_window(app, win, p5b)
        captured["05b_observabilidad_scrolled"] = p5b

        # 6. Step 05: WLS Solver (Single + Batch) — top & scrolled bottom
        win.go_to_step(4, show_canvas=False)
        ok_single = _run_async_solver_action(app, win.step_05_view, win.step_05_view.run_single)
        if not ok_single:
            raise RuntimeError("Step 05 single WLS reconciliation failed on Sample A")
        ok_batch = _run_async_solver_action(app, win.step_05_view, win.step_05_view.run_batch)
        if not ok_batch or win.state.batch_result is None:
            raise RuntimeError("Step 05 batch WLS reconciliation failed on Sample A")

        win.step_05_view.scroll_area.verticalScrollBar().setValue(0)
        p6 = out_dir / "06_solver_lote.png"
        _grab_window(app, win, p6)
        captured["06_solver_lote"] = p6

        vbar5 = win.step_05_view.scroll_area.verticalScrollBar()
        vbar5.setValue(vbar5.maximum())
        p6b = out_dir / "06b_solver_lote_scrolled.png"
        _grab_window(app, win, p6b)
        captured["06b_solver_lote_scrolled"] = p6b

        # 7. Step 06: Charts & KPIs
        win.go_to_step(5)
        win.step_06_view.body_scroll.verticalScrollBar().setValue(0)
        p7 = out_dir / "07_graficos_kpis.png"
        _grab_window(app, win, p7)
        captured["07_graficos_kpis"] = p7

        # 8. Step 07: Export test PDF + direct PDF Matplotlib chart PNGs
        win.go_to_step(6)
        _pump_events(app, rounds=3, delay_ms=20)
        pdf_path = win.step_07_view.export_pdf_to(out_dir / "audit_sample_a.pdf")
        if pdf_path is None or not pdf_path.exists():
            raise RuntimeError("Step 07 PDF export failed on Sample A")
        captured["audit_pdf"] = pdf_path

        res = win.state.reconciliation_result
        batch = win.state.batch_result
        if res is None or batch is None:
            raise RuntimeError("Missing reconciliation/batch results for PDF chart export")

        i18n = get_i18n()
        primary_hex = win.state.branding.primary_color or "#1B4965"

        chart_specs = [
            ("pdf_01_parity", _render_parity_png(res, i18n, primary_hex)),
            ("pdf_02_adjustments", _render_adjustments_png(res, i18n)),
            ("pdf_03_chi2", _render_chi2_control_png(batch, i18n, primary_hex)),
        ]
        for key, buf in chart_specs:
            target = out_dir / f"{key}.png"
            target.write_bytes(buf.getvalue())
            captured[key] = target
    finally:
        win.close()
        _pump_events(app, rounds=3, delay_ms=15)
        if ini_path.exists():
            try:
                ini_path.unlink()
            except OSError:
                pass

    return captured


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Capture 1366x768 Light Mode UI screenshots and PDF chart PNGs for visual QA."
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path(tempfile.gettempdir()) / "metbalance_visual_audit",
        help="Output directory for temporary audit images and test PDF.",
    )
    parser.add_argument("--width", type=int, default=1366, help="MainWindow width in px.")
    parser.add_argument("--height", type=int, default=768, help="MainWindow height in px.")
    parser.add_argument(
        "--gui",
        action="store_true",
        help="Use native desktop Qt platform instead of offscreen.",
    )
    args = parser.parse_args(argv)

    if not args.gui:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    captured = capture_visual_audit(args.out, width=args.width, height=args.height)
    print(f"Visual audit output directory: {args.out}")
    for name, path in captured.items():
        size_kb = path.stat().st_size / 1024.0
        print(f"  - {name}: {path} ({size_kb:.1f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
