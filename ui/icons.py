"""内置黑白简约 SVG 图标（Material 风格，运行时渲染为 QIcon）。"""

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

_SVGS = {
    "play": '<svg viewBox="0 0 24 24"><path fill="{c}" d="M8 5v14l11-7z"/></svg>',
    "pause": '<svg viewBox="0 0 24 24"><path fill="{c}" d="M7 5h4v14H7zM13 5h4v14h-4z"/></svg>',
    "stop": '<svg viewBox="0 0 24 24"><rect x="6" y="6" width="12" height="12" rx="1.5" fill="{c}"/></svg>',
    "volume": '<svg viewBox="0 0 24 24"><path fill="{c}" d="M3 9v6h4l5 5V4L7 9H3z"/>'
              '<path fill="none" stroke="{c}" stroke-width="2" stroke-linecap="round" '
              'd="M15.5 8.5a5 5 0 0 1 0 7M18.5 6a9 9 0 0 1 0 12"/></svg>',
    "volume_mute": '<svg viewBox="0 0 24 24"><path fill="{c}" d="M3 9v6h4l5 5V4L7 9H3z"/>'
                   '<path fill="none" stroke="{c}" stroke-width="2" stroke-linecap="round" '
                   'd="M16 9.5l5 5M21 9.5l-5 5"/></svg>',
    "fullscreen": '<svg viewBox="0 0 24 24"><path fill="none" stroke="{c}" stroke-width="2" '
                  'stroke-linecap="round" d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/></svg>',
    "fullscreen_exit": '<svg viewBox="0 0 24 24"><path fill="none" stroke="{c}" stroke-width="2" '
                       'stroke-linecap="round" d="M9 4v5H4M15 4v5h5M9 20v-5H4M15 20v-5h5"/></svg>',
    "close": '<svg viewBox="0 0 24 24"><path fill="none" stroke="{c}" stroke-width="2" '
             'stroke-linecap="round" d="M6 6l12 12M18 6L6 18"/></svg>',
    "minimize": '<svg viewBox="0 0 24 24"><path fill="none" stroke="{c}" stroke-width="2" '
                'stroke-linecap="round" d="M6 12h12"/></svg>',
    "maximize": '<svg viewBox="0 0 24 24"><rect x="5.5" y="5.5" width="13" height="13" rx="1.6" '
                'fill="none" stroke="{c}" stroke-width="1.7"/></svg>',
    "restore": '<svg viewBox="0 0 24 24"><rect x="4.5" y="8.5" width="11" height="11" rx="1.6" '
               'fill="none" stroke="{c}" stroke-width="1.7"/>'
               '<path fill="none" stroke="{c}" stroke-width="1.7" stroke-linejoin="round" '
               'd="M8.5 8.5V6.1c0-.9.7-1.6 1.6-1.6h9.2c.9 0 1.6.7 1.6 1.6v9.2'
               'c0 .9-.7 1.6-1.6 1.6h-2.4"/></svg>',
    "cast": '<svg viewBox="0 0 24 24"><path fill="{c}" d="M21 3H3c-1.1 0-2 .9-2 2v3h2V5h18v14h-7v2h7'
            'c1.1 0 2-.9 2-2V5c0-1.1-.9-2-2-2zM1 18v3h3c0-1.7-1.3-3-3-3zm0-4v2c2.8 0 5 2.2 5 5h2'
            'c0-3.9-3.1-7-7-7zm0-4v2c5 0 9 4 9 9h2c0-6.1-4.9-11-11-11z"/></svg>',
    "cast_off": '<svg viewBox="0 0 24 24"><path fill="{c}" d="M21 3H3c-1.1 0-2 .9-2 2v3h2V5h18v14h-7v2h7'
                'c1.1 0 2-.9 2-2V5c0-1.1-.9-2-2-2zM1 18v3h3c0-1.7-1.3-3-3-3zm0-4v2c2.8 0 5 2.2 5 5h2'
                'c0-3.9-3.1-7-7-7zm0-4v2c5 0 9 4 9 9h2c0-6.1-4.9-11-11-11z"/>'
                '<path fill="none" stroke="{c}" stroke-width="1.6" stroke-linecap="round" '
                'd="M3.5 3.5l17 17"/></svg>',
    "tv": '<svg viewBox="0 0 24 24"><path fill="{c}" d="M21 3H3c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h5v2h8'
          'v-2h5c1.1 0 2-.9 2-2V5c0-1.1-.9-2-2-2zm0 14H3V5h18v12z"/></svg>',
    "mirror": '<svg viewBox="0 0 24 24"><rect x="2" y="4.2" width="12.6" height="9.6" rx="2" '
              'fill="{c}"/><rect x="15.6" y="9.2" width="6.4" height="10.6" rx="2" '
              'fill="{c}"/></svg>',
    "chevron_right": '<svg viewBox="0 0 24 24"><path fill="{c}" '
                     'd="M9.2 4.8l7.2 7.2-7.2 7.2-1.60-1.60 5.6-5.6-5.6-5.6z"/></svg>',
    "aspect": '<svg viewBox="0 0 24 24"><path fill="{c}" d="M19 12h-2v3h-3v2h5v-5zM7 9h3V7H5v5h2V9z'
              'm14-6H3c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h18c1.1 0 2-.9 2-2V5c0-1.1-.9-2-2-2z'
              'm0 16.01H3V4.99h18v14.02z"/></svg>',
    "skip_intro": '<svg viewBox="0 0 24 24">'
                  '<path fill="{c}" fill-rule="evenodd" d="M12 5.2a8.4 8.4 0 1 0 0 16.8 8.4 8.4 0 0 0'
                  ' 0-16.8zm0 2a6.4 6.4 0 1 1 0 12.8 6.4 6.4 0 0 1 0-12.8z"/>'
                  '<rect x="9.4" y="1.8" width="5.2" height="1.9" rx=".95" fill="{c}"/>'
                  '<rect x="11.1" y="9.4" width="1.8" height="5.6" rx=".9" fill="{c}"/>'
                  '<rect x="12" y="13.2" width="4.4" height="1.8" rx=".9" fill="{c}"/></svg>',
    "help": '<svg viewBox="0 0 24 24"><path fill="{c}" fill-rule="evenodd" '
            'd="M12 2.4a9.6 9.6 0 1 0 0 19.2 9.6 9.6 0 0 0 0-19.2zm0 1.8a7.8 7.8 0 1 1 0 15.6'
            ' 7.8 7.8 0 0 1 0-15.6z"/>'
            '<path fill="{c}" d="M12.1 7.1c-1.8 0-3.1 1.1-3.2 2.8h1.8c.1-.8.6-1.3 1.4-1.3'
            '.8 0 1.3.5 1.3 1.1 0 .6-.3 1-1 1.4-.9.6-1.3 1.1-1.3 2.1v.5h1.8v-.4'
            'c0-.6.3-.9 1-1.4.9-.6 1.4-1.3 1.4-2.3 0-1.5-1.3-2.5-3.2-2.5z'
            'm-.1 8.5a1.15 1.15 0 1 0 0 2.3 1.15 1.15 0 0 0 0-2.3z"/></svg>',
    "settings": '<svg viewBox="0 0 24 24"><path fill="{c}" d="M19.14 12.94c.04-.3.06-.61.06-.94'
                's-.02-.64-.07-.94l2.03-1.58c.18-.14.23-.41.12-.61l-1.92-3.32c-.12-.22-.37-.29-.59-.22'
                'l-2.39.96c-.5-.38-1.03-.7-1.62-.94l-.36-2.54c-.04-.24-.24-.41-.48-.41h-3.84'
                'c-.24 0-.43.17-.47.41l-.36 2.54c-.59.24-1.13.57-1.62.94l-2.39-.96'
                'c-.22-.08-.47 0-.59.22L2.74 8.87c-.12.21-.08.47.12.61l2.03 1.58c-.05.3-.09.63-.09.94'
                's.02.64.07.94l-2.03 1.58c-.18.14-.23.41-.12.61l1.92 3.32c.12.22.37.29.59.22l2.39-.96'
                'c.5.38 1.03.7 1.62.94l.36 2.54c.05.24.24.41.48.41h3.84c.24 0 .44-.17.47-.41l.36-2.54'
                'c.59-.24 1.13-.56 1.62-.94l2.39.96c.22.08.47 0 .59-.22l1.92-3.32c.12-.22.07-.47-.12'
                '-.61l-2.01-1.58zM12 15.6c-1.98 0-3.6-1.62-3.6-3.6s1.62-3.6 3.6-3.6 3.6 1.62 3.6 3.6'
                '-1.62 3.6-3.6 3.6z"/></svg>',
}

_cache: dict[tuple[str, str, int, int], QIcon] = {}


def _render(svg: str, size: int, dpr: float) -> QPixmap:
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    pm = QPixmap(int(size * dpr), int(size * dpr))
    pm.fill(Qt.transparent)
    painter = QPainter(pm)
    renderer.render(painter)
    painter.end()
    pm.setDevicePixelRatio(dpr)
    return pm


def icon(name: str, color: str = "#FFFFFF", size: int = 24, dpr: int = 2) -> QIcon:
    key = (name, color, size, dpr)
    if key not in _cache:
        _cache[key] = QIcon(_render(_SVGS[name].replace("{c}", color), size, dpr))
    return _cache[key]


def pixmap(name: str, color: str = "#FFFFFF", size: int = 24, dpr: float = 2.0) -> QPixmap:
    return _render(_SVGS[name].replace("{c}", color), size, dpr)
