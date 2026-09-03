import time
import threading
import signal
import sys
from pathlib import Path
from typing import Dict, List, Optional
from .config import APP_ID, DEBOUNCE_SAVE_INTERVAL, get_state_file_path, get_xdg_desktop_dir
from .driftwm import subscribe_stream, is_our_window
from .state import load_positions, save_positions
from .i18n import tr

class DriftwmDesktopDaemon:
    """
    Background daemon that listens to 'driftwm msg subscribe --json',
    filters windows matching our app_id ('driftwm.desktop'), and persists
    their positions mapped by filename (title) to ~/.local/state/driftwm-desktop.json.
    
    Includes an 'enabled' guard to eliminate boot race conditions where random
    window positions assigned by the window manager during initial placement
    might otherwise overwrite the user's saved state.
    """

    def __init__(
        self,
        target_app_id: str = APP_ID,
        desktop_dir: Optional[Path] = None,
        debounce_interval: float = DEBOUNCE_SAVE_INTERVAL
    ):
        self.target_app_id = target_app_id
        self.desktop_dir = Path(desktop_dir) if desktop_dir else get_xdg_desktop_dir()
        self.debounce_interval = debounce_interval
        self.positions: Dict[str, List[int]] = load_positions()
        self.lock = threading.RLock()
        self.save_timer: Optional[threading.Timer] = None
        self.running = False
        # The daemon starts disabled so boot placement by the WM does not overwrite saved positions
        self.enabled = False
        self._thread: Optional[threading.Thread] = None

    def enable(self):
        """Enables position recording after positions have been restored."""
        with self.lock:
            self.enabled = True

    def disable(self):
        """Pauses position recording."""
        with self.lock:
            self.enabled = False

    def start_background(self):
        """Starts the daemon listener in a background daemon thread."""
        if self.running:
            return
        self.running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="DriftwmDesktopDaemon")
        self._thread.start()

    def run_foreground(self):
        """Runs the daemon listener in the foreground (for CLI daemon mode)."""
        self.running = True
        self.enabled = True
        print(tr("daemon_started", path=get_state_file_path()))

        def _signal_handler(sig, frame):
            print(tr("daemon_stopping"))
            self.stop()
            sys.exit(0)

        signal.signal(signal.SIGINT, _signal_handler)
        signal.signal(signal.SIGTERM, _signal_handler)

        try:
            self._run_loop()
        finally:
            self.flush()

    def stop(self):
        """Stops the daemon and flushes pending writes."""
        self.running = False
        self.flush()

    def _schedule_save(self):
        """Debounces file writes to avoid excessive I/O at high compositor refresh rates."""
        with self.lock:
            if self.save_timer:
                self.save_timer.cancel()
            self.save_timer = threading.Timer(self.debounce_interval, self.flush)
            self.save_timer.start()

    def flush(self):
        """Immediately flushes in-memory positions to disk."""
        with self.lock:
            if self.save_timer:
                self.save_timer.cancel()
                self.save_timer = None
            save_positions(self.positions.copy())

    def update_position(self, filename: str, coords: List[int]):
        """Directly updates a window position from drag events."""
        with self.lock:
            self.positions[filename] = [int(round(coords[0])), int(round(coords[1]))]
            self._schedule_save()

    def update_filename(self, old_name: str, new_name: str):
        """Handles file rename event safely without deadlock."""
        with self.lock:
            if old_name in self.positions:
                self.positions[new_name] = self.positions.pop(old_name)
                self._schedule_save()

    def remove_filename(self, filename: str):
        """Handles file removal event safely without deadlock."""
        with self.lock:
            if filename in self.positions:
                del self.positions[filename]
                self._schedule_save()

    def _run_loop(self):
        """Consumes state events from driftwm subscribe stream."""
        for state in subscribe_stream():
            if not self.running:
                break

            # Drop all incoming events until initial restore is completed
            if not self.enabled:
                continue

            windows = state.get("windows", [])
            changed = False

            with self.lock:
                for win in windows:
                    if is_our_window(win, self.target_app_id):
                        filename = win.get("title")
                        pos = win.get("position")
                        if not filename or not pos or not isinstance(pos, list) or len(pos) < 2:
                            continue

                        # Filter out dialogs (e.g. Rename / Confirmation dialogs) by verifying the file exists or is tracked
                        is_tracked = filename in self.positions
                        is_file_on_desktop = self.desktop_dir and (self.desktop_dir / filename).exists()
                        if not (is_tracked or is_file_on_desktop):
                            continue

                        int_pos = [int(round(pos[0])), int(round(pos[1]))]
                        if self.positions.get(filename) != int_pos:
                            self.positions[filename] = int_pos
                            changed = True

            if changed:
                self._schedule_save()

if __name__ == "__main__":
    daemon = DriftwmDesktopDaemon()
    daemon.run_foreground()
