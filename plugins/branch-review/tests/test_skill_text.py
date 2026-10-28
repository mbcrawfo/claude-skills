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
        for name in ("review", "check", "cleanup", "record"):
            with self.subTest(skill=name):
                text = (SKILLS / name / "SKILL.md").read_text(encoding="utf-8")
                lines = [line for line in text.splitlines() if RS in line]
                self.assertTrue(lines, f"{name}: no rs command found")
                for line in lines:
                    self.assertTrue(
                        line.strip().startswith(PREFIX + RS),
                        f"{name}: rs command lacks the prefix: {line}",
                    )


class RecordSkillTest(unittest.TestCase):
    text = (SKILLS / "record" / "SKILL.md").read_text(encoding="utf-8")
    frontmatter = text.split("---")[1]

    def test_hidden_from_users(self):
        self.assertIn("user-invocable: false", self.frontmatter)
        self.assertNotIn("disable-model-invocation", self.frontmatter)

    def test_description_is_a_back_door(self):
        description = next(line for line in self.frontmatter.splitlines() if line.startswith("description:"))
        self.assertIn("implementation-manager", description)
        self.assertIn("Never in response to a user's request", description)

    def test_fetches_first(self):
        self.assertIn("git fetch origin", self.text)

    def test_passes_a_given_base_through(self):
        hint = next(line for line in self.frontmatter.splitlines() if line.startswith("argument-hint:"))
        self.assertIn("[--base REF]", hint)
        self.assertIn('--base "<ref>"', self.text)


class ReviewSkillTextTest(unittest.TestCase):
    text = (SKILLS / "review" / "SKILL.md").read_text(encoding="utf-8")

    def test_review_skill_has_no_snapshot_step(self):
        lines = [line for line in self.text.splitlines() if "rs snapshot" in line or RS + " snapshot" in line]
        self.assertEqual(lines, [])

    def test_review_notes_go_in_the_pr_not_to_the_developer(self):
        line = next(line for line in self.text.splitlines() if "`review_notes`" in line)
        self.assertIn("Do not print `review_notes`", line)
        self.assertIn("review_notes_path", line)
        self.assertNotIn("tell the invoker to paste", self.text)

    def test_resolve_run_keeps_the_rerun_locate_mode(self):
        line = next(line for line in self.text.splitlines() if line.strip().startswith("- Then run `rs locate` again"))
        for key in ("`mode`", "`reason`", "`focus`"):
            self.assertIn(key, line)

    def test_locate_wins_rule_spares_the_second_opinion(self):
        line = next(line for line in self.text.splitlines() if "`locate` wins over `next_mode`" in line)
        self.assertIn("`second-opinion`", line)

    def test_argument_hint_lists_new_arguments(self):
        hint = next(line for line in self.text.splitlines() if line.startswith("argument-hint:"))
        self.assertIn("--notes-file", hint)
        self.assertIn("--related-branch", hint)


if __name__ == "__main__":
    unittest.main()
