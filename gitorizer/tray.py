"""
System tray indicator for gitorizer.

Reads the status file written by the daemon and shows one menu entry per
watched repository with its last successful sync and any current errors.
Runs as a separate process so the daemon itself stays headless.
"""

import argparse
import json
import os
import signal
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from gitorizer.status import default_status_path

POLL_MS = 2000
MAX_ERROR_CHARS = 100


@dataclass(frozen=True)
class RepoError:
    op: str
    message: str
    time: datetime | None


@dataclass(frozen=True)
class RepoEntry:
    path: Path
    label: str
    last_sync: datetime | None
    last_sync_op: str | None
    errors: tuple[RepoError, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class TrayModel:
    daemon_running: bool
    repos: tuple[RepoEntry, ...]

    @property
    def has_errors(self) -> bool:
        return any(r.errors for r in self.repos)


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _pid_alive(pid: object) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _display_path(path: Path) -> str:
    try:
        return "~/" + str(path.relative_to(Path.home()))
    except ValueError:
        return str(path)


def build_model(state: dict | None) -> TrayModel:
    """Turn the raw status JSON into a display model."""
    if not state:
        return TrayModel(daemon_running=False, repos=())

    running = bool(state.get("running")) and _pid_alive(state.get("pid"))
    repos: list[RepoEntry] = []
    for path_str, info in sorted(state.get("repos", {}).items()):
        path = Path(path_str)
        errors = tuple(
            RepoError(op, err.get("message", ""), _parse_time(err.get("time")))
            for op, err in sorted(info.get("errors", {}).items())
        )
        repos.append(RepoEntry(
            path=path,
            label=_display_path(path),
            last_sync=_parse_time(info.get("last_sync")),
            last_sync_op=info.get("last_sync_op"),
            errors=errors,
        ))
    return TrayModel(daemon_running=running, repos=tuple(repos))


def format_when(when: datetime | None, now: datetime) -> str:
    """'14:32 (5 min ago)', or a date for anything older than a day."""
    if when is None:
        return "never"
    local = when.astimezone()
    seconds = max(0, int((now - when).total_seconds()))
    if seconds < 60:
        ago = "just now"
    elif seconds < 3600:
        ago = f"{seconds // 60} min ago"
    elif seconds < 86400:
        ago = f"{seconds // 3600} h ago"
    else:
        return local.strftime("%Y-%m-%d %H:%M")
    return f"{local.strftime('%H:%M')} ({ago})"


def repo_text(repo: RepoEntry, now: datetime) -> str:
    return f"{repo.label} — last sync {format_when(repo.last_sync, now)}"


def error_text(err: RepoError, now: datetime) -> str:
    msg = err.message.replace("\n", " ")
    if len(msg) > MAX_ERROR_CHARS:
        msg = msg[: MAX_ERROR_CHARS - 1] + "…"
    when = f" at {format_when(err.time, now)}" if err.time else ""
    return f"    {err.op} failed{when}: {msg}"


def tooltip_text(model: TrayModel) -> str:
    if not model.daemon_running:
        return "Gitorizer: daemon not running"
    failing = sum(1 for r in model.repos if r.errors)
    if failing:
        return f"Gitorizer: {failing} of {len(model.repos)} repositories have errors"
    return f"Gitorizer: {len(model.repos)} repositories in sync"


def _load_state(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Qt UI
# ---------------------------------------------------------------------------

def _run_tray(status_path: Path) -> int:
    from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, QUrl
    from PySide6.QtGui import (
        QAction, QColor, QDesktopServices, QGuiApplication, QIcon, QPainter, QPen, QPixmap,
    )
    from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

    app = QApplication(sys.argv)
    app.setApplicationName("gitorizer")
    app.setApplicationDisplayName("Gitorizer")
    app.setQuitOnLastWindowClosed(False)

    def make_icon(color: str) -> QIcon:
        """A coloured disc with a white 'commit' glyph (line through a ring)."""
        icon = QIcon()
        for size in (16, 22, 24, 32, 48, 64):
            pix = QPixmap(size, size)
            pix.fill(Qt.GlobalColor.transparent)
            p = QPainter(pix)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(color))
            m = size * 0.04
            p.drawEllipse(QRectF(m, m, size - 2 * m, size - 2 * m))
            pen = QPen(QColor("white"))
            pen.setWidthF(max(1.5, size * 0.09))
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            c = size / 2
            r = size * 0.16
            p.drawLine(QPointF(size * 0.16, c), QPointF(c - r, c))
            p.drawLine(QPointF(c + r, c), QPointF(size * 0.84, c))
            p.drawEllipse(QPointF(c, c), r, r)
            p.end()
            icon.addPixmap(pix)
        return icon

    icons = {
        "ok": make_icon("#2e9d4f"),
        "error": make_icon("#d93a3a"),
        "stopped": make_icon("#8a8f98"),
    }

    tray = QSystemTrayIcon(icons["stopped"])
    menu = QMenu()
    menu.setToolTipsVisible(True)
    tray.setContextMenu(menu)

    last_rendered: list[object] = [None]

    def open_folder(path: Path) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def copy_text(text: str) -> None:
        QGuiApplication.clipboard().setText(text)

    def render(force: bool = False) -> None:
        model = build_model(_load_state(status_path))
        now = datetime.now(timezone.utc)

        # Only rebuild the menu when something visible changed; rebuilding
        # an open menu every poll makes it flicker.
        signature = (
            model.daemon_running,
            tuple(
                (repo_text(r, now), tuple(error_text(e, now) for e in r.errors))
                for r in model.repos
            ),
        )
        if not force and signature == last_rendered[0]:
            return
        last_rendered[0] = signature

        if not model.daemon_running:
            tray.setIcon(icons["stopped"])
        elif model.has_errors:
            tray.setIcon(icons["error"])
        else:
            tray.setIcon(icons["ok"])
        tray.setToolTip(tooltip_text(model))

        menu.clear()
        header = QAction(
            "Gitorizer" if model.daemon_running else "Gitorizer — daemon not running", menu
        )
        header.setEnabled(False)
        menu.addAction(header)
        menu.addSeparator()

        if not model.repos:
            empty = QAction(f"No status yet ({status_path})", menu)
            empty.setEnabled(False)
            menu.addAction(empty)

        for repo in model.repos:
            action = QAction(
                icons["error" if repo.errors else "ok"], repo_text(repo, now), menu
            )
            tip = str(repo.path)
            if repo.last_sync_op:
                tip += f"\nLast successful operation: {repo.last_sync_op}"
            action.setToolTip(tip + "\nClick to open folder")
            action.triggered.connect(lambda _=False, p=repo.path: open_folder(p))
            menu.addAction(action)

            for err in repo.errors:
                err_action = QAction(error_text(err, now), menu)
                err_action.setToolTip(err.message + "\n\nClick to copy")
                err_action.triggered.connect(
                    lambda _=False, m=err.message: copy_text(m)
                )
                menu.addAction(err_action)

        menu.addSeparator()
        quit_action = QAction("Quit tray", menu)
        quit_action.triggered.connect(app.quit)
        menu.addAction(quit_action)

    menu.aboutToShow.connect(render)
    timer = QTimer()
    timer.timeout.connect(render)
    timer.start(POLL_MS)

    def show_when_available() -> None:
        if QSystemTrayIcon.isSystemTrayAvailable():
            render(force=True)
            tray.show()
        else:
            QTimer.singleShot(2000, show_when_available)

    show_when_available()

    # Let Ctrl+C / SIGTERM stop the Qt event loop.
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    signal.signal(signal.SIGTERM, signal.SIG_DFL)
    return app.exec()


def main() -> None:
    default_status = default_status_path()
    parser = argparse.ArgumentParser(
        prog="gitorizer-tray",
        description="System tray indicator showing gitorizer sync status.",
    )
    parser.add_argument(
        "--status-file",
        type=Path,
        default=default_status,
        help=f"Status file written by the daemon (default: {default_status})",
    )
    args = parser.parse_args()

    try:
        import PySide6  # noqa: F401
    except ImportError:
        print(
            "Error: gitorizer-tray needs PySide6. Install with the 'tray' extra, e.g.\n"
            "  uv tool install --force './gitorizer[tray]'",
            file=sys.stderr,
        )
        sys.exit(1)

    sys.exit(_run_tray(args.status_file))


if __name__ == "__main__":
    main()
