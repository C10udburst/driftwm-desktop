import subprocess
import json
import shutil
from typing import Dict, List, Optional, Tuple, Generator
from .config import APP_ID

def is_driftwm_available() -> bool:
    """Checks whether driftwm binary is installed and reachable."""
    return shutil.which("driftwm") is not None

def run_driftwm_cmd(args: List[str]) -> Optional[dict]:
    """Executes a driftwm msg command and returns the parsed JSON reply."""
    cmd = ["driftwm", "msg"] + args + ["--json"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return json.loads(proc.stdout)
    except Exception:
        return None

def get_state() -> Optional[dict]:
    """Fetches current driftwm state."""
    res = run_driftwm_cmd(["state"])
    if res and "Ok" in res and "State" in res["Ok"]:
        return res["Ok"]["State"]
    return None

def get_windows() -> List[dict]:
    """Returns the list of current windows reported by driftwm."""
    state = get_state()
    if state and "windows" in state:
        return state["windows"]
    return []

def is_our_window(window_data: dict, target_app_id: str = APP_ID) -> bool:
    """Checks if window_data belongs to our desktop launcher."""
    app_id = window_data.get("app_id", "")
    # Check exact match or startswith to handle potential variations
    return app_id == target_app_id or app_id == "driftwm" or app_id.startswith(target_app_id)

def get_desktop_windows_map(target_app_id: str = APP_ID) -> Dict[str, dict]:
    """
    Returns a dictionary mapping window title (filename) to its window details:
    {
        filename: {"id": window_id, "position": [x, y], "size": [w, h]}
    }
    """
    mapping = {}
    for win in get_windows():
        if is_our_window(win, target_app_id):
            title = win.get("title")
            if title:
                mapping[title] = win
    return mapping

def move_window(window_id: int, x: float, y: float) -> bool:
    """Reposition window in driftwm canvas coordinate space."""
    cmd = ["driftwm", "msg", "move", "--id", str(window_id), str(int(round(x))), str(int(round(y))), "--json"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=True)
        res = json.loads(proc.stdout)
        return "Ok" in res
    except Exception:
        return False

def subscribe_stream() -> Generator[dict, None, None]:
    """
    Spawns 'driftwm msg subscribe --json' and yields state updates.
    Each update is {"State": {...}}.
    """
    cmd = ["driftwm", "msg", "subscribe", "--json"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        for line in iter(proc.stdout.readline, ""):
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                if "State" in data:
                    yield data["State"]
            except json.JSONDecodeError:
                continue
    finally:
        proc.kill()
