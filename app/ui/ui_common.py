"""Shared CAD UI utilities, theme palettes, SVG loaders, and central workflow state."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import pandas as pd
from PySide6.QtCore import QByteArray, QPoint, QRect, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPalette, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QHeaderView,
    QLayout,
    QLayoutItem,
    QSizePolicy,
    QStyle,
    QTableWidget,
    QTableWidgetItem,
    QWidget,
)

from core.etl import build_amira_p754_presets
from core.models import (
    BatchReconciliationResult,
    CircuitSpec,
    CleanedDataset,
    CleaningPlan,
    CorporateBrandingProfile,
    DataProfileReport,
    KPIReport,
    MeasurementPoint,
    ObservabilityReport,
    OutlierComparisonResult,
    ReconciliationResult,
    StepStatus,
    UncertaintyModel,
)
from core.topology import get_circuit_template

ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"
ICONS_DIR = ASSETS_DIR / "icons"
STYLES_DIR = ASSETS_DIR / "styles"
SAMPLE_DATA_DIR = ASSETS_DIR / "sample_data"

STEP_COUNT = 7


def monospace_font(point_size: float = 9.0, bold: bool = False) -> QFont:
    """Return crisp monospace font (Consolas) for tables, matrices, and metrics."""
    font = QFont("Consolas")
    font.setStyleHint(QFont.StyleHint.Monospace)
    font.setPointSizeF(point_size)
    font.setBold(bold)
    return font


def ui_font(point_size: float = 9.5, bold: bool = False) -> QFont:
    """Return industrial UI sans-serif font (Segoe UI)."""
    font = QFont("Segoe UI")
    font.setPointSizeF(point_size)
    font.setBold(bold)
    return font


def get_theme_palette(theme: str) -> dict[str, str]:
    """Return deterministic CAD color palette dictionary for light or dark engineering mode."""
    if theme == "dark":
        return {
            "app_bg": "#14181D",
            "panel_bg": "#1E242B",
            "header_bg": "#262E38",
            "border": "#334155",
            "grid_minor": "#1E242B",
            "grid_major": "#262E38",
            "text_primary": "#E2E8F0",
            "text_secondary": "#94A3B8",
            "accent": "#2E6F95",
            "accent_hover": "#3880AA",
            "validated": "#4ADE80",
            "validated_bg": "#14291E",
            "warning": "#FBBF24",
            "warning_bg": "#2B2111",
            "error": "#F87171",
            "error_bg": "#2C1517",
            "pending": "#94A3B8",
            "pending_bg": "#1E242B",
            "icon_stroke": "#CBD5E1",
            "stream_feed": "#3880AA",
            "stream_conc": "#4ADE80",
            "stream_tail": "#94A3B8",
            "stream_internal": "#60A5FA",
        }
    return {
        "app_bg": "#F3F5F7",
        "panel_bg": "#FFFFFF",
        "header_bg": "#E9EEF2",
        "border": "#CBD5E1",
        "grid_minor": "#E2E8F0",
        "grid_major": "#CBD5E1",
        "text_primary": "#1E293B",
        "text_secondary": "#475569",
        "accent": "#1B4965",
        "accent_hover": "#14384E",
        "validated": "#15803D",
        "validated_bg": "#ECFDF5",
        "warning": "#B45309",
        "warning_bg": "#FFFBEB",
        "error": "#B91C1C",
        "error_bg": "#FEF2F2",
        "pending": "#64748B",
        "pending_bg": "#F1F5F9",
        "icon_stroke": "#334155",
        "stream_feed": "#1B4965",
        "stream_conc": "#15803D",
        "stream_tail": "#64748B",
        "stream_internal": "#2E6F95",
    }


def build_palette(theme: str) -> QPalette:
    """Construct a complete explicit QPalette isolating Light/Dark mode from OS Dark Mode."""
    pal = QPalette()
    active_groups = (QPalette.ColorGroup.Active, QPalette.ColorGroup.Inactive)

    if theme == "dark":
        window = QColor("#14181D")
        window_text = QColor("#E2E8F0")
        base = QColor("#1E242B")
        alt_base = QColor("#191E24")
        text = QColor("#E2E8F0")
        button = QColor("#262E38")
        button_text = QColor("#E2E8F0")
        tooltip_base = QColor("#262E38")
        tooltip_text = QColor("#E2E8F0")
        highlight = QColor("#233B4D")
        highlighted_text = QColor("#FFFFFF")
        placeholder = QColor("#64748B")
        bright_text = QColor("#FFFFFF")
        link = QColor("#3880AA")
        link_visited = QColor("#60A5FA")
        light = QColor("#334155")
        midlight = QColor("#262E38")
        mid = QColor("#1E242B")
        dark = QColor("#0F1419")
        shadow = QColor("#0B0E12")
        disabled_text = QColor("#64748B")
        disabled_base = QColor("#1A2027")
        disabled_button = QColor("#1A2027")
        disabled_placeholder = QColor("#475569")
    else:
        window = QColor("#F3F5F7")
        window_text = QColor("#1E293B")
        base = QColor("#FFFFFF")
        alt_base = QColor("#F8FAFC")
        text = QColor("#1E293B")
        button = QColor("#FFFFFF")
        button_text = QColor("#1E293B")
        tooltip_base = QColor("#FFFFFF")
        tooltip_text = QColor("#1E293B")
        highlight = QColor("#DCE7EF")
        highlighted_text = QColor("#14384E")
        placeholder = QColor("#64748B")
        bright_text = QColor("#FFFFFF")
        link = QColor("#1B4965")
        link_visited = QColor("#14384E")
        light = QColor("#FFFFFF")
        midlight = QColor("#E9EEF2")
        mid = QColor("#CBD5E1")
        dark = QColor("#94A3B8")
        shadow = QColor("#64748B")
        disabled_text = QColor("#94A3B8")
        disabled_base = QColor("#F8FAFC")
        disabled_button = QColor("#F1F5F9")
        disabled_placeholder = QColor("#94A3B8")

    for group in active_groups:
        pal.setColor(group, QPalette.ColorRole.Window, window)
        pal.setColor(group, QPalette.ColorRole.WindowText, window_text)
        pal.setColor(group, QPalette.ColorRole.Base, base)
        pal.setColor(group, QPalette.ColorRole.AlternateBase, alt_base)
        pal.setColor(group, QPalette.ColorRole.Text, text)
        pal.setColor(group, QPalette.ColorRole.Button, button)
        pal.setColor(group, QPalette.ColorRole.ButtonText, button_text)
        pal.setColor(group, QPalette.ColorRole.ToolTipBase, tooltip_base)
        pal.setColor(group, QPalette.ColorRole.ToolTipText, tooltip_text)
        pal.setColor(group, QPalette.ColorRole.Highlight, highlight)
        pal.setColor(group, QPalette.ColorRole.HighlightedText, highlighted_text)
        pal.setColor(group, QPalette.ColorRole.PlaceholderText, placeholder)
        pal.setColor(group, QPalette.ColorRole.BrightText, bright_text)
        pal.setColor(group, QPalette.ColorRole.Link, link)
        pal.setColor(group, QPalette.ColorRole.LinkVisited, link_visited)
        pal.setColor(group, QPalette.ColorRole.Light, light)
        pal.setColor(group, QPalette.ColorRole.Midlight, midlight)
        pal.setColor(group, QPalette.ColorRole.Mid, mid)
        pal.setColor(group, QPalette.ColorRole.Dark, dark)
        pal.setColor(group, QPalette.ColorRole.Shadow, shadow)

    dg = QPalette.ColorGroup.Disabled
    pal.setColor(dg, QPalette.ColorRole.Window, window)
    pal.setColor(dg, QPalette.ColorRole.WindowText, disabled_text)
    pal.setColor(dg, QPalette.ColorRole.Base, disabled_base)
    pal.setColor(dg, QPalette.ColorRole.AlternateBase, alt_base)
    pal.setColor(dg, QPalette.ColorRole.Text, disabled_text)
    pal.setColor(dg, QPalette.ColorRole.Button, disabled_button)
    pal.setColor(dg, QPalette.ColorRole.ButtonText, disabled_text)
    pal.setColor(dg, QPalette.ColorRole.ToolTipBase, tooltip_base)
    pal.setColor(dg, QPalette.ColorRole.ToolTipText, tooltip_text)
    pal.setColor(dg, QPalette.ColorRole.Highlight, highlight)
    pal.setColor(dg, QPalette.ColorRole.HighlightedText, disabled_text)
    pal.setColor(dg, QPalette.ColorRole.PlaceholderText, disabled_placeholder)
    pal.setColor(dg, QPalette.ColorRole.BrightText, bright_text)
    pal.setColor(dg, QPalette.ColorRole.Light, light)
    pal.setColor(dg, QPalette.ColorRole.Midlight, midlight)
    pal.setColor(dg, QPalette.ColorRole.Mid, mid)
    pal.setColor(dg, QPalette.ColorRole.Dark, dark)
    pal.setColor(dg, QPalette.ColorRole.Shadow, shadow)
    return pal


def load_stylesheet(theme: str) -> str:
    """Read QSS stylesheet contents for 'light' or 'dark' engineering mode."""
    filename = "engineering_dark.qss" if theme == "dark" else "engineering_light.qss"
    path = STYLES_DIR / filename
    if not path.exists():
        return ""
    qss = path.read_text(encoding="utf-8")
    return qss.replace("__ICONS_DIR__", ICONS_DIR.as_posix())


def load_svg_bytes(filename: str, stroke_color: Optional[str] = None) -> QByteArray:
    """Load an SVG icon file and optionally recolor its default #334155 stroke."""
    path = ICONS_DIR / filename
    if not path.exists():
        return QByteArray()
    svg_text = path.read_text(encoding="utf-8")
    if stroke_color and stroke_color.lower() != "#334155":
        svg_text = svg_text.replace("#334155", stroke_color)
    return QByteArray(svg_text.encode("utf-8"))


def load_svg_icon(
    filename: str,
    theme: str = "light",
    color_override: Optional[str] = None,
    size: int = 24,
    dpr: Optional[float] = None,
) -> QIcon:
    """Render a vector SVG icon cleanly with explicit DPI bounds (no top-left clipping)."""
    stroke = color_override or get_theme_palette(theme)["icon_stroke"]
    svg_bytes = load_svg_bytes(filename, stroke_color=stroke)
    if svg_bytes.isEmpty():
        return QIcon()
    renderer = QSvgRenderer(svg_bytes)
    if not renderer.isValid():
        return QIcon()

    if dpr is None:
        screen_dpr = 1.0
        app = QApplication.instance()
        if isinstance(app, QApplication):
            screen = app.primaryScreen()
            if screen is not None:
                screen_dpr = float(screen.devicePixelRatio())
        resolved_dpr = max(2.0, screen_dpr)
    else:
        resolved_dpr = max(1.0, float(dpr))

    px = max(1, int(round(size * resolved_dpr)))
    effective_dpr = px / float(size)

    pixmap = QPixmap(px, px)
    pixmap.fill(Qt.GlobalColor.transparent)
    pixmap.setDevicePixelRatio(effective_dpr)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    renderer.render(painter, QRectF(0.0, 0.0, float(size), float(size)))
    painter.end()
    return QIcon(pixmap)


class FlowLayout(QLayout):
    """Wrapping horizontal layout based on the official Qt FlowLayout pattern."""

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        margin: int = 0,
        h_spacing: int = 6,
        v_spacing: int = 6,
    ) -> None:
        super().__init__(parent)
        self._item_list: list[QLayoutItem] = []
        self._h_space = h_spacing
        self._v_space = v_spacing
        self.setContentsMargins(margin, margin, margin, margin)

    def __del__(self) -> None:
        while self._item_list:
            self.takeAt(0)

    def addItem(self, item: QLayoutItem) -> None:
        self._item_list.append(item)

    def horizontalSpacing(self) -> int:
        if self._h_space >= 0:
            return self._h_space
        return self._smart_spacing(QStyle.PixelMetric.PM_LayoutHorizontalSpacing)

    def verticalSpacing(self) -> int:
        if self._v_space >= 0:
            return self._v_space
        return self._smart_spacing(QStyle.PixelMetric.PM_LayoutVerticalSpacing)

    def count(self) -> int:
        return len(self._item_list)

    def itemAt(self, index: int) -> Optional[QLayoutItem]:
        if 0 <= index < len(self._item_list):
            return self._item_list[index]
        return None

    def takeAt(self, index: int) -> Optional[QLayoutItem]:
        if 0 <= index < len(self._item_list):
            return self._item_list.pop(index)
        return None

    def expandingDirections(self) -> Qt.Orientations:
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._do_layout(QRect(0, 0, width, 0), test_only=True)

    def setGeometry(self, rect: QRect) -> None:
        super().setGeometry(rect)
        self._do_layout(rect, test_only=False)

    def sizeHint(self) -> QSize:
        return self.minimumSize()

    def minimumSize(self) -> QSize:
        size = QSize()
        for item in self._item_list:
            wid = item.widget()
            if wid is not None and wid.isHidden():
                continue
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        size += QSize(margins.left() + margins.right(), margins.top() + margins.bottom())
        return size

    def _do_layout(self, rect: QRect, test_only: bool) -> int:
        margins = self.contentsMargins()
        effective_rect = rect.adjusted(
            margins.left(), margins.top(), -margins.right(), -margins.bottom()
        )
        x = effective_rect.x()
        y = effective_rect.y()
        line_height = 0

        for item in self._item_list:
            wid = item.widget()
            if wid is not None and wid.isHidden():
                continue
            space_x = self.horizontalSpacing()
            if space_x == -1 and wid is not None:
                space_x = wid.style().layoutSpacing(
                    QSizePolicy.ControlType.PushButton,
                    QSizePolicy.ControlType.PushButton,
                    Qt.Orientation.Horizontal,
                )
            space_y = self.verticalSpacing()
            if space_y == -1 and wid is not None:
                space_y = wid.style().layoutSpacing(
                    QSizePolicy.ControlType.PushButton,
                    QSizePolicy.ControlType.PushButton,
                    Qt.Orientation.Vertical,
                )
            space_x = max(0, space_x)
            space_y = max(0, space_y)

            item_size = item.sizeHint().expandedTo(item.minimumSize())
            next_x = x + item_size.width() + space_x
            if next_x - space_x > effective_rect.right() + 1 and line_height > 0:
                x = effective_rect.x()
                y = y + line_height + space_y
                next_x = x + item_size.width() + space_x
                line_height = 0

            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), item_size))

            x = next_x
            line_height = max(line_height, item_size.height())

        return y + line_height - rect.y() + margins.bottom()

    def _smart_spacing(self, pm: QStyle.PixelMetric) -> int:
        parent = self.parent()
        if parent is None:
            return -1
        if isinstance(parent, QWidget):
            return parent.style().pixelMetric(pm, None, parent)
        return parent.spacing()


def configure_dense_table(
    table: QTableWidget,
    stretch_last: bool = True,
    editable: bool = False,
    has_embedded_widgets: bool = False,
) -> None:
    """Apply consistent high-density industrial table formatting."""
    table.setAlternatingRowColors(True)
    table.setWordWrap(False)
    v_header = table.verticalHeader()
    v_header.setVisible(False)
    if has_embedded_widgets:
        v_header.setMinimumSectionSize(32)
        v_header.setDefaultSectionSize(32)
        v_header.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
    else:
        v_header.setDefaultSectionSize(24)
    header = table.horizontalHeader()
    header.setHighlightSections(False)
    header.setStretchLastSection(stretch_last)
    header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
    if not editable:
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)


def make_text_item(
    text: str,
    *,
    editable: bool = False,
    align: Qt.AlignmentFlag = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
    color_hex: Optional[str] = None,
    bg_hex: Optional[str] = None,
    mono: bool = False,
    bold: bool = False,
    user_data: Any = None,
) -> QTableWidgetItem:
    """Create a configured QTableWidgetItem for text or identifier cells."""
    item = QTableWidgetItem(text)
    item.setTextAlignment(align)
    flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
    if editable:
        flags |= Qt.ItemFlag.ItemIsEditable
    item.setFlags(flags)
    if mono:
        item.setFont(monospace_font(9.0, bold=bold))
    elif bold:
        item.setFont(ui_font(9.0, bold=True))
    if color_hex:
        item.setForeground(QColor(color_hex))
    if bg_hex:
        item.setBackground(QColor(bg_hex))
    if user_data is not None:
        item.setData(Qt.ItemDataRole.UserRole, user_data)
    return item


def make_numeric_item(
    value: Optional[float],
    fmt: str = "{:.4f}",
    na_text: str = "N/D",
    *,
    editable: bool = False,
    color_hex: Optional[str] = None,
    bg_hex: Optional[str] = None,
    bold: bool = False,
    user_data: Any = None,
) -> QTableWidgetItem:
    """Create a right-aligned Consolas monospace numeric QTableWidgetItem."""
    if value is None:
        text = na_text
    else:
        text = fmt.format(value)
    return make_text_item(
        text,
        editable=editable,
        align=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
        color_hex=color_hex,
        bg_hex=bg_hex,
        mono=True,
        bold=bold,
        user_data=user_data,
    )


def apply_status_role(widget: QWidget, role: str) -> None:
    """Update dynamic Qt property 'statusRole' and refresh QSS styling cleanly."""
    widget.setProperty("statusRole", role)
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()


def status_to_role(status: StepStatus) -> str:
    """Map StepStatus enum to QSS statusRole property."""
    if status == StepStatus.VALIDATED:
        return "validated"
    if status == StepStatus.WARNINGS:
        return "warning"
    if status == StepStatus.BLOCKED:
        return "error"
    return "pending"


def set_combo_by_data(combo: QComboBox, target_data: Any) -> bool:
    """Select the combo item whose UserRole data equals target_data."""
    idx = combo.findData(target_data)
    if idx >= 0:
        combo.setCurrentIndex(idx)
        return True
    return False


@dataclass
class InspectorPayload:
    """Structured properties and technical alerts pushed to InspectorDock."""

    title: str
    properties: list[tuple[str, str]] = field(default_factory=list)
    alerts: list[tuple[str, str]] = field(default_factory=list)  # (severity: error|warning|info, msg)
    blocked_reason: Optional[str] = None


@dataclass
class ProjectState:
    """Central in-memory workflow state shared across all 7 sequential engineering views."""

    circuit: CircuitSpec = field(default_factory=lambda: get_circuit_template("polymetallic_4prod"))
    raw_df: Optional[pd.DataFrame] = None
    profile_report: Optional[DataProfileReport] = None
    cleaning_plan: Optional[CleaningPlan] = None
    cleaned_dataset: Optional[CleanedDataset] = None
    uncertainty_model: Optional[UncertaintyModel] = None
    selected_period_idx: int = 0
    observability_report: Optional[ObservabilityReport] = None
    reconciliation_result: Optional[ReconciliationResult] = None
    batch_result: Optional[BatchReconciliationResult] = None
    outlier_comparison: Optional[OutlierComparisonResult] = None
    excluded_outliers: list[str] = field(default_factory=list)
    kpi_report: Optional[KPIReport] = None
    branding: CorporateBrandingProfile = field(default_factory=CorporateBrandingProfile)
    step_statuses: list[StepStatus] = field(
        default_factory=lambda: [StepStatus.PENDING] * STEP_COUNT
    )
    step_block_reasons: dict[int, str] = field(default_factory=dict)

    def clear_from_step(self, start_step_idx: int) -> None:
        """Reset downstream workflow artifacts and statuses from start_step_idx onward."""
        if start_step_idx <= 1:
            self.raw_df = None
            self.profile_report = None
            self.cleaning_plan = None
            self.cleaned_dataset = None
            self.selected_period_idx = 0
        if start_step_idx <= 2:
            self.uncertainty_model = None
        if start_step_idx <= 3:
            self.observability_report = None
        if start_step_idx <= 4:
            self.reconciliation_result = None
            self.batch_result = None
            self.outlier_comparison = None
            self.excluded_outliers = []
        if start_step_idx <= 5:
            self.kpi_report = None

        for idx in range(start_step_idx, STEP_COUNT):
            self.step_statuses[idx] = StepStatus.PENDING
            self.step_block_reasons.pop(idx, None)

    def is_step_completed(self, step_idx: int) -> bool:
        """Return True if step_idx allows advancing to subsequent steps."""
        return self.step_statuses[step_idx] in (StepStatus.VALIDATED, StepStatus.WARNINGS)

    def is_step_unlocked(self, step_idx: int) -> bool:
        """Return True when all preceding sequential steps have completed."""
        if step_idx == 0:
            return True
        return all(self.is_step_completed(prev) for prev in range(step_idx))

    def current_period(self):
        """Return currently selected MeasurementPoint or None."""
        if not self.cleaned_dataset or not self.cleaned_dataset.periods:
            return None
        idx = max(0, min(self.selected_period_idx, len(self.cleaned_dataset.periods) - 1))
        return self.cleaned_dataset.periods[idx]

    def ensure_manual_dataset(self) -> MeasurementPoint:
        """Return active MeasurementPoint, creating a single-period manual dataset if empty."""
        if self.cleaned_dataset is None or not self.cleaned_dataset.periods:
            point = MeasurementPoint(period_id="P-001")
            self.cleaned_dataset = CleanedDataset(
                source_path="manual_canvas",
                file_sha256=hashlib.sha256(b"manual_canvas").hexdigest(),
                periods=[point],
                audit_log=[],
                raw_row_count=1,
                clean_row_count=1,
            )
            self.selected_period_idx = 0
            return point
        current = self.current_period()
        assert current is not None
        return current

    def set_manual_stream_value(
        self,
        stream_id: str,
        species_id: Optional[str],
        value: Optional[float],
    ) -> MeasurementPoint:
        """Update a manual stream mass flow or assay value and refresh canonical SHA-256."""
        point = self.ensure_manual_dataset()
        clean_val: Optional[float] = (
            float(value) if value is not None and math.isfinite(float(value)) else None
        )
        if species_id is None:
            point.mass_flows[stream_id] = clean_val
        else:
            point.assays.setdefault(stream_id, {})[species_id] = clean_val

        canonical = json.dumps(
            point.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        assert self.cleaned_dataset is not None
        self.cleaned_dataset.file_sha256 = hashlib.sha256(canonical).hexdigest()
        return point

    def ensure_uncertainty_model(self, alpha: float = 0.05) -> UncertaintyModel:
        """Build and store AMIRA P754 uncertainty presets if no model is loaded yet."""
        if self.uncertainty_model is None:
            self.uncertainty_model = build_amira_p754_presets(self.circuit, alpha=alpha)
        return self.uncertainty_model
