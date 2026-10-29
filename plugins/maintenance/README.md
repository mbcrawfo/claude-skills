# maintenance

Skills that keep a repo clean. This plugin has two skills: `superpowers-cleanup` and `agents-md-cleanup`.

## Requirements

You must have `python3` 3.9 or later.

## Install

```
/plugin marketplace add mbcrawfo/claude-skills
/plugin install maintenance@mbcrawfo-skills
```

## superpowers-cleanup

Removes old [superpowers](https://github.com/obra/superpowers) specs, plans and research files from a repo. Before it removes a file, it corrects each reference to that file. Thus, no comment or document points to a missing file, and the code does not lose a fact that it needs.

### Use

The skill runs only when you start it. Claude does not start it automatically. Tell it in plain words what to remove:

```
/maintenance:superpowers-cleanup specs older than two weeks
/maintenance:superpowers-cleanup specs created on 2026-10-22
/maintenance:superpowers-cleanup specs about the foobar epic
```

What it does:

- **Removes related files together.** When it removes a spec, it also removes the plans made from that spec. It finds them from the `**Spec:**` line of each plan. It also removes research that only the removed specs refer to.
- **Keeps unfinished work.** A file that is not on the main branch yet is kept until you confirm. A file whose plan names code that does not exist is also kept until you confirm.
- **Corrects references.** Each reference to a removed file gets one of three corrections:
  - It writes the necessary fact in the text.
  - It removes the reference.
  - It asks you.
- **Asks for one approval.** One message shows the open questions and a table of each removal and each reference correction. You answer and approve in one reply. It changes nothing before your approval.
- **Commits, but does not push.** It makes one commit, with only its own changes.

If you do not tell it what to remove, it shows a summary of the specs, plans and research. Then it asks what to remove.

### Repo policy

Specs and plans often record one point in time. Thus, a spec that you keep can correctly link to a spec that you remove. Write the rule for your repo in `AGENTS.md`, for example:

> Specs and plans under docs/superpowers are point-in-time records; links between them may go stale when old ones are removed.

If your repo has no rule, the skill asks one time in each run. It also offers to add the line for you.

### Folders

By default, it looks in `docs/superpowers/specs`, `docs/superpowers/plans` and `docs/superpowers/research`. If your `AGENTS.md` or `CLAUDE.md` gives different folders, it uses those folders.

## agents-md-cleanup

Examines each statement in the `AGENTS.md` files of a repo. Each statement must be accurate, useful, short, and at the deepest level where it applies. A useful statement tells an agent something that is not obvious. A statement that fails has one of these results:

- It moves down to a nested `AGENTS.md` file or to a code comment.
- It moves out to the docs of the repo.
- It becomes shorter.
- It is removed.

The skill can also keep rules and files in size limits that you choose.

### Use

The skill runs only when you start it. Claude does not start it automatically. To examine only some directories, give their names. You can also tell it how many reviewer agents can run at the same time:

```
/maintenance:agents-md-cleanup
/maintenance:agents-md-cleanup tests/
/maintenance:agents-md-cleanup 4 at a time
```

What it does:

- **Asks before it starts the reviewers.** It shows the number of files and blocks, and a limit for reviewers that run at the same time. The default limit is 4. It waits for your answer. On a later run, if you give a limit (`4 at a time`), it shows the numbers and starts.
- **Recommends a clean branch.** If the repo has uncommitted work or untracked files, it tells you first. It strongly recommends a clean branch and asks if you want to continue. It asks this also on a later run with a limit. If you continue, it asks you about each change to an uncommitted file. For a file that is not committed yet, the question tells you that the commit adds the full file.
- **Asks three policy questions on the first run:**
  - Can it make new nested `AGENTS.md` files?
  - Does a fact about one file go in a comment in that file, or in the nearest `AGENTS.md`?
  - Does it move statements down aggressively or conservatively? Aggressive moves each statement that applies to one subdirectory. Conservative moves only statements that name specific files or folders.
- **Records your answers.** It writes them in a short `AGENTS.md guidelines` section in the root `AGENTS.md`. Thus, later runs and your other agents use the same rules. The approval message shows the section, and the skill writes it only after you approve. If there is no root file, it offers to make one. If you do not accept, the answers apply only to that run.
- **Keeps project choices.** A statement such as "use `uv`, never pip" can look obvious. But it is a choice between real alternatives. Thus, the skill keeps it or asks you. It never removes such statements as a group.
- **Remembers your decisions.** Sometimes you keep a statement that the reviewers doubted. The skill records that decision in `.claude/agents-md-cleanup.md`, so the next run does not ask again. If git ignores `.claude/`, the approval message shows the `.gitignore` change that lets you commit and share the file. If you do not accept the change, the file stays only on your computer.
- **Asks for one approval.** One message shows the questions and a table of each change that it proposes. It changes nothing before you approve. If you edit a file while it waits, it skips that file.
- **Makes one rule for each bullet.** It divides a bullet that has many statements. Then it examines and moves each statement separately.
- **Moves documentation out.** Descriptions, procedures, reference material and explanations are documentation, not rules. Each one goes to the best page and heading in the docs of the repo. If no page is correct, it goes to a new file in the docs folder. If the statement gave an instruction to the agent, a one-line pointer stays in its place. The approval message shows each destination. Nothing moves until you approve.
- **Measures sizes and offers limits.** It counts the words in each rule and the bytes in each file. It warns about a rule over 80 words, a file over 8 KB, and a root file over 16 KB. On the first run that finds a file or rule over these values, it asks if you want limits. It recommends these three limits. You can give different numbers or `none`. You can also give them as arguments: `100 words`, `10 KB`, `20 KB root` or `no size limits`. It records the answer as one `Size limits:` line in the guidelines section. Later runs keep the files in these limits and tell you when a file is near its limit.
- **Adds a test only if you want one.** When you accept limits, it asks if you want a test for them. The test uses the test style of your repo. It checks each limit at its boundary. It has a list of the rules and files that you chose to keep over a limit. The skill runs the test alone before the commit. If the test cannot run, the skill commits the edits without the test and leaves the test file uncommitted for you. You can refuse the test.
- **Commits, but does not push.** It makes one commit, with only its own changes.

The `Size limits:` line has one of these three forms:

```
Size limits: none.
Size limits: 80 words per rule, 8 KB per file, 16 KB for the root; checked by tests/test_agents_md.py.
Size limits: 80 words per rule, 8 KB per file, 16 KB for the root; not checked by a test.
```

On a run for some directories only, such as `tests/`, the skill can change ancestor files, for example the root file. In those files, it only moves statements and asks questions. It does not change their other rules.

### Assumption

A rule in a nested `AGENTS.md` file has an effect only if your tool loads that file. The tool must load nested `AGENTS.md` files when it works in their directory. The skill assumes that your tool does this.
