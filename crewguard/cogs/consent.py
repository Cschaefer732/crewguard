"""Native membership-screening consent.

When a member clears Discord's built-in Membership (Rules) Screening — the "agree to the rules"
popup shown on join — `Member.pending` flips from True to False and this fires. We record the
consent and grant Verified, exactly like the in-channel [I Agree] button (both call the same
recorder). Additive on purpose: if screening is not enabled in Server Settings, `pending` is always
False, this never fires, and the button still handles consent — so nobody is ever locked out.

Requires the members privileged intent (already enabled).
"""

import logging

import discord
from discord.ext import commands

log = logging.getLogger(__name__)


class Consent(commands.Cog):
    def __init__(self, bot) -> None:
        self.bot = bot

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member) -> None:
        # Fires once, the moment a member finishes the native screening gate.
        if before.pending and not after.pending:
            from crewguard.consent_ui import (
                ConsentOutcome,
                alert_grant_failure,
                record_consent_and_verify,
            )

            outcome = await record_consent_and_verify(
                self.bot.engine, after.guild, after, reason="completed membership screening"
            )
            if outcome is ConsentOutcome.granted:
                log.info("granted Verified to %s after membership screening", after)
            elif outcome is ConsentOutcome.failed:
                await alert_grant_failure(self.bot, after.guild, after)


async def setup(bot) -> None:
    await bot.add_cog(Consent(bot))
