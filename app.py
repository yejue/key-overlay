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
    def play_file(self, path: Path):
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
                last_t = 0.0
                for e in events:
                    t = float(e.get("t", 0.0))
                    delay = max(0.0, t - last_t)
                    if delay:
                        time.sleep(delay)
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
            finally:
                self._is_playing = False
                self.bridge.playback_state_changed.emit(False)

        threading.Thread(target=_runner, daemon=True).start()

    def play_last(self):
        self.play_file(default_record_path())


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

        toggle_action = QAction("显示/隐藏显示面板", menu)
        toggle_action.triggered.connect(self.toggle_overlay)

        quit_action = QAction("退出", menu)
        quit_action.triggered.connect(self.quit)

        menu.addMenu(corner_menu)
        menu.addSeparator()
        menu.addAction(record_action)
        menu.addAction(stop_record_action)
        menu.addAction(play_action)
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

        container = QWidget(frame)
        container.setFixedHeight(50)
        container.setObjectName("container")
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self.btn_monitor = QPushButton("开始监视", container)
        self.btn_record = QPushButton("开始录制", container)
        self.btn_play_last = QPushButton("播放最近", container)
        self.btn_play_file = QPushButton("选择播放", container)
        self.btn_show = QPushButton("显示面板", container)

        for b in (self.btn_monitor, self.btn_record, self.btn_play_last, self.btn_play_file, self.btn_show):
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
                    font-size: 12px;
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
        layout.addWidget(self.btn_play_last)
        layout.addWidget(self.btn_play_file)
        layout.addWidget(self.btn_show)

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
        self.btn_play_last.clicked.connect(self.controller.play_last)
        self.btn_play_file.clicked.connect(self._play_choose_file)
        self.btn_show.clicked.connect(self._toggle_overlay_visible)
        self.btn_pin.toggled.connect(self._apply_stay_on_top)
        self.btn_min.clicked.connect(self.showMinimized)
        self.btn_close.clicked.connect(self.close)

        # sync state from controller via bridge
        self.bridge.recording_state_changed.connect(self._on_record_state)

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

    def _play_choose_file(self):
        start_dir = str(default_record_path().parent)
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "选择录制文件",
            str(default_record_path()),
            "JSON 文件 (*.json);;所有文件 (*.*)",
        )
        if file_path:
            self.controller.play_file(Path(file_path))

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


