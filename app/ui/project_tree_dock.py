"""Sequential 7-step Leapfrog-style workflow navigation dock."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QModelIndex, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPainter
from PySide6.QtWidgets import (
    QHeaderView,
    QSizePolicy,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTreeWidget,
    QTreeWidgetItem,
    QWidget,
)

from app.i18n.manager import get_i18n
from core.models import StepStatus

from .collapsible_dock import CollapsibleDockWidget
from .ui_common import (
    STEP_COUNT,
    ProjectState,
    get_theme_palette,
    load_svg_icon,
    monospace_font,
    ui_font,
)

_STEP_ICONS: tuple[str, ...] = (
    "pid_concentrator.svg",
    "import_table.svg",
    "gear.svg",
    "matrix.svg",
    "play.svg",
    "scatter_plot.svg",
    "export_doc.svg",
)

_ROLE_STEP_IDX = Qt.ItemDataRole.UserRole
_ROLE_STATUS_TEXT = Qt.ItemDataRole.UserRole + 1
_ROLE_STATUS_COLOR = Qt.ItemDataRole.UserRole + 2
_ROLE_UNLOCKED = Qt.ItemDataRole.UserRole + 3
_ROLE_ACTIVE = Qt.ItemDataRole.UserRole + 4


class _WorkflowStepDelegate(QStyledItemDelegate):
    """Two-line step delegate rendering full step title on line 1 and status tag on line 2."""

    def __init__(self, dock: "ProjectTreeDock") -> None:
        super().__init__(dock)
        self._dock = dock

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:
        return QSize(max(180, option.rect.width()), 44)

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index: QModelIndex,
    ) -> None:
        palette = get_theme_palette(self._dock.theme)
        rect = option.rect
        is_active = bool(index.data(_ROLE_ACTIVE))
        is_selected = bool(option.state & QStyle.StateFlag.State_Selected) or is_active
        is_hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        unlocked = bool(index.data(_ROLE_UNLOCKED))

        painter.save()
        if is_selected:
            bg = QColor(palette["header_bg"])
            painter.fillRect(rect, bg)
            painter.fillRect(
                QRect(rect.left(), rect.top(), 3, rect.height()),
                QColor(palette["accent"]),
            )
        elif is_hovered:
            hover_col = QColor(palette["app_bg"] if self._dock.theme == "light" else palette["header_bg"])
            painter.fillRect(rect, hover_col)
        else:
            painter.fillRect(rect, QColor(palette["panel_bg"]))

        # Icon (18x18) vertically centered on the left
        icon = index.data(Qt.ItemDataRole.DecorationRole)
        if isinstance(icon, QIcon) and not icon.isNull():
            icon_rect = QRect(rect.left() + 10, rect.top() + (rect.height() - 18) // 2, 18, 18)
            icon.paint(painter, icon_rect, Qt.AlignmentFlag.AlignCenter)

        text_left = rect.left() + 36
        text_width = max(40, rect.right() - text_left - 8)

        # Line 1: Full step title
        title = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
        title_font = ui_font(9.2, bold=is_active)
        painter.setFont(title_font)
        title_color = QColor(
            palette["text_primary"] if unlocked else palette["text_secondary"]
        )
        painter.setPen(title_color)
        title_rect = QRect(text_left, rect.top() + 5, text_width, 19)
        elided_title = painter.fontMetrics().elidedText(
            title, Qt.TextElideMode.ElideRight, text_width
        )
        painter.drawText(
            title_rect,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            elided_title,
        )

        # Line 2: Monospace status badge
        status_text = str(index.data(_ROLE_STATUS_TEXT) or "")
        status_color_str = str(index.data(_ROLE_STATUS_COLOR) or palette["pending"])
        painter.setFont(monospace_font(8.2, bold=True))
        painter.setPen(QColor(status_color_str))
        status_rect = QRect(text_left, rect.top() + 24, text_width, 16)
        painter.drawText(
            status_rect,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            status_text,
        )

        # Crisp 1px separator line at bottom
        painter.setPen(QColor(palette["grid_minor"]))
        painter.drawLine(rect.left() + 8, rect.bottom(), rect.right() - 8, rect.bottom())
        painter.restore()


class ProjectTreeDock(CollapsibleDockWidget):
    """Dockable sequential 7-step workflow tree with status tags and dependency locking."""

    step_selected = Signal(int)
    locked_step_clicked = Signal(int, str)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("ProjectTreeDock")
        self.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea
        )
        self.set_dock_icon("matrix.svg")

        self._i18n = get_i18n()
        self._state: Optional[ProjectState] = None
        self._current_step_idx = 0
        self._updating = False

        self.tree = QTreeWidget(self)
        self.tree.setObjectName("ProjectWorkflowTree")
        self.tree.setColumnCount(1)
        self.tree.setHeaderHidden(True)
        self.tree.setRootIsDecorated(False)
        self.tree.setIndentation(0)
        self.tree.setUniformRowHeights(True)
        self.tree.setAlternatingRowColors(False)
        self.tree.setWordWrap(False)
        self.tree.setMouseTracking(True)
        self.tree.setMinimumWidth(180)
        self.tree.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding
        )
        self.tree.setItemDelegate(_WorkflowStepDelegate(self))
        header = self.tree.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)

        self._items: list[QTreeWidgetItem] = []
        for idx in range(STEP_COUNT):
            item = QTreeWidgetItem(self.tree)
            item.setData(0, _ROLE_STEP_IDX, idx)
            item.setSizeHint(0, QSize(180, 44))
            self._items.append(item)

        self.setWidget(self.tree)
        self.setMinimumWidth(210)
        self.setMaximumWidth(16777215)
        self.setMinimumHeight(120)

        self.tree.itemClicked.connect(self._on_item_clicked)
        self.retranslate_ui()

    @property
    def theme(self) -> str:
        return self._theme

    def sizeHint(self) -> QSize:
        return QSize(260, 600)

    def minimumSizeHint(self) -> QSize:
        return QSize(210, 120)

    def step_item(self, idx: int) -> QTreeWidgetItem:
        """Return the QTreeWidgetItem for workflow step index (0..6)."""
        return self._items[idx]

    def set_theme(self, theme: str) -> None:
        """Update title bar, icon strokes, and status tag colors for the active theme."""
        super().set_theme(theme)
        self._refresh_visuals()

    def refresh_steps(self, state: ProjectState, current_step_idx: int) -> None:
        """Synchronize step titles, lock states, icons, and status tags from ProjectState."""
        self._state = state
        self._current_step_idx = current_step_idx
        self._refresh_visuals()

    def retranslate_ui(self) -> None:
        """Apply current i18n translations to dock title, steps, tooltips, and statuses."""
        self.setWindowTitle(self._i18n.t("docks.project_tree_title"))
        self.retranslate_title_bar()
        self._refresh_visuals()

    def _status_text_and_color(
        self, status: StepStatus, unlocked: bool
    ) -> tuple[str, str]:
        palette = get_theme_palette(self._theme)
        if not unlocked and status == StepStatus.PENDING:
            return self._i18n.t("workflow.status.blocked"), palette["pending"]
        if status == StepStatus.VALIDATED:
            return self._i18n.t("workflow.status.validated"), palette["validated"]
        if status == StepStatus.WARNINGS:
            return self._i18n.t("workflow.status.warnings"), palette["warning"]
        if status == StepStatus.BLOCKED:
            return self._i18n.t("workflow.status.blocked"), palette["error"]
        return self._i18n.t("workflow.status.pending"), palette["pending"]

    def _refresh_visuals(self) -> None:
        self._updating = True
        try:
            palette = get_theme_palette(self._theme)
            for idx, item in enumerate(self._items):
                step_num = f"{idx + 1:02d}"
                title = self._i18n.t(f"workflow.step_{step_num}_title")
                desc = self._i18n.t(f"workflow.step_{step_num}_desc")

                if self._state is not None:
                    unlocked = self._state.is_step_unlocked(idx)
                    status = self._state.step_statuses[idx]
                    block_reason = self._state.step_block_reasons.get(idx)
                else:
                    unlocked = idx == 0
                    status = StepStatus.PENDING
                    block_reason = None

                status_text, status_color = self._status_text_and_color(status, unlocked)
                is_active = idx == self._current_step_idx

                item.setText(0, title)
                item.setData(0, _ROLE_STATUS_TEXT, status_text)
                item.setData(0, _ROLE_STATUS_COLOR, status_color)
                item.setData(0, _ROLE_UNLOCKED, unlocked)
                item.setData(0, _ROLE_ACTIVE, is_active)

                icon_name = _STEP_ICONS[idx] if unlocked else "lock.svg"
                icon_color = (
                    palette["accent"]
                    if is_active
                    else (palette["icon_stroke"] if unlocked else palette["pending"])
                )
                item.setIcon(0, load_svg_icon(icon_name, theme=self._theme, color_override=icon_color, size=18))

                tooltip_lines = [desc]
                if not unlocked and idx > 0:
                    prev_title = self._i18n.t(f"workflow.step_{idx:02d}_title")
                    tooltip_lines.append(
                        self._i18n.t("workflow.nav.step_locked_msg", prev_step=prev_title)
                    )
                elif block_reason:
                    tooltip_lines.append(
                        self._i18n.t("docks.inspector_step_blocked_reason", reason=block_reason)
                    )
                tooltip = "\n".join(tooltip_lines)
                item.setToolTip(0, tooltip)

                if is_active:
                    self.tree.setCurrentItem(item)
            self.tree.viewport().update()
        finally:
            self._updating = False

    def _on_item_clicked(self, item: QTreeWidgetItem, _column: int) -> None:
        if self._updating:
            return
        step_idx = item.data(0, _ROLE_STEP_IDX)
        if not isinstance(step_idx, int):
            return
        if self._state is not None and not self._state.is_step_unlocked(step_idx):
            prev_title = self._i18n.t(f"workflow.step_{step_idx:02d}_title")
            self.tree.setCurrentItem(self._items[self._current_step_idx])
            self.locked_step_clicked.emit(step_idx, prev_title)
            return
        self.step_selected.emit(step_idx)
