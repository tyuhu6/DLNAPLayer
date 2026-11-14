"""生成应用图标 app.ico：深色圆角底 + 白色电视图形。

用法: python tools/make_icon.py（需 PySide6 + Pillow）
"""

import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from PySide6.QtCore import Qt, QBuffer
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import QApplication

from ui import icons

OUT_PNG = PROJECT / "tools" / "icon_256.png"
OUT_ICO = PROJECT / "app.ico"


def render_base() -> QPixmap:
    size = 256
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    # 深色圆角底
    path = QPainterPath()
    path.addRoundedRect(8, 8, size - 16, size - 16, 52, 52)
    p.fillPath(path, QColor("#16161A"))
    # 白色电视图形（居中，占约 62%）
    tv = icons.pixmap("tv", "#FFFFFF", 160, 1.0)
    p.drawPixmap((size - 160) // 2, (size - 160) // 2, tv)
    p.end()
    return pm


def main() -> int:
    app = QApplication(sys.argv)  # offscreen 不开窗
    pm = render_base()
    buf = QBuffer()
    buf.open(QBuffer.WriteOnly)
    pm.save(buf, "PNG")
    OUT_PNG.write_bytes(bytes(buf.data()))

    from PIL import Image
    import io
    img = Image.open(io.BytesIO(bytes(buf.data())))
    img.save(OUT_ICO, format="ICO", sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)])
    print(f"OK: {OUT_ICO} ({OUT_ICO.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
