"""Pydantic v2 domain contracts and metallurgical unit helpers for MetBalance Pro.

This module belongs to the headless core layer and MUST NEVER import PySide6.
"""

from __future__ import annotations

import math
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator


class _StrEnum(str, Enum):
    """Base string enum whose string representation matches its value."""

    def __str__(self) -> str:
        return str(self.value)


class MassUnit(_StrEnum):
    """Supported dry solids mass flow units."""

    T_H = "t/h"
    T_D = "t/d"
    TMS_GUARDIA = "TMS/guardia"


class GradeUnit(_StrEnum):
    """Supported metallurgical assay / grade units."""

    PERCENT = "%"
    G_T = "g/t"
    PPM = "ppm"
    OZ_T = "oz/t"


class StreamRole(_StrEnum):
    """Role of a stream within a metallurgical circuit."""

    FEED = "feed"
    CONCENTRATE = "concentrate"
    TAILINGS = "tailings"
    INTERNAL = "internal"


class UnitType(_StrEnum):
    """Process unit types supported by the flowsheet editor."""

    MIXER_SPLITTER = "mixer_splitter"
    FLOTATION_CELL = "flotation_cell"
    CONCENTRATOR = "concentrator"
    ROUGHER = "rougher"
    SCAVENGER = "scavenger"
    CLEANER = "cleaner"


class VariableClass(_StrEnum):
    """Observability and redundancy classification of a balance variable."""

    MEASURED_REDUNDANT = "measured_redundant"
    MEASURED_NON_REDUNDANT = "measured_non_redundant"
    UNMEASURED_OBSERVABLE = "unmeasured_observable"
    UNMEASURED_UNOBSERVABLE = "unmeasured_unobservable"


class UncertaintyMode(_StrEnum):
    """Source mode for variable standard deviation estimation."""

    PRESET_AMIRA = "preset_amira"
    EMPIRICAL = "empirical"
    MANUAL = "manual"


class UncertaintyScale(_StrEnum):
    """Scale in which uncertainty is expressed."""

    RELATIVE_PCT = "relative_pct"
    ABSOLUTE = "absolute"


class StepStatus(_StrEnum):
    """Status indicator for each step in the sequential engineering workflow."""

    PENDING = "pending"
    WARNINGS = "warnings"
    VALIDATED = "validated"
    BLOCKED = "blocked"


class GEDVerdict(_StrEnum):
    """Global Error Detection (chi-square) diagnostic verdict."""

    PASS = "pass"
    WARNING = "warning"
    FAIL = "fail"
    SKIPPED = "skipped"


_GRADE_FRACTION_FACTORS: dict[GradeUnit, float] = {
    GradeUnit.PERCENT: 1e-2,
    GradeUnit.G_T: 1e-6,
    GradeUnit.PPM: 1e-6,
    GradeUnit.OZ_T: 31.1034768e-6,
}


def make_mass_var_key(stream_id: str) -> str:
    """Return canonical variable key for a stream mass flow."""
    return f"M:{stream_id}"


def make_assay_var_key(stream_id: str, species_id: str) -> str:
    """Return canonical variable key for a stream species assay."""
    return f"X:{stream_id}:{species_id}"


class SpeciesSpec(BaseModel):
    """Chemical species / assayed element tracked in the metallurgical balance."""

    id: str
    name: str
    unit: GradeUnit

    def to_fraction_factor(self) -> float:
        """Return multiplicative factor converting this species' unit to mass fraction."""
        return _GRADE_FRACTION_FACTORS[GradeUnit(self.unit)]

    @staticmethod
    def convert_grade(value: float, from_unit: GradeUnit, to_unit: GradeUnit) -> float:
        """Convert an assay value between any two supported GradeUnit values."""
        src = GradeUnit(from_unit)
        dst = GradeUnit(to_unit)
        if src == dst:
            return float(value)
        fraction = float(value) * _GRADE_FRACTION_FACTORS[src]
        return fraction / _GRADE_FRACTION_FACTORS[dst]


class NodeSpec(BaseModel):
    """Process unit / junction node in the metallurgical flowsheet."""

    id: str
    name: str
    unit_type: UnitType
    pos_x: float = 0.0
    pos_y: float = 0.0


class StreamSpec(BaseModel):
    """Directed material stream connecting flowsheet nodes or system boundaries."""

    id: str
    name: str
    role: StreamRole
    from_node: Optional[str] = None
    to_node: Optional[str] = None


class CircuitSpec(BaseModel):
    """Complete topological and chemical specification of a plant circuit."""

    name: str
    description: str
    mass_unit: MassUnit
    nodes: list[NodeSpec]
    streams: list[StreamSpec]
    species: list[SpeciesSpec]


class MeasurementPoint(BaseModel):
    """Single campaign / shift / day observation of mass flows and assays."""

    period_id: str
    timestamp: Optional[str] = None
    mass_flows: dict[str, Optional[float]] = Field(default_factory=dict)
    assays: dict[str, dict[str, Optional[float]]] = Field(default_factory=dict)


class UncertaintySpec(BaseModel):
    """Measurement standard deviation specification for one variable."""

    variable_key: str
    stream_id: str
    species_id: Optional[str] = None
    mode: UncertaintyMode
    scale: UncertaintyScale
    value: float

    @field_validator("value")
    @classmethod
    def _validate_positive_value(cls, v: float) -> float:
        if not math.isfinite(v) or v <= 0.0:
            raise ValueError("Uncertainty value must be finite and strictly greater than 0.")
        return float(v)


class UncertaintyModel(BaseModel):
    """Collection of per-variable uncertainties and global test significance level."""

    specs: dict[str, UncertaintySpec] = Field(default_factory=dict)
    alpha: float = 0.05

    @field_validator("alpha")
    @classmethod
    def _validate_alpha(cls, v: float) -> float:
        if not math.isfinite(v) or not (0.0 < v < 1.0):
            raise ValueError("Significance level alpha must satisfy 0 < alpha < 1.")
        return float(v)


class AnomalyRecord(BaseModel):
    """Data quality issue detected during pre-ingestion ETL profiling."""

    row_index: Optional[int] = None
    column: str
    raw_value: Any = None
    anomaly_type: str
    severity: str
    detail: str
    suggested_action: str


class ColumnMappingSuggestion(BaseModel):
    """Heuristic mapping from a raw file column to a circuit variable."""

    source_column: str
    target_stream_id: Optional[str] = None
    target_species_id: Optional[str] = None
    is_mass_flow: bool = False
    detected_unit: Optional[str] = None
    is_non_metallurgical: bool = False
    confidence: float = 0.0


class DataProfileReport(BaseModel):
    """Full pre-cleaning diagnostic report produced by the ETL profiler."""

    source_path: str
    file_sha256: str
    sheet_name: Optional[str] = None
    delimiter: Optional[str] = None
    encoding: Optional[str] = None
    row_count: int
    columns: list[str]
    anomalies: list[AnomalyRecord]
    mapping_suggestions: list[ColumnMappingSuggestion]
    non_metallurgical_columns: list[str]


class CleaningPlan(BaseModel):
    """Explicit user-confirmed transformation plan applied to raw data."""

    mappings: list[ColumnMappingSuggestion]
    fix_decimal_commas: bool = True
    coerce_corrupt_text_to_nan: bool = True
    nullify_negative: bool = True
    nullify_out_of_range: bool = True
    convert_units: bool = True
    drop_non_metallurgical: bool = True
    drop_zero_or_negative_flow: bool = True
    missing_strategy: Literal["keep_as_unmeasured", "drop_row", "interpolate"] = "keep_as_unmeasured"
    confirmed: bool = False


class AuditLogEntry(BaseModel):
    """Immutable audit trail entry describing one ETL cleaning transformation."""

    timestamp_iso: str
    action: str
    column: Optional[str] = None
    affected_rows: int = 0
    detail: str


class CleanedDataset(BaseModel):
    """Validated metallurgical measurement periods plus ETL audit trail."""

    source_path: str
    file_sha256: str
    periods: list[MeasurementPoint]
    audit_log: list[AuditLogEntry]
    raw_row_count: int
    clean_row_count: int


class NodalImbalance(BaseModel):
    """Raw mass or species imbalance across a single process node."""

    node_id: str
    species_id: Optional[str] = None
    inflow: Optional[float] = None
    outflow: Optional[float] = None
    difference: Optional[float] = None
    relative_pct: Optional[float] = None


class VariableObservability(BaseModel):
    """Observability status of a single mass or assay variable."""

    variable_key: str
    stream_id: str
    species_id: Optional[str] = None
    is_measured: bool
    classification: VariableClass


class ObservabilityReport(BaseModel):
    """Pre-reconciliation observability, rank, DOF, and nodal balance diagnostics."""

    num_nodes: int
    num_streams: int
    num_species: int
    total_variables: int
    measured_count: int
    unmeasured_count: int
    rank_constraints: int
    rank_unmeasured: int
    degrees_of_freedom: int
    redundancy_dof: int
    is_deterministic: bool
    can_reconcile: bool
    unobservable_variables: list[str]
    missing_measurements_hint: list[str]
    variable_statuses: list[VariableObservability]
    nodal_imbalances: list[NodalImbalance]


class VariableAdjustment(BaseModel):
    """Measured vs. reconciled adjustment and standardized residual for one variable."""

    variable_key: str
    stream_id: str
    species_id: Optional[str] = None
    unit: str
    measured: Optional[float] = None
    reconciled: float
    sigma: Optional[float] = None
    absolute_diff: Optional[float] = None
    relative_pct: Optional[float] = None
    z_score: Optional[float] = None
    is_outlier: bool = False
    classification: VariableClass


class GEDReport(BaseModel):
    """Global Error Detection (chi-square and z-score) statistical report."""

    objective_j: float
    degrees_of_freedom: int
    alpha: float
    chi2_critical: float
    p_value: float
    z_critical: float
    verdict: GEDVerdict
    suspect_variables: list[str]
    max_abs_z_variable: Optional[str] = None
    max_abs_z_value: float = 0.0


class ReconciliationResult(BaseModel):
    """Complete reconciled balance and diagnostics for a single measurement period."""

    period_id: str
    converged: bool
    solver_used: str
    iterations: int
    max_mass_residual: float
    max_species_residual: float
    reconciled_mass_flows: dict[str, float]
    reconciled_assays: dict[str, dict[str, float]]
    adjustments: list[VariableAdjustment]
    ged: GEDReport
    excluded_outliers: list[str] = Field(default_factory=list)
    status: str = "OK"


class OutlierComparisonResult(BaseModel):
    """Side-by-side comparison of reconciliation with and without suspect outliers."""

    with_outlier: ReconciliationResult
    without_outlier: ReconciliationResult
    excluded_variables: list[str]
    j_reduction: float
    verdict_improved: bool


class BatchReconciliationResult(BaseModel):
    """Aggregated reconciliation results across multiple periods/shifts."""

    results: list[ReconciliationResult]
    summary_pass_count: int
    summary_warning_count: int
    summary_fail_count: int
    summary_skipped_count: int = 0
    mean_chi2: float


class ProductKPI(BaseModel):
    """Metallurgical KPIs (nominal vs. reconciled) for a concentrate or tailings stream."""

    stream_id: str
    stream_name: str
    role: StreamRole
    mass_pull_nom_pct: Optional[float] = None
    mass_pull_rec_pct: float
    conc_ratio_nom: Optional[float] = None
    conc_ratio_rec: float
    fine_metal_nom: dict[str, Optional[float]]
    fine_metal_rec: dict[str, float]
    recovery_nom_pct: dict[str, Optional[float]]
    recovery_rec_pct: dict[str, float]


class KPIReport(BaseModel):
    """Complete metallurgical performance and unexplained metal report for a period."""

    period_id: str
    feed_mass_nom: Optional[float] = None
    feed_mass_rec: float
    products: list[ProductKPI]
    unexplained_mass_nom: Optional[float] = None
    unexplained_mass_nom_pct: Optional[float] = None
    unexplained_mass_rec: float = 0.0
    unexplained_fine_nom: dict[str, Optional[float]]
    unexplained_fine_nom_pct: dict[str, Optional[float]]
    unexplained_fine_rec: dict[str, float]


class CorporateBrandingProfile(BaseModel):
    """Persistent corporate identity profile applied to Excel and PDF exports."""

    company_name: str = "MetBalance Engineering"
    mining_unit: str = "Planta Concentradora"
    technical_lead: str = "Ingeniero Metalurgista"
    document_code: str = "MBP-BAL-001"
    primary_color: str = "#1B4965"
    logo_path: Optional[str] = None
