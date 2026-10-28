import unittest

from agents_helpers import agents_md_inventory as inv
from agents_helpers import agents_md_limits as lim

PARAGRAPH = " ".join(["p"] * 90)
PIPELESS_TABLE = "Command | What it runs | When\n--- | --- | ---\n" + "".join(
    f"`make t{n}` | the unit tests for package {n} and its helpers | before every push\n" for n in range(5)
)


class RuleScannerTest(unittest.TestCase):
    def rules(self, text):
        return [(r["line"], r["words"]) for r in lim.measure_rules(text)]

    def test_wrapped_item_is_one_rule(self):
        self.assertEqual(self.rules("- one two\n  three four\n"), [(1, 4)])

    def test_nested_items_are_their_own_rules(self):
        self.assertEqual(self.rules("- a b\n  - c\n    - d e f\n"), [(1, 2), (2, 1), (3, 3)])

    def test_marker_is_not_a_word(self):
        self.assertEqual(self.rules("1. one\n10) two three\n* x\n"), [(1, 1), (2, 2), (3, 1)])

    def test_paragraph_outside_a_list_is_a_rule(self):
        self.assertEqual(self.rules("Intro line one\nline two\n\n- item\n"), [(1, 5), (4, 1)])

    def test_blank_line_ends_a_rule(self):
        self.assertEqual(self.rules("- one\n\n  two three\n"), [(1, 1), (3, 2)])

    def test_fences_headings_tables_quotes_comments_count_nothing(self):
        text = (
            "# H\n\n- one\n\n```\nnot counted\n```\n\n| a | b |\n|---|---|\n| c | d |\n\n"
            "> quoted\n\n<!-- a\ncomment -->\n\n~~~md\n- not a rule\n~~~\n"
        )
        self.assertEqual(self.rules(text), [(3, 1)])

    def test_fence_inside_an_item_counts_nothing(self):
        self.assertEqual(self.rules("- one\n\n  ```\n  x y z\n  ```\n"), [(1, 1)])

    def test_front_matter_is_skipped(self):
        self.assertEqual(self.rules("---\ntitle: x\n---\n\n- one\n"), [(5, 1)])

    def test_first_words_and_end_line(self):
        (rule,) = lim.measure_rules("- a b c d e f g h i j\n  k\n")
        self.assertEqual(rule["first_words"], "a b c d e f g h")
        self.assertEqual(rule["end_line"], 2)

    def test_crlf_text_measures_like_lf(self):
        self.assertEqual(self.rules("- one two\r\n  three\r\n"), [(1, 3)])

    def test_inline_comment_opener_in_backticks_hides_nothing(self):
        long_rule = "- " + " ".join(["w"] * 120) + "\n"
        self.assertEqual(self.rules("- Never put `<!--` in templates.\n" + long_rule), [(1, 5), (2, 120)])

    def test_complete_inline_comment_is_stripped_from_its_line(self):
        self.assertEqual(self.rules("text <!-- c --> more\n"), [(1, 2)])

    def test_tables_count_nothing_with_or_without_edge_pipes(self):
        rows = [
            ("leading pipes", "| a | b |\n|---|---|\n| c | d |\n"),
            ("no edge pipes, 5 rows", PIPELESS_TABLE),
            ("trailing pipes only", "a | b |\n--- | --- |\nc | d |\n"),
            ("aligned delimiter", "a | b\n:-- | --:\nc | d\n"),
            ("indented in a list item", "- item\n\n  a | b\n  --- | ---\n  c | d\n"),
        ]
        for name, table in rows:
            with self.subTest(name):
                self.assertEqual(
                    self.rules("- before\n\n" + table + "\n- after\n")[1:-1], [] if "item" not in name else [(3, 1)]
                )

    def test_table_ends_at_a_line_without_a_pipe(self):
        self.assertEqual(self.rules("a | b\n--- | ---\nc | d\nplain words here\n"), [(4, 3)])

    def test_fence_or_comment_ends_a_table(self):
        # As in split_blocks, a fence or comment opening on a line with a pipe ends the table; rows after it are text.
        for opener, closer in (("```x|y", "```"), ("<!-- a|b", "-->")):
            with self.subTest(opener):
                text = f"a | b\n--- | ---\n{opener}\ncode\n{closer}\nc | d e\n"
                self.assertEqual(self.rules(text), [(6, 4)])

    def test_table_start_ends_an_open_paragraph(self):
        self.assertEqual(self.rules("intro words\na | b\n--- | ---\nc | d\n"), [(1, 2)])

    def test_pipe_line_without_a_delimiter_row_is_text(self):
        self.assertEqual(self.rules("use a | b to pipe\nnext line\n"), [(1, 8)])

    def test_setext_headings_count_nothing(self):
        for underline in ("===", "---", "=", "-", "   ==="):
            with self.subTest(underline):
                self.assertEqual(self.rules(PARAGRAPH + "\nmore words\n" + underline + "\n\n- after\n"), [(5, 1)])

    def test_thematic_break_after_a_blank_line_is_not_a_heading(self):
        self.assertEqual(self.rules(PARAGRAPH + "\n\n---\n\n- after\n"), [(1, 90), (5, 1)])

    def test_underline_after_a_list_item_does_not_drop_it(self):
        self.assertEqual(self.rules("- one two\n---\n"), [(1, 2)])

    def test_thematic_break_first_is_not_front_matter(self):
        self.assertEqual(self.rules("---\n\n- one\n\n---\n\n- two three\n"), [(3, 1), (7, 2)])
        self.assertEqual(self.rules("---\ntitle: x\n\n- one\n"), [(2, 2), (4, 1)])

    def test_rules_agree_with_split_blocks(self):
        # The per-block guard measures each block's text; the inventory measures the file. They must agree. A table
        # block is never measured on its own: a lone row would read as a paragraph.
        texts = [
            "- before\n\n" + PIPELESS_TABLE + "\n- after\n",
            "- item\n\n  a | b\n  --- | ---\n  c | d\n\n- next\n",
            "- item one\n  a | b\n  --- | ---\n  c | d\n",
            PARAGRAPH + "\n---\n\n" + PARAGRAPH + "\n===\n\n- tail\n",
            "intro words\na | b\n--- | ---\nc | d\n",
            "---\n\n- one\n\n---\n\n- two\n",
            "a | b\n--- | ---\n```x|y\ncode\n```\nc | d e\n",
            "a | b\n--- | ---\n<!-- a|b\ncode\n-->\nc | d e\n",
            "- one two\n---\n",
        ]
        for text in texts:
            with self.subTest(text[:30]):
                by_block = [
                    (block["start_line"] + rule["line"] - 1, rule["words"])
                    for block in inv.split_blocks(text)
                    if block["kind"] not in inv.TABLE_KINDS
                    for rule in lim.measure_rules(block["text"])
                ]
                self.assertEqual(self.rules(text), by_block)

    def test_multi_line_comment_at_line_start_still_skips(self):
        self.assertEqual(self.rules("- one\n\n  <!-- a\nb c d -->\n\n- two three\n"), [(1, 1), (6, 2)])


class BytesTest(unittest.TestCase):
    def test_normalised_bytes_ignores_crlf(self):
        self.assertEqual(lim.normalised_bytes(b"- a\r\n- b\r\n"), lim.normalised_bytes(b"- a\n- b\n"))
        self.assertEqual(lim.normalised_bytes("- é\n".encode()), 5)


class LimitsLineTest(unittest.TestCase):
    def test_none(self):
        self.assertEqual(lim.parse_limits_line("- Push-down: aggressive.\n- Size limits: none.\n"), {"none": True})

    def test_none_without_a_period(self):
        self.assertEqual(lim.parse_limits_line("- Size limits: none\n"), {"none": True})

    def test_checked_by_a_test(self):
        got = lim.parse_limits_line(
            "13. Size limits: 80 words per rule, 8 KB per file, 16 KB for the root; "
            "checked by `tests/docs/test_agents_md.py`."
        )
        self.assertEqual(
            got, {"rule_words": 80, "file_bytes": 8192, "root_bytes": 16384, "test": "tests/docs/test_agents_md.py"}
        )

    def test_not_checked(self):
        got = lim.parse_limits_line(
            "Size limits: 100 words per rule, 10 KB per file, 20 KB for the root; not checked by a test."
        )
        self.assertEqual(got["test"], None)
        self.assertEqual((got["rule_words"], got["file_bytes"], got["root_bytes"]), (100, 10240, 20480))

    def test_absent(self):
        self.assertIsNone(lim.parse_limits_line("- Push-down: aggressive.\n"))

    def test_wrapped_line_parses(self):
        expected = {"rule_words": 80, "file_bytes": 8192, "root_bytes": 16384, "test": "tests/test_agents.py"}
        head = "Size limits: 80 words per rule, 8 KB per file, 16 KB for the root;"
        tail = "checked by tests/test_agents.py.\n"
        rows = [
            ("bullet wrapped after 'for the root;'", f"- {head}\n  {tail}"),
            ("lazy continuation", f"- {head}\n{tail}"),
            ("numbered, three lines", head.replace("rule, ", "rule,\n   ").join(["3. ", "\n   " + tail])),
            ("paragraph", f"{head}\n{tail}"),
            (
                "among other bullets",
                f"## AGENTS.md guidelines\n\n- Push-down: aggressive.\n- {head}\n  {tail}- Last.\n",
            ),
            ("backticked path", f"- {head}\n  " + tail.replace("tests/test_agents.py", "`tests/test_agents.py`")),
        ]
        for name, text in rows:
            with self.subTest(name):
                self.assertEqual(lim.parse_limits_line(text), expected)
        self.assertEqual(lim.parse_limits_line("- Size limits:\n  none.\n"), {"none": True})

    def test_next_bullet_or_heading_is_not_a_continuation(self):
        self.assertEqual(lim.parse_limits_line("- Size limits: none.\n- Push-down: aggressive.\n"), {"none": True})
        self.assertEqual(lim.parse_limits_line("- Size limits: none.\n## Next\n"), {"none": True})

    def test_malformed_raises(self):
        with self.assertRaises(ValueError) as cm:
            lim.parse_limits_line("- Size limits: 80 words, 8 KB.\n")
        self.assertEqual(str(cm.exception), "Size limits: 80 words, 8 KB.")

    def test_format_round_trips(self):
        line = lim.format_limits_line(80, 8192, 16384, "t/test_a.py")
        self.assertEqual(
            line, "Size limits: 80 words per rule, 8 KB per file, 16 KB for the root; checked by t/test_a.py."
        )
        self.assertEqual(lim.parse_limits_line(line)["test"], "t/test_a.py")
        backticked = line.replace("by t/test_a.py", "by `t/test_a.py`")
        self.assertEqual(lim.parse_limits_line(backticked)["test"], "t/test_a.py")
        self.assertEqual(lim.format_limits_line(80, 8192, 16384, None).split("; ")[1], "not checked by a test.")


class InForceTest(unittest.TestCase):
    def test_sources(self):
        self.assertEqual(lim.limits_in_force(None, {})["source"], "default")
        self.assertEqual(lim.limits_in_force({"none": True}, {})["source"], "none")
        self.assertEqual(lim.limits_in_force(None, {"none": True})["source"], "none")
        parsed = {"rule_words": 100, "file_bytes": 10240, "root_bytes": 20480, "test": None}
        self.assertEqual(lim.limits_in_force(parsed, {})["rule_words"], 100)
        self.assertEqual(lim.limits_in_force(parsed, {})["source"], "guidelines")
        got = lim.limits_in_force(parsed, {"rule_words": 60})
        self.assertEqual((got["source"], got["rule_words"], got["file_bytes"]), ("arguments", 60, 10240))

    def test_none_from_arguments_beats_guidelines_numbers(self):
        parsed = {"rule_words": 100, "file_bytes": 10240, "root_bytes": 20480, "test": None}
        got = lim.limits_in_force(parsed, {"none": True})
        self.assertEqual((got["source"], got["rule_words"], got["file_bytes"]), ("none", 80, 8192))

    def test_none_from_arguments_keeps_the_guidelines_test(self):
        # Test maintenance keys on `test`: a `none` override must still say which test enforces the old limits.
        parsed = {"rule_words": 100, "file_bytes": 10240, "root_bytes": 20480, "test": "tests/test_agents.py"}
        got = lim.limits_in_force(parsed, {"none": True})
        self.assertEqual((got["source"], got["rule_words"], got["test"]), ("none", 80, "tests/test_agents.py"))
        self.assertIsNone(lim.limits_in_force({**parsed, "test": None}, {"none": True})["test"])
        self.assertIsNone(lim.limits_in_force({"none": True}, {"none": True})["test"])
        self.assertIsNone(lim.limits_in_force(None, {"none": True})["test"])

    def test_numbers_from_arguments_beat_guidelines_none(self):
        got = lim.limits_in_force({"none": True}, {"rule_words": 60})
        self.assertEqual((got["source"], got["rule_words"], got["file_bytes"]), ("arguments", 60, 8192))

    def test_over_limits(self):
        limits = lim.limits_in_force(None, {})
        rules = [
            {"line": 1, "end_line": 1, "words": 81, "first_words": "x"},
            {"line": 2, "end_line": 2, "words": 80, "first_words": "y"},
        ]
        got = lim.over_limits(rules, 8193, False, limits)
        self.assertEqual([r["line"] for r in got["rules"]], [1])
        self.assertEqual((got["file"], got["limit"], got["headroom"]), (True, 8192, True))
        self.assertEqual(lim.over_limits(rules, 7000, False, limits)["headroom"], False)
        at_margin = int(0.9 * 8192)  # 7372, just under 90% (7372.8)
        self.assertEqual(lim.over_limits(rules, at_margin, False, limits)["headroom"], False)
        self.assertEqual(lim.over_limits(rules, at_margin + 1, False, limits)["headroom"], True)
        self.assertEqual(lim.over_limits(rules, int(0.9 * 16384), True, limits)["headroom"], False)
        self.assertEqual(lim.over_limits(rules, int(0.9 * 16384) + 1, True, limits)["headroom"], True)
        # 90% of 8192 is not a whole byte count, so `>` and `>=` agree there; 90% of 10240 is exactly 9216.
        even = lim.limits_in_force(None, {"file_bytes": 10240})
        self.assertEqual(lim.over_limits(rules, 9216, False, even)["headroom"], False)
        self.assertEqual(lim.over_limits(rules, 9217, False, even)["headroom"], True)
        self.assertEqual(lim.over_limits(rules, 16384, True, limits)["file"], False)
        none = lim.over_limits(rules, 99999, False, lim.limits_in_force({"none": True}, {}))
        self.assertEqual((none["rules"], none["file"], none["headroom"]), ([], False, False))


if __name__ == "__main__":
    unittest.main()
