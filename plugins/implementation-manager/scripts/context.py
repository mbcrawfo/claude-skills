"""The manager session's context size, read from its transcript, and when to ask the owner to compact.

The transcript is `<CLAUDE_CONFIG_DIR or ~/.claude>/projects/*/<CLAUDE_CODE_SESSION_ID>.jsonl`. The context size is
the input side of the last assistant message's usage: input, cache read and cache creation tokens.
"""

from __future__ import annotations

from collections.abc import Mapping
import json
from pathlib import Path

COMPACT_AT = 300_000
COMPACT_EVERY = 150_000
TAIL_BYTES = 4 * 1024 * 1024
_USAGE_KEYS = ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")


def transcript_path(env: Mapping[str, str]) -> Path | None:
    """The session's transcript file, or None when the session id is unset or no file matches."""
    session = env.get("CLAUDE_CODE_SESSION_ID")
    if not session:
        return None
    config = env.get("CLAUDE_CONFIG_DIR")
    root = Path(config) if config else Path.home() / ".claude"
    matches = sorted((root / "projects").glob(f"*/{session}.jsonl"))
    return matches[0] if matches else None


def _tail_lines(path: Path) -> list[bytes]:
    with open(path, "rb") as f:
        size = f.seek(0, 2)
        start = max(0, size - TAIL_BYTES)
        f.seek(start)
        lines = f.read().split(b"\n")
    return lines[1:] if start else lines  # the first line may be cut


def current_tokens(env: Mapping[str, str]) -> int | None:
    """The context size in tokens from the last assistant line with usage, or None if there is none."""
    path = transcript_path(env)
    if path is None:
        return None
    try:
        lines = _tail_lines(path)
    except OSError:
        return None
    for raw in reversed(lines):
        try:
            entry = json.loads(raw)
            if entry["type"] != "assistant":
                continue
            usage = entry["message"]["usage"]
            total = sum(int(usage.get(key) or 0) for key in _USAGE_KEYS)
        except (ValueError, KeyError, TypeError, AttributeError):
            continue
        if total:  # a line with no usage counted (a synthetic message) says nothing about the context
            return total
    return None


def compact_due(tokens: int, asked: int | None, quiet: bool) -> bool:
    """Whether to ask the owner to compact now: quiet, past COMPACT_AT, and COMPACT_EVERY past the last ask."""
    return quiet and tokens >= COMPACT_AT and (asked is None or tokens >= asked + COMPACT_EVERY)
