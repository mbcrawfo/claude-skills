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

    def test_repo_key_url_forms(self):
        cases = (
            ("https://github.com/Owner/Repo", "owner__repo"),
            ("https://github.com/Owner/Repo.git/", "owner__repo"),
            ("https://github.com/owner/repo/", "owner__repo"),
            ("ssh://git@github.com/owner/repo.git", "owner__repo"),
            ("ssh://git@host.example:2222/Owner/Repo.git", "owner__repo"),
            ("git@github.com:OWNER/REPO.git", "owner__repo"),
            ("https://gitlab.com/group/sub/repo.git", "group__sub__repo"),
            ("git@gitlab.com:Group/Sub/Deeper/Repo.git", "group__sub__deeper__repo"),
        )
        for i, (url, expected) in enumerate(cases):
            with self.subTest(url=url):
                repo = make_repo(self.tmp / f"r{i}", origin=url)
                self.assertEqual(review_state.repo_key(repo), expected)

    def test_repo_key_non_url_origin_falls_back_to_local_hash(self):
        repo = make_repo(self.tmp, origin=str(self.tmp / "elsewhere" / "repo.git"))
        common = (repo / ".git").resolve()
        expected = hashlib.sha256(str(common).encode()).hexdigest()[:12]
        self.assertEqual(review_state.repo_key(repo), "local-" + expected)

    def test_linked_worktree_shares_local_key(self):
        repo = make_repo(self.tmp, origin=None)
        linked = self.tmp / "linked"
        git(repo, "worktree", "add", "-q", "-b", "other", str(linked))
        self.assertTrue(review_state.repo_key(repo).startswith("local-"))
        self.assertEqual(review_state.repo_key(linked), review_state.repo_key(repo))

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
                "rounds": [{"number": 1, "added": 1, "snapshot": "0" * 40, "head": "0" * 40, "merge_base": None}],
                "findings": [
                    {"id": "F1", "status": "open"},
                    {"id": "F2", "status": "fixed"},
                    {"id": "F3", "status": "still-open"},
                    {"id": "F4", "status": "verified"},
                    {"id": "F5", "status": "invalid"},
                    {"id": "F6", "status": "escalated"},
                    {"id": "F7", "status": "deferred"},
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
