"""Heartbeat: one pinned status message in #mod-log, refreshed on a timer.

The GM sees at a glance that CrewGuard is alive and whether the smart tier is up — the
positive mirror of the "classifier offline" warning. It EDITS a single pinned message rather than
posting on every tick, so it never buries the removal log.
"""

import logging
import time
from datetime import UTC, datetime

import discord
from discord.ext import commands, tasks
from sqlmodel import Session, select

from crewguard.access import access_buckets
from crewguard.models import ConsentRecord, ModerationLog
from crewguard.moderation.classifier import ClassifierUnavailable
from crewguard.moderation.enforce import mod_log_channel

log = logging.getLogger(__name__)

HEARTBEAT_MINUTES = 20.0
ERROR_ALERT_COOLDOWN = 900.0  # 15 min — don't spam #mod-log while a box is flapping
MARKER = "CrewGuard status"  # identifies our pinned message so we can re-adopt it after a restart


def _watch_freshness(last: datetime | None, now: datetime, interval_min: int) -> str:
    if last is None:
        return "starting…"
    mins = int((now - last).total_seconds() // 60)
    if mins > 2 * interval_min:
        return f"⚠️ stalled — last ran {mins}m ago"
    return f"ran {mins}m ago"


class Heartbeat(commands.Cog):
    def __init__(self, bot, clock=time.monotonic) -> None:
        self.bot = bot
        self._message: discord.Message | None = None
        self._clock = clock
        self._last_error_alert = float("-inf")

    async def cog_load(self) -> None:
        self.beat.start()

    async def cog_unload(self) -> None:
        self.beat.cancel()

    @tasks.loop(minutes=HEARTBEAT_MINUTES)
    async def beat(self) -> None:
        await self._safe_tick()

    async def _safe_tick(self) -> None:
        """A single failing tick must never kill the loop: log, alert, and let the next tick retry."""
        try:
            await self._tick()
        except Exception as exc:
            log.exception("heartbeat tick failed")
            await self._alert_error(exc)

    async def _tick(self) -> None:
        with Session(self.bot.engine) as session:
            channel = mod_log_channel(self.bot, session)
            if channel is None:
                return
            embed = await self._status(session)
        await self._publish(channel, embed)

    @beat.before_loop
    async def _before(self) -> None:
        await self.bot.wait_until_ready()

    @beat.error
    async def _on_error(self, exc: Exception) -> None:
        """Last resort — if a tick's exception ever escapes the defensive body, revive the loop."""
        log.exception("heartbeat loop error", exc_info=exc)
        await self._alert_error(exc)
        self.beat.restart()

    async def _alert_error(self, exc: Exception) -> None:
        now = self._clock()
        if now - self._last_error_alert < ERROR_ALERT_COOLDOWN:
            return
        self._last_error_alert = now
        try:
            with Session(self.bot.engine) as session:
                channel = mod_log_channel(self.bot, session)
            if channel is not None:
                await channel.send(
                    f"⚠️ Heartbeat hit an error and is recovering: {type(exc).__name__}."
                )
        except Exception:
            # A last-resort alert must never raise: if even alerting fails (a dead #mod-log, a DB
            # read error looking it up), swallow it, or it would escape _safe_tick/_on_error and
            # kill the loop — the exact silent death this whole layer exists to prevent.
            log.warning("could not post heartbeat error alert", exc_info=True)

    async def _status(self, session: Session) -> discord.Embed:
        try:
            await self.bot.classifier.classify("status check")
            up = True
        except ClassifierUnavailable:
            up = False

        start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        removals = len(session.exec(select(ModerationLog).where(ModerationLog.ts >= start)).all())
        consented = len(session.exec(select(ConsentRecord)).all())

        embed = discord.Embed(
            title="✅ CrewGuard is up" if up else "⚠️ CrewGuard is up — classifier offline",
            colour=discord.Colour.green() if up else discord.Colour.dark_orange(),
        )
        embed.add_field(
            name="Coverage",
            value="full — AutoMod + classifier"
            if up
            else "AutoMod only — hostility in clean words is NOT being caught",
            inline=False,
        )
        embed.add_field(
            name="Classifier",
            value=f"{'up' if up else 'down'} · {self.bot.settings.classifier_model}",
            inline=True,
        )
        embed.add_field(
            name="Leadership model", value=self.bot.settings.leadership_model, inline=True
        )
        embed.add_field(name="Removals today", value=str(removals), inline=True)
        embed.add_field(name="Consented members", value=str(consented), inline=True)
        guild = self.bot.get_guild(self.bot.settings.guild_id)
        waiting = len(access_buckets(guild, session).waiting)
        embed.add_field(name="Waiting for approval", value=str(waiting), inline=True)
        last = getattr(self.bot, "last_watch_ok", None)
        embed.add_field(
            name="Watcher",
            value=_watch_freshness(
                last, discord.utils.utcnow(), int(self.bot.settings.watch_interval_minutes)
            ),
            inline=True,
        )
        embed.set_footer(text=f"{MARKER} · updates every {int(HEARTBEAT_MINUTES)} min")
        embed.timestamp = discord.utils.utcnow()
        return embed

    async def _publish(self, channel, embed: discord.Embed) -> None:
        if self._message is not None:
            try:
                await self._message.edit(embed=embed)
                return
            except discord.HTTPException:
                self._message = None  # message gone; fall through and re-create

        for pin in await channel.pins():
            footer = pin.embeds[0].footer.text if (pin.embeds and pin.embeds[0].footer) else ""
            if pin.author.id == self.bot.user.id and MARKER in (footer or ""):
                self._message = pin
                await pin.edit(embed=embed)
                return

        msg = await channel.send(embed=embed)
        await msg.pin()
        self._message = msg


async def setup(bot) -> None:
    await bot.add_cog(Heartbeat(bot))
