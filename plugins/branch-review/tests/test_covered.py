import contextlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from helpers import commit_file, git, make_repo, run
import review_state
from test_ledger import finding, output, report


class CoveredTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.state_dir = self.tmp / "state"
        self.repo = make_repo(self.tmp / "r")

    def cli(self, *argv):
        return run(self.repo, *argv, state_dir=self.state_dir)

    def review(self, findings=(), *extra):
        """Snapshot the working tree, begin a deep round on it and record a report."""
        snapshot = self.cli("snapshot")["snapshot"]
        self.cli(
            "begin-round",
            "--mode",
            "deep",
            "--model",
            "test-model",
            "--snapshot",
            snapshot,
            "--base-ref",
            "main",
            *extra,
        )
        path = self.tmp / "report.md"
        path.write_text(report(output(findings=list(findings))))
        self.cli("record-findings", "--file", str(path))

    def covered(self, *extra):
        return self.cli("covered", *extra)

    def assert_covered(self, result, reasons):
        self.assertEqual(result["reasons"], reasons)
        self.assertEqual(result["covered"], not reasons)

    def test_no_state_is_not_covered(self):
        result = self.covered()
        self.assert_covered(result, ["no-rounds"])
        self.assertEqual(result["rounds"], 0)
        self.assertEqual(result["commit"], git(self.repo, "rev-parse", "HEAD"))
        self.assertEqual(result["branch"], "feature/x")

    def test_begun_but_unrecorded_round_is_not_covered(self):
        snapshot = self.cli("snapshot")["snapshot"]
        self.cli("begin-round", "--mode", "deep", "--model", "m", "--snapshot", snapshot, "--base-ref", "main")
        self.assert_covered(self.covered(), ["no-rounds"])

    def test_clean_deep_round_covers_head(self):
        self.review()
        result = self.covered()
        self.assert_covered(result, [])
        self.assertTrue(result["covered"])
        self.assertEqual(result["rounds"], 1)
        self.assertEqual(result["counts"]["open"], 0)

    def test_open_finding_blocks(self):
        self.review([finding()])
        result = self.covered()
        self.assert_covered(result, ["open-findings"])
        self.assertEqual(result["counts"]["open"], 1)

    def test_still_open_finding_blocks(self):
        self.review([finding()])
        state_file = review_state.state_path(self.state_dir, self.repo) / "review.json"
        data = json.loads(state_file.read_text())
        data["findings"][0]["status"] = "still-open"
        state_file.write_text(json.dumps(data))
        result = self.covered()
        self.assert_covered(result, ["open-findings"])
        self.assertEqual(result["counts"]["still-open"], 1)

    def test_fixed_unverified_blocks(self):
        self.review([finding()])
        commit_file(self.repo, "b.txt", "fixed\n", "fix")
        self.cli("set-status", "--id", "R1-F1", "--status", "fixed", "--commit", git(self.repo, "rev-parse", "HEAD"))
        self.assert_covered(self.covered(), ["unverified-fixes", "changes-since-review"])

    def test_escalated_blocks(self):
        self.review([finding()])
        self.cli("set-status", "--id", "R1-F1", "--status", "escalated", "--note", "needs a decision")
        self.assert_covered(self.covered(), ["escalated"])

    def test_deferred_after_resolve_is_covered(self):
        self.review([finding()])
        self.cli("set-status", "--id", "R1-F1", "--status", "escalated", "--note", "needs a decision")
        self.cli("resolve", "--id", "R1-F1", "--decision", "defer", "--note", "later")
        result = self.covered()
        self.assert_covered(result, [])
        self.assertEqual(result["counts"]["deferred"], 1)

    def test_findings_closed_without_code_change_is_covered(self):
        self.review([finding()])
        self.cli("set-status", "--id", "R1-F1", "--status", "invalid", "--note", "not reachable")
        result = self.covered()
        self.assert_covered(result, [])
        self.assertEqual(result["counts"]["invalid"], 1)

    def test_new_commit_after_review_blocks(self):
        self.review()
        commit_file(self.repo, "c.txt", "c\n", "add c")
        self.assert_covered(self.covered(), ["changes-since-review"])

    def test_dirty_snapshot_then_committed_is_covered(self):
        (self.repo / "b.txt").write_text("edited\n")
        self.review()
        git(self.repo, "commit", "-am", "edit b")
        self.assert_covered(self.covered(), [])

    def test_clean_base_merge_is_covered(self):
        self.review()
        self.merge_main_cleanly()
        self.assert_covered(self.covered(), [])

    def test_clean_base_merge_plus_work_blocks(self):
        self.review()
        self.merge_main_cleanly()
        commit_file(self.repo, "c.txt", "c\n", "add c")
        self.assert_covered(self.covered(), ["changes-since-review"])

    def test_conflicting_base_merge_blocks(self):
        commit_file(self.repo, "a.txt", "branch\n", "branch edits a")
        self.review()
        git(self.repo, "checkout", "main")
        commit_file(self.repo, "a.txt", "main\n", "main edits a")
        git(self.repo, "checkout", "feature/x")
        merge = subprocess.run(["git", "merge", "main"], cwd=str(self.repo), capture_output=True, text=True)
        self.assertNotEqual(merge.returncode, 0)
        (self.repo / "a.txt").write_text("resolved\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "--no-edit")
        self.assert_covered(self.covered(), ["base-conflict"])

    def test_base_advanced_without_merge_is_covered(self):
        self.review()
        git(self.repo, "checkout", "main")
        commit_file(self.repo, "upstream.txt", "u\n", "upstream work")
        git(self.repo, "checkout", "feature/x")
        self.assert_covered(self.covered(), [])

    def test_rewritten_history_blocks(self):
        self.review()
        git(self.repo, "commit", "--amend", "--no-edit", "--date", "2001-01-01T00:00:00")
        self.assert_covered(self.covered(), ["history-rewritten"])

    def test_missing_snapshot_blocks(self):
        (self.repo / "b.txt").write_text("edited\n")
        self.review()
        git(self.repo, "commit", "-am", "edit b")
        snapshot = review_state.load_state(review_state.state_path(self.state_dir, self.repo))["rounds"][0]["snapshot"]
        self.assertNotEqual(snapshot, git(self.repo, "rev-parse", "HEAD"))
        git(self.repo, "reflog", "expire", "--expire=now", "--all")
        git(self.repo, "prune", "--expire=now")
        self.assert_covered(self.covered(), ["snapshot-missing"])

    def test_partial_scope_blocks(self):
        self.review([], "--paths", "b.txt")
        self.assert_covered(self.covered(), ["partial-scope"])

    def test_reasons_are_reported_in_table_order(self):
        commit_file(self.repo, "a.txt", "branch\n", "branch edits a")
        self.review([finding(), finding(title="Second problem.")], "--paths", "b.txt")
        self.cli("set-status", "--id", "R1-F1", "--status", "fixed", "--note", "done")
        self.cli("set-status", "--id", "R1-F2", "--status", "escalated", "--note", "ask")
        commit_file(self.repo, "c.txt", "c\n", "add c")
        self.assert_covered(
            self.covered(),
            ["unverified-fixes", "escalated", "partial-scope", "changes-since-review"],
        )

    def test_commit_option_judges_that_commit_not_worktree(self):
        self.review()
        reviewed = git(self.repo, "rev-parse", "HEAD")
        (self.repo / "b.txt").write_text("dirty\n")
        (self.repo / "untracked.txt").write_text("u\n")
        result = self.covered("--commit", reviewed)
        self.assert_covered(result, [])
        self.assertEqual(result["commit"], reviewed)

    def test_commit_option_accepts_revision_and_reports_full_sha(self):
        self.review()
        reviewed = git(self.repo, "rev-parse", "HEAD")
        commit_file(self.repo, "c.txt", "c\n", "add c")
        result = self.covered("--commit", "HEAD~1")
        self.assert_covered(result, [])
        self.assertEqual(result["commit"], reviewed)

    def test_base_option_overrides_stored_base(self):
        self.review()
        self.assert_covered(self.covered("--base", "main"), [])
        # An unresolvable base cannot prove the merge base is unchanged.
        self.assert_covered(self.covered("--base", "no-such-ref"), ["base-conflict"])

    def test_unknown_commit_errors(self):
        self.review()
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = review_state.main(
                ["covered", "--commit", "deadbeef", "--state-dir", str(self.state_dir), "--repo", str(self.repo)]
            )
        self.assertEqual(code, 1)
        self.assertIn("commit not found: deadbeef", err.getvalue())

    def merge_main_cleanly(self):
        git(self.repo, "checkout", "main")
        commit_file(self.repo, "upstream.txt", "u\n", "upstream work")
        git(self.repo, "checkout", "feature/x")
        git(self.repo, "merge", "--no-edit", "main")


if __name__ == "__main__":
    unittest.main()
