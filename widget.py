# -*- coding: utf-8 -*-
"""
DeskSwitch 桌面挂件

半透明置顶卡片，常驻屏幕一角：
    - 音频输出 / 输入设备点选切换
    - 键盘板载配置点选切换（一条 HID 命令，1 秒完成）
"""
import json
import os
import sys

from PyQt5.QtCore import Qt, QPoint, QTimer, QThread, pyqtSignal, QSize
from PyQt5.QtWidgets import QSizePolicy
from PyQt5.QtGui import QFont, QColor, QIcon, QPainter, QPixmap, QPen
from PyQt5.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout,
                             QLabel, QPushButton, QFrame, QMenu, QSystemTrayIcon,
                             QScrollArea)

import audio_core
import kb_onboard
import shortcut

APP_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(APP_DIR, "settings.json")
STARTUP_NAME = "DeskSwitch.lnk"

MIN_WIDTH = 330        # 三列时每列至少约 100
DEFAULT_WIDTH = 672    # 三列默认每列约 216
DEFAULT_HEIGHT = 300

# 配色（深色玻璃质感）
BG = "rgba(24, 26, 32, 205)"
BG_HOVER = "rgba(48, 52, 64, 230)"
ACCENT = "#4C9AFF"
TEXT = "#E6E8EE"
TEXT_DIM = "#98A0B3"
BORDER = "rgba(255, 255, 255, 26)"

MENU_STYLE = """
    QMenu { background: #1A1C22; color: %s; border: 1px solid #333; }
    QMenu::item { padding: 6px 24px; }
    QMenu::item:selected { background: #2C303C; }
""" % TEXT


def load_settings():
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_settings(data):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
    except Exception:
        pass


class SwitchWorker(QThread):
    """后台执行切换任务"""
    done = pyqtSignal(bool, str)

    def __init__(self, func, desc, parent=None):
        super().__init__(parent)
        self.func = func
        self.desc = desc

    def run(self):
        try:
            msg = self.func()
            self.done.emit(True, msg or self.desc + " 完成")
        except Exception as e:
            self.done.emit(False, "%s 失败：%s" % (self.desc, e))


class ResultWorker(QThread):
    """后台执行任务并回传任意结果对象"""
    done = pyqtSignal(object)

    def __init__(self, func, parent=None):
        super().__init__(parent)
        self.func = func

    def run(self):
        try:
            self.done.emit(("ok", self.func()))
        except Exception as e:
            self.done.emit(("err", str(e)))


class DeviceRow(QPushButton):
    """一个可点选的设备/配置行"""

    def __init__(self, text, active=False, on_click=None, max_width=236):
        super().__init__()
        self.on_click = on_click
        self.active = active
        self._base = text
        self._max_width = max_width
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(text)
        self.clicked.connect(self._clicked)
        self.refresh()

    def set_active(self, active):
        self.active = active
        self.refresh()

    def set_max_width(self, w):
        """窗口变宽/变窄时重新计算文字省略"""
        w = max(60, int(w))
        if w != self._max_width:
            self._max_width = w
            self.refresh()

    def sizeHint(self):
        # 不按全名算宽度，否则长设备名会把窗口撑开
        return QSize(self._max_width, 26)

    def minimumSizeHint(self):
        return QSize(60, 26)

    def refresh(self):
        mark = "● " if self.active else "○ "
        fm = self.fontMetrics()
        avail = self._max_width - 22
        body = self._base
        if fm.width(mark + body) > avail:
            body = fm.elidedText(body, Qt.ElideRight, avail - fm.width(mark))
        QPushButton.setText(self, mark + body)
        color = ACCENT if self.active else TEXT
        self.setStyleSheet("""
            QPushButton {
                text-align: left; padding: 6px 10px; border: none;
                border-radius: 6px; color: %s; background: %s;
                font-size: 12px; font-weight: %s;
            }
            QPushButton:hover { background: %s; }
        """ % (color, "rgba(76, 154, 255, 30)" if self.active else "transparent",
               "600" if self.active else "400", BG_HOVER))

    def _clicked(self):
        if self.on_click:
            self.on_click()


class Section(QFrame):
    """带标题的分组"""

    def __init__(self, title):
        super().__init__()
        self.setStyleSheet("background: transparent;")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        lab = QLabel(title)
        lab.setStyleSheet("color: %s; font-size: 11px; padding: 2px 6px;"
                          "background: transparent;" % TEXT_DIM)
        lay.addWidget(lab)
        self.body = QVBoxLayout()
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(1)
        lay.addLayout(self.body)

    def clear(self):
        while self.body.count():
            item = self.body.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

    def add_row(self, row):
        self.body.addWidget(row)

    def rows(self):
        """返回本组所有 DeviceRow（不含提示用的 QLabel）"""
        out = []
        for i in range(self.body.count()):
            w = self.body.itemAt(i).widget()
            if isinstance(w, DeviceRow):
                out.append(w)
        return out


class Grip(QWidget):
    """右下角拉伸把手（自绘 + 自己处理拖拽，不依赖 QSizeGrip）"""

    def __init__(self, parent):
        super().__init__(parent)
        self.setFixedSize(18, 18)
        self.setCursor(Qt.SizeFDiagCursor)
        self.setStyleSheet("background: transparent;")
        self._origin = None

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        pen = QPen(QColor(152, 160, 179, 170), 1.3)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        for i in range(3):
            x = 5 + i * 4
            p.drawLine(x, 15, 15, x)
        p.end()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            win = self.window()
            self._origin = (e.globalPos(), win.width(), win.height())
            e.accept()

    def mouseMoveEvent(self, e):
        if not self._origin:
            return
        gp, w0, h0 = self._origin
        d = e.globalPos() - gp
        win = self.window()
        win.resize(max(win.minimumWidth(), w0 + d.x()),
                   max(win.minimumHeight(), h0 + d.y()))
        e.accept()

    def mouseReleaseEvent(self, e):
        self._origin = None
        e.accept()


class DeskWidget(QWidget):
    def __init__(self):
        super().__init__()
        self.settings = load_settings()
        self.expanded = True
        self.drag_pos = None
        self.busy = False
        self.kb_busy = False
        self.kb_connected = False
        self.kb_cur = None
        self.worker = None
        self.lock_size = bool(self.settings.get("lock_size"))
        self.lock_pos = bool(self.settings.get("lock_pos"))
        self.always_on_top = bool(self.settings.get("always_on_top", False))
        self._expanded_h = None

        self.setWindowTitle("DeskSwitch")
        self._apply_window_flags()
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setMinimumWidth(MIN_WIDTH)
        self.setMinimumHeight(96)
        self.resize(DEFAULT_WIDTH, 300)

        self._build_ui()
        self._build_tray()
        self._restore_pos()
        self._apply_locks()
        self.refresh_all()

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.timeout.connect(self._save_geometry)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh_audio)
        self.timer.start(4000)

    # ---------- 界面 ----------
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 8, 10, 10)
        root.setSpacing(6)

        # 标题栏（可拖动）
        self.header = QWidget()
        self.header.setCursor(Qt.OpenHandCursor)
        h = QHBoxLayout(self.header)
        h.setContentsMargins(4, 2, 4, 2)
        self.title = QLabel("⇄  DeskSwitch")
        self.title.setStyleSheet("color: %s; font-size: 12px; font-weight: 600;"
                                 "background: transparent;" % TEXT)
        h.addWidget(self.title)
        h.addStretch()
        self.btn_toggle = QPushButton("▾")
        self.btn_toggle.setFixedSize(22, 20)
        self.btn_toggle.setCursor(Qt.PointingHandCursor)
        self.btn_toggle.setStyleSheet(
            "QPushButton { border: none; color: %s; background: transparent; }"
            "QPushButton:hover { color: %s; }" % (TEXT_DIM, ACCENT))
        self.btn_toggle.clicked.connect(self.toggle_fold)
        h.addWidget(self.btn_toggle)

        self.btn_menu = QPushButton("☰")
        self.btn_menu.setFixedSize(22, 20)
        self.btn_menu.setCursor(Qt.PointingHandCursor)
        self.btn_menu.setStyleSheet(
            "QPushButton { border: none; color: %s; background: transparent; }"
            "QPushButton:hover { color: %s; }" % (TEXT_DIM, ACCENT))
        self.btn_menu.clicked.connect(self.show_menu)
        h.addWidget(self.btn_menu)
        root.addWidget(self.header)

        # 内容区（可滚动，宽度自适应窗口）
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setStyleSheet("""
            QScrollArea { background: transparent; border: none; }
            QScrollArea > QWidget > QWidget { background: transparent; }
            QScrollBar:vertical {
                background: transparent; width: 6px; margin: 0px;
            }
            QScrollBar::handle:vertical {
                background: rgba(152, 160, 179, 90); border-radius: 3px; min-height: 24px;
            }
            QScrollBar::handle:vertical:hover { background: rgba(152, 160, 179, 150); }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
                background: transparent;
            }
        """)
        self.content = QWidget()
        cl = QHBoxLayout(self.content)
        cl.setContentsMargins(0, 2, 0, 0)
        cl.setSpacing(8)
        self.sec_out = Section("输出设备")
        self.sec_in = Section("输入设备")
        self.sec_kb = Section("键盘板载配置")
        for s in (self.sec_out, self.sec_in, self.sec_kb):
            cl.addWidget(s, 1)
        self.scroll.setWidget(self.content)
        root.addWidget(self.scroll, 1)

        # 状态行（不随列表滚动）
        self.status = QLabel("")
        self.status.setStyleSheet("color: %s; font-size: 11px;"
                                  "background: transparent;" % TEXT_DIM)
        self.status.setWordWrap(True)
        self.status.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        root.addWidget(self.status)

        # 右下角拉伸把手
        self.bottom = QWidget()
        self.bottom.setFixedHeight(16)
        bl = QHBoxLayout(self.bottom)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(0)
        bl.addStretch()
        self.grip = Grip(self)
        bl.addWidget(self.grip)
        root.addWidget(self.bottom)

        self.setStyleSheet("background: transparent;")
        self.header.mousePressEvent = self._drag_start
        self.header.mouseMoveEvent = self._drag_move
        self.header.mouseReleaseEvent = self._drag_end
        self._build_context_menu()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setBrush(QColor(24, 26, 32, 205))
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(self.rect(), 12, 12)

    def _build_tray(self):
        icon = self._make_icon()
        self.tray = QSystemTrayIcon(icon, self)
        self.tray.setToolTip("DeskSwitch")
        self.menu = QMenu()
        self.menu.setStyleSheet(MENU_STYLE)
        self.act_show = self.menu.addAction("显示 / 隐藏")
        self.act_refresh = self.menu.addAction("立即刷新")
        self.act_startup = self.menu.addAction("开机自启")
        self.act_startup.setCheckable(True)
        self.act_startup.setChecked(self._is_startup())
        self.menu.addSeparator()
        self.act_quit = self.menu.addAction("退出")
        self.act_show.triggered.connect(self.toggle_show)
        self.act_refresh.triggered.connect(self.refresh_all)
        self.act_startup.triggered.connect(self.toggle_startup)
        self.act_quit.triggered.connect(QApplication.quit)
        self.tray.setContextMenu(self.menu)
        self.tray.activated.connect(self._tray_activated)
        self.tray.show()

    def _build_context_menu(self):
        """窗口右键菜单：锁定格子 / 锁定位置"""
        self.ctx = QMenu()
        self.ctx.setStyleSheet(MENU_STYLE)
        self.act_lock_size = self.ctx.addAction("锁定格子（尺寸）")
        self.act_lock_size.setCheckable(True)
        self.act_lock_size.setChecked(self.lock_size)
        self.act_lock_pos = self.ctx.addAction("锁定位置")
        self.act_lock_pos.setCheckable(True)
        self.act_lock_pos.setChecked(self.lock_pos)
        self.act_lock_size.triggered.connect(self.toggle_lock_size)
        self.act_lock_pos.triggered.connect(self.toggle_lock_pos)
        self.ctx.addSeparator()
        self.act_on_top = self.ctx.addAction("置顶显示")
        self.act_on_top.setCheckable(True)
        self.act_on_top.setChecked(self.always_on_top)
        self.act_on_top.triggered.connect(self.toggle_always_on_top)
        self.ctx.addSeparator()
        act = self.ctx.addAction("立即刷新")
        act.triggered.connect(self.refresh_all)

        for wgt in (self, self.header, self.scroll, self.content):
            wgt.setContextMenuPolicy(Qt.CustomContextMenu)
            wgt.customContextMenuRequested.connect(self._show_ctx)

    def _show_ctx(self, pos):
        src = self.sender()
        gp = src.mapToGlobal(pos) if isinstance(src, QWidget) else self.mapToGlobal(pos)
        self.ctx.exec_(gp)

    def _make_icon(self):
        pm = QPixmap(64, 64)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        p.setBrush(QColor(76, 154, 255))
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(6, 24, 52, 12, 6, 6)
        p.setBrush(QColor(230, 232, 238))
        p.drawRoundedRect(6, 32, 52, 12, 6, 6)
        p.end()
        return QIcon(pm)

    def _apply_window_flags(self):
        """普通模式（不置顶，游戏/全屏窗口会盖住它）；需要时可勾「置顶显示」"""
        flags = Qt.FramelessWindowHint | Qt.Tool
        if self.always_on_top:
            flags |= Qt.WindowStaysOnTopHint
        was_visible = self.isVisible()
        pos = self.pos()
        self.setWindowFlags(flags)
        # 改 flags 会重建窗口句柄，半透明与无边框样式要补回来
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        if was_visible:
            self.move(pos)
            self.show()

    def toggle_always_on_top(self):
        self.always_on_top = self.act_on_top.isChecked()
        self.settings["always_on_top"] = self.always_on_top
        save_settings(self.settings)
        self._apply_window_flags()
        self.status.setText("已置顶显示" if self.always_on_top
                            else "已取消置顶（全屏应用会盖住它）")

    # ---------- 锁定 ----------
    def toggle_lock_size(self):
        self.lock_size = self.act_lock_size.isChecked()
        self.settings["lock_size"] = self.lock_size
        save_settings(self.settings)
        self._apply_locks()

    def toggle_lock_pos(self):
        self.lock_pos = self.act_lock_pos.isChecked()
        self.settings["lock_pos"] = self.lock_pos
        save_settings(self.settings)
        self._apply_locks()

    def _apply_locks(self):
        """锁定格子：固定当前尺寸、藏起拉伸把手；锁定位置：标题栏拖不动"""
        self.grip.setVisible(not self.lock_size)
        self.bottom.setVisible(not self.lock_size and self.expanded)
        self.header.setCursor(Qt.ArrowCursor if self.lock_pos else Qt.OpenHandCursor)
        if self.lock_size:
            self.setFixedSize(self.width(), self.height())
        else:
            self.setMaximumSize(16777215, 16777215)
            self.setMinimumSize(MIN_WIDTH, 96)
            if not self.expanded:
                self.setMinimumHeight(0)

    # ---------- 拖动 ----------
    def _drag_start(self, e):
        if self.lock_pos:
            return
        if e.button() == Qt.LeftButton:
            self.drag_pos = e.globalPos() - self.frameGeometry().topLeft()
            e.accept()

    def _drag_move(self, e):
        if self.drag_pos is not None and e.buttons() & Qt.LeftButton:
            self.move(e.globalPos() - self.drag_pos)
            e.accept()

    def _drag_end(self, e):
        self.drag_pos = None
        self.settings["pos"] = [self.x(), self.y()]
        save_settings(self.settings)

    def _restore_pos(self):
        size = self.settings.get("size")
        w, h = DEFAULT_WIDTH, DEFAULT_HEIGHT
        if size and len(size) == 2:
            w, h = size[0], size[1]
            if w < MIN_WIDTH:          # 旧版单列宽度，迁移到三列
                w, h = DEFAULT_WIDTH, DEFAULT_HEIGHT
        self.resize(max(self.minimumWidth(), w), max(self.minimumHeight(), h))
        scr = QApplication.primaryScreen().availableGeometry()
        pos = self.settings.get("pos")
        ok = False
        if pos and len(pos) == 2:
            # 位置必须还在屏幕内（换显示器 / 改分辨率时会自动拉回右上角）
            if (scr.x() <= pos[0] <= scr.right() - 80
                    and scr.y() <= pos[1] <= scr.bottom() - 60):
                self.move(pos[0], pos[1])
                ok = True
        if not ok:
            self.move(scr.right() - self.width() - 24, scr.y() + 24)
        self._expanded_h = self.height()

    def _save_geometry(self):
        self.settings["pos"] = [self.x(), self.y()]
        if self.expanded:
            self.settings["size"] = [self.width(), self.height()]
        save_settings(self.settings)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._relayout()
        if self.expanded:
            self._save_timer.start(400)   # 拖动结束后再写盘

    def _col_width(self):
        """每列宽度：按滚动区可视宽度三等分"""
        avail = self.scroll.viewport().width() - 4
        return max(96, int((avail - 16) // 3))

    def _row_width(self):
        return self._col_width() - 6

    def _relayout(self):
        """窗口变宽/变窄时，让每一行的文字省略跟着变"""
        avail = self._row_width()
        for sec in (self.sec_out, self.sec_in, self.sec_kb):
            for row in sec.rows():
                row.set_max_width(avail)

    # ---------- 交互 ----------
    def toggle_fold(self):
        self.expanded = not self.expanded
        self.scroll.setVisible(self.expanded)
        self.status.setVisible(self.expanded)
        self.bottom.setVisible(self.expanded and not self.lock_size)
        self.btn_toggle.setText("▾" if self.expanded else "▸")
        if self.expanded:
            h = self._expanded_h or DEFAULT_HEIGHT
            if self.lock_size:
                self.setFixedSize(self.width(), h)
            else:
                self.setMinimumSize(MIN_WIDTH, 96)
                self.setMaximumSize(16777215, 16777215)
                self.resize(self.width(), h)
        else:
            self._expanded_h = self.height()
            h = self.header.sizeHint().height() + 18
            if self.lock_size:
                self.setFixedSize(self.width(), h)
            else:
                self.setMinimumHeight(0)
                self.setFixedHeight(h)

    def toggle_show(self):
        if self.isVisible():
            self.hide()
        else:
            self.show()
            self.raise_()          # 不置顶时，从托盘调出也要能立刻看到
            self.activateWindow()

    def _tray_activated(self, reason):
        if reason == QSystemTrayIcon.Trigger:
            self.toggle_show()

    def show_menu(self):
        self.menu.exec_(self.btn_menu.mapToGlobal(QPoint(0, self.btn_menu.height())))

    def _is_startup(self):
        return os.path.exists(os.path.join(shortcut.startup_dir(), STARTUP_NAME))

    def toggle_startup(self):
        if self._is_startup():
            self._set_startup(False)
        else:
            self._set_startup(True)
        self.act_startup.setChecked(self._is_startup())

    def _set_startup(self, enable):
        lnk = os.path.join(shortcut.startup_dir(), STARTUP_NAME)
        if enable:
            try:
                shortcut.create_shortcut(
                    lnk, os.path.join(APP_DIR, "启动 DeskSwitch.vbs"),
                    APP_DIR, "DeskSwitch 桌面切换挂件")
            except Exception as e:
                self.status.setText("开机自启设置失败：%s" % e)
        else:
            try:
                if os.path.exists(lnk):
                    os.remove(lnk)
            except Exception as e:
                self.status.setText("取消开机自启失败：%s" % e)

    # ---------- 数据刷新 ----------
    def refresh_all(self):
        self.refresh_audio()
        self.refresh_keyboard()

    def refresh_audio(self):
        try:
            sess = audio_core.AudioSession()
            try:
                outs = sess.list_devices(audio_core.RENDER)
                ins = sess.list_devices(audio_core.CAPTURE)
                d_out = sess.get_default(audio_core.RENDER)
                d_in = sess.get_default(audio_core.CAPTURE)
            finally:
                sess.enum.release()
        except Exception as e:
            self.status.setText("音频设备读取失败：%s" % e)
            return

        def fill(section, devices, default_id, flow):
            section.clear()
            if not devices:
                lab = QLabel("  无可用设备")
                lab.setStyleSheet("color: %s; font-size: 11px;" % TEXT_DIM)
                section.body.addWidget(lab)
                return
            for d in devices:
                row = DeviceRow(d["name"], active=(d["id"] == default_id),
                                max_width=self._row_width(),
                                on_click=lambda dev=d, f=flow: self.switch_audio(dev, f))
                section.add_row(row)
            section.body.addStretch()

        fill(self.sec_out, outs, d_out, audio_core.RENDER)
        fill(self.sec_in, ins, d_in, audio_core.CAPTURE)
        self._relayout()

    # ---------- 键盘板载配置 ----------
    def refresh_keyboard(self):
        """后台读取当前板载配置索引"""
        if self.kb_busy:
            return
        self.kb_busy = True

        def done(res):
            self.kb_busy = False
            tag, val = res
            if tag == "ok":
                self.kb_connected = True
                self._render_keyboard(val)
            else:
                self.kb_connected = False
                self._render_keyboard(None)

        w = ResultWorker(kb_onboard.get_profile)
        w.done.connect(done)
        w.start()
        self.kb_worker = w

    def _render_keyboard(self, cur):
        self.kb_cur = cur
        self.sec_kb.clear()
        if not self.kb_connected or cur is None:
            lab = QLabel("  键盘未连接")
            lab.setStyleSheet("color: %s; font-size: 11px;" % TEXT_DIM)
            self.sec_kb.body.addWidget(lab)
            self._relayout()
            return
        count = int(self.settings.get("onboard_count", 4))
        names = self.settings.get("onboard_names", {})
        for i in range(count):
            label = names.get(str(i + 1)) or names.get(i + 1) or "板载配置 %d" % (i + 1)
            row = DeviceRow(label, active=(i == cur), max_width=self._row_width(),
                            on_click=lambda n=i: self.switch_onboard(n))
            self.sec_kb.add_row(row)
        self.sec_kb.body.addStretch()
        self._relayout()

    def switch_onboard(self, index):
        if self.kb_busy:
            return
        self.kb_busy = True
        self.status.setText("正在切换到板载配置 %d…" % (index + 1))
        self.setEnabled(False)

        def done(res):
            self.kb_busy = False
            self.setEnabled(True)
            tag, val = res
            if tag == "ok":
                ok, msg = val
                self.status.setText(msg)
                self.kb_cur = index if ok else self.kb_cur
                self._render_keyboard(self.kb_cur)
            else:
                self.status.setText("切换失败：%s" % val)

        w = ResultWorker(lambda: kb_onboard.set_profile(index))
        w.done.connect(done)
        w.start()
        self.kb_worker = w

    # ---------- 切换动作 ----------
    def switch_audio(self, dev, flow):
        if self.busy:
            return
        kind = "输出" if flow == audio_core.RENDER else "输入"
        self.status.setText("正在切换到 %s…" % dev["name"])

        def work():
            sess = audio_core.AudioSession()
            try:
                sess.set_default(dev["id"])
            finally:
                sess.enum.release()
            return "已切换到 %s：%s" % (kind, dev["name"])

        self._run(work, "切换音频设备", after=self.refresh_audio)

    def _run(self, func, desc, after=None):
        if self.busy:
            return
        self.busy = True
        self.status.setText(desc + "…")
        self.setEnabled(False)

        def finish(ok, msg):
            self.busy = False
            self.setEnabled(True)
            self.status.setText(msg)
            if after:
                QTimer.singleShot(300, after)

        self.worker = SwitchWorker(func, desc)
        self.worker.done.connect(finish)
        self.worker.start()


def _single_instance():
    """已有实例在运行则返回 False"""
    try:
        import ctypes
        from ctypes import wintypes
        ctypes.windll.kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL,
                                                        wintypes.LPCWSTR]
        ctypes.windll.kernel32.CreateMutexW.restype = wintypes.HANDLE
        h = ctypes.windll.kernel32.CreateMutexW(None, False, "DeskSwitch_SingleInstance")
        return ctypes.windll.kernel32.GetLastError() != 183  # ERROR_ALREADY_EXISTS
    except Exception:
        return True


def _log(msg):
    try:
        with open(os.path.join(APP_DIR, "run.log"), "a", encoding="utf-8") as f:
            import datetime
            f.write("[%s] %s\n" % (datetime.datetime.now().strftime("%m-%d %H:%M:%S"), msg))
    except Exception:
        pass


def main():
    import traceback
    if not _single_instance():
        _log("已有实例在运行，退出")
        return
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setFont(QFont("Microsoft YaHei UI", 9))
    try:
        w = DeskWidget()
        w.show()
        _log("启动成功")
        sys.exit(app.exec_())
    except Exception:
        _log("启动失败：\n" + traceback.format_exc())
        raise


if __name__ == "__main__":
    main()
