"""Policy directives over DM — the leadership behavior knob.

GM / Server Admin DM CrewGuard:

    policy add <instruction>   e.g.  policy add allow crew selling girl scout cookies
    policy list
    policy remove <id>

Directives are appended to the classifier's system prompt as explicit overrides. Deliberately a
deterministic command prefix, NOT intent detection — a casual DM must never accidentally change
store policy. DM-only (roles are checked against the guild member), and every change is audited
to #mod-log so this is never silent power.
"""

import asyncio
import logging

import discord
from discord.ext import commands
from sqlmodel import Session, select

from crewguard.access import access_buckets
from crewguard.feedback_ui import _is_reviewer
from crewguard.models import PolicyDirective
from crewguard.moderation.enforce import mod_log_channel
from crewguard.textutil import chunk_message

log = logging.getLogger(__name__)

MAX_DIRECTIVES = 20
MAX_LENGTH = 200

HELP = (
    "Policy commands:\n"
    "`policy add <instruction>` — e.g. `policy add allow crew selling girl scout cookies`\n"
    "`policy list`\n"
    "`policy remove <id>`"
)


def is_policy_command(text: str) -> bool:
    """Only real subcommands count — "policy question: can i wear rings?" is a Q&A DM, not a
    command, and must fall through to the assistant."""
    parts = text.strip().lower().split(maxsplit=2)
    return (
        len(parts) >= 2 and parts[0] == "policy" and parts[1] in ("add", "list", "remove", "help")
    )


def is_access_command(text: str) -> bool:
    """`access` or `access help` — the reviewer worklist. Anything longer falls through to Q&A."""
    parts = text.strip().lower().split()
    if not parts or parts[0] != "access":
        return False
    return len(parts) == 1 or (len(parts) == 2 and parts[1] == "help")


class Admin(commands.Cog):
    def __init__(self, bot) -> None:
        self.bot = bot

    def _session(self) -> Session:
        return Session(self.bot.engine)

    def _member(self, user: discord.abc.User) -> discord.Member | None:
        guild = self.bot.get_guild(self.bot.settings.guild_id)
        return guild.get_member(user.id) if guild else None

    async def _audit(self, session: Session, text: str) -> None:
        channel = mod_log_channel(self.bot, session)
        if channel:
            await channel.send(text)

    def _sweep(self, trigger: str) -> None:
        """A rule change applies to messages already in the chat, not just new ones."""
        cog = self.bot.get_cog("Moderation")
        if cog:
            asyncio.create_task(cog.sweep(trigger))

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        # DM-only surface; guild messages never reach this.
        if message.author.bot or message.guild is not None or not message.content:
            return
        if is_policy_command(message.content):
            await self._route(message, self._handle, "Policy is for the GM or Server Admin only.")
        elif is_access_command(message.content):
            await self._route(
                message, self._handle_access, "Access status is for the GM or Server Admin only."
            )

    async def _route(self, message: discord.Message, handler, refusal: str) -> None:
        member = self._member(message.author)
        with self._session() as session:
            if member is None or not _is_reviewer(session, member):
                await message.channel.send(refusal)
                return
            await handler(session, message)

    async def _handle(self, session: Session, message: discord.Message) -> None:
        parts = message.content.strip().split(maxsplit=2)
        sub = parts[1].lower() if len(parts) > 1 else "help"

        if sub == "add" and len(parts) == 3:
            text = parts[2].strip()
            if len(text) > MAX_LENGTH:
                await message.channel.send(f"Too long — keep it under {MAX_LENGTH} characters.")
                return
            count = len(session.exec(select(PolicyDirective)).all())
            if count >= MAX_DIRECTIVES:
                await message.channel.send(
                    f"{MAX_DIRECTIVES} directives already active — `policy remove <id>` one first."
                )
                return
            row = PolicyDirective(author_name=str(message.author), text=text)
            session.add(row)
            session.commit()
            session.refresh(row)
            await message.channel.send(
                f"Policy #{row.id} active: {text}\nRe-checking recent messages."
            )
            await self._audit(session, f"📜 Policy #{row.id} added by {message.author}: {text}")
            self._sweep(f"policy #{row.id}")
            return

        if sub == "list":
            rows = session.exec(select(PolicyDirective)).all()
            if not rows:
                await message.channel.send("No active directives.")
                return
            lines = [f"#{r.id} [{r.author_name}] {r.text}" for r in rows]
            for chunk in chunk_message("Active directives:\n" + "\n".join(lines)):
                await message.channel.send(chunk)
            return

        if sub == "remove" and len(parts) == 3 and parts[2].strip().isdigit():
            row = session.get(PolicyDirective, int(parts[2]))
            if row is None:
                await message.channel.send("No directive with that id.")
                return
            session.delete(row)
            session.commit()
            await message.channel.send(f"Policy #{row.id} removed.\nRe-checking recent messages.")
            await self._audit(
                session, f"📜 Policy #{row.id} removed by {message.author}: {row.text}"
            )
            self._sweep(f"policy #{row.id} removal")
            return

        await message.channel.send(HELP)

    async def _handle_access(self, session: Session, message: discord.Message) -> None:
        guild = self.bot.get_guild(self.bot.settings.guild_id)
        buckets = access_buckets(guild, session)
        if not buckets.waiting and not buckets.unverified:
            await message.channel.send("Nobody is waiting — everyone who agreed is in.")
            return
        lines = ["**Access status**"]
        if buckets.waiting:
            names = ", ".join(m.display_name for m in buckets.waiting)
            lines.append(f"✅ Waiting for approval (agreed — assign Crew): {names}")
        if buckets.unverified:
            names = ", ".join(m.display_name for m in buckets.unverified)
            lines.append(f"⚠️ Agreed but Verified missing (add it): {names}")
        for chunk in chunk_message("\n".join(lines)):
            await message.channel.send(chunk)


def active_directives(session: Session) -> tuple[str, ...]:
    return tuple(r.text for r in session.exec(select(PolicyDirective)).all())


async def setup(bot) -> None:
    await bot.add_cog(Admin(bot))
