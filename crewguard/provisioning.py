"""Idempotent guild provisioning. Safe to re-run; the spec is the source of truth.

Note: set_permissions REPLACES an overwrite, it does not merge — re-running this wipes manual
tweaks made in the Discord UI. That is intended.
"""

from dataclasses import dataclass

import discord

from crewguard import config_store as cfg
from crewguard import guild_spec as spec

AUTOMOD_RULE_NAME = "Profanity & Slurs"


def bot_permissions() -> discord.Permissions:
    """Everything the bot needs, and nothing it doesn't. No moderate_members — we never
    time anyone out. Also the Server Admin role's permission set: the operator is an
    extension of the bot, and a bot cannot grant permissions it does not itself hold.
    """
    return discord.Permissions(
        manage_roles=True,
        manage_channels=True,
        manage_guild=True,  # AutoMod rules
        manage_messages=True,  # delete
        send_messages=True,
        view_channel=True,
        embed_links=True,
    )


@dataclass(frozen=True)
class ProvisionResult:
    stored: dict[str, int]
    # False = the bot's role was too low to order the crew roles; the operator must drag the
    # bot's role above them in the UI and re-run setup-guild.
    roles_repositioned: bool


def _role_permissions(name: str) -> discord.Permissions | None:
    """Guild-wide permissions a rank role carries. None = no special permissions."""
    if name == spec.SERVER_ADMIN:
        return bot_permissions()
    if name in spec.MANAGERS:
        # Managers approve a new member by assigning them the Crew role; that needs manage_roles.
        # Nothing else — no delete, kick, or ban. Discord scopes it to roles below their own.
        return discord.Permissions(manage_roles=True)
    return None


async def ensure_role(guild: discord.Guild, name: str) -> discord.Role:
    perms = _role_permissions(name)
    existing = discord.utils.get(guild.roles, name=name)
    if existing:
        # Ensure the role has the permissions the spec requires, but MERGE (union) — never strip
        # extras an operator added by hand, e.g. toggling Administrator on the GM so the operator can edit
        # nicknames and do full admin work (a permission the bot deliberately doesn't hold and so
        # can't grant, but must not clobber either).
        if perms is not None:
            merged = existing.permissions | perms
            if existing.permissions != merged:
                await existing.edit(permissions=merged, reason="crewguard provisioning")
        return existing
    kwargs: dict = {"name": name, "mentionable": True, "reason": "crewguard provisioning"}
    if name == spec.SERVER_ADMIN:
        kwargs["hoist"] = True
    if perms is not None:
        kwargs["permissions"] = perms
    return await guild.create_role(**kwargs)


async def reposition_roles(guild: discord.Guild, roles: dict[str, discord.Role]) -> bool:
    """New roles land at the BOTTOM of the hierarchy, and a bot can only manage roles below its
    own. spec.ROLES is ascending authority, so position 1..9 sits just above @everyone (0) and
    below the bot.

    Returns False instead of raising when the bot's own role is too low to do this (50013) —
    the fix is a human dragging the bot's role up in the UI, and the rest of provisioning
    should still run so a re-run after the drag has less to do.
    """
    positions = {roles[name]: i + 1 for i, name in enumerate(spec.ROLES) if name in roles}
    if not positions:
        return True
    try:
        await guild.edit_role_positions(positions=positions, reason="crewguard provisioning")
        return True
    except discord.HTTPException as e:
        if e.code == 50013:  # Missing Permissions: bot's top role is at/below the targets
            return False
        raise


def _overwrites(guild: discord.Guild, roles: dict[str, discord.Role], ch: spec.ChannelSpec) -> dict:
    ow = {
        guild.default_role: discord.PermissionOverwrite(
            view_channel=ch.everyone_view, send_messages=ch.everyone_send
        ),
        # The bot must always be able to write to (and clean up in) every channel it manages.
        guild.me: discord.PermissionOverwrite(
            view_channel=True, send_messages=True, manage_messages=True
        ),
    }
    for role_name, (view, send) in ch.role_overrides.items():
        if role_name in roles:
            ow[roles[role_name]] = discord.PermissionOverwrite(
                view_channel=view, send_messages=send
            )
    return ow


async def ensure_channel(
    guild: discord.Guild, roles: dict[str, discord.Role], ch: spec.ChannelSpec
) -> discord.TextChannel:
    overwrites = _overwrites(guild, roles, ch)
    existing = discord.utils.get(guild.text_channels, name=ch.name)
    if existing:
        if existing.topic != ch.topic:
            await existing.edit(topic=ch.topic, reason="crewguard provisioning")
        for target, ow in overwrites.items():
            await existing.set_permissions(target, overwrite=ow, reason="crewguard provisioning")
        # Remove stale overwrites: set_permissions REPLACES but never REMOVES, so a managed role the
        # spec no longer grants keeps its old overwrite (e.g. a former schedule-poster left able to
        # post). Clear any managed-role overwrite the spec doesn't define.
        for role in roles.values():
            if role not in overwrites and role in existing.overwrites:
                await existing.set_permissions(
                    role, overwrite=None, reason="crewguard provisioning"
                )
        return existing
    return await guild.create_text_channel(
        ch.name, topic=ch.topic, overwrites=overwrites, reason="crewguard provisioning"
    )


async def ensure_automod(guild: discord.Guild, alert_channel: discord.TextChannel) -> None:
    """Tier 1. Discord allows only ONE keyword_preset rule per guild — creating a second 400s,
    so edit the existing one instead of blindly creating.
    """
    trigger = discord.AutoModTrigger(
        type=discord.AutoModRuleTriggerType.keyword_preset,
        presets=discord.AutoModPresets(profanity=True, sexual_content=True, slurs=True),
    )
    actions = [
        discord.AutoModRuleAction(custom_message="Removed. Check your DMs."),
        discord.AutoModRuleAction(channel_id=alert_channel.id),
    ]
    for rule in await guild.fetch_automod_rules():
        if rule.trigger.type is discord.AutoModRuleTriggerType.keyword_preset:
            await rule.edit(trigger=trigger, actions=actions, enabled=True)
            return
    await guild.create_automod_rule(
        name=AUTOMOD_RULE_NAME,
        event_type=discord.AutoModRuleEventType.message_send,
        trigger=trigger,
        actions=actions,
        enabled=True,
        reason="crewguard provisioning",
    )


async def provision(guild: discord.Guild, session) -> ProvisionResult:
    """Create/repair roles, channels, permissions and AutoMod. Safe to re-run."""
    roles = {name: await ensure_role(guild, name) for name in spec.ROLES}
    repositioned = await reposition_roles(guild, roles)

    channels = {ch.name: await ensure_channel(guild, roles, ch) for ch in spec.CHANNELS}
    await ensure_automod(guild, channels["message-removal-log"])

    stored: dict[str, int] = {}
    for name, role in roles.items():
        cfg.set_setting(session, cfg.role_key(name), str(role.id))
        stored[cfg.role_key(name)] = role.id
    for name, ch in channels.items():
        cfg.set_setting(session, cfg.channel_key(name), str(ch.id))
        stored[cfg.channel_key(name)] = ch.id
    return ProvisionResult(stored=stored, roles_repositioned=repositioned)
