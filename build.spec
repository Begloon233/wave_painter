# -*- mode: python ; coding: utf-8 -*-
"""跨平台打包配置（Windows / macOS / Linux 共用这一份）

用法（在项目根目录执行）：
    python -m PyInstaller --noconfirm --clean build.spec

可选环境变量：

    WP_CONSOLE=1   生成“带控制台”的调试版，文件名后缀 _debug。
                   用户报“双击没反应 / 一启动就报错”时，让他们运行这个版本，
                   控制台里会打印完整 traceback，比 GUI 弹窗有用得多。
                     PowerShell:  $env:WP_CONSOLE = "1"; python -m PyInstaller --noconfirm --clean build.spec
    WP_DEBUG=1     打开 PyInstaller 自身的调试输出（bootloader 日志）
    WP_EXE_NAME    覆盖产物文件名（CI 里用 wave_painter，本地默认用中文名）

产物：

    Windows / Linux -> dist/音频黑线转换器(.exe)        （单文件，无控制台）
                       dist/音频黑线转换器_debug.exe    （仅 WP_CONSOLE=1 时生成）
    macOS           -> dist/音频黑线转换器.app          （app bundle，内部 onedir）

可选：把静态 ffmpeg / ffprobe 放到 tools/ffmpeg/<win|mac|linux>/ 下，
打包时会自动塞进程序（运行时位于 sys._MEIPASS），用户就不用自己装 ffmpeg 了。
"""

import os
import sys

APP_NAME = "音频黑线转换器"

try:                        # PyInstaller 执行 spec 时会注入 SPECPATH
    HERE = SPECPATH
except NameError:           # 直接 python build.spec 时没有 SPECPATH
    HERE = os.path.dirname(os.path.abspath(__file__))


def _env_flag(name):
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


CONSOLE = _env_flag("WP_CONSOLE")          # 调试版：带控制台
PYI_DEBUG = _env_flag("WP_DEBUG")          # PyInstaller 自身调试输出

PLAT = "win" if sys.platform == "win32" else ("mac" if sys.platform == "darwin" else "linux")
EXT = ".exe" if PLAT == "win" else ""
FF_DIR = os.path.join(HERE, "tools", "ffmpeg", PLAT)

binaries = []
for _name in ("ffmpeg", "ffprobe"):
    _p = os.path.join(FF_DIR, _name + EXT)
    if os.path.exists(_p):
        binaries.append((_p, "."))
    else:
        # 注意：这里必须只用 ASCII。GitHub Actions 的 Windows runner 控制台是 cp1252，
        # 打印中文会抛 UnicodeEncodeError，直接让打包步骤失败。
        print("[build.spec] ffmpeg not bundled (not found): %s" % _p)

ICON = os.path.join(HERE, "wavep.ico")
ICON = ICON if os.path.exists(ICON) else None
ICNS = os.path.join(HERE, "wavep.icns")
ICNS = ICNS if PLAT == "mac" and os.path.exists(ICNS) else None

datas = [(ICON, ".")] if ICON else []

# Windows 版本资源：让 exe 有 FileVersion / ProductName，少一点 SmartScreen / 杀软误报
VERSION_FILE = os.path.join(HERE, "version_info.txt")
VERSION = VERSION_FILE if PLAT == "win" and os.path.exists(VERSION_FILE) else None

# 产物名：本地保持中文名，CI 里用 WP_EXE_NAME 统一成 wave_painter
BASE_NAME = os.environ.get("WP_EXE_NAME", "").strip() or APP_NAME
APP_NAME_OUT = BASE_NAME + ("_debug" if CONSOLE else "")

a = Analysis(
    [os.path.join(HERE, "waveform_ui.py")],
    pathex=[HERE],
    binaries=binaries,
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

_common = dict(
    name=APP_NAME_OUT,
    debug=PYI_DEBUG,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                      # UPX 会让每次启动都自解压：启动变慢，且更容易被杀软误报
    console=CONSOLE,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=ICON if PLAT == "win" else None,
)
if VERSION:
    _common["version"] = VERSION

if PLAT == "mac":
    # macOS 用 onedir + .app：onefile 塞进 .app 会出各种奇怪问题
    exe = EXE(pyz, a.scripts, [], exclude_binaries=True, **_common)
    coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False,
                   upx_exclude=[], name=APP_NAME_OUT)
    app = BUNDLE(coll, name=APP_NAME_OUT + ".app", icon=ICNS,
                 bundle_identifier="com.wavepainter.converter")
else:
    # Windows / Linux 保持单文件，和现在发出去的形式一致
    exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], **_common)
