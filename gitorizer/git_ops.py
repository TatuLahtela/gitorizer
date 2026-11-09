import logging
import subprocess
from datetime import datetime
from pathlib import Path

from gitorizer import status

logger = logging.getLogger(__name__)


def _run(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True)


def _log_git_failure(
    result: subprocess.CompletedProcess, repo_path: Path, op: str | None = None
) -> None:
    """Log detailed information about a failed git command and record it in the status."""
    cmd_str = " ".join(result.args)
    details = [f"command='{cmd_str}'", f"returncode={result.returncode}"]
    if result.stderr.strip():
        details.append(f"stderr='{result.stderr.strip()}'")
    if result.stdout.strip():
        details.append(f"stdout='{result.stdout.strip()}'")
    logger.error("Git command failed in %s: %s", repo_path, ", ".join(details))
    if op:
        status.record(
            repo_path, op, False,
            status.summarize_error(result.stderr, result.stdout, result.returncode),
        )


def get_changed_files(repo_path: Path) -> list[str]:
    """Stage all changes and return list of changed filenames. Empty list means nothing to commit."""
    add_result = _run(["git", "add", "-A"], cwd=repo_path)
    if add_result.returncode != 0:
        _log_git_failure(add_result, repo_path, "commit")
        return []

    status_result = _run(["git", "status", "--porcelain"], cwd=repo_path)
    if status_result.returncode != 0:
        _log_git_failure(status_result, repo_path, "commit")
        return []

    changed: list[str] = []
    for line in status_result.stdout.splitlines():
        if line.strip():
            # porcelain format: "XY filename" — filename starts at column 3
            changed.append(line[3:].strip())
    return changed


def commit(repo_path: Path, changed_files: list[str]) -> bool:
    """Commit staged changes with an auto-generated message. Returns True on success."""
    timestamp = datetime.now().strftime("%Y-%m-%dT%H:%M")
    files_str = ", ".join(changed_files[:10])
    if len(changed_files) > 10:
        files_str += f", ... (+{len(changed_files) - 10} more)"
    message = f"gitorizer: auto-commit {timestamp} [{files_str}]"

    result = _run(["git", "commit", "-m", message], cwd=repo_path)
    if result.returncode != 0:
        _log_git_failure(result, repo_path, "commit")
        return False

    logger.info("Committed in %s: %s", repo_path, message)
    status.record(repo_path, "commit", True)
    return True


def push(repo_path: Path) -> bool:
    """Push to the configured remote. Returns True on success."""
    result = _run(["git", "push"], cwd=repo_path)
    if result.returncode != 0:
        _log_git_failure(result, repo_path, "push")
        return False
    logger.info("Pushed %s", repo_path)
    status.record(repo_path, "push", True)
    return True


def pull(repo_path: Path) -> bool:
    """Pull with rebase. Logs errors but does not raise. Returns True on success."""
    result = _run(["git", "pull", "--rebase"], cwd=repo_path)
    if result.returncode != 0:
        _log_git_failure(result, repo_path, "pull")
        return False
    logger.info("Pulled %s", repo_path)
    status.record(repo_path, "pull", True)
    return True


def head(repo_path: Path) -> str | None:
    """Return the current HEAD object id, or None if it cannot be read."""
    result = _run(["git", "rev-parse", "HEAD"], cwd=repo_path)
    if result.returncode != 0:
        _log_git_failure(result, repo_path)
        return None
    return result.stdout.strip()


def fetch(repo_path: Path) -> bool:
    """Fetch from remote. Returns True on success."""
    result = _run(["git", "fetch"], cwd=repo_path)
    if result.returncode != 0:
        _log_git_failure(result, repo_path, "fetch")
        return False
    status.record(repo_path, "fetch", True)
    return True
