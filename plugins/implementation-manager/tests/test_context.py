"""Tests for context.py: the manager session's context size from its transcript."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import time
import unittest

import helpers  # noqa: F401  (puts the scripts dir on sys.path)

# isort: split
import context


def assistant(input_tokens, read, created):
    usage = {"input_tokens": input_tokens, "cache_read_input_tokens": read, "cache_creation_input_tokens": created}
    return json.dumps({"type": "assistant", "message": {"usage": usage}}) + "\n"


class CurrentTokensTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.config = Path(tmp.name)
        self.env = {"CLAUDE_CONFIG_DIR": str(self.config), "CLAUDE_CODE_SESSION_ID": "sid"}
        self.path = self.config / "projects" / "p" / "sid.jsonl"
        self.path.parent.mkdir(parents=True)

    def test_no_session_id_gives_none(self):
        self.assertIsNone(context.current_tokens({"CLAUDE_CONFIG_DIR": str(self.config)}))

    def test_missing_file_gives_none(self):
        self.assertIsNone(context.current_tokens(self.env))

    def test_last_assistant_usage_is_summed(self):
        user = json.dumps({"type": "user"}) + "\n"
        self.path.write_text(assistant(5, 1000, 200) + assistant(3, 400_000, 2000) + user)
        self.assertEqual(context.current_tokens(self.env), 402_003)

    def test_partial_last_line_is_skipped(self):
        self.path.write_text(assistant(5, 1000, 200) + '{"type": "assist')
        self.assertEqual(context.current_tokens(self.env), 1205)

    def test_zero_usage_line_is_skipped(self):
        self.path.write_text(assistant(5, 1000, 200) + assistant(0, 0, 0))
        self.assertEqual(context.current_tokens(self.env), 1205)

    def test_large_file_reads_tail_only(self):
        filler = json.dumps({"type": "user", "text": "x" * 1000}) + "\n"
        with open(self.path, "w") as f:
            for _ in range(5 * 1024):
                f.write(filler)
            f.write(assistant(1, 2, 3))
        start = time.monotonic()
        self.assertEqual(context.current_tokens(self.env), 6)
        self.assertLess(time.monotonic() - start, 2)


class CompactDueTests(unittest.TestCase):
    def test_compact_due(self):
        self.assertFalse(context.compact_due(299_999, None, True))
        self.assertTrue(context.compact_due(300_000, None, True))
        self.assertFalse(context.compact_due(300_000, None, False))
        self.assertFalse(context.compact_due(420_000, 300_000, True))
        self.assertTrue(context.compact_due(450_000, 300_000, True))


if __name__ == "__main__":
    unittest.main()
