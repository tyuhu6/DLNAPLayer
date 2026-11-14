"""屏幕镜像窗口。

AirPlay 接收由随包的 UxPlay（第三方开源，GPL）提供：启动后 iPhone/iPad
即可在控制中心「屏幕镜像」里看到本机。接收到的画面窗口会被嵌入到这里。

安卓设备原生不支持 AirPlay，需使用系统的无线显示（Miracast）。
"""

import ctypes
import subprocess
from ctypes import wintypes
from pathlib import Path

from PySide6.QtCore import (
    QEasingCurve, QPropertyAnimation, QTimer, Qt, Signal,
)
from PySide6.QtGui import QColor, QGuiApplication, QLinearGradient, QPainter
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QMainWindow, QVBoxLayout, QWidget,
)

from ui import icons
from ui.control_overlay import IconButton
from ui.frameless import FramelessMixin
from ui.loading import PulseHalo, Spinner

_user32 = ctypes.windll.user32
_shell32 = ctypes.windll.shell32
_GWL_STYLE = -16
_WS_CHILD = 0x40000000
_WS_POPUP = 0x80000000
_NIM_DELETE = 0x00000002
_SHCNE_ASSOCCHANGED = 0x08000000


class _NOTIFYICONDATA(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uID", wintypes.UINT),
        ("uFlags", wintypes.UINT),
        ("uCallbackMessage", wintypes.UINT),
        ("hIcon", wintypes.HICON),
        ("szTip", ctypes.c_wchar * 128),
        ("dwState", wintypes.DWORD),
        ("dwStateMask", wintypes.DWORD),
        ("szInfo", ctypes.c_wchar * 256),
        ("uTimeoutOrVersion", wintypes.UINT),
        ("szInfoTitle", ctypes.c_wchar * 64),
        ("dwInfoFlags", wintypes.DWORD),
        ("guidItem", ctypes.c_byte * 16),
        ("hBalloonIcon", wintypes.HICON),
    ]

_QSS = """
QLabel { background: transparent; color: #FFFFFF; }
QLabel#brand { color: rgba(255,255,255,120); font-size: 12px; }
QLabel#title { color: rgba(255,255,255,235); font-size: 17px; font-weight: 500; }
QLabel#sub { color: rgba(255,255,255,110); font-size: 13px; }
QLabel#status { color: rgba(255,255,255,80); font-size: 11px; }
"""


def _windows_of_pid(pid: int) -> tuple[list[int], bool]:
    """返回 (可嵌入窗口列表, 是否存在系统对话框)。

    排除 #32770（标准对话框），否则 UxPlay 的「安装 Bonjour」提示被嵌进画面区。
    """
    found: list[int] = []
    has_dialog = False

    def _cb(hwnd, _lparam):
        nonlocal has_dialog
        owner = wintypes.DWORD()
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value != pid or not _user32.IsWindowVisible(hwnd):
            return True
        cls = ctypes.create_unicode_buffer(128)
        _user32.GetClassNameW(hwnd, cls, 128)
        if cls.value == "#32770":
            has_dialog = True
            return True
        rect = wintypes.RECT()
        _user32.GetWindowRect(hwnd, ctypes.byref(rect))
        if rect.right - rect.left > 160 and rect.bottom - rect.top > 90:
            found.append(hwnd)
        return True

    proc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    _user32.EnumWindows(proc(_cb), 0)
    return found, has_dialog


def _find_uxplay_pid() -> int:
    """返回已在运行的 uxplay-windows 进程 PID（没有则 0）。"""
    try:
        out = subprocess.check_output(
            ["tasklist", "/FI", "IMAGENAME eq uxplay-windows.exe", "/FO", "CSV", "/NH"],
            text=True, errors="ignore", creationflags=subprocess.CREATE_NO_WINDOW)
    except Exception:
        return 0
    for line in out.splitlines():
        parts = [p.strip().strip('"') for p in line.split('","')]
        if len(parts) >= 2 and parts[0].lower().startswith("uxplay"):
            try:
                return int(parts[1])
            except ValueError:
                pass
    return 0


def _remove_uxplay_tray_icon(pid: int) -> None:
    """删除属于该进程的系统托盘图标并强制刷新 Shell。

    Windows Shell 的托盘图标删除后可能有缓存延迟，因此：
    1) 对每个候选 hwnd 尝试多个 uID（0~5），防止 Qt 用非零 ID
    2) 删除成功后调 SHChangeNotify 刷新通知区
    3) 被调方（_poll）会在 1.2s 后再来一次，进一步抵消重建/缓存
    """
    if not pid:
        return

    deleted_any = False

    def _cb(hwnd, _lparam):
        nonlocal deleted_any
        owner = wintypes.DWORD()
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value != pid:
            return True
        cls = ctypes.create_unicode_buffer(128)
        _user32.GetClassNameW(hwnd, cls, 128)
        # 对每个 hwnd 尝试多个常见 uID
        for uid in range(6):
            nid = _NOTIFYICONDATA()
            nid.cbSize = ctypes.sizeof(_NOTIFYICONDATA)
            nid.hWnd = hwnd
            nid.uID = uid
            ok = _shell32.Shell_NotifyIconW(_NIM_DELETE, ctypes.byref(nid))
            if ok:
                deleted_any = True
                print(f"[mirror] tray delete hwnd={hwnd} cls={cls.value!r} uID={uid} OK", flush=True)
        return True

    proc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    try:
        _user32.EnumWindows(proc(_cb), 0)
        if deleted_any:
            # 强制让 Shell 重新扫描通知区
            _shell32.SHChangeNotify(_SHCNE_ASSOCCHANGED, 0, None, None)
    except Exception as exc:  # noqa: BLE001
        print(f"[mirror] tray cleanup error: {exc}", flush=True)


class MirrorWindow(FramelessMixin, QMainWindow):
    closed = Signal()

    def __init__(self, device_name: str, vendor_dir: Path, parent=None) -> None:
        super().__init__(parent)
        self._device_name = device_name
        self._vendor_dir = vendor_dir
        self._process: subprocess.Popen | None = None
        self._pid: int = 0
        self._embedded: int = 0
        self._centered = False

        self.setWindowTitle("屏幕镜像")
        self.setMinimumSize(720, 480)
        self.resize(1000, 620)
        self.setup_frameless()

        central = QWidget(self)
        central.setObjectName("central")
        self.setCentralWidget(central)
        self.add_drag_widget(central)

        self.header = _Header(self)
        self.header.btn_min.clicked.connect(self.showMinimized)
        self.header.btn_max.clicked.connect(self.toggle_maximized)
        self.header.btn_close.clicked.connect(self.close)
        self.add_drag_widget(self.header)

        # 画面容器：UxPlay 的窗口会被嵌入到这里（未连接时隐藏）
        self.video_host = QWidget(self)
        self.video_host.setStyleSheet("background: #050506;")
        self.video_host.hide()

        self.tip_icon = QLabel()
        self.tip_icon.setPixmap(icons.pixmap("mirror", "rgba(255,255,255,150)", 34, 2.0))
        self.tip_icon.setAlignment(Qt.AlignCenter)

        self.title = QLabel("等待手机连接", self)
        self.title.setObjectName("title")
        self.title.setAlignment(Qt.AlignCenter)

        self.sub = QLabel(
            "iPhone / iPad：控制中心 → 屏幕镜像 → 选择本机\n"
            "安卓：AirPlay 为苹果专有协议，暂不支持",
            self)
        self.sub.setObjectName("sub")
        self.sub.setAlignment(Qt.AlignCenter)

        self.status = QLabel("正在启动 AirPlay 接收器…", self)
        self.status.setObjectName("status")

        # 等待连接时图标背后的呼吸光晕
        self._halo_box = QWidget(self)
        self._halo_box.setFixedSize(220, 220)
        self._halo = PulseHalo(self._halo_box)
        self._halo.setGeometry(0, 0, 220, 220)
        self.tip_icon.setParent(self._halo_box)
        self.tip_icon.setGeometry(0, 0, 220, 220)

        # 启动接收器时底部状态行的小号加载环
        self._spinner = Spinner(16, self)
        self._spinner.hide()

        lay = QVBoxLayout(central)
        lay.setContentsMargins(0, 44, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self.video_host, 1)

        self._hint = QWidget(self)
        hint_lay = QVBoxLayout(self._hint)
        hint_lay.setContentsMargins(0, 0, 0, 32)
        hint_lay.setSpacing(10)
        hint_lay.addStretch(1)
        hint_lay.addWidget(self._halo_box, 0, Qt.AlignHCenter)
        hint_lay.addWidget(self.title)
        hint_lay.addWidget(self.sub)
        hint_lay.addStretch(1)
        lay.addWidget(self._hint, 1)

        bottom = QHBoxLayout()
        bottom.setContentsMargins(18, 0, 18, 12)
        bottom.addWidget(self._spinner)
        bottom.addWidget(self.status)
        bottom.addStretch(1)
        lay.addLayout(bottom)

        self.setStyleSheet(_QSS)
        self._halo.start()
        self._spinner.start()

        # 入场淡入
        self._enter_anim = QPropertyAnimation(self, b"windowOpacity", self)
        self._enter_anim.setDuration(260)
        self._enter_anim.setStartValue(0.0)
        self._enter_anim.setEndValue(1.0)
        self._enter_anim.setEasingCurve(QEasingCurve.OutCubic)
        self.setWindowOpacity(0.0)

        self._timer = QTimer(self)
        self._timer.setInterval(1200)
        self._timer.timeout.connect(self._poll)
        self._timer.start()

        self._start_receiver()

    # ---------- AirPlay 接收器 ----------
    def _start_receiver(self) -> None:
        existing = _find_uxplay_pid()
        if existing:
            self._pid = existing
            _remove_uxplay_tray_icon(self._pid)
            self.status.setText(f"接收器已就绪 · 设备名「{self._device_name}」")
            self._spinner.stop()
            return
        exe = self._vendor_dir / "uxplay" / "uxplay-windows.exe"
        if not exe.exists():
            self.status.setText("未找到 AirPlay 接收组件（vendor/uxplay）")
            self._spinner.stop()
            return
        try:
            self._process = subprocess.Popen(
                [str(exe)], cwd=str(exe.parent),
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self._pid = self._process.pid
            self.status.setText(f"接收器已启动 · 设备名「{self._device_name}」")
            self._spinner.stop()
        except Exception as exc:  # noqa: BLE001
            self.status.setText(f"启动失败：{exc}")
            self._spinner.stop()

    def _poll(self) -> None:
        if not self._pid:
            return
        # UxPlay 可能在连接变化时重建托盘图标，每次轮询都清一次
        _remove_uxplay_tray_icon(self._pid)
        if self._process is not None and self._process.poll() is not None:
            return
        try:
            wins, has_dialog = _windows_of_pid(self._pid)
            if has_dialog and not self._embedded:
                self.status.setText("请在弹窗中安装 Bonjour 服务（仅首次需要）")
            if not wins:
                return
            hwnd = wins[0]
            if hwnd == self._embedded:
                self._fit_embedded()
                return
            self._embed(hwnd)
        except Exception:
            pass

    def _embed(self, hwnd: int) -> None:
        host = int(self.video_host.winId())
        # 样式值是 32 位有符号数，必须先截断，否则 setWindowLong 参数会溢出
        style = _user32.GetWindowLongW(hwnd, _GWL_STYLE) & 0xFFFFFFFF
        style = ((style & ~_WS_POPUP) | _WS_CHILD) & 0xFFFFFFFF
        _user32.SetWindowLongW(hwnd, _GWL_STYLE, style)
        _user32.SetParent(hwnd, host)
        self._embedded = hwnd
        self.video_host.show()
        self._fit_embedded()
        self._hint.hide()
        self._halo.stop()
        self.status.setText(f"正在镜像 · 设备名「{self._device_name}」")

    def _fit_embedded(self) -> None:
        if not self._embedded:
            return
        _user32.MoveWindow(self._embedded, 0, 0,
                           self.video_host.width(), self.video_host.height(), True)

    # ---------- 无边框钩子 ----------
    def on_drag_area_double_click(self) -> bool:
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()
        return True

    def on_maximized_changed(self, maximized: bool) -> None:
        self.header.btn_max.set_icon_name("restore" if maximized else "maximize")
        self.header.btn_max.setToolTip("向下还原" if maximized else "最大化")

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.header.setGeometry(0, 0, self.width(), 44)
        self._fit_embedded()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._centered:
            self._centered = True
            screen = QGuiApplication.primaryScreen()
            if screen is not None:
                geo = screen.availableGeometry()
                self.move(geo.center().x() - self.width() // 2,
                          geo.center().y() - self.height() // 2)
        if self._enter_anim.state() != QPropertyAnimation.Running:
            self.setWindowOpacity(0.0)
            self._enter_anim.start()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        grad = QLinearGradient(0, 0, 0, self.height())
        grad.setColorAt(0.0, QColor("#141416"))
        grad.setColorAt(0.5, QColor("#0C0C0E"))
        grad.setColorAt(1.0, QColor("#09090A"))
        p.fillRect(self.rect(), grad)

    def closeEvent(self, event) -> None:
        self._timer.stop()
        self._halo.stop()
        self._spinner.stop()
        if self._embedded:
            try:
                _user32.SetParent(self._embedded, 0)
            except Exception:
                pass
        self._kill_receiver()
        self.reset_frameless()
        self.closed.emit()
        super().closeEvent(event)

    def _kill_receiver(self) -> None:
        """终止 UxPlay 进程（无论是自己启动的还是复用的已有进程）。"""
        if not self._pid:
            return
        pid = self._pid
        self._pid = 0
        self._process = None
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW, timeout=5)
        except Exception:
            pass


class _Header(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(44)

        dot = QLabel(self)
        dot.setPixmap(icons.pixmap("mirror", "rgba(255,255,255,180)", 15, 2.0))
        dot.setFixedSize(16, 16)

        title = QLabel("屏幕镜像", self)
        title.setObjectName("brand")

        self.btn_min = IconButton("minimize", 14, 30, "最小化", self)
        self.btn_max = IconButton("maximize", 13, 30, "最大化", self)
        self.btn_close = IconButton("close", 14, 30, "关闭", self)
        for btn in (self.btn_min, self.btn_max, self.btn_close):
            btn.setStyleSheet(
                "QToolButton{background:transparent;border:none;border-radius:7px;padding:0;}"
                "QToolButton:hover{background:rgba(255,255,255,26);}")
        self.btn_close.setStyleSheet(
            "QToolButton{background:transparent;border:none;border-radius:7px;padding:0;}"
            "QToolButton:hover{background:rgba(232,62,62,210);}")

        lay = QHBoxLayout(self)
        lay.setContentsMargins(18, 0, 10, 0)
        lay.setSpacing(9)
        lay.addWidget(dot)
        lay.addWidget(title)
        lay.addStretch(1)
        lay.addWidget(self.btn_min)
        lay.addWidget(self.btn_max)
        lay.addWidget(self.btn_close)
