"""Shared fixtures for the branch-review state script tests."""

from collections.abc import Iterator
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Optional

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

# Drop variables that point git at another repository or index. They are set
# when the suite runs from a git hook (for example pre-commit), and would make
# every fixture command act on the outer repository instead.
for _var in (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_COMMON_DIR",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_IMPLICIT_WORK_TREE",
    "GIT_NAMESPACE",
    "GIT_PREFIX",
    "GIT_SHALLOW_FILE",
    "GIT_GRAFT_FILE",
    "GIT_REPLACE_REF_BASE",
    "GIT_NO_REPLACE_OBJECTS",
    "GIT_CONFIG",
    "GIT_CONFIG_PARAMETERS",
    "GIT_CONFIG_COUNT",
):
    os.environ.pop(_var, None)

# Isolate fixture repos from the developer's global/system git configuration.
os.environ["GIT_CONFIG_GLOBAL"] = os.devnull
os.environ["GIT_CONFIG_SYSTEM"] = os.devnull
os.environ["GIT_CONFIG_NOSYSTEM"] = "1"

import review_state  # noqa: E402


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=str(repo),
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def commit_file(repo: Path, name: str, content: str, message: str) -> None:
    path = Path(repo) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    git(repo, "add", "-A")
    git(repo, "commit", "-m", message)


def make_repo(
    tmp: Path,
    *,
    origin: Optional[str] = "git@github.com:Owner/Repo.git",
    branch: str = "feature/x",
) -> Path:
    """Create a repo with one commit on main and a second commit on `branch`.

    The repo lives in `tmp/repo`, so use a distinct `tmp` per repo.
    """
    repo = Path(tmp) / "repo"
    repo.mkdir(parents=True)
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.name", "Test User")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "commit.gpgsign", "false")
    commit_file(repo, "a.txt", "a\n", "initial commit")
    if origin is not None:
        git(repo, "remote", "add", "origin", origin)
    git(repo, "checkout", "-b", branch)
    commit_file(repo, "b.txt", "b\n", "add b")
    return repo


def run(repo: Path, *argv: str, state_dir: Path) -> dict:
    """Invoke the CLI's main() against `repo` and return the parsed JSON output."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = review_state.main([*argv, "--state-dir", str(state_dir), "--repo", str(repo)])
    assert code in (0, None), f"main exited with {code!r}"
    return json.loads(buf.getvalue())


CANARY = Path("canary") / "a" / "b" / "keep.txt"


@contextlib.contextmanager
def sandbox_cwd() -> Iterator[Path]:
    """Run inside a fresh temp directory holding a canary tree; check it survives.

    Every test that exercises `cleanup` or `prune`, or passes an empty, blank or
    relative `--state-dir`, runs inside this, never with the checkout as cwd.
    """
    previous = os.getcwd()
    with tempfile.TemporaryDirectory(prefix="br-sandbox-") as tmp:
        sandbox = Path(tmp).resolve()
        canary = sandbox / CANARY
        canary.parent.mkdir(parents=True)
        canary.write_text("canary\n")
        os.chdir(str(sandbox))
        try:
            yield sandbox
        finally:
            os.chdir(previous)
        if not canary.is_file():
            raise AssertionError(f"canary {canary} was removed")


def finding(**over):
    base = {
        "severity": "minor",
        "category": "logic",
        "file": "b.txt",
        "lines": "1-2",
        "title": "Handle the empty case.",
        "trigger": "empty input",
        "consequence": "crash",
        "reachable_now": "yes",
        "fix": "add a guard",
    }
    base.update(over)
    return base


def output(**over):
    base = {
        "summary": "Adds b. Looks fine.",
        "merge_risk": {"level": "low", "reason": "small change"},
        "intent_check": {"unmet_claims": [], "unmentioned_behavior": []},
        "verdicts": [],
        "findings": [finding(), finding(title="Second problem.")],
        "outside_change": [],
    }
    base.update(over)
    return base


def report(data):
    return "# Review\n\nProse.\n\n```json\n" + json.dumps(data) + "\n```\n"


class LedgerHarness:
    """Fixture and helpers for tests that drive the CLI through review rounds.

    Mix in with `unittest.TestCase`.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.state_dir = self.tmp / "state"
        self.repo = make_repo(self.tmp / "r")

    # helpers
    def cli(self, *argv):
        return run(self.repo, *argv, state_dir=self.state_dir)

    def call(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = review_state.main([*argv, "--state-dir", str(self.state_dir), "--repo", str(self.repo)])
        return code, out.getvalue(), err.getvalue()

    def assert_error(self, *argv):
        code, out, err = self.call(*argv)
        self.assertEqual(code, 1, err)
        self.assertEqual(out, "")
        self.assertTrue(err.startswith("error: "), err)
        self.assertEqual(len(err.strip().splitlines()), 1, err)

    def begin(self, mode="deep", *extra):
        if mode == "followup":
            # An empty round is never saved, so give each follow-up a delta.
            self._changes = getattr(self, "_changes", 0) + 1
            (self.repo / f"change-{self._changes}.txt").write_text("x\n")
        snap = self.cli("snapshot")["snapshot"]
        return self.cli(
            "begin-round",
            "--mode",
            mode,
            "--model",
            "m",
            "--snapshot",
            snap,
            "--base-ref",
            "main",
            *extra,
        )

    def write(self, name, text):
        path = self.tmp / name
        path.write_text(text)
        return str(path)

    def record(self, data, *extra, name="report.md", raw=None):
        path = self.write(name, raw if raw is not None else report(data))
        return self.cli("record-findings", "--file", path, *extra)

    @property
    def review_json(self):
        return Path(self.cli("locate", "--base", "main")["dir"]) / "review.json"

    def state(self):
        return json.loads(self.review_json.read_text())

    def find(self, fid):
        return next(f for f in self.state()["findings"] if f["id"] == fid)

    def set_status(self, fid, status, *extra):
        return self.cli("set-status", "--id", fid, "--status", status, *extra)

    def seeded(self, **over):
        self.begin()
        self.record(output(**over))
