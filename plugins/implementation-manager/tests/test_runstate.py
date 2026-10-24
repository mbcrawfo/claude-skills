"""Tests for run state: init, locking, units, gates and the WIP limit."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

import helpers  # noqa: F401  (puts the scripts dir on sys.path)
from runstate import RunError, add_unit, init_run, load, locked, open_gate, openable, set_option, stop_gate

NOW = 1000.0


def add(state, name, prereqs=(), specs=False):
    add_unit(state, name, prereqs=list(prereqs), branch=None, worktree=None, specs=specs)


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
                "branch": None,
                "worktree": None,
                "specs": False,
                "gate": "closed",
                "gate_at": None,
                "done": False,
                "prs": {},
                "yielded": None,
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
        for name in ("a b", "-x", "", "a/b"):
            with self.assertRaises(RunError, msg=name):
                add(state, name)

    def test_unit_name_charset_accepted(self):
        state = self.init()
        add(state, "1.2-a_b")
        self.assertIn("1.2-a_b", state["units"])


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

    def test_stop_gate_rejects_unknown_unit_and_multiline_reason(self):
        state = self.init()
        add(state, "a")
        with self.assertRaises(RunError):
            stop_gate(self.dir, state, "zz", "r")
        with self.assertRaises(RunError):
            stop_gate(self.dir, state, "a", "two\nlines")


if __name__ == "__main__":
    unittest.main()
