from pathlib import Path
import tempfile
import unittest

from agents_helpers import agents_md_inventory as inv
from agents_helpers import call, commit_all, git, make_repo, run, write

GUIDELINES_ROOT = "# R\n\n## AGENTS.md guidelines\n\n- Keep it lean.\n\n## Other\n\nx\n"


class RepoTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.repo = make_repo(self.tmp)

    def inventory(self, *extra):
        return run("inventory", "--repo", str(self.repo), *extra)


class InventoryTest(RepoTestCase):
    def test_whole_repo_finds_nested_and_lowercase(self):
        write(self.repo, "AGENTS.md", "# Root\n\n- a\n")
        write(self.repo, "src/AGENTS.md", "- b\n- c\n")
        write(self.repo, "docs/agents.md", "para\n")
        commit_all(self.repo, "add agents files")
        out = self.inventory()
        self.assertEqual([f["path"] for f in out["files"]], ["AGENTS.md", "docs/agents.md", "src/AGENTS.md"])
        self.assertTrue(all(f["role"] == "full" for f in out["files"]))
        self.assertEqual(out["agents_md_dirs"], [".", "docs", "src"])
        self.assertEqual(out["scope"], ["."])
        self.assertEqual(out["repo"], str(self.repo.resolve()))
        self.assertEqual(out["totals"]["agents"], 3)
        self.assertEqual(out["totals"]["files"], 3)
        self.assertEqual(out["totals"]["full"], 3)
        self.assertEqual(out["totals"]["ancestor"], 0)
        self.assertEqual(out["totals"]["blocks"], sum(len(f["blocks"]) for f in out["files"]))
        self.assertEqual(out["totals"]["blocks"], 4)

    def test_file_facts(self):
        content = "# Root\n\n- a\n- café\n"
        write(self.repo, "AGENTS.md", content)
        commit_all(self.repo, "add")
        (item,) = self.inventory()["files"]
        self.assertEqual(item["bytes"], len(content.encode("utf-8")))
        self.assertEqual(len(item["sha256"]), 64)
        self.assertEqual([b["id"] for b in item["blocks"]], ["b1", "b2"])
        self.assertTrue(item["tracked"])
        self.assertFalse(item["dirty"])

    def test_ignored_and_untracked(self):
        write(self.repo, ".gitignore", "build/\n")
        write(self.repo, "AGENTS.md", "- a\n")
        write(self.repo, "build/AGENTS.md", "- ignored\n")
        commit_all(self.repo, "add")
        write(self.repo, "new/AGENTS.md", "- fresh\n")
        out = self.inventory()
        paths = [f["path"] for f in out["files"]]
        self.assertNotIn("build/AGENTS.md", paths)
        self.assertIn("new/AGENTS.md", paths)
        self.assertNotIn("build", out["agents_md_dirs"])
        untracked = next(f for f in out["files"] if f["path"] == "new/AGENTS.md")
        self.assertFalse(untracked["tracked"])
        self.assertFalse(untracked["dirty"])

    def test_deleted_tracked_file_is_skipped(self):
        write(self.repo, "AGENTS.md", "- a\n")
        write(self.repo, "gone/AGENTS.md", "- b\n")
        commit_all(self.repo, "add")
        (self.repo / "gone" / "AGENTS.md").unlink()
        self.assertEqual([f["path"] for f in self.inventory()["files"]], ["AGENTS.md"])

    def test_directory_named_agents_md_is_skipped(self):
        (self.repo / "AGENTS.md").mkdir()
        write(self.repo, "AGENTS.md/inner.txt", "x")
        commit_all(self.repo, "add")
        self.assertEqual(self.inventory()["files"], [])

    def test_other_instruction_files_are_ignored(self):
        write(self.repo, "CLAUDE.md", "- a\n")
        write(self.repo, "GEMINI.md", "- a\n")
        write(self.repo, ".cursor/rules/x.mdc", "- a\n")
        write(self.repo, "AGENTS.md.bak", "- a\n")
        commit_all(self.repo, "add")
        self.assertEqual(self.inventory()["files"], [])

    def test_partial_run_roles(self):
        for rel in [
            "AGENTS.md",
            "tests/AGENTS.md",
            "tests/unit/AGENTS.md",
            "src/AGENTS.md",
            "tests/unit/deep/AGENTS.md",
        ]:
            write(self.repo, rel, "- x\n")
        commit_all(self.repo, "add")
        out = self.inventory("--scope", "tests/unit")
        self.assertEqual(
            {f["path"]: f["role"] for f in out["files"]},
            {
                "AGENTS.md": "ancestor",
                "tests/AGENTS.md": "ancestor",
                "tests/unit/AGENTS.md": "full",
                "tests/unit/deep/AGENTS.md": "full",
            },
        )
        self.assertEqual(out["scope"], ["tests/unit"])
        self.assertEqual(out["totals"]["full"], 2)
        self.assertEqual(out["totals"]["ancestor"], 2)
        self.assertEqual(out["totals"]["files"], 4)
        self.assertIn("src", out["agents_md_dirs"])

    def test_multiple_scopes(self):
        for rel in ["AGENTS.md", "a/AGENTS.md", "b/AGENTS.md", "c/AGENTS.md"]:
            write(self.repo, rel, "- x\n")
        commit_all(self.repo, "add")
        out = self.inventory("--scope", "a", "--scope", "b/")
        self.assertEqual(out["scope"], ["a", "b"])
        self.assertEqual(
            {f["path"]: f["role"] for f in out["files"]},
            {"AGENTS.md": "ancestor", "a/AGENTS.md": "full", "b/AGENTS.md": "full"},
        )

    def test_scope_dot_is_whole_repo(self):
        write(self.repo, "AGENTS.md", "- x\n")
        write(self.repo, "src/AGENTS.md", "- y\n")
        commit_all(self.repo, "add")
        out = self.inventory("--scope", ".")
        self.assertEqual(out["scope"], ["."])
        self.assertTrue(all(f["role"] == "full" for f in out["files"]))
        self.assertEqual(len(out["files"]), 2)

    def test_scope_resolving_to_root_is_whole_repo(self):
        write(self.repo, "src/AGENTS.md", "- y\n")
        commit_all(self.repo, "add")
        out = self.inventory("--scope", "src/..")
        self.assertEqual(out["scope"], ["."])
        self.assertEqual([f["role"] for f in out["files"]], ["full"])

    def test_scope_without_agents_md_still_finds_ancestors(self):
        write(self.repo, "AGENTS.md", "- x\n")
        (self.repo / "empty").mkdir()
        commit_all(self.repo, "add")
        out = self.inventory("--scope", "empty")
        self.assertEqual([(f["path"], f["role"]) for f in out["files"]], [("AGENTS.md", "ancestor")])

    def test_bad_scope(self):
        write(self.repo, "AGENTS.md", "- x\n")
        commit_all(self.repo, "add")
        for bad in ["missing", "../outside", "AGENTS.md", str(self.tmp)]:
            with self.subTest(scope=bad):
                code, out, err = call("inventory", "--repo", str(self.repo), "--scope", bad)
                self.assertEqual(code, 1)
                self.assertEqual(out, "")
                self.assertTrue(err.startswith("error: scope is not a directory in the repo"), err)
                self.assertEqual(len(err.strip().splitlines()), 1)

    def test_scope_symlink_out_of_repo_is_rejected(self):
        outside = self.tmp / "outside"
        outside.mkdir()
        (self.repo / "link").symlink_to(outside, target_is_directory=True)
        code, out, err = call("inventory", "--repo", str(self.repo), "--scope", "link")
        self.assertEqual(code, 1)
        self.assertTrue(err.startswith("error: scope is not a directory in the repo"), err)

    def test_guidelines_found(self):
        write(self.repo, "AGENTS.md", GUIDELINES_ROOT)
        commit_all(self.repo, "add")
        g = self.inventory()["guidelines"]
        self.assertEqual((g["path"], g["start_line"], g["end_line"]), ("AGENTS.md", 3, 6))
        self.assertIn("Keep it lean.", g["text"])
        self.assertTrue(g["text"].startswith("## AGENTS.md guidelines"))
        self.assertNotIn("## Other", g["text"])

    def test_guidelines_heading_is_case_insensitive_and_runs_to_end(self):
        write(self.repo, "agents.md", "# R\n\n### Agents.MD Guidelines\n\n- a\n\n#### Sub\n\n- b\n")
        commit_all(self.repo, "add")
        g = self.inventory()["guidelines"]
        self.assertEqual((g["path"], g["start_line"], g["end_line"]), ("agents.md", 3, 9))
        self.assertIn("- b", g["text"])

    def test_guidelines_stops_at_higher_heading(self):
        write(self.repo, "AGENTS.md", "## AGENTS.md guidelines\n\n- a\n\n# Top\n\n- b\n")
        commit_all(self.repo, "add")
        g = self.inventory()["guidelines"]
        self.assertEqual((g["start_line"], g["end_line"]), (1, 4))

    def test_guidelines_ignores_fenced_and_nested_agents(self):
        write(self.repo, "AGENTS.md", "# R\n\n```md\n## AGENTS.md guidelines\n```\n")
        write(self.repo, "src/AGENTS.md", GUIDELINES_ROOT)
        commit_all(self.repo, "add")
        self.assertIsNone(self.inventory()["guidelines"])

    def test_guidelines_null_without_section(self):
        write(self.repo, "AGENTS.md", "# R\n\n- a\n")
        commit_all(self.repo, "add")
        self.assertIsNone(self.inventory()["guidelines"])

    def test_guidelines_null_without_root_file(self):
        write(self.repo, "src/AGENTS.md", GUIDELINES_ROOT)
        commit_all(self.repo, "add")
        self.assertIsNone(self.inventory()["guidelines"])

    def test_guidelines_read_on_partial_run_outside_scope(self):
        write(self.repo, "AGENTS.md", GUIDELINES_ROOT)
        write(self.repo, "src/AGENTS.md", "- x\n")
        write(self.repo, "other/AGENTS.md", "- y\n")
        commit_all(self.repo, "add")
        out = self.inventory("--scope", "src")
        self.assertEqual(out["guidelines"]["path"], "AGENTS.md")
        self.assertEqual((out["guidelines"]["start_line"], out["guidelines"]["end_line"]), (3, 6))

    def test_rulings_missing_and_ignored(self):
        write(self.repo, ".gitignore", ".claude/\n")
        commit_all(self.repo, "ignore")
        self.assertEqual(
            self.inventory()["rulings"],
            {"path": ".claude/agents-md-cleanup.md", "exists": False, "ignored": True, "text": None},
        )

    def test_rulings_present_and_ignored(self):
        write(self.repo, ".gitignore", ".claude/\n")
        write(self.repo, ".claude/agents-md-cleanup.md", "keep uv\n")
        rulings = self.inventory()["rulings"]
        self.assertEqual((rulings["exists"], rulings["ignored"], rulings["text"]), (True, True, "keep uv\n"))

    def test_rulings_missing_and_not_ignored(self):
        self.assertEqual(
            self.inventory()["rulings"],
            {"path": ".claude/agents-md-cleanup.md", "exists": False, "ignored": False, "text": None},
        )

    def test_rulings_present(self):
        text = "# Rulings\n\n- keep uv\n"
        write(self.repo, ".claude/agents-md-cleanup.md", text)
        self.assertEqual(
            self.inventory()["rulings"],
            {"path": ".claude/agents-md-cleanup.md", "exists": True, "ignored": False, "text": text},
        )

    def test_dirty_and_branch(self):
        write(self.repo, "AGENTS.md", "- a\n")
        commit_all(self.repo, "add")
        git(self.repo, "checkout", "-b", "feature")
        write(self.repo, "AGENTS.md", "- a\n- b\n")
        out = self.inventory()
        self.assertTrue(out["files"][0]["dirty"])
        self.assertTrue(out["files"][0]["tracked"])
        self.assertEqual((out["base"], out["base_branch"], out["current_branch"]), ("main", "main", "feature"))

    def test_base_override_and_unknown_base(self):
        git(self.repo, "branch", "other")
        out = self.inventory("--base", "other")
        self.assertEqual((out["base"], out["base_branch"]), ("other", "other"))
        code, stdout, err = call("inventory", "--repo", str(self.repo), "--base", "nope")
        self.assertEqual(code, 1)
        self.assertEqual(stdout, "")
        self.assertEqual(err.strip(), "error: unknown base ref: nope")

    def test_no_agents_md(self):
        out = self.inventory()
        self.assertEqual(out["files"], [])
        self.assertEqual(out["agents_md_dirs"], [])
        self.assertEqual(out["totals"], {"files": 0, "full": 0, "ancestor": 0, "blocks": 0, "agents": 0})
        self.assertIsNone(out["guidelines"])

    def test_invalid_utf8_does_not_crash(self):
        write(self.repo, "AGENTS.md", b"- ok\n- bad \xff byte\n")
        commit_all(self.repo, "add")
        (item,) = self.inventory()["files"]
        self.assertEqual(len(item["blocks"]), 2)
        self.assertEqual(item["bytes"], 18)


class HelperTest(unittest.TestCase):
    def test_find_guidelines_pure(self):
        found = inv.find_guidelines(GUIDELINES_ROOT)
        self.assertEqual(set(found), {"start_line", "end_line", "text"})
        self.assertEqual((found["start_line"], found["end_line"]), (3, 6))
        self.assertIsNone(inv.find_guidelines("# R\n"))

    def test_find_guidelines_setext_heading(self):
        found = inv.find_guidelines("Intro\n\nAGENTS.md guidelines\n---------------------\n\n- a\n")
        self.assertEqual((found["start_line"], found["end_line"]), (3, 6))


class BlocksCommandTest(RepoTestCase):
    def test_blocks_matches_inventory(self):
        write(self.repo, "AGENTS.md", "# T\n\n- a\n- b\n")
        write(self.repo, "src/AGENTS.md", "- z\n")
        commit_all(self.repo, "add")
        inv_file = self.inventory()["files"][0]
        self.assertEqual(
            run("blocks", "--repo", str(self.repo), "AGENTS.md"),
            {"path": "AGENTS.md", "sha256": inv_file["sha256"], "blocks": inv_file["blocks"]},
        )
        nested = run("blocks", "--repo", str(self.repo), "src/AGENTS.md")
        self.assertEqual(nested["path"], "src/AGENTS.md")
        self.assertEqual(len(nested["blocks"]), 1)

    def test_blocks_sha_changes_with_content(self):
        write(self.repo, "AGENTS.md", "- a\n")
        commit_all(self.repo, "add")
        before = run("blocks", "--repo", str(self.repo), "AGENTS.md")["sha256"]
        write(self.repo, "AGENTS.md", "- a\n- b\n")
        after = run("blocks", "--repo", str(self.repo), "AGENTS.md")
        self.assertNotEqual(before, after["sha256"])
        self.assertEqual(len(after["blocks"]), 2)

    def test_blocks_missing_file(self):
        for bad in ["AGENTS.md", "nope/AGENTS.md", "../outside.md", "src", str(Path("/etc/hosts"))]:
            with self.subTest(path=bad):
                (self.repo / "src").mkdir(exist_ok=True)
                code, out, err = call("blocks", "--repo", str(self.repo), bad)
                self.assertEqual(code, 1)
                self.assertEqual(out, "")
                self.assertTrue(err.startswith("error: "), err)
                self.assertEqual(len(err.strip().splitlines()), 1)


class ErrorPathTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def test_not_a_git_repo(self):
        for command in (["inventory"], ["blocks", "AGENTS.md"]):
            with self.subTest(command=command[0]):
                code, out, err = call(command[0], "--repo", str(self.tmp), *command[1:])
                self.assertEqual(code, 1)
                self.assertEqual(out, "")
                self.assertTrue(err.startswith("error: "), err)
                self.assertEqual(len(err.strip().splitlines()), 1, err)

    def test_unknown_command(self):
        code, out, _ = call("frobnicate")
        self.assertNotEqual(code, 0)
        self.assertEqual(out, "")


if __name__ == "__main__":
    unittest.main()
