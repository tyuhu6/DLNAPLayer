"""验证 SSDP 发现：本机发送 M-SEARCH，断言能收到本设备的应答。

用法: 先运行 main.py，然后  python tests/test_ssdp.py
"""

import socket
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import load, get_lan_ip  # noqa: E402

SSDP_ADDR = ("239.255.255.250", 1900)
ST_LIST = [
    "ssdp:all",
    "upnp:rootdevice",
    "urn:schemas-upnp-org:device:MediaRenderer:1",
    "urn:schemas-upnp-org:service:AVTransport:1",
]


def msearch(sock: socket.socket, st: str) -> None:
    msg = (
        "M-SEARCH * HTTP/1.1\r\n"
        f"HOST: 239.255.255.250:1900\r\n"
        f'MAN: "ssdp:discover"\r\n'
        "MX: 2\r\n"
        f"ST: {st}\r\n"
        "\r\n"
    )
    sock.sendto(msg.encode(), SSDP_ADDR)


def main() -> int:
    settings = load()
    my_uuid = settings["uuid"]
    ip = settings.get("bind_ip") or get_lan_ip()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(6.0)
    sock.bind((ip, 0))

    responses = {}  # st -> location
    for st in ST_LIST:
        msearch(sock, st)
    print("[test] M-SEARCH 已发送，等待应答(6s)...")
    try:
        while True:
            data, addr = sock.recvfrom(4096)
            text = data.decode("utf-8", "replace")
            if not text.startswith("HTTP/1.1 200"):
                continue
            st = loc = ""
            for line in text.splitlines():
                k, _, v = line.partition(":")
                k = k.strip().upper()
                if k == "ST":
                    st = v.strip()
                elif k == "LOCATION":
                    loc = v.strip()
            if st and st not in responses:
                responses[st] = (loc, addr[0])
                print(f"[test] 应答 ST={st} LOCATION={loc} 来自 {addr[0]}")
    except socket.timeout:
        pass

    expected = {
        "upnp:rootdevice",
        "urn:schemas-upnp-org:device:MediaRenderer:1",
        "urn:schemas-upnp-org:service:AVTransport:1",
        "urn:schemas-upnp-org:service:ConnectionManager:1",
        "urn:schemas-upnp-org:service:RenderingControl:1",
    }
    found = set(responses.keys())
    missing = expected - found
    print(f"\n[test] 收到 {len(responses)} 类应答，缺失: {missing or '无'}")

    # 同一局域网可能存在其他 UPnP 设备（路由器/其他投屏软件），
    # 逐个拉取 desc.xml，按 DLNAPLayer 特征（manufacturer/model）识别本设备
    ours = None
    for loc, _ip in set(responses.values()):
        try:
            with urllib.request.urlopen(loc, timeout=5) as resp:
                body = resp.read().decode("utf-8", "replace")
            if "MediaRenderer" in body and "DLNAPLayer" in body:
                ours = loc
                break
        except Exception as exc:
            print(f"[test] 拉取 {loc} 失败: {exc}")
    if ours:
        print(f"[test] 本设备 desc.xml 可达: {ours}")
    else:
        print("[test] FAIL: 未找到 DLNAPLayer 设备描述")
        return 1

    if missing:
        print("[test] FAIL: SSDP 应答不完整（检查 main.py 是否在运行、防火墙是否放行 UDP 1900）")
        return 1
    print("[test] PASS: SSDP 发现正常")
    return 0


if __name__ == "__main__":
    sys.exit(main())
