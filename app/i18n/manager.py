"""Pure-Python reactive internationalization (i18n) manager for MetBalance Pro.

This module MUST NOT import PySide6 so that dictionaries and translations can be
tested and used by headless reporting or diagnostic components.
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path
from typing import Any, Callable, Optional


SUPPORTED_LANGUAGES: tuple[str, ...] = ("es", "en")


def _flatten_keys(mapping: dict[str, Any], prefix: str = "") -> set[str]:
    """Recursively collect dotted keys from a nested translation dictionary."""
    keys: set[str] = set()
    for key, value in mapping.items():
        full_key = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, dict):
            keys.update(_flatten_keys(value, full_key))
        else:
            keys.add(full_key)
    return keys


class I18nManager:
    """Thread-friendly in-memory translation loader and observer registry."""

    def __init__(
        self,
        default_language: str = "es",
        locales_dir: Optional[Path] = None,
    ) -> None:
        if default_language not in SUPPORTED_LANGUAGES:
            raise ValueError(
                f"Unsupported language '{default_language}'. Expected one of {SUPPORTED_LANGUAGES}."
            )
        self._locales_dir = Path(locales_dir) if locales_dir else Path(__file__).resolve().parent
        self._dictionaries: dict[str, dict[str, Any]] = {}
        self._language: str = default_language
        self._subscribers: list[Callable[..., None]] = []
        self._load_dictionaries()

    def _load_dictionaries(self) -> None:
        for lang in SUPPORTED_LANGUAGES:
            path = self._locales_dir / f"{lang}.json"
            if not path.exists():
                raise FileNotFoundError(f"Missing translation dictionary: {path}")
            with path.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
            if not isinstance(data, dict):
                raise ValueError(f"Translation file {path} must contain a JSON object.")
            self._dictionaries[lang] = data

    @property
    def language(self) -> str:
        """Return currently active language code ('es' or 'en')."""
        return self._language

    @language.setter
    def language(self, lang: str) -> None:
        self.set_language(lang)

    def available_languages(self) -> tuple[str, ...]:
        """Return supported language codes."""
        return SUPPORTED_LANGUAGES

    def set_language(self, lang: str) -> None:
        """Switch active language and notify all registered subscribers."""
        if lang not in SUPPORTED_LANGUAGES:
            raise ValueError(f"Unsupported language '{lang}'. Expected one of {SUPPORTED_LANGUAGES}.")
        if lang == self._language:
            return
        self._language = lang
        self._notify_subscribers()

    def _notify_subscribers(self) -> None:
        for cb in list(self._subscribers):
            if self._accepts_language_arg(cb):
                cb(self._language)
            else:
                cb()

    @staticmethod
    def _accepts_language_arg(callback: Callable[..., None]) -> bool:
        try:
            sig = inspect.signature(callback)
        except (TypeError, ValueError):
            return True
        positional = [
            p
            for p in sig.parameters.values()
            if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
        ]
        has_varargs = any(p.kind == p.VAR_POSITIONAL for p in sig.parameters.values())
        return has_varargs or len(positional) >= 1

    def subscribe(self, cb: Callable[..., None]) -> None:
        """Register a callback invoked whenever the active language changes."""
        if cb not in self._subscribers:
            self._subscribers.append(cb)

    def unsubscribe(self, cb: Callable[..., None]) -> None:
        """Remove a previously registered language-change callback."""
        if cb in self._subscribers:
            self._subscribers.remove(cb)

    def _lookup(self, dictionary: dict[str, Any], key: str) -> Optional[str]:
        if key in dictionary and isinstance(dictionary[key], str):
            return dictionary[key]
        current: Any = dictionary
        for part in key.split("."):
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return None
        return current if isinstance(current, str) else None

    def t(self, key: str, **kwargs: Any) -> str:
        """Translate a dotted key using the active language and optional format kwargs."""
        text = self._lookup(self._dictionaries[self._language], key)
        if text is None:
            fallback_lang = "en" if self._language != "en" else "es"
            text = self._lookup(self._dictionaries[fallback_lang], key)
        if text is None:
            return key
        if kwargs:
            return text.format(**kwargs)
        return text

    def get_dictionary(self, lang: Optional[str] = None) -> dict[str, Any]:
        """Return raw dictionary for the given or currently active language."""
        target = lang or self._language
        if target not in SUPPORTED_LANGUAGES:
            raise ValueError(f"Unsupported language '{target}'.")
        return self._dictionaries[target]

    def get_all_keys(self, lang: Optional[str] = None) -> set[str]:
        """Return flattened dotted keys for the given or active language."""
        return _flatten_keys(self.get_dictionary(lang))


_i18n_singleton: Optional[I18nManager] = None


def get_i18n() -> I18nManager:
    """Return process-wide singleton I18nManager instance."""
    global _i18n_singleton
    if _i18n_singleton is None:
        _i18n_singleton = I18nManager()
    return _i18n_singleton


def reset_i18n() -> None:
    """Reset the singleton I18nManager instance (intended for isolated tests)."""
    global _i18n_singleton
    _i18n_singleton = None
