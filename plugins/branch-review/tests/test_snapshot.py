import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path

from helpers import commit_file, git, make_repo, run
import review_state


class SnapshotTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.state_dir = self.tmp / "state"
        self.repo = make_repo(self.tmp / "r")

    def snapshot(self):
        return run(self.repo, "snapshot", state_dir=self.state_dir)

    def begin(self, *extra, mode="deep", snapshot=None, base_ref="main"):
        if snapshot is None:
            snapshot = self.snapshot()["snapshot"]
        return run(
            self.repo,
            "begin-round",
            "--mode", mode,
            "--model", "test-model",
            "--snapshot", snapshot,
            "--base-ref", base_ref,
            *extra,
            state_dir=self.state_dir,
        )

    def locate(self):
        return run(self.repo, "locate", "--base", "main", state_dir=self.state_dir)

    def test_snapshot_clean_tree_is_head(self):
        result = self.snapshot()
        self.assertEqual(result["snapshot"], git(self.repo, "rev-parse", "HEAD"))
        self.assertFalse(result["dirty"])

    def test_snapshot_includes_untracked_file(self):
        (self.repo / "new.txt").write_text("fresh\n")
        (self.repo / "b.txt").write_text("changed\n")
        status_before = git(self.repo, "status", "--porcelain")
        refs_before = git(self.repo, "for-each-ref")
        result = self.snapshot()
        self.assertTrue(result["dirty"])
        self.assertNotEqual(result["snapshot"], git(self.repo, "rev-parse", "HEAD"))
        self.assertEqual(
            git(self.repo, "show", result["snapshot"] + ":new.txt"), "fresh"
        )
        self.assertEqual(
            git(self.repo, "show", result["snapshot"] + ":b.txt"), "changed"
        )
        self.assertEqual(git(self.repo, "status", "--porcelain"), status_before)
        self.assertEqual(git(self.repo, "for-each-ref"), refs_before)

    def test_snapshot_honors_gitignore(self):
        commit_file(self.repo, ".gitignore", "ignored.txt\n", "ignore")
        (self.repo / "ignored.txt").write_text("x\n")
        (self.repo / "kept.txt").write_text("y\n")
        result = self.snapshot()
        tree = git(self.repo, "ls-tree", "--name-only", result["snapshot"])
        self.assertIn("kept.txt", tree.splitlines())
        self.assertNotIn("ignored.txt", tree.splitlines())

    def test_begin_round_deep_uses_merge_base(self):
        result = self.begin()
        self.assertEqual(result["number"], 1)
        self.assertEqual(result["diff_from"], git(self.repo, "merge-base", "HEAD", "main"))
        self.assertEqual(result["changed_files"], ["b.txt"])
        self.assertEqual(result["files"], 1)
        self.assertEqual(result["lines"], 1)
        self.assertFalse(result["oversized"])
        self.assertFalse(result["empty"])
        state = json.loads(
            (Path(self.locate()["dir"]) / "review.json").read_text()
        )
        self.assertEqual(len(state["rounds"]), 1)
        rnd = state["rounds"][0]
        self.assertEqual(rnd["head"], git(self.repo, "rev-parse", "HEAD"))
        self.assertIsNone(rnd["summary"])
        self.assertIsNone(rnd["merge_risk"])
        self.assertEqual(state["findings"], [])
        self.assertTrue(state["created"].endswith("Z"))
        self.assertTrue(state["updated"].endswith("Z"))

    def test_begin_round_stores_intent(self):
        intent = self.tmp / "intent.txt"
        intent.write_text("Make b exist\n")
        self.begin("--intent-file", str(intent))
        state = json.loads(
            (Path(self.locate()["dir"]) / "review.json").read_text()
        )
        self.assertEqual(state["intent"].strip(), "Make b exist")

    def test_begin_round_on_base_branch_is_empty(self):
        git(self.repo, "checkout", "main")
        result = self.begin()
        self.assertTrue(result["empty"])
        self.assertEqual(result["changed_files"], [])

    def test_begin_round_followup_uses_previous_snapshot(self):
        first = self.begin()
        (self.repo / "c.txt").write_text("c\n")
        snap = self.snapshot()["snapshot"]
        second = self.begin(mode="followup", snapshot=snap)
        self.assertEqual(second["number"], 2)
        self.assertEqual(second["diff_from"], first["diff_to"])
        self.assertEqual(second["diff_to"], snap)
        self.assertEqual(second["changed_files"], ["c.txt"])

    def test_begin_round_followup_without_previous_round_fails(self):
        snap = self.snapshot()["snapshot"]
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = review_state.main(
                [
                    "begin-round", "--mode", "followup", "--model", "m",
                    "--snapshot", snap, "--base-ref", "main",
                    "--state-dir", str(self.state_dir), "--repo", str(self.repo),
                ]
            )
        self.assertEqual(code, 1)
        self.assertEqual(len(err.getvalue().strip().splitlines()), 1)
        self.assertIn("no previous round", err.getvalue())

    def test_locate_followup_after_commit(self):
        self.begin()
        commit_file(self.repo, "c.txt", "c\n", "add c")
        result = self.locate()
        self.assertEqual(result["mode"], "followup")
        self.assertEqual(result["rounds"], 1)

    def test_locate_deep_after_amend(self):
        self.begin()
        git(self.repo, "commit", "--amend", "-m", "rewritten")
        result = self.locate()
        self.assertEqual(result["mode"], "deep")
        self.assertEqual(result["reason"], "history-rewritten")

    def test_locate_deep_when_snapshot_gone(self):
        self.begin()
        path = Path(self.locate()["dir"]) / "review.json"
        state = json.loads(path.read_text())
        state["rounds"][-1]["snapshot"] = "0" * 40
        path.write_text(json.dumps(state))
        result = self.locate()
        self.assertEqual(result["mode"], "deep")
        self.assertEqual(result["reason"], "snapshot-missing")

    def test_oversized_flag(self):
        for i in range(151):
            (self.repo / ("f%03d.txt" % i)).write_text("x\n")
        result = self.begin()
        self.assertEqual(result["files"], 152)
        self.assertTrue(result["oversized"])

    def test_not_oversized_at_threshold(self):
        for i in range(149):
            (self.repo / ("f%03d.txt" % i)).write_text("x\n")
        result = self.begin()
        self.assertEqual(result["files"], 150)
        self.assertFalse(result["oversized"])

    def test_lines_respect_paths_filter(self):
        (self.repo / "src").mkdir()
        (self.repo / "src" / "keep.py").write_text("k\n" * 3)
        (self.repo / "big.txt").write_text("x\n" * 10001)
        (self.repo / "blob.bin").write_bytes(b"\x00\x01\x02" * 10)
        everything = self.begin()
        self.assertTrue(everything["oversized"])
        narrowed = self.begin("--paths", "src/*", "*.bin")
        self.assertEqual(narrowed["changed_files"], ["blob.bin", "src/keep.py"])
        self.assertEqual(narrowed["files"], 2)
        self.assertEqual(narrowed["lines"], 3)  # binary counts 0
        self.assertFalse(narrowed["oversized"])

    def test_snapshot_ignores_gpg_signing_and_missing_identity(self):
        git(self.repo, "config", "commit.gpgSign", "true")
        git(self.repo, "config", "gpg.program", "/nonexistent/gpg")
        (self.repo / "new.txt").write_text("fresh\n")
        result = self.snapshot()
        self.assertTrue(result["dirty"])
        obj = git(self.repo, "cat-file", "-p", result["snapshot"])
        self.assertNotIn("gpgsig", obj)

    def test_snapshot_without_git_identity(self):
        git(self.repo, "config", "--unset", "user.name")
        git(self.repo, "config", "--unset", "user.email")
        (self.repo / "new.txt").write_text("fresh\n")
        saved = {k: os.environ.pop(k, None) for k in (
            "GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL",
            "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL", "EMAIL")}
        os.environ["GIT_CONFIG_NOSYSTEM"] = "1"
        try:
            result = self.snapshot()
        finally:
            for k, v in saved.items():
                if v is not None:
                    os.environ[k] = v
        self.assertTrue(result["dirty"])

    def test_paths_filter(self):
        (self.repo / "src").mkdir()
        (self.repo / "src" / "keep.py").write_text("k\n")
        result = self.begin("--paths", "src/*")
        self.assertEqual(result["changed_files"], ["src/keep.py"])
        self.assertEqual(result["files"], 1)


if __name__ == "__main__":
    unittest.main()
