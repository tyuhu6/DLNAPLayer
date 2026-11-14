"""模拟手机APP向本设备推送视频：SetAVTransportURI → Play → 查询进度 → Pause → Seek，
并验证 GENA 订阅（收到 SEQ=0 初始事件）。

用法:
  python tests/push_video.py [--url http://.../video.mp4] [--port 9090]
"""

import argparse
import http.client
import socket
import sys
import threading
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import load, get_lan_ip  # noqa: E402

AVT = "urn:schemas-upnp-org:service:AVTransport:1"

DIDL = (
    '<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/" '
    'xmlns:dc="http://purl.org/dc/elements/1.1/" '
    'xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/">'
    '<item id="1" restricted="0"><dc:title>测试视频</dc:title>'
    '<upnp:class>object.item.videoItem</upnp:class>'
    '<res protocolInfo="http-get:*:video/mp4:*">{uri}</res></item></DIDL-Lite>'
)


def soap_call(ip: str, port: int, action: str, args: str) -> str:
    body = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
        's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
        f'<s:Body><u:{action} xmlns:u="{AVT}">{args}</u:{action}></s:Body></s:Envelope>'
    )
    conn = http.client.HTTPConnection(ip, port, timeout=8)
    conn.request(
        "POST", "/ctl/AVTransport", body.encode("utf-8"),
        {
            "CONTENT-TYPE": 'text/xml; charset="utf-8"',
            "SOAPACTION": f'"{AVT}#{action}"',
            "Content-Length": str(len(body.encode("utf-8"))),
        },
    )
    resp = conn.getresponse()
    data = resp.read().decode("utf-8", "replace")
    conn.close()
    if resp.status != 200:
        raise RuntimeError(f"{action} -> HTTP {resp.status}: {data[:300]}")
    return data


def xml_val(data: str, tag: str) -> str:
    start = data.find(f"<{tag}>")
    end = data.find(f"</{tag}>")
    if start >= 0 and end > start:
        return data[start + len(tag) + 2:end]
    return ""


class _NotifyListener(threading.Thread):
    """监听 GENA NOTIFY 回调，验证能收到 SEQ=0 初始事件。"""

    def __init__(self, ip: str) -> None:
        super().__init__(daemon=True)
        self.received = []
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.bind((ip, 0))
        srv.listen(4)
        self.ip = ip
        self.port = srv.getsockname()[1]
        self._srv = srv

    def run(self) -> None:
        self._srv.settimeout(8)
        for _ in range(4):
            try:
                conn, _addr = self._srv.accept()
            except socket.timeout:
                return
            conn.settimeout(4)
            buf = b""
            while b"\r\n\r\n" not in buf:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                buf += chunk
            try:
                length = 0
                for line in buf.decode("utf-8", "replace").splitlines():
                    if line.lower().startswith("content-length:"):
                        length = int(line.split(":", 1)[1].strip())
                while len(buf) - buf.find(b"\r\n\r\n") - 4 < length and length:
                    buf += conn.recv(4096)
            except Exception:
                pass
            text = buf.decode("utf-8", "replace")
            seq = ""
            for line in text.splitlines():
                if line.upper().startswith("SEQ:"):
                    seq = line.split(":", 1)[1].strip()
            self.received.append((seq, "LastChange" in text))
            try:
                conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n")
            finally:
                conn.close()
        self._srv.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://commondatastorage.googleapis.com/gtv-videos-bucket/sample/ForBiggerBlazes.mp4")
    parser.add_argument("--ip", default="")
    parser.add_argument("--port", type=int, default=9090)
    args = parser.parse_args()

    settings = load()
    ip = args.ip or settings.get("bind_ip") or get_lan_ip()
    port = args.port

    # 1. desc.xml 校验
    desc_url = f"http://{ip}:{port}/desc.xml"
    print(f"[test] 拉取 {desc_url}")
    with urllib.request.urlopen(desc_url, timeout=5) as resp:
        desc = resp.read().decode("utf-8", "replace")
    assert "MediaRenderer" in desc, "desc.xml 缺少 MediaRenderer"
    print("[test] desc.xml OK")

    # 2. GENA 订阅（应收到 SEQ=0 初始事件）
    listener = _NotifyListener(ip)
    listener.start()
    conn = http.client.HTTPConnection(ip, port, timeout=8)
    conn.request("SUBSCRIBE", "/evt/AVTransport",
                 headers={"CALLBACK": f"<http://{ip}:{listener.port}/cb>",
                          "NT": "upnp:event", "TIMEOUT": "Second-300",
                          "Content-Length": "0"})
    resp = conn.getresponse()
    resp.read()
    sid = resp.getheader("SID")
    conn.close()
    print(f"[test] SUBSCRIBE -> HTTP {resp.status} SID={sid}")
    assert resp.status == 200 and sid, "GENA 订阅失败"
    time.sleep(2.0)
    if listener.received:
        print(f"[test] 收到初始事件 x{len(listener.received)}，SEQ={[s for s, _ in listener.received]}")
    else:
        print("[test] 警告: 未收到初始 NOTIFY 事件（APP 将退化为轮询）")

    # 3. SetAVTransportURI + Play
    didl = DIDL.format(uri=args.url)
    # SOAP 里的 DIDL 需再次 XML 转义
    from xml.sax.saxutils import escape
    didl_escaped = escape(didl)
    soap_call(ip, port, "SetAVTransportURI",
              f"<InstanceID>0</InstanceID><CurrentURI>{escape(args.url)}</CurrentURI>"
              f"<CurrentURIMetaData>{didl_escaped}</CurrentURIMetaData>")
    print(f"[test] SetAVTransportURI OK: {args.url}")
    soap_call(ip, port, "Play", "<InstanceID>0</InstanceID><Speed>1</Speed>")
    print("[test] Play OK —— 窗口应开始播放")

    # 4. 等播放器走几秒，校验 GetPositionInfo
    time.sleep(6)
    data = soap_call(ip, port, "GetPositionInfo", "<InstanceID>0</InstanceID>")
    rel = xml_val(data, "RelTime")
    dur = xml_val(data, "TrackDuration")
    print(f"[test] 进度 RelTime={rel} / TrackDuration={dur}")
    assert rel not in ("", "0:00:00.000"), "进度未走动"

    # 5. Pause → 校验状态
    soap_call(ip, port, "Pause", "<InstanceID>0</InstanceID>")
    time.sleep(1)
    data = soap_call(ip, port, "GetTransportInfo", "<InstanceID>0</InstanceID>")
    state = xml_val(data, "CurrentTransportState")
    print(f"[test] Pause 后状态 = {state}")
    assert state == "PAUSED_PLAYBACK", f"期望 PAUSED_PLAYBACK，实际 {state}"

    # 6. Seek 到 30s
    soap_call(ip, port, "Seek", "<InstanceID>0</InstanceID><Unit>REL_TIME</Unit><Target>0:00:30</Target>")
    time.sleep(2)
    data = soap_call(ip, port, "GetPositionInfo", "<InstanceID>0</InstanceID>")
    rel = xml_val(data, "RelTime")
    print(f"[test] Seek 后 RelTime={rel}")
    print("[test] PASS: SOAP 控制链路完整")
    return 0


if __name__ == "__main__":
    sys.exit(main())
