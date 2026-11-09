"""A child's brief: the fixed prompt text in templates/, filled from the run state.

`unit-brief.md` is a unit's prompt, `integration-brief.md` the integration unit's. A `{{name}}` in a template is
either a fragment slot, filled with a file from templates/fragments/ (or left out), or a value from the run.
Fragments may hold more slots and values. Values are put in last, in one pass, so text from the run (the facts
file, the manager's extra text, a title) is never read for placeholders. A line that holds nothing but a slot
whose text is empty is dropped, and runs of blank lines this leaves are cut to one.

Placeholders the children fill (`<sha>`, `<branch>`, `<id>`, ...) are plain text here and stay as they are.
"""

from __future__ import annotations

from pathlib import Path
import re

import runstate
from runstate import RunError

TEMPLATES = Path(__file__).resolve().parent.parent / "templates"
FACTS = "facts.md"
PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")

# In the integration mode every sync a unit makes names its base, the integration branch: the tool would
# otherwise sync with the repository's default branch.
SYNC_BASE = " with `base: {{base}}`"


def _fragment(name: str) -> str:
    return (TEMPLATES / "fragments" / f"{name}.md").read_text(encoding="utf-8").rstrip("\n")


def _slots(state: dict, integration_child: bool, *, gate: bool, designed: bool) -> dict[str, str]:
    """Each fragment slot's text, "" where the slot is left out."""
    mode = bool(state.get("integration"))
    unit_mode = mode and not integration_child
    return {
        "gate": _fragment("integration-gate" if integration_child else "gate") if gate else "",
        "start": _fragment("integration-start" if mode else "start"),
        "subagent_block": _fragment("subagent-block"),
        "parts_8_to_10": _fragment("parts-8-to-10"),
        "sandbox": _fragment("sandbox") if unit_mode else "",
        "sync_base": SYNC_BASE if unit_mode else "",
        "designed": _fragment("designed") if designed else "",
    }


def _is_empty_slot(line: str, empty: set[str]) -> bool:
    match = PLACEHOLDER.fullmatch(line.strip())
    return bool(match) and match[1] in empty


def _expand(text: str, slots: dict[str, str], empty: set[str]) -> str:
    """Fill the fragment slots in `text`, recursively, dropping lines that hold only an empty slot or value."""
    lines = [line for line in text.split("\n") if not _is_empty_slot(line, empty)]

    def fill(m: re.Match) -> str:
        if m[1] not in slots:
            return m[0]  # a value, put in later
        return _expand(slots[m[1]], slots, empty)

    return PLACEHOLDER.sub(fill, "\n".join(lines))


def _values(run_dir: Path, state: dict, name: str, record: dict, extra: str) -> dict[str, str]:
    integration_child = record.get("integration", False)
    facts_path = run_dir / FACTS
    try:
        facts = facts_path.read_text(encoding="utf-8") if facts_path.is_file() else ""
    except (OSError, ValueError) as exc:
        raise RunError(f"cannot read {facts_path}: {getattr(exc, 'strerror', None) or exc}") from None
    values = {
        "run": str(run_dir.absolute()),
        "name": name,
        "title": record.get("title") or name,
        "spec": record["spec"],
        # The integration unit's own base, for parts 8 to 10, is the run's: its final PR targets it.
        "base": state["base"] if integration_child else runstate.unit_base(state),
        "run_base": state["base"],
        "repo": state["repo"],
        "manager": state["manager"],
        "facts": facts.lstrip("\n").rstrip(),
        "extra": extra.lstrip("\n").rstrip(),
    }
    if state.get("integration"):
        values["integration"] = state["integration"]["branch"]
    return values


def render(run_dir: Path, state: dict, unit: str, *, extra: str = "", gate: bool = True, designed: bool = False) -> str:
    """The brief for `unit`: its template filled in. Refuses a run with no manager id, a unit with no spec, and a
    template that names a placeholder the run cannot fill."""
    record = runstate._unit(state, unit)
    if not state.get("manager"):
        raise RunError(f"no manager session id: run.py set {run_dir} manager <id>")
    if not record.get("spec"):
        raise RunError(f"unit {unit} has no spec: run.py unit {run_dir} {unit} --spec P")
    return _render(run_dir, state, unit, record, extra=extra, gate=gate, designed=designed)


def render_card(run_dir: Path, state: dict, name: str, *, title: str, spec: str, extra: str = "") -> str:
    """The brief for a card: work outside every unit, proposed before it is a unit.

    The unit is added only once the user starts the card, so the title and spec come from the caller. A card's
    brief is a unit's, gate included, with the integration mode's unit changes in such a run; it is never the
    integration child's. Refuses a name that is not a valid unit name or is already a unit, a title or spec that
    is empty or not one line, and a run with no manager id.
    """
    if not re.fullmatch(runstate.UNIT_RE, name):
        raise RunError(f"bad unit name {name!r}: use letters, digits, '_' and '-', starting with a letter or digit")
    if name in state["units"]:
        raise RunError(f"unit {name!r} already exists: a card is for work outside every unit")
    for label, text in (("title", title), ("spec", spec)):
        if not text or "\n" in text or "\r" in text:
            raise RunError(f"bad {label} {text!r}: use a single line")
    if not state.get("manager"):
        raise RunError(f"no manager session id: run.py set {run_dir} manager <id>")
    record = {"title": title, "spec": spec, "integration": False}
    return _render(run_dir, state, name, record, extra=extra, gate=True, designed=False)


def _render(run_dir: Path, state: dict, unit: str, record: dict, *, extra: str, gate: bool, designed: bool) -> str:
    integration_child = record.get("integration", False)
    if integration_child and designed:
        raise RunError("the integration unit is not designed with the owner: drop --designed")
    values = _values(run_dir, state, unit, record, extra)
    slots = _slots(state, integration_child, gate=gate, designed=designed)
    empty = {name for name, source in {**slots, **values}.items() if source == ""}
    template = (TEMPLATES / ("integration-brief.md" if integration_child else "unit-brief.md")).read_text(
        encoding="utf-8"
    )
    text = re.sub(r"\n{3,}", "\n\n", _expand(template, slots, empty)).strip("\n") + "\n"
    left: list[str] = []

    def fill(m: re.Match) -> str:
        if m[1] in values:
            return values[m[1]]
        left.append(m[0])
        return m[0]

    text = PLACEHOLDER.sub(fill, text)
    if left:
        raise RunError(f"brief left placeholders: {', '.join(dict.fromkeys(left))}")
    return text
