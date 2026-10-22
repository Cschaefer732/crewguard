"""The join gate. New members can see only #welcome-and-rules until they press [I Agree] on the
participation agreement. The button records who accepted, which version, and when, then grants the
Verified consent badge (proof of agreement); a manager then separately assigns Crew, which is
what unlocks the channels.

DynamicItem with a fixed custom_id so the one button keeps working across bot restarts without an
in-memory view registry (same pattern as feedback_ui).

Note: a click records which rules text the member acknowledged. It is not a contract and it
does not waive anyone's rights. The record exists so the operator can show what each member
was shown and agreed to.
"""

import enum
import logging
from datetime import UTC, datetime

import discord
from sqlmodel import Session

from crewguard import config_store as cfg
from crewguard import guild_spec as spec
from crewguard.models import ConsentRecord
from crewguard.moderation.enforce import mod_log_channel

log = logging.getLogger(__name__)

# Bump when the agreement text changes materially — lets you require re-acceptance later.
AGREEMENT_VERSION = "sample-1"


class ConsentOutcome(enum.Enum):
    granted = "granted"  # Verified newly added
    already = "already"  # member already had Verified
    no_role = "no_role"  # Verified role not provisioned/configured
    failed = "failed"  # add_roles raised — the silent-failure bug


async def record_consent_and_verify(engine, guild, member, *, reason: str) -> ConsentOutcome:
    """Upsert the member's consent record (who, which version, when) and grant the Verified role.
    Consent is committed first and never depends on the grant succeeding — it is the
    legally-relevant artifact. Returns a ConsentOutcome so callers can surface a failed grant."""
    with Session(engine) as session:
        record = session.get(ConsentRecord, member.id)
        if record:
            record.version = AGREEMENT_VERSION
            record.user_name = str(member)
            record.ts = datetime.now(UTC)
        else:
            session.add(
                ConsentRecord(user_id=member.id, user_name=str(member), version=AGREEMENT_VERSION)
            )
        session.commit()
        role_id = cfg.get_id(session, cfg.role_key(spec.VERIFIED))

    role = guild.get_role(role_id) if (guild and role_id) else None
    if role is None:
        return ConsentOutcome.no_role
    if role in getattr(member, "roles", ()):
        return ConsentOutcome.already
    try:
        await member.add_roles(role, reason=reason)
        return ConsentOutcome.granted
    except discord.HTTPException:
        log.warning("could not grant Verified to %s", member, exc_info=True)
        return ConsentOutcome.failed


async def alert_grant_failure(bot, guild, member) -> None:
    """Push a #mod-log alert when the Verified grant failed — otherwise the failure is invisible.
    Guarded: a failure to post must never re-raise into a listener or interaction callback."""
    try:
        with Session(bot.engine) as session:
            channel = mod_log_channel(bot, session)
            gm_id = cfg.get_id(session, cfg.role_key(spec.GM))
        if channel is None:
            return
        mention = f"<@&{gm_id}> " if gm_id else ""
        await channel.send(
            f"⚠️ {mention}Couldn't attach the Verified badge to {member} after they agreed. "
            f"Their consent IS recorded — assign Crew when ready. "
            f"Fix: CrewGuard's role must sit above Verified and keep Manage Roles.",
            allowed_mentions=discord.AllowedMentions(roles=True),
        )
    except discord.HTTPException:
        log.warning("could not post grant-failure alert for %s", member, exc_info=True)


class ConsentButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"crewguard:consent:agree",
):
    def __init__(self) -> None:
        super().__init__(
            discord.ui.Button(
                label="✅ I Agree",
                style=discord.ButtonStyle.success,
                custom_id="crewguard:consent:agree",
            )
        )

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls()

    async def callback(self, interaction: discord.Interaction) -> None:
        outcome = await record_consent_and_verify(
            interaction.client.engine,
            interaction.guild,
            interaction.user,
            reason="accepted the participation agreement",
        )
        await interaction.response.send_message(
            "Thanks — your agreement is recorded. A manager will review and give you access "
            "shortly. To have your data deleted at any time, DM the Server Admin.",
            ephemeral=True,
        )
        if outcome is ConsentOutcome.failed:
            await alert_grant_failure(interaction.client, interaction.guild, interaction.user)


def consent_view() -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(ConsentButton())
    return view
