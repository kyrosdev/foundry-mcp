"""Non-interactive desktop cursor layer owned by the selected Studio window."""
import ctypes
from ctypes import wintypes
import sys
import time

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPolygonF
from PySide6.QtWidgets import QWidget


class CursorLayer(QWidget):
    def __init__(self, owner, user):
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowTransparentForInput | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.rows = []
        handle = int(self.winId())
        ctypes.set_last_error(0)
        if not user.SetWindowLongPtrW(handle, -8, owner) and ctypes.get_last_error():
            raise ctypes.WinError(ctypes.get_last_error())

    def paintEvent(self, event):
        painter = QPainter(self)
        paint_cursors(painter,self.rows,self.width(),self.devicePixelRatioF())


def paint_cursors(painter, rows, layer_width, ratio=1):
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setFont(QFont("Segoe UI", 9))
    used = []
    for row in rows:
        point = QPointF(row["x"]/ratio, row["y"]/ratio)
        color = QColor("#e66e4d" if "claude" in row["provider"].lower() else "#8268ef")
        painter.setPen(QColor("#fff9f6"))
        painter.setBrush(color)
        painter.drawPolygon(QPolygonF([point, point+QPointF(4,15),point+QPointF(7,8),point+QPointF(15,7)]))
        caption = row["name"] + " · " + row["control"]
        caption = painter.fontMetrics().elidedText(caption, Qt.TextElideMode.ElideRight, 204)
        width = painter.fontMetrics().horizontalAdvance(caption)+16
        x = max(4,min(point.x()+10,layer_width-width-4))
        y = point.y()+14
        box = QRectF(x,y,width,23)
        while any(box.intersects(other) for other in used):
            box.translate(0,25)
        used.append(box)
        painter.setPen(color)
        painter.setBrush(QColor("#fff9f6"))
        painter.drawRoundedRect(box,5,5)
        painter.setPen(QColor("#181b24"))
        painter.drawText(box.adjusted(8,0,-8,0),Qt.AlignmentFlag.AlignVCenter,caption)


class RibbonOverlay:
    def __init__(self, feed, parent):
        self.feed = feed
        self.layers = {}
        self.user = ctypes.WinDLL("user32",use_last_error=True) if sys.platform == "win32" else None
        if not self.user:
            return
        self.user.SetWindowLongPtrW.argtypes = (wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t)
        self.user.SetWindowLongPtrW.restype = ctypes.c_ssize_t
        self.user.GetWindowRect.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.RECT))
        self.user.IsIconic.argtypes = (wintypes.HWND,)
        self.user.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
        self.user.SetWindowPos.argtypes = (wintypes.HWND,wintypes.HWND,ctypes.c_int,ctypes.c_int,
                                           ctypes.c_int,ctypes.c_int,wintypes.UINT)
        self.timer = QTimer(parent,interval=120)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()

    def refresh(self):
        windows = {}
        now = time.time()
        if not self.feed.cursor_preference.exists():
            for session in self.feed.latest[0]:
                if not session["connected"]:
                    continue
                agents = {item["id"]:item["name"] for item in session["agents"] if item["active"]}
                seen = set()
                for row in session["history"]:
                    actor = row["actor"]
                    if actor in seen or actor not in agents:
                        continue
                    seen.add(actor)
                    hit = row.get("ribbon_cursor")
                    if hit and row["success"] and now-row["finished"] < 5:
                        windows.setdefault(hit["window"],[]).append({
                            **hit,"name":agents[actor],"provider":agents.get(session["connection"],""),
                            "control":hit["name"],
                        })
        for handle in self.layers.keys()-windows.keys():
            self.layers.pop(handle).close()
        for handle, rows in windows.items():
            bounds = wintypes.RECT()
            pid = wintypes.DWORD()
            self.user.GetWindowThreadProcessId(handle,ctypes.byref(pid))
            rows = [row for row in rows if row.get("pid") == pid.value]
            if not rows or self.user.IsIconic(handle) or not self.user.GetWindowRect(handle,ctypes.byref(bounds)):
                if handle in self.layers:
                    self.layers[handle].hide()
                continue
            layer = self.layers.get(handle)
            if layer is None:
                try:
                    layer = self.layers[handle] = CursorLayer(handle,self.user)
                except OSError:
                    continue
            layer.rows = rows[:16]
            # Native coordinates keep the overlay aligned with the HWND at the current DPI.
            self.user.SetWindowPos(int(layer.winId()),None,bounds.left,bounds.top,
                                   bounds.right-bounds.left,min(680,bounds.bottom-bounds.top),0x0014)
            layer.show()
            layer.update()
