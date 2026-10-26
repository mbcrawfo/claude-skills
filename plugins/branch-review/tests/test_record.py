import contextlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from helpers import commit_file, finding, git, make_repo, output, report, run
import review_state


class RecordTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.state_dir = self.tmp / "state"
        self.repo = make_repo(self.tmp / "r")

    def cli(self, *argv):
        return run(self.repo, *argv, state_dir=self.state_dir)

    def call(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = review_state.main([*argv, "--state-dir", str(self.state_dir), "--repo", str(self.repo)])
        return code, out.getvalue(), err.getvalue()

    def review(self, findings=(), *extra, mode="deep"):
        """Snapshot the working tree, begin a round on it and record a report."""
        snapshot = self.cli("snapshot")["snapshot"]
        self.cli(
            "begin-round",
            "--mode",
            mode,
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

    def publish(self):
        git(self.repo, "update-ref", "refs/remotes/origin/feature/x", "HEAD")

    def reason(self, text="Merged from the integration branch."):
        path = self.tmp / "reason.txt"
        path.write_text(text)
        return str(path)

    def record(self, *extra):
        return self.cli("record", "--reason-file", self.reason(), "--base", "main", *extra)

    def state(self):
        directory = review_state.state_path(self.state_dir, self.repo)
        return json.loads((directory / "review.json").read_text())

    def test_records_a_published_commit(self):
        self.publish()
        result = self.record()
        head = git(self.repo, "rev-parse", "HEAD")
        self.assertTrue(result["recorded"])
        self.assertEqual(result["round"], 1)
        self.assertEqual(result["commit"], head)
        self.assertEqual(result["branch"], "feature/x")
        self.assertEqual(result["files"], 1)
        rnd = self.state()["rounds"][0]
        self.assertEqual(rnd["mode"], "recorded")
        self.assertEqual(rnd["snapshot"], head)
        self.assertEqual(rnd["head"], head)
        self.assertEqual(rnd["diff_to"], head)
        self.assertEqual(rnd["diff_from"], git(self.repo, "rev-parse", "main"))
        self.assertEqual(rnd["changed_files"], ["b.txt"])
        self.assertEqual(rnd["paths"], [])
        self.assertEqual(rnd["added"], [])
        self.assertEqual(rnd["mode_reason"], "recorded")
        self.assertEqual(rnd["reason"], "Merged from the integration branch.")
        for key in ("model", "summary", "merge_risk", "focus"):
            self.assertIsNone(rnd[key])
        for key in ("started", "base_ref", "merge_base", "plugin_version", "intent"):
            self.assertIn(key, rnd)
        covered = self.cli("covered", "--base", "main")
        self.assertTrue(covered["covered"], covered)

    def test_first_record_saves_an_empty_change(self):
        git(self.repo, "reset", "--hard", "main")
        self.publish()
        result = self.record()
        self.assertTrue(result["recorded"])
        self.assertEqual(result["files"], 0)
        self.assertEqual(result["round"], 1)
        self.assertTrue(self.cli("covered", "--base", "main")["covered"])

    def test_refuses_a_commit_not_on_the_remote_ref(self):
        self.publish()
        commit_file(self.repo, "c.txt", "c\n", "add c")
        code, out, err = self.call("record", "--reason-file", self.reason(), "--base", "main")
        self.assertEqual(code, 1)
        self.assertIn("not on origin/feature/x", err)
        self.assertEqual(out, "")

    def test_refuses_without_a_remote_ref(self):
        code, _, err = self.call("record", "--reason-file", self.reason(), "--base", "main")
        self.assertEqual(code, 1)
        self.assertIn("origin/feature/x", err)

    def test_refuses_an_empty_reason(self):
        self.publish()
        path = self.reason("  \n\t\n")
        code, _, err = self.call("record", "--reason-file", path, "--base", "main")
        self.assertEqual(code, 1)
        self.assertIn("reason", err)

    def test_refuses_a_detached_head(self):
        self.publish()
        git(self.repo, "checkout", "--detach")
        code, _, err = self.call("record", "--reason-file", self.reason(), "--base", "main")
        self.assertEqual(code, 1)
        self.assertIn("detached HEAD", err)

    def test_no_op_at_the_anchor_head(self):
        self.publish()
        self.assertTrue(self.record()["recorded"])
        second = self.record()
        self.assertFalse(second["recorded"])
        self.assertIsNone(second["round"])
        self.assertEqual(len(self.state()["rounds"]), 1)

    def test_keeps_open_findings(self):
        self.review([finding()])
        commit_file(self.repo, "c.txt", "c\n", "add c")
        self.publish()
        self.assertTrue(self.record()["recorded"])
        result = self.cli("covered", "--base", "main")
        self.assertIn("open-findings", result["reasons"])
        self.assertFalse(result["covered"])

    def test_clean_base_merge_after_record_is_covered(self):
        self.publish()
        self.record()
        git(self.repo, "checkout", "main")
        commit_file(self.repo, "upstream.txt", "u\n", "upstream work")
        git(self.repo, "checkout", "feature/x")
        git(self.repo, "merge", "--no-edit", "main")
        result = self.cli("covered", "--base", "main")
        self.assertEqual(result["reasons"], [])
        self.assertTrue(result["covered"])

    def test_conflicting_base_merge_after_record_is_merge_check(self):
        commit_file(self.repo, "a.txt", "branch\n", "branch edits a")
        self.publish()
        self.record()
        git(self.repo, "checkout", "main")
        commit_file(self.repo, "a.txt", "main\n", "main edits a")
        git(self.repo, "checkout", "feature/x")
        merge = subprocess.run(["git", "merge", "main"], cwd=str(self.repo), capture_output=True, text=True)
        self.assertNotEqual(merge.returncode, 0)
        (self.repo / "a.txt").write_text("resolved\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "--no-edit")
        self.assertEqual(self.cli("locate", "--base", "main")["mode"], "merge-check")
        snapshot = self.cli("snapshot")["snapshot"]
        begun = self.cli(
            "begin-round", "--mode", "merge-check", "--model", "m", "--snapshot", snapshot, "--base-ref", "main"
        )
        self.assertEqual(begun["changed_files"], ["a.txt"])

    def test_record_starts_from_previous_anchor(self):
        self.review()
        first = self.state()["rounds"][0]["snapshot"]
        self.publish()
        commit_file(self.repo, "c.txt", "c\n", "add c")
        self.publish()
        result = self.record()
        self.assertEqual(result["round"], 2)
        self.assertEqual(result["files"], 1)
        rnd = self.state()["rounds"][1]
        self.assertEqual(rnd["diff_from"], first)
        self.assertEqual(rnd["changed_files"], ["c.txt"])


if __name__ == "__main__":
    unittest.main()
