"""Tests verifying 100% ES/EN i18n parity, zero emojis, and clean i18n usage across UI & reporting."""

from __future__ import annotations

import ast
import json
import re
import string
import subprocess
import sys
from pathlib import Path
from typing import Any

from app.i18n.manager import I18nManager

PROJECT_ROOT = Path(__file__).resolve().parents[1]
I18N_DIR = PROJECT_ROOT / "app" / "i18n"
UI_DIR = PROJECT_ROOT / "app" / "ui"
REPORTING_DIR = PROJECT_ROOT / "reporting"
STYLES_DIR = PROJECT_ROOT / "app" / "assets" / "styles"

_EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001F9FF"
    "\U0001FA00-\U0001FAFF"
    "\U00002600-\U000027BF"
    "\U0001F1E6-\U0001F1FF"
    "\uFE0F\u200D"
    "]"
)

_UI_TEXT_CONSTRUCTORS = frozenset(
    {"QLabel", "QPushButton", "QCheckBox", "QRadioButton", "QGroupBox", "QAction", "QDockWidget"}
)
_UI_TEXT_SETTERS = frozenset(
    {"setText", "setWindowTitle", "setToolTip", "setPlaceholderText", "addTab", "addMenu", "setTitle"}
)


def _flatten(mapping: dict[str, Any], prefix: str = "") -> dict[str, str]:
    flat: dict[str, str] = {}
    for k, v in mapping.items():
        key = f"{prefix}.{k}" if prefix else str(k)
        if isinstance(v, dict):
            flat.update(_flatten(v, key))
        else:
            flat[key] = str(v)
    return flat


def _extract_placeholders(text: str) -> set[str]:
    formatter = string.Formatter()
    return {
        field_name.split(".")[0].split("[")[0]
        for _lit, field_name, _fmt, _conv in formatter.parse(text)
        if field_name is not None
    }


def _looks_like_untranslated_prose(literal: str) -> bool:
    """Return True if literal looks like a natural-language UI label rather than technical token."""
    text = literal.strip()
    if not text or len(text) < 4:
        return False
    # Ignore file filters, URLs, CSS/QSS, format strings, or pure identifiers/codes
    if any(tok in text for tok in ("(*.", "http://", "https://", "Q", "#", "{", "}", "://", "\n")):
        return False
    # Ignore pure technical abbreviations, units, or math symbols (e.g., "J*", "SHA-256", "AMIRA P754")
    if text.upper() == text and not re.search(r"[a-z]", text):
        return False
    # Two or more alphabetic words (at least one >=3 lowercase letters) suggests UI prose
    words = re.findall(r"[A-Za-zÁÉÍÓÚáéíóúÑñ]{2,}", text)
    has_lowercase_word = any(re.search(r"[a-záéíóúñ]{3,}", w) for w in words)
    return len(words) >= 2 and has_lowercase_word


def test_i18n_manager_headless_and_reactive() -> None:
    """Verify app.i18n.manager never imports PySide6 and fires reactive callbacks on switch."""
    script = (
        "import sys; "
        "from app.i18n.manager import I18nManager; "
        "assert 'PySide6' not in sys.modules; "
        "m = I18nManager('es'); "
        "seen = []; "
        "m.subscribe(lambda lang: seen.append(lang)); "
        "m.set_language('en'); "
        "assert seen == ['en'] and m.t('menu.file') == 'File'"
    )
    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, f"Subprocess failed:\n{proc.stdout}\n{proc.stderr}"


def test_es_en_key_and_placeholder_parity() -> None:
    """Verify 100% key parity, non-empty values, and matching format placeholders in ES/EN."""
    es_data = json.loads((I18N_DIR / "es.json").read_text(encoding="utf-8"))
    en_data = json.loads((I18N_DIR / "en.json").read_text(encoding="utf-8"))

    es_flat = _flatten(es_data)
    en_flat = _flatten(en_data)

    assert set(es_flat.keys()) == set(en_flat.keys()), (
        f"Key mismatch: ES-EN={set(es_flat) - set(en_flat)}, EN-ES={set(en_flat) - set(es_flat)}"
    )
    assert len(es_flat) >= 350

    for key, es_val in es_flat.items():
        en_val = en_flat[key]
        assert isinstance(es_val, str) and es_val.strip(), f"Empty ES value for key {key}"
        assert isinstance(en_val, str) and en_val.strip(), f"Empty EN value for key {key}"
        assert _extract_placeholders(es_val) == _extract_placeholders(en_val), (
            f"Placeholder mismatch for {key}: ES={es_val!r} vs EN={en_val!r}"
        )


def test_zero_emojis_across_i18n_ui_styles_and_reporting() -> None:
    """Verify ZERO emojis exist in translations, UI Python modules, QSS styles, or reporting."""
    files_to_scan = [
        I18N_DIR / "es.json",
        I18N_DIR / "en.json",
        PROJECT_ROOT / "app" / "main.py",
        *sorted(UI_DIR.glob("*.py")),
        *sorted(STYLES_DIR.glob("*.qss")),
        *sorted(REPORTING_DIR.glob("*.py")),
    ]
    for path in files_to_scan:
        if not path.exists():
            continue
        content = path.read_text(encoding="utf-8")
        match = _EMOJI_RE.search(content)
        assert match is None, f"Forbidden emoji character {match.group(0)!r} found in {path}"


def test_referenced_i18n_keys_exist_and_no_hardcoded_ui_prose() -> None:
    """Verify all .t('key') calls resolve cleanly and UI widgets avoid hardcoded prose literals."""
    i18n = I18nManager("es")
    valid_keys = i18n.get_all_keys("es") & i18n.get_all_keys("en")

    ui_files = sorted(p for p in UI_DIR.glob("*.py") if p.name != "__init__.py")
    assert len(ui_files) >= 5, "Expected UI modules under app/ui/"

    missing_keys: list[str] = []
    hardcoded_violations: list[str] = []

    for py_path in [*ui_files, *sorted(REPORTING_DIR.glob("*.py"))]:
        tree = ast.parse(py_path.read_text(encoding="utf-8"), filename=str(py_path))
        is_ui_file = py_path.parent == UI_DIR

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue

            # Check literal keys passed to .t("...")
            if isinstance(node.func, ast.Attribute) and node.func.attr == "t" and node.args:
                arg0 = node.args[0]
                if isinstance(arg0, ast.Constant) and isinstance(arg0.value, str):
                    if arg0.value not in valid_keys:
                        missing_keys.append(f"{py_path.name}:{node.lineno} -> {arg0.value!r}")

            if not is_ui_file:
                continue

            # Check UI constructor / setter literal strings
            func_name = None
            if isinstance(node.func, ast.Name):
                func_name = node.func.id
            elif isinstance(node.func, ast.Attribute):
                func_name = node.func.attr

            if func_name in _UI_TEXT_CONSTRUCTORS and node.args:
                arg0 = node.args[0]
                if isinstance(arg0, ast.Constant) and isinstance(arg0.value, str):
                    if _looks_like_untranslated_prose(arg0.value):
                        hardcoded_violations.append(
                            f"{py_path.name}:{node.lineno} {func_name}({arg0.value!r})"
                        )
            elif func_name in _UI_TEXT_SETTERS and node.args:
                arg0 = node.args[0]
                if isinstance(arg0, ast.Constant) and isinstance(arg0.value, str):
                    if _looks_like_untranslated_prose(arg0.value):
                        hardcoded_violations.append(
                            f"{py_path.name}:{node.lineno} .{func_name}({arg0.value!r})"
                        )
            elif func_name == "setHorizontalHeaderLabels" and node.args:
                arg0 = node.args[0]
                if isinstance(arg0, (ast.List, ast.Tuple)):
                    for elt in arg0.elts:
                        if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                            if _looks_like_untranslated_prose(elt.value):
                                hardcoded_violations.append(
                                    f"{py_path.name}:{node.lineno} header({elt.value!r})"
                                )

    assert not missing_keys, f"Undefined i18n keys referenced:\n" + "\n".join(missing_keys)
    assert not hardcoded_violations, (
        "Hardcoded UI prose strings found (must use i18n):\n" + "\n".join(hardcoded_violations)
    )
