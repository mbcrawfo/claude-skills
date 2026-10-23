#!/usr/bin/env python3
"""Watches the pull request from one branch and prints a single line when there is something to act on:

  READY <pr> <head-sha>       open, not draft, mergeable (CLEAN or HAS_HOOKS), at least one check and every check
                              completed and passing, no unresolved review thread; and the same on two polls in a row
  ATTENTION <pr> <sha> behind-base
                              needs someone to act: the repo requires up-to-date branches and this one is behind
  MERGED <pr> | CLOSED <pr>   only for the PR this watch saw open, so an old PR on a reused branch is ignored
  STALLED <pr> head=<sha7><reasons>
                              the PR is open and nothing changed for the stall period
  ERROR <message>             gh failed 10 polls in a row (login, network, API)
  NOT-READY <pr> head=<sha7><reasons> | NOT-READY - no open PR from <branch>
                              only with ONCE=1

Before a PR exists it waits without a stall limit: implementation can take hours. A passing check sends no event
the session can wait on, so this poll is the only signal that a PR is ready. The script prints exactly one line
and exits 0, also on ERROR, so a background run always ends with a line to read.

Usage: wait_pr_ready.py <owner/repo> <branch> [stall-hours, whole number, default 3]
Env:   POLL_SECONDS (default 120)
       SKIP_HEAD=<sha>  never report READY or ATTENTION for a head starting with this (set it when restarting
                        after either)
       ONCE=1           check now (two polls 30 s apart), print READY or NOT-READY <pr> head=<sha7><reasons>, and exit
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import json
import os
import subprocess
import sys
import time

QUERY = """query($o:String!,$n:String!,$p:Int!){repository(owner:$o,name:$n){pullRequest(number:$p){
  state isDraft mergeStateStatus headRefOid
  reviewThreads(first:100){totalCount nodes{isResolved}}
  commits(last:1){nodes{commit{statusCheckRollup{contexts(first:100){nodes{
    __typename
    ... on CheckRun{name status conclusion}
    ... on StatusContext{context state description}
  }}}}}}}}}"""

MAX_FAILURES = 10
ONCE_POLL_SECONDS = 30
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


def poll_pr(repo: str, pr: int, gh: Callable[[list[str]], str]) -> Summary:
    owner, _, name = repo.rpartition("/")
    out = gh(["api", "graphql", "-f", f"query={QUERY}", "-f", f"o={owner}", "-f", f"n={name}", "-F", f"p={pr}"])
    if not out.strip():
        raise GhError("empty output")
    try:
        return summarize(json.loads(out)["data"]["repository"]["pullRequest"])
    except (ValueError, KeyError, IndexError, TypeError, AttributeError) as exc:
        raise GhError(f"unexpected output: {out.strip()[:200]}") from exc


def watch(
    repo: str,
    branch: str,
    *,
    stall_seconds: float,
    poll_seconds: float,
    skip_head: str,
    once: bool,
    gh: Callable[[list[str]], str],
    sleep: Callable[[float], None],
    now: Callable[[], float],
) -> str:
    """Polls until there is something to report and returns that line."""
    if once:
        poll_seconds = ONCE_POLL_SECONDS
    failures = 0
    pr: int | None = None
    last_sig = ""
    since = now()
    prev: Summary | None = None
    polls = 0

    def failed(message: str) -> str | None:
        nonlocal failures
        failures += 1
        if once or failures >= MAX_FAILURES:
            return f"ERROR {message}"
        sleep(poll_seconds)
        return None

    while True:
        if pr is None:
            try:
                found = find_open_pr(repo, branch, gh)
            except GhError as exc:
                line = failed(f"gh pr list: {exc}")
                if line:
                    return line
                continue
            failures = 0
            if found is None:
                if once:
                    return f"NOT-READY - no open PR from {branch}"
                sleep(poll_seconds)
                continue
            pr = found
            since = now()

        try:
            s = poll_pr(repo, pr, gh)
        except GhError as exc:
            line = failed(f"gh api graphql on PR {pr}: {exc}")
            if line:
                return line
            continue
        failures = 0

        if s.state == "MERGED":
            return f"MERGED {pr}"
        if s.state == "CLOSED":
            return f"CLOSED {pr}"
        skipped = bool(skip_head) and s.head.startswith(skip_head)
        if not skipped and s.merge == "BEHIND":
            return f"ATTENTION {pr} {s.head} behind-base"

        reasons = s.reasons()
        if not reasons and skipped:
            reasons = " skip-head"

        polls += 1
        if not reasons:
            if prev == s:
                return f"READY {pr} {s.head}"
        elif once and polls >= 2:
            return f"NOT-READY {pr} head={s.head[:7]}{reasons}"
        prev = s

        sig = s.head + reasons
        if sig != last_sig:
            last_sig, since = sig, now()
        elif now() - since >= stall_seconds:
            return f"STALLED {pr} head={s.head[:7]}{reasons}"
        sleep(poll_seconds)


def main(argv: list[str]) -> int:
    if not 2 <= len(argv) <= 3:
        print("usage: wait_pr_ready.py <owner/repo> <branch> [stall-hours]", file=sys.stderr)
        return 2
    try:
        stall_hours = int(argv[2]) if len(argv) == 3 else 3
        poll_seconds = float(os.environ.get("POLL_SECONDS") or 120)
    except ValueError as exc:
        print(f"invalid argument or POLL_SECONDS: {exc}", file=sys.stderr)
        return 2
    line = watch(
        argv[0],
        argv[1],
        stall_seconds=stall_hours * 3600,
        poll_seconds=poll_seconds,
        skip_head=os.environ.get("SKIP_HEAD", ""),
        once=os.environ.get("ONCE") == "1",
        gh=run_gh,
        sleep=time.sleep,
        now=time.time,
    )
    print(line, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
