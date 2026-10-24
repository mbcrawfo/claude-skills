#!/usr/bin/env python3
"""Facts about a repo's superpowers spec, plan and research files.

Used by the superpowers-cleanup `cleanup` skill. Every command prints one JSON
object on stdout. On failure it exits 1 and prints one `error: ...` line on
stderr. The script only reads: it never writes, deletes or edits anything.

Commands:
  inventory  every spec, plan and research entry, with dates, links and git state
  refs       every line in the repo that mentions given files, by path, filename or stem
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
        # Literal pathspecs: a file named `what[1].md` must never match `what1.md`.
        # The `-c` settings override user config that would change the output.
        [
            "git",
            "--literal-pathspecs",
            "-c",
            "core.quotepath=off",
            "-c",
            "color.ui=never",
            "-c",
            "grep.column=false",
            *args,
        ],
        cwd=str(root),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
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


def _ref_exists(root: Path, ref: str) -> bool:
    return _git(root, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}", check=False).returncode == 0


def detect_base(root: Path, base: str | None) -> str | None:
    if base is not None:
        if not _ref_exists(root, base):
            raise CliError(f"unknown base ref: {base}")
        return base
    head = _git(root, "symbolic-ref", "--quiet", "refs/remotes/origin/HEAD", check=False)
    if head.returncode == 0:
        ref = head.stdout.strip()
        if _ref_exists(root, ref):
            return ref[len("refs/remotes/") :] if ref.startswith("refs/remotes/") else ref
    for name in ("main", "master"):
        if _ref_exists(root, f"refs/heads/{name}"):
            return name
    return None


def base_branch(root: Path, base: str | None) -> str | None:
    """The branch name a base ref stands for: `origin/main` -> `main`."""
    if base is None:
        return None
    if _ref_exists(root, f"refs/remotes/{base}") and "/" in base:
        return base.split("/", 1)[1]
    return base[len("refs/heads/") :] if base.startswith("refs/heads/") else base


def current_branch(root: Path) -> str | None:
    result = _git(root, "symbolic-ref", "--quiet", "--short", "HEAD", check=False)
    return result.stdout.strip() if result.returncode == 0 else None


def git_facts(root: Path, path: str, base: str | None) -> dict:
    is_dir = (root / path).is_dir()
    added = _git(root, "log", "--diff-filter=A", "--format=%ad", "--date=short", "--", path).stdout.split()
    first_added = added[-1] if added else None

    if is_dir:
        known = _git(root, "ls-files", "-z", "--", path).stdout.strip("\0")
        others = _git(root, "ls-files", "-z", "--others", "--exclude-standard", "--", path).stdout.strip("\0")
        tracked = bool(known) and not others
    else:
        tracked = _git(root, "ls-files", "--error-unmatch", "--", path, check=False).returncode == 0

    status = _git(root, "status", "--porcelain=v1", "-z", "--", path).stdout
    dirty = any(entry[:2] != "??" for entry in status.split("\0") if len(entry) > 3)

    on_base = None
    if base is not None:
        in_base = _git(root, "cat-file", "-e", f"{base}:{path}", check=False).returncode == 0
        same = _git(root, "diff", "--quiet", base, "--", path, check=False).returncode == 0
        on_base = in_base and tracked and same
    return {"first_added": first_added, "tracked": tracked, "dirty": dirty, "on_base": on_base}


def cmd_inventory(args: argparse.Namespace) -> dict:
    root = repo_root(Path(args.repo))
    base = detect_base(root, args.base)
    files, unreadable, missing = collect_files(root, args.specs, args.plans, args.research)
    for item in files:
        item.update(git_facts(root, item["path"], base))
    return {
        "base": base,
        "base_branch": base_branch(root, base),
        "current_branch": current_branch(root),
        "dirs": {"specs": _posix(args.specs), "plans": _posix(args.plans), "research": _posix(args.research)},
        "missing_dirs": missing,
        "files": files,
        "unreadable": unreadable,
    }


_KIND_RANK = {"path": 0, "filename": 1, "stem": 2}


def ref_patterns(target: str) -> dict[str, str]:
    path = _posix(target)
    filename = path.rsplit("/", 1)[-1]
    stem = _DATE_PREFIX.sub("", filename)
    if stem.endswith(".md"):
        stem = stem[: -len(".md")]
    patterns = {"path": path, "filename": filename}
    if stem and stem != filename:
        patterns["stem"] = stem
    return patterns


def _relative_target(root: Path, target: str) -> str:
    path = Path(target)
    if path.is_absolute():
        try:
            path = path.resolve().relative_to(root)
        except ValueError as exc:
            raise CliError(f"target outside the repository: {target}") from exc
    rel = _posix(str(path))
    if not rel or not (root / rel).exists():
        raise CliError(f"no such file: {target}")
    return rel


def _in_targets(file: str, targets: list[str]) -> bool:
    return any(file == t or file.startswith(t + "/") for t in targets)


def find_refs(root: Path, targets: list[str]) -> list[dict]:
    results = []
    for target in targets:
        patterns = ref_patterns(target)
        argv = ["grep", "-n", "-I", "--untracked", "-F", "-z", "--no-color", "--no-column"]
        for value in patterns.values():
            argv += ["-e", value]
        result = _git(root, *argv, check=False)
        if result.returncode not in (0, 1):
            first = (result.stderr.strip().splitlines() or ["git grep failed"])[0]
            raise CliError(first)
        matches = []
        for line in result.stdout.split("\n"):
            parts = line.split("\0", 2)
            if len(parts) != 3:
                continue
            file, lineno, text = parts
            kind = min(
                (k for k, v in patterns.items() if v in text),
                key=_KIND_RANK.__getitem__,
                default=None,
            )
            if kind is None:
                continue
            matches.append(
                {
                    "file": file,
                    "line": int(lineno),
                    "text": text.strip()[:300],
                    "match": kind,
                    "in_target": _in_targets(file, targets),
                }
            )
        matches.sort(key=lambda m: (m["file"], m["line"]))
        results.append({"target": target, "matches": matches})
    return results


def cmd_refs(args: argparse.Namespace) -> dict:
    root = repo_root(Path(args.repo))
    targets = [_relative_target(root, t) for t in args.targets]
    return {"targets": find_refs(root, targets)}


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

    refs = sub.add_parser("refs", help="find every mention of the given files")
    refs.add_argument("--repo", default=".")
    refs.add_argument("targets", nargs="+")
    refs.set_defaults(func=cmd_refs)
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
