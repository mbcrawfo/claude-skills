from pathlib import Path
import tempfile
import unittest

from helpers import call


class ErrorPathTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def test_not_a_git_repo(self):
        code, out, err = call("inventory", "--repo", str(self.tmp))
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertTrue(err.startswith("error: "), err)
        self.assertEqual(len(err.strip().splitlines()), 1, err)

    def test_unknown_command(self):
        code, out, _ = call("frobnicate")
        self.assertNotEqual(code, 0)
        self.assertEqual(out, "")


if __name__ == "__main__":
    unittest.main()
