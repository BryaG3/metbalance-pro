"""Headless ETL profiling, cleaning, audit logging, and uncertainty estimation.

This module belongs to the headless core layer and MUST NEVER import PySide6.
"""

from __future__ import annotations

import csv
import hashlib
import math
import re
import unicodedata
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Optional

import numpy as np
import openpyxl
import pandas as pd

from .models import (
    AnomalyRecord,
    AuditLogEntry,
    CircuitSpec,
    CleanedDataset,
    CleaningPlan,
    ColumnMappingSuggestion,
    DataProfileReport,
    GradeUnit,
    MassUnit,
    MeasurementPoint,
    SpeciesSpec,
    StreamRole,
    StreamSpec,
    UncertaintyMode,
    UncertaintyModel,
    UncertaintyScale,
    UncertaintySpec,
    make_assay_var_key,
    make_mass_var_key,
)

_EXCEL_EXTENSIONS = {".xlsx", ".xlsm"}
_TEXT_EXTENSIONS = {".csv", ".txt", ".tsv"}
_CANDIDATE_ENCODINGS = ("utf-8-sig", "utf-8", "cp1252", "latin-1")
_CANDIDATE_DELIMITERS = (",", ";", "\t", "|")

_MISSING_STRINGS = frozenset(
    {"", "nan", "na", "n/a", "null", "none", "-", "--", "---", "nil"}
)

_PERIOD_TOKENS = frozenset(
    {
        "periodo",
        "period",
        "guardia",
        "shift",
        "dia",
        "day",
        "turno",
        "id",
        "lote",
        "batch",
        "muestra",
        "sample",
        "campaign",
        "campana",
        "mes",
        "month",
        "semana",
        "week",
    }
)
_TIMESTAMP_TOKENS = frozenset(
    {"fecha", "date", "timestamp", "datetime", "fechahora", "hora", "time"}
)
_METADATA_MODIFIER_TOKENS = frozenset({"num", "numero", "nro", "no", "code", "codigo"})

_NON_MET_EXACT_OR_TOKEN = frozenset(
    {
        "f80",
        "p80",
        "d80",
        "d50",
        "k80",
        "t80",
        "rpm",
        "kw",
        "hp",
        "bwi",
        "wi",
        "ph",
        "orp",
        "eh",
        "sg",
        "psd",
        "solidos",
        "solids",
        "densidad",
        "density",
        "malla",
        "mesh",
        "granulometria",
        "potencia",
        "power",
        "amperaje",
        "amps",
        "presion",
        "pressure",
        "torque",
        "dosificacion",
        "reactivo",
        "reagent",
        "colector",
        "espumante",
        "xantato",
        "mibc",
        "temperatura",
        "temperature",
        "agua",
        "water",
        "aire",
        "molino",
        "mill",
        "sag",
        "hidrociclon",
        "cyclone",
    }
)
_NON_MET_PATTERNS = (
    re.compile(r"(?:^|[^a-z0-9])(f80|p80|d80|d50|k80|t80)(?:$|[^a-z0-9])"),
    re.compile(r"(?:^|[^a-z0-9])(%?\s*[+-]?\s*(?:65|100|150|200|270|325|400)\s*m)(?:$|[^a-z0-9])"),
    re.compile(r"(?:^|[^a-z0-9])(pct_?mas|pct_?menos|mas_?\d+m|menos_?\d+m)(?:$|[^a-z0-9])"),
    re.compile(r"(?:^|[^a-z0-9])(tonelaje_?molino|solidos_?%|%_?solidos|pct_?solidos|work_?index)(?:$|[^a-z0-9])"),
)

_MASS_KEYWORDS = frozenset(
    {
        "tms",
        "tph",
        "tpd",
        "flow",
        "flujo",
        "masa",
        "mass",
        "ton",
        "tonelaje",
        "tonnage",
        "caudal",
        "rate",
        "wt",
        "weight",
        "peso",
    }
)

_FEED_ALIASES = frozenset(
    {"cbz", "cabeza", "feed", "alim", "alimentacion", "f_fresh", "fresh", "head", "ore"}
)
_TAILINGS_ALIASES = frozenset(
    {"rlv", "relave", "rel", "tail", "tailing", "tailings", "cola", "colas", "final_tail"}
)
_CONC_GENERAL_ALIASES = frozenset(
    {"conc", "concentrate", "concentrado", "final_conc"}
)

_SPECIES_SYNONYMS: dict[str, tuple[str, ...]] = {
    "cu": ("cu", "copper", "cobre"),
    "ag": ("ag", "silver", "plata"),
    "au": ("au", "gold", "oro"),
    "pb": ("pb", "lead", "plomo"),
    "zn": ("zn", "zinc", "cinc"),
    "mo": ("mo", "molybdenum", "molibdeno"),
    "fe": ("fe", "iron", "hierro", "fierro"),
    "s": ("s", "sulfur", "sulphur", "azufre"),
    "as": ("as", "arsenic", "arsenico"),
    "sb": ("sb", "antimony", "antimonio"),
    "bi": ("bi", "bismuth", "bismuto"),
    "ni": ("ni", "nickel", "niquel"),
    "co": ("co", "cobalt", "cobalto"),
    "sn": ("sn", "tin", "estano"),
}
_PRECIOUS_SPECIES = frozenset({"au", "ag", "gold", "oro", "silver", "plata"})

# Decimal comma patterns (avoiding ambiguous thousands-comma forms like "1,234" or "1,234.56")
_US_THOUSANDS_RE = re.compile(r"^[+-]?\d{1,3}(?:,\d{3})+(?:\.\d+)?$")
_EU_THOUSANDS_DEC_COMMA_RE = re.compile(r"^[+-]?\d{1,3}(?:\.\d{3})+,\d+$")
_PLAIN_DEC_COMMA_RE = re.compile(r"^[+-]?\d+,\d+$")
_AMBIGUOUS_THREE_DIGIT_COMMA_RE = re.compile(r"^[+-]?\d{1,3},\d{3}$")

_OZ_T_TO_G_T = 31.1034768
_MAX_G_T = 1_000_000.0


def _strip_accents(text: str) -> str:
    """Return lowercase ASCII-folded text without combining diacritics."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).lower()


def _tokenize_ascii(text: str) -> list[str]:
    """Split normalized text into alphanumeric tokens."""
    return [tok for tok in re.split(r"[^a-z0-9]+", _strip_accents(text)) if tok]


def _utc_now_iso() -> str:
    """Return current UTC timestamp in ISO-8601 format."""
    return datetime.now(timezone.utc).isoformat()


def _make_unique_headers(raw_headers: list[Any]) -> list[str]:
    """Convert raw header cells to clean, unique column names."""
    seen: dict[str, int] = {}
    result: list[str] = []
    for idx, cell in enumerate(raw_headers):
        base = str(cell).strip() if cell is not None and str(cell).strip() else f"Column_{idx + 1}"
        count = seen.get(base, 0) + 1
        seen[base] = count
        result.append(base if count == 1 else f"{base}_{count}")
    return result


def list_excel_sheets(path: str | Path) -> list[str]:
    """Return worksheet names from an Excel workbook using read-only openpyxl."""
    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError(f"Excel file not found: {file_path}")
    wb = openpyxl.load_workbook(filename=file_path, read_only=True, data_only=True)
    try:
        return list(wb.sheetnames)
    finally:
        wb.close()


def compute_file_sha256(path: str | Path) -> str:
    """Return lowercase hex SHA-256 digest of the raw file bytes."""
    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")
    digest = hashlib.sha256()
    with file_path.open("rb") as fh:
        while chunk := fh.read(65536):
            digest.update(chunk)
    return digest.hexdigest().lower()


def _detect_text_encoding(raw_bytes: bytes) -> str:
    """Detect text file encoding among utf-8-sig, utf-8, cp1252, and latin-1."""
    if raw_bytes.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"
    for enc in ("utf-8", "cp1252", "latin-1"):
        try:
            raw_bytes.decode(enc)
            return enc
        except UnicodeDecodeError:
            continue
    return "latin-1"


def _detect_csv_delimiter(sample_text: str, suffix: str) -> str:
    """Detect delimiter among ',', ';', '\\t', and '|' from sample lines."""
    lines = [line for line in sample_text.splitlines() if line.strip()][:30]
    if not lines:
        return "\t" if suffix == ".tsv" else ","

    candidates = ("\t", ",", ";", "|") if suffix == ".tsv" else _CANDIDATE_DELIMITERS
    best_delim = candidates[0]
    best_score = (-1, -1, -1)

    for delim in candidates:
        try:
            parsed = list(csv.reader(lines, delimiter=delim))
        except csv.Error:
            continue
        if not parsed:
            continue
        header_cols = len(parsed[0])
        if header_cols <= 1:
            continue
        data_rows = parsed[1:]
        consistent_rows = sum(1 for row in data_rows if len(row) == header_cols)
        non_ragged_penalty = -sum(abs(len(row) - header_cols) for row in data_rows)
        score = (consistent_rows, non_ragged_penalty, header_cols)
        if score > best_score:
            best_score = score
            best_delim = delim

    if best_score[0] < 0:
        return "\t" if suffix == ".tsv" else ","
    return best_delim


def _read_excel_raw(
    file_path: Path, sheet_name: Optional[str]
) -> tuple[pd.DataFrame, str]:
    """Read an Excel sheet directly via openpyxl without silent coercions."""
    wb = openpyxl.load_workbook(filename=file_path, read_only=True, data_only=True)
    try:
        available = list(wb.sheetnames)
        if not available:
            raise ValueError(f"Workbook '{file_path}' contains no sheets.")
        if sheet_name is None:
            resolved_sheet = available[0]
        else:
            if sheet_name not in available:
                raise ValueError(
                    f"Sheet '{sheet_name}' not found in '{file_path}'. Available: {available}"
                )
            resolved_sheet = sheet_name

        ws = wb[resolved_sheet]
        raw_rows = list(ws.iter_rows(values_only=True))
    finally:
        wb.close()

    if not raw_rows:
        return pd.DataFrame(), resolved_sheet

    # Skip leading completely blank rows if present, then extract header row.
    start_idx = 0
    while start_idx < len(raw_rows) and all(
        cell is None or str(cell).strip() == "" for cell in raw_rows[start_idx]
    ):
        start_idx += 1
    if start_idx >= len(raw_rows):
        return pd.DataFrame(), resolved_sheet

    header_cells = list(raw_rows[start_idx])
    while header_cells and (header_cells[-1] is None or str(header_cells[-1]).strip() == ""):
        header_cells.pop()
    if not header_cells:
        return pd.DataFrame(), resolved_sheet

    headers = _make_unique_headers(header_cells)
    width = len(headers)

    data_rows: list[list[Any]] = []
    for row in raw_rows[start_idx + 1 :]:
        padded = list(row[:width])
        if len(padded) < width:
            padded.extend([None] * (width - len(padded)))
        data_rows.append(padded)

    # Trim trailing completely empty rows that Excel sometimes emits for styled blanks.
    while data_rows and all(
        cell is None or (isinstance(cell, str) and cell.strip() == "")
        for cell in data_rows[-1]
    ):
        data_rows.pop()

    df = pd.DataFrame(data_rows, columns=headers, dtype=object)
    return df, resolved_sheet


def _read_text_raw(file_path: Path) -> tuple[pd.DataFrame, str, str]:
    """Read a CSV/TXT/TSV file preserving all cell values as raw strings."""
    raw_bytes = file_path.read_bytes()
    encoding = _detect_text_encoding(raw_bytes)
    text = raw_bytes.decode(encoding)
    delimiter = _detect_csv_delimiter(text, file_path.suffix.lower())
    if not text.strip():
        return pd.DataFrame(), delimiter, encoding
    try:
        df = pd.read_csv(
            file_path,
            sep=delimiter,
            encoding=encoding,
            dtype=object,
            keep_default_na=False,
            na_filter=False,
        )
    except pd.errors.EmptyDataError:
        return pd.DataFrame(), delimiter, encoding
    df.columns = _make_unique_headers(list(df.columns))
    return df, delimiter, encoding


def _is_period_or_timestamp_header(col: str) -> tuple[bool, bool]:
    """Return (is_period_id, is_timestamp) for metadata header names."""
    tokens = _tokenize_ascii(col)
    if not tokens:
        return False, False
    allowed = _PERIOD_TOKENS | _TIMESTAMP_TOKENS | _METADATA_MODIFIER_TOKENS
    if not all(tok in allowed for tok in tokens):
        return False, False
    has_period = any(tok in _PERIOD_TOKENS for tok in tokens)
    has_ts = any(tok in _TIMESTAMP_TOKENS for tok in tokens)
    if has_period and not has_ts:
        return True, False
    if has_ts and not has_period:
        return False, True
    # Combined like Fecha_Guardia -> prefer timestamp if fecha/date leads, else period
    if tokens[0] in _TIMESTAMP_TOKENS:
        return False, True
    return True, False


def _is_non_metallurgical_header(col: str, circuit: CircuitSpec) -> bool:
    """Detect operational milling/grinding/flotation utility columns."""
    norm = _strip_accents(col)
    tokens = _tokenize_ascii(col)
    if any(pat.search(norm) for pat in _NON_MET_PATTERNS):
        return True

    # Avoid false positive if a circuit stream or species explicitly shares a token.
    stream_tokens = {_strip_accents(s.id) for s in circuit.streams} | {
        _strip_accents(s.name) for s in circuit.streams
    }
    species_tokens = {_strip_accents(sp.id) for sp in circuit.species} | {
        _strip_accents(sp.name) for sp in circuit.species
    }
    if any(tok in _NON_MET_EXACT_OR_TOKEN for tok in tokens):
        # Allow stream-associated tonnage unless specifically mill tonnage
        if norm in stream_tokens or any(sp in tokens for sp in species_tokens):
            if "molino" in tokens or "mill" in tokens or "solidos" in tokens or "solids" in tokens:
                return True
            return False
        return True
    return False


def _extract_unit_and_strip(col: str, circuit: CircuitSpec) -> tuple[Optional[str], bool, str]:
    """Detect measurement unit from header and return (unit, has_mass_kw, stripped_header)."""
    norm = _strip_accents(col)
    detected_unit: Optional[str] = None
    has_mass_kw = False
    cleaned = norm

    # Check specific compound units before generic single tokens.
    unit_rules: list[tuple[re.Pattern[str], str, bool]] = [
        (
            re.compile(r"(?<![a-z0-9])tms\s*[/_\s-]\s*(?:guardia|turno|shift|g)(?![a-z0-9])"),
            MassUnit.TMS_GUARDIA.value,
            True,
        ),
        (
            re.compile(r"(?<![a-z0-9])(?:tms|t|tm|ton|tn)\s*[/_\s]\s*h(?:r|ora)?(?![a-z0-9])|(?<![a-z0-9])tph(?![a-z0-9])"),
            MassUnit.T_H.value,
            True,
        ),
        (
            re.compile(r"(?<![a-z0-9])(?:tms|t|tm|ton|tn)\s*[/_\s]\s*d(?:ia|ay)?(?![a-z0-9])|(?<![a-z0-9])tpd(?![a-z0-9])"),
            MassUnit.T_D.value,
            True,
        ),
        (
            re.compile(r"(?<![a-z0-9])(?:oz\s*/\s*t(?:c|m)?|oz_t|ozt|opt)(?![a-z0-9])"),
            GradeUnit.OZ_T.value,
            False,
        ),
        (
            re.compile(r"(?<![a-z0-9])(?:gr?\s*/\s*t(?:m)?|gr?_t|gpt|gt)(?![a-z0-9])"),
            GradeUnit.G_T.value,
            False,
        ),
        (
            re.compile(r"(?<![a-z0-9])ppm(?![a-z0-9])"),
            GradeUnit.PPM.value,
            False,
        ),
        (
            re.compile(r"%|(?<![a-z0-9])(?:pct|porc|porcentaje|percent)(?![a-z0-9])"),
            GradeUnit.PERCENT.value,
            False,
        ),
    ]

    for pattern, unit_val, is_mass in unit_rules:
        if pattern.search(cleaned):
            detected_unit = unit_val
            has_mass_kw = has_mass_kw or is_mass
            cleaned = pattern.sub(" ", cleaned)
            break

    tokens = _tokenize_ascii(cleaned)
    if any(tok in _MASS_KEYWORDS for tok in tokens):
        has_mass_kw = True
        if detected_unit is None and "tms" in tokens and circuit.mass_unit == MassUnit.TMS_GUARDIA:
            detected_unit = MassUnit.TMS_GUARDIA.value

    return detected_unit, has_mass_kw, cleaned


def _species_aliases(sp: SpeciesSpec) -> set[str]:
    """Return normalized matching aliases for a species."""
    aliases = {_strip_accents(sp.id), _strip_accents(sp.name)}
    sp_id_norm = _strip_accents(sp.id)
    if sp_id_norm in _SPECIES_SYNONYMS:
        aliases.update(_SPECIES_SYNONYMS[sp_id_norm])
    return {a for a in aliases if a}


def _match_stream_from_tokens(
    tokens: list[str], circuit: CircuitSpec
) -> tuple[Optional[StreamSpec], set[int], float]:
    """Match a stream in header tokens, returning (stream, consumed_token_indices, score)."""
    if not tokens:
        return None, set(), 0.0

    # 1. Exact token-sequence or concatenated match against stream.id / stream.name.
    best_stream: Optional[StreamSpec] = None
    best_indices: set[int] = set()
    best_score = 0.0

    for stream in circuit.streams:
        for candidate_text, base_score in ((stream.id, 0.96), (stream.name, 0.92)):
            cand_tokens = _tokenize_ascii(candidate_text)
            if not cand_tokens:
                continue
            k = len(cand_tokens)
            for i in range(len(tokens) - k + 1):
                if tokens[i : i + k] == cand_tokens:
                    score = base_score + 0.01 * k
                    if score > best_score:
                        best_stream = stream
                        best_indices = set(range(i, i + k))
                        best_score = score
            # Concatenated token check (e.g., "concu" <-> "con_cu", "rofeed" <-> "ro_feed")
            joined_cand = "".join(cand_tokens)
            for i, tok in enumerate(tokens):
                if tok == joined_cand:
                    score = base_score
                    if score > best_score:
                        best_stream = stream
                        best_indices = {i}
                        best_score = score
                if i + 1 < len(tokens) and (tokens[i] + tokens[i + 1]) == joined_cand:
                    score = base_score - 0.02
                    if score > best_score:
                        best_stream = stream
                        best_indices = {i, i + 1}
                        best_score = score

    if best_stream is not None:
        return best_stream, best_indices, min(best_score, 0.99)

    # 2. Check element-specific concentrate aliases (e.g., cucon, concu, conpb, conzn, con_cu).
    conc_streams = [s for s in circuit.streams if s.role == StreamRole.CONCENTRATE]
    for i, tok in enumerate(tokens):
        for sp in circuit.species:
            sp_key = _strip_accents(sp.id)
            if tok in {f"con{sp_key}", f"{sp_key}con", f"conc{sp_key}"}:
                matched = _find_concentrate_for_species(conc_streams, sp_key)
                if matched is not None:
                    return matched, {i}, 0.90
        if i + 1 < len(tokens):
            pair = tokens[i] + tokens[i + 1]
            for sp in circuit.species:
                sp_key = _strip_accents(sp.id)
                if pair in {f"con{sp_key}", f"conc{sp_key}", f"{sp_key}conc"}:
                    matched = _find_concentrate_for_species(conc_streams, sp_key)
                    if matched is not None:
                        return matched, {i, i + 1}, 0.88

    # 3. Role-based stream aliases (cbz -> FEED, rlv -> TAILINGS, conc -> CONCENTRATE).
    feed_streams = [s for s in circuit.streams if s.role == StreamRole.FEED]
    tail_streams = [s for s in circuit.streams if s.role == StreamRole.TAILINGS]

    for i, tok in enumerate(tokens):
        if tok in _FEED_ALIASES and feed_streams:
            return feed_streams[0], {i}, 0.88
        if tok in _TAILINGS_ALIASES and tail_streams:
            return tail_streams[0], {i}, 0.88
        if tok in _CONC_GENERAL_ALIASES and len(conc_streams) == 1:
            return conc_streams[0], {i}, 0.85

    return None, set(), 0.0


def _find_concentrate_for_species(
    conc_streams: list[StreamSpec], species_norm: str
) -> Optional[StreamSpec]:
    """Select the concentrate stream associated with a given species."""
    if not conc_streams:
        return None
    for stream in conc_streams:
        s_norm = _strip_accents(stream.id) + "_" + _strip_accents(stream.name)
        if species_norm in s_norm:
            return stream
    if len(conc_streams) == 1:
        return conc_streams[0]
    return None


def _match_species_from_tokens(
    tokens: list[str],
    consumed_indices: set[int],
    circuit: CircuitSpec,
) -> Optional[SpeciesSpec]:
    """Match a chemical species in tokens not already consumed by stream matching."""
    remaining = [tok for idx, tok in enumerate(tokens) if idx not in consumed_indices]
    for sp in circuit.species:
        aliases = _species_aliases(sp)
        if any(tok in aliases for tok in remaining):
            return sp
    return None


def _suggest_column_mapping(
    col: str, circuit: CircuitSpec
) -> tuple[ColumnMappingSuggestion, bool]:
    """Build a heuristic ColumnMappingSuggestion and flag whether column is metadata."""
    is_period, is_ts = _is_period_or_timestamp_header(col)
    if is_period or is_ts:
        return (
            ColumnMappingSuggestion(
                source_column=col,
                target_stream_id=None,
                target_species_id=None,
                is_mass_flow=False,
                detected_unit=None,
                is_non_metallurgical=False,
                confidence=0.0,
            ),
            True,
        )

    if _is_non_metallurgical_header(col, circuit):
        return (
            ColumnMappingSuggestion(
                source_column=col,
                target_stream_id=None,
                target_species_id=None,
                is_mass_flow=False,
                detected_unit=None,
                is_non_metallurgical=True,
                confidence=0.0,
            ),
            False,
        )

    detected_unit, has_mass_kw, stripped = _extract_unit_and_strip(col, circuit)
    tokens = _tokenize_ascii(stripped)

    stream, consumed, stream_score = _match_stream_from_tokens(tokens, circuit)
    species = _match_species_from_tokens(tokens, consumed, circuit)

    is_grade_unit = detected_unit in {u.value for u in GradeUnit}
    is_mass_unit = detected_unit in {u.value for u in MassUnit}

    # Case 1: Explicit stream + species assay
    if stream is not None and species is not None:
        conf = min(0.99, round(stream_score * 0.5 + 0.48 + (0.02 if is_grade_unit else 0.0), 3))
        return (
            ColumnMappingSuggestion(
                source_column=col,
                target_stream_id=stream.id,
                target_species_id=species.id,
                is_mass_flow=False,
                detected_unit=detected_unit,
                is_non_metallurgical=False,
                confidence=conf,
            ),
            False,
        )

    # Case 2: Stream + mass flow keyword or no species / grade unit -> mass flow
    if stream is not None and species is None:
        if has_mass_kw or is_mass_unit or not is_grade_unit:
            conf = min(0.98, round(stream_score * (0.98 if has_mass_kw or is_mass_unit else 0.85), 3))
            return (
                ColumnMappingSuggestion(
                    source_column=col,
                    target_stream_id=stream.id,
                    target_species_id=None,
                    is_mass_flow=True,
                    detected_unit=detected_unit,
                    is_non_metallurgical=False,
                    confidence=conf,
                ),
                False,
            )
        # Grade unit on concentrate stream like "ConCu_%" -> infer species from stream name
        if is_grade_unit and stream.role == StreamRole.CONCENTRATE:
            s_norm = _strip_accents(stream.id)
            for sp in circuit.species:
                if _strip_accents(sp.id) in s_norm:
                    return (
                        ColumnMappingSuggestion(
                            source_column=col,
                            target_stream_id=stream.id,
                            target_species_id=sp.id,
                            is_mass_flow=False,
                            detected_unit=detected_unit,
                            is_non_metallurgical=False,
                            confidence=0.72,
                        ),
                        False,
                    )

    # Case 3: Bare mass flow column without stream name (e.g. "TMS/guardia", "TPH") -> primary feed
    if stream is None and species is None and (has_mass_kw or is_mass_unit):
        feed_streams = [s for s in circuit.streams if s.role == StreamRole.FEED]
        if feed_streams:
            return (
                ColumnMappingSuggestion(
                    source_column=col,
                    target_stream_id=feed_streams[0].id,
                    target_species_id=None,
                    is_mass_flow=True,
                    detected_unit=detected_unit,
                    is_non_metallurgical=False,
                    confidence=0.72,
                ),
                False,
            )

    return (
        ColumnMappingSuggestion(
            source_column=col,
            target_stream_id=None,
            target_species_id=None,
            is_mass_flow=False,
            detected_unit=detected_unit,
            is_non_metallurgical=False,
            confidence=0.0,
        ),
        False,
    )


def _resolve_fallback_collisions(suggestions: list[ColumnMappingSuggestion]) -> None:
    """Clear low-confidence fallback mappings if an explicit column claims the same target."""
    explicit_targets: set[tuple[str, bool, Optional[str]]] = set()
    for sug in suggestions:
        if sug.target_stream_id is not None and sug.confidence >= 0.80:
            explicit_targets.add((sug.target_stream_id, sug.is_mass_flow, sug.target_species_id))

    for sug in suggestions:
        if sug.target_stream_id is not None and sug.confidence < 0.80:
            key = (sug.target_stream_id, sug.is_mass_flow, sug.target_species_id)
            if key in explicit_targets:
                sug.target_stream_id = None
                sug.target_species_id = None
                sug.is_mass_flow = False
                sug.confidence = 0.0


def _is_missing_cell(val: Any) -> bool:
    """Return True if a raw cell represents a missing value."""
    if val is None:
        return True
    if isinstance(val, float):
        return math.isnan(val)
    if isinstance(val, (np.floating,)):
        return bool(np.isnan(val))
    try:
        if pd.isna(val):
            return True
    except (TypeError, ValueError):
        pass
    if isinstance(val, str):
        return val.strip().lower() in _MISSING_STRINGS
    return False


def _is_decimal_comma_string(text: str) -> bool:
    """Return True when text uses a comma decimal separator, excluding thousands commas."""
    s = text.strip()
    if "," not in s:
        return False
    if _US_THOUSANDS_RE.match(s):
        return False
    if _EU_THOUSANDS_DEC_COMMA_RE.match(s):
        return True
    if _AMBIGUOUS_THREE_DIGIT_COMMA_RE.match(s):
        return False
    return bool(_PLAIN_DEC_COMMA_RE.match(s))


def _normalize_decimal_comma_text(text: str) -> str:
    """Convert decimal-comma numeric string (including optional EU dot thousands) to dot float."""
    s = text.strip()
    if _EU_THOUSANDS_DEC_COMMA_RE.match(s):
        return s.replace(".", "").replace(",", ".")
    return s.replace(",", ".")


def _parse_numeric_cell(val: Any) -> tuple[Optional[float], bool]:
    """Parse raw cell into (finite_float_or_none, is_decimal_comma)."""
    if isinstance(val, bool):
        return None, False
    if isinstance(val, (int, float, np.integer, np.floating)):
        fval = float(val)
        return (fval, False) if math.isfinite(fval) else (None, False)
    if not isinstance(val, str):
        return None, False

    s = val.strip()
    if not s:
        return None, False

    if _is_decimal_comma_string(s):
        try:
            fval = float(_normalize_decimal_comma_text(s))
            return (fval, True) if math.isfinite(fval) else (None, True)
        except ValueError:
            return None, True

    # Handle US thousands-formatted numeric strings like "1,234.56" or "1,200"
    if _US_THOUSANDS_RE.match(s) or _AMBIGUOUS_THREE_DIGIT_COMMA_RE.match(s):
        try:
            fval = float(s.replace(",", ""))
            return (fval, False) if math.isfinite(fval) else (None, False)
        except ValueError:
            return None, False

    try:
        fval = float(s)
        return (fval, False) if math.isfinite(fval) else (None, False)
    except ValueError:
        return None, False


def _max_physical_grade(unit: Optional[str]) -> Optional[float]:
    """Return physical upper bound for an assay unit, or None when unbounded."""
    if unit == GradeUnit.PERCENT.value:
        return 100.0
    if unit in (GradeUnit.G_T.value, GradeUnit.PPM.value):
        return _MAX_G_T
    if unit == GradeUnit.OZ_T.value:
        return _MAX_G_T / _OZ_T_TO_G_T
    return None


def _column_looks_numeric(series: pd.Series) -> bool:
    """Heuristic check for unmapped non-metadata columns to decide numeric profiling."""
    non_missing = [v for v in series.tolist() if not _is_missing_cell(v)]
    if not non_missing:
        return False
    numeric_hits = sum(1 for v in non_missing if _parse_numeric_cell(v)[0] is not None)
    return numeric_hits > 0


def profile_file(
    path: str | Path, circuit: CircuitSpec, sheet_name: Optional[str] = None
) -> tuple[pd.DataFrame, DataProfileReport]:
    """Profile an Excel or CSV/TXT/TSV dataset without silent corrections."""
    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError(f"Input file not found: {file_path}")

    suffix = file_path.suffix.lower()
    file_sha = compute_file_sha256(file_path)

    resolved_sheet: Optional[str] = None
    delimiter: Optional[str] = None
    encoding: Optional[str] = None

    if suffix in _EXCEL_EXTENSIONS:
        raw_df, resolved_sheet = _read_excel_raw(file_path, sheet_name)
    elif suffix in _TEXT_EXTENSIONS:
        raw_df, delimiter, encoding = _read_text_raw(file_path)
    else:
        raise ValueError(
            f"Unsupported file format '{suffix}'. Expected one of {sorted(_EXCEL_EXTENSIONS | _TEXT_EXTENSIONS)}."
        )

    columns = list(raw_df.columns)
    suggestions: list[ColumnMappingSuggestion] = []
    metadata_cols: set[str] = set()
    for col in columns:
        sug, is_meta = _suggest_column_mapping(col, circuit)
        suggestions.append(sug)
        if is_meta:
            metadata_cols.add(col)

    _resolve_fallback_collisions(suggestions)
    non_met_cols = [sug.source_column for sug in suggestions if sug.is_non_metallurgical]

    species_by_id = {sp.id: sp for sp in circuit.species}
    anomalies: list[AnomalyRecord] = []

    for sug in suggestions:
        col = sug.source_column
        if col in metadata_cols or sug.is_non_metallurgical:
            continue

        is_mapped_var = sug.target_stream_id is not None
        if not is_mapped_var and not _column_looks_numeric(raw_df[col]):
            continue

        # Unit mismatch check (column level)
        effective_grade_unit: Optional[str] = None
        if sug.target_species_id is not None and sug.target_species_id in species_by_id:
            target_sp = species_by_id[sug.target_species_id]
            effective_grade_unit = sug.detected_unit or target_sp.unit.value
            if sug.detected_unit is not None and sug.detected_unit != target_sp.unit.value:
                anomalies.append(
                    AnomalyRecord(
                        row_index=None,
                        column=col,
                        raw_value=sug.detected_unit,
                        anomaly_type="unit_mismatch",
                        severity="warning",
                        detail=(
                            f"Column '{col}' uses unit '{sug.detected_unit}' while target species "
                            f"'{target_sp.id}' expects '{target_sp.unit.value}'."
                        ),
                        suggested_action=f"Convert '{sug.detected_unit}' to '{target_sp.unit.value}'",
                    )
                )
        elif sug.is_mass_flow and sug.detected_unit is not None:
            if sug.detected_unit != circuit.mass_unit.value:
                anomalies.append(
                    AnomalyRecord(
                        row_index=None,
                        column=col,
                        raw_value=sug.detected_unit,
                        anomaly_type="unit_mismatch",
                        severity="warning",
                        detail=(
                            f"Mass flow column '{col}' uses unit '{sug.detected_unit}' while "
                            f"circuit expects '{circuit.mass_unit.value}'."
                        ),
                        suggested_action=f"Align mass unit to '{circuit.mass_unit.value}'",
                    )
                )
        elif not is_mapped_var and sug.detected_unit in {u.value for u in GradeUnit}:
            effective_grade_unit = sug.detected_unit

        max_grade = _max_physical_grade(effective_grade_unit) if not sug.is_mass_flow else None

        col_values = raw_df[col].tolist()
        for row_idx, raw_val in enumerate(col_values):
            if _is_missing_cell(raw_val):
                anomalies.append(
                    AnomalyRecord(
                        row_index=row_idx,
                        column=col,
                        raw_value=raw_val,
                        anomaly_type="missing_value",
                        severity="warning",
                        detail=f"Missing value at row {row_idx + 1} in column '{col}'.",
                        suggested_action="Keep as unmeasured, drop row, or interpolate",
                    )
                )
                continue

            parsed_val, is_dec_comma = _parse_numeric_cell(raw_val)
            if is_dec_comma:
                anomalies.append(
                    AnomalyRecord(
                        row_index=row_idx,
                        column=col,
                        raw_value=raw_val,
                        anomaly_type="decimal_comma",
                        severity="warning",
                        detail=f"Decimal comma '{raw_val}' at row {row_idx + 1} in column '{col}'.",
                        suggested_action="Replace decimal comma ',' with '.'",
                    )
                )

            if parsed_val is None:
                anomalies.append(
                    AnomalyRecord(
                        row_index=row_idx,
                        column=col,
                        raw_value=raw_val,
                        anomaly_type="corrupt_text",
                        severity="error",
                        detail=f"Non-numeric value '{raw_val}' at row {row_idx + 1} in column '{col}'.",
                        suggested_action="Coerce corrupt text to NaN/unmeasured",
                    )
                )
                continue

            if parsed_val < 0.0:
                anomalies.append(
                    AnomalyRecord(
                        row_index=row_idx,
                        column=col,
                        raw_value=raw_val,
                        anomaly_type="negative_value",
                        severity="error",
                        detail=f"Negative value {parsed_val} at row {row_idx + 1} in column '{col}'.",
                        suggested_action="Nullify negative value to unmeasured",
                    )
                )
                continue

            if max_grade is not None and parsed_val > max_grade:
                anomalies.append(
                    AnomalyRecord(
                        row_index=row_idx,
                        column=col,
                        raw_value=raw_val,
                        anomaly_type="out_of_range",
                        severity="error",
                        detail=(
                            f"Value {parsed_val} exceeds physical maximum {max_grade:g} "
                            f"({effective_grade_unit}) at row {row_idx + 1} in column '{col}'."
                        ),
                        suggested_action="Nullify out-of-range value to unmeasured",
                    )
                )

    report = DataProfileReport(
        source_path=str(file_path),
        file_sha256=file_sha,
        sheet_name=resolved_sheet,
        delimiter=delimiter,
        encoding=encoding,
        row_count=len(raw_df),
        columns=columns,
        anomalies=anomalies,
        mapping_suggestions=suggestions,
        non_metallurgical_columns=non_met_cols,
    )
    return raw_df, report


def build_default_cleaning_plan(profile: DataProfileReport) -> CleaningPlan:
    """Create an unconfirmed CleaningPlan initialized from a DataProfileReport."""
    return CleaningPlan(
        mappings=[m.model_copy(deep=True) for m in profile.mapping_suggestions],
        fix_decimal_commas=True,
        coerce_corrupt_text_to_nan=True,
        nullify_negative=True,
        nullify_out_of_range=True,
        convert_units=True,
        drop_non_metallurgical=True,
        missing_strategy="keep_as_unmeasured",
        confirmed=False,
    )


def _format_period_id(val: Any, row_idx: int) -> str:
    """Format a period/shift identifier cell cleanly with deterministic fallback."""
    if _is_missing_cell(val):
        return f"P-{row_idx + 1:03d}"
    if isinstance(val, float) and math.isfinite(val) and val.is_integer():
        return str(int(val))
    text = str(val).strip()
    return text if text else f"P-{row_idx + 1:03d}"


def _format_timestamp(val: Any) -> Optional[str]:
    """Format a timestamp/date cell cleanly as string or ISO date."""
    if _is_missing_cell(val):
        return None
    if isinstance(val, datetime):
        return val.isoformat()
    if isinstance(val, date):
        return val.isoformat()
    text = str(val).strip()
    return text if text else None


def _convert_mass_unit_if_supported(
    value: float, from_unit: str, to_unit: str
) -> Optional[float]:
    """Convert between exact time-based mass units t/h and t/d."""
    if from_unit == to_unit:
        return value
    if from_unit == MassUnit.T_H.value and to_unit == MassUnit.T_D.value:
        return value * 24.0
    if from_unit == MassUnit.T_D.value and to_unit == MassUnit.T_H.value:
        return value / 24.0
    return None


def apply_cleaning_plan(
    raw_df: pd.DataFrame,
    profile: DataProfileReport,
    circuit: CircuitSpec,
    plan: CleaningPlan,
) -> CleanedDataset:
    """Apply an explicitly confirmed CleaningPlan to a copy of raw_df with audit trail."""
    if not plan.confirmed:
        raise ValueError("CleaningPlan must be explicitly confirmed before applying transformations.")

    df = raw_df.copy(deep=True)
    audit_log: list[AuditLogEntry] = []

    stream_ids = {s.id for s in circuit.streams}
    species_by_id = {sp.id: sp for sp in circuit.species}

    # Locate period ID and timestamp columns from unmapped metadata columns.
    mapped_sources = {
        m.source_column for m in plan.mappings if m.target_stream_id is not None or m.is_non_metallurgical
    }
    period_col: Optional[str] = None
    timestamp_col: Optional[str] = None
    for col in df.columns:
        if col in mapped_sources:
            continue
        is_period, is_ts = _is_period_or_timestamp_header(col)
        if is_period and period_col is None:
            period_col = col
        elif is_ts and timestamp_col is None:
            timestamp_col = col

    # 1. Drop non-metallurgical columns if configured.
    non_met_cols = [
        m.source_column
        for m in plan.mappings
        if m.is_non_metallurgical and m.source_column in df.columns
    ]
    if plan.drop_non_metallurgical and non_met_cols:
        for col in non_met_cols:
            df = df.drop(columns=[col])
            audit_log.append(
                AuditLogEntry(
                    timestamp_iso=_utc_now_iso(),
                    action="drop_non_metallurgical",
                    column=col,
                    affected_rows=len(df),
                    detail=f"Dropped non-metallurgical operational column '{col}'.",
                )
            )

    # 2. Clean mapped variable columns into float64 series without mutating raw_df.
    active_mappings = [
        m
        for m in plan.mappings
        if not m.is_non_metallurgical
        and m.target_stream_id is not None
        and (m.is_mass_flow or m.target_species_id is not None)
        and m.source_column in df.columns
    ]

    cleaned_series: dict[str, pd.Series] = {}
    for mapping in active_mappings:
        col = mapping.source_column
        if mapping.target_stream_id not in stream_ids:
            raise ValueError(
                f"Mapping for column '{col}' references unknown stream '{mapping.target_stream_id}'."
            )
        if not mapping.is_mass_flow and mapping.target_species_id not in species_by_id:
            raise ValueError(
                f"Mapping for column '{col}' references unknown species '{mapping.target_species_id}'."
            )

        raw_values = df[col].tolist()
        decimal_comma_count = 0
        corrupt_text_count = 0
        negative_count = 0
        out_of_range_count = 0

        target_sp = species_by_id.get(mapping.target_species_id or "")
        effective_grade_unit = (
            (mapping.detected_unit or target_sp.unit.value) if target_sp is not None else None
        )
        max_grade = _max_physical_grade(effective_grade_unit) if not mapping.is_mass_flow else None

        numeric_vals: list[float] = []
        for val in raw_values:
            if _is_missing_cell(val):
                numeric_vals.append(float("nan"))
                continue

            if isinstance(val, str) and _is_decimal_comma_string(val):
                if plan.fix_decimal_commas:
                    decimal_comma_count += 1
                    parsed_val, _ = _parse_numeric_cell(val)
                else:
                    parsed_val = None
            else:
                parsed_val, _ = _parse_numeric_cell(val)

            if parsed_val is None:
                if plan.coerce_corrupt_text_to_nan:
                    corrupt_text_count += 1
                    numeric_vals.append(float("nan"))
                    continue
                raise ValueError(
                    f"Cannot parse non-numeric value {val!r} in column '{col}' while coerce_corrupt_text_to_nan is False."
                )

            if parsed_val < 0.0:
                if plan.nullify_negative:
                    negative_count += 1
                    numeric_vals.append(float("nan"))
                    continue

            if max_grade is not None and parsed_val > max_grade:
                if plan.nullify_out_of_range:
                    out_of_range_count += 1
                    numeric_vals.append(float("nan"))
                    continue

            numeric_vals.append(float(parsed_val))

        if decimal_comma_count > 0:
            audit_log.append(
                AuditLogEntry(
                    timestamp_iso=_utc_now_iso(),
                    action="fix_decimal_commas",
                    column=col,
                    affected_rows=decimal_comma_count,
                    detail=f"Replaced decimal commas with dots in {decimal_comma_count} row(s).",
                )
            )
        if corrupt_text_count > 0:
            audit_log.append(
                AuditLogEntry(
                    timestamp_iso=_utc_now_iso(),
                    action="coerce_corrupt_text_to_nan",
                    column=col,
                    affected_rows=corrupt_text_count,
                    detail=f"Coerced {corrupt_text_count} corrupt text cell(s) to NaN.",
                )
            )
        if negative_count > 0:
            audit_log.append(
                AuditLogEntry(
                    timestamp_iso=_utc_now_iso(),
                    action="nullify_negative",
                    column=col,
                    affected_rows=negative_count,
                    detail=f"Nullified {negative_count} negative value(s) to NaN.",
                )
            )
        if out_of_range_count > 0:
            audit_log.append(
                AuditLogEntry(
                    timestamp_iso=_utc_now_iso(),
                    action="nullify_out_of_range",
                    column=col,
                    affected_rows=out_of_range_count,
                    detail=(
                        f"Nullified {out_of_range_count} out-of-range value(s) "
                        f"(> {max_grade:g} {effective_grade_unit}) to NaN."
                    ),
                )
            )

        series = pd.Series(numeric_vals, index=df.index, dtype="float64")

        # 3. Unit conversion if enabled and unit differs from target circuit/species unit.
        if plan.convert_units and mapping.detected_unit is not None:
            if not mapping.is_mass_flow and target_sp is not None:
                if mapping.detected_unit != target_sp.unit.value:
                    src_unit = GradeUnit(mapping.detected_unit)
                    dst_unit = target_sp.unit
                    valid_mask = series.notna()
                    converted_count = int(valid_mask.sum())
                    series = series.apply(
                        lambda x: SpeciesSpec.convert_grade(x, src_unit, dst_unit)
                        if pd.notna(x)
                        else float("nan")
                    )
                    audit_log.append(
                        AuditLogEntry(
                            timestamp_iso=_utc_now_iso(),
                            action="convert_units",
                            column=col,
                            affected_rows=converted_count,
                            detail=f"Converted column '{col}' from '{src_unit.value}' to '{dst_unit.value}'.",
                        )
                    )
            elif mapping.is_mass_flow and mapping.detected_unit != circuit.mass_unit.value:
                valid_mask = series.notna()
                sample_conv = _convert_mass_unit_if_supported(
                    1.0, mapping.detected_unit, circuit.mass_unit.value
                )
                if sample_conv is not None:
                    converted_count = int(valid_mask.sum())
                    factor = sample_conv
                    series = series * factor
                    audit_log.append(
                        AuditLogEntry(
                            timestamp_iso=_utc_now_iso(),
                            action="convert_units",
                            column=col,
                            affected_rows=converted_count,
                            detail=(
                                f"Converted mass flow '{col}' from '{mapping.detected_unit}' "
                                f"to '{circuit.mass_unit.value}' (factor {factor:g})."
                            ),
                        )
                    )
                else:
                    audit_log.append(
                        AuditLogEntry(
                            timestamp_iso=_utc_now_iso(),
                            action="unit_mismatch_unconverted",
                            column=col,
                            affected_rows=0,
                            detail=(
                                f"Mass unit '{mapping.detected_unit}' cannot be automatically "
                                f"converted to '{circuit.mass_unit.value}' without shift duration."
                            ),
                        )
                    )

        cleaned_series[col] = series

    # 4. Missing data strategy across mapped columns.
    surviving_indices = list(df.index)
    if active_mappings:
        mapped_frame = pd.DataFrame(cleaned_series, index=df.index)
        if plan.missing_strategy == "drop_row":
            complete_mask = mapped_frame.notna().all(axis=1)
            dropped_count = int((~complete_mask).sum())
            surviving_indices = [idx for idx, keep in zip(df.index, complete_mask.tolist()) if keep]
            if dropped_count > 0:
                audit_log.append(
                    AuditLogEntry(
                        timestamp_iso=_utc_now_iso(),
                        action="missing_strategy_drop_row",
                        column=None,
                        affected_rows=dropped_count,
                        detail=f"Dropped {dropped_count} row(s) containing missing values in mapped variables.",
                    )
                )
        elif plan.missing_strategy == "interpolate":
            for col, series in list(cleaned_series.items()):
                missing_before = int(series.isna().sum())
                if missing_before > 0 and series.notna().any():
                    interpolated = series.interpolate(method="linear", limit_direction="both")
                    filled_count = missing_before - int(interpolated.isna().sum())
                    cleaned_series[col] = interpolated
                    if filled_count > 0:
                        audit_log.append(
                            AuditLogEntry(
                                timestamp_iso=_utc_now_iso(),
                                action="missing_strategy_interpolate",
                                column=col,
                                affected_rows=filled_count,
                                detail=f"Linearly interpolated {filled_count} missing value(s) in '{col}'.",
                            )
                        )
        else:
            total_missing = int(mapped_frame.isna().sum().sum())
            if total_missing > 0:
                audit_log.append(
                    AuditLogEntry(
                        timestamp_iso=_utc_now_iso(),
                        action="missing_strategy_keep_as_unmeasured",
                        column=None,
                        affected_rows=total_missing,
                        detail=f"Retained {total_missing} missing cell(s) as unmeasured (None).",
                    )
                )

    # 5. Assemble MeasurementPoint list.
    periods: list[MeasurementPoint] = []
    for pos, orig_idx in enumerate(surviving_indices):
        row_pos = df.index.get_loc(orig_idx)
        p_id = (
            _format_period_id(df.iloc[row_pos][period_col], pos)
            if period_col is not None
            else f"P-{pos + 1:03d}"
        )
        ts = (
            _format_timestamp(df.iloc[row_pos][timestamp_col])
            if timestamp_col is not None
            else None
        )
        mass_flows: dict[str, Optional[float]] = {}
        assays: dict[str, dict[str, Optional[float]]] = {}

        for mapping in active_mappings:
            col = mapping.source_column
            raw_num = cleaned_series[col].loc[orig_idx]
            val_opt: Optional[float] = (
                float(raw_num) if pd.notna(raw_num) and math.isfinite(float(raw_num)) else None
            )
            stream_id = mapping.target_stream_id
            assert stream_id is not None
            if mapping.is_mass_flow:
                mass_flows[stream_id] = val_opt
            elif mapping.target_species_id is not None:
                assays.setdefault(stream_id, {})[mapping.target_species_id] = val_opt

        periods.append(
            MeasurementPoint(
                period_id=p_id,
                timestamp=ts,
                mass_flows=mass_flows,
                assays=assays,
            )
        )

    audit_log.append(
        AuditLogEntry(
            timestamp_iso=_utc_now_iso(),
            action="cleaning_applied",
            column=None,
            affected_rows=len(periods),
            detail=(
                f"Applied confirmed CleaningPlan ({len(active_mappings)} mapped columns, "
                f"{len(raw_df)} raw rows -> {len(periods)} clean periods)."
            ),
        )
    )

    return CleanedDataset(
        source_path=profile.source_path,
        file_sha256=profile.file_sha256,
        periods=periods,
        audit_log=audit_log,
        raw_row_count=len(raw_df),
        clean_row_count=len(periods),
    )


def _amira_mass_rsd(role: StreamRole) -> float:
    """Return AMIRA P754 / Gy relative standard deviation (%) for a stream mass flow."""
    if role == StreamRole.FEED:
        return 2.0
    if role in (StreamRole.CONCENTRATE, StreamRole.TAILINGS):
        return 3.0
    return 5.0


def _amira_assay_rsd(role: StreamRole, species: SpeciesSpec) -> float:
    """Return AMIRA P754 / Gy relative standard deviation (%) for a stream species assay."""
    sp_id = _strip_accents(species.id)
    sp_name = _strip_accents(species.name)
    if sp_id in _PRECIOUS_SPECIES or sp_name in _PRECIOUS_SPECIES:
        return 12.0
    if role == StreamRole.FEED:
        return 4.0
    if role == StreamRole.CONCENTRATE:
        return 2.5
    if role == StreamRole.TAILINGS:
        return 10.0
    return 5.0


def build_amira_p754_presets(
    circuit: CircuitSpec, alpha: float = 0.05
) -> UncertaintyModel:
    """Build industrial AMIRA P754 / Gy sampling relative (%) uncertainty presets."""
    specs: dict[str, UncertaintySpec] = {}
    for stream in circuit.streams:
        m_key = make_mass_var_key(stream.id)
        specs[m_key] = UncertaintySpec(
            variable_key=m_key,
            stream_id=stream.id,
            species_id=None,
            mode=UncertaintyMode.PRESET_AMIRA,
            scale=UncertaintyScale.RELATIVE_PCT,
            value=_amira_mass_rsd(stream.role),
        )
        for species in circuit.species:
            a_key = make_assay_var_key(stream.id, species.id)
            specs[a_key] = UncertaintySpec(
                variable_key=a_key,
                stream_id=stream.id,
                species_id=species.id,
                mode=UncertaintyMode.PRESET_AMIRA,
                scale=UncertaintyScale.RELATIVE_PCT,
                value=_amira_assay_rsd(stream.role, species),
            )
    return UncertaintyModel(specs=specs, alpha=alpha)


def compute_empirical_uncertainties(
    dataset: CleanedDataset, circuit: CircuitSpec, alpha: float = 0.05
) -> UncertaintyModel:
    """Estimate empirical sample standard deviations (ddof=1) from CleanedDataset periods."""
    specs: dict[str, UncertaintySpec] = {}
    fallback_floor = 1e-6

    for stream in circuit.streams:
        m_key = make_mass_var_key(stream.id)
        m_vals = [
            float(val)
            for p in dataset.periods
            if (val := p.mass_flows.get(stream.id)) is not None and math.isfinite(float(val))
        ]
        if len(m_vals) >= 2:
            sigma_m = max(float(np.std(m_vals, ddof=1)), fallback_floor)
        elif len(m_vals) == 1:
            sigma_m = max(abs(m_vals[0]) * (_amira_mass_rsd(stream.role) / 100.0), fallback_floor)
        else:
            sigma_m = max(_amira_mass_rsd(stream.role), fallback_floor)

        specs[m_key] = UncertaintySpec(
            variable_key=m_key,
            stream_id=stream.id,
            species_id=None,
            mode=UncertaintyMode.EMPIRICAL,
            scale=UncertaintyScale.ABSOLUTE,
            value=sigma_m,
        )

        for species in circuit.species:
            a_key = make_assay_var_key(stream.id, species.id)
            a_vals = [
                float(val)
                for p in dataset.periods
                if (val := p.assays.get(stream.id, {}).get(species.id)) is not None
                and math.isfinite(float(val))
            ]
            amira_pct = _amira_assay_rsd(stream.role, species)
            if len(a_vals) >= 2:
                sigma_a = max(float(np.std(a_vals, ddof=1)), fallback_floor)
            elif len(a_vals) == 1:
                sigma_a = max(abs(a_vals[0]) * (amira_pct / 100.0), fallback_floor)
            else:
                sigma_a = max(amira_pct / 100.0, fallback_floor)

            specs[a_key] = UncertaintySpec(
                variable_key=a_key,
                stream_id=stream.id,
                species_id=species.id,
                mode=UncertaintyMode.EMPIRICAL,
                scale=UncertaintyScale.ABSOLUTE,
                value=sigma_a,
            )

    return UncertaintyModel(specs=specs, alpha=alpha)
