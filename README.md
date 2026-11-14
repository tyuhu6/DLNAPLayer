# DLNA 投屏播放器

把 Windows 电脑变成一台"电视"：手机视频 App 里的「投屏 / TV」按钮能直接搜到这台电脑，点一下就把片子推过来播放。

![](https://img.shields.io/badge/platform-Windows%2010%2F11-0078D4) ![](https://img.shields.io/badge/python-3.12-3776AB) ![](https://img.shields.io/badge/PySide6-6.x-41CD52)

## 功能

| 模式 | 说明 |
|---|---|
| 投屏播放 | 标准 DLNA/UPnP 接收端（DMR），爱奇艺、腾讯视频、优酷、B 站、芒果、抖音等 App 可直接投屏 |
| 屏幕镜像 | 内置 UxPlay，接收 iPhone / iPad 的 AirPlay 屏幕镜像，画面内嵌到镜像窗口 |
| 本地播放 | 直接播放电脑上的视频文件，与投屏共用同一套播放内核 |

播放器本身支持画面比例切换（含智能去黑边）、倍速、跳过片头、音量、键盘快捷键，顶栏显示分辨率与帧率。

界面为无边框沉浸式暗色风格：窗口任意位置拖动、8 向边缘拉伸、控制栏 3 秒无操作自动隐藏、极简加载动效。

## 系统要求

- Windows 10 / 11 64 位
- 手机与电脑连接**同一个 Wi-Fi**
- 首次运行需在防火墙放行 UDP 1900 与 TCP 9090（或允许程序通过防火墙）

## 安装 / 运行

### 方式一：安装程序（推荐）

下载 `DLNAPLayer-Setup.exe`，双击后自选安装目录 →「开始安装」。安装完会创建桌面与开始菜单快捷方式，并注册卸载项，可在「应用和功能」里正常卸载。

### 方式二：免安装

下载 `DLNAPLayer-1.0.0-portable.zip`，解压后直接运行 `DLNAPLayer.exe`（整个目录需要保留在一起）。

## 从源码运行

```powershell
# 1. 创建虚拟环境并安装依赖
uv venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

# 2. 准备播放内核（下载 libmpv-2.dll 到 player\ 目录）
.\.venv\Scripts\python.exe tools\fetch_libmpv.py

# 3. 启动
.\.venv\Scripts\python.exe main.py
```

屏幕镜像需要 `vendor\uxplay\` 下的 UxPlay 运行库（仓库已包含）。首次启动接收端时若提示安装 Bonjour，按提示装一次即可被苹果设备发现。

## 打包

```powershell
# 生成 dist\DLNAPLayer\（免安装目录版，含 UxPlay 运行库）
.\.venv\Scripts\python.exe tools\build_exe.py

# 生成 dist\DLNAPLayer-Setup.exe（单文件安装程序）
.\.venv\Scripts\python.exe tools\make_installer.py
```

## 支持哪些投屏协议

| 手机端入口 | 协议 | 支持情况 |
|---|---|---|
| 视频 App 里的「投屏 / TV」 | DLNA / UPnP | ✅ 已支持 |
| iPhone / iPad 控制中心「屏幕镜像」 | AirPlay | ✅ 已支持 |
| vivo / 小米等设置里的「投屏」（手机屏幕实时上屏） | Miracast / Wi-Fi Display | ❌ 不支持 |
| vivo 投屏到电脑（扫码） | vivo 互传私有协议 | ❌ 不支持 |

Miracast 需要 Wi-Fi Direct 组网 + WFD RTSP 协商 + RTP 解码，无法在 Python 层实现。安卓手机镜像可改用系统自带的「无线显示」接收端（Microsoft Store 可装，Win11 22H2+ 可用）。

## 常见问题

**手机上搜不到设备**
检查是否同一 Wi-Fi、防火墙是否放行、路由器是否开启了 AP 隔离；本机有多套投屏软件时可能同名冲突，可在首页点击设备名改名。

**播放中断开**
部分 App 在切后台时会主动断开；重新投一次即可。

**镜像窗口里没有画面**
UxPlay 首次运行需要 Bonjour 服务，按弹窗提示安装一次。

## 目录结构

```
main.py              应用入口与全局控制器
config.py            settings.json 配置读写、局域网 IP 探测
bridge.py            跨线程 Qt 信号中枢
upnp/                SSDP 发现、HTTP 服务、SOAP action、GENA 事件
player/              mpv 封装与 OpenGL 渲染控件
ui/                  无边框首页、播放窗口、镜像窗口、控件与动效
tools/               图标生成、libmpv 下载、打包与安装程序脚本
vendor/uxplay/       AirPlay 接收端（第三方）
tests/               协议层离线冒烟与模拟投屏脚本
```

## 第三方组件

- [mpv / libmpv](https://github.com/mpv-player/mpv) — GPL/LGPL，播放内核
- [python-mpv](https://github.com/jaseg/python-mpv) — GPL，mpv 的 Python 绑定
- [UxPlay](https://github.com/FDH2/UxPlay) — GPLv3，AirPlay 接收端
- [PySide6](https://www.qt.io/qt-for-python) — LGPL，Qt 界面绑定

第三方二进制位于 `vendor/` 与 `player/`，版权归各自作者所有。
