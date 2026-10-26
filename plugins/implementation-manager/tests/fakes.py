"""Scripted gh and clock fakes shared by the implementation-manager tests."""

from __future__ import annotations

import json

HEAD_A = "a" * 40
HEAD_B = "b" * 40


def check_run(status="COMPLETED", conclusion="SUCCESS", name="build"):
    return {"__typename": "CheckRun", "name": name, "status": status, "conclusion": conclusion}


def status_context(state="SUCCESS", context="ci/legacy"):
    return {"__typename": "StatusContext", "context": context, "state": state, "description": ""}


def make_pr(*, state="OPEN", draft=False, merge="CLEAN", head=HEAD_A, checks=None, threads=(), total_threads=None):
    """A GraphQL `pullRequest` object; a green CheckRun by default."""
    contexts = [check_run()] if checks is None else checks
    return {
        "state": state,
        "isDraft": draft,
        "mergeStateStatus": merge,
        "headRefOid": head,
        "reviewThreads": {
            "totalCount": len(threads) if total_threads is None else total_threads,
            "nodes": [{"isResolved": resolved} for resolved in threads],
        },
        "commits": {"nodes": [{"commit": {"statusCheckRollup": {"contexts": {"nodes": contexts}}}}]},
    }


def graphql(pr):
    return json.dumps({"data": {"repository": {"pullRequest": pr}}})


class FakeGh:
    """Answers `gh pr list` and `gh api graphql` from scripted queues.

    An item is a str (returned), a dict (a pullRequest, wrapped and dumped) or an exception (raised).
    The last `pr list` answer repeats; running out of graphql answers fails the test.

    For several PRs, `pr_list` may be a dict from branch to answers and `polls` a dict from PR number to
    answers. In a dict, each queue's last answer repeats.

    `branches` maps a branch name to a queue of answers for `gh api repos/<repo>/branches/<branch>` (the last
    repeats); an unlisted branch fails the test.
    """

    def __init__(self, pr_list, polls=(), branches=None):
        self.pr_list = {k: list(v) for k, v in pr_list.items()} if isinstance(pr_list, dict) else list(pr_list)
        self.polls = {k: list(v) for k, v in polls.items()} if isinstance(polls, dict) else list(polls)
        self.branches = {k: list(v) for k, v in (branches or {}).items()}
        self.calls = []

    def __call__(self, args):
        self.calls.append(list(args))
        if args[0] == "api" and args[1].startswith("repos/"):
            branch = args[1].split("/branches/", 1)[1]
            if branch not in self.branches:
                raise AssertionError(f"unexpected branch-head call {args}")
            queue = self.branches[branch]
            item = queue.pop(0) if len(queue) > 1 else queue[0]
        elif args[:2] == ["pr", "list"]:
            queue = self.pr_list
            if isinstance(queue, dict):
                queue = queue[args[args.index("--head") + 1]]
            item = queue.pop(0) if len(queue) > 1 else queue[0]
        elif args[:2] == ["api", "graphql"] and isinstance(self.polls, dict):
            queue = self.polls[int(next(arg[2:] for arg in args if arg.startswith("p=")))]
            item = queue.pop(0) if len(queue) > 1 else queue[0]
        elif args[:2] == ["api", "graphql"]:
            if not self.polls:
                raise AssertionError("unexpected extra graphql call")
            item = self.polls.pop(0)
        else:
            raise AssertionError(f"unexpected gh call {args}")
        if isinstance(item, Exception):
            raise item
        return graphql(item) if isinstance(item, dict) else item

    def count(self, kind):
        return sum(1 for call in self.calls if call[:2] == kind)

    def branch_calls(self):
        """The branch-head calls made so far."""
        return [call for call in self.calls if call[0] == "api" and call[1].startswith("repos/")]


PR7 = json.dumps([{"number": 7}])
PR8 = json.dumps([{"number": 8}])
NO_PR = "[]"


class FakeClock:
    def __init__(self):
        self.t = 1_000_000.0
        self.sleeps = []

    def now(self):
        return self.t

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.t += seconds
