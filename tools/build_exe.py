"""打包 DLNAPLayer 为独立 Windows 程序（PyInstaller onedir）。

产物: dist/DLNAPLayer/DLNAPLayer.exe（整个 DLNAPLayer 文件夹需一起拷贝）
用法: python tools/build_exe.py
"""

import shutil
import subprocess
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
DIST = PROJECT / "dist" / "DLNAPLayer"

CMD = [
    sys.executable, "-m", "PyInstaller",
    "--noconfirm",
    "--clean",
    "--noconsole",               # 无黑窗口
    "--name", "DLNAPLayer",
    "--icon", str(PROJECT / "app.ico"),
    # 运行时窗口/任务栏图标（main._app_icon 从 _MEIPASS 读取）
    "--add-data", f"{PROJECT / 'app.ico'};.",
    # mpv 运行库 → _internal/player/（与 mpv_controller 的 __file__ 同目录查找逻辑一致）
    "--add-binary", f"{PROJECT / 'player' / 'libmpv-2.dll'};player",
    # UPnP 协议资源（desc.xml + SCPD）
    "--add-data", f"{PROJECT / 'upnp' / 'resources'};upnp/resources",
    # 排除未使用的 Qt 大模块，减小体积
    "--exclude-module", "PySide6.QtWebEngineCore",
    "--exclude-module", "PySide6.QtWebEngineWidgets",
    "--exclude-module", "PySide6.QtQml",
    "--exclude-module", "PySide6.QtQuick",
    "--exclude-module", "PySide6.QtQuick3D",
    "--exclude-module", "PySide6.Qt3DCore",
    "--exclude-module", "PySide6.QtCharts",
    "--exclude-module", "PySide6.QtDataVisualization",
    "--exclude-module", "PySide6.QtPdf",
    "--exclude-module", "PySide6.QtWebChannel",
    "--exclude-module", "PySide6.QtWebSockets",
    "--exclude-module", "PySide6.QtPositioning",
    "--exclude-module", "PySide6.QtLocation",
    "--exclude-module", "PySide6.QtSensors",
    "--exclude-module", "PySide6.QtSerialPort",
    "--exclude-module", "PySide6.QtBluetooth",
    "--exclude-module", "PySide6.QtNfc",
    "--exclude-module", "PySide6.QtTest",
    "--exclude-module", "tkinter",
    "--exclude-module", "py7zr",
    str(PROJECT / "main.py"),
]


def _copy_vendor() -> None:
    """把 UxPlay 运行库复制到 exe 同级 vendor/uxplay（main._vendor_dir 的查找位置）。"""
    src = PROJECT / "vendor" / "uxplay"
    if not src.exists():
        print("[build] 跳过 vendor/uxplay（不存在）")
        return
    dst = DIST / "vendor" / "uxplay"
    if dst.exists():
        shutil.rmtree(dst, ignore_errors=True)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns("compile_commands.json"))
    size = sum(f.stat().st_size for f in dst.rglob("*") if f.is_file()) / 1024 / 1024
    print(f"[build] 已复制 UxPlay 运行库: {size:.0f} MB")


def main() -> int:
    print("[build] 开始 PyInstaller 打包...")
    result = subprocess.run(CMD, cwd=str(PROJECT))
    if result.returncode != 0:
        print("[build] FAIL")
        return result.returncode
    exe = DIST / "DLNAPLayer.exe"
    if not exe.exists():
        print("[build] FAIL: 未找到产物 exe")
        return 1
    _copy_vendor()
    size_mb = exe.stat().st_size / 1024 / 1024
    total = sum(f.stat().st_size for f in DIST.rglob("*") if f.is_file()) / 1024 / 1024
    print(f"[build] 完成: {exe}")
    print(f"[build] exe {size_mb:.1f} MB / 整目录 {total:.1f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
