import json
from pathlib import Path
import re
import tempfile
import unittest

from agents_helpers import agents_md_inventory as inv
from agents_helpers import call, commit_all, make_repo, write

PLUGIN = Path(__file__).resolve().parent.parent
AGENT = PLUGIN / "agents" / "agents-md-reviewer.md"

_FENCE = re.compile(r"^```(\w+)\n(.*?)\n```$", re.S | re.M)


def parse_frontmatter(path: Path) -> dict:
    """Simple `key: value` lines between the first two `---` lines."""
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines and lines[0] == "---", "SKILL.md must start with frontmatter"
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
            "`accurate`",
            "`valuable`",
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
        for key in ("REPO", "SCRIPT", "FILE", "ROLE", "SCOPE", "GUIDELINES", "POLICY", "RULINGS", "AGENTS_MD_DIRS"):
            self.assertIn(f"`{key}`", text)
        self.assertIn("python3 <SCRIPT> blocks --repo <REPO> <FILE>", text)

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
                if "target_kind" in verdict:
                    self.assertIn(verdict["target_kind"], inv.TARGET_KINDS)
                actions.add(verdict["action"])
        self.assertEqual(actions, set(inv.ACTIONS))

    def test_example_passes_the_validator(self):
        # The worked example's file and verdicts pass check-verdicts, so the example can't teach a bad shape.
        (sample,) = fences("markdown")
        (example,) = fences("json")
        data = json.loads(example)
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(Path(tmp))
            write(repo, data["file"], sample + "\n")
            for verdict in data["verdicts"]:
                if verdict.get("target_kind") == "comment":
                    write(repo, verdict["target"], "// placeholder\n")
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


if __name__ == "__main__":
    unittest.main()
