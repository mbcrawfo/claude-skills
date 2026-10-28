#!/usr/bin/env python3
"""Facts about a repo's AGENTS.md files, for the maintenance plugin's `agents-md-cleanup` skill.

Every command prints one JSON object on stdout. On failure it exits 1 and prints one
`error: ...` line on stderr. The script only reads: it never writes, deletes or edits anything.

Commands:
  inventory  every AGENTS.md in scope (and the ancestors of a partial run), split into blocks,
             with git state, the repo's uncommitted work, the root file's guidelines section and any
             saved rulings; paths that are one file through symlinks merge into one entry, with the
             file's `resolved` path
  blocks     one file's blocks and content hash, to check it before an edit is applied
  check-verdicts
             validate a reviewer's verdicts for one file against that file's blocks; a deletion that may
             drop a project choice comes back as an `ask`

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
from typing import NoReturn

RULINGS_PATH = ".claude/agents-md-cleanup.md"
UNCOMMITTED_SHOWN = 10  # paths listed in `uncommitted`; `count` has them all

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
_INLINE_MARKUP = re.compile(r"[`*_~\\]")
_TABLE_DELIMITER = re.compile(r"^[ \t]*\|?[ \t]*:?-+:?[ \t]*(?:\|[ \t]*:?-+:?[ \t]*)*\|?[ \t]*$")


class CliError(Exception):
    pass


class _Parser(argparse.ArgumentParser):
    """Reports a usage error as a CliError, so it exits 1 with one `error: ...` line like any other failure."""

    def error(self, message: str) -> NoReturn:
        raise CliError(" ".join(message.split()))


def _indent(line: str) -> int:
    expanded = line.expandtabs(4)
    return len(expanded) - len(expanded.lstrip())


def _fence_open(line: str, offset: int = 0) -> tuple[str, int] | None:
    """(fence char, length) when `line` opens a fence, its indent counted from column `offset`."""
    if offset:
        line = line.expandtabs(4)[min(offset, _indent(line)) :]
    match = _FENCE.match(line)
    if not match:
        return None
    fence, info = match.groups()
    if fence[0] == "`" and "`" in info:
        return None  # inline code span, not a fence
    return fence[0], len(fence)


def _fence_end(lines: list[str], start: int, offset: int = 0) -> int:
    """Index of the line that closes the fence opened at `start`; the last line if it never closes."""
    char, length = _fence_open(lines[start], offset)  # type: ignore[misc]
    for j in range(start + 1, len(lines)):
        stripped = lines[j].strip()
        if _indent(lines[j]) < offset + 4 and stripped.startswith(char * length) and not stripped.strip(char):
            return j
    return len(lines) - 1


def _is_comment_start(line: str) -> bool:
    return _indent(line) < 4 and line.lstrip().startswith("<!--")


def _comment_end(lines: list[str], start: int) -> int:
    """Index of the line holding the `-->` that closes the comment opened at `start`.

    A comment with no `-->` before the file ends or the next comment opens is its opening line alone, so it
    can't hide the statements after it.
    """
    if "-->" in lines[start]:
        return start
    for j in range(start + 1, len(lines)):
        if _is_comment_start(lines[j]):
            break
        if "-->" in lines[j]:
            return j
    return start


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


def _content_indent(line: str) -> int:
    """The column where a list item's text starts: after its marker and the 1 to 4 spaces that follow it."""
    expanded = line.expandtabs(4)
    stripped = expanded.lstrip()
    marker = _LIST_MARKER.match(stripped).group(0)  # type: ignore[union-attr]
    width = len(marker.rstrip())
    spaces = len(marker) - width
    return len(expanded) - len(stripped) + width + (spaces if 1 <= spaces <= 4 else 1)


def _list_item_end(lines: list[str], start: int) -> int:
    """Index of the last line of the list item that starts at `start`."""
    base = _indent(lines[start])
    # (indent, content indent) of this item and each nested item: a fence belongs to the deepest one it sits in.
    items = [(base, _content_indent(lines[start]))]
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
        if _LIST_MARKER.match(line.lstrip()):
            if indent <= base:
                break
            items.append((indent, _content_indent(line)))
        else:
            content = next((c for i, c in reversed(items) if i < indent), items[0][1])
            if _fence_open(line, content):
                j = _fence_end(lines, j, content)
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
            end = _comment_end(lines, i)
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
    """The first section whose heading contains "AGENTS.md guidelines": {start_line, end_line, text}.

    Inline markup is ignored, so `` ## `AGENTS.md` guidelines `` matches.
    """
    headings = _parse(text)[1]
    lines = _split_lines(text)
    for index, found in enumerate(headings):
        if "agents.md guidelines" not in " ".join(_INLINE_MARKUP.sub("", found["title"]).lower().split()):
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
    except (OSError, RuntimeError, ValueError):  # RuntimeError: a symlink loop, on Python 3.9-3.12
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
        except (OSError, RuntimeError, ValueError):  # RuntimeError: a symlink loop, on Python 3.9-3.12
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


def _on_disk_spelling(root: Path, rel: str) -> str:
    """`rel` with each part spelled as it is on disk. Only a case-insensitive filesystem makes them differ."""
    current = root
    spelled = []
    for part in rel.split("/"):
        try:
            names = os.listdir(current)
        except OSError:
            names = []
        if part not in names:  # the path exists, so a name that differs only in case is this part
            part = next((name for name in names if name.lower() == part.lower()), part)
        spelled.append(part)
        current = current / part
    return "/".join(spelled)


def _resolved(root: Path, path: str) -> str:
    """The repo-relative path of the file `path` is, once symlinks are followed: where its edits go."""
    return _on_disk_spelling(root, (root / path).resolve().relative_to(root).as_posix())


def _status(root: Path, *paths: str, untracked: str = "all") -> list[tuple[str, str]]:
    """(XY code, path) per `git status` entry, for the whole repo or the given paths.

    Untracked files are listed whatever the user's `status.showUntrackedFiles` says.
    """
    out = _git(root, "status", "--porcelain=v1", "-z", f"--untracked-files={untracked}", "--", *paths).stdout
    fields = out.split("\0")
    entries = []
    i = 0
    while i < len(fields):
        field = fields[i]
        if len(field) > 3:
            entries.append((field[:2], field[3:]))
            if field[0] in "RC" or field[1] in "RC":
                i += 1  # a rename or copy, staged or in the work tree, is followed by its source path
        i += 1
    return entries


def _git_state(root: Path, resolved: str, paths: list[str]) -> tuple[bool, bool]:
    """(tracked, dirty) for one file and the links to it.

    `tracked` is the resolved file's. `dirty` is true when the resolved file has any change, untracked included,
    since committing an edit to an untracked file adds all of it. A link counts when it changed (a retargeted link),
    but not when it is merely untracked: the commit stages only the resolved file.
    """
    tracked = _git(root, "ls-files", "--error-unmatch", "--", resolved, check=False).returncode == 0
    links = [path for path in paths if path != resolved]
    dirty = bool(_status(root, resolved)) or any(code != "??" for code, _ in (_status(root, *links) if links else []))
    return tracked, dirty


def _in_head(root: Path, resolved: str) -> bool:
    """Whether HEAD holds the file. One that is staged or intent-to-add is tracked, but committing it adds all of it."""
    return _git(root, "cat-file", "-e", f"HEAD:{resolved}", check=False).returncode == 0


def _uncommitted(root: Path) -> dict:
    """The repo's uncommitted work: staged or unstaged changes, and untracked files that aren't ignored."""
    entries = _status(root, untracked="normal")  # an untracked directory is one entry
    return {"count": len(entries), "paths": [path for _, path in entries[:UNCOMMITTED_SHOWN]]}


def _file_entry(root: Path, path: str, resolved: str, aliases: list[str], role: str) -> dict:
    raw = _read_bytes(root, path)
    tracked, dirty = _git_state(root, resolved, [path, *aliases])
    return {
        "path": path,
        "resolved": resolved,
        "aliases": aliases,
        "role": role,
        "tracked": tracked,
        "in_head": _in_head(root, resolved),
        "dirty": dirty,
        "bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "blocks": split_blocks(_decode(raw)),
    }


def _entries(root: Path, all_paths: list[str], scopes: list[str]) -> list[dict]:
    """One entry per file: paths that resolve to the same file merge, so it is reviewed and edited once.

    The entry's `path` is the shallowest of its paths in scope, since agents load the file at every path and the
    shallowest covers the most; the others, in scope or not, are its `aliases`. It is `full` if any path is.
    """
    groups: dict[str, list[str]] = {}
    for path in all_paths:
        groups.setdefault(_resolved(root, path), []).append(path)
    files = []
    for resolved, paths in groups.items():
        roles = {path: _role(path, scopes) for path in paths}
        in_scope = [path for path in paths if roles[path] is not None]
        if not in_scope:
            continue
        lead = min(in_scope, key=lambda path: (path.count("/"), path))
        role = "full" if "full" in roles.values() else "ancestor"
        files.append(_file_entry(root, lead, resolved, [path for path in paths if path != lead], role))
    return sorted(files, key=lambda entry: entry["path"])


def _guidelines(root: Path, all_paths: list[str]) -> dict | None:
    for path in all_paths:
        if _dir_of(path) == ".":  # sorted, so a root AGENTS.md wins over agents.md
            found = find_guidelines(_decode(_read_bytes(root, path)))
            return {"path": path, **found} if found else None
    return None


def _rulings(root: Path) -> dict:
    if (root / RULINGS_PATH).parent.is_symlink():
        raise CliError(".claude is a symlink; git can't track the rulings file through it")
    # Like every other file the script reads, the rulings count only when they resolve to a file inside the repo.
    exists = _is_inside(root, root / RULINGS_PATH)
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
    files = _entries(root, all_paths, scopes)
    full = sum(1 for f in files if f["role"] == "full")
    uncommitted = _uncommitted(root)
    return {
        "repo": str(root),
        "scope": scopes,
        "worktree_clean": uncommitted["count"] == 0,
        "uncommitted": uncommitted,
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


def _repo_file(root: Path, arg: str) -> tuple[str, bytes]:
    """Normalise a user-given path to repo-relative POSIX and read it; refuse anything but a file in the repo."""
    # Report the requested path, normalised but with its last component unresolved, so a symlinked
    # AGENTS.md keeps its own name, as `inventory` lists it. A link that leads outside the repo is refused.
    try:
        rel = Path(os.path.normpath(root / arg)).relative_to(root).as_posix()
        is_file = _is_inside(root, root / rel)
    except ValueError:
        is_file = False
    if not is_file:
        raise CliError(f"no such file in the repo: {arg}")
    return rel, _read_bytes(root, rel)


def cmd_blocks(args: argparse.Namespace) -> dict:
    root = repo_root(Path(args.repo))
    rel, raw = _repo_file(root, args.path)
    return {
        "path": rel,
        "resolved": _resolved(root, rel),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "blocks": split_blocks(_decode(raw)),
    }


ACTIONS = ("keep", "fix", "trim", "delete", "merge", "move", "ask")
CRITERIA = ("accurate", "valuable", "brief", "level")
TARGET_KINDS = ("agents_md", "comment")
# The fields a verdict may hold: these on every action, plus those its action takes. Anything else is rejected.
ALWAYS_KEYS = ("blocks", "action", "criterion", "evidence", "ruling")
ACTION_KEYS = {
    "keep": (),
    "fix": ("new_text", "rename_only"),
    "trim": ("new_text",),
    "delete": (),
    "merge": ("new_text",),
    "move": ("new_text", "target", "target_kind", "target_heading"),
    "ask": ("question", "recommendation", "proposed"),
}
TOP_LEVEL_KEYS = ("file", "verdicts")
# An ask's `proposed` verdict: what it may be, and what it takes from the ask unless it gives its own.
PROPOSED_ACTIONS = tuple(action for action in ACTIONS if action != "ask")
PROPOSED_INHERITS = ("blocks", "criterion", "evidence")
_JSON_FENCE = re.compile(r"```json\s*\n(.*?)\n```", re.S)
# What marks a statement as a possible project choice: a tool, command or path (an inline code span), or an
# alternative weighed against it.
_CHOICE = re.compile(r"`[^`]+`|\b(?:rather than|instead of|always|never|not|only|prefer|avoid)\b", re.I)
_CITATION = re.compile(r"`?([^\s:`'\"()]+)`?:\d+")
CHOICE_QUESTION = "Delete this statement? It names a tool, command or alternative, so it may record a project choice."


def _text_field(verdict: dict, name: str) -> str:
    value = verdict.get(name)
    if not isinstance(value, str) or not value.strip():
        raise CliError(f"{name} must be a non-empty string")
    return value


def _can_hold_file(root: Path, target: str) -> bool:
    """Whether `target` could be written as a regular file that stays inside the repo.

    The path need not exist yet, but its nearest existing ancestor must be a directory (the target itself,
    when it exists, a regular file) once symlinks are resolved. A symlink that leads nowhere, dangling or
    in a loop, refuses the target.
    """
    path = root / target
    try:
        path.resolve().relative_to(root)  # RuntimeError: a symlink loop, on Python 3.9-3.12
        probe = path
        # Walk the unresolved path: from Python 3.13, resolve() returns a looping path instead of raising.
        while not probe.exists():
            if probe.is_symlink():
                return False
            probe = probe.parent
        return probe.is_file() if probe == path else probe.is_dir()
    except (OSError, RuntimeError, ValueError):
        return False


def _same_file(one: Path, other: Path) -> bool:
    """Whether both paths exist and are one file, through symlinks or a case-insensitive filesystem."""
    try:
        return one.samefile(other)
    except OSError:
        return False


def _is_root_file(root: Path, file: str) -> bool:
    """Whether `file` is the root AGENTS.md (any case), by its own path or as a link to it or from it."""
    try:
        names = os.listdir(root)
    except OSError:
        return False
    return any(name.lower() == "agents.md" and _same_file(root / name, root / file) for name in names)


def _in_git_dir(root: Path, target: str) -> bool:
    """Whether `target` has a `.git` part in any case, as written or once its symlinks are resolved.

    Case is ignored, since on a case-insensitive filesystem `.GIT` is `.git`. This mirrors the skill's write guard.
    """
    parts = target.split("/")
    try:
        parts += (root / target).resolve().relative_to(root).parts
    except (OSError, RuntimeError, ValueError):
        pass  # outside the repo or a symlink loop: the containment checks refuse it
    return ".git" in (part.lower() for part in parts)


def _check_move(verdict: dict, file: str, root: Path) -> None:
    target = _text_field(verdict, "target")
    kind = verdict.get("target_kind")
    if kind not in TARGET_KINDS:
        raise CliError(f"target_kind must be one of {', '.join(TARGET_KINDS)}")
    if target.startswith("/") or "\\" in target or posixpath.normpath(target) != target or ".." in target.split("/"):
        raise CliError(f"target must be a repo-relative path without '..': {target}")
    if "'" in target or any(ord(ch) < 32 or ord(ch) == 127 for ch in target):
        # The skill single-quotes targets in shell commands; a quote or control character could break out.
        raise CliError(f"target must not contain a single quote or a control character: {target!r}")
    if target == file or _same_file(root / target, root / file):
        raise CliError(f"target must not be the file itself: {target}")
    if _in_git_dir(root, target):
        raise CliError(f"target {target} is inside .git")
    if kind == "agents_md":
        if posixpath.basename(target).lower() != "agents.md":
            raise CliError(f"target {target} is not an AGENTS.md file")
        if not _can_hold_file(root, target):
            raise CliError(f"target {target} is not a file path inside the repo")
    if kind == "comment" and not _is_inside(root, root / target):
        raise CliError(f"target {target} is not a file in the repo")
    _check_stageable(root, target)
    if "target_heading" in verdict and not isinstance(verdict["target_heading"], str):
        raise CliError("target_heading must be a string")


def _check_stageable(root: Path, target: str) -> None:
    """Refuse a target the skill's commit can't stage: ignored, in a submodule or nested repo, or past a symlink.

    `target` is already known to be a normalised path inside the repo.
    """
    # `check-ignore` rejects --literal-pathspecs, so a `./` prefix keeps a leading `:` from reading as pathspec
    # magic; it never globs. It answers for a path that doesn't exist yet, and fails on one in a submodule or
    # past a symlinked directory, which `git add` refuses too.
    result = _git(root, "check-ignore", "-q", "--", f"./{target}", literal=False, check=False)
    if result.returncode == 0:
        raise CliError(f"target {target} is ignored by git, so it can't be committed")
    if result.returncode != 1:
        first = (result.stderr.strip().splitlines() or ["git check-ignore failed"])[0]
        raise CliError(f"target {target} can't be staged: {first}")
    # A nested repo that isn't a submodule passes check-ignore, and `git add` skips it without an error. Any
    # directory below the root with a `.git` (a repo's directory, a submodule's or linked worktree's file) is one;
    # a dangling `.git` symlink is not, and git stages paths there.
    for parent in Path(target).parents:
        if parent != Path(".") and os.path.exists(root / parent / ".git"):
            raise CliError(f"target {target} is inside another git repository: {parent.as_posix()}")


def _check_verdict(verdict: dict, file: str, root: Path) -> None:
    action = verdict.get("action")
    if action not in ACTIONS:
        raise CliError(f"unknown action {action!r}; expected one of {', '.join(ACTIONS)}")
    extra = sorted(set(verdict) - set(ALWAYS_KEYS) - set(ACTION_KEYS[action]))
    if extra:
        raise CliError(f"{action} does not take {', '.join(extra)}")
    if action != "keep" or "criterion" in verdict:
        if verdict.get("criterion") not in CRITERIA:
            raise CliError(f"criterion must be one of {', '.join(CRITERIA)}")
    evidence = verdict.get("evidence")
    if action != "keep" or "evidence" in verdict:
        if not (isinstance(evidence, list) and evidence and all(isinstance(e, str) and e.strip() for e in evidence)):
            raise CliError("evidence must be a non-empty list of non-empty strings")
    if action in ("fix", "trim", "merge") or (action == "move" and "new_text" in verdict):
        _text_field(verdict, "new_text")
    if "rename_only" in verdict and not isinstance(verdict["rename_only"], bool):
        raise CliError("rename_only must be a boolean")
    if action == "merge" and len(verdict["blocks"]) < 2:
        raise CliError("merge needs at least 2 blocks")
    if action == "move":
        _check_move(verdict, file, root)
    if action == "ask":
        _text_field(verdict, "question")
        _text_field(verdict, "recommendation")
        if "proposed" in verdict:
            if not isinstance(verdict["proposed"], dict):
                raise CliError("proposed must be an object")
            try:
                _check_proposed(verdict, file, root)
            except CliError as exc:
                raise CliError(f"proposed: {exc}") from exc
    if "ruling" in verdict and not isinstance(verdict["ruling"], str):
        raise CliError("ruling must be a string")


def _proposed(ask: dict) -> dict:
    """The verdict an ask's `proposed` stands for: it inherits the ask's blocks, criterion and evidence."""
    return {**{key: ask[key] for key in PROPOSED_INHERITS if key in ask}, **ask["proposed"]}


def _check_proposed(ask: dict, file: str, root: Path) -> None:
    """Check an ask's `proposed` like a top-level verdict, since answering as recommended applies it as it is."""
    proposed = ask["proposed"]
    if proposed.get("action") not in PROPOSED_ACTIONS:
        raise CliError(f"action must be one of {', '.join(PROPOSED_ACTIONS)}")
    if "blocks" in proposed and proposed["blocks"] != ask["blocks"]:
        raise CliError("blocks must be the ask's blocks")
    _check_verdict(_proposed(ask), file, root)


def _cites_other_file(evidence: list[str], file: str, root: Path) -> bool:
    """Whether the evidence cites a `path:line` in an existing repo file other than `file`."""
    return any(
        _is_inside(root, root / path) and not _same_file(root / path, root / file)
        for entry in evidence
        for path in _CITATION.findall(entry)
    )


def _guard_choice(verdict: dict, blocks: list[dict], file: str, root: Path) -> dict:
    """Turn a `valuable` or `brief` delete of a statement that may be a project choice into an `ask`.

    A project choice is never obvious, so only a cited copy in another file lets such a deletion rest on the
    reviewer's word.
    """
    if verdict["action"] != "delete" or verdict["criterion"] not in ("valuable", "brief"):
        return verdict
    if not any(_CHOICE.search(block["text"]) for block in blocks) or _cites_other_file(verdict["evidence"], file, root):
        return verdict
    return {
        **verdict,
        "action": "ask",
        "question": CHOICE_QUESTION,
        "recommendation": "Delete it, as the reviewer proposes, unless it records a choice the project made.",
        "proposed": {"action": "delete"},
    }


def _check_ancestor(verdict: dict, scope: list[str], allowed: tuple[str, ...]) -> None:
    """An ancestor file's blocks change only by moving down into the scope."""
    if verdict["action"] not in allowed:
        raise CliError(f"action {verdict['action']} is not allowed on an ancestor file; use {' or '.join(allowed)}")
    if verdict["action"] == "move" and not any(_within(_dir_of(verdict["target"]), s) for s in scope):
        raise CliError(f"target {verdict['target']} is outside the scope")


def validate_verdicts(
    blocks: list[dict],
    data: dict,
    role: str,
    scope: list[str],
    file: str,
    root: Path,
    guidelines: dict | None = None,
) -> dict:
    """Check a reviewer's verdicts against `file`'s blocks; return the report, or raise CliError.

    The report's verdicts are the reviewer's, except that a deletion that may drop a project choice is an `ask`.

    `guidelines` is the root file's guidelines section ({start_line, end_line}): its blocks take only keep or ask.
    """
    got = data.get("file")
    if got != file:
        raise CliError(f"verdicts are for {got if isinstance(got, str) else 'no file'}, expected {file}")
    extra = sorted(set(data) - set(TOP_LEVEL_KEYS))
    if extra:
        raise CliError(f"unknown top-level key {', '.join(extra)}; expected only {', '.join(TOP_LEVEL_KEYS)}")
    verdicts = data.get("verdicts")
    if not isinstance(verdicts, list):
        raise CliError("verdicts must be a list")
    by_id = {block["id"]: block for block in blocks}
    seen: set[str] = set()
    for number, verdict in enumerate(verdicts, start=1):
        try:
            if not isinstance(verdict, dict):
                raise CliError("must be an object")
            listed = verdict.get("blocks")
            if not (isinstance(listed, list) and listed and all(isinstance(b, str) for b in listed)):
                raise CliError("blocks must be a non-empty list of block ids")
            for block in listed:
                if block not in by_id:
                    raise CliError(f"unknown block {block}")
                if block in seen:
                    raise CliError(f"block {block} is in more than one verdict")
                seen.add(block)
            _check_verdict(verdict, file, root)
            if guidelines and verdict["action"] not in ("keep", "ask"):
                for block in listed:
                    found = by_id[block]
                    if found["start_line"] <= guidelines["end_line"] and found["end_line"] >= guidelines["start_line"]:
                        raise CliError(
                            f"block {block} is in the guidelines section, whose blocks take only keep or ask"
                        )
            if role == "ancestor":
                _check_ancestor(verdict, scope, ("move", "ask"))
                if "proposed" in verdict:
                    try:
                        _check_ancestor(_proposed(verdict), scope, ("move",))
                    except CliError as exc:
                        raise CliError(f"proposed: {exc}") from exc
        except CliError as exc:
            raise CliError(f"verdict {number}: {exc}") from exc
    if role == "full":
        for block in blocks:
            if block["id"] not in seen:
                raise CliError(f"block {block['id']} not covered")
    verdicts = [_guard_choice(verdict, [by_id[b] for b in verdict["blocks"]], file, root) for verdict in verdicts]
    counts: dict[str, int] = {}
    for verdict in verdicts:
        counts[verdict["action"]] = counts.get(verdict["action"], 0) + 1
    return {"file": file, "role": role, "verdicts": verdicts, "counts": counts}


def cmd_check_verdicts(args: argparse.Namespace) -> dict:
    root = repo_root(Path(args.repo))
    scope = resolve_scope(root, args.scope) if args.scope else []
    if args.role == "ancestor" and not scope:
        raise CliError("--scope is required for the ancestor role")
    file, raw = _repo_file(root, args.file)
    try:
        message = _decode(Path(args.input).read_bytes())
    except OSError as exc:
        raise CliError(f"cannot read {args.input}: {exc.strerror or exc}") from exc
    fences = _JSON_FENCE.findall(message)
    if not fences:
        raise CliError("no json block")
    try:
        data = json.loads(fences[-1])
    except ValueError as exc:
        raise CliError(f"invalid json: {exc}") from exc
    if not isinstance(data, dict):
        raise CliError("invalid json: the top level must be an object")
    text = _decode(raw)
    # The root AGENTS.md holds the guidelines section; a nested file's matching heading is ordinary.
    guidelines = find_guidelines(text) if _is_root_file(root, file) else None
    return validate_verdicts(split_blocks(text), data, args.role, scope, file, root, guidelines)


def _parser() -> argparse.ArgumentParser:
    parser = _Parser(prog="agents_md_inventory.py", description=__doc__.splitlines()[0])
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

    verdicts = sub.add_parser("check-verdicts", help="validate a reviewer's verdicts for one file")
    verdicts.add_argument("--repo", default=".")
    verdicts.add_argument("--file", required=True, help="repo-relative path of the reviewed file")
    verdicts.add_argument("--role", required=True, choices=["full", "ancestor"])
    verdicts.add_argument("--scope", action="append", default=[], help="a reviewed directory; required for ancestor")
    verdicts.add_argument("--input", required=True, help="a file holding the reviewer's whole message")
    verdicts.set_defaults(func=cmd_check_verdicts)
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
        result = args.func(args)
    except CliError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
