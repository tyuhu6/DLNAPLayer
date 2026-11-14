"""mpv 渲染嵌入组件（libmpv render API + QOpenGLWidget）。

- initializeGL/paintGL 中创建并驱动 MpvRenderContext，mpv 直接画进 Qt 的 FBO
- update 回调运行在 mpv 线程，必须经 Qt 信号跨线程队列投递到 GUI 线程
- render API 初始化失败时发 render_failed 信号，由 MainWindow 降级到 wid 模式
"""

import ctypes

import mpv
from PySide6.QtCore import Signal, Qt
from PySide6.QtOpenGLWidgets import QOpenGLWidget

_pycapsule_get_name = ctypes.pythonapi.PyCapsule_GetName
_pycapsule_get_name.restype = ctypes.c_char_p
_pycapsule_get_name.argtypes = [ctypes.py_object]
_pycapsule_get_pointer = ctypes.pythonapi.PyCapsule_GetPointer
_pycapsule_get_pointer.restype = ctypes.c_void_p
_pycapsule_get_pointer.argtypes = [ctypes.py_object, ctypes.c_char_p]


def _resolve_gl_proc(ptr) -> int:
    """把 Qt 返回的函数指针对象解析成整数地址。

    兼容 PySide6 的 PyCapsule / shiboken VoidPtr / 直接可 int 的对象。
    """
    if ptr is None:
        return 0
    try:
        return int(ptr)
    except (TypeError, ValueError):
        pass
    try:
        return int(ptr.value)
    except AttributeError:
        pass
    try:
        name = _pycapsule_get_name(ptr)
        return _pycapsule_get_pointer(ptr, name) or 0
    except Exception:
        return 0


class MpvGLWidget(QOpenGLWidget):
    _repaint = Signal()
    render_failed = Signal(str)  # 失败原因

    def __init__(self, controller, parent=None) -> None:
        super().__init__(parent)
        self.controller = controller
        self._ctx = None
        self._failed = False
        self._repaint.connect(self.update)

    # ---------- libmpv render API ----------
    def ensure_context(self) -> bool:
        """惰性创建渲染上下文（首次 paintGL 时调用，GL 上下文已就绪）。"""
        if self._ctx is not None or self._failed:
            return self._ctx is not None
        try:
            self._ctx = mpv.MpvRenderContext(
                self.controller.mpv,
                "opengl",
                opengl_init_params={
                    "get_proc_address": mpv.MpvGlGetProcAddressFn(self._get_proc_address)
                },
            )
            # python-mpv 1.0.x 的更新回调属性名为 update_cb
            self._ctx.update_cb = self._on_mpv_update
        except Exception as exc:  # GL 兼容性问题
            self._failed = True
            self._ctx = None
            self.render_failed.emit(str(exc))
        return self._ctx is not None

    def _get_proc_address(self, *args) -> int:
        # C 回调签名可能是 (ctx, name) 或 (name)，取最后一个参数
        name = args[-1]
        if isinstance(name, bytes):
            name = name.decode("utf-8", "replace")
        glctx = self.context()
        if glctx is None:
            return 0
        try:
            return _resolve_gl_proc(glctx.getProcAddress(name))
        except Exception:
            return 0

    def _on_mpv_update(self) -> None:
        # 运行在 mpv 线程：只允许 emit 信号（队列投递到 GUI 线程后调 update()）
        self._repaint.emit()

    # ---------- QOpenGLWidget ----------
    def paintGL(self) -> None:
        if not self.ensure_context() or self._ctx is None:
            return
        dpr = self.devicePixelRatioF()
        w = max(1, int(self.width() * dpr))
        h = max(1, int(self.height() * dpr))
        fbo = int(self.defaultFramebufferObject())
        self._ctx.render(flip_y=True, opengl_fbo={"fbo": fbo, "w": w, "h": h})

    def shutdown(self) -> None:
        """销毁渲染上下文（必须在 mpv.terminate() 之前调用）。"""
        if self._ctx is not None:
            try:
                self._ctx.free()
            except Exception:
                pass
            self._ctx = None
