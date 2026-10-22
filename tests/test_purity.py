"""The hard invariant from AGENTS.md, enforced.

classifier / prompt / guild_spec must stay free of Discord and DB imports. That is what lets the
part the project lives or dies on be tested without a gateway.
"""

import ast
import pathlib

import pytest

SRC = pathlib.Path(__file__).parent.parent / "crewguard"

PURE = [
    "moderation/classifier.py",
    "moderation/prompt.py",
    "guild_spec.py",
    "ollama.py",
    "textutil.py",
    "assistant/kb.py",
    "assistant/prompts.py",
    "assistant/answerer.py",
]
FORBIDDEN = (
    "discord",
    "sqlmodel",
    "sqlalchemy",
    "crewguard.db",
    "crewguard.models",
)


def _imports(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text())
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


@pytest.mark.parametrize("rel", PURE)
def test_module_stays_pure(rel):
    found = _imports(SRC / rel)
    dirty = {i for i in found for f in FORBIDDEN if i == f or i.startswith(f + ".")}
    assert not dirty, f"{rel} must not import {dirty} — see AGENTS.md"
