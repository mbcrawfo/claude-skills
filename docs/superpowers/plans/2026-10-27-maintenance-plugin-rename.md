# Maintenance Plugin Rename Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rename the `superpowers-cleanup` plugin to `maintenance`, rename its `cleanup` skill to `superpowers-cleanup`, and move the skill's script into the skill folder.

**Architecture:** Pure restructuring with `git mv`; no behavior change. Task 1 moves the files and fixes every path the tests, eval and SKILL.md depend on, guarded by updated tests. Task 2 renames the plugin in the manifests and docs and runs the full repo checks.

**Tech Stack:** Claude Code plugin manifests (JSON), Markdown skills, Python 3.9+ stdlib `unittest`, ruff.

**Spec:** `docs/superpowers/specs/2026-10-27-maintenance-plugin-rename-design.md`

## Global Constraints

- Plugin name `maintenance`; skill name `superpowers-cleanup`; command `/maintenance:superpowers-cleanup`.
- Plugin version `0.2.0`.
- Script location `plugins/maintenance/skills/superpowers-cleanup/scripts/docs_inventory.py`, called from SKILL.md as `${CLAUDE_SKILL_DIR}/scripts/docs_inventory.py`.
- Tests stay in `plugins/maintenance/tests/`; eval case is `plugins/maintenance/evals/superpowers-cleanup-run/`.
- Do not edit `docs/superpowers/specs/2026-10-24-superpowers-cleanup-design.md` or `docs/superpowers/plans/2026-10-24-superpowers-cleanup.md`.
- Never write a dollar sign followed by a digit in SKILL.md (an existing test enforces this).
- Use `git mv` for every move so history follows the files.

## Review Focus

- **Script path in an installed copy:** `${CLAUDE_SKILL_DIR}` is the skill folder, so every call in SKILL.md must be exactly `${CLAUDE_SKILL_DIR}/scripts/docs_inventory.py`; a leftover `../../scripts` breaks every run. Pinned by `test_script_lives_in_skill` (Task 1).
- **Leftover plugin-level `scripts/` folder:** an empty or `__pycache__`-only `plugins/maintenance/scripts/` makes the old layout look live. Pinned by `test_script_lives_in_skill` (Task 1).
- **Old command left in a user-facing file:** a README or prompt still saying `/superpowers-cleanup:cleanup` sends users to a command that no longer exists. Pinned by the grep in Task 2 Step 4.
- **Existing installs:** `superpowers-cleanup@mbcrawfo-skills` will not update into `maintenance`; the plugin README must tell users to uninstall the old name and install the new. Checked in Task 2 Step 1.
- **Eval resolving the plugin:** `case.yaml`'s `plugins: ["../.."]` must still point at the plugin root after the folder rename (depth is unchanged). Optional eval run in Task 2 Step 6.

---

### Task 1: Move the skill and script, fix every path

**Files:**
- Move: `plugins/superpowers-cleanup/` → `plugins/maintenance/`
- Move: `plugins/maintenance/skills/cleanup/` → `plugins/maintenance/skills/superpowers-cleanup/`
- Move: `plugins/maintenance/scripts/docs_inventory.py` → `plugins/maintenance/skills/superpowers-cleanup/scripts/docs_inventory.py`
- Move: `plugins/maintenance/evals/cleanup-run/` → `plugins/maintenance/evals/superpowers-cleanup-run/`
- Modify: `plugins/maintenance/skills/superpowers-cleanup/SKILL.md` (frontmatter `name`, `description`, the two script-call lines 21-22)
- Modify: `plugins/maintenance/skills/superpowers-cleanup/scripts/docs_inventory.py:4` (docstring)
- Modify: `plugins/maintenance/evals/superpowers-cleanup-run/case.yaml` (`name`)
- Modify: `plugins/maintenance/evals/superpowers-cleanup-run/prompt.md` (command)
- Modify: `plugins/maintenance/tests/helpers.py:18` (`SCRIPTS_DIR`)
- Modify: `plugins/maintenance/tests/test_eval_fixture.py:9` (`SCAFFOLD`)
- Test: `plugins/maintenance/tests/test_skill_md.py`

**Interfaces:**
- Produces: the final file layout above, which Task 2's docs and manifests describe.

- [ ] **Step 1: Move the plugin folder first, so test edits land at their final path**

```bash
git mv plugins/superpowers-cleanup plugins/maintenance
```

- [ ] **Step 2: Update `test_skill_md.py` to the new layout, and add the script-location test**

Set `SKILL = Path(__file__).resolve().parent.parent / "skills" / "superpowers-cleanup" / "SKILL.md"`, change the frontmatter assertion to `self.assertEqual(meta["name"], "superpowers-cleanup")`, and change `NothingChangedGraderTest.GRADER` to use `"superpowers-cleanup-run"`. Add to `SkillMdTest`:

```python
def test_names_the_new_command(self):
    meta = parse_frontmatter(SKILL)
    self.assertIn("/maintenance:superpowers-cleanup", meta["description"])


def test_script_lives_in_skill(self):
    self.assertTrue((SKILL.parent / "scripts" / "docs_inventory.py").is_file())
    self.assertFalse((SKILL.parent.parent.parent / "scripts").exists())
    text = SKILL.read_text(encoding="utf-8")
    calls = re.findall(r'"([^"]*docs_inventory\.py)"', text)
    self.assertTrue(calls)
    for call in calls:
        self.assertEqual(call, "${CLAUDE_SKILL_DIR}/scripts/docs_inventory.py")
```

- [ ] **Step 3: Run the tests to confirm they fail**

Run: `python3 -m unittest discover -s plugins/maintenance/tests`
Expected: errors and failures. `SkillMdTest` and `NothingChangedGraderTest` fail with `FileNotFoundError` on the new paths.

- [ ] **Step 4: Move the skill, script and eval folders**

```bash
git mv plugins/maintenance/skills/cleanup plugins/maintenance/skills/superpowers-cleanup
mkdir -p plugins/maintenance/skills/superpowers-cleanup/scripts
git mv plugins/maintenance/scripts/docs_inventory.py plugins/maintenance/skills/superpowers-cleanup/scripts/docs_inventory.py
rm -rf plugins/maintenance/scripts
git mv plugins/maintenance/evals/cleanup-run plugins/maintenance/evals/superpowers-cleanup-run
```

The `rm -rf` only clears an untracked `__pycache__`, if there is one. Check `git status` shows nothing tracked deleted there besides the moved script.

- [ ] **Step 5: Fix the paths and names in the moved files**

- `SKILL.md` frontmatter: `name: superpowers-cleanup`. In `description`, replace `/superpowers-cleanup:cleanup` with `/maintenance:superpowers-cleanup` and change nothing else in that sentence.
- `SKILL.md` lines 21-22: `"${CLAUDE_SKILL_DIR}/../../scripts/docs_inventory.py"` → `"${CLAUDE_SKILL_DIR}/scripts/docs_inventory.py"`.
- `docs_inventory.py` docstring line 4: `Used by the maintenance plugin's \`superpowers-cleanup\` skill. Every command prints one JSON` (rewrap the following line if ruff's 120-column limit needs it).
- `helpers.py`: `SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "skills" / "superpowers-cleanup" / "scripts"`.
- `test_eval_fixture.py`: `SCAFFOLD` path uses `"superpowers-cleanup-run"`.
- `case.yaml`: `name: superpowers-cleanup-run`.
- `prompt.md`: `/maintenance:superpowers-cleanup remove specs older than two weeks`.

- [ ] **Step 6: Run the tests to confirm they pass**

Run: `python3 -m unittest discover -s plugins/maintenance/tests`
Expected: `OK`, with the same test count as before plus the 2 new tests.

- [ ] **Step 7: Commit**

```bash
git add -A plugins/
git commit -m "refactor(maintenance): rename plugin dir, skill to superpowers-cleanup, move script into skill"
```

### Task 2: Rename the plugin in manifests and docs, run repo checks

**Files:**
- Modify: `plugins/maintenance/.claude-plugin/plugin.json`
- Modify: `.claude-plugin/marketplace.json:20-24`
- Modify: `plugins/maintenance/README.md`
- Modify: `README.md:20`

**Interfaces:**
- Consumes: the layout from Task 1.

- [ ] **Step 1: Update the manifests**

Use this exact description in `plugin.json` and the `marketplace.json` entry:

> Repo maintenance skills. superpowers-cleanup removes old superpowers spec, plan and research files on request, handling every reference to them before one reviewed commit.

- `plugin.json`: `"name": "maintenance"`, `"version": "0.2.0"`, the description above, `"keywords": ["maintenance", "superpowers", "cleanup", "docs"]`.
- `marketplace.json` entry: `"name": "maintenance"`, `"source": "./plugins/maintenance"`, the description above.

- [ ] **Step 2: Restructure the plugin README**

- Title `# maintenance`, with a one-paragraph intro: repo maintenance skills, one skill today.
- `## Install`: `/plugin install maintenance@mbcrawfo-skills`. Below it, an upgrade note: if `superpowers-cleanup@mbcrawfo-skills` is installed, uninstall it (`/plugin uninstall superpowers-cleanup@mbcrawfo-skills`) and install `maintenance`, since the old name no longer updates.
- `## superpowers-cleanup`: the current intro paragraph, then the current `Use`, `Repo policy` and `Folders` sections demoted to `###`. Usage examples become `/maintenance:superpowers-cleanup ...`.

- [ ] **Step 3: Update the root README's plugin list**

Replace the `superpowers-cleanup` line with:

```markdown
- [`maintenance`](plugins/maintenance/README.md): repo maintenance skills. `superpowers-cleanup` removes old superpowers spec, plan and research files on request, handling every reference to them before one reviewed commit.
```

- [ ] **Step 4: Check that no old name is left outside the old records**

Run: `git grep -nE "superpowers-cleanup:cleanup|skills/cleanup|plugins/superpowers-cleanup|(^|[^-])cleanup-run|\.\./\.\./scripts" -- . ':!docs/superpowers/specs/2026-10-24-*' ':!docs/superpowers/plans/2026-10-24-*' ':!docs/superpowers/specs/2026-10-27-*' ':!docs/superpowers/plans/2026-10-27-*'`
Expected: no output. Fix and re-run until it's empty.

- [ ] **Step 5: Run the repo checks**

```bash
python3 -m unittest discover -s plugins/maintenance/tests
ruff check .
ruff format --check .
claude plugin validate .
claude plugin validate plugins/maintenance
```

Expected: tests `OK`, ruff clean, both validations pass.

- [ ] **Step 6 (optional): Run the eval end to end**

Run per the repo's eval notes (login shell for the token): `zsh -lc 'cd plugins/maintenance && claude plugin eval evals/superpowers-cleanup-run --scaffold'` (adjust flags to the installed CLI's `--help`).
Expected: the `skill-ran` grader passes, showing the script path resolves from the skill folder.

- [ ] **Step 7: Commit**

```bash
git add .claude-plugin/marketplace.json README.md plugins/maintenance/.claude-plugin/plugin.json plugins/maintenance/README.md
git commit -m "feat(maintenance): rename superpowers-cleanup plugin to maintenance (0.2.0)"
```
