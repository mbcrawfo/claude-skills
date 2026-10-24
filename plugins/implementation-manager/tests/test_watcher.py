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
    init_run,
    load,
    locked,
    record_merged,
    set_option,
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

    def test_next_reported_again_when_the_slot_was_held_only_between_waits(self):
        self.hold_a_queue_b()
        with locked(self.dir) as state:
            yield_slot(state, "a", "behind", NOW + 2)
        self.assertEqual(run_wait(self.dir), f"NEXT b {SHA2}")
        with locked(self.dir) as state:
            go(self.dir, state, "b", SHA2, NOW + 3)
            yield_slot(state, "b", "behind", NOW + 4)
            go(self.dir, state, "c", SHA3, NOW + 5)
            enqueue(state, "b", SHA2, NOW + 6)
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


RUNNING = [check_run(status="IN_PROGRESS", conclusion=None)]
FAILING = [check_run(conclusion="FAILURE")]
SHA_A7 = HEAD_A[:7]


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
        self.assertEqual(state["marks"]["a"]["ready_head"], HEAD_A)
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
        self.assertEqual(self.wait(gh), f"READY a 7 {HEAD_A}")

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
            enqueue(state, "b", HEAD_B, self.clock.now())
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

    def test_stuck_failing_waits_for_pending_request(self):
        append(self.dir, f"a push {HEAD_B} feat/a\n")
        gh = self.gh_a(make_pr(checks=FAILING))
        self.assertEqual(self.wait(gh), f"REQUEST a {HEAD_B} feat/a")
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
