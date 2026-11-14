"""极简加载/动效组件。

设计原则：
- 不出现旋转菊花；用细断点圆环 + 缓慢呼吸光晕
- 所有过渡 1.0~1.6s 缓动，单周期内只出现一个动态元素
- 颜色仅用白色 + 透明度层级，不引入新主色
"""

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget


class Spinner(QWidget):
    """极简断点圆环加载指示器。

    两段 80° 的白色细弧在圆环上以 1.2s 周期匀速旋转，其余部分留白。
    尺寸固定 36x36，线宽 2px。
    """

    def __init__(self, size: int = 36, parent=None) -> None:
        super().__init__(parent)
        self._size = size
        self._angle = 0.0
        self.setFixedSize(size, size)

        self._timer = QTimer(self)
        self._timer.setInterval(16)  # ~60fps
        self._timer.timeout.connect(self._tick)

    def start(self) -> None:
        self._timer.start()
        self.show()

    def stop(self) -> None:
        self._timer.stop()
        self.hide()

    def is_running(self) -> bool:
        return self._timer.isActive()

    def _tick(self) -> None:
        self._angle = (self._angle + 3.0) % 360.0
        self.update()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        rect = QRectF(2, 2, self._size - 4, self._size - 4)
        pen = QPen(QColor(255, 255, 255, 200))
        pen.setWidth(2)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)

        # 两段弧：间隔 180°，每段扫过 80°
        for offset in (self._angle, self._angle + 180.0):
            p.drawArc(rect, int(offset * 16), int(80 * 16))
        p.end()


class PulseHalo(QWidget):
    """呼吸光晕：一个圆形半透明区域，透明度与半径缓慢循环。

    用于 Idle 页、镜像等待页等「等待中」场景，营造温和的存在感知。
    周期 2.4s，透明度 0.05~0.16，半径 ±12% 脉动。
    """

    CYCLE_MS = 2400

    def __init__(self, parent=None, color: tuple[int, int, int] = (255, 255, 255)) -> None:
        super().__init__(parent)
        self._t = 0.0
        self._color = color
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)

        self._timer = QTimer(self)
        self._timer.setInterval(24)  # ~42fps
        self._timer.timeout.connect(self._tick)

    def start(self) -> None:
        self._timer.start()
        self.show()

    def stop(self) -> None:
        self._timer.stop()
        self.hide()

    def is_running(self) -> bool:
        return self._timer.isActive()

    def _tick(self) -> None:
        self._t = (self._t + 24 / self.CYCLE_MS) % 1.0
        self.update()

    def paintEvent(self, event) -> None:
        import math
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        cx, cy = w / 2, h / 2
        base = min(w, h) * 0.5
        # 正弦呼吸：0.05~0.16 透明度，半径在 base 基础上 ±12%
        phase = math.sin(self._t * 2 * math.pi)
        alpha = int(round((0.105 + phase * 0.055) * 255))
        r = base * (1.0 + phase * 0.12)

        path = QPainterPath()
        path.addEllipse(QPointF(cx, cy), r, r)
        r_, g_, b_ = self._color
        p.fillPath(path, QColor(r_, g_, b_, alpha))
        p.end()
