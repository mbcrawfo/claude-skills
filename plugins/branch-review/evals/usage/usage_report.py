"""Report what branch-review reviewer runs cost and what they found.

Reads, and only reads:
  * reviewer subagent transcripts under ``~/.claude/projects/*/*/subagents/``
    (``agent-<id>.jsonl`` with a ``.meta.json`` whose ``agentType`` is
    ``branch-review:reviewer``);
  * ``review.json`` state files under the ``branch-review*`` directories of
    ``~/.claude/plugins/data/``.

It joins each round in the state to the reviewer run with the same
``diff_from``/``diff_to``, then groups rounds by
``mode/focus/model/shape/plugin_version`` and prints cost, turns, peak context
and finding counts per group. It writes nothing and calls no network service.

Cost comes from the token counts in each transcript, counted once per distinct
``message.id`` (a message can span several lines; the last line has the final
counts). Prices are USD per million tokens, from public list prices: Opus
$5/$25, Sonnet $3/$15 (input/output). Cache write is 1.25x input for 5 minutes
and 2x for 1 hour; cache read is 0.1x input. The Haiku and Fable rows are
placeholders to check against current list prices.

Usage: python3 usage_report.py [--since YYYY-MM-DD] [--until YYYY-MM-DD]
       [--projects-dir PATH] [--state-dir PATH ...] [--json]
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
import json
from pathlib import Path
import re
import statistics
import sys

# family -> (input, output) USD per million tokens.
# Haiku and Fable: check against current list prices before relying on them.
PRICES: dict[str, tuple[float, float]] = {
    "opus": (5.0, 25.0),
    "sonnet": (3.0, 15.0),
    "haiku": (1.0, 5.0),
    "fable": (15.0, 75.0),
}

REVIEWER_AGENT = "branch-review:reviewer"


def model_family(model: str | None) -> str | None:
    """Return the price family named in a model id or Agent-tool name."""
    if not model:
        return None
    lowered = model.lower()
    for family in PRICES:
        if family in lowered:
            return family
    return None


def _load(line: str) -> dict | None:
    try:
        obj = json.loads(line)
    except ValueError:
        return None
    return obj if isinstance(obj, dict) else None


def message_usages(lines: Iterable[str]) -> list[tuple[str | None, dict]]:
    """Return one (model, usage) per assistant message id, last line winning."""
    seen: dict[str, tuple[str | None, dict]] = {}
    for line in lines:
        obj = _load(line)
        if not obj or obj.get("type") != "assistant":
            continue
        message = obj.get("message") or {}
        usage = message.get("usage")
        if not isinstance(usage, dict):
            continue
        # dict assignment keeps the first-seen position and the last value.
        seen[message.get("id")] = (message.get("model"), usage)
    return list(seen.values())


def cost_usd(usage: dict, family: str) -> float:
    """Price one message's usage in USD."""
    price_in, price_out = PRICES[family]
    breakdown = usage.get("cache_creation")
    if isinstance(breakdown, dict) and breakdown:
        write_5m = breakdown.get("ephemeral_5m_input_tokens", 0) or 0
        write_1h = breakdown.get("ephemeral_1h_input_tokens", 0) or 0
    else:
        write_5m = usage.get("cache_creation_input_tokens", 0) or 0
        write_1h = 0
    total = (
        (usage.get("input_tokens", 0) or 0) * price_in
        + write_5m * price_in * 1.25
        + write_1h * price_in * 2.0
        + (usage.get("cache_read_input_tokens", 0) or 0) * price_in * 0.1
        + (usage.get("output_tokens", 0) or 0) * price_out
    )
    return total / 1_000_000


def _field(prompt: str, label: str) -> str | None:
    match = re.search(rf"^{label}: *(.*)$", prompt, re.M)
    if not match:
        return None
    return match.group(1).strip() or None


def parse_dispatch(prompt: str) -> dict:
    """Read the inputs of a reviewer dispatch prompt (SKILL.md section 5)."""
    files: list[str] = []
    match = re.search(r"^CHANGED_FILES[^\n]*\n", prompt, re.M)
    if match:
        for line in prompt[match.end() :].split("\n"):
            if not line.strip() or re.match(r"^[A-Z_]+:", line):
                break
            files.append(line.strip())
    version = re.search(r"branch-review/([^/\s]+)/", _field(prompt, "CHECKLIST_PATH") or "")
    return {
        "mode": _field(prompt, "MODE"),
        "focus": _field(prompt, "FOCUS"),
        "repo": _field(prompt, "REPO"),
        "diff_from": _field(prompt, "DIFF_FROM"),
        "diff_to": _field(prompt, "DIFF_TO"),
        "changed_files": files,
        "plugin_version": version.group(1) if version else None,
    }


@dataclass
class ReviewerRun:
    path: Path
    started: str
    model: str | None
    cost: float
    turns: int
    peak_context: int
    tool_calls: int
    dispatch: dict


def _first_prompt(objs: list[dict]) -> str:
    for obj in objs:
        if obj.get("type") != "user":
            continue
        content = (obj.get("message") or {}).get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "".join(b.get("text", "") for b in content if isinstance(b, dict))
        return ""
    return ""


def _in_range(stamp: str, since: date, until: date) -> bool:
    try:
        day = datetime.fromisoformat(stamp.replace("Z", "+00:00")).date()
    except ValueError:
        return False
    return since <= day <= until


def reviewer_runs(projects_dir: Path, since: date, until: date) -> list[ReviewerRun]:
    """Return every branch-review reviewer transcript with a timestamp in range."""
    runs = []
    for meta_path in sorted(projects_dir.glob("*/*/subagents/*.meta.json")):
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(meta, dict) or meta.get("agentType") != REVIEWER_AGENT:
            continue
        transcript = meta_path.with_name(meta_path.name[: -len(".meta.json")] + ".jsonl")
        try:
            lines = transcript.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        objs = [o for o in map(_load, lines) if o]
        stamps = sorted(str(o["timestamp"]) for o in objs if o.get("timestamp"))
        if not any(_in_range(s, since, until) for s in stamps):
            continue
        usages = message_usages(lines)
        model = next((m for m, _ in reversed(usages) if m), None)
        family = model_family(model) or model_family(meta.get("model")) or "opus"
        tool_ids = set()
        for o in objs:
            if o.get("type") != "assistant":
                continue
            for block in (o.get("message") or {}).get("content") or []:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    tool_ids.add(block.get("id") or id(block))
        peak = max(
            (
                (u.get("input_tokens", 0) or 0)
                + (u.get("cache_creation_input_tokens", 0) or 0)
                + (u.get("cache_read_input_tokens", 0) or 0)
                for _, u in usages
            ),
            default=0,
        )
        runs.append(
            ReviewerRun(
                path=transcript,
                started=stamps[0],
                model=family,
                cost=sum(cost_usd(u, family) for _, u in usages),
                turns=len(usages),
                peak_context=peak,
                tool_calls=len(tool_ids),
                dispatch=parse_dispatch(_first_prompt(objs)),
            )
        )
    return runs


def state_rounds(state_dirs: list[Path], since: date, until: date) -> list[dict]:
    """Return one dict per review round found under the state directories.

    Rounds of mode ``recorded`` are not reviews (no model, no reviewer run) and
    are left out. A state file is skipped whole when any other round's model is
    not a known family, which drops test fixtures such as ``test-model``.
    """
    out = []
    for state_dir in state_dirs:
        for path in sorted(state_dir.glob("*/*/review.json")):
            try:
                state = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(state, dict):
                continue
            rounds = [r for r in state.get("rounds") or [] if r.get("mode") != "recorded"]
            if any(model_family(r.get("model")) is None for r in rounds):
                continue
            findings = state.get("findings") or []
            for rnd in rounds:
                if not _in_range(str(rnd.get("started") or ""), since, until):
                    continue
                number = rnd.get("number")
                out.append(
                    {
                        "branch": state.get("branch") or path.parent.name,
                        "number": number,
                        "mode": rnd.get("mode"),
                        "focus": rnd.get("focus"),
                        "model": rnd.get("model"),
                        "shape": rnd.get("shape", "single"),
                        "plugin_version": rnd.get("plugin_version"),
                        "diff_from": rnd.get("diff_from"),
                        "diff_to": rnd.get("diff_to"),
                        "changed_files": rnd.get("changed_files") or [],
                        "findings": [
                            f for f in findings if f.get("round_opened") == number and f.get("scope") == "change"
                        ],
                        "started": rnd.get("started"),
                        "mode_reason": rnd.get("mode_reason"),
                        "state_file": str(path),
                        "base_ref": state.get("base_ref"),
                        "intent": state.get("intent"),
                    }
                )
    return out


def join(runs: list[ReviewerRun], rounds: list[dict]) -> list[dict]:
    """Attach to each round every reviewer run that matches it.

    A run matches on diff range, mode and focus. `runs` lists them all (a round
    can have several reviewer runs); `run` is the first, or None, kept for
    callers that expect one.
    """
    by_key: dict[tuple, list[ReviewerRun]] = {}
    for run in runs:
        d = run.dispatch
        if d.get("diff_from") and d.get("diff_to"):
            key = (d["diff_from"], d["diff_to"], d.get("mode"), d.get("focus"))
            by_key.setdefault(key, []).append(run)
    out = []
    for r in rounds:
        key = (r["diff_from"], r["diff_to"], r["mode"], r["focus"])
        matched = by_key.get(key, [])
        out.append({**r, "runs": matched, "run": matched[0] if matched else None})
    return out


def _p90(values: list[float]) -> float:
    ordered = sorted(values)
    return ordered[max(0, -(-len(ordered) * 9 // 10) - 1)] if ordered else 0.0


def summarize(joined: list[dict]) -> dict:
    """Group joined rounds and count findings; list the second opinions."""
    grouped: dict[str, list[dict]] = defaultdict(list)
    for r in joined:
        key = "/".join(str(x) for x in (r["mode"], r["focus"] or "-", r["model"], r["shape"], r["plugin_version"]))
        grouped[key].append(r)
    groups = {}
    for key, items in sorted(grouped.items()):
        with_runs = [r["runs"] for r in items if r["runs"]]
        runs = [x for rs in with_runs for x in rs]
        costs = [sum(x.cost for x in rs) for rs in with_runs]
        turns = sum(x.turns for x in runs)
        counts = defaultdict(int)
        for r in items:
            for f in r["findings"]:
                counts[f.get("severity")] += 1
                if f.get("status") in ("invalid", "duplicate"):
                    counts[f["status"]] += 1
        groups[key] = {
            "rounds": len(items),
            "cost_total": sum(costs),
            "cost_median": statistics.median(costs) if costs else 0.0,
            "cost_p90": _p90(costs),
            "turns_median": statistics.median([sum(x.turns for x in rs) for rs in with_runs]) if runs else 0,
            "peak_median": statistics.median([max(x.peak_context for x in rs) for rs in with_runs]) if runs else 0,
            "calls_per_turn": sum(x.tool_calls for x in runs) / turns if turns else 0.0,
            "major": counts["major"],
            "minor": counts["minor"],
            "critical": counts["critical"],
            "invalid": counts["invalid"],
            "duplicate": counts["duplicate"],
        }
    by_branch: dict[str, list[dict]] = defaultdict(list)
    for r in joined:
        by_branch[r["branch"]].append(r)
    second = []
    for branch, items in sorted(by_branch.items()):
        items.sort(key=lambda r: r["number"] or 0)
        for r in items:
            if r["focus"] != "second-opinion":
                continue
            prior = [p for p in items if p["mode"] == "deep" and (p["number"] or 0) < (r["number"] or 0)]
            second.append(
                {
                    "branch": branch,
                    "majors": sum(1 for f in r["findings"] if f.get("severity") == "major"),
                    "prior_shape": prior[-1]["shape"] if prior else None,
                }
            )
    return {"groups": groups, "second_opinions": second}


def _default_state_dirs() -> list[Path]:
    base = Path.home() / ".claude" / "plugins" / "data"
    if not base.is_dir():
        return []
    return sorted(p for p in base.iterdir() if p.is_dir() and p.name.startswith("branch-review"))


def _format(summary: dict) -> str:
    head = (
        f"{'group':<52} {'n':>3} {'total$':>8} {'med$':>6} {'p90$':>6} {'turns':>5} "
        f"{'peak':>8} {'c/t':>4} {'crit':>4} {'maj':>3} {'min':>3} {'inv':>3} {'dup':>3}"
    )
    rows = [head]
    for key, g in summary["groups"].items():
        rows.append(
            f"{key:<52} {g['rounds']:>3} {g['cost_total']:>8.2f} {g['cost_median']:>6.2f} "
            f"{g['cost_p90']:>6.2f} {g['turns_median']:>5.0f} {g['peak_median']:>8.0f} "
            f"{g['calls_per_turn']:>4.1f} {g['critical']:>4} {g['major']:>3} {g['minor']:>3} "
            f"{g['invalid']:>3} {g['duplicate']:>3}"
        )
    by_shape: dict[str | None, list[int]] = defaultdict(list)
    for s in summary["second_opinions"]:
        by_shape[s["prior_shape"]].append(s["majors"])
    for shape, majors in sorted(by_shape.items(), key=lambda kv: str(kv[0])):
        rows.append(f"second opinions after {shape} deep: {len(majors)}, {sum(majors) / len(majors):.2f} majors each")
    return "\n".join(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    today = date.today()
    parser.add_argument("--since", type=date.fromisoformat, default=today - timedelta(days=14))
    parser.add_argument("--until", type=date.fromisoformat, default=today)
    parser.add_argument("--projects-dir", type=Path, default=Path.home() / ".claude" / "projects")
    parser.add_argument("--state-dir", type=Path, action="append", default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    state_dirs = args.state_dir if args.state_dir else _default_state_dirs()
    runs = reviewer_runs(args.projects_dir, args.since, args.until)
    rounds = state_rounds(state_dirs, args.since, args.until)
    summary = summarize(join(runs, rounds))
    if args.json:
        json.dump(summary, sys.stdout, indent=2, default=str)
        print()
    else:
        print(_format(summary))
        total = sum(r.cost for r in runs)
        print(f"{len(runs)} reviewer runs, {len(rounds)} rounds, ${total:.2f} total")
    return 0


if __name__ == "__main__":
    sys.exit(main())
