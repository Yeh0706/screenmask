"""ScreenMask（螢幕遮罩）：在螢幕或指定視窗上蓋一塊模糊／純色遮罩，例如擋住遊戲的 UID。

- 跟著視窗時，只有該視窗在前景才顯示；也可以改成全局顯示。
- 滑鼠會穿透遮罩，不影響操作。
- 左鍵點系統匣圖示（或按 Ctrl+Alt+H）開／關遮罩；右鍵打開選單。
- 從選單的「調整位置」可以拖曳移動、拖邊縮放。
- 設定存在 %APPDATA%\\ScreenMask\\config.json。
"""

import ctypes
import json
import os
import sys
from ctypes import wintypes

from PySide6.QtCore import QFileInfo, QLocale, QPoint, QRect, QRectF, Qt, QTimer
from PySide6.QtGui import (QAction, QColor, QFont, QIcon, QImage, QPainter, QPainterPath, QPen,
                           QPixmap)
from PySide6.QtWidgets import (QApplication, QCheckBox, QColorDialog, QComboBox, QDialog,
                               QDialogButtonBox, QFileIconProvider, QFontComboBox, QFormLayout,
                               QHBoxLayout, QLabel, QLineEdit, QMenu, QPushButton, QSpinBox,
                               QSystemTrayIcon, QVBoxLayout, QWidget)

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, ctypes.c_uint]
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetAncestor.restype = wintypes.HWND
user32.GetAncestor.argtypes = [wintypes.HWND, ctypes.c_uint]
kernel32.OpenProcess.restype = wintypes.HANDLE
user32.SetWindowDisplayAffinity.argtypes = [wintypes.HWND, wintypes.DWORD]

gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
_vp = ctypes.c_void_p
user32.GetDC.restype = _vp
user32.GetDC.argtypes = [_vp]
user32.ReleaseDC.argtypes = [_vp, _vp]
gdi32.CreateCompatibleDC.restype = _vp
gdi32.CreateCompatibleDC.argtypes = [_vp]
gdi32.CreateCompatibleBitmap.restype = _vp
gdi32.CreateCompatibleBitmap.argtypes = [_vp, ctypes.c_int, ctypes.c_int]
gdi32.SelectObject.restype = _vp
gdi32.SelectObject.argtypes = [_vp, _vp]
gdi32.BitBlt.argtypes = [_vp, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                         _vp, ctypes.c_int, ctypes.c_int, wintypes.DWORD]
gdi32.GetDIBits.argtypes = [_vp, _vp, ctypes.c_uint, ctypes.c_uint, _vp, _vp, ctypes.c_uint]
gdi32.DeleteObject.argtypes = [_vp]
gdi32.DeleteDC.argtypes = [_vp]
gdi32.CreateRoundRectRgn.restype = _vp
gdi32.CreateRoundRectRgn.argtypes = [ctypes.c_int] * 6
user32.SetWindowRgn.argtypes = [wintypes.HWND, _vp, wintypes.BOOL]
WDA_EXCLUDEFROMCAPTURE = 0x11
SRCCOPY = 0x00CC0020

GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x20
WS_EX_LAYERED = 0x80000
WS_EX_TOOLWINDOW = 0x80
WS_EX_NOACTIVATE = 0x08000000
HWND_TOPMOST = wintypes.HWND(-1)
SWP_NOACTIVATE = 0x10
SWP_NOOWNERZORDER = 0x200
SWP_FRAMECHANGED = 0x20
SWP_NOMOVE = 0x2
SWP_NOSIZE = 0x1
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
GA_ROOT = 2

APP_NAME = "ScreenMask"
_APPDATA = os.environ.get("APPDATA", os.path.expanduser("~"))
CONFIG_DIR = os.path.join(_APPDATA, APP_NAME)
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")
OLD_CONFIG_PATH = os.path.join(_APPDATA, "UidMask", "config.json")  # 改名前的設定位置

# 全域快速鍵：Ctrl+Alt+H 開／關遮罩
WM_HOTKEY = 0x0312
HOTKEY_ID = 0x5D01
MOD_ALT, MOD_CONTROL, MOD_NOREPEAT = 0x1, 0x2, 0x4000
HOTKEY_MODS, HOTKEY_VK, HOTKEY_TEXT = MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, ord("H"), "Ctrl+Alt+H"
user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_uint, ctypes.c_uint]
user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]

# 跟著視窗時，位置與大小用「視窗畫面高度的比例」表示，錨點在右下角。
# 全局顯示時，位置用螢幕座標記在 screen_rect。
DEFAULT_CONFIG = {
    "enabled": True,         # 遮罩開關（左鍵點系統匣圖示或按 Ctrl+Alt+H 切換）
    "anchor": "window",      # window = 跟著目標視窗；screen = 全局顯示（一直顯示在螢幕上）
    "targets": ["StarRail.exe"],
    "target_title": "",      # 同一個程式有多個視窗時，優先找標題相同的
    "screen_rect": None,     # 全局顯示的位置 [x, y, w, h]
    "mode": "soft",          # soft = 柔和模糊；blur = 系統模糊（較重）；solid = 純色
    "strength": 6,           # 柔和模糊的強度，數字越大越糊
    "feather": 14,           # 柔和模糊邊緣漸層的寬度（像素）
    "color": [18, 18, 24],
    "blur_tint_alpha": 0,    # 系統模糊模式疊上去的顏色濃度 0-255
    "rect": {"left": 0.0, "top": 0.0, "width": 0.42, "height": 0.09},
    "lang": "",              # zh-TW / zh-CN / en；空白 = 依系統語言
    "corner": 0.0,           # 圓角程度 0~1（1 = 短邊完全變成半圓）
    # 自訂文字（蓋在遮罩上，留空就不顯示）
    "text": "",
    "text_color": [255, 255, 255],
    "text_scale": 0.55,      # 字的高度佔框高的比例
    "text_align": "center",  # left / center / right
    "text_bold": False,
    "text_shadow": True,
    "text_font": "Microsoft JhengHei UI",
}


def load_config():
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    try:
        # 第一次用新名字啟動時，沿用改名前的設定。
        path = CONFIG_PATH if os.path.exists(CONFIG_PATH) or not os.path.exists(OLD_CONFIG_PATH) \
            else OLD_CONFIG_PATH
        # utf-8-sig：用記事本或 PowerShell 存的檔案開頭可能有 BOM。
        with open(path, encoding="utf-8-sig") as f:
            saved = json.load(f)
        for k, v in saved.items():
            if k == "rect" and not (isinstance(v, dict) and "width" in v and "height" in v):
                continue  # 位置資料不完整就用預設
            cfg[k] = v
    except (OSError, ValueError):
        pass
    return cfg


def save_config(cfg):
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


# ---------- 介面語言 ----------

LANG_NAMES = {"zh-TW": "繁體中文", "zh-CN": "简体中文", "en": "English"}

STRINGS = {
    "app": ("ScreenMask 螢幕遮罩", "ScreenMask 屏幕遮罩", "ScreenMask"),
    "enabled": ("啟用遮罩", "启用遮罩", "Mask on"),
    "paused_tip": ("{app}（已暫停）", "{app}（已暂停）", "{app} (paused)"),
    "edit": ("調整位置", "调整位置", "Adjust position"),
    "done": ("完成調整", "完成调整", "Finish adjusting"),
    "no_target": ("找不到目標視窗，請先打開它，或從「顯示在」改選其他視窗。",
                  "找不到目标窗口，请先打开它，或从“显示在”改选其他窗口。",
                  'Target window not found. Open it first, or pick another window under "Show on".'),
    "show_on": ("顯示在", "显示在", "Show on"),
    "global": ("全局顯示（一直顯示在螢幕上）", "全局显示（一直显示在屏幕上）", "Global (always on screen)"),
    "global_msg": ("已改成全局顯示。用「調整位置」把遮罩拖到要蓋的地方。",
                   "已改为全局显示。用“调整位置”把遮罩拖到要盖的地方。",
                   'Switched to global. Use "Adjust position" to drag the mask into place.'),
    "follow": ("跟著視窗（只在該視窗在前景時顯示）：", "跟随窗口（仅在该窗口位于前台时显示）：",
               "Follow a window (shown only while it is in front):"),
    "not_open": ("{name}（目前沒開）", "{name}（当前未打开）", "{name} (not open)"),
    "style": ("遮罩樣式", "遮罩样式", "Mask style"),
    "soft": ("柔和模糊", "柔和模糊", "Soft blur"),
    "blur": ("系統模糊（較重）", "系统模糊（较重）", "System blur (stronger)"),
    "solid": ("純色", "纯色", "Solid color"),
    "strength": ("柔和模糊強度", "柔和模糊强度", "Soft blur strength"),
    "s_light": ("輕", "轻", "Light"),
    "s_mid": ("中", "中", "Medium"),
    "s_heavy": ("重", "重", "Heavy"),
    "s_max": ("很重", "很重", "Very heavy"),
    "corner": ("圓角", "圆角", "Rounded corners"),
    "c_none": ("無（直角）", "无（直角）", "None (square)"),
    "c_small": ("小", "小", "Small"),
    "c_mid": ("中", "中", "Medium"),
    "c_large": ("大", "大", "Large"),
    "c_full": ("全圓（膠囊形）", "全圆（胶囊形）", "Full (pill)"),
    "color": ("遮罩顏色…", "遮罩颜色…", "Mask color…"),
    "color_title": ("遮罩顏色", "遮罩颜色", "Mask color"),
    "text": ("遮罩文字…", "遮罩文字…", "Mask text…"),
    "reset": ("恢復預設位置", "恢复默认位置", "Reset position"),
    "language": ("語言", "语言", "Language"),
    "quit": ("結束", "退出", "Quit"),
    "started": ("已在背景執行。左鍵點系統匣圖示（或按 {hotkey}）開／關遮罩，右鍵打開選單。",
                "已在后台运行。左键点击托盘图标（或按 {hotkey}）开／关遮罩，右键打开菜单。",
                "Running in the background. Left-click the tray icon (or press {hotkey}) to turn the "
                "mask on/off; right-click for the menu."),
    "hint_edit": ("拖曳移動・拖邊縮放\n完成後按系統匣的「完成調整」",
                  "拖动移动・拖边缩放\n完成后点托盘的“完成调整”",
                  'Drag to move · drag edges to resize\nThen choose "Finish adjusting" in the tray'),
    "hint_small": ("調整中", "调整中", "Adjusting"),
    # 文字設定視窗
    "dlg_title": ("遮罩文字", "遮罩文字", "Mask text"),
    "dlg_placeholder": ("例如：UID: 888888888（留空就不顯示文字）",
                        "例如：UID: 888888888（留空则不显示文字）",
                        "e.g. UID: 888888888 (leave empty to hide)"),
    "dlg_text": ("文字", "文字", "Text"),
    "dlg_font": ("字型", "字体", "Font"),
    "dlg_size": ("大小", "大小", "Size"),
    "dlg_size_tip": ("字的高度佔框高的比例", "字的高度占框高的比例", "Text height as a share of the box height"),
    "dlg_align": ("對齊", "对齐", "Align"),
    "dlg_left": ("靠左", "靠左", "Left"),
    "dlg_center": ("置中", "居中", "Center"),
    "dlg_right": ("靠右", "靠右", "Right"),
    "dlg_color": ("顏色", "颜色", "Color"),
    "dlg_color_title": ("文字顏色", "文字颜色", "Text color"),
    "dlg_bold": ("粗體", "粗体", "Bold"),
    "dlg_shadow": ("陰影", "阴影", "Shadow"),
    "dlg_hint": ("改動會即時顯示在遮罩上。", "改动会实时显示在遮罩上。", "Changes show on the mask right away."),
    "ok": ("確定", "确定", "OK"),
    "cancel": ("取消", "取消", "Cancel"),
}

_LANG_INDEX = {"zh-TW": 0, "zh-CN": 1, "en": 2}
_lang = "zh-TW"


def system_lang():
    name = QLocale.system().name()  # 例如 zh_TW、zh_CN、en_US
    if name.startswith("zh"):
        return "zh-CN" if name in ("zh_CN", "zh_SG") else "zh-TW"
    return "en"


def set_lang(lang):
    global _lang
    _lang = lang if lang in _LANG_INDEX else system_lang()


def tr(key, **kw):
    text = STRINGS[key][_LANG_INDEX[_lang]]
    return text.format(**kw) if kw else text


# ---------- Win32 輔助 ----------

def process_name(hwnd):
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(len(buf))
        if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return os.path.basename(buf.value)
        return ""
    finally:
        kernel32.CloseHandle(h)


def client_rect_on_screen(hwnd):
    rc = wintypes.RECT()
    if not user32.GetClientRect(hwnd, ctypes.byref(rc)):
        return None
    pt = wintypes.POINT(0, 0)
    user32.ClientToScreen(hwnd, ctypes.byref(pt))
    w, h = rc.right - rc.left, rc.bottom - rc.top
    if w <= 0 or h <= 0:
        return None
    return pt.x, pt.y, w, h


def window_title(hwnd):
    n = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


dwmapi = ctypes.WinDLL("dwmapi")


def is_cloaked(hwnd):
    cloaked = wintypes.DWORD()
    dwmapi.DwmGetWindowAttribute(wintypes.HWND(hwnd), 14, ctypes.byref(cloaked),
                                 ctypes.sizeof(cloaked))  # 14 = DWMWA_CLOAKED
    return cloaked.value != 0


def list_windows():
    """列出可以選的視窗：看得到、有標題、不是工具視窗、不是自己。回傳 [(hwnd, 標題, 程式名)]。"""
    me = os.getpid()
    out = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _):
        if not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
            return True
        if user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE) & WS_EX_TOOLWINDOW:
            return True
        if is_cloaked(hwnd):  # 看起來可見、其實藏起來的系統視窗（輸入法、背景的設定頁等）
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        title = window_title(hwnd)
        cr = client_rect_on_screen(hwnd)
        if pid.value != me and title and cr and cr[2] > 200 and cr[3] > 100:
            out.append((hwnd, title, process_name(hwnd)))
        return True

    user32.EnumWindows(cb, 0)
    return out


def find_game_window(targets, title=""):
    targets = {t.lower() for t in targets}
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _):
        if user32.IsWindowVisible(hwnd) and process_name(hwnd).lower() in targets:
            cr = client_rect_on_screen(hwnd)
            if cr and cr[2] > 200 and cr[3] > 100:
                found.append(hwnd)
        return True

    user32.EnumWindows(cb, 0)
    for hwnd in found:
        if title and window_title(hwnd) == title:
            return hwnd
    return found[0] if found else None


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]


user32.MonitorFromRect.restype = ctypes.c_void_p
user32.MonitorFromRect.argtypes = [ctypes.POINTER(wintypes.RECT), wintypes.DWORD]
user32.GetMonitorInfoW.argtypes = [ctypes.c_void_p, ctypes.POINTER(MONITORINFO)]


def monitor_rect(x, y, w, h):
    """這塊範圍所在（或最接近）的螢幕範圍。"""
    rc = wintypes.RECT(x, y, x + w, y + h)
    mon = user32.MonitorFromRect(ctypes.byref(rc), 2)  # MONITOR_DEFAULTTONEAREST
    mi = MONITORINFO()
    mi.cbSize = ctypes.sizeof(MONITORINFO)
    user32.GetMonitorInfoW(mon, ctypes.byref(mi))
    m = mi.rcMonitor
    return m.left, m.top, m.right - m.left, m.bottom - m.top


def window_rect(hwnd):
    rc = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rc))
    return rc.left, rc.top, rc.right - rc.left, rc.bottom - rc.top


def cursor_pos():
    pt = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y


class ACCENT_POLICY(ctypes.Structure):
    _fields_ = [("AccentState", ctypes.c_int), ("AccentFlags", ctypes.c_int),
                ("GradientColor", ctypes.c_uint), ("AnimationId", ctypes.c_int)]


class WINCOMPATTRDATA(ctypes.Structure):
    _fields_ = [("Attribute", ctypes.c_int), ("Data", ctypes.c_void_p),
                ("SizeOfData", ctypes.c_size_t)]


def set_blur(hwnd, enabled):
    accent = ACCENT_POLICY(3 if enabled else 0, 0, 0, 0)  # 3 = ACCENT_ENABLE_BLURBEHIND
    data = WINCOMPATTRDATA(19, ctypes.cast(ctypes.pointer(accent), ctypes.c_void_p),
                           ctypes.sizeof(accent))
    user32.SetWindowCompositionAttribute(wintypes.HWND(hwnd), ctypes.byref(data))


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD),
                ("biCompression", wintypes.DWORD), ("biSizeImage", wintypes.DWORD),
                ("biXPelsPerMeter", wintypes.LONG), ("biYPelsPerMeter", wintypes.LONG),
                ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]


def grab_screen(x, y, w, h):
    """截取螢幕上一塊區域（實體像素）。遮罩本身設了不被截圖，所以截到的是底下的遊戲畫面。"""
    sdc = user32.GetDC(None)
    mdc = gdi32.CreateCompatibleDC(sdc)
    bmp = gdi32.CreateCompatibleBitmap(sdc, w, h)
    old = gdi32.SelectObject(mdc, bmp)
    try:
        gdi32.BitBlt(mdc, 0, 0, w, h, sdc, x, y, SRCCOPY)
        bi = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
        buf = ctypes.create_string_buffer(w * h * 4)
        gdi32.GetDIBits(mdc, bmp, 0, h, buf, ctypes.byref(bi), 0)
        return QImage(buf.raw, w, h, w * 4, QImage.Format_RGB32).copy()
    finally:
        gdi32.SelectObject(mdc, old)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mdc)
        user32.ReleaseDC(None, sdc)


def soft_blur(img, strength):
    """縮小再放大兩次，做出柔和的模糊。"""
    w, h = img.width(), img.height()
    k = max(2, int(strength))
    out = img
    for _ in range(2):
        small = out.scaled(max(1, w // k), max(1, h // k), Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        out = small.scaled(w, h, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    return out


def _ramp(n, start, end):
    """長度 n 的透明度序列：前 start 格從透明漸變到不透明，後 end 格再漸變回透明。"""
    out = []
    for i in range(n):
        a = 1.0
        if i < start:
            a = min(a, ((i + 0.5) / start) ** 1.5)
        if i >= n - end:
            a = min(a, ((n - i - 0.5) / end) ** 1.5)
        out.append(round(255 * a))
    return out


def feather_mask(w, h, ring):
    """遮色片：框選範圍內完全不透明，外圍 ring=(左, 上, 右, 下) 那一圈漸漸變透明。"""
    l, t, r, b = ring
    fmt = QImage.Format_ARGB32_Premultiplied
    hx = QImage(w, 1, fmt)
    for x, a in enumerate(_ramp(w, l, r)):
        hx.setPixelColor(x, 0, QColor(0, 0, 0, a))
    vy = QImage(1, h, fmt)
    for y, a in enumerate(_ramp(h, t, b)):
        vy.setPixelColor(0, y, QColor(0, 0, 0, a))
    mask = hx.scaled(w, h, Qt.IgnoreAspectRatio, Qt.FastTransformation)
    p = QPainter(mask)
    p.setCompositionMode(QPainter.CompositionMode_DestinationIn)
    p.drawImage(QRect(0, 0, w, h), vy)
    p.end()
    return mask


def shape_mask(w, h, ring, radius, feather):
    """圓角遮色片：框選範圍（扣掉外圍 ring）內完全不透明，往外 feather 像素內漸漸變透明。

    radius 是框選範圍的圓角半徑（像素）。

    做法：圓角矩形往外擴 d 像素，剛好是半徑 +d 的圓角矩形；從最外圈往內一圈一圈蓋上
    越來越不透明的圓角矩形，就得到照形狀漸層的邊緣。"""
    l, t, r, b = ring
    sw, sh = max(1, w - l - r), max(1, h - t - b)
    rad = max(0.0, min(radius, sw / 2, sh / 2))
    mask = QImage(w, h, QImage.Format_ARGB32_Premultiplied)
    mask.fill(Qt.transparent)
    p = QPainter(mask)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setCompositionMode(QPainter.CompositionMode_Source)
    for i in range(max(0, int(feather)), -1, -1):
        d = float(i)
        a = 1.0 if feather <= 0 else ((feather - d + 0.5) / (feather + 0.5)) ** 1.5
        path = QPainterPath()
        path.addRoundedRect(QRectF(l - d, t - d, sw + 2 * d, sh + 2 * d), rad + d, rad + d)
        p.fillPath(path, QColor(0, 0, 0, round(255 * min(1.0, a))))
    p.end()
    return mask


# ---------- 遮罩視窗 ----------

EDGE = 10  # 調整模式下，邊緣幾像素內算縮放


class Mask(QWidget):
    def __init__(self, cfg, targets):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                         | Qt.WindowDoesNotAcceptFocus)
        self.cfg = cfg
        self.targets = targets
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setMouseTracking(True)
        self.game = None
        self.edit_mode = False
        self.drag = None  # (zone, cursor_start, rect_start)
        self.resize(10, 10)
        self.hwnd = int(self.winId())
        self.frame = None
        self.ring = (0, 0, 0, 0)  # 框選範圍外圍漸層的寬度（左、上、右、下）
        self._mask_cache = (None, None)
        self._region_key = None
        self.on_hotkey = None  # 按下全域快速鍵時呼叫
        # 讓遮罩不出現在截圖裡，柔和模糊才截得到底下的畫面。
        user32.SetWindowDisplayAffinity(self.hwnd, WDA_EXCLUDEFROMCAPTURE)
        self.apply_style()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(100)

        self.cap_timer = QTimer(self)
        self.cap_timer.timeout.connect(self.capture)
        self.cap_timer.start(50)

    def capture(self):
        if self.cfg["mode"] != "soft" or not self.isVisible():
            return
        x, y, w, h = window_rect(self.hwnd)
        if w < 4 or h < 4:
            return
        img = soft_blur(grab_screen(x, y, w, h), self.cfg.get("strength", 6))
        feather = 0 if self.edit_mode else max(0, int(self.cfg.get("feather", 14)))
        key = (w, h, self.ring, self.corner_radius(), feather)
        if self._mask_cache[0] != key:
            self._mask_cache = (key, shape_mask(w, h, self.ring, key[3], feather))
        out = img.convertToFormat(QImage.Format_ARGB32_Premultiplied)
        p = QPainter(out)
        p.setCompositionMode(QPainter.CompositionMode_DestinationIn)
        p.drawImage(0, 0, self._mask_cache[1])
        p.end()
        self.frame = out
        self.update()

    # --- 外觀 ---
    def apply_style(self):
        ex = user32.GetWindowLongPtrW(self.hwnd, GWL_EXSTYLE)
        ex |= WS_EX_TOOLWINDOW | WS_EX_LAYERED
        if self.edit_mode:
            ex &= ~(WS_EX_TRANSPARENT | WS_EX_NOACTIVATE)
        else:
            ex |= WS_EX_TRANSPARENT | WS_EX_NOACTIVATE
        user32.SetWindowLongPtrW(self.hwnd, GWL_EXSTYLE, ex)
        user32.SetWindowPos(self.hwnd, None, 0, 0, 0, 0,
                            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_FRAMECHANGED)
        set_blur(self.hwnd, self.cfg["mode"] == "blur")
        self._region_key = None
        self.update_region()
        if self.cfg["mode"] == "soft":
            self.capture()
        self.setCursor(Qt.SizeAllCursor if self.edit_mode else Qt.ArrowCursor)
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        r, g, b = self.cfg["color"]
        mode = self.cfg["mode"]
        if mode == "soft":
            p.setCompositionMode(QPainter.CompositionMode_Source)
            p.fillRect(self.rect(), QColor(0, 0, 0, 1))
            if self.frame is not None:
                p.setRenderHint(QPainter.SmoothPixmapTransform)
                p.drawImage(self.rect(), self.frame)
            p.setCompositionMode(QPainter.CompositionMode_SourceOver)
            if self.edit_mode:
                p.setRenderHint(QPainter.Antialiasing)
                p.fillPath(self.shape_path(), QColor(r, g, b, 90))
        else:
            alpha = self.cfg["blur_tint_alpha"] if mode == "blur" else 255
            if self.edit_mode:
                alpha = max(alpha, 120)
            p.setCompositionMode(QPainter.CompositionMode_Source)
            p.fillRect(self.rect(), Qt.transparent)
            p.setCompositionMode(QPainter.CompositionMode_SourceOver)
            p.setRenderHint(QPainter.Antialiasing)
            # 全透明的像素在模糊模式下會被當成「沒有視窗」，所以至少留 1。
            p.fillPath(self.shape_path(), QColor(r, g, b, max(alpha, 1)))
        has_text = bool(self.cfg.get("text", "").strip())
        if has_text:
            self.draw_text(p)
        if self.edit_mode:
            p.setRenderHint(QPainter.Antialiasing)
            p.setPen(QPen(QColor(255, 200, 80), 2, Qt.DashLine))
            p.setBrush(Qt.NoBrush)
            p.drawPath(self.shape_path(inset=1))
            p.setPen(QColor(255, 255, 255))
            f = QFont("Microsoft JhengHei UI")
            if has_text:
                # 有自訂文字時，提示縮小放在左上角，免得擋住預覽。
                f.setPixelSize(11)
                p.setFont(f)
                p.drawText(self.rect().adjusted(6, 3, -6, -3), Qt.AlignLeft | Qt.AlignTop, tr("hint_small"))
            else:
                f.setPixelSize(max(11, min(16, self.height() // 4)))
                p.setFont(f)
                p.drawText(self.rect(), Qt.AlignCenter | Qt.TextWordWrap, tr("hint_edit"))
        p.end()

    def corner_radius(self):
        """框選範圍的圓角半徑（像素）。"""
        l, t, r, b = self.ring
        w, h = self.width() - l - r, self.height() - t - b
        return round(max(0.0, min(1.0, float(self.cfg.get("corner", 0)))) * min(w, h) / 2, 1)

    def shape_path(self, inset=0):
        """框選範圍的圓角外框（不含外圍漸層）。"""
        l, t, r, b = self.ring
        box = QRectF(self.rect()).adjusted(l + inset, t + inset, -r - inset, -b - inset)
        rad = max(0.0, min(self.corner_radius() - inset, box.width() / 2, box.height() / 2))
        path = QPainterPath()
        path.addRoundedRect(box, rad, rad)
        return path

    def update_region(self):
        """系統模糊是整個視窗一起模糊，圓角時要把視窗本身裁成圓角形狀。"""
        rad = self.corner_radius() if self.cfg["mode"] == "blur" else 0
        key = (self.width(), self.height(), rad)
        if key == self._region_key:
            return
        self._region_key = key
        if rad > 0:
            d = max(2, round(rad * 2))
            rgn = gdi32.CreateRoundRectRgn(0, 0, self.width() + 1, self.height() + 1, d, d)
            user32.SetWindowRgn(self.hwnd, rgn, True)  # 視窗接手 rgn，不用自己刪
        else:
            user32.SetWindowRgn(self.hwnd, None, True)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if getattr(self, "_region_key", "init") != "init":  # 建構途中還沒準備好就跳過
            self.update_region()

    def draw_text(self, p):
        """在框選範圍內畫自訂文字（不含外圍漸層）。"""
        l, t, r, b = self.ring
        box = self.rect().adjusted(l, t, -r, -b)
        pad = max(2, box.height() // 10)
        box = box.adjusted(pad, 0, -pad, 0)
        f = QFont(self.cfg.get("text_font", "Microsoft JhengHei UI"))
        f.setPixelSize(max(6, round(box.height() * self.cfg.get("text_scale", 0.55))))
        f.setBold(bool(self.cfg.get("text_bold", False)))
        p.setFont(f)
        align = {"left": Qt.AlignLeft, "center": Qt.AlignHCenter,
                 "right": Qt.AlignRight}.get(self.cfg.get("text_align", "center"), Qt.AlignHCenter)
        flags = align | Qt.AlignVCenter
        text = self.cfg["text"]
        p.setRenderHint(QPainter.TextAntialiasing)
        if self.cfg.get("text_shadow", True):
            p.setPen(QColor(0, 0, 0, 160))
            p.drawText(box.translated(1, 1), flags, text)
        p.setPen(QColor(*self.cfg.get("text_color", [255, 255, 255])))
        p.drawText(box, flags, text)

    # --- 位置 ---
    @property
    def is_global(self):
        return self.cfg.get("anchor") == "screen"

    def area(self, x=None, y=None, w=1, h=1):
        """遮罩可以活動的範圍：跟著視窗時是視窗畫面；全局顯示時是遮罩所在的那個螢幕。"""
        if self.is_global:
            if x is None:
                x, y, w, h = self.cfg.get("screen_rect") or (0, 0, 1, 1)
            return monitor_rect(x, y, w, h)
        return client_rect_on_screen(self.game)

    def target_rect(self):
        if self.is_global:
            sr = self.cfg.get("screen_rect")
            if not sr:
                # 第一次切成全局：放在主螢幕左上角。
                mx, my, mw, mh = monitor_rect(0, 0, 1, 1)
                w, h = round(mh * 0.3), round(mh * 0.06)
                sr = [mx, my, w, h]
                self.cfg["screen_rect"] = sr
            return self.clamp(*sr, keep_size=True)
        gx, gy, gw, gh = client_rect_on_screen(self.game)
        rc = self.cfg["rect"]
        w = round(rc["width"] * gh)
        h = round(rc["height"] * gh)
        # 位置記的是到最近那個角的距離（left/right、top/bottom 各存其一）。
        if "left" in rc:
            x = gx + round(rc["left"] * gh)
        else:
            x = gx + gw - round(rc.get("right", 0) * gh) - w
        if "top" in rc:
            y = gy + round(rc["top"] * gh)
        else:
            y = gy + gh - round(rc.get("bottom", 0) * gh) - h
        return self.clamp(x, y, w, h, keep_size=True)

    def clamp(self, x, y, w, h, keep_size):
        """把範圍限制在活動範圍內。keep_size=True 時整塊推回來（移動用），否則超出的部分切掉（縮放用）。"""
        gx, gy, gw, gh = self.area(x, y, w, h)
        w, h = min(max(w, 20), gw), min(max(h, 12), gh)
        if keep_size:
            x = min(max(x, gx), gx + gw - w)
            y = min(max(y, gy), gy + gh - h)
            return x, y, w, h
        x2, y2 = min(x + w, gx + gw), min(y + h, gy + gh)
        x, y = max(x, gx), max(y, gy)
        return x, y, max(x2 - x, 20), max(y2 - y, 12)

    def outer_rect(self, x, y, w, h):
        """柔和模糊時，視窗比框選範圍向外多一圈漸層（但不超出活動範圍）。回傳視窗範圍與每邊的漸層寬度。"""
        if self.cfg["mode"] != "soft" or self.edit_mode:
            return (x, y, w, h), (0, 0, 0, 0)
        f = max(0, int(self.cfg.get("feather", 14)))
        gx, gy, gw, gh = self.area(x, y, w, h)
        l, t = min(f, x - gx), min(f, y - gy)
        r, b = min(f, gx + gw - (x + w)), min(f, gy + gh - (y + h))
        l, t, r, b = (max(0, v) for v in (l, t, r, b))
        return (x - l, y - t, w + l + r, h + t + b), (l, t, r, b)

    def store_rect(self):
        """把目前框選的位置存起來（跟著視窗時換算成比例）。"""
        x, y, w, h = window_rect(self.hwnd)
        if self.is_global:
            self.cfg["screen_rect"] = [x, y, w, h]
            save_config(self.cfg)
            return
        if not self.game or not user32.IsWindow(self.game):
            return
        gx, gy, gw, gh = client_rect_on_screen(self.game)
        # 以最靠近的角為基準：遊戲介面通常貼著角落排版，視窗大小改變時位置才對得上。
        rc = {"width": round(w / gh, 5), "height": round(h / gh, 5)}
        if x + w / 2 < gx + gw / 2:
            rc["left"] = round((x - gx) / gh, 5)
        else:
            rc["right"] = round((gx + gw - (x + w)) / gh, 5)
        if y + h / 2 < gy + gh / 2:
            rc["top"] = round((y - gy) / gh, 5)
        else:
            rc["bottom"] = round((gy + gh - (y + h)) / gh, 5)
        self.cfg["rect"] = rc
        save_config(self.cfg)

    def place(self, x, y, w, h):
        user32.SetWindowPos(self.hwnd, HWND_TOPMOST, x, y, max(w, 20), max(h, 12),
                            SWP_NOACTIVATE | SWP_NOOWNERZORDER)

    def set_target(self, exe, title=""):
        """改成跟著指定的視窗。"""
        self.cfg["anchor"] = "window"
        self.cfg["targets"] = [exe]
        self.cfg["target_title"] = title
        self.targets = [exe]
        self.game = None
        save_config(self.cfg)

    def set_global(self):
        self.cfg["anchor"] = "screen"
        save_config(self.cfg)

    # --- 開關 ---
    @property
    def enabled(self):
        return bool(self.cfg.get("enabled", True))

    def set_enabled(self, on):
        if not on and self.edit_mode:
            self.set_edit_mode(False)
        self.cfg["enabled"] = bool(on)
        save_config(self.cfg)
        if not on:
            self.hide()

    def nativeEvent(self, event_type, message):
        # 全域快速鍵（RegisterHotKey 綁在這個視窗上）
        if event_type == b"windows_generic_MSG":
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == WM_HOTKEY and msg.wParam == HOTKEY_ID and self.on_hotkey:
                self.on_hotkey()
                return True, 0
        return super().nativeEvent(event_type, message)

    # --- 主迴圈 ---
    def tick(self):
        if not self.enabled:
            self.hide()
            return
        if not self.is_global:
            if not self.game or not user32.IsWindow(self.game) or not user32.IsWindowVisible(self.game):
                self.game = find_game_window(self.targets, self.cfg.get("target_title", ""))
            if not self.game or user32.IsIconic(self.game) or not client_rect_on_screen(self.game):
                self.hide()
                return

            fg = user32.GetForegroundWindow()
            fg_root = user32.GetAncestor(fg, GA_ROOT) if fg else None
            game_active = fg_root == self.game or fg == self.game
            if not (game_active or self.edit_mode):
                self.hide()
                return

        if not self.isVisible():
            self.show()
            self.apply_style()
        if self.drag is None:
            outer, ring = self.outer_rect(*self.target_rect())
            self.ring = ring
            self.place(*outer)

    # --- 調整模式 ---
    def set_edit_mode(self, on):
        if on and not self.is_global and not self.game:
            return False
        self.edit_mode = on
        self.drag = None
        self.apply_style()
        if not on:
            self.store_rect()
        return True

    def zone_at(self, pos: QPoint):
        r = self.rect()
        left = pos.x() <= EDGE
        right = pos.x() >= r.width() - EDGE
        top = pos.y() <= EDGE
        bottom = pos.y() >= r.height() - EDGE
        return (left, right, top, bottom)

    def mousePressEvent(self, e):
        if self.edit_mode and e.button() == Qt.LeftButton:
            self.drag = (self.zone_at(e.position().toPoint()), cursor_pos(), window_rect(self.hwnd))

    def mouseMoveEvent(self, e):
        if not self.edit_mode:
            return
        if self.drag is None:
            left, right, top, bottom = self.zone_at(e.position().toPoint())
            if (left and top) or (right and bottom):
                self.setCursor(Qt.SizeFDiagCursor)
            elif (right and top) or (left and bottom):
                self.setCursor(Qt.SizeBDiagCursor)
            elif left or right:
                self.setCursor(Qt.SizeHorCursor)
            elif top or bottom:
                self.setCursor(Qt.SizeVerCursor)
            else:
                self.setCursor(Qt.SizeAllCursor)
            return
        (left, right, top, bottom), (cx0, cy0), (x, y, w, h) = self.drag
        cx, cy = cursor_pos()
        dx, dy = cx - cx0, cy - cy0
        if not (left or right or top or bottom):
            x, y = x + dx, y + dy
        else:
            if left:
                x, w = x + dx, w - dx
            if right:
                w = w + dx
            if top:
                y, h = y + dy, h - dy
            if bottom:
                h = h + dy
        moving = not (left or right or top or bottom)
        self.place(*self.clamp(x, y, w, h, keep_size=moving))

    def mouseReleaseEvent(self, e):
        if self.edit_mode and self.drag is not None:
            self.drag = None
            self.store_rect()


# ---------- 文字設定視窗 ----------

TEXT_KEYS = ("text", "text_color", "text_scale", "text_align", "text_bold", "text_shadow", "text_font")


class TextDialog(QDialog):
    """編輯遮罩上的自訂文字，改動會即時顯示在遮罩上；按「取消」還原。"""

    def __init__(self, cfg, mask):
        super().__init__(None, Qt.WindowStaysOnTopHint)
        self.cfg, self.mask = cfg, mask
        self.backup = {k: cfg.get(k) for k in TEXT_KEYS}
        self.setWindowTitle(tr("dlg_title"))
        self.setWindowIcon(menu_icon("text"))
        self.setMinimumWidth(360)

        self.edit = QLineEdit(cfg.get("text", ""))
        self.edit.setPlaceholderText(tr("dlg_placeholder"))
        self.font_box = QFontComboBox()
        self.font_box.setCurrentFont(QFont(cfg.get("text_font", "Microsoft JhengHei UI")))
        self.size = QSpinBox()
        self.size.setRange(10, 100)
        self.size.setSuffix(" %")
        self.size.setValue(round(cfg.get("text_scale", 0.55) * 100))
        self.size.setToolTip(tr("dlg_size_tip"))
        self.align = QComboBox()
        for key in ("left", "center", "right"):
            self.align.addItem(tr("dlg_" + key), key)
        self.align.setCurrentIndex(max(0, self.align.findData(cfg.get("text_align", "center"))))
        self.bold = QCheckBox(tr("dlg_bold"))
        self.bold.setChecked(bool(cfg.get("text_bold", False)))
        self.shadow = QCheckBox(tr("dlg_shadow"))
        self.shadow.setChecked(bool(cfg.get("text_shadow", True)))
        self.color_btn = QPushButton()
        self.color_btn.setIcon(menu_icon("color"))
        self.color_btn.clicked.connect(self.pick_color)
        self._color = QColor(*cfg.get("text_color", [255, 255, 255]))
        self._refresh_color_btn()

        form = QFormLayout()
        form.addRow(tr("dlg_text"), self.edit)
        form.addRow(tr("dlg_font"), self.font_box)
        form.addRow(tr("dlg_size"), self.size)
        form.addRow(tr("dlg_align"), self.align)
        form.addRow(tr("dlg_color"), self.color_btn)
        row = QHBoxLayout()
        row.addWidget(self.bold)
        row.addWidget(self.shadow)
        row.addStretch()
        form.addRow("", row)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText(tr("ok"))
        buttons.button(QDialogButtonBox.Cancel).setText(tr("cancel"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        hint = QLabel(tr("dlg_hint"))
        hint.setStyleSheet("color: gray;")
        lay.addWidget(hint)
        lay.addWidget(buttons)

        for sig in (self.edit.textChanged, self.font_box.currentFontChanged, self.size.valueChanged,
                    self.align.currentIndexChanged, self.bold.toggled, self.shadow.toggled):
            sig.connect(self.apply)

    def _refresh_color_btn(self):
        self.color_btn.setText(self._color.name().upper())

    def pick_color(self):
        c = QColorDialog.getColor(self._color, self, tr("dlg_color_title"))
        if c.isValid():
            self._color = c
            self._refresh_color_btn()
            self.apply()

    def apply(self, *_):
        self.cfg["text"] = self.edit.text()
        self.cfg["text_font"] = self.font_box.currentFont().family()
        self.cfg["text_scale"] = self.size.value() / 100
        self.cfg["text_align"] = self.align.currentData()
        self.cfg["text_bold"] = self.bold.isChecked()
        self.cfg["text_shadow"] = self.shadow.isChecked()
        self.cfg["text_color"] = [self._color.red(), self._color.green(), self._color.blue()]
        self.mask.update()

    def accept(self):
        self.apply()
        save_config(self.cfg)
        super().accept()

    def reject(self):
        self.cfg.update(self.backup)
        self.mask.update()
        super().reject()


# ---------- 選單圖示 ----------

# Windows 內建的圖示字型（Win11 是 Segoe Fluent Icons，Win10 是 Segoe MDL2 Assets）。
ICON_FONT = "Segoe Fluent Icons"
ICON_GLYPHS = {
    "move": "\uE7C2",      # 四向箭頭
    "done": "\uE73E",      # 打勾
    "monitor": "\uE7F4",   # 螢幕
    "globe": "\uE774",     # 地球
    "window": "\uE737",    # 視窗
    "style": "\uE81E",     # 圖層
    "soft": "\uE793",      # 半透明
    "blur": "\uE81E",      # 圖層
    "solid": "\uE73B",     # 實心方塊
    "strength": "\uE9E9",  # 滑桿
    "color": "\uE790",     # 調色盤
    "text": "\uE8D2",      # 字型
    "reset": "\uE777",     # 復原
    "quit": "\uE7E8",      # 電源
    "language": "\uE8C1",  # A字
    "corner": "\uF16B",    # 圓角方框
    "eye": "\uE7B3",       # 眼睛（開關）
}
_icon_cache = {}


def menu_icon(name):
    if name in _icon_cache:
        return _icon_cache[name]
    glyph = ICON_GLYPHS.get(name)
    if not glyph:
        return QIcon()
    icon = QIcon()
    for size in (16, 20, 24, 32):
        pm = QPixmap(size, size)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.TextAntialiasing)
        f = QFont(ICON_FONT)
        f.setPixelSize(round(size * 0.8))
        p.setFont(f)
        p.setPen(QApplication.palette().windowText().color())
        p.drawText(pm.rect(), Qt.AlignCenter, glyph)
        p.end()
        icon.addPixmap(pm)
    _icon_cache[name] = icon
    return icon


# ---------- 系統匣 ----------

def make_icon(enabled=True):
    """系統匣圖示：白色圓角框，下方一條白色遮罩條。暫停時變淡。"""
    pm = QPixmap(64, 64)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setOpacity(1.0 if enabled else 0.35)
    p.setPen(QPen(QColor(255, 255, 255), 5))
    p.setBrush(Qt.NoBrush)
    p.drawRoundedRect(QRectF(5.5, 5.5, 53, 53), 13, 13)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(255, 255, 255))
    p.drawRoundedRect(QRectF(15, 34, 34, 13), 6, 6)
    p.end()
    return QIcon(pm)


def exe_icon(hwnd):
    """視窗所屬程式的圖示。"""
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if not h:
        return menu_icon("window")
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(len(buf))
        if not kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return menu_icon("window")
    finally:
        kernel32.CloseHandle(h)
    icon = QFileIconProvider().icon(QFileInfo(buf.value))
    return icon if not icon.isNull() else menu_icon("window")


def build_menu(app, cfg, mask, tray, on_lang, on_enable, hotkey_ok):
    """建立系統匣選單。換語言時整個選單重建一次。"""
    menu = QMenu()
    menu.setToolTipsVisible(True)

    # 開關：放在最上面。快速鍵有註冊成功才顯示在右邊。
    label = tr("enabled") + (f"\t{HOTKEY_TEXT}" if hotkey_ok else "")
    act_on = QAction(menu_icon("eye"), label, menu, checkable=True)
    act_on.setChecked(mask.enabled)
    act_on.triggered.connect(lambda checked: on_enable(checked))
    menu.addAction(act_on)
    menu.addSeparator()

    act_edit = QAction(menu)

    def refresh_edit_action():
        act_edit.setText(tr("done") if mask.edit_mode else tr("edit"))
        act_edit.setIcon(menu_icon("done" if mask.edit_mode else "move"))

    def toggle_edit():
        if mask.edit_mode:
            mask.set_edit_mode(False)
        else:
            if not mask.enabled:
                on_enable(True)  # 要調整位置，先把遮罩打開
            if not mask.set_edit_mode(True):
                tray.showMessage(tr("app"), tr("no_target"))
        refresh_edit_action()

    refresh_edit_action()
    act_edit.triggered.connect(toggle_edit)
    menu.addAction(act_edit)

    # 顯示在：全局 / 指定視窗（每次打開選單時重新列出目前的視窗）
    where_menu = menu.addMenu(menu_icon("monitor"), tr("show_on"))

    def leave_edit():
        if mask.edit_mode:
            toggle_edit()

    def choose_global():
        leave_edit()
        mask.set_global()
        tray.showMessage(tr("app"), tr("global_msg"))

    def choose_window(exe, title):
        leave_edit()
        mask.set_target(exe, title)

    def rebuild_where():
        where_menu.clear()
        a = QAction(menu_icon("globe"), tr("global"), where_menu, checkable=True)
        a.setChecked(mask.is_global)
        a.triggered.connect(choose_global)
        where_menu.addAction(a)
        where_menu.addSeparator()
        head = QAction(tr("follow"), where_menu)
        head.setEnabled(False)
        where_menu.addAction(head)
        current = {t.lower() for t in mask.targets}
        seen = False
        for hwnd, title, exe in list_windows():
            label = title if len(title) <= 40 else title[:39] + "…"
            a = QAction(exe_icon(hwnd), f"{label}　[{exe}]", where_menu, checkable=True)
            a.setToolTip(title)
            hit = (not mask.is_global and exe.lower() in current
                   and (hwnd == mask.game or not seen and not mask.game))
            a.setChecked(hit)
            seen = seen or hit
            a.triggered.connect(lambda _=False, e=exe, t=title: choose_window(e, t))
            where_menu.addAction(a)
        if not mask.is_global and not seen:
            a = QAction(menu_icon("window"), tr("not_open", name=", ".join(mask.targets)),
                        where_menu, checkable=True)
            a.setChecked(True)
            a.setEnabled(False)
            where_menu.addAction(a)

    where_menu.aboutToShow.connect(rebuild_where)
    rebuild_where()
    menu.addSeparator()

    # 遮罩樣式
    style_menu = menu.addMenu(menu_icon("style"), tr("style"))
    modes = {"soft": QAction(menu_icon("soft"), tr("soft"), style_menu, checkable=True),
             "blur": QAction(menu_icon("blur"), tr("blur"), style_menu, checkable=True),
             "solid": QAction(menu_icon("solid"), tr("solid"), style_menu, checkable=True)}

    def set_mode(mode):
        cfg["mode"] = mode
        for k, a in modes.items():
            a.setChecked(k == mode)
        save_config(cfg)
        mask.frame = None
        mask.apply_style()

    for k, a in modes.items():
        a.triggered.connect(lambda _=False, k=k: set_mode(k))
        style_menu.addAction(a)
    for k, a in modes.items():
        a.setChecked(k == cfg["mode"])

    strength_menu = menu.addMenu(menu_icon("strength"), tr("strength"))
    strengths = {"s_light": 4, "s_mid": 6, "s_heavy": 9, "s_max": 13}
    strength_acts = {}

    def set_strength(v):
        cfg["strength"] = v
        for val, a in strength_acts.items():
            a.setChecked(val == v)
        save_config(cfg)

    for key, v in strengths.items():
        a = QAction(tr(key), strength_menu, checkable=True)
        a.setChecked(cfg.get("strength", 6) == v)
        a.triggered.connect(lambda _=False, v=v: set_strength(v))
        strength_menu.addAction(a)
        strength_acts[v] = a

    corner_menu = menu.addMenu(menu_icon("corner"), tr("corner"))
    corners = {"c_none": 0.0, "c_small": 0.25, "c_mid": 0.5, "c_large": 0.75, "c_full": 1.0}
    corner_acts = {}

    def set_corner(v):
        cfg["corner"] = v
        for val, a in corner_acts.items():
            a.setChecked(val == v)
        save_config(cfg)
        mask.update_region()
        mask.update()

    for key, v in corners.items():
        a = QAction(tr(key), corner_menu, checkable=True)
        a.setChecked(abs(float(cfg.get("corner", 0)) - v) < 1e-6)
        a.triggered.connect(lambda _=False, v=v: set_corner(v))
        corner_menu.addAction(a)
        corner_acts[v] = a

    act_color = QAction(menu_icon("color"), tr("color"), menu)

    def pick_color():
        c = QColorDialog.getColor(QColor(*cfg["color"]), None, tr("color_title"))
        if c.isValid():
            cfg["color"] = [c.red(), c.green(), c.blue()]
            save_config(cfg)
            mask.update()

    act_color.triggered.connect(pick_color)
    menu.addAction(act_color)

    act_text = QAction(menu_icon("text"), tr("text"), menu)
    dialogs = []

    def edit_text():
        dlg = TextDialog(cfg, mask)
        dialogs.append(dlg)  # 留住參照，避免視窗被回收
        dlg.finished.connect(lambda _: dialogs.remove(dlg))
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    act_text.triggered.connect(edit_text)
    menu.addAction(act_text)

    act_reset = QAction(menu_icon("reset"), tr("reset"), menu)

    def reset_rect():
        if mask.is_global:
            cfg["screen_rect"] = None
        else:
            cfg["rect"] = dict(DEFAULT_CONFIG["rect"])
        save_config(cfg)

    act_reset.triggered.connect(reset_rect)
    menu.addAction(act_reset)
    menu.addSeparator()

    # 語言：選單上同時寫「語言 / Language」，切錯語言時也找得到
    lang_menu = menu.addMenu(menu_icon("language"),
                             tr("language") if _lang == "en" else f"{tr('language')} / Language")
    for code, name in LANG_NAMES.items():
        a = QAction(name, lang_menu, checkable=True)
        a.setChecked(code == _lang)
        a.triggered.connect(lambda _=False, c=code: on_lang(c))
        lang_menu.addAction(a)
    menu.addSeparator()

    act_quit = QAction(menu_icon("quit"), tr("quit"), menu)
    act_quit.triggered.connect(app.quit)
    menu.addAction(act_quit)
    return menu, toggle_edit, act_on


def log_error(text):
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(os.path.join(CONFIG_DIR, "error.log"), "a", encoding="utf-8") as f:
            f.write(text + "\n")
    except OSError:
        pass


def main():
    targets = None
    if "--target" in sys.argv:
        targets = [sys.argv[sys.argv.index("--target") + 1]]

    # 不讓 Qt 處理螢幕縮放（DPI unaware）。多螢幕、不同縮放時，所有座標交給 Windows 統一換算，
    # 否則遮罩在 100% 與 150% 螢幕之間會被 Qt 自動縮放，位置和大小會跑掉。
    os.environ["QT_QPA_PLATFORM"] = "windows:dpiawareness=0"
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName(APP_NAME)
    app.setWindowIcon(make_icon(True))  # 文字設定、顏色選擇等視窗的標題列圖示
    cfg = load_config()
    set_lang(cfg.get("lang", ""))
    mask = Mask(cfg, targets or cfg["targets"])

    tray = QSystemTrayIcon(make_icon(mask.enabled), app)
    state = {}

    # 全域快速鍵綁在遮罩視窗上；被別的程式佔用時就只用左鍵點圖示開關
    hotkey_ok = bool(user32.RegisterHotKey(mask.hwnd, HOTKEY_ID, HOTKEY_MODS, HOTKEY_VK))
    app.aboutToQuit.connect(lambda: user32.UnregisterHotKey(mask.hwnd, HOTKEY_ID))

    def refresh_tray():
        tray.setIcon(make_icon(mask.enabled))
        tray.setToolTip(tr("app") if mask.enabled else tr("paused_tip", app=tr("app")))
        if state.get("act_on") is not None:
            state["act_on"].setChecked(mask.enabled)

    def set_enabled(on):
        mask.set_enabled(on)
        refresh_tray()

    mask.on_hotkey = lambda: set_enabled(not mask.enabled)

    def install_menu():
        old = state.get("menu")
        menu, toggle, act_on = build_menu(app, cfg, mask, tray, change_lang, set_enabled, hotkey_ok)
        state.update(menu=menu, toggle=toggle, act_on=act_on)
        tray.setContextMenu(menu)
        refresh_tray()
        if old is not None:
            old.deleteLater()

    def change_lang(code):
        cfg["lang"] = code
        save_config(cfg)
        set_lang(code)
        mask.update()
        # 等目前的選單動作跑完再換掉選單
        QTimer.singleShot(0, install_menu)

    install_menu()
    # 左鍵點一下圖示 = 開／關遮罩
    tray.activated.connect(lambda reason: set_enabled(not mask.enabled)
                           if reason == QSystemTrayIcon.Trigger else None)
    tray.show()
    tray.showMessage(tr("app"), tr("started", hotkey=HOTKEY_TEXT))
    sys.exit(app.exec())


if __name__ == "__main__":
    # pythonw 沒有主控台，出錯時看不到訊息，所以把錯誤記到 %APPDATA%\ScreenMask\error.log。
    import datetime
    import traceback

    def _hook(exc_type, exc, tb):
        log_error(f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}]\n"
                  + "".join(traceback.format_exception(exc_type, exc, tb)))

    sys.excepthook = _hook
    try:
        main()
    except SystemExit:
        raise
    except BaseException:
        _hook(*sys.exc_info())
        raise
