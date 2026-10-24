import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from helpers import by_path, by_suffix, git, run

SCAFFOLD = Path(__file__).resolve().parent.parent / "evals" / "cleanup-run" / "scaffold.sh"


class EvalFixtureTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.ws = Path(self._tmp.name).resolve()

    def scaffold(self):
        # The eval runs the scaffold with only these variables set.
        env = {"PATH": os.environ["PATH"], "HOME": str(self.ws), "TERM": "dumb"}
        return subprocess.run(["bash", str(SCAFFOLD)], cwd=str(self.ws), env=env, capture_output=True, text=True)

    def test_fixture_builds_and_inventory_reads_it(self):
        result = self.scaffold()
        self.assertEqual(result.returncode, 0, result.stderr)
        out = run("inventory", "--repo", str(self.ws))
        self.assertEqual(out["base"], "main")
        self.assertEqual(out["current_branch"], "chore/cleanup")
        retry_plan = by_path(out, "docs/superpowers/plans/2020-01-16-retry.md")
        self.assertEqual(retry_plan["spec"], "docs/superpowers/specs/2020-01-15-retry-design.md")
        export_plan = by_path(out, "docs/superpowers/plans/2020-01-21-export.md")
        self.assertEqual(export_plan["spec"], "docs/superpowers/specs/2020-01-20-export-design.md")
        retry = by_path(out, "docs/superpowers/specs/2020-01-15-retry-design.md")
        self.assertEqual(retry["research"], ["docs/superpowers/research/2020-01-10-upstream-limits"])
        search = by_suffix(out, "-search-design.md")
        self.assertEqual(search["research"], ["docs/superpowers/research/2020-01-10-upstream-limits"])
        self.assertEqual(len(out["files"]), 7)
        self.assertTrue(all(f["on_base"] for f in out["files"]))
        self.assertEqual(git(self.ws, "status", "--porcelain"), "")
        self.assertFalse((self.ws / "src" / "export.py").exists())

    def test_refuses_to_overwrite(self):
        self.assertEqual(self.scaffold().returncode, 0)
        second = self.scaffold()
        self.assertNotEqual(second.returncode, 0)
        self.assertIn("already exists", second.stderr)


if __name__ == "__main__":
    unittest.main()
