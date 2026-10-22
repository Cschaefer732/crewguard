from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from sqlmodel import select

from crewguard import config_store as cfg
from crewguard.cogs.moderation import Moderation
from crewguard.models import ModerationLog
from crewguard.moderation.classifier import ClassifierUnavailable, Verdict

from .discord_mocks import make_bot, make_channel, make_member, make_message


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


@pytest.fixture
def mod_log():
    return make_channel("mod-log", channel_id=900)


@pytest.fixture
def bot(engine, mod_log, session):
    cfg.set_setting(session, cfg.channel_key("mod-log"), "900")
    b = make_bot(engine, channel=mod_log)
    b.classifier = MagicMock()
    b.classifier.classify = AsyncMock(return_value=Verdict("allow", "none", ""))
    return b


@pytest.fixture
def cog(bot):
    return Moderation(bot, clock=FakeClock())


def _rows(engine):
    from sqlmodel import Session

    with Session(engine) as s:
        return s.exec(select(ModerationLog)).all()


async def test_flagged_message_is_deleted_and_logged(cog, bot, engine, mod_log):
    bot.classifier.classify.return_value = Verdict("flag", "poor_attitude", "demeaning")
    msg = make_message("nobody here can stand you")

    await cog.on_message(msg)

    msg.delete.assert_awaited_once()
    msg.author.send.assert_awaited_once()
    assert len(_rows(engine)) == 1


async def test_clean_message_is_untouched(cog, bot, engine):
    msg = make_message("can anyone cover saturday")
    await cog.on_message(msg)

    msg.delete.assert_not_awaited()
    msg.author.send.assert_not_awaited()
    assert _rows(engine) == []


async def test_bot_messages_are_ignored(cog, bot):
    msg = make_message("beep", author=make_member("bot", is_bot=True))
    await cog.on_message(msg)
    bot.classifier.classify.assert_not_awaited()


async def test_empty_messages_are_ignored(cog, bot):
    """Image-only posts have no content to classify."""
    await cog.on_message(make_message(""))
    bot.classifier.classify.assert_not_awaited()


async def test_classifier_down_warns_in_mod_log_and_does_not_delete(cog, bot, mod_log, engine):
    bot.classifier.classify.side_effect = ClassifierUnavailable("all backends down")
    msg = make_message("something nasty")

    await cog.on_message(msg)

    msg.delete.assert_not_awaited()  # never delete on a guess
    embed = mod_log.send.await_args.kwargs["embed"]
    assert "Classifier offline" in embed.title
    assert _rows(engine) == []


async def test_degraded_warning_is_rate_limited(cog, bot, mod_log):
    """A rebooting box must not spam #mod-log on every message."""
    bot.classifier.classify.side_effect = ClassifierUnavailable("down")

    for _ in range(5):
        await cog.on_message(make_message("x"))

    assert mod_log.send.await_count == 1


async def test_degraded_warning_repeats_after_cooldown(cog, bot, mod_log):
    bot.classifier.classify.side_effect = ClassifierUnavailable("down")

    await cog.on_message(make_message("x"))
    cog._clock.t = 1000.0
    await cog.on_message(make_message("y"))

    assert mod_log.send.await_count == 2


async def test_unparseable_verdict_is_surfaced_but_not_deleted(cog, bot, mod_log, engine):
    """A parse error must never delete an innocent message."""
    bot.classifier.classify.return_value = Verdict("review", "unparseable", "bad json")
    msg = make_message("ambiguous")

    await cog.on_message(msg)

    msg.delete.assert_not_awaited()
    mod_log.send.assert_awaited_once()
    assert _rows(engine) == []


def _automod_execution(content="you piece of trash", member=None):
    ex = MagicMock()
    ex.action.type = discord.AutoModRuleActionType.block_message
    ex.member = member or make_member("crew1")
    ex.user_id = ex.member.id
    ex.channel_id = 100
    ex.content = content
    ex.matched_keyword = "trash"
    return ex


async def test_automod_hit_is_dmd_and_logged(cog, bot, mod_log, engine):
    """Without this, AutoMod is a silent black hole: no DM, nothing in #mod-log."""
    ex = _automod_execution()

    await cog.on_automod_action(ex)

    ex.member.send.assert_awaited_once()
    mod_log.send.assert_awaited_once()
    rows = _rows(engine)
    assert len(rows) == 1
    assert rows[0].tier == "automod"


async def test_automod_alert_only_actions_are_ignored(cog, bot, engine):
    """Only a real block is a removal. An alert-only action isn't."""
    ex = _automod_execution()
    ex.action.type = discord.AutoModRuleActionType.send_alert_message

    await cog.on_automod_action(ex)

    assert _rows(engine) == []


async def test_automod_hit_on_uncached_member_is_fetched_and_enforced(cog, bot, mod_log, engine):
    """A member missing from cache must be fetched, not dropped — otherwise tier 1 is the exact
    silent black hole the listener exists to prevent."""
    fetched = make_member("ghost", member_id=321)
    fetched.__str__ = MagicMock(return_value="ghost")
    bot.get_user = MagicMock(return_value=None)  # cache miss
    bot.fetch_user = AsyncMock(return_value=fetched)
    ex = _automod_execution(member=None)
    ex.member = None  # _automod_execution(member=None) falls back to a default member; force it
    ex.user_id = 321

    await cog.on_automod_action(ex)

    bot.fetch_user.assert_awaited_once_with(321)
    fetched.send.assert_awaited_once()
    assert len(_rows(engine)) == 1


async def test_automod_hit_drops_cleanly_when_fetch_fails(cog, bot, engine):
    """A deleted/blocked account can't be fetched — drop without raising."""
    bot.get_user = MagicMock(return_value=None)
    bot.fetch_user = AsyncMock(
        side_effect=discord.HTTPException(MagicMock(status=404), "Unknown User")
    )
    ex = _automod_execution(member=None)
    ex.member = None  # _automod_execution(member=None) falls back to a default member; force it
    ex.user_id = 321

    await cog.on_automod_action(ex)  # must not raise

    assert _rows(engine) == []


def _history_channel(name, channel_id, messages):
    ch = make_channel(name, channel_id=channel_id)

    def history(limit=None):
        async def gen():
            for m in messages:
                yield m

        return gen()

    ch.history = history
    return ch


def _sweep_guild(bot, channels):
    guild = MagicMock()
    guild.text_channels = channels
    bot.get_guild = MagicMock(return_value=guild)
    bot.settings = MagicMock(guild_id=1)
    return guild


async def test_sweep_removes_now_flagged_messages_and_reports(cog, bot, engine, mod_log):
    hostile = make_message("nobody here can stand you")
    clean = make_message("can anyone cover saturday")
    from_bot = make_message("beep", author=make_member("helperbot", is_bot=True))
    general = _history_channel("general", 100, [clean, hostile, from_bot])
    _sweep_guild(bot, [general])

    async def classify(text, **kwargs):
        if "stand you" in text:
            return Verdict("flag", "poor_attitude", "demeaning")
        return Verdict("allow", "none", "")

    bot.classifier.classify = AsyncMock(side_effect=classify)

    await cog.sweep("policy #1")

    hostile.delete.assert_awaited_once()
    hostile.author.send.assert_awaited_once()
    clean.delete.assert_not_awaited()
    assert len(_rows(engine)) == 1
    summary = [c.args[0] for c in mod_log.send.await_args_list if c.args][-1]
    assert "Re-checked 2" in summary and "removed 1" in summary


async def test_sweep_skips_mod_log_channel(cog, bot, engine, mod_log):
    logged = make_message("#3 — hostility toward a coworker")
    log_ch = _history_channel("mod-log", 900, [logged])
    _sweep_guild(bot, [log_ch])

    await cog.sweep("policy #1")

    bot.classifier.classify.assert_not_awaited()
    logged.delete.assert_not_awaited()


async def test_sweep_skips_the_removal_log_channel(cog, bot, engine, mod_log):
    """The removal log is CrewGuard's own output — never re-scan it."""
    logged = make_message("#3 — hostility toward a coworker")
    _sweep_guild(bot, [_history_channel("message-removal-log", 901, [logged])])

    await cog.sweep("policy #1")

    bot.classifier.classify.assert_not_awaited()
    logged.delete.assert_not_awaited()


async def test_sweep_aborts_when_classifier_is_down(cog, bot, engine, mod_log):
    msg = make_message("whatever")
    _sweep_guild(bot, [_history_channel("general", 100, [msg])])
    bot.classifier.classify = AsyncMock(side_effect=ClassifierUnavailable("down"))

    await cog.sweep("policy #1")

    msg.delete.assert_not_awaited()
    titles = [c.kwargs["embed"].title for c in mod_log.send.await_args_list if "embed" in c.kwargs]
    assert any("offline" in t for t in titles)


async def test_scheduled_watch_is_silent_when_it_removes_nothing(cog, bot, engine, mod_log):
    """The watcher runs on a timer — a clean scan must not spam #mod-log."""
    clean = make_message("can anyone cover saturday")
    _sweep_guild(bot, [_history_channel("general", 100, [clean])])

    await cog.sweep("scheduled watch", quiet=True)

    clean.delete.assert_not_awaited()
    summaries = [
        c.args[0] for c in mod_log.send.await_args_list if c.args and "Re-checked" in c.args[0]
    ]
    assert summaries == []


async def test_scheduled_watch_reports_when_it_removes(cog, bot, engine, mod_log):
    """A quiet watch still tells managers when it actually deletes something."""
    hostile = make_message("nobody here can stand you")
    _sweep_guild(bot, [_history_channel("general", 100, [hostile])])
    bot.classifier.classify = AsyncMock(return_value=Verdict("flag", "poor_attitude", "demeaning"))

    await cog.sweep("scheduled watch", quiet=True)

    hostile.delete.assert_awaited_once()
    summaries = [
        c.args[0] for c in mod_log.send.await_args_list if c.args and "Re-checked" in c.args[0]
    ]
    assert len(summaries) == 1 and "removed 1" in summaries[0]


async def test_watch_tick_survives_a_sweep_error(cog, bot, mod_log):
    """If a sweep raises, the watch tick alerts and returns — the loop stays alive."""
    cog.sweep = AsyncMock(side_effect=RuntimeError("history blew up"))

    await cog._safe_watch()  # must not raise

    titles = [c.args[0] for c in mod_log.send.await_args_list if c.args]
    assert any("watcher hit an error" in t for t in titles)


async def test_watch_error_handler_alerts_and_restarts(cog, bot, mod_log):
    cog.watch.restart = MagicMock()

    await cog._on_watch_error(RuntimeError("loop died"))

    cog.watch.restart.assert_called_once()
    assert any("watcher hit an error" in c.args[0] for c in mod_log.send.await_args_list if c.args)


async def test_watch_stamps_last_watch_ok(cog, bot, engine, mod_log):
    from datetime import datetime

    bot.last_watch_ok = None
    clean = make_message("can anyone cover saturday")
    _sweep_guild(bot, [_history_channel("general", 100, [clean])])

    await cog._safe_watch()

    assert isinstance(bot.last_watch_ok, datetime)


async def test_policy_sweep_does_not_stamp_last_watch_ok(cog, bot, engine, mod_log):
    """A GM policy change runs sweep() too — it must NOT mark the watch loop as freshly run, or the
    heartbeat would read healthy while the scheduled watch is dead."""
    bot.last_watch_ok = None
    clean = make_message("can anyone cover saturday")
    _sweep_guild(bot, [_history_channel("general", 100, [clean])])

    await cog.sweep("policy #1")

    assert bot.last_watch_ok is None


async def test_watch_alert_never_raises_on_a_non_http_failure(cog, bot, mod_log):
    """A last-resort alert must never itself kill the loop — even a DB/other error is swallowed."""
    mod_log.send = AsyncMock(side_effect=RuntimeError("db is down"))
    cog.sweep = AsyncMock(side_effect=RuntimeError("sweep boom"))

    await cog._safe_watch()  # must not raise despite the alert path also failing


async def test_watch_alert_is_rate_limited(cog, bot, mod_log):
    """A flapping backstop must not spam #mod-log — one alert per cooldown window, then re-fire."""
    cog.sweep = AsyncMock(side_effect=RuntimeError("boom"))

    await cog._safe_watch()  # alert #1 fires
    await cog._safe_watch()  # same clock tick -> suppressed
    assert mod_log.send.await_count == 1

    cog._clock.t = 1000.0  # past DEGRADED_WARNING_COOLDOWN (900s)
    await cog._safe_watch()  # re-fires
    assert mod_log.send.await_count == 2
