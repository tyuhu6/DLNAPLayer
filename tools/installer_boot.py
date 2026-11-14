"""安装程序引导体（单文件安装包的内核）。

构建流程见 tools/make_installer.py：先用 PyInstaller 把本文件打成一个小的
onefile exe，再把压缩好的程序目录以

    [引导 exe][payload.zip][8 字节长度][8 字节魔数]

的形式追加到 exe 末尾。运行安装包时本程序从自身尾部读出 payload 解压安装。

安装位置：安装界面上由用户自行选择（默认取可用空间最大的非系统盘，
          并记住上次选择；免管理员写入）
安装内容：程序文件 + 桌面/开始菜单快捷方式 + 卸载项（含 uninstall.cmd）

用法：
    DLNAPLayer-Setup.exe              图形界面安装（可选安装目录）
    DLNAPLayer-Setup.exe --silent     静默安装到默认/上次目录
    DLNAPLayer-Setup.exe --target DIR 安装到指定目录
"""

import os
import shutil
import subprocess
import sys
import tempfile
import threading
import zipfile
from pathlib import Path

APP_NAME = "DLNAPLayer"
APP_TITLE = "DLNA 投屏播放器"
APP_VERSION = "1.0.0"
SHORTCUT_NAME = "DLNA 投屏播放器"
MAGIC = b"DLNAPYLD"
TAIL = 16
SETTINGS_KEY = rf"Software\{APP_NAME}"

KEEP_FILES = ("settings.json", "crash.log")


# ---------------------------------------------------------------- 路径
def _saved_install_dir() -> str:
    """读取上次选择的安装目录（所在磁盘已不存在则忽略）。"""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, SETTINGS_KEY) as key:
            value, _ = winreg.QueryValueEx(key, "InstallDir")
    except OSError:
        return ""
    path = Path(str(value))
    if not path.anchor or not Path(path.anchor).exists():
        return ""
    return str(value)


def save_install_dir(path: Path) -> None:
    try:
        import winreg
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, SETTINGS_KEY) as key:
            winreg.SetValueEx(key, "InstallDir", 0, winreg.REG_SZ, str(path))
    except OSError:
        pass


def _roomiest_drive() -> Path:
    """返回可用空间最大的非系统盘（没有其他盘则退回用户目录）。"""
    best: tuple[Path, int] | None = None
    for letter in "DEFGHIJKLMNOPQRSTUVWXYZ":
        root = Path(f"{letter}:\\")
        if not root.exists():
            continue
        try:
            free = shutil.disk_usage(root).free
        except OSError:
            continue
        if best is None or free > best[1]:
            best = (root, free)
    if best is not None:
        return best[0]
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    return Path(base)


def default_install_dir() -> Path:
    """默认安装目录：上次选择 > 空间最大的非系统盘 > 用户目录。"""
    saved = _saved_install_dir()
    if saved:
        return Path(saved)
    return _roomiest_drive() / "Programs" / APP_NAME


# ---------------------------------------------------------------- 读 payload
def read_payload(dest: Path) -> Path:
    """从自身 exe 尾部取出 payload.zip 写到 dest。"""
    exe = Path(sys.executable)
    total = exe.stat().st_size
    if total < TAIL:
        raise RuntimeError("安装包不完整")
    with open(exe, "rb") as f:
        f.seek(total - TAIL)
        tail = f.read(TAIL)
        if tail[8:] != MAGIC:
            raise RuntimeError("安装数据校验失败，请重新下载安装包")
        length = int.from_bytes(tail[:8], "little")
        if length <= 0 or length > total - TAIL:
            raise RuntimeError("安装数据长度异常，请重新下载安装包")
        f.seek(total - TAIL - length)
        data = f.read(length)
    dest.write_bytes(data)
    return dest


# ---------------------------------------------------------------- 安装
def install(payload_zip: Path, target: Path, report) -> None:
    target.mkdir(parents=True, exist_ok=True)

    # 覆盖安装时保留用户数据
    saved: dict[str, bytes] = {}
    for name in KEEP_FILES:
        p = target / name
        if p.exists():
            try:
                saved[name] = p.read_bytes()
            except OSError:
                pass

    with zipfile.ZipFile(payload_zip) as zf:
        infos = [i for i in zf.infolist() if not i.is_dir()]
        total_bytes = sum(i.file_size for i in infos) or 1
        done = 0
        for info in infos:
            report(int(done / total_bytes * 88) + 2, "正在解压程序文件…")
            zf.extract(info, target)
            done += info.file_size

    for name, data in saved.items():
        try:
            (target / name).write_bytes(data)
        except OSError:
            pass

    report(92, "正在创建快捷方式…")
    create_shortcuts(target)

    report(96, "正在注册卸载信息…")
    write_uninstaller(target)
    register_uninstall(target)
    save_install_dir(target)

    report(100, "安装完成")


# ---------------------------------------------------------------- 快捷方式
def create_shortcuts(target: Path) -> None:
    exe = target / f"{APP_NAME}.exe"
    script = (
        "$ws = New-Object -ComObject WScript.Shell\n"
        "$dirs = @([Environment]::GetFolderPath('Desktop'), "
        "(Join-Path $env:APPDATA 'Microsoft\\Windows\\Start Menu\\Programs'))\n"
        "foreach ($d in $dirs) {\n"
        "  if (-not (Test-Path $d)) { continue }\n"
        f"  $p = Join-Path $d '{SHORTCUT_NAME}.lnk'\n"
        "  $s = $ws.CreateShortcut($p)\n"
        f"  $s.TargetPath = '{exe}'\n"
        f"  $s.WorkingDirectory = '{target}'\n"
        f"  $s.IconLocation = '{exe},0'\n"
        f"  $s.Description = '{APP_TITLE}'\n"
        "  $s.Save()\n"
        "}\n"
    )
    _run_powershell(script)


def _run_powershell(script: str) -> None:
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-Command", script],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=flags, timeout=60)


# ---------------------------------------------------------------- 卸载信息
UNINSTALL_CMD = """@echo off
chcp 65001 >nul
cd /d "%TEMP%"
taskkill /F /IM {exe} >nul 2>&1
timeout /t 1 /nobreak >nul
del "%USERPROFILE%\\Desktop\\{shortcut}.lnk" >nul 2>&1
del "%APPDATA%\\Microsoft\\Windows\\Start Menu\\Programs\\{shortcut}.lnk" >nul 2>&1
reg delete "HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\{name}" /f >nul 2>&1
reg delete "HKCU\\Software\\{name}" /f >nul 2>&1
rmdir /s /q "{target}"
"""


def write_uninstaller(target: Path) -> None:
    content = UNINSTALL_CMD.format(
        exe=f"{APP_NAME}.exe", shortcut=SHORTCUT_NAME, name=APP_NAME, target=target)
    (target / "uninstall.cmd").write_text(content, encoding="utf-8")


def register_uninstall(target: Path) -> None:
    import winreg

    exe = target / f"{APP_NAME}.exe"
    uninstall = target / "uninstall.cmd"
    size_kb = int(sum(f.stat().st_size for f in target.rglob("*") if f.is_file()) / 1024)
    key_path = rf"Software\Microsoft\Windows\CurrentVersion\Uninstall\{APP_NAME}"
    values = {
        "DisplayName": APP_TITLE,
        "DisplayVersion": APP_VERSION,
        "Publisher": "DLNAPLayer",
        "DisplayIcon": str(exe),
        "InstallLocation": str(target),
        "UninstallString": f'"{uninstall}"',
        "NoModify": 1,
        "NoRepair": 1,
        "EstimatedSize": size_kb,
    }
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path) as key:
        for name, value in values.items():
            if isinstance(value, int):
                winreg.SetValueEx(key, name, 0, winreg.REG_DWORD, value)
            else:
                winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)


# ---------------------------------------------------------------- 静默
def run_silent(target: Path) -> int:
    tmpdir = Path(tempfile.mkdtemp(prefix="dlna_setup_"))
    try:
        payload = read_payload(tmpdir / "payload.zip")
        print("[setup] 正在安装到", target)
        install(payload, target, lambda _p, msg: print("[setup]", msg))
        print("[setup] 安装完成")
        return 0
    except Exception as exc:  # noqa: BLE001
        print("[setup] 安装失败:", exc)
        return 1
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ---------------------------------------------------------------- 图形界面
def run_gui(target: Path) -> int:
    from PySide6.QtCore import QPoint, Qt, QTimer, Signal
    from PySide6.QtGui import QColor, QLinearGradient, QPainter
    from PySide6.QtWidgets import (
        QApplication, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton,
        QVBoxLayout, QWidget,
    )

    class _CloseLabel(QLabel):
        def __init__(self, parent=None) -> None:
            super().__init__("✕", parent)
            self.setFixedSize(28, 28)
            self.setAlignment(Qt.AlignCenter)
            self.setCursor(Qt.PointingHandCursor)
            self.setStyleSheet(
                "color:rgba(255,255,255,150);font-size:13px;background:transparent;")

        def mouseReleaseEvent(self, event) -> None:
            if event.button() == Qt.LeftButton:
                self.window().close()
            super().mouseReleaseEvent(event)

    class Installer(QWidget):
        # 工作线程 -> UI 线程
        progress = Signal(int, str)
        finished = Signal(bool, str)

        def __init__(self) -> None:
            super().__init__()
            self.setFixedSize(580, 360)
            self.setWindowFlags(Qt.FramelessWindowHint | Qt.Window)
            self.setWindowTitle(f"{APP_TITLE} 安装程序")
            self._pct = 0
            self._drag: QPoint | None = None
            self._target = Path(target)
            self._installing = False

            self.title = QLabel(APP_TITLE, self)
            self.title.setStyleSheet(
                "color:#FFFFFF;font-size:20px;font-weight:300;background:transparent;")
            self.sub = QLabel(f"版本 {APP_VERSION} · 请选择安装位置", self)
            self.sub.setStyleSheet(
                "color:rgba(255,255,255,110);font-size:12px;background:transparent;")

            path_label = QLabel("安装位置", self)
            path_label.setStyleSheet(
                "color:rgba(255,255,255,130);font-size:12px;background:transparent;")

            self.path_edit = QLineEdit(str(self._target), self)
            self.path_edit.setFixedHeight(32)
            self.path_edit.setStyleSheet(
                "QLineEdit{background:rgba(255,255,255,14);border:1px solid "
                "rgba(255,255,255,24);border-radius:7px;color:#FFFFFF;"
                "font-size:12px;padding:0 10px;selection-background-color:"
                "rgba(255,255,255,60);}"
                "QLineEdit:focus{border:1px solid rgba(255,255,255,70);}")

            self.browse_btn = QPushButton("浏览…", self)
            self.browse_btn.setFixedSize(72, 32)
            self.browse_btn.setCursor(Qt.PointingHandCursor)
            self.browse_btn.setStyleSheet(
                "QPushButton{background:transparent;border:1px solid "
                "rgba(255,255,255,45);border-radius:7px;color:#EDEDEF;font-size:12px;}"
                "QPushButton:hover{background:rgba(255,255,255,16);}"
                "QPushButton:pressed{background:rgba(255,255,255,30);}")
            self.browse_btn.clicked.connect(self._browse)

            self.start_btn = QPushButton("开始安装", self)
            self.start_btn.setFixedSize(104, 32)
            self.start_btn.setCursor(Qt.PointingHandCursor)
            self.start_btn.setStyleSheet(
                "QPushButton{background:#FFFFFF;border:none;border-radius:7px;"
                "color:#0A0A0C;font-size:12px;font-weight:500;}"
                "QPushButton:hover{background:#E8E8EA;}"
                "QPushButton:pressed{background:#CFCFD2;}"
                "QPushButton:disabled{background:rgba(255,255,255,60);color:rgba(0,0,0,110);}")
            self.start_btn.clicked.connect(self._begin)

            self.status = QLabel("准备就绪", self)
            self.status.setStyleSheet(
                "color:rgba(255,255,255,160);font-size:12px;background:transparent;")

            self._close = _CloseLabel(self)

            top = QHBoxLayout()
            top.addStretch(1)
            top.addWidget(self._close)

            path_row = QHBoxLayout()
            path_row.setSpacing(8)
            path_row.addWidget(self.path_edit, 1)
            path_row.addWidget(self.browse_btn)

            bottom = QHBoxLayout()
            bottom.addWidget(self.status, 1)
            bottom.addWidget(self.start_btn)

            lay = QVBoxLayout(self)
            lay.setContentsMargins(34, 18, 24, 34)
            lay.setSpacing(0)
            lay.addLayout(top)
            lay.addSpacing(14)
            lay.addWidget(self.title)
            lay.addSpacing(6)
            lay.addWidget(self.sub)
            lay.addSpacing(26)
            lay.addWidget(path_label)
            lay.addSpacing(8)
            lay.addLayout(path_row)
            lay.addStretch(1)
            lay.addLayout(bottom)

            self.progress.connect(self._on_progress)
            self.finished.connect(self._on_finished)

        # ---- 选择目录 ----
        def _browse(self) -> None:
            start = self.path_edit.text().strip() or str(self._target)
            chosen = QFileDialog.getExistingDirectory(self, "选择安装位置", start)
            if chosen:
                self.path_edit.setText(str(Path(chosen) / APP_NAME))

        def _resolve_target(self) -> Path | None:
            text = self.path_edit.text().strip().strip('"')
            if not text:
                self.status.setText("请先选择安装位置")
                return None
            path = Path(text)
            if path.drive and path.parent == path:  # 只有盘符根目录
                path = path / APP_NAME
            try:
                path.mkdir(parents=True, exist_ok=True)
                probe = path / ".write_test"
                probe.write_bytes(b"1")
                probe.unlink()
            except OSError as exc:
                self.status.setText(f"该位置不可写入：{exc}")
                return None
            self.path_edit.setText(str(path))
            return path

        # ---- 开始安装 ----
        def _begin(self) -> None:
            if self._installing:
                return
            chosen = self._resolve_target()
            if chosen is None:
                return
            self._target = chosen
            self._installing = True
            self.path_edit.setEnabled(False)
            self.browse_btn.setEnabled(False)
            self.start_btn.setEnabled(False)
            self.start_btn.setText("安装中…")
            self.sub.setText(f"版本 {APP_VERSION} · 安装到 {chosen}")
            self.status.setText("准备安装…")
            threading.Thread(target=self._work, daemon=True).start()

        # ---- 后台安装 ----
        def _work(self) -> None:
            tmpdir = Path(tempfile.mkdtemp(prefix="dlna_setup_"))
            try:
                self.progress.emit(1, "正在读取安装数据…")
                payload = read_payload(tmpdir / "payload.zip")
                install(payload, self._target, lambda p, m: self.progress.emit(p, m))
                self.finished.emit(True, "安装完成，正在启动…")
            except Exception as exc:  # noqa: BLE001
                self.finished.emit(False, f"安装失败：{exc}")
            finally:
                shutil.rmtree(tmpdir, ignore_errors=True)

        def _on_progress(self, pct: int, msg: str) -> None:
            self._pct = max(self._pct, min(100, pct))
            self.status.setText(msg)
            self.update()

        def _on_finished(self, ok: bool, msg: str) -> None:
            self._pct = 100 if ok else self._pct
            self.status.setText(msg)
            self.update()
            if ok:
                self.start_btn.setText("安装完成")
                QTimer.singleShot(900, self._launch)
            else:
                self._installing = False
                self.path_edit.setEnabled(True)
                self.browse_btn.setEnabled(True)
                self.start_btn.setEnabled(True)
                self.start_btn.setText("重试")

        def _launch(self) -> None:
            exe = self._target / f"{APP_NAME}.exe"
            try:
                subprocess.Popen([str(exe)], cwd=str(self._target))
            except Exception:  # noqa: BLE001
                pass
            self.close()

        # ---- 自绘 ----
        def paintEvent(self, event) -> None:
            p = QPainter(self)
            p.setRenderHint(QPainter.Antialiasing)
            grad = QLinearGradient(0, 0, 0, self.height())
            grad.setColorAt(0.0, QColor("#141416"))
            grad.setColorAt(1.0, QColor("#09090A"))
            p.fillRect(self.rect(), grad)

            # 细进度条（屏幕最下方）
            x, y, w = 34, self.height() - 18, self.width() - 58
            p.fillRect(x, y, w, 2, QColor(255, 255, 255, 30))
            filled = int(w * self._pct / 100)
            if filled > 0:
                p.fillRect(x, y, filled, 2, QColor(255, 255, 255, 235))
            p.end()

        # ---- 拖动 ----
        def mousePressEvent(self, event) -> None:
            if event.button() == Qt.LeftButton:
                self._drag = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

        def mouseMoveEvent(self, event) -> None:
            if self._drag is not None and event.buttons() & Qt.LeftButton:
                self.move(event.globalPosition().toPoint() - self._drag)

        def mouseReleaseEvent(self, event) -> None:
            self._drag = None

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(True)
    win = Installer()
    win.show()
    app.exec()
    return 0


# ---------------------------------------------------------------- 入口
def main() -> int:
    args = sys.argv[1:]
    target = default_install_dir()
    if "--target" in args:
        idx = args.index("--target")
        if idx + 1 < len(args):
            target = Path(args[idx + 1]).resolve()
    if "--silent" in args:
        return run_silent(target)
    return run_gui(target)


if __name__ == "__main__":
    sys.exit(main())
