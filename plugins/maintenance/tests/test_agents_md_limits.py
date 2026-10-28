import unittest

from agents_helpers import agents_md_limits as lim


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


class BytesTest(unittest.TestCase):
    def test_normalised_bytes_ignores_crlf(self):
        self.assertEqual(lim.normalised_bytes(b"- a\r\n- b\r\n"), lim.normalised_bytes(b"- a\n- b\n"))
        self.assertEqual(lim.normalised_bytes("- é\n".encode()), 5)


class LimitsLineTest(unittest.TestCase):
    def test_none(self):
        self.assertEqual(lim.parse_limits_line("- Push-down: aggressive.\n- Size limits: none.\n"), {"none": True})

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

    def test_malformed_raises(self):
        with self.assertRaises(ValueError):
            lim.parse_limits_line("- Size limits: 80 words, 8 KB.\n")

    def test_format_round_trips(self):
        line = lim.format_limits_line(80, 8192, 16384, "t/test_a.py")
        self.assertEqual(
            line, "Size limits: 80 words per rule, 8 KB per file, 16 KB for the root; checked by `t/test_a.py`."
        )
        self.assertEqual(lim.parse_limits_line(line)["test"], "t/test_a.py")
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
        self.assertEqual(lim.over_limits(rules, 16384, True, limits)["file"], False)
        none = lim.over_limits(rules, 99999, False, lim.limits_in_force({"none": True}, {}))
        self.assertEqual((none["rules"], none["file"], none["headroom"]), ([], False, False))


if __name__ == "__main__":
    unittest.main()
