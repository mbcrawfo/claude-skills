"""Tests for watch() over a scripted gh and the wait_pr_ready command line."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from fakes import HEAD_A, HEAD_B, NO_PR, PR7, FakeClock, FakeGh, check_run, make_pr
import helpers  # noqa: F401  (puts the scripts dir on sys.path)
from prstate import GhError
import wait_pr_ready
from wait_pr_ready import watch

STALL = 3 * 3600
POLL = 120


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
