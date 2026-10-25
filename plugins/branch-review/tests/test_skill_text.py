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


if __name__ == "__main__":
    unittest.main()
