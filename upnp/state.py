"""跨线程共享的渲染器状态缓存（线程安全）。

被以下位置读写：
- mpv 观察者线程：刷新 position / duration / transport_state
- HTTP 服务线程：SOAP GetPositionInfo / GetTransportInfo 直接读这里
- GUI 线程：读状态渲染 UI；用户操作经 controller 写入
"""

import threading


def upnp_time(seconds: float) -> str:
    """秒 → UPnP 时间格式 H:MM:SS.xxx"""
    if seconds is None or seconds < 0:
        seconds = 0
    total = int(seconds)
    ms = int(round((seconds - total) * 1000))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}.{ms:03d}"


def parse_upnp_time(text: str) -> float:
    """UPnP 时间格式 H:MM:SS[.xxx] → 秒，解析失败返回 -1"""
    if not text or not text.strip():
        return -1
    try:
        parts = text.strip().split(":")
        if len(parts) == 3:
            h, m, s = parts
            return int(h) * 3600 + int(m) * 60 + float(s)
        if len(parts) == 2:
            m, s = parts
            return int(m) * 60 + float(s)
    except ValueError:
        pass
    return -1


class RendererState:
    STOPPED = "STOPPED"
    PLAYING = "PLAYING"
    PAUSED_PLAYBACK = "PAUSED_PLAYBACK"
    TRANSITIONING = "TRANSITIONING"
    NO_MEDIA = "NO_MEDIA_PRESENT"

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._transport_state = self.NO_MEDIA
        self._current_uri = ""
        self._current_uri_metadata = ""
        self._title = ""
        self._position = 0.0
        self._duration = 0.0
        self._volume = 100

    # ---- 通用 ----
    def snapshot(self) -> dict:
        with self._lock:
            return {
                "transport_state": self._transport_state,
                "uri": self._current_uri,
                "metadata": self._current_uri_metadata,
                "title": self._title,
                "position": self._position,
                "duration": self._duration,
                "volume": self._volume,
            }

    # ---- 传输状态 ----
    def get_transport_state(self) -> str:
        with self._lock:
            return self._transport_state

    def set_transport_state(self, value: str) -> None:
        with self._lock:
            self._transport_state = value

    # ---- 媒体信息 ----
    def set_media(self, uri: str, metadata: str = "", title: str = "") -> None:
        with self._lock:
            self._current_uri = uri
            self._current_uri_metadata = metadata
            self._title = title or uri.rsplit("/", 1)[-1] or "未知视频"

    def get_media(self) -> tuple[str, str, str]:
        with self._lock:
            return self._current_uri, self._current_uri_metadata, self._title

    def clear_media(self) -> None:
        with self._lock:
            self._current_uri = ""
            self._current_uri_metadata = ""
            self._title = ""
            self._position = 0.0
            self._duration = 0.0

    # ---- 播放进度 ----
    def set_position(self, pos: float) -> None:
        with self._lock:
            self._position = max(0.0, pos)

    def set_duration(self, dur: float) -> None:
        with self._lock:
            self._duration = max(0.0, dur)

    def get_progress(self) -> tuple[float, float]:
        with self._lock:
            return self._position, self._duration

    # ---- 音量 ----
    def set_volume(self, v: int) -> None:
        with self._lock:
            self._volume = max(0, min(100, int(v)))

    def get_volume(self) -> int:
        with self._lock:
            return self._volume
