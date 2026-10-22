from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from sqlmodel import Session, select

from crewguard import config_store as cfg
from crewguard.assistant.answerer import SILENT, Answer, Interjection
from crewguard.cogs.assistant import DAILY_CAP, Assistant
from crewguard.models import UnansweredQuestion

from .discord_mocks import make_bot, make_channel, make_member, make_message


class FakeClock:
    def __init__(self):
        self.t = 100_000.0

    def __call__(self):
        return self.t


@pytest.fixture
def mod_log():
    return make_channel("mod-log", channel_id=900)


@pytest.fixture
def bot(engine, mod_log, session):
    cfg.set_setting(session, cfg.channel_key("mod-log"), "900")
    b = make_bot(engine, channel=mod_log)
    b.recently_removed = []
    b.settings = MagicMock(guild_id=1, crew_qa_enabled=True)
    b.get_guild = MagicMock(return_value=None)  # DM senders resolve to crew unless a test says so
    b.answerer = MagicMock()
    b.answerer.answer = AsyncMock(
        return_value=Answer(
            kind="answer", topic="Checking your hours", answer="Open the schedule app."
        )
    )
    b.answerer.consider_interjecting = AsyncMock(return_value=SILENT)
    # Leadership guidance is a separate answerer (the bigger model).
    b.leadership_answerer = MagicMock()
    b.leadership_answerer.answer = AsyncMock(
        return_value=Answer(
            kind="answer", topic="managing CrewGuard", answer="Press ❌ Too strict."
        )
    )
    return b


@pytest.fixture
def cog(bot):
    c = Assistant(bot, clock=FakeClock())
    c.moderation_grace = 0.0  # tests don't wait out the real window
    return c


def _dm(content, author=None):
    msg = make_message(content, author=author)
    msg.guild = None
    # typing() context manager
    typing_cm = MagicMock()
    typing_cm.__aenter__ = AsyncMock()
    typing_cm.__aexit__ = AsyncMock()
    msg.channel.typing = MagicMock(return_value=typing_cm)
    return msg


def _guild_msg(content, bot, mentioned=False, author=None):
    msg = make_message(content, author=author)
    msg.mentions = [bot.user] if mentioned else []
    typing_cm = MagicMock()
    typing_cm.__aenter__ = AsyncMock()
    typing_cm.__aexit__ = AsyncMock()
    msg.channel.typing = MagicMock(return_value=typing_cm)
    return msg


async def test_dm_gets_answer_with_topic_citation(cog, bot):
    msg = _dm("how do i see my hours")
    await cog.on_message(msg)
    reply = msg.channel.send.await_args.args[0]
    assert "the schedule app" in reply
    assert "Checking your hours" in reply


async def test_dm_non_work_is_refused(cog, bot):
    bot.answerer.answer.return_value = Answer(kind="not_work_related", topic="", answer="")
    msg = _dm("write me a poem about minecraft")
    await cog.on_message(msg)
    assert "work" in msg.channel.send.await_args.args[0].lower()


async def test_dm_unknown_refuses_logs_and_hits_mod_log(cog, bot, engine, mod_log):
    bot.answerer.answer.return_value = Answer(kind="not_in_kb", topic="", answer="")
    msg = _dm("how much pto do i accrue per hour?")
    await cog.on_message(msg)

    assert "manager" in msg.channel.send.await_args.args[0].lower()
    with Session(engine) as s:
        rows = s.exec(select(UnansweredQuestion)).all()
    assert len(rows) == 1
    assert rows[0].surface == "dm"
    assert any("Unanswered" in c.args[0] for c in mod_log.send.await_args_list if c.args)


async def test_cooldown_blocks_rapid_fire(cog, bot):
    author = make_member("spammer", member_id=5)
    m1, m2 = _dm("q1", author=author), _dm("q2", author=author)
    await cog.on_message(m1)
    await cog.on_message(m2)  # 0s later — inside the 20s cooldown

    assert bot.answerer.answer.await_count == 1
    assert "plenty for today" in m2.channel.send.await_args.args[0]


async def test_daily_cap(cog, bot):
    author = make_member("chatty", member_id=6)
    for i in range(DAILY_CAP + 2):
        cog._clock.t += 30  # past cooldown each time
        await cog.on_message(_dm(f"q{i}", author=author))
    assert bot.answerer.answer.await_count == DAILY_CAP


async def test_mention_answers_in_channel(cog, bot):
    msg = _guild_msg(f"{bot.user.mention} where are the training videos", bot, mentioned=True)
    await cog.on_message(msg)
    bot.answerer.answer.assert_awaited_once()


async def test_removed_message_is_never_answered(cog, bot):
    msg = _guild_msg(f"{bot.user.mention} do your own job for once?", bot, mentioned=True)
    bot.recently_removed = [msg.id]
    await cog.on_message(msg)
    bot.answerer.answer.assert_not_awaited()
    msg.channel.send.assert_not_awaited()


async def test_non_question_messages_skip_the_interject_gate(cog, bot):
    await cog.on_message(_guild_msg("great shift everyone", bot))
    bot.answerer.consider_interjecting.assert_not_awaited()


async def test_question_hits_gate_but_silence_stays_silent(cog, bot):
    msg = _guild_msg("how does the quarterly bonus work?", bot)
    await cog.on_message(msg)
    bot.answerer.consider_interjecting.assert_awaited_once()
    msg.channel.send.assert_not_awaited()


async def test_confident_gate_pass_interjects(cog, bot):
    bot.answerer.consider_interjecting.return_value = Interjection(
        speak=True,
        topic="Shift pocket checklist",
        answer="Walk the opening list before the doors open.",
    )
    msg = _guild_msg("where do i find the training videos?", bot)
    await cog.on_message(msg)
    assert "opening list" in msg.channel.send.await_args.args[0]


async def test_join_greeting_dms_once(cog, bot):
    member = make_member("newbie", member_id=9)
    await cog.on_member_join(member)
    member.send.assert_awaited_once()
    assert "welcome" in member.send.await_args.args[0].lower()


async def test_join_greeting_tolerates_closed_dms(cog, bot):
    member = make_member("private", member_id=10)
    member.send.side_effect = discord.Forbidden(MagicMock(status=403), "closed")
    await cog.on_member_join(member)  # must not raise


async def test_bot_members_get_no_greeting(cog, bot):
    member = make_member("otherbot", member_id=11, is_bot=True)
    await cog.on_member_join(member)
    member.send.assert_not_awaited()


async def test_launch_greeting_does_not_promise_qa_when_disabled(cog, bot):
    """With crew Q&A off, the greeting must not tell new members to DM CrewGuard questions."""
    bot.settings.crew_qa_enabled = False
    member = make_member("newbie2", member_id=12)
    await cog.on_member_join(member)
    sent = member.send.await_args.args[0].lower()
    assert "welcome" in sent
    assert "dm me work questions" not in sent


def _in_guild(bot, member):
    guild = MagicMock()
    guild.get_member = MagicMock(return_value=member)
    bot.get_guild = MagicMock(return_value=guild)


async def test_gm_dm_is_answered_as_leadership(cog, bot, session):
    from crewguard import guild_spec as spec

    from .discord_mocks import make_role

    cfg.set_setting(session, cfg.role_key(spec.GM), "701")
    gm = make_member("boss", member_id=7)
    gm.roles = [make_role(spec.GM, role_id=701)]
    gm.guild_permissions.administrator = False
    _in_guild(bot, gm)

    await cog.on_message(_dm("how do i make you less strict", author=gm))

    assert bot.leadership_answerer.answer.await_args.args[1] == "leadership"


async def test_crew_dm_is_answered_as_crew(cog, bot, session):
    from crewguard import guild_spec as spec

    from .discord_mocks import make_role

    crew = make_member("crew1", member_id=8)
    crew.roles = [make_role(spec.CREW, role_id=999)]
    crew.guild_permissions.administrator = False
    _in_guild(bot, crew)

    await cog.on_message(_dm("how do i see my hours", author=crew))

    assert bot.answerer.answer.await_args.args[1] == "crew"


async def test_mention_in_channel_is_always_crew_voiced(cog, bot, session):
    from crewguard import guild_spec as spec

    from .discord_mocks import make_role

    cfg.set_setting(session, cfg.role_key(spec.GM), "701")
    gm = make_member("boss", member_id=7)
    gm.roles = [make_role(spec.GM, role_id=701)]
    gm.guild_permissions.administrator = False
    _in_guild(bot, gm)

    msg = _guild_msg(f"{bot.user.mention} where are the trainings", bot, mentioned=True, author=gm)
    await cog.on_message(msg)

    assert bot.answerer.answer.await_args.args[1] == "crew"


# --- launch: crew Q&A is OFF, leadership + greeting stay on ------------------


async def test_crew_dm_declined_when_qa_disabled(cog, bot):
    bot.settings.crew_qa_enabled = False
    msg = _dm("how do i see my hours")
    await cog.on_message(msg)

    bot.answerer.answer.assert_not_awaited()
    assert "manager" in msg.channel.send.await_args.args[0].lower()


async def test_mention_ignored_when_qa_disabled(cog, bot):
    bot.settings.crew_qa_enabled = False
    msg = _guild_msg(f"{bot.user.mention} where are the trainings", bot, mentioned=True)
    await cog.on_message(msg)

    bot.answerer.answer.assert_not_awaited()
    msg.channel.send.assert_not_awaited()


async def test_interjection_skipped_when_qa_disabled(cog, bot):
    bot.settings.crew_qa_enabled = False
    msg = _guild_msg("how does the quarterly bonus work?", bot)
    await cog.on_message(msg)

    bot.answerer.consider_interjecting.assert_not_awaited()


async def test_leadership_dm_answered_even_when_qa_disabled(cog, bot, session):
    """Guide-only management must survive the crew-Q&A kill switch."""
    from crewguard import guild_spec as spec

    from .discord_mocks import make_role

    bot.settings.crew_qa_enabled = False
    cfg.set_setting(session, cfg.role_key(spec.GM), "701")
    gm = make_member("boss", member_id=7)
    gm.roles = [make_role(spec.GM, role_id=701)]
    gm.guild_permissions.administrator = False
    _in_guild(bot, gm)

    await cog.on_message(_dm("how do i make you less strict", author=gm))

    assert bot.leadership_answerer.answer.await_args.args[1] == "leadership"


async def test_access_dm_is_left_to_the_admin_cog(cog, bot):
    """A reviewer DMing `access` must get the worklist from Admin, not a Q&A answer from here."""
    await cog.on_message(_dm("access"))

    bot.answerer.answer.assert_not_awaited()
    bot.leadership_answerer.answer.assert_not_awaited()
