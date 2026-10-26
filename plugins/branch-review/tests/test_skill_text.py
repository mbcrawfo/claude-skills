"""Checks on the skills' command text."""

from pathlib import Path
import unittest

SKILLS = Path(__file__).resolve().parent.parent / "skills"

# Claude Code writes ${CLAUDE_PLUGIN_DATA} into skill text on load but does not
# set CLAUDE_PLUGIN_DATA in Bash shells, so each rs command must carry it.
PREFIX = 'CLAUDE_PLUGIN_DATA="${CLAUDE_PLUGIN_DATA}" '
RS = '"${CLAUDE_SKILL_DIR}/../../scripts/rs"'


class RsPrefixTest(unittest.TestCase):
    def test_every_rs_command_sets_plugin_data(self):
        for name in ("review", "check", "cleanup"):
            with self.subTest(skill=name):
                text = (SKILLS / name / "SKILL.md").read_text(encoding="utf-8")
                lines = [line for line in text.splitlines() if RS in line]
                self.assertTrue(lines, f"{name}: no rs command found")
                for line in lines:
                    self.assertTrue(
                        line.strip().startswith(PREFIX + RS),
                        f"{name}: rs command lacks the prefix: {line}",
                    )


class ReviewSkillTextTest(unittest.TestCase):
    text = (SKILLS / "review" / "SKILL.md").read_text(encoding="utf-8")

    def test_review_skill_has_no_snapshot_step(self):
        lines = [line for line in self.text.splitlines() if "rs snapshot" in line or RS + " snapshot" in line]
        self.assertEqual(lines, [])

    def test_review_skill_pastes_review_notes(self):
        self.assertIn("review_notes", self.text)

    def test_argument_hint_lists_new_arguments(self):
        hint = next(line for line in self.text.splitlines() if line.startswith("argument-hint:"))
        self.assertIn("--notes-file", hint)
        self.assertIn("--related-branch", hint)


if __name__ == "__main__":
    unittest.main()
