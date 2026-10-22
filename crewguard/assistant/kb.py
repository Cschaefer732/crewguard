"""Knowledge base loader. Pure — filesystem in, strings out (see AGENTS.md).

Each docs/kb/*.md file is one topic; its first `# heading` is the topic title CrewGuard cites in
answers. An empty KB is a valid state: every question then routes to "ask a manager".
"""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Topic:
    name: str
    body: str


def load_kb(kb_dir: Path) -> tuple[Topic, ...]:
    topics = []
    for f in sorted(kb_dir.glob("*.md")):
        body = f.read_text().strip()
        if not body:
            continue
        first = body.splitlines()[0]
        name = first.lstrip("# ").strip() if first.startswith("#") else f.stem
        topics.append(Topic(name=name, body=body))
    return tuple(topics)


def render_kb(topics: tuple[Topic, ...]) -> str:
    if not topics:
        return "(the knowledge base is empty)"
    return "\n\n---\n\n".join(t.body for t in topics)
