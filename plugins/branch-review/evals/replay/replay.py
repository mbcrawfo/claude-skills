"""Small replay: re-run the reviewer on a few past deep rounds and compare.

It is a contributor tool. It answers one question: does a new reviewer version
cost less than past deep rounds did, and find as much? There is no re-run of the
old version. The baseline is what the past rounds cost and found at the time.

Procedure
  1. ``pick`` lists past first deep rounds that were later followed by a second
     opinion, are 5-15 files, and ran on plugin 0.12.0 or later. It spends
     nothing. Trim the written set file to 3-4 entries by hand.
  2. ``run`` replays each entry. In a scratch worktree at the round's DIFF_TO (made
     under a temp directory outside every checkout, so the run loads no other
     repo's project instructions) it
     begins a round in a scratch state dir (never the real plugin data dir),
     runs ``claude -p --agent branch-review:reviewer`` on the SKILL.md section 5
     dispatch prompt, then runs a Sonnet judge that labels every replay finding
     as ``matches`` a known finding, ``new`` or ``duplicate``. Known findings
     are the round-1 findings plus the later second opinion's findings that
     still exist at DIFF_TO.
  3. ``score`` re-scores saved results and spends nothing. A set passes when
     every branch ran, no round-1 major was missed, no new finding failed the
     judge's check, and the replay cost less than the old rounds on all but at
     most one branch. Findings the judge could not verify are listed for a spot-check
     and do not fail the set. `run` exits 1 when the set fails and 0 when it passes.

Spend caps (hard): each reviewer run gets ``--max-budget-usd`` set to
``--round-budget-usd`` (default 15), shrunk so the reviewer plus a $2 judge fit
under what is left of ``--max-total-usd`` (default 40). An entry does not start
when spend has reached the total, or when less than $1 is left for its reviewer.
Spend is the larger of the stream-json token-count estimate (at the list prices
in ``usage_report.PRICES``) and the cost claude reports.

Results go under ``plugins/branch-review/evals/results/`` (gitignored). The
replay reads only local files and always uses a scratch ``--state-dir``.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "usage"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts"))

import review_state  # noqa: E402
import usage_report  # noqa: E402

PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
RESULTS_DIR = PLUGIN_ROOT / "evals" / "results"
SCHEMA_NAME = "report-schema.md"
JUDGE_BUDGET_USD = 2.0
MIN_REVIEWER_USD = 1.0
ALLOWED_TOOLS = ["Read", "Grep", "Glob", "Bash"]
FIRST_DEEP_REASONS = ("no-state", "no-rounds")


def _version(text: str | None) -> tuple[int, ...] | None:
    match = re.match(r"^(\d+)\.(\d+)\.(\d+)", text or "")
    return tuple(int(x) for x in match.groups()) if match else None


def has_commit(repo: str, sha: str) -> bool:
    """Return True when `repo` is a git repo that holds the commit `sha`."""
    if not repo or not Path(repo).is_dir():
        return False
    done = subprocess.run(["git", "cat-file", "-e", f"{sha}^{{commit}}"], cwd=repo, capture_output=True)
    return done.returncode == 0


def resolve_repo(repo: str | None, sha: str, has: Callable[[str, str], bool] = has_commit) -> str | None:
    """Return a checkout of the repo that holds `sha`, or None.

    The run's REPO is often a session worktree deleted after merge. The commit
    is still in the main checkout, which is the path before `/.claude/worktrees/`.
    """
    if not repo:
        return None
    if has(repo, sha):
        return repo
    marker = "/.claude/worktrees/"
    if marker in repo:
        main = repo.split(marker, 1)[0]
        if has(main, sha):
            return main
    return None


def pick_candidates(
    joined: list[dict],
    states: dict[str, dict],
    min_files: int = 5,
    max_files: int = 15,
    min_version: str = "0.12.0",
    repo_for: Callable[[str | None, str], str | None] = resolve_repo,
    skipped: list[str] | None = None,
) -> list[dict]:
    """Return replay-set entries for first deep rounds that had a second opinion.

    `states` maps a state file path to its parsed review.json; a round whose
    state is not in it is left out.
    """
    floor = _version(min_version) or (0, 0, 0)
    out = []
    for r in joined:
        version = _version(r.get("plugin_version"))
        if (
            r["mode"] != "deep"
            or r["focus"]
            or r.get("mode_reason") not in FIRST_DEEP_REASONS
            or not r["runs"]
            or r["state_file"] not in states
            or not min_files <= len(r["changed_files"]) <= max_files
            or version is None
            or version < floor
        ):
            continue
        later = [
            x
            for x in joined
            if x["state_file"] == r["state_file"]
            and x["focus"] == "second-opinion"
            and (x["number"] or 0) > (r["number"] or 0)
        ]
        if not later:
            continue
        second = min(later, key=lambda x: x["number"] or 0)
        run = r["runs"][0]
        repo = repo_for(run.dispatch.get("repo"), r["diff_to"])
        if repo is None:
            if skipped is not None:
                skipped.append(
                    f"{r['branch']}: no checkout holds {r['diff_to'][:10]} (REPO {run.dispatch.get('repo')})"
                )
            continue
        out.append(
            {
                "repo": repo,
                "branch": r["branch"],
                "base_ref": r.get("base_ref"),
                "diff_from": r["diff_from"],
                "diff_to": r["diff_to"],
                "intent": r.get("intent"),
                "state_file": r["state_file"],
                "round": r["number"],
                "old_cost": sum(x.cost for x in r["runs"]),
                "old_transcript": str(run.path),
                "second_opinion_round": second["number"],
                "changed_files": r["changed_files"],
            }
        )
    return out


def known_findings(state: dict, entry: dict, line_count: Callable[[str], int | None]) -> tuple[list[dict], list[dict]]:
    """Return (known, excluded) findings for an entry.

    Known findings are the round-1 findings and the second opinion's findings,
    tagged with their `origin`. A finding judged invalid or duplicate is excluded:
    a replay that rightly does not report it must not count as a miss. A
    second-opinion finding is excluded when its file is absent at DIFF_TO
    (`line_count` gives None) or its first line number is past the end of the
    file: it was found in code the snapshot lacks.
    """
    known, excluded = [], []
    for f in state.get("findings") or []:
        if f.get("scope") != "change":
            continue
        opened = f.get("round_opened")
        if opened == entry["round"]:
            origin = "round-1"
        elif opened == entry.get("second_opinion_round"):
            origin = "second-opinion"
        else:
            continue
        if f.get("status") in usage_report.NOT_REAL:
            excluded.append({**f, "origin": origin})
        elif origin == "round-1":
            known.append({**f, "origin": origin})
        else:
            tagged = {**f, "origin": origin}
            count = line_count(f.get("file") or "")
            first = re.match(r"\d+", f.get("lines") or "")
            if count is None or (first and int(first.group()) > count):
                excluded.append(tagged)
            else:
                known.append(tagged)
    return known, excluded


CLOSING = (
    "Write the whole report to REPORT_PATH before you hand back, however short the round: "
    "the orchestrator records from that file, not from your final message."
)


def dispatch_prompt(fields: dict) -> str:
    """Build the SKILL.md section 5 prompt for a deep round with no optional lines."""
    f = fields
    return f"""Review this change set as your agent definition describes. Inputs:

MODE: {f["MODE"]}
REPO: {f["REPO"]}
BASE_REF: {f["BASE_REF"]}
DIFF_FROM: {f["DIFF_FROM"]}
DIFF_TO: {f["DIFF_TO"]}
REPORT_PATH: {f["REPORT_PATH"]}
CHECKLIST_PATH: {f["CHECKLIST_PATH"]}
SCHEMA_PATH: {f["SCHEMA_PATH"]}
CHECK_COMMAND (run it after you write REPORT_PATH, and fix the report until it passes):
{f["CHECK_COMMAND"]}
CHANGED_FILES (the review's scope; diff only these, with `git diff DIFF_FROM DIFF_TO -- <files>`):
{chr(10).join(f["CHANGED_FILES"])}
INTENT:
{f["INTENT"]}
OPEN_FINDINGS:
[]

{CLOSING}
"""


def _is_major(known: dict) -> bool:
    return known.get("severity") in ("major", "critical")


def _title(findings: list[dict], index) -> str | None:
    if isinstance(index, int) and 0 <= index < len(findings):
        return findings[index].get("title")
    return None


def score(results: list[dict]) -> dict:
    """Score saved per-entry results; see the module docstring for the pass rule."""
    branches = []
    for r in results:
        judge = r.get("judge") or []
        matched = {str(j.get("of")) for j in judge if j.get("label") == "matches"}
        round1 = [k for k in r.get("known") or [] if k.get("origin") == "round-1"]
        new = [j for j in judge if j.get("label") == "new"]
        titles = r.get("replay_findings") or []
        unverified = [
            {"replay": j.get("replay"), "title": _title(titles, j.get("replay")), "check": j.get("check")}
            for j in new
            if j.get("holds") is None
        ]
        branches.append(
            {
                "branch": r.get("branch"),
                "status": r.get("status"),
                "old_cost": r.get("old_cost", 0.0),
                "new_cost": r.get("new_cost", 0.0),
                "majors_missed": sum(1 for k in round1 if _is_major(k) and str(k.get("id")) not in matched),
                "minors_found": sum(1 for k in round1 if not _is_major(k) and str(k.get("id")) in matched),
                "new_held": sum(1 for j in new if j.get("holds") is True),
                "new_not_real": sum(1 for j in new if j.get("holds") is False),
                "new_unverified": len(unverified),
                "unverified": unverified,
                "duplicates": sum(1 for j in judge if j.get("label") == "duplicate"),
            }
        )
    costlier = sum(1 for b in branches if b["new_cost"] >= b["old_cost"])
    passed = (
        bool(branches)
        and all(b["status"] == "ok" for b in branches)
        and all(b["majors_missed"] == 0 for b in branches)
        and sum(b["new_not_real"] for b in branches) == 0
        and costlier <= 1
    )
    spend = sum((r.get("new_cost") or 0.0) + (r.get("judge_cost") or 0.0) for r in results)
    return {"branches": branches, "passed": passed, "spend": spend}


class Runner:
    """The processes the replay starts. Tests replace it with a fake."""

    def __init__(self, plugin: Path):
        self.plugin = plugin

    def _run(self, cmd: list[str], cwd: str | Path | None = None, stdin: str | None = None) -> str:
        done = subprocess.run(cmd, cwd=cwd, input=stdin, capture_output=True, text=True)
        if done.returncode != 0:
            raise RuntimeError(f"{cmd[0]} failed: {(done.stderr or done.stdout).strip()[:300]}")
        return done.stdout

    def worktree_add(self, repo: str, path: Path, rev: str) -> None:
        self._run(["git", "worktree", "add", "--detach", str(path), rev], cwd=repo)

    def worktree_remove(self, repo: str, path: Path) -> None:
        self._run(["git", "worktree", "remove", "--force", str(path)], cwd=repo)

    def rs(self, args: list[str]) -> dict:
        return json.loads(self._run([str(self.plugin / "scripts" / "rs"), *args]))

    def line_count(self, cwd: Path, rev: str, file: str) -> int | None:
        done = subprocess.run(["git", "show", f"{rev}:{file}"], cwd=cwd, capture_output=True)
        return None if done.returncode != 0 else done.stdout.count(b"\n") + (not done.stdout.endswith(b"\n"))

    def _claude(self, args: list[str], prompt: str, cwd: Path, budget: float) -> str:
        cmd = [
            "claude",
            "-p",
            *args,
            "--output-format",
            "stream-json",
            "--verbose",
            "--max-budget-usd",
            f"{budget:.2f}",
        ]
        # A run that hits its budget exits nonzero; its stream is still the record.
        done = subprocess.run(cmd, cwd=cwd, input=prompt, capture_output=True, text=True)
        return done.stdout

    def reviewer(self, prompt: str, cwd: Path, budget: float) -> str:
        # --strict-mcp-config and --tools keep the user's MCP servers and other
        # tools out of the context, so it matches a subagent's (agent tools only).
        args = ["--plugin-dir", str(self.plugin), "--agent", "branch-review:reviewer", "--model", "opus"]
        args += ["--strict-mcp-config", "--tools", *ALLOWED_TOOLS]
        return self._claude([*args, "--allowedTools", *ALLOWED_TOOLS], prompt, cwd, budget)

    def judge(self, prompt: str, cwd: Path, budget: float) -> str:
        args = ["--model", "sonnet", "--strict-mcp-config", "--allowedTools", *ALLOWED_TOOLS]
        return self._claude(args, prompt, cwd, budget)


def stream_costs(stream_text: str, default_family: str) -> dict:
    """Return the token-count estimate, claude's reported cost and their max.

    The reported cost is the final `result` event's `total_cost_usd` (None when
    absent). Spend is the larger of the two, so the caps never rest on our price
    table alone.
    """
    lines = stream_text.splitlines()
    estimate = sum(
        usage_report.cost_usd(u, usage_report.model_family(m) or default_family)
        for m, u in usage_report.message_usages(lines)
    )
    reported = None
    for line in lines:
        obj = usage_report._load(line)
        if obj and obj.get("type") == "result" and isinstance(obj.get("total_cost_usd"), (int, float)):
            reported = float(obj["total_cost_usd"])
    return {"estimate": estimate, "reported": reported, "spend": max(estimate, reported or 0.0)}


def check_context(stream_text: str, plugin: Path) -> str | None:
    """Return why the run's context is wrong, or None when it is right.

    The first `system`/`init` event lists the tools, agents and plugins the run
    loaded. The reviewer must have only ALLOWED_TOOLS, and `branch-review` must
    come from `plugin`, not from an installed copy.
    """
    for line in stream_text.splitlines():
        obj = usage_report._load(line)
        if obj and obj.get("type") == "system" and obj.get("subtype") == "init":
            extra = sorted(set(obj.get("tools") or []) - set(ALLOWED_TOOLS))
            if extra:
                return f"unexpected tools {extra}"
            if obj.get("mcp_servers"):
                return "MCP servers loaded"
            if "branch-review:reviewer" not in (obj.get("agents") or []):
                return "reviewer agent not loaded"
            paths = [p.get("path") for p in obj.get("plugins") or [] if p.get("name") == "branch-review"]
            want = os.path.realpath(plugin)
            if not paths or any(os.path.realpath(p or "") != want for p in paths):
                return f"branch-review plugin loaded from {paths}, not {want}"
            return None
    return "no init event in the stream"


def final_text(stream_text: str) -> str:
    """Return the final answer text of a stream-json run."""
    texts, result = [], None
    for line in stream_text.splitlines():
        obj = usage_report._load(line)
        if not obj:
            continue
        if obj.get("type") == "result" and isinstance(obj.get("result"), str):
            result = obj["result"]
        elif obj.get("type") == "assistant":
            for block in (obj.get("message") or {}).get("content") or []:
                if isinstance(block, dict) and block.get("type") == "text":
                    texts.append(block.get("text", ""))
    return result if result is not None else "\n".join(texts)


def last_json_block(text: str):
    """Return the parsed last fenced json block in text, or None.

    It uses the state script's parser, so the replay accepts what check-report
    accepts. A missing or malformed block is None.
    """
    try:
        return review_state.extract_json_block(text)
    except ValueError:
        return None


def _fill_judge_prompt(known: list[dict], replay_findings: list[dict]) -> str:
    template = (Path(__file__).resolve().parent / "judge-prompt.md").read_text(encoding="utf-8")
    keep = ("id", "origin", "severity", "file", "lines", "title", "trigger", "consequence")
    known_view = [{k: f.get(k) for k in keep} for f in known]
    replay_view = [{"replay": i, **f} for i, f in enumerate(replay_findings)]
    return template.replace("{{KNOWN}}", json.dumps(known_view, indent=1)).replace(
        "{{REPLAY}}", json.dumps(replay_view, indent=1)
    )


def _replay_entry(entry: dict, i: int, plugin: Path, out: Path, budget: float, runner, result: dict) -> None:
    """Replay one entry, filling `result`. Raises on failure.

    The worktree is the reviewer's and the judge's cwd. It lives in a temp
    directory outside every checkout, so `claude -p` finds no other repo's
    project instructions above it. Only results stay under `out`.
    """
    work = out / str(i)
    work.mkdir(parents=True, exist_ok=True)
    (work / "intent.md").write_text(entry.get("intent") or "", encoding="utf-8")
    scratch = Path(tempfile.mkdtemp(prefix="branch-review-replay-"))
    try:
        tree = scratch / "repo"
        runner.worktree_add(entry["repo"], tree, entry["diff_to"])
        failure = None
        try:
            _replay_in_tree(entry, plugin, work, tree, budget, runner, result)
        except Exception as exc:  # the removal below must not hide it
            failure = exc
        try:
            runner.worktree_remove(entry["repo"], tree)
        except Exception as exc:
            removal = f"worktree removal failed: {exc}"
            if failure is not None:
                raise RuntimeError(f"{failure}; {removal}") from failure
            result["reason"] = f"{result['reason']}; {removal}" if result.get("reason") else removal
        if failure is not None:
            raise failure
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def _replay_in_tree(entry: dict, plugin: Path, work: Path, tree: Path, budget: float, runner, result: dict) -> None:
    """Run the reviewer and judge in `tree`, filling `result`."""
    state_dir = work / "state"
    report = work / "round.md"
    begun = runner.rs(
        [
            "begin-round",
            "--state-dir",
            str(state_dir),
            "--repo",
            str(tree),
            "--mode",
            "deep",
            "--mode-reason",
            "replay",
            "--model",
            "opus",
            "--snapshot",
            entry["diff_to"],
            "--base-ref",
            entry["diff_from"],
            "--intent-file",
            str(work / "intent.md"),
        ]  # fmt: skip
    )
    if begun.get("diff_from") != entry["diff_from"]:
        result.update(status="failed", reason="diff mismatch")
        return
    skill = plugin / "skills" / "review"
    rs_path = plugin / "scripts" / "rs"
    prompt = dispatch_prompt(
        {
            "MODE": "deep",
            "REPO": str(tree),
            "BASE_REF": entry["diff_from"],
            "DIFF_FROM": entry["diff_from"],
            "DIFF_TO": entry["diff_to"],
            "REPORT_PATH": str(report),
            "CHECKLIST_PATH": str(skill / "reviewer-checklist.md"),
            "SCHEMA_PATH": str(skill / SCHEMA_NAME),
            "CHECK_COMMAND": (
                f"\"{rs_path}\" check-report --state-dir '{state_dir}' --repo '{tree}' --file '{report}'"
            ),
            "CHANGED_FILES": entry.get("changed_files") or [],
            "INTENT": entry.get("intent") or "",
        }
    )
    stream = runner.reviewer(prompt, tree, budget)
    costs = stream_costs(stream, "opus")  # before any write, so an I/O error keeps the spend
    result.update(new_cost=costs["spend"], new_cost_estimate=costs["estimate"], new_cost_reported=costs["reported"])
    (work / "stream.jsonl").write_text(stream, encoding="utf-8")
    problem = check_context(stream, plugin)
    if problem:
        result.update(status="failed", reason=f"wrong context: {problem}")
        return
    parsed = last_json_block(report.read_text(encoding="utf-8")) if report.is_file() else None
    if not isinstance(parsed, dict) or not isinstance(parsed.get("findings"), list):
        result.update(status="failed", reason="no report")
        return
    state = json.loads(Path(entry["state_file"]).read_text(encoding="utf-8"))
    known, excluded = known_findings(state, entry, lambda f: runner.line_count(tree, entry["diff_to"], f))
    result.update(known=known, excluded=excluded, replay_findings=parsed["findings"])
    if parsed["findings"]:
        judged = runner.judge(_fill_judge_prompt(known, parsed["findings"]), tree, JUDGE_BUDGET_USD)
        costs = stream_costs(judged, "sonnet")
        result.update(
            judge_cost=costs["spend"], judge_cost_estimate=costs["estimate"], judge_cost_reported=costs["reported"]
        )
        (work / "judge.jsonl").write_text(judged, encoding="utf-8")
        labels = last_json_block(final_text(judged))
        if not isinstance(labels, list):
            result.update(status="failed", reason="no judge output")
            return
        result["judge"] = labels
    result["status"] = "ok"


def run_replay(
    entries: list[dict], plugin: Path, out: Path, round_budget: float, max_total: float, runner
) -> list[dict]:
    """Replay each entry in order under the spend caps; return the results."""
    out.mkdir(parents=True, exist_ok=True)
    results, spend = [], 0.0
    for i, entry in enumerate(entries, start=1):
        result = {
            "index": i,
            "branch": entry["branch"],
            "status": "skipped",
            "reason": "",
            "old_cost": entry.get("old_cost", 0.0),
            "new_cost": 0.0,
            "judge_cost": 0.0,
            "known": [],
            "judge": [],
        }
        left = max_total - spend - JUDGE_BUDGET_USD
        budget = min(round_budget, left)
        if spend >= max_total:
            result["reason"] = "total cap reached"
        elif left < MIN_REVIEWER_USD:
            result["reason"] = "budget"
        else:
            try:
                _replay_entry(entry, i, plugin, out, budget, runner, result)
            except Exception as exc:  # report and go on with the next entry
                result.update(status="failed", reason=str(exc)[:300])
        spend += result["new_cost"] + result["judge_cost"]
        (out / str(i)).mkdir(parents=True, exist_ok=True)
        (out / str(i) / "result.json").write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
        results.append(result)
        print(f"[{i}/{len(entries)}] {entry['branch']}: {result['status']} {result['reason']}  spend ${spend:.2f}")
    return results


def load_results(out: Path) -> list[dict]:
    paths = sorted(out.glob("*/result.json"), key=lambda p: int(p.parent.name) if p.parent.name.isdigit() else 0)
    return [json.loads(p.read_text(encoding="utf-8")) for p in paths]


def _print_score(scored: dict) -> None:
    cols = (
        "branch",
        "status",
        "old_cost",
        "new_cost",
        "majors_missed",
        "minors_found",
        "new_held",
        "new_not_real",
        "duplicates",
    )
    print("  ".join(f"{c:>13}" for c in cols))
    for b in scored["branches"]:
        print("  ".join(f"{b[c]:>13.2f}" if isinstance(b[c], float) else f"{b[c]!s:>13}" for c in cols))
    for b in scored["branches"]:
        for u in b["unverified"]:
            print(f"unverified, spot-check: {b['branch']} #{u['replay']} {u['title']!r}: {u['check']}")
    print(f"spend ${scored['spend']:.2f}  {'PASSED' if scored['passed'] else 'FAILED'}")


def _load_states(state_dirs: list[Path], joined: list[dict]) -> dict[str, dict]:
    states = {}
    for r in joined:
        try:
            states[r["state_file"]] = json.loads(Path(r["state_file"]).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
    return states


def cmd_pick(args: argparse.Namespace) -> int:
    state_dirs = args.state_dir if args.state_dir else usage_report._default_state_dirs()
    runs = usage_report.reviewer_runs(args.projects_dir, args.since, args.until)
    rounds = usage_report.state_rounds(state_dirs, args.since, args.until)
    joined = usage_report.join(runs, rounds)
    skipped: list[str] = []
    picked = pick_candidates(
        joined, _load_states(state_dirs, joined), args.min_files, args.max_files, args.min_version, skipped=skipped
    )
    for line in skipped:
        print(f"skipped {line}")
    path = args.output
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(picked, indent=2), encoding="utf-8")
    for n, e in enumerate(picked):
        print(f"{n:>2} {e['branch'][:48]:<48} {len(e['changed_files']):>3} files  old ${e['old_cost']:.2f}")
    print(f"{len(picked)} candidates written to {path}; trim the file to 3-4 entries before `run`")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    entries = json.loads(args.set.read_text(encoding="utf-8"))
    plugin = args.plugin.resolve()
    out = (args.out or RESULTS_DIR / ("replay-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S"))).resolve()
    results = run_replay(entries, plugin, out, args.round_budget_usd, args.max_total_usd, Runner(plugin))
    scored = score(results)
    _print_score(scored)
    print(f"results in {out}")
    return 0 if scored["passed"] else 1


def cmd_score(args: argparse.Namespace) -> int:
    _print_score(score(load_results(args.out)))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(__doc__ or "").split("\n")[0], epilog=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    today = date.today()
    pick = sub.add_parser("pick", help="list candidate rounds (spends nothing)")
    pick.add_argument("--since", type=date.fromisoformat, default=today - timedelta(days=30))
    pick.add_argument("--until", type=date.fromisoformat, default=today)
    pick.add_argument("--projects-dir", type=Path, default=Path.home() / ".claude" / "projects")
    pick.add_argument("--state-dir", type=Path, action="append", default=None)
    pick.add_argument("--min-files", type=int, default=5)
    pick.add_argument("--max-files", type=int, default=15)
    pick.add_argument("--min-version", default="0.12.0")
    pick.add_argument("-o", "--output", type=Path, default=RESULTS_DIR / "replay-set.json")
    pick.set_defaults(func=cmd_pick)
    run = sub.add_parser("run", help="replay the set (spends money, capped)")
    run.add_argument("--set", type=Path, required=True)
    run.add_argument("--plugin", type=Path, required=True)
    run.add_argument("--out", type=Path, default=None)
    run.add_argument("--round-budget-usd", type=float, default=15.0)
    run.add_argument("--max-total-usd", type=float, default=40.0)
    run.set_defaults(func=cmd_run)
    sc = sub.add_parser("score", help="re-score saved results (spends nothing)")
    sc.add_argument("--out", type=Path, required=True)
    sc.set_defaults(func=cmd_score)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
