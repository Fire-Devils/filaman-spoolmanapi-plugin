"""Persistent settings for the SpoolmanAPI plugin.

Settings are stored as a JSON file inside the plugin directory so they
survive plugin updates via ZIP re-install.  The file is created lazily
on first write.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from threading import Lock

from .schemas import SpoolmanAPISettings

_logger = logging.getLogger(__name__)

_SETTINGS_FILE = Path(__file__).parent / "settings.json"
_lock = Lock()

# Module-level cache so we don't read from disk on every request.
_cached: SpoolmanAPISettings | None = None


def load_settings() -> SpoolmanAPISettings:
    """Return current settings (cached after first load)."""
    global _cached
    if _cached is not None:
        return _cached

    with _lock:
        # Double-check after acquiring the lock.
        if _cached is not None:
            return _cached

        if _SETTINGS_FILE.exists():
            try:
                raw = json.loads(_SETTINGS_FILE.read_text(encoding="utf-8"))
                _cached = SpoolmanAPISettings(**raw)
            except Exception:
                _logger.exception("Fehler beim Laden der SpoolmanAPI-Settings – verwende Defaults")
                _cached = SpoolmanAPISettings()
        else:
            _cached = SpoolmanAPISettings()

    return _cached


def save_settings(settings: SpoolmanAPISettings) -> None:
    """Persist *settings* to disk and update the module cache."""
    global _cached

    with _lock:
        _SETTINGS_FILE.write_text(
            json.dumps(settings.model_dump(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        _cached = settings

    _logger.info("SpoolmanAPI-Settings gespeichert: %s", _SETTINGS_FILE)
