import os
import shlex
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from PyQt5.QtCore import QMimeDatabase

try:
    import gi
    gi.require_version('Gio', '2.0')
    from gi.repository import Gio
    HAS_GIO = True
except Exception:
    HAS_GIO = False

@dataclass
class DesktopAction:
    action_id: str
    name: str
    cmd: List[str]
    icon_name: str = ""

@dataclass
class DesktopItemInfo:
    filepath: Path
    display_name: str
    icon_name: str
    mime_type: str = ""
    icon_candidates: List[str] = field(default_factory=list)
    cmd: List[str] = field(default_factory=list)
    actions: List[DesktopAction] = field(default_factory=list)
    is_desktop: bool = False
    is_dir: bool = False

_MIME_DB: Optional[QMimeDatabase] = None

def get_mime_database() -> QMimeDatabase:
    """Singleton getter for QMimeDatabase."""
    global _MIME_DB
    if _MIME_DB is None:
        _MIME_DB = QMimeDatabase()
    return _MIME_DB

def resolve_icon_candidates(filepath: Path) -> Tuple[str, str, List[str]]:
    """
    Determines MIME type and candidate icon names for a given file.
    Utilizes QMimeDatabase (FreeDesktop shared-mime-info standard) and GIO.
    Returns (mime_type_name, primary_icon_name, candidate_list).
    """
    filepath = Path(filepath)
    if filepath.is_dir():
        return ("inode/directory", "folder", ["folder", "inode-directory"])

    mime_db = get_mime_database()
    mt = mime_db.mimeTypeForFile(str(filepath))
    mime_name = mt.name()

    candidates: List[str] = []

    # 1. Primary icon name from MIME database (e.g. application-pdf, image-png, model-stl)
    icon_name = mt.iconName()
    if icon_name and icon_name not in candidates:
        candidates.append(icon_name)

    # 2. Normalized mime slug (e.g. model/stl -> model-stl)
    mime_slug = mime_name.replace("/", "-")
    if mime_slug not in candidates:
        candidates.append(mime_slug)

    # 3. Query system GIO for content-type icon suggestions if available
    if HAS_GIO:
        try:
            gfile = Gio.File.new_for_path(str(filepath))
            info = gfile.query_info("standard::icon", Gio.FileQueryInfoFlags.NONE, None)
            gicon = info.get_icon()
            if gicon:
                for name in gicon.get_names():
                    if name and name not in candidates:
                        candidates.append(name)
        except Exception:
            pass

    # 4. Generic MIME category icon (e.g. x-office-document, image-x-generic)
    gen_icon = mt.genericIconName()
    if gen_icon and gen_icon not in candidates:
        candidates.append(gen_icon)

    # 5. Top-level category fallbacks
    if mime_name.startswith("image/"):
        for fb in ["image-x-generic", "image"]:
            if fb not in candidates:
                candidates.append(fb)
    elif mime_name.startswith("video/"):
        for fb in ["video-x-generic", "video"]:
            if fb not in candidates:
                candidates.append(fb)
    elif mime_name.startswith("audio/"):
        for fb in ["audio-x-generic", "audio"]:
            if fb not in candidates:
                candidates.append(fb)
    elif mime_name.startswith("text/"):
        for fb in ["text-x-generic", "text-plain"]:
            if fb not in candidates:
                candidates.append(fb)
    elif mime_name.startswith("model/"):
        for fb in ["model-stl", "image-x-generic", "text-x-generic"]:
            if fb not in candidates:
                candidates.append(fb)
    elif any(term in mime_name for term in ["compressed", "archive", "zip", "tar", "gzip"]):
        for fb in ["package-x-generic", "application-x-tar", "application-zip"]:
            if fb not in candidates:
                candidates.append(fb)

    # Generic catch-all
    if "text-x-generic" not in candidates:
        candidates.append("text-x-generic")

    best_icon = candidates[0] if candidates else "text-x-generic"
    return (mime_name, best_icon, candidates)

def parse_item_info(filepath: Path) -> DesktopItemInfo:
    """Parses display name, icon name, and execution command for a given desktop file or path."""
    filepath = Path(filepath)
    is_desktop = filepath.suffix == ".desktop"
    is_dir = filepath.is_dir()
    display_name = filepath.name
    icon_name = ""
    cmd = []
    actions: List[DesktopAction] = []

    mime_type, best_icon, candidates = resolve_icon_candidates(filepath)

    if is_desktop:
        exec_cmd = ""
        try:
            import configparser
            from .i18n import get_current_language
            curr_lang = get_current_language()

            cp = configparser.ConfigParser(interpolation=None, strict=False)
            cp.read(filepath, encoding="utf-8")

            if "Desktop Entry" in cp:
                entry = cp["Desktop Entry"]
                name_val = entry.get(f"Name[{curr_lang}]") or entry.get("Name")
                if name_val:
                    display_name = name_val.strip()
                if entry.get("Icon"):
                    icon_name = entry.get("Icon").strip()
                exec_cmd = entry.get("Exec", "")

                actions_str = entry.get("Actions", "")
                action_ids = [a.strip() for a in actions_str.split(";") if a.strip()]
                for aid in action_ids:
                    sec_name = f"Desktop Action {aid}"
                    if sec_name in cp:
                        sec = cp[sec_name]
                        act_name = sec.get(f"Name[{curr_lang}]") or sec.get("Name", aid).strip()
                        act_exec = sec.get("Exec", "")
                        act_icon = sec.get("Icon", "").strip()
                        act_cmd = []
                        if act_exec:
                            try:
                                for word in shlex.split(act_exec):
                                    if not word.startswith("%"):
                                        act_cmd.append(word)
                            except Exception:
                                act_cmd = [w for w in act_exec.split() if not w.startswith("%")]
                        if act_cmd:
                            actions.append(DesktopAction(
                                action_id=aid,
                                name=act_name,
                                cmd=act_cmd,
                                icon_name=act_icon
                            ))
        except Exception:
            pass

        # Clean XDG parameter codes (%u, %F, %i, %c, %k, etc.)
        if exec_cmd:
            try:
                for word in shlex.split(exec_cmd):
                    if not word.startswith("%"):
                        cmd.append(word)
            except Exception:
                cmd = [w for w in exec_cmd.split() if not w.startswith("%")]

        if not icon_name:
            icon_name = "application-x-executable"
        candidates = [icon_name, "application-x-desktop", "application-x-executable"]
    elif is_dir:
        icon_name = "folder"
    else:
        icon_name = best_icon

    return DesktopItemInfo(
        filepath=filepath,
        display_name=display_name,
        icon_name=icon_name,
        mime_type=mime_type,
        icon_candidates=candidates,
        cmd=cmd,
        actions=actions,
        is_desktop=is_desktop,
        is_dir=is_dir
    )
