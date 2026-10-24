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
import os
import sys
import time

from prstate import GhError, Summary, check_once, find_open_pr, poll_pr, run_gh

MAX_FAILURES = 10
ONCE_POLL_SECONDS = 30


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
        return check_once(repo, branch, gh=gh, sleep=sleep, confirm_seconds=ONCE_POLL_SECONDS)
    failures = 0
    pr: int | None = None
    last_sig = ""
    since = now()
    prev: Summary | None = None

    def failed(message: str) -> str | None:
        nonlocal failures
        failures += 1
        if failures >= MAX_FAILURES:
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

        if not reasons:
            if prev == s:
                return f"READY {pr} {s.head}"
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
