"""Desktop application entry point for MetBalance Pro (`python -m app.main`)."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from typing import Optional

from PySide6.QtWidgets import QApplication

# Ensure metbalance_pro root is importable regardless of working directory
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# Ensure Matplotlib uses a writable config/cache directory on restricted environments
os.environ.setdefault("MPLCONFIGDIR", str(tempfile.gettempdir()))

from app.settings_manager import SettingsManager
from app.ui.main_window import MainWindow
from app.ui.ui_common import build_palette, load_stylesheet, load_svg_icon, ui_font


def main(argv: Optional[list[str]] = None) -> int:
    """Create QApplication, apply engineering theme & typography, and show MainWindow."""
    existing = QApplication.instance()
    created_app = existing is None
    app = existing if isinstance(existing, QApplication) else QApplication(argv or sys.argv)

    app.setStyle("Fusion")
    app.setProperty("metbalance_fusion_applied", True)
    app.setApplicationName("MetBalancePro")
    app.setOrganizationName("MetBalancePro")
    app.setFont(ui_font(9.5))

    settings = SettingsManager()
    theme = settings.get_theme()
    app.setPalette(build_palette(theme))
    app.setStyleSheet(load_stylesheet(theme))
    app.setWindowIcon(load_svg_icon("pid_concentrator.svg", theme=theme, size=32))

    window = MainWindow(settings_manager=settings)
    window.show()

    if created_app:
        return int(app.exec())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
