"""Crew Q&A: join greeting, DM answers, @mention answers, and the rare unprompted interjection.

Ordering rule (see AGENTS.md): moderation runs first. This cog must never answer a message the
classifier flagged — the moderation cog deletes it, and answering a deleted message would be
absurd. Coordination is via `bot.recently_removed` (message ids), set by the moderation cog.
"""

import asyncio
import logging
import time
from collections import defaultdict

import discord
from discord.ext import commands
from sqlmodel import Session

from crewguard.assistant.prompts import (
    GREETING,
    GREETING_NO_QA,
    REFUSAL_CAP,
    REFUSAL_NOT_IN_KB,
    REFUSAL_NOT_WORK,
    REFUSAL_QA_DISABLED,
    looks_like_question,
)
from crewguard.feedback_ui import _is_reviewer
from crewguard.models import UnansweredQuestion
from crewguard.moderation.enforce import mod_log_channel
from crewguard.ollama import AllBackendsDown

log = logging.getLogger(__name__)

COOLDOWN_SECONDS = 20.0
DAILY_CAP = 25


class Assistant(commands.Cog):
    moderation_grace = 3.0  # seconds; classifier p95 ~2s

    def __init__(self, bot, clock=time.monotonic) -> None:
        self.bot = bot
        self._clock = clock
        self._last_asked: dict[int, float] = {}
        self._daily: dict[int, int] = defaultdict(int)
        self._daily_day: int = 0

    def _session(self) -> Session:
        return Session(self.bot.engine)

    # --- rate limiting -------------------------------------------------------

    def _over_limit(self, user_id: int) -> bool:
        now = self._clock()
        day = int(now // 86400)
        if day != self._daily_day:
            self._daily_day = day
            self._daily.clear()
        if now - self._last_asked.get(user_id, float("-inf")) < COOLDOWN_SECONDS:
            return True
        if self._daily[user_id] >= DAILY_CAP:
            return True
        self._last_asked[user_id] = now
        self._daily[user_id] += 1
        return False

    # --- shared answer path --------------------------------------------------

    def _audience(self, user: discord.abc.User) -> str:
        """Leadership DMs get the management controls; everyone else gets the learning tool.
        DM-only distinction — a channel answer is crew-visible, so it stays crew-voiced."""
        guild = self.bot.get_guild(self.bot.settings.guild_id)
        member = guild.get_member(user.id) if guild else None
        if member is None:
            return "crew"
        with self._session() as session:
            return "leadership" if _is_reviewer(session, member) else "crew"

    async def _respond(
        self, message: discord.Message, question: str, surface: str, audience: str = "crew"
    ) -> None:
        # Leadership guidance runs on the bigger constitution-anchored model; crew Q&A on the fast
        # classifier-tier model.
        answerer = self.bot.leadership_answerer if audience == "leadership" else self.bot.answerer
        try:
            async with message.channel.typing():
                ans = await answerer.answer(question, audience)
        except AllBackendsDown:
            await message.channel.send("I'm offline right now — ask a manager.")
            return

        if ans.kind == "answer":
            await message.channel.send(f"{ans.answer}\n-# from *{ans.topic}*")
            return
        if ans.kind == "not_work_related":
            await message.channel.send(REFUSAL_NOT_WORK)
            return
        # not_in_kb: honest refusal + feed the docs backlog
        await message.channel.send(REFUSAL_NOT_IN_KB)
        with self._session() as session:
            session.add(
                UnansweredQuestion(author_id=message.author.id, question=question, surface=surface)
            )
            session.commit()
            channel = mod_log_channel(self.bot, session)
            if channel:
                await channel.send(
                    f"❓ Unanswered ({surface}) from {message.author}: {question[:300]}"
                )

    # --- listeners -----------------------------------------------------------

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        if member.bot:
            return
        greeting = GREETING if self.bot.settings.crew_qa_enabled else GREETING_NO_QA
        try:
            await member.send(greeting.format(name=member.display_name))
        except (discord.Forbidden, discord.HTTPException):
            pass  # DMs closed — nothing to do

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or not message.content:
            return

        # DM: leadership always gets management guidance; crew Q&A is gated (OFF at launch).
        if message.guild is None:
            from crewguard.cogs.admin import is_access_command, is_policy_command

            if is_policy_command(message.content) or is_access_command(message.content):
                return  # the Admin cog owns policy/access commands; don't double-reply
            audience = self._audience(message.author)
            if audience != "leadership" and not self.bot.settings.crew_qa_enabled:
                await message.channel.send(REFUSAL_QA_DISABLED)
                return
            if self._over_limit(message.author.id):
                await message.channel.send(REFUSAL_CAP)
                return
            await self._respond(message, message.content, "dm", audience)
            return

        # Everything below is crew-facing (@mention, unprompted interjection) — OFF at launch.
        if not self.bot.settings.crew_qa_enabled:
            return

        # Moderation runs CONCURRENTLY with this listener (discord.py dispatches both cogs at
        # once), so wait out its classify+delete window, then check. Answering a message that
        # moderation is about to delete is the failure mode this buys us out of.
        await asyncio.sleep(self.moderation_grace)
        if message.id in getattr(self.bot, "recently_removed", ()):
            return

        # @CrewGuard mention: explicit ask, answer in channel.
        if self.bot.user in message.mentions:
            if self._over_limit(message.author.id):
                await message.channel.send(REFUSAL_CAP)
                return
            question = message.content.replace(self.bot.user.mention, "").strip()
            if question:
                await self._respond(message, question, "mention")
            return

        # Unprompted interjection: cheap pre-filter, then the strict LLM gate. Silence default.
        if not looks_like_question(message.content):
            return
        try:
            verdict = await self.bot.answerer.consider_interjecting(message.content)
        except AllBackendsDown:
            return  # nobody asked us; stay quiet
        if verdict.speak and verdict.answer:
            await message.channel.send(f"{verdict.answer}\n-# from *{verdict.topic}*")


async def setup(bot) -> None:
    await bot.add_cog(Assistant(bot))
