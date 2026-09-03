import os
from pathlib import Path

# DriftWM window identification
APP_ID = "driftwm.desktop"
# QtWayland automatically chops ".desktop" suffix, so we pass with double suffix to preserve it
QT_DESKTOP_FILE_NAME = "driftwm.desktop.desktop"

def get_state_file_path() -> Path:
    """Returns the path to the desktop launchers state JSON file (~/.local/state/driftwm-desktop.json)."""
    xdg_state_home = os.environ.get("XDG_STATE_HOME")
    if xdg_state_home:
        base_dir = Path(xdg_state_home)
    else:
        base_dir = Path.home() / ".local" / "state"
    base_dir.mkdir(parents=True, exist_ok=True)
    return base_dir / "driftwm-desktop.json"

def get_xdg_desktop_dir() -> Path:
    """Dynamically retrieves the XDG Desktop directory."""
    if "XDG_DESKTOP_DIR" in os.environ:
        return Path(os.environ["XDG_DESKTOP_DIR"])

    config_path = Path.home() / ".config" / "user-dirs.dirs"
    if config_path.exists():
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("XDG_DESKTOP_DIR="):
                        raw_path = line.split("=", 1)[1].strip().strip('"').strip("'")
                        return Path(os.path.expandvars(raw_path))
        except Exception:
            pass

    return Path.home() / "Desktop"

# Sizing and UI defaults
ICON_SIZE = 36
GRID_SPACING_X = 140
GRID_SPACING_Y = 100
DEFAULT_OFFSET_X = 100
DEFAULT_OFFSET_Y = 100
DEBOUNCE_SAVE_INTERVAL = 0.3  # seconds
