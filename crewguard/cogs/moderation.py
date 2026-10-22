"""Both moderation tiers. They funnel into the same enforce().

on_message         -> tier 2, the classifier (harassment, threats, poor attitude)
on_automod_action  -> tier 1, Discord AutoMod (profanity, slurs, sexual)

AutoMod's block_message rejects a message BEFORE it is created, so on_message never fires for it.
Without the on_automod_action listener, tier 1 would be a silent black hole: the offender sees
Discord's generic block notice, gets no DM from us, and nothing reaches #mod-log.
"""

import logging
import time
from collections import deque

import discord
from discord.ext import commands, tasks
from sqlmodel import Session, select

from crewguard import config_store as cfg
from crewguard import guild_spec as spec
from crewguard.config import DEFAULT_WATCH_INTERVAL_MINUTES
from crewguard.models import LeadershipNote, ModerationLog
from crewguard.moderation.classifier import ClassifierUnavailable
from crewguard.moderation.enforce import (
    Removal,
    enforce,
    mod_log_channel,
    removal_log_channel,
)

TUNING_EXAMPLES = 15  # newest leadership-overruled removals fed back as allow-examples

log = logging.getLogger(__name__)

DEGRADED_WARNING_COOLDOWN = 900.0  # 15 min — don't spam #mod-log while a box is rebooting
SWEEP_LIMIT = 100  # recent messages re-checked per channel after a rule change or on the watcher


class Moderation(commands.Cog):
    def __init__(self, bot, clock=time.monotonic) -> None:
        self.bot = bot
        # Assistant cog checks this before answering a guild message — a removed message must
        # never get a reply. Bounded; ids only need to live for the coordination window.
        self.bot.recently_removed = deque(maxlen=500)
        self._clock = clock
        self._sweeping = False
        # -inf, not 0: a monotonic clock starting near zero would otherwise put the very first
        # outage inside its own cooldown and swallow the warning — the one thing that must never
        # be silent.
        self._last_degraded_warning = float("-inf")
        self._last_loop_error_alert = float("-inf")

    def _session(self) -> Session:
        return Session(self.bot.engine)

    async def cog_load(self) -> None:
        if self.bot.settings.watcher_enabled:
            self.watch.change_interval(minutes=self.bot.settings.watch_interval_minutes)
            self.watch.start()

    async def cog_unload(self) -> None:
        self.watch.cancel()

    @tasks.loop(minutes=DEFAULT_WATCH_INTERVAL_MINUTES)
    async def watch(self) -> None:
        """The watcher: re-scan recent history on a timer and remove anything that now breaks the
        rules — the backstop for messages edited into hostility, or let through while the classifier
        was briefly down. Quiet: it only posts to #message-removal-log when it actually removes
        something."""
        await self._safe_watch()

    async def _safe_watch(self) -> None:
        """A single failing scan must not kill the backstop loop: log, alert, and retry next tick."""
        try:
            await self.sweep("scheduled watch", quiet=True, from_watch=True)
        except Exception as exc:
            log.exception("watch tick failed")
            await self._alert_loop_error(exc)

    @watch.before_loop
    async def _before_watch(self) -> None:
        await self.bot.wait_until_ready()

    @watch.error
    async def _on_watch_error(self, exc: Exception) -> None:
        """Last resort — revive the loop if an exception ever escapes the defensive body."""
        log.exception("watch loop error", exc_info=exc)
        await self._alert_loop_error(exc)
        self.watch.restart()

    async def _alert_loop_error(self, exc: Exception) -> None:
        now = self._clock()
        if now - self._last_loop_error_alert < DEGRADED_WARNING_COOLDOWN:
            return
        self._last_loop_error_alert = now
        try:
            with self._session() as session:
                channel = mod_log_channel(self.bot, session)
            if channel is not None:
                await channel.send(
                    f"⚠️ The watcher hit an error and is recovering: {type(exc).__name__}."
                )
        except Exception:
            # A last-resort alert must never raise: if even alerting fails (a dead #mod-log, a DB
            # read error looking it up), swallow it, or it would escape _safe_watch/_on_watch_error
            # and kill the loop — the exact silent death this whole layer exists to prevent.
            log.warning("could not post watcher error alert", exc_info=True)

    async def _warn_degraded(self, session: Session, error: Exception) -> None:
        """Never fail open *silently*. Managers must know the smart tier is dark."""
        now = self._clock()
        if now - self._last_degraded_warning < DEGRADED_WARNING_COOLDOWN:
            return
        self._last_degraded_warning = now
        log.error("classifier unavailable: %s", error)

        channel = mod_log_channel(self.bot, session)
        if channel:
            await channel.send(
                embed=discord.Embed(
                    title="⚠️ Classifier offline — AutoMod-only coverage",
                    description=(
                        "Profanity and slurs are still blocked by Discord AutoMod, but **hostility "
                        "written in clean words is NOT being caught right now.** Keep an eye on the "
                        "chat until this clears."
                    ),
                    colour=discord.Colour.dark_orange(),
                )
            )

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or not message.guild or not message.content:
            return

        with self._session() as session:
            try:
                from crewguard.cogs.admin import active_directives

                verdict = await self.bot.classifier.classify(
                    message.content,
                    extra_allow=_tuning_examples(session),
                    directives=active_directives(session),
                )
            except ClassifierUnavailable as e:
                await self._warn_degraded(session, e)
                return

            if verdict.needs_review:
                # The model answered unintelligibly. Surface it — but do NOT delete on a parse error.
                channel = mod_log_channel(self.bot, session)
                if channel:
                    await channel.send(
                        f"Couldn't classify a message from {message.author.mention} "
                        f"in {message.channel.mention}. Left it up — worth a look."
                    )
                return

            if not verdict.flagged:
                self._capture_leadership(session, message)
                return

            self.bot.recently_removed.append(message.id)
            await enforce(
                self.bot,
                session,
                Removal(
                    tier="classifier",
                    author=message.author,
                    channel_id=message.channel.id,
                    content=message.content,
                    category=verdict.category,
                    reason=verdict.reason,
                    message=message,
                ),
            )

    async def sweep(self, trigger: str, quiet: bool = False, *, from_watch: bool = False) -> None:
        """Re-check recent channel history under the CURRENT rules — run after a policy change so
        a new rule applies to what's already in the chat, not just what comes next, and on the
        watcher's timer. Removal-only by nature: a loosened rule can't restore messages Discord
        already deleted. `quiet` skips the summary post when nothing was removed (for the watcher,
        which runs often and must not spam #mod-log).
        """
        if self._sweeping:
            return
        self._sweeping = True
        try:
            guild = self.bot.get_guild(self.bot.settings.guild_id)
            if guild is None:
                return
            checked = removed = 0
            with self._session() as session:
                from crewguard.cogs.admin import active_directives

                allow = _tuning_examples(session)
                directives = active_directives(session)
                for channel in guild.text_channels:
                    if channel.name in ("mod-log", "message-removal-log"):
                        continue
                    async for msg in channel.history(limit=SWEEP_LIMIT):
                        if msg.author.bot or not msg.content:
                            continue
                        checked += 1
                        try:
                            verdict = await self.bot.classifier.classify(
                                msg.content, extra_allow=allow, directives=directives
                            )
                        except ClassifierUnavailable as e:
                            await self._warn_degraded(session, e)
                            return
                        if not verdict.flagged:
                            continue
                        self.bot.recently_removed.append(msg.id)
                        await enforce(
                            self.bot,
                            session,
                            Removal(
                                tier="classifier",
                                author=msg.author,
                                channel_id=channel.id,
                                content=msg.content,
                                category=verdict.category,
                                reason=f"sweep after {trigger}: {verdict.reason}",
                                message=msg,
                            ),
                        )
                        removed += 1
                if from_watch:
                    self.bot.last_watch_ok = discord.utils.utcnow()
                log_channel = removal_log_channel(self.bot, session)
                if log_channel and not (quiet and removed == 0):
                    await log_channel.send(
                        f"🔁 Re-checked {checked} recent messages after {trigger}: "
                        f"removed {removed}."
                    )
        finally:
            self._sweeping = False

    def _capture_leadership(self, session: Session, message: discord.Message) -> None:
        """GM words are raw material for future KB docs. Passive, no LLM. (The assistant manager is treated
        as a service supervisor now, so only the GM counts as leadership here.)"""
        lead_ids = {rid for role in (spec.GM,) if (rid := cfg.get_id(session, cfg.role_key(role)))}
        author_role_ids = {r.id for r in getattr(message.author, "roles", ())}
        if lead_ids & author_role_ids:
            session.add(
                LeadershipNote(
                    author_id=message.author.id,
                    author_name=str(message.author),
                    channel_id=message.channel.id,
                    content=message.content,
                )
            )
            session.commit()

    @commands.Cog.listener()
    async def on_automod_action(self, execution: discord.AutoModAction) -> None:
        """Tier 1 hit. The message was never created, so there is nothing to delete —
        but the author still gets the same DM and it still lands in #mod-log.
        """
        if execution.action.type is not discord.AutoModRuleActionType.block_message:
            return

        author = execution.member or self.bot.get_user(execution.user_id)
        if author is None and execution.user_id:
            try:
                author = await self.bot.fetch_user(execution.user_id)  # API, not cache
            except discord.HTTPException:
                author = None
        if author is None or author.bot:
            return

        with self._session() as session:
            await enforce(
                self.bot,
                session,
                Removal(
                    tier="automod",
                    author=author,
                    channel_id=execution.channel_id or 0,
                    content=execution.content or "",
                    category="profanity",
                    reason=f"Discord AutoMod: {execution.matched_keyword or 'blocked term'}",
                    message=None,
                ),
            )


def _tuning_examples(session: Session) -> tuple[str, ...]:
    """Messages leadership marked "too strict" — the aggressiveness knob (see feedback_ui)."""
    rows = session.exec(
        select(ModerationLog)
        .where(ModerationLog.feedback == "too_strict")
        .order_by(ModerationLog.id.desc())
        .limit(TUNING_EXAMPLES)
    ).all()
    return tuple(r.content for r in rows)


async def setup(bot) -> None:
    await bot.add_cog(Moderation(bot))
