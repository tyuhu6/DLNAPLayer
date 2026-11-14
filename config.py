"""配置读写（文件存储 settings.json）与网络工具。"""

import json
import socket
import uuid as uuidlib
from pathlib import Path

SETTINGS_PATH = Path(__file__).resolve().parent / "settings.json"

DEFAULTS = {
    "device_name": "我的电脑",
    "http_port": 9090,
    "bind_ip": "",
    "volume": 80,
    "intro_skip": 0,
    "uuid": "",
}


def load() -> dict:
    data = dict(DEFAULTS)
    try:
        with open(SETTINGS_PATH, encoding="utf-8") as f:
            stored = json.load(f)
        if isinstance(stored, dict):
            data.update({k: v for k, v in stored.items() if k in DEFAULTS})
    except (OSError, ValueError):
        pass
    if not data["uuid"]:
        data["uuid"] = str(uuidlib.uuid4())
        save(data)
    return data


def save(data: dict) -> None:
    try:
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


def get_lan_ip() -> str:
    """通过 UDP connect 探测默认路由出口 IP（不实际发包）。"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.5)
        try:
            s.connect(("223.5.5.5", 80))
            ip = s.getsockname()[0]
        finally:
            s.close()
        if ip and not ip.startswith("127."):
            return ip
    except OSError:
        pass
    try:
        return socket.gethostbyname(socket.gethostname())
    except OSError:
        return "127.0.0.1"
