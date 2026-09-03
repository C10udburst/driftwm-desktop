import os
import time
import shutil
import subprocess
from pathlib import Path
from typing import Optional, Dict

from .i18n import tr

_last_launch_by_path: Dict[Path, float] = {}

def launch_item(filepath: Path, cmd: Optional[list] = None, debounce_sec: float = 0.8) -> bool:
    """
    Launches a desktop application or opens a file/folder with default handler.
    Includes debounce protection to prevent multiple instances from rapid double-clicks.
    """
    filepath = Path(filepath).resolve()
    now = time.time()
    last = _last_launch_by_path.get(filepath, 0.0)
    if now - last < debounce_sec:
        return False
    _last_launch_by_path[filepath] = now

    try:
        if cmd and len(cmd) > 0:
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        else:
            subprocess.Popen(["xdg-open", str(filepath)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
    except Exception as e:
        print(f"Error launching {filepath}: {e}")
        return False

def open_with(filepath: Path, parent=None) -> bool:
    """
    Displays the system "Open With..." / "Choose Application" dialog.
    First tries the official FreeDesktop / KDE Desktop Portal, then falls
    back to an application chooser dialog.
    """
    filepath = Path(filepath).resolve()

    # 1. FreeDesktop Portal OpenFile with ask=True (native KDE / system dialog)
    try:
        import dbus
        fd = os.open(str(filepath), os.O_RDONLY)
        bus = dbus.SessionBus()
        portal = bus.get_object("org.freedesktop.portal.Desktop", "/org/freedesktop/portal/desktop")
        open_uri = dbus.Interface(portal, "org.freedesktop.portal.OpenURI")
        opts = {"ask": dbus.Boolean(True)}
        open_uri.OpenFile("", dbus.types.UnixFd(fd), opts)
        os.close(fd)
        return True
    except Exception:
        pass

    # 2. Fallback: Show built-in Qt application chooser dialog
    return show_fallback_app_chooser(filepath, parent=parent)

def show_fallback_app_chooser(filepath: Path, parent=None) -> bool:
    """Fallback Qt dialog to choose which application to open the file with."""
    from PyQt5.QtWidgets import (
        QDialog, QVBoxLayout, QHBoxLayout, QLineEdit, QListWidget,
        QListWidgetItem, QPushButton, QLabel, QApplication
    )
    from PyQt5.QtGui import QIcon
    from PyQt5.QtCore import Qt, QSize

    dialog = QDialog(parent)
    dialog.setWindowTitle(tr("choose_app_title"))
    dialog.resize(420, 500)

    layout = QVBoxLayout(dialog)

    # Search bar
    search_input = QLineEdit(dialog)
    search_input.setPlaceholderText(tr("search_apps"))
    layout.addWidget(search_input)

    # Applications list
    list_widget = QListWidget(dialog)
    list_widget.setIconSize(QSize(24, 24))
    layout.addWidget(list_widget)

    # Scan installed applications
    app_dirs = [
        Path.home() / ".local/share/applications",
        Path("/run/current-system/sw/share/applications"),
        Path("/nix/var/nix/profiles/default/share/applications"),
        Path("/usr/share/applications")
    ]
    apps = {}
    for d in app_dirs:
        if d.exists():
            for f in d.glob("*.desktop"):
                try:
                    name, icon, exec_cmd = "", "", ""
                    with open(f, "r", encoding="utf-8", errors="ignore") as fp:
                        for line in fp:
                            if line.startswith("Name=") and not name:
                                name = line.split("=", 1)[1].strip()
                            elif line.startswith("Icon=") and not icon:
                                icon = line.split("=", 1)[1].strip()
                            elif line.startswith("Exec=") and not exec_cmd:
                                exec_cmd = line.split("=", 1)[1].strip()
                            elif line.startswith("NoDisplay=true"):
                                name = ""
                                break
                    if name and exec_cmd and name not in apps:
                        apps[name] = {"icon": icon, "exec": exec_cmd}
                except Exception:
                    pass

    # Populate list
    sorted_names = sorted(apps.keys(), key=lambda s: s.lower())
    for name in sorted_names:
        item = QListWidgetItem(name)
        icon_name = apps[name]["icon"]
        icon = QIcon.fromTheme(icon_name) if icon_name else QIcon()
        if not icon.isNull():
            item.setIcon(icon)
        item.setData(Qt.UserRole, apps[name]["exec"])
        list_widget.addItem(item)

    # Filter handler
    def _on_search_changed(text):
        for i in range(list_widget.count()):
            it = list_widget.item(i)
            it.setHidden(text.lower() not in it.text().lower())

    search_input.textChanged.connect(_on_search_changed)

    # Buttons
    btn_layout = QHBoxLayout()
    btn_cancel = QPushButton(tr("cancel"), dialog)
    btn_cancel.clicked.connect(dialog.reject)
    btn_ok = QPushButton(tr("open"), dialog)
    btn_ok.clicked.connect(dialog.accept)
    btn_layout.addStretch()
    btn_layout.addWidget(btn_cancel)
    btn_layout.addWidget(btn_ok)
    layout.addLayout(btn_layout)

    list_widget.itemDoubleClicked.connect(lambda: dialog.accept())

    if dialog.exec_() == QDialog.Accepted:
        selected = list_widget.currentItem()
        if selected:
            raw_exec = selected.data(Qt.UserRole)
            # Clean % codes and launch
            cmd = []
            for token in raw_exec.split():
                if not token.startswith("%"):
                    cmd.append(token)
            cmd.append(str(filepath))
            try:
                subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return True
            except Exception as e:
                print(f"Error launching chosen app: {e}")

    return False

def show_properties(filepath: Path) -> bool:
    """
    Opens the native file properties dialog (KDE / Dolphin / KIO / FreeDesktop).
    Fixes the issue where previously it just opened Dolphin folder.
    """
    filepath = Path(filepath).resolve()
    uri = filepath.as_uri()

    # 1. Try kioclient / kioclient5 (native KDE file properties dialog)
    for tool in ["kioclient", "kioclient5"]:
        if shutil.which(tool):
            try:
                subprocess.Popen([tool, "openProperties", uri], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return True
            except Exception:
                pass

    # 2. Try DBus org.freedesktop.FileManager1 ShowItemProperties
    if shutil.which("qdbus"):
        try:
            res = subprocess.run([
                "qdbus", "org.freedesktop.FileManager1", "/org/freedesktop/FileManager1",
                "org.freedesktop.FileManager1.ShowItemProperties", f"['{uri}']", ""
            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=1)
            if res.returncode == 0:
                return True
        except Exception:
            pass

    # 3. Fallback: open parent folder with xdg-open
    try:
        subprocess.Popen(["xdg-open", str(filepath.parent)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except Exception:
        return False

def open_containing_folder(filepath: Path) -> bool:
    """Opens the containing folder in the file manager and selects the item."""
    filepath = Path(filepath).resolve()
    uri = filepath.as_uri()

    if shutil.which("qdbus"):
        try:
            res = subprocess.run([
                "qdbus", "org.freedesktop.FileManager1", "/org/freedesktop/FileManager1",
                "org.freedesktop.FileManager1.ShowItems", f"['{uri}']", ""
            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=1)
            if res.returncode == 0:
                return True
        except Exception:
            pass

    try:
        subprocess.Popen(["xdg-open", str(filepath.parent)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except Exception:
        return False

def move_to_trash(filepath: Path) -> bool:
    """Moves the file to the system trash."""
    filepath = Path(filepath).resolve()
    try:
        from PyQt5.QtCore import QFile
        if QFile.moveToTrash(str(filepath)):
            return True
    except Exception:
        pass

    if shutil.which("gio"):
        try:
            res = subprocess.run(["gio", "trash", str(filepath)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if res.returncode == 0:
                return True
        except Exception:
            pass

    if shutil.which("kioclient"):
        try:
            res = subprocess.run(["kioclient", "move", filepath.as_uri(), "trash:/"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if res.returncode == 0:
                return True
        except Exception:
            pass

    return False

def delete_permanently(filepath: Path) -> bool:
    """Permanently deletes the file or directory."""
    filepath = Path(filepath).resolve()
    try:
        if filepath.is_dir():
            shutil.rmtree(filepath)
        else:
            filepath.unlink()
        return True
    except Exception as e:
        print(f"Error deleting {filepath}: {e}")
        return False

def copy_to_clipboard(filepath: Path, is_cut: bool = False):
    """Copies file URI to clipboard, optionally setting KDE cut flag."""
    try:
        from PyQt5.QtWidgets import QApplication
        from PyQt5.QtCore import QMimeData, QUrl, QByteArray
        mime_data = QMimeData()
        mime_data.setUrls([QUrl.fromLocalFile(str(filepath.resolve()))])
        if is_cut:
            mime_data.setData("application/x-kde-cutselection", QByteArray(b"1"))
        QApplication.clipboard().setMimeData(mime_data)
    except Exception as e:
        print(f"Error copying to clipboard: {e}")
