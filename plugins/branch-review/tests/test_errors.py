import contextlib
import io
import os
from pathlib import Path
import tempfile
import unittest

from helpers import make_repo
import review_state


class ErrorPathTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def call(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = review_state.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def assert_one_line_error(self, code, out, err):
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertTrue(err.startswith("error: "), err)
        self.assertEqual(len(err.strip().splitlines()), 1, err)

    def test_helpers_drop_git_repository_variables(self):
        # Set by git hooks; left in place they would point fixture git commands at the outer repo.
        for var in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR", "GIT_OBJECT_DIRECTORY"):
            self.assertNotIn(var, os.environ)

    def test_unknown_subcommand(self):
        self.assert_one_line_error(*self.call("bogus"))

    def test_missing_subcommand(self):
        self.assert_one_line_error(*self.call())

    def test_unknown_flag_and_missing_value(self):
        self.assert_one_line_error(*self.call("locate", "--nope"))
        self.assert_one_line_error(*self.call("locate", "--base"))

    def test_repo_is_not_a_git_repo(self):
        plain = self.tmp / "plain"
        plain.mkdir()
        self.assert_one_line_error(*self.call("locate", "--repo", str(plain), "--state-dir", str(self.tmp / "s")))

    def test_repo_path_does_not_exist(self):
        self.assert_one_line_error(
            *self.call("locate", "--repo", str(self.tmp / "missing"), "--state-dir", str(self.tmp / "s"))
        )

    def _locate_with_review_json(self, content: bytes):
        repo = make_repo(self.tmp)
        state_dir = self.tmp / "state"
        directory = review_state.state_path(state_dir, repo)
        directory.mkdir(parents=True)
        (directory / "review.json").write_bytes(content)
        return self.call("locate", "--repo", str(repo), "--state-dir", str(state_dir))

    def test_corrupt_review_json(self):
        self.assert_one_line_error(*self._locate_with_review_json(b"{not json"))

    def test_non_utf8_review_json(self):
        self.assert_one_line_error(*self._locate_with_review_json(b"\xff\xfe\x00"))

    def test_review_json_not_an_object(self):
        self.assert_one_line_error(*self._locate_with_review_json(b"[1, 2]"))


if __name__ == "__main__":
    unittest.main()
