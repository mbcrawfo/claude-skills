"""Checks that the reviewer's prompt and the schema doc keep up with the state script."""

from pathlib import Path
import re
import unittest

import helpers  # noqa: F401  (puts scripts/ on sys.path)
import review_state

ROOT = Path(__file__).resolve().parent.parent
REVIEWER = (ROOT / "agents" / "reviewer.md").read_text(encoding="utf-8")
SCHEMA = (ROOT / "skills" / "review" / "finding-schema.md").read_text(encoding="utf-8")
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

    def test_reviewer_lists_new_inputs(self):
        inputs = section(REVIEWER, "Inputs")
        for name in ("EARLIER_NOT_CHECKED", "EARLIER_REVIEW_NOTES", "RELATED_BRANCHES"):
            with self.subTest(input=name):
                self.assertIn(f"`{name}`", inputs)


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
