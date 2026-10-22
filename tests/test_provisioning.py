from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from crewguard import config_store as cfg
from crewguard import guild_spec as spec
from crewguard.provisioning import provision

from .discord_mocks import make_guild


@pytest.fixture
def guild():
    return make_guild()


async def test_creates_nine_roles_and_nine_channels(guild, session):
    await provision(guild, session)
    assert guild.create_role.await_count == 9  # eight ranks + Verified
    assert guild.create_text_channel.await_count == 9


async def test_is_idempotent(guild, session):
    await provision(guild, session)
    guild.create_role.reset_mock()
    guild.create_text_channel.reset_mock()

    await provision(guild, session)
    assert guild.create_role.await_count == 0
    assert guild.create_text_channel.await_count == 0


async def test_stores_every_id(guild, session):
    result = await provision(guild, session)
    for role in spec.ROLES:
        assert cfg.get_id(session, cfg.role_key(role)) is not None
    for ch in spec.CHANNELS:
        assert cfg.get_id(session, cfg.channel_key(ch.name)) is not None
    assert len(result.stored) == 18  # 9 roles + 9 channels


async def test_reports_success_when_roles_reposition(guild, session):
    result = await provision(guild, session)
    assert result.roles_repositioned is True


async def test_low_bot_role_fails_soft_and_still_provisions_channels(guild, session):
    """The live failure of 2026-07-14: bot role at position 1 -> 50013. Provisioning must
    finish (channels, automod, ids) and report it, not stack-trace out half-done."""
    err = discord.HTTPException(
        MagicMock(status=400), {"code": 50013, "message": "Missing Permissions"}
    )
    guild.edit_role_positions = AsyncMock(side_effect=err)

    result = await provision(guild, session)

    assert result.roles_repositioned is False
    assert guild.create_text_channel.await_count == 9  # the rest still ran
    guild.create_automod_rule.assert_awaited_once()


async def test_roles_are_repositioned_above_everyone_in_authority_order(guild, session):
    await provision(guild, session)
    guild.edit_role_positions.assert_awaited_once()
    positions = guild.edit_role_positions.await_args.kwargs["positions"]
    by_name = {role.name: pos for role, pos in positions.items()}
    # Ascending authority; nothing at 0 (that's @everyone). Verified is lowest at 1.
    assert by_name[spec.VERIFIED] == 1
    assert by_name[spec.CREW] == 2
    assert by_name[spec.GM] == 8
    assert by_name[spec.SERVER_ADMIN] == 9  # above GM, directly below the bot
    assert min(by_name.values()) == 1


def _overwrites_for(guild, channel_name):
    call = next(c for c in guild.create_text_channel.await_args_list if c.args[0] == channel_name)
    return call.kwargs["overwrites"]


def _for_role(overwrites, role_name):
    for target, ow in overwrites.items():
        if getattr(target, "name", None) == role_name:
            return ow
    return None


async def test_managers_channel_is_invisible_to_everyone_by_default(guild, session):
    await provision(guild, session)
    ow = _overwrites_for(guild, "managers")
    assert _for_role(ow, "@everyone").view_channel is False


@pytest.mark.parametrize("role", spec.MANAGERS)
async def test_managers_channel_grants_the_four_manager_roles(guild, session, role):
    await provision(guild, session)
    ow = _for_role(_overwrites_for(guild, "managers"), role)
    assert ow.view_channel is True
    assert ow.send_messages is True


@pytest.mark.parametrize("role", (spec.CREW, spec.CT_FOH, spec.CT_BOH))
async def test_managers_channel_has_no_grant_for_crew_or_cts(guild, session, role):
    """No overwrite at all -> they inherit @everyone's view_channel=False."""
    await provision(guild, session)
    assert _for_role(_overwrites_for(guild, "managers"), role) is None


async def test_schedule_is_gated_but_readable_by_crew(guild, session):
    await provision(guild, session)
    everyone = _for_role(_overwrites_for(guild, "schedule"), "@everyone")
    assert everyone.view_channel is False  # no rank -> no access
    crew = _for_role(_overwrites_for(guild, "schedule"), spec.CREW)
    assert crew.view_channel is True
    assert crew.send_messages is False


async def test_only_the_gm_posts_the_schedule(guild, session):
    await provision(guild, session)
    assert _for_role(_overwrites_for(guild, "schedule"), spec.GM).send_messages is True


@pytest.mark.parametrize("role", (spec.APPRENTICE, spec.SERVICE_LEADER, spec.KITCHEN_LEADER))
async def test_leaders_and_apprentice_are_read_only_in_schedule(guild, session, role):
    await provision(guild, session)
    assert _for_role(_overwrites_for(guild, "schedule"), role).send_messages is False


async def test_mod_log_visible_to_gm_only(guild, session):
    await provision(guild, session)
    ow = _overwrites_for(guild, "mod-log")
    assert _for_role(ow, "@everyone").view_channel is False
    assert _for_role(ow, spec.GM).view_channel is True
    assert _for_role(ow, spec.GM).send_messages is False
    # Explicit denies, not inherited: the assistant manager and the supervisors once had
    # view and must be actively revoked.
    for role in (spec.APPRENTICE, spec.SERVICE_LEADER, spec.KITCHEN_LEADER):
        assert _for_role(ow, role).view_channel is False


async def test_bot_can_always_write_to_mod_log(guild, session):
    await provision(guild, session)
    assert _overwrites_for(guild, "mod-log")[guild.me].send_messages is True


async def test_creates_automod_preset_rule(guild, session):
    await provision(guild, session)
    guild.create_automod_rule.assert_awaited_once()
    trigger = guild.create_automod_rule.await_args.kwargs["trigger"]
    assert trigger.type is discord.AutoModRuleTriggerType.keyword_preset
    assert trigger.presets.profanity and trigger.presets.slurs


async def test_automod_alerts_route_to_removal_log(guild, session):
    """A blocked message is a removal — its native AutoMod alert belongs in the removal log."""
    await provision(guild, session)
    removal_log = next(c for c in guild.text_channels if c.name == "message-removal-log")
    actions = guild.create_automod_rule.await_args.kwargs["actions"]
    assert any(a.channel_id == removal_log.id for a in actions)


async def test_existing_preset_rule_is_edited_not_duplicated(guild, session):
    """Discord allows exactly ONE keyword_preset rule per guild; a second create 400s."""
    existing = MagicMock()
    existing.trigger.type = discord.AutoModRuleTriggerType.keyword_preset
    existing.edit = AsyncMock()
    guild.fetch_automod_rules = AsyncMock(return_value=[existing])

    await provision(guild, session)

    existing.edit.assert_awaited_once()
    guild.create_automod_rule.assert_not_awaited()


async def test_stale_overwrite_for_a_dropped_role_is_removed(guild, session):
    """set_permissions never removes overwrites, so a role the spec no longer grants (e.g. the
    assistant manager, dropped from schedule-posters) must be actively cleared on re-provision."""
    await provision(guild, session)
    welcome = next(c for c in guild.text_channels if c.name == "welcome-and-rules")
    ap = next(r for r in guild.roles if r.name == spec.APPRENTICE)
    welcome.overwrites = {ap: discord.PermissionOverwrite(send_messages=True)}
    welcome.set_permissions.reset_mock()

    await provision(guild, session)

    cleared = [
        call
        for call in welcome.set_permissions.await_args_list
        if call.args and call.args[0] is ap and call.kwargs.get("overwrite") is None
    ]
    assert cleared, "stale assistant manager overwrite on #welcome-and-rules was not removed"


@pytest.mark.parametrize("role", spec.MANAGERS)
async def test_manager_roles_can_assign_roles_to_approve(guild, session, role):
    """Any manager approves a new member by assigning Crew — that needs manage_roles,
    and nothing more."""
    await provision(guild, session)
    call = next(c for c in guild.create_role.await_args_list if c.kwargs.get("name") == role)
    perms = call.kwargs["permissions"]
    assert perms.manage_roles
    assert not perms.ban_members and not perms.manage_messages  # only role assignment


async def test_reprovision_preserves_a_manual_admin_toggle(guild, session):
    """A permission the operator adds by hand — e.g. Administrator on the GM so the operator can edit
    nicknames and do full admin work — must survive re-provision. ensure_role MERGES, never
    replaces (the bot can't grant Administrator, but it must not strip it either)."""
    await provision(guild, session)
    gm = next(r for r in guild.roles if r.name == spec.GM)
    gm.permissions = discord.Permissions(
        administrator=True
    )  # operator toggled it, no manage_roles yet
    gm.edit.reset_mock()

    await provision(guild, session)

    gm.edit.assert_awaited()  # admin -> admin+manage_roles is a change, so it edits
    new_perms = gm.edit.await_args.kwargs["permissions"]
    assert new_perms.administrator  # the manual toggle is NOT stripped
    assert new_perms.manage_roles  # and the approve permission is still ensured


async def test_crew_role_has_no_special_permissions(guild, session):
    await provision(guild, session)
    call = next(c for c in guild.create_role.await_args_list if c.kwargs.get("name") == spec.CREW)
    assert "permissions" not in call.kwargs  # a bare rank carries nothing guild-wide


async def test_server_admin_role_mirrors_bot_permissions(guild, session):
    """A bot cannot grant permissions it does not hold; Server Admin = the bot's own set."""
    await provision(guild, session)
    call = next(
        c for c in guild.create_role.await_args_list if c.kwargs.get("name") == spec.SERVER_ADMIN
    )
    perms = call.kwargs["permissions"]
    assert perms.manage_messages and perms.manage_guild and perms.manage_roles
    assert not perms.administrator  # the bot doesn't have it, so it can't grant it
    assert call.kwargs["hoist"] is True
