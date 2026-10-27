#!/usr/bin/env python3
"""Facts about a repo's AGENTS.md files, for the maintenance plugin's `agents-md-cleanup` skill.

Every command prints one JSON object on stdout. On failure it exits 1 and prints one
`error: ...` line on stderr. The script only reads: it never writes, deletes or edits anything.

Commands:
  inventory  every AGENTS.md in scope (and the ancestors of a partial run), split into blocks,
             with git state, the root file's guidelines section and any saved rulings
  blocks     one file's blocks and content hash, to check it before an edit is applied

`split_blocks` cuts a markdown file into the addressable blocks the skill's verdicts refer to.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import posixpath
import re
import subprocess
import sys

RULINGS_PATH = ".claude/agents-md-cleanup.md"

_ATX_HEADING = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*?))?[ \t]*$")
_ATX_CLOSING = re.compile(r"(?:^|[ \t]+)#+[ \t]*$")
_SETEXT_1 = re.compile(r"^ {0,3}=+[ \t]*$")
_SETEXT_2 = re.compile(r"^ {0,3}-+[ \t]*$")
_THEMATIC = re.compile(r"^ {0,3}([-*_])(?:[ \t]*\1){2,}[ \t]*$")
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
_LIST_MARKER = re.compile(r"^(?:[-*+]|\d{1,9}[.)])(?:[ \t]+|$)")
# What may interrupt a paragraph: a bullet, or an ordered item numbered 1, with content.
_LIST_INTERRUPT = re.compile(r"^(?:[-*+]|1[.)])[ \t]+\S")
_QUOTE = re.compile(r"^ {0,3}>")
_YAML_KEY = re.compile(r"^[A-Za-z_][\w-]*:")
_TABLE_DELIMITER = re.compile(r"^[ \t]*\|?[ \t]*:?-+:?[ \t]*(?:\|[ \t]*:?-+:?[ \t]*)*\|?[ \t]*$")


class CliError(Exception):
    pass


def _indent(line: str) -> int:
    expanded = line.expandtabs(4)
    return len(expanded) - len(expanded.lstrip())


def _fence_open(line: str) -> tuple[str, int] | None:
    match = _FENCE.match(line)
    if not match:
        return None
    fence, info = match.groups()
    if fence[0] == "`" and "`" in info:
        return None  # inline code span, not a fence
    return fence[0], len(fence)


def _fence_end(lines: list[str], start: int) -> int:
    """Index of the line that closes the fence opened at `start`; the last line if it never closes."""
    char, length = _fence_open(lines[start])  # type: ignore[misc]
    for j in range(start + 1, len(lines)):
        stripped = lines[j].strip()
        if _indent(lines[j]) < 4 and stripped.startswith(char * length) and not stripped.strip(char):
            return j
    return len(lines) - 1


def _is_comment_start(line: str) -> bool:
    return _indent(line) < 4 and line.lstrip().startswith("<!--")


def _is_table_start(lines: list[str], i: int) -> bool:
    return (
        i + 1 < len(lines)
        and "|" in lines[i]
        and "|" in lines[i + 1]
        and _TABLE_DELIMITER.match(lines[i + 1]) is not None
    )


def _is_block_start(line: str) -> bool:
    """A line that always starts a block of its own, ending a paragraph or list item before it."""
    return bool(
        _ATX_HEADING.match(line)
        or _fence_open(line)
        or _THEMATIC.match(line)
        or _QUOTE.match(line)
        or _is_comment_start(line)
    )


def _list_item_end(lines: list[str], start: int) -> int:
    """Index of the last line of the list item that starts at `start`."""
    base = _indent(lines[start])
    last = start
    j = start + 1
    while j < len(lines):
        line = lines[j]
        if not line.strip():
            following = next((k for k in range(j + 1, len(lines)) if lines[k].strip()), None)
            if following is None or _indent(lines[following]) < 2:
                break
            j = following
            continue
        indent = _indent(line)
        if indent < 2 and _is_block_start(line):
            break
        if _LIST_MARKER.match(line.lstrip()) and indent <= base:
            break
        if indent >= 2 and _fence_open(line):
            j = _fence_end(lines, j)
        last = j
        j += 1
    return last


def _paragraph_end(lines: list[str], start: int) -> tuple[int, int]:
    """Return (last line index, setext heading level or 0) for the paragraph that starts at `start`."""
    j = start + 1
    while j < len(lines):
        line = lines[j]
        if not line.strip():
            break
        if _SETEXT_1.match(line):
            return j, 1
        if _SETEXT_2.match(line):
            return j, 2
        if (
            _is_block_start(line)
            or (_indent(line) < 4 and _LIST_INTERRUPT.match(line.lstrip()))
            or _is_table_start(lines, j)
        ):
            break
        j += 1
    return j - 1, 0


def _front_matter_end(lines: list[str]) -> int:
    """Index of the line after a leading `---` front matter block, or 0 when there is none.

    The line after the opening `---` must look like a YAML key, so a thematic break is never read as front matter.
    """
    if len(lines) > 1 and lines[0].rstrip() == "---" and _YAML_KEY.match(lines[1]):
        for j in range(1, len(lines)):
            if lines[j].rstrip() == "---":
                return j + 1
    return 0


def _split_lines(text: str) -> list[str]:
    # Split on "\n" only (not str.splitlines, which also splits on form feed, U+2028 and the like), so line
    # numbers match an editor's. One trailing "\r" per line is dropped so CRLF files work.
    lines = [line[:-1] if line.endswith("\r") else line for line in text.split("\n")]
    if lines and lines[-1] == "":
        lines.pop()  # the final newline ends the last line; it does not start another
    return lines


def _parse(text: str) -> tuple[list[dict], list[dict]]:
    """Return (blocks, headings). Each heading is {level, title, start_line}, with 1-based lines."""
    lines = _split_lines(text)
    blocks: list[dict] = []
    headings: list[dict] = []
    path: list[tuple[int, str]] = []

    def add(kind: str, start: int, end: int) -> None:
        blocks.append(
            {
                "id": f"b{len(blocks) + 1}",
                "kind": kind,
                "heading_path": [title for _, title in path],
                "start_line": start + 1,
                "end_line": end + 1,
                "text": "\n".join(lines[start : end + 1]),
            }
        )

    def heading(level: int, title: str, start: int) -> None:
        headings.append({"level": level, "title": title, "start_line": start + 1})
        while path and path[-1][0] >= level:
            path.pop()
        path.append((level, title))

    i = _front_matter_end(lines)
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        indent = _indent(line)
        end = i
        if indent < 4 and _fence_open(line):
            end = _fence_end(lines, i)
            add("code", i, end)
        elif _is_comment_start(line):
            while end < len(lines) - 1 and "-->" not in lines[end]:
                end += 1
            add("comment", i, end)
        elif match := _ATX_HEADING.match(line):
            heading(len(match.group(1)), _ATX_CLOSING.sub("", match.group(2) or "").strip(), i)
        elif _THEMATIC.match(line):
            pass
        elif indent < 4 and _LIST_MARKER.match(line.lstrip()):
            end = _list_item_end(lines, i)
            add("list_item", i, end)
        elif _QUOTE.match(line):
            while end + 1 < len(lines) and _QUOTE.match(lines[end + 1]):
                end += 1
            add("quote", i, end)
        elif _is_table_start(lines, i):
            add("table_header", i, i + 1)
            end = i + 1
            while end + 1 < len(lines) and lines[end + 1].strip() and "|" in lines[end + 1]:
                if _is_block_start(lines[end + 1]):
                    break
                end += 1
                add("table_row", end, end)
        else:
            end, level = _paragraph_end(lines, i)
            if level:
                heading(level, " ".join(part.strip() for part in lines[i:end]), i)
            else:
                add("paragraph", i, end)
        i = end + 1
    return blocks, headings


def split_blocks(text: str) -> list[dict]:
    """Cut markdown into blocks. Headings are context, not blocks: each block records its heading path."""
    return _parse(text)[0]


def find_guidelines(text: str) -> dict | None:
    """The first section whose heading contains "AGENTS.md guidelines": {start_line, end_line, text}."""
    headings = _parse(text)[1]
    lines = _split_lines(text)
    for index, found in enumerate(headings):
        if "agents.md guidelines" not in found["title"].lower():
            continue
        end = len(lines)
        for later in headings[index + 1 :]:
            if later["level"] <= found["level"]:
                end = later["start_line"] - 1
                break
        start = found["start_line"]
        return {"start_line": start, "end_line": end, "text": "\n".join(lines[start - 1 : end])}
    return None


def _git(root: Path, *args: str, literal: bool = True, check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(
        # Literal pathspecs: a file named `what[1].md` must never match `what1.md`.
        # The `-c` settings override user config that would change the output.
        [
            "git",
            *(["--literal-pathspecs"] if literal else []),
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


def _is_inside(root: Path, path: Path) -> bool:
    """Whether `path` is a file whose symlinks, if any, resolve to somewhere inside the repo."""
    try:
        resolved = path.resolve()
        resolved.relative_to(root)
        return resolved.is_file()
    except (OSError, ValueError):
        return False


def discover(root: Path) -> list[str]:
    """Repo-relative paths of every AGENTS.md (any case) that git knows and that exists, sorted.

    Asking git skips `.git` and ignored paths for free.
    """
    listing = _git(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard").stdout
    found = {
        path
        for path in listing.split("\0")
        if path and posixpath.basename(path).lower() == "agents.md" and _is_inside(root, root / path)
    }
    return sorted(found)


def resolve_scope(root: Path, scope: list[str]) -> list[str]:
    """Repo-relative POSIX directories for the `--scope` values; "." (the default) is the whole repo."""
    if not scope:
        return ["."]
    resolved: list[str] = []
    for arg in scope:
        try:
            target = (root / arg).resolve()
            rel = target.relative_to(root)
            is_dir = target.is_dir()
        except (OSError, ValueError):
            is_dir = False
        if not is_dir:
            raise CliError(f"scope is not a directory in the repo: {arg}")
        entry = rel.as_posix()  # "." for the root itself
        if entry not in resolved:
            resolved.append(entry)
    return resolved


def _dir_of(path: str) -> str:
    return posixpath.dirname(path) or "."


def _within(directory: str, scope: str) -> bool:
    """Whether `directory` is the scope directory or below it."""
    return scope == "." or directory == scope or directory.startswith(scope + "/")


def _is_ancestor(directory: str, scope: str) -> bool:
    """Whether `directory` is the root or a parent of the scope directory."""
    return directory == "." or scope == directory or scope.startswith(directory + "/")


def _role(path: str, scopes: list[str]) -> str | None:
    directory = _dir_of(path)
    if any(_within(directory, scope) for scope in scopes):
        return "full"
    if any(_is_ancestor(directory, scope) for scope in scopes):
        return "ancestor"
    return None


def _read_bytes(root: Path, path: str) -> bytes:
    try:
        return (root / path).read_bytes()
    except OSError as exc:
        raise CliError(f"cannot read {path}: {exc.strerror or exc}") from exc


def _decode(raw: bytes) -> str:
    return raw.decode("utf-8", errors="replace")


def _git_state(root: Path, path: str) -> tuple[bool, bool]:
    """(tracked, dirty) for one file. Untracked files are not dirty: only non-`??` status entries count."""
    tracked = _git(root, "ls-files", "--error-unmatch", "--", path, check=False).returncode == 0
    status = _git(root, "status", "--porcelain=v1", "-z", "--", path).stdout
    dirty = any(entry[:2] != "??" for entry in status.split("\0") if len(entry) > 3)
    return tracked, dirty


def _file_entry(root: Path, path: str, role: str) -> dict:
    raw = _read_bytes(root, path)
    tracked, dirty = _git_state(root, path)
    return {
        "path": path,
        "role": role,
        "tracked": tracked,
        "dirty": dirty,
        "bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "blocks": split_blocks(_decode(raw)),
    }


def _guidelines(root: Path, all_paths: list[str]) -> dict | None:
    for path in all_paths:
        if _dir_of(path) == ".":  # sorted, so a root AGENTS.md wins over agents.md
            found = find_guidelines(_decode(_read_bytes(root, path)))
            return {"path": path, **found} if found else None
    return None


def _rulings(root: Path) -> dict:
    target = root / RULINGS_PATH
    exists = target.is_file()
    # `check-ignore` rejects --literal-pathspecs, so it runs without it; the path is a fixed literal anyway.
    result = _git(root, "check-ignore", "-q", RULINGS_PATH, literal=False, check=False)
    if result.returncode not in (0, 1):
        raise CliError((result.stderr.strip().splitlines() or ["git check-ignore failed"])[0])
    ignored = result.returncode == 0
    text = _decode(_read_bytes(root, RULINGS_PATH)) if exists else None
    return {"path": RULINGS_PATH, "exists": exists, "ignored": ignored, "text": text}


def cmd_inventory(args: argparse.Namespace) -> dict:
    root = repo_root(Path(args.repo))
    scopes = resolve_scope(root, args.scope)
    base = detect_base(root, args.base)
    all_paths = discover(root)
    files = []
    for path in all_paths:
        role = _role(path, scopes)
        if role is not None:
            files.append(_file_entry(root, path, role))
    full = sum(1 for f in files if f["role"] == "full")
    return {
        "repo": str(root),
        "scope": scopes,
        "files": files,
        "agents_md_dirs": sorted({_dir_of(path) for path in all_paths}),
        "guidelines": _guidelines(root, all_paths),
        "rulings": _rulings(root),
        "base": base,
        "base_branch": base_branch(root, base),
        "current_branch": current_branch(root),
        "totals": {
            "files": len(files),
            "full": full,
            "ancestor": len(files) - full,
            "blocks": sum(len(f["blocks"]) for f in files),
            "agents": len(files),
        },
    }


def cmd_blocks(args: argparse.Namespace) -> dict:
    root = repo_root(Path(args.repo))
    # Report the requested path, normalised but with its last component unresolved, so a symlinked
    # AGENTS.md keeps its own name, as `inventory` lists it. A link that leads outside the repo is refused.
    try:
        rel = Path(os.path.normpath(root / args.path)).relative_to(root).as_posix()
        is_file = _is_inside(root, root / rel)
    except ValueError:
        is_file = False
    if not is_file:
        raise CliError(f"no such file in the repo: {args.path}")
    raw = _read_bytes(root, rel)
    return {"path": rel, "sha256": hashlib.sha256(raw).hexdigest(), "blocks": split_blocks(_decode(raw))}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agents_md_inventory.py", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    inv = sub.add_parser("inventory", help="list AGENTS.md files with their blocks and facts")
    inv.add_argument("--repo", default=".")
    inv.add_argument("--scope", action="append", default=[], help="a directory to review in full; repeatable")
    inv.add_argument("--base", default=None)
    inv.set_defaults(func=cmd_inventory)

    blocks = sub.add_parser("blocks", help="split one file into blocks and hash it")
    blocks.add_argument("--repo", default=".")
    blocks.add_argument("path", help="repo-relative path of the file")
    blocks.set_defaults(func=cmd_blocks)
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
