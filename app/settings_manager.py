"""Persistent application settings manager backed by PySide6 QSettings."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QByteArray, QSettings

from core.models import CorporateBrandingProfile


class SettingsManager:
    """Persists window geometry, dock state, layout snapshots, theme, language, and branding."""

    KEY_LANGUAGE = "ui/language"
    KEY_THEME = "ui/theme"
    KEY_WINDOW_GEOMETRY = "layout/window_geometry"
    KEY_WINDOW_STATE = "layout/window_state"
    KEY_DEFAULT_GEOMETRY = "layout/default_geometry"
    KEY_DEFAULT_STATE = "layout/default_state"
    KEY_BRANDING_JSON = "reporting/branding_json"
    KEY_LAST_DIR = "io/last_directory"

    VALID_LANGUAGES = ("es", "en")
    VALID_THEMES = ("light", "dark")

    def __init__(self, qsettings: Optional[QSettings] = None) -> None:
        self._settings = qsettings or QSettings("MetBalancePro", "MetBalancePro")

    @property
    def qsettings(self) -> QSettings:
        """Return underlying QSettings instance."""
        return self._settings

    def sync(self) -> None:
        """Flush pending changes to persistent storage."""
        self._settings.sync()

    # --- Language ---

    def get_language(self) -> str:
        """Return saved UI language code ('es' or 'en'), defaulting to 'es'."""
        val = str(self._settings.value(self.KEY_LANGUAGE, "es") or "es")
        return val if val in self.VALID_LANGUAGES else "es"

    def set_language(self, lang: str) -> None:
        """Persist active language code ('es' or 'en')."""
        if lang not in self.VALID_LANGUAGES:
            raise ValueError(f"Unsupported language '{lang}'. Expected one of {self.VALID_LANGUAGES}.")
        self._settings.setValue(self.KEY_LANGUAGE, lang)
        self._settings.sync()

    # --- Theme ---

    def get_theme(self) -> str:
        """Return saved engineering theme ('light' or 'dark'), defaulting to 'light'."""
        val = str(self._settings.value(self.KEY_THEME, "light") or "light")
        return val if val in self.VALID_THEMES else "light"

    def set_theme(self, theme: str) -> None:
        """Persist engineering theme ('light' or 'dark')."""
        if theme not in self.VALID_THEMES:
            raise ValueError(f"Unsupported theme '{theme}'. Expected one of {self.VALID_THEMES}.")
        self._settings.setValue(self.KEY_THEME, theme)
        self._settings.sync()

    # --- Geometry & Dock Layout ---

    def _read_byte_array(self, key: str) -> Optional[QByteArray]:
        raw = self._settings.value(key)
        if raw is None:
            return None
        if isinstance(raw, QByteArray):
            return raw if not raw.isEmpty() else None
        if isinstance(raw, (bytes, bytearray)):
            ba = QByteArray(raw)
            return ba if not ba.isEmpty() else None
        return None

    @staticmethod
    def _to_byte_array(data: QByteArray | bytes) -> QByteArray:
        return data if isinstance(data, QByteArray) else QByteArray(data)

    def save_window_geometry(self, geometry: QByteArray | bytes) -> None:
        """Save current QMainWindow geometry."""
        self._settings.setValue(self.KEY_WINDOW_GEOMETRY, self._to_byte_array(geometry))
        self._settings.sync()

    def load_window_geometry(self) -> Optional[QByteArray]:
        """Load previously saved QMainWindow geometry."""
        return self._read_byte_array(self.KEY_WINDOW_GEOMETRY)

    def save_window_state(self, state: QByteArray | bytes) -> None:
        """Save current QMainWindow dock/toolbar state."""
        self._settings.setValue(self.KEY_WINDOW_STATE, self._to_byte_array(state))
        self._settings.sync()

    def load_window_state(self) -> Optional[QByteArray]:
        """Load previously saved QMainWindow dock/toolbar state."""
        return self._read_byte_array(self.KEY_WINDOW_STATE)

    def save_default_layout(self, geometry: QByteArray | bytes, state: QByteArray | bytes) -> None:
        """Store default dock/window layout snapshot used by View -> Reset Layout."""
        self._settings.setValue(self.KEY_DEFAULT_GEOMETRY, self._to_byte_array(geometry))
        self._settings.setValue(self.KEY_DEFAULT_STATE, self._to_byte_array(state))
        self._settings.sync()

    def load_default_geometry(self) -> Optional[QByteArray]:
        """Return default window geometry snapshot if saved."""
        return self._read_byte_array(self.KEY_DEFAULT_GEOMETRY)

    def load_default_state(self) -> Optional[QByteArray]:
        """Return default dock state snapshot if saved."""
        return self._read_byte_array(self.KEY_DEFAULT_STATE)

    def has_default_layout(self) -> bool:
        """Return True if a default layout snapshot exists."""
        return self.load_default_state() is not None

    # --- Corporate Branding JSON Persistence ---

    def save_branding(self, profile: CorporateBrandingProfile) -> None:
        """Persist CorporateBrandingProfile as JSON."""
        self._settings.setValue(self.KEY_BRANDING_JSON, profile.model_dump_json())
        self._settings.sync()

    def load_branding(self) -> CorporateBrandingProfile:
        """Load persisted CorporateBrandingProfile or return defaults."""
        raw = self._settings.value(self.KEY_BRANDING_JSON)
        if not raw or not isinstance(raw, str):
            return CorporateBrandingProfile()
        try:
            return CorporateBrandingProfile.model_validate_json(raw)
        except Exception:
            return CorporateBrandingProfile()

    # --- File Dialog Path Helper ---

    def get_last_directory(self) -> str:
        """Return last directory used in file open/save dialogs."""
        return str(self._settings.value(self.KEY_LAST_DIR, "") or "")

    def set_last_directory(self, directory: str) -> None:
        """Persist last directory used in file open/save dialogs."""
        self._settings.setValue(self.KEY_LAST_DIR, directory)
        self._settings.sync()
