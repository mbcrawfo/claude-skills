"""Tests for PR reading: summarize() over GraphQL objects, poll_pr() over a scripted gh and check_once()."""

from __future__ import annotations

import json
import unittest

from fakes import HEAD_A, HEAD_B, NO_PR, PR7, FakeClock, FakeGh, check_run, make_pr, status_context
import helpers  # noqa: F401  (puts the scripts dir on sys.path)
from prstate import GhError, branch_head, check_once, poll_pr, summarize


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

    def test_conflict_property(self):
        self.assertTrue(summarize(make_pr(merge="DIRTY")).conflict)
        self.assertTrue(summarize(make_pr(merge="CONFLICTING")).conflict)
        self.assertFalse(summarize(make_pr(merge="BEHIND")).conflict)


class PollPrTests(unittest.TestCase):
    def poll(self, answer):
        return poll_pr("owner/repo", 7, FakeGh([PR7], [answer]))

    def test_empty_output_is_a_failure(self):
        with self.assertRaises(GhError):
            self.poll("")

    def test_unparseable_output_is_a_failure(self):
        with self.assertRaises(GhError):
            self.poll("not json")

    def test_null_pull_request_is_a_failure(self):
        with self.assertRaises(GhError):
            self.poll(json.dumps({"data": {"repository": {"pullRequest": None}}}))

    def test_malformed_rollup_is_a_failure(self):
        pr = make_pr()
        pr["commits"]["nodes"][0]["commit"]["statusCheckRollup"] = "bad"
        with self.assertRaises(GhError):
            self.poll(pr)

    def test_null_head_ref_oid_is_a_failure(self):
        with self.assertRaises(GhError):
            self.poll(make_pr(head=None))


class BranchHeadTests(unittest.TestCase):
    def test_returns_the_sha_and_asks_the_branches_api(self):
        gh = FakeGh([PR7], branches={"integration/x": [HEAD_A + "\n"]})
        self.assertEqual(branch_head("owner/repo", "integration/x", gh), HEAD_A)
        self.assertEqual(gh.calls, [["api", "repos/owner/repo/branches/integration/x", "--jq", ".commit.sha"]])

    def test_a_failing_call_is_a_failure(self):
        gh = FakeGh([PR7], branches={"integration/x": [GhError("Not Found")]})
        with self.assertRaises(GhError):
            branch_head("owner/repo", "integration/x", gh)

    def test_an_answer_that_is_not_a_sha_is_a_failure(self):
        for answer in ("Not Found", "", "abc123", HEAD_A + "0", HEAD_A.upper()):
            with self.subTest(answer=answer):
                gh = FakeGh([PR7], branches={"integration/x": [answer]})
                with self.assertRaises(GhError):
                    branch_head("owner/repo", "integration/x", gh)


class CheckOnceTests(unittest.TestCase):
    def test_check_once_ready_after_two_clean_polls(self):
        gh = FakeGh([PR7], [make_pr(), make_pr()])
        clock = FakeClock()
        self.assertEqual(check_once("owner/repo", "feat/x", gh=gh, sleep=clock.sleep), f"READY 7 {HEAD_A}")
        self.assertEqual(clock.sleeps, [30])

    def test_check_once_no_pr(self):
        gh = FakeGh([NO_PR])
        self.assertEqual(
            check_once("owner/repo", "feat/x", gh=gh, sleep=FakeClock().sleep), "NOT-READY - no open PR from feat/x"
        )

    def test_check_once_not_ready_reports_reasons_after_two_polls(self):
        running = make_pr(checks=[check_run("IN_PROGRESS", None)], threads=[False])
        clock = FakeClock()
        line = check_once("owner/repo", "feat/x", gh=FakeGh([PR7], [running, running]), sleep=clock.sleep)
        self.assertEqual(line, "NOT-READY 7 head=aaaaaaa checks-running=1 unresolved-threads=1")
        self.assertEqual(clock.sleeps, [30])

    def test_check_once_merged_and_closed(self):
        for state in ("MERGED", "CLOSED"):
            with self.subTest(state=state):
                gh = FakeGh([PR7], [make_pr(state=state)])
                self.assertEqual(check_once("owner/repo", "feat/x", gh=gh, sleep=FakeClock().sleep), f"{state} 7")

    def test_check_once_behind_base_is_not_ready(self):
        gh = FakeGh([PR7], [make_pr(merge="BEHIND")] * 2)
        line = check_once("owner/repo", "feat/x", gh=gh, sleep=FakeClock().sleep)
        self.assertEqual(line, "NOT-READY 7 head=aaaaaaa merge=BEHIND")

    def test_check_once_confirm_seconds_sets_the_gap(self):
        clock = FakeClock()
        check_once("owner/repo", "feat/x", gh=FakeGh([PR7], [make_pr()] * 2), sleep=clock.sleep, confirm_seconds=5)
        self.assertEqual(clock.sleeps, [5])

    def test_check_once_error_on_first_failure_does_not_sleep(self):
        clock = FakeClock()
        line = check_once("owner/repo", "feat/x", gh=FakeGh([PR7], [GhError("boom")]), sleep=clock.sleep)
        self.assertEqual(line, "ERROR gh api graphql on PR 7: boom")
        self.assertEqual(clock.sleeps, [])

    def test_check_once_errors_name_the_failing_call(self):
        line = check_once("owner/repo", "feat/x", gh=FakeGh([GhError("boom")]), sleep=FakeClock().sleep)
        self.assertEqual(line, "ERROR gh pr list: boom")
        line = check_once("owner/repo", "feat/x", gh=FakeGh([PR7], [GhError("boom")]), sleep=FakeClock().sleep)
        self.assertEqual(line, "ERROR gh api graphql on PR 7: boom")
        line = check_once("owner/repo", "feat/x", gh=FakeGh([PR7], [make_pr(head=None)]), sleep=FakeClock().sleep)
        self.assertTrue(line.startswith("ERROR gh api graphql on PR 7:"), line)


if __name__ == "__main__":
    unittest.main()
