"""
ui.icons - shared access to the plugin's package icon, used by every
QgsSourceSelectProvider so all three Data Source Manager categories show
the same real icon, rather than each provider needing its own copy of
the path-resolution logic.
"""

from pathlib import Path

from qgis.PyQt.QtGui import QIcon

_ICON_PATH = Path(__file__).resolve().parent.parent / "icon.png"


def plugin_icon() -> QIcon:
    """Returns the plugin's package icon (sigate/icon.png), resolved
    relative to this module's own location rather than any assumption
    about where the plugin happens to be installed.

    Returns:
        A QIcon. If icon.png is missing, Qt's own QIcon constructor does
        not raise - it silently returns a null (empty) icon rather than
        an error."""
    return QIcon(str(_ICON_PATH))
