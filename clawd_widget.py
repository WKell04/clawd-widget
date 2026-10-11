#!/usr/bin/env python3
"""Clawd desktop widget: walks in, asks what to start, then poofs and exits."""
import os
import shutil
import sys
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, QUrl, QProcess
from PySide6.QtGui import (QColor, QDesktopServices, QFont, QPainter, QPainterPath, QPen,
                           QPixmap, QRegion)
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QVBoxLayout, QWidget

ASSETS = Path(__file__).resolve().parent / "assets"
FPS = 24
TIMEOUT_MS = 10_000
# folder Claude Code starts in (set by install.sh --code-dir)
CODE_DIR = Path(os.environ.get("CLAWD_CODE_DIR", Path.home())).expanduser()
DESKTOP_CMD = shutil.which("claude-desktop")
# private per-user socket (XDG_RUNTIME_DIR is only accessible by the user), not /tmp
_RUNTIME = os.environ.get("XDG_RUNTIME_DIR")
SOCKET_NAME = os.path.join(_RUNTIME, "clawd-widget.sock") if _RUNTIME else "clawd-widget"

# Sprite geometry in source pixels (measured from the rendered frames)
FEET_Y, CENTER_X, HEAD_Y = 246, 132, 80

SPRITE_DPR = 1.1  # 288 px sprites -> ~262 logical px
WIN_W, WIN_H = 560, 440
WALK_IN = 43  # how far Clawd stops left of the right edge (1 cm ~ 51 px here)
ORANGE = QColor("#E8833A")
WALK_FRAMES = 48  # three walk cycles while moving in (multiple of 16)


def load(name):
    frames = [QPixmap(str(p)) for p in sorted((ASSETS / name).glob("*.png"))]
    if not frames:
        sys.exit(f"missing sprites in {ASSETS / name}")
    return frames


class Bubble(QWidget):
    """Speech bubble with a tail pointing down-right towards Clawd."""
    TAIL = 18

    def __init__(self, parent, on_pick):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 16 + self.TAIL)
        lay.setSpacing(8)
        title = QLabel("Womit soll ich starten?")
        f = QFont(); f.setPointSize(13); f.setBold(True)
        title.setFont(f)
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("color:#1a1a1a;")
        lay.addWidget(title)
        desktop_text = "Claude Desktop" if DESKTOP_CMD else "Claude Desktop – nicht installiert"
        for key, text, enabled in (("code", "Claude Code", True),
                                   ("desktop", desktop_text, bool(DESKTOP_CMD)),
                                   ("browser", "Claude im Browser", True)):
            b = QPushButton(text)
            b.setEnabled(enabled)
            b.setCursor(Qt.PointingHandCursor if enabled else Qt.ArrowCursor)
            b.setFocusPolicy(Qt.NoFocus)
            b.clicked.connect(lambda _=False, k=key: on_pick(k))
            lay.addWidget(b)
        self.setStyleSheet("""
            QPushButton { background:#f3f3f3; color:#1a1a1a; border:2px solid #E8833A;
                          border-radius:15px; padding:6px 14px; font-size:13px; }
            QPushButton:hover { background:#E8833A; color:white; }
            QPushButton:disabled { color:#9a9a9a; border-color:#e3c3a8; background:#f6f6f6; }
        """)
        self.adjustSize()
        self.setFixedSize(300, self.sizeHint().height())

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(2, 2, -2, -2 - self.TAIL)
        path = QPainterPath()
        path.addRoundedRect(r, 22, 22)
        tail = QPainterPath()
        tx = r.right() - 60
        tail.moveTo(tx, r.bottom() - 2)
        tail.lineTo(tx + 32, r.bottom() - 2)
        tail.lineTo(tx + 34, r.bottom() + self.TAIL)
        tail.closeSubpath()
        path = path.united(tail)
        p.setPen(QPen(ORANGE, 3))
        p.setBrush(QColor("#fafafa"))
        p.drawPath(path)


class Clawd(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Clawd")
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(WIN_W, WIN_H)

        self.anim = {n: load(n) for n in ("walk", "turn", "idle", "poof")}
        # fixed logical size; Qt's reported ratio is unreliable with fractional scaling
        dpr = SPRITE_DPR
        for frames in self.anim.values():
            for pm in frames:
                pm.setDevicePixelRatio(dpr)
        self.s = 1 / dpr  # source px -> logical px
        size = self.anim["idle"][0].width() * self.s

        # final sprite position: WALK_IN px left of the window's right edge
        self.end_x = WIN_W - size - WALK_IN
        self.sy = WIN_H - 14 - FEET_Y * self.s
        self.start_x = WIN_W
        self.sx = self.start_x
        self.sprite_size = size

        self.bubble = Bubble(self, self.pick)
        # tail tip (bubble width - 28) points at the left side of Clawd's face
        cx = self.end_x + CENTER_X * self.s
        tail_target = cx - 35
        self.bubble.move(int(tail_target - (self.bubble.width() - 28)),
                         int(self.sy + HEAD_Y * self.s - self.bubble.height()))
        self.bubble.hide()
        self.bubble.installEventFilter(self)

        self.state, self.tick_n, self.shadow = "walk", 0, 1.0
        self.timer = QTimer(self, timeout=self.tick)
        self.timer.start(1000 // FPS)
        self.idle_timeout = QTimer(self, singleShot=True, timeout=self.start_poof)

    # --- animation state machine ---
    def frame(self):
        frames = self.anim[self.state]
        i = self.tick_n % len(frames) if self.state in ("walk", "idle") else min(self.tick_n, len(frames) - 1)
        return frames[i]

    def tick(self):
        self.tick_n += 1
        if self.state == "walk":
            t = min(self.tick_n / WALK_FRAMES, 1.0)
            ease = 1 - (1 - t) ** 2
            self.sx = self.start_x + (self.end_x - self.start_x) * ease
            if self.tick_n >= WALK_FRAMES:
                self.set_state("turn")
        elif self.state == "turn" and self.tick_n >= len(self.anim["turn"]) - 1:
            self.set_state("idle")
            self.show_bubble()
        elif self.state == "poof":
            self.shadow = max(0.0, 1 - self.tick_n / 6)
            if self.tick_n >= len(self.anim["poof"]):
                QApplication.quit()
        self.update()

    def set_state(self, s):
        self.state, self.tick_n = s, 0

    def show_bubble(self):
        self.bubble.show()
        # only Clawd and the bubble take clicks; the rest stays click-through
        sprite = QRegion(int(self.end_x), int(self.sy), int(self.sprite_size), int(self.sprite_size))
        self.setMask(sprite.united(QRegion(self.bubble.geometry())))
        self.idle_timeout.start(TIMEOUT_MS)

    def start_poof(self):
        if self.state == "poof":
            return
        self.idle_timeout.stop()
        self.bubble.hide()
        self.set_state("poof")

    # --- actions ---
    def pick(self, key):
        if key == "code":
            CODE_DIR.mkdir(parents=True, exist_ok=True)
            # login shell so ~/.local/bin is on PATH; keep the shell open after claude exits
            shell = os.environ.get("SHELL", "/bin/bash")
            QProcess.startDetached("konsole", ["--workdir", str(CODE_DIR), "-e",
                                               shell, "-lc", f"claude; exec {shell}"])
        elif key == "desktop" and DESKTOP_CMD:
            QProcess.startDetached(DESKTOP_CMD, [])
        elif key == "browser":
            QDesktopServices.openUrl(QUrl("https://claude.ai"))
        self.start_poof()

    # --- events ---
    def eventFilter(self, obj, ev):
        # pause the auto-poof while the mouse is over the bubble
        if obj is self.bubble and self.state == "idle":
            if ev.type() == ev.Type.Enter:
                self.idle_timeout.stop()
            elif ev.type() == ev.Type.Leave:
                self.idle_timeout.start(TIMEOUT_MS)
        return False

    def keyPressEvent(self, ev):
        if ev.key() == Qt.Key_Escape:
            self.start_poof()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        if self.shadow > 0:
            cx = self.sx + CENTER_X * self.s
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(0, 0, 0, int(70 * self.shadow)))
            p.drawEllipse(QPointF(cx, self.sy + (FEET_Y - 2) * self.s), 62 * self.s, 10 * self.s)
        p.drawPixmap(QPointF(self.sx, self.sy), self.frame())


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("clawd-widget")
    app.setDesktopFileName("clawd-widget")

    # second key press: tell the running Clawd to poof, then exit
    sock = QLocalSocket()
    sock.connectToServer(SOCKET_NAME)
    if sock.waitForConnected(200):
        sock.write(b"poof")
        sock.waitForBytesWritten(200)
        return

    w = Clawd()
    QLocalServer.removeServer(SOCKET_NAME)  # clean up a stale socket after a crash
    server = QLocalServer(w)
    server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
    server.listen(SOCKET_NAME)
    server.newConnection.connect(lambda: (server.nextPendingConnection(), w.start_poof()))

    w.show()
    w.activateWindow()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
