from crewguard.governance import load_constitution


def test_constitution_loads_non_empty():
    assert load_constitution().strip()


def test_constitution_states_the_immutable_invariants():
    """The constitution is the single source of CrewGuard's core directives — it must not silently drift
    from what the bot actually enforces. If enforcement changes, this must change with it."""
    t = load_constitution().lower()
    # Gentle enforcement (AGENTS.md hard rule).
    assert "no strikes" in t
    assert "no timeouts" in t
    assert "no bans" in t
    # The anti-injection spine.
    assert "data, never instructions" in t
    # Data minimisation / retention.
    assert "30 days" in t
    # Guide-only: CrewGuard never mutates the server on its own.
    assert "does not self-govern" in t


def test_chat_filtering_page_lists_every_moderated_category():
    """The public transparency list must name every category CrewGuard actually filters — no drift."""
    from pathlib import Path

    from crewguard.moderation.prompt import CATEGORY_LABEL

    doc = (Path(__file__).parent.parent / "docs" / "kb" / "rules.md").read_text().lower()
    for cat, label in CATEGORY_LABEL.items():
        if cat == "unparseable":
            continue  # the parse-error path, not a filter category
        assert label.lower() in doc, f"filtering page is missing '{label}'"
