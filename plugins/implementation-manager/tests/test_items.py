"""Tests for the owner queue: adding, ordering, answering, forwarding and dropping items."""

from __future__ import annotations

import copy
import itertools
from pathlib import Path
import tempfile
import unittest

import helpers  # noqa: F401  (puts the scripts dir on sys.path)
from items import (
    add_item,
    current_text,
    done_item,
    drop_item,
    drop_unit_items,
    forward_due,
    header,
    mark_forwarded,
    queue_order,
)
from runstate import RunError, add_unit, init_run


class ItemsTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name) / "run"
        self.state = init_run(self.dir, repo="o/r", base="main", method="merge", wip=None, queue_mode="serial", now=0)
        for name in ("a", "b", "c"):
            add_unit(self.state, name, prereqs=None, branch=None, worktree=None, specs=None)
        self.clock = itertools.count(1)

    def add(self, unit, ref=None, kind="question", depends=(), blocks=False):
        return add_item(
            self.dir,
            self.state,
            unit,
            ref=ref,
            kind=kind,
            blocks=blocks,
            depends=list(depends),
            summary=f"{unit} {ref}",
            body="text",
            now=next(self.clock),
        )

    def item(self, item_id):
        return next(i for i in self.state["items"]["list"] if i["id"] == item_id)

    def order(self):
        return [i["id"] for i in queue_order(self.state)]

    def assertRefused(self, regex, fn, *args, **kwargs):
        before = copy.deepcopy(self.state["items"])
        with self.assertRaisesRegex(RunError, regex):
            fn(*args, **kwargs)
        self.assertEqual(self.state["items"], before)


class AddAndOrderTests(ItemsTestCase):
    def test_first_item_becomes_current(self):
        self.assertTrue(self.add("a", "Q1").startswith("current a-1 (Q1) · a · question"))
        self.assertEqual(self.state["items"]["current"], "a-1")
        self.assertEqual(self.item("a-1")["status"], "current")
        self.assertEqual(self.state["items"]["sticky"], "a")

    def test_add_writes_the_body_file_and_record(self):
        self.add("a", "Q1")
        self.assertEqual((self.dir / "items" / "a-1.md").read_text(), "text")
        record = self.item("a-1")
        self.assertEqual(
            {k: record[k] for k in ("unit", "ref", "generation", "kind", "blocks", "depends", "summary", "seq")},
            {
                "unit": "a",
                "ref": "Q1",
                "generation": 1,
                "kind": "question",
                "blocks": False,
                "depends": [],
                "summary": "a Q1",
                "seq": 1,
            },
        )
        self.assertEqual(self.state["items"]["counters"], {"a": 1})

    def test_sticky_unit_beats_older_other_unit(self):
        self.add("a", "Q1")
        self.add("b", "Q1")
        self.add("a", "Q2")
        self.assertEqual(self.order(), ["a-2", "b-1"])
        done_item(self.dir, self.state, "a-1", "yes")
        self.add("a", "Q3")  # arrives while the owner is on a
        self.assertEqual(self.order(), ["a-3", "b-1"])

    def test_next_unit_is_the_one_with_the_oldest_item(self):
        for unit, ref in (("a", "Q1"), ("c", "Q1"), ("b", "Q1"), ("b", "Q2"), ("c", "Q2")):
            self.add(unit, ref)
        done_item(self.dir, self.state, "a-1", "x")
        self.assertEqual([self.state["items"]["current"]] + self.order(), ["c-1", "c-2", "b-1", "b-2"])

    def test_blocking_does_not_jump(self):
        self.add("a", "Q1")
        self.add("a", "Q2")
        self.add("b", "Q1", kind="finding", blocks=True)
        self.assertEqual(self.order(), ["a-2", "b-1"])

    def test_resume_item_is_picked_before_the_oldest(self):
        self.add("a", "Q1")
        self.add("b", "Q1")
        self.add("c", "Q1")
        self.state["items"]["resume"] = "c-1"
        self.state["items"]["sticky"] = None
        self.assertEqual(self.order(), ["c-1", "b-1"])

    def test_promote_clears_resume_when_it_becomes_current(self):
        self.add("a", "Q1")
        self.add("b", "Q1")
        self.state["items"]["resume"] = "b-1"
        done_item(self.dir, self.state, "a-1", "x")
        self.assertEqual(self.state["items"]["current"], "b-1")
        self.assertIsNone(self.state["items"]["resume"])

    def test_queued_output_counts_waiting(self):
        self.add("a", "Q1")
        self.assertEqual(self.add("b", "Q1"), "queued b-1 for b, 1 waiting")
        self.assertEqual(self.add("c", "Q1"), "queued c-1 for c, 2 waiting")

    def test_run_wide_decision(self):
        out = add_item(
            self.dir,
            self.state,
            "",
            ref=None,
            kind="decision",
            blocks=True,
            depends=[],
            summary="every unit stopped",
            body="x",
            now=1,
        )
        self.assertEqual(out, "current run-1 · run · decision · blocks merge: yes · every unit stopped")
        self.assertEqual(self.item("run-1")["unit"], "")
        self.assertEqual(self.state["items"]["sticky"], "")
        self.assertEqual((self.dir / "items" / "run-1.md").read_text(), "x")

    def test_header_shows_depends(self):
        self.add("a", "Q1")
        self.add("a", "Q2", depends=["Q1"])
        self.assertEqual(
            header(self.item("a-2")), "a-2 (Q2) · a · question · blocks merge: no · depends on: a-1 · a Q2"
        )

    def test_duplicate_ref_names_existing_item(self):
        self.add("a", "Q1")
        with self.assertRaisesRegex(RunError, r"ref Q1 of unit 'a' is already a-1"):
            self.add("a", "Q1")

    def test_duplicate_ref_counts_closed_items(self):
        self.add("a", "Q1")
        drop_item(self.state, "a-1", "gone")
        self.assertRefused(r"ref Q1 of unit 'a' is already a-1", self.add, "a", "Q1")

    def test_new_generation_reuses_refs_and_scopes_depends(self):
        self.add("a", "Q1")
        add_unit(self.state, "a", prereqs=None, branch=None, worktree="/w1", specs=None)
        add_unit(self.state, "a", prereqs=None, branch=None, worktree="/w2", specs=None)
        self.add("a", "Q1")  # a-2, generation 2
        self.add("a", "Q2", depends=["Q1"])
        self.assertEqual(self.item("a-3")["depends"], ["a-2"])
        self.assertEqual(self.item("a-2")["generation"], 2)

    def test_depends_on_parked_item_is_stored_parked(self):
        self.add("a", "Q1")
        self.add("a", "Q2")
        self.item("a-2").update(status="parked", parked_at=1, parked_with=None, note="later")
        self.add("a", "Q3", depends=["Q2"])
        record = self.item("a-3")
        self.assertEqual(
            (record["status"], record["parked_with"], record["note"], record["parked_at"]),
            ("parked", "a-2", "depends on a-2", record["added"]),
        )
        self.assertNotIn("a-3", self.order())

    def test_refusals(self):
        self.add("a", "Q1")
        self.state["units"]["c"]["gate"] = "stopped"
        base = dict(ref=None, kind="question", blocks=False, depends=[], summary="s", body="b", now=1)

        def attempt(unit, **overrides):
            return (self.dir, self.state, unit), {**base, **overrides}

        cases = [
            ("unknown unit 'zz'", "zz", dict(ref="Q1")),
            ("unit 'c' is stopped", "c", dict(ref="Q1")),
            ("kind", "b", dict(ref="Q1", kind="idea")),
            ("run-wide", "", dict(kind="question")),
            ("decision", "b", dict(ref="Q1", kind="decision")),
            ("run-wide", "", dict(ref="Q1", kind="decision")),
            ("ref", "b", dict(ref="q1")),
            ("ref", "b", dict(ref="Q0")),
            ("depends", "b", dict(depends=["Q1"])),
            ("depends", "b", dict(ref="Q2", depends=["Q1"])),
            ("summary", "b", dict(ref="Q1", summary="two\nlines")),
            ("summary", "b", dict(ref="Q1", summary="")),
        ]
        for regex, unit, overrides in cases:
            with self.subTest(unit=unit, overrides=overrides):
                args, kwargs = attempt(unit, **overrides)
                self.assertRefused(regex, add_item, *args, **kwargs)
        self.assertEqual([i["id"] for i in self.state["items"]["list"]], ["a-1"])
        self.assertFalse((self.dir / "items" / "b-1.md").exists())


class DoneAndForwardTests(ItemsTestCase):
    def test_done_answered_then_forward_due_when_leaving_unit(self):
        self.add("a", "Q1")
        self.add("a", "Q2")
        self.add("b", "Q1")
        out = done_item(self.dir, self.state, "a-1", "keep it")
        self.assertEqual(out, header(self.item("a-2")))  # no forward due yet
        self.assertEqual((self.dir / "items" / "a-1.answer.md").read_text(), "keep it")
        self.assertEqual(self.item("a-1")["status"], "answered")
        out = done_item(self.dir, self.state, "a-2", "drop it")
        self.assertEqual(out.splitlines(), [header(self.item("b-1")), "forward due a a-1 a-2"])
        self.assertEqual(mark_forwarded(self.state, "a"), "forwarded a-1 a-2")
        self.assertEqual(forward_due(self.state), [])
        self.assertEqual(self.item("a-1")["status"], "forwarded")

    def test_done_with_nothing_left_says_none(self):
        self.add("a", "Q1")
        self.assertEqual(done_item(self.dir, self.state, "a-1", "x").splitlines(), ["none", "forward due a a-1"])
        self.assertIsNone(self.state["items"]["current"])

    def test_out_of_order_answer(self):
        self.add("a", "Q1")
        self.add("b", "Q1")
        self.assertIn("forward due b b-1", done_item(self.dir, self.state, "b-1", "yes"))
        self.assertEqual(self.state["items"]["current"], "a-1")

    def test_forward_due_orders_units_by_earliest_answered_item(self):
        self.add("c", "Q1")
        self.add("a", "Q1")
        self.add("b", "Q1")
        done_item(self.dir, self.state, "b-1", "x")
        done_item(self.dir, self.state, "a-1", "x")
        done_item(self.dir, self.state, "c-1", "x")
        self.assertEqual(forward_due(self.state), ["forward due c c-1", "forward due a a-1", "forward due b b-1"])

    def test_decision_needs_no_answer_and_is_not_forwarded(self):
        add_item(
            self.dir,
            self.state,
            "",
            ref=None,
            kind="decision",
            blocks=False,
            depends=[],
            summary="every unit stopped",
            body="x",
            now=1,
        )
        done_item(self.dir, self.state, "run-1", None)
        self.assertEqual(self.item("run-1")["status"], "forwarded")
        self.assertEqual(forward_due(self.state), [])
        self.assertFalse((self.dir / "items" / "run-1.answer.md").exists())

    def test_done_refuses_missing_answer_and_closed_items(self):
        self.add("a", "Q1")
        self.assertRefused(r"needs an answer", done_item, self.dir, self.state, "a-1", None)
        self.assertRefused(r"unknown item 'zz-1'", done_item, self.dir, self.state, "zz-1", "x")
        done_item(self.dir, self.state, "a-1", "x")
        self.assertRefused(r"item a-1 is answered", done_item, self.dir, self.state, "a-1", "x")
        mark_forwarded(self.state, "a")
        self.assertRefused(r"item a-1 is forwarded", done_item, self.dir, self.state, "a-1", "x")
        self.add("a", "Q2")
        drop_item(self.state, "a-2", "gone")
        self.assertRefused(r"item a-2 is dropped", done_item, self.dir, self.state, "a-2", "x")

    def test_done_accepts_a_parked_item(self):
        self.add("a", "Q1")
        self.add("b", "Q1")
        self.item("b-1").update(status="parked", parked_at=1, parked_with=None, note="later")
        done_item(self.dir, self.state, "b-1", "x")
        self.assertEqual(self.item("b-1")["status"], "answered")

    def test_mark_forwarded_refuses_a_unit_with_nothing_answered(self):
        self.add("a", "Q1")
        self.assertRefused(r"unit 'a' has no answered items", mark_forwarded, self.state, "a")

    def test_current_text(self):
        self.assertEqual(current_text(self.dir, self.state), "none")
        self.add("a", "Q1")
        self.assertEqual(current_text(self.dir, self.state), header(self.item("a-1")) + "\n\ntext")


class DropTests(ItemsTestCase):
    def test_drop_unit_items_promotes_next(self):
        self.add("a", "Q1")
        self.add("a", "Q2")
        self.add("b", "Q1")
        self.assertEqual(drop_unit_items(self.state, "a", "unit stopped"), ["dropped a-1", "dropped a-2"])
        self.assertEqual(self.state["items"]["current"], "b-1")
        self.assertEqual(self.item("a-1")["status"], "dropped")
        self.assertEqual(self.item("a-1")["reason"], "unit stopped")

    def test_drop_unit_items_takes_answered_and_parked_but_not_forwarded(self):
        self.add("a", "Q1")
        self.add("a", "Q2")
        self.add("a", "Q3")
        done_item(self.dir, self.state, "a-1", "x")
        self.item("a-3").update(status="parked", parked_at=1, parked_with=None, note="later")
        done_item(self.dir, self.state, "a-2", "x")
        mark_forwarded(self.state, "a")
        self.add("a", "Q4")
        self.assertEqual(drop_unit_items(self.state, "a", "unit stopped"), ["dropped a-3", "dropped a-4"])
        self.assertEqual(self.item("a-1")["status"], "forwarded")

    def test_drop_item(self):
        self.add("a", "Q1")
        self.add("b", "Q1")
        drop_item(self.state, "a-1", "not needed")
        self.assertEqual(self.item("a-1")["status"], "dropped")
        self.assertEqual(self.state["items"]["current"], "b-1")

    def test_drop_item_refusals(self):
        self.add("a", "Q1")
        self.assertRefused(r"unknown item 'zz-1'", drop_item, self.state, "zz-1", "r")
        drop_item(self.state, "a-1", "r")
        self.assertRefused(r"item a-1 is dropped", drop_item, self.state, "a-1", "r")
        self.add("a", "Q2")
        self.item("a-2")["status"] = "forwarded"
        self.assertRefused(r"item a-2 is forwarded", drop_item, self.state, "a-2", "r")


if __name__ == "__main__":
    unittest.main()
