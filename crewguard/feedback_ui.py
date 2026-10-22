"""Review buttons on every #message-removal-log removal entry.

GM/Server Admin press [Good call] or [Too strict] on removal #N. "Too strict" rows are
fed back into the classifier prompt as allow-examples — leadership's verdicts ARE the
aggressiveness knob. Pressing the other button later flips the verdict; nothing is permanent.

DynamicItem: the custom_id carries the row id, so buttons keep working across bot restarts
without any in-memory view registry.
"""

import re

import discord
from sqlmodel import Session

from crewguard import config_store as cfg
from crewguard import guild_spec as spec
from crewguard.models import ModerationLog

# The assistant manager is treated as a service supervisor now — moderation review is GM (+ Server Admin) only.
REVIEWER_ROLES = (spec.GM, spec.SERVER_ADMIN)


def _is_reviewer(session: Session, member: discord.Member) -> bool:
    if getattr(member.guild_permissions, "administrator", False):
        return True
    allowed = {rid for role in REVIEWER_ROLES if (rid := cfg.get_id(session, cfg.role_key(role)))}
    return bool(allowed & {r.id for r in getattr(member, "roles", ())})


class FeedbackButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"crewguard:fb:(?P<verdict>good|strict):(?P<row_id>\d+)",
):
    def __init__(self, verdict: str, row_id: int) -> None:
        label = "✅ Good call" if verdict == "good" else "❌ Too strict"
        super().__init__(
            discord.ui.Button(
                label=label,
                style=discord.ButtonStyle.secondary,
                custom_id=f"crewguard:fb:{verdict}:{row_id}",
            )
        )
        self.verdict = verdict
        self.row_id = row_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match: re.Match):
        return cls(match["verdict"], int(match["row_id"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        engine = interaction.client.engine
        with Session(engine) as session:
            if not _is_reviewer(session, interaction.user):
                await interaction.response.send_message(
                    "Only the GM or Server Admin can review removals.",
                    ephemeral=True,
                )
                return
            row = session.get(ModerationLog, self.row_id)
            if row is None:
                await interaction.response.send_message("Removal not found.", ephemeral=True)
                return
            row.feedback = "good_call" if self.verdict == "good" else "too_strict"
            row.feedback_by = str(interaction.user)
            session.add(row)
            session.commit()

        verdict_text = (
            "✅ good call"
            if self.verdict == "good"
            else "❌ too strict — CrewGuard will allow messages like this now"
        )
        embed = interaction.message.embeds[0]
        embed.set_footer(text=f"Reviewed by {interaction.user.display_name}: {verdict_text}")
        await interaction.response.edit_message(embed=embed)


def feedback_view(row_id: int) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(FeedbackButton("good", row_id))
    view.add_item(FeedbackButton("strict", row_id))
    return view
