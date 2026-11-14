"""投屏监视窗口：无边框沉浸式播放器。

- 无投屏时：深色渐变等待页（设备名 + 等待手机投屏提示），不是纯黑
- 投屏后：mpv 画面铺满窗口，顶栏/控制栏悬浮渐显，3s 无操作隐藏
- 顶栏含最小化/关闭；关闭本窗口即结束投屏监视并回到主界面
- 边缘 8 向拉伸、顶栏/视频区拖动、双击切换全屏（FramelessMixin）
"""

from pathlib import Path

from PySide6.QtCore import QEvent, QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QRadialGradient
from PySide6.QtWidgets import (
    QAbstractSpinBox, QApplication, QLabel, QLineEdit, QMainWindow, QVBoxLayout, QWidget,
)

import config
from player.mpv_widget import MpvGLWidget
from ui import icons
from ui.control_overlay import (
    ControlBar, FadeMixin, HelpPanel, IntroPanel, ListPanel, Toast, TopBar,
)
from ui.frameless import FramelessMixin
from ui.loading import PulseHalo, Spinner
from upnp.state import RendererState

SEEK_STEP = 5      # 方向键快进/快退步长（秒）


class IdlePage(QWidget, FadeMixin):
    """等待投屏页：渐变背景 + 设备信息。"""

    def __init__(self, device_name: str, lan_ip: str, parent=None) -> None:
        super().__init__(parent)
        self.setup_fade()

        self.icon_label = QLabel()
        self.icon_label.setPixmap(icons.pixmap("cast", "rgba(255,255,255,190)", 64, 2.0))
        self.icon_label.setAlignment(Qt.AlignCenter)

        self.name_label = QLabel(device_name, self)
        self.name_label.setAlignment(Qt.AlignCenter)
        self.name_label.setStyleSheet(
            "QLabel{color:#FFFFFF;font-size:20px;font-weight:600;background:transparent;}")

        self.wait_label = QLabel("等待手机投屏…", self)
        self.wait_label.setAlignment(Qt.AlignCenter)
        self.wait_label.setStyleSheet(
            "QLabel{color:rgba(255,255,255,175);font-size:14px;background:transparent;}")

        hint = QLabel("在手机视频 APP 的播放界面点击「投屏 / TV」图标，\n选择上方这个设备即可开始播放", self)
        hint.setAlignment(Qt.AlignCenter)
        hint.setStyleSheet(
            "QLabel{color:rgba(255,255,255,110);font-size:12px;background:transparent;line-height:160%;}")

        self.ip_label = QLabel(f"局域网地址  {lan_ip}", self)
        self.ip_label.setAlignment(Qt.AlignCenter)
        self.ip_label.setStyleSheet(
            "QLabel{color:rgba(255,255,255,80);font-size:12px;background:transparent;}")

        lay = QVBoxLayout(self)
        lay.setSpacing(14)
        lay.addStretch(2)

        # 图标 + 呼吸光晕
        self._halo_box = QWidget(self)
        self._halo_box.setFixedSize(200, 200)
        self._halo = PulseHalo(self._halo_box, color=(120, 170, 255))
        self._halo.setGeometry(0, 0, 200, 200)
        self.icon_label.setParent(self._halo_box)
        self.icon_label.setGeometry(0, 0, 200, 200)
        lay.addWidget(self._halo_box, 0, Qt.AlignHCenter)

        lay.addWidget(self.name_label)
        lay.addWidget(self.wait_label)
        lay.addSpacing(12)
        lay.addWidget(hint)
        lay.addWidget(self.ip_label)
        lay.addStretch(3)

    def start_pulse(self) -> None:
        self._halo.start()

    def stop_pulse(self) -> None:
        self._halo.stop()

    def set_device_name(self, name: str) -> None:
        self.name_label.setText(name)

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        grad = QLinearGradient(0, 0, 0, self.height())
        grad.setColorAt(0.0, QColor("#171B24"))
        grad.setColorAt(1.0, QColor("#0A0C10"))
        p.fillRect(self.rect(), grad)
        halo = QRadialGradient(self.width() / 2, self.height() * 0.42, max(self.width(), self.height()) * 0.45)
        halo.setColorAt(0.0, QColor(59, 130, 246, 42))
        halo.setColorAt(1.0, QColor(59, 130, 246, 0))
        p.fillRect(self.rect(), halo)


class MainWindow(FramelessMixin, QMainWindow):
    """投屏监视窗口。关闭时发出 closed，由上层停止服务并回到主界面。"""

    closed = Signal()

    def __init__(self, controller, bridge, state, device, settings=None, parent=None) -> None:
        super().__init__(parent)
        self.controller = controller
        self.bridge = bridge
        self.state = state
        self.device = device
        self.settings = settings if settings is not None else {}

        self._video_active = False      # 是否正在播放（投屏或本地）
        self._local_playing = False     # 当前播放是否来自本地文件
        self._controls_visible = True
        self._wid_mode = False
        self._intro_skip = max(0, int(self.settings.get("intro_skip", 0)))
        self._intro_pending = False     # 当前这部片子还没跳过片头

        self.setWindowTitle("正在投屏 · DLNA")
        self.setMinimumSize(800, 450)
        self.resize(1120, 630)
        self.setStyleSheet("QMainWindow{background:#000000;}")

        # ---------- mpv 渲染层 ----------
        self.mpv_widget = MpvGLWidget(controller, self)
        self.setCentralWidget(self.mpv_widget)
        self.mpv_widget.render_failed.connect(self._on_render_failed)

        # 无边框交互：视频画面即拖动区
        self.setup_frameless(drag_widgets=[self.mpv_widget])
        # ---------- 悬浮层 ----------
        self.top_bar = TopBar(self)
        self.control_bar = ControlBar(self)
        self.idle_page = IdlePage(device.name, device.ip, self)
        self.toast = Toast(self)
        # 控制栏浮层：画面比例 / 倍速 / 跳过片头 / 使用说明
        self.fit_panel = ListPanel(ControlBar.FIT_ITEMS, self)
        self.speed_panel = ListPanel(ControlBar.SPEED_ITEMS, self)
        self.intro_panel = IntroPanel(self._intro_skip, self)
        self.help_panel = HelpPanel(self)
        self._panels = [
            (self.fit_panel, self.control_bar.btn_aspect),
            (self.speed_panel, self.control_bar.btn_speed),
            (self.intro_panel, self.control_bar.btn_intro),
            (self.help_panel, self.control_bar.btn_help),
        ]
        for panel, _anchor in self._panels:
            panel.hide()
        for w in (self.top_bar, self.control_bar, self.idle_page, self.toast):
            w.raise_()
        # 等待页铺满窗口，也允许在其上拖动窗口
        self.add_drag_widget(self.idle_page)
        self.add_drag_widget(self.top_bar)

        # ---------- 加载指示（收到投屏到出画面之间） ----------
        self.loading = Spinner(34, self)
        self.loading_label = QLabel("正在载入…", self)
        self.loading_label.setAlignment(Qt.AlignCenter)
        self.loading_label.setStyleSheet(
            "QLabel{color:rgba(255,255,255,130);font-size:12px;background:transparent;}")
        self.loading_label.hide()
        for w in (self.loading, self.loading_label):
            w.setAttribute(Qt.WA_TransparentForMouseEvents, True)

        # ---------- 定时器 ----------
        self.position_timer = QTimer(self)
        self.position_timer.setInterval(250)
        self.position_timer.timeout.connect(self._update_position)

        self.hide_timer = QTimer(self)
        self.hide_timer.setSingleShot(True)
        self.hide_timer.timeout.connect(self._hide_controls)

        # 分辨率 / 帧率：由 mpv 属性观察回调经 bridge 信号推送，不做轮询
        self.bridge.video_info_changed.connect(self.top_bar.info_label.setText)

        # ---------- 信号连接 ----------
        self.bridge.cast_received.connect(self._on_cast_received)
        self.bridge.media_ended.connect(self._on_media_ended)
        self.bridge.transport_changed.connect(self._on_transport_changed)
        self.bridge.volume_changed.connect(self.control_bar.set_volume)
        self.bridge.title_changed.connect(self._on_title_changed)
        self.bridge.toast.connect(self.toast.show_message)

        self.top_bar.minimize_clicked.connect(self.showMinimized)
        self.top_bar.maximize_clicked.connect(self.toggle_maximized)
        self.top_bar.close_clicked.connect(self.close)

        self.control_bar.play_toggled.connect(self.controller.toggle_play)
        self.control_bar.mute_toggled.connect(self._toggle_mute)
        self.control_bar.volume_changed.connect(self.controller.set_volume)
        self.control_bar.disconnect_clicked.connect(self._disconnect_cast)
        self.control_bar.fullscreen_clicked.connect(self.toggle_fullscreen)
        self.control_bar.fit_mode_changed.connect(self.controller.set_fit_mode)
        self.control_bar.fit_menu_requested.connect(
            lambda: self._toggle_panel(self.fit_panel))
        self.control_bar.speed_menu_requested.connect(
            lambda: self._toggle_panel(self.speed_panel))
        self.control_bar.intro_requested.connect(
            lambda: self._toggle_panel(self.intro_panel))
        self.control_bar.help_requested.connect(
            lambda: self._toggle_panel(self.help_panel))
        self.fit_panel.item_selected.connect(self._on_fit_selected)
        self.speed_panel.item_selected.connect(self._on_speed_selected)
        self.intro_panel.seconds_changed.connect(self._on_intro_changed)
        # 默认「铺满（裁掉黑边）」：等比放大到无黑边，不变形
        self.controller.set_fit_mode(self.control_bar.fit_mode())
        self.controller.set_speed(float(self.control_bar.speed()))
        self.control_bar.seek.sliderPressed.connect(
            lambda: setattr(self.control_bar.seek, "is_dragging", True))
        self.control_bar.seek.sliderReleased.connect(self._commit_seek)
        self.control_bar.seek.jump_requested.connect(self._jump_seek)
        self.control_bar.volume.valueChanged.connect(self.controller.set_volume)
        self.control_bar.volume.jump_requested.connect(self.controller.set_volume)
        # 滑块初值必须与播放内核当前音量一致（否则会显示 0 但实际有声）
        self.control_bar.set_volume(self.state.get_volume())

        # 键盘快捷键走应用级过滤：焦点可能停在任意子控件上，窗口级 keyPressEvent 收不到
        QApplication.instance().installEventFilter(self)

        self._enter_idle(initial=True)

    # ---------- FramelessMixin 钩子 ----------
    def on_drag_area_move(self) -> None:
        if self._video_active:
            self._show_controls_temporary()

    def on_drag_area_double_click(self) -> bool:
        self.toggle_fullscreen()
        return True

    def on_drag_area_click(self, global_pos: QPoint) -> None:
        """单击画面：播放 / 暂停（控制栏、顶栏区域点击不算）。"""
        if not self._video_active:
            return
        if any(p.isVisible() for p, _a in self._panels):
            self._hide_panels()
            return
        pos = self.mapFromGlobal(global_pos)
        if pos.y() < 48 or pos.y() > self.height() - self.control_bar.height():
            return
        self.controller.toggle_play()
        self._show_controls_temporary()

    # ---------- 页面切换 ----------
    def _enter_idle(self, initial: bool = False) -> None:
        self._video_active = False
        self._local_playing = False
        self.position_timer.stop()
        self.idle_page.set_device_name(self.device.name)
        self.idle_page.show()
        self.idle_page.raise_()
        self.idle_page.fade_in()
        self.idle_page.start_pulse()
        self._hide_loading()
        # 等待页铺满窗口，必须重新把顶栏/提示提到最上层，否则看不到最小化/关闭
        self.top_bar.raise_()
        self.toast.raise_()
        self.top_bar.set_status("等待投屏")
        self.top_bar.status_icon.setPixmap(icons.pixmap("tv", "#FFFFFF", 16, 2.0))
        self.control_bar.hide()
        self._hide_panels()
        if initial:
            self._show_controls_permanent()
        if not self._wid_mode:
            self.top_bar.fade_in()
        self.hide_timer.stop()

    def _enter_playing(self) -> None:
        self._video_active = True
        # 清掉残留焦点：否则空格会触发最后一次点过的按钮（例如最小化）
        focused = QApplication.focusWidget()
        if focused is not None and focused is not self.intro_panel.input:
            focused.clearFocus()
        self.idle_page.fade_out()
        self.idle_page.stop_pulse()
        QTimer.singleShot(260, self._hide_idle_page)
        self.control_bar.show()
        self.control_bar.raise_()
        self.top_bar.raise_()
        self.toast.raise_()
        self._show_loading()
        self.position_timer.start()
        self._show_controls_temporary()

    def _hide_idle_page(self) -> None:
        if self._video_active:
            self.idle_page.hide()

    # ---------- 加载指示 ----------
    def _show_loading(self) -> None:
        self._place_loading()
        self.loading.start()
        self.loading_label.show()
        self.loading.raise_()
        self.loading_label.raise_()
        QTimer.singleShot(0, self._raise_loading)
        # 兜底：超过 12s 还没出画面就不挡着了
        QTimer.singleShot(12000, self._hide_loading)

    def _raise_loading(self) -> None:
        if self.loading.isVisible():
            self.loading.raise_()
        if self.loading_label.isVisible():
            self.loading_label.raise_()

    def _hide_loading(self) -> None:
        self.loading.stop()
        self.loading_label.hide()

    def _place_loading(self) -> None:
        w, h = self.width(), self.height()
        self.loading.move((w - self.loading.width()) // 2, h // 2 - 30)
        self.loading_label.setGeometry(0, h // 2 + 8, w, 20)

    # ---------- 事件处理 ----------
    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        w, h = self.width(), self.height()
        self.top_bar.setGeometry(0, 0, w, 44)
        self.control_bar.setGeometry(0, h - 58, w, 58)
        self.idle_page.setGeometry(0, 0, w, h)
        self.toast.move((w - self.toast.width()) // 2, 56)
        if hasattr(self, "loading"):
            self._place_loading()
        for panel, anchor in getattr(self, "_panels", ()):
            if panel.isVisible():
                self._place_panel(panel, anchor)

    def _handle_shortcut(self, event) -> bool:
        """播放快捷键；已处理返回 True。"""
        if not self._video_active or event.isAutoRepeat():
            return False
        # 正在输入数字时不要抢键盘
        if isinstance(QApplication.focusWidget(), (QLineEdit, QAbstractSpinBox)):
            return False
        key = event.key()
        if key == Qt.Key_Space:
            self.controller.toggle_play()
            self._show_controls_temporary()
            return True
        if key in (Qt.Key_Left, Qt.Key_Right):
            step = -SEEK_STEP if key == Qt.Key_Left else SEEK_STEP
            self.controller.seek_relative(step)
            self.toast.show_message(
                f"快退 {SEEK_STEP} 秒" if step < 0 else f"快进 {SEEK_STEP} 秒", 1100)
            self._show_controls_temporary()
            return True
        return False

    def eventFilter(self, obj, event) -> bool:
        # 焦点可能在任何子控件上，窗口级 keyPressEvent 不一定收得到，这里统一拦截
        if event.type() == QEvent.Type.KeyPress and self.isActiveWindow():
            if self._handle_shortcut(event):
                return True
        return super().eventFilter(obj, event)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Escape and self.isFullScreen():
            self.toggle_fullscreen()
            return
        if self._handle_shortcut(event):
            return
        super().keyPressEvent(event)

    # ---------- 控制栏浮层 ----------
    def _place_panel(self, panel: QWidget, anchor: QWidget) -> None:
        panel.adjustSize()
        # 控制栏刚显示时布局可能还没生效，先强制排一次，否则锚点坐标是 0
        if self.control_bar.layout() is not None:
            self.control_bar.layout().activate()
        pos = anchor.mapTo(self, QPoint(0, 0))
        x = pos.x() + anchor.width() // 2 - panel.width() // 2
        x = max(10, min(x, self.width() - panel.width() - 10))
        y = self.height() - self.control_bar.height() - panel.height() - 10
        panel.move(x, max(48, y))

    def _toggle_panel(self, panel: QWidget) -> None:
        if panel.isVisible():
            panel.hide()
            return
        self._hide_panels(keep=panel)
        # 先更新内容再量尺寸，否则带勾选前缀的选项会被截断
        if panel is self.fit_panel:
            self.fit_panel.set_current(self.control_bar.fit_mode())
        elif panel is self.speed_panel:
            self.speed_panel.set_current(self.control_bar.speed())
        elif panel is self.intro_panel:
            self.intro_panel.set_seconds(self._intro_skip)
        panel.adjustSize()
        for p, anchor in self._panels:
            if p is panel:
                self._place_panel(p, anchor)
                break
        panel.show()
        panel.raise_()
        if panel is self.intro_panel:
            self.intro_panel.focus_input()
        # 视频是 QOpenGLWidget，合成后可能盖住同级控件；下一轮事件循环再提一次
        QTimer.singleShot(0, panel.raise_)
        # 操作浮层期间不要把控制栏收起来
        self.hide_timer.stop()

    def _hide_panels(self, keep: QWidget | None = None) -> None:
        for p, _anchor in self._panels:
            if p is not keep and p.isVisible():
                p.hide()

    def _on_fit_selected(self, mode: str) -> None:
        self.fit_panel.hide()
        self.control_bar.set_fit_mode(mode)
        self._show_controls_temporary()

    def _on_speed_selected(self, value: str) -> None:
        self.speed_panel.hide()
        speed = float(value)
        self.controller.set_speed(speed)
        self.control_bar.set_speed(speed)
        self.toast.show_message(f"播放速度 {speed:g}×", 1400)
        self._show_controls_temporary()

    def _on_intro_changed(self, seconds: int) -> None:
        self.intro_panel.hide()
        self._intro_skip = max(0, int(seconds))
        self.settings["intro_skip"] = self._intro_skip
        config.save(self.settings)
        if self._intro_skip:
            self.toast.show_message(f"已设置：每次播放从第 {self._intro_skip} 秒开始", 2200)
        else:
            self.toast.show_message("已关闭跳过片头", 1600)
        self._show_controls_temporary()

    # ---------- 控制栏显隐 ----------
    def _show_controls_temporary(self) -> None:
        if self._wid_mode:
            return
        if not self._controls_visible:
            self._controls_visible = True
            self.top_bar.fade_in()
            self.control_bar.fade_in()
        self.hide_timer.start(3000)

    def _show_controls_permanent(self) -> None:
        self._controls_visible = True
        self.top_bar.fade_in()
        if self.control_bar.isVisible():
            self.control_bar.fade_in()

    def _hide_controls(self) -> None:
        if any(p.isVisible() for p, _a in self._panels):
            self.hide_timer.start(3000)
            return
        if not self._video_active or self._wid_mode:
            return
        self._controls_visible = False
        self.top_bar.fade_out()
        self.control_bar.fade_out()

    # ---------- 播放状态 ----------
    def play_local(self, path: str) -> None:
        """本地文件播放：直接载入文件，与手机推送走同一条播放链路。"""
        self._local_playing = True
        self.controller.load(Path(path).as_uri(), None, Path(path).stem)

    def _update_position(self) -> None:
        pos, dur = self.state.get_progress()
        self.control_bar.set_progress(pos, dur)

    def _status_prefix(self) -> str:
        return "正在播放" if self._local_playing else "正在投屏"

    def _on_cast_received(self, url: str, title: str) -> None:
        self._enter_playing()
        self.control_bar.set_progress(0, 0)
        # 换片（含连播）后重新跳过片头
        self._intro_pending = self._intro_skip > 0
        if title:
            self.toast.show_message(f"{'正在播放' if self._local_playing else '收到投屏'}：{title}")
            self.top_bar.set_status(f"{self._status_prefix()}：{title}")
        # 智能去黑边依赖真实画面，换片后延迟重新侦测
        if self.control_bar.fit_mode() == "smart":
            QTimer.singleShot(1500, lambda: self.controller.set_fit_mode("smart"))

    def _on_title_changed(self, title: str) -> None:
        if title:
            self.top_bar.set_status(f"{self._status_prefix()}：{title}")

    def _on_transport_changed(self, st: str) -> None:
        self.control_bar.set_playing(st == RendererState.PLAYING)
        if st == RendererState.PLAYING:
            # 画面/声音已起，撤掉加载指示
            self._hide_loading()
        if st == RendererState.PLAYING and self._intro_pending:
            self._intro_pending = False
            self.controller.seek_absolute(self._intro_skip)
            self.toast.show_message(f"已跳过片头 {self._intro_skip} 秒", 1600)

    def _on_media_ended(self) -> None:
        self._enter_idle()
        self.toast.show_message("投屏已结束")

    def _commit_seek(self) -> None:
        self.control_bar.seek.is_dragging = False
        pos, dur = self.state.get_progress()
        if dur > 0:
            val = self.control_bar.seek.value()
            self.controller.seek_absolute(val / 10000.0 * dur)

    def _jump_seek(self, val: int) -> None:
        _pos, dur = self.state.get_progress()
        if dur > 0:
            self.control_bar.seek.is_dragging = False
            self.controller.seek_absolute(val / 10000.0 * dur)

    def _toggle_mute(self) -> None:
        muted = bool(self.controller.mpv.mute)
        self.controller.mpv.mute = not muted
        self.control_bar.set_muted(not muted)

    def _disconnect_cast(self) -> None:
        self.controller.stop()

    def toggle_fullscreen(self) -> None:
        self._hide_panels()
        if self.isFullScreen():
            self.showNormal()
            self.control_bar.btn_fullscreen.set_icon_name("fullscreen")
        else:
            self.showFullScreen()
            self.control_bar.btn_fullscreen.set_icon_name("fullscreen_exit")
        self._show_controls_temporary()

    def on_maximized_changed(self, maximized: bool) -> None:
        self.top_bar.set_maximized(maximized)

    # ---------- render API 失败降级 ----------
    def _on_render_failed(self, reason: str) -> None:
        print(f"[mpv] render API 初始化失败({reason})，降级为 wid 嵌入模式")
        self._wid_mode = True
        hwnd = int(self.mpv_widget.winId())
        self.controller.reinit_with_wid(hwnd)
        self.controller.set_fit_mode(self.control_bar.fit_mode())
        self.controller.set_speed(float(self.control_bar.speed()))
        for w in (self.idle_page, self.top_bar, self.control_bar, self.toast):
            w.setAttribute(Qt.WA_NativeWindow, True)
            w.raise_()
        self.hide_timer.stop()

    # ---------- 关闭 ----------
    def closeEvent(self, event) -> None:
        self.position_timer.stop()
        self.hide_timer.stop()
        self.idle_page.stop_pulse()
        self._hide_loading()
        self.reset_frameless()
        try:
            self.mpv_widget.shutdown()
        except Exception:
            pass
        self.closed.emit()
        super().closeEvent(event)
