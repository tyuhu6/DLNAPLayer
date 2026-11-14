"""DLNAPLayer 入口：装配播放内核、UPnP 服务栈与 UI。

启动后先显示主界面（首页），用户点击「启动投屏」才开启 DLNA 服务并弹出
投屏监视窗口；关闭监视窗口即停止服务并回到主界面。

首次运行需要允许 Windows 防火墙（专用网络放行 UDP 1900 与 TCP 服务端口），
否则手机将无法发现本设备。
"""

import ctypes
import sys
import traceback
from pathlib import Path

from PySide6.QtCore import QObject, Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QFileDialog, QMenu, QSystemTrayIcon

import config
from bridge import Bridge
from player.mpv_controller import MpvController
from ui.home_window import HomeWindow
from ui.main_window import MainWindow
from ui.mirror_window import MirrorWindow
from upnp.gena import GenaManager
from upnp.http_server import DeviceContext, start_upnp_server
from upnp.services import ServiceRegistry
from upnp.ssdp import SSDPServer
from upnp.state import RendererState


def _crash_log_path() -> Path:
    if getattr(sys, "frozen", False):  # PyInstaller 打包后
        return Path(sys.executable).parent / "crash.log"
    return Path(__file__).resolve().parent / "crash.log"


def _resource_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent


def _vendor_dir() -> Path:
    """第三方组件目录：打包后在 exe 同级的 vendor，源码运行在项目根目录。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent / "vendor"
    return Path(__file__).resolve().parent / "vendor"


def _app_icon() -> QIcon:
    """应用图标：打包后来自 _internal/app.ico，源码运行来自项目根目录。"""
    ico = _resource_dir() / "app.ico"
    return QIcon(str(ico)) if ico.exists() else QIcon()


def _set_app_user_model_id() -> None:
    """让 Windows 任务栏使用本程序自己的图标与分组（而非 python.exe）。"""
    if sys.platform.startswith("win"):
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "DLNAPLayer.CastPlayer")
        except Exception:
            pass


def _install_crash_logger() -> None:
    """打包版无控制台，未捕获异常写入 crash.log 便于排查。

    注意：不启用 faulthandler——其未处理异常过滤器会与 libmpv 内部线程的
    SEH 异常处理相互干扰，导致进程被误杀。
    """
    log_path = _crash_log_path()
    try:
        f = open(log_path, "a", encoding="utf-8", buffering=1)
        sys.stderr = f

        def _hook(t, v, tb):
            f.write("\n===== UNCAUGHT EXCEPTION =====\n")
            traceback.print_exception(t, v, tb, file=f)
        sys.excepthook = _hook

        import threading

        def _thread_hook(args):
            f.write("\n===== THREAD EXCEPTION =====\n")
            traceback.print_exception(args.exc_type, args.exc_value,
                                      args.exc_traceback, file=f)
        threading.excepthook = _thread_hook
    except Exception:
        pass


def _log(msg: str) -> None:
    print(msg)
    try:
        with open(_crash_log_path(), "a", encoding="utf-8") as f:
            f.write(msg + "\n")
    except OSError:
        pass


class App(QObject):
    """应用级控制器：管理主界面、投屏服务和监视窗口的生命周期。"""

    def __init__(self, app: QApplication) -> None:
        super().__init__()
        self.app = app
        self.settings = config.load()
        self.bind_ip = self.settings.get("bind_ip") or config.get_lan_ip()
        self.base_port = int(self.settings.get("http_port", 9090))
        self.device_uuid = self.settings["uuid"]
        self.name = self.settings.get("device_name", "客厅电视")

        # ---------- 播放内核（进程内复用） ----------
        self.bridge = Bridge()
        self.state = RendererState()
        self.state.set_volume(int(self.settings.get("volume", 100)))
        self.controller = MpvController(self.state, self.bridge)
        self.gena = GenaManager(self.state)
        self.registry = ServiceRegistry(self.state, self.controller, self.gena)
        self.controller.transport_hook = self.gena.notify_avt
        self.controller.volume_hook = self.gena.notify_rc

        # ---------- 服务与窗口 ----------
        self.device: DeviceContext | None = None
        self.ssdp: SSDPServer | None = None
        self.server = None
        self.player: MainWindow | None = None
        self.mirror: MirrorWindow | None = None

        self.home = HomeWindow(self.name, self.bind_ip)
        self.home.setWindowIcon(_app_icon())
        self.home.toggle_requested.connect(self.toggle_service)
        self.home.mirror_requested.connect(self.open_mirror)
        self.home.local_requested.connect(self.open_local)
        self.home.device_name_changed.connect(self._on_rename)
        self.home.closed.connect(self.quit)
        self.home.show()

        self._setup_tray()
        _log(f"[app] 局域网 IP: {self.bind_ip}  设备名: {self.name}")

    # ---------- 托盘 ----------
    def _setup_tray(self) -> None:
        self.tray = QSystemTrayIcon(_app_icon(), self)
        self.tray.setToolTip("DLNA 投屏播放器")

        menu = QMenu()
        menu.setStyleSheet(
            "QMenu{background:#1A1A1C;color:#E9E9EC;border:1px solid rgba(255,255,255,26);"
            "border-radius:8px;padding:6px;}"
            "QMenu::item{padding:7px 26px 7px 14px;border-radius:5px;font-size:12px;}"
            "QMenu::item:selected{background:rgba(255,255,255,22);}"
            "QMenu::separator{height:1px;background:rgba(255,255,255,20);margin:5px 8px;}")

        act_show = menu.addAction("显示主界面")
        act_show.triggered.connect(self.show_home)
        self.act_toggle = menu.addAction("启动投屏")
        self.act_toggle.triggered.connect(self.toggle_service)
        menu.addSeparator()
        menu.addAction("退出").triggered.connect(self.quit)

        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_tray_activated)
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray.show()
            _log("[app] 托盘图标已显示")
        else:
            _log("[app] 当前系统不支持托盘图标")

    def _on_tray_activated(self, reason) -> None:
        if reason in (QSystemTrayIcon.ActivationReason.Trigger,
                      QSystemTrayIcon.ActivationReason.DoubleClick):
            self.show_home()

    def show_home(self) -> None:
        self.home.showNormal()
        self.home.show()
        self.home.raise_()
        self.home.activateWindow()

    # ---------- 服务生命周期 ----------
    def toggle_service(self) -> None:
        if self.server is None:
            self.start_service()
        else:
            self.stop_service()
            self._sync_home()

    def start_service(self) -> None:
        if self.server is not None:
            return
        try:
            self.device = DeviceContext(self.bind_ip, self.base_port,
                                        self.device_uuid, self.name)
            self.server, _thread = start_upnp_server(
                self.bind_ip, self.base_port, self.registry, self.gena, self.device)
            self.ssdp = SSDPServer(self.bind_ip, self.device.port,
                                   self.device_uuid, self.device.location_url())
            self.ssdp.start()
        except Exception as exc:
            _log(f"[app] 启动投屏服务失败: {exc}")
            self._sync_home()
            return

        _log(f"[app] 投屏服务已就绪: {self.device.location_url()}")
        self._sync_home()
        self._open_player()

    def stop_service(self) -> None:
        try:
            self.controller.stop()
        except Exception:
            pass
        if self.ssdp is not None:
            try:
                self.ssdp.stop()
            except Exception:
                pass
            self.ssdp = None
        if self.server is not None:
            try:
                self.server.shutdown()
            except Exception:
                pass
            self.server = None

    def _sync_home(self) -> None:
        running = self.server is not None
        self.home.set_running(running)
        if hasattr(self, "act_toggle"):
            self.act_toggle.setText("停止投屏" if running else "启动投屏")

    # ---------- 窗口 ----------
    def _open_player(self) -> None:
        if self.device is None:
            _log("[app] _open_player: device 为空")
            return
        try:
            self.player = MainWindow(self.controller, self.bridge, self.state,
                                     self.device, self.settings)
        except Exception as exc:
            _log(f"[app] 播放窗口创建失败: {exc!r}")
            return
        self.player.setWindowIcon(_app_icon())
        self.player.closed.connect(self._on_player_closed)
        self.player.show()
        self.player.raise_()
        self.player.activateWindow()

    def _on_player_closed(self) -> None:
        self.player = None
        self.stop_service()
        self._sync_home()
        # 主界面不隐藏，仅把焦点还回去
        self.show_home()

    # ---------- 屏幕镜像 ----------
    def open_mirror(self) -> None:
        if self.mirror is not None:
            self.mirror.showNormal()
            self.mirror.show()
            self.mirror.raise_()
            self.mirror.activateWindow()
            return
        self.mirror = MirrorWindow(self.name, _vendor_dir())
        self.mirror.setWindowIcon(_app_icon())
        self.mirror.closed.connect(self._on_mirror_closed)
        self.mirror.show()
        self.mirror.raise_()
        self.mirror.activateWindow()
        _log("[app] 屏幕镜像窗口已打开")

    def _on_mirror_closed(self) -> None:
        self.mirror = None
        self.show_home()

    # ---------- 本地播放 ----------
    def open_local(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self.home, "选择视频文件", "",
            "视频文件 (*.mp4 *.mkv *.avi *.mov *.wmv *.flv *.ts *.m2ts *.webm *.m4v *.m3u8);;"
            "所有文件 (*.*)")
        if not path:
            return
        if self.device is None:
            self.device = DeviceContext(self.bind_ip, self.base_port,
                                        self.device_uuid, self.name)
        if self.player is None:
            self._open_player()
        if self.player is None:
            return
        self.player.raise_()
        self.player.activateWindow()
        self.player.play_local(path)
        _log(f"[app] 本地播放: {path}")

    def _on_rename(self, name: str) -> None:
        self.name = name
        self.settings["device_name"] = name
        config.save(self.settings)
        if self.device is not None:
            self.device.name = name
        if self.player is not None:
            self.player.device.name = name
            self.player.idle_page.set_device_name(name)

    # ---------- 退出 ----------
    def quit(self) -> None:
        _log("[app] quit() 被调用")
        self.stop_service()
        if self.player is not None:
            self.player.blockSignals(True)
            self.player.close()
            self.player = None
        if self.mirror is not None:
            self.mirror.blockSignals(True)
            self.mirror.close()
            self.mirror = None
        if hasattr(self, "tray"):
            self.tray.hide()
        self.settings["volume"] = self.state.get_volume()
        config.save(self.settings)
        try:
            self.controller.terminate()
        except Exception:
            pass
        self.app.quit()


def main() -> int:
    _install_crash_logger()
    # 必须在 QApplication 创建前设置：Windows 默认 ANGLE GLES 与 mpv render API 不兼容
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_UseDesktopOpenGL, True)
    _set_app_user_model_id()

    app = QApplication(sys.argv)
    app.setApplicationName("DLNAPLayer")
    app.setWindowIcon(_app_icon())
    # 由 App 显式控制退出，避免主界面隐藏期间窗口数归零导致误退出
    app.setQuitOnLastWindowClosed(False)

    _app = App(app)  # noqa: F841  保持引用
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
