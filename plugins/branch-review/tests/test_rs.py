"""Tests for the rs wrapper script."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from helpers import make_repo, sandbox_cwd

RS = Path(__file__).resolve().parent.parent / "scripts" / "rs"


def run_rs(shell, *argv, cwd, plugin_data=None):
    env = dict(os.environ)
    env.pop("CLAUDE_PLUGIN_DATA", None)
    if plugin_data is not None:
        env["CLAUDE_PLUGIN_DATA"] = str(plugin_data)
    return subprocess.run(
        [shell, str(RS), *argv],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
    )


class RsTests:
    shell = "sh"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name).resolve()
        self.state = self.tmp / "state"
        self.repo = make_repo(self.tmp / "r")

    def rs(self, *argv, cwd, plugin_data="default"):
        if plugin_data == "default":
            plugin_data = self.state
        return run_rs(self.shell, *argv, cwd=cwd, plugin_data=plugin_data)

    def test_rs_defaults_state_dir_and_repo(self):
        result = self.rs("locate", cwd=self.repo)
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertTrue(Path(data["dir"]).is_relative_to(self.state), data["dir"])

    def test_rs_from_subdirectory_uses_top_level(self):
        sub = self.repo / "sub"
        sub.mkdir()
        root = json.loads(self.rs("locate", cwd=self.repo).stdout)
        result = self.rs("locate", cwd=sub)
        self.assertEqual(result.returncode, 0, result.stderr)
        nested = json.loads(result.stdout)
        self.assertEqual(nested["repo_key"], root["repo_key"])
        self.assertEqual(nested["dir"], root["dir"])

    def test_rs_outside_a_repo_adds_no_repo(self):
        outside = self.tmp / "outside"
        outside.mkdir()
        result = self.rs("list", cwd=outside)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("reviews", json.loads(result.stdout))

    def test_rs_explicit_args_win(self):
        other = self.tmp / "other"
        result = self.rs("locate", "--state-dir", str(other), cwd=self.repo)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(Path(json.loads(result.stdout)["dir"]).is_relative_to(other))
        result = self.rs("locate", f"--state-dir={other}", cwd=self.repo)
        self.assertTrue(Path(json.loads(result.stdout)["dir"]).is_relative_to(other))

    def test_state_dir_before_command_is_not_doubled(self):
        other = self.tmp / "other"
        result = self.rs("--state-dir", str(other), "locate", cwd=self.repo)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(Path(json.loads(result.stdout)["dir"]).is_relative_to(other))

    def test_rs_without_plugin_data_reports_error(self):
        with sandbox_cwd() as sandbox:
            result = self.rs("list", cwd=sandbox, plugin_data=None)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertTrue(result.stderr.startswith("error: "), result.stderr)
        self.assertEqual(len(result.stderr.strip().splitlines()), 1, result.stderr)

    def test_rs_help_works_without_plugin_data(self):
        with sandbox_cwd() as sandbox:
            result = self.rs("locate", "--help", cwd=sandbox, plugin_data=None)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("usage:", result.stdout)

    def test_rs_passes_script_exit_status(self):
        result = self.rs("no-such-command", cwd=self.repo)
        self.assertNotEqual(result.returncode, 0)


class RsShTests(RsTests, unittest.TestCase):
    shell = "sh"


@unittest.skipUnless(shutil.which("zsh"), "zsh not installed")
class RsZshTests(RsTests, unittest.TestCase):
    shell = "zsh"


if __name__ == "__main__":
    unittest.main()
