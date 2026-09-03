import time
from pathlib import Path
from typing import Dict, List, Optional

from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import QTimer, QFileSystemWatcher

from .config import (
    APP_ID, GRID_SPACING_X, GRID_SPACING_Y,
    get_xdg_desktop_dir, get_state_file_path
)
from .widget import DesktopItemWidget
from .driftwm import (
    is_driftwm_available, get_desktop_windows_map,
    move_window, get_state
)
from .state import load_positions, save_positions
from .daemon import DriftwmDesktopDaemon

class DesktopManager:
    """
    Manages desktop item widgets:
    - Spawns widgets for all desktop items
    - Actively monitors Desktop directory changes (additions, deletions, edits) via QFileSystemWatcher (inotify)
    - Eliminates race conditions on boot by ensuring initial WM placements don't overwrite saved coordinates
    - Restores window positions in DriftWM canvas via 'driftwm msg move --id <id> <x> <y>'
    - Enables background tracking daemon only after positions are confirmed
    - Handles interactive drag & drop coordinate updates
    - Resets positions to clean grid on request
    """

    def __init__(self, desktop_dir: Optional[Path] = None, enable_daemon: bool = True):
        self.desktop_dir = Path(desktop_dir) if desktop_dir else get_xdg_desktop_dir()
        self.enable_daemon = enable_daemon
        self.widgets: Dict[str, DesktopItemWidget] = {}
        self.daemon: Optional[DriftwmDesktopDaemon] = None

        # Filesystem watcher to dynamically reflect additions, deletions, and modifications
        self.watcher = QFileSystemWatcher()
        if self.desktop_dir.exists():
            self.watcher.addPath(str(self.desktop_dir.resolve()))
            self.watcher.directoryChanged.connect(self.on_directory_changed)

    def scan_items(self) -> List[Path]:
        """Scans the desktop directory for non-hidden files and folders."""
        if not self.desktop_dir.exists():
            return []
        items = [
            p for p in self.desktop_dir.iterdir()
            if not p.name.startswith(".") and (p.is_file() or p.is_dir())
        ]
        items.sort(key=lambda p: (not p.is_dir(), p.name.lower()))
        return items

    def spawn_windows(self):
        """Spawns all desktop item widgets and hooks callbacks."""
        items = self.scan_items()
        for filepath in items:
            win = DesktopItemWidget(
                filepath=filepath,
                on_deleted=self.on_item_deleted,
                on_renamed=self.on_item_renamed,
                on_moved=self.on_item_moved
            )
            win.show()
            self.widgets[filepath.name] = win

    def start_lifecycle(self):
        """
        Starts the desktop management lifecycle:
        1. Spawns widgets
        2. Starts the daemon in disabled/paused state (protecting saved positions from random boot positions)
        3. Schedules delayed position restore once the window manager finishes surface mapping
        """
        self.spawn_windows()
        if not self.widgets:
            return

        if self.enable_daemon and is_driftwm_available():
            self.daemon = DriftwmDesktopDaemon(
                target_app_id=APP_ID,
                desktop_dir=self.desktop_dir
            )
            # Daemon starts disabled to prevent recording random WM placements
            self.daemon.disable()
            self.daemon.start_background()

        # Schedule restore inside the event loop after the compositor maps the windows
        QTimer.singleShot(250, self.restore_positions)

    def restore_positions(self, max_wait_sec: float = 2.0):
        """
        Restores window positions using DriftWM IPC:
        - Waits until driftwm registers the newly mapped surfaces
        - Reads ~/.local/state/driftwm-desktop.json
        - Repositions each window via 'driftwm msg move --id <id> <x> <y>'
        - Binds driftwm_id and canvas_pos to each widget for real-time drag & drop
        - Enables the daemon after positions settle
        """
        if not is_driftwm_available():
            return

        app = QApplication.instance()
        if app:
            app.processEvents()

        # Wait until driftwm registers our spawned surfaces
        deadline = time.time() + max_wait_sec
        title_to_win = {}
        while time.time() < deadline:
            title_to_win = get_desktop_windows_map(APP_ID)
            if len(title_to_win) >= len(self.widgets):
                break
            if app:
                app.processEvents()
            time.sleep(0.05)

        saved_positions = load_positions()
        updated_saved = False

        # Reference anchor from camera for new, unplaced items
        state = get_state()
        camera = state.get("camera", [0, 0]) if state else [0, 0]
        cam_x, cam_y = camera[0], camera[1]
        grid_start_x = int(round(cam_x - 350))
        grid_start_y = int(round(cam_y + 250))

        idx = 0
        for filename, widget in self.widgets.items():
            win_info = title_to_win.get(filename)
            if not win_info:
                continue

            win_id = win_info["id"]
            widget.driftwm_id = win_id

            if filename in saved_positions:
                target_x, target_y = saved_positions[filename]
                move_window(win_id, target_x, target_y)
                widget.canvas_pos = [target_x, target_y]
            else:
                # Default grid layout for new files
                col = idx % 8
                row = idx // 8
                target_x = grid_start_x + (col * GRID_SPACING_X)
                target_y = grid_start_y - (row * GRID_SPACING_Y)
                move_window(win_id, target_x, target_y)
                saved_positions[filename] = [target_x, target_y]
                widget.canvas_pos = [target_x, target_y]
                updated_saved = True

            idx += 1

        if updated_saved:
            save_positions(saved_positions)

        # Allow the compositor 150ms to settle positions before enabling daemon
        QTimer.singleShot(150, self._finalize_restore)

    def _finalize_restore(self):
        """Synchronizes daemon state and enables position tracking."""
        if self.daemon:
            saved = load_positions()
            with self.daemon.lock:
                self.daemon.positions = saved.copy()
            self.daemon.enable()

    def on_directory_changed(self, path: str):
        """
        Handles live changes in Desktop directory (via inotify/QFileSystemWatcher):
        - Detects deleted files and closes their widgets
        - Detects added files, spawns their widgets, and positions them
        - Refreshes existing widgets if metadata changed
        """
        current_items = {p.name: p for p in self.scan_items()}
        current_names = set(current_items.keys())
        existing_names = set(self.widgets.keys())

        # 1. Handle deleted files
        removed = existing_names - current_names
        for name in removed:
            widget = self.widgets.pop(name, None)
            if widget:
                widget.close()
            if self.daemon:
                self.daemon.remove_filename(name)

        # 2. Handle added files
        added = current_names - existing_names
        if added:
            for name in added:
                filepath = current_items[name]
                win = DesktopItemWidget(
                    filepath=filepath,
                    on_deleted=self.on_item_deleted,
                    on_renamed=self.on_item_renamed,
                    on_moved=self.on_item_moved
                )
                win.show()
                self.widgets[name] = win

            app = QApplication.instance()
            if app:
                app.processEvents()

            # Schedule positioning of newly spawned items after compositor registers them
            QTimer.singleShot(200, lambda: self._position_new_items(list(added)))

        # 3. Refresh remaining files if their properties or content changed
        common = current_names & existing_names
        for name in common:
            widget = self.widgets.get(name)
            if widget:
                widget.refresh()

    def _position_new_items(self, item_names: List[str]):
        """Positions dynamically added desktop items."""
        title_to_win = get_desktop_windows_map(APP_ID)
        saved_positions = load_positions()
        updated_saved = False

        state = get_state()
        camera = state.get("camera", [0, 0]) if state else [0, 0]
        cam_x, cam_y = camera[0], camera[1]
        grid_start_x = int(round(cam_x - 350))
        grid_start_y = int(round(cam_y + 250))

        # Find existing occupied coordinates to avoid overlaps
        occupied_positions = set(tuple(p) for p in saved_positions.values())

        for name in item_names:
            win_info = title_to_win.get(name)
            widget = self.widgets.get(name)
            if not win_info or not widget:
                continue

            win_id = win_info["id"]
            widget.driftwm_id = win_id

            if name in saved_positions:
                target_x, target_y = saved_positions[name]
            else:
                # Find next free grid slot
                slot_idx = len(saved_positions)
                while True:
                    col = slot_idx % 8
                    row = slot_idx // 8
                    cand_x = grid_start_x + (col * GRID_SPACING_X)
                    cand_y = grid_start_y - (row * GRID_SPACING_Y)
                    if (cand_x, cand_y) not in occupied_positions:
                        target_x, target_y = cand_x, cand_y
                        break
                    slot_idx += 1

                saved_positions[name] = [target_x, target_y]
                occupied_positions.add((target_x, target_y))
                updated_saved = True

            move_window(win_id, target_x, target_y)
            widget.canvas_pos = [target_x, target_y]

        if updated_saved:
            save_positions(saved_positions)
            if self.daemon:
                with self.daemon.lock:
                    self.daemon.positions = saved_positions.copy()

    def reset_positions(self) -> bool:
        """
        Resets all desktop item positions to a clean default grid.
        Moves active windows in DriftWM if running, and saves the new grid
        coordinates to ~/.local/state/driftwm-desktop.json.
        """
        title_to_win = get_desktop_windows_map(APP_ID)

        state = get_state()
        camera = state.get("camera", [0, 0]) if state else [0, 0]
        cam_x, cam_y = camera[0], camera[1]
        grid_start_x = int(round(cam_x - 350))
        grid_start_y = int(round(cam_y + 250))

        items = list(self.widgets.keys()) if self.widgets else list(title_to_win.keys())
        if not items:
            items = [p.name for p in self.scan_items()]

        new_positions = {}
        for idx, filename in enumerate(items):
            col = idx % 8
            row = idx // 8
            target_x = grid_start_x + (col * GRID_SPACING_X)
            target_y = grid_start_y - (row * GRID_SPACING_Y)
            new_positions[filename] = [target_x, target_y]

            # If window is active in driftwm, move it
            win_info = title_to_win.get(filename)
            if win_info:
                win_id = win_info["id"]
                move_window(win_id, target_x, target_y)
                if filename in self.widgets:
                    self.widgets[filename].driftwm_id = win_id
                    self.widgets[filename].canvas_pos = [target_x, target_y]

        save_positions(new_positions)
        if self.daemon:
            with self.daemon.lock:
                self.daemon.positions = new_positions.copy()

        return True

    def on_item_moved(self, filename: str, coords: List[int]):
        """Handles widget drag-and-drop move event."""
        if self.daemon:
            self.daemon.update_position(filename, coords)

    def on_item_renamed(self, old_name: str, new_name: str):
        """Handles item rename event."""
        if old_name in self.widgets:
            self.widgets[new_name] = self.widgets.pop(old_name)
        if self.daemon:
            self.daemon.update_filename(old_name, new_name)

    def on_item_deleted(self, filename: str):
        """Handles item deletion event."""
        if filename in self.widgets:
            del self.widgets[filename]
        if self.daemon:
            self.daemon.remove_filename(filename)

    def shutdown(self):
        """Shuts down daemon on app termination."""
        if self.daemon:
            self.daemon.stop()
