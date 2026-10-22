"""CrewGuard's constitution — the immutable core directives.

Loaded read-only at startup and fed to the classifier-adjacent leadership assistant as the top of
the prompt hierarchy (constitution > leadership policy > message content). CrewGuard has no file-writing
tool, so the file is immutable by construction; this loader just reads it. Filesystem in, string
out — no Discord, no DB (see AGENTS.md).
"""

from pathlib import Path

CONSTITUTION_PATH = Path(__file__).parent.parent / "docs" / "governance" / "constitution.md"


def load_constitution() -> str:
    return CONSTITUTION_PATH.read_text().strip()
