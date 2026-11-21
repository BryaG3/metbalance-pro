"""Properties and technical alerts inspector dock."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QDockWidget,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QSplitter,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from app.i18n.manager import get_i18n

from .ui_common import (
    InspectorPayload,
    apply_status_role,
    configure_dense_table,
    get_theme_palette,
    load_svg_icon,
    make_text_item,
    ui_font,
)


class InspectorDock(QDockWidget):
    """Dockable property table and technical alert inspector for nodes, streams, and steps."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("InspectorDock")
        self.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea
            | Qt.DockWidgetArea.RightDockWidgetArea
            | Qt.DockWidgetArea.BottomDockWidgetArea
        )
        self.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetMovable
            | QDockWidget.DockWidgetFeature.DockWidgetFloatable
            | QDockWidget.DockWidgetFeature.DockWidgetClosable
        )

        self._i18n = get_i18n()
        self._theme = "light"
        self._payload: Optional[InspectorPayload] = None

        container = QWidget(self)
        container.setAutoFillBackground(True)
        root_layout = QVBoxLayout(container)
        root_layout.setContentsMargins(6, 6, 6, 6)
        root_layout.setSpacing(6)

        self.header_label = QLabel(container)
        self.header_label.setFont(ui_font(9.5, bold=True))
        self.header_label.setWordWrap(True)
        root_layout.addWidget(self.header_label)

        self.blocked_banner = QLabel(container)
        self.blocked_banner.setWordWrap(True)
        self.blocked_banner.setContentsMargins(6, 5, 6, 5)
        apply_status_role(self.blocked_banner, "error")
        self.blocked_banner.setVisible(False)
        root_layout.addWidget(self.blocked_banner)

        splitter = QSplitter(Qt.Orientation.Vertical, container)
        splitter.setHandleWidth(6)
        splitter.setChildrenCollapsible(False)

        self.props_table = QTableWidget(0, 2, splitter)
        self.props_table.setObjectName("InspectorPropertiesTable")
        configure_dense_table(self.props_table, stretch_last=True, editable=False)
        self.props_table.setHorizontalHeaderLabels(["", ""])
        splitter.addWidget(self.props_table)

        alerts_panel = QWidget(splitter)
        alerts_layout = QVBoxLayout(alerts_panel)
        alerts_layout.setContentsMargins(0, 4, 0, 0)
        alerts_layout.setSpacing(4)

        self.alerts_header_label = QLabel(alerts_panel)
        self.alerts_header_label.setFont(ui_font(9.0, bold=True))
        alerts_layout.addWidget(self.alerts_header_label)

        self.alerts_list = QListWidget(alerts_panel)
        self.alerts_list.setObjectName("InspectorAlertsList")
        self.alerts_list.setWordWrap(True)
        alerts_layout.addWidget(self.alerts_list)

        splitter.addWidget(alerts_panel)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)

        root_layout.addWidget(splitter, 1)
        self.setWidget(container)
        self.setMinimumWidth(270)

        self.retranslate_ui()

    def set_theme(self, theme: str) -> None:
        """Refresh alert colors and icons for active theme."""
        self._theme = theme
        self._render_payload()

    def set_payload(self, payload: Optional[InspectorPayload]) -> None:
        """Display properties, blocked reasons, and technical alerts in the inspector."""
        self._payload = payload
        self._render_payload()

    def retranslate_ui(self) -> None:
        """Translate inspector dock title, table headers, and alert prefixes."""
        self.setWindowTitle(self._i18n.t("docks.inspector_title"))
        self.props_table.setHorizontalHeaderLabels(
            [
                self._i18n.t("docks.inspector_col_property"),
                self._i18n.t("docks.inspector_col_value"),
            ]
        )
        self._render_payload()

    def _render_payload(self) -> None:
        palette = get_theme_palette(self._theme)

        if self._payload is None:
            self.header_label.setText(self._i18n.t("docks.inspector_empty"))
            self.blocked_banner.setVisible(False)
            self.props_table.setRowCount(0)
            self.alerts_header_label.setText(
                self._i18n.t("docks.inspector_alerts_count", count=0)
            )
            self.alerts_list.clear()
            return

        payload = self._payload
        self.header_label.setText(payload.title)

        if payload.blocked_reason:
            self.blocked_banner.setText(
                self._i18n.t("docks.inspector_step_blocked_reason", reason=payload.blocked_reason)
            )
            self.blocked_banner.setVisible(True)
        else:
            self.blocked_banner.setVisible(False)

        self.props_table.setRowCount(len(payload.properties))
        for row, (key, val) in enumerate(payload.properties):
            self.props_table.setItem(row, 0, make_text_item(key))
            self.props_table.setItem(
                row,
                1,
                make_text_item(
                    val,
                    align=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                    mono=True,
                ),
            )
        self.props_table.resizeColumnToContents(0)

        self.alerts_header_label.setText(
            self._i18n.t("docks.inspector_alerts_count", count=len(payload.alerts))
        )
        self.alerts_list.clear()
        for severity, msg in payload.alerts:
            sev_lower = severity.lower()
            if sev_lower == "error":
                prefix = self._i18n.t("common.error")
                color_hex = palette["error"]
                icon_name = "alert_triangle.svg"
            elif sev_lower == "warning":
                prefix = self._i18n.t("common.warning")
                color_hex = palette["warning"]
                icon_name = "alert_triangle.svg"
            else:
                prefix = self._i18n.t("common.info")
                color_hex = palette["text_secondary"]
                icon_name = "check_circle.svg"

            list_item = QListWidgetItem(f"[{prefix}] {msg}")
            list_item.setForeground(QColor(color_hex))
            list_item.setIcon(
                load_svg_icon(icon_name, theme=self._theme, color_override=color_hex, size=16)
            )
            self.alerts_list.addItem(list_item)
