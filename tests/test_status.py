import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from gitorizer import status
from gitorizer.tray import (
    build_model, error_text, failure_keys, format_when, new_failures,
    notification_text, tooltip_text,
)


class StatusStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "status.json"
        self.repo = Path("/notes")
        status.enable(self.path, [self.repo])

    def tearDown(self) -> None:
        status.disable()
        self.tmp.cleanup()

    def state(self) -> dict:
        return json.loads(self.path.read_text())

    def test_initial_state_lists_repos(self) -> None:
        state = self.state()
        self.assertTrue(state["running"])
        self.assertEqual(state["pid"], os.getpid())
        self.assertIsNone(state["repos"]["/notes"]["last_sync"])

    def test_success_sets_last_sync(self) -> None:
        status.record(self.repo, "pull", True)
        repo = self.state()["repos"]["/notes"]
        self.assertIsNotNone(repo["last_sync"])
        self.assertEqual(repo["last_sync_op"], "pull")

    def test_failure_is_cleared_by_success_of_same_op(self) -> None:
        status.record(self.repo, "push", False, "fatal: no remote")
        status.record(self.repo, "pull", True)
        self.assertIn("push", self.state()["repos"]["/notes"]["errors"])
        status.record(self.repo, "push", True)
        self.assertEqual(self.state()["repos"]["/notes"]["errors"], {})

    def test_disable_marks_not_running(self) -> None:
        status.disable()
        self.assertFalse(self.state()["running"])


class SummarizeErrorTest(unittest.TestCase):
    def test_prefers_fatal_line(self) -> None:
        stderr = "hint: something\nfatal: could not read from remote\nhint: more"
        self.assertEqual(status.summarize_error(stderr), "fatal: could not read from remote")

    def test_falls_back_to_exit_code(self) -> None:
        self.assertEqual(status.summarize_error("", "", 128), "exit code 128")


class TrayModelTest(unittest.TestCase):
    def test_missing_state_means_stopped(self) -> None:
        model = build_model(None)
        self.assertFalse(model.daemon_running)
        self.assertEqual(tooltip_text(model), "Gitorizer: daemon not running")

    def test_errors_and_liveness(self) -> None:
        model = build_model({
            "pid": os.getpid(),
            "running": True,
            "repos": {
                "/b": {"last_sync": None, "errors": {}},
                "/a": {
                    "last_sync": "2026-11-09T10:00:00+00:00",
                    "last_sync_op": "pull",
                    "errors": {"push": {"message": "fatal: denied", "time": None}},
                },
            },
        })
        self.assertTrue(model.daemon_running)
        self.assertTrue(model.has_errors)
        self.assertEqual([r.label for r in model.repos], ["/a", "/b"])
        self.assertEqual(model.repos[0].errors[0].op, "push")
        self.assertIn("1 of 2", tooltip_text(model))

    def test_dead_pid_means_stopped(self) -> None:
        model = build_model({"pid": 2**22 + 12345, "running": True, "repos": {}})
        self.assertFalse(model.daemon_running)

    def test_format_when(self) -> None:
        now = datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc)
        self.assertEqual(format_when(None, now), "never")
        self.assertTrue(format_when(now - timedelta(minutes=5), now).endswith("(5 min ago)"))
        self.assertTrue(format_when(now - timedelta(seconds=10), now).endswith("(just now)"))
        self.assertRegex(format_when(now - timedelta(days=3), now), r"^\d{4}-\d{2}-\d{2} ")

    def test_error_text_truncates(self) -> None:
        from gitorizer.tray import RepoError
        text = error_text(RepoError("push", "x" * 500, None), datetime.now(timezone.utc))
        self.assertLess(len(text), 130)
        self.assertTrue(text.endswith("…"))


def _state(errors: dict[str, dict[str, str]], running: bool = True) -> dict:
    return {
        "pid": os.getpid(),
        "running": running,
        "repos": {
            path: {
                "last_sync": None,
                "errors": {op: {"message": msg, "time": None} for op, msg in errs.items()},
            }
            for path, errs in errors.items()
        },
    }


class NotificationTest(unittest.TestCase):
    def test_startup_failures_are_new(self) -> None:
        model = build_model(_state({"/a": {"pull": "fatal: denied"}}))
        fresh = new_failures(frozenset(), model)
        self.assertEqual([(r.label, e.op) for r, e in fresh], [("/a", "pull")])

    def test_repeated_failure_is_reported_once(self) -> None:
        model = build_model(_state({"/a": {"pull": "fatal: denied"}}))
        known = failure_keys(model)
        again = build_model(_state({"/a": {"pull": "fatal: denied again"}}))
        self.assertEqual(new_failures(known, again), [])

    def test_failure_after_recovery_is_reported_again(self) -> None:
        failing = build_model(_state({"/a": {"pull": "fatal: denied"}}))
        recovered = build_model(_state({"/a": {}}))
        known = failure_keys(recovered)
        self.assertEqual(len(new_failures(known, failing)), 1)

    def test_only_the_new_op_is_reported(self) -> None:
        known = failure_keys(build_model(_state({"/a": {"pull": "x"}})))
        model = build_model(_state({"/a": {"pull": "x", "push": "y"}}))
        self.assertEqual([e.op for _, e in new_failures(known, model)], ["push"])

    def test_stopped_daemon_does_not_notify(self) -> None:
        model = build_model(_state({"/a": {"pull": "x"}}, running=False))
        self.assertEqual(new_failures(frozenset(), model), [])

    def test_notification_text(self) -> None:
        one = build_model(_state({"/a": {"pull": "fatal: denied"}}))
        title, body = notification_text(new_failures(frozenset(), one))
        self.assertEqual(title, "Gitorizer: pull failed in /a")
        self.assertEqual(body, "fatal: denied")

        same_repo = build_model(_state({"/a": {"pull": "p", "push": "q"}}))
        title, body = notification_text(new_failures(frozenset(), same_repo))
        self.assertEqual(title, "Gitorizer: 2 operations failed in /a")
        self.assertEqual(body, "pull: p\npush: q")

        two_repos = build_model(_state({"/a": {"pull": "p"}, "/b": {"push": "q"}}))
        title, body = notification_text(new_failures(frozenset(), two_repos))
        self.assertEqual(title, "Gitorizer: 2 operations failed")
        self.assertIn("/b: push — q", body)


if __name__ == "__main__":
    unittest.main()
