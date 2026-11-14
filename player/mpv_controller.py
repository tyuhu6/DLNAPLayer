"""mpv 播放内核封装。

- 启动前预加载 player/libmpv-2.dll（python-mpv 依赖）
- render API 模式：vo="libmpv"，由 MpvGLWidget 负责绘制
- wid 降级模式：mpv 直接嵌入原生窗口句柄
- 所有 API 调用线程安全（python-mpv 保证），属性观察者回调运行在 mpv 事件线程
"""

import ctypes
import os
from pathlib import Path

_DLL_DIR = Path(__file__).resolve().parent
_DLL_PATH = _DLL_DIR / "libmpv-2.dll"
if _DLL_PATH.exists():
    # python-mpv 在 %PATH% 中查找 libmpv-2.dll；双保险：PATH 前置 + 显式预载
    os.environ["PATH"] = str(_DLL_DIR) + os.pathsep + os.environ.get("PATH", "")
    os.add_dll_directory(str(_DLL_DIR))
    ctypes.CDLL(str(_DLL_PATH), winmode=0)

import mpv  # noqa: E402

from PySide6.QtCore import QObject  # noqa: E402

from upnp.state import RendererState  # noqa: E402


class MpvController(QObject):
    # 模式 -> (keepaspect, panscan, video-aspect-override)
    FIT_MODES = {
        "original": (True, 0.0, "no"),
        "crop": (True, 1.0, "no"),
        "stretch": (False, 0.0, "no"),
        "16:9": (True, 0.0, "16:9"),
        "4:3": (True, 0.0, "4:3"),
    }

    def __init__(self, state: RendererState, bridge) -> None:
        super().__init__()
        self.state = state
        self.bridge = bridge
        self._mpv = None
        # 状态外发钩子（由 main 装配：transport/volume 变化 → GENA 通知）
        self.transport_hook = None
        self.volume_hook = None
        # mpv 属性本地镜像（事件线程更新，避免回调内回读属性）
        self._paused = False
        self._idle_active = True
        self._core_idle = True
        # 视频信息缓存（由 mpv 属性观察回调更新，UI 只读缓存，避免直接读 mpv 属性）
        self._vinfo = (0, 0, None)
        self._speed = 1.0
        self._create(wid=None)

    # ---------- 实例创建 ----------
    def _create(self, wid=None) -> None:
        kwargs = dict(
            log_handler=self._mpv_log,
            loglevel="warn",
            input_default_bindings=False,
            input_vo_keyboard=False,
            input_cursor=False,
            osc=False,
            idle="yes",
            keep_open="no",
            hwdec="auto-safe",
        )
        if wid is None:
            kwargs["vo"] = "libmpv"
        else:
            kwargs["wid"] = int(wid)
        self._mpv = mpv.MPV(**kwargs)
        self._mpv.volume = self.state.get_volume()

        self._mpv.observe_property("time-pos", self._on_time_pos)
        self._mpv.observe_property("duration", self._on_duration)
        self._mpv.observe_property("pause", self._on_pause)
        self._mpv.observe_property("idle-active", self._on_idle)
        self._mpv.observe_property("core-idle", self._on_core_idle)
        self._mpv.observe_property("volume", self._on_volume)
        self._mpv.observe_property("width", self._on_width)
        self._mpv.observe_property("height", self._on_height)
        self._mpv.observe_property("container-fps", self._on_fps)

    # ---------- 视频信息（回调在 mpv 线程，只写 Python 缓存） ----------
    def _push_video_info(self) -> None:
        w, h, fps = self._vinfo
        if w and h:
            text = f"{w}×{h}"
            if fps:
                text += f" · {fps:.0f}fps"
        else:
            text = ""
        try:
            self.bridge.video_info_changed.emit(text)
        except Exception:
            pass

    def _on_width(self, _name, value) -> None:
        try:
            self._vinfo = (int(value or 0), self._vinfo[1], self._vinfo[2])
        except (TypeError, ValueError):
            return
        self._push_video_info()

    def _on_height(self, _name, value) -> None:
        try:
            self._vinfo = (self._vinfo[0], int(value or 0), self._vinfo[2])
        except (TypeError, ValueError):
            return
        self._push_video_info()

    def _on_fps(self, _name, value) -> None:
        try:
            fps = float(value) if value else None
        except (TypeError, ValueError):
            fps = None
        self._vinfo = (self._vinfo[0], self._vinfo[1], fps)
        self._push_video_info()

    def set_fit_mode(self, mode: str) -> None:
        """画面适配方式。

        - original: 保持原比例，可能留黑边
        - crop:     等比放大到铺满，裁掉溢出部分（不留黑边、不变形）
        - smart:    先侦测并裁掉视频内嵌黑边，再等比铺满
        - stretch:  非等比拉伸铺满（不留黑边，会变形）
        - 16:9/4:3: 强制按指定比例显示
        """
        if self._mpv is None:
            return
        if mode == "smart":
            self._set_prop("keepaspect", True)
            self._set_prop("video_aspect_override", "no")
            self._set_prop("video_crop", self._detect_bars())
            self._set_prop("panscan", 1.0)
            return
        # 切换模式时清掉上一次的裁切
        self._set_prop("video_crop", "")
        keepaspect, panscan, aspect = self.FIT_MODES.get(mode, (True, 0.0, "no"))
        self._set_prop("keepaspect", keepaspect)
        self._set_prop("panscan", panscan)
        self._set_prop("video_aspect_override", aspect)

    def _set_prop(self, name: str, value) -> None:
        """逐项独立设置：某一项不被当前 mpv 版本支持时不连累其它项。"""
        try:
            setattr(self._mpv, name, value)
        except Exception:
            pass

    def _detect_bars(self) -> str:
        """侦测视频内嵌黑边，返回 mpv 的 video-crop 描述（无黑边返回空串）。

        内嵌黑边是画面像素的一部分，panscan 无法去掉，只能先裁掉。
        """
        try:
            res = self._mpv.command("screenshot-raw", "video")
        except Exception:
            return ""
        if not res or res.get("format") != "bgr0":
            return ""
        try:
            w, h = int(res["w"]), int(res["h"])
            stride, data = int(res["stride"]), res["data"]
        except (KeyError, TypeError, ValueError):
            return ""

        step_x = max(1, w // 120)
        step_y = max(1, h // 120)
        thr = 45          # 三通道之和低于此值视为黑

        def row_dark(y: int) -> bool:
            base = y * stride
            for x in range(0, w, step_x):
                i = base + x * 4
                if data[i] + data[i + 1] + data[i + 2] > thr:
                    return False
            return True

        top = 0
        while top < h // 3 and row_dark(top):
            top += 1
        bottom = h - 1
        while bottom > h * 2 // 3 and row_dark(bottom):
            bottom -= 1
        # 黑边过大（可能是全黑帧/淡入）时放弃，避免误裁
        if top >= h // 3 or bottom <= h * 2 // 3 or bottom <= top:
            return ""

        def col_dark(x: int) -> bool:
            for y in range(top, bottom + 1, step_y):
                i = y * stride + x * 4
                if data[i] + data[i + 1] + data[i + 2] > thr:
                    return False
            return True

        left = 0
        while left < w // 3 and col_dark(left):
            left += 1
        right = w - 1
        while right > w * 2 // 3 and col_dark(right):
            right -= 1
        cw, ch = right - left + 1, bottom - top + 1
        if cw < 16 or ch < 16 or (cw >= w - 2 and ch >= h - 2):
            return ""
        return f"{cw}x{ch}+{left}+{top}"

    def reinit_with_wid(self, hwnd: int) -> None:
        """render API 失败时的降级：销毁旧实例，用 wid 嵌入重建。"""
        self.terminate()
        self._create(wid=hwnd)

    def terminate(self) -> None:
        if self._mpv is not None:
            try:
                self._mpv.terminate()
            except Exception:
                pass
            self._mpv = None

    @property
    def mpv(self):
        return self._mpv

    # ---------- 命令 ----------
    def load(self, url: str, metadata: str = "", title: str = "") -> None:
        """接收投屏推送：载入并立即播放。"""
        self._paused = False
        self._idle_active = False
        self._core_idle = True
        self.state.set_media(url, metadata, title)
        self._set_transport(RendererState.TRANSITIONING)
        self.bridge.cast_received.emit(url, self.state.get_media()[2])
        self.bridge.title_changed.emit(self.state.get_media()[2])
        self._mpv.command("loadfile", url, "replace")
        self._mpv.pause = False
        # 新文件沿用当前倍速（mpv 会记住，但重建实例后需要恢复）
        try:
            self._mpv.speed = self._speed
        except Exception:
            pass

    def play(self) -> None:
        self._mpv.pause = False

    def pause(self) -> None:
        self._mpv.pause = True

    def toggle_play(self) -> None:
        self._mpv.pause = not bool(self._mpv.pause)

    def stop(self) -> None:
        self._mpv.command("stop")

    def seek_absolute(self, seconds: float) -> None:
        if seconds >= 0:
            self._mpv.command("seek", seconds, "absolute", "exact")

    def seek_relative(self, seconds: float) -> None:
        """相对当前进度快进（正数）/ 快退（负数）。"""
        if self._mpv is None or self._idle_active:
            return
        try:
            self._mpv.command("seek", float(seconds), "relative+exact")
        except Exception:
            pass

    def set_speed(self, speed: float) -> None:
        """设置播放倍速（1.0 为原速）。"""
        self._speed = float(speed)
        if self._mpv is None:
            return
        try:
            self._mpv.speed = self._speed
        except Exception:
            pass

    def speed(self) -> float:
        return self._speed

    def set_volume(self, v: int) -> None:
        self._mpv.volume = max(0, min(100, int(v)))

    def video_info(self) -> tuple[int, int, float | None]:
        """当前视频的宽、高与帧率（无视频时返回 0,0,None）。

        只读观察缓存：直接访问 mpv 属性在空闲状态下会触发原生崩溃。
        """
        if self._mpv is None or self._idle_active:
            return 0, 0, None
        return self._vinfo

    # ---------- 属性观察者（mpv 事件线程）----------
    def _on_time_pos(self, _name, value):
        if value is not None:
            self.state.set_position(float(value))

    def _on_duration(self, _name, value):
        if value is not None:
            self.state.set_duration(float(value))

    def _on_pause(self, _name, paused):
        if paused is not None:
            self._paused = bool(paused)
            self._refresh_transport()

    def _on_idle(self, _name, idle):
        if idle is not None:
            self._idle_active = bool(idle)
            if self._idle_active:
                self._vinfo = (0, 0, None)
                self._push_video_info()
            self._refresh_transport()

    def _on_core_idle(self, _name, core_idle):
        if core_idle is not None:
            self._core_idle = bool(core_idle)
            self._refresh_transport()

    def _on_volume(self, _name, value):
        if value is None:
            return
        v = int(value)
        old = self.state.get_volume()
        self.state.set_volume(v)
        if v != old:
            self.bridge.volume_changed.emit(v)
            if self.volume_hook:
                try:
                    self.volume_hook(v)
                except Exception:
                    pass

    # ---------- 内部 ----------
    def _refresh_transport(self) -> None:
        """由 (idle-active, core-idle, pause) 三个属性镜像推导传输状态。

        - idle-active=True        -> 无文件，STOPPED（自然结束/被停止）
        - core-idle=True + paused -> PAUSED_PLAYBACK
        - core-idle=True 播放中缓冲 -> 保持 TRANSITIONING
        - core-idle=False         -> PLAYING
        """
        if self._idle_active:
            new = RendererState.STOPPED
            ended = self.state.get_transport_state() != RendererState.STOPPED
            self._set_transport(new)
            if ended:
                self.state.clear_media()
                self.bridge.media_ended.emit()
            return
        if self._core_idle:
            if self._paused:
                self._set_transport(RendererState.PAUSED_PLAYBACK)
            # 未暂停的 core-idle（缓冲/加载）保持 TRANSITIONING
            return
        self._set_transport(RendererState.PLAYING)

    def _set_transport(self, new_state: str) -> None:
        if self.state.get_transport_state() == new_state:
            return
        self.state.set_transport_state(new_state)
        self.bridge.transport_changed.emit(new_state)
        if self.transport_hook:
            try:
                self.transport_hook(new_state)
            except Exception:
                pass

    def _mpv_log(self, level, prefix, text):
        print(f"[mpv{prefix}] {text.rstrip()}")
