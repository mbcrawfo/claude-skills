#!/usr/bin/env python3
"""Size limits for AGENTS.md files: what a rule, a word and a byte are, and the guidelines line.

Standard library only. This module must not import agents_md_inventory, which imports it.
The regexes below are copies of the ones in that script.
"""

from __future__ import annotations

import re

DEFAULT_RULE_WORDS = 80
DEFAULT_FILE_BYTES = 8192
DEFAULT_ROOT_BYTES = 16384
HEADROOM = 0.9

_ATX_HEADING = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*?))?[ \t]*$")
_THEMATIC = re.compile(r"^ {0,3}([-*_])(?:[ \t]*\1){2,}[ \t]*$")
_LIST_MARKER = re.compile(r"^(?:[-*+]|\d{1,9}[.)])(?:[ \t]+|$)")
_FENCE_OPEN = re.compile(r"^[ \t]*(`{3,}|~{3,})")
_COMMENT_SPAN = re.compile(r"<!--.*?-->")
_SETEXT_1 = re.compile(r"^ {0,3}=+[ \t]*$")
_SETEXT_2 = re.compile(r"^ {0,3}-+[ \t]*$")
_TABLE_DELIMITER = re.compile(r"^[ \t]*\|?[ \t]*:?-+:?[ \t]*(?:\|[ \t]*:?-+:?[ \t]*)*\|?[ \t]*$")
_YAML_KEY = re.compile(r"^[A-Za-z_][\w-]*:")

_LIMITS_LINE = re.compile(r"^[ \t]*(?:(?:[-*+]|\d{1,9}[.)])[ \t]+)?(size limits:.*?)[ \t]*$", re.I)
_LIMITS_NONE = re.compile(r"^size limits:\s*none\.?$", re.I)
_LIMITS_FULL = re.compile(
    r"^size limits:\s*(\d+)\s+words per rule,\s*(\d+)\s*KB per file,\s*(\d+)\s*KB for the root;"
    r"\s*(?:checked by\s+`?([^`]+?)`?|not checked by a test)\.$",
    re.I,
)


def count_words(text: str) -> int:
    """Whitespace-separated words in `text`."""
    return len(text.split())


def normalised_bytes(raw: bytes) -> int:
    """Byte length with CRLF counted as LF."""
    return len(raw.replace(b"\r\n", b"\n"))


def _skipped(line: str) -> bool:
    """Whether `line` is a heading, table row, quote or thematic break (never a rule)."""
    stripped = line.lstrip()
    return bool(_ATX_HEADING.match(line) or _THEMATIC.match(line) or stripped.startswith(("|", ">")))


def _rule(start: int, text: str, item: bool) -> dict:
    return {"line": start, "end_line": start, "text": text, "item": item}


def _front_matter_end(lines: list[str]) -> int:
    """Index of the line after a leading `---` front matter block, or 0 when there is none.

    As in the inventory, the line after the opening `---` must look like a YAML key and a closing `---` must follow,
    so a thematic break is never read as front matter.
    """
    if len(lines) > 1 and lines[0].rstrip() == "---" and _YAML_KEY.match(lines[1]):
        for j in range(1, len(lines)):
            if lines[j].rstrip() == "---":
                return j + 1
    return 0


def _is_table_start(lines: list[str], i: int) -> bool:
    """A GFM table header: a line holding `|`, then a delimiter row holding `|`, edge pipes optional."""
    return (
        i + 1 < len(lines)
        and "|" in lines[i]
        and "|" in lines[i + 1]
        and _TABLE_DELIMITER.match(lines[i + 1]) is not None
    )


def _scan_rules(text: str) -> list[dict]:
    """Every rule with its 1-based inclusive lines and its text: wrapped lines joined, list marker dropped."""
    lines = text.replace("\r\n", "\n").split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    rules: list[dict] = []
    open_rule = None
    fence = None  # (character, length) while inside a fenced block
    comment = False
    table = False  # inside a table: each following non-blank line holding `|` is a row
    for number in range(_front_matter_end(lines) + 1, len(lines) + 1):
        line = lines[number - 1]
        stripped = line.strip()
        if fence is not None:
            if re.match(rf"^[ \t]*{re.escape(fence[0])}{{{fence[1]},}}[ \t]*$", line):
                fence = None
            continue
        if comment:
            comment = "-->" not in line
            continue
        opened = _FENCE_OPEN.match(line)
        if opened:
            fence = (opened.group(1)[0], len(opened.group(1)))
            open_rule, table = None, False
            continue
        if stripped.startswith("<!--") and "-->" not in stripped[4:]:
            comment = True
            open_rule, table = None, False
            continue
        if table and "|" in line:
            continue
        table = False
        if _is_table_start(lines, number - 1):
            open_rule, table = None, True
            continue
        if open_rule is not None and not open_rule["item"] and (_SETEXT_1.match(line) or _SETEXT_2.match(line)):
            rules.remove(open_rule)  # a setext heading: the paragraph above its underline is its title
            open_rule = None
            continue
        line = _COMMENT_SPAN.sub("", line)
        stripped = line.strip()
        if not stripped or _skipped(line):
            open_rule = None
            continue
        marker = _LIST_MARKER.match(stripped)
        if marker:
            open_rule = _rule(number, stripped[marker.end() :], True)
            rules.append(open_rule)
        elif open_rule is not None:
            open_rule["end_line"] = number
            open_rule["text"] += " " + stripped
        else:
            open_rule = _rule(number, stripped, False)
            rules.append(open_rule)
    return rules


def measure_rules(text: str) -> list[dict]:
    """Every rule (list item or paragraph) with its 1-based inclusive lines and word count.

    Fenced code, headings (setext ones included), tables (header, delimiter and rows, edge pipes optional), quote
    lines, thematic breaks, HTML comments and front matter are never rules.
    """
    rules = _scan_rules(text)
    return [
        {
            "line": r["line"],
            "end_line": r["end_line"],
            "words": count_words(r["text"]),
            "first_words": " ".join(r["text"].split()[:8]),
        }
        for r in rules
    ]


def parse_limits_line(text: str) -> dict | None:
    """Parse the `Size limits:` line in a guidelines section; None when absent, ValueError when malformed.

    The line is a rule as `measure_rules` reads one, so a bullet wrapped onto several lines parses as one line.
    """
    for rule in _scan_rules(text):
        found = _LIMITS_LINE.match(rule["text"])
        if not found:
            continue
        body = found.group(1)
        if _LIMITS_NONE.match(body):
            return {"none": True}
        full = _LIMITS_FULL.match(body)
        if not full:
            raise ValueError(body)
        return {
            "rule_words": int(full.group(1)),
            "file_bytes": int(full.group(2)) * 1024,
            "root_bytes": int(full.group(3)) * 1024,
            "test": full.group(4),
        }
    return None


def format_limits_line(rule_words: int, file_bytes: int, root_bytes: int, test: str | None) -> str:
    """The guidelines line recording these limits."""
    checked = f"checked by {test}" if test else "not checked by a test"
    return (
        f"Size limits: {rule_words} words per rule, {file_bytes // 1024} KB per file, "
        f"{root_bytes // 1024} KB for the root; {checked}."
    )


def limits_in_force(parsed: dict | None, overrides: dict) -> dict:
    """Resolve the limits. Arguments always win over the guidelines line, which wins over the defaults.

    `overrides["none"]` gives source `none` with the default numbers, whatever the guidelines line says, but keeps
    the line's `test`, which still enforces the old limits until the run updates or removes it. Numeric overrides
    over a guidelines `none` give source `arguments`, the other numbers coming from the defaults.
    """
    limits = {
        "source": "default",
        "rule_words": DEFAULT_RULE_WORDS,
        "file_bytes": DEFAULT_FILE_BYTES,
        "root_bytes": DEFAULT_ROOT_BYTES,
        "test": None,
    }
    if overrides.get("none"):
        limits["source"] = "none"
        if parsed and not parsed.get("none"):
            limits["test"] = parsed["test"]
        return limits
    given = {k: overrides[k] for k in ("rule_words", "file_bytes", "root_bytes") if overrides.get(k) is not None}
    if parsed and parsed.get("none"):
        limits["source"] = "none"
    elif parsed:
        limits.update({k: parsed[k] for k in ("rule_words", "file_bytes", "root_bytes", "test")})
        limits["source"] = "guidelines"
    if given:
        limits.update(given)
        limits["source"] = "arguments"
    return limits


def over_limits(rules: list[dict], size: int, is_root: bool, limits: dict) -> dict:
    """The rules and file size that exceed `limits`, and whether the file is within the headroom margin."""
    limit = limits["root_bytes"] if is_root else limits["file_bytes"]
    if limits["source"] == "none":
        return {"rules": [], "file": False, "limit": limit, "headroom": False}
    return {
        "rules": [r for r in rules if r["words"] > limits["rule_words"]],
        "file": size > limit,
        "limit": limit,
        "headroom": size > HEADROOM * limit,
    }
