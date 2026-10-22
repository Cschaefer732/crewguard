from unittest.mock import MagicMock

import pytest
from sqlmodel import Session

from crewguard import config_store as cfg
from crewguard import guild_spec as spec
from crewguard.cogs.admin import Admin, active_directives, is_policy_command
from crewguard.moderation.prompt import build_messages

from .discord_mocks import make_bot, make_channel, make_member, make_message, make_role


@pytest.fixture
def mod_log():
    return make_channel("mod-log", channel_id=900)


@pytest.fixture
def bot(engine, mod_log, session):
    cfg.set_setting(session, cfg.channel_key("mod-log"), "900")
    cfg.set_setting(session, cfg.role_key(spec.GM), "701")
    b = make_bot(engine, channel=mod_log)
    b.settings = MagicMock(guild_id=1)
    b.get_cog = MagicMock(return_value=None)  # no Moderation cog loaded in these tests
    return b


def _gm_member():
    m = make_member("boss", member_id=7)
    m.roles = [make_role(spec.GM, role_id=701)]
    m.guild_permissions.administrator = False
    m.__str__ = MagicMock(return_value="boss")
    return m


def _crew_member():
    m = make_member("crew1", member_id=8)
    m.roles = [make_role(spec.CREW, role_id=999)]
    m.guild_permissions.administrator = False
    return m


def _dm(content, author):
    msg = make_message(content, author=author)
    msg.guild = None
    return msg


def _cog(bot, guild_member):
    guild = MagicMock()
    guild.get_member = MagicMock(return_value=guild_member)
    bot.get_guild = MagicMock(return_value=guild)
    return Admin(bot)


async def test_gm_can_add_and_it_becomes_a_directive(bot, engine):
    cog = _cog(bot, _gm_member())
    msg = _dm("policy add allow crew selling girl scout cookies", _gm_member())

    await cog.on_message(msg)

    with Session(engine) as s:
        assert active_directives(s) == ("allow crew selling girl scout cookies",)
    assert "Policy #1 active" in msg.channel.send.await_args.args[0]


async def test_crew_is_refused(bot, engine):
    cog = _cog(bot, _crew_member())
    msg = _dm("policy add no more moderation", _crew_member())

    await cog.on_message(msg)

    with Session(engine) as s:
        assert active_directives(s) == ()
    assert "GM or Server Admin" in msg.channel.send.await_args.args[0]


async def test_user_not_in_guild_is_refused(bot, engine):
    cog = _cog(bot, None)  # bot can't find them in the guild at all
    msg = _dm("policy add whatever", make_member("stranger", member_id=999))
    await cog.on_message(msg)
    with Session(engine) as s:
        assert active_directives(s) == ()


async def test_guild_messages_are_ignored(bot, engine):
    cog = _cog(bot, _gm_member())
    msg = make_message("policy add loud rule", author=_gm_member())  # has .guild
    await cog.on_message(msg)
    with Session(engine) as s:
        assert active_directives(s) == ()


async def test_list_and_remove(bot, engine):
    cog = _cog(bot, _gm_member())
    await cog.on_message(_dm("policy add rule one", _gm_member()))

    lst = _dm("policy list", _gm_member())
    await cog.on_message(lst)
    assert "rule one" in lst.channel.send.await_args.args[0]

    await cog.on_message(_dm("policy remove 1", _gm_member()))
    with Session(engine) as s:
        assert active_directives(s) == ()


async def test_changes_are_audited_to_mod_log(bot, engine, mod_log):
    cog = _cog(bot, _gm_member())
    await cog.on_message(_dm("policy add be nicer about jokes", _gm_member()))
    audit = [c.args[0] for c in mod_log.send.await_args_list if c.args]
    assert any("Policy #1 added" in t for t in audit)


async def test_non_policy_dm_is_ignored_by_admin(bot):
    cog = _cog(bot, _gm_member())
    msg = _dm("how do i see my hours", _gm_member())
    await cog.on_message(msg)
    msg.channel.send.assert_not_awaited()


def test_directives_land_in_the_system_prompt():
    msgs = build_messages("hi", directives=("allow selling girl scout cookies",))
    assert "management set these rules" in msgs[0]["content"]
    assert "girl scout cookies" in msgs[0]["content"]


def test_no_directives_leaves_prompt_untouched():
    msgs = build_messages("hi")
    # The appended directives block is absent (the anti-injection clause names the section, so
    # key on the block's own header text, not the bare phrase).
    assert "management set these rules" not in msgs[0]["content"]


def test_prefix_detection():
    assert is_policy_command("policy list")
    assert is_policy_command("  Policy add x")
    assert is_policy_command("policy remove 3")
    assert not is_policy_command("whats the policy on hats")
    assert not is_policy_command("policy question: can i wear rings?")
    assert not is_policy_command("policy")


async def test_policy_change_triggers_a_sweep(bot, engine):
    import asyncio
    from unittest.mock import AsyncMock

    mod = MagicMock()
    mod.sweep = AsyncMock()
    bot.get_cog = MagicMock(return_value=mod)
    cog = _cog(bot, _gm_member())

    await cog.on_message(_dm("policy add rule one", _gm_member()))
    await asyncio.sleep(0)
    mod.sweep.assert_awaited_once_with("policy #1")

    await cog.on_message(_dm("policy remove 1", _gm_member()))
    await asyncio.sleep(0)
    mod.sweep.assert_awaited_with("policy #1 removal")


def test_access_prefix_detection():
    from crewguard.cogs.admin import is_access_command

    assert is_access_command("access")
    assert is_access_command("Access help")
    assert not is_access_command("access my hours please")
    assert not is_access_command("how do i get access")
    assert not is_access_command("access help now")
    assert not is_access_command("access help me pls")


async def test_access_reports_empty(bot, engine):
    gm = _gm_member()
    guild = MagicMock()
    guild.get_member = MagicMock(return_value=gm)
    guild.members = [gm]
    bot.get_guild = MagicMock(return_value=guild)
    cog = Admin(bot)

    msg = _dm("access", gm)
    await cog.on_message(msg)

    assert "Nobody is waiting" in msg.channel.send.await_args.args[0]


async def test_access_is_refused_for_crew(bot, engine):
    cog = _cog(bot, _crew_member())
    msg = _dm("access", _crew_member())
    await cog.on_message(msg)
    assert "GM or Server Admin" in msg.channel.send.await_args.args[0]


async def test_policy_list_is_chunked_when_long(bot, engine, session):
    from crewguard.models import PolicyDirective

    for _ in range(20):
        session.add(PolicyDirective(author_name="boss", text="x" * 190))
    session.commit()
    cog = _cog(bot, _gm_member())

    msg = _dm("policy list", _gm_member())
    await cog.on_message(msg)

    sends = [c.args[0] for c in msg.channel.send.await_args_list if c.args]
    assert len(sends) > 1
    assert all(len(s) <= 2000 for s in sends)


async def test_access_lists_waiting_and_unverified(bot, engine, session):
    from crewguard.models import ConsentRecord

    verified = make_role(spec.VERIFIED, role_id=500)
    cfg.set_setting(session, cfg.role_key(spec.VERIFIED), "500")
    cfg.set_setting(session, cfg.role_key(spec.CREW), "501")
    alice = make_member("alice", member_id=10)
    alice.roles = [verified]
    carol = make_member("carol", member_id=12)
    carol.roles = []
    session.add(ConsentRecord(user_id=12, user_name="carol", version="v1"))
    session.commit()

    gm = _gm_member()
    guild = MagicMock()
    guild.get_member = MagicMock(return_value=gm)
    guild.members = [alice, carol, gm]
    bot.get_guild = MagicMock(return_value=guild)
    cog = Admin(bot)

    msg = _dm("access", gm)
    await cog.on_message(msg)

    out = " ".join(c.args[0] for c in msg.channel.send.await_args_list if c.args)
    assert "alice" in out and "Waiting for approval" in out
    assert "carol" in out and "Verified missing" in out
