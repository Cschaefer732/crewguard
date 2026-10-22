"""The single enforcement path. Both tiers land here — see AGENTS.md.

Deliberately gentle: delete, DM, log. No strikes, no timeouts, no bans.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import discord
from sqlmodel import Session, select

from crewguard import config_store as cfg
from crewguard import guild_spec as spec
from crewguard.feedback_ui import feedback_view
from crewguard.models import ModerationLog
from crewguard.moderation.prompt import CATEGORY_LABEL, build_dm

log = logging.getLogger(__name__)

MOD_LOG = "mod-log"  # CrewGuard/server changes + status
REMOVAL_LOG = "message-removal-log"  # the removed messages themselves

# No strikes, ever — but a pattern is something a manager should hear about once.
PATTERN_THRESHOLD = 3
PATTERN_WINDOW = timedelta(days=7)


@dataclass(frozen=True)
class Removal:
    """What happened, whichever tier caught it."""

    tier: str  # "automod" | "classifier"
    author: discord.abc.User
    channel_id: int
    content: str
    category: str
    reason: str
    message: discord.Message | None = None  # None for automod: it was never created


async def _dm(author: discord.abc.User, content: str, category: str) -> bool:
    try:
        await author.send(build_dm(content, category))
        return True
    except (discord.Forbidden, discord.HTTPException):
        # DMs closed, or the bot is blocked. Still delete, still log.
        return False


def _log_channel(bot, session: Session, name: str) -> discord.TextChannel | None:
    channel_id = cfg.get_id(session, cfg.channel_key(name))
    return bot.get_channel(channel_id) if channel_id else None


def mod_log_channel(bot, session: Session) -> discord.TextChannel | None:
    """CrewGuard/server changes + status (policy edits, classifier up/down, heartbeat, constitution)."""
    return _log_channel(bot, session, MOD_LOG)


def removal_log_channel(bot, session: Session) -> discord.TextChannel | None:
    """The removed messages themselves. Falls back to #mod-log if it isn't provisioned yet, so a
    removal is never silently lost on a half-provisioned server."""
    return _log_channel(bot, session, REMOVAL_LOG) or _log_channel(bot, session, MOD_LOG)


def _embed(r: Removal, dm_sent: bool, row_id: int | None = None) -> discord.Embed:
    tag = f"#{row_id} — " if row_id else ""
    e = discord.Embed(
        title=f"{tag}{CATEGORY_LABEL.get(r.category, r.category)}",
        description=r.content[:1024] or "(no text)",
        colour=discord.Colour.orange() if r.tier == "automod" else discord.Colour.red(),
    )
    e.add_field(name="Author", value=f"{r.author} ({r.author.id})", inline=True)
    e.add_field(name="Channel", value=f"<#{r.channel_id}>", inline=True)
    e.add_field(name="Caught by", value=r.tier, inline=True)
    e.add_field(name="Why", value=r.reason or "—", inline=False)
    if not dm_sent:
        e.set_footer(text="Could not DM this user — their DMs are closed.")
    return e


async def enforce(bot, session: Session, r: Removal) -> ModerationLog:
    """Delete (if the message exists), DM the author, log it. Returns the log row."""
    deleted = False
    if r.message is not None:
        try:
            await r.message.delete()
            deleted = True
        except discord.NotFound:
            deleted = True  # already gone; nothing to do
        except discord.HTTPException:
            log.warning("could not delete message %s", r.message.id)

    dm_sent = await _dm(r.author, r.content, r.category)

    row = ModerationLog(
        tier=r.tier,
        author_id=r.author.id,
        author_name=str(r.author),
        channel_id=r.channel_id,
        message_id=r.message.id if r.message else None,
        content=r.content,
        category=r.category,
        reason=r.reason,
        deleted=deleted,
        dm_sent=dm_sent,
    )
    session.add(row)
    session.commit()

    channel = removal_log_channel(bot, session)
    if channel:
        # Review buttons only make sense for classifier removals — "too strict" feeds allow-
        # examples back into the classifier prompt, which AutoMod's wordlist never consults.
        view = feedback_view(row.id) if r.tier == "classifier" else None
        await channel.send(embed=_embed(r, dm_sent, row.id), view=view)
        await _ping_on_pattern(channel, session, r)
    return row


def _recent_removals(session: Session, author_id: int) -> int:
    since = datetime.now(UTC) - PATTERN_WINDOW
    rows = session.exec(
        select(ModerationLog).where(ModerationLog.author_id == author_id, ModerationLog.ts >= since)
    ).all()
    return len(rows)


async def _ping_on_pattern(channel, session: Session, r: Removal) -> None:
    """3+ removals in a week -> tell the managers once per removal past the line.
    Humans escalate; the bot never does.
    """
    count = _recent_removals(session, r.author.id)
    if count < PATTERN_THRESHOLD:
        return
    mentions = " ".join(
        f"<@&{rid}>" for role in (spec.GM,) if (rid := cfg.get_id(session, cfg.role_key(role)))
    )
    await channel.send(
        f"⚠️ {mentions} Pattern: {r.author} has {count} removals in the last 7 days.",
        allowed_mentions=discord.AllowedMentions(roles=True),
    )
