"""无边框窗口通用能力：8 向边缘拉伸 + 拖动区移动窗口。

- 边缘拉伸：命中窗口边缘时切换光标，按下直接调用 QWindow.startSystemResize
- 拖动区：在 drag_widgets 内按下后移动鼠标，调用 QWindow.startSystemMove
- 全部使用 Qt 原生 API（勿用 nativeEvent/WM_NCHITTEST，noconsole exe 会原生崩溃）

注意：mixin 必须写在基类列表最前面，否则 QWidget/QMainWindow 自带的
eventFilter 会遮蔽本 mixin 的实现，导致拉伸/拖动完全失效。
"""

from PySide6.QtCore import QPoint, QRect, Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication, QWidget

RESIZE_BORDER = 6  # 边缘拉伸敏感区（逻辑像素）
CLICK_DELAY = 230  # 单击延迟（用于区分单击与双击）


class FramelessMixin:
    """与 QMainWindow/QWidget 混用，提供无边框交互能力。"""

    def setup_frameless(self, drag_widgets=()) -> None:
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Window)
        self._drag_pending = False
        self._drag_moved = False
        self._drag_origin = QPoint()
        self._drag_window_origin = QPoint()
        self._click_pos = QPoint()
        self._resize_edge = Qt.Edges()
        self._edge_cursor_on = False
        self._edge_cursor_shape = None
        self._maximized = False
        self._normal_geometry: QRect | None = None
        self._drag_widgets = []
        # 单击要在双击判定窗口之后再触发，否则双击会先切一次播放状态
        self._click_timer = QTimer(self)
        self._click_timer.setSingleShot(True)
        self._click_timer.setInterval(CLICK_DELAY)
        self._click_timer.timeout.connect(self._emit_drag_click)
        self.setMouseTracking(True)
        QApplication.instance().installEventFilter(self)
        for w in drag_widgets:
            self.add_drag_widget(w)
        central = self.centralWidget() if hasattr(self, "centralWidget") else None
        if central is not None:
            central.setMouseTracking(True)

    def add_drag_widget(self, widget: QWidget) -> None:
        """把控件加入拖动区：在其上按住后移动即可拖动整个窗口。

        注意只装事件过滤器而不改控件属性，所以按钮等仍能正常响应点击。
        """
        if widget in self._drag_widgets:
            return
        self._drag_widgets.append(widget)
        widget.setMouseTracking(True)
        widget.installEventFilter(self)

    def reset_frameless(self) -> None:
        """窗口关闭前调用，清理残留的拉伸光标。"""
        self._drag_pending = False
        self._resize_edge = Qt.Edges()
        self._restore_cursor()

    # ---- 子类可覆盖的钩子 ----
    def _can_drag(self) -> bool:
        return not self.isFullScreen() and not self._maximized

    def on_drag_area_move(self) -> None:
        pass

    def on_drag_area_double_click(self) -> bool:
        return False

    def on_drag_area_click(self, global_pos: QPoint) -> None:
        """拖动区被单击（未移动）时触发，global_pos 为点击位置（全局坐标）。"""

    def on_maximized_changed(self, maximized: bool) -> None:
        pass

    # ---- 最大化 / 还原（避开任务栏） ----
    def toggle_maximized(self) -> None:
        if self._maximized:
            if self._normal_geometry is not None:
                self.setGeometry(self._normal_geometry)
            self._maximized = False
        else:
            self._normal_geometry = self.geometry()
            screen = self.screen() or QGuiApplication.primaryScreen()
            if screen is not None:
                self.setGeometry(screen.availableGeometry())
            self._maximized = True
        self.on_maximized_changed(self._maximized)

    # ---- 事件过滤 ----
    def eventFilter(self, obj, event):
        t = event.type()

        # 鼠标离开窗口 / 窗口失活 / 隐藏时立刻撤销拉伸光标，避免残留到其他窗口
        if t in (event.Type.Leave, event.Type.WindowDeactivate, event.Type.Hide):
            if self._edge_cursor_on and (self._is_own_event(obj) or obj is self):
                self._restore_cursor()

        # 边缘拉伸（优先于其他交互，避免被子控件吃掉边缘）
        if not self.isFullScreen() and not self._maximized and t in (
            event.Type.MouseMove,
            event.Type.MouseButtonPress,
            event.Type.MouseButtonRelease,
        ):
            if self._is_own_event(obj):
                edge = self._edge_at(self.mapFromGlobal(event.globalPosition().toPoint()))
                if t == event.Type.MouseMove:
                    self._apply_edge_cursor(edge)
                    self._resize_edge = edge
                    # 非边缘时必须继续往下走，否则会吞掉拖动中的移动事件
                    if edge:
                        return False
                if t == event.Type.MouseButtonPress and event.button() == Qt.LeftButton:
                    if edge:
                        handle = self.windowHandle()
                        if handle is not None:
                            handle.startSystemResize(edge)
                        self._apply_edge_cursor(Qt.Edges())
                        return True
                if t == event.Type.MouseButtonRelease:
                    self._resize_edge = Qt.Edges()

        # 鼠标在拖动区移动（用于播放窗口显示控制栏等钩子）
        if t == event.Type.MouseMove and obj in self._drag_widgets:
            self.on_drag_area_move()

        # 拖动进行中：只要本窗口处于被按下拖动状态，就让窗口跟随鼠标。
        # 注意不能再用 obj 归属判断：Windows 上移动事件的 obj 是 QWindow 或顶层窗口，
        # 与 drag widget 不是同一对象，会把事件全部漏掉。
        if self._drag_pending and t == event.Type.MouseMove:
            if self._can_drag():
                delta = event.globalPosition().toPoint() - self._drag_origin
                if delta.manhattanLength() > 4:
                    self._drag_moved = True
                self.move(self._drag_window_origin + delta)
            return False

        # 拖动区：按下开始 / 双击
        if obj in self._drag_widgets:
            if t == event.Type.MouseButtonDblClick:
                self._click_timer.stop()
                if self.on_drag_area_double_click():
                    return True
                return False
            if t == event.Type.MouseButtonPress and event.button() == Qt.LeftButton:
                self._drag_origin = event.globalPosition().toPoint()
                self._drag_window_origin = self.pos()
                self._drag_pending = True
                self._drag_moved = False
                if self._can_drag():
                    # 强制捕获鼠标：否则窗口移动过程中 move 事件可能不再派发
                    self.grabMouse()
                return False
            if t == event.Type.MouseButtonRelease:
                self._finish_drag_with_click(event.globalPosition().toPoint())
                return False

        # 释放无论如何都要结束拖动（避免漏掉 release 导致窗口粘鼠标）
        if t == event.Type.MouseButtonRelease:
            self._finish_drag_with_click(event.globalPosition().toPoint())

        return False

    def _finish_drag_with_click(self, global_pos: QPoint) -> None:
        clicked = self._drag_pending and not self._drag_moved
        self._end_drag()
        if clicked:
            self._click_pos = QPoint(global_pos)
            self._click_timer.start()

    def _emit_drag_click(self) -> None:
        self.on_drag_area_click(self._click_pos)

    def _end_drag(self) -> None:
        self._drag_pending = False
        try:
            self.releaseMouse()
        except RuntimeError:
            pass

    def _is_own_event(self, obj) -> bool:
        """判断事件对象是否属于本窗口（widget 或 window handle）。"""
        if isinstance(obj, QWidget):
            return obj.window() is self
        return obj is not None and obj is self.windowHandle()

    # ---- 边缘判定与光标 ----
    def _apply_edge_cursor(self, edge: Qt.Edges) -> None:
        """用应用级 override 光标显示拉伸形状（子控件无法遮挡），离开窗口时立即恢复。

        必须成对管理：只在形状变化时设置，窗口失去焦点/鼠标离开时撤销，
        否则光标会残留在其他窗口上。
        """
        shape = self._cursor_for_edge(edge) if edge else None
        if shape is None:
            self._restore_cursor()
            return
        if shape == self._edge_cursor_shape:
            return
        if self._edge_cursor_on:
            QApplication.changeOverrideCursor(shape)
        else:
            QApplication.setOverrideCursor(shape)
            self._edge_cursor_on = True
        self._edge_cursor_shape = shape

    def _restore_cursor(self) -> None:
        if self._edge_cursor_on:
            QApplication.restoreOverrideCursor()
            self._edge_cursor_on = False
        self._edge_cursor_shape = None

    def _edge_at(self, pos: QPoint) -> Qt.Edges:
        """返回窗口坐标 pos 命中的拉伸边（0 表示无）。"""
        x, y = pos.x(), pos.y()
        w, h = self.width(), self.height()
        b = RESIZE_BORDER
        edge = Qt.Edges()
        if x <= b:
            edge |= Qt.LeftEdge
        elif x >= w - b:
            edge |= Qt.RightEdge
        if y <= b:
            edge |= Qt.TopEdge
        elif y >= h - b:
            edge |= Qt.BottomEdge
        return edge

    @staticmethod
    def _cursor_for_edge(edge: Qt.Edge) -> Qt.CursorShape:
        if edge in (Qt.TopEdge | Qt.LeftEdge, Qt.BottomEdge | Qt.RightEdge):
            return Qt.SizeFDiagCursor
        if edge in (Qt.TopEdge | Qt.RightEdge, Qt.BottomEdge | Qt.LeftEdge):
            return Qt.SizeBDiagCursor
        if edge in (Qt.LeftEdge, Qt.RightEdge):
            return Qt.SizeHorCursor
        if edge in (Qt.TopEdge, Qt.BottomEdge):
            return Qt.SizeVerCursor
        return Qt.ArrowCursor
