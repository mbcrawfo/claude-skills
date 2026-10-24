import subprocess
import unittest
from unittest import mock

from helpers import LedgerHarness, commit_file, git, output
import review_state


class GeneratedFilesTest(LedgerHarness, unittest.TestCase):
    def deep_round(self):
        """Record a first deep round so follow-ups are possible."""
        self.begin()
        self.record(output())

    def followup(self, *extra, model="sonnet"):
        snap = self.cli("snapshot")["snapshot"]
        return self.cli(
            "begin-round",
            "--mode",
            "followup",
            "--model",
            model,
            "--snapshot",
            snap,
            "--base-ref",
            "main",
            *extra,
        )

    def add_files(self, directory, count, text="x\n"):
        for i in range(count):
            path = self.repo / directory / f"f{i:03d}.txt"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)

    def test_linguist_generated_excluded_from_counts(self):
        commit_file(self.repo, ".gitattributes", "fixtures/** linguist-generated\n", "mark fixtures")
        self.deep_round()
        self.add_files("fixtures", 50)
        (self.repo / "src.py").write_text("print(1)\n")
        result = self.followup()
        self.assertEqual(result["mode"], "followup")
        self.assertEqual(result["counted_files"], 1)
        self.assertEqual(result["counted_lines"], 1)
        self.assertEqual(result["files"], 51)
        self.assertEqual(len(result["changed_files"]), 51)
        self.assertEqual(len(result["generated"]), 50)
        self.assertNotIn("src.py", result["generated"])

    def test_diff_unset_counts_as_generated(self):
        commit_file(self.repo, ".gitattributes", "*.snap -diff\n", "mark snaps")
        self.deep_round()
        self.add_files("snaps", 3)
        (self.repo / "snaps" / "a.snap").write_text("s\n")
        result = self.followup()
        self.assertEqual(result["generated"], ["snaps/a.snap"])
        self.assertEqual(result["counted_files"], 3)

    def test_followup_with_large_delta_is_promoted(self):
        self.deep_round()
        self.add_files("src", 40)
        result = self.followup("--mode-reason", "continuing", "--deep-model", "opus")
        self.assertEqual(result["mode"], "deep")
        self.assertEqual(result["mode_reason"], "large-delta")
        self.assertEqual(result["model"], "opus")
        self.assertEqual(result["diff_from"], git(self.repo, "merge-base", "main", "HEAD"))
        self.assertEqual(result["counted_files"], 41)  # b.txt is back in the whole set
        self.assertEqual(sorted(result["changed_files"]), sorted(["b.txt"] + [f"src/f{i:03d}.txt" for i in range(40)]))
        saved = self.state()["rounds"][-1]
        self.assertEqual((saved["mode"], saved["mode_reason"], saved["model"]), ("deep", "large-delta", "opus"))
        self.assertEqual(saved["diff_from"], result["diff_from"])

    def test_deep_model_defaults_to_model(self):
        self.deep_round()
        self.add_files("src", 40)
        result = self.followup()
        self.assertEqual((result["mode"], result["model"]), ("deep", "sonnet"))

    def test_large_delta_by_lines(self):
        self.deep_round()
        (self.repo / "big.txt").write_text("x\n" * 2000)
        result = self.followup("--deep-model", "opus")
        self.assertEqual((result["mode"], result["mode_reason"]), ("deep", "large-delta"))

    def test_delta_below_thresholds_stays_followup(self):
        self.deep_round()
        self.add_files("src", 38)
        (self.repo / "big.txt").write_text("x\n" * 1000)
        result = self.followup("--mode-reason", "continuing", "--deep-model", "opus")
        self.assertEqual(result["mode"], "followup")
        self.assertEqual(result["mode_reason"], "continuing")
        self.assertEqual(result["model"], "sonnet")
        self.assertNotEqual(result["diff_from"], git(self.repo, "merge-base", "main", "HEAD"))

    def test_generated_lines_do_not_promote(self):
        self.deep_round()
        (self.repo / "big.txt").write_text("x\n" * 3000)
        result = self.followup("--generated", "big.txt")
        self.assertEqual(result["mode"], "followup")
        self.assertEqual(result["counted_lines"], 0)

    def test_generated_glob_is_stored_and_reused(self):
        self.begin("deep", "--generated", "gen/**")
        self.record(output())
        self.assertEqual(self.state()["generated"], ["gen/**"])
        self.add_files("gen", 3)
        (self.repo / "src.py").write_text("print(1)\n")
        result = self.followup()
        self.assertEqual(result["counted_files"], 1)
        self.assertEqual(result["generated"], ["gen/f000.txt", "gen/f001.txt", "gen/f002.txt"])
        self.assertEqual(self.state()["generated"], ["gen/**"])

    def test_generated_glob_is_replaced_when_given(self):
        self.begin("deep", "--generated", "gen/**")
        self.record(output())
        self.add_files("gen", 2)
        self.add_files("other", 2)
        result = self.followup("--generated", "other/**")
        self.assertEqual(result["generated"], ["other/f000.txt", "other/f001.txt"])
        self.assertEqual(self.state()["generated"], ["other/**"])

    def test_generated_defaults_to_empty(self):
        result = self.begin()
        self.assertEqual(result["generated"], [])
        self.assertEqual(self.state()["generated"], [])

    def test_generated_paths_with_spaces_and_unicode(self):
        commit_file(self.repo, ".gitattributes", "fixtures/** linguist-generated\n", "mark fixtures")
        self.deep_round()
        name = "fixtures/a b/é.json"
        path = self.repo / name
        path.parent.mkdir(parents=True)
        path.write_text("{}\n")
        (self.repo / "plain é.py").write_text("x = 1\n")
        result = self.followup()
        self.assertEqual(result["generated"], [name])
        self.assertEqual(result["counted_files"], 1)
        self.assertIn("plain é.py", result["changed_files"])

    def test_glob_matches_path_with_spaces_and_unicode(self):
        (self.repo / "out dir").mkdir()
        (self.repo / "out dir" / "ü b.txt").write_text("x\n")
        (self.repo / "x é.json").write_text("{}\n")
        first = self.begin("deep", "--generated", "out dir/**", "*é.json")
        self.assertEqual(first["generated"], ["out dir/ü b.txt", "x é.json"])
        self.record(output())
        (self.repo / "out dir" / "ü c.txt").write_text("x\n")
        (self.repo / "y é.json").write_text("{}\n")
        result = self.followup()
        self.assertEqual(result["generated"], ["out dir/ü c.txt", "y é.json"])

    def test_oversized_ignores_generated(self):
        commit_file(self.repo, ".gitattributes", "fixtures/** linguist-generated\n", "mark fixtures")
        self.add_files("fixtures", 160)
        (self.repo / "src.py").write_text("print(1)\n")
        result = self.begin()
        self.assertFalse(result["oversized"])
        self.assertEqual(result["counted_files"], 3)  # b.txt, .gitattributes, src.py
        self.assertGreater(result["files"], 160)

    def test_oversized_counts_non_generated(self):
        self.add_files("src", 151)
        self.assertTrue(self.begin()["oversized"])

    def test_attributes_fall_back_to_working_tree(self):
        commit_file(self.repo, ".gitattributes", "fixtures/** linguist-generated\n", "mark fixtures")
        self.deep_round()
        self.add_files("fixtures", 2)
        snap = self.cli("snapshot")["snapshot"]
        real = review_state._check_attr

        def without_source(repo, files, source):
            if source is not None:
                raise subprocess.CalledProcessError(129, ["git", "check-attr"])
            return real(repo, files, source)

        with mock.patch.object(review_state, "_check_attr", side_effect=without_source) as patched:
            found = review_state.generated_files(self.repo, snap, ["fixtures/f000.txt", "b.txt"], [])
        self.assertEqual(found, {"fixtures/f000.txt"})
        self.assertEqual([call.args[2] is None for call in patched.call_args_list], [False, True])

    def test_no_files_means_no_git_call(self):
        with mock.patch.object(review_state, "_check_attr") as patched:
            self.assertEqual(review_state.generated_files(self.repo, "HEAD", [], ["x"]), set())
        patched.assert_not_called()


if __name__ == "__main__":
    unittest.main()
