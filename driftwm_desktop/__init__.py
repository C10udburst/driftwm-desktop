"""
driftwm-desktop: Modular, spatial desktop icons manager for DriftWM.
"""
from .config import APP_ID, get_xdg_desktop_dir, get_state_file_path
from .widget import DesktopItemWidget
from .manager import DesktopManager
from .daemon import DriftwmDesktopDaemon
from .driftwm import get_state, move_window
from .i18n import tr, set_language

__all__ = [
    "APP_ID",
    "get_xdg_desktop_dir",
    "get_state_file_path",
    "DesktopItemWidget",
    "DesktopManager",
    "DriftwmDesktopDaemon",
    "get_state",
    "move_window",
    "tr",
    "set_language"
]
