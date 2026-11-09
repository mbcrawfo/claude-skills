"""Checks on the implementation-manager skill's text: the compaction-safe core and its reference files."""

from pathlib import Path
import re
import unittest

SKILL_DIR = Path(__file__).resolve().parent.parent / "skills" / "implementation-manager"
SKILL = SKILL_DIR / "SKILL.md"
REFERENCES = SKILL_DIR / "references"

# After a compaction Claude Code re-injects only the first 5,000 tokens of an invoked skill; this is the
# character proxy for that limit.
CORE_LIMIT = 17500
COMPACT_LINE = (
    "/compact Resume as the implementation manager for <run>: read notes.md, then run.py status, before acting."
)


def _references() -> dict[str, str]:
    return {p.name: p.read_text(encoding="utf-8") for p in sorted(REFERENCES.glob("*.md"))}


class CoreTest(unittest.TestCase):
    text = SKILL.read_text(encoding="utf-8")

    def test_core_under_limit(self):
        self.assertLess(len(self.text), CORE_LIMIT)

    def test_no_dollar_digit(self):
        # Claude Code replaces a dollar sign followed by a digit with a skill argument when the skill loads.
        self.assertIsNone(re.search(r"\$[0-9]", self.text))

    def test_core_has_compact_line(self):
        self.assertTrue(COMPACT_LINE in self.text, "no compact line")

    def test_core_has_resume_rule(self):
        paragraphs = re.split(r"\n\s*\n", self.text)
        self.assertTrue(
            any("compacted" in p and "notes.md" in p and "run.py status" in p for p in paragraphs),
            "no paragraph names compacted, notes.md and run.py status together",
        )

    def test_prune_uses_retention_option(self):
        # Single quotes: an unexpanded ${user_config...} in double quotes is a shell "bad substitution".
        prune = "run.py prune <run> --days '${user_config.retention_days}'"
        self.assertTrue(prune in self.text, f"no {prune!r}")
        setup = (REFERENCES / "setup.md").read_text(encoding="utf-8")
        self.assertTrue("prune" in setup and "run.py init" in setup, "setup.md names no prune or no run.py init")
        self.assertLess(setup.index("prune"), setup.index("run.py init"))


class ReferencesTest(unittest.TestCase):
    def test_references_have_no_placeholders(self):
        # Only SKILL.md is substituted when the skill loads; a reference keeps its placeholders as written.
        refs = _references()
        self.assertTrue(refs)
        for name, text in refs.items():
            self.assertFalse("${" in text or "$ARGUMENTS" in text, name)

    def test_every_named_reference_exists(self):
        core = SKILL.read_text(encoding="utf-8")
        named = set(re.findall(r"references/([\w-]+\.md)", core))
        on_disk = set(_references())
        self.assertTrue(named)
        self.assertEqual(named - on_disk, set(), "named in SKILL.md but missing")
        self.assertEqual(on_disk - named, set(), "in references/ but not named in SKILL.md")

    def test_wait_is_full_everywhere(self):
        texts = {"SKILL.md": SKILL.read_text(encoding="utf-8"), **_references()}
        full = re.compile(r"run\.py wait <run>( \[?--stall-hours [^\s\]]+\]?)? --full")
        seen = 0
        for name, text in texts.items():
            for m in re.finditer(r"run\.py wait", text):
                seen += 1
                self.assertIsNotNone(full.match(text, m.start()), f"{name}: {text[m.start() : m.start() + 60]!r}")
        self.assertGreater(seen, 0)

    def test_merge_bar_same_sha(self):
        text = (REFERENCES / "merge-bar.md").read_text(encoding="utf-8")
        bullets = [line for line in text.splitlines() if line.lstrip().startswith("- ") and "verdict" in line]
        self.assertTrue(bullets, "no merge bar bullet mentions the verdict")
        for line in bullets:
            self.assertTrue("same" in line and "sha" in line, line)


if __name__ == "__main__":
    unittest.main()
