"""Checks that the reviewer's prompt and the schema doc keep up with the state script."""

import json
from pathlib import Path
import re
import unittest

import helpers  # noqa: F401  (puts scripts/ on sys.path)
import review_state

ROOT = Path(__file__).resolve().parent.parent
REVIEWER = (ROOT / "agents" / "reviewer.md").read_text(encoding="utf-8")
SCHEMA = (ROOT / "skills" / "review" / "finding-schema.md").read_text(encoding="utf-8")
SKILL = (ROOT / "skills" / "review" / "SKILL.md").read_text(encoding="utf-8")
PROCESS_SECTIONS = (
    "Process for deep mode",
    "Process for second-opinion focus",
    "Process for follow-up mode",
    "Process for merge-check mode",
)


def section(text: str, title: str) -> str:
    match = re.search(rf"^## {re.escape(title)}\n(.*?)(?=^## |\Z)", text, re.DOTALL | re.MULTILINE)
    if match is None:
        raise AssertionError(f"no section {title!r}")
    return match.group(1)


class ReviewerPromptTest(unittest.TestCase):
    def test_every_process_section_ends_with_report_write(self):
        for title in PROCESS_SECTIONS:
            with self.subTest(section=title):
                steps = re.split(r"^\d+\. ", section(REVIEWER, title), flags=re.MULTILINE)
                self.assertGreater(len(steps), 1, "no numbered steps")
                last = steps[-1]
                self.assertIn("REPORT_PATH", last)
                self.assertIn("tail -n 3", last)
                self.assertIn("CHECK_COMMAND", last)

    def test_reviewer_lists_new_inputs(self):
        inputs = section(REVIEWER, "Inputs")
        for name in ("EARLIER_NOT_CHECKED", "EARLIER_REVIEW_NOTES", "RELATED_BRANCHES"):
            with self.subTest(input=name):
                self.assertIn(f"`{name}`", inputs)

    def test_dispatch_template_passes_the_check_command(self):
        dispatch = section(SKILL, "5. Dispatch")
        self.assertIn("CHECK_COMMAND", dispatch)
        self.assertIn("check-report --repo '<repo>' --file '<tmp>/round-<number>.md'", dispatch)
        self.assertIn("`CHECK_COMMAND`", section(REVIEWER, "Inputs"))


class ReviewerShapeTest(unittest.TestCase):
    """The block shape inlined in reviewer.md must match what the state script accepts."""

    @classmethod
    def setUpClass(cls):
        blocks = re.findall(r"^```json\n(.*?)^```$", section(REVIEWER, "Output"), re.DOTALL | re.MULTILINE)
        assert len(blocks) == 1, f"expected one json block in Output, found {len(blocks)}"
        cls.shape = json.loads(blocks[0])

    def enum(self, value):
        return tuple(value.split("|"))

    def test_top_level_keys(self):
        self.assertEqual(
            set(self.shape),
            {
                "summary",
                "merge_risk",
                "intent_check",
                "verdicts",
                "findings",
                "outside_change",
                "coverage",
                "not_checked",
            },
        )

    def test_enums_match_the_script(self):
        f = self.shape["findings"][0]
        self.assertEqual(self.enum(self.shape["merge_risk"]["level"]), review_state.RISK_LEVELS)
        self.assertEqual(self.enum(self.shape["verdicts"][0]["status"]), review_state.VERDICT_STATUSES)
        self.assertEqual(self.enum(f["severity"]), review_state.SEVERITIES)
        self.assertEqual(self.enum(f["category"]), review_state.CATEGORIES)
        self.assertEqual(self.enum(f["reachable_now"]), review_state.REACHABLE)
        self.assertEqual(self.enum(f["effort"]), review_state.EFFORTS)
        self.assertEqual(self.enum(f["variants"][0]["result"]), review_state.VARIANT_RESULTS)

    def test_finding_keys_match_the_script(self):
        keys = set(self.shape["findings"][0])
        expected = set(review_state.FINDING_REQUIRED) | set(review_state.FINDING_OPTIONAL)
        self.assertEqual(keys, expected | {"effort", "variants", "fix_options"})

    def test_coverage_items_are_listed(self):
        output = section(REVIEWER, "Output")
        listed = re.search(r"`coverage` has one entry for each of (.*?)\.\n", output).group(1)
        self.assertEqual(re.findall(r"`([a-z-]+)`", listed), list(review_state.COVERAGE_ITEMS))


class SchemaDocTest(unittest.TestCase):
    def test_schema_documents_every_coverage_item(self):
        for item in review_state.COVERAGE_ITEMS:
            with self.subTest(item=item):
                self.assertIn(f"`{item}`", SCHEMA)

    def test_schema_documents_every_status(self):
        table = section(SCHEMA, "Statuses and transitions")
        for status in review_state.STATUSES:
            with self.subTest(status=status):
                self.assertIn(f"| `{status}` |", table)


if __name__ == "__main__":
    unittest.main()
