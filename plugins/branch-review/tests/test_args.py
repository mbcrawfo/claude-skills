"""Global arguments in either position, and the plugin version helpers."""

import contextlib
import io
import json
from pathlib import Path
import unittest

from helpers import LedgerHarness
import review_state


class ArgumentOrderTest(LedgerHarness, unittest.TestCase):
    def main_json(self, argv):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
            code = review_state.main(argv)
        self.assertEqual(code, 0)
        return json.loads(buf.getvalue())

    def test_state_dir_before_subcommand(self):
        out = self.main_json(["--state-dir", str(self.state_dir), "--repo", str(self.repo), "locate", "--base", "main"])
        self.assertTrue(Path(out["dir"]).is_relative_to(self.state_dir), out["dir"])

    def test_repo_before_subcommand(self):
        out = self.main_json(["--repo", str(self.repo), "locate", "--state-dir", str(self.state_dir)])
        self.assertEqual(out["repo_key"], "owner__repo")

    def test_after_wins_over_before(self):
        before, after = self.tmp / "before", self.tmp / "after"
        out = self.main_json(
            ["--state-dir", str(before), "locate", "--state-dir", str(after), "--repo", str(self.repo)]
        )
        self.assertTrue(Path(out["dir"]).is_relative_to(after), out["dir"])

    def test_default_state_dir_when_absent(self):
        args = review_state._apply_defaults(review_state._build_parser().parse_args(["locate"]))
        self.assertEqual(args.state_dir, review_state.DEFAULT_STATE_DIR)
        self.assertEqual(args.repo, ".")

    def test_locate_reports_plugin_version(self):
        out = self.cli("locate", "--base", "main")
        self.assertEqual(out["plugin_version"], review_state.PLUGIN_VERSION)
        self.assertEqual(review_state.PLUGIN_VERSION, "0.11.0")


class VersionTest(unittest.TestCase):
    def test_version_at_least(self):
        v09 = review_state.V09
        self.assertTrue(review_state._version_at_least("0.9.0", v09))
        self.assertFalse(review_state._version_at_least("0.8.1", v09))
        self.assertTrue(review_state._version_at_least("0.10.0", v09))
        self.assertFalse(review_state._version_at_least(None, v09))
        self.assertFalse(review_state._version_at_least("x", v09))


if __name__ == "__main__":
    unittest.main()
