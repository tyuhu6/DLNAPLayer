"""生成单文件安装程序 dist/DLNAPLayer-Setup.exe。

步骤：
1. 把 dist/DLNAPLayer（由 tools/build_exe.py 产出）打成 payload.zip
2. 用 PyInstaller 把 tools/installer_boot.py 打成小的 onefile 引导 exe
3. 把 payload.zip 追加到引导 exe 尾部，形成单文件安装包

用法: python tools/make_installer.py
"""

import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
DIST = PROJECT / "dist"
APP_DIR = DIST / "DLNAPLayer"
WORK = PROJECT / "build" / "installer"
BOOT_EXE = DIST / "_setup" / "DLNAPLayer-Setup.exe"
SETUP_EXE = DIST / "DLNAPLayer-Setup.exe"
MAGIC = b"DLNAPYLD"


def make_payload() -> Path:
    if not (APP_DIR / "DLNAPLayer.exe").exists():
        raise SystemExit("[setup] 未找到 dist/DLNAPLayer，请先运行 tools/build_exe.py")
    payload = WORK / "payload.zip"
    payload.parent.mkdir(parents=True, exist_ok=True)
    print("[setup] 正在压缩程序目录…")
    count = 0
    with zipfile.ZipFile(payload, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for path in sorted(APP_DIR.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(APP_DIR).as_posix())
                count += 1
    print(f"[setup] payload.zip: {payload.stat().st_size / 1024 / 1024:.1f} MB / {count} 个文件")
    return payload


def build_boot() -> Path:
    print("[setup] 正在编译安装程序引导体…")
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--onefile",
        "--noconsole",
        "--name", "DLNAPLayer-Setup",
        "--icon", str(PROJECT / "app.ico"),
        "--distpath", str(DIST / "_setup"),
        "--workpath", str(WORK / "pyi"),
        "--specpath", str(WORK),
        "--exclude-module", "tkinter",
        str(PROJECT / "tools" / "installer_boot.py"),
    ]
    result = subprocess.run(cmd, cwd=str(PROJECT))
    if result.returncode != 0 or not BOOT_EXE.exists():
        raise SystemExit("[setup] 引导体编译失败")
    return BOOT_EXE


def attach(payload: Path, boot: Path) -> None:
    print("[setup] 正在合成单文件安装包…")
    if SETUP_EXE.exists():
        SETUP_EXE.unlink()
    shutil.copyfile(boot, SETUP_EXE)
    length = payload.stat().st_size
    with open(SETUP_EXE, "ab") as f:
        with open(payload, "rb") as src:
            shutil.copyfileobj(src, f, length=8 * 1024 * 1024)
        f.write(length.to_bytes(8, "little"))
        f.write(MAGIC)


def main() -> int:
    payload = make_payload()
    boot = build_boot()
    attach(payload, boot)
    size = SETUP_EXE.stat().st_size / 1024 / 1024
    print(f"[setup] 完成: {SETUP_EXE}  ({size:.1f} MB)")
    print(f"[setup] 免安装版: {APP_DIR / 'DLNAPLayer.exe'}（需保留整个 DLNAPLayer 目录）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
