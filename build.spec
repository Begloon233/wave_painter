# -*- mode: python ; coding: utf-8 -*-
"""跨平台打包配置（Windows / macOS / Linux 共用一份）

用法（在项目根目录）：
    pyinstaller build.spec
产物：
    Windows / Linux -> dist/音频黑线转换器          （单文件）
    macOS           -> dist/音频黑线转换器.app      （app bundle，内部 onedir）

可选：把静态 ffmpeg / ffprobe 放到 tools/ffmpeg/<win|mac|linux>/ 下，
打包时会自动塞进程序（运行时位于 sys._MEIPASS），用户就不用自己装 ffmpeg 了。
"""

import os
import sys

APP_NAME = "音频黑线转换器"

PLAT = "win" if sys.platform == "win32" else ("mac" if sys.platform == "darwin" else "linux")
EXT = ".exe" if PLAT == "win" else ""
FF_DIR = os.path.join("tools", "ffmpeg", PLAT)

binaries = []
for _name in ("ffmpeg", "ffprobe"):
    _p = os.path.join(FF_DIR, _name + EXT)
    if os.path.exists(_p):
        binaries.append((_p, "."))
    else:
        print("[build.spec] 未找到 %s，本次不打包 ffmpeg" % _p)

ICON = "wavep.ico" if os.path.exists("wavep.ico") else None
ICNS = "wavep.icns" if os.path.exists("wavep.icns") else None
datas = [(ICON, ".")] if ICON else []

a = Analysis(
    ["waveform_ui.py"],
    pathex=[],
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
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                      # UPX 会让每次启动都自解压，启动明显变慢
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=ICON if PLAT == "win" else None,
)

if PLAT == "mac":
    # macOS 用 onedir + .app，onefile 塞进 .app 会出各种奇怪问题
    exe = EXE(pyz, a.scripts, [], exclude_binaries=True, **_common)
    coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False,
                   upx_exclude=[], name=APP_NAME)
    app = BUNDLE(coll, name=APP_NAME + ".app", icon=ICNS,
                 bundle_identifier="com.wavepainter.converter")
else:
    # Windows / Linux 保持单文件，和你现在发出去的形式一致
    exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], **_common)
