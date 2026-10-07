"""
ui.settings - typed get/set access to SIGate's scalar settings, backed by
QgsSettings under the "SIGate" namespace. Per the config-storage rule
established for this project, QgsSettings is used only for genuinely
small scalar values (a size threshold, a file path, a locale code) - the
growable source-config list lives elsewhere, as JSON.
"""

from pathlib import Path
from typing import Optional

from qgis.core import QgsApplication, QgsSettings

from sigate.download.pipeline import DEFAULT_SIZE_WARNING_THRESHOLD_BYTES

_NAMESPACE = "SIGate"
_KEY_SIZE_WARNING_THRESHOLD_BYTES = f"{_NAMESPACE}/size_warning_threshold_bytes"
_KEY_SEVENZIP_PATH_OVERRIDE = f"{_NAMESPACE}/sevenzip_path_override"
_KEY_LOCALE = f"{_NAMESPACE}/locale"
_KEY_LAST_CENTRAL_REPO = f"{_NAMESPACE}/last_central_repo"
_KEY_LAST_DESTINATION = f"{_NAMESPACE}/last_destination"
_KEY_LAST_COUNTRY_FILTER = f"{_NAMESPACE}/last_country_filter"
_KEY_LAST_ORGANISATION_FILTER = f"{_NAMESPACE}/last_organisation_filter"

DEFAULT_LOCALE = "en"


def get_sigate_data_dir() -> Path:
    """The canonical location for SIGate's own user-growable JSON files
    (source config overrides, in particular) - a SIGate-specific
    subfolder within the active QGIS profile's own settings directory,
    rather than writing directly into the profile root."""
    return Path(QgsApplication.qgisSettingsDirPath()) / "sigate"


def get_size_warning_threshold_bytes() -> int:
    # Stored and read back as a string rather than via QgsSettings'
    # type=int coercion: that coercion goes through a 32-bit signed C int
    # internally, which silently wraps around for values at or above 2 GB
    # (2**31) - a realistic value for this exact setting.
    settings = QgsSettings()
    value = settings.value(
        _KEY_SIZE_WARNING_THRESHOLD_BYTES,
        str(DEFAULT_SIZE_WARNING_THRESHOLD_BYTES),
        type=str,
    )
    return int(value)


def set_size_warning_threshold_bytes(value: int) -> None:
    """Raises ValueError if value is negative - a negative byte
    threshold has no meaningful interpretation for this setting."""
    if value < 0:
        raise ValueError("size warning threshold must be non-negative")
    QgsSettings().setValue(_KEY_SIZE_WARNING_THRESHOLD_BYTES, str(int(value)))


def get_sevenzip_path_override() -> Optional[str]:
    settings = QgsSettings()
    value = settings.value(_KEY_SEVENZIP_PATH_OVERRIDE, "", type=str)
    return value or None


def set_sevenzip_path_override(path: Optional[str]) -> None:
    """path=None clears the override, reverting to auto-detection."""
    QgsSettings().setValue(_KEY_SEVENZIP_PATH_OVERRIDE, path or "")


def get_locale() -> str:
    settings = QgsSettings()
    return settings.value(_KEY_LOCALE, DEFAULT_LOCALE, type=str)


def set_locale(locale: str) -> None:
    QgsSettings().setValue(_KEY_LOCALE, locale)


def get_last_central_repo() -> Optional[str]:
    settings = QgsSettings()
    value = settings.value(_KEY_LAST_CENTRAL_REPO, "", type=str)
    return value or None


def set_last_central_repo(path: Optional[str]) -> None:
    """path=None clears the remembered value."""
    QgsSettings().setValue(_KEY_LAST_CENTRAL_REPO, path or "")


def get_last_destination() -> Optional[str]:
    settings = QgsSettings()
    value = settings.value(_KEY_LAST_DESTINATION, "", type=str)
    return value or None


def set_last_destination(path: Optional[str]) -> None:
    """path=None clears the remembered value."""
    QgsSettings().setValue(_KEY_LAST_DESTINATION, path or "")


def get_last_country_filter() -> Optional[str]:
    """The country last picked in a connection manager's country combo,
    shared by every tab; None means "all countries"."""
    value = QgsSettings().value(_KEY_LAST_COUNTRY_FILTER, "", type=str)
    return value or None


def set_last_country_filter(country: Optional[str]) -> None:
    """country=None (all countries) clears the remembered value."""
    QgsSettings().setValue(_KEY_LAST_COUNTRY_FILTER, country or "")


def get_last_organisation_filter() -> Optional[str]:
    """The organisation key last picked in a connection manager's
    organisation combo; None means "all organisations"."""
    value = QgsSettings().value(_KEY_LAST_ORGANISATION_FILTER, "", type=str)
    return value or None


def set_last_organisation_filter(organisation: Optional[str]) -> None:
    """organisation=None (all organisations) clears the remembered value."""
    QgsSettings().setValue(_KEY_LAST_ORGANISATION_FILTER, organisation or "")
