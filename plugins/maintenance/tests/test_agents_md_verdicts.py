import json
from pathlib import Path
import shutil
import tempfile
import unittest

from agents_helpers import call, commit_all, git, make_repo, write

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
        split = {
            "blocks": ["b1"],
            "action": "split",
            "criterion": "brief",
            "parts": [{"text": "- a"}, {"text": "- b"}],
            "evidence": ["e"],
        }
        code, out, err = self.check(self.keep_rest(split))
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["counts"], {"split": 1, "keep": 3})

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
        "move": {"new_text", "target", "target_kind", "target_heading", "pointer"},
        "split": {"parts"},
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
        "split": {
            "blocks": ["b1"],
            "action": "split",
            "criterion": "brief",
            "parts": [{"text": "- a"}, {"text": "- b"}],
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
        "pointer": "- see docs",
        "parts": [{"text": "- a"}, {"text": "- b"}],
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
                if action == "move":  # a pointer goes with a doc target
                    verdict = {**verdict, "target": "docs/x.md", "target_kind": "doc"}
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


class UnstageableTargetTest(VerdictsTestCase):
    """A move target git can't stage would leave the moved text uncommitted, so check-verdicts rejects it."""

    def setUp(self):
        super().setUp()
        write(self.repo, ".gitignore", "build/\ndist/\n*.gen.js\n/:gen/\n")
        write(self.repo, "build/out.js", "x = 1\n")
        write(self.repo, "src/b.gen.js", "x = 1\n")
        write(self.repo, "src/tracked.gen.js", "x = 1\n")
        commit_all(self.repo, "ignore")
        git(self.repo, "add", "-f", "src/tracked.gen.js")
        git(self.repo, "commit", "-m", "tracked")

    def move(self, target, kind="agents_md"):
        verdict = {
            "blocks": ["b1"],
            "action": "move",
            "criterion": "level",
            "target": target,
            "target_kind": kind,
            "evidence": ["e"],
        }
        return self.check(self.keep_rest(verdict))

    def test_ignored_targets_are_rejected(self):
        for target, kind in [
            ("build/AGENTS.md", "agents_md"),  # a new file in an ignored directory that exists
            ("dist/AGENTS.md", "agents_md"),  # and in one that doesn't
            ("dist/deep/AGENTS.md", "agents_md"),
            ("build/out.js", "comment"),  # an existing ignored file
            ("src/b.gen.js", "comment"),
        ]:
            with self.subTest(target=target):
                self.assertRejected(self.move(target, kind), f"verdict 1: target {target} is ignored by git")
        write(self.repo, "build/AGENTS.md", "- b\n")
        self.assertRejected(self.move("build/AGENTS.md"), "is ignored by git")

    def test_tracked_and_unignored_targets_are_accepted(self):
        for target, kind in [
            ("src/AGENTS.md", "agents_md"),
            ("src/a.py", "comment"),
            ("src/tracked.gen.js", "comment"),  # tracked, so git stages it although a rule matches it
            ("buil*/AGENTS.md", "agents_md"),  # the path is read literally, never as a glob
            (":build/AGENTS.md", "agents_md"),  # nor as pathspec magic
        ]:
            with self.subTest(target=target):
                code, _, err = self.move(target, kind)
                self.assertEqual(code, 0, err)

    def test_a_leading_colon_is_not_pathspec_magic(self):
        # Read as magic, `:gen/AGENTS.md` would be checked as `gen/AGENTS.md`, which no rule ignores.
        self.assertRejected(self.move(":gen/AGENTS.md"), "verdict 1: target :gen/AGENTS.md is ignored by git")

    def test_targets_in_a_submodule_are_rejected(self):
        sub = make_repo(self.tmp / "sub-src")
        write(sub, "f.py", "x = 1\n")
        commit_all(sub, "sub")
        sha = git(sub, "rev-parse", "HEAD").strip()
        # An initialised submodule: a nested repo, recorded in the index as a gitlink.
        shutil.copytree(sub, self.repo / "vendor" / "lib", symlinks=True)
        git(self.repo, "add", "vendor/lib")
        # Uninitialised ones: a gitlink in the index, with an empty directory or none.
        git(self.repo, "update-index", "--add", "--cacheinfo", f"160000,{sha},vendor/empty")
        (self.repo / "vendor" / "empty").mkdir()
        git(self.repo, "update-index", "--add", "--cacheinfo", f"160000,{sha},vendor/gone")
        git(self.repo, "commit", "-m", "submodules")
        self.assertEqual(git(self.repo, "ls-files", "-s", "vendor").count("160000"), 3)
        for target, kind in [
            ("vendor/lib/AGENTS.md", "agents_md"),
            ("vendor/lib/deep/AGENTS.md", "agents_md"),
            ("vendor/lib/f.py", "comment"),
            ("vendor/empty/AGENTS.md", "agents_md"),
            ("vendor/gone/AGENTS.md", "agents_md"),
        ]:
            with self.subTest(target=target):
                self.assertRejected(self.move(target, kind), f"verdict 1: target {target} ")
        self.assertIn("submodule", self.move("vendor/empty/AGENTS.md")[2])

    def test_targets_in_a_nested_repo_are_rejected(self):
        # Not a submodule: git add skips the path and exits 0, so nothing is staged.
        nested = make_repo(self.repo / "nested")
        write(nested, "f.py", "x = 1\n")
        for target, kind in [
            ("nested/repo/AGENTS.md", "agents_md"),
            ("nested/repo/deep/AGENTS.md", "agents_md"),
            ("nested/repo/f.py", "comment"),
        ]:
            with self.subTest(target=target):
                self.assertRejected(
                    self.move(target, kind), f"verdict 1: target {target} is inside another git repository"
                )

    def test_targets_in_a_linked_worktree_are_rejected(self):
        # A linked worktree's `.git` is a file, not a directory; git add skips the path and exits 0.
        git(self.repo, "worktree", "add", "-q", "wt")
        self.assertTrue((self.repo / "wt" / ".git").is_file())
        for target, kind in [("wt/AGENTS.md", "agents_md"), ("wt/src/a.py", "comment")]:
            with self.subTest(target=target):
                self.assertRejected(
                    self.move(target, kind), f"verdict 1: target {target} is inside another git repository: wt"
                )

    def test_a_dangling_git_symlink_is_not_a_repo(self):
        # git stages paths there, so the target stays valid.
        (self.repo / "odd").mkdir()
        (self.repo / "odd" / ".git").symlink_to("nowhere")
        code, _, err = self.move("odd/AGENTS.md")
        self.assertEqual(code, 0, err)

    def test_targets_beyond_a_symlinked_directory_are_rejected(self):
        # git stages neither path, though both lead to src/ inside the repo.
        (self.repo / "alias").symlink_to("src", target_is_directory=True)
        for target, kind in [("alias/AGENTS.md", "agents_md"), ("alias/a.py", "comment")]:
            with self.subTest(target=target):
                self.assertRejected(self.move(target, kind), f"verdict 1: target {target} can't be staged")


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
        self.assertRejected(self.check([], role="partial"), "--role")


class ProposedVerdictTest(VerdictsTestCase):
    """An ask's `proposed` verdict is applied as it is when the user answers as recommended, so it is checked like
    a top-level verdict, on the ask's blocks, criterion and evidence."""

    def ask(self, proposed, blocks=("b1",), **fields):
        return {
            "blocks": list(blocks),
            "action": "ask",
            "criterion": "valuable",
            "evidence": ["e"],
            "question": "q?",
            "recommendation": "r",
            "proposed": proposed,
            **fields,
        }

    MOVE = {"action": "move", "target": "src/AGENTS.md", "target_kind": "agents_md"}

    def test_every_action_but_ask_is_accepted(self):
        for proposed, blocks in [
            ({"action": "keep"}, ("b1",)),
            ({"action": "fix", "new_text": "n", "rename_only": False}, ("b1",)),
            ({"action": "trim", "new_text": "n"}, ("b1",)),
            ({"action": "delete"}, ("b1",)),
            ({"action": "merge", "new_text": "n"}, ("b1", "b2")),
            ({**self.MOVE, "new_text": "n", "target_heading": "Rules"}, ("b1",)),
            ({"action": "move", "target": "src/a.py", "target_kind": "comment"}, ("b1",)),
        ]:
            with self.subTest(action=proposed["action"], target=proposed.get("target")):
                verdict = self.ask(proposed, blocks)
                code, out, err = self.check(self.keep_rest(verdict))
                self.assertEqual(code, 0, err)
                self.assertIn(verdict, json.loads(out)["verdicts"])

    def test_it_inherits_the_asks_blocks_criterion_and_evidence(self):
        # Without inheritance a bare delete has no criterion or evidence, and a merge no blocks.
        for proposed, blocks in [({"action": "delete"}, ("b1",)), ({"action": "merge", "new_text": "n"}, ("b1", "b2"))]:
            with self.subTest(action=proposed["action"]):
                code, _, err = self.check(self.keep_rest(self.ask(proposed, blocks)))
                self.assertEqual(code, 0, err)
        self.assertRejected(
            self.check(self.keep_rest(self.ask({"action": "merge", "new_text": "n"}))),
            "verdict 1: proposed: merge needs at least 2 blocks",
        )

    def test_its_own_criterion_evidence_and_blocks_are_checked(self):
        code, _, err = self.check(
            self.keep_rest(self.ask({"action": "trim", "new_text": "n", "criterion": "brief", "evidence": ["x"]}))
        )
        self.assertEqual(code, 0, err)
        code, _, err = self.check(self.keep_rest(self.ask({"action": "delete", "blocks": ["b1"]})))
        self.assertEqual(code, 0, err)
        for proposed, expected in [
            ({"action": "delete", "criterion": "bogus"}, "criterion must be one of"),
            ({"action": "delete", "evidence": []}, "evidence must be"),
            ({"action": "delete", "evidence": "e"}, "evidence must be"),
            ({"action": "delete", "blocks": ["b2"]}, "blocks must be the ask's blocks"),
            ({"action": "delete", "blocks": "b1"}, "blocks must be the ask's blocks"),
            ({"action": "delete", "ruling": 3}, "ruling must be a string"),
        ]:
            with self.subTest(proposed=proposed):
                self.assertRejected(self.check(self.keep_rest(self.ask(proposed))), f"verdict 1: proposed: {expected}")

    def test_its_action_is_a_verdict_action_other_than_ask(self):
        for proposed in [
            {"action": "ask", "question": "q?", "recommendation": "r"},
            {"action": "bogus", "x": 1},
            {"action": "bogus"},
            {},
            {"new_text": "n"},
        ]:
            with self.subTest(proposed=proposed):
                self.assertRejected(
                    self.check(self.keep_rest(self.ask(proposed))), "verdict 1: proposed: action must be one of"
                )

    def test_it_takes_only_the_fields_its_action_takes(self):
        for proposed, field in [
            ({"action": "keep", "new_text": "n"}, "new_text"),
            ({"action": "delete", "question": "q?"}, "question"),
            ({"action": "delete", "proposed": {"action": "keep"}}, "proposed"),
            ({**self.MOVE, "new_txt": "n"}, "new_txt"),
            ({"action": "trim", "new_text": "n", "target": "src/AGENTS.md"}, "target"),
        ]:
            with self.subTest(proposed=proposed):
                self.assertRejected(
                    self.check(self.keep_rest(self.ask(proposed))),
                    f"verdict 1: proposed: {proposed['action']} does not",
                )
                self.assertIn(field, self.check(self.keep_rest(self.ask(proposed)))[2])

    def test_new_text_and_rename_only_rules(self):
        for proposed, expected in [
            ({"action": "trim"}, "new_text must be"),
            ({"action": "fix", "new_text": " "}, "new_text must be"),
            ({"action": "merge"}, "new_text must be"),
            ({**self.MOVE, "new_text": ""}, "new_text must be"),
            ({"action": "fix", "new_text": "n", "rename_only": "yes"}, "rename_only must be a boolean"),
        ]:
            blocks = ("b1", "b2") if proposed["action"] == "merge" else ("b1",)
            with self.subTest(proposed=proposed):
                self.assertRejected(
                    self.check(self.keep_rest(self.ask(proposed, blocks))), f"verdict 1: proposed: {expected}"
                )

    def test_move_targets_are_checked_like_a_top_level_move(self):
        write(self.repo, "src/it's.py", "x = 1\n")
        commit_all(self.repo, "quote")
        for target, kind in [
            ("../out's/AGENTS.md", "agents_md"),
            ("../outs/AGENTS.md", "agents_md"),
            ("./tests//AGENTS.md", "agents_md"),
            ("src/../AGENTS.md", "agents_md"),
            ("/abs/AGENTS.md", "agents_md"),
            ("x';touch pwned;'/AGENTS.md", "agents_md"),
            ("src/it's.py", "comment"),
            ("src\nx/AGENTS.md", "agents_md"),
            ("src\x7fx/AGENTS.md", "agents_md"),
            (".git/AGENTS.md", "agents_md"),
            (".GIT/config", "comment"),
            ("AGENTS.md", "agents_md"),
            ("src/notes.md", "agents_md"),
            ("src/missing.py", "comment"),
            ("src/a.py", "other"),
        ]:
            with self.subTest(target=target, kind=kind):
                proposed = {"action": "move", "target": target, "target_kind": kind}
                self.assertRejected(self.check(self.keep_rest(self.ask(proposed))), "verdict 1: proposed: ")
        for drop in ("target", "target_kind"):
            with self.subTest(drop=drop):
                proposed = {k: v for k, v in self.MOVE.items() if k != drop}
                self.assertRejected(self.check(self.keep_rest(self.ask(proposed))), f"verdict 1: proposed: {drop}")
        self.assertRejected(
            self.check(self.keep_rest(self.ask({**self.MOVE, "target_heading": 3}))),
            "verdict 1: proposed: target_heading must be a string",
        )

    def test_ancestor_proposed_is_a_move_into_the_scope(self):
        write(self.repo, "src/deep/AGENTS.md", "- d\n")
        for proposed in (self.MOVE, {**self.MOVE, "target": "src/deep/AGENTS.md"}):
            with self.subTest(target=proposed["target"]):
                code, _, err = self.check([self.ask(proposed)], role="ancestor", scope=["src"])
                self.assertEqual(code, 0, err)
        for proposed, expected in [
            ({**self.MOVE, "target": "new/AGENTS.md"}, "target new/AGENTS.md is outside the scope"),
            (
                {"action": "move", "target": "README.md", "target_kind": "comment"},
                "target README.md is outside the scope",
            ),
            ({"action": "delete"}, "action delete is not allowed on an ancestor file; use move"),
            ({"action": "trim", "new_text": "n"}, "action trim is not allowed on an ancestor file"),
            ({"action": "keep"}, "action keep is not allowed on an ancestor file"),
        ]:
            with self.subTest(proposed=proposed):
                self.assertRejected(
                    self.check([self.ask(proposed)], role="ancestor", scope=["src"]), f"verdict 1: proposed: {expected}"
                )

    def test_a_guidelines_block_still_takes_an_ask_with_any_proposed(self):
        # The ask is the user's say on a guidelines block, so its proposed change is allowed there.
        write(self.repo, "AGENTS.md", GUIDED)
        commit_all(self.repo, "guided")
        verdicts = [{"blocks": [f"b{n}"], "action": "keep"} for n in range(1, 5) if n != 2]
        verdicts.insert(1, self.ask({"action": "trim", "new_text": "- Push-down: aggressive."}, ("b2",)))
        code, _, err = self.check(verdicts)
        self.assertEqual(code, 0, err)


CHOICES = (
    "- Write clear, readable code.\n"
    "- Use `uv` for dependencies.\n"
    "- Prefer composition rather than inheritance.\n"
    "- Lint with ruff, never black.\n"
)


class ProjectChoiceDeletionTest(VerdictsTestCase):
    """A deletion that may drop a project choice comes back as an ask, unless a copy in another file is cited."""

    def setUp(self):
        super().setUp()
        write(self.repo, "AGENTS.md", CHOICES)
        write(self.repo, "docs/style.md", "Use uv.\n")
        commit_all(self.repo, "choices")

    def delete(self, block, *evidence, criterion="valuable"):
        return {"blocks": [block], "action": "delete", "criterion": criterion, "evidence": list(evidence) or ["e"]}

    def verdicts(self, verdict):
        code, out, err = self.check(self.keep_rest(verdict))
        self.assertEqual(code, 0, err)
        return json.loads(out)

    def assertAsks(self, verdict):
        report = self.verdicts(verdict)
        (got,) = [v for v in report["verdicts"] if v["blocks"] == verdict["blocks"]]
        self.assertEqual(got["action"], "ask")
        self.assertEqual(got["proposed"], {"action": "delete"})
        self.assertEqual((got["criterion"], got["evidence"]), (verdict["criterion"], verdict["evidence"]))
        self.assertTrue(got["question"].strip() and got["recommendation"].strip())
        self.assertEqual(report["counts"], {"ask": 1, "keep": 3})
        # The rewritten verdict is itself valid.
        code, _, err = self.check(report["verdicts"])
        self.assertEqual(code, 0, err)

    def assertDeletes(self, verdict):
        report = self.verdicts(verdict)
        self.assertIn(verdict, report["verdicts"])
        self.assertEqual(report["counts"], {"delete": 1, "keep": 3})

    def test_generic_advice_stays_a_delete(self):
        self.assertDeletes(self.delete("b1", "AGENTS.md:1 is advice any project follows"))

    def test_tool_or_alternative_becomes_an_ask(self):
        for block in ("b2", "b3", "b4"):
            with self.subTest(block=block):
                self.assertAsks(self.delete(block, f"AGENTS.md:{block[1]} is common practice"))

    def test_a_brief_delete_naming_a_tool_becomes_an_ask(self):
        self.assertAsks(self.delete("b2", "says nothing", criterion="brief"))

    def test_a_cited_copy_in_another_file_keeps_the_delete(self):
        for evidence in ("docs/style.md:1 says the same", "`docs/style.md`:1 Use uv."):
            with self.subTest(evidence=evidence):
                self.assertDeletes(self.delete("b2", "AGENTS.md:2", evidence))

    def test_a_citation_that_proves_no_copy_still_asks(self):
        for evidence in ("AGENTS.md:2 duplicates itself", "docs/nope.md:1", "../outside.md:1", "/etc/hosts:1"):
            with self.subTest(evidence=evidence):
                self.assertAsks(self.delete("b2", evidence))

    def test_a_false_statement_stays_a_delete(self):
        self.assertDeletes(self.delete("b2", "uv.lock: no such file", criterion="accurate"))

    def test_ruling_is_kept(self):
        verdict = {**self.delete("b2"), "ruling": "Tool choices are never obvious."}
        (got,) = [v for v in self.verdicts(verdict)["verdicts"] if v["blocks"] == ["b2"]]
        self.assertEqual((got["action"], got["ruling"]), ("ask", "Tool choices are never obvious."))


if __name__ == "__main__":
    unittest.main()


class SplitAndDocTest(VerdictsTestCase):
    def setUp(self):
        super().setUp()
        write(self.repo, "docs/architecture.md", "# Arch\n\n## Retry\n")
        commit_all(self.repo, "docs")

    def doc_move(self, **extra):
        return {
            "blocks": ["b1"],
            "action": "move",
            "criterion": "placement",
            "evidence": ["AGENTS.md:1 explains"],
            "target": "docs/architecture.md",
            "target_kind": "doc",
            "target_heading": "Retry",
            "new_text": "The retry loop backs off twice.",
            **extra,
        }

    def test_doc_move_with_pointer_is_accepted(self):
        code, out, err = self.check(
            self.keep_rest(self.doc_move(pointer="- Retry behaviour: see `docs/architecture.md#retry`."))
        )
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["counts"], {"move": 1, "keep": 3})

    def test_doc_move_to_a_new_page_is_accepted(self):
        v = self.doc_move(target="docs/branding.md")
        del v["target_heading"]
        self.assertEqual(self.check(self.keep_rest(v))[0], 0)

    def test_doc_move_requires_new_text(self):
        v = self.doc_move()
        del v["new_text"]
        self.assertRejected(self.check(self.keep_rest(v)), "new_text")

    def test_pointer_only_on_doc_moves(self):
        v = {**self.doc_move(pointer="- see docs"), "target": "src/AGENTS.md", "target_kind": "agents_md"}
        self.assertRejected(self.check(self.keep_rest(v)), "pointer")

    def test_doc_target_rules(self):
        for target, expected in (
            ("docs/AGENTS.md", "AGENTS.md"),
            ("src/a.py", ".md"),
            ("../out.md", ".."),
            ("README.md/x.md", "not a file path"),
        ):
            self.assertRejected(self.check(self.keep_rest(self.doc_move(target=target))), expected)

    def test_placement_is_a_criterion(self):
        v = {
            "blocks": ["b1"],
            "action": "delete",
            "criterion": "placement",
            "evidence": ["docs/architecture.md:3 already says it"],
        }
        self.assertEqual(self.check(self.keep_rest(v))[0], 0)

    def split(self, parts):
        return {
            "blocks": ["b1"],
            "action": "split",
            "criterion": "brief",
            "evidence": ["AGENTS.md:1 holds two rules"],
            "parts": parts,
        }

    def test_split_is_accepted_with_staying_and_leaving_parts(self):
        v = self.split(
            [
                {"text": "- First rule."},
                {
                    "text": "- Second rule: see `docs/architecture.md#retry`.",
                    "target": "docs/architecture.md",
                    "target_kind": "doc",
                    "target_heading": "Retry",
                    "pointer": "- Retry: see `docs/architecture.md#retry`.",
                },
                {"text": "Keep MAX at 3.", "target": "src/a.py", "target_kind": "comment"},
            ]
        )
        code, out, err = self.check(self.keep_rest(v))
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["counts"]["split"], 1)

    def test_split_needs_two_parts_with_text(self):
        self.assertRejected(self.check(self.keep_rest(self.split([{"text": "- one"}]))), "parts")
        self.assertRejected(self.check(self.keep_rest(self.split([{"text": "- one"}, {"text": ""}]))), "text")
        self.assertRejected(
            self.check(self.keep_rest(self.split([{"text": "- one"}, {"text": "- two", "extra": 1}]))), "extra"
        )

    def test_split_part_targets_are_checked_like_moves(self):
        bad = self.split([{"text": "- one"}, {"text": "- two", "target": "docs/AGENTS.md", "target_kind": "doc"}])
        self.assertRejected(self.check(self.keep_rest(bad)), "AGENTS.md")
        missing = self.split([{"text": "- one"}, {"text": "- two", "target": "docs/architecture.md"}])
        self.assertRejected(self.check(self.keep_rest(missing)), "target_kind")
        pointer = self.split(
            [{"text": "- one"}, {"text": "- two", "target": "src/a.py", "target_kind": "comment", "pointer": "x"}]
        )
        self.assertRejected(self.check(self.keep_rest(pointer)), "pointer")

    def test_split_takes_no_new_text(self):
        self.assertRejected(
            self.check(self.keep_rest({**self.split([{"text": "- a"}, {"text": "- b"}]), "new_text": "x"})), "new_text"
        )

    def test_split_is_not_allowed_on_an_ancestor(self):
        write(self.repo, "src/AGENTS.md", "- s\n")
        commit_all(self.repo, "src")
        self.assertRejected(
            self.check([self.split([{"text": "- a"}, {"text": "- b"}])], role="ancestor", scope=["src"]), "not allowed"
        )

    def test_proposed_may_be_a_split_or_a_doc_move(self):
        ask = {
            "blocks": ["b1"],
            "action": "ask",
            "criterion": "brief",
            "evidence": ["e"],
            "question": "q",
            "recommendation": "r",
            "proposed": {"action": "split", "parts": [{"text": "- a"}, {"text": "- b"}]},
        }
        self.assertEqual(self.check(self.keep_rest(ask))[0], 0)
        ask["proposed"] = {"action": "move", "target": "docs/architecture.md", "target_kind": "doc", "new_text": "t"}
        self.assertEqual(self.check(self.keep_rest(ask))[0], 0)


class WordLimitGuardTest(VerdictsTestCase):
    LONG = "- " + " ".join(["w"] * 81)

    def check_limited(self, verdicts, **kw):
        path = self.tmp / "verdicts.txt"
        path.write_text(
            "Report\n```json\n" + json.dumps({"file": "AGENTS.md", "verdicts": verdicts}) + "\n```\n", encoding="utf-8"
        )
        return call(
            "check-verdicts",
            "--repo",
            str(self.repo),
            "--file",
            "AGENTS.md",
            "--role",
            "full",
            "--input",
            str(path),
            "--rule-words",
            "80",
        )

    def test_keep_over_the_word_limit_becomes_an_ask(self):
        write(self.repo, "AGENTS.md", self.LONG + "\n\n- two\n\n- three\n\n- four\n")
        commit_all(self.repo, "long")
        code, out, err = self.check_limited(self.keep_rest())
        self.assertEqual(code, 0, err)
        first = json.loads(out)["verdicts"][0]
        self.assertEqual((first["action"], first["criterion"]), ("ask", "brief"))
        self.assertIn("81 words", first["evidence"][0])
        self.assertIn("exception list", first["question"])
        self.assertNotIn("proposed", first)

    def test_keep_with_a_ruling_stays_a_keep(self):
        write(self.repo, "AGENTS.md", self.LONG + "\n\n- two\n\n- three\n\n- four\n")
        commit_all(self.repo, "long")
        verdicts = self.keep_rest({"blocks": ["b1"], "action": "keep", "ruling": "whole: the security rule"})
        self.assertEqual(json.loads(self.check_limited(verdicts)[1])["verdicts"][0]["action"], "keep")

    def test_new_text_over_the_limit_is_rejected(self):
        long = "- " + " ".join(["x"] * 81)
        trim = {"blocks": ["b1"], "action": "trim", "criterion": "brief", "evidence": ["e"], "new_text": long}
        self.assertRejected(self.check_limited(self.keep_rest(trim)), "81 words, over the limit of 80")
        split = {
            "blocks": ["b1"],
            "action": "split",
            "criterion": "brief",
            "evidence": ["e"],
            "parts": [{"text": "- a"}, {"text": long}],
        }
        self.assertRejected(self.check_limited(self.keep_rest(split)), "parts")
        exact = {
            "blocks": ["b1"],
            "action": "trim",
            "criterion": "brief",
            "evidence": ["e"],
            "new_text": "- " + " ".join(["x"] * 80),
        }
        self.assertEqual(self.check_limited(self.keep_rest(exact))[0], 0)

    def test_without_the_flag_nothing_is_guarded(self):
        write(self.repo, "AGENTS.md", self.LONG + "\n\n- two\n\n- three\n\n- four\n")
        commit_all(self.repo, "long")
        self.assertEqual(json.loads(self.check(self.keep_rest())[1])["verdicts"][0]["action"], "keep")
