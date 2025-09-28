import sys
import json
import threading
import time
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QRect, QSize, QPoint, QObject, Signal
from PySide6.QtGui import QAction, QFont, QGuiApplication, QPainter, QColor, QIcon, QPixmap, QPolygon
from PySide6.QtWidgets import (
    QApplication,
    QWidget,
    QSystemTrayIcon,
    QMenu,
    QPushButton,
    QHBoxLayout,
    QVBoxLayout,
    QSizePolicy,
    QLabel,
    QFileDialog,
    QMessageBox,
    QToolButton,
    QInputDialog,
    QDialog,
    QProgressBar,
)

try:
    import keyboard  # global keyboard hook
except Exception as exc:  # pragma: no cover
    keyboard = None
    _keyboard_import_error = exc


class OverlayWidget(QWidget):
    def __init__(self):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setWindowFlag(Qt.NoDropShadowWindowHint, True)
        self.setWindowFlag(Qt.WindowDoesNotAcceptFocus, True)

        self.text_to_show = ""
        self.font = QFont("Segoe UI", 36, QFont.Black)
        self.text_color = QColor(255, 255, 255, 220)
        self.margin = 14
        self.corner = "bottom_right"  # bottom_left, bottom_right, top_left, top_right
        self.resize_handle_size = 18
        self._dragging = False
        self._drag_offset = QPoint(0, 0)
        self._resizing = False
        self._resize_origin_geom = None
        self._resize_origin_pos = None

        self.resize(720, 140)
        self.reposition_to_corner()

        # Timer to auto-clear text after inactivity
        self.clear_delay_ms = 1200
        self.clear_timer = QTimer(self)
        self.clear_timer.setSingleShot(True)
        self.clear_timer.timeout.connect(self.clear_text)

    def set_corner(self, corner: str):
        self.corner = corner
        self.reposition_to_corner()

    def set_text(self, text: str):
        self.text_to_show = text
        self.clear_timer.start(self.clear_delay_ms)
        self.update()

    def clear_text(self):
        self.text_to_show = ""
        self.update()

    def reposition_to_corner(self):
        screen = QGuiApplication.primaryScreen()
        if not screen:
            return
        geometry: QRect = screen.availableGeometry()
        size: QSize = self.size()
        x = geometry.left() + self.margin
        y = geometry.top() + self.margin
        if self.corner.endswith("right"):
            x = geometry.right() - size.width() - self.margin
        if self.corner.startswith("bottom"):
            y = geometry.bottom() - size.height() - self.margin
        self.move(QPoint(x, y))

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        # Background long bar (semi-transparent gray-black)
        bar_height = max(64, self.height() - 2 * self.margin)
        bar_rect = QRect(self.margin, self.height() // 2 - bar_height // 2, self.width() - 2 * self.margin, bar_height)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(20, 20, 24, 180))
        painter.drawRoundedRect(bar_rect, 14, 14)

        # Draw keys text
        painter.setPen(self.text_color)
        painter.setFont(self.font)
        text_rect = bar_rect.adjusted(16, 0, -16, 0)
        painter.drawText(text_rect, Qt.AlignLeft | Qt.AlignVCenter | Qt.TextSingleLine, self.text_to_show)

        # Optional resize handle indicator (bottom-right triangle)
        handle = self.resize_handle_size
        painter.setBrush(QColor(255, 255, 255, 60))
        points = [
            QPoint(self.width() - handle, self.height()),
            QPoint(self.width(), self.height()),
            QPoint(self.width(), self.height() - handle),
        ]
        painter.drawPolygon(QPolygon(points))

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            if self._in_resize_handle(event.position().toPoint()):
                self._resizing = True
                self._resize_origin_geom = self.geometry()
                self._resize_origin_pos = event.globalPosition().toPoint()
            else:
                self._dragging = True
                self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._resizing and self._resize_origin_geom and self._resize_origin_pos:
            delta = event.globalPosition().toPoint() - self._resize_origin_pos
            new_w = max(360, self._resize_origin_geom.width() + delta.x())
            new_h = max(100, self._resize_origin_geom.height() + delta.y())
            self.resize(new_w, new_h)
            event.accept()
            return
        if self._dragging:
            new_pos = event.globalPosition().toPoint() - self._drag_offset
            self.move(new_pos)
            event.accept()
            return
        # update cursor shape over resize area
        if self._in_resize_handle(event.position().toPoint()):
            self.setCursor(Qt.SizeFDiagCursor)
        else:
            self.setCursor(Qt.ArrowCursor)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._dragging = False
            self._resizing = False
            self._resize_origin_geom = None
            self._resize_origin_pos = None
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def _in_resize_handle(self, pos: QPoint) -> bool:
        return pos.x() >= self.width() - self.resize_handle_size and pos.y() >= self.height() - self.resize_handle_size


class KeyStateBridge(QObject):
    keys_text_changed = Signal(str)
    recording_state_changed = Signal(bool)
    playback_state_changed = Signal(bool)


class KeyOverlayController:
    def __init__(self, bridge: KeyStateBridge):
        if keyboard is None:
            raise RuntimeError(f"keyboard module not available: {_keyboard_import_error}")
        self.bridge = bridge
        self.current_pressed = set()
        self.current_pressed_lock = threading.Lock()
        self._hook = None
        self._record_hook = None
        self._is_recording = False
        self._record_events = []
        self._record_start_time = 0.0
        self._is_playing = False
        self._monitor_enabled = False
        self._playback_stop = threading.Event()

    def start(self):
        # No-op: monitoring is controlled from UI
        return

    def shutdown(self):
        if self._hook is not None:
            keyboard.unhook(self._hook)
            self._hook = None
        if self._record_hook is not None:
            keyboard.unhook(self._record_hook)
            self._record_hook = None
        self._monitor_enabled = False

    def _normalize_key(self, name: str) -> str:
        if len(name) == 1:
            return name.upper()
        return name.replace(" space", "space").replace(" ", "_").upper()

    def _on_key_event(self, event):
        name = event.name or ""
        if not name:
            return
        norm = self._normalize_key(name)
        with self.current_pressed_lock:
            if event.event_type == "down":
                self.current_pressed.add(norm)
            elif event.event_type == "up":
                self.current_pressed.discard(norm)
            display = "+".join(sorted(self.current_pressed))
        self.bridge.keys_text_changed.emit(display)

        if self._is_recording:
            now = time.monotonic()
            self._record_events.append({
                "t": now - self._record_start_time,
                "type": event.event_type,
                "name": event.name,
                "scan_code": getattr(event, "scan_code", None),
            })

    def set_monitor_enabled(self, enabled: bool):
        if enabled == self._monitor_enabled:
            return
        self._monitor_enabled = enabled
        if enabled:
            if self._hook is None:
                self._hook = keyboard.hook(self._on_key_event, suppress=False)
        else:
            if self._hook is not None:
                keyboard.unhook(self._hook)
                self._hook = None
            with self.current_pressed_lock:
                self.current_pressed.clear()
            self.bridge.keys_text_changed.emit("")

    # Recording controls
    def start_recording(self):
        if self._is_recording:
            return
        # Ensure monitoring is on so we receive events
        self.set_monitor_enabled(True)
        self._record_events = []
        self._record_start_time = time.monotonic()
        self._is_recording = True
        self.bridge.recording_state_changed.emit(True)

    def stop_recording(self, save_path: Path | None = None) -> Path | None:
        if not self._is_recording:
            return None
        self._is_recording = False
        self.bridge.recording_state_changed.emit(False)
        data = {
            "version": 1,
            "created_at": time.time(),
            "events": self._record_events,
        }
        save_dir = (save_path or default_record_path()).parent
        save_dir.mkdir(parents=True, exist_ok=True)
        target = save_path or default_record_path()
        target.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return target

    def toggle_recording(self):
        if self._is_recording:
            self.stop_recording()
        else:
            self.start_recording()

    # Playback controls
    def play_file(self, path: Path, times: int | None = 1, loop: bool = False, show_countdown: bool = True):
        if self._is_playing:
            return
        if not path.exists():
            return
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            events = raw.get("events", [])
        except Exception:
            return

        def _runner():
            self._is_playing = True
            self.bridge.playback_state_changed.emit(True)
            try:
                # Pre-compute unique keys for emergency release on stop
                unique_keys = set()
                for e in events:
                    name = e.get("name")
                    if name:
                        unique_keys.add(name)

                cycle = 0
                self._playback_stop.clear()
                while not self._playback_stop.is_set():
                    cycle += 1
                    last_t = 0.0
                    for e in events:
                        if self._playback_stop.is_set():
                            break
                        t = float(e.get("t", 0.0))
                        delay = max(0.0, t - last_t)
                        if delay:
                            # Sleep in small chunks so stop reacts quickly
                            end_time = time.monotonic() + delay
                            while not self._playback_stop.is_set() and time.monotonic() < end_time:
                                time.sleep(min(0.01, end_time - time.monotonic()))
                            if self._playback_stop.is_set():
                                break
                        name = e.get("name")
                        etype = e.get("type")
                        try:
                            if etype == "down":
                                keyboard.press(name)
                            elif etype == "up":
                                keyboard.release(name)
                        except Exception:
                            pass
                        last_t = t

                    if not loop:
                        # If times is None, treat as once when loop=False
                        if times is None:
                            break
                        if cycle >= max(1, int(times)):
                            break
                # On stop, attempt to release all keys
                try:
                    for k in unique_keys:
                        keyboard.release(k)
                except Exception:
                    pass
            finally:
                self._is_playing = False
                self.bridge.playback_state_changed.emit(False)

        threading.Thread(target=_runner, daemon=True).start()

    def play_last(self):
        self.play_file(default_record_path(), times=1, loop=False)

    def play_last_n(self, times: int):
        self.play_file(default_record_path(), times=max(1, int(times)), loop=False)

    def loop_last(self):
        self.play_file(default_record_path(), times=None, loop=True)

    def stop_playback(self):
        if self._is_playing:
            self._playback_stop.set()


class SystemTray:
    def __init__(self, app: QApplication, overlay: OverlayWidget, controller: KeyOverlayController, bridge: KeyStateBridge):
        self.app = app
        self.overlay = overlay
        self.controller = controller
        self.bridge = bridge
        self.tray = QSystemTrayIcon()
        self.tray.setIcon(generate_tray_icon())
        self.tray.setVisible(True)
        menu = QMenu()

        corner_menu = QMenu("显示位置", menu)
        for key, label in (
            ("bottom_left", "左下角"),
            ("bottom_right", "右下角"),
            ("top_left", "左上角"),
            ("top_right", "右上角"),
        ):
            action = QAction(label, corner_menu)
            action.triggered.connect(lambda _=False, c=key: self.overlay.set_corner(c))
            corner_menu.addAction(action)

        record_action = QAction("开始录制", menu)
        record_action.triggered.connect(self.controller.start_recording)

        stop_record_action = QAction("停止录制", menu)
        stop_record_action.triggered.connect(self._stop_record_and_notify)

        play_action = QAction("播放录制", menu)
        play_action.triggered.connect(self.controller.play_last)

        play5_action = QAction("播放最近×5", menu)
        play5_action.triggered.connect(lambda: self.controller.play_last_n(5))

        loop_action = QAction("循环播放最近", menu)
        loop_action.triggered.connect(self.controller.loop_last)

        stop_playback_action = QAction("停止播放", menu)
        stop_playback_action.triggered.connect(self.controller.stop_playback)

        toggle_action = QAction("显示/隐藏显示面板", menu)
        toggle_action.triggered.connect(self.toggle_overlay)

        quit_action = QAction("退出", menu)
        quit_action.triggered.connect(self.quit)

        menu.addMenu(corner_menu)
        menu.addSeparator()
        menu.addAction(record_action)
        menu.addAction(stop_record_action)
        menu.addAction(play_action)
        menu.addAction(play5_action)
        menu.addAction(loop_action)
        menu.addAction(stop_playback_action)
        menu.addSeparator()
        menu.addAction(toggle_action)
        menu.addSeparator()
        menu.addAction(quit_action)

        self.tray.setContextMenu(menu)

        # Reflect states in tooltips
        def on_rec(rec: bool):
            self.tray.setToolTip("Recording..." if rec else "Key Overlay")
        self.bridge.recording_state_changed.connect(on_rec)

    def toggle_overlay(self):
        self.overlay.setVisible(not self.overlay.isVisible())
        if self.overlay.isVisible():
            self.overlay.reposition_to_corner()

    def quit(self):
        self.tray.setVisible(False)
        try:
            self.controller.shutdown()
        except Exception:
            pass
        self.app.quit()

    def _stop_record_and_notify(self):
        target = self.controller.stop_recording()
        if target is not None:
            self.tray.showMessage("Key Overlay", f"Saved recording to\n{target}")


def generate_tray_icon() -> QIcon:
    pix = QPixmap(64, 64)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(QColor(255, 255, 255, 220))
    p.setBrush(QColor(30, 144, 255, 200))
    p.drawRoundedRect(4, 4, 56, 56, 10, 10)
    p.setPen(QColor(255, 255, 255))
    font = QFont("Segoe UI", 26, QFont.Black)
    p.setFont(font)
    p.drawText(pix.rect(), Qt.AlignCenter, "K")
    p.end()
    return QIcon(pix)


def default_record_path() -> Path:
    base = Path.home() / ".key_overlay"
    return base / "last_record.json"


class CountdownDialog(QDialog):
    def __init__(self, parent=None, countdown_seconds=3):
        super().__init__(parent, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setModal(True)
        self.countdown_seconds = countdown_seconds
        self.remaining = countdown_seconds
        
        # Center on screen
        screen = QGuiApplication.primaryScreen()
        if screen:
            geometry = screen.availableGeometry()
            self.setGeometry(
                geometry.center().x() - 150,
                geometry.center().y() - 75,
                300, 150
            )
        
        # Layout
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(15)
        
        self.label = QLabel(f"播放开始倒计时", self)
        self.label.setAlignment(Qt.AlignCenter)
        self.label.setStyleSheet("""
            QLabel {
                color: rgba(255,255,255,240);
                font-family: 'Segoe UI';
                font-size: 16px;
                font-weight: 600;
            }
        """)
        
        self.countdown_label = QLabel(str(self.remaining), self)
        self.countdown_label.setAlignment(Qt.AlignCenter)
        self.countdown_label.setStyleSheet("""
            QLabel {
                color: rgb(0, 200, 255);
                font-family: 'Segoe UI';
                font-size: 48px;
                font-weight: 800;
            }
        """)
        
        self.progress = QProgressBar(self)
        self.progress.setRange(0, countdown_seconds * 10)
        self.progress.setValue(countdown_seconds * 10)
        self.progress.setTextVisible(False)
        self.progress.setStyleSheet("""
            QProgressBar {
                border: 2px solid rgba(255,255,255,60);
                border-radius: 8px;
                background-color: rgba(40,40,48,180);
            }
            QProgressBar::chunk {
                background-color: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 rgb(0, 200, 255), stop:1 rgb(0, 150, 200));
                border-radius: 6px;
            }
        """)
        
        layout.addWidget(self.label)
        layout.addWidget(self.countdown_label)
        layout.addWidget(self.progress)
        
        # Background styling
        self.setStyleSheet("""
            QDialog {
                background-color: rgba(20, 20, 24, 240);
                border-radius: 12px;
                border: 2px solid rgba(0, 200, 255, 120);
            }
        """)
        
        # Timer for countdown
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._update_countdown)
        self.timer.start(100)  # Update every 100ms for smooth progress
        
        self._start_time = time.monotonic()
    
    def _update_countdown(self):
        elapsed = time.monotonic() - self._start_time
        remaining_time = max(0, self.countdown_seconds - elapsed)
        
        if remaining_time <= 0:
            self.timer.stop()
            self.accept()
            return
        
        # Update countdown display
        self.remaining = int(remaining_time) + 1
        self.countdown_label.setText(str(self.remaining))
        
        # Update progress bar
        progress_value = int((self.countdown_seconds - elapsed) * 10)
        self.progress.setValue(max(0, progress_value))
    
    def keyPressEvent(self, event):
        # Allow Escape to cancel
        if event.key() == Qt.Key_Escape:
            self.reject()
        else:
            super().keyPressEvent(event)


class ControlWindow(QWidget):
    def __init__(self, controller: "KeyOverlayController", overlay: OverlayWidget, bridge: KeyStateBridge):
        super().__init__(None, Qt.FramelessWindowHint | Qt.Window)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.controller = controller
        self.overlay = overlay
        self.bridge = bridge

        self.setWindowTitle("键盘监视控制面板")
        self.setWindowIcon(generate_tray_icon())
        self.setMinimumSize(520, 70)
        self._stay_on_top = False
        self._dragging = False
        self._drag_offset = QPoint(0, 0)
        self._resizing = False
        self._resize_origin_geom = None
        self._resize_origin_pos = None
        self._resize_handle_size = 12

        # Frame (rounded) as root background
        frame = QWidget(self)
        frame.setObjectName("frame")
        frame_layout = QVBoxLayout(frame)
        frame_layout.setContentsMargins(10, 10, 10, 10)
        frame_layout.setSpacing(6)

        # Header with pin/minimize/close (WeChat-style)
        header = QWidget(frame)
        header.setObjectName("header")
        hb = QHBoxLayout(header)
        hb.setContentsMargins(6, 0, 6, 0)
        hb.setSpacing(6)
        self.title_label = QLabel("键盘监视控制面板", header)
        self.title_label.setStyleSheet("color: rgba(255,255,255,220); font-size: 12px;")
        hb.addWidget(self.title_label)
        hb.addStretch(1)
        self.btn_pin = QToolButton(header)
        self.btn_pin.setCheckable(True)
        self.btn_pin.setText("📌")
        self.btn_pin.setToolTip("置顶显示（控制面板与显示面板）")
        self.btn_pin.setFixedSize(28, 24)
        self.btn_pin.setStyleSheet(
            """
            QToolButton { background-color: transparent; color: rgba(255,255,255,220); border: none; }
            QToolButton:hover { background-color: rgba(255,255,255,30); border-radius: 4px; }
            QToolButton:checked { color: rgb(0, 200, 255); }
            """
        )
        hb.addWidget(self.btn_pin)
        self.btn_min = QToolButton(header)
        self.btn_min.setText("—")
        self.btn_min.setToolTip("最小化")
        self.btn_min.setFixedSize(28, 24)
        self.btn_min.setStyleSheet(
            """
            QToolButton { background-color: transparent; color: rgba(255,255,255,220); border: none; }
            QToolButton:hover { background-color: rgba(255,255,255,30); border-radius: 4px; }
            """
        )
        hb.addWidget(self.btn_min)
        self.btn_close = QToolButton(header)
        self.btn_close.setText("×")
        self.btn_close.setToolTip("关闭")
        self.btn_close.setFixedSize(28, 24)
        self.btn_close.setStyleSheet(
            """
            QToolButton { background-color: transparent; color: rgba(255,255,255,220); border: none; }
            QToolButton:hover { background-color: rgba(255,80,80,160); color: white; border-radius: 4px; }
            """
        )
        hb.addWidget(self.btn_close)

        # Single controls container with unified layout
        container = QWidget(frame)
        container.setFixedHeight(50)
        container.setObjectName("container")
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        # Main control buttons
        self.btn_monitor = QPushButton("开始监视", container)
        self.btn_record = QPushButton("开始录制", container)
        self.btn_show = QPushButton("显示面板", container)
        
        # Playback controls
        self.btn_file_select = QPushButton("选择文件 ▼", container)
        self.btn_play_options = QPushButton("播放模式 ▼", container)
        self.btn_play_toggle = QPushButton("开始播放", container)

        # Track current selections
        self._selected_file = default_record_path()
        self._selected_file_name = "最近录制"
        self._play_mode = "once"  # "once", "n_times", "loop"
        self._play_times = 1
        self._is_playing = False

        # Style all buttons
        all_buttons = [self.btn_monitor, self.btn_record, self.btn_show, 
                      self.btn_file_select, self.btn_play_options, self.btn_play_toggle]
        
        for b in all_buttons:
            b.setCursor(Qt.PointingHandCursor)
            b.setFixedSize(100, 50)
            b.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
            b.setStyleSheet(
                """
                QPushButton {
                    background-color: rgba(40, 40, 48, 220);
                    color: rgba(255,255,255, 230);
                    border: 1px solid rgba(255,255,255, 40);
                    border-radius: 8px;
                    font-family: 'Segoe UI';
                    font-size: 11px;
                    font-weight: 600;
                    padding: 0px 0px;
                }
                QPushButton:hover {
                    background-color: rgba(60, 60, 72, 240);
                    border: 1px solid rgba(0, 200, 255, 120);
                }
                QPushButton:pressed {
                    background-color: rgba(30, 30, 38, 240);
                }
                """
            )

        layout.addWidget(self.btn_monitor)
        layout.addWidget(self.btn_record)
        layout.addWidget(self.btn_show)
        layout.addStretch()
        layout.addWidget(self.btn_file_select)
        layout.addWidget(self.btn_play_options)
        layout.addWidget(self.btn_play_toggle)

        frame_layout.addWidget(header)
        frame_layout.addWidget(container)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(frame)

        self.setStyleSheet(
            """
            QWidget#frame { background-color: rgba(20,20,24, 220); border-radius: 12px; }
            QWidget#header { background-color: transparent; }
            QWidget#container {
                background-color: rgba(30, 30, 36, 180);
                border: 1px solid rgba(255,255,255,40);
                border-radius: 8px;
            }
            """
        )

        # wire actions
        self._monitoring = False
        self._recording = False
        self.btn_monitor.clicked.connect(self._toggle_monitor)
        self.btn_record.clicked.connect(self._toggle_record)
        self.btn_show.clicked.connect(self._toggle_overlay_visible)
        self.btn_file_select.clicked.connect(self._show_file_menu)
        self.btn_play_options.clicked.connect(self._show_play_options_menu)
        self.btn_play_toggle.clicked.connect(self._toggle_playback)
        self.btn_pin.toggled.connect(self._apply_stay_on_top)
        self.btn_min.clicked.connect(self.showMinimized)
        self.btn_close.clicked.connect(self.close)

        # sync state from controller via bridge
        self.bridge.recording_state_changed.connect(self._on_record_state)
        self.bridge.playback_state_changed.connect(self._on_playback_state)

        # auto-size window to content, minimize extra whitespace
        self.adjustSize()

    # Dragging by header
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self.childAt(event.position().toPoint()) in (self.title_label, self.btn_pin, self.btn_min, self.btn_close):
            # allow buttons to handle
            super().mousePressEvent(event)
            return
        if event.button() == Qt.LeftButton and self._in_header(event.position().toPoint()):
            self._dragging = True
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
            return
        if event.button() == Qt.LeftButton and self._in_resize_handle(event.position().toPoint()):
            self._resizing = True
            self._resize_origin_geom = self.geometry()
            self._resize_origin_pos = event.globalPosition().toPoint()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._resizing and self._resize_origin_geom and self._resize_origin_pos:
            delta = event.globalPosition().toPoint() - self._resize_origin_pos
            new_w = max(520, self._resize_origin_geom.width() + delta.x())
            new_h = max(70, self._resize_origin_geom.height() + delta.y())
            self.resize(new_w, new_h)
            event.accept()
            return
        if self._dragging:
            new_pos = event.globalPosition().toPoint() - self._drag_offset
            self.move(new_pos)
            event.accept()
            return
        # cursor feedback
        if self._in_resize_handle(event.position().toPoint()):
            self.setCursor(Qt.SizeFDiagCursor)
        else:
            self.setCursor(Qt.ArrowCursor)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._dragging = False
            self._resizing = False
            self._resize_origin_geom = None
            self._resize_origin_pos = None
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _in_header(self, pos: QPoint) -> bool:
        # Top 32px area used as draggable header
        return 0 <= pos.y() <= 32

    def _in_resize_handle(self, pos: QPoint) -> bool:
        return pos.x() >= self.width() - self._resize_handle_size and pos.y() >= self.height() - self._resize_handle_size

    def _apply_stay_on_top(self, checked: bool):
        self._stay_on_top = checked
        # Control window
        self.setWindowFlag(Qt.WindowStaysOnTopHint, checked)
        self.show()
        # Overlay window
        self.overlay.setWindowFlag(Qt.WindowStaysOnTopHint, checked)
        self.overlay.show()

    def _toggle_monitor(self):
        self._monitoring = not self._monitoring
        self.controller.set_monitor_enabled(self._monitoring)
        self.btn_monitor.setText("停止监视" if self._monitoring else "开始监视")
        if not self._monitoring:
            self.overlay.clear_text()

    def _toggle_record(self):
        if self._recording:
            # Ask how to save
            choice = QMessageBox(self)
            choice.setWindowTitle("保存录制")
            choice.setText("选择保存方式：")
            btn_default = choice.addButton("保存到默认", QMessageBox.AcceptRole)
            btn_choose = choice.addButton("选择保存位置", QMessageBox.ActionRole)
            btn_cancel = choice.addButton("取消", QMessageBox.RejectRole)
            choice.setIcon(QMessageBox.Question)
            choice.exec()

            clicked = choice.clickedButton()
            if clicked is btn_choose:
                # Choose a file path; if canceled, fallback to default
                start_dir = str(default_record_path().parent)
                default_name = str(default_record_path().name)
                file_path, _ = QFileDialog.getSaveFileName(
                    self,
                    "选择保存位置",
                    str(default_record_path()),
                    "JSON 文件 (*.json);;所有文件 (*.*)",
                )
                if file_path:
                    self.controller.stop_recording(Path(file_path))
                else:
                    self.controller.stop_recording()
            elif clicked is btn_default:
                self.controller.stop_recording()
            else:
                # Cancel pressed: still stop and save to default (per requirement default path)
                self.controller.stop_recording()
        else:
            self.controller.start_recording()

    def _toggle_overlay_visible(self):
        self.overlay.setVisible(not self.overlay.isVisible())
        if self.overlay.isVisible():
            self.overlay.reposition_to_corner()
        self.btn_show.setText("隐藏面板" if self.overlay.isVisible() else "显示面板")

    def _on_record_state(self, is_rec: bool):
        self._recording = is_rec
        self.btn_record.setText("停止录制" if is_rec else "开始录制")

    def _on_playback_state(self, is_playing: bool):
        self._is_playing = is_playing
        self.btn_play_toggle.setText("停止播放" if is_playing else "开始播放")

    def _show_file_menu(self):
        menu = QMenu(self)
        
        # Recent recording option
        recent_action = QAction("最近录制", menu)
        recent_action.triggered.connect(lambda: self._select_file(default_record_path(), "最近录制"))
        
        # Choose file option
        choose_action = QAction("选择文件...", menu)
        choose_action.triggered.connect(self._choose_file)
        
        # Mark current selection
        if self._selected_file == default_record_path():
            recent_action.setCheckable(True)
            recent_action.setChecked(True)
        
        menu.addAction(recent_action)
        menu.addAction(choose_action)
        
        # Show menu below the button
        button_pos = self.btn_file_select.mapToGlobal(QPoint(0, self.btn_file_select.height()))
        menu.exec(button_pos)

    def _choose_file(self):
        start_dir = str(default_record_path().parent)
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "选择录制文件",
            str(default_record_path()),
            "JSON 文件 (*.json);;所有文件 (*.*)",
        )
        if file_path:
            file_name = Path(file_path).stem
            self._select_file(Path(file_path), file_name)

    def _select_file(self, file_path: Path, display_name: str):
        self._selected_file = file_path
        self._selected_file_name = display_name
        # Update button text to show selection
        self.btn_file_select.setText(f"{display_name[:8]}...")

    def _show_play_options_menu(self):
        menu = QMenu(self)
        
        once_action = QAction("播放一次", menu)
        once_action.triggered.connect(lambda: self._set_play_mode("once"))
        
        n_times_action = QAction("播放N次", menu)
        n_times_action.triggered.connect(self._set_play_n_times)
        
        loop_action = QAction("循环播放", menu)
        loop_action.triggered.connect(lambda: self._set_play_mode("loop"))
        
        # Mark current selection
        if self._play_mode == "once":
            once_action.setCheckable(True)
            once_action.setChecked(True)
        elif self._play_mode == "n_times":
            n_times_action.setCheckable(True)
            n_times_action.setChecked(True)
        elif self._play_mode == "loop":
            loop_action.setCheckable(True)
            loop_action.setChecked(True)
        
        menu.addAction(once_action)
        menu.addAction(n_times_action)
        menu.addAction(loop_action)
        
        # Show menu below the button
        button_pos = self.btn_play_options.mapToGlobal(QPoint(0, self.btn_play_options.height()))
        menu.exec(button_pos)

    def _set_play_mode(self, mode: str):
        self._play_mode = mode
        if mode == "once":
            self.btn_play_options.setText("播放一次")
        elif mode == "loop":
            self.btn_play_options.setText("循环播放")

    def _set_play_n_times(self):
        times, ok = QInputDialog.getInt(self, "播放若干次", "次数：", self._play_times, 1, 9999, 1)
        if ok:
            self._play_times = times
            self._play_mode = "n_times"
            self.btn_play_options.setText(f"播放{times}次")

    def _toggle_playback(self):
        if self._is_playing:
            # Stop current playback
            self.controller.stop_playback()
        else:
            # Start playback with countdown
            countdown = CountdownDialog(self)
            if countdown.exec() == QDialog.Accepted:
                if self._play_mode == "once":
                    self.controller.play_file(self._selected_file, times=1, loop=False, show_countdown=False)
                elif self._play_mode == "n_times":
                    self.controller.play_file(self._selected_file, times=self._play_times, loop=False, show_countdown=False)
                elif self._play_mode == "loop":
                    self.controller.play_file(self._selected_file, times=None, loop=True, show_countdown=False)

    # Using standard window frame; no custom drag handlers needed
def main():
    app = QApplication(sys.argv)
    overlay = OverlayWidget()

    # bridge UI updates
    bridge = KeyStateBridge()
    bridge.keys_text_changed.connect(overlay.set_text)

    # controller for global hooks, recording, playback
    controller = KeyOverlayController(bridge)
    controller.start()

    overlay.show()
    _tray = SystemTray(app, overlay, controller, bridge)
    control = ControlWindow(controller, overlay, bridge)
    control.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())


