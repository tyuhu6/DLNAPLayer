"""SSDP 发现服务（UPnP 设备发现的 UDP 层）。

- 绑定本机 LAN IP 的 1900 端口（Windows 多网卡必须绑具体 IP，避免与系统 SSDP 服务冲突）
- 加入多播组 239.255.255.250，应答 M-SEARCH（组播与单播请求都应答）
- 按 MX 随机延迟回复，对 ssdp:all / rootdevice / uuid / 设备类型 / 服务类型逐条应答
- 周期发送 NOTIFY alive（默认每 30s），退出时发送 byebye
"""

import random
import socket
import struct
import threading
import time

SSDP_MULTICAST = "239.255.255.250"
SSDP_PORT = 1900
CACHE_AGE = 1800
ALIVE_INTERVAL = 30


class SSDPServer:
    def __init__(self, bind_ip: str, http_port: int, device_uuid: str, location_url: str) -> None:
        self.bind_ip = bind_ip
        self.http_port = http_port
        self.uuid = device_uuid
        self.location = location_url
        self.server_str = "Windows/10 UPnP/1.0 DLNAPLayer/1.0"
        self._sock: socket.socket | None = None
        self._running = False
        self._thread: threading.Thread | None = None
        self._timers: list[threading.Timer] = []

    # ---------- 目标列表 ----------
    def _targets(self) -> list[tuple[str, str]]:
        """(NT/ST, USN) 列表"""
        u = f"uuid:{self.uuid}"
        return [
            ("upnp:rootdevice", f"{u}::upnp:rootdevice"),
            (u, u),
            ("urn:schemas-upnp-org:device:MediaRenderer:1", f"{u}::urn:schemas-upnp-org:device:MediaRenderer:1"),
            ("urn:schemas-upnp-org:service:AVTransport:1", f"{u}::urn:schemas-upnp-org:service:AVTransport:1"),
            ("urn:schemas-upnp-org:service:ConnectionManager:1", f"{u}::urn:schemas-upnp-org:service:ConnectionManager:1"),
            ("urn:schemas-upnp-org:service:RenderingControl:1", f"{u}::urn:schemas-upnp-org:service:RenderingControl:1"),
        ]

    # ---------- 生命周期 ----------
    def start(self) -> None:
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True, name="ssdp-server")
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        for t in self._timers:
            t.cancel()
        self._timers.clear()
        if self._sock is not None:
            try:
                self._send_byebye()
            except Exception:
                pass
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None
        if self._thread is not None:
            self._thread.join(timeout=3)

    # ---------- 主循环 ----------
    def _run(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((self.bind_ip, SSDP_PORT))
        except OSError as exc:
            print(f"[ssdp] 绑定 {self.bind_ip}:1900 失败: {exc}")
            return
        mreq = struct.pack("4s4s", socket.inet_aton(SSDP_MULTICAST), socket.inet_aton(self.bind_ip))
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
        sock.settimeout(1.0)
        self._sock = sock

        try:
            for _ in range(3):
                self._send_alive()
                time.sleep(0.15)
        except Exception:
            pass
        last_alive = time.time()

        while self._running:
            try:
                data, addr = sock.recvfrom(4096)
            except socket.timeout:
                if time.time() - last_alive >= ALIVE_INTERVAL:
                    try:
                        self._send_alive()
                    except Exception:
                        pass
                    last_alive = time.time()
                continue
            except OSError:
                break
            if time.time() - last_alive >= ALIVE_INTERVAL:
                try:
                    self._send_alive()
                except Exception:
                    pass
                last_alive = time.time()
            try:
                self._handle_msearch(data, addr)
            except Exception:
                pass

    # ---------- M-SEARCH ----------
    def _handle_msearch(self, data: bytes, addr) -> None:
        try:
            text = data.decode("utf-8", "replace")
        except Exception:
            return
        head = text.split("\r\n", 1)[0].strip().upper()
        if not head.startswith("M-SEARCH"):
            return
        st = ""
        mx = 1.0
        for line in text.splitlines():
            if ":" not in line:
                continue
            key, _, value = line.partition(":")
            key = key.strip().upper()
            value = value.strip()
            if key == "ST":
                st = value
            elif key == "MX":
                try:
                    mx = max(0.5, min(float(value), 4.0))
                except ValueError:
                    mx = 1.0
        if not st:
            return
        matched = [t for t in self._targets() if t[0] == st]
        if st == "ssdp:all":
            matched = self._targets()
        if not matched:
            return
        for nt, usn in matched:
            delay = random.uniform(0.05, mx)
            timer = threading.Timer(delay, self._send_msearch_response, args=(nt, usn, addr))
            timer.daemon = True
            timer.start()
            self._timers.append(timer)

    def _send_msearch_response(self, st: str, usn: str, addr) -> None:
        if self._sock is None or not self._running:
            return
        msg = (
            "HTTP/1.1 200 OK\r\n"
            f"CACHE-CONTROL: max-age={CACHE_AGE}\r\n"
            "EXT:\r\n"
            f"LOCATION: {self.location}\r\n"
            f"SERVER: {self.server_str}\r\n"
            f"ST: {st}\r\n"
            f"USN: {usn}\r\n"
            "Content-Length: 0\r\n"
            "\r\n"
        )
        try:
            self._sock.sendto(msg.encode("utf-8"), addr)
        except OSError:
            pass

    # ---------- NOTIFY alive / byebye ----------
    def _notify_packet(self, nt: str, usn: str, nts: str, with_location: bool) -> bytes:
        lines = ["NOTIFY * HTTP/1.1", f"CACHE-CONTROL: max-age={CACHE_AGE}"]
        if with_location:
            lines.append(f"LOCATION: {self.location}")
        lines.append(f"NT: {nt}")
        lines.append(f"NTS: {nts}")
        lines.append(f"SERVER: {self.server_str}")
        lines.append(f"USN: {usn}")
        return ("\r\n".join(lines) + "\r\n\r\n").encode("utf-8")

    def _send_alive(self) -> None:
        if self._sock is None:
            return
        for nt, usn in self._targets():
            self._sock.sendto(self._notify_packet(nt, usn, "ssdp:alive", True),
                              (SSDP_MULTICAST, SSDP_PORT))

    def _send_byebye(self) -> None:
        if self._sock is None:
            return
        for nt, usn in self._targets():
            self._sock.sendto(self._notify_packet(nt, usn, "ssdp:byebye", False),
                              (SSDP_MULTICAST, SSDP_PORT))
