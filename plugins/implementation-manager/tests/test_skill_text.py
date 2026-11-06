"""Checks on the implementation-manager skill's command text."""

from pathlib import Path
import unittest

SKILL = Path(__file__).resolve().parent.parent / "skills" / "implementation-manager" / "SKILL.md"


class PruneStepTest(unittest.TestCase):
    text = SKILL.read_text(encoding="utf-8")

    def test_prune_runs_before_init_with_retention_option(self):
        lines = self.text.splitlines()
        prune_at = next(i for i, line in enumerate(lines) if "run.py prune" in line)
        init_at = next(i for i, line in enumerate(lines) if "run.py init" in line)
        # Single quotes: an unexpanded ${user_config...} in double quotes is a shell "bad substitution".
        self.assertIn("run.py prune --days '${user_config.retention_days}'", lines[prune_at])
        self.assertLess(
            (prune_at, lines[prune_at].index("run.py prune")),
            (init_at, lines[init_at].index("run.py init")),
        )


if __name__ == "__main__":
    unittest.main()
