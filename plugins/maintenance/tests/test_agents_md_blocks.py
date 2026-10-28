import unittest

from agents_helpers import agents_md_inventory as inv


def kl(text):
    return [(b["kind"], b["start_line"], b["end_line"], b["heading_path"]) for b in inv.split_blocks(text)]


class SplitBlocksTest(unittest.TestCase):
    def test_core_rules_section_is_three_blocks(self):
        text = "# T\n\n## Core rules\n\nThese apply.\n\n1. **A.** x\n2. **B.** y\n"
        self.assertEqual(
            kl(text),
            [
                ("paragraph", 5, 5, ["T", "Core rules"]),
                ("list_item", 7, 7, ["T", "Core rules"]),
                ("list_item", 8, 8, ["T", "Core rules"]),
            ],
        )

    def test_ids_and_text_exact(self):
        blocks = inv.split_blocks("a\nb\n\n- c\n")
        self.assertEqual([b["id"] for b in blocks], ["b1", "b2"])
        self.assertEqual(blocks[0]["text"], "a\nb")

    def test_nested_items_belong_to_parent(self):
        self.assertEqual(kl("- a\n  - a1\n  - a2\n- b\n"), [("list_item", 1, 3, []), ("list_item", 4, 4, [])])

    def test_multi_paragraph_item(self):
        self.assertEqual(kl("1. step\n\n   more\n2. next\n"), [("list_item", 1, 3, []), ("list_item", 4, 4, [])])

    def test_lazy_continuation(self):
        self.assertEqual(kl("- a long\nwrapped\n- b\n"), [("list_item", 1, 2, []), ("list_item", 3, 3, [])])

    def test_list_then_paragraph(self):
        self.assertEqual(kl("- a\n\nPara.\n"), [("list_item", 1, 1, []), ("paragraph", 3, 3, [])])

    def test_other_markers(self):
        self.assertEqual([k for k, *_ in kl("* a\n+ b\n3) c\n")], ["list_item"] * 3)

    def test_fence_hides_headings_and_blanks(self):
        self.assertEqual(kl("```\n# not heading\n\nx\n```\n"), [("code", 1, 5, [])])

    def test_tilde_fence(self):
        self.assertEqual(kl("~~~\n- x\n~~~\n"), [("code", 1, 3, [])])

    def test_fence_inside_item(self):
        self.assertEqual(
            kl("- run:\n\n  ```\n  cmd\n  ```\n- next\n"),
            [("list_item", 1, 5, []), ("list_item", 6, 6, [])],
        )

    def test_table(self):
        self.assertEqual(
            kl("| a | b |\n|---|---|\n| 1 | 2 |\n| 3 | 4 |\n"),
            [("table_header", 1, 2, []), ("table_row", 3, 3, []), ("table_row", 4, 4, [])],
        )

    def test_quote(self):
        self.assertEqual(kl("> one\n> two\n\nP\n"), [("quote", 1, 2, []), ("paragraph", 4, 4, [])])

    def test_comment(self):
        self.assertEqual(kl("<!-- a\nb -->\nP\n"), [("comment", 1, 2, []), ("paragraph", 3, 3, [])])

    def test_front_matter_skipped(self):
        self.assertEqual(kl("---\nname: x\n---\n# H\nP\n"), [("paragraph", 5, 5, ["H"])])

    def test_setext_headings(self):
        self.assertEqual(kl("Title\n=====\n\nSub\n---\n\nP\n"), [("paragraph", 7, 7, ["Title", "Sub"])])

    def test_thematic_break_is_not_heading(self):
        self.assertEqual(kl("P1\n\n---\n\nP2\n"), [("paragraph", 1, 1, []), ("paragraph", 5, 5, [])])

    def test_heading_levels_pop(self):
        self.assertEqual(
            [p for *_, p in kl("# A\n## B\nx\n## C\ny\n# D\nz\n")],
            [["A", "B"], ["A", "C"], ["D"]],
        )

    def test_closing_hashes_dropped(self):
        self.assertEqual(kl("## Foo ##\nx\n")[0][3], ["Foo"])

    def test_empty(self):
        self.assertEqual(inv.split_blocks(""), [])

    def test_list_directly_after_paragraph_line(self):
        self.assertEqual(
            kl("Intro:\n- a\n- b\n"), [("paragraph", 1, 1, []), ("list_item", 2, 2, []), ("list_item", 3, 3, [])]
        )

    def test_heading_ends_list_item(self):
        self.assertEqual(kl("- a\n# H\nx\n"), [("list_item", 1, 1, []), ("paragraph", 3, 3, ["H"])])

    def test_unclosed_fence_runs_to_end(self):
        self.assertEqual(kl("```\nx\n\ny\n"), [("code", 1, 4, [])])

    def test_text_is_exact_source_lines(self):
        blocks = inv.split_blocks("- a\n  - b\n\n```py\nx\n```\n")
        self.assertEqual([b["text"] for b in blocks], ["- a\n  - b", "```py\nx\n```"])

    def test_only_newline_splits_lines(self):
        self.assertEqual(kl("a\x0cb\n\nc"), [("paragraph", 1, 1, []), ("paragraph", 3, 3, [])])

    def test_crlf_lines(self):
        blocks = inv.split_blocks("a\r\n\r\n- b\r\n")
        self.assertEqual(
            [(b["kind"], b["start_line"], b["end_line"]) for b in blocks], [("paragraph", 1, 1), ("list_item", 3, 3)]
        )
        self.assertEqual(blocks[0]["text"], "a")

    def test_inline_code_with_backticks_is_not_a_fence(self):
        # Three backticks with a backtick later on the line are an inline code span; as a fence they would
        # swallow the rest of the file into one code block.
        self.assertEqual(kl("```foo``` is inline\n\nP\n"), [("paragraph", 1, 1, []), ("paragraph", 3, 3, [])])

    def test_only_an_item_numbered_one_interrupts_a_paragraph(self):
        self.assertEqual(kl("Steps:\n2. second\n"), [("paragraph", 1, 2, [])])
        self.assertEqual(kl("Steps:\n1) first\n"), [("paragraph", 1, 1, []), ("list_item", 2, 2, [])])

    def test_block_start_after_a_table_row_ends_the_table(self):
        self.assertEqual(
            kl("| a | b |\n|---|---|\n| 1 | 2 |\n> quote | x\n"),
            [("table_header", 1, 2, []), ("table_row", 3, 3, []), ("quote", 4, 4, [])],
        )

    def test_dashes_without_yaml_key_are_not_front_matter(self):
        self.assertEqual(kl("---\n\nP\n\n---\n\nQ\n"), [("paragraph", 3, 3, []), ("paragraph", 7, 7, [])])

    def test_unclosed_comment_is_one_line(self):
        # With no `-->` to end it, a comment must not hide the statements after it.
        self.assertEqual(
            kl("<!-- note\n\n- a\n\n- b\n"), [("comment", 1, 1, []), ("list_item", 3, 3, []), ("list_item", 5, 5, [])]
        )
        self.assertEqual(kl("# H\n<!-- note\nP\n"), [("comment", 2, 2, ["H"]), ("paragraph", 3, 3, ["H"])])

    def test_comment_unclosed_before_the_next_one_is_one_line(self):
        # A later comment's `-->` does not close an earlier unclosed one across the statements between them.
        self.assertEqual(
            kl("<!-- note\n\n- a\n\n<!-- b -->\n"),
            [("comment", 1, 1, []), ("list_item", 3, 3, []), ("comment", 5, 5, [])],
        )

    def test_closed_comment_spans_blank_lines(self):
        self.assertEqual(kl("<!-- a\n\nb -->\nP\n"), [("comment", 1, 3, []), ("paragraph", 4, 4, [])])

    def test_fence_indented_four_in_a_nested_item(self):
        # The fence belongs to the nested item, so its lines are code: no heading, no stray closing fence.
        text = "- a\n  - b:\n\n    ```sh\n# comment\n- not an item\n    ```\n- c\n"
        self.assertEqual(kl(text), [("list_item", 1, 7, []), ("list_item", 8, 8, [])])

    def test_fence_indented_to_an_ordered_items_content(self):
        text = "1. step:\n\n    ```\n    x\n\n # y\n    ```\n2. next\n"
        self.assertEqual(kl(text), [("list_item", 1, 7, []), ("list_item", 8, 8, [])])

    def test_fence_too_deep_for_its_item_is_not_a_fence(self):
        # Four or more columns past the item's content is indented code, not a fence, so a later item still splits.
        self.assertEqual(kl("- a\n\n      ```\n- b\n"), [("list_item", 1, 3, []), ("list_item", 4, 4, [])])


if __name__ == "__main__":
    unittest.main()
