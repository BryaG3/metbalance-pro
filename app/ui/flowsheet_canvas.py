"""Excalidraw-style interactive engineering flowsheet canvas and proportional Sankey renderer."""

from __future__ import annotations

import math
from collections import defaultdict, deque
from enum import Enum
from typing import Optional

from PySide6.QtCore import SIGNAL, QPoint, QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QAction,
    QBrush,
    QColor,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPainterPathStroker,
    QPen,
    QPolygonF,
    QUndoCommand,
    QUndoStack,
    QWheelEvent,
)
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QGraphicsItem,
    QGraphicsPathItem,
    QGraphicsPolygonItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsSimpleTextItem,
    QGraphicsTextItem,
    QGraphicsView,
    QHBoxLayout,
    QMenu,
    QStyleOptionGraphicsItem,
    QToolButton,
    QWidget,
)

from app.i18n.manager import get_i18n
from core.models import (
    CircuitSpec,
    CleanedDataset,
    GradeUnit,
    MassUnit,
    ObservabilityReport,
    ReconciliationResult,
    SpeciesSpec,
    StreamRole,
    StreamSpec,
    UnitType,
    VariableClass,
)
from core.topology import get_circuit_template

from .ui_common import get_theme_palette, load_svg_bytes, load_svg_icon, monospace_font, ui_font

GRID_STEP = 24.0
SNAP_STEP = 12.0
PROCESS_W = 192.0
PROCESS_H = 76.0
TERMINAL_W = 148.0
TERMINAL_H = 58.0
PORT_RADIUS = 4.5
PORT_HIT_RADIUS = 11.0

_UNIT_ICON_MAP: dict[UnitType, str] = {
    UnitType.MIXER_SPLITTER: "pid_mixer_splitter.svg",
    UnitType.FLOTATION_CELL: "pid_flotation_cell.svg",
    UnitType.CONCENTRATOR: "pid_concentrator.svg",
    UnitType.ROUGHER: "pid_rougher.svg",
    UnitType.SCAVENGER: "pid_scavenger.svg",
    UnitType.CLEANER: "pid_cleaner.svg",
}

_TEMPLATE_MENU_ITEMS: tuple[tuple[str, str], ...] = (
    ("concentrator_2prod", "step01.template_2prod"),
    ("concentrator_3prod", "step01.template_3prod"),
    ("polymetallic_4prod", "step01.template_4prod"),
    ("multinode_rsc", "step01.template_rsc"),
)


class CanvasBlockKind(str, Enum):
    """Semantic role of a visual block on the interactive flowsheet canvas."""

    FEED = "feed"
    PROCESS = "process"
    PRODUCT = "product"

    def __str__(self) -> str:
        return str(self.value)


class CanvasToolMode(str, Enum):
    """Active interaction mode selected in the floating canvas toolbar."""

    SELECT = "select"
    ADD_FEED = "feed"
    ADD_PROCESS = "process"
    ADD_PRODUCT = "product"
    CONNECT = "connect"


def _is_tailings_name(name: str) -> bool:
    low = name.lower()
    return any(tok in low for tok in ("relav", "tail", "cola"))


def _snap_coord(value: float) -> float:
    return round(float(value) / SNAP_STEP) * SNAP_STEP


def _snap_point(pt: QPointF) -> QPointF:
    return QPointF(_snap_coord(pt.x()), _snap_coord(pt.y()))


def _unique_id(preferred: str, existing: set[str]) -> str:
    base = preferred.strip() or "Item"
    if base not in existing:
        return base
    idx = 2
    while f"{base}_{idx}" in existing:
        idx += 1
    return f"{base}_{idx}"


class _InlineTextEditor(QGraphicsTextItem):
    """In-place scene text editor committed on Enter or focus-out without modal popups."""

    def __init__(
        self,
        initial_text: str,
        width: float,
        palette: dict[str, str],
        on_commit,
        on_cancel,
    ) -> None:
        super().__init__(initial_text)
        self._on_commit = on_commit
        self._on_cancel = on_cancel
        self._done = False
        self._palette = palette
        self.setTextWidth(max(80.0, width))
        self.setFont(ui_font(8.8, bold=True))
        self.setDefaultTextColor(QColor(palette["text_primary"]))
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextEditorInteraction)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsFocusable, True)
        self.setZValue(200.0)

        cursor = self.textCursor()
        cursor.select(cursor.SelectionType.Document)
        self.setTextCursor(cursor)

    def paint(self, painter: QPainter, option: QStyleOptionGraphicsItem, widget=None) -> None:
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setBrush(QBrush(QColor(self._palette["panel_bg"])))
        painter.setPen(QPen(QColor(self._palette["accent"]), 1.6))
        painter.drawRoundedRect(self.boundingRect().adjusted(-2, -1, 2, 1), 4.0, 4.0)
        painter.restore()
        super().paint(painter, option, widget)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not (
            event.modifiers() & Qt.KeyboardModifier.ShiftModifier
        ):
            event.accept()
            self._finish(commit=True)
            return
        if event.key() == Qt.Key.Key_Escape:
            event.accept()
            self._finish(commit=False)
            return
        super().keyPressEvent(event)

    def focusOutEvent(self, event) -> None:
        super().focusOutEvent(event)
        self._finish(commit=True)

    def _finish(self, commit: bool) -> None:
        if self._done:
            return
        self._done = True
        text = self.toPlainText().strip()
        self.setVisible(False)
        cb = self._on_commit if commit else self._on_cancel
        cb(text)


class CanvasBlockItem(QGraphicsItem):
    """Excalidraw-inspired rounded engineering block with role accent, badge, and 4 hover ports."""

    def __init__(
        self,
        block_id: str,
        kind: CanvasBlockKind,
        title: str,
        canvas: "FlowsheetCanvas",
        *,
        unit_type: UnitType = UnitType.FLOTATION_CELL,
        is_tailings: bool = False,
        pos: QPointF = QPointF(0.0, 0.0),
        interactive: bool = True,
    ) -> None:
        super().__init__()
        self.block_id = block_id
        self.kind = CanvasBlockKind(kind)
        self.title = title
        self.unit_type = unit_type
        self.is_tailings = is_tailings
        self._canvas = canvas
        self._interactive = interactive
        self._hovered = False
        self._hovered_port: Optional[str] = None
        self._drop_target_highlight = False
        self._editing_active = False

        self._badge_text = ""
        self._badge_role = "pending"
        self._has_outlier = False

        self.setPos(pos)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, interactive)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges, True)
        self.setAcceptHoverEvents(True)
        self.setZValue(10.0)

        self._svg_renderer = self._build_svg_renderer()

    @property
    def box_width(self) -> float:
        return PROCESS_W if self.kind == CanvasBlockKind.PROCESS else TERMINAL_W

    @property
    def box_height(self) -> float:
        return PROCESS_H if self.kind == CanvasBlockKind.PROCESS else TERMINAL_H

    def card_rect(self) -> QRectF:
        w, h = self.box_width, self.box_height
        return QRectF(-w / 2.0, -h / 2.0, w, h)

    def boundingRect(self) -> QRectF:
        return self.card_rect().adjusted(-12.0, -14.0, 12.0, 12.0)

    def shape(self) -> QPainterPath:
        path = QPainterPath()
        path.addRoundedRect(self.card_rect().adjusted(-6.0, -6.0, 6.0, 6.0), 10.0, 10.0)
        return path

    def port_local_pos(self, port_name: str) -> QPointF:
        hw, hh = self.box_width / 2.0, self.box_height / 2.0
        if port_name == "N":
            return QPointF(0.0, -hh)
        if port_name == "S":
            return QPointF(0.0, hh)
        if port_name == "W":
            return QPointF(-hw, 0.0)
        return QPointF(hw, 0.0)

    def port_scene_pos(self, port_name: str) -> QPointF:
        return self.mapToScene(self.port_local_pos(port_name))

    def port_at_scene(self, scene_pos: QPointF) -> Optional[str]:
        local = self.mapFromScene(scene_pos)
        for p_name in ("E", "W", "N", "S"):
            pt = self.port_local_pos(p_name)
            if math.hypot(local.x() - pt.x(), local.y() - pt.y()) <= PORT_HIT_RADIUS:
                return p_name
        return None

    def nearest_port_to_scene(self, scene_pos: QPointF) -> str:
        local = self.mapFromScene(scene_pos)
        best_port = "W"
        best_dist = float("inf")
        for p_name in ("N", "S", "E", "W"):
            pt = self.port_local_pos(p_name)
            d = math.hypot(local.x() - pt.x(), local.y() - pt.y())
            if d < best_dist:
                best_dist = d
                best_port = p_name
        return best_port

    def _role_accent_hex(self, palette: dict[str, str]) -> str:
        if self.kind == CanvasBlockKind.FEED:
            return palette["stream_feed"]
        if self.kind == CanvasBlockKind.PRODUCT:
            if self.is_tailings or _is_tailings_name(self.title):
                return palette["stream_tail"]
            return palette["stream_conc"]
        return palette["accent"]

    def _build_svg_renderer(self) -> QSvgRenderer:
        palette = get_theme_palette(self._canvas.theme)
        if self.kind == CanvasBlockKind.PROCESS:
            svg_name = _UNIT_ICON_MAP.get(self.unit_type, "pid_concentrator.svg")
        elif self.kind == CanvasBlockKind.FEED:
            svg_name = "block_feed.svg"
        else:
            svg_name = "block_product.svg"
        svg_bytes = load_svg_bytes(svg_name, stroke_color=self._role_accent_hex(palette))
        return QSvgRenderer(svg_bytes)

    def refresh_visuals(self) -> None:
        self._svg_renderer = self._build_svg_renderer()
        self.update()

    def set_badge(self, text: str, role: str, has_outlier: bool = False) -> None:
        self._badge_text = text
        self._badge_role = role
        self._has_outlier = has_outlier
        self.update()

    def set_drop_target_highlight(self, highlighted: bool) -> None:
        if self._drop_target_highlight != highlighted:
            self._drop_target_highlight = highlighted
            self.update()

    def set_editing_active(self, active: bool) -> None:
        self._editing_active = active
        self.update()

    def label_scene_rect(self) -> QRectF:
        rect = self.card_rect()
        top_left = self.mapToScene(QPointF(rect.left() + 40.0, rect.top() + 12.0))
        return QRectF(top_left.x(), top_left.y(), self.box_width - 48.0, self.box_height - 20.0)

    def hoverEnterEvent(self, event) -> None:
        self._hovered = True
        self.update()
        super().hoverEnterEvent(event)

    def hoverMoveEvent(self, event) -> None:
        if self._interactive:
            local = event.pos()
            found: Optional[str] = None
            for p_name in ("E", "W", "N", "S"):
                pt = self.port_local_pos(p_name)
                if math.hypot(local.x() - pt.x(), local.y() - pt.y()) <= PORT_HIT_RADIUS:
                    found = p_name
                    break
            if found != self._hovered_port:
                self._hovered_port = found
                self.setCursor(
                    Qt.CursorShape.CrossCursor if found else Qt.CursorShape.ArrowCursor
                )
                self.update()
        super().hoverMoveEvent(event)

    def hoverLeaveEvent(self, event) -> None:
        self._hovered = False
        self._hovered_port = None
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.update()
        super().hoverLeaveEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        if self._interactive and event.button() == Qt.MouseButton.LeftButton:
            self._canvas.start_inline_block_edit(self.block_id)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionGraphicsItem,
        widget: Optional[QWidget] = None,
    ) -> None:
        palette = get_theme_palette(self._canvas.theme)
        is_dark = self._canvas.theme == "dark"
        rect = self.card_rect()
        selected = self.isSelected()
        role_color = QColor(self._role_accent_hex(palette))

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        # Subtle soft shadow directly painted (100% offscreen safe)
        shadow_color = QColor(0, 0, 0, 50 if is_dark else 20)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(shadow_color))
        painter.drawRoundedRect(rect.translated(2.0, 2.5), 10.0, 10.0)

        # Card fill
        if self.kind == CanvasBlockKind.FEED:
            fill_hex = "#1A2732" if is_dark else "#F4F9FC"
        elif self.kind == CanvasBlockKind.PRODUCT:
            if self.is_tailings or _is_tailings_name(self.title):
                fill_hex = "#1E242B" if is_dark else "#F8FAFC"
            else:
                fill_hex = "#172720" if is_dark else "#F3FAF6"
        else:
            fill_hex = palette["panel_bg"]

        # Border
        if self._has_outlier:
            border_color = QColor(palette["error"])
            border_width = 2.2
        elif selected or self._drop_target_highlight:
            border_color = QColor(palette["accent"])
            border_width = 2.2
        else:
            border_color = QColor("#94A3B8" if is_dark else "#1E293B")
            border_width = 1.5

        painter.setPen(QPen(border_color, border_width))
        painter.setBrush(QBrush(QColor(fill_hex)))
        painter.drawRoundedRect(rect, 10.0, 10.0)

        # Role indicator accent bar inside left edge
        stripe_rect = QRectF(rect.left() + 6.0, rect.top() + 9.0, 4.0, rect.height() - 18.0)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(role_color))
        painter.drawRoundedRect(stripe_rect, 2.0, 2.0)

        # ISO P&ID / Role icon
        is_process = self.kind == CanvasBlockKind.PROCESS
        icon_size = 26.0 if is_process else 22.0
        icon_y = rect.top() + (rect.height() - icon_size) / 2.0 + 2.0
        icon_rect = QRectF(
            rect.left() + (13.0 if is_process else 14.0),
            icon_y,
            icon_size,
            icon_size,
        )
        if self._svg_renderer.isValid():
            self._svg_renderer.render(painter, icon_rect)

        # Title and subtitle
        if not self._editing_active:
            text_x = icon_rect.right() + 6.0
            text_w = max(36.0, rect.right() - text_x - 8.0)
            if self.kind == CanvasBlockKind.PROCESS:
                title_rect = QRectF(text_x, rect.top() + 14.0, text_w, 34.0)
                painter.setFont(ui_font(9.0, bold=True))
                painter.setPen(QColor(palette["text_primary"]))
                painter.drawText(
                    title_rect,
                    int(
                        Qt.AlignmentFlag.AlignLeft
                        | Qt.AlignmentFlag.AlignVCenter
                        | Qt.TextFlag.TextWordWrap
                    ),
                    self.title,
                )
                sub_rect = QRectF(text_x, rect.bottom() - 20.0, text_w, 15.0)
                painter.setFont(ui_font(7.5, bold=False))
                painter.setPen(QColor(palette["text_secondary"]))
                sub_txt = self._canvas.i18n.t(f"enums.unit_type.{self.unit_type.value}")
                elided = painter.fontMetrics().elidedText(
                    sub_txt, Qt.TextElideMode.ElideRight, int(text_w)
                )
                painter.drawText(
                    sub_rect,
                    int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                    elided,
                )
            else:
                title_rect = QRectF(text_x, rect.top() + 8.0, text_w, rect.height() - 14.0)
                painter.setFont(ui_font(8.8, bold=True))
                painter.setPen(QColor(palette["text_primary"]))
                painter.drawText(
                    title_rect,
                    int(
                        Qt.AlignmentFlag.AlignLeft
                        | Qt.AlignmentFlag.AlignVCenter
                        | Qt.TextFlag.TextWordWrap
                    ),
                    self.title,
                )

        # Top-right corner status pill
        self._paint_status_badge(painter, rect, palette)

        # 4 Hover anchor ports (N, S, E, W)
        show_ports = self._interactive and (
            self._hovered
            or selected
            or self._drop_target_highlight
            or self._canvas.tool_mode == CanvasToolMode.CONNECT
            or self._canvas.is_dragging_port
        )
        if show_ports:
            for p_name in ("N", "S", "E", "W"):
                pt = self.port_local_pos(p_name)
                is_hot = self._hovered_port == p_name
                rad = PORT_RADIUS + (1.2 if is_hot else 0.0)
                painter.setPen(QPen(QColor(palette["accent"]), 1.5))
                painter.setBrush(
                    QBrush(QColor(palette["accent"] if is_hot else palette["panel_bg"]))
                )
                painter.drawEllipse(pt, rad, rad)

        painter.restore()

    def _paint_status_badge(
        self,
        painter: QPainter,
        rect: QRectF,
        palette: dict[str, str],
    ) -> None:
        role = self._badge_role
        if role == "error":
            bg_hex, fg_hex, border_hex = palette["error"], "#FFFFFF", palette["error"]
        elif role == "validated":
            bg_hex, fg_hex, border_hex = palette["validated_bg"], palette["validated"], palette["validated"]
        elif role == "reconciled":
            bg_hex, fg_hex, border_hex = palette["accent"], "#FFFFFF", palette["accent"]
        elif role == "warning":
            bg_hex, fg_hex, border_hex = palette["warning_bg"], palette["warning"], palette["warning"]
        else:
            bg_hex, fg_hex, border_hex = palette["pending_bg"], palette["pending"], palette["border"]

        if self._badge_text:
            painter.setFont(monospace_font(7.0, bold=True))
            fm = painter.fontMetrics()
            txt_w = fm.horizontalAdvance(self._badge_text)
            pill_w = min(84.0, max(26.0, txt_w + 10.0))
            pill_h = 15.0
            pill_rect = QRectF(rect.right() - pill_w - 4.0, rect.top() - 7.0, pill_w, pill_h)
            painter.setPen(QPen(QColor(border_hex), 1.0))
            painter.setBrush(QBrush(QColor(bg_hex)))
            painter.drawRoundedRect(pill_rect, 7.5, 7.5)
            painter.setPen(QColor(fg_hex))
            elided = fm.elidedText(self._badge_text, Qt.TextElideMode.ElideRight, int(pill_w - 6))
            painter.drawText(pill_rect, int(Qt.AlignmentFlag.AlignCenter), elided)
        else:
            dot_rect = QRectF(rect.right() - 14.0, rect.top() + 7.0, 7.0, 7.0)
            painter.setPen(QPen(QColor(border_hex), 1.0))
            painter.setBrush(QBrush(QColor(bg_hex if role != "pending" else border_hex)))
            painter.drawEllipse(dot_rect)

    def itemChange(self, change: QGraphicsItem.GraphicsItemChange, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionChange and self._canvas.is_user_dragging_block:
            return _snap_point(value)
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            self._canvas.on_block_item_moved(self.block_id)
        return super().itemChange(change, value)


class CanvasStreamItem(QGraphicsPathItem):
    """Selectable directed stream arrow with smooth orthogonal routing and inline-editable tag."""

    def __init__(
        self,
        stream_id: str,
        stream_name: str,
        source_block_id: str,
        target_block_id: str,
        canvas: "FlowsheetCanvas",
        *,
        source_port: Optional[str] = None,
        target_port: Optional[str] = None,
    ) -> None:
        super().__init__()
        self.stream_id = stream_id
        self.stream_name = stream_name
        self.source_block_id = source_block_id
        self.target_block_id = target_block_id
        self.source_port = source_port
        self.target_port = target_port
        self._canvas = canvas
        self._label_anchor = QPointF(0.0, 0.0)
        self._editing_active = False
        self._is_outlier = False

        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setAcceptHoverEvents(True)
        self.setZValue(3.0)

        self._arrow_item = QGraphicsPolygonItem(self)
        self._arrow_item.setZValue(4.0)
        self._tag_bg = QGraphicsRectItem(self)
        self._tag_bg.setZValue(5.0)
        self._tag_text = QGraphicsSimpleTextItem(self._tag_bg)
        self._tag_text.setZValue(6.0)

    def shape(self) -> QPainterPath:
        stroker = QPainterPathStroker()
        stroker.setWidth(max(14.0, self.pen().widthF() + 10.0))
        base_shape = stroker.createStroke(self.path())
        base_shape.addRect(self._tag_bg.rect())
        return base_shape

    def label_scene_rect(self) -> QRectF:
        r = self._tag_bg.rect()
        return QRectF(r.x() - 12.0, r.y() - 2.0, max(100.0, r.width() + 24.0), max(24.0, r.height() + 4.0))

    def set_editing_active(self, active: bool) -> None:
        self._editing_active = active
        self._tag_text.setVisible(not active)

    def mouseDoubleClickEvent(self, event) -> None:
        if self._canvas.interactive and event.button() == Qt.MouseButton.LeftButton:
            self._canvas.start_inline_stream_edit(self.stream_id)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def update_geometry(
        self,
        start_pt: QPointF,
        end_pt: QPointF,
        detour_y: Optional[float],
        stroke_width: float,
        color_hex: str,
        label_text: str,
        *,
        pen_style: Qt.PenStyle = Qt.PenStyle.SolidLine,
        is_outlier: bool = False,
        mono_label: bool = False,
        vertical_mode: bool = False,
    ) -> None:
        palette = get_theme_palette(self._canvas.theme)
        selected = self.isSelected()
        self._is_outlier = is_outlier

        if is_outlier:
            line_color = QColor(palette["error"])
        elif selected:
            line_color = QColor(palette["accent"])
        else:
            line_color = QColor(color_hex)

        pen = QPen(line_color, stroke_width + (1.2 if selected else 0.0), pen_style)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        self.setPen(pen)

        path = QPainterPath(start_pt)
        if detour_y is not None:
            step_x = 26.0
            p1 = QPointF(start_pt.x() + step_x, start_pt.y())
            p2 = QPointF(start_pt.x() + step_x, detour_y)
            p3 = QPointF(end_pt.x() - step_x, detour_y)
            p4 = QPointF(end_pt.x() - step_x, end_pt.y())
            path.lineTo(p1)
            path.lineTo(p2)
            path.lineTo(p3)
            path.lineTo(p4)
            path.lineTo(end_pt)
            label_anchor = QPointF((p2.x() + p3.x()) / 2.0, detour_y)
            arrow_prev = p4
        elif vertical_mode:
            if abs(start_pt.x() - end_pt.x()) > 2.0:
                mid_y = (start_pt.y() + end_pt.y()) / 2.0
                p1 = QPointF(start_pt.x(), mid_y)
                p2 = QPointF(end_pt.x(), mid_y)
                path.lineTo(p1)
                path.lineTo(p2)
                path.lineTo(end_pt)
                label_anchor = QPointF((start_pt.x() + end_pt.x()) / 2.0, mid_y)
                arrow_prev = p2
            else:
                path.lineTo(end_pt)
                label_anchor = QPointF(
                    (start_pt.x() + end_pt.x()) / 2.0,
                    (start_pt.y() + end_pt.y()) / 2.0,
                )
                arrow_prev = start_pt
        elif abs(start_pt.y() - end_pt.y()) > 2.0:
            mid_x = (start_pt.x() + end_pt.x()) / 2.0
            p1 = QPointF(mid_x, start_pt.y())
            p2 = QPointF(mid_x, end_pt.y())
            path.lineTo(p1)
            path.lineTo(p2)
            path.lineTo(end_pt)
            label_anchor = QPointF(mid_x, (start_pt.y() + end_pt.y()) / 2.0)
            arrow_prev = p2
        else:
            path.lineTo(end_pt)
            label_anchor = QPointF(
                (start_pt.x() + end_pt.x()) / 2.0,
                (start_pt.y() + end_pt.y()) / 2.0,
            )
            arrow_prev = start_pt

        self._label_anchor = label_anchor
        self.setPath(path)

        # Arrowhead pointing into end_pt
        dx = end_pt.x() - arrow_prev.x()
        dy = end_pt.y() - arrow_prev.y()
        length = math.hypot(dx, dy) or 1.0
        ux, uy = dx / length, dy / length
        arrow_len = max(9.0, 6.0 + stroke_width * 0.85)
        arrow_half_w = arrow_len * 0.48
        base = QPointF(end_pt.x() - ux * arrow_len, end_pt.y() - uy * arrow_len)
        perp = QPointF(-uy * arrow_half_w, ux * arrow_half_w)
        poly = QPolygonF(
            [
                end_pt,
                QPointF(base.x() + perp.x(), base.y() + perp.y()),
                QPointF(base.x() - perp.x(), base.y() - perp.y()),
            ]
        )
        self._arrow_item.setPolygon(poly)
        self._arrow_item.setBrush(QBrush(line_color))
        self._arrow_item.setPen(QPen(line_color, 1.0))

        # Stream tag card
        self._tag_text.setFont(
            monospace_font(7.8, bold=False) if mono_label else ui_font(8.0, bold=True)
        )
        self._tag_text.setText(label_text)
        self._tag_text.setBrush(
            QBrush(QColor(palette["error"] if is_outlier else palette["text_primary"]))
        )
        t_rect = self._tag_text.boundingRect()
        pad_x, pad_y = 6.0, 2.5
        bg_w = t_rect.width() + 2 * pad_x
        bg_h = t_rect.height() + 2 * pad_y
        bg_x = label_anchor.x() - bg_w / 2.0
        bg_y = label_anchor.y() - bg_h / 2.0
        self._tag_bg.setRect(bg_x, bg_y, bg_w, bg_h)
        bg_fill = palette["error_bg"] if is_outlier else palette["panel_bg"]
        border_col = (
            palette["error"]
            if is_outlier
            else (palette["accent"] if selected else palette["border"])
        )
        self._tag_bg.setBrush(QBrush(QColor(bg_fill)))
        self._tag_bg.setPen(QPen(QColor(border_col), 1.4 if (is_outlier or selected) else 1.0))
        self._tag_text.setPos(bg_x + pad_x, bg_y + pad_y)


# --- QUndoCommands ---


class _AddBlockCommand(QUndoCommand):
    def __init__(self, canvas: "FlowsheetCanvas", spec: dict, text: str) -> None:
        super().__init__(text)
        self._canvas = canvas
        self._spec = dict(spec)

    def redo(self) -> None:
        self._canvas._internal_create_block(self._spec)

    def undo(self) -> None:
        self._canvas._internal_delete_block(self._spec["block_id"])


class _RemoveBlockCommand(QUndoCommand):
    def __init__(
        self,
        canvas: "FlowsheetCanvas",
        block_spec: dict,
        attached_streams: list[dict],
        text: str,
    ) -> None:
        super().__init__(text)
        self._canvas = canvas
        self._block_spec = dict(block_spec)
        self._streams = [dict(s) for s in attached_streams]

    def redo(self) -> None:
        for s in self._streams:
            self._canvas._internal_delete_stream(s["stream_id"])
        self._canvas._internal_delete_block(self._block_spec["block_id"])

    def undo(self) -> None:
        self._canvas._internal_create_block(self._block_spec)
        for s in self._streams:
            self._canvas._internal_create_stream(s)


class _MoveBlocksCommand(QUndoCommand):
    def __init__(
        self,
        canvas: "FlowsheetCanvas",
        moves: list[tuple[str, QPointF, QPointF]],
        text: str,
    ) -> None:
        super().__init__(text)
        self._canvas = canvas
        self._moves = moves

    def redo(self) -> None:
        for block_id, _old_pos, new_pos in self._moves:
            self._canvas._internal_set_block_pos(block_id, new_pos)

    def undo(self) -> None:
        for block_id, old_pos, _new_pos in self._moves:
            self._canvas._internal_set_block_pos(block_id, old_pos)


class _RenameBlockCommand(QUndoCommand):
    def __init__(
        self,
        canvas: "FlowsheetCanvas",
        block_id: str,
        old_name: str,
        new_name: str,
        linked_stream_id: Optional[str],
        old_stream_name: Optional[str],
        new_stream_name: Optional[str],
        text: str,
    ) -> None:
        super().__init__(text)
        self._canvas = canvas
        self._block_id = block_id
        self._old_name = old_name
        self._new_name = new_name
        self._linked_stream_id = linked_stream_id
        self._old_stream_name = old_stream_name
        self._new_stream_name = new_stream_name

    def redo(self) -> None:
        self._canvas._internal_apply_block_name(
            self._block_id, self._new_name, self._linked_stream_id, self._new_stream_name
        )

    def undo(self) -> None:
        self._canvas._internal_apply_block_name(
            self._block_id, self._old_name, self._linked_stream_id, self._old_stream_name
        )


class _ConnectStreamCommand(QUndoCommand):
    def __init__(self, canvas: "FlowsheetCanvas", stream_spec: dict, text: str) -> None:
        super().__init__(text)
        self._canvas = canvas
        self._spec = dict(stream_spec)

    def redo(self) -> None:
        self._canvas._internal_create_stream(self._spec)

    def undo(self) -> None:
        self._canvas._internal_delete_stream(self._spec["stream_id"])


class _RemoveStreamCommand(QUndoCommand):
    def __init__(self, canvas: "FlowsheetCanvas", stream_spec: dict, text: str) -> None:
        super().__init__(text)
        self._canvas = canvas
        self._spec = dict(stream_spec)

    def redo(self) -> None:
        self._canvas._internal_delete_stream(self._spec["stream_id"])

    def undo(self) -> None:
        self._canvas._internal_create_stream(self._spec)


class _RenameStreamCommand(QUndoCommand):
    def __init__(
        self,
        canvas: "FlowsheetCanvas",
        stream_id: str,
        old_name: str,
        new_name: str,
        terminal_block_id: Optional[str],
        old_block_name: Optional[str],
        new_block_name: Optional[str],
        text: str,
    ) -> None:
        super().__init__(text)
        self._canvas = canvas
        self._stream_id = stream_id
        self._old_name = old_name
        self._new_name = new_name
        self._terminal_block_id = terminal_block_id
        self._old_block_name = old_block_name
        self._new_block_name = new_block_name

    def redo(self) -> None:
        self._canvas._internal_apply_stream_name(
            self._stream_id, self._new_name, self._terminal_block_id, self._new_block_name
        )

    def undo(self) -> None:
        self._canvas._internal_apply_stream_name(
            self._stream_id, self._old_name, self._terminal_block_id, self._old_block_name
        )


class _CanvasFloatingToolbar(QFrame):
    """Centered Excalidraw-style floating action bar at top of interactive FlowsheetCanvas."""

    def __init__(self, canvas: "FlowsheetCanvas") -> None:
        super().__init__(canvas.viewport())
        self.setObjectName("CanvasFloatingToolBar")
        self._canvas = canvas
        self._i18n = get_i18n()

        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(3)

        self.mode_group = QButtonGroup(self)
        self.mode_group.setExclusive(True)

        self.btn_select = self._make_btn(checkable=True, checked=True)
        self.btn_feed = self._make_btn(checkable=True)
        self.btn_process = self._make_btn(checkable=True)
        self.btn_product = self._make_btn(checkable=True)
        self.btn_connect = self._make_btn(checkable=True)

        self.mode_group.addButton(self.btn_select, 0)
        self.mode_group.addButton(self.btn_feed, 1)
        self.mode_group.addButton(self.btn_process, 2)
        self.mode_group.addButton(self.btn_product, 3)
        self.mode_group.addButton(self.btn_connect, 4)

        for btn in (
            self.btn_select,
            self.btn_feed,
            self.btn_process,
            self.btn_product,
            self.btn_connect,
        ):
            layout.addWidget(btn)

        layout.addWidget(self._make_separator())

        self.btn_templates = self._make_btn(checkable=False)
        self.btn_templates.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.btn_templates.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.templates_menu = QMenu(self.btn_templates)
        self._template_actions: dict[str, QAction] = {}
        for tpl_id, _key in _TEMPLATE_MENU_ITEMS:
            act = QAction(self.templates_menu)
            act.setData(tpl_id)
            act.triggered.connect(lambda _chk=False, tid=tpl_id: self._canvas._on_template_action(tid))
            self.templates_menu.addAction(act)
            self._template_actions[tpl_id] = act
        self.btn_templates.setMenu(self.templates_menu)
        layout.addWidget(self.btn_templates)

        layout.addWidget(self._make_separator())

        self.btn_undo = self._make_btn(checkable=False)
        self.btn_redo = self._make_btn(checkable=False)
        self.btn_fit = self._make_btn(checkable=False)
        self.btn_undo.setEnabled(False)
        self.btn_redo.setEnabled(False)

        layout.addWidget(self.btn_undo)
        layout.addWidget(self.btn_redo)
        layout.addWidget(self.btn_fit)

        self.btn_select.clicked.connect(lambda: self._canvas.set_tool_mode(CanvasToolMode.SELECT))
        self.btn_feed.clicked.connect(lambda: self._canvas.set_tool_mode(CanvasToolMode.ADD_FEED))
        self.btn_process.clicked.connect(lambda: self._canvas.set_tool_mode(CanvasToolMode.ADD_PROCESS))
        self.btn_product.clicked.connect(lambda: self._canvas.set_tool_mode(CanvasToolMode.ADD_PRODUCT))
        self.btn_connect.clicked.connect(lambda: self._canvas.set_tool_mode(CanvasToolMode.CONNECT))

        self.btn_undo.clicked.connect(self._canvas.undo_stack.undo)
        self.btn_redo.clicked.connect(self._canvas.undo_stack.redo)
        self.btn_fit.clicked.connect(self._canvas.zoom_fit)

        self._canvas.undo_stack.canUndoChanged.connect(self.btn_undo.setEnabled)
        self._canvas.undo_stack.canRedoChanged.connect(self.btn_redo.setEnabled)

        self.refresh_icons("light")
        self.retranslate_ui()

    def _make_btn(self, *, checkable: bool, checked: bool = False) -> QToolButton:
        btn = QToolButton(self)
        btn.setProperty("canvasTool", True)
        btn.setIconSize(QSize(18, 18))
        btn.setCheckable(checkable)
        if checkable:
            btn.setChecked(checked)
        btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        return btn

    def _make_separator(self) -> QFrame:
        sep = QFrame(self)
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setFrameShadow(QFrame.Shadow.Sunken)
        sep.setFixedWidth(2)
        return sep

    def sync_mode(self, mode: CanvasToolMode) -> None:
        mapping = {
            CanvasToolMode.SELECT: self.btn_select,
            CanvasToolMode.ADD_FEED: self.btn_feed,
            CanvasToolMode.ADD_PROCESS: self.btn_process,
            CanvasToolMode.ADD_PRODUCT: self.btn_product,
            CanvasToolMode.CONNECT: self.btn_connect,
        }
        btn = mapping.get(mode, self.btn_select)
        btn.setChecked(True)

    def refresh_icons(self, theme: str) -> None:
        self.btn_select.setIcon(load_svg_icon("cursor_select.svg", theme, size=18))
        self.btn_feed.setIcon(load_svg_icon("block_feed.svg", theme, size=18))
        self.btn_process.setIcon(load_svg_icon("block_process.svg", theme, size=18))
        self.btn_product.setIcon(load_svg_icon("block_product.svg", theme, size=18))
        self.btn_connect.setIcon(load_svg_icon("link_stream.svg", theme, size=18))
        self.btn_templates.setIcon(load_svg_icon("matrix.svg", theme, size=18))
        self.btn_undo.setIcon(load_svg_icon("undo.svg", theme, size=18))
        self.btn_redo.setIcon(load_svg_icon("redo.svg", theme, size=18))
        self.btn_fit.setIcon(load_svg_icon("reset_layout.svg", theme, size=18))

    def retranslate_ui(self) -> None:
        t = self._i18n.t
        self.btn_select.setToolTip(t("canvas.tool_select"))
        self.btn_feed.setToolTip(t("canvas.tool_feed"))
        self.btn_process.setToolTip(t("canvas.tool_process"))
        self.btn_product.setToolTip(t("canvas.tool_product"))
        self.btn_connect.setToolTip(t("canvas.tool_connect"))
        self.btn_templates.setText(t("canvas.tool_templates"))
        self.btn_templates.setToolTip(t("canvas.tool_templates"))
        self.btn_undo.setToolTip(t("canvas.tool_undo"))
        self.btn_redo.setToolTip(t("canvas.tool_redo"))
        self.btn_fit.setToolTip(t("canvas.tool_fit"))
        for tpl_id, key in _TEMPLATE_MENU_ITEMS:
            self._template_actions[tpl_id].setText(t(key))
        self.adjustSize()


class FlowsheetCanvas(QGraphicsView):
    """Excalidraw-style CAD QGraphicsView flowsheet editor and proportional Sankey viewer."""

    circuit_changed = Signal(object)
    block_selected = Signal(str, str)  # (block_id, CanvasBlockKind.value)
    node_selected = Signal(str)
    stream_selected = Signal(str)
    selection_cleared = Signal()
    node_moved = Signal(str, float, float)
    template_requested = Signal(str)
    connect_stream_picked = Signal(object, object)  # (from_node_id or None, to_node_id or None)

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        interactive: bool = True,
    ) -> None:
        super().__init__(parent)
        self._interactive = interactive
        self._sankey_mode = not interactive
        self._theme = "light"
        self._i18n = get_i18n()
        self._tool_mode = CanvasToolMode.SELECT

        self.undo_stack = QUndoStack(self)

        self._circuit: Optional[CircuitSpec] = None
        self._cleaned_dataset: Optional[CleanedDataset] = None
        self._observability: Optional[ObservabilityReport] = None
        self._reconciliation: Optional[ReconciliationResult] = None
        self._active_step_idx: int = 0
        self._active_period_idx: int = 0

        self._block_items: dict[str, CanvasBlockItem] = {}
        self._stream_items: dict[str, CanvasStreamItem] = {}
        self._terminal_positions: dict[str, QPointF] = {}

        # Interaction states
        self._space_held = False
        self._panning = False
        self._pan_start = QPoint()
        self._is_user_dragging_block = False
        self._drag_start_positions: dict[str, QPointF] = {}

        self._port_drag_src: Optional[str] = None
        self._port_drag_src_port: Optional[str] = None
        self._connect_source_block: Optional[str] = None
        self._drag_preview_path: Optional[QGraphicsPathItem] = None
        self._drag_preview_arrow: Optional[QGraphicsPolygonItem] = None
        self._highlighted_drop_block: Optional[str] = None
        self._editing_item: Optional[_InlineTextEditor] = None
        self._pending_zoom_fit = False

        self._scene = QGraphicsScene(self)
        self._scene.setSceneRect(-1200, -900, 3600, 2400)
        self.setScene(self._scene)
        self.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.FullViewportUpdate)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setDragMode(
            QGraphicsView.DragMode.RubberBandDrag
            if interactive
            else QGraphicsView.DragMode.ScrollHandDrag
        )

        self._toolbar = _CanvasFloatingToolbar(self)
        self._toolbar.setVisible(interactive)

        self._scene.selectionChanged.connect(self._on_selection_changed)
        self._reposition_toolbar()

    @property
    def theme(self) -> str:
        return self._theme

    @property
    def interactive(self) -> bool:
        return self._interactive

    @property
    def i18n(self):
        return self._i18n

    @property
    def tool_mode(self) -> CanvasToolMode:
        return self._tool_mode

    @property
    def is_dragging_port(self) -> bool:
        return self._port_drag_src is not None

    @property
    def is_user_dragging_block(self) -> bool:
        return self._is_user_dragging_block

    # --- Public Configuration & Compatibility API ---

    def set_theme(self, theme: str) -> None:
        """Apply light/dark CAD colors to grid, blocks, streams, badges, and floating toolbar."""
        self._theme = "dark" if theme == "dark" else "light"
        self._toolbar.refresh_icons(self._theme)
        for blk in self._block_items.values():
            blk.refresh_visuals()
        self._refresh_all_overlays()
        self.viewport().update()

    def set_sankey_mode(self, enabled: bool) -> None:
        """Switch between interactive topology editing and proportional Sankey display."""
        self._sankey_mode = enabled
        self._interactive = not enabled
        self._toolbar.setVisible(self._interactive)
        self.setDragMode(
            QGraphicsView.DragMode.RubberBandDrag
            if self._interactive
            else QGraphicsView.DragMode.ScrollHandDrag
        )
        for item in self._block_items.values():
            item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, self._interactive)
        self._refresh_all_overlays()

    def set_tool_mode(self, mode: CanvasToolMode) -> None:
        """Switch active Excalidraw toolbar mode."""
        self._tool_mode = mode
        self._toolbar.sync_mode(mode)
        self._cancel_port_or_connect_drag()
        if mode == CanvasToolMode.SELECT:
            self.setDragMode(
                QGraphicsView.DragMode.RubberBandDrag
                if self._interactive
                else QGraphicsView.DragMode.ScrollHandDrag
            )
            self.setCursor(Qt.CursorShape.ArrowCursor)
        else:
            self.setDragMode(QGraphicsView.DragMode.NoDrag)
            self.setCursor(Qt.CursorShape.CrossCursor)
        for blk in self._block_items.values():
            blk.update()

    def set_connect_mode(self, enabled: bool) -> None:
        """Backwards-compatible alias for activating/deactivating stream connect mode."""
        self.set_tool_mode(CanvasToolMode.CONNECT if enabled else CanvasToolMode.SELECT)

    def retranslate_ui(self) -> None:
        """Refresh floating toolbar labels, template menu, and stream/badge texts."""
        self._toolbar.retranslate_ui()
        self._reposition_toolbar()
        for blk in self._block_items.values():
            blk.update()
        self._refresh_all_overlays()

    def set_circuit_ref(self, circuit: CircuitSpec) -> None:
        """Update CircuitSpec metadata/species/units without resetting canvas blocks."""
        self._circuit = circuit
        for node in circuit.nodes:
            if node.id in self._block_items:
                blk = self._block_items[node.id]
                blk.title = node.name
                blk.unit_type = node.unit_type
                blk.refresh_visuals()
        for stream in circuit.streams:
            if stream.id in self._stream_items:
                s_item = self._stream_items[stream.id]
                s_item.stream_name = stream.name
                term_id = self._terminal_for_stream(stream.id)
                if term_id and term_id in self._block_items:
                    t_blk = self._block_items[term_id]
                    t_blk.title = stream.name
                    t_blk.is_tailings = stream.role == StreamRole.TAILINGS
                    t_blk.refresh_visuals()
        self._refresh_all_overlays()

    def set_overlay_state(
        self,
        cleaned_dataset: Optional[CleanedDataset] = None,
        observability_report: Optional[ObservabilityReport] = None,
        reconciliation_result: Optional[ReconciliationResult] = None,
        active_step_idx: int = 0,
        active_period_idx: int = 0,
    ) -> None:
        """Update live data-readiness, observability, and GED/WLS visual overlays."""
        self._cleaned_dataset = cleaned_dataset
        self._observability = observability_report
        self._reconciliation = reconciliation_result
        self._active_step_idx = active_step_idx
        self._active_period_idx = active_period_idx
        self._refresh_all_overlays()

    def clear_circuit(self, keep_species: bool = True) -> None:
        """Clear all blocks and streams on canvas so a fresh circuit can be drawn."""
        self._finish_inline_edit(commit=False)
        self._cancel_port_or_connect_drag()
        self._scene.blockSignals(True)
        try:
            self._scene.clear()
            self._block_items.clear()
            self._stream_items.clear()
            self._terminal_positions.clear()
            self.undo_stack.clear()
        finally:
            self._scene.blockSignals(False)

        base = self._circuit
        species = (
            [sp.model_copy(deep=True) for sp in base.species]
            if (keep_species and base is not None and base.species)
            else [SpeciesSpec(id="Cu", name="Copper", unit=GradeUnit.PERCENT)]
        )
        self._circuit = CircuitSpec(
            name=base.name if base is not None else self._i18n.t("app.title"),
            description=base.description if base is not None else "",
            mass_unit=base.mass_unit if base is not None else MassUnit.T_H,
            nodes=[],
            streams=[],
            species=species,
        )
        self._cleaned_dataset = None
        self._observability = None
        self._reconciliation = None
        self.circuit_changed.emit(self.sync_circuit_from_canvas())

    def load_circuit(
        self,
        circuit: CircuitSpec,
        reconciliation: Optional[ReconciliationResult] = None,
    ) -> None:
        """Rebuild Excalidraw blocks and arrows from CircuitSpec (synthesizing terminal blocks)."""
        self._finish_inline_edit(commit=False)
        self._cancel_port_or_connect_drag()
        self._circuit = circuit
        if reconciliation is not None or not self._interactive:
            self._reconciliation = reconciliation

        self._scene.blockSignals(True)
        try:
            self._terminal_positions.clear()
            self._scene.clear()
            self._block_items.clear()
            self._stream_items.clear()
            self.undo_stack.clear()

            # 1. Create PROCESS blocks from CircuitSpec.nodes
            for node in circuit.nodes:
                blk = CanvasBlockItem(
                    block_id=node.id,
                    kind=CanvasBlockKind.PROCESS,
                    title=node.name,
                    canvas=self,
                    unit_type=node.unit_type,
                    pos=QPointF(node.pos_x, node.pos_y),
                    interactive=self._interactive,
                )
                self._scene.addItem(blk)
                self._block_items[node.id] = blk

            # 2. Group external streams to spread terminal blocks cleanly left/right
            in_ext: dict[str, list[StreamSpec]] = defaultdict(list)
            out_ext: dict[str, list[StreamSpec]] = defaultdict(list)
            for stream in circuit.streams:
                if stream.from_node is None and stream.to_node is not None:
                    in_ext[stream.to_node].append(stream)
                elif stream.from_node is not None and stream.to_node is None:
                    out_ext[stream.from_node].append(stream)

            # 3. Synthesize FEED terminal blocks on the left & connect arrows
            for to_nid, streams in in_ext.items():
                proc_blk = self._block_items.get(to_nid)
                base_pos = proc_blk.pos() if proc_blk else QPointF(260.0, 200.0)
                n_in = len(streams)
                for idx, stream in enumerate(streams):
                    term_id = f"TERM_IN__{stream.id}"
                    dy = (idx - (n_in - 1) / 2.0) * 92.0
                    pos = self._terminal_positions.get(
                        stream.id, QPointF(base_pos.x() - 265.0, base_pos.y() + dy)
                    )
                    feed_blk = CanvasBlockItem(
                        block_id=term_id,
                        kind=CanvasBlockKind.FEED,
                        title=stream.name,
                        canvas=self,
                        pos=pos,
                        interactive=self._interactive,
                    )
                    self._scene.addItem(feed_blk)
                    self._block_items[term_id] = feed_blk
                    self._terminal_positions[stream.id] = pos

                    s_item = CanvasStreamItem(
                        stream.id, stream.name, term_id, to_nid, self
                    )
                    self._scene.addItem(s_item)
                    self._stream_items[stream.id] = s_item

            # 4. Synthesize PRODUCT terminal blocks on the right & connect arrows
            for from_nid, streams in out_ext.items():
                proc_blk = self._block_items.get(from_nid)
                base_pos = proc_blk.pos() if proc_blk else QPointF(260.0, 200.0)
                n_out = len(streams)
                for idx, stream in enumerate(streams):
                    term_id = f"TERM_OUT__{stream.id}"
                    dy = (idx - (n_out - 1) / 2.0) * 90.0
                    pos = self._terminal_positions.get(
                        stream.id, QPointF(base_pos.x() + 280.0, base_pos.y() + dy)
                    )
                    is_tail = stream.role == StreamRole.TAILINGS
                    prod_blk = CanvasBlockItem(
                        block_id=term_id,
                        kind=CanvasBlockKind.PRODUCT,
                        title=stream.name,
                        canvas=self,
                        is_tailings=is_tail,
                        pos=pos,
                        interactive=self._interactive,
                    )
                    self._scene.addItem(prod_blk)
                    self._block_items[term_id] = prod_blk
                    self._terminal_positions[stream.id] = pos

                    s_item = CanvasStreamItem(
                        stream.id, stream.name, from_nid, term_id, self
                    )
                    self._scene.addItem(s_item)
                    self._stream_items[stream.id] = s_item

            # 5. Internal PROCESS -> PROCESS arrows
            for stream in circuit.streams:
                if stream.from_node is not None and stream.to_node is not None:
                    if stream.from_node in self._block_items and stream.to_node in self._block_items:
                        s_item = CanvasStreamItem(
                            stream.id, stream.name, stream.from_node, stream.to_node, self
                        )
                        self._scene.addItem(s_item)
                        self._stream_items[stream.id] = s_item
        finally:
            self._scene.blockSignals(False)

        self._refresh_all_overlays()
        self.zoom_fit()

    def set_reconciliation_result(
        self,
        circuit: CircuitSpec,
        result: Optional[ReconciliationResult],
    ) -> None:
        """Render Sankey proportional stream widths and reconciled assays."""
        self._sankey_mode = result is not None
        self._reconciliation = result
        self.load_circuit(circuit, reconciliation=result)
        self.zoom_fit()

    # --- Programmatic Canvas Authoring API (with QUndoStack support) ---

    def add_block(
        self,
        kind: CanvasBlockKind | str,
        name: Optional[str] = None,
        pos: Optional[QPointF | tuple[float, float]] = None,
        *,
        block_id: Optional[str] = None,
        unit_type: UnitType = UnitType.FLOTATION_CELL,
        is_tailings: bool = False,
        use_undo: bool = True,
    ) -> str:
        """Add a FEED, PROCESS, or PRODUCT block to the canvas and sync CircuitSpec."""
        self._ensure_circuit_exists()
        resolved_kind = CanvasBlockKind(kind)
        resolved_pos = self._resolve_new_block_pos(resolved_kind, pos)

        if name and name.strip():
            resolved_name = name.strip()
        elif resolved_kind == CanvasBlockKind.FEED:
            resolved_name = self._i18n.t("canvas.default_feed_name")
        elif resolved_kind == CanvasBlockKind.PRODUCT:
            resolved_name = self._i18n.t(
                "canvas.default_tailings_name" if is_tailings else "canvas.default_product_name"
            )
        else:
            resolved_name = self._i18n.t("canvas.default_process_name")

        existing_ids = set(self._block_items.keys())
        if block_id and block_id.strip():
            resolved_id = _unique_id(block_id.strip(), existing_ids)
        elif resolved_kind == CanvasBlockKind.PROCESS:
            resolved_id = _unique_id(resolved_name, existing_ids)
        else:
            prefix = "FEED" if resolved_kind == CanvasBlockKind.FEED else "PROD"
            idx = sum(1 for b in self._block_items.values() if b.kind == resolved_kind) + 1
            resolved_id = _unique_id(f"TERM_{prefix}_{idx}", existing_ids)

        spec = {
            "block_id": resolved_id,
            "kind": resolved_kind,
            "title": resolved_name,
            "unit_type": unit_type,
            "is_tailings": bool(is_tailings or (resolved_kind == CanvasBlockKind.PRODUCT and _is_tailings_name(resolved_name))),
            "pos": QPointF(resolved_pos),
        }
        if use_undo:
            self.undo_stack.push(
                _AddBlockCommand(self, spec, self._i18n.t("canvas.cmd_add_block"))
            )
        else:
            self._internal_create_block(spec)
        return resolved_id

    def connect_blocks(
        self,
        source_block_id: str,
        target_block_id: str,
        *,
        stream_id: Optional[str] = None,
        stream_name: Optional[str] = None,
        source_port: Optional[str] = None,
        target_port: Optional[str] = None,
        use_undo: bool = True,
    ) -> str:
        """Connect two blocks with a directed StreamSpec arrow."""
        self._ensure_circuit_exists()
        src = self._block_items.get(source_block_id)
        dst = self._block_items.get(target_block_id)
        if src is None or dst is None:
            raise KeyError("Source or target block ID not found on canvas.")
        if not self._is_valid_connection(source_block_id, target_block_id):
            raise ValueError(
                f"Invalid flow connection from {src.kind.value} '{source_block_id}' "
                f"to {dst.kind.value} '{target_block_id}'."
            )

        existing_stream_ids = set(self._stream_items.keys())
        if stream_name and stream_name.strip():
            resolved_name = stream_name.strip()
        elif src.kind == CanvasBlockKind.FEED:
            resolved_name = src.title
        elif dst.kind == CanvasBlockKind.PRODUCT:
            resolved_name = dst.title
        else:
            idx = len(self._stream_items) + 1
            while f"S-{idx:02d}" in existing_stream_ids:
                idx += 1
            resolved_name = f"S-{idx:02d}"

        if stream_id and stream_id.strip():
            resolved_sid = _unique_id(stream_id.strip(), existing_stream_ids)
        else:
            resolved_sid = _unique_id(resolved_name, existing_stream_ids)

        spec = {
            "stream_id": resolved_sid,
            "stream_name": resolved_name,
            "source_block_id": source_block_id,
            "target_block_id": target_block_id,
            "source_port": source_port,
            "target_port": target_port,
        }
        if use_undo:
            self.undo_stack.push(
                _ConnectStreamCommand(self, spec, self._i18n.t("canvas.cmd_connect_stream"))
            )
        else:
            self._internal_create_stream(spec)

        from_node = None if src.kind == CanvasBlockKind.FEED else src.block_id
        to_node = None if dst.kind == CanvasBlockKind.PRODUCT else dst.block_id
        self.connect_stream_picked.emit(from_node, to_node)
        return resolved_sid

    def remove_block(self, block_id: str, use_undo: bool = True) -> None:
        """Delete a block and any attached stream arrows."""
        blk = self._block_items.get(block_id)
        if blk is None:
            return
        block_spec = {
            "block_id": blk.block_id,
            "kind": blk.kind,
            "title": blk.title,
            "unit_type": blk.unit_type,
            "is_tailings": blk.is_tailings,
            "pos": QPointF(blk.pos()),
        }
        attached = [
            {
                "stream_id": s.stream_id,
                "stream_name": s.stream_name,
                "source_block_id": s.source_block_id,
                "target_block_id": s.target_block_id,
                "source_port": s.source_port,
                "target_port": s.target_port,
            }
            for s in self._stream_items.values()
            if s.source_block_id == block_id or s.target_block_id == block_id
        ]
        if use_undo:
            self.undo_stack.push(
                _RemoveBlockCommand(self, block_spec, attached, self._i18n.t("canvas.cmd_delete"))
            )
        else:
            for s in attached:
                self._internal_delete_stream(s["stream_id"])
            self._internal_delete_block(block_id)

    def remove_stream(self, stream_id: str, use_undo: bool = True) -> None:
        """Delete a stream arrow."""
        s = self._stream_items.get(stream_id)
        if s is None:
            return
        spec = {
            "stream_id": s.stream_id,
            "stream_name": s.stream_name,
            "source_block_id": s.source_block_id,
            "target_block_id": s.target_block_id,
            "source_port": s.source_port,
            "target_port": s.target_port,
        }
        if use_undo:
            self.undo_stack.push(
                _RemoveStreamCommand(self, spec, self._i18n.t("canvas.cmd_delete"))
            )
        else:
            self._internal_delete_stream(stream_id)

    def rename_block(self, block_id: str, new_name: str, use_undo: bool = True) -> None:
        """Rename a block (and connected external terminal stream name if applicable)."""
        blk = self._block_items.get(block_id)
        clean = new_name.strip()
        if blk is None or not clean or clean == blk.title:
            return

        linked_sid = self.stream_for_terminal(block_id)
        old_s_name = (
            self._stream_items[linked_sid].stream_name
            if linked_sid and linked_sid in self._stream_items
            else None
        )
        new_s_name = clean if linked_sid else None

        if use_undo:
            self.undo_stack.push(
                _RenameBlockCommand(
                    self,
                    block_id,
                    blk.title,
                    clean,
                    linked_sid,
                    old_s_name,
                    new_s_name,
                    self._i18n.t("canvas.cmd_rename"),
                )
            )
        else:
            self._internal_apply_block_name(block_id, clean, linked_sid, new_s_name)

    def rename_stream(self, stream_id: str, new_name: str, use_undo: bool = True) -> None:
        """Rename a stream arrow (and its linked terminal block title if external)."""
        s_item = self._stream_items.get(stream_id)
        clean = new_name.strip()
        if s_item is None or not clean or clean == s_item.stream_name:
            return

        term_id = self._terminal_for_stream(stream_id)
        old_b_name = self._block_items[term_id].title if term_id and term_id in self._block_items else None
        new_b_name = clean if term_id else None

        if use_undo:
            self.undo_stack.push(
                _RenameStreamCommand(
                    self,
                    stream_id,
                    s_item.stream_name,
                    clean,
                    term_id,
                    old_b_name,
                    new_b_name,
                    self._i18n.t("canvas.cmd_rename"),
                )
            )
        else:
            self._internal_apply_stream_name(stream_id, clean, term_id, new_b_name)

    def select_block(self, block_id: str) -> None:
        """Programmatically select a block item on the canvas."""
        self._scene.clearSelection()
        if block_id in self._block_items:
            self._block_items[block_id].setSelected(True)

    def select_node(self, node_id: str) -> None:
        """Backwards-compatible alias selecting a PROCESS node block."""
        self.select_block(node_id)

    def select_stream(self, stream_id: str) -> None:
        """Programmatically select a stream arrow on the canvas."""
        self._scene.clearSelection()
        if stream_id in self._stream_items:
            self._stream_items[stream_id].setSelected(True)

    def block_kind(self, block_id: str) -> Optional[CanvasBlockKind]:
        """Return CanvasBlockKind for block_id, or None if not found."""
        blk = self._block_items.get(block_id)
        return blk.kind if blk is not None else None

    def stream_for_terminal(self, block_id: str) -> Optional[str]:
        """Return connected stream_id for a FEED or PRODUCT terminal block."""
        blk = self._block_items.get(block_id)
        if blk is None or blk.kind == CanvasBlockKind.PROCESS:
            return None
        for s_item in self._stream_items.values():
            if blk.kind == CanvasBlockKind.FEED and s_item.source_block_id == block_id:
                return s_item.stream_id
            if blk.kind == CanvasBlockKind.PRODUCT and s_item.target_block_id == block_id:
                return s_item.stream_id
        return None

    def sync_circuit_from_canvas(self) -> CircuitSpec:
        """Derive CircuitSpec cleanly from canvas blocks/arrows (PROCESS -> NodeSpec only)."""
        self._ensure_circuit_exists()
        assert self._circuit is not None

        nodes: list = []
        for blk in self._block_items.values():
            if blk.kind == CanvasBlockKind.PROCESS:
                from core.models import NodeSpec

                nodes.append(
                    NodeSpec(
                        id=blk.block_id,
                        name=blk.title,
                        unit_type=blk.unit_type,
                        pos_x=float(blk.pos().x()),
                        pos_y=float(blk.pos().y()),
                    )
                )

        streams: list[StreamSpec] = []
        for s_item in self._stream_items.values():
            src = self._block_items.get(s_item.source_block_id)
            dst = self._block_items.get(s_item.target_block_id)
            if src is None or dst is None:
                continue

            if src.kind == CanvasBlockKind.FEED and dst.kind == CanvasBlockKind.PROCESS:
                from_node = None
                to_node = dst.block_id
                role = StreamRole.FEED
            elif src.kind == CanvasBlockKind.PROCESS and dst.kind == CanvasBlockKind.PRODUCT:
                from_node = src.block_id
                to_node = None
                is_tail = dst.is_tailings or _is_tailings_name(dst.title) or _is_tailings_name(
                    s_item.stream_name
                )
                role = StreamRole.TAILINGS if is_tail else StreamRole.CONCENTRATE
            elif src.kind == CanvasBlockKind.PROCESS and dst.kind == CanvasBlockKind.PROCESS:
                from_node = src.block_id
                to_node = dst.block_id
                role = StreamRole.INTERNAL
            else:
                continue

            streams.append(
                StreamSpec(
                    id=s_item.stream_id,
                    name=s_item.stream_name,
                    role=role,
                    from_node=from_node,
                    to_node=to_node,
                )
            )

        self._circuit = CircuitSpec(
            name=self._circuit.name,
            description=self._circuit.description,
            mass_unit=self._circuit.mass_unit,
            nodes=nodes,
            streams=streams,
            species=list(self._circuit.species),
        )
        return self._circuit

    # --- Layout & Camera ---

    def auto_layout(self) -> None:
        """Arrange PROCESS blocks left-to-right and align FEED/PRODUCT terminals cleanly."""
        circuit = self.sync_circuit_from_canvas()
        if not circuit.nodes:
            return
        node_ids = [n.id for n in circuit.nodes]
        indeg: dict[str, int] = {nid: 0 for nid in node_ids}
        adj: dict[str, list[str]] = {nid: [] for nid in node_ids}
        has_feed = {
            s.to_node for s in circuit.streams if s.from_node is None and s.to_node is not None
        }
        for stream in circuit.streams:
            if (
                stream.from_node is not None
                and stream.to_node is not None
                and stream.from_node != stream.to_node
            ):
                adj[stream.from_node].append(stream.to_node)
                indeg[stream.to_node] += 1

        layer: dict[str, int] = {}
        seeds = [nid for nid in node_ids if nid in has_feed] or [
            min(node_ids, key=lambda nid: indeg[nid])
        ]
        queue: deque[str] = deque()
        for seed in seeds:
            layer[seed] = 0
            queue.append(seed)
        while queue:
            cur = queue.popleft()
            for nxt in adj.get(cur, ()):
                if nxt not in layer:
                    layer[nxt] = layer[cur] + 1
                    queue.append(nxt)
        for nid in node_ids:
            layer.setdefault(nid, 0)

        by_layer: dict[int, list[str]] = defaultdict(list)
        for nid in node_ids:
            by_layer[layer[nid]].append(nid)

        x_spacing = 270.0
        y_spacing = 160.0
        for col, layer_nids in sorted(by_layer.items()):
            total_h = (len(layer_nids) - 1) * y_spacing
            start_y = 200.0 - total_h / 2.0
            for row, nid in enumerate(layer_nids):
                pos = QPointF(120.0 + col * x_spacing, start_y + row * y_spacing)
                if nid in self._block_items:
                    self._block_items[nid].setPos(pos)

        # Reposition terminals relative to connected PROCESS blocks
        in_by_proc: dict[str, list[CanvasStreamItem]] = defaultdict(list)
        out_by_proc: dict[str, list[CanvasStreamItem]] = defaultdict(list)
        for s_item in self._stream_items.values():
            src = self._block_items.get(s_item.source_block_id)
            dst = self._block_items.get(s_item.target_block_id)
            if src and dst:
                if src.kind == CanvasBlockKind.FEED and dst.kind == CanvasBlockKind.PROCESS:
                    in_by_proc[dst.block_id].append(s_item)
                elif src.kind == CanvasBlockKind.PROCESS and dst.kind == CanvasBlockKind.PRODUCT:
                    out_by_proc[src.block_id].append(s_item)

        for nid, items in in_by_proc.items():
            base = self._block_items[nid].pos()
            for idx, s_item in enumerate(items):
                dy = (idx - (len(items) - 1) / 2.0) * 92.0
                pos = QPointF(base.x() - 265.0, base.y() + dy)
                self._block_items[s_item.source_block_id].setPos(pos)
                self._terminal_positions[s_item.stream_id] = pos

        for nid, items in out_by_proc.items():
            base = self._block_items[nid].pos()
            for idx, s_item in enumerate(items):
                dy = (idx - (len(items) - 1) / 2.0) * 90.0
                pos = QPointF(base.x() + 280.0, base.y() + dy)
                self._block_items[s_item.target_block_id].setPos(pos)
                self._terminal_positions[s_item.stream_id] = pos

        self.sync_circuit_from_canvas()
        self._refresh_all_overlays()
        self.zoom_fit()

    def zoom_fit(self) -> None:
        """Fit all flowsheet blocks and arrows comfortably inside the viewport."""
        rect = self._scene.itemsBoundingRect()
        if rect.isEmpty():
            return
        if self.viewport().width() <= 16 or self.viewport().height() <= 16:
            self._pending_zoom_fit = True
            return
        self._pending_zoom_fit = False
        margin_rect = rect.adjusted(-75.0, -75.0, 75.0, 60.0)
        self.fitInView(margin_rect, Qt.AspectRatioMode.KeepAspectRatio)

    def fit_to_view(self) -> None:
        """Fit all flowsheet items comfortably inside the viewport."""
        self.zoom_fit()

    # --- Internal Undo/Redo Mutations ---

    def _ensure_circuit_exists(self) -> None:
        if self._circuit is None:
            self._circuit = CircuitSpec(
                name=self._i18n.t("app.title"),
                description="",
                mass_unit=MassUnit.T_H,
                nodes=[],
                streams=[],
                species=[SpeciesSpec(id="Cu", name="Copper", unit=GradeUnit.PERCENT)],
            )

    def _resolve_new_block_pos(
        self,
        kind: CanvasBlockKind,
        pos: Optional[QPointF | tuple[float, float]],
    ) -> QPointF:
        if pos is not None:
            raw = pos if isinstance(pos, QPointF) else QPointF(float(pos[0]), float(pos[1]))
            return _snap_point(raw)
        existing_of_kind = [b for b in self._block_items.values() if b.kind == kind]
        idx = len(existing_of_kind)
        if kind == CanvasBlockKind.FEED:
            return _snap_point(QPointF(24.0, 180.0 + idx * 96.0))
        if kind == CanvasBlockKind.PRODUCT:
            return _snap_point(QPointF(528.0, 144.0 + idx * 96.0))
        return _snap_point(QPointF(264.0 + (idx % 3) * 192.0, 180.0 + (idx // 3) * 120.0))

    def _is_valid_connection(self, source_block_id: str, target_block_id: str) -> bool:
        if source_block_id == target_block_id:
            return False
        src = self._block_items.get(source_block_id)
        dst = self._block_items.get(target_block_id)
        if src is None or dst is None:
            return False
        if src.kind == CanvasBlockKind.FEED and dst.kind == CanvasBlockKind.PROCESS:
            return True
        if src.kind == CanvasBlockKind.PROCESS and dst.kind in (
            CanvasBlockKind.PROCESS,
            CanvasBlockKind.PRODUCT,
        ):
            return True
        return False

    def _terminal_for_stream(self, stream_id: str) -> Optional[str]:
        s_item = self._stream_items.get(stream_id)
        if s_item is None:
            return None
        src = self._block_items.get(s_item.source_block_id)
        if src and src.kind == CanvasBlockKind.FEED:
            return src.block_id
        dst = self._block_items.get(s_item.target_block_id)
        if dst and dst.kind == CanvasBlockKind.PRODUCT:
            return dst.block_id
        return None

    def _internal_create_block(self, spec: dict) -> None:
        bid = spec["block_id"]
        if bid in self._block_items:
            return
        blk = CanvasBlockItem(
            block_id=bid,
            kind=spec["kind"],
            title=spec["title"],
            canvas=self,
            unit_type=spec.get("unit_type", UnitType.FLOTATION_CELL),
            is_tailings=bool(spec.get("is_tailings", False)),
            pos=QPointF(spec["pos"]),
            interactive=self._interactive,
        )
        self._scene.addItem(blk)
        self._block_items[bid] = blk
        self._refresh_all_overlays()
        self.circuit_changed.emit(self.sync_circuit_from_canvas())

    def _internal_delete_block(self, block_id: str) -> None:
        blk = self._block_items.pop(block_id, None)
        if blk is None:
            return
        self._scene.removeItem(blk)
        self._refresh_all_overlays()
        self.circuit_changed.emit(self.sync_circuit_from_canvas())

    def _internal_set_block_pos(self, block_id: str, pos: QPointF) -> None:
        blk = self._block_items.get(block_id)
        if blk is None:
            return
        blk.setPos(pos)
        if blk.kind != CanvasBlockKind.PROCESS:
            sid = self.stream_for_terminal(block_id)
            if sid:
                self._terminal_positions[sid] = QPointF(pos)
        self.sync_circuit_from_canvas()
        self._update_all_stream_geometries()
        self.node_moved.emit(block_id, float(pos.x()), float(pos.y()))

    def _internal_apply_block_name(
        self,
        block_id: str,
        name: str,
        linked_stream_id: Optional[str],
        stream_name: Optional[str],
    ) -> None:
        blk = self._block_items.get(block_id)
        if blk is None:
            return
        blk.title = name
        if blk.kind == CanvasBlockKind.PRODUCT and _is_tailings_name(name):
            blk.is_tailings = True
        blk.refresh_visuals()
        if linked_stream_id and stream_name and linked_stream_id in self._stream_items:
            self._stream_items[linked_stream_id].stream_name = stream_name
        self._refresh_all_overlays()
        self.circuit_changed.emit(self.sync_circuit_from_canvas())

    def _internal_create_stream(self, spec: dict) -> None:
        sid = spec["stream_id"]
        if sid in self._stream_items:
            return
        s_item = CanvasStreamItem(
            stream_id=sid,
            stream_name=spec["stream_name"],
            source_block_id=spec["source_block_id"],
            target_block_id=spec["target_block_id"],
            canvas=self,
            source_port=spec.get("source_port"),
            target_port=spec.get("target_port"),
        )
        self._scene.addItem(s_item)
        self._stream_items[sid] = s_item
        self._refresh_all_overlays()
        self.circuit_changed.emit(self.sync_circuit_from_canvas())

    def _internal_delete_stream(self, stream_id: str) -> None:
        s_item = self._stream_items.pop(stream_id, None)
        if s_item is None:
            return
        self._scene.removeItem(s_item)
        self._refresh_all_overlays()
        self.circuit_changed.emit(self.sync_circuit_from_canvas())

    def _internal_apply_stream_name(
        self,
        stream_id: str,
        name: str,
        terminal_block_id: Optional[str],
        block_name: Optional[str],
    ) -> None:
        s_item = self._stream_items.get(stream_id)
        if s_item is None:
            return
        s_item.stream_name = name
        if terminal_block_id and block_name and terminal_block_id in self._block_items:
            t_blk = self._block_items[terminal_block_id]
            t_blk.title = block_name
            t_blk.refresh_visuals()
        self._refresh_all_overlays()
        self.circuit_changed.emit(self.sync_circuit_from_canvas())

    # --- Inline Text Editing ---

    def start_inline_block_edit(self, block_id: str) -> None:
        """Open inline QGraphicsTextItem editor directly over a block label."""
        blk = self._block_items.get(block_id)
        if blk is None or not self._interactive:
            return
        self._finish_inline_edit(commit=True)
        blk.set_editing_active(True)
        rect = blk.label_scene_rect()
        palette = get_theme_palette(self._theme)

        def _commit(text: str) -> None:
            self._clear_editor_item()
            blk.set_editing_active(False)
            if text and text != blk.title:
                self.rename_block(block_id, text, use_undo=True)

        def _cancel(_text: str) -> None:
            self._clear_editor_item()
            blk.set_editing_active(False)

        editor = _InlineTextEditor(blk.title, rect.width(), palette, _commit, _cancel)
        editor.setPos(rect.topLeft())
        self._scene.addItem(editor)
        self._editing_item = editor
        self.setFocus(Qt.FocusReason.OtherFocusReason)
        self._scene.setFocusItem(editor, Qt.FocusReason.MouseFocusReason)
        editor.setFocus(Qt.FocusReason.MouseFocusReason)

    def start_inline_stream_edit(self, stream_id: str) -> None:
        """Open inline QGraphicsTextItem editor directly over an arrow tag."""
        s_item = self._stream_items.get(stream_id)
        if s_item is None or not self._interactive:
            return
        self._finish_inline_edit(commit=True)
        s_item.set_editing_active(True)
        rect = s_item.label_scene_rect()
        palette = get_theme_palette(self._theme)

        def _commit(text: str) -> None:
            self._clear_editor_item()
            s_item.set_editing_active(False)
            if text and text != s_item.stream_name:
                self.rename_stream(stream_id, text, use_undo=True)

        def _cancel(_text: str) -> None:
            self._clear_editor_item()
            s_item.set_editing_active(False)

        editor = _InlineTextEditor(s_item.stream_name, rect.width(), palette, _commit, _cancel)
        editor.setPos(rect.topLeft())
        self._scene.addItem(editor)
        self._editing_item = editor
        self.setFocus(Qt.FocusReason.OtherFocusReason)
        self._scene.setFocusItem(editor, Qt.FocusReason.MouseFocusReason)
        editor.setFocus(Qt.FocusReason.MouseFocusReason)

    def _finish_inline_edit(self, commit: bool) -> None:
        if self._editing_item is not None:
            self._editing_item._finish(commit=commit)

    def _clear_editor_item(self) -> None:
        ed = self._editing_item
        self._editing_item = None
        if ed is not None:
            ed.setVisible(False)
            QTimer.singleShot(0, lambda item=ed: self._remove_scene_item_safely(item))

    def _remove_scene_item_safely(self, item: QGraphicsItem) -> None:
        try:
            if item.scene() is self._scene:
                self._scene.removeItem(item)
        except RuntimeError:
            pass

    # --- Port Drag & Interactive Connection Preview ---

    def _update_drag_preview(self, start_pt: QPointF, end_pt: QPointF) -> None:
        palette = get_theme_palette(self._theme)
        if self._drag_preview_path is None:
            self._drag_preview_path = QGraphicsPathItem()
            pen = QPen(QColor(palette["accent"]), 2.0, Qt.PenStyle.DashLine)
            self._drag_preview_path.setPen(pen)
            self._drag_preview_path.setZValue(50.0)
            self._scene.addItem(self._drag_preview_path)
        if self._drag_preview_arrow is None:
            self._drag_preview_arrow = QGraphicsPolygonItem()
            self._drag_preview_arrow.setBrush(QBrush(QColor(palette["accent"])))
            self._drag_preview_arrow.setPen(QPen(QColor(palette["accent"]), 1.0))
            self._drag_preview_arrow.setZValue(51.0)
            self._scene.addItem(self._drag_preview_arrow)

        path = QPainterPath(start_pt)
        path.lineTo(end_pt)
        self._drag_preview_path.setPath(path)

        dx = end_pt.x() - start_pt.x()
        dy = end_pt.y() - start_pt.y()
        length = math.hypot(dx, dy) or 1.0
        ux, uy = dx / length, dy / length
        arrow_len = 10.0
        base = QPointF(end_pt.x() - ux * arrow_len, end_pt.y() - uy * arrow_len)
        perp = QPointF(-uy * 4.5, ux * 4.5)
        self._drag_preview_arrow.setPolygon(
            QPolygonF(
                [
                    end_pt,
                    QPointF(base.x() + perp.x(), base.y() + perp.y()),
                    QPointF(base.x() - perp.x(), base.y() - perp.y()),
                ]
            )
        )

    def _cancel_port_or_connect_drag(self) -> None:
        self._port_drag_src = None
        self._port_drag_src_port = None
        self._connect_source_block = None
        if self._highlighted_drop_block and self._highlighted_drop_block in self._block_items:
            self._block_items[self._highlighted_drop_block].set_drop_target_highlight(False)
        self._highlighted_drop_block = None
        if self._drag_preview_path is not None:
            self._scene.removeItem(self._drag_preview_path)
            self._drag_preview_path = None
        if self._drag_preview_arrow is not None:
            self._scene.removeItem(self._drag_preview_arrow)
            self._drag_preview_arrow = None

    def _block_at_scene(self, scene_pos: QPointF) -> Optional[CanvasBlockItem]:
        for blk in sorted(self._block_items.values(), key=lambda b: b.zValue(), reverse=True):
            rect = blk.card_rect().adjusted(-PORT_HIT_RADIUS, -PORT_HIT_RADIUS, PORT_HIT_RADIUS, PORT_HIT_RADIUS)
            if rect.contains(blk.mapFromScene(scene_pos)):
                return blk
        return None

    # --- Events ---

    def _on_template_action(self, template_id: str) -> None:
        if self.receivers(SIGNAL("template_requested(QString)")) > 0:
            self.template_requested.emit(template_id)
        else:
            self.load_circuit(get_circuit_template(template_id))
            self.auto_layout()
            self.fit_to_view()
            self.circuit_changed.emit(self.sync_circuit_from_canvas())

    def _on_selection_changed(self) -> None:
        selected = self._scene.selectedItems()
        if not selected:
            self.selection_cleared.emit()
            return
        first = selected[0]
        if isinstance(first, CanvasBlockItem):
            self.block_selected.emit(first.block_id, first.kind.value)
            if first.kind == CanvasBlockKind.PROCESS:
                self.node_selected.emit(first.block_id)
        elif isinstance(first, CanvasStreamItem):
            self.stream_selected.emit(first.stream_id)

    def on_block_item_moved(self, block_id: str) -> None:
        """Update connected arrows and store terminal positions during interactive drags."""
        blk = self._block_items.get(block_id)
        if blk is not None and blk.kind != CanvasBlockKind.PROCESS:
            sid = self.stream_for_terminal(block_id)
            if sid:
                self._terminal_positions[sid] = QPointF(blk.pos())
        self._update_all_stream_geometries()
        if blk is not None:
            self.node_moved.emit(block_id, float(blk.pos().x()), float(blk.pos().y()))

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.MiddleButton:
            self._panning = True
            self._pan_start = event.position().toPoint()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return

        if event.button() == Qt.MouseButton.LeftButton and self._space_held:
            super().mousePressEvent(event)
            return

        if not self._interactive:
            super().mousePressEvent(event)
            return

        scene_pos = self.mapToScene(event.pos())
        if event.button() == Qt.MouseButton.LeftButton:
            if self._editing_item is not None:
                ed_rect = self._editing_item.mapRectToScene(self._editing_item.boundingRect())
                if not ed_rect.contains(scene_pos):
                    self._finish_inline_edit(commit=True)

            # 1. Placement tools (Feed / Process / Product)
            if self._tool_mode in (
                CanvasToolMode.ADD_FEED,
                CanvasToolMode.ADD_PROCESS,
                CanvasToolMode.ADD_PRODUCT,
            ):
                kind_map = {
                    CanvasToolMode.ADD_FEED: CanvasBlockKind.FEED,
                    CanvasToolMode.ADD_PROCESS: CanvasBlockKind.PROCESS,
                    CanvasToolMode.ADD_PRODUCT: CanvasBlockKind.PRODUCT,
                }
                new_id = self.add_block(kind_map[self._tool_mode], pos=scene_pos, use_undo=True)
                self.set_tool_mode(CanvasToolMode.SELECT)
                self.select_block(new_id)
                event.accept()
                return

            # 2. Port drag check (in Select or Connect mode)
            for blk in self._block_items.values():
                port_name = blk.port_at_scene(scene_pos)
                if port_name is not None:
                    self._port_drag_src = blk.block_id
                    self._port_drag_src_port = port_name
                    self._update_drag_preview(blk.port_scene_pos(port_name), scene_pos)
                    event.accept()
                    return

            # 3. Two-click Connect mode
            if self._tool_mode == CanvasToolMode.CONNECT:
                clicked_blk = self._block_at_scene(scene_pos)
                if self._connect_source_block is None:
                    if clicked_blk is not None:
                        self._connect_source_block = clicked_blk.block_id
                        self.select_block(clicked_blk.block_id)
                        self._update_drag_preview(clicked_blk.pos(), scene_pos)
                else:
                    src_id = self._connect_source_block
                    self._cancel_port_or_connect_drag()
                    if clicked_blk is not None and self._is_valid_connection(src_id, clicked_blk.block_id):
                        sid = self.connect_blocks(src_id, clicked_blk.block_id, use_undo=True)
                        self.set_tool_mode(CanvasToolMode.SELECT)
                        self.select_stream(sid)
                event.accept()
                return

            # 4. Standard Select/Move mode: snapshot positions for Undo
            self._drag_start_positions = {
                bid: QPointF(blk.pos()) for bid, blk in self._block_items.items()
            }
            self._is_user_dragging_block = True

        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._panning:
            cur = event.position().toPoint()
            delta = cur - self._pan_start
            self._pan_start = cur
            h_bar = self.horizontalScrollBar()
            v_bar = self.verticalScrollBar()
            h_bar.setValue(h_bar.value() - delta.x())
            v_bar.setValue(v_bar.value() - delta.y())
            event.accept()
            return

        if self._port_drag_src is not None or self._connect_source_block is not None:
            scene_pos = self.mapToScene(event.pos())
            src_id = self._port_drag_src or self._connect_source_block
            assert src_id is not None
            src_blk = self._block_items.get(src_id)
            if src_blk is not None:
                start_pt = (
                    src_blk.port_scene_pos(self._port_drag_src_port)
                    if self._port_drag_src_port
                    else src_blk.pos()
                )
                self._update_drag_preview(start_pt, scene_pos)

                target_blk = self._block_at_scene(scene_pos)
                valid_id = (
                    target_blk.block_id
                    if (
                        target_blk is not None
                        and self._is_valid_connection(src_id, target_blk.block_id)
                    )
                    else None
                )
                if valid_id != self._highlighted_drop_block:
                    if (
                        self._highlighted_drop_block
                        and self._highlighted_drop_block in self._block_items
                    ):
                        self._block_items[self._highlighted_drop_block].set_drop_target_highlight(
                            False
                        )
                    self._highlighted_drop_block = valid_id
                    if valid_id and valid_id in self._block_items:
                        self._block_items[valid_id].set_drop_target_highlight(True)
            event.accept()
            return

        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._panning and event.button() == Qt.MouseButton.MiddleButton:
            self._panning = False
            self.setCursor(
                Qt.CursorShape.ArrowCursor
                if self._tool_mode == CanvasToolMode.SELECT
                else Qt.CursorShape.CrossCursor
            )
            event.accept()
            return

        if self._port_drag_src is not None and event.button() == Qt.MouseButton.LeftButton:
            scene_pos = self.mapToScene(event.pos())
            src_id = self._port_drag_src
            src_port = self._port_drag_src_port
            target_blk = self._block_at_scene(scene_pos)
            self._cancel_port_or_connect_drag()

            if target_blk is not None and self._is_valid_connection(src_id, target_blk.block_id):
                tgt_port = target_blk.nearest_port_to_scene(scene_pos)
                sid = self.connect_blocks(
                    src_id,
                    target_blk.block_id,
                    source_port=src_port,
                    target_port=tgt_port,
                    use_undo=True,
                )
                self.select_stream(sid)
            event.accept()
            return

        super().mouseReleaseEvent(event)

        if self._is_user_dragging_block:
            self._is_user_dragging_block = False
            moves: list[tuple[str, QPointF, QPointF]] = []
            for bid, old_pos in self._drag_start_positions.items():
                blk = self._block_items.get(bid)
                if blk is not None:
                    new_pos = QPointF(blk.pos())
                    if (new_pos - old_pos).manhattanLength() > 0.5:
                        moves.append((bid, old_pos, new_pos))
            self._drag_start_positions.clear()
            if moves:
                self.undo_stack.push(
                    _MoveBlocksCommand(self, moves, self._i18n.t("canvas.cmd_move_block"))
                )

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if self._editing_item is not None:
            super().keyPressEvent(event)
            return

        key = event.key()
        mods = event.modifiers()

        if key == Qt.Key.Key_Space and not event.isAutoRepeat() and not self._space_held:
            self._space_held = True
            self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
            event.accept()
            return

        if not self._interactive:
            super().keyPressEvent(event)
            return

        if mods & Qt.KeyboardModifier.ControlModifier:
            if key == Qt.Key.Key_Z:
                if mods & Qt.KeyboardModifier.ShiftModifier:
                    self.undo_stack.redo()
                else:
                    self.undo_stack.undo()
                event.accept()
                return
            if key == Qt.Key.Key_Y:
                self.undo_stack.redo()
                event.accept()
                return

        if key == Qt.Key.Key_Escape:
            self._cancel_port_or_connect_drag()
            self.set_tool_mode(CanvasToolMode.SELECT)
            event.accept()
            return
        if key == Qt.Key.Key_V:
            self.set_tool_mode(CanvasToolMode.SELECT)
            event.accept()
            return
        if key == Qt.Key.Key_F:
            self.set_tool_mode(CanvasToolMode.ADD_FEED)
            event.accept()
            return
        if key == Qt.Key.Key_P:
            self.set_tool_mode(CanvasToolMode.ADD_PROCESS)
            event.accept()
            return
        if key == Qt.Key.Key_S:
            self.set_tool_mode(CanvasToolMode.ADD_PRODUCT)
            event.accept()
            return
        if key == Qt.Key.Key_A:
            self.set_tool_mode(CanvasToolMode.CONNECT)
            event.accept()
            return
        if key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self._delete_selected_items()
            event.accept()
            return

        super().keyPressEvent(event)

    def keyReleaseEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Space and not event.isAutoRepeat() and self._space_held:
            self._space_held = False
            self.set_tool_mode(self._tool_mode)
            event.accept()
            return
        super().keyReleaseEvent(event)

    def _delete_selected_items(self) -> None:
        selected = self._scene.selectedItems()
        if not selected:
            return
        block_ids = [item.block_id for item in selected if isinstance(item, CanvasBlockItem)]
        stream_ids = [item.stream_id for item in selected if isinstance(item, CanvasStreamItem)]
        if not block_ids and not stream_ids:
            return
        self.undo_stack.beginMacro(self._i18n.t("canvas.cmd_delete"))
        try:
            for sid in stream_ids:
                if sid in self._stream_items:
                    self.remove_stream(sid, use_undo=True)
            for bid in block_ids:
                if bid in self._block_items:
                    self.remove_block(bid, use_undo=True)
        finally:
            self.undo_stack.endMacro()

    def wheelEvent(self, event: QWheelEvent) -> None:
        delta = event.angleDelta().y()
        if delta == 0:
            return
        factor = 1.15 if delta > 0 else 1.0 / 1.15
        cur_scale = self.transform().m11()
        next_scale = cur_scale * factor
        if 0.25 <= next_scale <= 3.0:
            self.scale(factor, factor)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._reposition_toolbar()
        if self._pending_zoom_fit:
            self.zoom_fit()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._reposition_toolbar()
        if self._pending_zoom_fit:
            self.zoom_fit()

    def _reposition_toolbar(self) -> None:
        if not self._interactive:
            return
        self._toolbar.adjustSize()
        vw = self.viewport().width()
        tw = self._toolbar.width()
        x = max(8, (vw - tw) // 2)
        self._toolbar.move(x, 12)

    def drawBackground(self, painter: QPainter, rect: QRectF) -> None:
        is_dark = self._theme == "dark"
        bg_hex = "#14181D" if is_dark else "#F8FAFC"
        dot_hex = "#262E38" if is_dark else "#E2E8F0"

        painter.fillRect(rect, QColor(bg_hex))

        left = math.floor(rect.left() / GRID_STEP) * GRID_STEP
        top = math.floor(rect.top() / GRID_STEP) * GRID_STEP
        nx = int((rect.right() - left) / GRID_STEP) + 1
        ny = int((rect.bottom() - top) / GRID_STEP) + 1
        if nx * ny > 18000:
            return

        points: list[QPointF] = []
        x = left
        while x <= rect.right():
            y = top
            while y <= rect.bottom():
                points.append(QPointF(x, y))
                y += GRID_STEP
            x += GRID_STEP

        if points:
            painter.save()
            painter.setPen(
                QPen(QColor(dot_hex), 2.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
            )
            painter.drawPoints(points)
            painter.restore()

    # --- Overlay & Geometry Rendering ---

    def _refresh_all_overlays(self) -> None:
        self._refresh_block_badges()
        self._update_all_stream_geometries()

    def _active_measurement_point(self):
        if not self._cleaned_dataset or not self._cleaned_dataset.periods:
            return None
        idx = max(0, min(self._active_period_idx, len(self._cleaned_dataset.periods) - 1))
        return self._cleaned_dataset.periods[idx]

    def _stream_has_data(self, stream_id: str) -> bool:
        pt = self._active_measurement_point()
        if pt is None:
            return False
        m_val = pt.mass_flows.get(stream_id)
        if m_val is not None and math.isfinite(float(m_val)):
            return True
        assays = pt.assays.get(stream_id, {})
        return any(v is not None and math.isfinite(float(v)) for v in assays.values())

    def _stream_outlier_flag(self, stream_id: str) -> bool:
        if self._reconciliation is None:
            return False
        return any(
            adj.stream_id == stream_id and adj.is_outlier
            for adj in self._reconciliation.adjustments
        )

    def _refresh_block_badges(self) -> None:
        for blk in self._block_items.values():
            if blk.kind == CanvasBlockKind.PROCESS:
                in_out_streams = [
                    s.stream_id
                    for s in self._stream_items.values()
                    if s.source_block_id == blk.block_id or s.target_block_id == blk.block_id
                ]
                has_out = any(self._stream_outlier_flag(sid) for sid in in_out_streams)
                if has_out:
                    blk.set_badge(self._i18n.t("canvas.badge_outlier"), "error", has_outlier=True)
                elif self._reconciliation is not None and self._reconciliation.converged:
                    blk.set_badge(self._i18n.t("canvas.badge_reconciled"), "reconciled")
                elif in_out_streams and all(self._stream_has_data(sid) for sid in in_out_streams):
                    blk.set_badge(self._i18n.t("canvas.badge_ready"), "validated")
                else:
                    blk.set_badge("", "pending")
            else:
                sid = self.stream_for_terminal(blk.block_id)
                if sid is None:
                    blk.set_badge("", "pending")
                    continue
                is_out = self._stream_outlier_flag(sid)
                if is_out:
                    blk.set_badge(self._i18n.t("canvas.badge_outlier"), "error", has_outlier=True)
                elif self._reconciliation is not None and sid in self._reconciliation.reconciled_mass_flows:
                    m_val = self._reconciliation.reconciled_mass_flows[sid]
                    unit = self._circuit.mass_unit.value if self._circuit else ""
                    blk.set_badge(f"{m_val:.1f} {unit}".strip(), "reconciled")
                elif self._stream_has_data(sid):
                    blk.set_badge(self._i18n.t("canvas.badge_ready"), "validated")
                else:
                    blk.set_badge(self._i18n.t("canvas.badge_no_data"), "pending")

    def _stream_visual_style(
        self,
        stream_id: str,
        src_kind: CanvasBlockKind,
        dst_kind: CanvasBlockKind,
        dst_is_tailings: bool,
    ) -> tuple[str, float, Qt.PenStyle, bool]:
        palette = get_theme_palette(self._theme)
        is_outlier = self._stream_outlier_flag(stream_id)
        if is_outlier:
            return palette["error"], 3.0, Qt.PenStyle.SolidLine, True

        # Step 04 Observability Mode
        if self._observability is not None and self._active_step_idx == 3 and not self._sankey_mode:
            classes = [
                st.classification
                for st in self._observability.variable_statuses
                if st.stream_id == stream_id
            ]
            if VariableClass.UNMEASURED_UNOBSERVABLE in classes:
                return palette["error"], 2.8, Qt.PenStyle.DashLine, False
            if VariableClass.UNMEASURED_OBSERVABLE in classes:
                return palette["warning"], 2.2, Qt.PenStyle.DashLine, False
            if classes and all(c == VariableClass.MEASURED_REDUNDANT for c in classes):
                return palette["validated"], 2.2, Qt.PenStyle.SolidLine, False
            if VariableClass.MEASURED_REDUNDANT in classes:
                return palette["validated"], 2.2, Qt.PenStyle.SolidLine, False
            return palette["stream_internal"], 2.0, Qt.PenStyle.SolidLine, False

        # Reconciled / Sankey Mode
        if self._reconciliation is not None and (self._sankey_mode or self._active_step_idx >= 4):
            if src_kind == CanvasBlockKind.FEED:
                col = palette["stream_feed"]
            elif dst_kind == CanvasBlockKind.PRODUCT:
                col = palette["stream_tail"] if dst_is_tailings else palette["stream_conc"]
            else:
                col = palette["stream_internal"]
            return col, 2.2, Qt.PenStyle.SolidLine, False

        # Data ready vs default
        if self._stream_has_data(stream_id):
            return palette["validated"], 2.1, Qt.PenStyle.SolidLine, False

        if src_kind == CanvasBlockKind.FEED:
            return palette["stream_feed"], 1.9, Qt.PenStyle.SolidLine, False
        if dst_kind == CanvasBlockKind.PRODUCT:
            col = palette["stream_tail"] if dst_is_tailings else palette["stream_conc"]
            return col, 1.9, Qt.PenStyle.SolidLine, False
        return palette["pending"], 1.9, Qt.PenStyle.SolidLine, False

    def _stream_label(self, s_item: CanvasStreamItem) -> str:
        show_reconciled = (
            self._reconciliation is not None
            and self._circuit is not None
            and (self._sankey_mode or self._active_step_idx >= 4)
        )
        if not show_reconciled:
            return s_item.stream_name

        assert self._reconciliation is not None and self._circuit is not None
        mass_val = self._reconciliation.reconciled_mass_flows.get(s_item.stream_id)
        if mass_val is None:
            return s_item.stream_name

        lines = [
            s_item.stream_name,
            self._i18n.t(
                "step06.charts.sankey_mass_fmt",
                mass=mass_val,
                unit=self._circuit.mass_unit.value,
            ),
        ]
        if self._sankey_mode:
            stream_assays = self._reconciliation.reconciled_assays.get(s_item.stream_id, {})
            for sp in self._circuit.species[:3]:
                val = stream_assays.get(sp.id)
                if val is not None:
                    lines.append(
                        self._i18n.t(
                            "step06.charts.sankey_assay_fmt",
                            species=sp.id,
                            val=val,
                            unit=sp.unit.value,
                        )
                    )
        return "\n".join(lines)

    def _update_all_stream_geometries(self) -> None:
        if not self._stream_items:
            return

        max_flow = 1.0
        use_sankey_widths = self._sankey_mode and self._reconciliation is not None
        if use_sankey_widths and self._reconciliation is not None:
            flows = list(self._reconciliation.reconciled_mass_flows.values())
            if flows:
                max_flow = max(max(flows), 1e-6)

        out_ports: dict[str, list[CanvasStreamItem]] = defaultdict(list)
        in_ports: dict[str, list[CanvasStreamItem]] = defaultdict(list)
        for s_item in self._stream_items.values():
            out_ports[s_item.source_block_id].append(s_item)
            in_ports[s_item.target_block_id].append(s_item)

        all_ys = [blk.pos().y() for blk in self._block_items.values()] or [200.0]
        min_y = min(all_ys)
        max_y = max(all_ys)
        feedback_idx = 0

        for s_item in self._stream_items.values():
            src_blk = self._block_items.get(s_item.source_block_id)
            dst_blk = self._block_items.get(s_item.target_block_id)
            if src_blk is None or dst_blk is None:
                continue

            out_list = out_ports[src_blk.block_id]
            in_list = in_ports[dst_blk.block_id]
            out_slot = out_list.index(s_item)
            in_slot = in_list.index(s_item)

            out_dy = ((out_slot + 1) / (len(out_list) + 1) - 0.5) * (src_blk.box_height * 0.65)
            in_dy = ((in_slot + 1) / (len(in_list) + 1) - 0.5) * (dst_blk.box_height * 0.65)

            detour_y: Optional[float] = None
            vertical_mode = False

            dx = dst_blk.pos().x() - src_blk.pos().x()
            dy = dst_blk.pos().y() - src_blk.pos().y()

            if s_item.source_port in ("N", "S") and s_item.target_port in ("N", "S"):
                vertical_mode = True
                start_pt = src_blk.port_scene_pos(s_item.source_port)
                end_pt = dst_blk.port_scene_pos(s_item.target_port)
            elif abs(dx) < (src_blk.box_width + dst_blk.box_width) * 0.45 and abs(dy) > 70.0:
                vertical_mode = True
                if dy >= 0:
                    start_pt = src_blk.port_scene_pos("S")
                    end_pt = dst_blk.port_scene_pos("N")
                else:
                    start_pt = src_blk.port_scene_pos("N")
                    end_pt = dst_blk.port_scene_pos("S")
            else:
                start_pt = QPointF(
                    src_blk.pos().x() + src_blk.box_width / 2.0,
                    src_blk.pos().y() + out_dy,
                )
                end_pt = QPointF(
                    dst_blk.pos().x() - dst_blk.box_width / 2.0,
                    dst_blk.pos().y() + in_dy,
                )
                if start_pt.x() >= end_pt.x() - 10.0:
                    if feedback_idx % 2 == 0:
                        detour_y = min_y - 75.0 - 42.0 * (feedback_idx // 2)
                    else:
                        detour_y = max_y + 75.0 + 42.0 * (feedback_idx // 2)
                    feedback_idx += 1

            color_hex, base_width, pen_style, is_outlier = self._stream_visual_style(
                s_item.stream_id,
                src_blk.kind,
                dst_blk.kind,
                dst_blk.is_tailings or _is_tailings_name(dst_blk.title),
            )
            stroke_width = base_width
            if use_sankey_widths and self._reconciliation is not None:
                m_val = self._reconciliation.reconciled_mass_flows.get(s_item.stream_id, 0.0)
                ratio = max(0.0, min(1.0, m_val / max_flow))
                stroke_width = 1.8 + 8.5 * ratio

            s_item.update_geometry(
                start_pt=start_pt,
                end_pt=end_pt,
                detour_y=detour_y,
                stroke_width=stroke_width,
                color_hex=color_hex,
                label_text=self._stream_label(s_item),
                pen_style=pen_style,
                is_outlier=is_outlier,
                mono_label=self._sankey_mode,
                vertical_mode=vertical_mode,
            )


FlowsheetNodeItem = CanvasBlockItem
FlowsheetStreamItem = CanvasStreamItem
