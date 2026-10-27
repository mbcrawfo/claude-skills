import json
from pathlib import Path
import re
import unittest

PLUGIN = Path(__file__).resolve().parent.parent
SKILL = PLUGIN / "skills" / "superpowers-cleanup" / "SKILL.md"


def parse_frontmatter(path: Path) -> dict:
    """Simple `key: value` lines between the first two `---` lines."""
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines and lines[0] == "---", "SKILL.md must start with frontmatter"
    meta = {}
    for line in lines[1:]:
        if line == "---":
            return meta
        key, _, value = line.partition(":")
        meta[key.strip()] = value.strip().strip('"')
    raise AssertionError("frontmatter is not closed")


class SkillMdTest(unittest.TestCase):
    def test_frontmatter(self):
        meta = parse_frontmatter(SKILL)
        self.assertEqual(meta["name"], "superpowers-cleanup")
        self.assertEqual(meta["disable-model-invocation"], "true")
        self.assertIn("manual", meta["description"].lower())
        self.assertEqual(meta["argument-hint"], "<what to remove, in plain words>")

    def test_names_the_new_command(self):
        # The command comes from the manifests, so build it from them rather than a fixed string.
        plugin = json.loads((PLUGIN / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))["name"]
        self.assertEqual(plugin, "maintenance")
        marketplace = json.loads((PLUGIN.parent.parent / ".claude-plugin" / "marketplace.json").read_text())
        entry = next(p for p in marketplace["plugins"] if p["source"] == f"./plugins/{PLUGIN.name}")
        self.assertEqual(entry["name"], plugin)
        meta = parse_frontmatter(SKILL)
        self.assertIn(f"/{plugin}:{meta['name']}", meta["description"])

    def test_script_lives_in_skill(self):
        self.assertTrue((SKILL.parent / "scripts" / "docs_inventory.py").is_file())
        self.assertFalse((PLUGIN / "scripts").exists())
        # Every mention, quoted or not, must call the script inside the skill folder.
        prefixes = re.findall(r"(\S*)docs_inventory\.py", SKILL.read_text(encoding="utf-8"))
        self.assertTrue(prefixes)
        for prefix in prefixes:
            self.assertEqual(prefix, '"${CLAUDE_SKILL_DIR}/scripts/')

    def test_no_dollar_digit(self):
        self.assertIsNone(re.search(r"\$\d", SKILL.read_text(encoding="utf-8")))

    def test_names_only_real_script_commands(self):
        text = SKILL.read_text(encoding="utf-8")
        self.assertIn("docs_inventory.py", text)
        commands = re.findall(r"docs_inventory\.py\"?\s+(\w+)", text)
        self.assertTrue(commands)
        for cmd in commands:
            self.assertIn(cmd, {"inventory", "refs"})

    def test_unfinished_plan_holds_back_its_spec(self):
        text = SKILL.read_text(encoding="utf-8")
        self.assertIn("An unfinished plan holds back its spec", text)
        self.assertIn("a held-back spec holds back all its plans", text)

    def test_in_target_skip_only_for_files_actually_removed(self):
        text = SKILL.read_text(encoding="utf-8")
        self.assertIn("only when the file holding them is in the final *Files to remove*", text)
        self.assertNotIn("inside a file being removed", text)

    def test_commit_limited_to_own_paths(self):
        text = SKILL.read_text(encoding="utf-8")
        self.assertIn("git commit -F <message file> -- <paths>", text)
        self.assertIn("git status --porcelain -- <file>", text)

    def test_table_has_kept_note_and_base_branch(self):
        text = SKILL.read_text(encoding="utf-8")
        self.assertIn("*Kept, and why*", text)
        self.assertIn("`base_branch`", text)

    def test_apply_handles_dirty_and_mixed_paths_literally(self):
        text = SKILL.read_text(encoding="utf-8")
        self.assertIn("GIT_LITERAL_PATHSPECS=1 git rm -r -f -q -- <path>", text)

    def test_folders_found_before_any_inventory(self):
        text = SKILL.read_text(encoding="utf-8")
        self.assertLess(text.index("## 1. Find the folders"), text.index("## 2. Read the request"))

    def test_never_committed_paths(self):
        text = SKILL.read_text(encoding="utf-8")
        self.assertIn("`tracked` is false or `first_added` is null", text)
        self.assertIn("git cat-file -e HEAD:<path>", text)

    def test_reply_rules_default_to_not_approved(self):
        self.assertIn("Anything else is not approval", SKILL.read_text(encoding="utf-8"))


class NothingChangedGraderTest(unittest.TestCase):
    GRADER = (
        Path(__file__).resolve().parent.parent / "evals" / "superpowers-cleanup-run" / "graders" / "nothing-changed.md"
    )

    def pattern(self):
        line = next(x for x in self.GRADER.read_text().splitlines() if x.startswith("input_match:"))
        return re.compile(line.split(":", 1)[1].strip().strip("'"))

    def test_bounds_allow_zero_calls(self):
        # tool_used defaults min to 1, so max: 0 alone can never pass.
        lines = self.GRADER.read_text().splitlines()
        self.assertIn("min: 0", lines)
        self.assertIn("max: 0", lines)

    def test_catches_changes(self):
        for cmd in [
            "git rm -r -q -- docs/x.md",
            "git commit -m x",
            "git add docs",
            "git checkout -- src/a.py",
            "git restore src/a.py",
            "git stash push -m x",
            "git reset --hard",
            "git switch main",
            "git clean -fd",
            "rm -r docs/x",
            "mv a b",
            "sed -i '' 's/a/b/' src/a.py",
        ]:
            self.assertIsNotNone(self.pattern().search(cmd), cmd)

    def test_allows_inspection(self):
        for cmd in [
            "git status --porcelain",
            "git log --oneline -20",
            "git grep -n export_csv main",
            "git ls-tree -r --name-only main",
            "git diff --stat",
            'python3 "x/docs_inventory.py" inventory --repo . 2>&1',
            "sed -n 1,20p src/a.py",
        ]:
            self.assertIsNone(self.pattern().search(cmd), cmd)


if __name__ == "__main__":
    unittest.main()
