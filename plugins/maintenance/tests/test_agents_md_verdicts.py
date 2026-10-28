import json
from pathlib import Path
import tempfile
import unittest

from agents_helpers import call, commit_all, make_repo, write

AGENTS = "- one\n\n- two\n\n- three\n\n- four\n"


class VerdictsTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.repo = make_repo(self.tmp)
        write(self.repo, "AGENTS.md", AGENTS)
        write(self.repo, "src/a.py", "x = 1\n")
        commit_all(self.repo, "add")

    def check(self, verdicts, role="full", scope=(), file="AGENTS.md", message=None, payload=None, repo=None):
        if payload is None:
            payload = {"file": file, "verdicts": verdicts}
        if message is None:
            message = "Report...\n```json\n" + json.dumps(payload) + "\n```\n"
        path = self.tmp / "verdicts.txt"
        path.write_text(message, encoding="utf-8")
        argv = [
            "check-verdicts",
            "--repo",
            str(repo or self.repo),
            "--file",
            file,
            "--role",
            role,
            "--input",
            str(path),
        ]
        for entry in scope:
            argv += ["--scope", entry]
        return call(*argv)

    def assertRejected(self, result, expected=None):
        code, out, err = result
        self.assertEqual(code, 1, out)
        self.assertEqual(out, "")
        self.assertTrue(err.startswith("error: "), err)
        self.assertEqual(len(err.strip().splitlines()), 1, err)
        if expected is not None:
            self.assertIn(expected, err)

    def keep_rest(self, *verdicts, skip=()):
        """The given verdicts plus `keep` for every other block of the fixture file."""
        used = {b for v in verdicts if isinstance(v["blocks"], list) for b in v["blocks"] if isinstance(b, str)}
        used |= set(skip)
        return [*verdicts, *({"blocks": [b], "action": "keep"} for b in ("b1", "b2", "b3", "b4") if b not in used)]


class FullRoleTest(VerdictsTestCase):
    def test_valid_full_coverage(self):
        verdicts = [
            {"blocks": ["b1"], "action": "keep"},
            {"blocks": ["b2"], "action": "trim", "criterion": "brief", "new_text": "x", "evidence": ["wordy"]},
            {
                "blocks": ["b3", "b4"],
                "action": "merge",
                "criterion": "brief",
                "new_text": "y",
                "evidence": ["same rule"],
            },
        ]
        code, out, err = self.check(verdicts)
        self.assertEqual(code, 0, err)
        result = json.loads(out)
        self.assertEqual(result["counts"], {"keep": 1, "trim": 1, "merge": 1})
        self.assertEqual(result["file"], "AGENTS.md")
        self.assertEqual(result["role"], "full")
        self.assertEqual(result["verdicts"], verdicts)

    def test_every_action_is_accepted(self):
        verdicts = [
            {"blocks": ["b1"], "action": "fix", "criterion": "accurate", "new_text": "n", "evidence": ["e"]},
            {"blocks": ["b2"], "action": "delete", "criterion": "valuable", "evidence": ["e"]},
            {
                "blocks": ["b3"],
                "action": "move",
                "criterion": "level",
                "target": "src/AGENTS.md",
                "target_kind": "agents_md",
                "target_heading": "Rules",
                "new_text": "z",
                "evidence": ["e"],
            },
            {
                "blocks": ["b4"],
                "action": "ask",
                "criterion": "valuable",
                "question": "q?",
                "recommendation": "r",
                "proposed": {"action": "delete"},
                "evidence": ["e"],
                "ruling": "saved",
            },
        ]
        code, out, err = self.check(verdicts)
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["counts"], {"fix": 1, "delete": 1, "move": 1, "ask": 1})

    def test_rename_only_on_fix_is_accepted(self):
        verdict = {
            "blocks": ["b1"],
            "action": "fix",
            "criterion": "accurate",
            "new_text": "n",
            "evidence": ["e"],
            "rename_only": True,
        }
        code, _, err = self.check(self.keep_rest(verdict))
        self.assertEqual(code, 0, err)

    def test_uses_last_json_fence(self):
        good = {"file": "AGENTS.md", "verdicts": [{"blocks": [b], "action": "keep"} for b in ("b1", "b2", "b3", "b4")]}
        message = "first\n```json\n{not json\n```\nthen\n```json\n" + json.dumps(good) + "\n```\nbye\n"
        code, _, err = self.check(None, message=message)
        self.assertEqual(code, 0, err)

    def test_last_fence_wins_when_it_is_the_bad_one(self):
        good = {"file": "AGENTS.md", "verdicts": [{"blocks": [b], "action": "keep"} for b in ("b1", "b2", "b3", "b4")]}
        message = "```json\n" + json.dumps(good) + "\n```\n```json\n{oops\n```\n"
        self.assertRejected(self.check(None, message=message), "error: invalid json: ")

    def test_no_json_block(self):
        code, out, err = self.check(None, message="nothing here\n```\n{}\n```\n")
        self.assertEqual((code, out, err), (1, "", "error: no json block\n"))

    def test_input_file_missing(self):
        code, out, err = call(
            "check-verdicts", "--repo", str(self.repo), "--file", "AGENTS.md", "--role", "full", "--input", "/no/such"
        )
        self.assertEqual((code, out), (1, ""))
        self.assertTrue(err.startswith("error: cannot read /no/such"), err)
        self.assertEqual(len(err.strip().splitlines()), 1)

    def test_top_level_must_be_an_object(self):
        self.assertRejected(self.check(None, payload=[1, 2]), "error: ")

    def test_verdicts_must_be_a_list(self):
        self.assertRejected(self.check(None, payload={"file": "AGENTS.md", "verdicts": {"a": 1}}), "verdicts")

    def test_verdict_must_be_an_object(self):
        self.assertRejected(self.check(["keep"]), "verdict 1")

    def test_wrong_file(self):
        write(self.repo, "src/AGENTS.md", "- x\n")
        self.assertRejected(
            self.check(self.keep_rest(), payload={"file": "src/AGENTS.md", "verdicts": []}),
            "error: verdicts are for src/AGENTS.md, expected AGENTS.md",
        )

    def test_missing_file_field(self):
        self.assertRejected(self.check(None, payload={"verdicts": []}), "expected AGENTS.md")

    def test_file_argument_is_normalised(self):
        verdicts = [{"blocks": [b], "action": "keep"} for b in ("b1", "b2", "b3", "b4")]
        code, out, err = self.check(
            verdicts, file="./src/../AGENTS.md", payload={"file": "AGENTS.md", "verdicts": verdicts}
        )
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["file"], "AGENTS.md")

    def test_file_must_exist_in_repo(self):
        code, out, err = self.check([], file="nope/AGENTS.md")
        self.assertEqual((code, out), (1, ""))
        self.assertEqual(err, "error: no such file in the repo: nope/AGENTS.md\n")

    def test_unknown_block(self):
        self.assertRejected(self.check(self.keep_rest({"blocks": ["b9"], "action": "keep"})), "b9")

    def test_uncovered_block_full(self):
        verdicts = [{"blocks": [b], "action": "keep"} for b in ("b1", "b2", "b3")]
        code, out, err = self.check(verdicts)
        self.assertEqual((code, out, err), (1, "", "error: block b4 not covered\n"))

    def test_duplicate_block(self):
        verdicts = self.keep_rest({"blocks": ["b1"], "action": "delete", "criterion": "brief", "evidence": ["e"]})
        verdicts.append({"blocks": ["b1"], "action": "keep"})
        self.assertRejected(self.check(verdicts), "b1")

    def test_duplicate_block_within_one_verdict(self):
        verdict = {"blocks": ["b1", "b1"], "action": "merge", "criterion": "brief", "new_text": "n", "evidence": ["e"]}
        self.assertRejected(self.check(self.keep_rest(verdict)), "b1")

    def test_blocks_must_be_a_non_empty_list(self):
        for blocks in ([], "b1", None, [1]):
            with self.subTest(blocks=blocks):
                self.assertRejected(
                    self.check(self.keep_rest({"blocks": blocks, "action": "keep"}, skip=["b1"])), "blocks"
                )

    def test_bad_action(self):
        self.assertRejected(self.check(self.keep_rest({"blocks": ["b1"], "action": "rewrite"})), "rewrite")

    def test_bad_criterion(self):
        verdict = {"blocks": ["b1"], "action": "delete", "criterion": "vibes", "evidence": ["e"]}
        self.assertRejected(self.check(self.keep_rest(verdict)), "criterion")

    def test_criterion_required_unless_keep(self):
        verdict = {"blocks": ["b1"], "action": "delete", "evidence": ["e"]}
        self.assertRejected(self.check(self.keep_rest(verdict)), "criterion")

    def test_missing_new_text(self):
        for action in ("fix", "trim", "merge"):
            with self.subTest(action=action):
                blocks = ["b1", "b2"] if action == "merge" else ["b1"]
                verdict = {"blocks": blocks, "action": action, "criterion": "brief", "evidence": ["e"]}
                self.assertRejected(self.check(self.keep_rest(verdict)), "new_text")
                verdict["new_text"] = ""
                self.assertRejected(self.check(self.keep_rest(verdict)), "new_text")

    def test_rename_only_only_on_fix(self):
        verdict = {
            "blocks": ["b1"],
            "action": "trim",
            "criterion": "brief",
            "new_text": "n",
            "evidence": ["e"],
            "rename_only": True,
        }
        self.assertRejected(self.check(self.keep_rest(verdict)), "rename_only")

    def test_rename_only_must_be_bool(self):
        verdict = {
            "blocks": ["b1"],
            "action": "fix",
            "criterion": "accurate",
            "new_text": "n",
            "evidence": ["e"],
            "rename_only": "yes",
        }
        self.assertRejected(self.check(self.keep_rest(verdict)), "rename_only")

    def test_merge_needs_two_blocks(self):
        verdict = {"blocks": ["b1"], "action": "merge", "criterion": "brief", "new_text": "n", "evidence": ["e"]}
        self.assertRejected(self.check(self.keep_rest(verdict)), "merge")

    def test_missing_evidence(self):
        for evidence in (None, [], [""], "text", [1]):
            with self.subTest(evidence=evidence):
                verdict = {"blocks": ["b1"], "action": "delete", "criterion": "brief"}
                if evidence is not None:
                    verdict["evidence"] = evidence
                self.assertRejected(self.check(self.keep_rest(verdict)), "evidence")

    def test_ask_needs_question_and_recommendation(self):
        full = {
            "blocks": ["b1"],
            "action": "ask",
            "criterion": "valuable",
            "evidence": ["e"],
            "question": "q?",
            "recommendation": "r",
        }
        code, _, err = self.check(self.keep_rest(full))
        self.assertEqual(code, 0, err)
        for missing in ("question", "recommendation"):
            with self.subTest(missing=missing):
                verdict = {k: v for k, v in full.items() if k != missing}
                self.assertRejected(self.check(self.keep_rest(verdict)), missing)

    def test_proposed_is_ask_only_and_an_object(self):
        verdict = {"blocks": ["b1"], "action": "delete", "criterion": "brief", "evidence": ["e"], "proposed": {}}
        self.assertRejected(self.check(self.keep_rest(verdict)), "proposed")
        ask = {
            "blocks": ["b1"],
            "action": "ask",
            "criterion": "valuable",
            "evidence": ["e"],
            "question": "q?",
            "recommendation": "r",
            "proposed": "delete",
        }
        self.assertRejected(self.check(self.keep_rest(ask)), "proposed")

    def test_ruling_must_be_a_string(self):
        self.assertRejected(self.check(self.keep_rest({"blocks": ["b1"], "action": "keep", "ruling": 3})), "ruling")

    def test_move_targets(self):
        write(self.repo, "src/notes.md", "n\n")
        for target, kind, ok in [
            ("src/AGENTS.md", "agents_md", True),
            ("src/agents.md", "agents_md", True),
            ("src/a.py", "comment", True),
            ("src/missing.py", "comment", False),
            ("src", "comment", False),
            ("src/notes.md", "agents_md", False),
            ("../x/AGENTS.md", "agents_md", False),
            ("src/../AGENTS.md", "agents_md", False),
            ("/abs/AGENTS.md", "agents_md", False),
            ("AGENTS.md", "agents_md", False),
            ("./AGENTS.md", "agents_md", False),
            ("", "agents_md", False),
            ("src/a.py", "other", False),
        ]:
            with self.subTest(target=target, kind=kind):
                verdict = {
                    "blocks": ["b1"],
                    "action": "move",
                    "criterion": "level",
                    "target": target,
                    "target_kind": kind,
                    "evidence": ["e"],
                }
                code, out, err = self.check(self.keep_rest(verdict))
                self.assertEqual(code == 0, ok, err)

    def test_move_target_rejects_quotes_and_control_characters(self):
        # The skill single-quotes targets in shell commands, so a quote or control character must never pass.
        write(self.repo, "src/it's.py", "x = 1\n")
        commit_all(self.repo, "quote")
        for target, kind in [
            ("x';touch pwned;'/AGENTS.md", "agents_md"),
            ("src/it's.py", "comment"),
            ("src\nx/AGENTS.md", "agents_md"),
            ("src\tx/AGENTS.md", "agents_md"),
            ("src\x1fx/AGENTS.md", "agents_md"),
            ("src\x7fx/AGENTS.md", "agents_md"),
            ("src/a\x7f.py", "comment"),
        ]:
            with self.subTest(target=target):
                verdict = {
                    "blocks": ["b1"],
                    "action": "move",
                    "criterion": "level",
                    "target": target,
                    "target_kind": kind,
                    "evidence": ["e"],
                }
                self.assertRejected(self.check(self.keep_rest(verdict)), "verdict 1:")

    def test_move_requires_target_and_kind(self):
        for drop in ("target", "target_kind"):
            with self.subTest(drop=drop):
                verdict = {
                    "blocks": ["b1"],
                    "action": "move",
                    "criterion": "level",
                    "target": "src/AGENTS.md",
                    "target_kind": "agents_md",
                    "evidence": ["e"],
                }
                del verdict[drop]
                self.assertRejected(self.check(self.keep_rest(verdict)), drop)

    def test_target_fields_are_move_only(self):
        verdict = {"blocks": ["b1"], "action": "delete", "criterion": "brief", "evidence": ["e"], "target": "src/a.py"}
        self.assertRejected(self.check(self.keep_rest(verdict)), "target")

    def test_target_heading_must_be_a_string(self):
        for heading in (3, None, ["Rules"]):
            with self.subTest(heading=heading):
                verdict = {
                    "blocks": ["b1"],
                    "action": "move",
                    "criterion": "level",
                    "target": "src/AGENTS.md",
                    "target_kind": "agents_md",
                    "target_heading": heading,
                    "evidence": ["e"],
                }
                self.assertRejected(self.check(self.keep_rest(verdict)), "target_heading must be a string")

    # The fields each action takes, from the reviewer agent's schema table.
    ALWAYS = {"blocks", "action", "criterion", "evidence", "ruling"}
    TAKES = {
        "keep": set(),
        "fix": {"new_text", "rename_only"},
        "trim": {"new_text"},
        "delete": set(),
        "merge": {"new_text"},
        "move": {"new_text", "target", "target_kind", "target_heading"},
        "ask": {"question", "recommendation", "proposed"},
    }
    # A valid verdict per action holding only its required fields, and a valid value for every known field.
    BASE = {
        "keep": {"blocks": ["b1"], "action": "keep"},
        "fix": {"blocks": ["b1"], "action": "fix", "criterion": "accurate", "new_text": "n", "evidence": ["e"]},
        "trim": {"blocks": ["b1"], "action": "trim", "criterion": "brief", "new_text": "n", "evidence": ["e"]},
        "delete": {"blocks": ["b1"], "action": "delete", "criterion": "valuable", "evidence": ["e"]},
        "merge": {"blocks": ["b1", "b2"], "action": "merge", "criterion": "brief", "new_text": "n", "evidence": ["e"]},
        "move": {
            "blocks": ["b1"],
            "action": "move",
            "criterion": "level",
            "target": "src/AGENTS.md",
            "target_kind": "agents_md",
            "evidence": ["e"],
        },
        "ask": {
            "blocks": ["b1"],
            "action": "ask",
            "criterion": "valuable",
            "question": "q?",
            "recommendation": "r",
            "evidence": ["e"],
        },
    }
    VALUES = {
        "criterion": "brief",
        "evidence": ["e"],
        "ruling": "a pattern",
        "new_text": "- shorter",
        "rename_only": True,
        "target": "src/AGENTS.md",
        "target_kind": "agents_md",
        "target_heading": "Rules",
        "question": "q?",
        "recommendation": "r",
        "proposed": {"action": "keep"},
    }
    # Misspelled and unknown keys, which the validator must never pass through as if absent.
    UNKNOWN = {"new_txt": "- reworded", "bogus": 1, "Question": "q?", "targets": "src/AGENTS.md"}

    def test_every_field_an_action_takes_is_accepted(self):
        for action, base in self.BASE.items():
            with self.subTest(action=action):
                verdict = {**base, **{k: self.VALUES[k] for k in self.ALWAYS | self.TAKES[action] if k in self.VALUES}}
                code, _, err = self.check(self.keep_rest(verdict))
                self.assertEqual(code, 0, err)

    def test_fields_an_action_does_not_take_are_rejected(self):
        for action, base in self.BASE.items():
            stray = {k: v for k, v in self.VALUES.items() if k not in self.ALWAYS | self.TAKES[action]}
            for field, value in {**stray, **self.UNKNOWN}.items():
                with self.subTest(action=action, field=field):
                    result = self.check(self.keep_rest({**base, field: value}))
                    self.assertRejected(result, "verdict 1: ")
                    self.assertIn(field, result[2])

    def test_unknown_top_level_keys_are_rejected(self):
        verdicts = [{"blocks": [b], "action": "keep"} for b in ("b1", "b2", "b3", "b4")]
        for key in ("summary", "Verdicts", "files"):
            with self.subTest(key=key):
                payload = {"file": "AGENTS.md", "verdicts": verdicts, key: []}
                self.assertRejected(self.check(None, payload=payload), key)

    def test_move_target_git_parts_in_any_case_or_through_symlinks(self):
        # On a case-insensitive filesystem `.GIT` is `.git`; a symlink can lead into `.git` under another name.
        (self.repo / "gitlink").symlink_to(".git", target_is_directory=True)
        (self.repo / "caselink").symlink_to(".GIT", target_is_directory=True)
        (self.repo / "src" / "cfg").symlink_to("../.git/config")
        outside = self.tmp / "outside"
        (outside / ".git").mkdir(parents=True)
        (outside / ".git" / "config").write_text("x\n", encoding="utf-8")
        (self.repo / "outlink").symlink_to(outside, target_is_directory=True)
        for target, kind in [
            (".git/AGENTS.md", "agents_md"),
            (".GIT/config", "comment"),
            (".Git/AGENTS.md", "agents_md"),
            ("src/.gIt/AGENTS.md", "agents_md"),
            ("gitlink/config", "comment"),
            ("gitlink/AGENTS.md", "agents_md"),
            ("gitlink/hooks/AGENTS.md", "agents_md"),
            ("src/cfg", "comment"),
            ("caselink/config", "comment"),
            ("caselink/AGENTS.md", "agents_md"),
            ("outlink/.git/config", "comment"),
            ("outlink/.Git/AGENTS.md", "agents_md"),
        ]:
            with self.subTest(target=target):
                verdict = {
                    "blocks": ["b1"],
                    "action": "move",
                    "criterion": "level",
                    "target": target,
                    "target_kind": kind,
                    "evidence": ["e"],
                }
                self.assertRejected(self.check(self.keep_rest(verdict)), "is inside .git")

    def test_move_target_that_is_the_same_file_is_rejected(self):
        # A link to the reviewed file, or the file a link points to, is the file itself.
        (self.repo / "docs").mkdir()
        (self.repo / "docs" / "AGENTS.md").symlink_to("../AGENTS.md")
        write(self.repo, "CLAUDE.md", "- c\n")
        (self.repo / "web").mkdir()
        (self.repo / "web" / "AGENTS.md").symlink_to("../CLAUDE.md")
        commit_all(self.repo, "links")
        for file, target, kind in [
            ("AGENTS.md", "docs/AGENTS.md", "agents_md"),
            ("docs/AGENTS.md", "AGENTS.md", "agents_md"),
            ("web/AGENTS.md", "CLAUDE.md", "comment"),
        ]:
            with self.subTest(file=file, target=target):
                verdict = {
                    "blocks": ["b1"],
                    "action": "move",
                    "criterion": "level",
                    "target": target,
                    "target_kind": kind,
                    "evidence": ["e"],
                }
                blocks = ("b1",) if file == "web/AGENTS.md" else ("b1", "b2", "b3", "b4")
                verdicts = [verdict, *({"blocks": [b], "action": "keep"} for b in blocks if b != "b1")]
                payload = {"file": file, "verdicts": verdicts}
                self.assertRejected(self.check(None, file=file, payload=payload), "the file itself")

    def test_symlink_loop_file_is_rejected(self):
        (self.repo / "loop").mkdir()
        (self.repo / "loop" / "AGENTS.md").symlink_to("AGENTS.md")
        commit_all(self.repo, "loop")
        code, stdout, err = self.check([], file="loop/AGENTS.md")
        self.assertEqual((code, stdout), (1, ""))
        self.assertEqual(err, "error: no such file in the repo: loop/AGENTS.md\n")

    def test_agents_md_target_must_stay_in_the_repo_as_a_file(self):
        outside = self.tmp / "outside"
        outside.mkdir()
        (outside / "AGENTS.md").write_text("- x\n", encoding="utf-8")
        (self.repo / "linkdir").symlink_to(outside, target_is_directory=True)
        (self.repo / "src" / "AGENTS.md").symlink_to(outside / "AGENTS.md")
        (self.repo / "loopdir").symlink_to("loopdir")
        (self.repo / "gone").symlink_to("nowhere", target_is_directory=True)
        (self.repo / "docs").mkdir()
        (self.repo / "docs" / "AGENTS.md").symlink_to("missing.md")
        for target in [
            "linkdir/AGENTS.md",
            ".git/AGENTS.md",
            "src/a.py/AGENTS.md",
            "src/a.py/deeper/AGENTS.md",
            "src/AGENTS.md",
            "loopdir/AGENTS.md",
            "loopdir/sub/AGENTS.md",
            "gone/AGENTS.md",
            "docs/AGENTS.md",
        ]:
            with self.subTest(target=target):
                verdict = {
                    "blocks": ["b1"],
                    "action": "move",
                    "criterion": "level",
                    "target": target,
                    "target_kind": "agents_md",
                    "evidence": ["e"],
                }
                self.assertRejected(self.check(self.keep_rest(verdict)), "verdict 1: ")

    def test_new_agents_md_target_in_a_new_directory_is_accepted(self):
        verdict = {
            "blocks": ["b1"],
            "action": "move",
            "criterion": "level",
            "target": "brand/new/AGENTS.md",
            "target_kind": "agents_md",
            "evidence": ["e"],
        }
        code, _, err = self.check(self.keep_rest(verdict))
        self.assertEqual(code, 0, err)

    def test_comment_target_symlink_out_of_repo(self):
        outside = self.tmp / "outside.py"
        outside.write_text("x\n", encoding="utf-8")
        (self.repo / "src" / "link.py").symlink_to(outside)
        verdict = {
            "blocks": ["b1"],
            "action": "move",
            "criterion": "level",
            "target": "src/link.py",
            "target_kind": "comment",
            "evidence": ["e"],
        }
        self.assertRejected(self.check(self.keep_rest(verdict)), "target")


GUIDED = "- one\n\n## AGENTS.md guidelines\n\n- Push-down: aggressive.\n- Brief.\n\n## Other\n\n- two\n"


class GuidelinesSectionTest(VerdictsTestCase):
    """In the root AGENTS.md, blocks under the guidelines heading change only through `ask`."""

    def setUp(self):
        super().setUp()
        write(self.repo, "AGENTS.md", GUIDED)  # b1 before the section, b2 and b3 in it, b4 after it
        write(self.repo, "src/AGENTS.md", GUIDED)
        commit_all(self.repo, "guidelines")

    def verdict(self, action, blocks=("b2",)):
        verdict = {**FullRoleTest.BASE[action], "blocks": list(blocks)}
        if action == "merge" and len(blocks) < 2:
            verdict["blocks"] = ["b2", "b3"]
        return verdict

    def test_only_keep_or_ask_on_a_guidelines_block(self):
        for action in ("fix", "trim", "delete", "move"):
            with self.subTest(action=action):
                self.assertRejected(self.check(self.keep_rest(self.verdict(action))), "guidelines")
        for blocks in (["b2", "b3"], ["b1", "b2"], ["b3", "b4"]):
            with self.subTest(merge=blocks):
                self.assertRejected(self.check(self.keep_rest(self.verdict("merge", blocks))), "guidelines")
        for action in ("keep", "ask"):
            with self.subTest(action=action):
                code, _, err = self.check(self.keep_rest(self.verdict(action)))
                self.assertEqual(code, 0, err)

    def test_blocks_outside_the_section_are_unaffected(self):
        for blocks in (["b1"], ["b4"]):
            with self.subTest(blocks=blocks):
                code, _, err = self.check(self.keep_rest(self.verdict("trim", blocks)))
                self.assertEqual(code, 0, err)

    def test_root_file_named_any_way(self):
        verdicts = self.keep_rest(self.verdict("trim"))
        self.assertRejected(
            self.check(verdicts, file="./AGENTS.md", payload={"file": "AGENTS.md", "verdicts": verdicts}), "guidelines"
        )
        for number, name in enumerate(("agents.md", "Agents.MD")):
            with self.subTest(name=name):
                repo = make_repo(self.tmp / f"other{number}")
                write(repo, name, GUIDED)
                commit_all(repo, "add")
                payload = {"file": name, "verdicts": verdicts}
                self.assertRejected(self.check(None, file=name, payload=payload, repo=repo), "guidelines")

    def test_a_link_to_the_root_file_is_the_root(self):
        # The skill may pass any path of a merged entry; the root's guidelines stay protected through each.
        (self.repo / "docs").mkdir()
        (self.repo / "docs" / "AGENTS.md").symlink_to("../AGENTS.md")
        commit_all(self.repo, "link")
        payload = {"file": "docs/AGENTS.md", "verdicts": self.keep_rest(self.verdict("trim"))}
        self.assertRejected(self.check(None, file="docs/AGENTS.md", payload=payload), "guidelines")

    def test_nested_file_is_not_the_root(self):
        payload = {"file": "src/AGENTS.md", "verdicts": self.keep_rest(self.verdict("trim"))}
        code, _, err = self.check(None, file="src/AGENTS.md", payload=payload)
        self.assertEqual(code, 0, err)
        # A nested link to a root file that isn't an AGENTS.md is not the root either.
        write(self.repo, "CLAUDE.md", GUIDED)
        (self.repo / "web").mkdir()
        (self.repo / "web" / "AGENTS.md").symlink_to("../CLAUDE.md")
        commit_all(self.repo, "link")
        payload = {"file": "web/AGENTS.md", "verdicts": self.keep_rest(self.verdict("trim"))}
        code, _, err = self.check(None, file="web/AGENTS.md", payload=payload)
        self.assertEqual(code, 0, err)

    def test_ancestor_root_cannot_move_a_guidelines_block(self):
        verdict = self.verdict("move")
        verdict["target"] = "src/deep/AGENTS.md"
        self.assertRejected(self.check([verdict], role="ancestor", scope=["src"]), "guidelines")
        code, _, err = self.check([{**verdict, "blocks": ["b4"]}], role="ancestor", scope=["src"])
        self.assertEqual(code, 0, err)


class AncestorRoleTest(VerdictsTestCase):
    MOVE = {
        "blocks": ["b2"],
        "action": "move",
        "criterion": "level",
        "target": "src/AGENTS.md",
        "target_kind": "agents_md",
        "evidence": ["about src"],
    }

    def test_ancestor_allows_uncovered(self):
        code, out, err = self.check([self.MOVE], role="ancestor", scope=["src"])
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["counts"], {"move": 1})

    def test_ancestor_with_no_verdicts(self):
        code, out, err = self.check([], role="ancestor", scope=["src"])
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["counts"], {})

    def test_ancestor_accepts_ask(self):
        ask = {
            "blocks": ["b1"],
            "action": "ask",
            "criterion": "level",
            "evidence": ["e"],
            "question": "q?",
            "recommendation": "r",
        }
        code, _, err = self.check([ask], role="ancestor", scope=["src"])
        self.assertEqual(code, 0, err)

    def test_ancestor_rejects_other_actions(self):
        trim = {"blocks": ["b1"], "action": "trim", "criterion": "brief", "new_text": "n", "evidence": ["e"]}
        for verdict in (trim, {"blocks": ["b1"], "action": "keep"}):
            with self.subTest(action=verdict["action"]):
                self.assertRejected(self.check([verdict], role="ancestor", scope=["src"]), verdict["action"])

    def test_ancestor_move_must_target_scope(self):
        write(self.repo, "docs/AGENTS.md", "- d\n")
        self.assertRejected(
            self.check([{**self.MOVE, "target": "docs/AGENTS.md"}], role="ancestor", scope=["src"]), "scope"
        )

    def test_ancestor_move_target_below_scope_and_comment_in_scope(self):
        write(self.repo, "src/deep/AGENTS.md", "- d\n")
        for target, kind in [("src/deep/AGENTS.md", "agents_md"), ("src/a.py", "comment")]:
            with self.subTest(target=target):
                code, _, err = self.check(
                    [{**self.MOVE, "target": target, "target_kind": kind}], role="ancestor", scope=["src"]
                )
                self.assertEqual(code, 0, err)

    def test_ancestor_scope_dot_contains_everything(self):
        code, _, err = self.check([self.MOVE], role="ancestor", scope=["."])
        self.assertEqual(code, 0, err)

    def test_ancestor_any_of_several_scopes(self):
        write(self.repo, "docs/AGENTS.md", "- d\n")
        code, _, err = self.check([{**self.MOVE, "target": "docs/AGENTS.md"}], role="ancestor", scope=["src", "docs"])
        self.assertEqual(code, 0, err)

    def test_ancestor_requires_scope(self):
        self.assertRejected(self.check([self.MOVE], role="ancestor"), "--scope")

    def test_ancestor_scope_must_be_a_directory(self):
        self.assertRejected(self.check([self.MOVE], role="ancestor", scope=["nope"]), "scope is not a directory")

    def test_ancestor_unknown_block_and_duplicates_still_rejected(self):
        self.assertRejected(self.check([{**self.MOVE, "blocks": ["b9"]}], role="ancestor", scope=["src"]), "b9")
        self.assertRejected(self.check([self.MOVE, self.MOVE], role="ancestor", scope=["src"]), "b2")

    def test_bad_role_is_a_usage_error(self):
        code, _, _ = self.check([], role="partial")
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
