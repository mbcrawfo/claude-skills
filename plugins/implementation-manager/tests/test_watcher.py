"""Tests for the run watcher: push requests and queue turns."""

from __future__ import annotations

import fcntl
from pathlib import Path
import tempfile
import unittest

from fakes import FakeClock, FakeGh
import helpers  # noqa: F401  (puts the scripts dir on sys.path)
from runstate import LOCK_FILE, add_unit, enqueue, go, init_run, load, locked, record_merged, yield_slot
from watcher import next_event, take_request, wait

NOW = 1000.0
SHA1 = "1" * 40
SHA2 = "2" * 40
SHA3 = "3" * 40


class Timeout(Exception):
    """Raised by StopClock once a wait has slept its fill without an event."""


class StopClock(FakeClock):
    """A FakeClock whose sleep raises Timeout after `limit` sleeps and can run a hook after each one."""

    def __init__(self, limit=3, hook=None):
        super().__init__()
        self.limit = limit
        self.hook = hook

    def sleep(self, seconds):
        if len(self.sleeps) >= self.limit:
            raise Timeout
        super().sleep(seconds)
        if self.hook:
            self.hook(len(self.sleeps))


def append(run_dir: Path, text: str) -> None:
    with open(run_dir / "requests.log", "a", encoding="utf-8") as f:
        f.write(text)


def append_bytes(run_dir: Path, data: bytes) -> None:
    with open(run_dir / "requests.log", "ab") as f:
        f.write(data)


def assert_unlocked(run_dir: Path) -> None:
    """Fail at once, rather than deadlock, if the run lock is held while the watcher sleeps."""
    with open(run_dir / LOCK_FILE, "a") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise AssertionError("wait held the run lock across a sleep") from None
        fcntl.flock(f, fcntl.LOCK_UN)


def run_wait(run_dir: Path, clock: StopClock | None = None) -> str:
    clock = clock or StopClock()
    return wait(run_dir, gh=FakeGh([], []), sleep=clock.sleep, now=clock.now)


class WatcherTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name) / "run"
        init_run(self.dir, repo="o/r", base="main", method="merge", wip=None, queue_mode="serial", now=NOW)
        with locked(self.dir) as state:
            for name in ("a", "b", "c"):
                add_unit(state, name, prereqs=[], branch=None, worktree=None, specs=False)


class RequestTests(WatcherTestCase):
    def test_request_reported_and_pending(self):
        append(self.dir, f"a push {SHA1} feat/a\n")
        self.assertEqual(run_wait(self.dir), f"REQUEST a {SHA1} feat/a")
        pending = load(self.dir)["pending"]
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["sha"], SHA1)
        self.assertEqual(pending[0]["unit"], "a")
        self.assertEqual(pending[0]["branch"], "feat/a")

    def test_two_waiters_report_a_request_once(self):
        append(self.dir, f"a push {SHA1} feat/a\n")
        self.assertEqual(run_wait(self.dir), f"REQUEST a {SHA1} feat/a")
        with self.assertRaises(Timeout):
            run_wait(self.dir, StopClock(limit=3))

    def test_one_request_per_wait(self):
        append(self.dir, f"a push {SHA1} feat/a\nb push {SHA2} feat/b\n")
        self.assertEqual(run_wait(self.dir), f"REQUEST a {SHA1} feat/a")
        self.assertEqual(run_wait(self.dir), f"REQUEST b {SHA2} feat/b")

    def test_newer_request_replaces_pending_entry(self):
        append(self.dir, f"a push {SHA1} feat/a\na push {SHA2} feat/a\n")
        run_wait(self.dir)
        run_wait(self.dir)
        pending = load(self.dir)["pending"]
        self.assertEqual([(p["unit"], p["sha"]) for p in pending], [("a", SHA2)])

    def test_partial_request_line_waits_for_newline(self):
        append(self.dir, f"a push {SHA1} feat/a")
        with locked(self.dir) as state:
            self.assertIsNone(take_request(self.dir, state, NOW))
            self.assertEqual(state["requests_offset"], 0)
            self.assertEqual(state["bad_requests"], [])
        append(self.dir, "\n")
        self.assertEqual(run_wait(self.dir), f"REQUEST a {SHA1} feat/a")

    def test_bad_request_lines_are_skipped_and_listed(self):
        bad = ["a push abc feat/a", f"zz push {SHA1} feat/z", f"a push {SHA1} b extra"]
        append(self.dir, "".join(line + "\n" for line in bad) + f"a push {SHA2} feat/a\n")
        self.assertEqual(run_wait(self.dir), f"REQUEST a {SHA2} feat/a")
        self.assertEqual(load(self.dir)["bad_requests"], bad)

    def test_undecodable_branch_is_a_bad_request(self):
        append_bytes(self.dir, f"a push {SHA1} feat/".encode() + b"\xffx\n" + f"b push {SHA2} feat/b\n".encode())
        self.assertEqual(run_wait(self.dir), f"REQUEST b {SHA2} feat/b")
        self.assertEqual(load(self.dir)["bad_requests"], [f"a push {SHA1} feat/�x"])
        self.assertEqual([p["unit"] for p in load(self.dir)["pending"]], ["b"])

    def test_undecodable_unit_is_a_bad_request(self):
        append_bytes(self.dir, b"\xfe push " + SHA1.encode() + b" feat/a\n" + f"b push {SHA2} feat/b\n".encode())
        self.assertEqual(run_wait(self.dir), f"REQUEST b {SHA2} feat/b")
        self.assertEqual(load(self.dir)["bad_requests"], [f"� push {SHA1} feat/a"])

    def test_control_character_in_branch_is_a_bad_request(self):
        append(self.dir, f"a push {SHA1} feat/\x00x\na push {SHA2} feat/\x7fy\nb push {SHA2} feat/b\n")
        self.assertEqual(run_wait(self.dir), f"REQUEST b {SHA2} feat/b")
        self.assertEqual(load(self.dir)["bad_requests"], [f"a push {SHA1} feat/\x00x", f"a push {SHA2} feat/\x7fy"])

    def test_carriage_return_inside_a_line_does_not_stall(self):
        append(self.dir, f"a push {SHA1}\rfeat/a\nb push {SHA2} feat/b\n")
        self.assertEqual(run_wait(self.dir), f"REQUEST b {SHA2} feat/b")
        self.assertEqual(load(self.dir)["bad_requests"], [f"a push {SHA1}\rfeat/a"])

    def test_bad_requests_keep_the_last_twenty(self):
        append(self.dir, "".join(f"junk {i}\n" for i in range(25)))
        with self.assertRaises(Timeout):
            run_wait(self.dir)
        state = load(self.dir)
        self.assertEqual(state["bad_requests"], [f"junk {i}" for i in range(5, 25)])
        self.assertEqual(state["requests_offset"], (self.dir / "requests.log").stat().st_size)

    def test_request_from_done_unit_is_reported(self):
        with locked(self.dir) as state:
            record_merged(state, "a", 7, more=False)
        append(self.dir, f"a push {SHA1} feat/a\n")
        self.assertEqual(run_wait(self.dir), f"REQUEST a {SHA1} feat/a")

    def test_truncated_requests_log_restarts_from_the_top(self):
        append(self.dir, f"a push {SHA1} feat/a\n")
        run_wait(self.dir)
        (self.dir / "requests.log").write_text(f"b push {SHA2} x\n", encoding="utf-8")
        self.assertEqual(run_wait(self.dir), f"REQUEST b {SHA2} x")
        self.assertEqual(load(self.dir)["bad_requests"], ["requests.log was truncated"])

    def test_no_event_sleeps_request_seconds_without_gh(self):
        clock = StopClock(limit=3)
        with self.assertRaises(Timeout):
            run_wait(self.dir, clock)
        self.assertEqual(clock.sleeps, [5.0, 5.0, 5.0])


class NextTests(WatcherTestCase):
    def hold_a_queue_b(self):
        with locked(self.dir) as state:
            go(self.dir, state, "a", SHA1, NOW)
            enqueue(state, "b", SHA2, NOW + 1)

    def test_no_next_while_slot_held(self):
        self.hold_a_queue_b()
        with self.assertRaises(Timeout):
            run_wait(self.dir)

    def test_next_after_slot_frees(self):
        self.hold_a_queue_b()
        with locked(self.dir) as state:
            yield_slot(state, "a", "behind", NOW + 2)
        self.assertEqual(run_wait(self.dir), f"NEXT b {SHA2}")
        with self.assertRaises(Timeout):
            run_wait(self.dir)
        self.assertEqual(load(self.dir)["marks"]["_next"], ["b", SHA2])

    def test_next_reported_again_after_the_slot_was_held_in_between(self):
        self.hold_a_queue_b()
        with locked(self.dir) as state:
            yield_slot(state, "a", "behind", NOW + 2)
        self.assertEqual(run_wait(self.dir), f"NEXT b {SHA2}")
        with locked(self.dir) as state:
            go(self.dir, state, "b", SHA2, NOW + 3)
            yield_slot(state, "b", "behind", NOW + 4)
            go(self.dir, state, "c", SHA3, NOW + 5)
            enqueue(state, "b", SHA2, NOW + 6)
        with self.assertRaises(Timeout):
            run_wait(self.dir)
        with locked(self.dir) as state:
            yield_slot(state, "c", "done", NOW + 7)
        self.assertEqual(run_wait(self.dir), f"NEXT b {SHA2}")

    def test_next_reported_again_for_a_new_head(self):
        self.hold_a_queue_b()
        with locked(self.dir) as state:
            yield_slot(state, "a", "behind", NOW + 2)
        self.assertEqual(run_wait(self.dir), f"NEXT b {SHA2}")
        with locked(self.dir) as state:
            enqueue(state, "b", SHA3, NOW + 3)
        self.assertEqual(run_wait(self.dir), f"NEXT b {SHA3}")

    def test_next_skips_and_drops_done_units(self):
        with locked(self.dir) as state:
            enqueue(state, "c", SHA3, NOW)
            enqueue(state, "b", SHA2, NOW + 1)
            record_merged(state, "c", 7, more=False)
        self.assertEqual(run_wait(self.dir), f"NEXT b {SHA2}")
        self.assertEqual([e["unit"] for e in load(self.dir)["queue"]], ["b"])

    def test_no_next_in_parallel_mode(self):
        with locked(self.dir) as state:
            enqueue(state, "b", SHA2, NOW)
            state["queue_mode"] = "parallel"
            self.assertIsNone(next_event(state))

    def test_request_comes_before_next(self):
        self.hold_a_queue_b()
        with locked(self.dir) as state:
            yield_slot(state, "a", "behind", NOW + 2)
        append(self.dir, f"c push {SHA3} feat/c\n")
        self.assertEqual(run_wait(self.dir), f"REQUEST c {SHA3} feat/c")
        self.assertEqual(run_wait(self.dir), f"NEXT b {SHA2}")


class ConcurrencyTests(WatcherTestCase):
    def test_go_during_poll_is_not_lost(self):
        def hook(count):
            assert_unlocked(self.dir)
            if count == 1:
                with locked(self.dir) as state:
                    go(self.dir, state, "a", SHA1, NOW)
            if count == 2:
                append(self.dir, f"b push {SHA2} feat/b\n")

        self.assertEqual(run_wait(self.dir, StopClock(limit=5, hook=hook)), f"REQUEST b {SHA2} feat/b")
        state = load(self.dir)
        self.assertEqual(state["slot"]["unit"], "a")
        self.assertEqual(state["slot"]["sha"], SHA1)


if __name__ == "__main__":
    unittest.main()
