from pathlib import Path
import tempfile
import unittest

from agents_helpers import agents_md_inventory as inv
from agents_helpers import call, commit_all, git, make_repo, run, write

USAGE_ERRORS = (
    [],
    ["frobnicate"],
    ["inventory", "--bogus"],
    ["blocks"],
    ["check-verdicts", "--file", "AGENTS.md", "--input", "x"],
    ["check-verdicts", "--file", "AGENTS.md", "--role", "partial", "--input", "x"],
)
HELP = (["--help"], ["inventory", "--help"], ["blocks", "-h"], ["check-verdicts", "--help"])
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
        # Committing an edit to an untracked file adds the whole file, so it is asked like any dirty one.
        self.assertTrue(untracked["dirty"])
        root = next(f for f in out["files"] if f["path"] == "AGENTS.md")
        self.assertFalse(root["dirty"])
        self.assertFalse(out["worktree_clean"])
        self.assertEqual(out["uncommitted"], {"count": 1, "paths": ["new/"]})

    def test_in_head_says_whether_a_commit_adds_the_whole_file(self):
        # A file staged or marked intent-to-add is tracked, but not in HEAD: committing it adds all of it.
        write(self.repo, "AGENTS.md", "- a\n")
        commit_all(self.repo, "add")
        write(self.repo, "staged/AGENTS.md", "- s\n")
        git(self.repo, "add", "staged/AGENTS.md")
        write(self.repo, "ita/AGENTS.md", "- i\n")
        git(self.repo, "add", "-N", "ita/AGENTS.md")
        write(self.repo, "new/AGENTS.md", "- n\n")
        facts = {f["path"]: (f["tracked"], f["dirty"], f["in_head"]) for f in self.inventory()["files"]}
        self.assertEqual(
            facts,
            {
                "AGENTS.md": (True, False, True),
                "staged/AGENTS.md": (True, True, False),
                "ita/AGENTS.md": (True, True, False),
                "new/AGENTS.md": (False, True, False),
            },
        )

    def test_in_head_with_no_commits_yet(self):
        repo = self.tmp / "unborn"
        repo.mkdir()
        git(repo, "init", "-b", "main")
        write(repo, "AGENTS.md", "- a\n")
        git(repo, "add", "AGENTS.md")
        (item,) = run("inventory", "--repo", str(repo))["files"]
        self.assertEqual((item["tracked"], item["in_head"]), (True, False))

    def test_untracked_counts_as_dirty_whatever_the_user_config(self):
        write(self.repo, "AGENTS.md", "- a\n")
        commit_all(self.repo, "add")
        write(self.repo, "new/AGENTS.md", "- fresh\n")
        git(self.repo, "config", "status.showUntrackedFiles", "no")
        out = self.inventory()
        self.assertTrue(next(f for f in out["files"] if f["path"] == "new/AGENTS.md")["dirty"])
        self.assertFalse(out["worktree_clean"])
        self.assertEqual(out["uncommitted"]["count"], 1)

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

    def test_scope_symlink_loop_is_rejected(self):
        (self.repo / "loopdir").symlink_to("loopdir")
        code, out, err = call("inventory", "--repo", str(self.repo), "--scope", "loopdir")
        self.assertEqual((code, out), (1, ""))
        self.assertEqual(err, "error: scope is not a directory in the repo: loopdir\n")

    def test_symlink_loop_file_is_omitted_and_rejected(self):
        write(self.repo, "AGENTS.md", "- a\n")
        (self.repo / "loop").mkdir()
        (self.repo / "loop" / "AGENTS.md").symlink_to("AGENTS.md")
        commit_all(self.repo, "loop")
        self.assertEqual([f["path"] for f in self.inventory()["files"]], ["AGENTS.md"])
        code, out, err = call("blocks", "--repo", str(self.repo), "loop/AGENTS.md")
        self.assertEqual((code, out), (1, ""))
        self.assertEqual(err, "error: no such file in the repo: loop/AGENTS.md\n")

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

    def test_rulings_through_a_symlink_out_of_the_repo_are_absent(self):
        outside = self.tmp / "outside"
        outside.mkdir()
        (outside / "agents-md-cleanup.md").write_text("- keep everything\n", encoding="utf-8")
        (self.repo / ".claude").mkdir()
        (self.repo / ".claude" / "agents-md-cleanup.md").symlink_to(outside / "agents-md-cleanup.md")
        rulings = self.inventory()["rulings"]
        self.assertEqual((rulings["exists"], rulings["text"]), (False, None))

    def test_rulings_directory_symlink_is_an_error(self):
        # Git can't track a path through a symlinked directory, so the rulings could be neither read safely nor
        # committed there; the run stops with one line instead of reading them.
        outside = self.tmp / "outside"
        outside.mkdir()
        (outside / "agents-md-cleanup.md").write_text("- keep everything\n", encoding="utf-8")
        write(self.repo, "config/claude/agents-md-cleanup.md", "- keep uv\n")
        for target in (outside, self.repo / "config" / "claude"):
            with self.subTest(target=target.name):
                (self.repo / ".claude").symlink_to(target, target_is_directory=True)
                code, out, err = call("inventory", "--repo", str(self.repo))
                (self.repo / ".claude").unlink()
                self.assertEqual((code, out), (1, ""))
                self.assertEqual(err, "error: .claude is a symlink; git can't track the rulings file through it\n")

    def test_rulings_through_a_symlink_in_the_repo_are_read(self):
        write(self.repo, "docs/rulings.md", "- keep uv\n")
        (self.repo / ".claude").mkdir()
        (self.repo / ".claude" / "agents-md-cleanup.md").symlink_to("../docs/rulings.md")
        rulings = self.inventory()["rulings"]
        self.assertEqual((rulings["exists"], rulings["text"]), (True, "- keep uv\n"))

    def test_dirty_and_branch(self):
        write(self.repo, "AGENTS.md", "- a\n")
        commit_all(self.repo, "add")
        git(self.repo, "checkout", "-b", "feature")
        write(self.repo, "AGENTS.md", "- a\n- b\n")
        out = self.inventory()
        self.assertTrue(out["files"][0]["dirty"])
        self.assertTrue(out["files"][0]["tracked"])
        self.assertEqual((out["base"], out["base_branch"], out["current_branch"]), ("main", "main", "feature"))

    def test_a_clean_worktree(self):
        write(self.repo, ".gitignore", "build/\n")
        write(self.repo, "AGENTS.md", "- a\n")
        commit_all(self.repo, "add")
        write(self.repo, "build/out.txt", "ignored\n")  # ignored files are not uncommitted work
        out = self.inventory()
        self.assertTrue(out["worktree_clean"])
        self.assertEqual(out["uncommitted"], {"count": 0, "paths": []})

    def test_every_kind_of_uncommitted_work_is_reported(self):
        def staged_new(repo):
            write(repo, "docs/new.md", "x\n")
            git(repo, "add", "docs/new.md")

        def staged_edit(repo):
            write(repo, "src/a.py", "x = 2\n")
            git(repo, "add", "src/a.py")

        def staged_rename(repo):
            git(repo, "mv", "src/a.py", "src/b.py")

        def worktree_rename(repo):
            (repo / "src" / "a.py").rename(repo / "src" / "c.py")
            git(repo, "add", "-N", "src/c.py")

        for name, change, paths in [
            ("unstaged edit", lambda repo: write(repo, "src/a.py", "x = 2\n"), ["src/a.py"]),
            ("staged edit", staged_edit, ["src/a.py"]),
            ("staged new file", staged_new, ["docs/new.md"]),
            ("deletion", lambda repo: (repo / "src" / "a.py").unlink(), ["src/a.py"]),
            ("untracked file", lambda repo: write(repo, "notes.txt", "x\n"), ["notes.txt"]),
            ("staged rename", staged_rename, ["src/b.py"]),  # one entry, though porcelain -z gives two paths
            ("worktree rename", worktree_rename, ["src/c.py"]),  # ` R`: the rename is in the second column
        ]:
            with self.subTest(name=name):
                with tempfile.TemporaryDirectory() as tmp:
                    repo = make_repo(Path(tmp))
                    write(repo, "AGENTS.md", "- a\n")
                    write(repo, "src/a.py", "x = 1\n")
                    commit_all(repo, "add")
                    change(repo)
                    out = run("inventory", "--repo", str(repo), "--scope", ".")
                    self.assertFalse(out["worktree_clean"])
                    self.assertEqual(out["uncommitted"], {"count": 1, "paths": paths})

    def test_uncommitted_work_counts_across_the_repo_on_a_partial_run(self):
        write(self.repo, "AGENTS.md", "- a\n")
        write(self.repo, "tests/AGENTS.md", "- t\n")
        commit_all(self.repo, "add")
        write(self.repo, "docs/draft.md", "x\n")
        out = self.inventory("--scope", "tests")
        self.assertFalse(out["worktree_clean"])
        self.assertEqual(out["uncommitted"]["paths"], ["docs/"])

    def test_uncommitted_paths_are_capped(self):
        write(self.repo, "AGENTS.md", "- a\n")
        commit_all(self.repo, "add")
        for n in range(25):
            write(self.repo, f"f{n:02}.txt", "x\n")
        out = self.inventory()
        self.assertEqual(out["uncommitted"]["count"], 25)
        self.assertEqual(out["uncommitted"]["paths"], [f"f{n:02}.txt" for n in range(10)])

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

    def test_find_guidelines_ignores_inline_markup(self):
        for heading in ("## `AGENTS.md` guidelines", "## **AGENTS.md** Guidelines", "## _AGENTS.md_  guidelines"):
            with self.subTest(heading=heading):
                found = inv.find_guidelines(f"# R\n\n{heading}\n\n- a\n")
                self.assertEqual((found["start_line"], found["end_line"]), (3, 5))

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
            {"path": "AGENTS.md", "resolved": "AGENTS.md", "sha256": inv_file["sha256"], "blocks": inv_file["blocks"]},
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
        (self.repo / "src").mkdir()
        for bad in ["AGENTS.md", "nope/AGENTS.md", "../outside.md", "src", str(Path("/etc/hosts"))]:
            with self.subTest(path=bad):
                code, out, err = call("blocks", "--repo", str(self.repo), bad)
                self.assertEqual(code, 1)
                self.assertEqual(out, "")
                self.assertTrue(err.startswith("error: "), err)
                self.assertEqual(len(err.strip().splitlines()), 1)

    def test_in_repo_symlink_keeps_its_own_path(self):
        # The link keeps its own path, the one agents load; `resolved` names the file that edits go to.
        write(self.repo, "CLAUDE.md", "- shared\n")
        (self.repo / "AGENTS.md").symlink_to("CLAUDE.md")
        commit_all(self.repo, "add")
        files = self.inventory()["files"]
        self.assertEqual([(f["path"], f["resolved"], f["aliases"]) for f in files], [("AGENTS.md", "CLAUDE.md", [])])
        out = run("blocks", "--repo", str(self.repo), "AGENTS.md")
        self.assertEqual(
            out,
            {"path": "AGENTS.md", "resolved": "CLAUDE.md", "sha256": files[0]["sha256"], "blocks": files[0]["blocks"]},
        )

    def test_outside_repo_symlink_is_omitted_and_rejected(self):
        outside = self.tmp / "outside.md"
        outside.write_text("- secret\n", encoding="utf-8")
        (self.repo / "AGENTS.md").symlink_to(outside)
        write(self.repo, "src/AGENTS.md", "- x\n")
        commit_all(self.repo, "add")
        out = self.inventory()
        self.assertEqual([f["path"] for f in out["files"]], ["src/AGENTS.md"])
        self.assertEqual(out["agents_md_dirs"], ["src"])
        code, stdout, err = call("blocks", "--repo", str(self.repo), "AGENTS.md")
        self.assertEqual(code, 1)
        self.assertEqual(stdout, "")
        self.assertEqual(err.strip(), "error: no such file in the repo: AGENTS.md")

    def test_blocks_normalises_path(self):
        write(self.repo, "src/AGENTS.md", "- x\n")
        commit_all(self.repo, "add")
        self.assertEqual(run("blocks", "--repo", str(self.repo), "./src/../src/AGENTS.md")["path"], "src/AGENTS.md")
        self.assertEqual(
            run("blocks", "--repo", str(self.repo), str(self.repo.resolve() / "src" / "AGENTS.md"))["path"],
            "src/AGENTS.md",
        )


def _case_insensitive_fs(directory: Path) -> bool:
    probe = Path(directory) / "CaseProbe"
    probe.write_text("x", encoding="utf-8")
    try:
        return (Path(directory) / "caseprobe").exists()
    finally:
        probe.unlink()


class LinkedFileTest(RepoTestCase):
    """An AGENTS.md that is a symlink is checked, edited and committed through the file it resolves to."""

    def entries(self, *extra):
        return [(f["path"], f["resolved"], f["aliases"], f["role"]) for f in self.inventory(*extra)["files"]]

    def test_dirty_and_resolved_follow_the_link(self):
        write(self.repo, "CLAUDE.md", "- one\n- two\n")
        (self.repo / "AGENTS.md").symlink_to("CLAUDE.md")
        commit_all(self.repo, "add")
        write(self.repo, "CLAUDE.md", "- one\n- two\n- three\n")
        (item,) = self.inventory()["files"]
        self.assertEqual((item["path"], item["resolved"]), ("AGENTS.md", "CLAUDE.md"))
        self.assertTrue(item["dirty"])
        self.assertTrue(item["tracked"])

    def test_tracked_is_the_resolved_file(self):
        write(self.repo, "CLAUDE.md", "- one\n")
        commit_all(self.repo, "add")
        (self.repo / "AGENTS.md").symlink_to("CLAUDE.md")  # the link itself is new and untracked
        (item,) = self.inventory()["files"]
        self.assertTrue(item["tracked"])
        self.assertFalse(item["dirty"])

    def test_an_untracked_resolved_file_is_dirty(self):
        write(self.repo, "README.md", "# r\n")
        commit_all(self.repo, "add")
        write(self.repo, "CLAUDE.md", "- one\n")  # untracked, so a commit would add all of it
        (self.repo / "AGENTS.md").symlink_to("CLAUDE.md")
        (item,) = self.inventory()["files"]
        self.assertEqual((item["resolved"], item["tracked"], item["dirty"]), ("CLAUDE.md", False, True))

    def test_in_head_is_the_resolved_files(self):
        write(self.repo, "CLAUDE.md", "- one\n")
        commit_all(self.repo, "add")
        (self.repo / "AGENTS.md").symlink_to("CLAUDE.md")  # a new link to a committed file
        (item,) = self.inventory()["files"]
        self.assertTrue(item["in_head"])

    def test_a_retargeted_link_is_dirty(self):
        write(self.repo, "CLAUDE.md", "- one\n")
        write(self.repo, "OTHER.md", "- other\n")
        (self.repo / "AGENTS.md").symlink_to("CLAUDE.md")
        commit_all(self.repo, "add")
        (self.repo / "AGENTS.md").unlink()
        (self.repo / "AGENTS.md").symlink_to("OTHER.md")
        (item,) = self.inventory()["files"]
        self.assertEqual(item["resolved"], "OTHER.md")
        self.assertTrue(item["dirty"])

    def test_links_to_one_file_merge_into_one_entry(self):
        write(self.repo, "AGENTS.md", "- shared\n- rule\n")
        (self.repo / "docs").mkdir()
        (self.repo / "docs" / "AGENTS.md").symlink_to("../AGENTS.md")
        (self.repo / "web").mkdir()
        (self.repo / "web" / "AGENTS.md").symlink_to("../AGENTS.md")
        write(self.repo, "src/AGENTS.md", "- own\n")
        commit_all(self.repo, "add")
        out = self.inventory()
        self.assertEqual(
            [(f["path"], f["resolved"], f["aliases"]) for f in out["files"]],
            [("AGENTS.md", "AGENTS.md", ["docs/AGENTS.md", "web/AGENTS.md"]), ("src/AGENTS.md", "src/AGENTS.md", [])],
        )
        self.assertEqual(out["totals"], {"files": 2, "full": 2, "ancestor": 0, "blocks": 3, "agents": 2})
        self.assertEqual(out["agents_md_dirs"], [".", "docs", "src", "web"])

    def test_two_links_to_one_other_file_merge(self):
        write(self.repo, "CLAUDE.md", "- shared\n")
        (self.repo / "AGENTS.md").symlink_to("CLAUDE.md")
        (self.repo / "docs").mkdir()
        (self.repo / "docs" / "AGENTS.md").symlink_to("../CLAUDE.md")
        commit_all(self.repo, "add")
        self.assertEqual(self.entries(), [("AGENTS.md", "CLAUDE.md", ["docs/AGENTS.md"], "full")])

    def test_shallowest_path_leads_a_merged_entry(self):
        # Agents load the content at every path; the shallowest covers the most, and keeps the root's guidelines.
        # `0lib` sorts before `AGENTS.md`, so the lead is chosen by depth, not by name.
        write(self.repo, "0lib/deep/AGENTS.md", "- shared\n")
        (self.repo / "AGENTS.md").symlink_to("0lib/deep/AGENTS.md")
        (self.repo / "0lib" / "AGENTS.md").symlink_to("deep/AGENTS.md")
        commit_all(self.repo, "add")
        self.assertEqual(
            self.entries(), [("AGENTS.md", "0lib/deep/AGENTS.md", ["0lib/AGENTS.md", "0lib/deep/AGENTS.md"], "full")]
        )

    def test_merged_entry_is_full_when_any_path_is_in_scope(self):
        write(self.repo, "AGENTS.md", "- shared\n")
        (self.repo / "docs").mkdir()
        (self.repo / "docs" / "AGENTS.md").symlink_to("../AGENTS.md")
        write(self.repo, "src/AGENTS.md", "- own\n")
        commit_all(self.repo, "add")
        self.assertEqual(self.entries("--scope", "docs"), [("AGENTS.md", "AGENTS.md", ["docs/AGENTS.md"], "full")])
        self.assertEqual(
            self.entries("--scope", "src"),
            [
                ("AGENTS.md", "AGENTS.md", ["docs/AGENTS.md"], "ancestor"),
                ("src/AGENTS.md", "src/AGENTS.md", [], "full"),
            ],
        )

    def test_an_out_of_scope_link_is_still_an_alias(self):
        # docs/AGENTS.md is outside --scope src, but it is the same file, so the skill must know it links there.
        write(self.repo, "src/AGENTS.md", "- shared\n")
        (self.repo / "docs").mkdir()
        (self.repo / "docs" / "AGENTS.md").symlink_to("../src/AGENTS.md")
        commit_all(self.repo, "add")
        self.assertEqual(
            self.entries("--scope", "src"), [("src/AGENTS.md", "src/AGENTS.md", ["docs/AGENTS.md"], "full")]
        )

    def test_blocks_reports_the_resolved_path(self):
        write(self.repo, "CLAUDE.md", "- shared\n")
        (self.repo / "docs").mkdir()
        (self.repo / "docs" / "AGENTS.md").symlink_to("../CLAUDE.md")
        commit_all(self.repo, "add")
        out = run("blocks", "--repo", str(self.repo), "docs/AGENTS.md")
        self.assertEqual((out["path"], out["resolved"]), ("docs/AGENTS.md", "CLAUDE.md"))
        self.assertEqual(run("blocks", "--repo", str(self.repo), "CLAUDE.md")["resolved"], "CLAUDE.md")

    def test_resolved_uses_the_on_disk_spelling(self):
        if not _case_insensitive_fs(self.tmp):
            self.skipTest("needs a case-insensitive filesystem")
        write(self.repo, "AGENTS.md", "- shared\n")
        (self.repo / "docs").mkdir()
        (self.repo / "docs" / "AGENTS.md").symlink_to("../agents.MD")
        commit_all(self.repo, "add")
        self.assertEqual(self.entries(), [("AGENTS.md", "AGENTS.md", ["docs/AGENTS.md"], "full")])
        self.assertEqual(run("blocks", "--repo", str(self.repo), "docs/AGENTS.md")["resolved"], "AGENTS.md")


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

    def test_usage_errors_exit_1_with_one_error_line(self):
        for argv in USAGE_ERRORS:
            with self.subTest(argv=argv):
                code, out, err = call(*argv)
                self.assertEqual((code, out), (1, ""))
                self.assertTrue(err.startswith("error: "), err)
                self.assertEqual(len(err.strip().splitlines()), 1, err)

    def test_help_still_works(self):
        for argv in HELP:
            with self.subTest(argv=argv):
                code, out, err = call(*argv)
                self.assertEqual((code, err), (0, ""))
                self.assertIn("usage:", out)


if __name__ == "__main__":
    unittest.main()
