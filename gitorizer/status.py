"""
Per-repository sync status, shared with the tray UI through a JSON file.

The daemon calls enable() at startup; until then record() is a no-op, so
git_ops stays usable (and testable) without a status file.
"""

import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

# Operations that talk to (or produce something for) the remote and so count
# as a "sync" when they succeed.
SYNC_OPS = ("commit", "push", "pull", "fetch")


def default_status_path() -> Path:
    xdg = os.environ.get("XDG_STATE_HOME")
    base = Path(xdg) if xdg else Path.home() / ".local" / "state"
    return base / "gitorizer" / "status.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def summarize_error(stderr: str, stdout: str = "", returncode: int | None = None) -> str:
    """Pick the most useful line of git output for display."""
    lines = [l.strip() for l in (stderr or stdout).splitlines() if l.strip()]
    for prefix in ("fatal:", "error:", "! [rejected]", "CONFLICT"):
        for line in lines:
            if line.startswith(prefix):
                return line
    if lines:
        return lines[-1]
    return f"exit code {returncode}" if returncode is not None else "unknown error"


class StatusStore:
    def __init__(self, path: Path, repos: list[Path]) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._state: dict = {
            "pid": os.getpid(),
            "running": True,
            "started": _now(),
            "repos": {
                str(repo): {"last_sync": None, "last_sync_op": None, "errors": {}}
                for repo in repos
            },
        }
        self._write()

    def record(self, repo_path: Path, op: str, ok: bool, error: str | None = None) -> None:
        with self._lock:
            repo = self._state["repos"].setdefault(
                str(repo_path), {"last_sync": None, "last_sync_op": None, "errors": {}}
            )
            if ok:
                repo["errors"].pop(op, None)
                if op in SYNC_OPS:
                    repo["last_sync"] = _now()
                    repo["last_sync_op"] = op
            else:
                repo["errors"][op] = {"message": error or "unknown error", "time": _now()}
            self._write()

    def close(self) -> None:
        with self._lock:
            self._state["running"] = False
            self._write()

    def _write(self) -> None:
        self._state["updated"] = _now()
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._state, indent=2))
            os.replace(tmp, self._path)
        except OSError as e:
            logger.warning("Could not write status file %s: %s", self._path, e)


_store: StatusStore | None = None


def enable(path: Path, repos: list[Path]) -> StatusStore:
    global _store
    _store = StatusStore(path, repos)
    return _store


def disable() -> None:
    global _store
    if _store is not None:
        _store.close()
    _store = None


def record(repo_path: Path, op: str, ok: bool, error: str | None = None) -> None:
    if _store is not None:
        _store.record(repo_path, op, ok, error)
