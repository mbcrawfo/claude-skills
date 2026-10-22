import hashlib
from pathlib import Path
import tempfile
import unittest

from helpers import git, make_repo, run
import review_state


class KeysTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def test_repo_key_ssh_and_https_normalize(self):
        ssh = make_repo(self.tmp / "ssh", origin="git@github.com:Owner/Repo.git")
        https = make_repo(self.tmp / "https", origin="https://github.com/Owner/Repo.git")
        self.assertEqual(review_state.repo_key(ssh), "owner__repo")
        self.assertEqual(review_state.repo_key(https), "owner__repo")

    def test_repo_key_without_origin_is_hashed(self):
        repo = make_repo(self.tmp, origin=None)
        key = review_state.repo_key(repo)
        self.assertTrue(key.startswith("local-"))
        self.assertEqual(len(key), 18)
        common = (repo / ".git").resolve()
        expected = hashlib.sha256(str(common).encode()).hexdigest()[:12]
        self.assertEqual(key, "local-" + expected)

    def test_branch_key_replaces_slashes(self):
        repo = make_repo(self.tmp)
        self.assertEqual(review_state.branch_key(repo), ("feature/x", "feature__x"))

    def test_branch_key_detached_head(self):
        repo = make_repo(self.tmp)
        sha = git(repo, "rev-parse", "HEAD")
        git(repo, "checkout", "--detach")
        name, key = review_state.branch_key(repo)
        self.assertEqual(name, "HEAD")
        self.assertEqual(key, "detached-" + sha[:7])

    def test_default_base_prefers_origin_head(self):
        repo = make_repo(self.tmp)
        self.assertEqual(review_state.default_base(repo), "main")
        git(repo, "update-ref", "refs/remotes/origin/main", "main")
        git(repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
        self.assertEqual(review_state.default_base(repo), "origin/main")

    def test_locate_without_state(self):
        repo = make_repo(self.tmp)
        state_dir = self.tmp / "state"
        out = run(repo, "locate", state_dir=state_dir)
        self.assertFalse(out["exists"])
        self.assertEqual(out["mode"], "deep")
        self.assertEqual(out["reason"], "no-state")
        self.assertEqual(out["rounds"], 0)
        self.assertEqual(out["open_findings"], [])
        self.assertEqual(out["base"], "main")
        self.assertEqual(Path(out["dir"]), state_dir / "owner__repo" / "feature__x")

    def test_locate_base_override(self):
        repo = make_repo(self.tmp)
        out = run(repo, "locate", "--base", "develop", state_dir=self.tmp / "state")
        self.assertEqual(out["base"], "develop")

    def test_locate_with_existing_state(self):
        repo = make_repo(self.tmp)
        state_dir = self.tmp / "state"
        review_state.save_state(
            review_state.state_path(state_dir, repo),
            {
                "rounds": [{"number": 1, "added": 1}],
                "findings": [
                    {"id": "F1", "status": "open"},
                    {"id": "F2", "status": "fixed"},
                    {"id": "F3", "status": "still-open"},
                    {"id": "F4", "status": "dismissed"},
                ],
            },
        )
        out = run(repo, "locate", state_dir=state_dir)
        self.assertTrue(out["exists"])
        self.assertEqual(out["mode"], "deep")
        self.assertEqual(out["reason"], "snapshot-missing")
        self.assertEqual(out["rounds"], 1)
        self.assertEqual([f["id"] for f in out["open_findings"]], ["F1", "F2", "F3"])


if __name__ == "__main__":
    unittest.main()
