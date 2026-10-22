from unittest.mock import AsyncMock, MagicMock

import discord
from sqlmodel import Session, select

from crewguard import config_store as cfg
from crewguard import guild_spec as spec
from crewguard.consent_ui import (
    AGREEMENT_VERSION,
    ConsentButton,
    ConsentOutcome,
    record_consent_and_verify,
)
from crewguard.models import ConsentRecord

from .discord_mocks import make_channel, make_member, make_role


def _interaction(engine, member, verified_role):
    itx = MagicMock()
    itx.client.engine = engine
    itx.user = member
    member.__str__ = MagicMock(return_value=member.name)
    member.add_roles = AsyncMock()
    guild = MagicMock()
    guild.get_role = MagicMock(return_value=verified_role)
    itx.guild = guild
    itx.response.send_message = AsyncMock()
    return itx


async def test_agree_records_consent_and_grants_verified(engine, session):
    verified = make_role(spec.VERIFIED, role_id=500)
    cfg.set_setting(session, cfg.role_key(spec.VERIFIED), "500")
    member = make_member("newbie", member_id=77)
    member.roles = []
    itx = _interaction(engine, member, verified)

    await ConsentButton().callback(itx)

    with Session(engine) as s:
        rec = s.get(ConsentRecord, 77)
    assert rec is not None
    assert rec.version == AGREEMENT_VERSION
    member.add_roles.assert_awaited_once()
    assert member.add_roles.await_args.args[0] is verified
    itx.response.send_message.assert_awaited_once()
    assert itx.response.send_message.await_args.kwargs.get("ephemeral") is True


async def test_agree_is_idempotent(engine, session):
    verified = make_role(spec.VERIFIED, role_id=500)
    cfg.set_setting(session, cfg.role_key(spec.VERIFIED), "500")
    member = make_member("newbie", member_id=77)
    member.roles = []

    await ConsentButton().callback(_interaction(engine, member, verified))
    # already has the role now
    member.roles = [verified]
    itx2 = _interaction(engine, member, verified)
    await ConsentButton().callback(itx2)

    with Session(engine) as s:
        rows = s.exec(select(ConsentRecord)).all()
    assert len(rows) == 1  # one record, updated not duplicated
    itx2.user.add_roles.assert_not_awaited()  # not re-granted when already held


def _screened_member(member_id, pending):
    m = make_member("newbie", member_id=member_id)
    m.pending = pending
    m.roles = []
    m.add_roles = AsyncMock()
    m.__str__ = MagicMock(return_value="newbie")
    m.guild = MagicMock()
    return m


async def test_membership_screening_completion_records_consent_and_verifies(engine, session):
    """The native 'agree to the rules' popup: pending True -> False grants Verified + records."""
    from crewguard.cogs.consent import Consent

    verified = make_role(spec.VERIFIED, role_id=500)
    cfg.set_setting(session, cfg.role_key(spec.VERIFIED), "500")
    bot = MagicMock()
    bot.engine = engine
    before = _screened_member(88, pending=True)
    after = _screened_member(88, pending=False)
    after.guild.get_role = MagicMock(return_value=verified)

    await Consent(bot).on_member_update(before, after)

    with Session(engine) as s:
        assert s.get(ConsentRecord, 88) is not None
    after.add_roles.assert_awaited_once()
    assert after.add_roles.await_args.args[0] is verified


async def test_ordinary_member_update_is_ignored(engine, session):
    """A nick/role change (pending unchanged) must not touch consent."""
    from crewguard.cogs.consent import Consent

    cfg.set_setting(session, cfg.role_key(spec.VERIFIED), "500")
    bot = MagicMock()
    bot.engine = engine
    before = _screened_member(88, pending=False)
    after = _screened_member(88, pending=False)

    await Consent(bot).on_member_update(before, after)

    after.add_roles.assert_not_awaited()
    with Session(engine) as s:
        assert s.get(ConsentRecord, 88) is None


async def test_grant_failure_still_records_consent_and_returns_failed(engine, session):
    verified = make_role(spec.VERIFIED, role_id=500)
    cfg.set_setting(session, cfg.role_key(spec.VERIFIED), "500")
    member = make_member("newbie", member_id=77)
    member.roles = []
    member.add_roles = AsyncMock(
        side_effect=discord.Forbidden(MagicMock(status=403), "Missing Permissions")
    )
    guild = MagicMock()
    guild.get_role = MagicMock(return_value=verified)

    outcome = await record_consent_and_verify(engine, guild, member, reason="x")

    assert outcome is ConsentOutcome.failed
    with Session(engine) as s:
        assert s.get(ConsentRecord, 77) is not None  # consent recorded despite the grant failing


async def test_button_alerts_mod_log_on_grant_failure(engine, session):
    mod_log = make_channel("mod-log", channel_id=900)
    cfg.set_setting(session, cfg.channel_key("mod-log"), "900")
    cfg.set_setting(session, cfg.role_key(spec.GM), "701")
    verified = make_role(spec.VERIFIED, role_id=500)
    cfg.set_setting(session, cfg.role_key(spec.VERIFIED), "500")

    member = make_member("newbie", member_id=77)
    member.roles = []
    member.add_roles = AsyncMock(
        side_effect=discord.Forbidden(MagicMock(status=403), "Missing Permissions")
    )
    member.__str__ = MagicMock(return_value="newbie")

    itx = MagicMock()
    itx.client.engine = engine
    itx.client.get_channel = MagicMock(return_value=mod_log)
    itx.user = member
    guild = MagicMock()
    guild.get_role = MagicMock(return_value=verified)
    itx.guild = guild
    itx.response.send_message = AsyncMock()

    order = []
    itx.response.send_message.side_effect = lambda *a, **k: order.append("thanks")
    mod_log.send.side_effect = lambda *a, **k: order.append("alert")

    await ConsentButton().callback(itx)

    itx.response.send_message.assert_awaited_once()  # member still thanked
    assert itx.response.send_message.await_args.kwargs.get("ephemeral") is True
    assert any("Verified" in c.args[0] for c in mod_log.send.await_args_list if c.args)
    assert order == ["thanks", "alert"]


async def test_native_screening_alerts_on_grant_failure(engine, session):
    from crewguard.cogs.consent import Consent

    mod_log = make_channel("mod-log", channel_id=900)
    cfg.set_setting(session, cfg.channel_key("mod-log"), "900")
    cfg.set_setting(session, cfg.role_key(spec.GM), "701")
    verified = make_role(spec.VERIFIED, role_id=500)
    cfg.set_setting(session, cfg.role_key(spec.VERIFIED), "500")

    bot = MagicMock()
    bot.engine = engine
    bot.get_channel = MagicMock(return_value=mod_log)
    before = _screened_member(88, pending=True)
    after = _screened_member(88, pending=False)
    after.add_roles = AsyncMock(
        side_effect=discord.Forbidden(MagicMock(status=403), "Missing Permissions")
    )
    after.guild.get_role = MagicMock(return_value=verified)

    await Consent(bot).on_member_update(before, after)

    assert any("Verified" in c.args[0] for c in mod_log.send.await_args_list if c.args)
