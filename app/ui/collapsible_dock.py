"""ArcMap-style auto-hide collapsible dock widgets and vertical side tab strips."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QEvent, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QAction,
    QColor,
    QContextMenuEvent,
    QEnterEvent,
    QFontMetrics,
    QIcon,
    QPainter,
    QPaintEvent,
    QPalette,
    QPen,
)
from PySide6.QtWidgets import (
    QDockWidget,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QSizePolicy,
    QStyle,
    QStyleOption,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from app.i18n.manager import get_i18n

from .ui_common import get_theme_palette, load_svg_icon, ui_font

_TAB_STRIP_WIDTH = 28
_TAB_ICON_SIZE = 14


def _blend_colors(base_hex: str, overlay_hex: str, ratio: float) -> QColor:
    """Linearly blend two hex colors cleanly for hover/pressed CAD states."""
    base = QColor(base_hex)
    over = QColor(overlay_hex)
    r = int(round(base.red() * (1.0 - ratio) + over.red() * ratio))
    g = int(round(base.green() * (1.0 - ratio) + over.green() * ratio))
    b = int(round(base.blue() * (1.0 - ratio) + over.blue() * ratio))
    return QColor(r, g, b)


class VerticalTabButton(QPushButton):
    """28px-wide rotated vertical tab button for collapsed left/right CAD docks."""

    def __init__(
        self,
        title: str | QWidget | None = "",
        icon_name: str = "",
        side: str = "left",
        parent: Optional[QWidget] = None,
    ) -> None:
        if isinstance(title, QWidget):
            parent = title
            title_str = ""
        else:
            title_str = "" if title is None else str(title)
        super().__init__(parent)

        self._side: str = "right" if side == "right" else "left"
        self._theme: str = "light"
        self._tab_title: str = title_str
        self._icon_name: str = icon_name
        self._icon_normal: QIcon = QIcon()
        self._icon_hover: QIcon = QIcon()

        self.setFixedWidth(_TAB_STRIP_WIDTH)
        self.setCheckable(False)
        self.setFlat(True)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        self.setContentsMargins(0, 0, 0, 0)
        if self._tab_title:
            self.setToolTip(self._tab_title)

        self._rebuild_icons()

    @property
    def side(self) -> str:
        """Return dock strip side ('left' or 'right')."""
        return self._side

    @property
    def theme(self) -> str:
        """Return active visual theme ('light' or 'dark')."""
        return self._theme

    @property
    def tab_title(self) -> str:
        """Return vertical tab title text."""
        return self._tab_title

    @property
    def tab_icon(self) -> str:
        """Return SVG icon filename assigned to this tab."""
        return self._icon_name

    def set_side(self, side: str) -> None:
        """Set side orientation ('left' reads -90 deg, 'right' reads +90 deg)."""
        resolved = "right" if side == "right" else "left"
        if resolved != self._side:
            self._side = resolved
            self.update()

    def set_theme(self, theme: str) -> None:
        """Update theme palette and re-render cached SVG icon pixmaps."""
        resolved = "dark" if theme == "dark" else "light"
        if resolved != self._theme:
            self._theme = resolved
        self._rebuild_icons()
        self.update()

    def set_tab_title(self, title: str) -> None:
        """Set vertical tab label and update geometry and tooltip."""
        self._tab_title = str(title)
        self.setToolTip(self._tab_title)
        self.updateGeometry()
        self.update()

    def setText(self, text: str) -> None:
        """Forward QPushButton.setText to vertical tab title cleanly."""
        super().setText(text)
        self.set_tab_title(text)

    def set_tab_icon(self, icon_name: str) -> None:
        """Set top SVG icon filename and refresh cached pixmaps."""
        self._icon_name = str(icon_name or "")
        self._rebuild_icons()
        self.updateGeometry()
        self.update()

    def _rebuild_icons(self) -> None:
        if not self._icon_name:
            self._icon_normal = QIcon()
            self._icon_hover = QIcon()
            return
        palette = get_theme_palette(self._theme)
        self._icon_normal = load_svg_icon(
            self._icon_name,
            theme=self._theme,
            color_override=palette["icon_stroke"],
            size=_TAB_ICON_SIZE,
        )
        self._icon_hover = load_svg_icon(
            self._icon_name,
            theme=self._theme,
            color_override=palette["text_primary"],
            size=_TAB_ICON_SIZE,
        )

    def sizeHint(self) -> QSize:
        fm = QFontMetrics(ui_font(8.8, bold=True))
        text_len = fm.horizontalAdvance(self._tab_title) if self._tab_title else 84
        icon_extra = (8 + _TAB_ICON_SIZE + 6) if self._icon_name else 8
        height = max(120, text_len + icon_extra + 24)
        return QSize(_TAB_STRIP_WIDTH, height)

    def minimumSizeHint(self) -> QSize:
        return QSize(_TAB_STRIP_WIDTH, 110)

    def enterEvent(self, event: QEnterEvent) -> None:
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event: QEvent) -> None:
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, _event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

        palette = get_theme_palette(self._theme)
        rect = self.rect()
        hovered = self.underMouse() and self.isEnabled()
        pressed = self.isDown() and self.isEnabled()

        if pressed:
            bg = _blend_colors(
                palette["panel_bg"],
                palette["accent"],
                0.30 if self._theme == "dark" else 0.20,
            )
        elif hovered:
            bg = _blend_colors(
                palette["panel_bg"],
                palette["accent"],
                0.22 if self._theme == "dark" else 0.14,
            )
        else:
            bg = QColor(palette["header_bg"])

        border_color = QColor(palette["accent"] if (hovered or pressed) else palette["border"])

        fill_rect = QRectF(0.5, 0.5, float(rect.width() - 1), float(rect.height() - 1))
        painter.setPen(QPen(border_color, 1.0))
        painter.setBrush(bg)
        painter.drawRoundedRect(fill_rect, 2.0, 2.0)

        # Crisp outer-edge CAD indicator stripe on hover/press
        if hovered or pressed:
            accent_x = 1 if self._side == "left" else rect.width() - 3
            painter.fillRect(
                QRect(accent_x, 2, 2, max(1, rect.height() - 4)),
                QColor(palette["accent"]),
            )

        # Optional 14x14 SVG icon centered horizontally at top
        active_icon = self._icon_hover if (hovered or pressed) else self._icon_normal
        has_icon = not active_icon.isNull()
        if has_icon:
            icon_x = (rect.width() - _TAB_ICON_SIZE) // 2
            icon_rect = QRect(icon_x, 8, _TAB_ICON_SIZE, _TAB_ICON_SIZE)
            active_icon.paint(painter, icon_rect, Qt.AlignmentFlag.AlignCenter)

        text_top = (8 + _TAB_ICON_SIZE + 6) if has_icon else 8
        text_bottom = rect.height() - 8
        text_avail = max(12, text_bottom - text_top)

        if self._tab_title:
            font = ui_font(8.8, bold=True)
            painter.setFont(font)
            painter.setPen(
                QColor(palette["text_primary"] if (hovered or pressed) else palette["text_secondary"])
            )
            fm = QFontMetrics(font)
            if fm.horizontalAdvance(self._tab_title) <= text_avail:
                label_text = self._tab_title
            else:
                label_text = fm.elidedText(
                    self._tab_title, Qt.TextElideMode.ElideRight, text_avail
                )

            painter.save()
            if self._side == "left":
                # -90 deg: bottom-to-top reading facing center
                painter.translate(0.0, float(rect.height()))
                painter.rotate(-90.0)
                draw_rect = QRectF(8.0, 0.0, float(text_avail), float(rect.width()))
            else:
                # +90 deg: top-to-bottom reading facing center
                painter.translate(float(rect.width()), 0.0)
                painter.rotate(90.0)
                draw_rect = QRectF(float(text_top), 0.0, float(text_avail), float(rect.width()))
            painter.drawText(draw_rect, Qt.AlignmentFlag.AlignCenter, label_text)
            painter.restore()

        painter.end()


class _CollapsibleDockTitleBar(QWidget):
    """Custom crisp CAD dock title bar with title, dock menu, float toggle, and auto-hide pin button."""

    collapse_clicked = Signal()
    float_clicked = Signal()
    dock_left_clicked = Signal()
    dock_right_clicked = Signal()
    stack_below_clicked = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("CollapsibleDockTitleBar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Minimum)
        self._theme = "light"

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 4, 4)
        layout.setSpacing(4)

        self.title_label = QLabel(self)
        self.title_label.setFont(ui_font(9.0, bold=True))
        self.title_label.setWordWrap(True)
        self.title_label.setMinimumWidth(40)
        self.title_label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum
        )
        self.title_label.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, True
        )
        layout.addWidget(self.title_label, 1)

        self.dock_menu_btn = QToolButton(self)
        self.dock_menu_btn.setProperty("dockTitleBtn", True)
        self.dock_menu_btn.setAutoRaise(True)
        self.dock_menu_btn.setFixedSize(20, 20)
        self.dock_menu_btn.setIconSize(QSize(13, 13))
        self.dock_menu_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.dock_menu_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.dock_menu_btn.setCursor(Qt.CursorShape.PointingHandCursor)

        self.dock_menu = QMenu(self.dock_menu_btn)
        self.act_dock_left = QAction(self.dock_menu)
        self.act_dock_right = QAction(self.dock_menu)
        self.act_stack_below = QAction(self.dock_menu)
        self.dock_menu.addAction(self.act_dock_left)
        self.dock_menu.addAction(self.act_dock_right)
        self.dock_menu.addAction(self.act_stack_below)
        self.dock_menu_btn.setMenu(self.dock_menu)
        layout.addWidget(self.dock_menu_btn)

        self.float_btn = QToolButton(self)
        self.float_btn.setProperty("dockTitleBtn", True)
        self.float_btn.setAutoRaise(True)
        self.float_btn.setFixedSize(20, 20)
        self.float_btn.setIconSize(QSize(13, 13))
        self.float_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.float_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        layout.addWidget(self.float_btn)

        self.pin_btn = QToolButton(self)
        self.pin_btn.setProperty("dockTitleBtn", True)
        self.pin_btn.setAutoRaise(True)
        self.pin_btn.setFixedSize(20, 20)
        self.pin_btn.setIconSize(QSize(13, 13))
        self.pin_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.pin_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        layout.addWidget(self.pin_btn)

        self.act_dock_left.triggered.connect(self.dock_left_clicked)
        self.act_dock_right.triggered.connect(self.dock_right_clicked)
        self.act_stack_below.triggered.connect(self.stack_below_clicked)
        self.float_btn.clicked.connect(self.float_clicked)
        self.pin_btn.clicked.connect(self.collapse_clicked)

    def set_title(self, title: str) -> None:
        """Update displayed dock title text."""
        self.title_label.setText(title)

    def set_theme(self, theme: str, collapsed: bool) -> None:
        """Update title bar icons and palette colors for light/dark theme."""
        self._theme = "dark" if theme == "dark" else "light"
        menu_icon_name = (
            "chevron_down_dark.svg" if self._theme == "dark" else "chevron_down.svg"
        )
        menu_icon = load_svg_icon(menu_icon_name, theme=self._theme, size=12)
        if menu_icon.isNull():
            menu_icon = load_svg_icon("chevron_down.svg", theme=self._theme, size=12)
        self.dock_menu_btn.setIcon(menu_icon)

        float_icon = load_svg_icon("reset_layout.svg", theme=self._theme, size=13)
        self.float_btn.setIcon(float_icon)

        primary_pin = "pin.svg" if collapsed else "unpin.svg"
        pin_icon = load_svg_icon(primary_pin, theme=self._theme, size=13)
        if pin_icon.isNull():
            pin_icon = load_svg_icon("chevron_right.svg", theme=self._theme, size=13)
        if pin_icon.isNull():
            pin_icon = float_icon
        self.pin_btn.setIcon(pin_icon)
        self.update()

    def set_tooltips(self, pin_tooltip: str, float_tooltip: str) -> None:
        """Apply translated tooltips to the pin and float buttons."""
        self.pin_btn.setToolTip(pin_tooltip)
        self.float_btn.setToolTip(float_tooltip)

    def set_menu_texts(
        self,
        menu_tip: str,
        left_txt: str,
        right_txt: str,
        stack_txt: str,
    ) -> None:
        """Apply translated tooltip and action labels to the dock-options menu."""
        self.dock_menu_btn.setToolTip(menu_tip)
        self.act_dock_left.setText(left_txt)
        self.act_dock_right.setText(right_txt)
        self.act_stack_below.setText(stack_txt)

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:
        """Open the dock-options menu when right-clicking the title bar."""
        self.dock_menu.exec(event.globalPos())
        event.accept()

    def paintEvent(self, _event: QPaintEvent) -> None:
        palette = get_theme_palette(self._theme)
        painter = QPainter(self)
        rect = self.rect()
        painter.fillRect(rect, QColor(palette["header_bg"]))
        painter.setPen(QColor(palette["border"]))
        painter.drawLine(0, rect.bottom(), rect.right(), rect.bottom())

        opt = QStyleOption()
        opt.initFrom(self)
        self.style().drawPrimitive(QStyle.PrimitiveElement.PE_Widget, opt, painter, self)
        painter.end()


class CollapsibleDockWidget(QDockWidget):
    """QDockWidget with ArcMap-style custom title bar and auto-hide collapse signal."""

    collapse_requested = Signal(object)
    dock_left_requested = Signal(object)
    dock_right_requested = Signal(object)
    stack_below_requested = Signal(object)

    def __init__(
        self,
        title_or_parent: str | QWidget | None = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        if isinstance(title_or_parent, str):
            super().__init__(title_or_parent, parent)
            initial_title = title_or_parent
        else:
            super().__init__(title_or_parent)
            initial_title = ""

        self._i18n = get_i18n()
        self._theme: str = "light"
        self._collapsed: bool = False
        self._dock_icon_name: str = ""

        self._title_bar = _CollapsibleDockTitleBar(self)
        self.setTitleBarWidget(self._title_bar)

        self.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea
        )
        self.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetMovable
            | QDockWidget.DockWidgetFeature.DockWidgetFloatable
            | QDockWidget.DockWidgetFeature.DockWidgetClosable
        )

        self._title_bar.collapse_clicked.connect(self._on_collapse_clicked)
        self._title_bar.float_clicked.connect(self._on_float_clicked)
        self._title_bar.dock_left_clicked.connect(
            lambda: self.dock_left_requested.emit(self)
        )
        self._title_bar.dock_right_clicked.connect(
            lambda: self.dock_right_requested.emit(self)
        )
        self._title_bar.stack_below_clicked.connect(
            lambda: self.stack_below_requested.emit(self)
        )
        self.topLevelChanged.connect(self._on_top_level_changed)

        if initial_title:
            self._title_bar.set_title(initial_title)

        self._refresh_title_bar_icons()
        self.retranslate_title_bar()

    @property
    def pin_button(self) -> QToolButton:
        """Return the title-bar pin/unpin button."""
        return self._title_bar.pin_btn

    @property
    def float_button(self) -> QToolButton:
        """Return the title-bar float/dock toggle button."""
        return self._title_bar.float_btn

    @property
    def dock_menu_button(self) -> QToolButton:
        """Return the title-bar dock-options menu button."""
        return self._title_bar.dock_menu_btn

    @property
    def dock_actions(self) -> tuple[QAction, QAction, QAction]:
        """Return (dock_left, dock_right, stack_below) QActions from the dock menu."""
        return (
            self._title_bar.act_dock_left,
            self._title_bar.act_dock_right,
            self._title_bar.act_stack_below,
        )

    @property
    def dock_icon_name(self) -> str:
        """Return SVG icon filename associated with this dock for side tab strips."""
        return self._dock_icon_name

    def set_dock_icon(self, icon_name: str) -> None:
        """Store SVG icon filename used when this dock collapses onto a SideTabStrip."""
        self._dock_icon_name = str(icon_name or "")

    def setWindowTitle(self, title: str) -> None:
        """Synchronize both QDockWidget windowTitle and custom title bar label."""
        self._title_bar.set_title(title)
        super().setWindowTitle(title)

    def is_collapsed(self) -> bool:
        """Return True when this dock is currently collapsed into a SideTabStrip."""
        return self._collapsed

    def set_collapsed_state(self, collapsed: bool) -> None:
        """Update collapsed state and refresh title-bar pin icon and tooltip."""
        self._collapsed = bool(collapsed)
        self._refresh_title_bar_icons()
        self.retranslate_title_bar()

    def set_theme(self, theme: str) -> None:
        """Apply light or dark CAD theme styling to the dock title bar."""
        self._theme = "dark" if theme == "dark" else "light"
        self._refresh_title_bar_icons()

    def retranslate_title_bar(self) -> None:
        """Apply i18n tooltips to title-bar pin, float, and dock-options controls cleanly."""
        pin_key = "docks.pin_tooltip" if self._collapsed else "docks.unpin_tooltip"
        pin_tip = self._i18n.t(pin_key)
        if pin_tip == pin_key:
            pin_tip = self._i18n.t("menu.view_docks")

        float_tip = self._i18n.t("docks.float_tooltip")
        if float_tip == "docks.float_tooltip":
            float_tip = self._i18n.t("menu.view_docks")

        self._title_bar.set_tooltips(pin_tip, float_tip)

        menu_tip = self._i18n.t("docks.dock_menu_tooltip")
        if menu_tip == "docks.dock_menu_tooltip":
            menu_tip = self._i18n.t("menu.view_docks")
        left_txt = self._i18n.t("docks.dock_left")
        right_txt = self._i18n.t("docks.dock_right")
        stack_txt = self._i18n.t("docks.stack_below_other")
        self._title_bar.set_menu_texts(menu_tip, left_txt, right_txt, stack_txt)

    def _refresh_title_bar_icons(self) -> None:
        self._title_bar.set_theme(self._theme, self._collapsed)

    def _on_collapse_clicked(self) -> None:
        self.collapse_requested.emit(self)

    def _on_float_clicked(self) -> None:
        self.setFloating(not self.isFloating())

    def _on_top_level_changed(self, floating: bool) -> None:
        if floating and self._collapsed:
            self._collapsed = False
        self._refresh_title_bar_icons()
        self.retranslate_title_bar()


class SideTabStrip(QFrame):
    """28px vertical side rail displaying vertical tab buttons for collapsed docks."""

    tab_clicked = Signal(object)

    def __init__(self, side: str = "left", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._side: str = "right" if side == "right" else "left"
        self._theme: str = "light"
        self.setObjectName(
            "RightSideTabStrip" if self._side == "right" else "LeftSideTabStrip"
        )
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setFixedWidth(_TAB_STRIP_WIDTH)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 6, 0, 6)
        self._layout.setSpacing(6)
        self._layout.addStretch(1)

        self._tabs: dict[CollapsibleDockWidget, VerticalTabButton] = {}
        self._apply_strip_palette()
        self.setVisible(False)

    @property
    def side(self) -> str:
        """Return side orientation ('left' or 'right')."""
        return self._side

    def tab_button(self, dock: CollapsibleDockWidget) -> Optional[VerticalTabButton]:
        """Return registered VerticalTabButton for dock, or None if absent."""
        return self._tabs.get(dock)

    def register_tab(
        self,
        dock: CollapsibleDockWidget,
        button: Optional[VerticalTabButton] = None,
    ) -> VerticalTabButton:
        """Register a dock and its VerticalTabButton on this side strip."""
        if button is None:
            button = VerticalTabButton(
                title=dock.windowTitle(),
                icon_name=dock.dock_icon_name,
                side=self._side,
                parent=self,
            )

        existing = self._tabs.get(dock)
        if existing is not None and existing is not button:
            self.unregister_tab(dock)

        button.set_side(self._side)
        button.set_theme(self._theme)
        if dock.windowTitle():
            button.set_tab_title(dock.windowTitle())
        if dock.dock_icon_name and not button.tab_icon:
            button.set_tab_icon(dock.dock_icon_name)

        if existing is not button:
            insert_idx = max(0, self._layout.count() - 1)
            self._layout.insertWidget(insert_idx, button)
            self._tabs[dock] = button
            dock.windowTitleChanged.connect(button.set_tab_title)
            button.clicked.connect(lambda _checked=False, d=dock: self.tab_clicked.emit(d))

        button.setVisible(dock.is_collapsed())
        self.refresh_visibility()
        return button

    def unregister_tab(self, dock: CollapsibleDockWidget) -> Optional[VerticalTabButton]:
        """Detach a dock's vertical tab button from this strip and update visibility."""
        btn = self._tabs.pop(dock, None)
        if btn is not None:
            try:
                dock.windowTitleChanged.disconnect(btn.set_tab_title)
            except (RuntimeError, TypeError):
                pass
            self._layout.removeWidget(btn)
            btn.setVisible(False)
            btn.setParent(None)
            self.refresh_visibility()
        return btn

    def set_tab_visible(self, dock: CollapsibleDockWidget, visible: bool) -> None:
        """Show or hide the tab for dock and collapse the strip to 0px when empty."""
        btn = self._tabs.get(dock)
        if btn is None:
            return
        btn.setVisible(bool(visible))
        self.refresh_visibility()

    def refresh_visibility(self) -> None:
        """Show this strip only when at least one registered tab is visible."""
        any_visible = any(not btn.isHidden() for btn in self._tabs.values())
        self.setVisible(any_visible)

    def set_theme(self, theme: str) -> None:
        """Propagate active theme to the strip surface and all tab buttons."""
        self._theme = "dark" if theme == "dark" else "light"
        self._apply_strip_palette()
        for btn in self._tabs.values():
            btn.set_theme(self._theme)

    def _apply_strip_palette(self) -> None:
        palette = get_theme_palette(self._theme)
        pal = self.palette()
        pal.setColor(QPalette.ColorRole.Window, QColor(palette["app_bg"]))
        self.setPalette(pal)
        self.setAutoFillBackground(True)
