import functools
import logging
import signal
import threading
from collections.abc import Callable

from gitorizer import git_ops, status
from gitorizer.hooks import PostChangeHook
from gitorizer.config import AppConfig, RepoConfig
from gitorizer.watcher import RepoWatcher

logger = logging.getLogger(__name__)


def _pull_once(
    config: RepoConfig,
    on_content_change: Callable[[], None] | None = None,
) -> None:
    """Pull one repo, reporting a content change when the pull moves HEAD."""
    before = git_ops.head(config.path)
    if not git_ops.pull(config.path):
        return
    after = git_ops.head(config.path)
    if before is not None and after is not None and before != after and on_content_change:
        on_content_change()


def _pull_loop(
    config: RepoConfig,
    stop_event: threading.Event,
    on_content_change: Callable[[], None] | None = None,
) -> None:
    """
    Background thread: periodically pull for one repo.
    Uses stop_event.wait(timeout) so shutdown is immediate rather than
    waiting out the full pull_interval sleep.
    """
    logger.info(
        "Pull scheduler started for %s (interval=%ds)",
        config.path,
        config.pull_interval,
    )
    while not stop_event.wait(timeout=config.pull_interval):
        _pull_once(config, on_content_change)
    logger.info("Pull scheduler stopped for %s", config.path)


def run(app_config: AppConfig) -> None:
    """Main daemon entry point. Blocks until SIGINT or SIGTERM."""
    logger.info("Gitorizer starting up...")
    status.enable(status.default_status_path(), [r.path for r in app_config.repos])

    # Verify git connectivity by fetching all repos at startup
    all_ok = True
    for repo_config in app_config.repos:
        logger.info("Verifying git connectivity for %s...", repo_config.path)
        if not git_ops.fetch(repo_config.path):
            all_ok = False

    if all_ok:
        logger.info("All repositories verified successfully.")
    else:
        logger.warning("Some repositories failed connectivity check. Continuing anyway.")

    stop_event = threading.Event()
    post_change_hook = (
        PostChangeHook(app_config.post_change_hook) if app_config.post_change_hook else None
    )

    def _signal_handler(signum, frame):
        sig_name = signal.Signals(signum).name
        logger.info("Received %s, shutting down...", sig_name)
        stop_event.set()

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    watchers: list[RepoWatcher] = []
    pull_threads: list[threading.Thread] = []

    for repo_config in app_config.repos:
        on_content_change = (
            functools.partial(post_change_hook.notify, repo_config.path)
            if post_change_hook
            else None
        )
        watcher = RepoWatcher(repo_config, stop_event, on_content_change)
        watcher.start()
        watchers.append(watcher)

        if repo_config.pull_interval > 0:
            t = threading.Thread(
                target=_pull_loop,
                args=(repo_config, stop_event, on_content_change),
                daemon=True,
                name=f"pull-{repo_config.path.name}",
            )
            t.start()
            pull_threads.append(t)

    logger.info(
        "Gitorizer running. Watching %d repo(s). Press Ctrl+C to stop.",
        len(app_config.repos),
    )

    stop_event.wait()

    logger.info("Stopping watchers...")
    for watcher in watchers:
        watcher.stop()

    logger.info("Waiting for pull threads to finish...")
    for t in pull_threads:
        t.join(timeout=5.0)

    if post_change_hook:
        logger.info("Finishing pending post-change hook runs...")
        post_change_hook.stop()

    status.disable()
    logger.info("Gitorizer stopped.")
