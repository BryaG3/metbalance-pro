"""Unit tests for ETL profiling, anomaly detection, confirmed cleaning, and uncertainty models."""

from __future__ import annotations

import hashlib
from pathlib import Path

import openpyxl
import pytest

from core.etl import (
    apply_cleaning_plan,
    build_amira_p754_presets,
    build_default_cleaning_plan,
    compute_empirical_uncertainties,
    compute_file_sha256,
    list_excel_sheets,
    profile_file,
)
from core.models import (
    GradeUnit,
    SpeciesSpec,
    StreamRole,
    UncertaintyMode,
    UncertaintyScale,
    make_assay_var_key,
    make_mass_var_key,
)
from core.topology import get_circuit_template

SAMPLE_DATA_DIR = Path(__file__).resolve().parents[1] / "app" / "assets" / "sample_data"


def _dirty_fixture_rows() -> tuple[list[str], list[list[object]]]:
    """Build representative dirty metallurgical rows with decimal commas, NaNs, corrupt text, and oz/t."""
    headers = [
        "Guardia",
        "Fecha",
        "Cabeza_TMS/guardia",
        "ConCu_TMS/guardia",
        "ConPb_TMS/guardia",
        "ConZn_TMS/guardia",
        "Rlv_TMS/guardia",
        "Cabeza_Ag_oz/t",
        "En_Cu_cbz_%",
        "Cabeza_Pb_%",
        "Cabeza_Zn_%",
        "ConCu_Ag_g/t",
        "ConCu_Cu_%",
        "ConCu_Pb_%",
        "ConCu_Zn_%",
        "ConPb_Ag_g/t",
        "ConPb_Cu_%",
        "ConPb_Pb_%",
        "ConPb_Zn_%",
        "ConZn_Ag_g/t",
        "ConZn_Cu_%",
        "ConZn_Pb_%",
        "ConZn_Zn_%",
        "Rlv_Ag_g/t",
        "Rlv_Cu_%",
        "Rlv_Pb_%",
        "Rlv_Zn_%",
        "F80_um",
        "P80_um",
        "Pct_Mas_65M",
        "Pct_Menos_200M",
    ]
    rows: list[list[object]] = [
        [
            "G-001", "2026-01-01",
            1000.0, 28.0, 22.0, 65.0, 885.0,
            2.0, "1,25", 1.35, 3.85,
            1450.0, 26.5, 2.4, 3.1,
            1820.0, 1.2, 54.0, 4.2,
            110.0, 0.85, 1.1, 52.5,
            18.5, 0.14, 0.22, 0.35,
            145.0, 74.0, 12.3, 66.5,
        ],
        [
            "G-002", "2026-01-02",
            995.0, 27.5, 21.8, 64.0, 881.7,
            2.5, 1.18, 1.30, 3.80,
            1410.0, 115.0, 2.3, 3.0,
            1790.0, 1.1, None, 4.1,
            108.0, 0.82, 1.0, 52.1,
            19.0, "N/D", 0.21, -0.5,
            148.0, 76.0, 13.1, 64.8,
        ],
        [
            "G-003", "2026-01-03",
            1010.0, 28.4, 22.2, 65.5, 893.9,
            3.0, 1.22, 1.33, 3.90,
            1460.0, 26.8, 2.5, 3.2,
            1830.0, 1.25, 54.5, 4.3,
            112.0, 0.88, 1.15, 52.9,
            18.0, 0.13, 0.20, 0.33,
            142.0, 72.0, 11.8, 67.2,
        ],
    ]
    return headers, rows


def _write_dirty_xlsx(path: Path) -> None:
    """Create a dirty Excel workbook on disk for ETL tests."""
    headers, rows = _dirty_fixture_rows()
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Balances"
    ws.append(headers)
    for row in rows:
        ws.append(row)
    wb.create_sheet("Metadata")
    wb.save(path)


def _write_dirty_csv(path: Path) -> None:
    """Create a semicolon-delimited dirty CSV file on disk for ETL tests."""
    headers, rows = _dirty_fixture_rows()
    lines = [";".join(headers)]
    for row in rows:
        cells = [("" if val is None else str(val)) for val in row]
        lines.append(";".join(cells))
    path.write_text("\n".join(lines), encoding="utf-8")


def _find_suggestion(profile, source_col: str):
    """Look up a ColumnMappingSuggestion by exact source column name."""
    for item in profile.mapping_suggestions:
        if item.source_column == source_col:
            return item
    raise AssertionError(f"Missing mapping suggestion for column {source_col!r}")


def test_list_excel_sheets_and_sha256(tmp_path: Path) -> None:
    """Verify sheet enumeration and lowercase SHA-256 checksum calculation."""
    xlsx_path = tmp_path / "dirty.xlsx"
    _write_dirty_xlsx(xlsx_path)

    assert list_excel_sheets(xlsx_path) == ["Balances", "Metadata"]
    expected_hash = hashlib.sha256(xlsx_path.read_bytes()).hexdigest().lower()
    assert compute_file_sha256(xlsx_path) == expected_hash


@pytest.mark.parametrize("file_kind", ["xlsx", "csv"])
def test_profile_and_confirmed_cleaning_dirty_files(tmp_path: Path, file_kind: str) -> None:
    """Verify profile_file detects all anomalies and confirmed apply_cleaning_plan cleans & converts."""
    circuit = get_circuit_template("polymetallic_4prod")
    file_path = tmp_path / f"dirty.{file_kind}"
    if file_kind == "xlsx":
        _write_dirty_xlsx(file_path)
    else:
        _write_dirty_csv(file_path)

    raw_df, profile = profile_file(file_path, circuit, sheet_name="Balances" if file_kind == "xlsx" else None)

    assert profile.row_count == 3
    assert profile.file_sha256 == compute_file_sha256(file_path)

    # Confirm profiling did NOT silently modify raw string/corrupt values in raw_df
    assert str(raw_df.loc[0, "En_Cu_cbz_%"]) == "1,25"
    assert str(raw_df.loc[1, "Rlv_Cu_%"]) == "N/D"

    anomaly_types = {a.anomaly_type for a in profile.anomalies}
    for expected_type in (
        "decimal_comma",
        "missing_value",
        "corrupt_text",
        "negative_value",
        "out_of_range",
        "unit_mismatch",
    ):
        assert expected_type in anomaly_types, f"Missing anomaly_type {expected_type} in {anomaly_types}"

    for non_met in ("F80_um", "P80_um", "Pct_Mas_65M", "Pct_Menos_200M"):
        assert non_met in profile.non_metallurgical_columns
        assert _find_suggestion(profile, non_met).is_non_metallurgical is True

    # Heuristic mapping checks
    cu_head_map = _find_suggestion(profile, "En_Cu_cbz_%")
    assert cu_head_map.target_stream_id == "Cabeza"
    assert cu_head_map.target_species_id == "Cu"

    tms_head_map = _find_suggestion(profile, "Cabeza_TMS/guardia")
    assert tms_head_map.target_stream_id == "Cabeza"
    assert tms_head_map.is_mass_flow is True

    concu_map = _find_suggestion(profile, "ConCu_Cu_%")
    assert concu_map.target_stream_id == "ConCu"
    assert concu_map.target_species_id == "Cu"

    rlv_map = _find_suggestion(profile, "Rlv_Cu_%")
    assert rlv_map.target_stream_id == "Relave"
    assert rlv_map.target_species_id == "Cu"

    ag_oz_map = _find_suggestion(profile, "Cabeza_Ag_oz/t")
    assert ag_oz_map.target_stream_id == "Cabeza"
    assert ag_oz_map.target_species_id == "Ag"
    assert ag_oz_map.detected_unit == GradeUnit.OZ_T.value

    # Unconfirmed plan MUST raise ValueError
    plan = build_default_cleaning_plan(profile)
    assert plan.confirmed is False
    with pytest.raises(ValueError):
        apply_cleaning_plan(raw_df, profile, circuit, plan)

    # Confirmed plan cleans without exceptions and logs every transformation
    plan.confirmed = True
    raw_snapshot = raw_df.copy(deep=True)
    cleaned = apply_cleaning_plan(raw_df, profile, circuit, plan)

    assert raw_df.equals(raw_snapshot), "apply_cleaning_plan must not mutate raw_df"
    assert cleaned.raw_row_count == 3
    assert cleaned.clean_row_count == 3
    assert len(cleaned.periods) == 3
    assert len(cleaned.audit_log) > 0
    assert all(entry.timestamp_iso and entry.action and entry.detail for entry in cleaned.audit_log)

    p0 = cleaned.periods[0]
    assert p0.period_id == "G-001"
    assert p0.assays["Cabeza"]["Cu"] == pytest.approx(1.25)
    expected_ag_gt = SpeciesSpec.convert_grade(2.0, GradeUnit.OZ_T, GradeUnit.G_T)
    assert p0.assays["Cabeza"]["Ag"] == pytest.approx(expected_ag_gt, rel=1e-6)

    p1 = cleaned.periods[1]
    assert p1.period_id == "G-002"
    assert p1.assays["Relave"]["Cu"] is None
    assert p1.assays["ConPb"]["Pb"] is None
    assert p1.assays["Relave"]["Zn"] is None
    assert p1.assays["ConCu"]["Cu"] is None

    # Test missing_strategy='drop_row' and 'interpolate'
    drop_plan = plan.model_copy(update={"missing_strategy": "drop_row"})
    cleaned_drop = apply_cleaning_plan(raw_df, profile, circuit, drop_plan)
    assert cleaned_drop.clean_row_count == 2
    assert [p.period_id for p in cleaned_drop.periods] == ["G-001", "G-003"]

    interp_plan = plan.model_copy(update={"missing_strategy": "interpolate"})
    cleaned_interp = apply_cleaning_plan(raw_df, profile, circuit, interp_plan)
    assert cleaned_interp.clean_row_count == 3
    assert cleaned_interp.periods[1].assays["ConPb"]["Pb"] == pytest.approx(54.25, rel=1e-4)


@pytest.mark.parametrize(
    "filename,template_id,expected_rows",
    [
        ("dataset_a_polimetalico_4prod.xlsx", "polymetallic_4prod", 60),
        ("dataset_a_polimetalico_4prod.csv", "polymetallic_4prod", 60),
        ("dataset_b_multinodo_rsc_cu_mo.xlsx", "multinode_rsc", 30),
        ("dataset_b_multinodo_rsc_cu_mo.csv", "multinode_rsc", 30),
    ],
)
def test_generated_sample_datasets_profile_and_clean(
    filename: str,
    template_id: str,
    expected_rows: int,
) -> None:
    """Verify both generated sample datasets (Excel and CSV) profile and clean end-to-end."""
    file_path = SAMPLE_DATA_DIR / filename
    assert file_path.exists(), f"Missing generated sample dataset: {file_path}"

    circuit = get_circuit_template(template_id)
    raw_df, profile = profile_file(file_path, circuit)
    assert profile.row_count == expected_rows

    if template_id == "polymetallic_4prod":
        assert len(profile.anomalies) > 0
        for col in ("F80_um", "P80_um", "Pct_Mas_65M", "Pct_Menos_200M"):
            assert col in profile.non_metallurgical_columns

    plan = build_default_cleaning_plan(profile)
    plan.confirmed = True
    cleaned = apply_cleaning_plan(raw_df, profile, circuit, plan)
    assert len(cleaned.periods) == expected_rows


def test_amira_p754_presets_and_empirical_uncertainties() -> None:
    """Verify AMIRA P754 presets and empirical uncertainty estimation produce positive valid specs."""
    circuit = get_circuit_template("polymetallic_4prod")
    amira = build_amira_p754_presets(circuit, alpha=0.05)

    assert amira.alpha == pytest.approx(0.05)
    for stream in circuit.streams:
        mk = make_mass_var_key(stream.id)
        assert mk in amira.specs
        mspec = amira.specs[mk]
        assert mspec.mode == UncertaintyMode.PRESET_AMIRA
        assert mspec.scale == UncertaintyScale.RELATIVE_PCT
        assert mspec.value > 0.0
        if stream.role == StreamRole.FEED:
            assert mspec.value == pytest.approx(2.0)
        elif stream.role in (StreamRole.CONCENTRATE, StreamRole.TAILINGS):
            assert mspec.value == pytest.approx(3.0)

        for sp in circuit.species:
            ak = make_assay_var_key(stream.id, sp.id)
            assert ak in amira.specs
            aspec = amira.specs[ak]
            assert aspec.mode == UncertaintyMode.PRESET_AMIRA
            assert aspec.scale == UncertaintyScale.RELATIVE_PCT
            assert aspec.value > 0.0
            if sp.id in ("Au", "Ag"):
                assert aspec.value == pytest.approx(12.0)

    # Empirical uncertainty from cleaned Dataset A
    raw_df, profile = profile_file(SAMPLE_DATA_DIR / "dataset_a_polimetalico_4prod.xlsx", circuit)
    plan = build_default_cleaning_plan(profile)
    plan.confirmed = True
    cleaned = apply_cleaning_plan(raw_df, profile, circuit, plan)

    empirical = compute_empirical_uncertainties(cleaned, circuit, alpha=0.05)
    assert empirical.alpha == pytest.approx(0.05)
    assert len(empirical.specs) == len(amira.specs)
    for key, spec in empirical.specs.items():
        assert spec.mode == UncertaintyMode.EMPIRICAL
        assert spec.scale == UncertaintyScale.ABSOLUTE
        assert spec.value > 0.0


def test_bare_tms_guardia_maps_to_feed(tmp_path: Path) -> None:
    """Verify bare heuristic header 'TMS/guardia' maps to primary feed mass flow."""
    circuit = get_circuit_template("polymetallic_4prod")
    csv_path = tmp_path / "bare_tms.csv"
    csv_path.write_text(
        "Guardia,TMS/guardia,En_Cu_cbz_%,ConCu_Cu_%,Rlv_Cu_%\n"
        "G-01,1500.0,1.1,26.0,0.10\n",
        encoding="utf-8",
    )
    _df, profile = profile_file(csv_path, circuit)
    tms_map = _find_suggestion(profile, "TMS/guardia")
    assert tms_map.target_stream_id == "Cabeza"
    assert tms_map.is_mass_flow is True
