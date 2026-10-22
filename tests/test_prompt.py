from crewguard.moderation.prompt import CATEGORIES, EXAMPLES, build_messages

from .eval_messages import ALL_ALLOW, ALL_FLAG


def _norm(s: str) -> str:
    return " ".join(s.lower().split())


def test_eval_set_is_disjoint_from_few_shot():
    """Scoring the model on its own few-shot examples measures nothing. Keep them separate."""
    few_shot = {_norm(m) for m, *_ in EXAMPLES}
    evaluated = {_norm(m) for m, _ in ALL_ALLOW + ALL_FLAG}
    overlap = few_shot & evaluated
    assert not overlap, f"eval messages leaked into the prompt few-shot: {overlap}"


def test_few_shot_examples_use_valid_categories():
    for msg, action, category, _reason in EXAMPLES:
        assert action in ("allow", "flag"), msg
        assert category in CATEGORIES, msg
        assert (category == "none") == (action == "allow"), msg


def test_few_shot_teaches_both_sides_of_the_boundary():
    actions = [a for _, a, _, _ in EXAMPLES]
    assert actions.count("allow") >= 4
    assert actions.count("flag") >= 4


def test_build_messages_puts_the_target_message_last():
    msgs = build_messages("nobody here can stand you")
    assert msgs[0]["role"] == "system"
    assert msgs[-1] == {"role": "user", "content": "nobody here can stand you"}


def test_system_prompt_warns_about_injection():
    msgs = build_messages("hi")
    system = msgs[0]["content"]
    assert "IGNORE" in system and "instructions" in system.lower()
