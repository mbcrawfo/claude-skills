"""Tests for run state: init, locking, units, gates and the WIP limit."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

import helpers  # noqa: F401  (puts the scripts dir on sys.path)
from runstate import (
    RunError,
    add_unit,
    enqueue,
    go,
    has_open_pr,
    init_run,
    load,
    locked,
    mergeable,
    open_gate,
    openable,
    queue_order,
    record_closed,
    record_merged,
    redo,
    reportable,
    set_option,
    stop_gate,
    yield_slot,
)

NOW = 1000.0
SHA1 = "1" * 40
SHA2 = "2" * 40


def add(state, name, prereqs=(), specs=False, after=()):
    add_unit(state, name, prereqs=list(prereqs), branch=None, worktree=None, specs=specs, after=list(after))


class RunStateTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name) / "run"

    def init(self, wip=None, queue_mode="serial"):
        return init_run(self.dir, repo="o/r", base="main", method="merge", wip=wip, queue_mode=queue_mode, now=NOW)

    def open_unit(self, state, name):
        open_gate(self.dir, state, name, NOW)


class InitTests(RunStateTestCase):
    def test_init_creates_files(self):
        state = self.init()
        for name in ("run.json", "requests.log", "pushes.log", "gates.log"):
            self.assertTrue((self.dir / name).exists())
        self.assertEqual(state["slot"], None)

    def test_init_writes_the_documented_shape(self):
        self.init()
        self.assertEqual(
            json.loads((self.dir / "run.json").read_text()),
            {
                "version": 1,
                "repo": "o/r",
                "base": "main",
                "method": "merge",
                "queue_mode": "serial",
                "wip": None,
                "units": {},
                "slot": None,
                "queue": [],
                "pending": [],
                "bad_requests": [],
                "requests_offset": 0,
                "marks": {},
            },
        )

    def test_init_refuses_existing_run(self):
        self.init()
        with self.assertRaises(RunError):
            self.init()

    def test_init_rejects_bad_options(self):
        with self.assertRaises(RunError):
            init_run(self.dir, repo="o/r", base="main", method="ff", wip=None, queue_mode="serial", now=NOW)
        with self.assertRaises(RunError):
            init_run(self.dir, repo="o/r", base="main", method="merge", wip=None, queue_mode="both", now=NOW)
        with self.assertRaises(RunError):
            init_run(self.dir, repo="o/r", base="main", method="merge", wip=0, queue_mode="serial", now=NOW)


class LockedTests(RunStateTestCase):
    def test_locked_writes_on_normal_exit(self):
        self.init()
        with locked(self.dir) as state:
            add(state, "a")
        self.assertIn("a", load(self.dir)["units"])
        self.assertEqual(list(self.dir.glob("*.tmp")), [])

    def test_locked_writes_atomically_and_not_on_error(self):
        self.init()
        before = (self.dir / "run.json").read_text()
        with self.assertRaises(ValueError):
            with locked(self.dir) as state:
                add(state, "a")
                raise ValueError("boom")
        self.assertEqual((self.dir / "run.json").read_text(), before)
        self.assertEqual(sorted(p.name for p in self.dir.iterdir() if p.name.endswith(".tmp")), [])

    def test_locked_without_a_run_is_an_error(self):
        with self.assertRaises(RunError):
            with locked(self.dir):
                pass

    def test_load_refuses_a_run_json_of_the_wrong_shape(self):
        self.init()
        good = json.loads((self.dir / "run.json").read_text())
        for bad in ({}, [], "x", {**good, "version": 2}, {k: v for k, v in good.items() if k != "units"}):
            with self.subTest(bad=bad):
                (self.dir / "run.json").write_text(json.dumps(bad))
                with self.assertRaises(RunError) as caught:
                    load(self.dir)
                self.assertIn("run.json", str(caught.exception))

    def test_run_json_is_pretty_printed(self):
        self.init()
        self.assertIn("\n  ", (self.dir / "run.json").read_text())


class UnitTests(RunStateTestCase):
    def test_add_unit_defaults_and_order(self):
        state = self.init()
        add(state, "a")
        add_unit(state, "b", prereqs=["a"], branch="feat/b", worktree="/w/b", specs=True)
        self.assertEqual(
            state["units"]["a"],
            {
                "order": 0,
                "prereqs": [],
                "after": [],
                "hold": None,
                "title": None,
                "branch": None,
                "worktree": None,
                "specs": False,
                "gate": "closed",
                "gate_at": None,
                "done": False,
                "prs": {},
            },
        )
        self.assertEqual(state["units"]["b"]["order"], 1)
        self.assertEqual(state["units"]["b"]["prereqs"], ["a"])
        self.assertEqual(state["units"]["b"]["branch"], "feat/b")
        self.assertTrue(state["units"]["b"]["specs"])

    def test_add_unit_update_keeps_none_fields_and_order(self):
        state = self.init()
        add_unit(state, "a", prereqs=[], branch="x", worktree="/w", specs=False)
        add(state, "b")
        add_unit(state, "a", prereqs=["b"], branch=None, worktree=None, specs=False)
        unit = state["units"]["a"]
        self.assertEqual((unit["branch"], unit["worktree"], unit["prereqs"], unit["order"]), ("x", "/w", ["b"], 0))

    def test_unknown_prereq_rejected(self):
        state = self.init()
        with self.assertRaises(RunError):
            add(state, "a", prereqs=["nope"])
        self.assertNotIn("a", state["units"])

    def test_bad_unit_name_rejected(self):
        state = self.init()
        for name in ("a b", "-x", "", "a/b", "1.2", "a.b"):
            with self.assertRaises(RunError, msg=name):
                add(state, name)

    def test_unit_name_charset_accepted(self):
        state = self.init()
        add(state, "1-2a_B")
        self.assertIn("1-2a_B", state["units"])


class OptionTests(RunStateTestCase):
    def test_set_wip_and_queue(self):
        state = self.init()
        set_option(state, "wip", "3")
        self.assertEqual(state["wip"], 3)
        set_option(state, "wip", "none")
        self.assertIsNone(state["wip"])
        set_option(state, "queue", "parallel")
        self.assertEqual(state["queue_mode"], "parallel")

    def test_bad_options_rejected(self):
        state = self.init()
        for key, value in (("wip", "0"), ("wip", "x"), ("wip", "-1"), ("queue", "fast"), ("color", "red")):
            with self.assertRaises(RunError, msg=f"{key}={value}"):
                set_option(state, key, value)


class OpenableTests(RunStateTestCase):
    def test_openable_respects_prereqs_and_order(self):
        state = self.init()
        add(state, "a")
        add(state, "b", ["a"])
        add(state, "c")
        self.assertEqual(openable(state), ["a", "c"])

    def test_openable_after_prereq_done(self):
        state = self.init()
        add(state, "a")
        add(state, "b", ["a"])
        self.open_unit(state, "a")
        state["units"]["a"]["done"] = True
        self.assertEqual(openable(state), ["b"])

    def test_openable_respects_wip(self):
        state = self.init(wip=2)
        for name in "abc":
            add(state, name)
        self.open_unit(state, "a")
        self.assertEqual(openable(state), ["b"])

    def test_done_units_free_wip(self):
        state = self.init(wip=1)
        add(state, "a")
        add(state, "b")
        self.open_unit(state, "a")
        state["units"]["a"]["done"] = True
        self.assertEqual(openable(state), ["b"])

    def test_specs_unit_not_counted_toward_wip(self):
        state = self.init(wip=1)
        add(state, "s", specs=True)
        add(state, "a")
        self.open_unit(state, "s")
        self.assertEqual(openable(state), ["a"])

    def test_specs_unit_is_never_cut(self):
        state = self.init(wip=1)
        add(state, "a")
        add(state, "s", specs=True)
        add(state, "b")
        self.open_unit(state, "a")
        self.assertEqual(openable(state), ["s"])

    def test_specs_unit_does_not_use_up_free_wip(self):
        state = self.init(wip=1)
        add(state, "s", specs=True)
        add(state, "a")
        add(state, "b")
        self.assertEqual(openable(state), ["s", "a"])

    def test_no_limit_is_unbounded(self):
        state = self.init()
        for name in "abcde":
            add(state, name)
        self.assertEqual(openable(state), list("abcde"))

    def test_lowering_wip_closes_nothing(self):
        state = self.init(wip=3)
        for name in "abc":
            add(state, name)
            self.open_unit(state, name)
        set_option(state, "wip", "1")
        self.assertEqual([state["units"][n]["gate"] for n in "abc"], ["open"] * 3)
        self.assertEqual(openable(state), [])

    def test_open_units_are_not_openable(self):
        state = self.init()
        add(state, "a")
        self.open_unit(state, "a")
        self.assertEqual(openable(state), [])


class GateTests(RunStateTestCase):
    def test_open_gate_appends_line(self):
        state = self.init()
        add(state, "a")
        open_gate(self.dir, state, "a", 1234.5)
        self.assertEqual((self.dir / "gates.log").read_text(), "a open\n")
        self.assertEqual(state["units"]["a"]["gate"], "open")
        self.assertEqual(state["units"]["a"]["gate_at"], 1234.5)
        with self.assertRaises(RunError):
            open_gate(self.dir, state, "a", 1300.0)
        self.assertEqual((self.dir / "gates.log").read_text(), "a open\n")

    def test_open_unknown_unit_rejected(self):
        state = self.init()
        with self.assertRaises(RunError):
            open_gate(self.dir, state, "zz", NOW)

    def test_stop_gate_appends_line(self):
        state = self.init()
        add(state, "b")
        stop_gate(self.dir, state, "b", "dropped by owner")
        self.assertEqual((self.dir / "gates.log").read_text(), "b stop dropped by owner\n")

    def test_gate_log_lines_accumulate(self):
        state = self.init()
        add(state, "a")
        add(state, "b")
        self.open_unit(state, "a")
        stop_gate(self.dir, state, "a", "x")
        self.assertEqual((self.dir / "gates.log").read_text(), "a open\na stop x\n")

    def test_stopped_unit_is_never_openable_and_frees_wip(self):
        state = self.init(wip=1)
        add(state, "a")
        add(state, "b")
        add(state, "c", prereqs=["b"])
        self.open_unit(state, "a")
        stop_gate(self.dir, state, "a", "dropped")
        stop_gate(self.dir, state, "b", "dropped")
        self.assertEqual(state["units"]["a"]["gate"], "stopped")
        self.assertEqual(state["units"]["b"]["gate"], "stopped")
        self.assertEqual(openable(state), [])  # b is never reopened; c waits on b; a no longer fills the WIP
        add(state, "d")
        self.assertEqual(openable(state), ["d"])

    def test_stop_frees_the_slot_and_drops_queue_and_pending(self):
        state = self.init()
        for name in "abc":
            add(state, name)
        go(self.dir, state, "a", SHA1, 5.0)
        enqueue(self.dir, state, "b", SHA2, 6.0)
        state["pending"].append({"unit": "a", "sha": SHA2, "branch": "feat/a", "at": 7.0})
        state["pending"].append({"unit": "c", "sha": SHA2, "branch": "feat/c", "at": 7.0})
        stop_gate(self.dir, state, "a", "dropped")
        stop_gate(self.dir, state, "b", "dropped")
        self.assertIsNone(state["slot"])
        self.assertEqual(state["queue"], [])
        self.assertEqual([entry["unit"] for entry in state["pending"]], ["c"])

    def test_stopped_unit_refuses_go_queue_and_open(self):
        state = self.init()
        add(state, "a")
        stop_gate(self.dir, state, "a", "dropped")
        for call in (
            lambda: go(self.dir, state, "a", SHA1, 5.0),
            lambda: enqueue(self.dir, state, "a", SHA1, 5.0),
            lambda: open_gate(self.dir, state, "a", 5.0),
        ):
            with self.assertRaises(RunError) as caught:
                call()
            self.assertIn("stop", str(caught.exception))
        self.assertEqual((self.dir / "gates.log").read_text(), "a stop dropped\n")
        self.assertEqual((self.dir / "pushes.log").read_text(), "")

    def test_stop_gate_rejects_unknown_unit_and_multiline_reason(self):
        state = self.init()
        add(state, "a")
        with self.assertRaises(RunError):
            stop_gate(self.dir, state, "zz", "r")
        with self.assertRaises(RunError):
            stop_gate(self.dir, state, "a", "two\nlines")


class SlotTests(RunStateTestCase):
    def setup_units(self, *names, queue_mode="serial"):
        state = self.init(queue_mode=queue_mode)
        for name in names:
            add(state, name)
        return state

    def pushes(self):
        return (self.dir / "pushes.log").read_text()

    def test_go_takes_free_slot(self):
        state = self.setup_units("a")
        state["marks"]["a"] = {"head": "abc"}
        go(self.dir, state, "a", SHA1, 5.0)
        self.assertEqual(state["slot"], {"unit": "a", "sha": SHA1, "at": 5.0, "head": "abc"})
        self.assertEqual(self.pushes(), f"a go {SHA1}\n")

    def test_go_taking_the_slot_drops_the_next_mark(self):
        state = self.setup_units("a")
        state["marks"]["_next"] = ["a", SHA1]
        go(self.dir, state, "a", SHA1, 5.0)
        self.assertNotIn("_next", state["marks"])

    def test_go_in_parallel_mode_keeps_the_next_mark(self):
        state = self.setup_units("a", queue_mode="parallel")
        state["marks"]["_next"] = ["a", SHA1]
        go(self.dir, state, "a", SHA1, 5.0)
        self.assertEqual(state["marks"]["_next"], ["a", SHA1])

    def test_go_without_head_mark_stores_none(self):
        state = self.setup_units("a")
        go(self.dir, state, "a", SHA1, 5.0)
        self.assertIsNone(state["slot"]["head"])

    def test_go_records_the_head_it_was_given_over(self):
        state = self.setup_units("a")
        state["marks"]["a"] = {"head": SHA2}
        go(self.dir, state, "a", SHA1, 5.0)
        self.assertEqual(state["marks"]["a"]["go_head"], SHA2)

    def test_go_in_parallel_mode_records_the_head_it_was_given_over(self):
        state = self.setup_units("a", queue_mode="parallel")
        state["marks"]["a"] = {"head": SHA2}
        go(self.dir, state, "a", SHA1, 5.0)
        self.assertEqual(state["marks"]["a"]["go_head"], SHA2)

    def test_go_records_none_without_a_head_mark(self):
        state = self.setup_units("a")
        go(self.dir, state, "a", SHA1, 5.0)
        self.assertIsNone(state["marks"]["a"]["go_head"])

    def test_go_for_the_head_already_pushed_holds_nothing(self):
        state = self.setup_units("a")
        state["marks"]["a"] = {"head": SHA1, "go_head": SHA2}
        go(self.dir, state, "a", SHA1, 5.0)
        self.assertNotIn("go_head", state["marks"]["a"])

    def test_go_refused_while_other_holds(self):
        state = self.setup_units("a", "b")
        go(self.dir, state, "a", SHA1, 5.0)
        with self.assertRaises(RunError) as ctx:
            go(self.dir, state, "b", SHA2, 6.0)
        self.assertIn("held by 'a'", str(ctx.exception))
        self.assertIn("queue", str(ctx.exception))
        self.assertEqual(state["slot"]["unit"], "a")
        self.assertEqual(self.pushes(), f"a go {SHA1}\n")

    def test_go_refused_while_another_unit_is_first_in_the_queue(self):
        state = self.setup_units("a", "b", "c")
        go(self.dir, state, "a", SHA1, 5.0)
        enqueue(self.dir, state, "b", SHA2, 6.0)
        yield_slot(state, "a", "owner question", 7.0)
        with self.assertRaises(RunError) as ctx:
            go(self.dir, state, "c", SHA1, 8.0)
        self.assertIn("'b' is first in the queue", str(ctx.exception))
        self.assertIn("use queue", str(ctx.exception))
        self.assertIsNone(state["slot"])
        self.assertEqual(self.pushes(), f"a go {SHA1}\n")
        go(self.dir, state, "b", SHA2, 9.0)  # the queue's first unit may take the slot
        self.assertEqual(state["slot"]["unit"], "b")
        self.assertEqual(state["queue"], [])

    def test_go_ignores_queue_entries_of_done_units(self):
        state = self.setup_units("a", "b")
        enqueue(self.dir, state, "b", SHA2, 6.0)
        state["units"]["b"]["done"] = True
        go(self.dir, state, "a", SHA1, 7.0)
        self.assertEqual(state["slot"]["unit"], "a")

    def test_parallel_go_ignores_the_queue(self):
        state = self.setup_units("a", "b")
        enqueue(self.dir, state, "b", SHA2, 6.0)
        set_option(state, "queue", "parallel")
        go(self.dir, state, "a", SHA1, 7.0)
        self.assertEqual(self.pushes(), f"a go {SHA1}\n")

    def test_holder_go_again_keeps_slot(self):
        state = self.setup_units("a")
        go(self.dir, state, "a", SHA1, 5.0)
        go(self.dir, state, "a", SHA2, 6.0)
        self.assertEqual(state["slot"]["unit"], "a")
        self.assertEqual(state["slot"]["sha"], SHA2)
        self.assertEqual(self.pushes(), f"a go {SHA1}\na go {SHA2}\n")

    def test_parallel_go_never_takes_slot(self):
        state = self.setup_units("a", "b", queue_mode="parallel")
        go(self.dir, state, "a", SHA1, 5.0)
        go(self.dir, state, "b", SHA2, 6.0)
        self.assertIsNone(state["slot"])
        self.assertEqual(self.pushes(), f"a go {SHA1}\nb go {SHA2}\n")

    def test_go_refused_for_done_unit(self):
        state = self.setup_units("a")
        state["units"]["a"]["done"] = True
        with self.assertRaises(RunError) as ctx:
            go(self.dir, state, "a", SHA1, 5.0)
        self.assertIn("pr-merged", str(ctx.exception))
        self.assertEqual(self.pushes(), "")

    def test_go_validates_unit_and_sha_and_writes_nothing(self):
        state = self.setup_units("a")
        with self.assertRaises(RunError):
            go(self.dir, state, "zz", SHA1, 5.0)
        with self.assertRaises(RunError):
            go(self.dir, state, "a", "abc123", 5.0)
        self.assertEqual(self.pushes(), "")
        self.assertIsNone(state["slot"])

    def test_go_clears_pending_and_queue(self):
        state = self.setup_units("a", "b")
        state["pending"] = [
            {"unit": "a", "sha": SHA1, "branch": "x", "at": 1},
            {"unit": "b", "sha": SHA1, "branch": "y", "at": 1},
        ]
        state["queue"] = [
            {"unit": "a", "sha": SHA1, "at": 1, "front": False},
            {"unit": "b", "sha": SHA1, "at": 2, "front": False},
        ]
        go(self.dir, state, "a", SHA2, 5.0)
        self.assertEqual([e["unit"] for e in state["pending"]], ["b"])
        self.assertEqual([e["unit"] for e in state["queue"]], ["b"])

    def test_redo_frees_holders_slot(self):
        state = self.setup_units("a")
        go(self.dir, state, "a", SHA1, 5.0)
        redo(self.dir, state, "a", SHA2, "pr-merged")
        self.assertIsNone(state["slot"])

    def test_redo_clears_the_go_head_mark(self):
        state = self.setup_units("a")
        state["marks"]["a"] = {"head": "abc"}
        go(self.dir, state, "a", SHA1, 5.0)
        self.assertIn("go_head", state["marks"]["a"])
        redo(self.dir, state, "a", SHA1, "base-conflict")
        self.assertIsNone(state["slot"])
        self.assertNotIn("go_head", state["marks"]["a"])  # the child must ask again
        self.assertEqual(state["marks"]["a"]["head"], "abc")

    def test_redo_leaves_other_holder_alone(self):
        state = self.setup_units("a", "b")
        go(self.dir, state, "a", SHA1, 5.0)
        redo(self.dir, state, "b", SHA2, "nope")
        self.assertEqual(state["slot"]["unit"], "a")

    def test_redo_line_format(self):
        state = self.setup_units("b")
        redo(self.dir, state, "b", SHA1, "changes-since-review, escalated")
        self.assertEqual(self.pushes(), f"b redo {SHA1} changes-since-review, escalated\n")

    def test_redo_removes_pending_and_queue_entries(self):
        state = self.setup_units("b")
        state["pending"] = [{"unit": "b", "sha": SHA1, "branch": "y", "at": 1}]
        state["queue"] = [{"unit": "b", "sha": SHA1, "at": 2, "front": False}]
        redo(self.dir, state, "b", SHA2, "r")
        self.assertEqual(state["pending"], [])
        self.assertEqual(state["queue"], [])

    def test_redo_rejects_bad_input_and_writes_nothing(self):
        state = self.setup_units("b")
        for sha, reasons in ((SHA1, ""), (SHA1, "two\nlines"), ("short", "r")):
            with self.assertRaises(RunError):
                redo(self.dir, state, "b", sha, reasons)
        with self.assertRaises(RunError):
            redo(self.dir, state, "zz", SHA1, "r")
        self.assertEqual(self.pushes(), "")

    def test_has_open_pr(self):
        state = self.setup_units("a")
        self.assertFalse(has_open_pr(state, "a"))
        state["units"]["a"]["prs"] = {"4": "MERGED"}
        self.assertFalse(has_open_pr(state, "a"))
        state["units"]["a"]["prs"]["5"] = "OPEN"
        self.assertTrue(has_open_pr(state, "a"))

    def test_queue_order_open_pr_first(self):
        state = self.setup_units("a", "b", "c")
        state["units"]["b"]["prs"] = {"5": "OPEN"}
        enqueue(self.dir, state, "c", SHA1, 1.0)
        enqueue(self.dir, state, "b", SHA1, 2.0)
        self.assertEqual([e["unit"] for e in queue_order(state)], ["b", "c"])

    def test_queue_order_by_time_within_group(self):
        state = self.setup_units("a", "b", "c")
        enqueue(self.dir, state, "c", SHA1, 3.0)
        enqueue(self.dir, state, "a", SHA1, 1.0)
        enqueue(self.dir, state, "b", SHA1, 2.0)
        self.assertEqual([e["unit"] for e in queue_order(state)], ["a", "b", "c"])

    def test_enqueue_replaces_units_entry(self):
        state = self.setup_units("a")
        enqueue(self.dir, state, "a", SHA1, 1.0)
        enqueue(self.dir, state, "a", SHA2, 2.0)
        self.assertEqual(state["queue"], [{"unit": "a", "sha": SHA2, "at": 2.0, "front": False}])

    def test_enqueue_again_with_the_same_sha_keeps_its_place(self):
        # A NEXT whose go was refused because a unit with an open PR got in front is queued again as it was.
        state = self.setup_units("a", "b", "c")
        enqueue(self.dir, state, "a", SHA1, 1.0)
        enqueue(self.dir, state, "b", SHA1, 2.0)
        enqueue(self.dir, state, "a", SHA1, 3.0)
        self.assertEqual([(e["unit"], e["at"]) for e in queue_order(state)], [("a", 1.0), ("b", 2.0)])

    def test_enqueue_removes_pending_entry(self):
        state = self.setup_units("a")
        state["pending"] = [{"unit": "a", "sha": SHA1, "branch": "x", "at": 1}]
        enqueue(self.dir, state, "a", SHA1, 2.0)
        self.assertEqual(state["pending"], [])

    def test_enqueue_refusals(self):
        state = self.setup_units("a", "b")
        go(self.dir, state, "a", SHA1, 5.0)
        with self.assertRaises(RunError):
            enqueue(self.dir, state, "a", SHA2, 6.0)
        with self.assertRaises(RunError):
            enqueue(self.dir, state, "b", "bad", 6.0)
        with self.assertRaises(RunError):
            enqueue(self.dir, state, "zz", SHA2, 6.0)
        self.assertEqual(state["queue"], [])
        state["queue_mode"] = "parallel"
        with self.assertRaises(RunError):
            enqueue(self.dir, state, "b", SHA2, 6.0)

    def test_yield_requires_holder(self):
        state = self.setup_units("a", "b")
        with self.assertRaises(RunError):
            yield_slot(state, "a", "owner-wait", 5.0)
        go(self.dir, state, "a", SHA1, 5.0)
        with self.assertRaises(RunError):
            yield_slot(state, "b", "owner-wait", 6.0)
        yield_slot(state, "a", "owner-wait", 7.0)
        self.assertIsNone(state["slot"])
        self.assertNotIn("yielded", state["units"]["a"])

    def test_yield_keeps_the_go_head_mark(self):
        # The child may still push the approved commit, so READY for the old head stays held.
        state = self.setup_units("a")
        state["marks"]["a"] = {"head": "abc"}
        go(self.dir, state, "a", SHA1, 5.0)
        yield_slot(state, "a", "stuck", 6.0)
        self.assertIsNone(state["slot"])
        self.assertEqual(state["marks"]["a"]["go_head"], "abc")

    def test_set_queue_parallel_keeps_the_queue(self):
        state = self.setup_units("a", "b")
        go(self.dir, state, "a", SHA1, 5.0)
        enqueue(self.dir, state, "b", SHA2, 6.0)
        set_option(state, "queue", "parallel")
        self.assertEqual([entry["unit"] for entry in state["queue"]], ["b"])

    def test_merged_frees_slot_and_marks_done(self):
        state = self.setup_units("a")
        go(self.dir, state, "a", SHA1, 5.0)
        record_merged(state, "a", 7, more=False)
        self.assertEqual(state["units"]["a"]["prs"]["7"], "MERGED")
        self.assertTrue(state["units"]["a"]["done"])
        self.assertIsNone(state["slot"])

    def test_merged_part_keeps_unit_open(self):
        state = self.setup_units("a")
        go(self.dir, state, "a", SHA1, 5.0)
        record_merged(state, "a", 7, more=True)
        self.assertFalse(state["units"]["a"]["done"])
        self.assertIsNone(state["slot"])

    def test_merged_by_non_holder_keeps_slot(self):
        state = self.setup_units("a", "b")
        go(self.dir, state, "a", SHA1, 5.0)
        record_merged(state, "b", 7, more=False)
        self.assertEqual(state["slot"]["unit"], "a")

    def test_closed_frees_slot_and_does_not_finish(self):
        state = self.setup_units("a")
        go(self.dir, state, "a", SHA1, 5.0)
        record_closed(state, "a", 8)
        self.assertEqual(state["units"]["a"]["prs"]["8"], "CLOSED")
        self.assertFalse(state["units"]["a"]["done"])
        self.assertIsNone(state["slot"])

    def test_reportable(self):
        state = self.setup_units("a", "b")
        self.assertTrue(reportable(state, "a"))
        self.assertTrue(reportable(state, "b"))
        go(self.dir, state, "a", SHA1, 5.0)
        self.assertTrue(reportable(state, "a"))
        self.assertFalse(reportable(state, "b"))
        state["queue_mode"] = "parallel"
        self.assertTrue(reportable(state, "b"))

    def test_a_stopped_unit_is_never_reportable(self):
        for mode in ("serial", "parallel"):
            with self.subTest(mode=mode):
                self.setUp()
                state = self.setup_units("a", queue_mode=mode)
                go(self.dir, state, "a", SHA1, 5.0)
                stop_gate(self.dir, state, "a", "cancelled")
                self.assertFalse(reportable(state, "a"))

    def test_stop_drops_the_go_head_mark(self):
        state = self.setup_units("a")
        state["marks"]["a"] = {"head": "abc"}
        go(self.dir, state, "a", SHA1, 5.0)
        self.assertIn("go_head", state["marks"]["a"])
        stop_gate(self.dir, state, "a", "cancelled")
        self.assertNotIn("go_head", state["marks"]["a"])


class AfterTests(RunStateTestCase):
    def test_add_unit_defaults_include_after_hold_title(self):
        state = self.init()
        add(state, "a")
        unit = state["units"]["a"]
        self.assertEqual((unit["after"], unit["hold"], unit["title"]), ([], None, None))

    def test_load_fills_new_fields_on_an_old_run(self):
        state = self.init()
        add(state, "a")
        for key in ("after", "hold", "title"):
            del state["units"]["a"][key]
        (self.dir / "run.json").write_text(json.dumps(state))
        unit = load(self.dir)["units"]["a"]
        self.assertEqual((unit["after"], unit["hold"], unit["title"]), ([], None, None))

    def test_after_unknown_or_self_rejected(self):
        state = self.init()
        add(state, "a")
        for after in (["zz"], ["b"]):
            with self.assertRaises(RunError, msg=after):
                add(state, "b", after=after)
        self.assertNotIn("b", state["units"])

    def test_after_and_prereq_on_same_unit_rejected(self):
        state = self.init()
        add(state, "a")
        with self.assertRaises(RunError):
            add(state, "b", prereqs=["a"], after=["a"])
        self.assertNotIn("b", state["units"])

    def test_cycle_across_prereq_and_after_rejected(self):
        state = self.init()
        add(state, "a")
        add(state, "b", prereqs=["a"])
        with self.assertRaises(RunError) as caught:
            add_unit(state, "a", prereqs=None, branch=None, worktree=None, specs=None, after=["b"])
        self.assertIn("wait on itself", str(caught.exception))
        self.assertEqual(state["units"]["a"]["after"], [])

    def test_after_empty_list_clears(self):
        state = self.init()
        add(state, "a")
        add(state, "b", after=["a"])
        self.assertEqual(state["units"]["b"]["after"], ["a"])
        add(state, "b", after=[])
        self.assertEqual(state["units"]["b"]["after"], [])

    def test_after_none_keeps_the_list(self):
        state = self.init()
        add(state, "a")
        add(state, "b", after=["a"])
        add_unit(state, "b", prereqs=None, branch="x", worktree=None, specs=None)
        self.assertEqual(state["units"]["b"]["after"], ["a"])

    def test_after_does_not_gate_opening(self):
        state = self.init()
        add(state, "a")
        add(state, "b", after=["a"])
        self.assertEqual(openable(state), ["a", "b"])

    def test_mergeable_waits_for_after_units(self):
        state = self.init()
        add(state, "a")
        add(state, "b", after=["a"])
        self.assertTrue(mergeable(state, "a"))
        self.assertFalse(mergeable(state, "b"))
        record_merged(state, "a", 1, more=False)
        self.assertTrue(mergeable(state, "b"))

    def test_stopped_after_unit_counts_as_satisfied(self):
        state = self.init()
        add(state, "a")
        add(state, "b", after=["a"])
        stop_gate(self.dir, state, "a", "dropped")
        self.assertTrue(mergeable(state, "b"))

    def test_wip_exempts_a_unit_an_open_unit_merges_after(self):
        state = self.init(wip=1)
        add(state, "a")
        add(state, "b", after=["a"])
        self.open_unit(state, "b")
        self.assertEqual(openable(state), ["a"])

    def test_wip_exemption_ends_when_the_open_unit_is_done(self):
        state = self.init(wip=1)
        add(state, "a")
        add(state, "b", after=["a"])
        add(state, "c")
        self.open_unit(state, "b")
        state["units"]["b"]["done"] = True
        self.open_unit(state, "c")
        self.assertEqual(openable(state), [])

    def test_exempt_unit_does_not_use_up_free_wip(self):
        state = self.init(wip=2)
        add(state, "a")
        add(state, "b", after=["a"])
        add(state, "c")
        add(state, "d")
        self.open_unit(state, "b")
        self.assertEqual(openable(state), ["a", "c"])

    def test_reportable_false_until_after_units_merge(self):
        state = self.init(queue_mode="parallel")
        add(state, "a")
        add(state, "b", after=["a"])
        self.assertFalse(reportable(state, "b"))
        record_merged(state, "a", 1, more=False)
        self.assertTrue(reportable(state, "b"))

    def test_go_serial_refuses_a_unit_that_merges_after_an_unmerged_unit(self):
        state = self.init()
        add(state, "a")
        add(state, "b", after=["a"])
        with self.assertRaises(RunError) as caught:
            go(self.dir, state, "b", SHA1, NOW)
        self.assertIn("merges after 'a'", str(caught.exception))
        self.assertEqual((self.dir / "pushes.log").read_text(), "")
        self.assertIsNone(state["slot"])

    def test_go_serial_allows_it_once_the_after_unit_merged(self):
        state = self.init()
        add(state, "a")
        add(state, "b", after=["a"])
        record_merged(state, "a", 1, more=False)
        go(self.dir, state, "b", SHA1, NOW)
        self.assertEqual((self.dir / "pushes.log").read_text(), f"b go {SHA1}\n")

    def test_go_parallel_allows_it(self):
        state = self.init(queue_mode="parallel")
        add(state, "a")
        add(state, "b", after=["a"])
        go(self.dir, state, "b", SHA1, NOW)
        self.assertEqual((self.dir / "pushes.log").read_text(), f"b go {SHA1}\n")

    def test_first_queued_skips_units_not_mergeable(self):
        state = self.init()
        add(state, "a")
        add(state, "b", after=["a"])
        add(state, "c")
        enqueue(self.dir, state, "b", SHA1, 1.0)
        enqueue(self.dir, state, "c", SHA2, 2.0)
        go(self.dir, state, "c", SHA2, NOW)
        self.assertEqual(state["slot"]["unit"], "c")


if __name__ == "__main__":
    unittest.main()
