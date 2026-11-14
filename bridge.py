"""跨线程事件中枢。

mpv 观察者线程 / SSDP / HTTP 服务线程产生的事件，
统一通过这里的 Qt 信号（AutoConnection → 队列投递）送入 GUI 线程。
"""

from PySide6.QtCore import QObject, Signal


class Bridge(QObject):
    # 手机推送了新视频 (url, title)
    cast_received = Signal(str, str)
    # 传输状态变化: PLAYING / PAUSED_PLAYBACK / STOPPED / TRANSITIONING / NO_MEDIA_PRESENT
    transport_changed = Signal(str)
    # 播放自然结束
    media_ended = Signal()
    # 音量变化
    volume_changed = Signal(int)
    # 收到的视频标题（DIDL 解析后）
    title_changed = Signal(str)
    # toast 提示文本
    toast = Signal(str)
    # 视频信息文本（"1920×1080 · 24fps"，无视频时为空串）
    video_info_changed = Signal(str)
