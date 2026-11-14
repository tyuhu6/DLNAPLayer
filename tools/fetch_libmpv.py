"""下载 libmpv-2.dll（python-mpv 运行依赖）。

从 zhongfly/mpv-winbuild 最新 Release 下载 mpv-dev-x86_64 构建包，
用 py7zr 解出 libmpv-2.dll 放到 player/ 目录。
GitHub 直连失败时自动尝试国内加速镜像。

用法: python tools/fetch_libmpv.py
"""

import json
import sys
import tempfile
import urllib.request
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
DEST_DIR = PROJECT / "player"
DEST_DLL = DEST_DIR / "libmpv-2.dll"

API_URL = "https://api.github.com/repos/zhongfly/mpv-winbuild/releases/latest"
# 直连失败时的镜像前缀（镜像会代理 github release 下载链接）
MIRRORS = ["", "https://ghfast.top/", "https://gh-proxy.com/"]


def fetch_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "DLNAPLayer/1.0", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def pick_asset(release: dict) -> dict:
    candidates = []
    for asset in release.get("assets", []):
        name = asset["name"]
        if name.startswith("mpv-dev-x86_64-") and name.endswith(".7z") \
                and "-v3" not in name and "lgpl" not in name and "arm" not in name:
            candidates.append(asset)
    if not candidates:
        raise RuntimeError("release 中未找到 mpv-dev-x86_64-*.7z 资产")
    candidates.sort(key=lambda a: a["name"], reverse=True)
    return candidates[0]


def download(url: str, dest: Path) -> Path:
    last_err = None
    for mirror in MIRRORS:
        target = f"{mirror}{url}"
        try:
            print(f"[fetch] 下载: {target}")
            req = urllib.request.Request(target, headers={"User-Agent": "DLNAPLayer/1.0"})
            with urllib.request.urlopen(req, timeout=60) as resp, open(dest, "wb") as f:
                total = 0
                while True:
                    chunk = resp.read(1024 * 512)
                    if not chunk:
                        break
                    f.write(chunk)
                    total += len(chunk)
            print(f"[fetch] 完成: {total / 1024 / 1024:.1f} MB")
            return dest
        except Exception as exc:  # 换下一个镜像
            last_err = exc
            print(f"[fetch] 失败: {exc}")
    raise RuntimeError(f"所有镜像均失败: {last_err}")


def _find_7z() -> Path:
    """优先找系统已装的 7z，否则下载官方独立控制台 7zr.exe（仅支持 7z，约 600KB）。"""
    for cand in (Path("C:/Program Files/7-Zip/7z.exe"), Path("C:/Program Files (x86)/7-Zip/7z.exe")):
        if cand.exists():
            return cand
    dest = Path(tempfile.gettempdir()) / "dlnaplayer_7zr.exe"
    if not dest.exists():
        print("[fetch] 下载 7zr.exe（7-Zip 独立控制台）...")
        req = urllib.request.Request("https://www.7-zip.org/a/7zr.exe",
                                     headers={"User-Agent": "DLNAPLayer/1.0"})
        with urllib.request.urlopen(req, timeout=60) as resp, open(dest, "wb") as f:
            f.write(resp.read())
    return dest


def extract(archive: Path, member: str, dest: Path) -> None:
    import subprocess
    workdir = archive.parent / "extract"
    workdir.mkdir(exist_ok=True)
    sevenzip = _find_7z()
    cmd = [str(sevenzip), "x", f"-y", f"-o{workdir}", str(archive), member, "-r"]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"7z 解压失败(rc={result.returncode}): {result.stdout[-500:]} {result.stderr[-200:]}")
    src = workdir / member
    if not src.exists():
        found = [str(p.relative_to(workdir)) for p in workdir.rglob("*")][:20]
        raise RuntimeError(f"解压后未找到 {member}，目录内容: {found}")
    import shutil
    shutil.copy2(src, dest)
    shutil.rmtree(workdir, ignore_errors=True)


def main() -> int:
    if DEST_DLL.exists():
        print(f"[fetch] 已存在: {DEST_DLL}，跳过下载")
        return 0

    print("[fetch] 查询最新 release...")
    release = fetch_json(API_URL)
    asset = pick_asset(release)
    print(f"[fetch] 选中: {asset['name']}")

    import shutil
    import tempfile
    work = Path(tempfile.mkdtemp(prefix="dlnaplayer_mpv_"))
    try:
        archive = download(asset["browser_download_url"], work / asset["name"])
        extract(archive, "libmpv-2.dll", DEST_DLL)
    finally:
        shutil.rmtree(work, ignore_errors=True)

    size = DEST_DLL.stat().st_size / 1024 / 1024
    print(f"[fetch] 已放置: {DEST_DLL} ({size:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
