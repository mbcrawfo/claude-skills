"""Small replay: re-run the reviewer on a few past deep rounds and compare.

It is a contributor tool. It answers one question: does a new reviewer version
cost less than past deep rounds did, and find as much? There is no re-run of the
old version. The baseline is what the past rounds cost and found at the time.

Procedure
  1. ``pick`` lists past first deep rounds that were later followed by a second
     opinion, are 5-15 files, and ran on plugin 0.12.0 or later. It spends
     nothing. Trim the written set file to 3-4 entries by hand.
  2. ``run`` replays each entry. In a scratch worktree at the round's DIFF_TO it
     begins a round in a scratch state dir (never the real plugin data dir),
     runs ``claude -p --agent branch-review:reviewer`` on the SKILL.md section 5
     dispatch prompt, then runs a Sonnet judge that labels every replay finding
     as ``matches`` a known finding, ``new`` or ``duplicate``. Known findings
     are the round-1 findings plus the later second opinion's findings that
     still exist at DIFF_TO.
  3. ``score`` re-scores saved results and spends nothing. A set passes when
     every branch ran, no round-1 major was missed, no new finding failed the
     judge's check, and the replay cost less than the old rounds on all but at
     most one branch.

Spend caps (hard): each reviewer run gets ``--max-budget-usd`` set to
``--round-budget-usd`` (default 15), shrunk so the reviewer plus a $2 judge fit
under what is left of ``--max-total-usd`` (default 40). An entry does not start
when spend has reached the total. Spend is counted from the stream-json token
counts, at the list prices in ``usage_report.PRICES``.

Results go under ``plugins/branch-review/evals/results/`` (gitignored). The
replay reads only local files and always uses a scratch ``--state-dir``.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
import re
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "usage"))

import usage_report  # noqa: E402

PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
RESULTS_DIR = PLUGIN_ROOT / "evals" / "results"
SCHEMA_NAME = "report-schema.md"
JUDGE_BUDGET_USD = 2.0
ALLOWED_TOOLS = ["Read", "Grep", "Glob", "Bash"]
FIRST_DEEP_REASONS = ("no-state", "no-rounds")


def _version(text: str | None) -> tuple[int, ...] | None:
    match = re.match(r"^(\d+)\.(\d+)\.(\d+)", text or "")
    return tuple(int(x) for x in match.groups()) if match else None


def pick_candidates(
    joined: list[dict],
    states: dict[str, dict],
    min_files: int = 5,
    max_files: int = 15,
    min_version: str = "0.12.0",
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
        out.append(
            {
                "repo": run.dispatch.get("repo"),
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
    tagged with their `origin`. A second-opinion finding is excluded when its
    file is absent at DIFF_TO (`line_count` gives None) or its first line number
    is past the end of the file: it was found in code the snapshot lacks.
    """
    known, excluded = [], []
    for f in state.get("findings") or []:
        if f.get("scope") != "change":
            continue
        opened = f.get("round_opened")
        if opened == entry["round"]:
            known.append({**f, "origin": "round-1"})
        elif opened == entry.get("second_opinion_round"):
            tagged = {**f, "origin": "second-opinion"}
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


def score(results: list[dict]) -> dict:
    """Score saved per-entry results; see the module docstring for the pass rule."""
    branches = []
    for r in results:
        judge = r.get("judge") or []
        matched = {str(j.get("of")) for j in judge if j.get("label") == "matches"}
        round1 = [k for k in r.get("known") or [] if k.get("origin") == "round-1"]
        new = [j for j in judge if j.get("label") == "new"]
        branches.append(
            {
                "branch": r.get("branch"),
                "status": r.get("status"),
                "old_cost": r.get("old_cost", 0.0),
                "new_cost": r.get("new_cost", 0.0),
                "majors_missed": sum(1 for k in round1 if _is_major(k) and str(k.get("id")) not in matched),
                "minors_found": sum(1 for k in round1 if not _is_major(k) and str(k.get("id")) in matched),
                "new_held": sum(1 for j in new if j.get("holds") is True),
                # A new finding the judge could not confirm (null) counts as not real.
                "new_not_real": sum(1 for j in new if j.get("holds") is not True),
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
        args = ["--plugin-dir", str(self.plugin), "--agent", "branch-review:reviewer", "--model", "opus"]
        return self._claude([*args, "--allowedTools", *ALLOWED_TOOLS], prompt, cwd, budget)

    def judge(self, prompt: str, cwd: Path, budget: float) -> str:
        return self._claude(["--model", "sonnet", "--allowedTools", *ALLOWED_TOOLS], prompt, cwd, budget)


def stream_cost(stream_text: str, default_family: str) -> float:
    lines = stream_text.splitlines()
    return sum(
        usage_report.cost_usd(u, usage_report.model_family(m) or default_family)
        for m, u in usage_report.message_usages(lines)
    )


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
    """Return the parsed last fenced json block in text, or None."""
    blocks = re.findall(r"```json[ \t]*\r?\n(.*?)```", text, re.S | re.I)
    for body in reversed(blocks[-1:]):
        try:
            return json.loads(body)
        except ValueError:
            return None
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
    """Replay one entry, filling `result`. Raises on failure."""
    work = out / str(i)
    work.mkdir(parents=True, exist_ok=True)
    tree = work / "repo"
    state_dir = work / "state"
    report = work / "round.md"
    (work / "intent.md").write_text(entry.get("intent") or "", encoding="utf-8")
    runner.worktree_add(entry["repo"], tree, entry["diff_to"])
    try:
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
                    f"\"{rs_path}\" check-report --state-dir {state_dir} --repo '{tree}' --file '{report}'"
                ),
                "CHANGED_FILES": entry.get("changed_files") or [],
                "INTENT": entry.get("intent") or "",
            }
        )
        stream = runner.reviewer(prompt, tree, budget)
        (work / "stream.jsonl").write_text(stream, encoding="utf-8")
        result["new_cost"] = stream_cost(stream, "opus")
        parsed = last_json_block(report.read_text(encoding="utf-8")) if report.is_file() else None
        if not isinstance(parsed, dict) or not isinstance(parsed.get("findings"), list):
            result.update(status="failed", reason="no report")
            return
        state = json.loads(Path(entry["state_file"]).read_text(encoding="utf-8"))
        known, excluded = known_findings(state, entry, lambda f: runner.line_count(tree, entry["diff_to"], f))
        result.update(known=known, excluded=excluded, replay_findings=parsed["findings"])
        if parsed["findings"]:
            judged = runner.judge(_fill_judge_prompt(known, parsed["findings"]), tree, JUDGE_BUDGET_USD)
            (work / "judge.jsonl").write_text(judged, encoding="utf-8")
            result["judge_cost"] = stream_cost(judged, "sonnet")
            labels = last_json_block(final_text(judged))
            if not isinstance(labels, list):
                result.update(status="failed", reason="no judge output")
                return
            result["judge"] = labels
        result["status"] = "ok"
    finally:
        runner.worktree_remove(entry["repo"], tree)


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
        budget = min(round_budget, max_total - spend - JUDGE_BUDGET_USD)
        if spend >= max_total or budget <= 0:
            result["reason"] = "total cap reached"
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
    picked = pick_candidates(joined, _load_states(state_dirs, joined), args.min_files, args.max_files, args.min_version)
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
    _print_score(score(results))
    print(f"results in {out}")
    return 0


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
