import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest

from helpers import commit_file, git, make_repo, run
import review_state
from test_ledger import finding, output, report


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
        base = ["--base-ref", base_ref] if base_ref is not None else []
        return run(
            self.repo,
            "begin-round",
            "--mode",
            mode,
            "--model",
            "test-model",
            "--snapshot",
            snapshot,
            *base,
            *extra,
            state_dir=self.state_dir,
        )

    def record(self, findings=()):
        """Record a reviewer report for the latest round."""
        path = self.tmp / "report.md"
        path.write_text(report(output(findings=list(findings))))
        return run(self.repo, "record-findings", "--file", str(path), state_dir=self.state_dir)

    def locate(self, *extra, base="main"):
        args = ["--base", base] if base is not None else []
        return run(self.repo, "locate", *args, *extra, state_dir=self.state_dir)

    def review_json(self):
        return json.loads((review_state.state_path(self.state_dir, self.repo) / "review.json").read_text())

    def call(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = review_state.main([*argv, "--state-dir", str(self.state_dir), "--repo", str(self.repo)])
        return code, out.getvalue(), err.getvalue()

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
        self.assertEqual(git(self.repo, "show", result["snapshot"] + ":new.txt"), "fresh")
        self.assertEqual(git(self.repo, "show", result["snapshot"] + ":b.txt"), "changed")
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
        self.assertTrue(result["saved"])
        self.assertEqual(result["merge_base"], result["diff_from"])
        state = json.loads((Path(self.locate()["dir"]) / "review.json").read_text())
        self.assertEqual(len(state["rounds"]), 1)
        rnd = state["rounds"][0]
        self.assertEqual(rnd["head"], git(self.repo, "rev-parse", "HEAD"))
        self.assertEqual(rnd["merge_base"], git(self.repo, "merge-base", "HEAD", "main"))
        self.assertIsNone(rnd["summary"])
        self.assertIsNone(rnd["merge_risk"])
        self.assertEqual(state["findings"], [])
        self.assertTrue(state["created"].endswith("Z"))
        self.assertTrue(state["updated"].endswith("Z"))

    def test_begin_round_stores_intent(self):
        intent = self.tmp / "intent.txt"
        intent.write_text("Make b exist\n")
        self.begin("--intent-file", str(intent))
        state = json.loads((Path(self.locate()["dir"]) / "review.json").read_text())
        self.assertEqual(state["intent"].strip(), "Make b exist")

    def test_begin_round_on_base_branch_is_empty(self):
        git(self.repo, "checkout", "main")
        result = self.begin()
        self.assertTrue(result["empty"])
        self.assertFalse(result["saved"])
        self.assertEqual(result["changed_files"], [])

    def test_begin_round_followup_uses_previous_snapshot(self):
        first = self.begin()
        self.record()
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
                    "begin-round",
                    "--mode",
                    "followup",
                    "--model",
                    "m",
                    "--snapshot",
                    snap,
                    "--base-ref",
                    "main",
                    "--state-dir",
                    str(self.state_dir),
                    "--repo",
                    str(self.repo),
                ]
            )
        self.assertEqual(code, 1)
        self.assertEqual(len(err.getvalue().strip().splitlines()), 1)
        self.assertIn("no recorded round", err.getvalue())

    def test_begin_round_followup_after_unrecorded_round_fails(self):
        self.begin()
        snap = self.snapshot()["snapshot"]
        code, out, err = self.call("begin-round", "--mode", "followup", "--model", "m", "--snapshot", snap)
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("no recorded round", err)

    def test_locate_followup_after_commit(self):
        self.begin()
        self.record()
        commit_file(self.repo, "c.txt", "c\n", "add c")
        result = self.locate()
        self.assertEqual(result["mode"], "followup")
        self.assertEqual(result["rounds"], 1)

    def test_locate_deep_after_amend(self):
        self.begin()
        self.record()
        git(self.repo, "commit", "--amend", "-m", "rewritten")
        result = self.locate()
        self.assertEqual(result["mode"], "deep")
        self.assertEqual(result["reason"], "history-rewritten")

    def test_locate_deep_when_snapshot_gone(self):
        self.begin()
        self.record()
        path = Path(self.locate()["dir"]) / "review.json"
        state = json.loads(path.read_text())
        state["rounds"][-1]["snapshot"] = "0" * 40
        path.write_text(json.dumps(state))
        result = self.locate()
        self.assertEqual(result["mode"], "deep")
        self.assertEqual(result["reason"], "snapshot-missing")

    def test_oversized_flag(self):
        for i in range(151):
            (self.repo / f"f{i:03d}.txt").write_text("x\n")
        result = self.begin()
        self.assertEqual(result["files"], 152)
        self.assertTrue(result["oversized"])

    def test_not_oversized_at_threshold(self):
        for i in range(149):
            (self.repo / f"f{i:03d}.txt").write_text("x\n")
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
        saved = {
            k: os.environ.pop(k, None)
            for k in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL", "EMAIL")
        }
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

    # unrecorded rounds
    def test_unrecorded_round_does_not_anchor_followup(self):
        self.begin()  # the reviewer never reported, so record-findings never ran
        commit_file(self.repo, "c.txt", "c\n", "add c")
        result = self.locate()
        self.assertEqual((result["mode"], result["reason"]), ("deep", "no-rounds"))
        self.assertEqual(result["rounds"], 0)

    def test_unrecorded_round_is_dropped_by_next_begin(self):
        first = self.begin()
        self.record()
        self.begin(mode="followup")  # never recorded
        commit_file(self.repo, "c.txt", "c\n", "add c")
        located = self.locate()
        self.assertEqual((located["mode"], located["reason"]), ("followup", "continuing"))
        self.assertEqual(located["rounds"], 1)
        again = self.begin(mode="followup")
        self.assertEqual(again["number"], 2)
        self.assertEqual(again["diff_from"], first["diff_to"])
        self.assertEqual(again["changed_files"], ["c.txt"])
        self.assertEqual([r["number"] for r in self.review_json()["rounds"]], [1, 2])

    def test_empty_first_round_is_not_saved(self):
        git(self.repo, "checkout", "-b", "feature/empty", "main")
        result = self.begin()
        self.assertTrue(result["empty"])
        self.assertFalse(result["saved"])
        self.assertFalse((review_state.state_path(self.state_dir, self.repo) / "review.json").exists())
        commit_file(self.repo, "c.txt", "c\n", "add c")
        located = self.locate()
        self.assertEqual((located["mode"], located["reason"]), ("deep", "no-state"))
        self.assertEqual(self.begin()["changed_files"], ["c.txt"])

    def test_empty_followup_round_is_not_saved(self):
        self.begin()
        self.record()
        commit_file(self.repo, "c.txt", "c\n", "add c")
        self.begin(mode="followup")  # never recorded
        git(self.repo, "reset", "--hard", "HEAD~1")
        result = self.begin(mode="followup")
        self.assertTrue(result["empty"])
        self.assertFalse(result["saved"])
        self.assertEqual(result["number"], 2)
        rounds = self.review_json()["rounds"]
        self.assertEqual([r["number"] for r in rounds], [1])  # the unrecorded round is dropped too
        self.assertEqual(run(self.repo, "status", state_dir=self.state_dir)["reason"], "converged")

    # base ref
    def test_locate_and_begin_round_reuse_stored_base_ref(self):
        git(self.repo, "branch", "develop", "main")
        self.begin(base_ref="develop")
        self.record()
        self.assertEqual(self.locate(base=None)["base"], "develop")
        self.assertEqual(self.locate(base="main")["base"], "main")
        result = self.begin(mode="followup", base_ref=None)
        self.assertEqual(result["base_ref"], "develop")
        self.assertEqual(self.review_json()["base_ref"], "develop")

    def test_begin_round_without_base_ref_or_state_uses_default_base(self):
        result = self.begin(base_ref=None)
        self.assertEqual(result["base_ref"], "main")
        self.assertEqual(result["diff_from"], git(self.repo, "merge-base", "HEAD", "main"))

    # base moved
    def test_locate_deep_when_base_merged_into_branch(self):
        self.begin()
        self.record(findings=[finding()])
        git(self.repo, "checkout", "main")
        commit_file(self.repo, "upstream.txt", "u\n", "upstream work")
        git(self.repo, "checkout", "feature/x")
        git(self.repo, "merge", "--no-edit", "main")
        result = self.locate()
        self.assertEqual((result["mode"], result["reason"]), ("deep", "base-moved"))
        self.assertEqual([f["id"] for f in result["open_findings"]], ["R1-F1"])
        deep = self.begin()
        self.assertEqual(deep["changed_files"], ["b.txt"])  # upstream.txt is not part of the change

    def test_locate_followup_when_base_advances_without_merge(self):
        self.begin()
        self.record()
        git(self.repo, "checkout", "main")
        commit_file(self.repo, "upstream.txt", "u\n", "upstream work")
        git(self.repo, "checkout", "feature/x")
        commit_file(self.repo, "c.txt", "c\n", "add c")
        result = self.locate()
        self.assertEqual((result["mode"], result["reason"]), ("followup", "continuing"))

    # paths
    def test_locate_deep_when_paths_change(self):
        (self.repo / "src").mkdir()
        (self.repo / "src" / "keep.py").write_text("k\n")
        self.begin("--paths", "src/*")
        self.record(findings=[finding()])
        same = self.locate("--paths", "./src/*")
        self.assertEqual((same["mode"], same["reason"]), ("followup", "continuing"))
        for extra in ((), ("--paths", "lib/*"), ("--paths", "src/*", "lib/*")):
            with self.subTest(paths=extra):
                result = self.locate(*extra)
                self.assertEqual((result["mode"], result["reason"]), ("deep", "paths-changed"))
                self.assertEqual([f["id"] for f in result["open_findings"]], ["R1-F1"])

    def test_paths_directory_forms_match_nested_files(self):
        (self.repo / "src" / "a").mkdir(parents=True)
        (self.repo / "src" / "a" / "b.py").write_text("b\n")
        (self.repo / "srcfile.py").write_text("s\n")
        for pattern in ("src/", "src", "./src/"):
            with self.subTest(pattern=pattern):
                result = self.begin("--paths", pattern)
                self.assertEqual(result["changed_files"], ["src/a/b.py"])
                self.assertEqual(result["paths"], ["src/**"])
        self.record()
        for pattern in ("src", "src/", "./src"):
            with self.subTest(locate=pattern):
                result = self.locate("--paths", pattern)
                self.assertEqual((result["mode"], result["reason"]), ("followup", "continuing"))


if __name__ == "__main__":
    unittest.main()
