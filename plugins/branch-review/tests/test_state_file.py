"""Tests for review.json reads and writes when another process holds the file (a Windows sharing conflict)."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import helpers  # noqa: F401  (puts the scripts dir on sys.path)
import review_state

CHILD_HOLDS_FILE = """
import sys, time
f = open(sys.argv[1], encoding="utf-8")
print("open", flush=True)
time.sleep(float(sys.argv[2]))
"""


def flaky(real, failures):
    """`real`, raising PermissionError on its first `failures` calls; also returns the list of calls."""
    calls = []

    def call(*args, **kwargs):
        calls.append(args)
        if len(calls) <= failures:
            raise PermissionError(13, "Access is denied")
        return real(*args, **kwargs)

    return call, calls


class StateFileSharingTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name) / "state"
        review_state.save_state(self.dir, {"findings": [], "n": 1})

    def test_save_waits_out_a_reader_holding_review_json(self):
        reader = subprocess.Popen(
            [sys.executable, "-c", CHILD_HOLDS_FILE, str(self.dir / review_state.STATE_FILE), "0.5"],
            stdout=subprocess.PIPE,
            encoding="utf-8",
        )
        self.addCleanup(reader.wait)
        self.assertEqual(reader.stdout.readline().strip(), "open")
        review_state.save_state(self.dir, {"findings": [], "n": 2})
        reader.stdout.close()
        self.assertEqual(review_state.load_state(self.dir)["n"], 2)

    def test_replace_and_open_retry_a_permission_error(self):
        replace, replaced = flaky(os.replace, 2)
        with mock.patch.object(review_state, "_RETRY_SHARING", True), mock.patch.object(review_state.time, "sleep"):
            with mock.patch.object(review_state.os, "replace", replace):
                review_state.save_state(self.dir, {"findings": [], "n": 2})
            self.assertEqual(len(replaced), 3)
            opener, opened = flaky(open, 2)
            with mock.patch("review_state.open", opener, create=True):
                self.assertEqual(review_state.load_state(self.dir)["n"], 2)
            self.assertEqual(len(opened), 3)

    def test_retry_gives_up_after_its_window_and_leaves_no_temp_file(self):
        replace, replaced = flaky(os.replace, 10_000)
        with mock.patch.object(review_state, "_RETRY_SHARING", True), mock.patch.object(review_state.time, "sleep"):
            with mock.patch.object(review_state.os, "replace", replace), self.assertRaises(PermissionError):
                review_state.save_state(self.dir, {"findings": [], "n": 2})
        self.assertEqual(len(replaced), review_state.SHARING_TRIES)
        self.assertEqual([p.name for p in self.dir.iterdir()], [review_state.STATE_FILE])
        self.assertEqual(review_state.load_state(self.dir)["n"], 1)

    def test_no_retry_off_windows(self):
        replace, replaced = flaky(os.replace, 1)
        with mock.patch.object(review_state, "_RETRY_SHARING", False):
            with mock.patch.object(review_state.os, "replace", replace), self.assertRaises(PermissionError):
                review_state.save_state(self.dir, {"findings": [], "n": 2})
        self.assertEqual(len(replaced), 1)


if __name__ == "__main__":
    unittest.main()
