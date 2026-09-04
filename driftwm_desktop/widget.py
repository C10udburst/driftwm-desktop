import sys
import time
from pathlib import Path
from typing import Callable, Optional, List

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QMenu, QAction,
    QInputDialog, QMessageBox, QGraphicsDropShadowEffect, QApplication
)
from PyQt5.QtGui import QIcon, QFontMetrics, QColor, QPalette, QCursor, QPainter, QDrag
from PyQt5.QtCore import Qt, QSize, QPoint, QMimeData, QUrl

from .config import ICON_SIZE, APP_ID
from .parser import parse_item_info, DesktopItemInfo, DesktopAction
from .actions import (
    launch_item, open_with, show_properties,
    move_to_trash, delete_permanently, copy_to_clipboard
)
from .driftwm import move_window, move_window_async, get_desktop_windows_map
from .i18n import tr

class DesktopItemWidget(QWidget):
    """
    Modular desktop icon widget with:
    - Transparent background
    - Automatic text-width sizing without ellipses
    - OS theme palette awareness
    - Solid OS-themed context menu with Desktop Actions (e.g. Immich/SiYuan actions)
    - Launch debounce
    - Interactive 1:1 real-time Drag & Drop positioning in DriftWM canvas via IPC
    - Wayland motion compensation eliminating random drag jumping
    - Canvas zoom awareness
    - Native QDrag file dropping support into external programs (via Ctrl/Shift + Drag)
    - 'Open With...' choose application dialog
    - Dolphin-like file operations
    - Dynamic refresh on file change
    """

    def __init__(
        self,
        filepath: Path,
        on_deleted: Optional[Callable[[str], None]] = None,
        on_renamed: Optional[Callable[[str, str], None]] = None,
        on_moved: Optional[Callable[[str, List[int]], None]] = None,
        parent=None
    ):
        super().__init__(parent)
        self.filepath = Path(filepath).resolve()
        self.on_deleted = on_deleted
        self.on_renamed = on_renamed
        self.on_moved = on_moved

        self.item_info: DesktopItemInfo = parse_item_info(self.filepath)
        self.driftwm_id: Optional[int] = None
        self.canvas_pos: Optional[List[int]] = None

        self._last_launch_time = 0.0

        # Drag & motion tracking
        self._press_pos: Optional[QPoint] = None
        self._last_drag_pos: Optional[QPoint] = None
        self._is_dragging = False
        self._drag_dist = 0
        self._current_canvas_pos: Optional[List[int]] = None
        self._last_drag_move_time = 0.0
        self._is_hovered = False

        self.init_ui()

    def init_ui(self):
        # Window identification: Title must be the filename
        self.setWindowTitle(self.filepath.name)

        # Frameless window with transparent background
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Window)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setObjectName("DesktopItemWidget")

        # Layout
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(6, 6, 6, 6)
        self.layout.setSpacing(4)

        # Icon display label
        self.icon_label = QLabel(self)
        self.icon_label.setAlignment(Qt.AlignCenter)
        self.icon_label.setFixedSize(ICON_SIZE, ICON_SIZE)
        # Transparent for mouse events so parent widget receives all drag and click gestures
        self.icon_label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._update_icon()
        self.layout.addWidget(self.icon_label, alignment=Qt.AlignCenter)

        # Text label under icon - displays full text without ellipses
        self.label = QLabel(self.item_info.display_name, self)
        self.label.setAlignment(Qt.AlignCenter)
        self.label.setToolTip(self.item_info.display_name)
        # Transparent for mouse events so clicking label also allows dragging
        self.label.setAttribute(Qt.WA_TransparentForMouseEvents, True)

        # Use OS theme text color and subtle shadow for contrast on any wallpaper
        pal = self.palette()
        text_color = pal.color(QPalette.WindowText).name()
        self.label.setStyleSheet(f"color: {text_color}; background: transparent; font-size: 11px;")

        # Shadow effect for legibility against both light and dark backgrounds
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(4)
        shadow.setColor(QColor(0, 0, 0, 180))
        shadow.setOffset(1, 1)
        self.label.setGraphicsEffect(shadow)

        self.layout.addWidget(self.label, alignment=Qt.AlignCenter)

        # Resize the window to fit the full text without ellipses
        self.adjust_size_to_text()

    def _update_icon(self):
        """Loads and sets the icon pixmap based on item info and MIME-type candidates."""
        icon = QIcon()

        # 1. Try explicit icon_name (e.g. from .desktop or primary MIME icon)
        if self.item_info.icon_name:
            if Path(self.item_info.icon_name).is_file():
                icon = QIcon(self.item_info.icon_name)
            else:
                icon = QIcon.fromTheme(self.item_info.icon_name)

        # 2. Iterate candidate MIME icons if the primary was null
        if icon.isNull() and self.item_info.icon_candidates:
            for candidate in self.item_info.icon_candidates:
                if candidate:
                    candidate_icon = QIcon.fromTheme(candidate)
                    if not candidate_icon.isNull():
                        icon = candidate_icon
                        break

        # 3. Final category fallbacks
        if icon.isNull():
            fallback = "folder" if self.item_info.is_dir else (
                "application-x-executable" if self.item_info.is_desktop else "text-x-generic"
            )
            icon = QIcon.fromTheme(fallback)

        pixmap = icon.pixmap(QSize(int(ICON_SIZE * 0.85), int(ICON_SIZE * 0.85)))
        self.icon_label.setPixmap(pixmap)

    def refresh(self):
        """Reloads item metadata, updates icon/label, and recalculates size."""
        self.item_info = parse_item_info(self.filepath)
        self.setWindowTitle(self.filepath.name)
        self.label.setText(self.item_info.display_name)
        self.label.setToolTip(self.item_info.display_name)
        self._update_icon()
        self.adjust_size_to_text()

    def adjust_size_to_text(self):
        """Calculates required width and height to fit full text without ellipses."""
        fm = QFontMetrics(self.label.font())
        text_rect = fm.boundingRect(self.item_info.display_name)
        text_width = text_rect.width()
        text_height = max(fm.height(), text_rect.height())

        window_width = max(ICON_SIZE + 20, text_width + 20)
        window_height = ICON_SIZE + 4 + text_height + 16

        self.setFixedSize(window_width, window_height)

    def _refresh_driftwm_info(self):
        """
        Always queries current live window ID and position from DriftWM,
        preventing stale position jumps after viewport/camera pans.
        """
        mapping = get_desktop_windows_map(APP_ID)
        win_info = mapping.get(self.filepath.name)
        if win_info:
            self.driftwm_id = win_info.get("id")
            pos = win_info.get("position")
            if pos and len(pos) >= 2:
                self.canvas_pos = [int(round(pos[0])), int(round(pos[1]))]

    def paintEvent(self, event):
        """Draws subtle OS highlight on hover."""
        if self._is_hovered:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.Antialiasing)
            pal = self.palette()
            hl = pal.color(QPalette.Highlight)
            highlight_color = QColor(hl.red(), hl.green(), hl.blue(), 55)
            painter.setBrush(highlight_color)
            painter.setPen(Qt.NoPen)
            painter.drawRoundedRect(self.rect().adjusted(2, 2, -2, -2), 6, 6)
        super().paintEvent(event)

    def enterEvent(self, event):
        self._is_hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._is_hovered = False
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        """Initiates drag tracking on left click with live coordinates and zoom."""
        if event.button() == Qt.LeftButton:
            self._press_pos = event.pos()
            self._last_drag_pos = event.pos()
            self._is_dragging = False
            self._drag_dist = 0

            if not self.canvas_pos:
                self._refresh_driftwm_info()

            if self.canvas_pos:
                self._current_canvas_pos = list(self.canvas_pos)
            else:
                self._current_canvas_pos = [0, 0]
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        """
        Tracks physical mouse motion during drag with pure frame-to-frame deltas.
        Smoothly repositions the window via DriftWM IPC at 1:1 mouse speed.
        If Ctrl or Shift is held, launches a native QDrag for dropping into other programs.
        """
        if (event.buttons() & Qt.LeftButton) and self._last_drag_pos is not None:
            # If Ctrl or Shift is pressed while dragging, activate native QDrag to drop into other programs
            if event.modifiers() & (Qt.ControlModifier | Qt.ShiftModifier):
                self.start_file_drag()
                return

            # Pure incremental delta from the last event position (no positive feedback / no acceleration)
            step_dx = event.pos().x() - self._last_drag_pos.x()
            step_dy = event.pos().y() - self._last_drag_pos.y()
            self._last_drag_pos = event.pos()

            if not self._is_dragging:
                self._drag_dist += abs(event.pos().x() - self._press_pos.x()) + abs(event.pos().y() - self._press_pos.y())
                if self._drag_dist >= 6:
                    self._is_dragging = True
                    QApplication.setOverrideCursor(Qt.ClosedHandCursor)

            if self._is_dragging:
                if self._current_canvas_pos is None:
                    if self.canvas_pos:
                        self._current_canvas_pos = list(self.canvas_pos)
                    else:
                        self._current_canvas_pos = [0, 0]

                # event.pos() in DriftWM canvas surfaces is already in canvas units;
                # 1 surface pixel delta equals 1 canvas coordinate unit.
                canvas_dx = step_dx
                canvas_dy = -step_dy  # DriftWM canvas Y is UP

                if canvas_dx != 0 or canvas_dy != 0:
                    self._current_canvas_pos[0] += canvas_dx
                    self._current_canvas_pos[1] += canvas_dy

                    now = time.time()
                    if now - self._last_drag_move_time >= 0.015:
                        self._last_drag_move_time = now
                        if self.driftwm_id is not None:
                            move_window_async(
                                self.driftwm_id,
                                self._current_canvas_pos[0],
                                self._current_canvas_pos[1]
                            )
                event.accept()
                return

        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        """Finalizes position via IPC and updates persistent state."""
        if event.button() == Qt.LeftButton:
            if self._is_dragging:
                QApplication.restoreOverrideCursor()
                self._is_dragging = False
                if self._current_canvas_pos and self.driftwm_id is not None:
                    move_window(self.driftwm_id, self._current_canvas_pos[0], self._current_canvas_pos[1])
                    self.canvas_pos = list(self._current_canvas_pos)
                    if self.on_moved:
                        self.on_moved(self.filepath.name, self.canvas_pos)
                self._press_pos = None
                self._last_drag_pos = None
                event.accept()
                return
            else:
                self._press_pos = None
                self._last_drag_pos = None

        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        """Double-clicking launches the item (with debounce)."""
        if event.button() == Qt.LeftButton:
            self.launch()
            event.accept()

    def contextMenuEvent(self, event):
        """Right-clicking displays the context menu."""
        self.show_context_menu(event.globalPos())
        event.accept()

    def start_file_drag(self):
        """
        Initiates a standard FreeDesktop/Qt QDrag object with text/uri-list.
        Allows dragging this file and dropping it into external applications
        (Dolphin, VS Code, web browsers, terminal, Discord, etc.).
        """
        if QApplication.overrideCursor():
            QApplication.restoreOverrideCursor()
        self._is_dragging = False

        drag = QDrag(self)
        mime_data = QMimeData()
        mime_data.setUrls([QUrl.fromLocalFile(str(self.filepath.resolve()))])
        drag.setMimeData(mime_data)

        pixmap = self.grab()
        drag.setPixmap(pixmap)
        hotspot = self._press_pos if self._press_pos else QPoint(pixmap.width() // 2, pixmap.height() // 2)
        drag.setHotSpot(hotspot)

        drag.exec_(Qt.CopyAction | Qt.MoveAction)

    def launch(self):
        """Executes or opens the desktop item with a debounce to prevent double opens."""
        now = time.time()
        if now - self._last_launch_time < 0.8:
            return
        self._last_launch_time = now
        launch_item(self.filepath, self.item_info.cmd, debounce_sec=0.8)

    def action_rename(self):
        """Prompts user to rename the file, renames on disk and updates widget."""
        new_name, ok = QInputDialog.getText(
            self,
            tr("rename_title"),
            tr("new_name_prompt"),
            text=self.filepath.name
        )
        if ok and new_name and new_name != self.filepath.name:
            old_name = self.filepath.name
            new_path = self.filepath.parent / new_name
            try:
                self.filepath.rename(new_path)
                self.filepath = new_path
                self.item_info = parse_item_info(self.filepath)
                self.setWindowTitle(self.filepath.name)
                self.label.setText(self.item_info.display_name)
                self.adjust_size_to_text()
                if self.on_renamed:
                    self.on_renamed(old_name, new_name)
            except Exception as e:
                QMessageBox.critical(self, tr("error"), tr("rename_error", error=e))

    def action_trash(self):
        """Moves item to trash and closes widget."""
        filename = self.filepath.name
        if move_to_trash(self.filepath):
            if self.on_deleted:
                self.on_deleted(filename)
            self.close()
        else:
            QMessageBox.warning(self, tr("warning"), tr("trash_error", filename=filename))

    def action_delete_permanently(self):
        """Confirms and permanently deletes item."""
        reply = QMessageBox.question(
            self,
            tr("delete_confirm_title"),
            tr("delete_confirm_msg", filename=self.filepath.name),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            filename = self.filepath.name
            if delete_permanently(self.filepath):
                if self.on_deleted:
                    self.on_deleted(filename)
                self.close()
            else:
                QMessageBox.critical(self, tr("error"), tr("delete_error", filename=filename))

    def show_context_menu(self, global_pos):
        """
        Displays an opaque, OS-themed context menu with Dolphin-like options,
        all native Desktop Actions (e.g. Immich/SiYuan shortcuts), and 'Open With...'.
        """
        menu = QMenu(self)
        menu.setAttribute(Qt.WA_TranslucentBackground, False)
        menu.setAutoFillBackground(True)

        # Enforce solid background and OS theme palette styling
        menu.setStyleSheet("""
            QMenu {
                background-color: palette(window);
                color: palette(window-text);
                border: 1px solid palette(mid);
                padding: 4px;
            }
            QMenu::item {
                padding: 5px 24px 5px 10px;
                border-radius: 3px;
            }
            QMenu::item:selected {
                background-color: palette(highlight);
                color: palette(highlighted-text);
            }
            QMenu::separator {
                height: 1px;
                background-color: palette(mid);
                margin: 4px 6px;
            }
        """)

        # Main Launch / Open action
        open_label = tr("launch") if self.item_info.is_desktop else tr("open")
        open_icon = QIcon.fromTheme("system-run" if self.item_info.is_desktop else "document-open")
        act_open = menu.addAction(open_icon, open_label, self.launch)
        font = act_open.font()
        font.setBold(True)
        act_open.setFont(font)

        # Native Desktop Actions declared in the .desktop file
        if self.item_info.actions:
            for act in self.item_info.actions:
                act_icon = QIcon.fromTheme(act.icon_name) if act.icon_name else QIcon()
                if act_icon.isNull():
                    act_icon = open_icon
                cmd_to_run = list(act.cmd)
                menu.addAction(act_icon, act.name, lambda c=cmd_to_run: launch_item(self.filepath, c))

        menu.addSeparator()

        # Open With... (Choose application dialog)
        open_with_icon = QIcon.fromTheme("document-open")
        menu.addAction(open_with_icon, tr("open_with"), lambda: open_with(self.filepath, parent=self))

        menu.addSeparator()

        # Cut
        cut_icon = QIcon.fromTheme("edit-cut")
        menu.addAction(cut_icon, tr("cut"), lambda: copy_to_clipboard(self.filepath, is_cut=True))

        # Copy
        copy_icon = QIcon.fromTheme("edit-copy")
        menu.addAction(copy_icon, tr("copy"), lambda: copy_to_clipboard(self.filepath, is_cut=False))

        # Rename
        rename_icon = QIcon.fromTheme("edit-rename")
        menu.addAction(rename_icon, tr("rename"), self.action_rename)

        menu.addSeparator()

        # Move to trash
        trash_icon = QIcon.fromTheme("user-trash")
        menu.addAction(trash_icon, tr("trash"), self.action_trash)

        # Delete permanently
        delete_icon = QIcon.fromTheme("edit-delete")
        menu.addAction(delete_icon, tr("delete_permanently"), self.action_delete_permanently)

        menu.addSeparator()

        # File properties
        prop_icon = QIcon.fromTheme("document-properties")
        menu.addAction(prop_icon, tr("properties"), lambda: show_properties(self.filepath))

        menu.addSeparator()

        # Close widget
        close_icon = QIcon.fromTheme("window-close")
        menu.addAction(close_icon, tr("hide_widget"), self.close)

        menu.exec_(global_pos)
