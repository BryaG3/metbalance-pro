"""Contextual CAD inspector dock adapting to Project, Stream/Terminal, and Process Stage selection."""

from __future__ import annotations

import math
from typing import Optional

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.i18n.manager import get_i18n
from core.etl import build_amira_p754_presets, build_default_cleaning_plan
from core.models import (
    ColumnMappingSuggestion,
    GradeUnit,
    MassUnit,
    NodeSpec,
    SpeciesSpec,
    StreamRole,
    StreamSpec,
    UncertaintyMode,
    UncertaintyScale,
    UncertaintySpec,
    UnitType,
    make_assay_var_key,
    make_mass_var_key,
)
from core.observability import analyze_observability

from .collapsible_dock import CollapsibleDockWidget
from .ui_common import (
    FlowLayout,
    InspectorPayload,
    ProjectState,
    apply_status_role,
    configure_dense_table,
    get_theme_palette,
    load_svg_icon,
    make_numeric_item,
    make_text_item,
    monospace_font,
    set_combo_by_data,
    ui_font,
)

_TAILINGS_NAME_TOKENS: tuple[str, ...] = ("tail", "relav", "rlv", "cola")


def _invalidate_layout_chain(widget: Optional[QWidget]) -> None:
    """Propagate layout invalidation and updateGeometry up to the scroll body."""
    cur = widget
    scroll_body: Optional[QWidget] = None
    scroll_area: Optional[QScrollArea] = None
    while cur is not None:
        if isinstance(cur, QScrollArea):
            scroll_area = cur
            break
        if cur.objectName() == "qt_scrollarea_viewport":
            parent = cur.parentWidget()
            if isinstance(parent, QScrollArea):
                scroll_area = parent
            break
        scroll_body = cur
        lay = cur.layout()
        if lay is not None:
            lay.invalidate()
        cur.updateGeometry()
        cur = cur.parentWidget()

    if (
        scroll_area is not None
        and scroll_body is not None
        and scroll_area.isVisible()
        and scroll_body.isVisible()
    ):
        vp = scroll_area.viewport()
        target_w = max(vp.width(), scroll_body.minimumSizeHint().width())
        target_h = (
            scroll_body.heightForWidth(target_w)
            if scroll_body.hasHeightForWidth()
            else scroll_body.sizeHint().height()
        )
        target_h = max(vp.height(), target_h)
        if scroll_body.width() != target_w or scroll_body.height() != target_h:
            scroll_body.resize(target_w, target_h)
        lay = scroll_body.layout()
        if lay is not None:
            lay.activate()


class _ShrinkableScrollArea(QScrollArea):
    """QScrollArea whose minimumSizeHint never forces its parent dock wider than 210px."""

    def minimumSizeHint(self) -> QSize:
        return QSize(180, 120)


class _AdaptiveStackedWidget(QStackedWidget):
    """QStackedWidget sized to its active page so QScrollArea shows no dead whitespace."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        self.currentChanged.connect(self._on_current_changed)

    def _on_current_changed(self, _idx: int) -> None:
        _invalidate_layout_chain(self)

    def hasHeightForWidth(self) -> bool:
        cur = self.currentWidget()
        if cur is not None:
            return cur.hasHeightForWidth()
        return super().hasHeightForWidth()

    def heightForWidth(self, width: int) -> int:
        cur = self.currentWidget()
        if cur is not None and cur.hasHeightForWidth():
            return cur.heightForWidth(width)
        return super().heightForWidth(width)

    def sizeHint(self) -> QSize:
        cur = self.currentWidget()
        return cur.sizeHint() if cur is not None else super().sizeHint()

    def minimumSizeHint(self) -> QSize:
        cur = self.currentWidget()
        if cur is None:
            return QSize(160, 120)
        return cur.minimumSizeHint()


class _CollapsibleSection(QWidget):
    """Flat CAD accordion section with chevron toggle button and collapsible body."""

    def __init__(
        self,
        expanded: bool = True,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._theme = "light"
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(3)

        self.header_btn = QPushButton(self)
        self.header_btn.setProperty("sectionHeader", True)
        self.header_btn.setCheckable(True)
        self.header_btn.setChecked(expanded)
        self.header_btn.setIconSize(QSize(12, 12))
        self.header_btn.setSizePolicy(
            QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed
        )
        self.header_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        root.addWidget(self.header_btn)

        self.body_frame = QFrame(self)
        self.body_frame.setProperty("card", True)
        self.body_frame.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        self._body_layout = QVBoxLayout(self.body_frame)
        self._body_layout.setContentsMargins(6, 6, 6, 6)
        self._body_layout.setSpacing(5)
        self.body_frame.setVisible(expanded)
        root.addWidget(self.body_frame)

        self.header_btn.toggled.connect(self._on_toggled)
        self._refresh_chevron()

    def content_layout(self) -> QVBoxLayout:
        """Return inner vertical layout for this collapsible section."""
        return self._body_layout

    def set_title(self, title: str) -> None:
        """Set header button text."""
        self.header_btn.setText(title)

    def is_expanded(self) -> bool:
        """Return True when section body is expanded."""
        return self.header_btn.isChecked()

    def set_expanded(self, expanded: bool) -> None:
        """Expand or collapse section body programmatically."""
        self.header_btn.setChecked(bool(expanded))

    def set_theme(self, theme: str) -> None:
        """Update chevron SVG icon color for active theme."""
        self._theme = "dark" if theme == "dark" else "light"
        self._refresh_chevron()

    def _on_toggled(self, checked: bool) -> None:
        self.body_frame.setVisible(checked)
        self._refresh_chevron()
        _invalidate_layout_chain(self)

    def _refresh_chevron(self) -> None:
        icon_name = "chevron_down.svg" if self.header_btn.isChecked() else "chevron_right.svg"
        self.header_btn.setIcon(load_svg_icon(icon_name, theme=self._theme, size=12))


class _VerticalFormLayout(QVBoxLayout):
    """Vertical label-above-field layout with accurate word-wrap heightForWidth support."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setContentsMargins(0, 0, 0, 0)
        self.setSpacing(6)

    def addRow(self, label: QWidget, field: QWidget) -> None:
        """Stack a form label directly above its field widget."""
        row_box = QVBoxLayout()
        row_box.setContentsMargins(0, 0, 0, 0)
        row_box.setSpacing(3)
        row_box.addWidget(label)
        row_box.addWidget(field)
        self.addLayout(row_box)


class ContextualInspectorDock(CollapsibleDockWidget):
    """Right/left collapsible CAD inspector adapting to Project, Stream, or Stage context."""

    circuit_edited = Signal()
    stream_renamed = Signal(str, str)
    stage_renamed = Signal(str, str)
    manual_data_changed = Signal()
    uncertainty_changed = Signal()
    import_data_requested = Signal()
    open_etl_requested = Signal()
    open_full_table_requested = Signal()
    outlier_compare_requested = Signal(list)
    stream_jump_requested = Signal(str)
    run_diagnostics_requested = Signal()
    run_reconcile_requested = Signal()

    def __init__(
        self,
        state: ProjectState,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._state = state
        self._i18n = get_i18n()
        self._syncing = False
        self._mode: str = "project"
        self._payload: Optional[InspectorPayload] = None

        self.selected_stream_id: Optional[str] = None
        self.selected_node_id: Optional[str] = None

        self.manual_species_rows: dict[str, tuple[QCheckBox, QDoubleSpinBox]] = {}
        self.historical_mapping_combos: dict[Optional[str], QComboBox] = {}
        self.uncertainty_spins: dict[str, QDoubleSpinBox] = {}

        self.setObjectName("InspectorDock")
        self.set_dock_icon("gear.svg")
        self.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea
        )
        self.setMinimumWidth(210)
        self.setMaximumWidth(16777215)
        self.setMinimumHeight(120)

        self._build_ui()
        self.retranslate_ui()
        self.show_project_context()

    def sizeHint(self) -> QSize:
        return QSize(320, 650)

    def minimumSizeHint(self) -> QSize:
        return QSize(210, 120)

    def current_context_mode(self) -> str:
        """Return active contextual mode: 'project', 'stream', or 'stage'."""
        return self._mode

    # ------------------------------------------------------------------
    # UI Construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        container = QWidget(self)
        container.setAutoFillBackground(True)
        container.setMinimumWidth(180)
        container.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        root = QVBoxLayout(container)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(6)

        # Context header badge + identifier subtitle (stacked vertically to avoid overlap)
        header_card = QFrame(container)
        header_card.setProperty("card", True)
        header_card.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum
        )
        hdr_layout = QVBoxLayout(header_card)
        hdr_layout.setContentsMargins(8, 5, 8, 5)
        hdr_layout.setSpacing(2)

        self.mode_badge = QLabel(header_card)
        self.mode_badge.setFont(ui_font(9.2, bold=True))
        self.mode_badge.setWordWrap(True)
        self.mode_badge.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum
        )
        self.mode_badge.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        hdr_layout.addWidget(self.mode_badge)

        self.context_id_label = QLabel(header_card)
        self.context_id_label.setFont(monospace_font(8.8, bold=True))
        self.context_id_label.setProperty("role", "secondary")
        self.context_id_label.setWordWrap(True)
        self.context_id_label.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum
        )
        self.context_id_label.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        hdr_layout.addWidget(self.context_id_label)
        root.addWidget(header_card)

        # Workflow blocked banner (kept visible whenever a step block reason is active)
        self.blocked_banner = QLabel(container)
        self.blocked_banner.setWordWrap(True)
        self.blocked_banner.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum
        )
        self.blocked_banner.setContentsMargins(6, 5, 6, 5)
        apply_status_role(self.blocked_banner, "error")
        self.blocked_banner.setVisible(False)
        root.addWidget(self.blocked_banner)

        # Scrollable contextual pages
        self.scroll_area = _ShrinkableScrollArea(container)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_area.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self.scroll_area.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self.scroll_area.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )

        scroll_body = QWidget(self.scroll_area)
        scroll_body.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        scroll_layout = QVBoxLayout(scroll_body)
        scroll_layout.setContentsMargins(0, 0, 2, 0)
        scroll_layout.setSpacing(6)

        self.context_stack = _AdaptiveStackedWidget(scroll_body)
        self._project_page = self._build_project_page()
        self._stream_page = self._build_stream_page()
        self._stage_page = self._build_stage_page()
        self.context_stack.addWidget(self._project_page)
        self.context_stack.addWidget(self._stream_page)
        self.context_stack.addWidget(self._stage_page)
        scroll_layout.addWidget(self.context_stack)

        # Collapsible backwards-compatible Properties & Alerts drawer
        self.alerts_drawer = _CollapsibleSection(expanded=False, parent=scroll_body)
        drawer_layout = self.alerts_drawer.content_layout()

        self.payload_title_label = QLabel(self.alerts_drawer)
        self.payload_title_label.setFont(ui_font(8.8, bold=True))
        self._configure_form_label(self.payload_title_label, word_wrap=True)
        drawer_layout.addWidget(self.payload_title_label)

        self.props_table = QTableWidget(0, 2, self.alerts_drawer)
        self.props_table.setObjectName("InspectorPropertiesTable")
        configure_dense_table(self.props_table, stretch_last=True, editable=False)
        self.props_table.setHorizontalHeaderLabels(["", ""])
        self.props_table.setMinimumWidth(120)
        self.props_table.setMinimumHeight(80)
        self.props_table.setMaximumHeight(160)
        self.props_table.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        drawer_layout.addWidget(self.props_table)

        self.alerts_list = QListWidget(self.alerts_drawer)
        self.alerts_list.setObjectName("InspectorAlertsList")
        self.alerts_list.setWordWrap(True)
        self.alerts_list.setMinimumWidth(120)
        self.alerts_list.setMinimumHeight(64)
        self.alerts_list.setMaximumHeight(150)
        self.alerts_list.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        drawer_layout.addWidget(self.alerts_list)

        scroll_layout.addWidget(self.alerts_drawer)
        scroll_layout.addStretch(1)

        self.scroll_area.setWidget(scroll_body)
        root.addWidget(self.scroll_area, 1)

        self.setWidget(container)
        self._sections: list[_CollapsibleSection] = [
            self.proj_meta_section,
            self.proj_species_section,
            self.stream_id_section,
            self.stream_data_section,
            self.stream_sigma_section,
            self.stream_rec_section,
            self.stage_id_section,
            self.stage_conn_section,
            self.stage_balance_section,
            self.stage_kpi_section,
            self.alerts_drawer,
        ]

    @staticmethod
    def _configure_form_label(lbl: QLabel, *, word_wrap: bool = True) -> None:
        lbl.setWordWrap(word_wrap)
        lbl.setSizePolicy(
            QSizePolicy.Policy.Preferred,
            QSizePolicy.Policy.Minimum if word_wrap else QSizePolicy.Policy.Preferred,
        )

    @staticmethod
    def _configure_compact_edit(edit: QLineEdit) -> None:
        edit.setMinimumWidth(120)
        edit.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    @staticmethod
    def _configure_compact_combo(combo: QComboBox) -> None:
        combo.setMinimumWidth(120)
        combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        combo.setMinimumContentsLength(8)

    @staticmethod
    def _configure_compact_button(btn: QPushButton) -> None:
        btn.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)

    @staticmethod
    def _configure_compact_table(table: QTableWidget) -> None:
        table.setMinimumWidth(120)
        table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    @staticmethod
    def _configure_numeric_spin(
        parent: QWidget,
        *,
        minimum: float = 0.0,
        maximum: float = 1e7,
        decimals: int = 3,
        step: float = 1.0,
    ) -> QDoubleSpinBox:
        spin = QDoubleSpinBox(parent)
        spin.setRange(minimum, maximum)
        spin.setDecimals(decimals)
        spin.setSingleStep(step)
        spin.setFont(monospace_font(9.0))
        spin.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        spin.setMinimumWidth(90)
        spin.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        return spin

    @staticmethod
    def _make_vertical_form() -> _VerticalFormLayout:
        """Create a stacked label-above-field form layout suitable for narrow dock widths."""
        return _VerticalFormLayout()

    def _build_project_page(self) -> QWidget:
        page = QWidget(self.context_stack)
        page.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        # Project Metadata & Summary Section
        self.proj_meta_section = _CollapsibleSection(expanded=True, parent=page)
        meta_body = self.proj_meta_section.content_layout()

        form = self._make_vertical_form()

        self.lbl_project_name = QLabel(self.proj_meta_section)
        self._configure_form_label(self.lbl_project_name, word_wrap=True)
        self.project_name_edit = QLineEdit(self.proj_meta_section)
        self._configure_compact_edit(self.project_name_edit)

        self.lbl_mass_unit = QLabel(self.proj_meta_section)
        self._configure_form_label(self.lbl_mass_unit, word_wrap=True)
        self.mass_unit_combo = QComboBox(self.proj_meta_section)
        self._configure_compact_combo(self.mass_unit_combo)

        form.addRow(self.lbl_project_name, self.project_name_edit)
        form.addRow(self.lbl_mass_unit, self.mass_unit_combo)
        meta_body.addLayout(form)

        self.project_summary_label = QLabel(self.proj_meta_section)
        self.project_summary_label.setFont(monospace_font(8.5, bold=True))
        self._configure_form_label(self.project_summary_label, word_wrap=True)
        self.project_summary_label.setContentsMargins(6, 4, 6, 4)
        apply_status_role(self.project_summary_label, "pending")
        meta_body.addWidget(self.project_summary_label)

        layout.addWidget(self.proj_meta_section)

        # Compact Editable Species Section
        self.proj_species_section = _CollapsibleSection(expanded=True, parent=page)
        sp_body = self.proj_species_section.content_layout()

        self.species_table = QTableWidget(0, 3, self.proj_species_section)
        self.species_table.setObjectName("InspectorSpeciesTable")
        configure_dense_table(
            self.species_table, stretch_last=True, editable=True, has_embedded_widgets=True
        )
        self._configure_compact_table(self.species_table)
        self.species_table.setMinimumHeight(115)
        self.species_table.setMaximumHeight(210)
        sp_body.addWidget(self.species_table)

        sp_btns_host = QWidget(self.proj_species_section)
        sp_btns_host.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        sp_btns = FlowLayout(sp_btns_host, margin=0, h_spacing=6, v_spacing=6)
        self.add_species_btn = QPushButton(sp_btns_host)
        self.add_species_btn.setProperty("compact", True)
        self._configure_compact_button(self.add_species_btn)
        self.remove_species_btn = QPushButton(sp_btns_host)
        self.remove_species_btn.setProperty("compact", True)
        self.remove_species_btn.setProperty("danger", True)
        self._configure_compact_button(self.remove_species_btn)
        sp_btns.addWidget(self.add_species_btn)
        sp_btns.addWidget(self.remove_species_btn)
        sp_body.addWidget(sp_btns_host)

        layout.addWidget(self.proj_species_section)

        # Quick Actions Bar
        actions_card = QFrame(page)
        actions_card.setProperty("card", True)
        actions_card.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        act_layout = FlowLayout(actions_card, margin=6, h_spacing=6, v_spacing=6)

        self.btn_proj_open_etl = QPushButton(actions_card)
        self.btn_proj_open_etl.setProperty("compact", True)
        self.btn_proj_amira = QPushButton(actions_card)
        self.btn_proj_amira.setProperty("compact", True)
        self.btn_proj_diag = QPushButton(actions_card)
        self.btn_proj_diag.setProperty("compact", True)
        self.btn_proj_reconcile = QPushButton(actions_card)
        self.btn_proj_reconcile.setProperty("compact", True)
        self.btn_proj_reconcile.setProperty("accent", True)

        for btn in (
            self.btn_proj_open_etl,
            self.btn_proj_amira,
            self.btn_proj_diag,
            self.btn_proj_reconcile,
        ):
            self._configure_compact_button(btn)
            act_layout.addWidget(btn)
        layout.addWidget(actions_card)

        # Connect project signals
        self.project_name_edit.textChanged.connect(self._on_project_name_changed)
        self.project_name_edit.editingFinished.connect(self._on_project_name_committed)
        self.mass_unit_combo.currentIndexChanged.connect(self._on_mass_unit_changed)
        self.species_table.itemChanged.connect(self._on_species_table_changed)
        self.add_species_btn.clicked.connect(self._on_add_species)
        self.remove_species_btn.clicked.connect(self._on_remove_species)
        self.btn_proj_open_etl.clicked.connect(self.open_etl_requested.emit)
        self.btn_proj_amira.clicked.connect(self._on_apply_project_amira)
        self.btn_proj_diag.clicked.connect(self.run_diagnostics_requested.emit)
        self.btn_proj_reconcile.clicked.connect(self.run_reconcile_requested.emit)

        return page

    def _build_stream_page(self) -> QWidget:
        page = QWidget(self.context_stack)
        page.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        # a) Identification
        self.stream_id_section = _CollapsibleSection(expanded=True, parent=page)
        id_body = self.stream_id_section.content_layout()
        id_form = self._make_vertical_form()

        self.lbl_stream_name = QLabel(self.stream_id_section)
        self._configure_form_label(self.lbl_stream_name, word_wrap=True)
        self.stream_name_edit = QLineEdit(self.stream_id_section)
        self._configure_compact_edit(self.stream_name_edit)

        self.lbl_stream_role = QLabel(self.stream_id_section)
        self._configure_form_label(self.lbl_stream_role, word_wrap=True)
        self.stream_role_combo = QComboBox(self.stream_id_section)
        self._configure_compact_combo(self.stream_role_combo)

        self.lbl_stream_conn = QLabel(self.stream_id_section)
        self._configure_form_label(self.lbl_stream_conn, word_wrap=True)
        self.stream_conn_value = QLabel(self.stream_id_section)
        self.stream_conn_value.setFont(monospace_font(8.5))
        self._configure_form_label(self.stream_conn_value, word_wrap=True)

        id_form.addRow(self.lbl_stream_name, self.stream_name_edit)
        id_form.addRow(self.lbl_stream_role, self.stream_role_combo)
        id_form.addRow(self.lbl_stream_conn, self.stream_conn_value)
        id_body.addLayout(id_form)
        layout.addWidget(self.stream_id_section)

        # b) Data Entry (Manual vs Historical Excel/CSV)
        self.stream_data_section = _CollapsibleSection(expanded=True, parent=page)
        data_body = self.stream_data_section.content_layout()

        mode_toggle_host = QWidget(self.stream_data_section)
        mode_toggle_host.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        toggle_flow = FlowLayout(mode_toggle_host, margin=0, h_spacing=6, v_spacing=6)
        self.btn_mode_manual = QPushButton(mode_toggle_host)
        self.btn_mode_manual.setCheckable(True)
        self.btn_mode_manual.setChecked(True)
        self.btn_mode_manual.setProperty("compact", True)
        self._configure_compact_button(self.btn_mode_manual)
        self.btn_mode_historical = QPushButton(mode_toggle_host)
        self.btn_mode_historical.setCheckable(True)
        self.btn_mode_historical.setProperty("compact", True)
        self._configure_compact_button(self.btn_mode_historical)

        self._data_mode_group = QButtonGroup(self)
        self._data_mode_group.setExclusive(True)
        self._data_mode_group.addButton(self.btn_mode_manual, 0)
        self._data_mode_group.addButton(self.btn_mode_historical, 1)
        self._sync_data_mode_buttons(0)
        toggle_flow.addWidget(self.btn_mode_manual)
        toggle_flow.addWidget(self.btn_mode_historical)
        data_body.addWidget(mode_toggle_host)

        self.data_entry_stack = _AdaptiveStackedWidget(self.stream_data_section)

        # Manual sub-page (two-line variable blocks prevent narrow horizontal cramping)
        manual_widget = QWidget(self.data_entry_stack)
        manual_widget.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        manual_v = QVBoxLayout(manual_widget)
        manual_v.setContentsMargins(0, 4, 0, 0)
        manual_v.setSpacing(8)

        self.manual_grid_widget = QWidget(manual_widget)
        self.manual_grid_widget.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        mass_block = QVBoxLayout(self.manual_grid_widget)
        mass_block.setContentsMargins(0, 0, 0, 0)
        mass_block.setSpacing(4)

        self.lbl_manual_mass = QLabel(self.manual_grid_widget)
        self._configure_form_label(self.lbl_manual_mass, word_wrap=True)
        mass_block.addWidget(self.lbl_manual_mass)

        mass_row = QHBoxLayout()
        mass_row.setContentsMargins(0, 0, 0, 0)
        mass_row.setSpacing(6)
        self.manual_mass_chk = QCheckBox(self.manual_grid_widget)
        self.manual_mass_chk.setSizePolicy(
            QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed
        )
        self.manual_mass_spin = self._configure_numeric_spin(
            self.manual_grid_widget, minimum=0.0, maximum=1e7, decimals=3, step=1.0
        )
        mass_row.addWidget(self.manual_mass_chk)
        mass_row.addWidget(self.manual_mass_spin, 1)
        mass_block.addLayout(mass_row)

        self.manual_species_container = QWidget(manual_widget)
        self.manual_species_container.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        self.manual_species_layout = QVBoxLayout(self.manual_species_container)
        self.manual_species_layout.setContentsMargins(0, 0, 0, 0)
        self.manual_species_layout.setSpacing(8)

        manual_v.addWidget(self.manual_grid_widget)
        manual_v.addWidget(self.manual_species_container)
        self.data_entry_stack.addWidget(manual_widget)

        # Historical sub-page
        hist_widget = QWidget(self.data_entry_stack)
        hist_widget.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        hist_v = QVBoxLayout(hist_widget)
        hist_v.setContentsMargins(0, 4, 0, 0)
        hist_v.setSpacing(6)

        self.btn_stream_import = QPushButton(hist_widget)
        self.btn_stream_import.setProperty("accent", True)
        self.btn_stream_import.setProperty("compact", True)
        self._configure_compact_button(self.btn_stream_import)
        hist_v.addWidget(self.btn_stream_import)

        self.hist_mapping_panel = QWidget(hist_widget)
        self.hist_mapping_panel.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        hist_map_v = QVBoxLayout(self.hist_mapping_panel)
        hist_map_v.setContentsMargins(0, 0, 0, 0)
        hist_map_v.setSpacing(6)

        self.hist_mapping_form = self._make_vertical_form()
        hist_map_v.addLayout(self.hist_mapping_form)

        hist_btns_host = QWidget(self.hist_mapping_panel)
        hist_btns_host.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        hist_btns = FlowLayout(hist_btns_host, margin=0, h_spacing=6, v_spacing=6)
        self.btn_stream_open_etl = QPushButton(hist_btns_host)
        self.btn_stream_open_etl.setProperty("compact", True)
        self.btn_stream_open_etl.setProperty("accent", True)
        self._configure_compact_button(self.btn_stream_open_etl)
        self.btn_stream_open_table = QPushButton(hist_btns_host)
        self.btn_stream_open_table.setProperty("compact", True)
        self._configure_compact_button(self.btn_stream_open_table)
        hist_btns.addWidget(self.btn_stream_open_etl)
        hist_btns.addWidget(self.btn_stream_open_table)
        hist_map_v.addWidget(hist_btns_host)

        hist_v.addWidget(self.hist_mapping_panel)
        self.data_entry_stack.addWidget(hist_widget)
        data_body.addWidget(self.data_entry_stack)
        layout.addWidget(self.stream_data_section)

        # c) Uncertainty (sigma)
        self.stream_sigma_section = _CollapsibleSection(expanded=True, parent=page)
        sigma_body = self.stream_sigma_section.content_layout()

        sigma_top_host = QWidget(self.stream_sigma_section)
        sigma_top_host.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        sigma_top = FlowLayout(sigma_top_host, margin=0, h_spacing=6, v_spacing=6)
        self.btn_stream_amira = QPushButton(sigma_top_host)
        self.btn_stream_amira.setProperty("compact", True)
        self._configure_compact_button(self.btn_stream_amira)
        sigma_top.addWidget(self.btn_stream_amira)
        sigma_body.addWidget(sigma_top_host)

        self.sigma_grid_container = QWidget(self.stream_sigma_section)
        self.sigma_grid_container.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        self.sigma_form = self._make_vertical_form()
        self.sigma_grid_container.setLayout(self.sigma_form)
        sigma_body.addWidget(self.sigma_grid_container)
        layout.addWidget(self.stream_sigma_section)

        # d) Reconciliation Results & Outlier Compare
        self.stream_rec_section = _CollapsibleSection(expanded=True, parent=page)
        rec_body = self.stream_rec_section.content_layout()

        self.stream_no_rec_label = QLabel(self.stream_rec_section)
        self.stream_no_rec_label.setProperty("role", "secondary")
        self._configure_form_label(self.stream_no_rec_label, word_wrap=True)
        rec_body.addWidget(self.stream_no_rec_label)

        self.stream_rec_table = QTableWidget(0, 5, self.stream_rec_section)
        self.stream_rec_table.setObjectName("InspectorStreamRecTable")
        configure_dense_table(self.stream_rec_table, stretch_last=True, editable=False)
        self._configure_compact_table(self.stream_rec_table)
        self.stream_rec_table.setMinimumHeight(88)
        self.stream_rec_table.setMaximumHeight(190)
        rec_body.addWidget(self.stream_rec_table)

        self.btn_outlier_compare = QPushButton(self.stream_rec_section)
        self.btn_outlier_compare.setProperty("compact", True)
        self._configure_compact_button(self.btn_outlier_compare)
        self.btn_outlier_compare.setEnabled(False)
        rec_body.addWidget(self.btn_outlier_compare)
        layout.addWidget(self.stream_rec_section)

        # Connect stream signals
        self.stream_name_edit.textChanged.connect(self._on_stream_name_changed)
        self.stream_role_combo.currentIndexChanged.connect(self._on_stream_role_changed)
        self._data_mode_group.idClicked.connect(self._on_data_mode_switched)
        self.data_entry_stack.currentChanged.connect(
            lambda _i: self.context_stack.updateGeometry()
        )
        self.manual_mass_chk.toggled.connect(self._on_manual_mass_toggled)
        self.manual_mass_spin.valueChanged.connect(self._on_manual_mass_value_changed)
        self.btn_stream_import.clicked.connect(self.import_data_requested.emit)
        self.btn_stream_open_etl.clicked.connect(self.open_etl_requested.emit)
        self.btn_stream_open_table.clicked.connect(self.open_full_table_requested.emit)
        self.btn_stream_amira.clicked.connect(self._on_apply_stream_amira)
        self.btn_outlier_compare.clicked.connect(self._on_outlier_compare_clicked)

        return page

    def _build_stage_page(self) -> QWidget:
        page = QWidget(self.context_stack)
        page.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        # Identification
        self.stage_id_section = _CollapsibleSection(expanded=True, parent=page)
        id_body = self.stage_id_section.content_layout()
        form = self._make_vertical_form()

        self.lbl_stage_name = QLabel(self.stage_id_section)
        self._configure_form_label(self.lbl_stage_name, word_wrap=True)
        self.stage_name_edit = QLineEdit(self.stage_id_section)
        self._configure_compact_edit(self.stage_name_edit)

        self.lbl_stage_type = QLabel(self.stage_id_section)
        self._configure_form_label(self.lbl_stage_type, word_wrap=True)
        self.stage_type_combo = QComboBox(self.stage_id_section)
        self._configure_compact_combo(self.stage_type_combo)

        form.addRow(self.lbl_stage_name, self.stage_name_edit)
        form.addRow(self.lbl_stage_type, self.stage_type_combo)
        id_body.addLayout(form)
        layout.addWidget(self.stage_id_section)

        # Connected Streams (clickable pills)
        self.stage_conn_section = _CollapsibleSection(expanded=True, parent=page)
        conn_body = self.stage_conn_section.content_layout()

        self.lbl_inflows = QLabel(self.stage_conn_section)
        self.lbl_inflows.setFont(ui_font(8.5, bold=True))
        self.lbl_inflows.setProperty("role", "secondary")
        self._configure_form_label(self.lbl_inflows, word_wrap=True)
        conn_body.addWidget(self.lbl_inflows)

        self.inflows_host = QWidget(self.stage_conn_section)
        self.inflows_host.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        self.inflows_flow = FlowLayout(self.inflows_host, margin=0, h_spacing=6, v_spacing=6)
        conn_body.addWidget(self.inflows_host)

        self.lbl_outflows = QLabel(self.stage_conn_section)
        self.lbl_outflows.setFont(ui_font(8.5, bold=True))
        self.lbl_outflows.setProperty("role", "secondary")
        self._configure_form_label(self.lbl_outflows, word_wrap=True)
        conn_body.addWidget(self.lbl_outflows)

        self.outflows_host = QWidget(self.stage_conn_section)
        self.outflows_host.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        self.outflows_flow = FlowLayout(self.outflows_host, margin=0, h_spacing=6, v_spacing=6)
        conn_body.addWidget(self.outflows_host)
        layout.addWidget(self.stage_conn_section)

        # Nodal Balance Table
        self.stage_balance_section = _CollapsibleSection(expanded=True, parent=page)
        bal_body = self.stage_balance_section.content_layout()
        self.stage_balance_table = QTableWidget(0, 5, self.stage_balance_section)
        self.stage_balance_table.setObjectName("InspectorStageBalanceTable")
        configure_dense_table(self.stage_balance_table, stretch_last=True, editable=False)
        self._configure_compact_table(self.stage_balance_table)
        self.stage_balance_table.setMinimumHeight(96)
        self.stage_balance_table.setMaximumHeight(200)
        bal_body.addWidget(self.stage_balance_table)
        layout.addWidget(self.stage_balance_section)

        # Stage KPIs
        self.stage_kpi_section = _CollapsibleSection(expanded=True, parent=page)
        kpi_body = self.stage_kpi_section.content_layout()

        self.stage_kpi_empty_label = QLabel(self.stage_kpi_section)
        self.stage_kpi_empty_label.setProperty("role", "secondary")
        self._configure_form_label(self.stage_kpi_empty_label, word_wrap=True)
        kpi_body.addWidget(self.stage_kpi_empty_label)

        self.stage_mass_pull_label = QLabel(self.stage_kpi_section)
        self.stage_mass_pull_label.setFont(monospace_font(8.8, bold=True))
        self._configure_form_label(self.stage_mass_pull_label, word_wrap=True)
        kpi_body.addWidget(self.stage_mass_pull_label)

        self.stage_kpi_table = QTableWidget(0, 3, self.stage_kpi_section)
        self.stage_kpi_table.setObjectName("InspectorStageKPITable")
        configure_dense_table(self.stage_kpi_table, stretch_last=True, editable=False)
        self._configure_compact_table(self.stage_kpi_table)
        self.stage_kpi_table.setMinimumHeight(88)
        self.stage_kpi_table.setMaximumHeight(190)
        kpi_body.addWidget(self.stage_kpi_table)
        layout.addWidget(self.stage_kpi_section)

        # Connect stage signals
        self.stage_name_edit.textChanged.connect(self._on_stage_name_changed)
        self.stage_type_combo.currentIndexChanged.connect(self._on_stage_type_changed)

        return page

    # ------------------------------------------------------------------
    # Public Mode & Refresh API
    # ------------------------------------------------------------------

    def show_project_context(self) -> None:
        """Switch inspector to Project / Circuit Overview context."""
        self._mode = "project"
        self.selected_stream_id = None
        self.selected_node_id = None
        self.context_stack.setCurrentWidget(self._project_page)
        self._populate_project_context()
        self._update_context_header()
        _invalidate_layout_chain(self.context_stack)

    def show_stream_context(self, stream_id: str) -> None:
        """Switch inspector to Stream / Terminal context for stream_id."""
        if self._find_stream(stream_id) is None:
            self.show_project_context()
            return
        self._mode = "stream"
        self.selected_stream_id = stream_id
        self.selected_node_id = None
        self.context_stack.setCurrentWidget(self._stream_page)
        self._populate_stream_context()
        self._update_context_header()
        _invalidate_layout_chain(self.context_stack)

    def show_stage_context(self, node_id: str) -> None:
        """Switch inspector to Process Stage context for node_id."""
        if self._find_node(node_id) is None:
            self.show_project_context()
            return
        self._mode = "stage"
        self.selected_node_id = node_id
        self.selected_stream_id = None
        self.context_stack.setCurrentWidget(self._stage_page)
        self._populate_stage_context()
        self._update_context_header()
        _invalidate_layout_chain(self.context_stack)

    def refresh_from_state(self) -> None:
        """Refresh active contextual view from ProjectState without breaking focus."""
        if self._mode == "stream" and self.selected_stream_id:
            if self._find_stream(self.selected_stream_id) is not None:
                self._populate_stream_context()
                self._update_context_header()
                _invalidate_layout_chain(self.context_stack)
                return
            self.show_project_context()
            return
        if self._mode == "stage" and self.selected_node_id:
            if self._find_node(self.selected_node_id) is not None:
                self._populate_stage_context()
                self._update_context_header()
                _invalidate_layout_chain(self.context_stack)
                return
            self.show_project_context()
            return
        self._populate_project_context()
        self._update_context_header()
        _invalidate_layout_chain(self.context_stack)

    def set_theme(self, theme: str) -> None:
        """Propagate Light/Dark theme to dock title bar, section chevrons, icons, and alerts."""
        super().set_theme(theme)
        for sec in self._sections:
            sec.set_theme(self._theme)
        self._update_icons()
        self._render_payload()
        self.refresh_from_state()

    def set_payload(self, payload: Optional[InspectorPayload]) -> None:
        """Render workflow summary properties, blocked banners, and alerts in the alerts drawer."""
        self._payload = payload
        self._render_payload()

    def retranslate_ui(self) -> None:
        """Retranslate dock title, all 3 contextual modes, table headers, and payload drawer."""
        t = self._i18n.t
        self._syncing = True
        try:
            self.setWindowTitle(t("docks.inspector_title"))
            self.retranslate_title_bar()

            # Project context labels & headers
            self.proj_meta_section.set_title(t("docks.inspector_mode_project"))
            self.lbl_project_name.setText(t("step01.project_name"))
            self.lbl_mass_unit.setText(t("step01.mass_unit"))
            self.proj_species_section.set_title(t("step01.section_species"))
            self.species_table.setHorizontalHeaderLabels(
                [
                    t("step01.species_id"),
                    t("step01.species_name"),
                    t("step01.species_unit"),
                ]
            )
            self.add_species_btn.setText(t("step01.add_species_btn"))
            self.remove_species_btn.setText(t("step01.remove_species_btn"))
            self.btn_proj_open_etl.setText(t("docks.open_full_etl_btn"))
            self.btn_proj_amira.setText(t("step03.apply_amira_btn"))
            self.btn_proj_diag.setText(t("workspace.quick_diag_btn"))
            self.btn_proj_reconcile.setText(t("workspace.quick_solve_btn"))

            cur_mu = self.mass_unit_combo.currentData() or self._state.circuit.mass_unit
            self.mass_unit_combo.clear()
            for mu in MassUnit:
                self.mass_unit_combo.addItem(t(f"enums.mass_unit.{mu.value}"), mu)
            set_combo_by_data(self.mass_unit_combo, cur_mu)

            # Stream context labels & headers
            self.stream_id_section.set_title(t("docks.section_identification"))
            self.lbl_stream_name.setText(t("step01.stream_name"))
            self.lbl_stream_role.setText(t("step01.stream_role"))
            self.lbl_stream_conn.setText(t("docks.connection_label"))

            cur_role = self.stream_role_combo.currentData()
            self.stream_role_combo.clear()
            for role in StreamRole:
                self.stream_role_combo.addItem(t(f"enums.stream_role.{role.value}"), role)
            if cur_role is not None:
                set_combo_by_data(self.stream_role_combo, cur_role)

            self.stream_data_section.set_title(t("docks.section_data_entry"))
            self.btn_mode_manual.setText(t("docks.mode_manual"))
            self.btn_mode_historical.setText(t("docks.mode_historical"))
            self.manual_mass_chk.setText(t("docks.measured_chk"))
            self.btn_stream_import.setText(t("docks.import_data_btn"))
            self.btn_stream_open_etl.setText(t("docks.apply_mapping_btn"))
            self.btn_stream_open_table.setText(t("docks.open_full_table_btn"))

            self.stream_sigma_section.set_title(t("docks.section_uncertainty"))
            self.btn_stream_amira.setText(t("docks.quick_amira_preset"))

            self.stream_rec_section.set_title(t("docks.section_reconciliation"))
            self.stream_no_rec_label.setText(t("docks.no_reconciliation_yet"))
            self.stream_rec_table.setHorizontalHeaderLabels(
                [
                    t("step05.col_var"),
                    t("step05.col_measured"),
                    t("step05.col_reconciled"),
                    t("step05.col_rel_pct"),
                    t("step05.col_z_score"),
                ]
            )
            self.btn_outlier_compare.setText(t("docks.reconcile_compare_btn"))

            # Stage context labels & headers
            self.stage_id_section.set_title(t("docks.section_identification"))
            self.lbl_stage_name.setText(t("step01.node_name"))
            self.lbl_stage_type.setText(t("step01.node_type"))
            cur_ut = self.stage_type_combo.currentData()
            self.stage_type_combo.clear()
            for ut in UnitType:
                self.stage_type_combo.addItem(t(f"enums.unit_type.{ut.value}"), ut)
            if cur_ut is not None:
                set_combo_by_data(self.stage_type_combo, cur_ut)

            self.stage_conn_section.set_title(t("docks.section_connected_streams"))
            self.lbl_inflows.setText(t("docks.inflows_label"))
            self.lbl_outflows.setText(t("docks.outflows_label"))

            self.stage_balance_section.set_title(t("docks.section_stage_balance"))
            self.stage_balance_table.setHorizontalHeaderLabels(
                [
                    t("step06.col_species"),
                    t("step04.col_inflow"),
                    t("step04.col_outflow"),
                    t("step04.col_diff"),
                    t("step05.col_reconciled"),
                ]
            )

            self.stage_kpi_section.set_title(t("docks.section_stage_kpis"))
            self.stage_kpi_empty_label.setText(t("docks.no_reconciliation_yet"))
            self.stage_kpi_table.setHorizontalHeaderLabels(
                [
                    t("step06.col_stream"),
                    t("step06.col_species"),
                    t("step06.col_rec_rec"),
                ]
            )

            # Backwards-compatible properties & alerts drawer
            self.props_table.setHorizontalHeaderLabels(
                [
                    t("docks.inspector_col_property"),
                    t("docks.inspector_col_value"),
                ]
            )
        finally:
            self._syncing = False

        self._update_icons()
        self._update_context_header()
        self._render_payload()
        self.refresh_from_state()

    # ------------------------------------------------------------------
    # Internal Helpers & Lookup
    # ------------------------------------------------------------------

    def _find_stream(self, stream_id: Optional[str]) -> Optional[StreamSpec]:
        if not stream_id:
            return None
        for stream in self._state.circuit.streams:
            if stream.id == stream_id:
                return stream
        return None

    def _find_node(self, node_id: Optional[str]) -> Optional[NodeSpec]:
        if not node_id:
            return None
        for node in self._state.circuit.nodes:
            if node.id == node_id:
                return node
        return None

    def _update_icons(self) -> None:
        t = self._theme
        self.add_species_btn.setIcon(load_svg_icon("plus.svg", t, size=14))
        self.remove_species_btn.setIcon(load_svg_icon("trash.svg", t, size=14))
        self.btn_proj_open_etl.setIcon(load_svg_icon("import_table.svg", t, size=14))
        self.btn_proj_amira.setIcon(load_svg_icon("gear.svg", t, size=14))
        self.btn_proj_diag.setIcon(load_svg_icon("matrix.svg", t, size=14))
        self.btn_proj_reconcile.setIcon(
            load_svg_icon("play.svg", t, color_override="#FFFFFF", size=14)
        )
        self.btn_stream_import.setIcon(
            load_svg_icon("import_table.svg", t, color_override="#FFFFFF", size=14)
        )
        self.btn_stream_open_etl.setIcon(
            load_svg_icon("import_table.svg", t, color_override="#FFFFFF", size=14)
        )
        self.btn_stream_open_table.setIcon(load_svg_icon("matrix.svg", t, size=14))
        self.btn_stream_amira.setIcon(load_svg_icon("gear.svg", t, size=14))
        self.btn_outlier_compare.setIcon(load_svg_icon("scatter_plot.svg", t, size=14))

    def _update_context_header(self) -> None:
        t = self._i18n.t
        if self._mode == "stream" and self.selected_stream_id:
            self.mode_badge.setText(t("docks.inspector_mode_stream"))
            self.context_id_label.setText(self.selected_stream_id)
        elif self._mode == "stage" and self.selected_node_id:
            self.mode_badge.setText(t("docks.inspector_mode_stage"))
            self.context_id_label.setText(self.selected_node_id)
        else:
            self.mode_badge.setText(t("docks.inspector_mode_project"))
            self.context_id_label.setText(self._state.circuit.mass_unit.value)
        self.mode_badge.updateGeometry()
        self.context_id_label.updateGeometry()
        header_card = self.mode_badge.parentWidget()
        if header_card is not None:
            header_card.updateGeometry()
            if header_card.layout() is not None:
                header_card.layout().invalidate()

    @classmethod
    def _clear_layout_widgets(cls, layout) -> None:
        """Recursively remove, hide, and schedule deletion for all items and nested layouts."""
        while layout.count():
            item = layout.takeAt(0)
            if item is None:
                continue
            child_layout = item.layout()
            if child_layout is not None:
                cls._clear_layout_widgets(child_layout)
                child_layout.deleteLater()
            wid = item.widget()
            if wid is not None:
                wid.hide()
                wid.setParent(None)
                wid.deleteLater()

    @classmethod
    def _clear_form_layout(cls, form: _VerticalFormLayout) -> None:
        """Remove, hide, and schedule deletion for every row in a vertical form layout."""
        cls._clear_layout_widgets(form)

    @staticmethod
    def _fit_table_height(
        table: QTableWidget, row_height: int, min_h: int = 88, max_h: int = 200
    ) -> None:
        rows = max(1, table.rowCount())
        header_h = table.horizontalHeader().height() or 26
        target = header_h + rows * row_height + 6
        table.setFixedHeight(max(min_h, min(max_h, target)))

    # ------------------------------------------------------------------
    # Context 1: Project / Circuit Overview
    # ------------------------------------------------------------------

    def _populate_project_context(self) -> None:
        circuit = self._state.circuit
        self._syncing = True
        try:
            if not self.project_name_edit.hasFocus():
                self.project_name_edit.setText(circuit.name)
            set_combo_by_data(self.mass_unit_combo, circuit.mass_unit)
            self._populate_species_table()
        finally:
            self._syncing = False

        self._refresh_project_summary()

    def _refresh_project_summary(self) -> None:
        circuit = self._state.circuit
        dof_str = self._i18n.t("common.na")
        role = "pending"

        obs = self._state.observability_report
        if obs is None:
            point = self._state.current_period()
            if point is not None and circuit.nodes and circuit.streams and circuit.species:
                try:
                    obs = analyze_observability(circuit, point)
                except Exception:
                    obs = None

        if obs is not None:
            dof_str = str(obs.degrees_of_freedom)
            if not obs.can_reconcile:
                role = "error"
            elif obs.is_deterministic:
                role = "warning"
            else:
                role = "validated"

        self.project_summary_label.setText(
            self._i18n.t(
                "workspace.status_summary_fmt",
                nodes=len(circuit.nodes),
                streams=len(circuit.streams),
                species=len(circuit.species),
                dof=dof_str,
            )
        )
        apply_status_role(self.project_summary_label, role)

    def _populate_species_table(self) -> None:
        species_list = self._state.circuit.species
        for old_row in range(self.species_table.rowCount()):
            old_widget = self.species_table.cellWidget(old_row, 2)
            if old_widget is not None:
                self.species_table.removeCellWidget(old_row, 2)
                old_widget.hide()
                old_widget.setParent(None)
                old_widget.deleteLater()
        self.species_table.setRowCount(0)
        self.species_table.setRowCount(len(species_list))
        for row, sp in enumerate(species_list):
            self.species_table.setItem(row, 0, make_text_item(sp.id, editable=True, mono=True))
            self.species_table.setItem(row, 1, make_text_item(sp.name, editable=True))

            unit_combo = QComboBox(self.species_table)
            unit_combo.setMinimumWidth(56)
            unit_combo.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
            )
            unit_combo.setSizeAdjustPolicy(
                QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
            )
            unit_combo.setMinimumContentsLength(3)
            for gu in GradeUnit:
                unit_combo.addItem(self._i18n.t(f"enums.grade_unit.{gu.value}"), gu)
            set_combo_by_data(unit_combo, sp.unit)
            unit_combo.currentIndexChanged.connect(
                lambda _i, r=row, cb=unit_combo: self._on_species_unit_changed(r, cb)
            )
            self.species_table.setCellWidget(row, 2, unit_combo)

        self._fit_table_height(self.species_table, row_height=32, min_h=104, max_h=210)

    def _on_project_name_changed(self, text: str) -> None:
        if self._syncing:
            return
        cleaned = text.strip()
        if cleaned:
            self._state.circuit.name = cleaned

    def _on_project_name_committed(self) -> None:
        if self._syncing:
            return
        self.circuit_edited.emit()

    def _on_mass_unit_changed(self) -> None:
        if self._syncing:
            return
        data = self.mass_unit_combo.currentData()
        if data is not None:
            mu = MassUnit(data)
            self._state.circuit.mass_unit = mu
            self._update_context_header()
            self.circuit_edited.emit()

    def _on_species_unit_changed(self, row: int, combo: QComboBox) -> None:
        if self._syncing or row >= len(self._state.circuit.species):
            return
        data = combo.currentData()
        if data is not None:
            gu = GradeUnit(data)
            self._state.circuit.species[row].unit = gu
            self._refresh_project_summary()
            self.circuit_edited.emit()

    def _on_species_table_changed(self, item: QTableWidgetItem) -> None:
        if self._syncing:
            return
        row = item.row()
        if row < 0 or row >= len(self._state.circuit.species):
            return
        sp = self._state.circuit.species[row]
        if item.column() == 0:
            new_id = item.text().strip()
            if new_id:
                sp.id = new_id
        elif item.column() == 1:
            sp.name = item.text().strip() or sp.id
        self._refresh_project_summary()
        self.circuit_edited.emit()

    def _on_add_species(self) -> None:
        existing = {s.id for s in self._state.circuit.species}
        idx = len(self._state.circuit.species) + 1
        code = f"E{idx}"
        while code in existing:
            idx += 1
            code = f"E{idx}"
        self._state.circuit.species.append(
            SpeciesSpec(id=code, name=code, unit=GradeUnit.PERCENT)
        )
        self._syncing = True
        try:
            self._populate_species_table()
        finally:
            self._syncing = False
        self._refresh_project_summary()
        self.circuit_edited.emit()

    def _on_remove_species(self) -> None:
        if len(self._state.circuit.species) <= 1:
            return
        row = self.species_table.currentRow()
        if row < 0 or row >= len(self._state.circuit.species):
            row = len(self._state.circuit.species) - 1
        self._state.circuit.species.pop(row)
        self._syncing = True
        try:
            self._populate_species_table()
        finally:
            self._syncing = False
        self._refresh_project_summary()
        self.circuit_edited.emit()

    def _on_apply_project_amira(self) -> None:
        self._state.uncertainty_model = build_amira_p754_presets(self._state.circuit)
        self.uncertainty_changed.emit()

    # ------------------------------------------------------------------
    # Context 2: Stream / Feed / Product
    # ------------------------------------------------------------------

    def _populate_stream_context(self) -> None:
        stream = self._find_stream(self.selected_stream_id)
        if stream is None:
            return

        t = self._i18n.t
        ext_lbl = t("step01.external_boundary")
        from_txt = ext_lbl
        to_txt = ext_lbl
        if stream.from_node:
            fn = self._find_node(stream.from_node)
            from_txt = f"{fn.name} ({fn.id})" if fn else stream.from_node
        if stream.to_node:
            tn = self._find_node(stream.to_node)
            to_txt = f"{tn.name} ({tn.id})" if tn else stream.to_node

        self._syncing = True
        try:
            if not self.stream_name_edit.hasFocus():
                self.stream_name_edit.setText(stream.name)
            set_combo_by_data(self.stream_role_combo, stream.role)
            self.stream_conn_value.setText(f"{from_txt} -> {to_txt}")

            self._populate_stream_manual_inputs(stream)
            self._populate_stream_historical_inputs(stream)
            self._populate_stream_uncertainty_inputs(stream)
            self._populate_stream_reconciliation_table(stream)
        finally:
            self._syncing = False

    def _populate_stream_manual_inputs(self, stream: StreamSpec) -> None:
        point = self._state.current_period()
        mass_val = point.mass_flows.get(stream.id) if point is not None else None
        mass_measured = mass_val is not None and math.isfinite(float(mass_val))

        self.lbl_manual_mass.setText(
            f"{self._i18n.t('step02.is_mass_flow')} ({self._state.circuit.mass_unit.value})"
        )
        self.manual_mass_chk.setChecked(mass_measured)
        self.manual_mass_spin.setEnabled(mass_measured)
        self.manual_mass_spin.setValue(float(mass_val) if mass_measured else 0.0)

        self._clear_layout_widgets(self.manual_species_layout)
        self.manual_species_rows.clear()
        measured_lbl = self._i18n.t("docks.measured_chk")

        for sp in self._state.circuit.species:
            raw_assay = (
                point.assays.get(stream.id, {}).get(sp.id)
                if point is not None
                else None
            )
            sp_measured = raw_assay is not None and math.isfinite(float(raw_assay))

            sp_block = QVBoxLayout()
            sp_block.setContentsMargins(0, 0, 0, 0)
            sp_block.setSpacing(4)

            lbl = QLabel(f"{sp.id} ({sp.unit.value})", self.manual_species_container)
            self._configure_form_label(lbl, word_wrap=True)
            sp_block.addWidget(lbl)

            row_h = QHBoxLayout()
            row_h.setContentsMargins(0, 0, 0, 0)
            row_h.setSpacing(6)

            chk = QCheckBox(measured_lbl, self.manual_species_container)
            chk.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
            chk.setChecked(sp_measured)

            max_val = 100.0 if sp.unit == GradeUnit.PERCENT else 1e6
            spin = self._configure_numeric_spin(
                self.manual_species_container,
                minimum=0.0,
                maximum=max_val,
                decimals=3,
                step=0.1 if sp.unit == GradeUnit.PERCENT else 1.0,
            )
            spin.setEnabled(sp_measured)
            spin.setValue(float(raw_assay) if sp_measured else 0.0)

            chk.toggled.connect(
                lambda checked, sid=sp.id, c=chk, s=spin: self._on_manual_species_toggled(
                    sid, checked, c, s
                )
            )
            spin.valueChanged.connect(
                lambda val, sid=sp.id, c=chk, s=spin: self._on_manual_species_value_changed(
                    sid, val, c, s
                )
            )

            row_h.addWidget(chk)
            row_h.addWidget(spin, 1)
            sp_block.addLayout(row_h)

            lbl.show()
            chk.show()
            spin.show()
            self.manual_species_layout.addLayout(sp_block)
            self.manual_species_rows[sp.id] = (chk, spin)

        _invalidate_layout_chain(self.manual_species_container)

    def _populate_stream_historical_inputs(self, stream: StreamSpec) -> None:
        report = self._state.profile_report
        has_profile = report is not None
        self.btn_stream_import.setVisible(not has_profile)
        self.hist_mapping_panel.setVisible(has_profile)
        self.btn_stream_open_table.setVisible(self._state.cleaned_dataset is not None)

        self._clear_form_layout(self.hist_mapping_form)
        self.historical_mapping_combos.clear()

        if report is None:
            _invalidate_layout_chain(self.data_entry_stack)
            return
        if self._state.cleaning_plan is None:
            self._state.cleaning_plan = build_default_cleaning_plan(report)

        plan = self._state.cleaning_plan
        unmapped_lbl = self._i18n.t("step02.unmapped")

        def _find_mapped_col(species_id: Optional[str]) -> Optional[str]:
            is_mass = species_id is None
            for m in plan.mappings:
                if (
                    not m.is_non_metallurgical
                    and m.target_stream_id == stream.id
                    and m.is_mass_flow == is_mass
                    and m.target_species_id == species_id
                ):
                    return m.source_column
            return None

        def _make_mapping_combo(species_id: Optional[str]) -> QComboBox:
            cb = QComboBox(self.hist_mapping_panel)
            self._configure_compact_combo(cb)
            cb.addItem(unmapped_lbl, None)
            for col_name in report.columns:
                cb.addItem(col_name, col_name)
            mapped_col = _find_mapped_col(species_id)
            set_combo_by_data(cb, mapped_col)
            cb.currentIndexChanged.connect(
                lambda _i, sp_id=species_id, combo=cb: self._on_hist_mapping_changed(
                    sp_id, combo
                )
            )
            self.historical_mapping_combos[species_id] = cb
            return cb

        mass_lbl = QLabel(
            f"{self._i18n.t('step02.is_mass_flow')} ({self._state.circuit.mass_unit.value})",
            self.hist_mapping_panel,
        )
        self._configure_form_label(mass_lbl, word_wrap=True)
        mass_cb = _make_mapping_combo(None)
        mass_lbl.show()
        mass_cb.show()
        self.hist_mapping_form.addRow(mass_lbl, mass_cb)

        for sp in self._state.circuit.species:
            sp_lbl = QLabel(f"{sp.id} ({sp.unit.value})", self.hist_mapping_panel)
            self._configure_form_label(sp_lbl, word_wrap=True)
            sp_cb = _make_mapping_combo(sp.id)
            sp_lbl.show()
            sp_cb.show()
            self.hist_mapping_form.addRow(sp_lbl, sp_cb)

        _invalidate_layout_chain(self.hist_mapping_panel)

    def _populate_stream_uncertainty_inputs(self, stream: StreamSpec) -> None:
        self._clear_form_layout(self.sigma_form)
        self.uncertainty_spins.clear()

        model = self._state.uncertainty_model
        if model is None:
            model = build_amira_p754_presets(self._state.circuit)

        entries: list[tuple[str, str, Optional[SpeciesSpec]]] = [
            (
                make_mass_var_key(stream.id),
                self._i18n.t("step02.is_mass_flow"),
                None,
            )
        ]
        for sp in self._state.circuit.species:
            entries.append((make_assay_var_key(stream.id, sp.id), sp.id, sp))

        for var_key, label_txt, sp_obj in entries:
            spec = model.specs.get(var_key)
            val = spec.value if spec is not None else 5.0
            scale = spec.scale if spec is not None else UncertaintyScale.RELATIVE_PCT

            lbl = QLabel(label_txt, self.sigma_grid_container)
            self._configure_form_label(lbl, word_wrap=True)
            spin = self._configure_numeric_spin(
                self.sigma_grid_container,
                minimum=0.001,
                maximum=10000.0,
                decimals=3,
                step=0.5,
            )
            if scale == UncertaintyScale.RELATIVE_PCT:
                spin.setSuffix(" %")
            else:
                unit_str = (
                    sp_obj.unit.value
                    if sp_obj is not None
                    else self._state.circuit.mass_unit.value
                )
                spin.setSuffix(f" {unit_str}")
            spin.setValue(max(0.001, float(val)))
            spin.valueChanged.connect(
                lambda v, k=var_key, sid=stream.id, sp_id=(
                    sp_obj.id if sp_obj is not None else None
                ), sc=scale: self._on_stream_sigma_changed(k, sid, sp_id, sc, v)
            )
            lbl.show()
            spin.show()
            self.sigma_form.addRow(lbl, spin)
            self.uncertainty_spins[var_key] = spin

        _invalidate_layout_chain(self.sigma_grid_container)

    def _populate_stream_reconciliation_table(self, stream: StreamSpec) -> None:
        result = self._state.reconciliation_result
        has_result = result is not None
        self.stream_no_rec_label.setVisible(not has_result)
        self.stream_rec_table.setVisible(has_result)
        self.btn_outlier_compare.setEnabled(has_result)

        if result is None:
            self.stream_rec_table.setRowCount(0)
            return

        stream_adjs = [a for a in result.adjustments if a.stream_id == stream.id]
        palette = get_theme_palette(self._theme)
        na_str = self._i18n.t("common.na")

        self.stream_rec_table.setRowCount(len(stream_adjs))
        for r, adj in enumerate(stream_adjs):
            var_lbl = adj.species_id if adj.species_id else self._i18n.t("step02.is_mass_flow")
            z_color: Optional[str] = None
            z_bg: Optional[str] = None
            if adj.is_outlier:
                z_color = palette["error"]
                z_bg = palette["error_bg"]
            elif adj.z_score is not None and abs(adj.z_score) > 2.0:
                z_color = palette["warning"]
                z_bg = palette["warning_bg"]

            self.stream_rec_table.setItem(
                r,
                0,
                make_text_item(var_lbl, mono=True, user_data=adj.variable_key),
            )
            self.stream_rec_table.setItem(
                r, 1, make_numeric_item(adj.measured, fmt="{:.3f}", na_text=na_str)
            )
            self.stream_rec_table.setItem(
                r, 2, make_numeric_item(adj.reconciled, fmt="{:.3f}", na_text=na_str, bold=True)
            )
            self.stream_rec_table.setItem(
                r, 3, make_numeric_item(adj.relative_pct, fmt="{:+.2f}", na_text=na_str)
            )
            self.stream_rec_table.setItem(
                r,
                4,
                make_numeric_item(
                    adj.z_score,
                    fmt="{:+.2f}",
                    na_text=na_str,
                    color_hex=z_color,
                    bg_hex=z_bg,
                    bold=adj.is_outlier,
                ),
            )

        self.stream_rec_table.resizeColumnToContents(0)
        self._fit_table_height(self.stream_rec_table, row_height=24, min_h=88, max_h=190)

    def _on_data_mode_switched(self, mode_idx: int) -> None:
        self.data_entry_stack.setCurrentIndex(mode_idx)
        self._sync_data_mode_buttons(mode_idx)
        _invalidate_layout_chain(self.data_entry_stack)

    def _sync_data_mode_buttons(self, mode_idx: int) -> None:
        for btn, active in (
            (self.btn_mode_manual, mode_idx == 0),
            (self.btn_mode_historical, mode_idx == 1),
        ):
            btn.setProperty("accent", active)
            style = btn.style()
            style.unpolish(btn)
            style.polish(btn)
            btn.update()

    def _on_stream_name_changed(self, text: str) -> None:
        if self._syncing or not self.selected_stream_id:
            return
        stream = self._find_stream(self.selected_stream_id)
        if stream is None:
            return
        new_name = text.strip() or stream.id
        if new_name != stream.name:
            stream.name = new_name
            self.stream_renamed.emit(stream.id, new_name)

    def _on_stream_role_changed(self) -> None:
        if self._syncing or not self.selected_stream_id:
            return
        stream = self._find_stream(self.selected_stream_id)
        if stream is None:
            return
        data = self.stream_role_combo.currentData()
        if data is not None:
            role = StreamRole(data)
            if role != stream.role:
                stream.role = role
                self.circuit_edited.emit()

    def _on_manual_mass_toggled(self, checked: bool) -> None:
        if self._syncing or not self.selected_stream_id:
            return
        self.manual_mass_spin.setEnabled(checked)
        val = float(self.manual_mass_spin.value()) if checked else None
        self._state.set_manual_stream_value(self.selected_stream_id, None, val)
        self.manual_data_changed.emit()

    def _on_manual_mass_value_changed(self, value: float) -> None:
        if self._syncing or not self.selected_stream_id:
            return
        if not self.manual_mass_chk.isChecked():
            self._syncing = True
            self.manual_mass_chk.setChecked(True)
            self.manual_mass_spin.setEnabled(True)
            self._syncing = False
        self._state.set_manual_stream_value(self.selected_stream_id, None, float(value))
        self.manual_data_changed.emit()

    def _on_manual_species_toggled(
        self, species_id: str, checked: bool, _chk: QCheckBox, spin: QDoubleSpinBox
    ) -> None:
        if self._syncing or not self.selected_stream_id:
            return
        spin.setEnabled(checked)
        val = float(spin.value()) if checked else None
        self._state.set_manual_stream_value(self.selected_stream_id, species_id, val)
        self.manual_data_changed.emit()

    def _on_manual_species_value_changed(
        self, species_id: str, value: float, chk: QCheckBox, spin: QDoubleSpinBox
    ) -> None:
        if self._syncing or not self.selected_stream_id:
            return
        if not chk.isChecked():
            self._syncing = True
            chk.setChecked(True)
            spin.setEnabled(True)
            self._syncing = False
        self._state.set_manual_stream_value(self.selected_stream_id, species_id, float(value))
        self.manual_data_changed.emit()

    def _on_hist_mapping_changed(
        self, species_id: Optional[str], combo: QComboBox
    ) -> None:
        if self._syncing or not self.selected_stream_id:
            return
        plan = self._state.cleaning_plan
        if plan is None:
            return
        is_mass = species_id is None
        source_col = combo.currentData()

        for m in plan.mappings:
            if (
                m.target_stream_id == self.selected_stream_id
                and m.is_mass_flow == is_mass
                and m.target_species_id == species_id
            ):
                m.target_stream_id = None
                m.target_species_id = None
                m.is_mass_flow = False

        if source_col is not None:
            target_entry: Optional[ColumnMappingSuggestion] = None
            for m in plan.mappings:
                if m.source_column == source_col:
                    target_entry = m
                    break
            if target_entry is None:
                target_entry = ColumnMappingSuggestion(source_column=str(source_col))
                plan.mappings.append(target_entry)
            target_entry.is_non_metallurgical = False
            target_entry.target_stream_id = self.selected_stream_id
            target_entry.is_mass_flow = is_mass
            target_entry.target_species_id = species_id
            target_entry.confidence = 1.0

    def _on_apply_stream_amira(self) -> None:
        if not self.selected_stream_id:
            return
        stream = self._find_stream(self.selected_stream_id)
        if stream is None:
            return
        presets = build_amira_p754_presets(self._state.circuit)
        um = self._state.ensure_uncertainty_model()
        prefix_m = make_mass_var_key(stream.id)
        if prefix_m in presets.specs:
            um.specs[prefix_m] = presets.specs[prefix_m].model_copy(deep=True)
        for sp in self._state.circuit.species:
            a_key = make_assay_var_key(stream.id, sp.id)
            if a_key in presets.specs:
                um.specs[a_key] = presets.specs[a_key].model_copy(deep=True)

        self._syncing = True
        try:
            self._populate_stream_uncertainty_inputs(stream)
        finally:
            self._syncing = False
        self.uncertainty_changed.emit()

    def _on_stream_sigma_changed(
        self,
        var_key: str,
        stream_id: str,
        species_id: Optional[str],
        scale: UncertaintyScale,
        value: float,
    ) -> None:
        if self._syncing:
            return
        um = self._state.ensure_uncertainty_model()
        safe_val = max(0.001, float(value))
        um.specs[var_key] = UncertaintySpec(
            variable_key=var_key,
            stream_id=stream_id,
            species_id=species_id,
            mode=UncertaintyMode.MANUAL,
            scale=scale,
            value=safe_val,
        )
        self.uncertainty_changed.emit()

    def _on_outlier_compare_clicked(self) -> None:
        if not self.selected_stream_id or self._state.reconciliation_result is None:
            return
        selected_rows = self.stream_rec_table.selectionModel().selectedRows()
        if selected_rows:
            row = selected_rows[0].row()
            item = self.stream_rec_table.item(row, 0)
            if item is not None:
                vkey = item.data(Qt.ItemDataRole.UserRole)
                if isinstance(vkey, str) and vkey:
                    self.outlier_compare_requested.emit([vkey])
                    return

        stream_adjs = [
            a
            for a in self._state.reconciliation_result.adjustments
            if a.stream_id == self.selected_stream_id
        ]
        if not stream_adjs:
            return
        ranked = [a for a in stream_adjs if a.z_score is not None]
        if ranked:
            best = max(ranked, key=lambda a: abs(float(a.z_score or 0.0)))
            self.outlier_compare_requested.emit([best.variable_key])
        else:
            self.outlier_compare_requested.emit([stream_adjs[0].variable_key])

    # ------------------------------------------------------------------
    # Context 3: Process Stage (Node)
    # ------------------------------------------------------------------

    def _populate_stage_context(self) -> None:
        node = self._find_node(self.selected_node_id)
        if node is None:
            return

        in_streams = [s for s in self._state.circuit.streams if s.to_node == node.id]
        out_streams = [s for s in self._state.circuit.streams if s.from_node == node.id]

        self._syncing = True
        try:
            if not self.stage_name_edit.hasFocus():
                self.stage_name_edit.setText(node.name)
            set_combo_by_data(self.stage_type_combo, node.unit_type)
            self._populate_stage_stream_pills(in_streams, out_streams)
            self._populate_stage_balance_table(in_streams, out_streams)
            self._populate_stage_kpi_section(in_streams, out_streams)
        finally:
            self._syncing = False

    def _populate_stage_stream_pills(
        self,
        in_streams: list[StreamSpec],
        out_streams: list[StreamSpec],
    ) -> None:
        na_str = self._i18n.t("common.na")
        for streams, host, flow in (
            (in_streams, self.inflows_host, self.inflows_flow),
            (out_streams, self.outflows_host, self.outflows_flow),
        ):
            self._clear_layout_widgets(flow)
            if not streams:
                na_lbl = QLabel(na_str, host)
                self._configure_form_label(na_lbl, word_wrap=False)
                na_lbl.show()
                flow.addWidget(na_lbl)
            else:
                for stream in streams:
                    btn = QPushButton(stream.name, host)
                    btn.setProperty("compact", True)
                    self._configure_compact_button(btn)
                    btn.setToolTip(stream.id)
                    btn.setCursor(Qt.CursorShape.PointingHandCursor)
                    btn.clicked.connect(
                        lambda _chk=False, sid=stream.id: self.stream_jump_requested.emit(
                            sid
                        )
                    )
                    flow.addWidget(btn)
                    btn.show()
            flow.invalidate()
            host.updateGeometry()
        _invalidate_layout_chain(self.stage_conn_section)
        self.inflows_flow.activate()
        self.outflows_flow.activate()

    def _populate_stage_balance_table(
        self,
        in_streams: list[StreamSpec],
        out_streams: list[StreamSpec],
    ) -> None:
        point = self._state.current_period()
        result = self._state.reconciliation_result
        species_list = self._state.circuit.species
        na_str = self._i18n.t("common.na")
        palette = get_theme_palette(self._theme)

        self.stage_balance_table.setRowCount(1 + len(species_list))

        def _raw_mass_sum(streams: list[StreamSpec]) -> Optional[float]:
            if point is None or not streams:
                return None
            vals: list[float] = []
            for s in streams:
                v = point.mass_flows.get(s.id)
                if v is None or not math.isfinite(float(v)):
                    return None
                vals.append(float(v))
            return sum(vals)

        def _raw_sp_sum(streams: list[StreamSpec], sp: SpeciesSpec) -> Optional[float]:
            if point is None or not streams:
                return None
            factor = sp.to_fraction_factor()
            total = 0.0
            for s in streams:
                m = point.mass_flows.get(s.id)
                x = point.assays.get(s.id, {}).get(sp.id)
                if (
                    m is None
                    or x is None
                    or not math.isfinite(float(m))
                    or not math.isfinite(float(x))
                ):
                    return None
                total += float(m) * float(x) * factor
            return total

        def _set_balance_row(
            row: int,
            comp_label: str,
            raw_in: Optional[float],
            raw_out: Optional[float],
            rec_in: Optional[float],
            rec_out: Optional[float],
        ) -> None:
            self.stage_balance_table.setItem(row, 0, make_text_item(comp_label, bold=(row == 0)))
            self.stage_balance_table.setItem(
                row, 1, make_numeric_item(raw_in, fmt="{:.2f}", na_text=na_str)
            )
            self.stage_balance_table.setItem(
                row, 2, make_numeric_item(raw_out, fmt="{:.2f}", na_text=na_str)
            )
            raw_diff = (raw_in - raw_out) if (raw_in is not None and raw_out is not None) else None
            diff_color = None
            if raw_diff is not None and raw_in and abs(raw_diff / max(abs(raw_in), 1e-9)) > 0.05:
                diff_color = palette["warning"]
            self.stage_balance_table.setItem(
                row,
                3,
                make_numeric_item(
                    raw_diff, fmt="{:+.2f}", na_text=na_str, color_hex=diff_color
                ),
            )
            if rec_in is not None and rec_out is not None:
                rec_txt = f"{rec_in:.2f} / {rec_out:.2f}"
                rec_item = make_text_item(
                    rec_txt,
                    align=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                    mono=True,
                    color_hex=palette["validated"],
                )
                rec_item.setToolTip(f"{rec_in:.4f} -> {rec_out:.4f}")
            else:
                rec_item = make_text_item(
                    na_str,
                    align=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                    mono=True,
                )
            self.stage_balance_table.setItem(row, 4, rec_item)

        raw_m_in = _raw_mass_sum(in_streams)
        raw_m_out = _raw_mass_sum(out_streams)
        rec_m_in = (
            sum(float(result.reconciled_mass_flows[s.id]) for s in in_streams)
            if result is not None and in_streams
            else None
        )
        rec_m_out = (
            sum(float(result.reconciled_mass_flows[s.id]) for s in out_streams)
            if result is not None and out_streams
            else None
        )
        _set_balance_row(
            0, self._i18n.t("step04.mass_total_label"), raw_m_in, raw_m_out, rec_m_in, rec_m_out
        )

        for idx, sp in enumerate(species_list, start=1):
            factor = sp.to_fraction_factor()
            raw_sp_in = _raw_sp_sum(in_streams, sp)
            raw_sp_out = _raw_sp_sum(out_streams, sp)
            rec_sp_in = (
                sum(
                    float(result.reconciled_mass_flows[s.id])
                    * float(result.reconciled_assays[s.id][sp.id])
                    * factor
                    for s in in_streams
                )
                if result is not None and in_streams
                else None
            )
            rec_sp_out = (
                sum(
                    float(result.reconciled_mass_flows[s.id])
                    * float(result.reconciled_assays[s.id][sp.id])
                    * factor
                    for s in out_streams
                )
                if result is not None and out_streams
                else None
            )
            _set_balance_row(idx, sp.id, raw_sp_in, raw_sp_out, rec_sp_in, rec_sp_out)

        self.stage_balance_table.resizeColumnToContents(0)
        self._fit_table_height(self.stage_balance_table, row_height=24, min_h=92, max_h=200)

    def _populate_stage_kpi_section(
        self,
        in_streams: list[StreamSpec],
        out_streams: list[StreamSpec],
    ) -> None:
        result = self._state.reconciliation_result
        has_result = result is not None
        self.stage_kpi_empty_label.setVisible(not has_result)
        self.stage_mass_pull_label.setVisible(has_result)
        self.stage_kpi_table.setVisible(has_result)

        if result is None:
            self.stage_kpi_table.setRowCount(0)
            return

        conc_streams = [s for s in out_streams if s.role == StreamRole.CONCENTRATE]
        if not conc_streams and len(out_streams) > 1:
            conc_streams = [
                s
                for s in out_streams
                if s.role != StreamRole.TAILINGS
                and not any(
                    tok in f"{s.id}_{s.name}".lower() for tok in _TAILINGS_NAME_TOKENS
                )
            ]

        feed_mass_rec = sum(
            float(result.reconciled_mass_flows.get(s.id, 0.0)) for s in in_streams
        )
        na_str = self._i18n.t("common.na")
        if not conc_streams or feed_mass_rec <= 1e-12:
            self.stage_mass_pull_label.setText(
                f"{self._i18n.t('docks.stage_mass_pull')}: {na_str}"
            )
            self.stage_kpi_table.setRowCount(0)
            return

        pull_parts: list[str] = []
        total_conc_mass = 0.0
        for cs in conc_streams:
            m_c = float(result.reconciled_mass_flows.get(cs.id, 0.0))
            total_conc_mass += m_c
            y_c = 100.0 * m_c / feed_mass_rec
            pull_parts.append(f"{cs.id}: {y_c:.2f}%")
        if len(conc_streams) == 1:
            self.stage_mass_pull_label.setText(
                f"{self._i18n.t('docks.stage_mass_pull')}: {100.0 * total_conc_mass / feed_mass_rec:.2f}%"
            )
        else:
            self.stage_mass_pull_label.setText(
                f"{self._i18n.t('docks.stage_mass_pull')}: " + " | ".join(pull_parts)
            )

        species_list = self._state.circuit.species
        rows_data: list[tuple[str, str, float]] = []
        for cs in conc_streams:
            m_c = float(result.reconciled_mass_flows.get(cs.id, 0.0))
            for sp in species_list:
                factor = sp.to_fraction_factor()
                feed_fine = sum(
                    float(result.reconciled_mass_flows.get(s.id, 0.0))
                    * float(result.reconciled_assays.get(s.id, {}).get(sp.id, 0.0))
                    * factor
                    for s in in_streams
                )
                conc_fine = (
                    m_c
                    * float(result.reconciled_assays.get(cs.id, {}).get(sp.id, 0.0))
                    * factor
                )
                rec_pct = 100.0 * conc_fine / feed_fine if feed_fine > 1e-15 else 0.0
                rows_data.append((cs.id, sp.id, rec_pct))

        self.stage_kpi_table.setRowCount(len(rows_data))
        for r, (stream_id, sp_id, rec_pct) in enumerate(rows_data):
            self.stage_kpi_table.setItem(r, 0, make_text_item(stream_id))
            self.stage_kpi_table.setItem(r, 1, make_text_item(sp_id, mono=True))
            self.stage_kpi_table.setItem(
                r, 2, make_numeric_item(rec_pct, fmt="{:.2f}%", bold=True)
            )
        self.stage_kpi_table.resizeColumnToContents(0)
        self._fit_table_height(self.stage_kpi_table, row_height=24, min_h=88, max_h=190)

    def _on_stage_name_changed(self, text: str) -> None:
        if self._syncing or not self.selected_node_id:
            return
        node = self._find_node(self.selected_node_id)
        if node is None:
            return
        new_name = text.strip() or node.id
        if new_name != node.name:
            node.name = new_name
            self.stage_renamed.emit(node.id, new_name)

    def _on_stage_type_changed(self) -> None:
        if self._syncing or not self.selected_node_id:
            return
        node = self._find_node(self.selected_node_id)
        if node is None:
            return
        data = self.stage_type_combo.currentData()
        if data is not None:
            ut = UnitType(data)
            if ut != node.unit_type:
                node.unit_type = ut
                self.circuit_edited.emit()

    # ------------------------------------------------------------------
    # Backwards-Compatible Payload & Alerts Drawer
    # ------------------------------------------------------------------

    def _render_payload(self) -> None:
        palette = get_theme_palette(self._theme)
        t = self._i18n.t

        if self._payload is None:
            self.payload_title_label.setText(t("docks.inspector_empty"))
            self.blocked_banner.setVisible(False)
            self.props_table.setRowCount(0)
            self.alerts_drawer.set_title(t("docks.inspector_alerts_count", count=0))
            self.alerts_list.clear()
            _invalidate_layout_chain(self.alerts_drawer)
            return

        payload = self._payload
        self.payload_title_label.setText(payload.title)

        if payload.blocked_reason:
            self.blocked_banner.setText(
                t("docks.inspector_step_blocked_reason", reason=payload.blocked_reason)
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

        self.alerts_drawer.set_title(
            t("docks.inspector_alerts_count", count=len(payload.alerts))
        )
        self.alerts_list.clear()
        for severity, msg in payload.alerts:
            sev_lower = severity.lower()
            if sev_lower == "error":
                prefix = t("common.error")
                color_hex = palette["error"]
                icon_name = "alert_triangle.svg"
            elif sev_lower == "warning":
                prefix = t("common.warning")
                color_hex = palette["warning"]
                icon_name = "alert_triangle.svg"
            else:
                prefix = t("common.info")
                color_hex = palette["text_secondary"]
                icon_name = "check_circle.svg"

            list_item = QListWidgetItem(f"[{prefix}] {msg}")
            list_item.setForeground(QColor(color_hex))
            list_item.setIcon(
                load_svg_icon(icon_name, theme=self._theme, color_override=color_hex, size=16)
            )
            self.alerts_list.addItem(list_item)

        if payload.blocked_reason or any(
            sev.lower() in ("error", "warning") for sev, _ in payload.alerts
        ):
            self.alerts_drawer.set_expanded(True)
        _invalidate_layout_chain(self.alerts_drawer)
