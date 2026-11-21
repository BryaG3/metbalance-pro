"""Step 06 — Metallurgical KPIs comparison and 4 reactive technical visualization tabs."""

from __future__ import annotations

import os
import tempfile
from typing import Optional

os.environ.setdefault("MPLCONFIGDIR", str(tempfile.gettempdir()))

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSplitter,
    QTabWidget,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from app.i18n.manager import get_i18n
from core.kpis import compute_kpis
from core.models import MeasurementPoint, ReconciliationResult, StepStatus, StreamRole

from .flowsheet_canvas import FlowsheetCanvas
from .ui_common import (
    InspectorPayload,
    ProjectState,
    configure_dense_table,
    get_theme_palette,
    make_numeric_item,
    make_text_item,
    monospace_font,
    set_combo_by_data,
    ui_font,
)


class ChartsView(QWidget):
    """Step 06 view showing KPIs table, Sankey flowsheet, Parity, Relative Adjustments, and Trends."""

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

        self._active_result: Optional[ReconciliationResult] = None
        self._active_point: Optional[MeasurementPoint] = None

        self._build_ui()
        self.retranslate_ui()
        self.refresh_from_state()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        # Header + period selector
        hdr = QHBoxLayout()
        self.header_title = QLabel(self)
        self.header_title.setFont(ui_font(11.0, bold=True))
        self.period_lbl = QLabel(self)
        self.period_combo = QComboBox(self)
        self.period_combo.setMinimumWidth(160)
        hdr.addWidget(self.header_title)
        hdr.addStretch(1)
        hdr.addWidget(self.period_lbl)
        hdr.addWidget(self.period_combo)
        root.addLayout(hdr)

        splitter = QSplitter(Qt.Orientation.Vertical, self)

        # KPIs Table Section
        self.kpi_group = QGroupBox(splitter)
        kpi_v = QVBoxLayout(self.kpi_group)
        kpi_v.setSpacing(4)

        summary_row = QHBoxLayout()
        self.feed_nom_lbl = QLabel(self.kpi_group)
        self.feed_nom_val = QLabel("-", self.kpi_group)
        self.feed_nom_val.setFont(monospace_font(9.5, bold=True))
        self.feed_rec_lbl = QLabel(self.kpi_group)
        self.feed_rec_val = QLabel("-", self.kpi_group)
        self.feed_rec_val.setFont(monospace_font(9.5, bold=True))
        self.unexp_mass_lbl = QLabel(self.kpi_group)
        self.unexp_mass_val = QLabel("-", self.kpi_group)
        self.unexp_mass_val.setFont(monospace_font(9.5, bold=True))

        for lbl in (
            self.feed_nom_lbl,
            self.feed_nom_val,
            self.feed_rec_lbl,
            self.feed_rec_val,
            self.unexp_mass_lbl,
            self.unexp_mass_val,
        ):
            summary_row.addWidget(lbl)
        summary_row.addStretch(1)
        kpi_v.addLayout(summary_row)

        self.kpi_table = QTableWidget(0, 11, self.kpi_group)
        self.kpi_table.setObjectName("KPIReportTable")
        configure_dense_table(self.kpi_table, stretch_last=True, editable=False)
        kpi_v.addWidget(self.kpi_table)
        splitter.addWidget(self.kpi_group)

        # 4 Integrated Visualization Tabs
        self.tabs = QTabWidget(splitter)
        tb = self.tabs.tabBar()
        tb.setExpanding(False)
        tb.setElideMode(Qt.TextElideMode.ElideNone)
        tb.setUsesScrollButtons(True)

        # Tab (a): Sankey FlowsheetCanvas
        sankey_tab = QWidget(self.tabs)
        sk_layout = QVBoxLayout(sankey_tab)
        self.sankey_title_lbl = QLabel(sankey_tab)
        self.sankey_title_lbl.setFont(ui_font(9.5, bold=True))
        self.sankey_canvas = FlowsheetCanvas(sankey_tab, interactive=False)
        self.sankey_canvas.setObjectName("Step06SankeyCanvas")
        sk_layout.addWidget(self.sankey_title_lbl)
        sk_layout.addWidget(self.sankey_canvas, 1)
        self.tabs.addTab(sankey_tab, "")

        # Tab (b): Parity Plot
        parity_tab = QWidget(self.tabs)
        p_layout = QVBoxLayout(parity_tab)
        self.parity_fig = Figure(figsize=(6.5, 4.0), dpi=100)
        self.parity_canvas = FigureCanvasQTAgg(self.parity_fig)
        p_layout.addWidget(self.parity_canvas, 1)
        self.tabs.addTab(parity_tab, "")

        # Tab (c): Relative Adjustments (%)
        adj_tab = QWidget(self.tabs)
        a_layout = QVBoxLayout(adj_tab)
        self.adj_fig = Figure(figsize=(6.5, 4.0), dpi=100)
        self.adj_canvas = FigureCanvasQTAgg(self.adj_fig)
        a_layout.addWidget(self.adj_canvas, 1)
        self.tabs.addTab(adj_tab, "")

        # Tab (d): Historical Trends & Chi2 Control
        trends_tab = QWidget(self.tabs)
        t_layout = QVBoxLayout(trends_tab)
        t_ctrl = QHBoxLayout()
        self.trend_sp_lbl = QLabel(trends_tab)
        self.trend_sp_combo = QComboBox(trends_tab)
        t_ctrl.addWidget(self.trend_sp_lbl)
        t_ctrl.addWidget(self.trend_sp_combo)
        t_ctrl.addStretch(1)
        t_layout.addLayout(t_ctrl)

        self.trends_fig = Figure(figsize=(6.5, 4.6), dpi=100)
        self.trends_canvas = FigureCanvasQTAgg(self.trends_fig)
        t_layout.addWidget(self.trends_canvas, 1)
        self.tabs.addTab(trends_tab, "")

        splitter.addWidget(self.tabs)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 3)
        root.addWidget(splitter, 1)

        # Navigation footer
        footer = QHBoxLayout()
        footer.addStretch(1)
        self.prev_btn = QPushButton(self)
        self.next_btn = QPushButton(self)
        self.next_btn.setProperty("accent", True)
        footer.addWidget(self.prev_btn)
        footer.addWidget(self.next_btn)
        root.addLayout(footer)

        self.period_combo.currentIndexChanged.connect(self._on_period_changed)
        self.trend_sp_combo.currentIndexChanged.connect(self._render_trends_chart)
        self.prev_btn.clicked.connect(self.prev_step_requested.emit)
        self.next_btn.clicked.connect(self._on_next_clicked)

    def set_theme(self, theme: str) -> None:
        self._theme = theme
        self.sankey_canvas.set_theme(theme)
        self._render_all_charts()

    def retranslate_ui(self) -> None:
        self._syncing = True
        try:
            self.header_title.setText(self._i18n.t("workflow.step_06_title"))
            self.period_lbl.setText(self._i18n.t("step05.period_label"))
            self.kpi_group.setTitle(self._i18n.t("step06.section_kpis"))
            self.feed_nom_lbl.setText(self._i18n.t("step06.feed_mass_nom"))
            self.feed_rec_lbl.setText(self._i18n.t("step06.feed_mass_rec"))
            self.unexp_mass_lbl.setText(self._i18n.t("step06.unexplained_mass"))
            self.trend_sp_lbl.setText(self._i18n.t("step06.col_species"))

            self.kpi_table.setHorizontalHeaderLabels(
                [
                    self._i18n.t("step06.col_stream"),
                    self._i18n.t("step06.col_role"),
                    self._i18n.t("step06.col_species"),
                    self._i18n.t("step06.col_mass_pull_nom"),
                    self._i18n.t("step06.col_mass_pull_rec"),
                    self._i18n.t("step06.col_conc_ratio_nom"),
                    self._i18n.t("step06.col_conc_ratio_rec"),
                    self._i18n.t("step06.col_fine_nom"),
                    self._i18n.t("step06.col_fine_rec"),
                    self._i18n.t("step06.col_rec_nom"),
                    self._i18n.t("step06.col_rec_rec"),
                ]
            )

            self.tabs.setTabText(0, self._i18n.t("step06.tab_sankey"))
            self.tabs.setTabText(1, self._i18n.t("step06.tab_parity"))
            self.tabs.setTabText(2, self._i18n.t("step06.tab_adjustments"))
            self.tabs.setTabText(3, self._i18n.t("step06.tab_trends"))

            self.prev_btn.setText(self._i18n.t("workflow.nav.prev_step"))
            self.next_btn.setText(self._i18n.t("workflow.nav.next_step"))
        finally:
            self._syncing = False

        self.sankey_canvas.retranslate_ui()
        self._render_kpis_table()
        self._render_all_charts()

    def refresh_from_state(self) -> None:
        """Compute KPIReport for active result and update KPI table + all 4 charts."""
        self._syncing = True
        try:
            self.period_combo.clear()
            batch = self._state.batch_result
            if batch and batch.results:
                for idx, res in enumerate(batch.results):
                    self.period_combo.addItem(res.period_id, idx)
                set_combo_by_data(self.period_combo, self._state.selected_period_idx)
            elif self._state.reconciliation_result is not None:
                self.period_combo.addItem(
                    self._state.reconciliation_result.period_id,
                    self._state.selected_period_idx,
                )

            cur_sp = self.trend_sp_combo.currentData()
            self.trend_sp_combo.clear()
            for sp in self._state.circuit.species:
                self.trend_sp_combo.addItem(f"{sp.id} ({sp.unit.value})", sp.id)
            if cur_sp:
                set_combo_by_data(self.trend_sp_combo, cur_sp)
        finally:
            self._syncing = False

        self._resolve_active_period_and_compute_kpis()
        self._render_kpis_table()
        self._render_all_charts()

    def _resolve_active_period_and_compute_kpis(self) -> None:
        cleaned = self._state.cleaned_dataset
        batch = self._state.batch_result
        res = self._state.reconciliation_result
        point = self._state.current_period()

        if batch and batch.results and cleaned:
            idx = max(0, min(self._state.selected_period_idx, len(batch.results) - 1))
            res = batch.results[idx]
            if idx < len(cleaned.periods):
                point = cleaned.periods[idx]

        if res is not None and point is not None:
            self._active_result = res
            self._active_point = point
            self._state.kpi_report = compute_kpis(self._state.circuit, point, res)
            if self._state.is_step_completed(4):
                self._state.step_statuses[5] = StepStatus.VALIDATED
        else:
            self._active_result = None
            self._active_point = None
            self._state.kpi_report = None

    def _on_period_changed(self, _idx: int) -> None:
        if self._syncing:
            return
        idx = self.period_combo.currentData()
        if isinstance(idx, int):
            self._state.selected_period_idx = idx
            if self._state.batch_result and idx < len(self._state.batch_result.results):
                self._state.reconciliation_result = self._state.batch_result.results[idx]
            self._resolve_active_period_and_compute_kpis()
            self._render_kpis_table()
            self._render_all_charts()

    def _render_kpis_table(self) -> None:
        kpi = self._state.kpi_report
        na_str = self._i18n.t("common.na")
        unit = self._state.circuit.mass_unit.value

        if kpi is None:
            self.feed_nom_val.setText("-")
            self.feed_rec_val.setText("-")
            self.unexp_mass_val.setText("-")
            self.kpi_table.setRowCount(0)
            return

        self.feed_nom_val.setText(
            f"{kpi.feed_mass_nom:.2f} {unit}" if kpi.feed_mass_nom is not None else na_str
        )
        self.feed_rec_val.setText(f"{kpi.feed_mass_rec:.2f} {unit}")
        unexp_nom_str = (
            f"{kpi.unexplained_mass_nom:+.2f}" if kpi.unexplained_mass_nom is not None else na_str
        )
        self.unexp_mass_val.setText(
            f"{unexp_nom_str} / {kpi.unexplained_mass_rec:+.2e} {unit}"
        )

        rows: list[tuple] = []
        for prod in kpi.products:
            for sp in self._state.circuit.species:
                rows.append((prod, sp))

        self.kpi_table.setRowCount(len(rows))
        for r, (prod, sp) in enumerate(rows):
            self.kpi_table.setItem(r, 0, make_text_item(prod.stream_name))
            self.kpi_table.setItem(
                r, 1, make_text_item(self._i18n.t(f"enums.stream_role.{prod.role.value}"))
            )
            self.kpi_table.setItem(r, 2, make_text_item(sp.id, mono=True))
            self.kpi_table.setItem(
                r, 3, make_numeric_item(prod.mass_pull_nom_pct, fmt="{:.2f}%", na_text=na_str)
            )
            self.kpi_table.setItem(
                r, 4, make_numeric_item(prod.mass_pull_rec_pct, fmt="{:.2f}%", bold=True)
            )
            self.kpi_table.setItem(
                r, 5, make_numeric_item(prod.conc_ratio_nom, fmt="{:.2f}", na_text=na_str)
            )
            self.kpi_table.setItem(
                r, 6, make_numeric_item(prod.conc_ratio_rec, fmt="{:.2f}")
            )
            self.kpi_table.setItem(
                r,
                7,
                make_numeric_item(prod.fine_metal_nom.get(sp.id), fmt="{:.4f}", na_text=na_str),
            )
            self.kpi_table.setItem(
                r,
                8,
                make_numeric_item(prod.fine_metal_rec.get(sp.id), fmt="{:.4f}", bold=True),
            )
            self.kpi_table.setItem(
                r,
                9,
                make_numeric_item(prod.recovery_nom_pct.get(sp.id), fmt="{:.2f}%", na_text=na_str),
            )
            self.kpi_table.setItem(
                r,
                10,
                make_numeric_item(prod.recovery_rec_pct.get(sp.id), fmt="{:.2f}%", bold=True),
            )

    def _render_all_charts(self) -> None:
        self._render_sankey()
        self._render_parity_chart()
        self._render_adjustments_chart()
        self._render_trends_chart()

    def _style_axes(self, fig: Figure, ax) -> dict[str, str]:
        palette = get_theme_palette(self._theme)
        fig.patch.set_facecolor(palette["panel_bg"])
        ax.set_facecolor(palette["panel_bg"])
        ax.tick_params(colors=palette["text_primary"], labelsize=8.5)
        ax.xaxis.label.set_color(palette["text_primary"])
        ax.yaxis.label.set_color(palette["text_primary"])
        ax.title.set_color(palette["text_primary"])
        for spine in ax.spines.values():
            spine.set_color(palette["border"])
        ax.grid(True, linestyle="--", linewidth=0.5, color=palette["border"], alpha=0.7)
        return palette

    def _render_sankey(self) -> None:
        if self._active_result is not None:
            self.sankey_title_lbl.setText(
                self._i18n.t("step06.charts.sankey_title", period=self._active_result.period_id)
            )
            self.sankey_canvas.set_reconciliation_result(
                self._state.circuit, self._active_result
            )
        else:
            self.sankey_title_lbl.setText(self._i18n.t("step06.tab_sankey"))
            self.sankey_canvas.load_circuit(self._state.circuit, reconciliation=None)

    def _render_parity_chart(self) -> None:
        self.parity_fig.clear()
        ax = self.parity_fig.add_subplot(111)
        palette = self._style_axes(self.parity_fig, ax)

        res = self._active_result
        if res is None:
            self.parity_canvas.draw_idle()
            return

        x_ok, y_ok = [], []
        x_out, y_out, out_labels = [], [], []
        for adj in res.adjustments:
            if adj.measured is None or adj.sigma is None or adj.sigma <= 0.0:
                continue
            xn = adj.measured / adj.sigma
            yn = adj.reconciled / adj.sigma
            if adj.is_outlier:
                x_out.append(xn)
                y_out.append(yn)
                out_labels.append(adj.variable_key)
            else:
                x_ok.append(xn)
                y_ok.append(yn)

        all_vals = x_ok + y_ok + x_out + y_out
        if all_vals:
            lo = max(0.0, min(all_vals) - 3.0)
            hi = max(all_vals) + 3.0
            grid_x = [lo, hi]
            ax.plot(
                grid_x,
                grid_x,
                color=palette["text_primary"],
                linewidth=1.2,
                label=self._i18n.t("step06.charts.parity_line_1_1"),
            )
            ax.fill_between(
                grid_x,
                [lo - 2.0, hi - 2.0],
                [lo + 2.0, hi + 2.0],
                color=palette["accent"],
                alpha=0.12,
                label=self._i18n.t("step06.charts.parity_band_2sigma"),
            )
            ax.plot(grid_x, [lo + 2.0, hi + 2.0], linestyle="--", linewidth=0.9, color=palette["accent"])
            ax.plot(grid_x, [lo - 2.0, hi - 2.0], linestyle="--", linewidth=0.9, color=palette["accent"])

        if x_ok:
            ax.scatter(
                x_ok,
                y_ok,
                color=palette["accent"],
                s=36,
                zorder=4,
                label=self._i18n.t("step06.charts.parity_points"),
            )
        if x_out:
            ax.scatter(
                x_out,
                y_out,
                color=palette["error"],
                marker="D",
                s=52,
                zorder=5,
                label=self._i18n.t("step06.charts.parity_outliers"),
            )
            for xv, yv, lbl in zip(x_out, y_out, out_labels):
                ax.annotate(
                    lbl,
                    (xv, yv),
                    textcoords="offset points",
                    xytext=(5, 4),
                    fontsize=7.5,
                    color=palette["error"],
                )

        ax.set_title(self._i18n.t("step06.charts.parity_title", period=res.period_id), fontsize=10)
        ax.set_xlabel(self._i18n.t("step06.charts.parity_x"), fontsize=9)
        ax.set_ylabel(self._i18n.t("step06.charts.parity_y"), fontsize=9)
        leg = ax.legend(loc="upper left", fontsize=8, frameon=True)
        leg.get_frame().set_facecolor(palette["panel_bg"])
        leg.get_frame().set_edgecolor(palette["border"])
        for txt in leg.get_texts():
            txt.set_color(palette["text_primary"])
        self.parity_fig.tight_layout()
        self.parity_canvas.draw_idle()

    def _render_adjustments_chart(self) -> None:
        self.adj_fig.clear()
        ax = self.adj_fig.add_subplot(111)
        palette = self._style_axes(self.adj_fig, ax)

        res = self._active_result
        if res is None:
            self.adj_canvas.draw_idle()
            return

        measured_adj = [
            a for a in res.adjustments if a.measured is not None and a.relative_pct is not None
        ]
        if not measured_adj:
            self.adj_canvas.draw_idle()
            return

        labels = [a.variable_key for a in measured_adj]
        vals = [float(a.relative_pct) for a in measured_adj]
        z_crit = res.ged.z_critical if res.ged.degrees_of_freedom > 0 else 1.96
        colors = []
        for a in measured_adj:
            z_mag = abs(a.z_score) if a.z_score is not None else 0.0
            if a.is_outlier or z_mag > z_crit:
                colors.append(palette["error"])
            elif z_mag > 2.0:
                colors.append(palette["warning"])
            else:
                colors.append(palette["accent"])

        positions = list(range(len(labels)))
        ax.bar(positions, vals, color=colors, width=0.68, edgecolor=palette["border"], linewidth=0.5)
        ax.axhline(0.0, color=palette["text_primary"], linewidth=0.9)
        ax.set_xticks(positions)
        ax.set_xticklabels(labels, rotation=45, ha="right", fontsize=7.5)
        ax.set_title(self._i18n.t("step06.charts.adj_title", period=res.period_id), fontsize=10)
        ax.set_xlabel(self._i18n.t("step06.charts.adj_x"), fontsize=9)
        ax.set_ylabel(self._i18n.t("step06.charts.adj_y"), fontsize=9)

        from matplotlib.patches import Patch

        handles = [
            Patch(facecolor=palette["accent"], label=self._i18n.t("step06.charts.adj_legend_normal")),
            Patch(facecolor=palette["warning"], label=self._i18n.t("step06.charts.adj_legend_warning")),
            Patch(facecolor=palette["error"], label=self._i18n.t("step06.charts.adj_legend_outlier")),
        ]
        leg = ax.legend(handles=handles, loc="best", fontsize=8, frameon=True)
        leg.get_frame().set_facecolor(palette["panel_bg"])
        leg.get_frame().set_edgecolor(palette["border"])
        for txt in leg.get_texts():
            txt.set_color(palette["text_primary"])
        self.adj_fig.tight_layout()
        self.adj_canvas.draw_idle()

    def _render_trends_chart(self) -> None:
        self.trends_fig.clear()
        ax1 = self.trends_fig.add_subplot(211)
        ax2 = self.trends_fig.add_subplot(212)
        palette = self._style_axes(self.trends_fig, ax1)
        self._style_axes(self.trends_fig, ax2)

        sp_id = self.trend_sp_combo.currentData()
        if not sp_id and self._state.circuit.species:
            sp_id = self._state.circuit.species[0].id
        if not sp_id:
            self.trends_canvas.draw_idle()
            return

        batch = self._state.batch_result
        cleaned = self._state.cleaned_dataset
        results: list[ReconciliationResult] = []
        points: list[MeasurementPoint] = []
        if batch and batch.results and cleaned:
            results = batch.results
            points = cleaned.periods[: len(results)]
        elif self._active_result is not None and self._active_point is not None:
            results = [self._active_result]
            points = [self._active_point]

        if not results:
            self.trends_canvas.draw_idle()
            return

        period_labels = [r.period_id for r in results]
        x_idx = list(range(len(results)))
        nom_recs: list[Optional[float]] = []
        rec_recs: list[float] = []
        j_vals: list[float] = []
        chi2_crit = results[0].ged.chi2_critical

        for pt, res in zip(points, results):
            kpi = compute_kpis(self._state.circuit, pt, res)
            concs = [p for p in kpi.products if p.role == StreamRole.CONCENTRATE]
            if not concs:
                concs = kpi.products
            rec_sum = sum(p.recovery_rec_pct.get(sp_id, 0.0) for p in concs)
            nom_terms = [p.recovery_nom_pct.get(sp_id) for p in concs]
            nom_sum = (
                sum(float(v) for v in nom_terms if v is not None)
                if concs and all(v is not None for v in nom_terms)
                else None
            )
            rec_recs.append(rec_sum)
            nom_recs.append(nom_sum)
            j_vals.append(res.ged.objective_j)

        # Top: Recovery trend
        valid_nom_x = [i for i, v in enumerate(nom_recs) if v is not None]
        valid_nom_y = [float(nom_recs[i]) for i in valid_nom_x]
        if valid_nom_x:
            ax1.plot(
                valid_nom_x,
                valid_nom_y,
                linestyle="--",
                marker="o",
                markersize=3.5,
                color=palette["text_secondary"],
                label=self._i18n.t("step06.charts.trends_rec_nom"),
            )
        ax1.plot(
            x_idx,
            rec_recs,
            linestyle="-",
            marker="s",
            markersize=4.0,
            linewidth=1.6,
            color=palette["validated"],
            label=self._i18n.t("step06.charts.trends_rec_reconciled"),
        )
        ax1.set_title(self._i18n.t("step06.charts.trends_rec_title", species=sp_id), fontsize=9.5)
        ax1.set_ylabel(self._i18n.t("step06.charts.trends_rec_y"), fontsize=8.5)
        leg1 = ax1.legend(loc="best", fontsize=7.5, frameon=True)
        leg1.get_frame().set_facecolor(palette["panel_bg"])
        leg1.get_frame().set_edgecolor(palette["border"])
        for txt in leg1.get_texts():
            txt.set_color(palette["text_primary"])

        # Bottom: Chi2 control chart
        ax2.plot(
            x_idx,
            j_vals,
            linestyle="-",
            marker="o",
            markersize=4.0,
            color=palette["accent"],
            label=self._i18n.t("step06.charts.trends_chi2_series"),
        )
        fail_x = [i for i, jv in enumerate(j_vals) if chi2_crit > 0 and jv > chi2_crit]
        fail_y = [j_vals[i] for i in fail_x]
        if fail_x:
            ax2.scatter(fail_x, fail_y, color=palette["error"], s=45, zorder=5)

        if chi2_crit > 0:
            alpha = results[0].ged.alpha
            ax2.axhline(
                chi2_crit,
                linestyle="--",
                linewidth=1.3,
                color=palette["error"],
                label=self._i18n.t("step06.charts.trends_chi2_crit", alpha=f"{alpha:.3f}"),
            )

        step = max(1, len(period_labels) // 12)
        shown_ticks = x_idx[::step]
        shown_labels = [period_labels[i] for i in shown_ticks]
        ax1.set_xticks(shown_ticks)
        ax1.set_xticklabels(shown_labels, fontsize=7.5)
        ax2.set_xticks(shown_ticks)
        ax2.set_xticklabels(shown_labels, rotation=30, ha="right", fontsize=7.5)

        ax2.set_title(self._i18n.t("step06.charts.trends_chi2_title"), fontsize=9.5)
        ax2.set_xlabel(self._i18n.t("step06.charts.trends_chi2_x"), fontsize=8.5)
        ax2.set_ylabel(self._i18n.t("step06.charts.trends_chi2_y"), fontsize=8.5)
        leg2 = ax2.legend(loc="best", fontsize=7.5, frameon=True)
        leg2.get_frame().set_facecolor(palette["panel_bg"])
        leg2.get_frame().set_edgecolor(palette["border"])
        for txt in leg2.get_texts():
            txt.set_color(palette["text_primary"])

        self.trends_fig.tight_layout()
        self.trends_canvas.draw_idle()

    def _on_next_clicked(self) -> None:
        if self._state.kpi_report is not None:
            self._state.step_statuses[5] = StepStatus.VALIDATED
        self.next_step_requested.emit()

    def build_inspector_payload(self) -> InspectorPayload:
        kpi = self._state.kpi_report
        if kpi is None:
            return InspectorPayload(title=self._i18n.t("workflow.step_06_title"))
        props = [
            (self._i18n.t("step05.period_label"), kpi.period_id),
            (self._i18n.t("step06.feed_mass_rec"), f"{kpi.feed_mass_rec:.2f}"),
            (self._i18n.t("step06.unexplained_mass"), f"{kpi.unexplained_mass_rec:+.3e}"),
        ]
        return InspectorPayload(
            title=self._i18n.t("workflow.step_06_title"),
            properties=props,
        )
