"""Reads a pull request's state through gh: one poll reduced to a Summary, and the check behind `run.py check`."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import json
import re
import subprocess

QUERY = """query($o:String!,$n:String!,$p:Int!){repository(owner:$o,name:$n){pullRequest(number:$p){
  state isDraft mergeStateStatus headRefOid
  reviewThreads(first:100){totalCount nodes{isResolved}}
  commits(last:1){nodes{commit{statusCheckRollup{contexts(first:100){nodes{
    __typename
    ... on CheckRun{name status conclusion}
    ... on StatusContext{context state description}
  }}}}}}}}}"""

PASSING_CONCLUSIONS = ("SUCCESS", "SKIPPED", "NEUTRAL")
RUNNING_STATES = ("PENDING", "EXPECTED")


class GhError(Exception):
    """A gh call failed; the message is what to show in the ERROR line."""


def run_gh(args: list[str]) -> str:
    try:
        result = subprocess.run(["gh", *args], capture_output=True, text=True)
    except OSError as exc:
        raise GhError(str(exc)) from exc
    if result.returncode != 0:
        raise GhError((result.stderr or result.stdout).strip() or f"gh exited {result.returncode}")
    return result.stdout


@dataclass(frozen=True)
class Summary:
    """What one poll saw. Two equal summaries on consecutive polls mean the PR is stable."""

    state: str
    draft: bool
    merge: str
    head: str
    unresolved: int
    many_threads: bool
    total: int
    not_done: int
    failed: int

    @property
    def conflict(self) -> bool:
        """The PR cannot merge as it stands because of conflicts with its base."""
        return self.merge in ("DIRTY", "CONFLICTING")

    def reasons(self) -> str:
        """Why the PR is not ready: a space-led list, empty when it is."""
        reasons = []
        if self.draft:
            reasons.append("draft")
        if self.merge not in ("CLEAN", "HAS_HOOKS"):
            reasons.append(f"merge={self.merge}")
        if self.total == 0:
            reasons.append("no-checks")
        if self.not_done:
            reasons.append(f"checks-running={self.not_done}")
        if self.failed:
            reasons.append(f"checks-failed={self.failed}")
        if self.unresolved:
            reasons.append(f"unresolved-threads={self.unresolved}")
        if self.many_threads:
            reasons.append("over-100-threads")
        return "".join(f" {reason}" for reason in reasons)


def summarize(pr: dict) -> Summary:
    """Reduces the GraphQL `pullRequest` object to a Summary.

    Raises KeyError, TypeError or AttributeError on a malformed object.
    """
    commits = pr["commits"]["nodes"]
    rollup = commits[0]["commit"]["statusCheckRollup"] if commits else None
    contexts = ((rollup or {}).get("contexts") or {}).get("nodes") or []
    checks = []  # (done, ok)
    for node in contexts:
        if node["__typename"] == "CheckRun":
            checks.append((node["status"] == "COMPLETED", node["conclusion"] in PASSING_CONCLUSIONS))
        else:
            checks.append((node["state"] not in RUNNING_STATES, node["state"] == "SUCCESS"))
    threads = pr["reviewThreads"]
    head = pr["headRefOid"]
    if not isinstance(head, str):
        raise TypeError("headRefOid is not a string")
    return Summary(
        state=pr["state"],
        draft=bool(pr["isDraft"]),
        merge=pr["mergeStateStatus"],
        head=head,
        unresolved=sum(1 for thread in threads["nodes"] if not thread["isResolved"]),
        many_threads=threads["totalCount"] > 100,
        total=len(checks),
        not_done=sum(1 for done, _ in checks if not done),
        failed=sum(1 for done, ok in checks if done and not ok),
    )


def find_open_pr(repo: str, branch: str, gh: Callable[[list[str]], str]) -> int | None:
    out = gh(["pr", "list", "-R", repo, "--head", branch, "--state", "open", "--json", "number"])
    try:
        prs = json.loads(out)
        return int(prs[0]["number"]) if prs else None
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise GhError(f"unexpected output: {out.strip()[:200]}") from exc


def branch_head(repo: str, branch: str, gh: Callable[[list[str]], str]) -> str:
    """The commit sha at the tip of the branch on the remote. Raises GhError when gh fails or answers no sha."""
    out = gh(["api", f"repos/{repo}/branches/{branch}", "--jq", ".commit.sha"]).strip()
    if not re.fullmatch(r"[0-9a-f]{40}", out):
        raise GhError(f"unexpected output: {out[:200]}")
    return out


def poll_pr(repo: str, pr: int, gh: Callable[[list[str]], str]) -> Summary:
    owner, _, name = repo.rpartition("/")
    out = gh(["api", "graphql", "-f", f"query={QUERY}", "-f", f"o={owner}", "-f", f"n={name}", "-F", f"p={pr}"])
    if not out.strip():
        raise GhError("empty output")
    try:
        return summarize(json.loads(out)["data"]["repository"]["pullRequest"])
    except (ValueError, KeyError, IndexError, TypeError, AttributeError) as exc:
        raise GhError(f"unexpected output: {out.strip()[:200]}") from exc


def check_once(
    repo: str,
    branch: str,
    *,
    gh: Callable[[list[str]], str],
    sleep: Callable[[float], None],
    confirm_seconds: float = 30,
) -> str:
    """Checks the branch's PR now and returns exactly one line:

    READY <pr> <head-sha>                    two identical clean polls `confirm_seconds` apart
    NOT-READY <pr> head=<sha7><reasons>      open but not ready; a PR behind its base says `merge=BEHIND`
    NOT-READY - no open PR from <branch>
    MERGED <pr> | CLOSED <pr>                the PR was seen in that state
    ERROR <message>                          the first failing gh call
    """
    try:
        pr = find_open_pr(repo, branch, gh)
    except GhError as exc:
        return f"ERROR gh pr list: {exc}"
    if pr is None:
        return f"NOT-READY - no open PR from {branch}"
    prev: Summary | None = None
    polls = 0
    while True:
        try:
            s = poll_pr(repo, pr, gh)
        except GhError as exc:
            return f"ERROR gh api graphql on PR {pr}: {exc}"
        if s.state in ("MERGED", "CLOSED"):
            return f"{s.state} {pr}"
        reasons = s.reasons()
        polls += 1
        if not reasons:
            if prev == s:
                return f"READY {pr} {s.head}"
        elif polls >= 2:
            return f"NOT-READY {pr} head={s.head[:7]}{reasons}"
        prev = s
        sleep(confirm_seconds)
