"""Tests for the run watcher: push requests, queue turns and PR events."""

from __future__ import annotations

import fcntl
from pathlib import Path
import tempfile
import unittest

from fakes import HEAD_A, HEAD_B, NO_PR, PR7, PR8, FakeClock, FakeGh, check_run, make_pr
import helpers  # noqa: F401  (puts the scripts dir on sys.path)
from prstate import GhError
from runstate import (
    LOCK_FILE,
    add_unit,
    enqueue,
    go,
    hold,
    init_run,
    load,
    locked,
    record_closed,
    record_merged,
    redo,
    release,
    set_option,
    stop_gate,
    yield_slot,
)
from watcher import next_event, poll_units, take_request, wait

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

    def allow(self, sleeps):
        """Let the next wait sleep `sleeps` more times before Timeout; time keeps running across waits."""
        self.limit = len(self.sleeps) + sleeps


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

    def test_a_request_clears_that_units_go_head(self):
        # The child has moved on from its last go; its new request is answered afresh.
        with locked(self.dir) as state:
            go(self.dir, state, "a", SHA1, NOW)
            yield_slot(state, "a", "owner question", NOW + 1)
            go(self.dir, state, "b", SHA2, NOW + 2)
        append(self.dir, f"a push {SHA3} feat/a\n")
        self.assertEqual(run_wait(self.dir), f"REQUEST a {SHA3} feat/a")
        marks = load(self.dir)["marks"]
        self.assertNotIn("go_head", marks["a"])
        self.assertIn("go_head", marks["b"])

    def test_no_event_sleeps_request_seconds_without_gh(self):
        clock = StopClock(limit=3)
        with self.assertRaises(Timeout):
            run_wait(self.dir, clock)
        self.assertEqual(clock.sleeps, [5.0, 5.0, 5.0])


class AnsweredBeforeReadTests(WatcherTestCase):
    """The manager answers a request it found before any wait read it, e.g. while handling another unit's."""

    def test_redo_before_wait_reads_the_request(self):
        append(self.dir, f"a push {SHA1} feat/a\nb push {SHA2} feat/b\n")
        self.assertEqual(run_wait(self.dir), f"REQUEST a {SHA1} feat/a")
        with locked(self.dir) as state:
            redo(self.dir, state, "a", SHA1, "not covered")
            redo(self.dir, state, "b", SHA2, "not covered")
        with self.assertRaises(Timeout):
            run_wait(self.dir)
        state = load(self.dir)
        self.assertEqual(state["pending"], [])
        self.assertEqual(state["requests_offset"], (self.dir / "requests.log").stat().st_size)

    def test_go_before_wait_reads_the_request_keeps_the_go(self):
        append(self.dir, f"a push {SHA1} feat/a\n")
        with locked(self.dir) as state:
            go(self.dir, state, "a", SHA1, NOW)
        with self.assertRaises(Timeout):
            run_wait(self.dir)
        state = load(self.dir)
        self.assertEqual(state["pending"], [])
        self.assertIn("go_head", state["marks"]["a"])  # the request came before the go, so it does not end it

    def test_queue_before_wait_reads_the_request(self):
        with locked(self.dir) as state:
            go(self.dir, state, "a", SHA1, NOW)
        append(self.dir, f"b push {SHA2} feat/b\n")
        with locked(self.dir) as state:
            enqueue(self.dir, state, "b", SHA2, NOW + 1)
        with self.assertRaises(Timeout):
            run_wait(self.dir)
        state = load(self.dir)
        self.assertEqual(state["pending"], [])
        self.assertEqual([entry["unit"] for entry in state["queue"]], ["b"])

    def test_the_same_sha_asked_again_after_the_answer_is_reported(self):
        append(self.dir, f"a push {SHA1} feat/a\n")
        with locked(self.dir) as state:
            redo(self.dir, state, "a", SHA1, "not covered")
        append(self.dir, f"a push {SHA1} feat/a\n")
        self.assertEqual(run_wait(self.dir), f"REQUEST a {SHA1} feat/a")
        self.assertEqual([entry["sha"] for entry in load(self.dir)["pending"]], [SHA1])

    def test_an_answer_for_another_sha_does_not_cover_the_request(self):
        append(self.dir, f"a push {SHA2} feat/a\n")
        with locked(self.dir) as state:
            redo(self.dir, state, "a", SHA1, "not covered")
        self.assertEqual(run_wait(self.dir), f"REQUEST a {SHA2} feat/a")

    def test_answers_are_forgotten_once_read_past(self):
        append(self.dir, f"a push {SHA1} feat/a\n")
        with locked(self.dir) as state:
            redo(self.dir, state, "a", SHA1, "not covered")
        with self.assertRaises(Timeout):
            run_wait(self.dir)
        append(self.dir, f"b push {SHA2} feat/b\n")
        run_wait(self.dir)
        self.assertNotIn("answered", load(self.dir)["marks"].get("a", {}))

    def test_truncation_forgets_answers_and_keeps_the_next_mark(self):
        append(self.dir, f"a push {SHA1} feat/a\nb push {SHA2} feat/b\n")
        run_wait(self.dir)
        with locked(self.dir) as state:
            go(self.dir, state, "a", SHA1, NOW)
            enqueue(self.dir, state, "b", SHA2, NOW + 1)
            yield_slot(state, "a", "behind", NOW + 2)
            self.assertEqual(next_event(state), f"NEXT b {SHA2}")
        self.assertIn("answered", load(self.dir)["marks"]["b"])
        (self.dir / "requests.log").write_text(f"b push {SHA2} x\n", encoding="utf-8")
        self.assertEqual(run_wait(self.dir), f"REQUEST b {SHA2} x")
        marks = load(self.dir)["marks"]
        self.assertNotIn("answered", marks["b"])
        self.assertEqual(marks["_next"], ["b", SHA2])

    def test_an_answer_after_every_request_was_read_is_not_kept(self):
        append(self.dir, f"a push {SHA1} feat/a\n")
        run_wait(self.dir)
        with locked(self.dir) as state:
            redo(self.dir, state, "a", SHA1, "not covered")
        self.assertNotIn("answered", load(self.dir)["marks"].get("a", {}))


class PendingTests(WatcherTestCase):
    def setUp(self):
        super().setUp()
        self.clock = StopClock(limit=0)

    def wait_for(self, sleeps):
        self.clock.allow(sleeps)
        return run_wait(self.dir, self.clock)

    def assert_quiet(self, sleeps):
        with self.assertRaises(Timeout):
            self.wait_for(sleeps)

    def test_pending_after_fifteen_minutes_once(self):
        append(self.dir, f"a push {SHA1} feat/a\n")
        self.assertEqual(self.wait_for(1), f"REQUEST a {SHA1} feat/a")
        reported_at = self.clock.t
        self.assertEqual(self.wait_for(400), f"PENDING a {SHA1} feat/a")
        self.assertGreaterEqual(self.clock.t - reported_at, 900)
        self.assertLess(self.clock.t - reported_at, 900 + 10)
        self.assert_quiet(400)
        self.assertEqual([entry["unit"] for entry in load(self.dir)["pending"]], ["a"])

    def test_no_pending_when_answered_within_fifteen_minutes(self):
        append(self.dir, f"a push {SHA1} feat/a\n")
        self.assertEqual(self.wait_for(1), f"REQUEST a {SHA1} feat/a")
        self.assert_quiet(170)  # 14 minutes
        with locked(self.dir) as state:
            go(self.dir, state, "a", SHA1, self.clock.now())
        self.assert_quiet(400)

    def test_a_new_request_gets_its_own_pending(self):
        append(self.dir, f"a push {SHA1} feat/a\n")
        self.assertEqual(self.wait_for(1), f"REQUEST a {SHA1} feat/a")
        self.assertEqual(self.wait_for(400), f"PENDING a {SHA1} feat/a")
        append(self.dir, f"a push {SHA1} feat/a\n")
        self.assertEqual(self.wait_for(1), f"REQUEST a {SHA1} feat/a")
        self.assertEqual(self.wait_for(400), f"PENDING a {SHA1} feat/a")


class NextTests(WatcherTestCase):
    def hold_a_queue_b(self):
        with locked(self.dir) as state:
            go(self.dir, state, "a", SHA1, NOW)
            enqueue(self.dir, state, "b", SHA2, NOW + 1)

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
            enqueue(self.dir, state, "b", SHA2, NOW + 6)
        with self.assertRaises(Timeout):
            run_wait(self.dir)
        with locked(self.dir) as state:
            yield_slot(state, "c", "done", NOW + 7)
        self.assertEqual(run_wait(self.dir), f"NEXT b {SHA2}")

    def test_next_reported_again_when_the_slot_was_held_only_between_waits(self):
        self.hold_a_queue_b()
        with locked(self.dir) as state:
            yield_slot(state, "a", "behind", NOW + 2)
        self.assertEqual(run_wait(self.dir), f"NEXT b {SHA2}")
        with locked(self.dir) as state:
            go(self.dir, state, "b", SHA2, NOW + 3)
            yield_slot(state, "b", "behind", NOW + 4)
            go(self.dir, state, "c", SHA3, NOW + 5)
            enqueue(self.dir, state, "b", SHA2, NOW + 6)
            yield_slot(state, "c", "done", NOW + 7)
        self.assertEqual(run_wait(self.dir), f"NEXT b {SHA2}")

    def test_next_reported_again_for_a_new_head(self):
        self.hold_a_queue_b()
        with locked(self.dir) as state:
            yield_slot(state, "a", "behind", NOW + 2)
        self.assertEqual(run_wait(self.dir), f"NEXT b {SHA2}")
        with locked(self.dir) as state:
            enqueue(self.dir, state, "b", SHA3, NOW + 3)
        self.assertEqual(run_wait(self.dir), f"NEXT b {SHA3}")

    def test_next_skips_and_drops_done_units(self):
        with locked(self.dir) as state:
            enqueue(self.dir, state, "c", SHA3, NOW)
            enqueue(self.dir, state, "b", SHA2, NOW + 1)
            record_merged(state, "c", 7, more=False)
        self.assertEqual(run_wait(self.dir), f"NEXT b {SHA2}")
        self.assertEqual([e["unit"] for e in load(self.dir)["queue"]], ["b"])

    def test_next_skips_entries_not_mergeable(self):
        with locked(self.dir) as state:
            add_unit(state, "b", prereqs=None, branch=None, worktree=None, specs=None, after=["a"])
            enqueue(self.dir, state, "b", SHA2, NOW)
            enqueue(self.dir, state, "c", SHA3, NOW + 1)
            self.assertEqual(next_event(state), f"NEXT c {SHA3}")

    def test_no_next_when_no_entry_is_mergeable(self):
        with locked(self.dir) as state:
            add_unit(state, "b", prereqs=None, branch=None, worktree=None, specs=None, after=["a"])
            enqueue(self.dir, state, "b", SHA2, NOW)
            self.assertIsNone(next_event(state))
            self.assertEqual(len(state["queue"]), 1)

    def test_no_next_in_parallel_mode(self):
        with locked(self.dir) as state:
            enqueue(self.dir, state, "b", SHA2, NOW)
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


RUNNING = [check_run(status="IN_PROGRESS", conclusion=None)]
FAILING = [check_run(conclusion="FAILURE")]
SHA_A7 = HEAD_A[:7]
PR9 = '[{"number": 9}]'


class PrTestCase(WatcherTestCase):
    """Unit `a` has branch feat/a and holds the slot, taken before any poll, so its no-push baseline is None."""

    def setUp(self):
        super().setUp()
        self.clock = StopClock(limit=0)
        with locked(self.dir) as state:
            add_unit(state, "a", prereqs=None, branch="feat/a", worktree=None, specs=None)
            go(self.dir, state, "a", HEAD_A, self.clock.now())

    def wait(self, gh, sleeps=40, **timings):
        self.clock.allow(sleeps)
        return wait(self.dir, gh=gh, sleep=self.clock.sleep, now=self.clock.now, **timings)

    def assert_quiet(self, gh, sleeps=40, **timings):
        with self.assertRaises(Timeout):
            self.wait(gh, sleeps, **timings)

    def gh_a(self, *polls):
        return FakeGh({"feat/a": [PR7]}, {7: list(polls)})

    def add_b(self, *, holds=False):
        with locked(self.dir) as state:
            add_unit(state, "b", prereqs=None, branch="feat/b", worktree=None, specs=None)
            if holds:
                yield_slot(state, "a", "test", self.clock.now())
                go(self.dir, state, "b", HEAD_B, self.clock.now())


class ReadyTests(PrTestCase):
    def test_ready_after_confirm_poll(self):
        gh = self.gh_a(make_pr())
        self.assertEqual(self.wait(gh), f"READY a 7 {HEAD_A}")
        self.assertEqual(self.clock.sleeps, [5.0] * 6)
        self.assertEqual(gh.count(["api", "graphql"]), 2)
        state = load(self.dir)
        self.assertEqual(state["units"]["a"]["prs"], {"7": "OPEN"})
        self.assertEqual(state["marks"]["a"]["ready_head"], f"7 {HEAD_A}")
        self.assertEqual(state["marks"]["a"]["head"], HEAD_A)

    def test_ready_needs_two_equal_clean_polls(self):
        two_checks = make_pr(checks=[check_run(), check_run(name="lint")])
        gh = self.gh_a(make_pr(), two_checks, two_checks)
        self.assertEqual(self.wait(gh), f"READY a 7 {HEAD_A}")
        self.assertEqual(gh.count(["api", "graphql"]), 3)

    def test_ready_not_repeated_for_same_head(self):
        gh = self.gh_a(make_pr())
        self.assertEqual(self.wait(gh), f"READY a 7 {HEAD_A}")
        self.assert_quiet(gh)
        gh.polls[7] = [make_pr(head=HEAD_B)]
        self.assertEqual(self.wait(gh), f"READY a 7 {HEAD_B}")

    def test_ready_suppressed_while_request_pending(self):
        append(self.dir, f"a push {HEAD_B} feat/a\n")
        gh = self.gh_a(make_pr())
        self.assertEqual(self.wait(gh), f"REQUEST a {HEAD_B} feat/a")
        self.assert_quiet(gh)
        with locked(self.dir) as state:
            go(self.dir, state, "a", HEAD_B, self.clock.now())
        # The go answers a new commit: the old head is not READY until the push lands and is confirmed.
        self.assert_quiet(gh)
        gh.polls[7] = [make_pr(head=HEAD_B)]
        pushed_at = self.clock.t
        self.assertEqual(self.wait(gh), f"READY a 7 {HEAD_B}")
        self.assertGreaterEqual(self.clock.t - pushed_at, 30)

    def go_over_clean_head_a(self, mode="serial"):
        """a's PR is clean on HEAD_A while a asks to push a new commit HEAD_B; then a gets go."""
        if mode == "parallel":
            with locked(self.dir) as state:
                yield_slot(state, "a", "test", self.clock.now())
                set_option(state, "queue", "parallel")
        append(self.dir, f"a push {HEAD_B} feat/a\n")
        gh = self.gh_a(make_pr())
        self.assertEqual(self.wait(gh), f"REQUEST a {HEAD_B} feat/a")
        self.assert_quiet(gh)
        with locked(self.dir) as state:
            go(self.dir, state, "a", HEAD_B, self.clock.now())
        return gh

    def test_no_ready_after_go_while_head_unchanged(self):
        for mode in ("serial", "parallel"):
            with self.subTest(mode=mode):
                self.setUp()
                gh = self.go_over_clean_head_a(mode)
                self.assert_quiet(gh, sleeps=300)  # 25 minutes, short of the no-push STUCK

    def test_ready_after_go_once_new_head_is_confirmed(self):
        for mode in ("serial", "parallel"):
            with self.subTest(mode=mode):
                self.setUp()
                gh = self.go_over_clean_head_a(mode)
                self.assert_quiet(gh)
                gh.polls[7] = [make_pr(head=HEAD_B)]
                pushed_at = self.clock.t
                self.assertEqual(self.wait(gh), f"READY a 7 {HEAD_B}")
                self.assertGreaterEqual(self.clock.t - pushed_at, 30)
                self.assertNotIn("go_head", load(self.dir)["marks"]["a"])

    def test_ready_stays_held_after_a_yield_with_an_outstanding_go(self):
        gh = self.go_over_clean_head_a()
        with locked(self.dir) as state:
            yield_slot(state, "a", "stuck", self.clock.now())
        self.assert_quiet(gh, sleeps=300)  # the child may still push HEAD_B: HEAD_A is not READY
        self.assertEqual(load(self.dir)["marks"]["a"]["go_head"], HEAD_A)
        gh.polls[7] = [make_pr(head=HEAD_B)]
        self.assertEqual(self.wait(gh), f"READY a 7 {HEAD_B}")
        self.assertNotIn("go_head", load(self.dir)["marks"]["a"])

    def test_ready_after_a_request_needs_a_fresh_confirm(self):
        append(self.dir, f"a push {HEAD_B} feat/a\n")
        gh = self.gh_a(make_pr())
        self.assertEqual(self.wait(gh), f"REQUEST a {HEAD_B} feat/a")
        self.assert_quiet(gh)
        with locked(self.dir) as state:
            redo(self.dir, state, "a", HEAD_B, "not-covered")  # also frees a's slot
        answered_at = self.clock.t
        self.assertEqual(self.wait(gh), f"READY a 7 {HEAD_A}")
        self.assertGreaterEqual(self.clock.t - answered_at, 30)

    def test_go_for_the_pushed_head_does_not_hold_ready(self):
        with locked(self.dir) as state:
            yield_slot(state, "a", "test", self.clock.now())
        gh = self.gh_a(make_pr())
        self.assert_quiet(gh, sleeps=1)
        with locked(self.dir) as state:
            go(self.dir, state, "a", HEAD_A, self.clock.now())
        self.assertEqual(self.wait(gh), f"READY a 7 {HEAD_A}")

    def test_request_during_poll_comes_before_ready(self):
        inner = self.gh_a(make_pr())

        def gh(args):
            if args[:2] == ["api", "graphql"] and inner.count(["api", "graphql"]) == 1:
                append(self.dir, f"a push {HEAD_B} feat/a\n")
            return inner(args)

        self.assertEqual(self.wait(gh), f"REQUEST a {HEAD_B} feat/a")
        self.assertEqual(inner.count(["api", "graphql"]), 2)
        self.assert_quiet(gh)

    def test_ready_for_a_new_pr_on_the_same_head(self):
        gh = FakeGh({"feat/a": [PR7, PR8]}, {7: [make_pr()], 8: [make_pr()]})
        self.assertEqual(self.wait(gh), f"READY a 7 {HEAD_A}")
        with locked(self.dir) as state:
            record_closed(state, "a", 7)
        self.assertEqual(self.wait(gh), f"READY a 8 {HEAD_A}")

    def test_ready_held_for_non_holder_until_slot_frees(self):
        with locked(self.dir) as state:
            yield_slot(state, "a", "test", self.clock.now())
            go(self.dir, state, "b", HEAD_B, self.clock.now())
        gh = self.gh_a(make_pr())
        self.assert_quiet(gh)
        with locked(self.dir) as state:
            yield_slot(state, "b", "test", self.clock.now())
        self.assertEqual(self.wait(gh), f"READY a 7 {HEAD_A}")

    def test_ready_comes_before_next(self):
        # c holds the slot while a's PR is confirmed clean; then the slot frees with b queued.
        with locked(self.dir) as state:
            yield_slot(state, "a", "test", self.clock.now())
            go(self.dir, state, "c", HEAD_B, self.clock.now())
        gh = self.gh_a(make_pr())
        self.assert_quiet(gh)
        with locked(self.dir) as state:
            enqueue(self.dir, state, "b", HEAD_B, self.clock.now())
            yield_slot(state, "c", "test", self.clock.now())
        self.assertEqual(self.wait(gh), f"READY a 7 {HEAD_A}")
        self.assertEqual(self.wait(gh), f"NEXT b {HEAD_B}")

    def test_remind_after_thirty_minutes(self):
        gh = self.gh_a(make_pr())
        self.assertEqual(self.wait(gh), f"READY a 7 {HEAD_A}")
        ready_at = self.clock.t
        self.assertEqual(self.wait(gh, sleeps=400), f"REMIND a 7 {HEAD_A}")
        self.assertGreaterEqual(self.clock.t - ready_at, 1800)
        self.assertLess(self.clock.t - ready_at, 1800 + 60)
        self.assert_quiet(gh, sleeps=400)

    def test_no_ready_for_a_stopped_unit(self):
        with locked(self.dir) as state:
            stop_gate(self.dir, state, "a", "cancelled")
        self.assert_quiet(self.gh_a(make_pr()))

    def test_no_remind_once_the_unit_is_stopped(self):
        gh = self.gh_a(make_pr())
        self.assertEqual(self.wait(gh), f"READY a 7 {HEAD_A}")
        with locked(self.dir) as state:
            stop_gate(self.dir, state, "a", "cancelled")
        self.assert_quiet(gh, sleeps=400)


class EndedTests(PrTestCase):
    def test_merged_reported_once_and_skipped_if_recorded(self):
        gh = FakeGh({"feat/a": [PR7, NO_PR]}, {7: [make_pr(checks=RUNNING), make_pr(state="MERGED")]})
        self.assertEqual(self.wait(gh), "MERGED a 7")
        self.assert_quiet(gh)
        self.assertEqual(load(self.dir)["units"]["a"]["prs"], {"7": "OPEN"})

        # Unit b's PR merges and the manager records it while the watcher's poll is out.
        self.add_b()
        gh = FakeGh(
            {"feat/a": [NO_PR], "feat/b": [PR8, NO_PR]}, {8: [make_pr(checks=RUNNING), make_pr(state="MERGED")]}
        )
        self.assert_quiet(gh, sleeps=1)

        def manager_records_merge(args):
            if args[:2] == ["api", "graphql"]:
                with locked(self.dir) as state:
                    record_merged(state, "b", 8, more=True)
            return gh(args)

        self.assert_quiet(manager_records_merge)
        self.assertEqual(load(self.dir)["units"]["b"]["prs"], {"8": "MERGED"})

    def test_recorded_merged_pr_is_not_reopened_or_reported(self):
        with locked(self.dir) as state:
            record_merged(state, "a", 7, more=True)
        gh = FakeGh({"feat/a": [PR7]}, {7: [make_pr(state="MERGED")]})
        self.assert_quiet(gh)
        self.assertEqual(load(self.dir)["units"]["a"]["prs"], {"7": "MERGED"})

    def test_closed_reported(self):
        gh = FakeGh({"feat/a": [PR7, NO_PR]}, {7: [make_pr(state="CLOSED")]})
        self.assertEqual(self.wait(gh), "CLOSED a 7")


class BehindStuckStalledTests(PrTestCase):
    def test_behind_only_for_holder(self):
        self.add_b()
        gh = FakeGh(
            {"feat/a": [PR7], "feat/b": [PR8]},
            {7: [make_pr(checks=RUNNING)], 8: [make_pr(merge="BEHIND", head=HEAD_B)]},
        )
        self.assert_quiet(gh)
        gh.polls[7] = [make_pr(merge="BEHIND")]
        self.assertEqual(self.wait(gh), f"BEHIND a 7 {HEAD_A}")
        self.assert_quiet(gh)

    def test_parallel_mode_reports_behind_for_all(self):
        self.add_b()
        with locked(self.dir) as state:
            yield_slot(state, "a", "test", self.clock.now())
            set_option(state, "queue", "parallel")
        gh = FakeGh(
            {"feat/a": [PR7], "feat/b": [PR8]},
            {7: [make_pr(merge="BEHIND")], 8: [make_pr(merge="BEHIND", head=HEAD_B)]},
        )
        self.assertEqual(self.wait(gh), f"BEHIND a 7 {HEAD_A}")
        self.assertEqual(self.wait(gh), f"BEHIND b 8 {HEAD_B}")
        self.assert_quiet(gh)

    def test_stuck_after_twenty_minutes_failing(self):
        start = self.clock.t
        gh = self.gh_a(make_pr(checks=FAILING))
        self.assertEqual(self.wait(gh, sleeps=400), "STUCK a 7 checks-failed=1")
        self.assertGreaterEqual(self.clock.t - start, 1200)
        self.assertLess(self.clock.t - start, 1200 + 60)
        self.assert_quiet(gh, sleeps=400)

    def test_no_failure_stuck_while_the_holders_go_is_outstanding(self):
        # The holder's PR fails and it got go for a fix: only the no-push STUCK covers a go that never lands.
        fix = "f" * 40
        gh = self.gh_a(make_pr(checks=FAILING))
        with locked(self.dir) as state:
            yield_slot(state, "a", "test", self.clock.now())
        self.assert_quiet(gh, sleeps=1)
        start = self.clock.t
        with locked(self.dir) as state:
            go(self.dir, state, "a", fix, start)
        self.assertEqual(self.wait(gh, sleeps=400), "STUCK a 7 no-push")
        self.assertGreaterEqual(self.clock.t - start, 1800)
        self.assert_quiet(gh, sleeps=400)
        gh.polls[7] = [make_pr(head=fix, checks=FAILING)]  # the fix lands and fails too
        pushed_at = self.clock.t
        self.assertEqual(self.wait(gh, sleeps=400), "STUCK a 7 checks-failed=1")
        self.assertGreaterEqual(self.clock.t - pushed_at, 1200)

    def test_stuck_failing_waits_for_pending_request(self):
        append(self.dir, f"a push {HEAD_B} feat/a\n")
        gh = self.gh_a(make_pr(checks=FAILING))
        self.assertEqual(self.wait(gh), f"REQUEST a {HEAD_B} feat/a")
        # The unanswered request is re-reported once, and never turns into a STUCK.
        self.assertEqual(self.wait(gh, sleeps=400), f"PENDING a {HEAD_B} feat/a")
        self.assert_quiet(gh, sleeps=400)

    def test_stuck_not_for_non_holder(self):
        with locked(self.dir) as state:
            yield_slot(state, "a", "test", self.clock.now())
        self.assert_quiet(self.gh_a(make_pr(checks=FAILING)), sleeps=400)

    def test_stuck_no_push(self):
        gh = self.gh_a(make_pr(checks=RUNNING))
        with locked(self.dir) as state:
            yield_slot(state, "a", "test", self.clock.now())
        self.assert_quiet(gh, sleeps=1)
        start = self.clock.t
        with locked(self.dir) as state:
            go(self.dir, state, "a", HEAD_A, start)
            self.assertEqual(state["slot"]["head"], HEAD_A)
        self.assertEqual(self.wait(gh, sleeps=400), "STUCK a 7 no-push")
        self.assertGreaterEqual(self.clock.t - start, 1800)
        self.assertLess(self.clock.t - start, 1800 + 60)
        self.assert_quiet(gh, sleeps=400)

    def test_no_stuck_after_a_push(self):
        gh = self.gh_a(make_pr(checks=RUNNING))
        with locked(self.dir) as state:
            yield_slot(state, "a", "test", self.clock.now())
        self.assert_quiet(gh, sleeps=1)
        with locked(self.dir) as state:
            go(self.dir, state, "a", HEAD_A, self.clock.now())
        gh.polls[7] = [make_pr(head=HEAD_B, checks=RUNNING)]
        self.assert_quiet(gh, sleeps=400)

    def test_stuck_no_push_without_pr(self):
        gh = FakeGh({"feat/a": [NO_PR]})
        self.assertEqual(self.wait(gh, sleeps=400), "STUCK a - no-push")

    def test_stalled_after_three_hours(self):
        with locked(self.dir) as state:
            yield_slot(state, "a", "test", self.clock.now())
        start = self.clock.t
        gh = self.gh_a(make_pr(checks=RUNNING))
        # A restart in between keeps the time the signature was first seen.
        self.assert_quiet(gh, sleeps=1000)
        self.assertEqual(self.wait(gh, sleeps=2000), f"STALLED a 7 head={SHA_A7} checks-running=1")
        self.assertGreaterEqual(self.clock.t - start, 10800)
        self.assertLess(self.clock.t - start, 10800 + 60)
        self.assert_quiet(gh, sleeps=1000)


class HoldTests(PrTestCase):
    def test_no_ready_remind_or_stalled_while_held(self):
        with locked(self.dir) as state:
            hold(self.dir, state, "a", "waiting on owner", self.clock.now())
        gh = self.gh_a(make_pr())
        # Well past the confirm, remind and stall times of a ready PR.
        self.assert_quiet(gh, sleeps=4000, stall_seconds=10800)
        marks = load(self.dir)["marks"]["a"]
        self.assertNotIn("ready_head", marks)
        self.assertNotIn("stalled_key", marks)
        with locked(self.dir) as state:
            release(state, "a")
        self.assertEqual(self.wait(gh), f"READY a 7 {HEAD_A}")

    def test_no_stalled_right_after_a_long_hold_ends(self):
        with locked(self.dir) as state:
            hold(self.dir, state, "a", "waiting on owner", self.clock.now())
        gh = self.gh_a(make_pr())
        self.assert_quiet(gh, sleeps=2500, stall_seconds=3600)  # several stall windows pass while held
        with locked(self.dir) as state:
            release(state, "a")
        start = self.clock.t
        self.assertEqual(self.wait(gh, stall_seconds=3600, remind_seconds=1e9), f"READY a 7 {HEAD_A}")
        # The stall window starts afresh at the release; the PR stays unchanged and ready for less than that.
        self.assert_quiet(gh, sleeps=500, stall_seconds=3600, remind_seconds=1e9)
        self.assertLess(self.clock.t - start, 3600)

    def test_stalled_while_held_answer_sent(self):
        # An `answer sent:` hold waits on the child to ask again, so a child that never does is reported.
        with locked(self.dir) as state:
            hold(self.dir, state, "a", "answer sent: naming", self.clock.now())
        gh = self.gh_a(make_pr())
        line = self.wait(gh, sleeps=2000, stall_seconds=3600, remind_seconds=1e9)
        self.assertEqual(line, f"STALLED a 7 head={SHA_A7}")
        self.assertNotIn("ready_head", load(self.dir)["marks"]["a"])

    def test_no_stalled_while_held_for_another_reason(self):
        with locked(self.dir) as state:
            hold(self.dir, state, "a", "own session: naming", self.clock.now())
        gh = self.gh_a(make_pr())
        self.assert_quiet(gh, sleeps=2000, stall_seconds=3600, remind_seconds=1e9)

    def test_ready_reported_afresh_after_hold_and_release_on_the_same_head(self):
        gh = self.gh_a(make_pr())
        self.assertEqual(self.wait(gh), f"READY a 7 {HEAD_A}")
        with locked(self.dir) as state:
            hold(self.dir, state, "a", "waiting on owner", self.clock.now())
        self.assert_quiet(gh)
        with locked(self.dir) as state:
            release(state, "a")
        self.assertEqual(self.wait(gh), f"READY a 7 {HEAD_A}")


class OrphanTests(PrTestCase):
    def test_orphan_reported_once_for_an_open_pr_on_a_done_units_branch(self):
        self.add_b()
        with locked(self.dir) as state:
            record_merged(state, "b", 8, more=False)
        gh = FakeGh({"feat/a": [PR7], "feat/b": [NO_PR, PR9]}, {7: [make_pr(checks=RUNNING)]})
        self.assert_quiet(gh, sleeps=1)
        self.assertEqual(self.wait(gh), "ORPHAN b 9")
        self.assert_quiet(gh)
        self.assertEqual(load(self.dir)["units"]["b"]["prs"], {"8": "MERGED"})

    def test_no_orphan_for_a_done_unit_without_an_open_pr(self):
        self.add_b()
        with locked(self.dir) as state:
            record_merged(state, "b", 8, more=False)
        gh = FakeGh({"feat/a": [PR7], "feat/b": [NO_PR]}, {7: [make_pr(checks=RUNNING)]})
        self.assert_quiet(gh)
        self.assertGreater(gh.count(["pr", "list"]), 1)


class GhFailureTests(PrTestCase):
    def test_requests_reported_while_gh_fails(self):
        gh = FakeGh({"feat/a": [GhError("boom")]})

        def hook(count):
            if self.clock.t - start >= 540:  # the tenth poll is due
                append(self.dir, f"a push {HEAD_B} feat/a\n")

        start = self.clock.t
        self.clock.hook = hook
        self.assertEqual(self.wait(gh, sleeps=200), f"REQUEST a {HEAD_B} feat/a")
        self.assertEqual(gh.count(["pr", "list"]), 9)

    def test_error_after_ten_failures(self):
        gh = FakeGh({"feat/a": [GhError("boom\nsecond line")]})
        line = self.wait(gh, sleeps=200)
        self.assertTrue(line.startswith("ERROR"), line)
        self.assertIn("boom second line", line)
        self.assertEqual(gh.count(["pr", "list"]), 10)

    def test_successful_poll_resets_failure_count(self):
        boom = GhError("boom")
        gh = FakeGh({"feat/a": [PR7]}, {7: [boom] * 9 + [make_pr(checks=RUNNING)] + [boom]})
        # 9 failures, a success, then the tenth failure in a row on the 20th poll.
        self.assertEqual(self.wait(gh, sleeps=400).split()[0], "ERROR")
        self.assertEqual(gh.count(["api", "graphql"]), 20)


class PollTests(PrTestCase):
    def test_poll_units_records_found_pr_and_head(self):
        self.add_b()
        with locked(self.dir) as state:
            record_merged(state, "b", 8, more=False)
            polled = poll_units(state, gh=self.gh_a(make_pr(checks=RUNNING)))
            self.assertEqual(list(polled), ["a"])
            self.assertEqual(polled["a"].head, HEAD_A)
            self.assertEqual(state["units"]["a"]["prs"], {"7": "OPEN"})
            self.assertEqual(state["marks"]["a"]["pr"], 7)
            self.assertEqual(state["marks"]["a"]["head"], HEAD_A)

    def test_recorded_open_pr_is_polled_without_a_search(self):
        gh = self.gh_a(make_pr(checks=RUNNING))
        self.assert_quiet(gh, sleeps=30)
        self.assertEqual(gh.count(["pr", "list"]), 1)
        self.assertEqual(gh.count(["api", "graphql"]), 3)

    def test_lock_is_free_during_gh_calls(self):
        inner = self.gh_a(make_pr())

        def gh(args):
            assert_unlocked(self.dir)
            return inner(args)

        self.assertEqual(self.wait(gh), f"READY a 7 {HEAD_A}")


if __name__ == "__main__":
    unittest.main()
