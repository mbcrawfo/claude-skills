import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

from agents_helpers import git, run

EVALS = Path(__file__).resolve().parent.parent / "evals"
FIRST = EVALS / "agents-md-cleanup-first-run"
REVIEW = EVALS / "agents-md-cleanup-review"
# The block count the first-run fixture's inventory reports; the pre-dispatch grader states it too.
FIRST_RUN_BLOCKS = 7


def front_matter(path):
    """The key: value lines of a grader's or prompt's front matter."""
    lines = path.read_text().splitlines()
    end = lines.index("---", 1)
    return lines[1:end]


class FixtureTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name).resolve()
        self.ws = self.tmp / "ws"
        self.ws.mkdir()

    def scaffold(self, case, ws=None):
        ws = ws or self.ws
        # The eval runs the scaffold with only these variables set.
        env = {"PATH": os.environ["PATH"], "HOME": str(ws), "TERM": "dumb"}
        return subprocess.run(["bash", str(case / "scaffold.sh")], cwd=str(ws), env=env, capture_output=True, text=True)

    def scaffold_ok(self, case, name):
        ws = self.tmp / name
        ws.mkdir()
        result = self.scaffold(case, ws)
        self.assertEqual(result.returncode, 0, result.stderr)
        return ws


class FixtureTest(FixtureTestCase):
    def test_first_run_fixture(self):
        self.assertEqual(self.scaffold(FIRST).returncode, 0)
        out = run("inventory", "--repo", str(self.ws))
        self.assertEqual([f["path"] for f in out["files"]], ["AGENTS.md", "src/AGENTS.md"])
        self.assertIsNone(out["guidelines"])
        self.assertEqual(out["current_branch"], "chore/agents-md")
        self.assertEqual(out["totals"]["blocks"], FIRST_RUN_BLOCKS)
        self.assertEqual(out["totals"]["agents"], 2)
        self.assertTrue((self.ws / "tests").is_dir())
        self.assertFalse((self.ws / "test").exists())
        self.assertEqual(git(self.ws, "status", "--porcelain"), "")
        self.assertEqual(git(self.ws, "branch", "--list", "main").split()[-1], "main")
        self.assertIn("MAX_ATTEMPTS = 3", (self.ws / "src" / "payments" / "retry.py").read_text())
        self.assertIn("Accepts ISO 8601 dates only.", (self.ws / "src" / "dates.py").read_text())

    def test_review_fixture_has_policy(self):
        self.assertEqual(self.scaffold(REVIEW).returncode, 0)
        out = run("inventory", "--repo", str(self.ws))
        self.assertIsNotNone(out["guidelines"])
        text = out["guidelines"]["text"]
        self.assertIn("New nested AGENTS.md files: allowed.", text)
        self.assertIn("File-specific quirks: comment in that file.", text)
        self.assertIn("Push-down: aggressive.", text)
        self.assertLess(len(text.strip().splitlines()), 10)
        self.assertEqual(out["current_branch"], "chore/agents-md")
        self.assertEqual(git(self.ws, "status", "--porcelain"), "")

    def test_fixtures_differ_only_in_guidelines(self):
        first = self.scaffold_ok(FIRST, "first")
        review = self.scaffold_ok(REVIEW, "review")
        first_files = git(first, "ls-files").splitlines()
        self.assertEqual(first_files, git(review, "ls-files").splitlines())
        for name in first_files:
            if name != "AGENTS.md":
                self.assertEqual((first / name).read_bytes(), (review / name).read_bytes(), name)
        first_root = (first / "AGENTS.md").read_text()
        review_root = (review / "AGENTS.md").read_text()
        self.assertNotIn("AGENTS.md guidelines", first_root)
        before, marker, _ = review_root.partition("## AGENTS.md guidelines")
        self.assertEqual(marker, "## AGENTS.md guidelines")
        self.assertEqual(before.rstrip("\n"), first_root.rstrip("\n"))

    def test_planted_statements(self):
        ws = self.scaffold_ok(FIRST, "first")
        root = (ws / "AGENTS.md").read_text()
        for statement in [
            "Run tests with `python3 -m unittest discover -s test`.",
            "Write clear, readable code.",
            "It is really very important that you always make sure to run `ruff format .` before you commit any "
            "Python code, because otherwise CI will fail.",
            "Use `uv` for dependencies; never call `pip` directly.",
            "`src/payments/retry.py`: keep `MAX_ATTEMPTS` at 3; the processor bans clients after 4 failed "
            "attempts a minute.",
            "API handlers in `src/` return `Result` objects, never raise.",
        ]:
            self.assertIn(statement, root)
        self.assertIn("`parse_date()` accepts ISO 8601 dates only.", (ws / "src" / "AGENTS.md").read_text())
        for name in [
            "pyproject.toml",
            "uv.lock",
            "tests/test_dates.py",
            "src/dates.py",
            "src/payments/retry.py",
            "src/handlers.py",
            ".github/workflows/ci.yml",
        ]:
            self.assertTrue((ws / name).is_file(), name)
        self.assertIn("[tool.ruff]", (ws / "pyproject.toml").read_text())
        self.assertIn("ruff format --check .", (ws / ".github" / "workflows" / "ci.yml").read_text())
        handlers = (ws / "src" / "handlers.py").read_text()
        self.assertIn("class Result", handlers)
        self.assertIn("return Result(", handlers)

    def test_refuses_to_overwrite(self):
        for case in (FIRST, REVIEW):
            ws = self.scaffold_ok(case, case.name)
            second = self.scaffold(case, ws)
            self.assertNotEqual(second.returncode, 0)
            self.assertIn("already exists", second.stderr)


class GraderTest(unittest.TestCase):
    def test_no_dispatch_bounds(self):
        # tool_used defaults min to 1, so max: 0 alone can never pass.
        lines = front_matter(FIRST / "graders" / "no-dispatch.md")
        self.assertIn("tool: Agent", lines)
        self.assertIn("min: 0", lines)
        self.assertIn("max: 0", lines)

    def test_nothing_changed_regex(self):
        for case in (FIRST, REVIEW):
            lines = front_matter(case / "graders" / "nothing-changed.md")
            self.assertIn("min: 0", lines)
            self.assertIn("max: 0", lines)
            line = next(x for x in lines if x.startswith("input_match:"))
            # A YAML single-quoted scalar writes a literal ' as ''.
            pattern = re.compile(line.split(":", 1)[1].strip()[1:-1].replace("''", "'"))
            for cmd in [
                "git rm -r -q -- AGENTS.md",
                "git commit -m x",
                "git add AGENTS.md",
                "git checkout -- src/a.py",
                "git restore src/a.py",
                "git stash push -m x",
                "git reset --hard",
                "git switch main",
                "git clean -fd",
                "rm -r src",
                "rm -r -- AGENTS.md",
                "rm -- 'AGENTS.md'",
                'rm -r -- "src"',
                "git -C . add x",
                "git -C /tmp/x commit -m x",
                "mv a b",
                "sed -i '' 's/a/b/' src/a.py",
            ]:
                self.assertIsNotNone(pattern.search(cmd), cmd)
            for cmd in [
                "git status --porcelain",
                "git log --oneline -20",
                "git diff --stat",
                'python3 "x/agents_md_inventory.py" inventory --repo . 2>&1',
                "sed -n 1,20p src/a.py",
                "git -C . status --porcelain",
            ]:
                self.assertIsNone(pattern.search(cmd), cmd)
            # The review run ends with the skill removing its own mktemp -d directory; the first-run case stays strict.
            for cmd in [
                "rm -r -- /var/folders/ab/T/tmp.XYZ",
                "rm -r -- /tmp/tmp.XYZ",
                "rm -rf /tmp/tmp.XYZ",
                "rm -r -- '/var/folders/x/tmp.A'",
                'rm -r -- "/tmp/x"',
            ]:
                if case == REVIEW:
                    self.assertIsNone(pattern.search(cmd), cmd)
                else:
                    self.assertIsNotNone(pattern.search(cmd), cmd)

    def test_no_repo_write_regex(self):
        # tool_used matches input_match against the JSON-encoded tool input.
        lines = front_matter(REVIEW / "graders" / "no-repo-write.md")
        self.assertIn("tool: Write", lines)
        self.assertIn("min: 0", lines)
        self.assertIn("max: 0", lines)
        line = next(x for x in lines if x.startswith("input_match:"))
        pattern = re.compile(line.split(":", 1)[1].strip()[1:-1].replace("''", "'"))
        for path in [
            "/x/ws/AGENTS.md",
            "/x/ws/src/AGENTS.md",
            "/x/ws/src/payments/AGENTS.md",
            "/x/ws/src/agents.md",
            "/x/ws/src/payments/retry.py",
            "/x/ws/src/handlers.py",
            "/x/ws/.gitignore",
            "/x/ws/.claude/agents-md-cleanup.md",
            "/x/ws/pyproject.toml",
            "/x/ws/uv.lock",
            "/x/ws/.github/workflows/ci.yml",
            "/private/tmp/e-AbC123/home/cwd/AGENTS.md",
        ]:
            for encoded in (
                json.dumps({"file_path": path, "content": "x"}, separators=(",", ":")),
                json.dumps({"file_path": path, "content": "x"}),
            ):
                self.assertIsNotNone(pattern.search(encoded), encoded)
        for path in [
            "/var/folders/ab/T/tmp.X/round.md",
            "/tmp/tmp.X/msg.md",
            "/var/folders/ab/T/tmp.Q1w2E3r4/1.md",
            "/tmp/tmp.X/2-retry.md",
            "/tmp/tmp.X/AGENTS.md",
            "/var/folders/ab/T/tmp.X/src/payments/retry.py",
        ]:
            # Saved reviewer messages quote repo paths, so the content must never decide the match.
            content = 'Moves to src/payments/retry.py; {"file_path":"/x/ws/AGENTS.md"} in AGENTS.md'
            encoded = json.dumps({"file_path": path, "content": content}, separators=(",", ":"))
            self.assertIsNone(pattern.search(encoded), encoded)

    def test_skill_ran_matches_inventory_command(self):
        for case in (FIRST, REVIEW):
            lines = front_matter(case / "graders" / "skill-ran.md")
            self.assertIn("tool: Bash", lines)
            line = next(x for x in lines if x.startswith("input_match:"))
            pattern = re.compile(line.split(":", 1)[1].strip().strip("'"))
            self.assertIsNotNone(
                pattern.search(
                    'python3 "/p/skills/agents-md-cleanup/scripts/agents_md_inventory.py" inventory --repo .'
                )
            )
            self.assertIsNone(pattern.search("git status"))

    def test_pre_dispatch_states_fixture_counts(self):
        text = (FIRST / "graders" / "pre-dispatch.md").read_text()
        self.assertIn("type: llm", text)
        self.assertIn("focus: last_message", text)
        self.assertIn(f"{FIRST_RUN_BLOCKS} blocks", text)
        self.assertIn("2 files", text)
        self.assertIn("2 agents", text)

    def test_case_files(self):
        for case, prompt, turns, timeout in [
            (FIRST, "/maintenance:agents-md-cleanup", "40", "900"),
            (REVIEW, "/maintenance:agents-md-cleanup 2 at a time", "80", "1800"),
        ]:
            self.assertIn("scaffold_script: scaffold.sh", (case / "case.yaml").read_text())
            self.assertIn(f"name: {case.name}", (case / "case.yaml").read_text())
            front = front_matter(case / "prompt.md")
            self.assertIn(f"max_turns: {turns}", front)
            self.assertIn(f"timeout_seconds: {timeout}", front)
            self.assertEqual((case / "prompt.md").read_text().splitlines()[-1], prompt)


if __name__ == "__main__":
    unittest.main()
