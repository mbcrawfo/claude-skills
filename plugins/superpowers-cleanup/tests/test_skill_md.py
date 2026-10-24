from pathlib import Path
import re
import unittest

SKILL = Path(__file__).resolve().parent.parent / "skills" / "cleanup" / "SKILL.md"


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
        self.assertEqual(meta["name"], "cleanup")
        self.assertEqual(meta["disable-model-invocation"], "true")
        self.assertIn("manual", meta["description"].lower())
        self.assertEqual(meta["argument-hint"], "<what to remove, in plain words>")

    def test_no_dollar_digit(self):
        self.assertIsNone(re.search(r"\$\d", SKILL.read_text(encoding="utf-8")))

    def test_names_only_real_script_commands(self):
        text = SKILL.read_text(encoding="utf-8")
        self.assertIn("docs_inventory.py", text)
        commands = re.findall(r"docs_inventory\.py\"?\s+(\w+)", text)
        self.assertTrue(commands)
        for cmd in commands:
            self.assertIn(cmd, {"inventory", "refs"})


if __name__ == "__main__":
    unittest.main()
