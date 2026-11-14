"""沉浸式播放器悬浮控件。

- TopBar：顶部信息栏（投屏状态/标题 + 最小化/关闭），渐变底
- ControlBar：底部控制栏（播放/进度/音量/断开投屏/全屏），渐变底
- Toast：居中提示气泡
- 均支持 QGraphicsOpacityEffect 渐显渐隐
"""

import math

from PySide6.QtCore import (
    QSize, Qt, QTimer, QPropertyAnimation, QEasingCurve, Signal,
)
from PySide6.QtGui import QColor, QIntValidator, QPainter, QLinearGradient
from PySide6.QtWidgets import (
    QGraphicsOpacityEffect, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSlider,
    QStyle, QToolButton, QVBoxLayout, QWidget,
)

from ui import icons


def _fmt_time(seconds: float) -> str:
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


class IconButton(QToolButton):
    def __init__(self, name: str, icon_size: int = 22, btn_size: int = 38,
                 tooltip: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._icon_name = name
        self._icon_size = icon_size
        self._color = "#FFFFFF"
        self.setFixedSize(btn_size, btn_size)
        self.setIconSize(QSize(icon_size, icon_size))
        self.setToolTip(tooltip)
        self.setCursor(Qt.PointingHandCursor)
        self._apply_icon()
        self.setStyleSheet(
            "QToolButton{background:transparent;border:none;border-radius:16px;padding:0;}"
            "QToolButton:hover{background:rgba(255,255,255,32);}"
            "QToolButton:pressed{background:rgba(255,255,255,64);}"
        )

    def _apply_icon(self) -> None:
        self.setIcon(icons.icon(self._icon_name, self._color, self._icon_size))

    def set_icon_name(self, name: str) -> None:
        if name != self._icon_name:
            self._icon_name = name
            self._apply_icon()

    def set_icon_color(self, color: str) -> None:
        self._color = color
        self._apply_icon()


class FadeMixin:
    """为悬浮控件提供渐显渐隐能力（QGraphicsOpacityEffect）。"""

    def setup_fade(self) -> None:
        self._fade_effect = QGraphicsOpacityEffect(self)
        self._fade_effect.setOpacity(1.0)
        self.setGraphicsEffect(self._fade_effect)
        self._fade_anim = QPropertyAnimation(self._fade_effect, b"opacity", self)
        self._fade_anim.setDuration(220)
        self._fade_anim.setEasingCurve(QEasingCurve.OutCubic)

    def fade_to(self, opacity: float) -> None:
        self._fade_anim.stop()
        self._fade_anim.setStartValue(self._fade_effect.opacity())
        self._fade_anim.setEndValue(opacity)
        self._fade_anim.start()

    def fade_in(self) -> None:
        self.fade_to(1.0)

    def fade_out(self) -> None:
        self.fade_to(0.0)


class MediaSlider(QSlider):
    """点击跳转 + 精细拖拽的横向滑块（暗色细轨、白色圆点手柄）。"""

    jump_requested = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(Qt.Horizontal, parent)
        self.is_dragging = False
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet(
            "QSlider{background:transparent;}"
            "QSlider::groove:horizontal{height:4px;background:rgba(255,255,255,64);border-radius:2px;}"
            "QSlider::sub-page:horizontal{background:rgba(255,255,255,235);border-radius:2px;}"
            "QSlider::handle:horizontal{width:12px;height:12px;margin:-4px 0;background:#FFFFFF;"
            "border-radius:6px;}"
            "QSlider::handle:horizontal:hover{width:14px;height:14px;margin:-5px 0;}"
            "QSlider::add-page:horizontal,QSlider::sub-page:horizontal{border-radius:2px;}"
        )

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and self.maximum() > self.minimum():
            val = QStyle.sliderValueFromPosition(
                self.minimum(), self.maximum(),
                int(event.position().x()), max(1, self.width()))
            self.setValue(val)
            self.jump_requested.emit(val)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        super().mouseReleaseEvent(event)
        if not self.isSliderDown():
            self.is_dragging = False


class TopBar(QWidget, FadeMixin):
    minimize_clicked = Signal()
    maximize_clicked = Signal()
    close_clicked = Signal()

    def __init__(self, parent: QWidget | None = None, transparent: bool = False) -> None:
        super().__init__(parent)
        self.setup_fade()
        self.setFixedHeight(44)
        self.setAttribute(Qt.WA_StyledBackground, False)
        self._drag_pending = False
        self._transparent = transparent

        self.status_icon = QLabel(self)
        self.status_icon.setPixmap(icons.pixmap("cast", "#FFFFFF", 16, 2.0))
        self.status_icon.setFixedSize(20, 20)
        self.status_icon.setScaledContents(True)

        self.status_label = QLabel("待机中", self)
        self.status_label.setStyleSheet(
            "QLabel{color:rgba(255,255,255,230);font-size:13px;background:transparent;}")

        self.info_label = QLabel("", self)
        self.info_label.setStyleSheet(
            "QLabel{color:rgba(255,255,255,215);font-size:12px;"
            "font-weight:500;background:transparent;}")

        self.btn_min = IconButton("minimize", 18, 38, "最小化")
        self.btn_max = IconButton("maximize", 16, 38, "最大化")
        self.btn_close = IconButton("close", 18, 38, "关闭")
        self.btn_min.clicked.connect(self.minimize_clicked.emit)
        self.btn_max.clicked.connect(self.maximize_clicked.emit)
        self.btn_close.clicked.connect(self.close_clicked.emit)
        # 顶栏按钮一旦获得焦点，按空格会触发「最小化」而不是播放/暂停
        for btn in (self.btn_min, self.btn_max, self.btn_close):
            btn.setFocusPolicy(Qt.NoFocus)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 0, 8, 0)
        lay.setSpacing(8)
        lay.addWidget(self.status_icon)
        lay.addWidget(self.status_label)
        lay.addSpacing(6)
        lay.addWidget(self.info_label)
        lay.addStretch(1)
        lay.addWidget(self.btn_min)
        lay.addWidget(self.btn_max)
        lay.addWidget(self.btn_close)

    def set_status(self, text: str) -> None:
        self.status_label.setText(text)

    def set_maximized(self, maximized: bool) -> None:
        self.btn_max.set_icon_name("restore" if maximized else "maximize")
        self.btn_max.setToolTip("向下还原" if maximized else "最大化")

    def paintEvent(self, event) -> None:
        if self._transparent:
            return
        p = QPainter(self)
        grad = QLinearGradient(0, 0, 0, self.height())
        grad.setColorAt(0.0, QColor(0, 0, 0, 170))
        grad.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.fillRect(self.rect(), grad)


class TextButton(QToolButton):
    """文字按钮（如倍速 "1.0×"）。"""

    def __init__(self, text: str, tooltip: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setText(text)
        self.setToolTip(tooltip)
        self.setFixedSize(50, 32)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self.setStyleSheet(
            "QToolButton{background:rgba(255,255,255,20);border:none;border-radius:8px;"
            "color:#FFFFFF;font-size:12px;padding:0;}"
            "QToolButton:hover{background:rgba(255,255,255,40);}"
            "QToolButton:pressed{background:rgba(255,255,255,64);}")


_PANEL_QSS = (
    "QWidget#listPanel{background:rgba(26,26,28,244);"
    "border:1px solid rgba(255,255,255,32);border-radius:10px;}"
    "QWidget#panelRow{background:transparent;border-radius:6px;}"
    "QWidget#panelRow:hover{background:rgba(255,255,255,22);}"
    "QLabel{color:#EDEDEF;font-size:12px;background:transparent;}"
    "QLabel#panelTitle{color:#FFFFFF;font-size:13px;font-weight:500;}"
    "QLabel#panelDesc{color:rgba(255,255,255,120);font-size:11px;}"
    "QSpinBox{background:rgba(255,255,255,18);border:1px solid rgba(255,255,255,30);"
    "border-radius:6px;color:#FFFFFF;font-size:12px;padding:3px 6px;}"
    "QLineEdit{background:rgba(255,255,255,18);border:1px solid rgba(255,255,255,45);"
    "border-radius:6px;color:#FFFFFF;font-size:14px;padding:5px 8px;}"
    "QLineEdit:focus{border:1px solid rgba(255,255,255,160);"
    "background:rgba(255,255,255,30);}"
    "QPushButton{background:rgba(255,255,255,230);border:none;border-radius:6px;"
    "color:#111111;font-size:12px;padding:6px 14px;}"
    "QPushButton:hover{background:#FFFFFF;}"
    "QPushButton[chip=\"true\"]{background:rgba(255,255,255,18);color:#EDEDEF;"
    "border-radius:6px;padding:4px 9px;font-size:11px;}"
    "QPushButton[chip=\"true\"]:hover{background:rgba(255,255,255,42);}")


class ControlBar(QWidget, FadeMixin):
    play_toggled = Signal()
    seek_requested = Signal(float)      # 秒
    volume_changed = Signal(int)
    mute_toggled = Signal()
    disconnect_clicked = Signal()
    fullscreen_clicked = Signal()
    fit_mode_changed = Signal(str)      # original / crop / smart / stretch / 16:9 / 4:3
    fit_menu_requested = Signal()       # 请求打开画面比例浮层
    speed_menu_requested = Signal()     # 请求打开倍速浮层
    intro_requested = Signal()          # 请求打开跳过片头设置
    help_requested = Signal()           # 请求打开使用说明

    # (模式, 菜单文案)
    FIT_ITEMS = (
        ("original", "保持原比例"),
        ("crop", "铺满"),
        ("smart", "智能去黑边"),
        ("stretch", "拉伸满屏"),
        ("16:9", "强制 16:9"),
        ("4:3", "强制 4:3"),
    )

    # (倍速值, 菜单文案)
    SPEED_ITEMS = (
        ("0.5", "0.5×"),
        ("0.75", "0.75×"),
        ("1.0", "1.0×"),
        ("1.25", "1.25×"),
        ("1.5", "1.5×"),
        ("2.0", "2.0×"),
    )

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setup_fade()
        self.setFixedHeight(58)
        self._fit_mode = "crop"
        self._speed = "1.0"

        self.btn_play = IconButton("play", 24, 42, "播放/暂停")
        self.btn_play.clicked.connect(self.play_toggled.emit)

        self.time_label = QLabel("00:00", self)
        self.time_label.setStyleSheet(
            "QLabel{color:rgba(255,255,255,200);font-size:12px;background:transparent;}")

        self.seek = MediaSlider(self)
        self.seek.setRange(0, 10000)

        self.dur_label = QLabel("00:00", self)
        self.dur_label.setStyleSheet(
            "QLabel{color:rgba(255,255,255,200);font-size:12px;background:transparent;}")

        self.btn_mute = IconButton("volume", 20, 38, "静音")
        self.btn_mute.clicked.connect(self.mute_toggled.emit)

        self.volume = MediaSlider(self)
        self.volume.setFixedWidth(96)
        self.volume.setRange(0, 100)

        self.btn_speed = TextButton("1.0×", "播放速度")
        self.btn_speed.pressed.connect(self.speed_menu_requested.emit)

        self.btn_aspect = IconButton("aspect", 19, 38, "画面比例")
        self.btn_aspect.pressed.connect(self.fit_menu_requested.emit)

        self.btn_intro = IconButton("skip_intro", 19, 38, "跳过片头（设置起始秒数）")
        self.btn_intro.pressed.connect(self.intro_requested.emit)

        self.btn_help = IconButton("help", 18, 38, "使用说明")
        self.btn_help.pressed.connect(self.help_requested.emit)

        self.btn_disconnect = IconButton("cast_off", 20, 38, "断开投屏")
        self.btn_disconnect.clicked.connect(self.disconnect_clicked.emit)

        self.btn_fullscreen = IconButton("fullscreen", 20, 38, "全屏")
        self.btn_fullscreen.clicked.connect(self.fullscreen_clicked.emit)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 6, 12, 8)
        lay.setSpacing(9)
        lay.addWidget(self.btn_play)
        lay.addWidget(self.time_label)
        lay.addWidget(self.seek, 1)
        lay.addWidget(self.dur_label)
        lay.addWidget(self.btn_mute)
        lay.addWidget(self.volume)
        lay.addWidget(self.btn_speed)
        lay.addWidget(self.btn_aspect)
        lay.addWidget(self.btn_intro)
        lay.addWidget(self.btn_help)
        lay.addWidget(self.btn_disconnect)
        lay.addWidget(self.btn_fullscreen)

        # 键盘方向键用于快进快退，按钮/滑块不能抢焦点
        for w in (self.btn_play, self.btn_mute, self.btn_disconnect, self.btn_aspect,
                  self.btn_speed, self.btn_intro, self.btn_help, self.btn_fullscreen,
                  self.seek, self.volume):
            w.setFocusPolicy(Qt.NoFocus)

    # ---- 画面比例 ----
    def set_fit_mode(self, mode: str) -> None:
        self._fit_mode = mode
        self.fit_mode_changed.emit(mode)

    def fit_mode(self) -> str:
        return self._fit_mode

    # ---- 倍速 ----
    @staticmethod
    def _fmt_speed(speed: float) -> str:
        return f"{speed:.1f}" if abs(speed - round(speed)) < 1e-6 else f"{speed:g}"

    def set_speed(self, speed: float) -> None:
        self._speed = self._fmt_speed(float(speed))
        self.btn_speed.setText(f"{self._speed}×")

    def speed(self) -> str:
        return self._speed

    # ---- 状态更新 ----
    def set_playing(self, playing: bool) -> None:
        self.btn_play.set_icon_name("pause" if playing else "play")

    def set_progress(self, pos: float, dur: float) -> None:
        if not self.seek.is_dragging:
            if dur > 0:
                self.seek.blockSignals(True)
                self.seek.setValue(int(pos / dur * 10000))
                self.seek.blockSignals(False)
            else:
                self.seek.blockSignals(True)
                self.seek.setValue(0)
                self.seek.blockSignals(False)
        self.time_label.setText(_fmt_time(pos))
        if dur > 0:
            self.dur_label.setText(_fmt_time(dur))

    def set_muted(self, muted: bool) -> None:
        self.btn_mute.set_icon_name("volume_mute" if muted else "volume")

    def set_volume(self, v: int) -> None:
        self.volume.blockSignals(True)
        self.volume.setValue(v)
        self.volume.blockSignals(False)

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        grad = QLinearGradient(0, 0, 0, self.height())
        grad.setColorAt(0.0, QColor(0, 0, 0, 0))
        grad.setColorAt(1.0, QColor(0, 0, 0, 190))
        p.fillRect(self.rect(), grad)


class _PanelRow(QWidget):
    """浮层里的一行选项。"""

    clicked = Signal()

    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("panelRow")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setAttribute(Qt.WA_Hover, True)
        self.setCursor(Qt.PointingHandCursor)
        self.label = QLabel(text, self)
        self.label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 7, 12, 7)
        lay.addWidget(self.label)

    def set_text(self, text: str) -> None:
        self.label.setText(text)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and self.rect().contains(
                event.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class ListPanel(QWidget, FadeMixin):
    """选项浮层（画面比例 / 倍速共用）。

    作为播放窗口的子控件显示，而不是弹出式 QMenu：全屏窗口上
    原生弹出菜单在部分环境下不会显示，子控件则一定随窗口一起绘制。

    注意：必须挂 QGraphicsOpacityEffect（setup_fade）——渲染用的
    QOpenGLWidget 会盖住普通兄弟控件，带透明度特效的控件由窗口合成
    器单独绘制，才能悬浮在视频画面之上。
    """

    item_selected = Signal(str)

    def __init__(self, items, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setup_fade()
        # 原生子窗口：否则视频（QOpenGLWidget）合成时会把这个同级控件盖住
        self.setAttribute(Qt.WA_NativeWindow, True)
        self.setObjectName("listPanel")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(_PANEL_QSS)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(1)
        self._rows: dict[str, tuple[_PanelRow, str]] = {}
        for key, text in items:
            row = _PanelRow(text, self)
            row.clicked.connect(lambda k=key: self.item_selected.emit(k))
            lay.addWidget(row)
            self._rows[key] = (row, text)

    def set_current(self, key: str) -> None:
        for k, (row, text) in self._rows.items():
            # 用前缀标记代替勾选框，深色背景下更清晰
            row.set_text(("✓  " if k == key else "    ") + text)
        # 前缀会改变文本宽度，重算一次否则最后一项会被截断
        self.adjustSize()


class _NumberEdit(QLineEdit):
    """数字输入框：点一下就全选，直接输入即可替换旧值。"""

    def mouseReleaseEvent(self, event) -> None:
        super().mouseReleaseEvent(event)
        if event.button() == Qt.LeftButton:
            self.selectAll()


class IntroPanel(QWidget, FadeMixin):
    """跳过片头设置：设定每次开始播放时的起始秒数。"""

    seconds_changed = Signal(int)

    def __init__(self, seconds: int = 0, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setup_fade()
        self.setAttribute(Qt.WA_NativeWindow, True)
        self.setObjectName("listPanel")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(_PANEL_QSS)
        self.setMinimumWidth(300)

        title = QLabel("跳过片头", self)
        title.setObjectName("panelTitle")

        # 用 QLineEdit 而非 QSpinBox：数字键盘输入更直接，也不受按钮符号干扰
        self.input = _NumberEdit(self)
        self.input.setValidator(QIntValidator(0, 3600, self))
        self.input.setAlignment(Qt.AlignCenter)
        self.input.setFocusPolicy(Qt.StrongFocus)
        self.input.setPlaceholderText("0")
        self.input.setMinimumWidth(170)
        self.input.returnPressed.connect(self._save)

        unit = QLabel("秒", self)
        unit.setStyleSheet("QLabel{color:rgba(255,255,255,170);font-size:12px;}")

        save = QPushButton("保存", self)
        save.setCursor(Qt.PointingHandCursor)
        save.setFocusPolicy(Qt.NoFocus)
        save.clicked.connect(self._save)

        line = QHBoxLayout()
        line.setContentsMargins(0, 0, 0, 0)
        line.setSpacing(8)
        line.addWidget(self.input, 1)
        line.addWidget(unit)
        line.addWidget(save)

        # 常用值快捷设置
        chips = QHBoxLayout()
        chips.setContentsMargins(0, 0, 0, 0)
        chips.setSpacing(6)
        for sec in (0, 5, 10, 15, 30, 60):
            chip = QPushButton("关闭" if sec == 0 else f"{sec}s", self)
            chip.setCursor(Qt.PointingHandCursor)
            chip.setFocusPolicy(Qt.NoFocus)
            chip.setProperty("chip", True)
            chip.clicked.connect(lambda _=False, s=sec: self._apply(s))
            chips.addWidget(chip)
        chips.addStretch(1)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 11, 12, 12)
        lay.setSpacing(9)
        lay.addWidget(title)
        lay.addLayout(line)
        lay.addLayout(chips)

        self.set_seconds(seconds)

    def set_seconds(self, seconds: int) -> None:
        self.input.setText(str(max(0, int(seconds))))

    def focus_input(self) -> None:
        self.input.setFocus(Qt.OtherFocusReason)
        self.input.selectAll()

    def _apply(self, seconds: int) -> None:
        self.set_seconds(seconds)
        self.seconds_changed.emit(int(seconds))

    def _save(self) -> None:
        text = self.input.text().strip()
        self.seconds_changed.emit(int(text) if text else 0)


class HelpPanel(QWidget, FadeMixin):
    """使用说明浮层。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setup_fade()
        self.setAttribute(Qt.WA_NativeWindow, True)
        self.setObjectName("listPanel")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(_PANEL_QSS)

        title = QLabel("使用说明", self)
        title.setObjectName("panelTitle")

        lines = (
            ("单击画面", "播放 / 暂停"),
            ("双击画面", "进入 / 退出全屏"),
            ("空格", "播放 / 暂停"),
            ("← / →", "快退 / 快进 5 秒"),
            ("Esc", "退出全屏"),
            ("拖动画面", "移动窗口"),
            ("拖动窗口边缘", "调整窗口大小"),
            ("画面比例按钮", "切换铺满 / 智能去黑边等"),
            ("×按钮", "切换播放倍速"),
            ("跳过片头按钮", "设置每次播放的起始秒数"),
        )
        grid = QVBoxLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(5)
        for key, desc in lines:
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(12)
            k = QLabel(key, self)
            k.setFixedWidth(96)
            k.setStyleSheet("QLabel{color:#FFFFFF;font-size:12px;background:transparent;}")
            d = QLabel(desc, self)
            d.setStyleSheet(
                "QLabel{color:rgba(255,255,255,150);font-size:12px;background:transparent;}")
            row.addWidget(k)
            row.addWidget(d, 1)
            grid.addLayout(row)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(10)
        lay.addWidget(title)
        lay.addLayout(grid)


class Toast(QLabel):
    """顶部居中提示气泡，自动淡出。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setStyleSheet(
            "QLabel{background:rgba(28,28,30,235);color:#FFFFFF;font-size:13px;"
            "padding:9px 18px;border-radius:18px;border:1px solid rgba(255,255,255,28);}")
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAlignment(Qt.AlignCenter)
        self.hide()

        self._effect = QGraphicsOpacityEffect(self)
        self._effect.setOpacity(0)
        self.setGraphicsEffect(self._effect)
        self._anim = QPropertyAnimation(self._effect, b"opacity", self)
        self._anim.setDuration(240)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self._fade_out)

    def show_message(self, text: str, duration_ms: int = 2600) -> None:
        self.setText(text)
        self.adjustSize()
        self.move((self.parentWidget().width() - self.width()) // 2, 56)
        self.show()
        self.raise_()
        self._anim.stop()
        self._anim.setStartValue(self._effect.opacity())
        self._anim.setEndValue(1.0)
        self._anim.start()
        self._hide_timer.start(duration_ms)

    def _fade_out(self) -> None:
        self._anim.stop()
        self._anim.setStartValue(self._effect.opacity())
        self._anim.setEndValue(0.0)
        self._anim.start()
        self._anim.finished.connect(self._on_done)

    def _on_done(self) -> None:
        try:
            self._anim.finished.disconnect(self._on_done)
        except (RuntimeError, TypeError):
            pass
        self.hide()
