#!/usr/bin/env python3
"""Facts about a repo's superpowers spec, plan and research files.

Used by the superpowers-cleanup `cleanup` skill. Every command prints one JSON
object on stdout. On failure it exits 1 and prints one `error: ...` line on
stderr. The script only reads: it never writes, deletes or edits anything.

Commands:
  inventory  every spec, plan and research entry, with dates, links and git state
"""

from __future__ import annotations

import argparse
import datetime
import json
from pathlib import Path
import re
import subprocess
import sys

DEFAULT_SPECS = "docs/superpowers/specs"
DEFAULT_PLANS = "docs/superpowers/plans"
DEFAULT_RESEARCH = "docs/superpowers/research"

_DATE_PREFIX = re.compile(r"^(\d{4}-\d{2}-\d{2})-")
_HEADING = re.compile(r"^#{1,6}\s+(.*\S)\s*$")
_SPEC_LINE = re.compile(r"^\*\*Spec:\*\*\s*(.*)$")
_MD_LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
_BACKTICK = re.compile(r"`([^`]+)`")


class CliError(Exception):
    pass


def _git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(
        ["git", "-c", "core.quotepath=off", *args],
        cwd=str(root),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="surrogateescape",
    )
    if check and result.returncode != 0:
        first = (result.stderr.strip().splitlines() or [f"git {args[0]} failed"])[0]
        raise CliError(first)
    return result


def repo_root(repo: Path) -> Path:
    if not Path(repo).is_dir():
        raise CliError(f"not a git repository: {repo}")
    result = _git(Path(repo), "rev-parse", "--show-toplevel", check=False)
    if result.returncode != 0:
        raise CliError(f"not a git repository: {repo}")
    return Path(result.stdout.strip()).resolve()


def filename_date(name: str) -> str | None:
    match = _DATE_PREFIX.match(name)
    if not match:
        return None
    try:
        datetime.date.fromisoformat(match.group(1))
    except ValueError:
        return None
    return match.group(1)


def first_heading(text: str) -> str | None:
    for line in text.splitlines():
        match = _HEADING.match(line)
        if match:
            return match.group(1).strip()
    return None


def parse_spec_link(text: str) -> str | None:
    for line in text.splitlines():
        match = _SPEC_LINE.match(line.strip())
        if not match:
            continue
        rest = match.group(1).strip()
        link = _MD_LINK.search(rest)
        if link:
            return link.group(1)
        tick = _BACKTICK.search(rest)
        if tick:
            return tick.group(1).strip()
        return rest.split()[0] if rest else None
    return None


def _posix(path: str) -> str:
    parts = []
    for part in Path(path).as_posix().split("/"):
        if part in ("", "."):
            continue
        parts.append(part)
    return "/".join(parts)


def resolve_spec_path(root: Path, plan_path: Path, raw: str) -> str:
    cleaned = raw[2:] if raw.startswith("./") else raw
    if (root / cleaned).exists():
        return _posix(cleaned)
    candidate = (plan_path.parent / raw).resolve()
    try:
        rel = candidate.relative_to(root)
    except ValueError:
        rel = None
    if rel is not None and candidate.exists():
        return rel.as_posix()
    return _posix(cleaned)


def cited_research(text: str, research_dir: str, entries: list[str]) -> list[str]:
    cited = []
    for entry in entries:
        name = entry.rsplit("/", 1)[-1]
        # The name must end there: no further name characters, and no `.ext`
        # (a sentence-ending period is fine).
        pattern = re.compile(
            r"(?:" + re.escape(f"{research_dir}/{name}") + "|" + re.escape(name) + r")(?![A-Za-z0-9_-]|\.[A-Za-z0-9])"
        )
        if pattern.search(text):
            cited.append(entry)
    return sorted(cited)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _md_files(folder: Path) -> list[Path]:
    return sorted(p for p in folder.iterdir() if p.is_file() and p.suffix == ".md")


def _research_entries(folder: Path) -> list[Path]:
    return sorted(p for p in folder.iterdir() if not p.name.startswith("."))


def _research_title(entry: Path) -> str | None:
    if entry.is_file():
        return first_heading(_read(entry)) if entry.suffix == ".md" else None
    for name in ("README.md", "index.md"):
        candidate = entry / name
        if candidate.is_file():
            return first_heading(_read(candidate))
    return None


def collect_files(root: Path, specs: str, plans: str, research: str) -> tuple[list[dict], list[dict], list[str]]:
    files: list[dict] = []
    unreadable: list[dict] = []
    missing: list[str] = []
    dirs = {"spec": _posix(specs), "plan": _posix(plans), "research": _posix(research)}
    for rel in dirs.values():
        if not (root / rel).is_dir():
            missing.append(rel)

    research_dir = root / dirs["research"]
    research_paths = _research_entries(research_dir) if research_dir.is_dir() else []
    research_rel = [p.relative_to(root).as_posix() for p in research_paths]

    for entry in research_paths:
        rel = entry.relative_to(root).as_posix()
        try:
            title = _research_title(entry)
        except (UnicodeDecodeError, OSError) as exc:
            unreadable.append({"path": rel, "reason": str(exc)})
            continue
        files.append({"kind": "research", "path": rel, "filename_date": filename_date(entry.name), "title": title})

    for kind in ("spec", "plan"):
        folder = root / dirs[kind]
        if not folder.is_dir():
            continue
        for path in _md_files(folder):
            rel = path.relative_to(root).as_posix()
            try:
                text = _read(path)
            except (UnicodeDecodeError, OSError) as exc:
                unreadable.append({"path": rel, "reason": str(exc)})
                continue
            item = {"kind": kind, "path": rel, "filename_date": filename_date(path.name), "title": first_heading(text)}
            if kind == "plan":
                raw = parse_spec_link(text)
                spec = resolve_spec_path(root, path, raw) if raw else None
                item["spec"] = spec
                item["spec_exists"] = bool(spec) and (root / spec).is_file()
            else:
                item["research"] = cited_research(text, dirs["research"], research_rel)
            files.append(item)

    files.sort(key=lambda f: f["path"])
    unreadable.sort(key=lambda f: f["path"])
    return files, unreadable, missing


def cmd_inventory(args: argparse.Namespace) -> dict:
    root = repo_root(Path(args.repo))
    files, unreadable, missing = collect_files(root, args.specs, args.plans, args.research)
    return {
        "dirs": {"specs": _posix(args.specs), "plans": _posix(args.plans), "research": _posix(args.research)},
        "missing_dirs": missing,
        "files": files,
        "unreadable": unreadable,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="docs_inventory.py", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    inv = sub.add_parser("inventory", help="list spec, plan and research files with their facts")
    inv.add_argument("--repo", default=".")
    inv.add_argument("--specs", default=DEFAULT_SPECS)
    inv.add_argument("--plans", default=DEFAULT_PLANS)
    inv.add_argument("--research", default=DEFAULT_RESEARCH)
    inv.add_argument("--base", default=None)
    inv.set_defaults(func=cmd_inventory)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = args.func(args)
    except CliError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
