"""GENA 事件订阅管理（UPnP 事件通知）。

- 处理 SUBSCRIBE / RENEW / UNSUBSCRIBE，维护 SID -> 订阅者表
- 状态变化时向所有订阅者 NOTIFY 推送 LastChange XML（AVTransport/RenderingControl）
- 订阅建立时立即推送 SEQ=0 的初始全量事件（部分 APP 依赖此事件才会更新界面）
- NOTIFY 发送在独立守护线程中执行，避免阻塞 SOAP 响应
"""

import http.client
import threading
import time
import uuid as uuidlib
from urllib.parse import urlparse

from xml.sax.saxutils import escape

AVT_NS = "urn:schemas-upnp-org:metadata-1-0/AVT/"
RCS_NS = "urn:schemas-upnp-org:metadata-1-0/RCS/"

MAX_NOTIFY_FAILS = 3


class _Sub:
    __slots__ = ("sid", "service", "callback", "expires", "seq", "fails")

    def __init__(self, sid, service, callback, expires):
        self.sid = sid
        self.service = service
        self.callback = callback
        self.expires = expires
        self.seq = 0
        self.fails = 0


class GenaManager:
    def __init__(self, state, server_str: str = "DLNAPLayer/1.0") -> None:
        self.state = state
        self._server = server_str
        self._lock = threading.Lock()
        self._subs: dict[str, _Sub] = {}

    # ---------- 订阅管理（HTTP 服务线程调用） ----------
    def subscribe(self, service: str, headers) -> tuple[str, int]:
        """处理 SUBSCRIBE（新订阅或续期）。返回 (SID, timeout秒)。"""
        timeout = 1800
        raw = headers.get("Timeout", "")
        if "second-" in raw.lower():
            try:
                timeout = int(raw.lower().split("second-")[1].split("-")[0])
            except (ValueError, IndexError):
                timeout = 1800
        timeout = max(300, min(timeout, 1800))

        sid = headers.get("Sid", "").strip()
        now = time.time()
        with self._lock:
            if sid and sid in self._subs:
                sub = self._subs[sid]
                sub.expires = now + timeout
                sub.fails = 0
                return sid, timeout
            callback = (headers.get("Callback") or "").strip()
            # CALLBACK: <http://192.168.1.5:30000/cb>（可能有多个，取第一个）
            callback = callback.split(">")[0].strip("<> ").strip()
            sid = f"uuid:{uuidlib.uuid4()}"
            self._subs[sid] = _Sub(sid, service, callback, now + timeout)
        self._send_initial(service, sid)
        return sid, timeout

    def unsubscribe(self, sid: str) -> bool:
        with self._lock:
            return self._subs.pop(sid, None) is not None

    def _send_initial(self, service: str, sid: str) -> None:
        """订阅建立后立即推送 SEQ=0 全量状态。"""
        if service.endswith("AVTransport:1"):
            xml = self.avt_lastchange()
        elif service.endswith("RenderingControl:1"):
            xml = self.rc_lastchange()
        else:  # ConnectionManager：无 LastChange，直接发属性
            self._notify(sid, self.cm_propertyset(), seq=0)
            return
        with self._lock:
            sub = self._subs.get(sid)
            if sub:
                sub.seq = 1
        self._notify(sid, xml, seq=0)

    # ---------- 状态变化推送（任意线程调用） ----------
    def notify_avt(self) -> None:
        """AVTransport 状态变化：TransportState/进度/时长。"""
        xml = self.avt_lastchange()
        self._broadcast("AVTransport", xml)

    def notify_rc(self) -> None:
        """RenderingControl 状态变化：Volume。"""
        xml = self.rc_lastchange()
        self._broadcast("RenderingControl", xml)

    def _broadcast(self, service_key: str, xml: str) -> None:
        now = time.time()
        with self._lock:
            targets = []
            for sub in list(self._subs.values()):
                if service_key in sub.service and sub.expires > now:
                    sub.seq += 1
                    targets.append((sub.sid, sub.seq))
            # 顺带清理过期/失败订阅
            for sid, sub in list(self._subs.items()):
                if sub.expires <= now or sub.fails >= MAX_NOTIFY_FAILS:
                    del self._subs[sid]
        for sid, seq in targets:
            self._notify(sid, xml, seq=seq)

    # ---------- LastChange XML 构建 ----------
    def avt_lastchange(self) -> str:
        snap = self.state.snapshot()
        from upnp.state import upnp_time
        dur = upnp_time(snap["duration"]) if snap["duration"] > 0 else "0:00:00.000"
        pos = upnp_time(snap["position"])
        return (
            f'<Event xmlns="{AVT_NS}"><InstanceID val="0">'
            f'<TransportState val="{snap["transport_state"]}"/>'
            f'<CurrentPlayMode val="NORMAL"/>'
            f'<NumberOfTracks val="{1 if snap["uri"] else 0}"/>'
            f'<CurrentTrack val="{1 if snap["uri"] else 0}"/>'
            f'<CurrentTrackDuration val="{dur}"/>'
            f'<CurrentMediaDuration val="{dur}"/>'
            f'<RelativeTimePosition val="{pos}"/>'
            f'<AbsoluteTimePosition val="{pos}"/>'
            f"</InstanceID></Event>"
        )

    def rc_lastchange(self) -> str:
        vol = self.state.get_volume()
        return (
            f'<Event xmlns="{RCS_NS}"><InstanceID val="0">'
            f'<Volume channel="Master" val="{vol}"/>'
            f'<Mute channel="Master" val="0"/>'
            f"</InstanceID></Event>"
        )

    def cm_propertyset(self) -> str:
        from upnp.services import SINK_PROTOCOL_INFO
        return (
            "<e:propertyset xmlns:e=\"urn:schemas-upnp-org:event-1-0\">"
            f"<e:property><SinkProtocolInfo>{escape(SINK_PROTOCOL_INFO)}</SinkProtocolInfo></e:property>"
            "<e:property><SourceProtocolInfo></SourceProtocolInfo></e:property>"
            "<e:property><CurrentConnectionIDs>0</CurrentConnectionIDs></e:property>"
            "</e:propertyset>"
        )

    # ---------- NOTIFY 发送 ----------
    def _propertyset(self, lastchange_xml: str) -> str:
        return (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<e:propertyset xmlns:e="urn:schemas-upnp-org:event-1-0">'
            f"<e:property><LastChange>{escape(lastchange_xml)}</LastChange></e:property>"
            "</e:propertyset>"
        )

    def _notify(self, sid: str, body_xml: str, seq: int) -> None:
        with self._lock:
            sub = self._subs.get(sid)
            callback = sub.callback if sub else None
        if not callback or not callback.lower().startswith("http://"):
            return
        body = self._propertyset(body_xml)
        threading.Thread(target=self._post_notify, args=(callback, sid, seq, body),
                         daemon=True).start()

    def _post_notify(self, callback: str, sid: str, seq: int, body: str) -> None:
        try:
            url = urlparse(callback)
            conn = http.client.HTTPConnection(url.hostname, url.port or 80, timeout=4)
            conn.request(
                "NOTIFY",
                url.path or "/",
                body.encode("utf-8"),
                {
                    "HOST": f"{url.hostname}:{url.port or 80}",
                    "CONTENT-TYPE": 'text/xml; charset="utf-8"',
                    "NT": "upnp:event",
                    "NTS": "ssdp:update",
                    "SID": sid,
                    "SEQ": str(seq),
                    "Content-Length": str(len(body.encode("utf-8"))),
                    "SERVER": self._server,
                },
            )
            conn.getresponse().read()
            conn.close()
            with self._lock:
                sub = self._subs.get(sid)
                if sub:
                    sub.fails = 0
        except Exception:
            with self._lock:
                sub = self._subs.get(sid)
                if sub:
                    sub.fails += 1
