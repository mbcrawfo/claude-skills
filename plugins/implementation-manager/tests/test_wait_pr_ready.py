"""Tests for the PR watch script: summarize() over GraphQL objects and watch() over a scripted gh."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import helpers  # noqa: F401  (puts the scripts dir on sys.path)
import wait_pr_ready
from wait_pr_ready import GhError, summarize, watch

HEAD_A = "a" * 40
HEAD_B = "b" * 40
STALL = 3 * 3600
POLL = 120


def check_run(status="COMPLETED", conclusion="SUCCESS", name="build"):
    return {"__typename": "CheckRun", "name": name, "status": status, "conclusion": conclusion}


def status_context(state="SUCCESS", context="ci/legacy"):
    return {"__typename": "StatusContext", "context": context, "state": state, "description": ""}


def make_pr(*, state="OPEN", draft=False, merge="CLEAN", head=HEAD_A, checks=None, threads=(), total_threads=None):
    """A GraphQL `pullRequest` object; a green CheckRun by default."""
    contexts = [check_run()] if checks is None else checks
    return {
        "state": state,
        "isDraft": draft,
        "mergeStateStatus": merge,
        "headRefOid": head,
        "reviewThreads": {
            "totalCount": len(threads) if total_threads is None else total_threads,
            "nodes": [{"isResolved": resolved} for resolved in threads],
        },
        "commits": {"nodes": [{"commit": {"statusCheckRollup": {"contexts": {"nodes": contexts}}}}]},
    }


def graphql(pr):
    return json.dumps({"data": {"repository": {"pullRequest": pr}}})


class FakeGh:
    """Answers `gh pr list` and `gh api graphql` from scripted queues.

    An item is a str (returned), a dict (a pullRequest, wrapped and dumped) or an exception (raised).
    The last `pr list` answer repeats; running out of graphql answers fails the test.
    """

    def __init__(self, pr_list, polls=()):
        self.pr_list = list(pr_list)
        self.polls = list(polls)
        self.calls = []

    def __call__(self, args):
        self.calls.append(list(args))
        if args[:2] == ["pr", "list"]:
            item = self.pr_list.pop(0) if len(self.pr_list) > 1 else self.pr_list[0]
        elif args[:2] == ["api", "graphql"]:
            if not self.polls:
                raise AssertionError("unexpected extra graphql call")
            item = self.polls.pop(0)
        else:
            raise AssertionError(f"unexpected gh call {args}")
        if isinstance(item, Exception):
            raise item
        return graphql(item) if isinstance(item, dict) else item

    def count(self, kind):
        return sum(1 for call in self.calls if call[:2] == kind)


PR7 = json.dumps([{"number": 7}])
NO_PR = "[]"


class FakeClock:
    def __init__(self):
        self.t = 1_000_000.0
        self.sleeps = []

    def now(self):
        return self.t

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.t += seconds


def run_watch(gh, *, skip_head="", once=False, stall=STALL, poll=POLL):
    clock = FakeClock()
    line = watch(
        "owner/repo",
        "feat/x",
        stall_seconds=stall,
        poll_seconds=poll,
        skip_head=skip_head,
        once=once,
        gh=gh,
        sleep=clock.sleep,
        now=clock.now,
    )
    return line, clock


class ReadyTests(unittest.TestCase):
    def test_no_pr_waits_then_ready_after_two_identical_polls(self):
        gh = FakeGh([NO_PR, PR7], [make_pr(), make_pr()])
        line, clock = run_watch(gh)
        self.assertEqual(line, f"READY 7 {HEAD_A}")
        self.assertEqual(gh.count(["pr", "list"]), 2)
        self.assertEqual(gh.count(["api", "graphql"]), 2)
        self.assertEqual(clock.sleeps, [POLL, POLL])

    def test_ready_requires_two_identical_polls_head_change_resets(self):
        gh = FakeGh([PR7], [make_pr(head=HEAD_A), make_pr(head=HEAD_B), make_pr(head=HEAD_B)])
        line, _ = run_watch(gh)
        self.assertEqual(line, f"READY 7 {HEAD_B}")
        self.assertEqual(gh.count(["api", "graphql"]), 3)

    def test_has_hooks_counts_as_mergeable(self):
        gh = FakeGh([PR7], [make_pr(merge="HAS_HOOKS")] * 2)
        line, _ = run_watch(gh)
        self.assertEqual(line, f"READY 7 {HEAD_A}")

    def test_pr_is_listed_once_then_polled_by_number(self):
        gh = FakeGh([PR7], [make_pr(checks=[check_run(status="IN_PROGRESS", conclusion=None)]), make_pr(), make_pr()])
        run_watch(gh)
        self.assertEqual(gh.count(["pr", "list"]), 1)
        graphql_call = next(c for c in gh.calls if c[:2] == ["api", "graphql"])
        self.assertIn("p=7", graphql_call)
        self.assertIn("o=owner", graphql_call)
        self.assertIn("n=repo", graphql_call)


class AttentionTests(unittest.TestCase):
    def test_behind_base_is_attention(self):
        gh = FakeGh([PR7], [make_pr(merge="BEHIND")])
        line, _ = run_watch(gh)
        self.assertEqual(line, f"ATTENTION 7 {HEAD_A} behind-base")

    def test_skip_head_suppresses_attention_and_ready(self):
        # Behind-base on the skipped head is not reported (once mode shows the poll's reasons).
        gh = FakeGh([PR7], [make_pr(merge="BEHIND")] * 2)
        line, _ = run_watch(gh, skip_head=HEAD_A[:7], once=True)
        self.assertEqual(line, "NOT-READY 7 head=aaaaaaa merge=BEHIND")
        # A clean skipped head never reports READY; it says why.
        gh = FakeGh([PR7], [make_pr()] * 2)
        line, _ = run_watch(gh, skip_head=HEAD_A[:7], once=True)
        self.assertEqual(line, "NOT-READY 7 head=aaaaaaa skip-head")

    def test_skip_head_does_not_cover_other_heads(self):
        gh = FakeGh([PR7], [make_pr(merge="BEHIND", head=HEAD_B)])
        line, _ = run_watch(gh, skip_head=HEAD_A[:7])
        self.assertEqual(line, f"ATTENTION 7 {HEAD_B} behind-base")

    def test_skip_head_then_new_head_becomes_ready(self):
        gh = FakeGh([PR7], [make_pr(head=HEAD_A), make_pr(head=HEAD_B), make_pr(head=HEAD_B)])
        line, _ = run_watch(gh, skip_head=HEAD_A)
        self.assertEqual(line, f"READY 7 {HEAD_B}")


class StateTests(unittest.TestCase):
    def test_merged_and_closed(self):
        for state in ("MERGED", "CLOSED"):
            with self.subTest(state=state):
                gh = FakeGh([PR7], [make_pr(state=state)])
                line, _ = run_watch(gh)
                self.assertEqual(line, f"{state} 7")


class ReasonTests(unittest.TestCase):
    def test_reasons(self):
        cases = [
            ("clean", make_pr(), ""),
            ("has hooks", make_pr(merge="HAS_HOOKS"), ""),
            ("draft", make_pr(draft=True), " draft"),
            ("blocked", make_pr(merge="BLOCKED"), " merge=BLOCKED"),
            ("behind", make_pr(merge="BEHIND"), " merge=BEHIND"),
            ("no checks", make_pr(checks=[]), " no-checks"),
            ("check run in progress", make_pr(checks=[check_run("IN_PROGRESS", None)]), " checks-running=1"),
            ("check run queued", make_pr(checks=[check_run("QUEUED", None), check_run()]), " checks-running=1"),
            ("status context pending", make_pr(checks=[status_context("PENDING")]), " checks-running=1"),
            ("status context expected", make_pr(checks=[status_context("EXPECTED")]), " checks-running=1"),
            ("check run failure", make_pr(checks=[check_run(conclusion="FAILURE")]), " checks-failed=1"),
            ("status context failure", make_pr(checks=[status_context("FAILURE")]), " checks-failed=1"),
            ("status context error", make_pr(checks=[status_context("ERROR")]), " checks-failed=1"),
            ("check run neutral passes", make_pr(checks=[check_run(conclusion="NEUTRAL")]), ""),
            ("check run skipped passes", make_pr(checks=[check_run(conclusion="SKIPPED")]), ""),
            ("status context success passes", make_pr(checks=[status_context("SUCCESS")]), ""),
            (
                "mixed counts",
                make_pr(checks=[check_run("IN_PROGRESS", None), check_run(conclusion="FAILURE"), check_run()]),
                " checks-running=1 checks-failed=1",
            ),
            ("unresolved threads", make_pr(threads=[False, True, False]), " unresolved-threads=2"),
            ("resolved threads", make_pr(threads=[True, True]), ""),
            ("over 100 threads", make_pr(total_threads=150), " over-100-threads"),
            (
                "everything at once",
                make_pr(
                    draft=True,
                    merge="DIRTY",
                    checks=[check_run("IN_PROGRESS", None)],
                    threads=[False],
                    total_threads=101,
                ),
                " draft merge=DIRTY checks-running=1 unresolved-threads=1 over-100-threads",
            ),
        ]
        for label, pr, expected in cases:
            with self.subTest(label):
                self.assertEqual(summarize(pr).reasons(), expected)

    def test_missing_rollup_means_no_checks(self):
        pr = make_pr()
        pr["commits"]["nodes"][0]["commit"]["statusCheckRollup"] = None
        self.assertEqual(summarize(pr).reasons(), " no-checks")
        pr["commits"]["nodes"] = []
        self.assertEqual(summarize(pr).reasons(), " no-checks")

    def test_summary_carries_head_and_state(self):
        summary = summarize(make_pr(state="MERGED", head=HEAD_B))
        self.assertEqual((summary.state, summary.head), ("MERGED", HEAD_B))

    def test_equal_summaries_compare_equal(self):
        self.assertEqual(summarize(make_pr()), summarize(make_pr()))
        self.assertNotEqual(summarize(make_pr()), summarize(make_pr(head=HEAD_B)))


class StallTests(unittest.TestCase):
    def test_stalled_after_unchanged_signature(self):
        running = make_pr(checks=[check_run("IN_PROGRESS", None)])
        gh = FakeGh([PR7], [running] * 200)
        line, clock = run_watch(gh)
        self.assertEqual(line, "STALLED 7 head=aaaaaaa checks-running=1")
        # Stall is measured from the first poll that saw this signature.
        self.assertGreaterEqual(sum(clock.sleeps), STALL)
        self.assertLess(sum(clock.sleeps) - POLL, STALL)

    def test_stall_resets_when_signature_changes(self):
        one = make_pr(checks=[check_run("IN_PROGRESS", None)])
        two = make_pr(checks=[check_run("IN_PROGRESS", None), check_run("IN_PROGRESS", None)])
        # 120 polls span 4 hours, but no signature lasts 3.
        gh = FakeGh([PR7], [one] * 60 + [two] * 60 + [make_pr()] * 2)
        line, clock = run_watch(gh)
        self.assertEqual(line, f"READY 7 {HEAD_A}")
        self.assertGreater(sum(clock.sleeps), STALL)

    def test_no_stall_limit_before_pr_exists(self):
        gh = FakeGh([NO_PR] * 200 + [PR7], [make_pr()] * 2)
        line, clock = run_watch(gh)
        self.assertEqual(line, f"READY 7 {HEAD_A}")
        self.assertGreater(sum(clock.sleeps), STALL)

    def test_stall_timer_starts_when_pr_appears(self):
        running = make_pr(checks=[check_run("IN_PROGRESS", None)])
        gh = FakeGh([NO_PR] * 200 + [PR7], [running] * 200)
        line, clock = run_watch(gh)
        self.assertEqual(line, "STALLED 7 head=aaaaaaa checks-running=1")
        self.assertGreater(sum(clock.sleeps), 2 * STALL)


class ErrorTests(unittest.TestCase):
    def test_error_after_ten_failures(self):
        gh = FakeGh([PR7], [GhError("boom")] * 10)
        line, clock = run_watch(gh)
        self.assertEqual(line, "ERROR gh api graphql on PR 7: boom")
        self.assertEqual(gh.count(["api", "graphql"]), 10)
        self.assertEqual(len(clock.sleeps), 9)

    def test_pr_list_failures_also_count(self):
        gh = FakeGh([GhError("no login")])
        line, _ = run_watch(gh)
        self.assertEqual(line, "ERROR gh pr list: no login")
        self.assertEqual(gh.count(["pr", "list"]), 10)

    def test_success_resets_the_failure_count(self):
        polls = [GhError("x")] * 9 + [make_pr()] + [GhError("x")] * 9 + [make_pr()]
        line, _ = run_watch(FakeGh([PR7], polls))
        self.assertEqual(line, f"READY 7 {HEAD_A}")

    def test_empty_output_is_a_failure(self):
        line, _ = run_watch(FakeGh([PR7], [""] * 10))
        self.assertTrue(line.startswith("ERROR gh api graphql on PR 7:"), line)

    def test_unparseable_output_is_a_failure(self):
        line, _ = run_watch(FakeGh([PR7], ["not json"] * 10))
        self.assertTrue(line.startswith("ERROR gh api graphql on PR 7:"), line)

    def test_null_pull_request_is_a_failure(self):
        line, _ = run_watch(FakeGh([PR7], [json.dumps({"data": {"repository": {"pullRequest": None}}})] * 10))
        self.assertTrue(line.startswith("ERROR gh api graphql on PR 7:"), line)

    def test_once_error_on_first_failure(self):
        gh = FakeGh([GhError("boom")])
        line, clock = run_watch(gh, once=True)
        self.assertEqual(line, "ERROR gh pr list: boom")
        self.assertEqual(clock.sleeps, [])
        gh = FakeGh([PR7], [GhError("boom")])
        line, _ = run_watch(gh, once=True)
        self.assertEqual(line, "ERROR gh api graphql on PR 7: boom")


class OnceTests(unittest.TestCase):
    def test_once_not_ready_after_two_polls(self):
        running = make_pr(checks=[check_run("IN_PROGRESS", None)], threads=[False])
        gh = FakeGh([PR7], [running, running])
        line, clock = run_watch(gh, once=True)
        self.assertEqual(line, "NOT-READY 7 head=aaaaaaa checks-running=1 unresolved-threads=1")
        self.assertEqual(clock.sleeps, [30])

    def test_once_ready_after_two_clean_polls(self):
        line, clock = run_watch(FakeGh([PR7], [make_pr(), make_pr()]), once=True)
        self.assertEqual(line, f"READY 7 {HEAD_A}")
        self.assertEqual(clock.sleeps, [30])

    def test_once_no_pr(self):
        line, clock = run_watch(FakeGh([NO_PR]), once=True)
        self.assertEqual(line, "NOT-READY - no open PR from feat/x")
        self.assertEqual(clock.sleeps, [])


FAKE_GH = """#!{python}
import json, os, sys
with open(os.environ["FAKE_GH_DATA"]) as f:
    data = json.load(f)
if sys.argv[1:3] == ["pr", "list"]:
    print(json.dumps(data["pr_list"]))
elif sys.argv[1:3] == ["api", "graphql"]:
    print(json.dumps(data["graphql"]))
else:
    print("unexpected: " + " ".join(sys.argv[1:]), file=sys.stderr)
    sys.exit(1)
"""


class CliTests(unittest.TestCase):
    def run_cli(self, data, extra_env=None):
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = Path(tmp) / "bin"
            bin_dir.mkdir()
            gh = bin_dir / "gh"
            gh.write_text(FAKE_GH.format(python=sys.executable))
            gh.chmod(0o755)
            data_file = Path(tmp) / "data.json"
            data_file.write_text(json.dumps(data))
            env = {
                **os.environ,
                "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
                "FAKE_GH_DATA": str(data_file),
                "POLL_SECONDS": "0",
                **(extra_env or {}),
            }
            return subprocess.run(
                [sys.executable, wait_pr_ready.__file__, "owner/repo", "feat/x"],
                capture_output=True,
                text=True,
                env=env,
                timeout=60,
            )

    def test_cli_prints_one_line(self):
        data = {"pr_list": [{"number": 7}], "graphql": {"data": {"repository": {"pullRequest": make_pr()}}}}
        result = self.run_cli(data)
        self.assertEqual(result.stdout, f"READY 7 {HEAD_A}\n")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_cli_error_still_exits_zero(self):
        data = {"pr_list": [{"number": 7}], "graphql": None}
        result = self.run_cli(data, {"ONCE": "1"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(result.stdout.startswith("ERROR "), result.stdout)
        self.assertEqual(len(result.stdout.splitlines()), 1)


if __name__ == "__main__":
    unittest.main()
