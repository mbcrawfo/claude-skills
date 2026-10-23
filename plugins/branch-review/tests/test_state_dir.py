"""`--state-dir` must be absolute: a value that resolves against the cwd is refused.

Every test here runs inside `sandbox_cwd()`, so even a regression that let an
empty or relative state dir through could only touch the sandbox, and the
sandbox's canary tree shows whether it did.
"""

import contextlib
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from helpers import CANARY, sandbox_cwd
import review_state

BAD_STATE_DIRS = ("", "   ", "\t", ".", "./", "relative", "./state", "canary", "../x")

# One invocation per command, otherwise valid, so the state dir is the only fault.
COMMANDS = (
    ("cleanup", "--scope", "all"),
    ("cleanup", "--scope", "repo"),
    ("cleanup", "--scope", "branch"),
    ("prune",),
    ("list",),
    ("locate",),
    ("snapshot",),
    ("status",),
    ("covered",),
    ("summary",),
    ("begin-round", "--mode", "deep", "--model", "m", "--snapshot", "HEAD"),
    ("record-findings", "--file", "report.md"),
    ("set-status", "--id", "R1-F1", "--status", "open"),
    ("discuss", "--id", "R1-F1", "--from", "orchestrator", "--text", "t"),
    ("resolve", "--id", "R1-F1", "--decision", "fix", "--note", "n"),
)


def call(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = review_state.main(list(argv))
    return code, out.getvalue(), err.getvalue()


class StateDirTest(unittest.TestCase):
    def test_every_command_rejects_empty_blank_or_relative_state_dir(self):
        with sandbox_cwd() as sandbox:
            # A tree shaped like review state, directly under the cwd: what an
            # empty or "." state dir would point cleanup at.
            lookalike = sandbox / "owner__repo" / "feature__x"
            (lookalike / "rounds").mkdir(parents=True)
            (lookalike / "review.json").write_text("{}")
            for command in COMMANDS:
                for value in BAD_STATE_DIRS:
                    with self.subTest(command=command[0], value=value):
                        code, out, err = call(*command, "--state-dir", value, "--repo", str(sandbox))
                        self.assertEqual(code, 1)
                        self.assertEqual(out, "")
                        self.assertTrue(err.startswith("error: --state-dir"), err)
                        self.assertEqual(len(err.strip().splitlines()), 1, err)
            self.assertTrue((sandbox / CANARY).is_file())
            self.assertTrue((lookalike / "review.json").is_file())
            self.assertEqual(Path(os.getcwd()).resolve(), sandbox)

    def test_root_and_home_state_dirs_are_rejected(self):
        with sandbox_cwd(), tempfile.TemporaryDirectory(prefix="br-home-") as home:
            home = str(Path(home).resolve())
            with mock.patch.dict(os.environ, {"HOME": home}):
                for value in ("/", "/..", "//", "~", "~/", home, home + "/", home + "/sub/.."):
                    # cleanup only against the fake home: a regression must never
                    # run it on the real filesystem root.
                    commands = [("list",)]
                    if value not in ("/", "/..", "//"):
                        commands.append(("cleanup", "--scope", "all"))
                    for command in commands:
                        with self.subTest(value=value, command=command[0]):
                            code, out, err = call(*command, "--state-dir", value)
                            self.assertEqual((code, out), (1, ""))
                            self.assertTrue(err.startswith("error: --state-dir"), err)
                            self.assertEqual(len(err.strip().splitlines()), 1, err)
                code, out, err = call("list", "--state-dir", "~/state")
                self.assertEqual(code, 0, err)

    def test_home_relative_state_dir_is_accepted(self):
        with sandbox_cwd():
            code, out, err = call("list", "--state-dir", "~/br-state-dir-test-does-not-exist")
            self.assertEqual(code, 0, err)
            self.assertIn('"reviews": []', out)


if __name__ == "__main__":
    unittest.main()
