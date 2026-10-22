from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlmodel import Session, select

from crewguard import config_store as cfg
from crewguard import guild_spec as spec
from crewguard.cogs.moderation import Moderation
from crewguard.models import LeadershipNote
from crewguard.moderation.classifier import Verdict

from .discord_mocks import make_bot, make_channel, make_member, make_message, make_role


@pytest.fixture
def bot(engine, session):
    cfg.set_setting(session, cfg.role_key(spec.GM), "701")
    cfg.set_setting(session, cfg.role_key(spec.APPRENTICE), "702")
    b = make_bot(engine, channel=make_channel())
    b.classifier = MagicMock()
    b.classifier.classify = AsyncMock(return_value=Verdict("allow", "none", ""))
    return b


def _msg_from(role_id: int | None, content="close at 10 on sundays, not 11"):
    author = make_member("boss", member_id=50)
    if role_id:
        role = make_role("x", role_id=role_id)
        author.roles = [role]
    return make_message(content, author=author)


def _notes(engine):
    with Session(engine) as s:
        return s.exec(select(LeadershipNote)).all()


async def test_gm_message_is_captured(bot, engine):
    await Moderation(bot).on_message(_msg_from(701))
    notes = _notes(engine)
    assert len(notes) == 1
    assert notes[0].content == "close at 10 on sundays, not 11"


async def test_apprentice_message_is_not_captured(bot, engine):
    """The assistant manager is treated as a service supervisor now — only the GM's words are captured."""
    await Moderation(bot).on_message(_msg_from(702))
    assert _notes(engine) == []


async def test_crew_message_is_not_captured(bot, engine):
    await Moderation(bot).on_message(_msg_from(999))
    assert _notes(engine) == []


async def test_flagged_leadership_message_is_not_captured(bot, engine):
    """A removed message is not doctrine."""
    bot.classifier.classify.return_value = Verdict("flag", "poor_attitude", "contempt")
    await Moderation(bot).on_message(_msg_from(701, "you people are useless"))
    assert _notes(engine) == []
