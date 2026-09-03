import sys
import time
import threading
from pathlib import Path
from typing import Callable, Optional, List

from PyQt5.QtWidgets import (
    QWidget, QPushButton, QVBoxLayout, QLabel, QMenu, QAction,
    QInputDialog, QMessageBox, QGraphicsDropShadowEffect, QApplication
)
from PyQt5.QtGui import QIcon, QFontMetrics, QColor, QPalette, QCursor
from PyQt5.QtCore import Qt, QSize, QPoint, QEvent

from .config import ICON_SIZE, APP_ID
from .parser import parse_item_info, DesktopItemInfo
from .actions import (
    launch_item, open_with, show_properties,
    move_to_trash, delete_permanently, copy_to_clipboard
)
from .driftwm import move_window, get_desktop_windows_map
from .i18n import tr

class DesktopItemWidget(QWidget):
    """
    Modular desktop icon widget with:
    - Transparent background
    - Automatic text-width sizing without ellipses
    - OS theme palette awareness
    - Solid OS-themed context menu
    - Launch debounce
    - Interactive real-time Drag & Drop positioning in DriftWM canvas
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

        # Drag and drop tracking
        self._press_global_pos: Optional[QPoint] = None
        self._drag_start_canvas_pos: Optional[List[int]] = None
        self._is_dragging = False
        self._current_canvas_pos: Optional[List[int]] = None
        self._last_drag_move_time = 0.0

        self.init_ui()

    def init_ui(self):
        # Window identification: Title must be the filename
        self.setWindowTitle(self.filepath.name)

        # Frameless window with transparent background
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Window)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        # Scope styling strictly to this widget so it does NOT cascade to child QMenu or popups
        self.setObjectName("DesktopItemWidget")
        self.setStyleSheet("QWidget#DesktopItemWidget { background: transparent; }")

        # Layout
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(4, 4, 4, 4)
        self.layout.setSpacing(3)

        # Button with icon
        self.btn = QPushButton(self)
        self.btn.setFixedSize(ICON_SIZE, ICON_SIZE)
        self.btn.setToolTip(str(self.filepath))

        self._update_icon()

        # Theme-aware hover effect using system highlight color instead of hardcoded RGBA
        pal = self.palette()
        hl = pal.color(QPalette.Highlight)
        hl_rgba = f"rgba({hl.red()}, {hl.green()}, {hl.blue()}, 0.25)"
        self.btn.setStyleSheet(f"""
            QPushButton {{
                border: none;
                background: transparent;
                border-radius: 4px;
            }}
            QPushButton:hover {{
                background-color: {hl_rgba};
            }}
        """)
        self.btn.clicked.connect(self._on_button_clicked)
        self.layout.addWidget(self.btn, alignment=Qt.AlignCenter)

        # Text label under icon - displays full text without ellipses
        self.label = QLabel(self.item_info.display_name, self)
        self.label.setAlignment(Qt.AlignCenter)
        self.label.setToolTip(self.item_info.display_name)

        # Use OS theme text color and subtle shadow for contrast on any wallpaper
        text_color = pal.color(QPalette.WindowText).name()
        self.label.setStyleSheet(f"color: {text_color}; background: transparent; font-size: 11px;")

        # Subtle shadow effect for legibility against both light and dark backgrounds
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(4)
        shadow.setColor(QColor(0, 0, 0, 180))
        shadow.setOffset(1, 1)
        self.label.setGraphicsEffect(shadow)

        self.layout.addWidget(self.label, alignment=Qt.AlignCenter)

        # Resize the window to fit the full text without ellipses
        self.adjust_size_to_text()

        # Install event filter to capture drag on icon, label, and widget background
        self.installEventFilter(self)
        self.btn.installEventFilter(self)
        self.label.installEventFilter(self)

        # Context menu policy
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self.show_context_menu)
        self.btn.setContextMenuPolicy(Qt.CustomContextMenu)
        self.btn.customContextMenuRequested.connect(self.show_context_menu)

    def _update_icon(self):
        """Loads and sets the button icon based on item info and MIME-type candidates."""
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

        self.btn.setIcon(icon)
        self.btn.setIconSize(QSize(int(ICON_SIZE * 0.8), int(ICON_SIZE * 0.8)))

    def refresh(self):
        """Reloads item metadata, updates icon/label, and recalculates size."""
        self.item_info = parse_item_info(self.filepath)
        self.setWindowTitle(self.filepath.name)
        self.label.setText(self.item_info.display_name)
        self.label.setToolTip(self.item_info.display_name)
        self.btn.setToolTip(str(self.filepath))
        self._update_icon()
        self.adjust_size_to_text()

    def adjust_size_to_text(self):
        """Calculates required width and height to fit full text without ellipses."""
        fm = QFontMetrics(self.label.font())
        text_rect = fm.boundingRect(self.item_info.display_name)
        text_width = text_rect.width()
        text_height = max(fm.height(), text_rect.height())

        window_width = max(ICON_SIZE + 16, text_width + 16)
        window_height = ICON_SIZE + 4 + text_height + 12

        self.setFixedSize(window_width, window_height)

    def _ensure_driftwm_info(self):
        """Ensures the window has its driftwm ID and canvas position cached."""
        if self.driftwm_id is not None and self.canvas_pos is not None:
            return
        mapping = get_desktop_windows_map(APP_ID)
        win_info = mapping.get(self.filepath.name)
        if win_info:
            self.driftwm_id = win_info.get("id")
            pos = win_info.get("position")
            if pos and len(pos) >= 2:
                self.canvas_pos = [int(round(pos[0])), int(round(pos[1]))]

    def eventFilter(self, watched, event):
        """
        Event filter to intercept and handle drag-and-drop across the icon,
        the label, and the widget background.
        """
        if event.type() == QEvent.MouseButtonPress:
            if event.button() == Qt.LeftButton:
                self._press_global_pos = event.globalPos()
                self._is_dragging = False
                self._ensure_driftwm_info()
                if self.canvas_pos:
                    self._drag_start_canvas_pos = list(self.canvas_pos)
                else:
                    self._drag_start_canvas_pos = None

        elif event.type() == QEvent.MouseMove:
            if (event.buttons() & Qt.LeftButton) and self._press_global_pos is not None:
                delta = event.globalPos() - self._press_global_pos
                dist_sq = delta.x() * delta.x() + delta.y() * delta.y()

                # Start dragging once threshold (5 pixels) is exceeded
                if not self._is_dragging:
                    if dist_sq >= 25:
                        self._is_dragging = True
                        QApplication.setOverrideCursor(Qt.ClosedHandCursor)

                if self._is_dragging:
                    if not self._drag_start_canvas_pos:
                        self._ensure_driftwm_info()
                        if self.canvas_pos:
                            self._drag_start_canvas_pos = list(self.canvas_pos)

                    if self._drag_start_canvas_pos:
                        # In DriftWM canvas: X is right (+x), Y is UP (-dy)
                        delta_x = delta.x()
                        delta_y = -delta.y()
                        new_x = self._drag_start_canvas_pos[0] + delta_x
                        new_y = self._drag_start_canvas_pos[1] + delta_y
                        self._current_canvas_pos = [new_x, new_y]

                        # Throttle IPC calls to 60fps (every 16ms)
                        now = time.time()
                        if now - self._last_drag_move_time >= 0.016:
                            self._last_drag_move_time = now
                            if self.driftwm_id is not None:
                                move_window(self.driftwm_id, new_x, new_y)

                    # Consume event to suppress button clicks while dragging
                    return True

        elif event.type() == QEvent.MouseButtonRelease:
            if event.button() == Qt.LeftButton:
                if self._is_dragging:
                    QApplication.restoreOverrideCursor()
                    if self._current_canvas_pos and self.driftwm_id is not None:
                        move_window(self.driftwm_id, self._current_canvas_pos[0], self._current_canvas_pos[1])
                        self.canvas_pos = list(self._current_canvas_pos)
                        if self.on_moved:
                            self.on_moved(self.filepath.name, self.canvas_pos)

                    self._is_dragging = False
                    self._press_global_pos = None
                    self._drag_start_canvas_pos = None
                    # Suppress the button click since this was a drag
                    return True

                self._is_dragging = False
                self._press_global_pos = None
                self._drag_start_canvas_pos = None

        return super().eventFilter(watched, event)

    def _on_button_clicked(self):
        """Called when icon button is clicked without dragging."""
        if not self._is_dragging:
            self.launch()

    def launch(self):
        """Executes or opens the desktop item with a debounce to prevent double opens."""
        now = time.time()
        if now - self._last_launch_time < 0.8:
            return
        self._last_launch_time = now
        launch_item(self.filepath, self.item_info.cmd, debounce_sec=0.8)

    def mouseDoubleClickEvent(self, event):
        """Double click on the widget launches the item (debounced)."""
        if event.button() == Qt.LeftButton:
            self.launch()
            event.accept()

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

    def show_context_menu(self, pos):
        """
        Displays an opaque, OS-themed context menu with Dolphin-like options,
        including 'Open With...' (app chooser dialog).
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

        # Launch / Open action
        open_label = tr("launch") if self.item_info.is_desktop else tr("open")
        open_icon = QIcon.fromTheme("system-run" if self.item_info.is_desktop else "document-open")
        act_open = menu.addAction(open_icon, open_label, self.launch)
        font = act_open.font()
        font.setBold(True)
        act_open.setFont(font)

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

        sender = self.sender()
        global_pos = sender.mapToGlobal(pos) if sender else self.mapToGlobal(pos)
        menu.exec_(global_pos)
