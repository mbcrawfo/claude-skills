from pathlib import Path
import tempfile
import unittest

from helpers import RESEARCH, SPECS, by_path, call, commit_all, git, make_repo, run, write


def facts(f):
    """(tracked, dirty, on_base, first_added) of one inventory entry."""
    return (f["tracked"], f["dirty"], f["on_base"], f["first_added"])


class GitStateTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def inventory(self, repo, *extra):
        return run("inventory", "--repo", str(repo), *extra)

    def test_base_from_origin_head(self):
        repo = make_repo(self.tmp / "one")
        bare = self.tmp / "origin.git"
        git(self.tmp, "clone", "--bare", "-q", str(repo), str(bare))
        git(repo, "remote", "add", "origin", str(bare))
        git(repo, "fetch", "-q", "origin")
        git(repo, "remote", "set-head", "origin", "main")
        self.assertEqual(self.inventory(repo)["base"], "origin/main")

    def test_base_falls_back_to_main_then_master_then_none(self):
        self.assertEqual(self.inventory(make_repo(self.tmp / "m"))["base"], "main")
        self.assertEqual(self.inventory(make_repo(self.tmp / "ms", branch="master"))["base"], "master")
        self.assertIsNone(self.inventory(make_repo(self.tmp / "t", branch="trunk"))["base"])

    def test_no_base_gives_null_on_base(self):
        repo = make_repo(self.tmp / "t", branch="trunk")
        write(repo, f"{SPECS}/2020-01-15-a.md", "# A\n")
        commit_all(repo, "a")
        self.assertIsNone(by_path(self.inventory(repo), f"{SPECS}/2020-01-15-a.md")["on_base"])

    def test_explicit_base(self):
        repo = make_repo(self.tmp / "e")
        git(repo, "branch", "release")
        self.assertEqual(self.inventory(repo, "--base", "release")["base"], "release")

    def test_explicit_base_must_exist(self):
        repo = make_repo(self.tmp / "e")
        code, out, err = call("inventory", "--repo", str(repo), "--base", "nope")
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertEqual(err.strip(), "error: unknown base ref: nope")

    def test_on_base_states(self):
        repo = make_repo(self.tmp / "s")
        a, b, c = (f"{SPECS}/2020-01-15-a.md", f"{SPECS}/2020-02-01-b.md", f"{SPECS}/2020-03-01-c.md")
        write(repo, a, "# A\n")
        commit_all(repo, "a", date="2020-01-15T12:00:00")
        git(repo, "checkout", "-q", "-b", "feature")
        write(repo, b, "# B\n")
        commit_all(repo, "b", date="2020-02-01T12:00:00")
        write(repo, a, "# A changed\n")
        write(repo, c, "# C\n")
        out = self.inventory(repo)
        self.assertEqual(facts(by_path(out, a)), (True, True, False, "2020-01-15"))
        self.assertEqual(facts(by_path(out, b)), (True, False, False, "2020-02-01"))
        self.assertEqual(facts(by_path(out, c)), (False, False, False, None))
        git(repo, "checkout", "--", a)
        fa = by_path(self.inventory(repo), a)
        self.assertEqual((fa["dirty"], fa["on_base"]), (False, True))

    def test_staged_new_file_is_tracked_but_not_on_base(self):
        repo = make_repo(self.tmp / "st")
        write(repo, f"{SPECS}/2020-01-15-a.md", "# A\n")
        git(repo, "add", "-A")
        self.assertEqual(facts(by_path(self.inventory(repo), f"{SPECS}/2020-01-15-a.md")), (True, True, False, None))

    def test_research_folder_tracked_only_when_every_file_is(self):
        repo = make_repo(self.tmp / "r")
        folder = f"{RESEARCH}/2020-01-10-limits"
        write(repo, f"{folder}/report.md", "# Limits\n")
        commit_all(repo, "research", date="2020-01-10T12:00:00")
        f = by_path(self.inventory(repo), folder)
        self.assertEqual((f["tracked"], f["on_base"], f["first_added"]), (True, True, "2020-01-10"))
        write(repo, f"{folder}/notes.md", "# Notes\n")
        f = by_path(self.inventory(repo), folder)
        self.assertEqual((f["tracked"], f["on_base"]), (False, False))

    def test_ignored_file_in_research_folder_does_not_untrack_it(self):
        repo = make_repo(self.tmp / "ig")
        folder = f"{RESEARCH}/2020-01-10-limits"
        write(repo, ".gitignore", "*.log\n")
        write(repo, f"{folder}/report.md", "# Limits\n")
        commit_all(repo, "research")
        write(repo, f"{folder}/debug.log", "noise\n")
        f = by_path(self.inventory(repo), folder)
        self.assertEqual((f["tracked"], f["on_base"]), (True, True))

    def test_glob_characters_are_literal(self):
        repo = make_repo(self.tmp / "g")
        write(repo, f"{SPECS}/what1.md", "# One\n")
        commit_all(repo, "one", date="2020-01-16T12:00:00")
        write(repo, f"{SPECS}/what[1].md", "# Bracket\n")
        f = by_path(self.inventory(repo), f"{SPECS}/what[1].md")
        self.assertEqual(facts(f), (False, False, False, None))

    def test_base_branch_strips_remote(self):
        repo = make_repo(self.tmp / "bb")
        bare = self.tmp / "origin.git"
        git(self.tmp, "clone", "--bare", "-q", str(repo), str(bare))
        git(repo, "remote", "add", "origin", str(bare))
        git(repo, "fetch", "-q", "origin")
        git(repo, "remote", "set-head", "origin", "main")
        out = self.inventory(repo)
        self.assertEqual((out["base"], out["base_branch"]), ("origin/main", "main"))
        self.assertEqual(self.inventory(repo, "--base", "main")["base_branch"], "main")
        self.assertIsNone(self.inventory(make_repo(self.tmp / "nb", branch="trunk"))["base_branch"])

    def test_current_branch_and_detached_head(self):
        repo = make_repo(self.tmp / "d")
        git(repo, "checkout", "-q", "-b", "feature")
        self.assertEqual(self.inventory(repo)["current_branch"], "feature")
        git(repo, "checkout", "-q", "--detach")
        self.assertIsNone(self.inventory(repo)["current_branch"])

    def test_paths_with_spaces_and_unicode(self):
        repo = make_repo(self.tmp / "u")
        rel = f"{SPECS}/2020-01-15-über design.md"
        write(repo, rel, "# Über\n")
        commit_all(repo, "unicode", date="2020-01-15T12:00:00")
        self.assertEqual(facts(by_path(self.inventory(repo), rel)), (True, False, True, "2020-01-15"))
        write(repo, rel, "# Über changed\n")
        self.assertTrue(by_path(self.inventory(repo), rel)["dirty"])


if __name__ == "__main__":
    unittest.main()
