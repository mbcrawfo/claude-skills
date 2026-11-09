"""Tests for brief.py: a unit's child prompt rendered from the templates and the run state."""

from __future__ import annotations

from pathlib import Path
import re
import shutil
import tempfile
import unittest
from unittest import mock

import helpers  # noqa: F401  (puts the scripts dir on sys.path)
from runstate import RunError, add_unit, init_run, set_option

# isort: split
import brief

GOLDEN = Path(__file__).resolve().parent / "golden"
RUN = "/tmp/x/run"  # the run folder the golden files show
SPEC = "docs/specs/widgets.md"
EXTRA = "Touch only src/widgets/."
SANDBOX = (
    "- If `sync_with_base_branch` refuses because the sync touches sandbox-protected files, push nothing and end"
    ' your turn with `[blocked]`, "blocks merge: no", and the paths it names.'
)
INTEGRATION = {"branch": "integration/widgets", "final_merge": "manager", "feature_review": True}


def add(state, name, **fields):
    add_unit(state, name, prereqs=None, branch=None, worktree=None, specs=None, **fields)


class BriefTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name) / "run"

    def make_run(self, integration=None):
        state = init_run(
            self.dir,
            repo="o/r",
            base="main",
            method="merge",
            wip=None,
            queue_mode="serial",
            now=0.0,
            integration=integration,
        )
        if integration:
            add(state, "integration", integration=True, title="integration", spec=SPEC)
        add(state, "u1", title="Add widgets", spec=SPEC)
        set_option(state, "manager", "local_abc")
        (self.dir / "facts.md").write_text("Run ./format.sh before validating.\n", encoding="utf-8")
        return state

    def render(self, state, unit="u1", **options):
        options.setdefault("extra", EXTRA)
        return brief.render(self.dir, state, unit, **options).replace(str(self.dir.absolute()), RUN)

    def golden(self, name):
        return (GOLDEN / name).read_text(encoding="utf-8")

    def renders(self):
        plain = self.render(self.make_run())
        self.dir = self.dir.with_name("run2")
        state = self.make_run(INTEGRATION)
        return plain, self.render(state), self.render(state, "integration")


class GoldenTests(BriefTestCase):
    def test_unit_brief_matches_golden(self):
        self.assertEqual(self.render(self.make_run()), self.golden("brief-unit.md"))

    def test_integration_unit_brief_matches_golden(self):
        self.assertEqual(self.render(self.make_run(INTEGRATION)), self.golden("brief-integration-unit.md"))

    def test_integration_child_brief_matches_golden(self):
        state = self.make_run(INTEGRATION)
        self.assertEqual(self.render(state, "integration"), self.golden("brief-integration-child.md"))

    def test_no_placeholders_left(self):
        for text in self.renders():
            self.assertNotIn("{{", text)

    def test_plain_run_mentions_no_integration_branch(self):
        self.assertNotIn("integration/", self.render(self.make_run()))


class IntegrationRulesTests(BriefTestCase):
    def test_integration_unit_rules(self):
        text = self.render(self.make_run(INTEGRATION))
        part3 = next(line for line in text.splitlines() if line.startswith("3. **Start:** "))
        self.assertTrue(part3[len("3. **Start:** ") :].startswith("`git fetch origin integration/widgets`"), part3)
        self.assertIn("/branch-review:review --base origin/integration/widgets", text)
        self.assertIn(SANDBOX, text.splitlines())
        # Every call of the tool names the integration branch; the sandbox line only says what a refusal means.
        calls = text.replace(SANDBOX, "")
        found = [m.start() for m in re.finditer("sync_with_base_branch", calls)]
        self.assertTrue(found)
        for start in found:
            sentence = re.split(r"\.(?:\s|$)", calls[start:], maxsplit=1)[0]
            self.assertIn("base: integration/widgets", sentence)

    def test_plain_unit_has_no_sandbox_line(self):
        self.assertNotIn("sandbox-protected", self.render(self.make_run()))

    def test_integration_child_uses_the_run_base(self):
        text = self.render(self.make_run(INTEGRATION), "integration")
        self.assertIn("/branch-review:review --base origin/main`", text)
        self.assertIn("'^integration (open|stop|final)'", text)
        self.assertNotIn("sandbox-protected", text)
        self.assertNotIn("11. One open PR", text)


class OptionTests(BriefTestCase):
    def test_no_gate_drops_part_2(self):
        state = self.make_run()
        self.assertIn("2. **Gate:**", self.render(state))
        text = self.render(state, gate=False)
        self.assertNotIn("**Gate:**", text)
        self.assertNotIn("gates.log", text)
        self.assertIn("\n\n3. **Start:** ", text)

    def test_designed_adds_the_designing_paragraph(self):
        state = self.make_run()
        paragraph = "While you are designing, begin every final message with `[designing]`."
        self.assertNotIn(paragraph, self.render(state))
        text = self.render(state, designed=True)
        self.assertIn(paragraph, text)
        self.assertLess(text.index("6. **Unit-specific instructions:**"), text.index(paragraph))
        self.assertLess(text.index(paragraph), text.index("7. **Pull request:**"))

    def test_empty_facts_and_extra_leave_no_gap(self):
        state = self.make_run()
        (self.dir / "facts.md").unlink()
        self.assertIn("6. **Unit-specific instructions:**\n\n7. **Pull request:**", self.render(state, extra=""))

    def test_title_defaults_to_the_name(self):
        state = self.make_run()
        state["units"]["u1"]["title"] = None
        self.assertTrue(self.render(state).startswith("1. **Goal:** implement `u1` as specified"))

    def test_extra_is_not_read_for_placeholders(self):
        text = self.render(self.make_run(), extra="Use ${{ github.ref }} and {{name}} as written.")
        self.assertIn("Use ${{ github.ref }} and {{name}} as written.", text)


class CardTests(BriefTestCase):
    def card(self, state, name="c1", **options):
        options.setdefault("title", "Fix the widget docs")
        options.setdefault("spec", "docs/cards/c1.md")
        return brief.render_card(self.dir, state, name, **options).replace(str(self.dir.absolute()), RUN)

    def test_card_renders_without_unit(self):
        state = self.make_run()
        text = self.card(state, extra=EXTRA)
        self.assertNotIn("c1", state["units"])
        goal = "1. **Goal:** implement `Fix the widget docs` as specified in `docs/cards/c1.md`"
        self.assertTrue(text.startswith(goal), text[:120])
        self.assertIn("2. **Gate:**", text)
        self.assertIn(f"f=\"{RUN}/gates.log\"; until l=$(grep -E '^c1 (open|stop)'", text)
        self.assertIn(EXTRA, text)
        self.assertIn("11. One open PR", text)
        self.assertNotIn("{{", text)

    def test_card_refuses_existing_unit(self):
        state = self.make_run()
        with self.assertRaisesRegex(RunError, "already exists"):
            self.card(state, "u1")

    def test_card_refuses_bad_name_title_spec_and_no_manager(self):
        state = self.make_run()
        with self.assertRaisesRegex(RunError, "bad unit name"):
            self.card(state, "-c")
        with self.assertRaisesRegex(RunError, "bad title"):
            self.card(state, title="a\nb")
        with self.assertRaisesRegex(RunError, "bad spec"):
            self.card(state, spec="")
        state["manager"] = None
        with self.assertRaisesRegex(RunError, "no manager session id"):
            self.card(state)

    def test_card_in_integration_run_targets_integration_branch(self):
        text = self.card(self.make_run(INTEGRATION))
        self.assertIn("as one PR to `integration/widgets`", text)
        self.assertIn("/branch-review:review --base origin/integration/widgets", text)
        self.assertIn(SANDBOX, text.splitlines())
        self.assertIn("'^c1 (open|stop)'", text)
        self.assertNotIn("Integration branch.", text)
        self.assertNotIn("{{", text)


class RefusalTests(BriefTestCase):
    def test_unknown_unit(self):
        with self.assertRaisesRegex(RunError, "unknown unit"):
            brief.render(self.dir, self.make_run(), "nope")

    def test_no_manager(self):
        state = self.make_run()
        state["manager"] = None
        with self.assertRaisesRegex(RunError, r"^no manager session id: run\.py set .* manager <id>$"):
            brief.render(self.dir, state, "u1")

    def test_no_spec(self):
        state = self.make_run()
        state["units"]["u1"]["spec"] = None
        with self.assertRaisesRegex(RunError, r"^unit u1 has no spec: run\.py unit .* u1 --spec P$"):
            brief.render(self.dir, state, "u1")

    def test_designed_integration_unit(self):
        with self.assertRaisesRegex(RunError, "integration unit"):
            brief.render(self.dir, self.make_run(INTEGRATION), "integration", designed=True)

    def test_unknown_placeholder(self):
        templates = self.dir.parent / "templates"
        shutil.copytree(brief.TEMPLATES / "fragments", templates / "fragments")
        (templates / "unit-brief.md").write_text("{{name}} {{nope}} {{gone}}\n", encoding="utf-8")
        with mock.patch.object(brief, "TEMPLATES", templates):
            with self.assertRaisesRegex(RunError, r"^brief left placeholders: \{\{nope\}\}, \{\{gone\}\}$"):
                brief.render(self.dir, self.make_run(), "u1")


if __name__ == "__main__":
    unittest.main()
