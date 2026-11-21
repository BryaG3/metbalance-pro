"""Step 01 — Project metadata, circuit templates, species, flowsheet editor, and Matrix A."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from app.i18n.manager import get_i18n
from core.models import (
    CircuitSpec,
    GradeUnit,
    MassUnit,
    NodeSpec,
    SpeciesSpec,
    StepStatus,
    StreamRole,
    StreamSpec,
    UnitType,
)
from core.topology import build_incidence_matrix, get_circuit_template, validate_topology

from .flowsheet_canvas import FlowsheetCanvas
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

_TEMPLATE_KEYS: list[tuple[str, str]] = [
    ("concentrator_2prod", "step01.template_2prod"),
    ("concentrator_3prod", "step01.template_3prod"),
    ("polymetallic_4prod", "step01.template_4prod"),
    ("multinode_rsc", "step01.template_rsc"),
]


class _ConnectStreamDialog(QDialog):
    """Modal dialog for creating a new directed stream between nodes or boundaries."""

    def __init__(
        self,
        circuit: CircuitSpec,
        default_from: Optional[str] = None,
        default_to: Optional[str] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._i18n = get_i18n()
        self.setWindowTitle(self._i18n.t("step01.add_stream_btn"))
        self.setMinimumWidth(340)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        idx_num = len(circuit.streams) + 1
        existing_ids = {s.id for s in circuit.streams}
        candidate_id = f"S-{idx_num:02d}"
        while candidate_id in existing_ids:
            idx_num += 1
            candidate_id = f"S-{idx_num:02d}"

        self.id_edit = QLineEdit(candidate_id, self)
        self.name_edit = QLineEdit(candidate_id, self)
        self.role_combo = QComboBox(self)
        for role in StreamRole:
            self.role_combo.addItem(self._i18n.t(f"enums.stream_role.{role.value}"), role)

        self.from_combo = QComboBox(self)
        self.to_combo = QComboBox(self)
        ext_label = self._i18n.t("step01.external_boundary")
        self.from_combo.addItem(ext_label, None)
        self.to_combo.addItem(ext_label, None)
        for node in circuit.nodes:
            label = f"{node.id} - {node.name}"
            self.from_combo.addItem(label, node.id)
            self.to_combo.addItem(label, node.id)

        set_combo_by_data(self.from_combo, default_from)
        set_combo_by_data(self.to_combo, default_to)
        if default_from is None and default_to is not None:
            set_combo_by_data(self.role_combo, StreamRole.FEED)
        elif default_from is not None and default_to is None:
            set_combo_by_data(self.role_combo, StreamRole.CONCENTRATE)
        elif default_from is not None and default_to is not None:
            set_combo_by_data(self.role_combo, StreamRole.INTERNAL)

        form.addRow(self._i18n.t("step01.stream_id"), self.id_edit)
        form.addRow(self._i18n.t("step01.stream_name"), self.name_edit)
        form.addRow(self._i18n.t("step01.stream_role"), self.role_combo)
        form.addRow(self._i18n.t("step01.from_node"), self.from_combo)
        form.addRow(self._i18n.t("step01.to_node"), self.to_combo)
        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            self,
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(self._i18n.t("common.ok"))
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(self._i18n.t("common.cancel"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def build_stream(self) -> StreamSpec:
        s_id = self.id_edit.text().strip() or "Stream"
        s_name = self.name_edit.text().strip() or s_id
        return StreamSpec(
            id=s_id,
            name=s_name,
            role=self.role_combo.currentData(),
            from_node=self.from_combo.currentData(),
            to_node=self.to_combo.currentData(),
        )


class ProjectTopologyView(QWidget):
    """Step 01 view for project metadata, circuit templates, species, P&ID flowsheet, and Matrix A."""

    topology_modified = Signal()
    step_validated = Signal(bool)
    inspector_requested = Signal(object)
    next_step_requested = Signal()

    def __init__(
        self,
        state: ProjectState,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._state = state
        self._i18n = get_i18n()
        self._theme = "light"
        self._syncing = False
        self._selected_node_id: Optional[str] = None
        self._selected_stream_id: Optional[str] = None

        self._build_ui()
        self.retranslate_ui()
        self.sync_from_state()

    @staticmethod
    def _configure_shrinkable_combo(combo: QComboBox) -> None:
        combo.setMinimumWidth(80)
        combo.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        combo.setMinimumContentsLength(6)

    @staticmethod
    def _configure_shrinkable_edit(edit: QLineEdit) -> None:
        edit.setMinimumWidth(80)
        edit.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)

    def _build_ui(self) -> None:
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(8, 8, 8, 8)
        root_layout.setSpacing(6)

        # Header
        self.header_title = QLabel(self)
        self.header_title.setFont(ui_font(11.0, bold=True))
        root_layout.addWidget(self.header_title)

        self.main_splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self.main_splitter.setChildrenCollapsible(False)
        self.main_splitter.setHandleWidth(6)

        # Left column: Scrollable container (Metadata + Species + Selected Node/Stream Editor)
        self.left_panel = QScrollArea(self.main_splitter)
        self.left_panel.setWidgetResizable(True)
        self.left_panel.setFrameShape(QFrame.Shape.NoFrame)
        self.left_panel.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.left_panel.setMinimumWidth(260)
        self.left_panel.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding
        )

        left_content = QWidget(self.left_panel)
        left_layout = QVBoxLayout(left_content)
        left_layout.setContentsMargins(0, 0, 4, 0)
        left_layout.setSpacing(6)

        self.meta_group = QGroupBox(left_content)
        self.meta_group.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        meta_form = QFormLayout(self.meta_group)
        meta_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        meta_form.setSpacing(5)

        self.project_name_edit = QLineEdit(self.meta_group)
        self.project_desc_edit = QLineEdit(self.meta_group)
        self.mass_unit_combo = QComboBox(self.meta_group)
        self._configure_shrinkable_edit(self.project_name_edit)
        self._configure_shrinkable_edit(self.project_desc_edit)
        self._configure_shrinkable_combo(self.mass_unit_combo)

        tpl_container = QWidget(self.meta_group)
        tpl_row = FlowLayout(tpl_container, margin=0, h_spacing=5, v_spacing=4)
        self.template_combo = QComboBox(tpl_container)
        self._configure_shrinkable_combo(self.template_combo)
        self.template_combo.setMinimumWidth(130)
        self.apply_template_btn = QPushButton(tpl_container)
        self.apply_template_btn.setProperty("compact", True)
        tpl_row.addWidget(self.template_combo)
        tpl_row.addWidget(self.apply_template_btn)

        self.lbl_project_name = QLabel(self.meta_group)
        self.lbl_project_desc = QLabel(self.meta_group)
        self.lbl_mass_unit = QLabel(self.meta_group)
        self.lbl_template = QLabel(self.meta_group)

        meta_form.addRow(self.lbl_project_name, self.project_name_edit)
        meta_form.addRow(self.lbl_project_desc, self.project_desc_edit)
        meta_form.addRow(self.lbl_mass_unit, self.mass_unit_combo)
        meta_form.addRow(self.lbl_template, tpl_container)
        left_layout.addWidget(self.meta_group)

        # Species group
        self.species_group = QGroupBox(left_content)
        self.species_group.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding
        )
        sp_layout = QVBoxLayout(self.species_group)
        sp_layout.setSpacing(5)

        self.species_table = QTableWidget(0, 3, self.species_group)
        self.species_table.setObjectName("SpeciesTable")
        self.species_table.setMinimumWidth(80)
        self.species_table.setMinimumHeight(140)
        self.species_table.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        configure_dense_table(
            self.species_table, stretch_last=True, editable=True, has_embedded_widgets=True
        )
        sp_layout.addWidget(self.species_table)

        sp_btns = FlowLayout(margin=0, h_spacing=6, v_spacing=6)
        self.add_species_btn = QPushButton(self.species_group)
        self.add_species_btn.setProperty("compact", True)
        self.remove_species_btn = QPushButton(self.species_group)
        self.remove_species_btn.setProperty("compact", True)
        self.remove_species_btn.setProperty("danger", True)
        sp_btns.addWidget(self.add_species_btn)
        sp_btns.addWidget(self.remove_species_btn)
        sp_layout.addLayout(sp_btns)
        left_layout.addWidget(self.species_group, 1)

        # Selected Item Property Editor (Node or Stream)
        self.props_group = QGroupBox(left_content)
        self.props_group.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.props_form = QFormLayout(self.props_group)
        self.props_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        self.props_form.setSpacing(5)

        self.prop_id_edit = QLineEdit(self.props_group)
        self.prop_name_edit = QLineEdit(self.props_group)
        self.prop_type_role_combo = QComboBox(self.props_group)
        self.prop_from_combo = QComboBox(self.props_group)
        self.prop_to_combo = QComboBox(self.props_group)
        self._configure_shrinkable_edit(self.prop_id_edit)
        self._configure_shrinkable_edit(self.prop_name_edit)
        self._configure_shrinkable_combo(self.prop_type_role_combo)
        self._configure_shrinkable_combo(self.prop_from_combo)
        self._configure_shrinkable_combo(self.prop_to_combo)
        self.prop_apply_btn = QPushButton(self.props_group)
        self.prop_apply_btn.setProperty("compact", True)

        self.lbl_prop_id = QLabel(self.props_group)
        self.lbl_prop_name = QLabel(self.props_group)
        self.lbl_prop_type_role = QLabel(self.props_group)
        self.lbl_prop_from = QLabel(self.props_group)
        self.lbl_prop_to = QLabel(self.props_group)

        self.props_form.addRow(self.lbl_prop_id, self.prop_id_edit)
        self.props_form.addRow(self.lbl_prop_name, self.prop_name_edit)
        self.props_form.addRow(self.lbl_prop_type_role, self.prop_type_role_combo)
        self.props_form.addRow(self.lbl_prop_from, self.prop_from_combo)
        self.props_form.addRow(self.lbl_prop_to, self.prop_to_combo)
        self.props_form.addRow("", self.prop_apply_btn)
        self.props_group.setVisible(False)
        left_layout.addWidget(self.props_group)

        self.left_panel.setWidget(left_content)
        self.main_splitter.addWidget(self.left_panel)

        # Right column: Flowsheet Canvas (top) + Live Incidence Matrix A (bottom)
        self.right_splitter = QSplitter(Qt.Orientation.Vertical, self.main_splitter)
        self.right_splitter.setChildrenCollapsible(False)
        self.right_splitter.setHandleWidth(6)
        self.right_splitter.setMinimumWidth(280)
        self.right_splitter.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding
        )

        self.flowsheet_group = QGroupBox(self.right_splitter)
        self.flowsheet_group.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding
        )
        fs_layout = QVBoxLayout(self.flowsheet_group)
        fs_layout.setSpacing(6)

        self.fs_toolbar = FlowLayout(margin=0, h_spacing=6, v_spacing=6)
        self.add_node_btn = QPushButton(self.flowsheet_group)
        self.delete_node_btn = QPushButton(self.flowsheet_group)
        self.delete_node_btn.setProperty("danger", True)
        self.add_stream_btn = QPushButton(self.flowsheet_group)
        self.delete_stream_btn = QPushButton(self.flowsheet_group)
        self.delete_stream_btn.setProperty("danger", True)
        self.auto_layout_btn = QPushButton(self.flowsheet_group)
        self.zoom_fit_btn = QPushButton(self.flowsheet_group)

        for btn in (
            self.add_node_btn,
            self.delete_node_btn,
            self.add_stream_btn,
            self.delete_stream_btn,
            self.auto_layout_btn,
            self.zoom_fit_btn,
        ):
            btn.setProperty("compact", True)
            self.fs_toolbar.addWidget(btn)
        fs_layout.addLayout(self.fs_toolbar)

        self.canvas = FlowsheetCanvas(self.flowsheet_group, interactive=True)
        self.canvas.setObjectName("Step01FlowsheetCanvas")
        self.canvas.setMinimumHeight(290)
        fs_layout.addWidget(self.canvas, 1)
        self.right_splitter.addWidget(self.flowsheet_group)

        self.matrix_group = QGroupBox(self.right_splitter)
        self.matrix_group.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        mat_layout = QVBoxLayout(self.matrix_group)
        self.matrix_table = QTableWidget(0, 0, self.matrix_group)
        self.matrix_table.setObjectName("IncidenceMatrixTable")
        configure_dense_table(self.matrix_table, stretch_last=False, editable=False)
        self.matrix_table.verticalHeader().setVisible(True)
        self.matrix_table.setFont(monospace_font(9.0))
        mat_layout.addWidget(self.matrix_table)
        self.right_splitter.addWidget(self.matrix_group)

        self.right_splitter.setStretchFactor(0, 3)
        self.right_splitter.setStretchFactor(1, 1)

        self.main_splitter.addWidget(self.right_splitter)
        self.main_splitter.setStretchFactor(0, 4)
        self.main_splitter.setStretchFactor(1, 6)
        self.main_splitter.setSizes([400, 600])
        root_layout.addWidget(self.main_splitter, 1)

        # Validation Status Banner + Navigation Footer
        footer_layout = QHBoxLayout()
        self.status_banner = QLabel(self)
        self.status_banner.setContentsMargins(8, 5, 8, 5)
        self.status_banner.setWordWrap(True)
        apply_status_role(self.status_banner, "pending")
        footer_layout.addWidget(self.status_banner, 1)

        self.validate_btn = QPushButton(self)
        self.validate_btn.setProperty("accent", True)
        self.next_btn = QPushButton(self)
        footer_layout.addWidget(self.validate_btn)
        footer_layout.addWidget(self.next_btn)
        root_layout.addLayout(footer_layout)

        # Wire signals
        self.project_name_edit.textChanged.connect(self._on_metadata_edited)
        self.project_desc_edit.textChanged.connect(self._on_metadata_edited)
        self.mass_unit_combo.currentIndexChanged.connect(self._on_metadata_edited)
        self.apply_template_btn.clicked.connect(self._on_apply_template_clicked)

        self.add_species_btn.clicked.connect(self._on_add_species)
        self.remove_species_btn.clicked.connect(self._on_remove_species)
        self.species_table.itemChanged.connect(self._on_species_table_changed)

        self.add_node_btn.clicked.connect(self._on_add_node)
        self.delete_node_btn.clicked.connect(self._on_delete_node)
        self.add_stream_btn.clicked.connect(self._on_add_stream)
        self.delete_stream_btn.clicked.connect(self._on_delete_stream)
        self.auto_layout_btn.clicked.connect(self._on_auto_layout)
        self.zoom_fit_btn.clicked.connect(self.canvas.zoom_fit)

        self.canvas.node_selected.connect(self._on_node_selected)
        self.canvas.stream_selected.connect(self._on_stream_selected)
        self.canvas.selection_cleared.connect(self._on_selection_cleared)
        self.canvas.connect_stream_picked.connect(self._on_canvas_stream_picked)
        self.canvas.template_requested.connect(self.apply_template)
        self.prop_apply_btn.clicked.connect(self._on_apply_selected_properties)

        self.validate_btn.clicked.connect(self.validate_step)
        self.next_btn.clicked.connect(self._on_next_clicked)

    def set_theme(self, theme: str) -> None:
        """Apply light/dark icons and canvas theme."""
        self._theme = theme
        self.canvas.set_theme(theme)
        self._update_icons()
        self._refresh_incidence_matrix()

    def _update_icons(self) -> None:
        self.add_species_btn.setIcon(load_svg_icon("plus.svg", self._theme, size=16))
        self.remove_species_btn.setIcon(load_svg_icon("trash.svg", self._theme, size=16))
        self.add_node_btn.setIcon(load_svg_icon("plus.svg", self._theme, size=16))
        self.delete_node_btn.setIcon(load_svg_icon("trash.svg", self._theme, size=16))
        self.add_stream_btn.setIcon(load_svg_icon("link_stream.svg", self._theme, size=16))
        self.delete_stream_btn.setIcon(load_svg_icon("trash.svg", self._theme, size=16))
        self.auto_layout_btn.setIcon(load_svg_icon("matrix.svg", self._theme, size=16))
        self.zoom_fit_btn.setIcon(load_svg_icon("reset_layout.svg", self._theme, size=16))
        self.validate_btn.setIcon(
            load_svg_icon("check_circle.svg", self._theme, color_override="#FFFFFF", size=16)
        )

    def retranslate_ui(self) -> None:
        """Retranslate all labels, combos, headers, and status text."""
        self._syncing = True
        try:
            self.header_title.setText(self._i18n.t("workflow.step_01_title"))
            self.meta_group.setTitle(self._i18n.t("step01.section_metadata"))
            self.lbl_project_name.setText(self._i18n.t("step01.project_name"))
            self.lbl_project_desc.setText(self._i18n.t("step01.project_desc"))
            self.lbl_mass_unit.setText(self._i18n.t("step01.mass_unit"))
            self.lbl_template.setText(self._i18n.t("step01.template_label"))
            self.apply_template_btn.setText(self._i18n.t("step01.apply_template_btn"))

            cur_mass = self.mass_unit_combo.currentData() or self._state.circuit.mass_unit
            self.mass_unit_combo.clear()
            for mu in MassUnit:
                self.mass_unit_combo.addItem(self._i18n.t(f"enums.mass_unit.{mu.value}"), mu)
            set_combo_by_data(self.mass_unit_combo, cur_mass)

            cur_tpl = self.template_combo.currentData() or "polymetallic_4prod"
            self.template_combo.clear()
            for tpl_id, key in _TEMPLATE_KEYS:
                self.template_combo.addItem(self._i18n.t(key), tpl_id)
            set_combo_by_data(self.template_combo, cur_tpl)

            self.species_group.setTitle(self._i18n.t("step01.section_species"))
            self.species_table.setHorizontalHeaderLabels(
                [
                    self._i18n.t("step01.species_id"),
                    self._i18n.t("step01.species_name"),
                    self._i18n.t("step01.species_unit"),
                ]
            )
            self.add_species_btn.setText(self._i18n.t("step01.add_species_btn"))
            self.remove_species_btn.setText(self._i18n.t("step01.remove_species_btn"))

            self.flowsheet_group.setTitle(self._i18n.t("step01.section_flowsheet"))
            self.add_node_btn.setText(self._i18n.t("step01.add_node_btn"))
            self.add_node_btn.setToolTip(self._i18n.t("step01.add_node_tip"))
            self.delete_node_btn.setText(self._i18n.t("step01.delete_node_btn"))
            self.delete_node_btn.setToolTip(self._i18n.t("step01.delete_node_tip"))
            self.add_stream_btn.setText(self._i18n.t("step01.add_stream_btn"))
            self.add_stream_btn.setToolTip(self._i18n.t("step01.add_stream_tip"))
            self.delete_stream_btn.setText(self._i18n.t("step01.delete_stream_btn"))
            self.delete_stream_btn.setToolTip(self._i18n.t("step01.delete_stream_tip"))
            self.auto_layout_btn.setText(self._i18n.t("step01.auto_layout_btn"))
            self.auto_layout_btn.setToolTip(self._i18n.t("step01.auto_layout_tip"))
            self.zoom_fit_btn.setText(self._i18n.t("step01.zoom_fit_btn"))
            self.zoom_fit_btn.setToolTip(self._i18n.t("step01.zoom_fit_tip"))

            self.matrix_group.setTitle(self._i18n.t("step01.incidence_matrix_title"))
            self.prop_apply_btn.setText(self._i18n.t("common.apply"))
            self.validate_btn.setText(self._i18n.t("workflow.nav.validate_step"))
            self.next_btn.setText(self._i18n.t("workflow.nav.next_step"))
        finally:
            self._syncing = False

        self._update_icons()
        self._refresh_species_table()
        self._refresh_selected_editor()
        self._refresh_validation_banner(mark_status=False)

    def sync_from_state(self) -> None:
        """Populate UI widgets from ProjectState.circuit."""
        self._syncing = True
        try:
            circuit = self._state.circuit
            self.project_name_edit.setText(circuit.name)
            self.project_desc_edit.setText(circuit.description)
            set_combo_by_data(self.mass_unit_combo, circuit.mass_unit)
            self._selected_node_id = None
            self._selected_stream_id = None
            self.props_group.setVisible(False)
        finally:
            self._syncing = False

        self._refresh_species_table()
        self.canvas.load_circuit(self._state.circuit)
        self.canvas.zoom_fit()
        self._refresh_incidence_matrix()
        self._refresh_validation_banner(mark_status=False)

    def apply_template(self, template_id: str) -> None:
        """Load a built-in circuit template and reset downstream workflow steps."""
        self._state.circuit = get_circuit_template(template_id)
        set_combo_by_data(self.template_combo, template_id)
        self._mark_topology_changed()
        self.sync_from_state()

    def _mark_topology_changed(self) -> None:
        self._state.step_statuses[0] = StepStatus.PENDING
        self._state.step_block_reasons.pop(0, None)
        self._state.clear_from_step(1)
        self.topology_modified.emit()

    def _on_apply_template_clicked(self) -> None:
        tpl_id = self.template_combo.currentData()
        if tpl_id:
            self.apply_template(str(tpl_id))

    def _on_metadata_edited(self) -> None:
        if self._syncing:
            return
        self._state.circuit.name = self.project_name_edit.text().strip() or self._state.circuit.name
        self._state.circuit.description = self.project_desc_edit.text().strip()
        mu = self.mass_unit_combo.currentData()
        if isinstance(mu, MassUnit):
            self._state.circuit.mass_unit = mu
        self._mark_topology_changed()
        self._refresh_validation_banner(mark_status=False)

    def _refresh_species_table(self) -> None:
        self._syncing = True
        try:
            species_list = self._state.circuit.species
            self.species_table.setRowCount(len(species_list))
            for row, sp in enumerate(species_list):
                self.species_table.setItem(row, 0, make_text_item(sp.id, editable=True, mono=True))
                self.species_table.setItem(row, 1, make_text_item(sp.name, editable=True))

                unit_combo = QComboBox(self.species_table)
                unit_combo.setMinimumWidth(68)
                unit_combo.setSizeAdjustPolicy(
                    QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
                )
                for gu in GradeUnit:
                    unit_combo.addItem(self._i18n.t(f"enums.grade_unit.{gu.value}"), gu)
                set_combo_by_data(unit_combo, sp.unit)
                unit_combo.currentIndexChanged.connect(
                    lambda _idx, r=row, cb=unit_combo: self._on_species_unit_changed(r, cb)
                )
                self.species_table.setCellWidget(row, 2, unit_combo)
        finally:
            self._syncing = False

    def _on_species_unit_changed(self, row: int, combo: QComboBox) -> None:
        if self._syncing or row >= len(self._state.circuit.species):
            return
        gu = combo.currentData()
        if isinstance(gu, GradeUnit):
            self._state.circuit.species[row].unit = gu
            self._mark_topology_changed()
            self._refresh_validation_banner(mark_status=False)

    def _on_species_table_changed(self, item) -> None:
        if self._syncing:
            return
        row = item.row()
        if row < 0 or row >= len(self._state.circuit.species):
            return
        sp = self._state.circuit.species[row]
        if item.column() == 0:
            sp.id = item.text().strip()
        elif item.column() == 1:
            sp.name = item.text().strip()
        self._mark_topology_changed()
        self._refresh_validation_banner(mark_status=False)

    def _on_add_species(self) -> None:
        idx = len(self._state.circuit.species) + 1
        existing = {s.id for s in self._state.circuit.species}
        code = f"E{idx}"
        while code in existing:
            idx += 1
            code = f"E{idx}"
        self._state.circuit.species.append(
            SpeciesSpec(id=code, name=code, unit=GradeUnit.PERCENT)
        )
        self._mark_topology_changed()
        self._refresh_species_table()
        self._refresh_validation_banner(mark_status=False)

    def _on_remove_species(self) -> None:
        row = self.species_table.currentRow()
        if row < 0 or row >= len(self._state.circuit.species):
            if self._state.circuit.species:
                row = len(self._state.circuit.species) - 1
            else:
                return
        self._state.circuit.species.pop(row)
        self._mark_topology_changed()
        self._refresh_species_table()
        self._refresh_validation_banner(mark_status=False)

    def _on_add_node(self) -> None:
        existing = {n.id for n in self._state.circuit.nodes}
        idx = len(self._state.circuit.nodes) + 1
        nid = f"U-{idx:02d}"
        while nid in existing:
            idx += 1
            nid = f"U-{idx:02d}"
        offset = 60.0 * (len(self._state.circuit.nodes) % 4)
        node = NodeSpec(
            id=nid,
            name=nid,
            unit_type=UnitType.FLOTATION_CELL,
            pos_x=220.0 + offset,
            pos_y=180.0 + offset,
        )
        self._state.circuit.nodes.append(node)
        self._mark_topology_changed()
        self.canvas.load_circuit(self._state.circuit)
        self.canvas.select_node(nid)
        self._refresh_incidence_matrix()
        self._refresh_validation_banner(mark_status=False)

    def _on_delete_node(self) -> None:
        if not self._selected_node_id:
            return
        nid = self._selected_node_id
        self._state.circuit.nodes = [n for n in self._state.circuit.nodes if n.id != nid]
        for stream in self._state.circuit.streams:
            if stream.from_node == nid:
                stream.from_node = None
            if stream.to_node == nid:
                stream.to_node = None
        self._selected_node_id = None
        self.props_group.setVisible(False)
        self._mark_topology_changed()
        self.canvas.load_circuit(self._state.circuit)
        self._refresh_incidence_matrix()
        self._refresh_validation_banner(mark_status=False)

    def _on_add_stream(self) -> None:
        dlg = _ConnectStreamDialog(self._state.circuit, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            stream = dlg.build_stream()
            self._state.circuit.streams.append(stream)
            self._mark_topology_changed()
            self.canvas.load_circuit(self._state.circuit)
            self.canvas.select_stream(stream.id)
            self._refresh_incidence_matrix()
            self._refresh_validation_banner(mark_status=False)

    def _on_canvas_stream_picked(self, from_node: Optional[str], to_node: Optional[str]) -> None:
        dlg = _ConnectStreamDialog(
            self._state.circuit,
            default_from=from_node,
            default_to=to_node,
            parent=self,
        )
        if dlg.exec() == QDialog.DialogCode.Accepted:
            stream = dlg.build_stream()
            self._state.circuit.streams.append(stream)
            self._mark_topology_changed()
            self.canvas.load_circuit(self._state.circuit)
            self.canvas.select_stream(stream.id)
            self._refresh_incidence_matrix()
            self._refresh_validation_banner(mark_status=False)

    def _on_delete_stream(self) -> None:
        if not self._selected_stream_id:
            return
        sid = self._selected_stream_id
        self._state.circuit.streams = [s for s in self._state.circuit.streams if s.id != sid]
        self._selected_stream_id = None
        self.props_group.setVisible(False)
        self._mark_topology_changed()
        self.canvas.load_circuit(self._state.circuit)
        self._refresh_incidence_matrix()
        self._refresh_validation_banner(mark_status=False)

    def _on_auto_layout(self) -> None:
        self.canvas.auto_layout()

    def _on_node_selected(self, node_id: str) -> None:
        self._selected_node_id = node_id
        self._selected_stream_id = None
        self._refresh_selected_editor()
        self._push_node_to_inspector(node_id)

    def _on_stream_selected(self, stream_id: str) -> None:
        self._selected_stream_id = stream_id
        self._selected_node_id = None
        self._refresh_selected_editor()
        self._push_stream_to_inspector(stream_id)

    def _on_selection_cleared(self) -> None:
        self._selected_node_id = None
        self._selected_stream_id = None
        self.props_group.setVisible(False)

    def _refresh_selected_editor(self) -> None:
        if self._selected_node_id:
            node = next(
                (n for n in self._state.circuit.nodes if n.id == self._selected_node_id),
                None,
            )
            if node is None:
                self.props_group.setVisible(False)
                return
            self.props_group.setTitle(f"{self._i18n.t('step01.node_id')}: {node.id}")
            self.lbl_prop_id.setText(self._i18n.t("step01.node_id"))
            self.lbl_prop_name.setText(self._i18n.t("step01.node_name"))
            self.lbl_prop_type_role.setText(self._i18n.t("step01.node_type"))
            self.prop_id_edit.setText(node.id)
            self.prop_name_edit.setText(node.name)
            self.prop_type_role_combo.clear()
            for ut in UnitType:
                self.prop_type_role_combo.addItem(self._i18n.t(f"enums.unit_type.{ut.value}"), ut)
            set_combo_by_data(self.prop_type_role_combo, node.unit_type)
            self.lbl_prop_from.setVisible(False)
            self.prop_from_combo.setVisible(False)
            self.lbl_prop_to.setVisible(False)
            self.prop_to_combo.setVisible(False)
            self.props_group.setVisible(True)
            return

        if self._selected_stream_id:
            stream = next(
                (s for s in self._state.circuit.streams if s.id == self._selected_stream_id),
                None,
            )
            if stream is None:
                self.props_group.setVisible(False)
                return
            self.props_group.setTitle(f"{self._i18n.t('step01.stream_id')}: {stream.id}")
            self.lbl_prop_id.setText(self._i18n.t("step01.stream_id"))
            self.lbl_prop_name.setText(self._i18n.t("step01.stream_name"))
            self.lbl_prop_type_role.setText(self._i18n.t("step01.stream_role"))
            self.lbl_prop_from.setText(self._i18n.t("step01.from_node"))
            self.lbl_prop_to.setText(self._i18n.t("step01.to_node"))
            self.prop_id_edit.setText(stream.id)
            self.prop_name_edit.setText(stream.name)

            self.prop_type_role_combo.clear()
            for role in StreamRole:
                self.prop_type_role_combo.addItem(
                    self._i18n.t(f"enums.stream_role.{role.value}"), role
                )
            set_combo_by_data(self.prop_type_role_combo, stream.role)

            ext_label = self._i18n.t("step01.external_boundary")
            self.prop_from_combo.clear()
            self.prop_to_combo.clear()
            self.prop_from_combo.addItem(ext_label, None)
            self.prop_to_combo.addItem(ext_label, None)
            for node in self._state.circuit.nodes:
                self.prop_from_combo.addItem(node.id, node.id)
                self.prop_to_combo.addItem(node.id, node.id)
            set_combo_by_data(self.prop_from_combo, stream.from_node)
            set_combo_by_data(self.prop_to_combo, stream.to_node)

            self.lbl_prop_from.setVisible(True)
            self.prop_from_combo.setVisible(True)
            self.lbl_prop_to.setVisible(True)
            self.prop_to_combo.setVisible(True)
            self.props_group.setVisible(True)
            return

        self.props_group.setVisible(False)

    def _on_apply_selected_properties(self) -> None:
        if self._selected_node_id:
            node = next(
                (n for n in self._state.circuit.nodes if n.id == self._selected_node_id),
                None,
            )
            if node is None:
                return
            old_id = node.id
            new_id = self.prop_id_edit.text().strip() or old_id
            node.id = new_id
            node.name = self.prop_name_edit.text().strip() or new_id
            ut = self.prop_type_role_combo.currentData()
            if isinstance(ut, UnitType):
                node.unit_type = ut
            if new_id != old_id:
                for s in self._state.circuit.streams:
                    if s.from_node == old_id:
                        s.from_node = new_id
                    if s.to_node == old_id:
                        s.to_node = new_id
                self._selected_node_id = new_id
            self._mark_topology_changed()
            self.canvas.load_circuit(self._state.circuit)
            self.canvas.select_node(new_id)
            self._refresh_incidence_matrix()
            self._refresh_validation_banner(mark_status=False)
            self._push_node_to_inspector(new_id)
            return

        if self._selected_stream_id:
            stream = next(
                (s for s in self._state.circuit.streams if s.id == self._selected_stream_id),
                None,
            )
            if stream is None:
                return
            new_id = self.prop_id_edit.text().strip() or stream.id
            stream.id = new_id
            stream.name = self.prop_name_edit.text().strip() or new_id
            role = self.prop_type_role_combo.currentData()
            if isinstance(role, StreamRole):
                stream.role = role
            stream.from_node = self.prop_from_combo.currentData()
            stream.to_node = self.prop_to_combo.currentData()
            self._selected_stream_id = new_id
            self._mark_topology_changed()
            self.canvas.load_circuit(self._state.circuit)
            self.canvas.select_stream(new_id)
            self._refresh_incidence_matrix()
            self._refresh_validation_banner(mark_status=False)
            self._push_stream_to_inspector(new_id)

    def _push_node_to_inspector(self, node_id: str) -> None:
        node = next((n for n in self._state.circuit.nodes if n.id == node_id), None)
        if node is None:
            return
        inflows = [s.id for s in self._state.circuit.streams if s.to_node == node_id]
        outflows = [s.id for s in self._state.circuit.streams if s.from_node == node_id]
        payload = InspectorPayload(
            title=f"{self._i18n.t('step01.node_id')}: {node.id}",
            properties=[
                (self._i18n.t("step01.node_id"), node.id),
                (self._i18n.t("step01.node_name"), node.name),
                (self._i18n.t("step01.node_type"), self._i18n.t(f"enums.unit_type.{node.unit_type.value}")),
                (self._i18n.t("step04.col_inflow"), ", ".join(inflows) or "-"),
                (self._i18n.t("step04.col_outflow"), ", ".join(outflows) or "-"),
            ],
        )
        self.inspector_requested.emit(payload)

    def _push_stream_to_inspector(self, stream_id: str) -> None:
        stream = next((s for s in self._state.circuit.streams if s.id == stream_id), None)
        if stream is None:
            return
        ext = self._i18n.t("step01.external_boundary")
        payload = InspectorPayload(
            title=f"{self._i18n.t('step01.stream_id')}: {stream.id}",
            properties=[
                (self._i18n.t("step01.stream_id"), stream.id),
                (self._i18n.t("step01.stream_name"), stream.name),
                (self._i18n.t("step01.stream_role"), self._i18n.t(f"enums.stream_role.{stream.role.value}")),
                (self._i18n.t("step01.from_node"), stream.from_node or ext),
                (self._i18n.t("step01.to_node"), stream.to_node or ext),
            ],
        )
        self.inspector_requested.emit(payload)

    def _refresh_incidence_matrix(self) -> None:
        mat, node_ids, stream_ids = build_incidence_matrix(self._state.circuit)
        palette = get_theme_palette(self._theme)
        self.matrix_table.setRowCount(len(node_ids))
        self.matrix_table.setColumnCount(len(stream_ids))
        self.matrix_table.setVerticalHeaderLabels(node_ids)
        self.matrix_table.setHorizontalHeaderLabels(stream_ids)

        for r in range(len(node_ids)):
            for c in range(len(stream_ids)):
                val = int(round(float(mat[r, c])))
                if val > 0:
                    text = "+1"
                    color = palette["validated"]
                    bold = True
                elif val < 0:
                    text = "-1"
                    color = palette["error"]
                    bold = True
                else:
                    text = "0"
                    color = palette["text_secondary"]
                    bold = False
                self.matrix_table.setItem(
                    r,
                    c,
                    make_text_item(
                        text,
                        align=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                        color_hex=color,
                        mono=True,
                        bold=bold,
                    ),
                )
        self.matrix_table.resizeColumnsToContents()

    def _refresh_validation_banner(self, mark_status: bool) -> list[str]:
        circuit = self._state.circuit
        errors = validate_topology(circuit)
        if errors:
            self.status_banner.setText(
                self._i18n.t("step01.topology_invalid", reason=errors[0])
            )
            apply_status_role(self.status_banner, "error")
            if mark_status:
                self._state.step_statuses[0] = StepStatus.BLOCKED
                self._state.step_block_reasons[0] = errors[0]
        else:
            self.status_banner.setText(
                self._i18n.t(
                    "step01.topology_valid",
                    nodes=len(circuit.nodes),
                    streams=len(circuit.streams),
                    species=len(circuit.species),
                )
            )
            if mark_status:
                apply_status_role(self.status_banner, "validated")
                self._state.step_statuses[0] = StepStatus.VALIDATED
                self._state.step_block_reasons.pop(0, None)
            else:
                role = "validated" if self._state.step_statuses[0] == StepStatus.VALIDATED else "pending"
                apply_status_role(self.status_banner, role)
        return errors

    def validate_step(self) -> bool:
        """Validate current circuit topology and update Step 01 completion state."""
        errors = self._refresh_validation_banner(mark_status=True)
        ok = len(errors) == 0
        self.inspector_requested.emit(self.build_inspector_payload())
        self.step_validated.emit(ok)
        return ok

    def _on_next_clicked(self) -> None:
        if self.validate_step():
            self.next_step_requested.emit()

    def build_inspector_payload(self) -> InspectorPayload:
        """Return Step 01 summary properties and topology errors for InspectorDock."""
        c = self._state.circuit
        errors = validate_topology(c)
        alerts = [("error", err) for err in errors]
        return InspectorPayload(
            title=self._i18n.t("workflow.step_01_title"),
            properties=[
                (self._i18n.t("step01.project_name"), c.name),
                (self._i18n.t("step01.mass_unit"), self._i18n.t(f"enums.mass_unit.{c.mass_unit.value}")),
                (self._i18n.t("step04.num_nodes"), str(len(c.nodes))),
                (self._i18n.t("step04.num_streams"), str(len(c.streams))),
                (self._i18n.t("step04.num_species"), str(len(c.species))),
            ],
            alerts=alerts,
            blocked_reason=errors[0] if errors else None,
        )
