# -*- coding: utf-8 -*-
"波形查看器"

import os
import shutil
import subprocess
import sys
import logging
import glob
import re

if os.name == "nt":
    _OrigPopen = subprocess.Popen

    class _NoConsolePopen(_OrigPopen):
        def __init__(self, *args, **kwargs):
            if kwargs.get("creationflags") is None:
                kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
            super().__init__(*args, **kwargs)

    subprocess.Popen = _NoConsolePopen

import ctypes
ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Begloon.WavePainter.1")

from PyQt5 import QtGui
import numpy as np
from pydub import AudioSegment

from PyQt5.QtCore import QEasingCurve, QPointF, QPropertyAnimation, QRectF, QUrl, Qt
from PyQt5.QtGui import (
    QColor, QDesktopServices, QFont, QFontMetrics, QPalette, QPainter,
    QPainterPath, QPen, QIcon
)
from PyQt5.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog,
    QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QPushButton, QSizePolicy, QSpinBox, QStyleFactory, QToolButton, QVBoxLayout,
    QWidget
)

import core


def resource_path(name):
    bases = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        bases.append(meipass)
    bases.append(os.path.dirname(os.path.abspath(sys.argv[0] or "")))
    bases.append(os.path.dirname(os.path.abspath(__file__)))
    bases.append(os.getcwd())
    for base in bases:
        p = os.path.join(base, name)
        if os.path.exists(p):
            return p
    return os.path.join(meipass or os.getcwd(), name)

SIZE_MAX = 16777215
TEXT_MUTED = "#6b6b6b"

PRESETS = ["全波形", "全波形 0ms", "单侧波形", "单侧波形 0ms", "对称波形", "对称波形 0ms"]
OUTPUTS = []

HINT_TEMPLATE = "{mode} · {t0} → {t1} ms · 采样 {dt} ms · 共 {n} 点"

#  带动态提示文字的数字输入框
class HintSpinBox(QDoubleSpinBox):

    def __init__(self, hint="", parent=None):
        super().__init__(parent)
        self._hint = hint
        self._empty = True
        self.setDecimals(3)
        self.setRange(-1e6, 1e6)
        self.setFixedWidth(max(84, self.fontMetrics().horizontalAdvance("9.999") + 28))
        self.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.setKeyboardTracking(False)
        self.lineEdit().textEdited.connect(self._on_text_edited)

        self._label = QLabel(self)
        self._label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._label.setObjectName("hintLabel")
        self._label.setText(hint)
        self._label.setVisible(bool(hint))
        self._place()

    def setHint(self, text):
        self._hint = text
        self._label.setText(text)
        self._sync_hint()

    def hint(self):
        return self._hint

    def _sync_hint(self):
        self._place()
        show = bool(self._hint) and self._empty and not self.hasFocus()
        self._label.setVisible(show)
        if show:
            self._label.raise_()

    def _place(self):
        base = self.lineEdit().palette().color(QPalette.Base).name()
        self._label.setStyleSheet(
            "QLabel#hintLabel { color:#9a9a9a; background:%s; border:none;"
            " padding-left:3px; }" % base)
        self._label.setGeometry(self.lineEdit().geometry().adjusted(0, 1, 0, -1))
        self._label.raise_()

    def _on_text_edited(self, text):
        empty = not text.strip()
        if empty != self._empty:
            self._empty = empty
            self._sync_hint()

    def stepBy(self, steps):
        super().stepBy(steps)
        if self._empty:
            self._empty = False
            self._sync_hint()

    def focusInEvent(self, ev):
        super().focusInEvent(ev)
        if self._empty and self.lineEdit().text().strip():
            self.lineEdit().setText("")
        self._sync_hint()

    def focusOutEvent(self, ev):
        super().focusOutEvent(ev)
        if not self.lineEdit().text().strip():
            self._empty = True
        self._sync_hint()

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._place()



#  波形显示视窗
def _msc_position(msc, ms):
    try:
        return msc._parse_position(ms)
    except AttributeError:                     
        return int(ms * msc.frame_rate / 1000.0)


def _channel_zero(msc):

    raw = msc.raw_data
    width = int(msc.sample_width)
    if width == 2:
        samples = np.frombuffer(raw, dtype=np.int16)
    elif width == 3:
        b = np.frombuffer(raw, dtype=np.uint8)
        b = b[: (len(b) // 3) * 3].reshape(-1, 3)
        u = (b[:, 0].astype(np.uint32)
             | b[:, 1].astype(np.uint32) << 8
             | b[:, 2].astype(np.uint32) << 16)
        samples = np.where(u & 0x800000, u - 0x1000000, u).astype(np.int32)
    elif width == 1:
        samples = np.frombuffer(raw, dtype=np.uint8).astype(np.int16) - 128
    else:
        samples = np.frombuffer(raw, dtype=np.int32)

    ch = int(msc.channels or 1)
    if ch > 1:
        samples = samples[: (len(samples) // ch) * ch].reshape(-1, ch)[:, 0]
    return np.ascontiguousarray(samples)


class WaveformView(QWidget):
    """自绘波形视窗"""

    COLS, ROWS = 5, 4

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(108)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        self._t0, self._t1, self._dt = 0, 1000, 1     # 单位统一为毫秒
        self._y0, self._y1 = -1.0, 1.0
        self._mode = "XZ模式"
        self._black_line = True
        self._color = QColor("#1f6feb")
        self._data = []

        # 音频缓存内存切片
        self._audio_fn = None     
        self._audio_msc = None     
        self._audio_chan = None     
        self._raw_key = None        
        self._raw = None

    # 接口
    def set_points(self, pl):
        self._data = pl

    def set_time(self, t0_ms, t1_ms, dt_ms, fn, wape):
        self._t0, self._t1, self._dt = int(t0_ms), int(t1_ms), int(dt_ms)
        if not fn is None:
            self.set_wav(fn, wape)
        self.update()

    def set_wav(self, fn, wape):
        arr = self._raw_channel(fn, float(self._t0), float(self._t1), self._dt)
        n = len(arr)
        if n == 0:
            self.set_points([])
            return

        mx = int(np.max(np.abs(arr)))
        if mx == 0:
            self.set_points([0.0] * n)
            return

        raw = arr / mx                      
        pl = np.where(raw > 0,
                      1.0 - (1.0 - raw) ** wape,
                      -(1.0 - (1.0 + raw) ** wape))
        self.set_points(pl.tolist())

    # 解码
    def _ensure_audio(self, fn):
        key = os.path.abspath(fn)
        if key == self._audio_fn and self._audio_chan is not None:
            return
        msc = AudioSegment.from_file(fn)
        self._audio_fn = key
        self._audio_msc = msc
        self._audio_chan = _channel_zero(msc)
        self._raw_key = None
        self._raw = None

    def _raw_channel(self, fn, t0_ms, t1_ms, sr_time):
        """按 (文件, 起止时间, 采样时间) 缓存原始样本"""
        self._ensure_audio(fn)
        key = (self._audio_fn, t0_ms, t1_ms, sr_time)
        if key == self._raw_key:
            return self._raw

        msc = self._audio_msc
        total_ms = len(msc)
        start = _msc_position(msc, min(t0_ms, total_ms))
        end = _msc_position(msc, min(t1_ms, total_ms))
        clip = self._audio_chan[start:end] if end > start else self._audio_chan[:0]

        n = len(clip)
        step = int(sr_time) * int(msc.frame_rate)
        if n == 0 or step <= 0:
            arr = clip
        else:
            kmax = (n - 1) * 1000 // step + 1
            idx = (np.arange(kmax, dtype=np.int64) * step) // 1000
            arr = clip[idx]

        self._raw_key = key
        self._raw = arr
        return arr


    def set_mode(self, mode):
        self._mode = mode
        self.update()

    def point_count(self):
        return len(self._data)

    def set_black_line(self, flag):
        self._black_line = bool(flag)
        self.update()

    def set_trace_color(self, color):
        if isinstance(color, QColor) and color.isValid():
            self._color = QColor(color)
            self.update()

    # 绘制
    @staticmethod
    def _fmt(v):
        a = abs(v)
        if a >= 1e5 or (a and a < 1e-3):
            return "%.1e" % v
        return ("%.4f" % v).rstrip("0").rstrip(".") or "0"

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.fillRect(self.rect(), QColor("#ffffff"))
        p.setPen(QPen(QColor("#b4b4b4"), 1))
        p.drawRect(self.rect().adjusted(0, 0, -1, -1))

        f = QFont(self.font()); f.setPointSizeF(8.0)
        fm = QFontMetrics(f)
        L = fm.horizontalAdvance("-0.000") + 10
        R, T, B = 8, 6, fm.height() + 3
        plot = QRectF(self.rect()).adjusted(L, T, -R, -B)
        if plot.width() < 30 or plot.height() < 30:
            p.end()
            return

        cols, rows = self.COLS, self.ROWS
        # 网格
        p.setPen(QPen(QColor("#e6e6e6"), 1))
        for i in range(1, cols):
            x = plot.left() + plot.width() * i / cols
            p.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))
        for j in range(1, rows):
            y = plot.top() + plot.height() * j / rows
            p.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))

        # 坐标轴
        p.setPen(QPen(QColor("#9a9a9a"), 1))
        p.drawLine(plot.bottomLeft(), plot.bottomRight())
        p.drawLine(plot.bottomLeft(), plot.topLeft())

        # 刻度文字
        p.setFont(f)
        p.setPen(QColor("#3c3c3c"))
        xr, yr = self._t1 - self._t0, self._y1 - self._y0
        for i in range(cols + 1):
            x = plot.left() + plot.width() * i / cols
            if x <= 1e-10:
                val = 0
            else:
                val = self._t0 + xr * i / cols
            h = fm.height()
            if i == 0:
                rect, al = QRectF(x, plot.bottom() + 2, 48, h), Qt.AlignLeft | Qt.AlignTop
            elif i == cols:
                rect, al = QRectF(x - 48, plot.bottom() + 2, 48, h), Qt.AlignRight | Qt.AlignTop
            else:
                rect, al = QRectF(x - 32, plot.bottom() + 2, 64, h), Qt.AlignHCenter | Qt.AlignTop
            p.drawText(rect, al, self._fmt(val))
        for j in range(rows + 1):
            y = plot.top() + plot.height() * j / rows
            val = self._y1 - yr * j / rows
            yw = plot.left() - 6
            if j == 0:
                rect, al = QRectF(0, plot.top() - 1, yw, fm.height()), Qt.AlignRight | Qt.AlignTop
            elif j == rows:
                rect, al = QRectF(0, plot.bottom() - fm.height() + 1, yw, fm.height()), Qt.AlignRight | Qt.AlignBottom
            else:
                rect, al = QRectF(0, y - fm.height() / 2.0, yw, fm.height()), Qt.AlignRight | Qt.AlignVCenter
            p.drawText(rect, al, self._fmt(val))

        # 右上角状态
        p.setPen(QColor(TEXT_MUTED))
        p.drawText(QRectF(plot.right() - 210, plot.top() + 1, 210, fm.height()),
                   Qt.AlignRight | Qt.AlignTop,
                   "%s · %d 采样点" % (self._mode, len(self._data)))

        if len(self._data) < 2:
            p.setPen(QColor(TEXT_MUTED))
            p.drawText(plot, Qt.AlignCenter, "时间或采样参数无效")
            p.end()
            return

        elif len(self._data) > 10000:
            p.setPen(QColor(TEXT_MUTED))
            p.drawText(plot, Qt.AlignCenter, "采样点数量过多")
            p.end()
            return
        
        # 曲线
        gspan = yr if abs(yr) > 1e-12 else 1.0
        n = len(self._data)
        path = QPainterPath()
        for i, v in enumerate(self._data):
            x = plot.left() + plot.width() * i / (n - 1)
            y = plot.bottom() - (v - self._y0) / gspan * plot.height()
            if i == 0:
                path.moveTo(x, y)
            else:
                path.lineTo(x, y)

        color = QColor("#000000") if self._black_line else QColor(self._color)
        pen = QPen(color, 1.4)
        pen.setJoinStyle(Qt.RoundJoin)
        pen.setCapStyle(Qt.RoundCap)
        p.save()
        p.setClipRect(plot)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)
        p.restore()
        p.end()


#  下拉隐藏栏
class CollapsibleBox(QWidget):
    def __init__(self, title="详细设置", parent=None):
        super().__init__(parent)
        self.toggle = QToolButton(self)
        self.toggle.setText(title)
        self.toggle.setCheckable(False)      
        self.toggle.setArrowType(Qt.RightArrow)
        self.toggle.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.toggle.setAutoRaise(True)
        self.toggle.setCursor(Qt.PointingHandCursor)
        self.toggle.clicked.connect(self._on_clicked)

        self.content = QWidget(self)
        self.content.setMaximumHeight(0)
        self.content.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        lay = QHBoxLayout(self.content)
        lay.setContentsMargins(16, 0, 0, 1)
        lay.setSpacing(6)
        self.body = lay

        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(2)
        box.addWidget(self.toggle, 0, Qt.AlignLeft)
        box.addWidget(self.content)

        self._expanded = False
        self._anim = QPropertyAnimation(self.content, b"maximumHeight", self)
        self._anim.setDuration(150)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.finished.connect(self._on_anim_done)

    def _on_clicked(self, checked=False):
        self._expanded = not self._expanded
        expanded = self._expanded
        self.toggle.setArrowType(Qt.DownArrow if expanded else Qt.RightArrow)
        self._anim.stop()
        if expanded:
            self.content.setVisible(True)
            target = max(self.content.sizeHint().height(), 24)
            self._anim.setStartValue(self.content.maximumHeight())
            self._anim.setEndValue(target)
        else:
            self._anim.setStartValue(self.content.height())
            self._anim.setEndValue(0)
        self._anim.start()

    def _on_anim_done(self):
        if self._expanded:
            self.content.setMaximumHeight(SIZE_MAX)
        else:
            self.content.setVisible(False)


#  主窗口
class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("音频黑线转换器")
        self.resize(650, 455)
        self._folder = ""
        self._last_dir = ""
        self._guard = False
        self.fn = None
        self.chart = None

        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(5)

        self.setWindowIcon(QIcon(resource_path("wavep.ico")))

        self.folder_check = QCheckBox("选择谱面文件夹", self)
        self.path_label = QLabel("未选择文件夹", self)
        self.path_label.setStyleSheet("color:%s;" % TEXT_MUTED)
        self.path_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.folder_check.toggled.connect(self._on_folder_toggled)

        self.combo_output = QComboBox(self)
        self.combo_output.addItems(OUTPUTS)
        if not OUTPUTS:
            self.combo_output.addItem("（暂无输出）")
            self.combo_output.model().item(0).setEnabled(False)
        self.combo_output.setFixedWidth(140)
        self.combo_output.setToolTip("输出文件：内容在源码顶部的 OUTPUTS 里填写")

        row_folder = QHBoxLayout()
        row_folder.setSpacing(5)
        row_folder.addWidget(self.folder_check)
        row_folder.addWidget(self.path_label, 1)
        row_folder.addLayout(self._group("输出文件", self.combo_output))

        self.view = WaveformView(self)
        gb_plot = QGroupBox("波形显示", self)
        gb_plot_lay = QVBoxLayout(gb_plot)
        gb_plot_lay.setContentsMargins(6, 6, 6, 6)
        gb_plot_lay.addWidget(self.view)

        gb_param = QGroupBox("参数", self)
        pv = QVBoxLayout(gb_param)
        pv.setContentsMargins(6, 5, 6, 5)
        pv.setSpacing(4)

        self.combo_preset = QComboBox()
        self.combo_preset.addItems(PRESETS)
        if not PRESETS:

            self.combo_preset.addItem("（暂无预设）")
            self.combo_preset.model().item(0).setEnabled(False)
        self.combo_preset.setFixedWidth(150)
        grp_preset = self._group("预设", self.combo_preset)

        self.spin_t0 = self._int_num(0, 999999, 0, "")
        self.spin_t1 = self._int_num(0, 999999, 1000, "")
        self.spin_dt = self._int_num(1, 999999, 1, "")
        self.spin_t0.setToolTip("波形起始时间（毫秒）")
        self.spin_t1.setToolTip("波形结束时间（毫秒）")
        grp_time = self._group("起止时间 ms", self.spin_t0, self._dash("~"), self.spin_t1)
        grp_dt = self._group("采样时间 ms", self.spin_dt)
        self.spin_dt.setToolTip("该值越短，波形精度越大\n注意采样时间短于10ms易导致arc头尾错误粘合，建议在此时使用0ms预设")

        self.combo_mode = QComboBox()
        self.combo_mode.addItems(["XZ模式", "YZ模式"])
        self.combo_mode.setToolTip("XZ：水平平面\nYZ：竖直平面")
        self.combo_mode.setFixedWidth(
            max(84, self.combo_mode.fontMetrics().horizontalAdvance("XZ模式") + 30))

        self.spin_y0 = self._num(-1e6, 1e6, -1.0, 2, width=76)
        self.spin_y1 = self._num(-1e6, 1e6, 1.0, 2, width=76)
        self.spin_coord = HintSpinBox("Y坐标")          
        for sp in (self.spin_y0, self.spin_y1):
            sp.setToolTip("音频波形映射到谱面坐标的范围（平面）")

        self.spin_coord.setToolTip("波形平面的放置位置")
        grp_mode = self._group("模式", self.combo_mode)
        grp_range = self._group("坐标范围", self.spin_y0, self._dash("~"),
                                self.spin_y1, self.spin_coord)

        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(5)
        grid.addLayout(grp_preset, 0, 0, Qt.AlignLeft)
        grid.addLayout(grp_time, 1, 0)
        grid.addLayout(grp_dt, 1, 1, Qt.AlignLeft)

        grid.addLayout(grp_mode, 2, 0, Qt.AlignLeft)
        grid.addLayout(grp_range, 2, 1, Qt.AlignLeft)
        grid.setColumnStretch(1, 1)

        self.box = CollapsibleBox("其他", gb_param)
        self.check_black = QCheckBox("是否为黑线", self.box.content)
        self.check_black.setChecked(True)

        self.lab_color = QLabel("颜色", self.box.content)
        self.edit_color = QLineEdit(self.box.content)
        self.edit_color.setPlaceholderText("blue/red/green/white")
        self.edit_color.setFixedWidth(120)
        self.edit_color.setToolTip("输入颜色名, 勾选“是否为黑线”时曲线固定为黑色")

        self.check_mirror = QCheckBox("镜像", self.box.content)
        self.check_mirror.setChecked(False)
        self.check_center_end = QCheckBox("集中末坐标", self.box.content)
        self.check_center_end.setToolTip("将所有arc的结束坐标设为一致的")
        self.check_center_end.setChecked(False)
        self.lab_power = QLabel("波形增幅幂数", self.box.content)
        self.spin_power = self._num(0.1, 10.0, 1.0, 2, width=64)
        self.spin_power.setToolTip("该数字越大，波形的增幅越大，波形对微小波动越敏感")
        self.spin_power.setValue(1.0)

        row_a = QHBoxLayout()
        row_a.setSpacing(8)
        row_a.addWidget(self.check_black)
        row_a.addWidget(self.lab_color)
        row_a.addWidget(self.edit_color)
        row_a.addStretch(1)

        row_b = QHBoxLayout()
        row_b.setSpacing(8)
        row_b.addWidget(self.check_mirror)
        row_b.addWidget(self.check_center_end)
        row_b.addWidget(self.lab_power)
        row_b.addWidget(self.spin_power)
        row_b.addStretch(1)

        col = QVBoxLayout()
        col.setSpacing(2)
        col.addLayout(row_a)
        col.addLayout(row_b)
        self.box.body.addLayout(col)
        self.box.body.addStretch(1)

        pv.addLayout(grid)
        pv.addWidget(self.box)

        self.btn_generate = QPushButton("生成", self)
        self.btn_generate.setFixedWidth(96)
        self.btn_generate.setToolTip("按当前参数生成谱面")
        self.btn_generate.clicked.connect(self._on_generate)
        self.lab_hint = QLabel("按当前参数生成波形", self)
        self.lab_hint.setStyleSheet("color:%s;" % TEXT_MUTED)
        self.lab_hint.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        row_generate = QHBoxLayout()
        row_generate.setSpacing(6)
        row_generate.addWidget(self.lab_hint, 1)
        row_generate.addWidget(self.btn_generate)

        root.addLayout(row_folder)
        root.addWidget(gb_plot, 1)
        root.addWidget(gb_param)
        root.addLayout(row_generate)

        self._edge_inputs()
        self._connect()
        self._sync_view()
        self._fitted = False

    def _edge_inputs(self):
        self._input_style = QStyleFactory.create("Fusion")
        for w in (self.spin_t0, self.spin_t1, self.spin_dt,
                  self.spin_y0, self.spin_y1, self.spin_coord,
                  self.combo_mode, self.combo_preset, self.combo_output,
                  self.edit_color, self.spin_power):
            w.setStyle(self._input_style)

    #  小工具
    @staticmethod
    def _int_num(lo, hi, val, suffix=" ms", width=78):
        """整数毫秒输入框（起止时间 / 采样时间）。"""
        sp = QSpinBox()
        sp.setRange(int(lo), int(hi))
        sp.setValue(int(val))
        sp.setSingleStep(1)
        sp.setAccelerated(True)
        if suffix:
            sp.setSuffix(suffix)
        need = sp.fontMetrics().horizontalAdvance("999999" + suffix) + 22
        sp.setFixedWidth(max(width, need))
        sp.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        sp.setKeyboardTracking(False)
        return sp

    @staticmethod
    def _num(lo, hi, val, decimals, suffix="", width=78):
        sp = QDoubleSpinBox()
        sp.setRange(lo, hi)
        sp.setDecimals(decimals)
        sp.setValue(val)
        sp.setSingleStep(0.1 if hi - lo > 1 else 0.01)
        if suffix:
            sp.setSuffix(suffix)
        need = sp.fontMetrics().horizontalAdvance("9.999" + suffix) + 28
        sp.setFixedWidth(max(width, need))
        sp.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        sp.setKeyboardTracking(False)
        return sp

    @staticmethod
    def _dash(text):
        lab = QLabel(text)
        lab.setStyleSheet("color:%s;" % TEXT_MUTED)
        return lab

    @staticmethod
    def _group(label_text, *widgets):
        box = QHBoxLayout()
        box.setSpacing(6)
        box.addWidget(QLabel(label_text))
        for w in widgets:
            box.addWidget(w)
        return box

    # 信号
    def _connect(self):
        for sp in (self.spin_t0, self.spin_t1, self.spin_dt, self.spin_power):
            sp.valueChanged.connect(self._sync_view)
        self.combo_mode.currentIndexChanged.connect(self._on_mode_changed)
        self.check_black.toggled.connect(self._sync_view)
        self.folder_check.toggled.connect(self._sync_view)
        self.edit_color.textChanged.connect(self._sync_view)

    def _on_mode_changed(self):
        self.spin_coord.setHint("Y坐标" if self.combo_mode.currentText() == "XZ模式"
                                else "X坐标")
        self._sync_view()

    def _on_generate(self):
        self._sync_view()
        num = core.core(f"{self._folder}/{self.chart}", self.fn, (self.spin_t0.value(), self.spin_t1.value()),
                  self.spin_dt.value(), self.combo_preset.currentText(), self.check_mirror.isChecked(),
                  self.check_center_end.isChecked(), self.combo_mode.currentText() == "XZ模式",
                  (self.spin_y0.value(), self.spin_y1.value()), self.spin_coord.value(),
                  self.check_black.isChecked(), self.edit_color.text().strip(), 
                  self.spin_power.value())
        setErrorBox(f"已在新创建的时间组中放置了 {num} 条arc，\n旧谱面已备份至 {self._folder}/{self.chart}.bak",
                    setIcon=False, title="完成")
        

    def _sync_view(self):
        self.view.set_time(self.spin_t0.value(), self.spin_t1.value(),
                           self.spin_dt.value(), self.fn, self.spin_power.value())
        self.view.set_mode(self.combo_mode.currentText())
        self.view.set_black_line(self.check_black.isChecked())
        n = self.view.point_count()
        if n <= 0:
            self.lab_hint.setText("参数无效 · 请检查起止时间与采样时间")
        else:
            self.lab_hint.setText(HINT_TEMPLATE.format(
                mode=self.combo_mode.currentText(),
                t0=self.spin_t0.value(), t1=self.spin_t1.value(),
                dt=self.spin_dt.value(), n=n))
        txt = self.edit_color.text().strip()
        col = QColor()
        if txt == "blue": col = QColor("#1f6feb")
        elif txt == "red": col = QColor("#e32525")
        elif txt == "green": col = QColor("#2ab847")
        elif txt == "white": col = QColor("#676767")
        #col = QColor(txt) if txt else QColor()
        self.view.set_trace_color(col if col.isValid() else QColor("#1f6feb"))
        if not self.folder_check.isChecked():
            self.btn_generate.setDisabled(True)
        else:
            self.btn_generate.setDisabled(False)
        self.chart = self.combo_output.currentText()

    def showEvent(self, ev):
        super().showEvent(ev)
        if not self._fitted:
            self._fitted = True
            self._fit_widths()

    def _fit_widths(self):
        """按实际字体度量微调宽度"""
        specs = [(self.spin_y0, "-1.000"),
                 (self.spin_y1, "1.000"), (self.spin_coord, "0.000"),
                 (self.spin_power, "9.99")]
        for sp, sample in specs:
            chrome = sp.width() - sp.lineEdit().width()
            need = sp.fontMetrics().horizontalAdvance(sample) + chrome + 8
            if need > sp.width():
                sp.setFixedWidth(int(need))
        combo_need = (self.combo_mode.fontMetrics()
                      .horizontalAdvance(self.combo_mode.currentText()) + 34)
        if combo_need > self.combo_mode.width():
            self.combo_mode.setFixedWidth(int(combo_need))

    def _on_folder_toggled(self, checked):
        if self._guard:
            return
        if not checked:
            self._folder = ""
            self.path_label.setText("未选择文件夹")
            self.path_label.setToolTip("选择你的谱面文件夹")
            return
        path = QFileDialog.getExistingDirectory(self, "选择文件夹", self._last_dir)
        if not path:
            self._guard = True
            self.folder_check.setChecked(False)
            self._guard = False
            self._folder = ""
            self.path_label.setText("未选择文件夹")
            self.path_label.setToolTip("选择你的谱面文件夹")
            return
        self._folder = path
        self._last_dir = path
        self.path_label.setToolTip(path)
        self._update_path_text()
        self.check_chart_path()

    def _update_path_text(self):
        if not self._folder:
            self.path_label.setText("未选择文件夹")
            return
        fm = QFontMetrics(self.path_label.font())
        width = max(80, self.path_label.width() - 4)
        self.path_label.setText(fm.elidedText(self._folder, Qt.ElideMiddle, width))

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._update_path_text()

    def keyPressEvent(self, ev):
        if ev.key() == Qt.Key_Escape:
            self.close()
        super().keyPressEvent(ev)

    def check_chart_path(self):

        self._sync_view()

        self.fn = None
        for f in ("ogg", "mp3", "wav"):
            if not os.path.exists(f"{self._folder}/base.{f}"):
                self.fn = None
            else:
                self.fn = f"{self._folder}/base.{f}"
                break

        paths = glob.glob(f"{self._folder}/*.aff")
        chartlist = [re.split(r"/|\\", path)[-1] for path in paths]
        self.combo_output.clear()
        self.combo_output.addItems(chartlist)
        if "2.aff" in chartlist:
            self.combo_output.setCurrentText("2.aff")
        else:
            self.combo_output.setCurrentIndex(0)

        if self.fn is None:
            setErrorBox("该路径下没有谱面音频文件")
            self._guard = True
            self.folder_check.setChecked(False)
            self._guard = False
            self._folder = ""
            self.path_label.setText("未选择文件夹")
            self.path_label.setToolTip("选择你的谱面文件夹")
            self.combo_output.clear()
            return

        else:
            self.view.set_wav(self.fn, self.spin_power.value())


def setErrorBox(text, setIcon=True, title="错误"):
    box = QMessageBox()
    if setIcon:
        box.setIcon(QMessageBox.Warning)
    box.setWindowTitle(title)
    box.setText(text)
    box.exec_()

def find_ffmpeg():
    path = shutil.which("ffmpeg")
    if path:
        return path
    candidates = [
        r"C:\ffmpeg\bin\ffmpeg.exe",
        r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "WinGet",
                     "Links", "ffmpeg.exe"),
    ]
    for cand in candidates:
        if cand and os.path.isfile(cand):
            return cand
    return None


def check_ffmpeg(parent=None):
    """启动时检查 ffmpeg"""
    if find_ffmpeg():
        return True
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Warning)
    box.setWindowTitle("未检测到 ffmpeg")
    box.setText("没有找到 ffmpeg。")
    box.setInformativeText(
        "ffmpeg 用于读取与处理音频文件，缺少它部分功能将无法使用。\n\n"
        "安装方法：\n"
        "1. 到 ffmpeg.org 下载 Windows 版压缩包，解压到任意目录；\n"
        "2. 把其中的 bin 目录下的三个 .exe 文件放入 C:\\Windows\\System32 目录下；\n"
        "3. 重新打开本程序。\n"
        "\n 如果你是其他系统的用户，请另寻他法。")
    btn_download = box.addButton("打开下载页", QMessageBox.ActionRole)
    box.addButton("知道了", QMessageBox.AcceptRole)
    box.exec_()
    if box.clickedButton() is btn_download:
        QDesktopServices.openUrl(QUrl("https://ffmpeg.org/download.html"))
    return False


def apply_style(app):
    font = QFont(app.font())
    font.setPointSizeF(8.0)
    app.setFont(font)
    for name in ("windowsvista", "windows"):
        if app.setStyle(name):
            break
    return app


logger = logging.getLogger(__name__)
handler = logging.StreamHandler(stream=sys.stdout)
logger.addHandler(handler)

def handle_exception(exc_type, exc_value, exc_traceback):
    
    setErrorBox(f"{exc_type}:\n{exc_value}")
    logger.error("Uncaught exception", exc_info=(exc_type, exc_value, exc_traceback)) 

sys.excepthook = handle_exception 
runCode = None


def main():
    if hasattr(Qt, "AA_EnableHighDpiScaling"):
        QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
        QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    app = apply_style(QApplication(sys.argv))
    # 应用级图标：任务栏、消息框、所有子窗口都会继承
    app.setWindowIcon(QIcon(resource_path("wavep.ico")))
    win = MainWindow()
    win.show()
    check_ffmpeg(win)
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
