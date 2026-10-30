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
    drop_old_generation,
    drop_unit_items,
    forward_due,
    header,
    mark_forwarded,
    park_item,
    queue_order,
    unpark_item,
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

    def test_queue_order_stays_on_each_unit_it_moves_to(self):
        self.add("a", "Q1")  # current
        self.add("b", "Q1")
        self.add("c", "Q1")
        self.add("b", "Q2")
        self.assertEqual(self.order(), ["b-1", "b-2", "c-1"])

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
        self.assertEqual(self.add("a", "Q3", depends=["Q2"]), "parked a-3 with a-2")
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

    def test_queued_item_of_the_unit_holds_its_forward(self):
        self.add("b", "Q1")  # current
        self.add("a", "Q1")
        self.add("a", "Q2")
        done_item(self.dir, self.state, "a-1", "yes")  # out of order; a-2 still queued
        self.assertEqual(forward_due(self.state), [])
        self.assertIn("forward due a a-1 a-2", done_item(self.dir, self.state, "a-2", "no"))

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

    def test_mark_forwarded_refuses_a_unit_that_is_not_forward_due(self):
        self.add("a", "Q1")
        self.add("a", "Q2")
        done_item(self.dir, self.state, "a-1", "x")  # a-2 current, so no forward due for a
        self.assertEqual(forward_due(self.state), [])
        self.assertRefused(
            r"unit 'a' is not forward due: it still has a current or queued item", mark_forwarded, self.state, "a"
        )
        self.add("a", "Q3")
        park_item(self.state, "a-2", "later", [], now=50)  # a-3 current
        self.assertRefused(r"unit 'a' is not forward due", mark_forwarded, self.state, "a")
        park_item(self.state, "a-3", "later", [], now=51)  # only parked items left: forward due
        self.assertEqual(forward_due(self.state), ["forward due a a-1"])
        self.assertEqual(mark_forwarded(self.state, "a"), "forwarded a-1")

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

    def test_drop_unit_items_takes_answered_items(self):
        self.add("a", "Q1")
        self.add("b", "Q1")
        done_item(self.dir, self.state, "b-1", "x")  # answered, forward due
        self.assertEqual(forward_due(self.state), ["forward due b b-1"])
        self.assertEqual(drop_unit_items(self.state, "b", "unit stopped"), ["dropped b-1"])
        self.assertEqual(self.item("b-1")["status"], "dropped")
        self.assertEqual(forward_due(self.state), [])

    def test_drop_unit_items_takes_parked_but_not_forwarded(self):
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

    def test_drop_old_generation_drops_open_items_of_earlier_sessions(self):
        add_unit(self.state, "a", prereqs=None, branch=None, worktree="/w/a1", specs=None)
        self.add("a", "Q1")
        done_item(self.dir, self.state, "a-1", "x")
        mark_forwarded(self.state, "a")  # a-1 forwarded
        self.add("a", "Q2")
        done_item(self.dir, self.state, "a-2", "x")  # a-2 answered
        self.add("a", "Q3")
        self.add("a", "Q4")
        self.add("b", "Q1")
        park_item(self.state, "a-3", "later", [], now=50)  # a-3 parked, a-4 current
        add_unit(self.state, "a", prereqs=None, branch=None, worktree="/w/a2", specs=None)
        self.add("a", "Q1")  # a-5, generation 2, queued behind a-4
        out = drop_old_generation(self.state, "a", "new session")
        self.assertEqual(out, ["dropped a-2", "dropped a-3", "dropped a-4"])
        self.assertEqual(self.item("a-4")["reason"], "new session")
        self.assertEqual(self.item("a-1")["status"], "forwarded")
        self.assertEqual(self.item("a-5")["status"], "current")
        self.assertEqual(forward_due(self.state), [])
        self.assertEqual(drop_old_generation(self.state, "a", "new session"), [])

    def test_drop_old_generation_drops_answers_not_yet_forwarded(self):
        add_unit(self.state, "a", prereqs=None, branch=None, worktree="/w/a1", specs=None)
        self.add("a", "Q1")
        done_item(self.dir, self.state, "a-1", "x")
        add_unit(self.state, "a", prereqs=None, branch=None, worktree="/w/a2", specs=None)
        self.add("a", "Q1")  # a-2, generation 2
        self.assertEqual(drop_old_generation(self.state, "a", "new session"), ["dropped a-1"])
        done_item(self.dir, self.state, "a-2", "y")
        self.assertEqual(forward_due(self.state), ["forward due a a-2"])

    def test_drop_old_generation_leaves_other_units_alone(self):
        self.add("b", "Q1")
        add_unit(self.state, "a", prereqs=None, branch=None, worktree="/w/a1", specs=None)
        self.add("a", "Q1")
        add_unit(self.state, "a", prereqs=None, branch=None, worktree="/w/a2", specs=None)
        self.assertEqual(drop_old_generation(self.state, "a", "new session"), ["dropped a-1"])
        self.assertEqual(self.item("b-1")["status"], "current")

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


class ParkTests(ItemsTestCase):
    def test_park_takes_marked_dependents_and_promotes(self):
        self.add("a", "Q1")
        self.add("a", "Q2", depends=["Q1"])
        self.add("a", "Q3")
        self.add("b", "Q1")
        out = park_item(self.state, "a-1", "ask Sam", [], now=50)
        self.assertEqual(out.splitlines()[:2], ["parked a-1", "parked a-2"])
        self.assertEqual(self.state["items"]["current"], "a-3")  # unmarked a-3 stays, sticky a
        self.assertEqual(out.splitlines()[2], header(self.item("a-3")))
        self.assertEqual(
            (self.item("a-1")["note"], self.item("a-1")["parked_at"], self.item("a-1")["parked_with"]),
            ("ask Sam", 50, None),
        )
        self.assertEqual(
            (self.item("a-2")["note"], self.item("a-2")["parked_at"], self.item("a-2")["parked_with"]),
            ("with a-1", 50, "a-1"),
        )

    def test_park_with_and_keep_it(self):
        self.add("a", "Q1")
        self.add("a", "Q2")
        self.add("b", "Q1")
        park_item(self.state, "a-1", "ask Sam", ["a-2"], now=50)
        self.assertEqual(self.item("a-2")["parked_with"], "a-1")
        self.assertEqual(unpark_item(self.state, "a-2", to_queue=True), "queued a-2")
        self.assertEqual(self.state["items"]["current"], "b-1")
        self.assertEqual(self.order(), ["a-2"])
        self.assertIsNone(self.item("a-2")["parked_with"])
        self.assertIsNone(self.item("a-2")["note"])

    def test_park_with_dependents_of_with_items(self):
        self.add("a", "Q1")
        self.add("a", "Q2")
        self.add("a", "Q3", depends=["Q2"])
        self.add("b", "Q1")
        out = park_item(self.state, "a-1", "later", ["a-2"], now=5)
        self.assertEqual(out.splitlines()[:3], ["parked a-1", "parked a-2", "parked a-3"])
        self.assertEqual(self.item("a-3")["parked_with"], "a-1")
        self.assertEqual(self.item("a-3")["note"], "with a-1")

    def test_park_a_queued_item_keeps_the_current_one(self):
        self.add("a", "Q1")
        self.add("a", "Q2")
        out = park_item(self.state, "a-2", "later", [], now=5)
        self.assertEqual(out.splitlines(), ["parked a-2", header(self.item("a-1"))])
        self.assertEqual(self.state["items"]["current"], "a-1")

    def test_park_the_only_item_says_none(self):
        self.add("a", "Q1")
        self.assertEqual(park_item(self.state, "a-1", "later", [], now=5), "parked a-1\nnone")
        self.assertIsNone(self.state["items"]["current"])

    def test_unpark_to_queue_with_nothing_current_makes_it_current(self):
        self.add("a", "Q1")
        park_item(self.state, "a-1", "later", [], now=50)
        self.assertIsNone(self.state["items"]["current"])
        self.assertEqual(unpark_item(self.state, "a-1", to_queue=True), f"current {header(self.item('a-1'))}")
        self.assertEqual(self.state["items"]["current"], "a-1")

    def test_unpark_resumes_displaced_item_after_unit(self):
        self.add("a", "Q1")
        self.add("a", "Q2", depends=["Q1"])
        self.add("b", "Q1")
        self.add("b", "Q2")
        self.add("c", "Q1")
        park_item(self.state, "a-1", "later", [], now=50)  # a-2 goes too; b-1 current
        out = unpark_item(self.state, "a-1", to_queue=False)
        self.assertEqual(out, header(self.item("a-1")))
        self.assertEqual(self.state["items"]["current"], "a-1")
        self.assertEqual(self.state["items"]["resume"], "b-1")
        self.assertEqual(self.state["items"]["sticky"], "a")
        self.assertEqual(self.item("b-1")["status"], "queued")
        self.assertEqual(self.order(), ["a-2", "b-1", "b-2", "c-1"])

    def test_unpark_keeps_sticky_unit_items_before_the_resume_item(self):
        self.add("a", "Q1")
        self.add("b", "Q1")  # older than a-2 and a-3
        self.add("a", "Q2")
        park_item(self.state, "a-1", "later", ["a-2"], now=50)  # b-1 current
        self.add("a", "Q3")
        unpark_item(self.state, "a-1", to_queue=False)
        self.assertEqual(self.state["items"]["resume"], "b-1")
        self.assertEqual(self.order(), ["a-2", "a-3", "b-1"])

    def test_unpark_with_nothing_current(self):
        self.add("a", "Q1")
        park_item(self.state, "a-1", "later", [], now=50)
        self.assertEqual(unpark_item(self.state, "a-1", to_queue=False), header(self.item("a-1")))
        self.assertEqual(self.state["items"]["current"], "a-1")
        self.assertIsNone(self.state["items"]["resume"])
        self.assertIsNone(self.item("a-1")["note"])

    def test_item_depending_on_parked_item_is_parked_on_add(self):
        self.add("a", "Q1")
        park_item(self.state, "a-1", "later", [], now=50)
        self.add("a", "Q2", depends=["Q1"])
        self.assertEqual(self.item("a-2")["status"], "parked")
        unpark_item(self.state, "a-1", to_queue=False)
        self.assertEqual(self.item("a-2")["status"], "queued")

    def test_unpark_returns_a_chain(self):
        self.add("a", "Q1")
        self.add("b", "Q1")
        park_item(self.state, "a-1", "later", [], now=50)
        self.add("a", "Q2", depends=["Q1"])  # parked with a-1
        self.add("a", "Q3", depends=["Q2"])  # parked with a-2
        self.assertEqual(self.item("a-3")["parked_with"], "a-2")
        unpark_item(self.state, "a-1", to_queue=False)
        self.assertEqual([self.item(i)["status"] for i in ("a-2", "a-3")], ["queued", "queued"])
        self.assertEqual(self.order(), ["a-2", "a-3", "b-1"])
        self.assertEqual((self.item("a-3")["parked_with"], self.item("a-3")["note"]), (None, None))

    def test_parking_rest_of_unit_makes_forward_due(self):
        self.add("a", "Q1")
        self.add("a", "Q2")
        self.add("b", "Q1")
        done_item(self.dir, self.state, "a-1", "yes")
        self.assertIn("forward due a a-1", park_item(self.state, "a-2", "later", [], now=60))

    def test_parking_the_resume_item_clears_resume(self):
        self.add("b", "Q1")  # current, sticky b
        self.add("c", "Q1")  # c-1, older than b-2
        self.add("b", "Q2")
        done_item(self.dir, self.state, "b-1", "x")  # b-2 current: the owner stays on b
        self.add("a", "Q1")
        park_item(self.state, "a-1", "later", [], now=50)
        unpark_item(self.state, "a-1", to_queue=False)  # b-2 displaced: the resume item
        self.assertEqual(self.state["items"]["resume"], "b-2")
        park_item(self.state, "b-2", "later", [], now=51)
        self.assertIsNone(self.state["items"]["resume"])
        unpark_item(self.state, "b-2", to_queue=True)
        self.assertEqual(self.order(), ["c-1", "b-2"])  # the oldest unit first; b-2 does not jump it

    def test_unpark_refuses_an_item_that_is_not_parked(self):
        self.add("a", "Q1")
        self.add("a", "Q2")
        self.assertRefused(r"item a-1 is current", unpark_item, self.state, "a-1", to_queue=False)
        self.assertRefused(r"item a-2 is queued", unpark_item, self.state, "a-2", to_queue=True)
        self.assertRefused(r"unknown item 'zz-1'", unpark_item, self.state, "zz-1", to_queue=True)

    def test_park_refusals(self):
        self.add("a", "Q1")
        self.add("a", "Q2")
        self.add("b", "Q1")
        self.add("c", "Q1")
        park_item(self.state, "c-1", "later", [], now=1)
        done_item(self.dir, self.state, "a-1", "x")  # a-1 answered; a-2 current
        cases = [
            (r"item a-1 is answered", ("a-1", "n", [])),
            (r"item c-1 is parked", ("c-1", "n", [])),
            (r"unknown item 'zz-1'", ("zz-1", "n", [])),
            (r"item c-1 is parked", ("a-2", "n", ["c-1"])),
            (r"item a-1 is answered", ("a-2", "n", ["a-1"])),
            (r"unknown item 'zz-1'", ("a-2", "n", ["zz-1"])),
            (r"with itself", ("a-2", "n", ["a-2"])),
            (r"note", ("a-2", "", [])),
            (r"note", ("a-2", "   ", [])),
            (r"note", ("a-2", "two\nlines", [])),
        ]
        for regex, (item_id, note, with_ids) in cases:
            with self.subTest(item_id=item_id, note=note, with_ids=with_ids):
                self.assertRefused(regex, park_item, self.state, item_id, note, with_ids, now=9)


class ClosingParkedItemsTests(ItemsTestCase):
    def parked_pair(self):
        self.add("a", "Q1")
        self.add("a", "Q2", depends=["Q1"])
        self.add("a", "Q3", depends=["Q2"])
        self.add("b", "Q1")
        park_item(self.state, "a-1", "later", [], now=50)  # a-2 and a-3 parked with a-1; b-1 current

    def assertReturned(self):
        for item_id in ("a-2", "a-3"):
            record = self.item(item_id)
            self.assertEqual((record["status"], record["parked_with"], record["note"]), ("queued", None, None))

    def test_done_on_a_parked_item_returns_its_dependents(self):
        self.parked_pair()
        done_item(self.dir, self.state, "a-1", "x")
        self.assertReturned()

    def test_drop_on_a_parked_item_returns_its_dependents(self):
        self.parked_pair()
        drop_item(self.state, "a-1", "gone")
        self.assertReturned()

    def test_drop_unit_items_returns_dependents_in_other_units(self):
        self.add("a", "Q1")
        self.add("b", "Q1")
        park_item(self.state, "a-1", "later", ["b-1"], now=50)
        self.assertEqual(drop_unit_items(self.state, "a", "stopped"), ["dropped a-1"])
        self.assertEqual(self.item("b-1")["status"], "current")
        self.assertIsNone(self.item("b-1")["parked_with"])

    def test_closing_the_resume_item_clears_resume(self):
        for close in (
            lambda: done_item(self.dir, self.state, "b-1", "x"),
            lambda: drop_item(self.state, "b-1", "gone"),
            lambda: drop_unit_items(self.state, "b", "stopped"),
        ):
            with self.subTest(close=close):
                self.setUp()
                self.add("a", "Q1")
                self.add("b", "Q1")
                self.add("c", "Q1")
                park_item(self.state, "a-1", "later", [], now=1)  # b-1 current
                unpark_item(self.state, "a-1", to_queue=False)  # b-1 queued, resume
                self.assertEqual(self.state["items"]["resume"], "b-1")
                close()
                self.assertNotEqual(self.state["items"]["resume"], "b-1")
                self.assertIsNone(self.state["items"]["resume"])


if __name__ == "__main__":
    unittest.main()
