import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

from agents_helpers import agents_md_inventory as inv
from agents_helpers import agents_md_limits, call, commit_all, make_repo, write

PLUGIN = Path(__file__).resolve().parent.parent
AGENT = PLUGIN / "agents" / "agents-md-reviewer.md"
SKILL = PLUGIN / "skills" / "agents-md-cleanup" / "SKILL.md"

_FENCE = re.compile(r"^```(\w+)\n(.*?)\n```$", re.S | re.M)


def parse_frontmatter(path: Path) -> dict:
    """Simple `key: value` lines between the first two `---` lines."""
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines and lines[0] == "---", f"{path} must start with frontmatter"
    meta = {}
    for line in lines[1:]:
        if line == "---":
            return meta
        key, _, value = line.partition(":")
        meta[key.strip()] = value.strip().strip('"')
    raise AssertionError("frontmatter is not closed")


def fences(lang: str) -> list[str]:
    return [body for kind, body in _FENCE.findall(AGENT.read_text(encoding="utf-8")) if kind == lang]


class ReviewerAgentTest(unittest.TestCase):
    def test_frontmatter(self):
        meta = parse_frontmatter(AGENT)
        self.assertEqual(meta["name"], "agents-md-reviewer")
        self.assertIn("never on its own", meta["description"])
        self.assertEqual(meta["model"], "opus")
        for tool in ("Edit", "Write", "Agent"):
            self.assertIn(tool, meta["disallowedTools"])
        self.assertNotIn("Edit", meta["tools"])
        self.assertNotIn("Write", meta["tools"])

    def test_names_every_action_and_criterion(self):
        text = AGENT.read_text(encoding="utf-8")
        for word in (
            "`keep`",
            "`fix`",
            "`trim`",
            "`delete`",
            "`merge`",
            "`move`",
            "`ask`",
            "`split`",
            "`accurate`",
            "`valuable`",
            "`placement`",
            "`brief`",
            "`level`",
            "`rename_only`",
            "`target_kind`",
        ):
            self.assertIn(word, text)

    def test_doubt_asks(self):
        self.assertIn("never `delete`", AGENT.read_text(encoding="utf-8"))

    def test_names_every_input(self):
        text = AGENT.read_text(encoding="utf-8")
        for key in (
            "REPO",
            "SCRIPT",
            "FILE",
            "ALIASES",
            "ROLE",
            "SCOPE",
            "GUIDELINES",
            "POLICY",
            "RULINGS",
            "AGENTS_MD_DIRS",
            "DOCS",
            "BYTES",
            "OVER",
        ):
            self.assertIn(f"`{key}`", text)
        self.assertIn("python3 '<SCRIPT>' blocks --repo '<REPO>' '<FILE>'", text)

    def test_names_every_policy_answer(self):
        text = AGENT.read_text(encoding="utf-8")
        for answer in (
            "allowed",
            "ask each time",
            "comment in that file",
            "nearest AGENTS.md",
            "aggressive",
            "conservative",
        ):
            self.assertIn(f"`{answer}`", text)

    def test_example_json_matches_schema(self):
        # Every ```json block in the agent file parses, and each verdict's action is a known action.
        blocks = fences("json")
        self.assertTrue(blocks)
        actions = set()
        for body in blocks:
            data = json.loads(body)
            for verdict in data["verdicts"]:
                self.assertIn(verdict["action"], inv.ACTIONS)
                if "criterion" in verdict:
                    self.assertIn(verdict["criterion"], inv.CRITERIA)
                for item in (verdict, *verdict.get("parts", [])):
                    if "target_kind" in item:
                        self.assertIn(item["target_kind"], inv.TARGET_KINDS)
                actions.add(verdict["action"])
        self.assertEqual(actions, set(inv.ACTIONS))

    def test_schema_table_lists_the_fields_the_validator_allows(self):
        # The agent is told the skill rejects any field an action doesn't take, so its table and the script agree.
        text = AGENT.read_text(encoding="utf-8")
        self.assertIn("Leave out any field the action doesn't take: the skill rejects them", text)
        section = text[text.index("| field | rule |") :]
        fields = set()
        for row in section.splitlines()[2:]:
            if not row.startswith("|"):
                break
            fields.update(re.findall(r"`(\w+)`", row.split("|")[1]))
        allowed = set().union(inv.ALWAYS_KEYS, *inv.ACTION_KEYS.values())
        self.assertEqual(fields, allowed)
        self.assertEqual(set(inv.ACTION_KEYS), set(inv.ACTIONS))

    def test_example_passes_the_validator(self):
        # The worked example's file and verdicts pass check-verdicts, so the example can't teach a bad shape.
        (sample,) = fences("markdown")
        (example,) = fences("json")
        data = json.loads(example)
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(Path(tmp))
            write(repo, data["file"], sample + "\n")
            for verdict in data["verdicts"]:
                for item in (verdict, *verdict.get("parts", [])):
                    if item.get("target_kind") == "comment":
                        write(repo, item["target"], "// placeholder\n")
                    if item.get("target_kind") == "doc":
                        write(repo, item["target"], "# Placeholder\n")
            commit_all(repo, "add")
            message = Path(tmp) / "message.txt"
            message.write_text("Summary.\n\n```json\n" + example + "\n```\n", encoding="utf-8")
            code, out, err = call(
                "check-verdicts",
                "--repo",
                str(repo),
                "--file",
                data["file"],
                "--role",
                "full",
                "--input",
                str(message),
            )
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["verdicts"], data["verdicts"])

    def test_placement_criterion_and_order(self):
        text = AGENT.read_text(encoding="utf-8")
        order = [text.index(h) for h in ("### Accurate", "### Valuable", "### Placement", "### Brief", "### Level")]
        self.assertEqual(order, sorted(order))
        placement = text[text.index("### Placement") : text.index("### Brief")]
        for phrase in (
            "describes the project rather than directs the agent",
            "only for some tasks",
            "`target_kind: doc`",
            "`pointer`",
            "never a second copy",
            "`docs/<name>.md`",
            "every clause",
        ):
            self.assertIn(phrase, placement)

    def test_split_and_limits_are_described(self):
        text = AGENT.read_text(encoding="utf-8")
        brief = text[text.index("### Brief") : text.index("### Level")]
        for phrase in ("`split`", "`parts`", "one rule each"):
            self.assertIn(phrase, brief)
        for phrase in ("size limits:", "90%", "never drop a clause", "`whole`", "`BYTES`", "`OVER`", "`DOCS`"):
            self.assertIn(phrase, text)

    def test_whole_keep_ruling_carries_the_token(self):
        text = AGENT.read_text(encoding="utf-8")
        rulings = text[text.index("## Rulings") : text.index("## Ancestor role")]
        self.assertIn("`<quote> (whole)`", rulings)
        inputs = text[text.index("## Inputs") : text.index("## Start")]
        self.assertIn("last clause", inputs)
        self.assertNotIn("a last line", inputs)


class SkillMdTest(unittest.TestCase):
    def test_frontmatter(self):
        meta = parse_frontmatter(SKILL)
        self.assertEqual(meta["name"], "agents-md-cleanup")
        self.assertEqual(meta["disable-model-invocation"], "true")
        self.assertIn("/maintenance:agents-md-cleanup", meta["description"])
        self.assertIn("manual", meta["description"].lower())

    def test_no_dollar_digit(self):
        self.assertIsNone(re.search(r"\$\d", SKILL.read_text(encoding="utf-8")))

    def test_script_calls(self):
        text = SKILL.read_text(encoding="utf-8")
        prefixes = re.findall(r"(\S*)agents_md_inventory\.py", text)
        self.assertTrue(prefixes)
        for p in prefixes:
            self.assertEqual(p, '"${CLAUDE_SKILL_DIR}/scripts/')
        for cmd in re.findall(r"agents_md_inventory\.py\"?\s+([\w-]+)", text):
            self.assertIn(cmd, {"inventory", "blocks", "check-verdicts"})

    def test_dispatch_and_fallback(self):
        text = SKILL.read_text(encoding="utf-8")
        self.assertIn("`maintenance:agents-md-reviewer`", text)
        self.assertIn("${CLAUDE_SKILL_DIR}/../../agents/agents-md-reviewer.md", text)
        self.assertTrue((SKILL.parent / ".." / ".." / "agents" / "agents-md-reviewer.md").resolve().is_file())

    def test_confirm_before_dispatch(self):
        text = SKILL.read_text(encoding="utf-8")
        self.assertLess(text.index("## 2. Confirm before dispatch"), text.index("## 3. Dispatch"))
        self.assertIn("Stop until the user answers", text)

    def test_policy_options(self):
        text = SKILL.read_text(encoding="utf-8")
        for opt in (
            "`allowed`",
            "`ask each time`",
            "`comment in that file`",
            "`nearest AGENTS.md`",
            "`aggressive`",
            "`conservative`",
        ):
            self.assertIn(opt, text)

    def test_rulings_path_fixed(self):
        self.assertIn(".claude/agents-md-cleanup.md", SKILL.read_text(encoding="utf-8"))

    def test_rulings_gitignore_draft_handles_rules_outside_the_repo(self):
        # A rule in a global excludes file or .git/info/exclude can't be edited from the repo, and a bare
        # negation can't re-include a file under a directory it excludes.
        text = SKILL.read_text(encoding="utf-8")
        bullet = text[text.index("- **Rulings in git.**") : text.index("## 5. Sort")]
        self.assertIn("never edit that file", bullet)
        for line in ("`!/.claude/`", "`/.claude/*`", "`!/.claude/agents-md-cleanup.md`"):
            self.assertIn(line, bullet)

    def test_rulings_gitignore_draft_never_replaces_an_in_repo_rule(self):
        # Replacing a broad rule such as `.*` would un-ignore `.env` and the rest of what it matched.
        text = SKILL.read_text(encoding="utf-8")
        bullet = text[text.index("- **Rulings in git.**") : text.index("## 5. Sort")]
        self.assertIn("Never replace or edit the matching rule", bullet)
        self.assertIn("`git check-ignore -q --no-index .claude/`", bullet)
        self.assertIn("`!/agents-md-cleanup.md`", bullet)
        for stale in ("in its place", "`**/.claude/*`"):
            self.assertNotIn(stale, bullet)

    def test_proposed_verdicts_are_described_as_checked(self):
        # check-verdicts validates an ask's proposed verdict, so both files may say so, and must say how it applies.
        text = SKILL.read_text(encoding="utf-8")
        self.assertIn("applies its `proposed` verdict to the ask's blocks", text)
        self.assertIn("`proposed` moves included", text[text.index("## 7. Apply") :])
        (row,) = [line for line in AGENT.read_text(encoding="utf-8").splitlines() if line.startswith("| `proposed` |")]
        for phrase in ("Any action but `ask`", "checked by the same rules", "only a `move` into `SCOPE`"):
            self.assertIn(phrase, row)

    def test_uncommitted_work_stops_the_run_for_a_question(self):
        text = SKILL.read_text(encoding="utf-8")
        confirm = text[text.index("## 2. Confirm before dispatch") : text.index("## 3. Dispatch")]
        self.assertIn("`worktree_clean` is true", confirm)  # in the condition for skipping the step
        (first,) = [line for line in confirm.splitlines() if line.startswith("- ") and "worktree_clean" in line]
        self.assertEqual(first, [line for line in confirm.splitlines() if line.startswith("- ")][0])
        for phrase in ("strongly recommends", "clean branch", "proceed"):
            self.assertIn(phrase, first)

    def test_untracked_files_are_dirty_and_their_question_says_so(self):
        text = SKILL.read_text(encoding="utf-8")
        merge = text[text.index("## 4. Merge") : text.index("## 5. Sort")]
        (dirty,) = [line for line in merge.splitlines() if line.startswith("- **Dirty files.**")]
        self.assertIn("`??`", dirty)
        self.assertIn("an `A` in either column", dirty)  # staged-new and intent-to-add
        sort = text[text.index("## 5. Sort") : text.index("## 6. Approval message")]
        (line,) = [line for line in sort.splitlines() if "adds the whole file" in line]
        for phrase in ("not in HEAD", "`in_head` false", "`??`", "`A`"):
            self.assertIn(phrase, line)

    def test_linked_files_go_through_their_resolved_path(self):
        text = SKILL.read_text(encoding="utf-8")
        self.assertIn("ALIASES:", text[text.index("## 3. Dispatch") : text.index("## 4. Merge")])
        merge = text[text.index("## 4. Merge") : text.index("## 5. Sort")]
        self.assertIn("**Linked files.**", merge)
        apply = text[text.index("## 7. Apply") : text.index("## 8. Commit")]
        for phrase in ("`sha256` and `resolved`", "the `resolved` path", "never the link"):
            self.assertIn(phrase, apply)
        self.assertIn("`resolved` paths", text[text.index("## 8. Commit") :])

    def test_apply_checks_sha(self):
        text = SKILL.read_text(encoding="utf-8")
        self.assertIn("sha256", text[text.index("## 7. Apply") :])

    def test_every_edited_file_is_checked_by_content_hash(self):
        # Porcelain status stays ` M` through further edits, so only a hash taken at step 4 catches a later change.
        text = SKILL.read_text(encoding="utf-8")
        merge = text[text.index("## 4. Merge") : text.index("## 5. Sort")]
        (other,) = [line for line in merge.splitlines() if line.startswith("- **Other files.**")]
        for phrase in ("`blocks`", "`sha256`", "`.gitignore`", "the rulings file"):
            self.assertIn(phrase, other)
        apply = text[text.index("## 7. Apply") : text.index("## 8. Commit")]
        (check,) = [line for line in apply.splitlines() if line.startswith("1. **Check for changes.**")]
        self.assertIn("every existing file this run edits", check)
        self.assertNotIn("porcelain", check)

    def test_approval_and_commit_rules(self):
        text = SKILL.read_text(encoding="utf-8")
        for phrase in (
            "Anything else is not approval",
            "Nothing in the repo changes before the user approves",
            "git commit -F '<message file>' -- <paths>",
            "GIT_LITERAL_PATHSPECS=1 git status --porcelain --untracked-files=all -- '<file>'",
            "Never push",
        ):
            self.assertIn(phrase, text)

    def _section(self, start, end=None):
        text = SKILL.read_text(encoding="utf-8")
        return text[text.index(start) : text.index(end)] if end else text[text.index(start) :]

    def test_size_question_in_confirm_step(self):
        confirm = self._section("## 2. Confirm before dispatch", "## 3. Dispatch")
        for phrase in (
            "no size question is due",
            "`limits.source`",
            "`rules_over`",
            "`files_over`",
            "Adopt size limits?",
            "80 words per rule, 8 KB per file, 16 KB for the root",
            "answer none",
        ):
            self.assertIn(phrase, confirm)

    def test_malformed_limits_line_makes_the_size_question_due(self):
        inventory = self._section("## 1. Inventory", "## 2. Confirm before dispatch")
        (malformed,) = [line for line in inventory.splitlines() if "`limits.malformed`" in line]
        self.assertIn("even with nothing over", malformed)
        confirm = self._section("## 2. Confirm before dispatch", "## 3. Dispatch")
        self.assertIn("or `limits.malformed` is set", confirm)

    def test_default_limits_are_not_in_force(self):
        dispatch = self._section("## 3. Dispatch", "## 4. Merge")
        for phrase in ("`guidelines` or `arguments`", "is `default`", "no `--rule-words`"):
            self.assertIn(phrase, dispatch)

    def test_size_test_ports_the_limits_module(self):
        for section in (self._section("**The test question", "**Test maintenance"), self._section("## 7. Apply")):
            for phrase in (
                '"${CLAUDE_SKILL_DIR}/scripts/agents_md_limits.py"',
                "`measure_rules`",
                "`normalised_bytes`",
            ):
                self.assertIn(phrase, section)
        question = self._section("**The test question", "**Test maintenance")
        for phrase in ("at any depth", "marker excluded", "whitespace tokens", "HTML comments", "front matter"):
            self.assertIn(phrase, question)

    def test_limits_line_grammar_in_skill(self):
        text = SKILL.read_text(encoding="utf-8")
        for line in ("Size limits: none.", "; checked by ", "; not checked by a test."):
            self.assertIn(line, text)

    def test_dispatch_carries_docs_bytes_over_and_limits(self):
        dispatch = self._section("## 3. Dispatch", "## 4. Merge")
        for phrase in ("DOCS:", "BYTES:", "OVER:", "size limits:", "--rule-words"):
            self.assertIn(phrase, dispatch)

    def test_merge_reconciles_repoints_and_backstops(self):
        merge = self._section("## 4. Merge", "## 5. Sort")
        for bullet in ("- **Reconcile destinations.**", "- **Re-point references.**", "- **Size accounting.**"):
            self.assertIn(bullet, merge)
        (size,) = [line for line in merge.splitlines() if line.startswith("- **Size accounting.**")]
        for phrase in ("at most once per file", "file-level", "exception list", "leaving those moves out"):
            self.assertIn(phrase, size)
        (refs,) = [line for line in merge.splitlines() if line.startswith("- **Re-point references.**")]
        for phrase in ("`references`", "backticked", "`truncated`"):
            self.assertIn(phrase, refs)

    def test_approval_message_sections(self):
        approval = self._section("## 6. Approval message", "## 7. Apply")
        for phrase in (
            "**Documentation moves",
            "**Splits",
            "**Size",
            "**The limits line",
            "**The test question",
            "**Test maintenance",
            "boundary cases",
            "exception list",
            "not checked by a test",
        ):
            self.assertIn(phrase, approval)

    def test_apply_writes_docs_and_runs_only_the_new_test(self):
        apply = self._section("## 7. Apply", "## 8. Commit")
        for phrase in (
            "`# <title>`",
            "`pointer`",
            "`parts`",
            "never the whole suite",
            "`dotnet test --filter`",
            "nothing is committed",
            "uncommitted",
        ):
            self.assertIn(phrase, apply)
        self.assertIn("could not be run", self._section("## 8. Commit"))

    def test_script_calls_accept_size_flags(self):
        text = SKILL.read_text(encoding="utf-8")
        for flag in ("--rule-words", "--file-bytes", "--root-bytes", "--no-size-limits"):
            self.assertIn(flag, text[text.index("## The script") : text.index("## 1. Inventory")])

    def test_manifest_descriptions_match(self):
        repo = PLUGIN.parent.parent
        plugin = json.loads((PLUGIN / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
        market = json.loads((repo / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
        entry = next(p for p in market["plugins"] if p["name"] == "maintenance")
        self.assertEqual(entry["description"], plugin["description"])
        self.assertIn("agents-md-cleanup", plugin["description"])
        readme = (repo / "README.md").read_text(encoding="utf-8")
        line = next(x for x in readme.splitlines() if x.startswith("- [`maintenance`]"))
        for skill in ("superpowers-cleanup", "agents-md-cleanup"):
            self.assertIn(f"`{skill}`", line)
        self.assertRegex(plugin["version"], r"^\d+\.\d+\.\d+$")


def shell_quote(value: str) -> str:
    """Single-quote a value the way SKILL.md and the agent file say: a ' inside it becomes '\\''."""
    return "'" + value.replace("'", "'\\''") + "'"


def fill(command: str, values: dict) -> str:
    """Replace each '<name>' in a documented command with the shell-quoted value."""
    for name, value in values.items():
        command = command.replace(f"'<{name}>'", shell_quote(value))
    assert "<" not in command, command
    return command


class RulingsGitignoreDraftTest(unittest.TestCase):
    """The `.gitignore` lines step 4 drafts re-include the rulings file and leave everything else as it was."""

    RULINGS = ".claude/agents-md-cleanup.md"
    OTHERS = (".env", ".cache/x", ".claude/settings.json", "x/.claude/y", "src/notes.md", "src/a.py")

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = make_repo(Path(self._tmp.name))
        for path in (self.RULINGS, *self.OTHERS):
            write(self.repo, path, "x\n")

    def ignored(self, path: str, *flags: str) -> bool:
        result = subprocess.run(["git", "check-ignore", "-q", *flags, path], cwd=str(self.repo))
        self.assertIn(result.returncode, (0, 1))
        return result.returncode == 0

    def draft(self) -> None:
        """Step 4's draft: keep the matching rule, and add the lines its case needs right after it."""
        found = subprocess.run(
            ["git", "check-ignore", "-v", self.RULINGS], cwd=str(self.repo), capture_output=True, text=True
        ).stdout
        source, line, _ = found.split("\t")[0].split(":", 2)
        if source == ".claude/.gitignore":
            added = ["!/agents-md-cleanup.md"]
        elif self.ignored(".claude/", "--no-index"):
            added = ["!/.claude/", "/.claude/*", "!/.claude/agents-md-cleanup.md"]
        else:
            added = ["!/.claude/agents-md-cleanup.md"]
        path = self.repo / source
        lines = path.read_text(encoding="utf-8").splitlines()
        lines[int(line) : int(line)] = added
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def test_each_rule_keeps_ignoring_what_it_ignored(self):
        for rule, excludes_dir in [
            (".*", True),
            (".c*", True),
            (".claude/", True),
            ("/.claude/", True),
            (".claude", True),
            # A rule that ignores everything in .claude/ counts too; the three lines then ignore what it did.
            (".claude/*", True),
            (".claude/**", True),
            ("*", True),
            ("*.md", False),
            ("agents-md-cleanup.md", False),
        ]:
            with self.subTest(rule=rule):
                write(self.repo, ".gitignore", f"{rule}\n!.gitignore\n")
                (self.repo / ".claude" / ".gitignore").unlink(missing_ok=True)
                self.assertTrue(self.ignored(self.RULINGS))
                self.assertEqual(self.ignored(".claude/", "--no-index"), excludes_dir)
                before = {path: self.ignored(path) for path in self.OTHERS}
                self.draft()
                self.assertFalse(self.ignored(self.RULINGS))
                self.assertEqual({path: self.ignored(path) for path in self.OTHERS}, before)
                self.assertEqual(
                    (self.repo / ".gitignore").read_text(encoding="utf-8").splitlines()[0], rule, "the rule is kept"
                )

    def test_a_rule_in_the_claude_folder(self):
        write(self.repo, ".claude/.gitignore", "*\n!.gitignore\n")
        self.assertTrue(self.ignored(self.RULINGS))
        self.draft()
        self.assertFalse(self.ignored(self.RULINGS))
        self.assertTrue(self.ignored(".claude/settings.json"))

    def test_tracked_files_under_claude_do_not_hide_the_directory_rule(self):
        # Without --no-index, check-ignore reports a directory holding tracked files as not ignored.
        write(self.repo, ".gitignore", ".*\n!.gitignore\n")
        commit_all(self.repo, "ignore")
        subprocess.run(["git", "add", "-f", ".claude/settings.json"], cwd=str(self.repo), check=True)
        self.assertFalse(self.ignored(".claude"))
        self.assertTrue(self.ignored(".claude/", "--no-index"))
        self.draft()
        self.assertFalse(self.ignored(self.RULINGS))
        self.assertTrue(self.ignored(".env"))


class QuotedPathsTest(unittest.TestCase):
    """Paths with spaces and quotes survive every documented shell command and dispatch input."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name).resolve()
        self.repo = make_repo(self.tmp / "my 'odd' repo")
        write(self.repo, "AGENTS.md", "- root\n")
        write(self.repo, "my docs/AGENTS.md", "- one\n- two\n")
        commit_all(self.repo, "add")
        self.skill_dir = self.tmp / "plugin cache" / "agents-md-cleanup"
        (self.skill_dir / "scripts").mkdir(parents=True)
        shutil.copy(inv.__file__, self.skill_dir / "scripts" / "agents_md_inventory.py")
        shutil.copy(agents_md_limits.__file__, self.skill_dir / "scripts" / "agents_md_limits.py")

    def bash(self, command: str) -> dict:
        env = {**os.environ, "CLAUDE_SKILL_DIR": str(self.skill_dir)}
        result = subprocess.run(["bash", "-c", command], capture_output=True, text=True, env=env, cwd=str(self.tmp))
        self.assertEqual(result.returncode, 0, f"{command}\n{result.stderr}")
        return json.loads(result.stdout)

    def test_skill_script_commands(self):
        text = SKILL.read_text(encoding="utf-8")
        lines = {
            m.group(1): m.group(0) for m in re.finditer(r"^python3 \S+agents_md_inventory\.py\" (\S+) .*$", text, re.M)
        }
        self.assertEqual(set(lines), {"inventory", "blocks", "check-verdicts"})
        repo = {"repo root": str(self.repo)}
        inventory = lines["inventory"].replace(" [--scope '<dir>' ...]", " --scope '<dir>'")
        inventory = inventory.replace(" [--base '<ref>']", "")
        # Run each documented optional flag with a real value; --no-size-limits can't join the numbers, so runs alone.
        self.assertIn(" [--no-size-limits]", inventory)
        sized = re.sub(r" \[(--[\w-]+) N\]", r" \1 81", inventory.replace(" [--no-size-limits]", ""))
        self.assertNotIn("[", sized)
        out = self.bash(fill(sized, {**repo, "dir": "my docs"}))
        self.assertEqual(out["limits"]["source"], "arguments")
        self.assertEqual(
            [out["limits"][k] for k in ("rule_words", "file_bytes", "root_bytes")], [81, 81, 81], "every flag reached"
        )
        unsized = re.sub(r" \[--[\w-]+ N\]", "", inventory).replace(" [--no-size-limits]", " --no-size-limits")
        self.assertEqual(self.bash(fill(unsized, {**repo, "dir": "my docs"}))["limits"]["source"], "none")
        self.assertEqual(out["scope"], ["my docs"])
        self.assertEqual([f["path"] for f in out["files"]], ["AGENTS.md", "my docs/AGENTS.md"])
        out = self.bash(fill(lines["blocks"], {**repo, "path": "my docs/AGENTS.md"}))
        self.assertEqual(len(out["blocks"]), 2)
        message = self.tmp / "tmp dir" / "1.md"
        message.parent.mkdir()
        payload = {"file": "my docs/AGENTS.md", "verdicts": [{"blocks": ["b1", "b2"], "action": "keep"}]}
        message.write_text("```json\n" + json.dumps(payload) + "\n```\n", encoding="utf-8")
        check = lines["check-verdicts"].replace("full|ancestor", "full").replace(" [--scope '<dir>' ...]", "")
        self.assertIn(" [--rule-words N]", check)
        check = check.replace(" [--rule-words N]", " --rule-words 80")
        out = self.bash(fill(check, {**repo, "path": "my docs/AGENTS.md", "message file": str(message)}))
        self.assertEqual(out["counts"], {"keep": 1})

    def test_measurement_command(self):
        # The documented measurement runs on a quoted path and applies the script's definitions, CRLF included.
        text = SKILL.read_text(encoding="utf-8")
        (command,) = re.findall(r"`(python3 -c 'import json, sys;[^`]*)`", text)
        sample = self.tmp / "size 'x' dir" / "AGENTS.md"
        sample.parent.mkdir()
        raw = ("# T\r\n\r\n- " + " ".join(["w"] * 81) + "\r\n").encode("utf-8")
        sample.write_bytes(raw)
        out = self.bash(fill(command, {"text file": str(sample), "word limit": "80", "byte limit": "8192"}))
        self.assertEqual(out["bytes_normalised"], len(raw.replace(b"\r\n", b"\n")))
        self.assertEqual(out["longest"], 81)
        self.assertEqual([r["words"] for r in out["over"]["rules"]], [81])
        self.assertFalse(out["over"]["file"])

    def test_reviewer_start_command(self):
        agent = AGENT.read_text(encoding="utf-8")
        (command,) = re.findall(r"`(cd '<REPO>' && python3 '<SCRIPT>' blocks [^`]*)`", agent)
        values = {
            "REPO": str(self.repo),
            "SCRIPT": str(self.skill_dir / "scripts" / "agents_md_inventory.py"),
            "FILE": "my docs/AGENTS.md",
        }
        self.assertEqual(self.bash(fill(command, values))["path"], "my docs/AGENTS.md")

    def test_list_inputs_are_json_arrays_in_both_files(self):
        skill = SKILL.read_text(encoding="utf-8")
        template = skill[skill.index("Review this AGENTS.md file") : skill.index("Keep each reviewer's agent id")]
        agent = AGENT.read_text(encoding="utf-8")
        inputs = agent[agent.index("## Inputs") : agent.index("## Start")]
        for key in ("ALIASES", "SCOPE", "AGENTS_MD_DIRS"):
            with self.subTest(key=key):
                (line,) = [x for x in template.splitlines() if x.startswith(f"{key}: ")]
                self.assertIn("JSON array", line)
                (item,) = [x for x in inputs.splitlines() if x.startswith(f"- `{key}`")]
                self.assertIn("JSON array", item)
        for text in (skill, agent):
            self.assertNotIn("space-separated", text)
        self.assertEqual(
            set(re.findall(r"^([A-Z_]+):", template, re.M)), set(re.findall(r"^- `([A-Z_]+)`", inputs, re.M))
        )

    def test_every_placeholder_in_a_shell_command_is_quoted(self):
        # A path placeholder written bare into a command splits on a space.
        for path in (SKILL, AGENT):
            text = path.read_text(encoding="utf-8")
            for command in re.findall(r"`((?:cd|python3|git|GIT_LITERAL_PATHSPECS=1|rm) [^`]*)`", text):
                with self.subTest(file=path.name, command=command):
                    bare = re.sub(r"'<[^'>]+>'|\[--scope '<dir>' \.\.\.\]|<paths>|full\|ancestor", "", command)
                    self.assertNotRegex(bare, r"<[\w ]+>")


if __name__ == "__main__":
    unittest.main()
