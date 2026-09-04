import sys
import argparse
from pathlib import Path

from PyQt5.QtWidgets import QApplication

from .config import QT_DESKTOP_FILE_NAME, get_xdg_desktop_dir
from .manager import DesktopManager
from .daemon import DriftwmDesktopDaemon
from .i18n import tr, set_language

def main():
    parser = argparse.ArgumentParser(description="driftwm-desktop: Modular Desktop Icons for DriftWM")
    parser.add_argument(
        "--daemon",
        action="store_true",
        help="Run only the driftwm subscription daemon to track window coordinates"
    )
    parser.add_argument(
        "--desktop-dir",
        type=Path,
        default=None,
        help="Custom desktop directory (defaults to XDG Desktop folder)"
    )
    parser.add_argument(
        "--no-daemon",
        action="store_true",
        help="Do not start the background subscribe daemon with the GUI"
    )
    parser.add_argument(
        "--restore-only",
        action="store_true",
        help="Only restore coordinates for already running desktop windows and exit"
    )
    parser.add_argument(
        "--reset-positions",
        "--reset",
        action="store_true",
        help="Reset all desktop item positions to a clean default grid and update saved state"
    )
    parser.add_argument(
        "--lang",
        type=str,
        default=None,
        help="Override language code (e.g. 'en', 'pl', defaults to environment/system locale)"
    )

    args = parser.parse_args()

    if args.lang:
        set_language(args.lang)

    desktop_dir = args.desktop_dir or get_xdg_desktop_dir()

    if args.reset_positions:
        app = QApplication.instance()
        if not app:
            app = QApplication(sys.argv)
            app.setApplicationName(QT_DESKTOP_FILE_NAME)
        manager = DesktopManager(desktop_dir=desktop_dir, enable_daemon=False)
        manager.reset_positions()
        print(tr("positions_reset"))
        sys.exit(0)

    if args.daemon:
        daemon = DriftwmDesktopDaemon(desktop_dir=desktop_dir)
        daemon.run_foreground()
        return

    # Initialize Qt Application
    app = QApplication(sys.argv)
    app.setDesktopFileName(QT_DESKTOP_FILE_NAME)
    app.setApplicationName("driftwm.desktop")

    if not desktop_dir.exists():
        print(tr("desktop_not_found", path=desktop_dir))
        sys.exit(1)

    manager = DesktopManager(
        desktop_dir=desktop_dir,
        enable_daemon=not args.no_daemon
    )

    if args.restore_only:
        manager.restore_positions()
        sys.exit(0)

    # Start the desktop lifecycle:
    # 1. Spawns widgets
    # 2. Starts daemon in paused/disabled state (immune to random boot placement)
    # 3. Restores saved positions once windows are mapped by driftwm
    # 4. Enables daemon for real-time tracking and drag & drop
    manager.start_lifecycle()

    if not manager.widgets:
        print(tr("no_files_found", path=desktop_dir))
        sys.exit(0)

    app.aboutToQuit.connect(manager.shutdown)
    sys.exit(app.exec_())

if __name__ == "__main__":
    main()
