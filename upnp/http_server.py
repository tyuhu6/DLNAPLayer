"""UPnP HTTP 服务：设备描述 / SCPD / SOAP 控制 / GENA 订阅。

ThreadingHTTPServer 承载（DLNA 请求量极低，标准库足够）。
"""

import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

SERVER_STR = "Windows/10 UPnP/1.0 DLNAPLayer/1.0"
RES_DIR = Path(__file__).resolve().parent / "resources"


class DeviceContext:
    """设备描述数据（friendlyName 支持运行时修改）。"""

    def __init__(self, ip: str, port: int, device_uuid: str, friendly_name: str) -> None:
        self.ip = ip
        self.port = port
        self.uuid = device_uuid
        self.name = friendly_name
        self._template = (RES_DIR / "desc.xml").read_text(encoding="utf-8")

    def desc_xml(self) -> bytes:
        return self._template.format(
            ip=self.ip, port=self.port, uuid=self.uuid, name=self.name
        ).encode("utf-8")

    def location_url(self) -> str:
        return f"http://{self.ip}:{self.port}/desc.xml"


class _SCPDStore:
    def __init__(self) -> None:
        self._files = {
            "AVTransport": RES_DIR / "AVTransport.xml",
            "ConnectionManager": RES_DIR / "ConnectionManager.xml",
            "RenderingControl": RES_DIR / "RenderingControl.xml",
        }
        self._cache: dict[str, bytes] = {}

    def get(self, name: str) -> bytes | None:
        path = self._files.get(name)
        if path is None:
            return None
        if name not in self._cache:
            self._cache[name] = path.read_bytes()
        return self._cache[name]


class UPnPHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "DLNAPLayer/1.0"

    # 由 UPnPHTTPServer 装配
    device: DeviceContext = None
    registry = None
    gena = None
    scpd: _SCPDStore = None

    # ---------- 工具 ----------
    def _send(self, code: int, body: bytes = b"", ctype: str = "text/xml; charset=\"utf-8\"",
              extra_headers: dict | None = None) -> None:
        self.send_response(code)
        self.send_header("DATE", self.date_time_string())
        self.send_header("SERVER", SERVER_STR)
        if extra_headers:
            for k, v in extra_headers.items():
                self.send_header(k, v)
        if body:
            self.send_header("CONTENT-TYPE", ctype)
            self.send_header("Content-Length", str(len(body)))
        else:
            self.send_header("Content-Length", "0")
        self.end_headers()
        if body:
            self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass  # 静默常规日志，避免刷屏

    def handle_one_request(self):
        # 手机APP常会突然断开连接，吞掉连接类异常避免刷无谓堆栈
        try:
            super().handle_one_request()
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError, TimeoutError):
            self.close_connection = True

    # ---------- GET：描述与 SCPD ----------
    def do_GET(self):
        path = urlparse(self.path).path
        if path in ("/desc.xml", "/description.xml"):
            self._send(200, self.device.desc_xml())
            return
        if path.startswith("/scpd/"):
            name = path[len("/scpd/"):].removesuffix(".xml")
            data = self.scpd.get(name)
            if data is None:
                self._send(404)
            else:
                self._send(200, data)
            return
        if path == "/":
            body = f"<h1>DLNAPLayer</h1><p>设备名：{self.device.name}</p>".encode("utf-8")
            self._send(200, body, 'text/html; charset="utf-8"')
            return
        self._send(404)

    # ---------- POST：SOAP 控制 ----------
    def do_POST(self):
        path = urlparse(self.path).path
        if not path.startswith("/ctl/"):
            self._send(404)
            return
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b""
        soapaction = self.headers.get("SOAPACTION") or self.headers.get("SoapAction") or ""
        try:
            resp = self.registry.dispatch(soapaction, body)
            self._send(200, resp.encode("utf-8"))
        except Exception as exc:
            from upnp.services import UpnpActionError
            if isinstance(exc, UpnpActionError):
                fault = self.registry.fault_xml(exc.code, exc.desc)
            else:
                fault = self.registry.fault_xml(501, f"Action failed: {exc}")
            self._send(500, fault.encode("utf-8"))

    # ---------- SUBSCRIBE / UNSUBSCRIBE：GENA ----------
    def do_SUBSCRIBE(self):
        path = urlparse(self.path).path
        if not path.startswith("/evt/"):
            self._send(404, extra_headers={"TIMEOUT": "Second-1800"})
            return
        service = path[len("/evt/"):].strip("/")
        sid = self.headers.get("SID", "").strip()
        if sid:
            # 续期
            try:
                new_sid, timeout = self.gena.subscribe(service, self.headers)
                self._send(200, extra_headers={"SID": new_sid,
                                               "TIMEOUT": f"Second-{timeout}"})
            except Exception:
                self._send(412)
            return
        nt = self.headers.get("NT", "").strip()
        if nt != "upnp:event" or not self.headers.get("CALLBACK"):
            self._send(412)
            return
        try:
            new_sid, timeout = self.gena.subscribe(f"urn:schemas-upnp-org:service:{service}:1",
                                                   self.headers)
            self._send(200, extra_headers={"SID": new_sid,
                                           "TIMEOUT": f"Second-{timeout}"})
        except Exception:
            self._send(412)

    def do_UNSUBSCRIBE(self):
        sid = self.headers.get("SID", "").strip()
        if not sid:
            self._send(400)
            return
        self.gena.unsubscribe(sid)
        self._send(200)


class UPnPHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def start_upnp_server(ip: str, port: int, registry, gena, device: DeviceContext):
    """启动 HTTP 服务，返回 (server, thread)。绑定失败会递增端口重试。"""
    handler_cls = UPnPHandler
    handler_cls.device = device
    handler_cls.registry = registry
    handler_cls.gena = gena
    handler_cls.scpd = _SCPDStore()

    for attempt in range(10):
        try:
            server = UPnPHTTPServer((ip, port + attempt), handler_cls)
            device.port = port + attempt
            break
        except OSError:
            if attempt == 9:
                raise
    thread = threading.Thread(target=server.serve_forever, daemon=True,
                              name="upnp-http-server")
    thread.start()
    return server, thread
