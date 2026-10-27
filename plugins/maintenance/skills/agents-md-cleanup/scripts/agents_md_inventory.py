#!/usr/bin/env python3
"""Facts about a repo's AGENTS.md files, for the maintenance plugin's `agents-md-cleanup` skill.

The script only reads: it never writes, deletes or edits anything. This module
starts with the block splitter, `split_blocks`, which cuts a markdown file into
the addressable blocks the skill's verdicts refer to.
"""

from __future__ import annotations

import re

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
    """Index of the line after a leading `---` front matter block, or 0 when there is none."""
    if lines and lines[0].rstrip() == "---":
        for j in range(1, len(lines)):
            if lines[j].rstrip() == "---":
                return j + 1
    return 0


def split_blocks(text: str) -> list[dict]:
    """Cut markdown into blocks. Headings are context, not blocks: each block records its heading path."""
    lines = text.splitlines()
    blocks: list[dict] = []
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

    def heading(level: int, title: str) -> None:
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
            heading(len(match.group(1)), _ATX_CLOSING.sub("", match.group(2) or "").strip())
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
                heading(level, " ".join(part.strip() for part in lines[i:end]))
            else:
                add("paragraph", i, end)
        i = end + 1
    return blocks
