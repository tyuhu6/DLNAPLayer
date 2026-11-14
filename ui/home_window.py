"""主界面（应用首页）：极简风格，三种模式入口。

- 投屏播放：DLNA 接收手机视频 APP 推送
- 屏幕镜像：AirPlay / 无线显示
- 本地播放：播放电脑上的视频文件
"""

from PySide6.QtCore import (
    Property, QEasingCurve, QPropertyAnimation, QSize, Qt, QTimer, Signal,
)
from PySide6.QtGui import QColor, QGuiApplication, QLinearGradient, QPainter, QPainterPath, QRadialGradient
from PySide6.QtWidgets import (
    QGraphicsOpacityEffect, QHBoxLayout, QLabel, QMainWindow,
    QInputDialog, QVBoxLayout, QWidget,
)

from ui import icons
from ui.control_overlay import IconButton
from ui.frameless import FramelessMixin

_QSS = """
QLabel { background: transparent; color: #FFFFFF; }
QLabel#brand { color: rgba(255, 255, 255, 120); font-size: 12px; }
QLabel#device { color: #FFFFFF; font-size: 32px; font-weight: 300; }
QLabel#meta { color: rgba(255, 255, 255, 95); font-size: 13px; }
QLabel#hint { color: rgba(255, 255, 255, 78); font-size: 11px; }
QLabel#modeTitle { color: #EDEDEF; font-size: 14px; font-weight: 500; }
QLabel#modeSub { color: rgba(255, 255, 255, 92); font-size: 12px; }
QWidget#divider { background: rgba(255, 255, 255, 20); }
"""


class _ClickableLabel(QLabel):
    clicked = Signal()

    def __init__(self, text: str = "", parent=None) -> None:
        super().__init__(text, parent)
        self.setCursor(Qt.PointingHandCursor)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class _ModeRow(QWidget):
    """一行模式入口：图标 + 标题/副标题 + 右箭头。

    hover 用自绘圆角背景 + QPropertyAnimation 平滑过渡，避免 QSS 瞬时切换。
    """

    clicked = Signal()

    def __init__(self, icon_name: str, title: str, subtitle: str,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("modeRow")
        self.setAttribute(Qt.WA_Hover, True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(64)
        self._icon_name = icon_name
        self._hover = 0.0
        self._pressed = False
        self._starting = False  # 投屏启动中：图标脉冲

        self.icon_label = QLabel(self)
        self.icon_label.setPixmap(icons.pixmap(icon_name, "rgba(255,255,255,210)", 19, 2.0))
        self.icon_label.setFixedSize(QSize(20, 20))
        self.icon_label.setScaledContents(True)

        self.title_label = QLabel(title, self)
        self.title_label.setObjectName("modeTitle")
        self.sub_label = QLabel(subtitle, self)
        self.sub_label.setObjectName("modeSub")

        text = QVBoxLayout()
        text.setContentsMargins(0, 0, 0, 0)
        text.setSpacing(2)
        text.addWidget(self.title_label)
        text.addWidget(self.sub_label)

        self.chevron = QLabel(self)
        self.chevron.setPixmap(icons.pixmap("chevron_right", "rgba(255,255,255,80)", 15, 2.0))
        self.chevron.setFixedSize(QSize(16, 16))
        self.chevron.setScaledContents(True)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(12)
        lay.addWidget(self.icon_label, 0, Qt.AlignTop)
        lay.addLayout(text, 1)
        lay.addWidget(self.chevron, 0, Qt.AlignVCenter)

        for child in (self.icon_label, self.title_label, self.sub_label, self.chevron):
            child.setAttribute(Qt.WA_TransparentForMouseEvents, True)

        # hover 动画：220ms 缓动
        self._hover_anim = QPropertyAnimation(self, b"hover", self)
        self._hover_anim.setDuration(220)
        self._hover_anim.setEasingCurve(QEasingCurve.OutCubic)

        # 启动脉冲：图标透明度呼吸
        self._pulse_timer = QTimer(self)
        self._pulse_timer.setInterval(40)
        self._pulse_timer.timeout.connect(self._pulse_tick)
        self._pulse_phase = 0.0

    @Property(float)
    def hover(self) -> float:
        return self._hover

    @hover.setter
    def hover(self, value: float) -> None:
        self._hover = float(value)
        self.update()

    def set_texts(self, title: str, subtitle: str) -> None:
        self.title_label.setText(title)
        self.sub_label.setText(subtitle)

    def set_starting(self, starting: bool) -> None:
        """投屏启动中：图标呼吸脉冲；启动完成后恢复。"""
        self._starting = starting
        if starting:
            self._pulse_timer.start()
        else:
            self._pulse_timer.stop()
            self._pulse_phase = 0.0
            self.icon_label.setPixmap(
                icons.pixmap(self._icon_name, "rgba(255,255,255,210)", 19, 2.0))

    def _pulse_tick(self) -> None:
        import math
        self._pulse_phase = (self._pulse_phase + 0.06) % (2 * math.pi)
        alpha = 120 + int(round(math.sin(self._pulse_phase) * 80))  # 40~200
        self.icon_label.setPixmap(
            icons.pixmap(self._icon_name, f"rgba(255,255,255,{alpha})", 19, 2.0))

    def enterEvent(self, event) -> None:
        self._hover_anim.stop()
        self._hover_anim.setStartValue(self._hover)
        self._hover_anim.setEndValue(1.0)
        self._hover_anim.start()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hover_anim.stop()
        self._hover_anim.setStartValue(self._hover)
        self._hover_anim.setEndValue(0.0)
        self._hover_anim.start()
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self._pressed = True
            self.update()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        was_pressed = self._pressed
        self._pressed = False
        self.update()
        if (was_pressed and event.button() == Qt.LeftButton
                and self.rect().contains(event.position().toPoint())):
            self.clicked.emit()
        super().mouseReleaseEvent(event)

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        # hover: rgba(255,255,255,14)；pressed: rgba(255,255,255,26)
        if self._pressed:
            alpha = 26
        else:
            alpha = int(round(14 * self._hover))
        if alpha > 0:
            path = QPainterPath()
            path.addRoundedRect(self.rect().adjusted(0, 0, -1, -1), 10, 10)
            p.fillPath(path, QColor(255, 255, 255, alpha))
        p.end()


class _Divider(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("divider")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFixedHeight(1)


class _Header(QWidget):
    """标题栏：应用标识 + 最小化 / 最大化 / 关闭。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(44)

        dot = QLabel(self)
        dot.setPixmap(icons.pixmap("cast", "rgba(255,255,255,180)", 15, 2.0))
        dot.setFixedSize(16, 16)
        dot.setScaledContents(True)

        brand = QLabel("DLNA 投屏播放器", self)
        brand.setObjectName("brand")

        self.btn_min = IconButton("minimize", 14, 30, "最小化", self)
        self.btn_max = IconButton("maximize", 13, 30, "最大化", self)
        self.btn_close = IconButton("close", 14, 30, "关闭", self)
        for btn in (self.btn_min, self.btn_max, self.btn_close):
            btn.setStyleSheet(
                "QToolButton{background:transparent;border:none;border-radius:7px;padding:0;}"
                "QToolButton:hover{background:rgba(255,255,255,26);}"
                "QToolButton:pressed{background:rgba(255,255,255,50);}")
        self.btn_close.setStyleSheet(
            "QToolButton{background:transparent;border:none;border-radius:7px;padding:0;}"
            "QToolButton:hover{background:rgba(232,62,62,210);}"
            "QToolButton:pressed{background:rgba(200,40,40,230);}")

        lay = QHBoxLayout(self)
        lay.setContentsMargins(18, 0, 10, 0)
        lay.setSpacing(9)
        lay.addWidget(dot)
        lay.addWidget(brand)
        lay.addStretch(1)
        lay.addWidget(self.btn_min)
        lay.addWidget(self.btn_max)
        lay.addWidget(self.btn_close)


class HomeWindow(FramelessMixin, QMainWindow):
    toggle_requested = Signal()
    mirror_requested = Signal()
    local_requested = Signal()
    device_name_changed = Signal(str)
    closed = Signal()

    def __init__(self, device_name: str, lan_ip: str, parent=None) -> None:
        super().__init__(parent)
        self._device_name = device_name
        self._lan_ip = lan_ip
        self._centered = False
        self._running = False

        self.setWindowTitle("DLNA 投屏播放器")
        self.setMinimumSize(560, 500)
        self.resize(640, 600)
        self.setup_frameless()

        central = QWidget(self)
        # 注意：不能给 central 设置无选择器的样式表，否则会覆盖所有子控件的背景
        central.setObjectName("central")
        self.setCentralWidget(central)
        self.add_drag_widget(central)  # 窗口任意空白处都能拖动

        self.header = _Header(self)
        self.header.btn_min.clicked.connect(self.showMinimized)
        self.header.btn_max.clicked.connect(self.toggle_maximized)
        self.header.btn_close.clicked.connect(self.close)
        self.add_drag_widget(self.header)

        self.name_label = _ClickableLabel(device_name)
        self.name_label.setObjectName("device")
        self.name_label.setToolTip("点击修改设备名")
        self.name_label.clicked.connect(self._edit_device_name)

        self.meta_label = QLabel("未启动")
        self.meta_label.setObjectName("meta")

        self.row_cast = _ModeRow("cast", "投屏播放", "把手机视频 App 投到这台电脑")
        self.row_mirror = _ModeRow("mirror", "屏幕镜像", "手机屏幕实时镜像到电脑")
        self.row_local = _ModeRow("play", "本地播放", "播放电脑上的视频文件")
        self.row_cast.clicked.connect(self._on_cast_clicked)
        self.row_mirror.clicked.connect(self.mirror_requested.emit)
        self.row_local.clicked.connect(self.local_requested.emit)

        hint = QLabel("手机与电脑需连接同一 Wi-Fi")
        hint.setObjectName("hint")

        lay = QVBoxLayout(central)
        lay.setContentsMargins(38, 30, 38, 26)
        lay.setSpacing(0)
        lay.addWidget(self.name_label)
        lay.addSpacing(8)
        lay.addWidget(self.meta_label)
        lay.addStretch(1)
        lay.addWidget(self.row_cast)
        lay.addWidget(_Divider())
        lay.addWidget(self.row_mirror)
        lay.addWidget(_Divider())
        lay.addWidget(self.row_local)
        lay.addStretch(1)
        lay.addWidget(hint)

        self.setStyleSheet(_QSS)
        self._set_status("idle")

        # 入场淡入动画
        self._enter_anim = QPropertyAnimation(self, b"windowOpacity", self)
        self._enter_anim.setDuration(260)
        self._enter_anim.setStartValue(0.0)
        self._enter_anim.setEndValue(1.0)
        self._enter_anim.setEasingCurve(QEasingCurve.OutCubic)
        self.setWindowOpacity(0.0)

    # ---------- 对外状态 ----------
    def set_device_name(self, name: str) -> None:
        self._device_name = name
        self.name_label.setText(name)

    def set_running(self, running: bool) -> None:
        self._running = running
        self.row_cast.setEnabled(True)
        self.row_cast.set_starting(False)  # 启动完成/停止后都停掉脉冲
        if running:
            self.row_cast.set_texts("停止投屏", f"投屏服务运行中 · {self._lan_ip}")
        else:
            self.row_cast.set_texts("投屏播放", "把手机视频 App 投到这台电脑")
        self._set_status("ready" if running else "idle")

    def _set_status(self, state: str) -> None:
        if state == "ready":
            self.meta_label.setText(f"投屏服务运行中 · {self._lan_ip}")
        else:
            self.meta_label.setText(f"未启动 · {self._lan_ip}")

    # ---------- 交互 ----------
    def _on_cast_clicked(self) -> None:
        self.row_cast.setEnabled(False)
        self.row_cast.set_starting(True)
        self.toggle_requested.emit()

    def _edit_device_name(self) -> None:
        name, ok = QInputDialog.getText(
            self, "修改设备名", "手机 APP 中显示的设备名：", text=self._device_name)
        if ok and name.strip() and name.strip() != self._device_name:
            self.set_device_name(name.strip())
            self.device_name_changed.emit(self._device_name)

    # ---------- 无边框钩子 ----------
    def on_drag_area_double_click(self) -> bool:
        self.toggle_maximized()
        return True

    def on_maximized_changed(self, maximized: bool) -> None:
        self.header.btn_max.set_icon_name("restore" if maximized else "maximize")
        self.header.btn_max.setToolTip("向下还原" if maximized else "最大化")

    # ---------- 窗口 ----------
    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._centered:
            self._centered = True
            screen = QGuiApplication.primaryScreen()
            if screen is not None:
                geo = screen.availableGeometry()
                self.move(geo.center().x() - self.width() // 2,
                          geo.center().y() - self.height() // 2)
        # 每次显示都淡入一次（从最小化恢复、首次显示等）
        if self._enter_anim.state() != QPropertyAnimation.Running:
            self.setWindowOpacity(0.0)
            self._enter_anim.start()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.header.setGeometry(0, 0, self.width(), 44)

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        grad = QLinearGradient(0, 0, 0, self.height())
        grad.setColorAt(0.0, QColor("#141416"))
        grad.setColorAt(0.45, QColor("#0C0C0E"))
        grad.setColorAt(1.0, QColor("#09090A"))
        p.fillRect(self.rect(), grad)
        halo = QRadialGradient(self.width() / 2, -120, self.width() * 0.75)
        halo.setColorAt(0.0, QColor(255, 255, 255, 24))
        halo.setColorAt(1.0, QColor(255, 255, 255, 0))
        p.fillRect(self.rect(), halo)

    def closeEvent(self, event) -> None:
        self.reset_frameless()
        self.closed.emit()
        super().closeEvent(event)
