from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlmodel import Session

from crewguard import config_store as cfg
from crewguard import guild_spec as spec
from crewguard.cogs.moderation import _tuning_examples
from crewguard.feedback_ui import FeedbackButton
from crewguard.models import ModerationLog
from crewguard.moderation.prompt import build_messages

from .discord_mocks import make_member, make_role


@pytest.fixture
def row(session):
    r = ModerationLog(
        tier="classifier",
        author_id=42,
        author_name="crew1",
        channel_id=100,
        content="wow real helpful crew tonight, thanks for nothing",
        category="poor_attitude",
        reason="sarcastic jab",
    )
    session.add(r)
    session.commit()
    session.refresh(r)
    return r


def _interaction(engine, member):
    itx = MagicMock()
    itx.client.engine = engine
    itx.user = member
    member.__str__ = MagicMock(return_value=member.name)
    itx.user.guild_permissions.administrator = False
    itx.response.send_message = AsyncMock()
    itx.response.edit_message = AsyncMock()
    embed = MagicMock()
    itx.message.embeds = [embed]
    return itx


def _gm(session):
    cfg.set_setting(session, cfg.role_key(spec.GM), "701")
    member = make_member("boss", member_id=7, role_names=())
    member.roles = [make_role(spec.GM, role_id=701)]
    return member


async def test_too_strict_records_verdict(engine, session, row):
    itx = _interaction(engine, _gm(session))
    await FeedbackButton("strict", row.id).callback(itx)

    with Session(engine) as s:
        updated = s.get(ModerationLog, row.id)
    assert updated.feedback == "too_strict"
    assert "boss" in updated.feedback_by
    itx.response.edit_message.assert_awaited_once()


async def test_good_call_flips_a_too_strict_back(engine, session, row):
    gm = _gm(session)
    await FeedbackButton("strict", row.id).callback(_interaction(engine, gm))
    await FeedbackButton("good", row.id).callback(_interaction(engine, gm))

    with Session(engine) as s:
        assert s.get(ModerationLog, row.id).feedback == "good_call"


async def test_crew_cannot_review(engine, session, row):
    crew = make_member("crew1", member_id=8)
    crew.roles = [make_role(spec.CREW, role_id=999)]
    itx = _interaction(engine, crew)

    await FeedbackButton("strict", row.id).callback(itx)

    with Session(engine) as s:
        assert s.get(ModerationLog, row.id).feedback is None
    assert "Only the GM" in itx.response.send_message.await_args.args[0]


def test_too_strict_rows_become_allow_examples(session, row):
    row.feedback = "too_strict"
    session.add(row)
    session.commit()

    examples = _tuning_examples(session)
    assert row.content in examples

    msgs = build_messages("new message", extra_allow=examples)
    tuned = [m for m in msgs if m["content"] == row.content]
    assert len(tuned) == 1
    # the very next turn teaches "allow"
    idx = msgs.index(tuned[0])
    assert '"action": "allow"' in msgs[idx + 1]["content"]
    # target message still last
    assert msgs[-1] == {"role": "user", "content": "new message"}


def test_good_call_rows_are_not_examples(session, row):
    row.feedback = "good_call"
    session.add(row)
    session.commit()
    assert _tuning_examples(session) == ()


def test_unreviewed_rows_are_not_examples(session, row):
    assert _tuning_examples(session) == ()
