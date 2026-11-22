"""Step 03 — Measurement uncertainty (sigma) configuration and AMIRA P754 presets."""

from __future__ import annotations

import math
from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from app.i18n.manager import get_i18n
from core.etl import build_amira_p754_presets, compute_empirical_uncertainties
from core.models import (
    StepStatus,
    UncertaintyMode,
    UncertaintyModel,
    UncertaintyScale,
    UncertaintySpec,
)
from core.observability import build_variable_catalog

from .ui_common import (
    FlowLayout,
    InspectorPayload,
    ProjectState,
    apply_status_role,
    autosize_table_headers,
    configure_dense_table,
    get_theme_palette,
    load_svg_icon,
    make_numeric_item,
    make_text_item,
    monospace_font,
    set_combo_by_data,
    ui_font,
)


class UncertaintyView(QWidget):
    """Step 03 view for configuring per-variable standard deviations (AMIRA P754, empirical, manual)."""

    uncertainty_validated = Signal(bool)
    inspector_requested = Signal(object)
    prev_step_requested = Signal()
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
        self._working_model: Optional[UncertaintyModel] = None

        self._build_ui()
        self.retranslate_ui()
        self.refresh_from_state()

    @staticmethod
    def _lock_vertical_fixed(
        widget: QWidget, h_policy: Optional[QSizePolicy.Policy] = None
    ) -> None:
        sp = widget.sizePolicy()
        if h_policy is not None:
            sp.setHorizontalPolicy(h_policy)
        sp.setVerticalPolicy(QSizePolicy.Policy.Fixed)
        widget.setSizePolicy(sp)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        # Fixed Header
        self.header_title = QLabel(self)
        self.header_title.setFont(ui_font(11.0, bold=True))
        self._lock_vertical_fixed(self.header_title, QSizePolicy.Policy.Ignored)
        root.addWidget(self.header_title)

        # Scrollable Central Body
        self.scroll_area = QScrollArea(self)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.scroll_area.viewport().setAutoFillBackground(False)

        body = QWidget()
        body.setAutoFillBackground(False)
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 4, 0)
        body_layout.setSpacing(8)
        self.scroll_area.setWidget(body)

        # Controls Group
        self.controls_group = QGroupBox(body)
        self.controls_group.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        ctrl_layout = FlowLayout(self.controls_group, margin=8, h_spacing=8, v_spacing=6)

        self.apply_amira_btn = QPushButton(self.controls_group)
        self.apply_amira_btn.setProperty("compact", True)
        self._lock_vertical_fixed(self.apply_amira_btn, QSizePolicy.Policy.Minimum)
        self.estimate_empirical_btn = QPushButton(self.controls_group)
        self.estimate_empirical_btn.setProperty("compact", True)
        self._lock_vertical_fixed(self.estimate_empirical_btn, QSizePolicy.Policy.Minimum)

        alpha_box = QWidget(self.controls_group)
        alpha_box.setAutoFillBackground(False)
        self._lock_vertical_fixed(alpha_box, QSizePolicy.Policy.Maximum)
        alpha_box_layout = QHBoxLayout(alpha_box)
        alpha_box_layout.setContentsMargins(0, 0, 0, 0)
        alpha_box_layout.setSpacing(6)
        self.alpha_lbl = QLabel(alpha_box)
        self._lock_vertical_fixed(self.alpha_lbl)
        self.alpha_spin = QDoubleSpinBox(alpha_box)
        self.alpha_spin.setRange(0.001, 0.250)
        self.alpha_spin.setSingleStep(0.005)
        self.alpha_spin.setDecimals(3)
        self.alpha_spin.setValue(0.050)
        self.alpha_spin.setFont(monospace_font(9.5))
        self._lock_vertical_fixed(self.alpha_spin, QSizePolicy.Policy.Fixed)
        alpha_box_layout.addWidget(self.alpha_lbl)
        alpha_box_layout.addWidget(self.alpha_spin)

        self.validate_sigma_btn = QPushButton(self.controls_group)
        self.validate_sigma_btn.setProperty("accent", True)
        self.validate_sigma_btn.setProperty("compact", True)
        self._lock_vertical_fixed(self.validate_sigma_btn, QSizePolicy.Policy.Minimum)

        ctrl_layout.addWidget(self.apply_amira_btn)
        ctrl_layout.addWidget(self.estimate_empirical_btn)
        ctrl_layout.addWidget(alpha_box)
        ctrl_layout.addWidget(self.validate_sigma_btn)
        body_layout.addWidget(self.controls_group)

        # AMIRA P754 Industrial Reference Strip
        self.preset_group = QGroupBox(body)
        self.preset_group.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        preset_layout = FlowLayout(self.preset_group, margin=8, h_spacing=12, v_spacing=6)
        self.preset_labels: list[tuple[str, QLabel]] = []
        for key in (
            "step03.preset_belt_weigher",
            "step03.preset_pulp_flow",
            "step03.preset_feed_assay",
            "step03.preset_conc_assay",
            "step03.preset_tail_assay",
            "step03.preset_precious_assay",
        ):
            lbl = QLabel(self.preset_group)
            lbl.setProperty("role", "secondary")
            self._lock_vertical_fixed(lbl)
            preset_layout.addWidget(lbl)
            self.preset_labels.append((key, lbl))
        body_layout.addWidget(self.preset_group)

        # Uncertainty Matrix Table
        self.sigma_table = QTableWidget(0, 6, body)
        self.sigma_table.setObjectName("UncertaintyMatrixTable")
        configure_dense_table(
            self.sigma_table, stretch_last=True, editable=True, has_embedded_widgets=True
        )
        self.sigma_table.setMinimumHeight(240)
        self.sigma_table.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        body_layout.addWidget(self.sigma_table, 1)

        root.addWidget(self.scroll_area, 1)

        # Fixed Footer banner + nav
        footer = QHBoxLayout()
        footer.setSpacing(8)
        self.status_banner = QLabel(self)
        self.status_banner.setWordWrap(True)
        self.status_banner.setContentsMargins(8, 5, 8, 5)
        self.status_banner.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Minimum
        )
        apply_status_role(self.status_banner, "pending")
        footer.addWidget(self.status_banner, 1)

        self.prev_btn = QPushButton(self)
        self._lock_vertical_fixed(self.prev_btn, QSizePolicy.Policy.Fixed)
        self.next_btn = QPushButton(self)
        self._lock_vertical_fixed(self.next_btn, QSizePolicy.Policy.Fixed)
        footer.addWidget(self.prev_btn)
        footer.addWidget(self.next_btn)
        root.addLayout(footer)

        # Signals
        self.apply_amira_btn.clicked.connect(self.apply_amira_presets)
        self.estimate_empirical_btn.clicked.connect(self.estimate_empirical)
        self.alpha_spin.valueChanged.connect(self._on_alpha_changed)
        self.validate_sigma_btn.clicked.connect(self.validate_uncertainties)
        self.sigma_table.itemChanged.connect(self._on_table_item_changed)
        self.prev_btn.clicked.connect(self.prev_step_requested.emit)
        self.next_btn.clicked.connect(self._on_next_clicked)

    def set_theme(self, theme: str) -> None:
        self._theme = theme
        self._update_icons()

    def _update_icons(self) -> None:
        self.apply_amira_btn.setIcon(load_svg_icon("gear.svg", self._theme, size=16))
        self.estimate_empirical_btn.setIcon(load_svg_icon("scatter_plot.svg", self._theme, size=16))
        self.validate_sigma_btn.setIcon(
            load_svg_icon("check_circle.svg", self._theme, color_override="#FFFFFF", size=16)
        )

    def retranslate_ui(self) -> None:
        self._syncing = True
        try:
            self.header_title.setText(self._i18n.t("workflow.step_03_title"))
            self.controls_group.setTitle(self._i18n.t("step03.section_controls"))
            self.apply_amira_btn.setText(self._i18n.t("step03.apply_amira_btn"))
            self.estimate_empirical_btn.setText(self._i18n.t("step03.estimate_empirical_btn"))
            self.alpha_lbl.setText(self._i18n.t("step03.alpha_label"))
            self.validate_sigma_btn.setText(self._i18n.t("step03.validate_sigma_btn"))
            self.preset_group.setTitle(self._i18n.t("step03.preset_group"))
            for key, lbl in self.preset_labels:
                lbl.setText(self._i18n.t(key))

            self.sigma_table.setHorizontalHeaderLabels(
                [
                    self._i18n.t("step03.col_variable"),
                    self._i18n.t("step03.col_stream"),
                    self._i18n.t("step03.col_species"),
                    self._i18n.t("step03.col_mode"),
                    self._i18n.t("step03.col_scale"),
                    self._i18n.t("step03.col_value"),
                ]
            )
            self.prev_btn.setText(self._i18n.t("workflow.nav.prev_step"))
            self.next_btn.setText(self._i18n.t("workflow.nav.next_step"))
        finally:
            self._syncing = False

        self._update_icons()
        self._populate_table()

    def refresh_from_state(self) -> None:
        """Ensure a working uncertainty model exists for the active circuit and render table."""
        if self._state.uncertainty_model is not None:
            self._working_model = self._state.uncertainty_model.model_copy(deep=True)
        else:
            self._working_model = build_amira_p754_presets(
                self._state.circuit, alpha=float(self.alpha_spin.value())
            )
        self._syncing = True
        try:
            self.alpha_spin.setValue(self._working_model.alpha)
        finally:
            self._syncing = False

        self._populate_table()
        if self._state.uncertainty_model is not None and self._state.is_step_completed(2):
            self.status_banner.setText(
                self._i18n.t(
                    "step03.sigma_validated",
                    count=len(self._state.uncertainty_model.specs),
                )
            )
            apply_status_role(self.status_banner, "validated")
        else:
            self.status_banner.setText(self._i18n.t("workflow.step_03_desc"))
            apply_status_role(self.status_banner, "pending")

    def apply_amira_presets(self) -> None:
        """Populate table with AMIRA P754 / Gy sampling relative RSD presets."""
        self._working_model = build_amira_p754_presets(
            self._state.circuit, alpha=float(self.alpha_spin.value())
        )
        self._invalidate_downstream()
        self._populate_table()

    def estimate_empirical(self) -> None:
        """Compute empirical historical sample standard deviations from CleanedDataset."""
        if self._state.cleaned_dataset is None:
            return
        self._working_model = compute_empirical_uncertainties(
            self._state.cleaned_dataset,
            self._state.circuit,
            alpha=float(self.alpha_spin.value()),
        )
        self._invalidate_downstream()
        self._populate_table()

    def _invalidate_downstream(self) -> None:
        self._state.uncertainty_model = None
        self._state.step_statuses[2] = StepStatus.PENDING
        self._state.clear_from_step(3)
        self.status_banner.setText(self._i18n.t("workflow.step_03_desc"))
        apply_status_role(self.status_banner, "pending")

    def _on_alpha_changed(self, value: float) -> None:
        if self._syncing or self._working_model is None:
            return
        self._working_model.alpha = float(value)
        self._invalidate_downstream()

    def _populate_table(self) -> None:
        if self._working_model is None:
            return
        catalog = build_variable_catalog(self._state.circuit)
        self._syncing = True
        try:
            self.sigma_table.setRowCount(len(catalog))
            for row, (var_key, stream_id, species_id) in enumerate(catalog):
                spec = self._working_model.specs.get(var_key)
                if spec is None:
                    spec = UncertaintySpec(
                        variable_key=var_key,
                        stream_id=stream_id,
                        species_id=species_id,
                        mode=UncertaintyMode.MANUAL,
                        scale=UncertaintyScale.RELATIVE_PCT,
                        value=5.0,
                    )
                    self._working_model.specs[var_key] = spec

                self.sigma_table.setItem(row, 0, make_text_item(var_key, mono=True, user_data=var_key))
                self.sigma_table.setItem(row, 1, make_text_item(stream_id))
                self.sigma_table.setItem(row, 2, make_text_item(species_id or "-"))

                mode_cb = QComboBox(self.sigma_table)
                for um in UncertaintyMode:
                    mode_cb.addItem(self._i18n.t(f"enums.uncertainty_mode.{um.value}"), um)
                set_combo_by_data(mode_cb, spec.mode)
                mode_cb.currentIndexChanged.connect(
                    lambda _i, k=var_key, cb=mode_cb: self._on_mode_changed(k, cb)
                )
                self.sigma_table.setCellWidget(row, 3, mode_cb)

                scale_cb = QComboBox(self.sigma_table)
                for us in UncertaintyScale:
                    scale_cb.addItem(self._i18n.t(f"enums.uncertainty_scale.{us.value}"), us)
                set_combo_by_data(scale_cb, spec.scale)
                scale_cb.currentIndexChanged.connect(
                    lambda _i, r=row, k=var_key, cb=scale_cb: self._on_scale_changed(r, k, cb)
                )
                self.sigma_table.setCellWidget(row, 4, scale_cb)

                self.sigma_table.setItem(
                    row,
                    5,
                    make_numeric_item(spec.value, fmt="{:.4f}", editable=True, user_data=var_key),
                )
        finally:
            self._syncing = False
        autosize_table_headers(self.sigma_table, stretch_last=True)

    def _on_mode_changed(self, var_key: str, cb: QComboBox) -> None:
        if self._syncing or self._working_model is None:
            return
        mode = cb.currentData()
        if isinstance(mode, UncertaintyMode) and var_key in self._working_model.specs:
            self._working_model.specs[var_key].mode = mode
            self._invalidate_downstream()

    def _on_scale_changed(self, row: int, var_key: str, cb: QComboBox) -> None:
        if self._syncing or self._working_model is None:
            return
        scale = cb.currentData()
        if isinstance(scale, UncertaintyScale) and var_key in self._working_model.specs:
            self._working_model.specs[var_key].scale = scale
            self._working_model.specs[var_key].mode = UncertaintyMode.MANUAL
            mode_cb = self.sigma_table.cellWidget(row, 3)
            if isinstance(mode_cb, QComboBox):
                self._syncing = True
                set_combo_by_data(mode_cb, UncertaintyMode.MANUAL)
                self._syncing = False
            self._invalidate_downstream()

    def _on_table_item_changed(self, item) -> None:
        if self._syncing or item.column() != 5 or self._working_model is None:
            return
        row = item.row()
        key_item = self.sigma_table.item(row, 0)
        if key_item is None:
            return
        var_key = key_item.text()
        palette = get_theme_palette(self._theme)

        try:
            val = float(item.text().replace(",", ".").strip())
            if not math.isfinite(val) or val <= 0.0:
                raise ValueError("sigma <= 0")
        except ValueError:
            self._syncing = True
            item.setForeground(self._qcolor(palette["error"]))
            item.setBackground(self._qcolor(palette["error_bg"]))
            self._syncing = False
            self.status_banner.setText(self._i18n.t("step03.sigma_error"))
            apply_status_role(self.status_banner, "error")
            self._state.step_statuses[2] = StepStatus.BLOCKED
            self._state.step_block_reasons[2] = self._i18n.t("step03.sigma_error")
            return

        self._syncing = True
        item.setForeground(self._qcolor(palette["text_primary"]))
        item.setBackground(self._qcolor(palette["panel_bg"]))
        mode_cb = self.sigma_table.cellWidget(row, 3)
        if isinstance(mode_cb, QComboBox):
            set_combo_by_data(mode_cb, UncertaintyMode.MANUAL)
        self._syncing = False

        if var_key in self._working_model.specs:
            self._working_model.specs[var_key].value = val
            self._working_model.specs[var_key].mode = UncertaintyMode.MANUAL
        self._invalidate_downstream()

    @staticmethod
    def _qcolor(hex_str: str):
        from PySide6.QtGui import QColor

        return QColor(hex_str)

    def validate_uncertainties(self) -> bool:
        """Validate every variable sigma > 0 and commit UncertaintyModel to ProjectState."""
        if self._working_model is None:
            return False

        validated_specs: dict[str, UncertaintySpec] = {}
        for row in range(self.sigma_table.rowCount()):
            key_item = self.sigma_table.item(row, 0)
            stream_item = self.sigma_table.item(row, 1)
            sp_item = self.sigma_table.item(row, 2)
            val_item = self.sigma_table.item(row, 5)
            mode_cb = self.sigma_table.cellWidget(row, 3)
            scale_cb = self.sigma_table.cellWidget(row, 4)
            if not key_item or not val_item:
                continue

            var_key = key_item.text()
            stream_id = stream_item.text() if stream_item else ""
            sp_txt = sp_item.text() if sp_item else "-"
            species_id = None if sp_txt == "-" else sp_txt
            mode = mode_cb.currentData() if isinstance(mode_cb, QComboBox) else UncertaintyMode.MANUAL
            scale = scale_cb.currentData() if isinstance(scale_cb, QComboBox) else UncertaintyScale.RELATIVE_PCT

            try:
                num_val = float(val_item.text().replace(",", ".").strip())
                spec = UncertaintySpec(
                    variable_key=var_key,
                    stream_id=stream_id,
                    species_id=species_id,
                    mode=mode,
                    scale=scale,
                    value=num_val,
                )
            except Exception:
                self.status_banner.setText(self._i18n.t("step03.sigma_error"))
                apply_status_role(self.status_banner, "error")
                self._state.step_statuses[2] = StepStatus.BLOCKED
                self._state.step_block_reasons[2] = self._i18n.t("step03.sigma_error")
                self.inspector_requested.emit(self.build_inspector_payload())
                self.uncertainty_validated.emit(False)
                return False

            validated_specs[var_key] = spec

        try:
            model = UncertaintyModel(
                specs=validated_specs,
                alpha=float(self.alpha_spin.value()),
            )
        except Exception as exc:
            self.status_banner.setText(str(exc))
            apply_status_role(self.status_banner, "error")
            self._state.step_statuses[2] = StepStatus.BLOCKED
            self._state.step_block_reasons[2] = str(exc)
            self.uncertainty_validated.emit(False)
            return False

        self._working_model = model
        self._state.uncertainty_model = model
        self._state.step_statuses[2] = StepStatus.VALIDATED
        self._state.step_block_reasons.pop(2, None)
        self.status_banner.setText(
            self._i18n.t("step03.sigma_validated", count=len(model.specs))
        )
        apply_status_role(self.status_banner, "validated")
        self.inspector_requested.emit(self.build_inspector_payload())
        self.uncertainty_validated.emit(True)
        return True

    def _on_next_clicked(self) -> None:
        if self.validate_uncertainties():
            self.next_step_requested.emit()

    def build_inspector_payload(self) -> InspectorPayload:
        model = self._state.uncertainty_model or self._working_model
        if model is None:
            return InspectorPayload(title=self._i18n.t("workflow.step_03_title"))
        return InspectorPayload(
            title=self._i18n.t("workflow.step_03_title"),
            properties=[
                (self._i18n.t("step03.alpha_label"), f"{model.alpha:.3f}"),
                (self._i18n.t("step04.total_vars"), str(len(model.specs))),
            ],
            blocked_reason=self._state.step_block_reasons.get(2),
        )
