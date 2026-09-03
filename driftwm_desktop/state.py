import json
import os
import tempfile
from pathlib import Path
from typing import Dict, List
from .config import get_state_file_path

def load_positions() -> Dict[str, List[int]]:
    """
    Loads saved window positions mapping {filename: [x, y]} from ~/.local/state/driftwm-desktop.json.
    """
    state_file = get_state_file_path()
    if not state_file.exists():
        return {}

    try:
        with open(state_file, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                # Normalize formats: support either [x, y] or {"x": x, "y": y}
                normalized = {}
                for k, v in data.items():
                    if isinstance(v, (list, tuple)) and len(v) >= 2:
                        normalized[k] = [int(round(v[0])), int(round(v[1]))]
                    elif isinstance(v, dict) and "x" in v and "y" in v:
                        normalized[k] = [int(round(v["x"])), int(round(v["y"]))]
                return normalized
    except Exception as e:
        print(f"Error loading state from {state_file}: {e}")
    return {}

def save_positions(positions: Dict[str, List[int]]) -> bool:
    """
    Atomically writes positions mapping {filename: [x, y]} to ~/.local/state/driftwm-desktop.json.
    """
    state_file = get_state_file_path()
    try:
        state_file.parent.mkdir(parents=True, exist_ok=True)
        # Write to temporary file in the same directory first to ensure atomic replacement
        with tempfile.NamedTemporaryFile("w", dir=str(state_file.parent), delete=False, encoding="utf-8") as tf:
            json.dump(positions, tf, indent=2, ensure_ascii=False)
            temp_path = tf.name

        os.replace(temp_path, str(state_file))
        return True
    except Exception as e:
        print(f"Error saving state to {state_file}: {e}")
        return False
