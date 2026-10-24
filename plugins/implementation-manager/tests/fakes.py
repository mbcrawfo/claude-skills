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
    """

    def __init__(self, pr_list, polls=()):
        self.pr_list = list(pr_list)
        self.polls = list(polls)
        self.calls = []

    def __call__(self, args):
        self.calls.append(list(args))
        if args[:2] == ["pr", "list"]:
            item = self.pr_list.pop(0) if len(self.pr_list) > 1 else self.pr_list[0]
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


PR7 = json.dumps([{"number": 7}])
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
