#!/usr/bin/env python3
"""State mechanics for the branch-review plugin.

Usage: review_state.py <command> [--state-dir PATH] [--repo PATH] ...

Every command prints one JSON object to stdout and exits 0. Usage and state
errors exit 1 with a one-line message on stderr. `prune` and `cleanup` keep
going past a directory they cannot remove: they still print their JSON object,
with the failures under `errors`, and then exit 1 with a one-line message.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import fnmatch
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

DEFAULT_STATE_DIR = "~/.claude/plugins/data/branch-review"
STATE_FILE = "review.json"
OPEN_STATUSES = ("open", "fixed", "still-open")
MAX_FILES = 150
MAX_LINES = 10000
LARGE_FILES = 40
LARGE_LINES = 2000
SNAPSHOT_MESSAGE = "branch-review snapshot"
FORCED_DEEP = ("snapshot-missing", "history-rewritten", "paths-changed")
SECOND_OPINION_FOCUS = "second-opinion"


def _read_plugin_version() -> str:
    manifest = Path(__file__).resolve().parent.parent / ".claude-plugin" / "plugin.json"
    try:
        version = json.loads(manifest.read_text(encoding="utf-8")).get("version")
    except (OSError, ValueError, AttributeError):
        return "unknown"
    return version if isinstance(version, str) and version else "unknown"


PLUGIN_VERSION = _read_plugin_version()
V09 = (0, 9, 0)


def _version_at_least(version: str | None, floor: tuple[int, int, int]) -> bool:
    """Whether `version` (`X.Y.Z`) is `floor` or later; false when it does not parse."""
    if not isinstance(version, str):
        return False
    parts = version.split(".")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        return False
    return tuple(int(p) for p in parts) >= floor


class StateError(Exception):
    """A usage or state problem that should be reported as a one-line error."""


class _Parser(argparse.ArgumentParser):
    """ArgumentParser whose usage errors follow the one-line, exit 1 contract."""

    def error(self, message):
        raise StateError(message)


# --- git helpers -----------------------------------------------------------


def _git(repo: Path, *args: str, env: dict | None = None) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=str(repo),
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _git_try(repo: Path, *args: str) -> str | None:
    """Run git, returning None instead of raising when it exits non-zero."""
    try:
        return _git(repo, *args)
    except subprocess.CalledProcessError:
        return None


# --- keys ------------------------------------------------------------------


def _repo_path_from_url(url: str) -> str | None:
    """The lowercased path of an ssh or https remote URL: `owner/repo`, `group/sub/repo`."""
    url = url.strip().rstrip("/")
    if url.endswith(".git"):
        url = url[: -len(".git")]
    if "://" in url:
        path = url.split("://", 1)[1].partition("/")[2]
    elif ":" in url:  # scp-like: git@github.com:Owner/Repo
        path = url.split(":", 1)[1]
    else:
        return None
    parts = [p.lower() for p in path.split("/") if p]
    if len(parts) < 2:
        return None
    return "/".join(parts)


def repo_key(repo: Path) -> str:
    """`owner__repo` (every path segment, joined by `__`) from the origin URL.

    Without a usable origin URL: `local-<hash of git common dir>`.
    """
    url = _git_try(repo, "config", "--get", "remote.origin.url")
    if url:
        repo_path = _repo_path_from_url(url)
        if repo_path:
            return re.sub(r"[^a-z0-9._/-]", "_", repo_path).replace("/", "__")
    common = _git(repo, "rev-parse", "--git-common-dir")
    common_dir = (Path(repo) / common).resolve()
    digest = hashlib.sha256(str(common_dir).encode("utf-8")).hexdigest()
    return "local-" + digest[:12]


def branch_key(repo: Path) -> tuple[str, str]:
    """`(branch_name, key)`; detached HEAD gives `("HEAD", "detached-<sha7>")`."""
    name = _git_try(repo, "symbolic-ref", "--short", "-q", "HEAD")
    if name:
        return name, name.replace("/", "__")
    # `--short=7` is a minimum length; the key is always exactly 7 characters.
    sha = _git(repo, "rev-parse", "HEAD")[:7]
    return "HEAD", "detached-" + sha


def default_base(repo: Path) -> str:
    """The `origin/HEAD` target (e.g. `origin/main`) if set, else `main`."""
    target = _git_try(repo, "symbolic-ref", "--short", "-q", "refs/remotes/origin/HEAD")
    return target or "main"


# --- state files -----------------------------------------------------------


def state_path(state_dir: Path, repo: Path) -> Path:
    _, bkey = branch_key(repo)
    return Path(state_dir) / repo_key(repo) / bkey


def _locate_keys(state_dir: Path, repo: Path) -> tuple[str, str, Path]:
    """`(repo_key, branch_name, state dir)`, computing each key once."""
    rkey = repo_key(repo)
    branch, bkey = branch_key(repo)
    return rkey, branch, Path(state_dir) / rkey / bkey


def load_state(dir: Path) -> dict | None:
    path = Path(dir) / STATE_FILE
    if not path.exists():
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            state = json.load(fh)
    except ValueError as exc:  # JSONDecodeError and UnicodeDecodeError
        raise StateError(f"corrupt state file {path}: {exc}") from exc
    if not isinstance(state, dict):
        raise StateError(f"corrupt state file {path}: expected a JSON object")
    return _migrate(state)


def _migrate(state: dict) -> dict:
    """Bring a stored state up to the current shape; safe to run on every load.

    v0.7 stored an outside-the-change finding as `deferred`, which was
    indistinguishable from a developer's deferral. v0.8 gives it its own status.
    """
    for f in state.get("findings", []):
        if not isinstance(f, dict) or f.get("scope") != "outside" or f.get("status") != "deferred":
            continue
        res = f.get("resolution")
        if isinstance(res, dict) and res.get("note") == "outside the change set":
            f["status"] = "outside"
            res["status"] = "outside"
    return state


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def save_state(dir: Path, state: dict) -> None:
    dir = Path(dir)
    state["updated"] = _now()
    dir.mkdir(parents=True, exist_ok=True)
    path = dir / STATE_FILE
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=str(dir), prefix=f".{STATE_FILE}.", suffix=".tmp", delete=False
    ) as fh:
        tmp = fh.name
        try:
            json.dump(state, fh, indent=2)
            fh.write("\n")
        except BaseException:
            fh.close()
            os.unlink(tmp)
            raise
    try:
        os.replace(tmp, str(path))
    except BaseException:
        os.unlink(tmp)
        raise


# --- snapshots -------------------------------------------------------------


def _identity_env(repo: Path) -> dict:
    """The environment for creating commits: a fallback identity only when git has none."""
    env = dict(os.environ)
    if _git_try(repo, "var", "GIT_COMMITTER_IDENT") is None:
        env.update(
            GIT_AUTHOR_NAME="branch-review",
            GIT_AUTHOR_EMAIL="branch-review@localhost",
            GIT_COMMITTER_NAME="branch-review",
            GIT_COMMITTER_EMAIL="branch-review@localhost",
        )
    return env


def make_snapshot(repo: Path) -> tuple[str, bool]:
    """Return `(sha, dirty)` for a commit holding the working tree's content.

    Uses a temporary index so the real index, working tree and refs are never
    touched; `--no-optional-locks` stops `git status` refreshing the real index.
    A clean tree returns HEAD itself and creates nothing.
    """
    head = _git(repo, "rev-parse", "HEAD")
    if not _git(repo, "--no-optional-locks", "status", "--porcelain"):
        return head, False
    with tempfile.TemporaryDirectory(prefix="branch-review-") as tmp:
        env = dict(_identity_env(repo), GIT_INDEX_FILE=os.path.join(tmp, "index"))

        def run_git(*args: str) -> str:
            result = subprocess.run(
                ["git", *args],
                cwd=str(repo),
                env=env,
                check=True,
                capture_output=True,
                text=True,
            )
            return result.stdout.strip()

        run_git("read-tree", "HEAD")
        run_git("add", "-A")
        tree = run_git("write-tree")
        sha = run_git(
            "commit-tree",
            "--no-gpg-sign",
            tree,
            "-p",
            head,
            "-m",
            SNAPSHOT_MESSAGE,
        )
    return sha, True


def snapshot_head(repo: Path, snapshot: str) -> str:
    """The commit a snapshot was taken on: its parent when dirty, else itself.

    A dirty snapshot is a dangling commit with the snapshot message. A commit
    on HEAD's history is a real commit, whatever its message says.
    """
    sha = _git_try(repo, "rev-parse", "--verify", "--quiet", snapshot + "^{commit}")
    if not sha:
        raise StateError(f"snapshot not found: {snapshot}")
    if _git(repo, "log", "-1", "--format=%B", sha) == SNAPSHOT_MESSAGE and not _is_ancestor(repo, sha, "HEAD"):
        return _git(repo, "rev-parse", sha + "^")
    return sha


def _is_ancestor(repo: Path, ancestor: str, descendant: str) -> bool:
    return _git_try(repo, "merge-base", "--is-ancestor", ancestor, descendant) is not None


def _object_exists(repo: Path, sha: str) -> bool:
    return _git_try(repo, "cat-file", "-e", sha + "^{commit}") is not None


def _recorded(rounds: list) -> list:
    """`rounds` without its trailing unrecorded rounds (those with no `added` key).

    A round is saved when it begins and gets `added` from `record-findings`. A
    round whose reviewer never reported reviewed nothing, so it must not anchor
    the next follow-up.
    """
    end = len(rounds)
    while end and "added" not in rounds[end - 1]:
        end -= 1
    return rounds[:end]


def _anchor(rounds: list) -> dict:
    """The last round of a recorded list, checked for the keys a follow-up needs."""
    anchor = rounds[-1]
    if not isinstance(anchor, dict):
        raise StateError("corrupt state: the last recorded round is not an object")
    number = anchor.get("number", "?")
    for key in ("snapshot", "head"):
        if not isinstance(anchor.get(key), str) or not anchor[key]:
            raise StateError(f"corrupt state: round {number} is missing {key}")
    if "merge_base" not in anchor:
        raise StateError(f"corrupt state: round {number} is missing merge_base")
    return anchor


def _merge_base(repo: Path, base_ref: str, commit: str = "HEAD") -> str | None:
    return _git_try(repo, "merge-base", commit, base_ref)


def simulate_base_update(repo: Path, anchor: dict, new_merge_base: str) -> str | None:
    """The tree of the anchor's snapshot with the base's new commits merged in, or None.

    None means the merge conflicts, or git could not do it (`merge-tree
    --write-tree --merge-base` needs git 2.40). The tree is the anchor's
    snapshot plus only the base's changes since the anchor's merge base.
    """
    out = _git_try(
        repo,
        "merge-tree",
        "--write-tree",
        f"--merge-base={anchor['merge_base']}",
        anchor["snapshot"],
        new_merge_base,
    )
    if not out:
        return None
    return out.splitlines()[0]


def base_conflicts(repo: Path, anchor: dict, new_merge_base: str) -> list[str] | None:
    """The paths that conflict when the base's new commits merge into the anchor's snapshot.

    `[]` means the merge is clean. None means git could not tell (`merge-tree
    --write-tree --merge-base` needs git 2.40). Paths come NUL-separated, so
    any file name passes through unquoted.
    """
    result = subprocess.run(
        [
            "git",
            "merge-tree",
            "--write-tree",
            "--name-only",
            "-z",
            f"--merge-base={anchor['merge_base']}",
            anchor["snapshot"],
            new_merge_base,
        ],
        cwd=str(repo),
        capture_output=True,
    )
    if result.returncode == 0:
        return []
    if result.returncode != 1:
        return None
    # The tree OID, then the conflicted paths; an empty field starts the messages.
    fields = os.fsdecode(result.stdout).split("\0")[1:]
    conflicts: list[str] = []
    for path in fields:
        if not path:
            break
        if path not in conflicts:
            conflicts.append(path)
    return conflicts


def normalize_paths(repo: Path, patterns: list[str] | None) -> list[str]:
    """Canonical `--paths` patterns: no leading `./`, a directory becomes `dir/**`.

    A pattern with a trailing `/`, or naming an existing directory in the repo,
    means everything under that directory. `.`, `./` or `/` means the whole
    repo, so any of them gives no filter (an empty list).
    """
    out = []
    for raw in patterns or []:
        pat = raw.strip()
        while pat.startswith("./"):
            pat = pat[2:]
        is_dir = pat.endswith("/")
        pat = pat.rstrip("/")
        if pat in ("", "."):
            if raw.strip():
                return []  # the whole repo
            continue
        if is_dir or (not pat.endswith("/**") and (Path(repo) / pat).is_dir()):
            pat += "/**"
        if pat not in out:
            out.append(pat)
    return sorted(out)


def path_matches(path: str, patterns: list[str]) -> bool:
    """True when `path` matches a normalized pattern (`dir/**` is a prefix match)."""
    for pat in patterns:
        if pat.endswith("/**"):
            if path.startswith(pat[: -len("**")]):
                return True
        elif fnmatch.fnmatchcase(path, pat):
            return True
    return False


def choose_mode(repo: Path, state: dict | None, base_ref: str, paths: list[str]) -> tuple[str, str]:
    """`(mode, reason)` for the next round on this branch.

    `paths` is the normalized `--paths` set for the next round. The anchor is
    the last recorded round.
    """
    if state is None:
        return "deep", "no-state"
    recorded = _recorded(state.get("rounds", []))
    if not recorded:
        return "deep", "no-rounds"
    anchor = _anchor(recorded)
    if not _object_exists(repo, anchor["snapshot"]):
        return "deep", "snapshot-missing"
    if not _is_ancestor(repo, anchor["head"], "HEAD"):
        return "deep", "history-rewritten"
    base_updated = conflicted = False
    new_merge_base = _merge_base(repo, base_ref)
    if anchor.get("merge_base") and anchor["merge_base"] != new_merge_base:
        if new_merge_base is None:
            return "deep", "base-moved"
        if simulate_base_update(repo, anchor, new_merge_base) is None:
            # Only the conflicted files, and the branch's own work, need review;
            # when git cannot list the conflicts, review everything.
            if not base_conflicts(repo, anchor, new_merge_base):
                return "deep", "base-moved"
            conflicted = True
        else:
            base_updated = True
    if normalize_paths(repo, anchor.get("paths")) != paths:
        return "deep", "paths-changed"
    if conflicted:
        return "merge-check", "base-moved"
    return "followup", "base-updated" if base_updated else "continuing"


# --- commands --------------------------------------------------------------


def _stored_base(state: dict | None) -> str | None:
    value = (state or {}).get("base_ref")
    return value if isinstance(value, str) and value else None


def _read_text_file(path: str) -> str:
    """Read a UTF-8 text argument file; decoding errors become a StateError."""
    try:
        return Path(path).read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise StateError(f"{path} is not valid UTF-8: {exc.reason}") from exc


def _earlier_not_checked(state: dict | None) -> list[dict]:
    """`not_checked` entries of the current cycle's recorded deep rounds, latest per `what`."""
    if state is None:
        return []
    rounds = [r for r in _recorded(state.get("rounds", [])) if isinstance(r, dict)]
    start = _cycle_start(rounds)
    found: dict[str, dict] = {}
    for rnd in rounds:
        if rnd.get("mode") != "deep" or rnd.get("number", 0) < start:
            continue
        for entry in rnd.get("not_checked") or []:
            found.pop(entry["what"], None)
            found[entry["what"]] = {**entry, "round": rnd.get("number")}
    return list(found.values())


def cmd_locate(args: argparse.Namespace) -> dict:
    repo = Path(args.repo)
    rkey, branch, directory = _locate_keys(Path(args.state_dir).expanduser(), repo)
    state = load_state(directory)

    open_findings: list[dict] = []
    rounds = 0
    stored_base = None
    if state is not None:
        rounds = len(_recorded(state.get("rounds", [])))
        open_findings = [f for f in state.get("findings", []) if f.get("status") in OPEN_STATUSES]
        stored_base = _stored_base(state)
    base = args.base or stored_base or default_base(repo)
    mode, reason = choose_mode(repo, state, base, normalize_paths(repo, args.paths))
    probe = state
    if state is not None and args.second_opinion is not None:
        # The invoker's flag is stored only by the next begin-round; count it now,
        # as that round would, so a requested second opinion replaces the follow-up.
        probe = dict(state)
        probe["second_opinion"] = {
            "requested": rounds + 1 if args.second_opinion else None,
            "skipped": not args.second_opinion,
        }
    focus = None
    if (
        mode == "followup"
        and reason in ("continuing", "base-updated")
        and not any(f.get("status") in ("open", "still-open") for f in open_findings)
        and second_opinion_due(probe)[0]
    ):
        # The second opinion replaces the follow-up and verifies the claimed fixes.
        mode, reason, focus = "deep", SECOND_OPINION_FOCUS, SECOND_OPINION_FOCUS
    conflicts: list[str] = []
    if mode == "merge-check":
        anchor = _anchor(_recorded(state["rounds"]))
        conflicts = base_conflicts(repo, anchor, _merge_base(repo, base)) or []

    return {
        "repo_key": rkey,
        "branch": branch,
        "base": base,
        "dir": str(directory),
        "exists": state is not None,
        "mode": mode,
        "reason": reason,
        "focus": focus,
        "rounds": rounds,
        "open_findings": open_findings,
        "conflicts": conflicts,
        "plugin_version": PLUGIN_VERSION,
        "earlier_not_checked": _earlier_not_checked(state),
    }


def cmd_snapshot(args: argparse.Namespace) -> dict:
    repo = Path(args.repo)
    sha, dirty = make_snapshot(repo)
    return {"snapshot": sha, "head": snapshot_head(repo, sha), "dirty": dirty}


def _numstat(repo: Path, diff_from: str, diff_to: str) -> list[tuple[str, int]]:
    """`(path, insertions + deletions)` per changed file; binary files count 0."""
    out = _git(repo, "diff", "--numstat", "-z", "--no-renames", diff_from, diff_to)
    entries = []
    for record in out.split("\0"):
        if not record:
            continue
        ins, dels, path = record.split("\t", 2)
        count = (int(ins) if ins.isdigit() else 0) + (int(dels) if dels.isdigit() else 0)
        entries.append((path, count))
    return entries


def _check_attr(repo: Path, files: list[str], source: str | None) -> str:
    """`git check-attr -z` output for the generated-file attributes of `files`.

    Paths go in on stdin, NUL-separated, so a long list and any file name
    (spaces, non-ASCII, quotes) pass through unchanged.
    """
    cmd = ["git", "check-attr", "-z"]
    if source is not None:
        cmd.append(f"--source={source}")
    cmd += ["--stdin", "linguist-generated", "diff"]
    stdin = b"".join(os.fsencode(f) + b"\0" for f in files)
    result = subprocess.run(cmd, cwd=str(repo), input=stdin, check=True, capture_output=True)
    return os.fsdecode(result.stdout)


def generated_files(repo: Path, snapshot: str, files: list[str], globs: list[str]) -> set[str]:
    """The subset of `files` that is generated.

    A file is generated when it matches one of `globs`, or when its attributes
    in `snapshot` say `linguist-generated` is set or `diff` is unset. Git older
    than 2.40 has no `check-attr --source`, so the attributes then come from
    the working tree.
    """
    found = {f for f in files if path_matches(f, globs)}
    rest = [f for f in files if f not in found]
    if not rest:
        return found
    try:
        out = _check_attr(repo, rest, snapshot)
    except subprocess.CalledProcessError:
        try:
            out = _check_attr(repo, rest, None)
        except subprocess.CalledProcessError:
            return found
    fields = out.split("\0")
    for i in range(0, len(fields) - 2, 3):
        path, attr, value = fields[i : i + 3]
        if (attr == "linguist-generated" and value in ("set", "true")) or (attr == "diff" and value == "unset"):
            found.add(path)
    return found


def _new_state(rkey: str, branch: str, base_ref: str) -> dict:
    """The state of a branch with no rounds yet."""
    now = _now()
    return {
        "repo_key": rkey,
        "branch": branch,
        "base_ref": base_ref,
        "intent": None,
        "created": now,
        "updated": now,
        "rounds": [],
        "findings": [],
    }


def cmd_begin_round(args: argparse.Namespace) -> dict:
    repo = Path(args.repo)
    rkey, branch, directory = _locate_keys(Path(args.state_dir).expanduser(), repo)
    state = load_state(directory)
    existed = state is not None
    base_ref = args.base_ref or _stored_base(state) or default_base(repo)

    if state is None:
        state = _new_state(rkey, branch, base_ref)
    state.setdefault("second_opinion", {"requested": None, "skipped": False})
    # A round that was begun but never recorded reviewed nothing: drop it.
    rounds = _recorded(state.get("rounds", []))
    state["rounds"] = rounds
    state.setdefault("findings", [])

    snapshot = args.snapshot or make_snapshot(repo)[0]
    # The round's head is the commit the snapshot was taken on, not whatever
    # HEAD is now, so head, merge base and snapshot always agree.
    head = snapshot_head(repo, snapshot)
    if _git_try(repo, "rev-parse", "--verify", "--quiet", base_ref + "^{commit}") is None:
        raise StateError(f"base ref not found: {base_ref}")
    merge_base = _merge_base(repo, base_ref, head)
    if merge_base is None:
        raise StateError(f"no merge base between {head[:12]} and {base_ref}")
    conflicts: list[str] = []
    upstream_overlap: list[str] = []
    change_set: set[str] = set()
    upstream: set[str] = set()
    if args.mode == "merge-check":
        if not rounds:
            raise StateError("cannot start a merge-check round: no recorded round")
        anchor = _anchor(rounds)
        found = base_conflicts(repo, anchor, merge_base)
        if found is None:
            raise StateError("cannot start a merge-check round: the base update cannot be simulated; run a deep round")
        conflicts = found
        diff_from = anchor["snapshot"]
        change_set = {f for f, _ in _numstat(repo, merge_base, snapshot)}
        upstream_files = [f for f, _ in _numstat(repo, anchor["merge_base"], merge_base)]
        upstream = set(upstream_files)
        upstream_overlap = [f for f in upstream_files if f in change_set]
    elif args.mode == "followup":
        if not rounds:
            raise StateError("cannot start a follow-up round: no recorded round")
        anchor = _anchor(rounds)
        diff_from = anchor["snapshot"]
        if anchor["merge_base"] and anchor["merge_base"] != merge_base:
            tree = simulate_base_update(repo, anchor, merge_base)
            if tree is None:
                raise StateError(
                    f"cannot start a follow-up round: the base update since round {anchor.get('number', '?')} "
                    "has conflicts; run a merge-check round (or deep if merge-check is unavailable)"
                )
            # The anchor's snapshot with the base's new commits merged in, so
            # the delta shows only the branch's own work.
            diff_from = _git(
                repo,
                "commit-tree",
                "--no-gpg-sign",
                tree,
                "-p",
                anchor["snapshot"],
                "-p",
                merge_base,
                "-m",
                "branch-review base update",
                env=_identity_env(repo),
            )
    else:
        diff_from = merge_base
    diff_to = snapshot

    state["base_ref"] = base_ref
    if args.intent_file:
        state["intent"] = _read_text_file(args.intent_file)
    if args.notes_file is not None:
        state["notes"] = _read_text_file(args.notes_file).strip() or None
    if args.related_branch is not None:
        state["related_branches"] = [] if args.related_branch == ["."] else list(args.related_branch)

    paths = normalize_paths(repo, args.paths)
    if args.generated is not None:
        state["generated"] = normalize_paths(repo, args.generated)
    globs = state.setdefault("generated", [])

    def measure(start: str) -> tuple[list[str], int, set[str], int, int]:
        """Changed files and lines since `start`, and the part that counts (not generated)."""
        entries = _numstat(repo, start, diff_to)
        if paths:
            entries = [(f, n) for f, n in entries if path_matches(f, paths)]
        if mode == "merge-check":
            # The branch's own work since the anchor plus the conflicted files;
            # files only the base changed drop out. A file the base did not
            # touch stays even when it no longer differs from the base (the
            # branch deleted or reverted its own change).
            entries = [(f, n) for f, n in entries if f not in upstream or f in change_set]
            listed = {f for f, _ in entries}
            entries += [(f, 0) for f in conflicts if f not in listed]
        names = [f for f, _ in entries]
        generated = generated_files(repo, diff_to, names, globs)
        counted = [n for f, n in entries if f not in generated]
        return names, sum(n for _, n in entries), generated, len(counted), sum(counted)

    mode, model, mode_reason = args.mode, args.model, args.mode_reason
    changed, lines, generated, counted_files, counted_lines = measure(diff_from)
    if mode == "followup" and (counted_files >= LARGE_FILES or counted_lines >= LARGE_LINES):
        # Too much changed since the last round to re-check by delta: review the
        # whole change set from the merge base with the deep model.
        mode, model, mode_reason = "deep", args.deep_model or args.model, "large-delta"
        diff_from = merge_base
        changed, lines, generated, counted_files, counted_lines = measure(diff_from)
    files = len(changed)
    if args.focus == SECOND_OPINION_FOCUS and mode != "deep":
        # Any round with this focus counts as the second opinion, so it must be
        # the deep review the second opinion is.
        raise StateError(f"--focus {SECOND_OPINION_FOCUS} needs a deep round, not {mode}")

    rnd = {
        "number": len(rounds) + 1,
        "mode": mode,
        "model": model,
        "started": _now(),
        "snapshot": diff_to,
        "head": head,
        "base_ref": base_ref,
        "merge_base": merge_base,
        "diff_from": diff_from,
        "diff_to": diff_to,
        "changed_files": changed,
        "paths": paths,
        "summary": None,
        "merge_risk": None,
        "focus": args.focus,
        "mode_reason": mode_reason,
        "plugin_version": PLUGIN_VERSION,
        "intent": state.get("intent"),
    }
    if mode == "merge-check":
        rnd["conflicts"] = conflicts
    # An empty round has nothing for a reviewer, so it is never saved. Existing
    # state is still saved, to drop unrecorded rounds; no state is created.
    saved = files > 0
    if saved:
        rounds.append(rnd)
    if saved or existed:
        if args.second_opinion is not None:
            # A request is the number of the round that made it (or would have,
            # for an empty round); it is met by a second-opinion round numbered
            # at least that. An empty round still stores the choice.
            state["second_opinion"] = {
                "requested": rnd["number"] if args.second_opinion else None,
                "skipped": not args.second_opinion,
            }
        save_state(directory, state)

    result = dict(rnd)
    result.update(
        {
            "dir": str(directory),
            "files": files,
            "lines": lines,
            "generated": sorted(generated),
            "counted_files": counted_files,
            "counted_lines": counted_lines,
            "oversized": counted_files > MAX_FILES or counted_lines > MAX_LINES,
            "empty": files == 0,
            "saved": saved,
            "notes": state.get("notes"),
            "related_branches": state.get("related_branches", []),
        }
    )
    if mode == "merge-check":
        result["upstream_overlap"] = upstream_overlap
    return result


def cmd_record(args: argparse.Namespace) -> dict:
    """Save a `recorded` round for a commit that is already on the remote branch.

    No reviewer runs. The round anchors later coverage and merge-check rounds,
    and leaves the findings ledger as it is.
    """
    repo = Path(args.repo)
    rkey, branch, directory = _locate_keys(Path(args.state_dir).expanduser(), repo)
    if branch_key(repo)[1].startswith("detached-"):
        raise StateError("record needs a branch, not a detached HEAD")
    commit = _git_try(repo, "rev-parse", "--verify", "--quiet", args.commit + "^{commit}")
    if not commit:
        raise StateError(f"commit not found: {args.commit}")
    remote = f"origin/{branch}"
    remote_sha = _git_try(repo, "rev-parse", "--verify", "--quiet", f"refs/remotes/{remote}^{{commit}}")
    if not remote_sha:
        raise StateError(f"no {remote} ref; fetch the branch first")
    if not _is_ancestor(repo, commit, remote_sha):
        raise StateError(f"commit {commit[:12]} is not on {remote}; record only what is already pushed")
    reason = _read_text_file(args.reason_file).strip()
    if not reason:
        raise StateError(f"{args.reason_file} holds no reason")

    state = load_state(directory)
    base_ref = args.base or _stored_base(state) or default_base(repo)
    if _git_try(repo, "rev-parse", "--verify", "--quiet", base_ref + "^{commit}") is None:
        raise StateError(f"base ref not found: {base_ref}")
    merge_base = _merge_base(repo, base_ref, commit)
    if merge_base is None:
        raise StateError(f"no merge base between {commit[:12]} and {base_ref}")
    if state is None:
        state = _new_state(rkey, branch, base_ref)
    state.setdefault("second_opinion", {"requested": None, "skipped": False})
    state.setdefault("findings", [])
    # A round that was begun but never recorded reviewed nothing: drop it.
    rounds = _recorded(state.get("rounds", []))
    state["rounds"] = rounds

    diff_from = merge_base
    if rounds:
        anchor = _anchor(rounds)
        if anchor["head"] == commit:
            return {
                "recorded": False,
                "round": None,
                "commit": commit,
                "branch": branch,
                "files": 0,
                "dir": str(directory),
            }
        diff_from = anchor["snapshot"]
    changed = [f for f, _ in _numstat(repo, diff_from, commit)]

    state["base_ref"] = base_ref
    rnd = {
        "number": len(rounds) + 1,
        "mode": "recorded",
        "model": None,
        "started": _now(),
        "snapshot": commit,
        "head": commit,
        "base_ref": base_ref,
        "merge_base": merge_base,
        "diff_from": diff_from,
        "diff_to": commit,
        "changed_files": changed,
        "paths": [],
        "summary": None,
        "merge_risk": None,
        "focus": None,
        "mode_reason": "recorded",
        "plugin_version": PLUGIN_VERSION,
        "intent": state.get("intent"),
        "reason": reason,
        "added": [],
    }
    rounds.append(rnd)
    save_state(directory, state)
    _write_summary(directory, state)
    return {
        "recorded": True,
        "round": rnd["number"],
        "commit": commit,
        "branch": branch,
        "files": len(changed),
        "dir": str(directory),
    }


# --- findings ledger -------------------------------------------------------

STATUSES = ("open", "fixed", "still-open", "verified", "invalid", "escalated", "deferred", "accepted", "outside")
SEVERITIES = ("critical", "major", "minor")
CATEGORIES = (
    "logic",
    "sibling-path",
    "security",
    "tests",
    "intent",
    "conventions",
    "docs",
    "build-ci",
)
# The steps a deep round must account for, besides the checklist categories.
COVERAGE_STEPS = (
    "break-tests",
    "doc-grep",
    "fulfilled-promises",
    "real-system",
    "related-records",
    "broad-mappings",
    "measurement-tests",
    "consumer-build",
)
COVERAGE_ITEMS = CATEGORIES + COVERAGE_STEPS
VARIANT_RESULTS = ("fails", "ok")
RISK_LEVELS = ("minimal", "low", "moderate", "high")
REACHABLE = ("yes", "no", "unclear")
EFFORTS = ("quick", "heavy")
VERDICT_STATUSES = ("verified", "still-open")
FINDING_REQUIRED = (
    "severity",
    "category",
    "file",
    "lines",
    "title",
    "trigger",
    "consequence",
    "reachable_now",
    "fix",
)
FINDING_OPTIONAL = ("reachable_note", "evidence", "test", "source")
MAX_ROUNDS = 6
_JSON_BLOCK = re.compile(r"^```json[ \t]*\n(.*?)^```[ \t]*$", re.DOTALL | re.MULTILINE | re.IGNORECASE)


def extract_json_block(text: str) -> dict:
    """Parse the last fenced ```json block (any case, LF or CRLF) in a reviewer report."""
    blocks = _JSON_BLOCK.findall(text.replace("\r\n", "\n"))
    if not blocks:
        raise ValueError("no json block")
    try:
        return json.loads(blocks[-1])
    except ValueError as exc:
        raise ValueError(f"json parse error: {exc}") from exc


def _bad(message: str) -> StateError:
    return StateError(f"invalid reviewer output: {message}")


def _require_str(obj: dict, key: str, where: str) -> str:
    if key not in obj:
        raise _bad(f"{where} is missing {key}")
    if not isinstance(obj[key], str):
        raise _bad(f"{where}.{key} must be a string")
    return obj[key]


def _require_enum(obj: dict, key: str, allowed: tuple[str, ...], where: str) -> str:
    value = _require_str(obj, key, where)
    if value not in allowed:
        raise _bad(f"{where}.{key} must be one of {'|'.join(allowed)}, got {value!r}")
    return value


def _require_list(obj: dict, key: str, where: str) -> list:
    if key not in obj:
        raise _bad(f"{where} is missing {key}")
    if not isinstance(obj[key], list):
        raise _bad(f"{where}.{key} must be a list")
    return obj[key]


def _optional_str(obj: dict, key: str, default: str, where: str) -> str:
    if key not in obj:
        return default
    if not isinstance(obj[key], str):
        raise _bad(f"{where}.{key} must be a string")
    return obj[key]


def _require_str_list(obj: dict, key: str, where: str) -> list:
    items = _require_list(obj, key, where)
    for i, item in enumerate(items):
        if not isinstance(item, str):
            raise _bad(f"{where}.{key}[{i}] must be a string")
    return items


def _require_text(obj: dict, key: str, where: str) -> str:
    value = _require_str(obj, key, where)
    if not value.strip():
        raise _bad(f"{where}.{key} must not be empty")
    return value


def _objects(data: dict, key: str, where: str, required: bool) -> list:
    """The list at `data[key]`, each entry an object; `[]` when absent and not required."""
    if key not in data and not required:
        return []
    items = _require_list(data, key, where)
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            raise _bad(f"{where}.{key}[{i}] must be an object")
    return items


def _validate_coverage(data: dict, required: bool) -> tuple[list, list]:
    """`coverage` and `not_checked`; both must be present, and cover every item, when `required`."""
    coverage = []
    for i, c in enumerate(_objects(data, "coverage", "output", required)):
        where = f"coverage[{i}]"
        coverage.append({"item": _require_text(c, "item", where), "done": _require_text(c, "done", where)})
    if required:
        listed = {c["item"] for c in coverage}
        missing = [item for item in COVERAGE_ITEMS if item not in listed]
        if missing:
            raise _bad("coverage is missing " + ", ".join(missing))
    not_checked = []
    for i, n in enumerate(_objects(data, "not_checked", "output", required)):
        where = f"not_checked[{i}]"
        not_checked.append({"what": _require_text(n, "what", where), "reason": _require_text(n, "reason", where)})
    return coverage, not_checked


def _validate_variants(f: dict, where: str) -> list:
    variants = []
    for i, v in enumerate(_objects(f, "variants", where, False)):
        at = f"{where}.variants[{i}]"
        variants.append(
            {"input": _require_text(v, "input", at), "result": _require_enum(v, "result", VARIANT_RESULTS, at)}
        )
    return variants


def _validate_fix_options(f: dict, where: str) -> list:
    if "fix_options" not in f:
        return []
    options = _require_str_list(f, "fix_options", where)
    if len(options) < 2 or not all(o.strip() for o in options):
        raise _bad(f"{where}.fix_options must hold two or more non-empty alternatives")
    return options


def _validate_output(data: object, known: dict, *, require_coverage: bool = False) -> dict:
    """Check the reviewer block; return its verdicts, findings, outside entries and coverage.

    `known` maps each ledger finding id to its status. With `require_coverage`
    (a deep round begun under 0.9 or later), `coverage` must list every item of
    `COVERAGE_ITEMS` and `not_checked` must be present.
    """
    if not isinstance(data, dict):
        raise _bad("expected a JSON object")
    _require_str(data, "summary", "output")
    risk = data.get("merge_risk")
    if not isinstance(risk, dict):
        raise _bad("output is missing merge_risk")
    _require_enum(risk, "level", RISK_LEVELS, "merge_risk")
    if not _require_str(risk, "reason", "merge_risk").strip():
        raise _bad("merge_risk.reason must not be empty")
    check = data.get("intent_check")
    if not isinstance(check, dict):
        raise _bad("output is missing intent_check")
    _require_str_list(check, "unmet_claims", "intent_check")
    _require_str_list(check, "unmentioned_behavior", "intent_check")
    coverage, not_checked = _validate_coverage(data, require_coverage)

    verdicts = []
    seen: set = set()
    for i, v in enumerate(_require_list(data, "verdicts", "output")):
        where = f"verdicts[{i}]"
        if not isinstance(v, dict):
            raise _bad(f"{where} must be an object")
        vid = _require_str(v, "id", where)
        status = _require_enum(v, "status", VERDICT_STATUSES, where)
        if vid not in known:
            raise _bad(f"{where} refers to unknown finding {vid}")
        if vid in seen:
            raise _bad(f"{where} repeats a verdict for {vid}")
        if known[vid] not in OPEN_STATUSES:
            raise _bad(f"{where} gives a verdict on {vid}, which is {known[vid]}, not open, fixed or still-open")
        seen.add(vid)
        verdicts.append({"id": vid, "status": status, "reason": _optional_str(v, "reason", "", where)})

    findings = []
    for i, f in enumerate(_require_list(data, "findings", "output")):
        where = f"findings[{i}]"
        if not isinstance(f, dict):
            raise _bad(f"{where} must be an object")
        rec = {}
        for key in FINDING_REQUIRED:
            if key == "severity":
                rec[key] = _require_enum(f, key, SEVERITIES, where)
            elif key == "category":
                rec[key] = _require_enum(f, key, CATEGORIES, where)
            elif key == "reachable_now":
                rec[key] = _require_enum(f, key, REACHABLE, where)
            else:
                rec[key] = _require_str(f, key, where)
        for key in FINDING_OPTIONAL:
            rec[key] = _optional_str(f, key, "", where)
        rec["effort"] = _require_enum(f, "effort", EFFORTS, where) if "effort" in f else "quick"
        rec["variants"] = _validate_variants(f, where)
        rec["fix_options"] = _validate_fix_options(f, where)
        findings.append(rec)

    outside = []
    for i, o in enumerate(_require_list(data, "outside_change", "output")):
        where = f"outside_change[{i}]"
        if not isinstance(o, dict):
            raise _bad(f"{where} must be an object")
        rec = {
            "severity": "minor",
            "category": "logic",
            "file": _require_str(o, "file", where),
            "lines": _optional_str(o, "lines", "", where),
            "title": _require_str(o, "title", where),
            "detail": _optional_str(o, "detail", "", where),
        }
        if "severity" in o:
            rec["severity"] = _require_enum(o, "severity", SEVERITIES, where)
        if "category" in o:
            rec["category"] = _require_enum(o, "category", CATEGORIES, where)
        outside.append(rec)

    return {
        "verdicts": verdicts,
        "findings": findings,
        "outside": outside,
        "coverage": coverage,
        "not_checked": not_checked,
    }


def _load_output(path: str) -> object:
    """Parse a bare JSON file, else the last json block of a markdown report."""
    text = _read_text_file(path)
    try:
        return json.loads(text)
    except ValueError as exc:
        bare_error = exc
    try:
        return extract_json_block(text)
    except ValueError as exc:
        if not _JSON_BLOCK.search(text.replace("\r\n", "\n")) and text.lstrip().startswith(("{", "[")):
            raise _bad(f"invalid JSON: {bare_error}") from bare_error
        raise _bad(str(exc)) from exc


def _ledger_state(args: argparse.Namespace) -> tuple[Path, dict]:
    """Load the branch's state, which must exist and hold at least one round."""
    directory = state_path(Path(args.state_dir).expanduser(), Path(args.repo))
    state = load_state(directory)
    if state is None or not state.get("rounds"):
        raise StateError("no review round in progress; run begin-round first")
    state.setdefault("findings", [])
    return directory, state


def _find(state: dict, finding_id: str) -> dict:
    for f in state["findings"]:
        if f.get("id") == finding_id:
            return f
    raise StateError(f"unknown finding id {finding_id}")


def cmd_record_findings(args: argparse.Namespace) -> dict:
    directory, state = _ledger_state(args)
    rnd = state["rounds"][-1]
    number = rnd["number"]
    if "added" in rnd:
        raise StateError(f"round {number} already has recorded findings")
    known = {f.get("id"): f.get("status") for f in state["findings"]}
    data = _load_output(args.file)
    required = rnd.get("mode") == "deep" and _version_at_least(rnd.get("plugin_version"), V09)
    checked = _validate_output(data, known, require_coverage=required)

    for v in checked["verdicts"]:
        f = _find(state, v["id"])
        f["status"] = v["status"]
        f["round_closed"] = number if v["status"] == "verified" else None
        if v["reason"]:
            f["discussion"].append({"from": "reviewer", "text": v["reason"], "round": number})

    new: list[dict] = []
    for rec in checked["findings"]:
        new.append(
            {
                "id": f"R{number}-F{len(new) + 1}",
                "status": "open",
                "scope": "change",
                "round_opened": number,
                "round_closed": None,
                **rec,
                "resolution": None,
                "discussion": [],
            }
        )
    added = len(new)
    for rec in checked["outside"]:
        new.append(
            {
                "id": f"R{number}-F{len(new) + 1}",
                "status": "outside",
                "scope": "outside",
                "round_opened": number,
                "round_closed": None,
                **rec,
                "resolution": {
                    "status": "outside",
                    "note": "outside the change set",
                    "commit": "",
                    "by": "reviewer",
                    "round": number,
                },
                "discussion": [],
            }
        )
    state["findings"].extend(new)

    rnd["summary"] = data["summary"]
    rnd["merge_risk"] = data["merge_risk"]
    rnd["intent_check"] = data["intent_check"]
    rnd["coverage"] = checked["coverage"]
    rnd["not_checked"] = checked["not_checked"]
    rnd["added"] = added

    # Copy first: a failed copy must leave the ledger as it was.
    if args.report:
        rounds_dir = directory / "rounds"
        rounds_dir.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copyfile(args.file, str(rounds_dir / f"{number}.md"))
        except shutil.SameFileError:
            pass  # --file is already rounds/<N>.md
    save_state(directory, state)
    _write_summary(directory, state)
    return {
        "round": number,
        "added": added,
        "outside": len(new) - added,
        "ids": [f["id"] for f in new],
        "verdicts": len(checked["verdicts"]),
    }


def _text_arg(inline: str | None, file: str | None, flag: str, required: bool) -> str:
    """The value of `--X` or the stripped UTF-8 contents of `--X-file`.

    Argparse makes the two forms mutually exclusive. With `required`, an empty
    or blank file is an error rather than an empty value.
    """
    if file is None:
        return inline or ""
    text = _read_text_file(file).strip()
    if required and not text:
        raise StateError(f"{flag}-file {file} is empty")
    return text


def _normalize_ws(text: str) -> str:
    """Runs of whitespace collapsed to one space, ends stripped; case kept."""
    return " ".join(text.split())


def _fix_options_basis(state: dict, finding: dict, args: argparse.Namespace) -> str | None:
    """What lets a finding with fix options be marked fixed.

    The developer's `resolve --decision fix` (returns None), or an
    `--intent-quote-file` whose text occurs in the intent stored on the round
    that opened the finding (returns the normalized quote). A finding with
    fewer than two options needs neither.
    """
    if len(finding.get("fix_options") or []) < 2:
        return None
    if (finding.get("decision") or {}).get("decision") == "fix":
        return None
    refusal = StateError(
        f"{finding['id']} offers fix options; record the developer's decision with resolve --decision fix, "
        "or pass --intent-quote-file with a line of the intent that settles the choice"
    )
    if args.intent_quote_file is None:
        raise refusal
    quote = _normalize_ws(_read_text_file(args.intent_quote_file))
    opened = finding.get("round_opened")
    rnd = next((r for r in state["rounds"] if r.get("number") == opened), None)
    intent = (rnd or {}).get("intent")
    if not isinstance(intent, str):
        raise StateError(f"no intent stored for round {opened}")
    if not quote or quote not in _normalize_ws(intent):
        raise refusal
    return quote


def cmd_set_status(args: argparse.Namespace) -> dict:
    directory, state = _ledger_state(args)
    finding = _find(state, args.id)
    note = _text_arg(args.note, args.note_file, "--note", required=False)
    commit = args.commit or ""
    if args.status == "fixed" and not (commit or note):
        raise StateError("fixed requires --commit or --note")
    if args.status in ("invalid", "escalated") and not note:
        raise StateError(f"{args.status} requires --note")
    quote = _fix_options_basis(state, finding, args) if args.status == "fixed" else None
    number = state["rounds"][-1]["number"]
    finding["status"] = args.status
    finding["resolution"] = {
        "status": args.status,
        "note": note,
        "commit": commit,
        "by": "orchestrator",
        "round": number,
    }
    if quote is not None:
        finding["resolution"]["intent_quote"] = quote
    finding["round_closed"] = number if args.status in ("invalid", "escalated") else None
    save_state(directory, state)
    _write_summary(directory, state)
    return {
        "id": finding["id"],
        "status": finding["status"],
        "round_closed": finding["round_closed"],
    }


def cmd_discuss(args: argparse.Namespace) -> dict:
    directory, state = _ledger_state(args)
    finding = _find(state, args.id)
    text = _text_arg(args.text, args.text_file, "--text", required=True)
    number = state["rounds"][-1]["number"]
    finding["discussion"].append({"from": args.source, "text": text, "round": number})
    save_state(directory, state)
    _write_summary(directory, state)
    return {"id": finding["id"], "discussion": len(finding["discussion"])}


def cmd_resolve(args: argparse.Namespace) -> dict:
    directory, state = _ledger_state(args)
    finding = _find(state, args.id)
    if finding["status"] != "escalated":
        raise StateError(f"{finding['id']} is {finding['status']}, not escalated")
    note = _text_arg(args.note, args.note_file, "--note", required=True)
    number = state["rounds"][-1]["number"]
    finding["status"] = {"fix": "open", "defer": "deferred", "accept": "accepted"}[args.decision]
    finding["resolution"] = {
        "status": finding["status"],
        "note": note,
        "commit": "",
        "by": "developer",
        "round": number,
    }
    # Kept apart from `resolution`, which set-status replaces.
    finding["decision"] = {"decision": args.decision, "note": note, "by": "developer", "round": number}
    finding["round_closed"] = None if args.decision == "fix" else number
    save_state(directory, state)
    _write_summary(directory, state)
    return {
        "id": finding["id"],
        "status": finding["status"],
        "round_closed": finding["round_closed"],
    }


def cmd_confirm_behavior(args: argparse.Namespace) -> dict:
    """Record a behavior change that existing users rely on, for the developer to confirm."""
    directory, state = _ledger_state(args)
    text = _text_arg(None, args.text_file, "--text", required=True)
    items = state.setdefault("behavior_to_confirm", [])
    if not any(item.get("text") == text for item in items):
        items.append({"text": text, "round": state["rounds"][-1]["number"]})
    save_state(directory, state)
    _write_summary(directory, state)
    return {"behavior_to_confirm": len(items)}


def _reported_state(directory: Path) -> dict:
    """Load state whose last round is recorded; `status` and `summary` need one."""
    state = load_state(directory)
    if state is None or not state.get("rounds"):
        raise StateError("no review rounds recorded for this branch")
    last = state["rounds"][-1]
    if "added" not in last:
        raise StateError(f"round {last.get('number', '?')} has no recorded findings; run record-findings first")
    return state


def _cycle_start(rounds: list) -> int:
    """Number of the last recorded round that forced a deep review, else 1.

    A forced deep round re-reviews the whole change, so what earlier rounds
    found no longer counts toward a second opinion.
    """
    start = 1
    for rnd in rounds:
        if isinstance(rnd, dict) and rnd.get("mode_reason") in FORCED_DEEP:
            start = rnd.get("number", start)
    return start


def _escalated(state: dict) -> bool:
    return any(isinstance(f, dict) and f.get("status") == "escalated" for f in state.get("findings", []))


def second_opinion_due(state: dict) -> tuple[bool, list[str]]:
    """Whether a second-opinion round is due now, and why.

    It waits while any finding is escalated: the developer's answer may add
    code the second opinion must see (`second_opinion_held` says so).
    """
    reasons = _second_opinion_triggers(state)
    if not reasons or _escalated(state):
        return False, []
    return True, reasons


def second_opinion_held(state: dict) -> bool:
    """A second opinion would be due but waits for escalated findings."""
    return bool(_second_opinion_triggers(state)) and _escalated(state)


def _second_opinion_triggers(state: dict) -> list[str]:
    """The reasons the recorded state calls for a second opinion, decisions aside.

    State from v0.7 (no round carries `plugin_version`) is exempt from the
    automatic reasons; a `--second-opinion` request still counts.
    """
    rounds = [r for r in _recorded(state.get("rounds", [])) if isinstance(r, dict)]
    opinions = [r.get("number", 0) for r in rounds if r.get("focus") == SECOND_OPINION_FOCUS]
    flags = state.get("second_opinion") or {}
    reasons: list[str] = []
    requested = flags.get("requested")
    if requested is not None and not any(n >= requested for n in opinions):
        reasons.append("requested")
    if flags.get("skipped") or opinions or not any("plugin_version" in r for r in rounds):
        return reasons
    start = _cycle_start(rounds)
    cycle = [r for r in rounds if r.get("number", 0) >= start]
    if any(r.get("mode") == "deep" and (r.get("merge_risk") or {}).get("level") in ("moderate", "high") for r in cycle):
        reasons.append("merge-risk")
    if any(
        isinstance(f, dict)
        and f.get("scope") == "change"
        and f.get("round_opened", 0) >= start
        and f.get("severity") in ("critical", "major")
        for f in state.get("findings", [])
    ):
        reasons.append("major-finding")
    return reasons


def _cap_floor(rounds: list, since_round: int) -> int:
    """Number of the last round that does not count toward the round cap.

    A forced deep round restarts the review, so the round before it is the
    floor when that is later than `since_round`.
    """
    if not any(isinstance(r, dict) and r.get("mode_reason") in FORCED_DEEP for r in rounds):
        return since_round
    return max(since_round, _cycle_start(rounds) - 1)


def cmd_status(args: argparse.Namespace) -> dict:
    state = _reported_state(state_path(Path(args.state_dir).expanduser(), Path(args.repo)))
    rounds = state["rounds"]
    counts = {s: 0 for s in STATUSES}
    for f in state.get("findings", []):
        counts[f.get("status")] = counts.get(f.get("status"), 0) + 1

    opinion_reasons: list[str] = []
    # Escalated findings wait for the developer; they do not keep the loop
    # going, but a loop that stops with them stops awaiting decisions. A due
    # second opinion runs once nothing is open, carrying the claimed fixes.
    due, reasons_now = second_opinion_due(state)
    if counts["open"] or counts["still-open"]:
        cont, reason = True, "open-findings"
    elif due:
        cont, reason, opinion_reasons = True, "second-opinion", reasons_now
    elif counts["fixed"]:
        cont, reason = True, "unverified-fixes"
    elif counts["escalated"]:
        cont, reason = False, "awaiting-decisions"
    else:
        cont, reason = False, "converged"
    # The cap only stops a loop that would otherwise go on.
    floor = _cap_floor(rounds, args.since_round)
    # A recorded round reviewed nothing, so it does not use up the cap.
    run_rounds = sum(1 for r in rounds if r.get("number", 0) > floor and r.get("mode") != "recorded")
    if cont and run_rounds >= MAX_ROUNDS:
        if reason == "unverified-fixes" and run_rounds == MAX_ROUNDS:
            reason = "final-verification"
        elif reason != "second-opinion":
            cont, reason = False, "max-rounds"
    second = reason == "second-opinion"
    return {
        "rounds": len(rounds),
        "last_mode": rounds[-1]["mode"],
        "counts": counts,
        "continue": cont,
        "reason": reason,
        "next_mode": "deep" if second else "followup" if reason == "final-verification" else None,
        "next_focus": SECOND_OPINION_FOCUS if second else None,
        "second_opinion_reasons": opinion_reasons if second else [],
        "second_opinion_held": second_opinion_held(state),
    }


def _partial_scope(recorded: list) -> bool:
    """True when a round since the last deep one (that one included) reviewed only some paths.

    A follow-up diffs from the previous snapshot, so it cannot see a change
    that an earlier `--paths` round skipped.
    """
    for rnd in reversed(recorded):
        if not isinstance(rnd, dict) or rnd.get("paths"):
            return True
        if rnd.get("mode") == "deep":
            return False
    return False


def cmd_covered(args: argparse.Namespace) -> dict:
    """Whether a clean review has seen everything in a commit (default `HEAD`).

    Never says covered for code the review did not see: anything it cannot
    prove, such as a conflicting base update, counts against the commit.
    """
    repo = Path(args.repo)
    _, branch, directory = _locate_keys(Path(args.state_dir).expanduser(), repo)
    commit = _git_try(repo, "rev-parse", "--verify", "--quiet", args.commit + "^{commit}")
    if not commit:
        raise StateError(f"commit not found: {args.commit}")
    state = load_state(directory)
    base = args.base or _stored_base(state) or default_base(repo)

    counts = {s: 0 for s in STATUSES}
    for f in (state or {}).get("findings", []):
        counts[f.get("status")] = counts.get(f.get("status"), 0) + 1
    recorded = _recorded(state.get("rounds", [])) if state else []

    reasons: list[str] = []
    if not recorded:
        reasons.append("no-rounds")
    else:
        anchor = _anchor(recorded)
        if counts["open"] or counts["still-open"]:
            reasons.append("open-findings")
        if counts["fixed"]:
            reasons.append("unverified-fixes")
        if counts["escalated"]:
            reasons.append("escalated")
        if _partial_scope(recorded):
            reasons.append("partial-scope")
        reviewed_tree = None
        if not _object_exists(repo, anchor["snapshot"]):
            reasons.append("snapshot-missing")
        elif not _is_ancestor(repo, anchor["head"], commit):
            reasons.append("history-rewritten")
        else:
            reviewed_tree = _git(repo, "rev-parse", anchor["snapshot"] + "^{tree}")
            new_merge_base = _merge_base(repo, base, commit)
            if anchor.get("merge_base") and anchor["merge_base"] != new_merge_base:
                reviewed_tree = simulate_base_update(repo, anchor, new_merge_base) if new_merge_base else None
                if reviewed_tree is None:
                    reasons.append("base-conflict")
        if reviewed_tree is not None and _git(repo, "rev-parse", commit + "^{tree}") != reviewed_tree:
            reasons.append("changes-since-review")
        if second_opinion_due(state)[0]:
            reasons.append("second-opinion-pending")

    return {
        "covered": not reasons,
        "commit": commit,
        "branch": branch,
        "reasons": reasons,
        "counts": counts,
        "rounds": len(recorded),
        "second_opinion_held": second_opinion_held(state) if recorded else False,
        "behavior_to_confirm": len((state or {}).get("behavior_to_confirm") or []),
    }


# --- summary, prune, list, cleanup -----------------------------------------

STALE_DAYS = 30


def _one_line(text: object) -> str:
    return " ".join(str(text or "").split())


def _location(finding: dict) -> str:
    file = finding.get("file") or "?"
    lines = finding.get("lines")
    return f"{file}:{lines}" if lines else file


def _entry(finding: dict, details: list[str]) -> list[str]:
    out = [f"- **{finding.get('id')}** {_one_line(finding.get('title'))} (`{_location(finding)}`)"]
    out.extend(f"  - {d}" for d in details if d)
    return out


def _resolution_note(finding: dict) -> list[str]:
    res = finding.get("resolution") or {}
    note = _one_line(res.get("note"))
    if not note:
        return []
    by = str(res.get("by") or "note").capitalize()
    return [f"{by}: {note}"]


def _invalid_details(finding: dict) -> list[str]:
    res = finding.get("resolution") or {}
    replies = [d for d in finding.get("discussion", []) if d.get("from") == "reviewer"]
    reply = _one_line(replies[-1].get("text")) if replies else ""
    return [
        f"Orchestrator: {_one_line(res.get('note')) or '(no note)'}",
        f"Reviewer: {reply or '(no reply)'}",
    ]


def _open_details(finding: dict) -> list[str]:
    """Status, the claimed fix if any, and the reviewer's latest word on it."""
    details = [f"Status: {finding.get('status')}"]
    details.extend(_resolution_note(finding))
    if finding.get("status") == "still-open":
        replies = [d for d in finding.get("discussion", []) if d.get("from") == "reviewer"]
        if replies:
            details.append("Reviewer: " + _one_line(replies[-1].get("text")))
    return details


def _verified_details(finding: dict) -> list[str]:
    res = finding.get("resolution") or {}
    return [f"Commit: {res.get('commit') or '(none)'}"]


def _intent_check_lines(state: dict, *, skip_empty: bool = False) -> list[str]:
    """The intent check: unmet claims and unmentioned behavior from the last deep round on.

    With `skip_empty`, a list with no items is left out instead of saying "None.".
    """
    rounds = state["rounds"]
    start = max((i for i, r in enumerate(rounds) if r.get("mode") == "deep"), default=0)

    def collect(chosen: list, key: str) -> list[str]:
        seen: dict[str, None] = {}
        for r in chosen:
            check = r.get("intent_check")
            for text in check.get(key, []) if isinstance(check, dict) else []:
                seen.setdefault(text)
        return list(seen)

    lines: list[str] = []
    for label, chosen, key in (
        ("Unmet claims:", rounds[start:], "unmet_claims"),
        ("Behavior the intent does not mention:", rounds[start:], "unmentioned_behavior"),
    ):
        items = collect(chosen, key)
        if skip_empty and not items:
            continue
        lines.extend([*([""] if lines else []), label])
        lines.extend(f"- {_one_line(text)}" for text in items)
        if not items:
            lines.append("None.")
    return lines


def _round_mode(rnd: dict) -> str:
    """A round's mode as the summary shows it, naming a second opinion."""
    mode = str(rnd.get("mode"))
    if rnd.get("focus") == SECOND_OPINION_FOCUS:
        mode += " (second opinion)"
    return mode


def _with_model(rnd: dict) -> str:
    """` with <model>` for a reviewed round, nothing for a recorded one."""
    return "" if rnd.get("model") is None else f" with {rnd.get('model')}"


def _model_in_parens(rnd: dict) -> str:
    return "" if rnd.get("model") is None else f" ({rnd.get('model')})"


def _recorded_lines(state: dict) -> list[str]:
    """One bullet per recorded round: the commit and why it was recorded."""
    return [
        f"- round {r.get('number')} at {str(r.get('head'))[:7]}: {_one_line(r.get('reason'))}"
        for r in state["rounds"]
        if r.get("mode") == "recorded"
    ]


def render_summary(state: dict) -> str:
    rounds = state["rounds"]
    findings = state.get("findings", [])
    last = rounds[-1]
    risk = next(
        (r["merge_risk"] for r in reversed(rounds) if isinstance(r.get("merge_risk"), dict)),
        None,
    )
    risk_text = "n/a"
    if risk:
        risk_text = f"{risk.get('level')} ({_one_line(risk.get('reason'))})"
    plural = "" if len(rounds) == 1 else "s"
    header = (
        f"# Review of `{state.get('branch')}` ({state.get('repo_key')}): {len(rounds)} round{plural}, "
        f"last {_round_mode(last)}{_with_model(last)}, merge risk {risk_text}; "
        f"written by branch-review {PLUGIN_VERSION}"
    )

    counts = {s: 0 for s in STATUSES}
    for f in findings:
        counts[f.get("status")] = counts.get(f.get("status"), 0) + 1
    listed = ", ".join(f"{r.get('number')} {_round_mode(r)}{_model_in_parens(r)}" for r in rounds)
    out = [
        header,
        "",
        f"Rounds: {listed}",
        "",
        "## Recorded without review",
        "",
        *(_recorded_lines(state) or ["None."]),
        "",
        "## Counts",
        "",
        "| Status | Count |",
        "| --- | --- |",
    ]
    out.extend(f"| {s} | {n} |" for s, n in counts.items())

    sections = (
        ("Open and unverified", OPEN_STATUSES, _open_details),
        ("Escalated", "escalated", _resolution_note),
        ("Deferred", "deferred", _resolution_note),
        ("Accepted", "accepted", _resolution_note),
        ("Invalid", "invalid", _invalid_details),
        ("Outside the change", "outside", _resolution_note),
        ("Fixed and verified", "verified", _verified_details),
    )
    for title, status, details in sections:
        out.extend(["", "## " + title, ""])
        chosen = _with_status(findings, status)
        if not chosen:
            out.append("None.")
        for f in chosen:
            out.extend(_entry(f, details(f)))
        if title == "Open and unverified":
            out.extend(["", "## Intent check", "", *_intent_check_lines(state)])
            out.extend(["", "## Not checked", "", *(_not_checked_lines(state) or ["None."])])
        if title == "Escalated":
            out.extend(["", "## Behavior changes to confirm", "", *(_behavior_lines(state) or ["None."])])
    return "\n".join(out) + "\n"


def _with_status(findings: list, status: str | tuple) -> list:
    wanted = status if isinstance(status, tuple) else (status,)
    return [f for f in findings if f.get("status") in wanted]


def _latest_deep(rounds: list) -> dict | None:
    return next((r for r in reversed(rounds) if r.get("mode") == "deep"), None)


def _not_checked_lines(state: dict) -> list[str]:
    """The latest deep round's `not_checked`, one bullet each."""
    rnd = _latest_deep(state["rounds"]) or {}
    return [f"- {_one_line(n.get('what'))}: {_one_line(n.get('reason'))}" for n in rnd.get("not_checked") or []]


def _behavior_lines(state: dict) -> list[str]:
    return [f"- {_one_line(b.get('text'))}" for b in state.get("behavior_to_confirm") or []]


def _notes_line(state: dict) -> str:
    """The generated first line of the Review notes block, from recorded rounds only."""
    rounds = state["rounds"]
    plural = "" if len(rounds) == 1 else "s"
    by_mode = []
    for mode in ("deep", "followup", "merge-check", "recorded"):
        chosen = [r for r in rounds if r.get("mode") == mode]
        if chosen and mode == "recorded":
            by_mode.append(f"{len(chosen)} recorded")
        elif chosen:
            models = list(dict.fromkeys(str(r.get("model")) for r in chosen))
            by_mode.append(f"{len(chosen)} {mode} with {' and '.join(models)}")
    opinion = "yes" if any(r.get("focus") == SECOND_OPINION_FOCUS for r in rounds) else "no"
    deep = _latest_deep(rounds)
    risk = (deep or {}).get("merge_risk")
    risk_text = "n/a"
    if isinstance(risk, dict):
        risk_text = f"{risk.get('level')} (round {deep.get('number')}, the latest deep round)"
    return f"{len(rounds)} round{plural} ({', '.join(by_mode)}); second opinion: {opinion}; merge risk {risk_text}."


def render_review_notes(state: dict) -> str:
    """The block to paste into a PR description, generated so nothing is retyped."""
    findings = state.get("findings", [])

    def entries(status: str, details) -> list[str]:
        lines: list[str] = []
        for f in _with_status(findings, status):
            lines.extend(_entry(f, details(f)))
        return lines

    sections = (
        ("Escalated", entries("escalated", _resolution_note)),
        ("Behavior changes to confirm", _behavior_lines(state)),
        ("Deferred", entries("deferred", _resolution_note)),
        ("Accepted", entries("accepted", _resolution_note)),
        ("Invalid", entries("invalid", _invalid_details)),
        ("Outside the change", entries("outside", _resolution_note)),
        ("Recorded without review", _recorded_lines(state)),
        ("Intent check", _intent_check_lines(state, skip_empty=True)),
        ("Not checked", _not_checked_lines(state)),
    )
    out = ["## Review notes", "", _notes_line(state)]
    empty = []
    for title, lines in sections:
        if not lines:
            empty.append(title)
            continue
        out.extend(["", "### " + title, "", *lines])
    fixed = [
        f"- {f.get('id')} ({f.get('severity')}) {_one_line(f.get('title'))}" for f in _with_status(findings, "verified")
    ]
    if fixed:
        out.extend(["", "### Found and fixed", "", *fixed])
    if empty:
        out.extend(["", "Nothing to report under: " + ", ".join(empty) + "."])
    return "\n".join(out) + "\n"


def _write_summary(directory: Path, state: dict) -> None:
    """Rewrite summary.md and review-notes.md, when the latest round is recorded.

    Called after every state change, so neither file goes stale. A failure to
    write leaves the old files and never fails the command.
    """
    rounds = state.get("rounds") or []
    if not rounds or "added" not in rounds[-1]:
        return
    try:
        (directory / "summary.md").write_text(render_summary(state), encoding="utf-8")
        (directory / "review-notes.md").write_text(render_review_notes(state), encoding="utf-8")
    except OSError:
        pass


def cmd_summary(args: argparse.Namespace) -> dict:
    directory = state_path(Path(args.state_dir).expanduser(), Path(args.repo))
    state = _reported_state(directory)
    text = render_summary(state)
    notes = render_review_notes(state)
    path = directory / "summary.md"
    notes_path = directory / "review-notes.md"
    path.write_text(text, encoding="utf-8")
    notes_path.write_text(notes, encoding="utf-8")
    return {"path": str(path), "text": text, "review_notes": notes, "review_notes_path": str(notes_path)}


def _parse_ts(value: object) -> datetime | None:
    try:
        return datetime.strptime(str(value), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _older_than(updated: object, days: float) -> bool:
    """True when `updated` is more than `days` ago; an unknown age is never old."""
    ts = _parse_ts(updated)
    if ts is None:
        return False
    return datetime.now(timezone.utc) - ts > timedelta(days=days)


def _read_review(directory: Path) -> tuple[dict | None, str | None]:
    """`(state, error)`: state is None when missing or corrupt, error set if corrupt."""
    try:
        return load_state(directory), None
    except (StateError, OSError) as exc:
        return None, str(exc)


def _subdirs(parent: Path) -> list[Path]:
    """Real (non-symlink) child directories, sorted by name."""
    if not parent.is_dir():
        return []
    return sorted(p for p in parent.iterdir() if p.is_dir() and not p.is_symlink())


def _symlinked_subdirs(parent: Path) -> list[Path]:
    """Child symlinks to directories, sorted by name; never followed, only reported."""
    if not parent.is_dir() or parent.is_symlink():
        return []
    return sorted(p for p in parent.iterdir() if p.is_symlink() and p.is_dir())


def _remove_dir(state_dir: Path, path: Path) -> None:
    """rmtree `path`, but only if it is strictly inside `state_dir`."""
    root = Path(state_dir).resolve()
    target = Path(path)
    resolved = target.resolve()
    if target.is_symlink() or resolved == root or root not in resolved.parents:
        raise StateError(f"refusing to remove {path}: not inside {state_dir}")
    shutil.rmtree(str(resolved))


def _is_review_dir(path: Path) -> bool:
    """True for a branch dir this script wrote: it holds review.json or rounds/."""
    return (path / STATE_FILE).is_file() or (path / "rounds").is_dir()


def _remove_if_empty(path: Path) -> None:
    try:
        path.rmdir()
    except OSError:
        pass  # not empty, or already gone


def _git_failure(exc: subprocess.CalledProcessError) -> str:
    detail = (exc.stderr or "").strip().splitlines()
    return detail[-1] if detail else f"git {' '.join(exc.cmd[1:3])} failed"


def _live_branch_keys(repo: Path) -> set:
    """Dir keys of every local branch and `origin/` branch that exists.

    A git failure is an error: an empty set would make every dir look stale.
    """
    try:
        refs = _git(repo, "for-each-ref", "--format=%(refname)", "refs/heads", "refs/remotes/origin")
    except subprocess.CalledProcessError as exc:
        raise StateError(f"cannot list branches: {_git_failure(exc)}") from exc
    keys = set()
    for ref in refs.splitlines():
        for prefix in ("refs/heads/", "refs/remotes/origin/"):
            if ref.startswith(prefix) and ref != prefix + "HEAD":
                keys.add(ref[len(prefix) :].replace("/", "__"))
    return keys


def _ref_exists(repo: Path, ref: str) -> bool:
    """True when `ref` exists exactly (no globbing); a git failure is an error."""
    result = subprocess.run(
        ["git", "show-ref", "--verify", "--quiet", ref],
        cwd=str(repo),
        capture_output=True,
        text=True,
    )
    if result.returncode in (0, 1):
        return result.returncode == 0
    detail = result.stderr.strip().splitlines()
    raise StateError(f"cannot check {ref}: {detail[-1] if detail else 'git show-ref failed'}")


def _mtime_age_older_than(bdir: Path, days: float) -> bool:
    """For state with no readable `updated`: the age of `review.json`, else the dir."""
    target = bdir / STATE_FILE if (bdir / STATE_FILE).is_file() else bdir
    when = datetime.fromtimestamp(target.stat().st_mtime, timezone.utc)
    return datetime.now(timezone.utc) - when > timedelta(days=days)


def _try_remove(state_dir: Path, bdir: Path, removed: list, errors: list) -> None:
    """Remove `bdir`, recording it under `removed` or, on failure, under `errors`."""
    try:
        _remove_dir(state_dir, bdir)
    except (StateError, OSError) as exc:
        errors.append({"dir": str(bdir), "error": str(exc)})
    else:
        removed.append(str(bdir))


_DETACHED_KEY = re.compile(r"detached-[0-9a-f]{7}")


def _prune_stale(repo: Path, bdir: Path, live_keys: set, days: int) -> bool | None:
    """Whether `bdir` is stale; None when its branch name cannot be read (skip it).

    Raises StateError or OSError when a check fails.
    """
    state, _err = _read_review(bdir)
    if _DETACHED_KEY.fullmatch(bdir.name):
        if state is None:
            return _mtime_age_older_than(bdir, days)
        return _older_than(state.get("updated"), days)
    if state is None:
        return bdir.name not in live_keys
    if _older_than(state.get("updated"), days):
        return True
    name = state.get("branch")
    if not isinstance(name, str) or not name:
        return None
    return not (_ref_exists(repo, f"refs/heads/{name}") or _ref_exists(repo, f"refs/remotes/origin/{name}"))


def cmd_prune(args: argparse.Namespace) -> dict:
    """Remove state whose branch is gone or whose last update is over `--days` old.

    Anything ambiguous is kept: an unparseable `updated` never counts as old, and
    a dir whose branch name cannot be read is kept (and reported as skipped)
    rather than guessed from its name. A dir with no readable `review.json` is
    removed only when no existing branch maps to its dir name. A `detached-<sha>`
    dir never matches a branch, so it is removed only by age (its `updated`, or
    the file's age when unreadable). A dir holding neither `review.json` nor
    `rounds/`, or a symlink (the repo dir itself included), is not review state:
    it is skipped. A dir whose check fails goes under `errors` and the rest are
    still processed.
    """
    repo = Path(args.repo)
    state_dir = Path(args.state_dir).expanduser()
    _, current_key = branch_key(repo)
    repo_dir = state_dir / repo_key(repo)
    live_keys = _live_branch_keys(repo)

    if repo_dir.is_symlink():
        return {"removed": [], "skipped": [str(repo_dir)], "errors": []}

    removed: list[str] = []
    skipped: list[str] = [str(p) for p in _symlinked_subdirs(repo_dir)]
    errors: list[dict] = []
    for bdir in _subdirs(repo_dir):
        if bdir.name == current_key:
            continue
        if not _is_review_dir(bdir):
            skipped.append(str(bdir))
            continue
        try:
            stale = _prune_stale(repo, bdir, live_keys, args.days)
        except (StateError, OSError) as exc:
            errors.append({"dir": str(bdir), "error": str(exc)})
            continue
        if stale is None:
            skipped.append(str(bdir))
        elif stale:
            _try_remove(state_dir, bdir, removed, errors)
    _remove_if_empty(repo_dir)
    return {"removed": removed, "skipped": skipped, "errors": errors}


def _list_shape_error(state: dict) -> str | None:
    """Why `state` cannot be listed, or None: the fields `list` reads must have their types."""
    if not isinstance(state.get("updated"), str):
        return "updated is not a string"
    if not isinstance(state.get("rounds", []), list):
        return "rounds is not a list"
    findings = state.get("findings", [])
    if not isinstance(findings, list) or not all(isinstance(f, dict) for f in findings):
        return "findings is not a list of objects"
    return None


def cmd_list(args: argparse.Namespace) -> dict:
    state_dir = Path(args.state_dir).expanduser()
    reviews = []
    for rdir in _subdirs(state_dir):
        for bdir in _subdirs(rdir):
            if not (bdir / STATE_FILE).exists():
                continue
            state, error = _read_review(bdir)
            entry = {
                "repo_key": rdir.name,
                "branch": bdir.name.replace("__", "/"),
                "rounds": None,
                "updated": None,
                "open": None,
                "dir": str(bdir),
            }
            if state is not None:
                error = _list_shape_error(state)
                if error:
                    error = f"corrupt state file {bdir / STATE_FILE}: {error}"
                    state = None
            if state is None:
                entry["error"] = error or "unreadable state file"
            else:
                entry["repo_key"] = state.get("repo_key") or rdir.name
                entry["branch"] = state.get("branch") or entry["branch"]
                entry["rounds"] = len(state.get("rounds", []))
                entry["updated"] = state.get("updated")
                entry["open"] = sum(1 for f in state.get("findings", []) if f.get("status") in OPEN_STATUSES)
            reviews.append(entry)
    reviews.sort(key=lambda e: e["updated"] or "", reverse=True)
    return {"reviews": reviews}


def cmd_cleanup(args: argparse.Namespace) -> dict:
    """Remove state by `--scope`; `--older-than` never removes a dir of unknown age.

    Only `<repo>/<branch>` dirs holding `review.json` or `rounds/` are removed;
    any other dir, and any symlink (a symlinked repo dir included), is reported
    under `skipped` and left alone.
    """
    state_dir = Path(args.state_dir).expanduser()

    only = None
    if args.scope == "all":
        repo_dirs = _subdirs(state_dir)
    else:
        repo = Path(args.repo)
        repo_dirs = [state_dir / repo_key(repo)]
        if args.scope == "branch":
            only = state_path(state_dir, repo)

    removed: list[str] = []
    skipped: list[str] = []
    errors: list[dict] = []
    if args.scope == "all":
        skipped.extend(str(p) for p in _symlinked_subdirs(state_dir))
    for rdir in repo_dirs:
        if rdir.is_symlink():
            skipped.append(str(rdir))
            continue
        if only is None:
            candidates = _subdirs(rdir)
            skipped.extend(str(p) for p in _symlinked_subdirs(rdir))
        else:
            candidates = [only]
        for bdir in candidates:
            if not bdir.is_dir():
                continue
            if bdir.is_symlink() or not _is_review_dir(bdir):
                skipped.append(str(bdir))
                continue
            if args.older_than is not None:
                state, _err = _read_review(bdir)
                if not _older_than(state.get("updated") if state else None, args.older_than):
                    continue
            _try_remove(state_dir, bdir, removed, errors)
        _remove_if_empty(rdir)
    return {"removed": removed, "skipped": skipped, "errors": errors}


# --- CLI -------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    # The global options are accepted before or after the subcommand. Both
    # declarations suppress their default, or the subcommand's would replace a
    # value given before it; `_apply_defaults` fills them in after parsing.
    def add_globals(target: argparse.ArgumentParser) -> None:
        target.add_argument(
            "--state-dir",
            default=argparse.SUPPRESS,
            help=f"root directory for review state (default: {DEFAULT_STATE_DIR})",
        )
        target.add_argument(
            "--repo",
            default=argparse.SUPPRESS,
            help="repository path (default: current directory)",
        )

    common = _Parser(add_help=False)
    add_globals(common)

    parser = _Parser(prog="review_state.py", description="branch-review state helper")
    add_globals(parser)
    sub = parser.add_subparsers(dest="command", metavar="<command>")
    sub.required = True

    locate = sub.add_parser("locate", parents=[common], help="find state for the current branch")
    locate.add_argument("--base", help="base ref (default: the stored base ref, else the detected default)")
    locate.add_argument("--paths", nargs="+", help="the --paths the next round will use")
    wish = locate.add_mutually_exclusive_group()
    wish.add_argument(
        "--second-opinion",
        dest="second_opinion",
        action="store_true",
        default=None,
        help="count the invoker's --second-opinion, which the next begin-round stores",
    )
    wish.add_argument(
        "--no-second-opinion",
        dest="second_opinion",
        action="store_false",
        help="count the invoker's --no-second-opinion, which the next begin-round stores",
    )
    locate.set_defaults(func=cmd_locate)

    snap = sub.add_parser(
        "snapshot",
        parents=[common],
        help="commit the working tree to a dangling object without touching refs",
    )
    snap.set_defaults(func=cmd_snapshot)

    begin = sub.add_parser("begin-round", parents=[common], help="record the start of a review round")
    begin.add_argument("--mode", required=True, choices=("deep", "followup", "merge-check"))
    begin.add_argument("--model", required=True)
    begin.add_argument("--snapshot", help="snapshot sha to review (default: take one now)")
    begin.add_argument(
        "--base-ref",
        help="ref the branch is reviewed against (default: the stored base ref, else the detected default)",
    )
    begin.add_argument("--intent-file", help="file holding the change's stated intent")
    begin.add_argument(
        "--focus",
        choices=(SECOND_OPINION_FOCUS,),
        help="what the round is for; second-opinion is the independent re-review of a risky change",
    )
    begin.add_argument("--mode-reason", help="why the mode was chosen, as `locate` reported it")
    begin.add_argument(
        "--generated",
        nargs="+",
        help="globs for generated files, kept out of the size checks; stored, and reused when absent",
    )
    begin.add_argument(
        "--notes-file",
        metavar="PATH",
        help="earlier reviewers' notes for the whole-branch review; stored, and reused when absent",
    )
    begin.add_argument(
        "--related-branch",
        nargs="+",
        metavar="REF",
        help="branches built alongside that mirror this change; stored, and reused when absent; . clears",
    )
    begin.add_argument(
        "--deep-model",
        help="model for a follow-up promoted to a deep round (default: the --model value)",
    )
    opinion = begin.add_mutually_exclusive_group()
    opinion.add_argument(
        "--second-opinion",
        dest="second_opinion",
        action="store_true",
        default=None,
        help="ask for a second opinion once no finding is open",
    )
    opinion.add_argument(
        "--no-second-opinion",
        dest="second_opinion",
        action="store_false",
        help="skip the automatic second opinion",
    )
    begin.add_argument(
        "--paths",
        nargs="+",
        help="glob patterns restricting the reviewed files; a directory or trailing / means everything under it",
    )
    begin.set_defaults(func=cmd_begin_round)

    record = sub.add_parser(
        "record",
        parents=[common],
        help="save a round for a commit already on the remote branch, without a reviewer",
    )
    record.add_argument("--commit", default="HEAD", help="commit to record (default: HEAD)")
    record.add_argument("--reason-file", required=True, metavar="PATH", help="file saying why the commit is recorded")
    record.add_argument("--base", help="base ref (default: the stored base ref, else the detected default)")
    record.set_defaults(func=cmd_record)

    rec = sub.add_parser("record-findings", parents=[common], help="record a reviewer report into the ledger")
    rec.add_argument("--file", required=True, help="reviewer report (markdown) or bare JSON")
    rec.add_argument("--report", action="store_true", help="keep a copy as rounds/<N>.md")
    rec.set_defaults(func=cmd_record_findings)

    setst = sub.add_parser("set-status", parents=[common], help="update a finding's status")
    setst.add_argument("--id", required=True)
    setst.add_argument("--status", required=True, choices=("fixed", "invalid", "escalated", "open"))
    setst.add_argument("--commit", help="commit that fixes the finding")
    setst.add_argument(
        "--intent-quote-file",
        metavar="PATH",
        help="a line of the intent that settles a finding's fix options (needed to mark one fixed)",
    )
    note = setst.add_mutually_exclusive_group()
    note.add_argument("--note", help="explanation")
    note.add_argument("--note-file", metavar="PATH", help="read the explanation from a UTF-8 file")
    setst.set_defaults(func=cmd_set_status)

    disc = sub.add_parser("discuss", parents=[common], help="append a discussion entry")
    disc.add_argument("--id", required=True)
    disc.add_argument("--from", dest="source", required=True, choices=("orchestrator", "reviewer"))
    text = disc.add_mutually_exclusive_group(required=True)
    text.add_argument("--text")
    text.add_argument("--text-file", metavar="PATH", help="read the text from a UTF-8 file")
    disc.set_defaults(func=cmd_discuss)

    res = sub.add_parser("resolve", parents=[common], help="record the developer's decision")
    res.add_argument("--id", required=True)
    res.add_argument("--decision", required=True, choices=("fix", "defer", "accept"))
    note = res.add_mutually_exclusive_group(required=True)
    note.add_argument("--note", help="the decision, in the developer's words")
    note.add_argument("--note-file", metavar="PATH", help="read the decision from a UTF-8 file")
    res.set_defaults(func=cmd_resolve)

    confirm = sub.add_parser(
        "confirm-behavior",
        parents=[common],
        help="record a behavior change existing users rely on, for the developer to confirm",
    )
    confirm.add_argument("--text-file", metavar="PATH", required=True, help="the behavior, as a UTF-8 file")
    confirm.set_defaults(func=cmd_confirm_behavior)

    stat = sub.add_parser("status", parents=[common], help="counts and whether to continue")
    stat.add_argument(
        "--since-round",
        type=int,
        default=0,
        metavar="N",
        help="count only rounds numbered above N toward the round cap (default: 0)",
    )
    stat.set_defaults(func=cmd_status)

    cov = sub.add_parser("covered", parents=[common], help="whether a commit is covered by a clean review")
    cov.add_argument("--commit", default="HEAD", help="commit to judge (default: HEAD)")
    cov.add_argument("--base", help="base ref (default: the stored base ref, else the detected default)")
    cov.set_defaults(func=cmd_covered)

    summ = sub.add_parser("summary", parents=[common], help="write and print summary.md for the branch")
    summ.set_defaults(func=cmd_summary)

    prune = sub.add_parser("prune", parents=[common], help="remove state for branches that are gone or stale")
    prune.add_argument("--days", type=int, default=STALE_DAYS, help="age in days that counts as stale")
    prune.set_defaults(func=cmd_prune)

    lst = sub.add_parser("list", parents=[common], help="list every review in the state dir")
    lst.set_defaults(func=cmd_list)

    clean = sub.add_parser(
        "cleanup",
        parents=[common],
        help="delete review state for one scope (branch and repo need a git repo)",
    )
    clean.add_argument("--scope", required=True, choices=("branch", "repo", "all"))
    clean.add_argument("--older-than", type=int, metavar="D", help="only dirs last updated more than D days ago")
    clean.set_defaults(func=cmd_cleanup)
    return parser


def _check_state_dir(value: str) -> None:
    """Reject an empty, blank or relative `--state-dir` before any command runs.

    `prune` and `cleanup` delete directories under the state dir, so a value
    that resolves against the current directory must never reach them.
    """
    if not value or not value.strip():
        raise StateError("--state-dir must not be empty")
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise StateError(f"--state-dir must be an absolute path, got {value!r}")
    resolved = path.resolve()
    if resolved == Path(resolved.anchor) or resolved == Path.home().resolve():
        raise StateError(f"--state-dir must not be the filesystem root or the home directory, got {value!r}")


def _apply_defaults(args: argparse.Namespace) -> argparse.Namespace:
    """Fill in the global options that neither position gave."""
    if not hasattr(args, "state_dir"):
        args.state_dir = DEFAULT_STATE_DIR
    if not hasattr(args, "repo"):
        args.repo = "."
    return args


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    try:
        args = _apply_defaults(parser.parse_args(argv))
        _check_state_dir(args.state_dir)
        result = args.func(args)
    except subprocess.CalledProcessError as exc:
        print(f"error: {_git_failure(exc)}", file=sys.stderr)
        return 1
    except (StateError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    errors = result.get("errors")
    if errors:
        print(f"error: could not remove {len(errors)} of the directories; see errors", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
