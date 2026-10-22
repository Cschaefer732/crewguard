from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from sqlmodel import Session

from crewguard import config_store as cfg
from crewguard import guild_spec as spec
from crewguard.cogs.heartbeat import Heartbeat, _watch_freshness
from crewguard.models import ConsentRecord, ModerationLog
from crewguard.moderation.classifier import ClassifierUnavailable, Verdict

from .discord_mocks import make_bot, make_channel, make_member, make_role


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
    b.settings = MagicMock(
        classifier_model="qwen3:8b",
        leadership_model="qwen3-coder:30b",
        guild_id=1,
        watch_interval_minutes=30,
    )
    b.get_guild = MagicMock(return_value=None)  # no guild -> waiting 0; specific tests override
    b.classifier = MagicMock()
    b.classifier.classify = AsyncMock(return_value=Verdict("allow", "none", ""))
    return b


@pytest.fixture
def cog(bot):
    return Heartbeat(bot, clock=FakeClock())


async def test_status_reports_up_when_classifier_answers(cog, bot, engine):
    with Session(engine) as s:
        embed = await cog._status(s)
    assert "up" in embed.title.lower()
    assert embed.colour == discord.Colour.green()


async def test_status_reports_degraded_when_classifier_down(cog, bot, engine):
    bot.classifier.classify.side_effect = ClassifierUnavailable("down")
    with Session(engine) as s:
        embed = await cog._status(s)
    assert "offline" in embed.title.lower()
    assert embed.colour == discord.Colour.dark_orange()


async def test_status_counts_todays_removals_and_consents(cog, bot, engine):
    with Session(engine) as s:
        s.add(
            ModerationLog(
                tier="classifier",
                author_id=1,
                author_name="a",
                channel_id=1,
                content="x",
                category="poor_attitude",
                reason="y",
            )
        )
        s.add(
            ModerationLog(
                tier="classifier",
                author_id=1,
                author_name="a",
                channel_id=1,
                content="old",
                category="poor_attitude",
                reason="y",
                ts=datetime.now(UTC) - timedelta(days=2),
            )
        )
        s.add(ConsentRecord(user_id=5, user_name="c", version="v1"))
        s.commit()
        embed = await cog._status(s)
    fields = {f.name: f.value for f in embed.fields}
    assert fields["Removals today"] == "1"  # the 2-day-old one is excluded
    assert fields["Consented members"] == "1"


def test_watch_freshness_wording():
    now = datetime(2026, 7, 23, 12, 0, tzinfo=UTC)
    assert _watch_freshness(None, now, 30) == "starting…"
    assert _watch_freshness(now - timedelta(minutes=4), now, 30) == "ran 4m ago"
    stale = _watch_freshness(now - timedelta(minutes=200), now, 30)
    assert stale.startswith("⚠️ stalled")


async def test_status_shows_waiting_count(cog, bot, engine, session):
    verified = make_role(spec.VERIFIED, role_id=500)
    cfg.set_setting(session, cfg.role_key(spec.VERIFIED), "500")
    alice = make_member("alice", member_id=10)
    alice.roles = [verified]
    guild = MagicMock()
    guild.members = [alice]
    bot.get_guild = MagicMock(return_value=guild)

    with Session(engine) as s:
        embed = await cog._status(s)

    fields = {f.name: f.value for f in embed.fields}
    assert fields["Waiting for approval"] == "1"
    assert "Watcher" in fields


async def test_publish_creates_and_pins_when_absent(cog, bot, mod_log):
    mod_log.pins = AsyncMock(return_value=[])
    posted = MagicMock()
    posted.pin = AsyncMock()
    mod_log.send = AsyncMock(return_value=posted)

    await cog._publish(mod_log, discord.Embed(title="✅ CrewGuard is up"))

    mod_log.send.assert_awaited_once()
    posted.pin.assert_awaited_once()
    assert cog._message is posted


async def test_publish_edits_existing_message_without_reposting(cog, bot, mod_log):
    existing = MagicMock()
    existing.edit = AsyncMock()
    cog._message = existing

    await cog._publish(mod_log, discord.Embed(title="⚠️ offline"))

    existing.edit.assert_awaited_once()
    mod_log.send.assert_not_awaited()


async def test_tick_failure_is_swallowed_and_alerted(cog, bot, mod_log):
    """A failing tick must not kill the loop — it logs, alerts #mod-log, and returns."""
    cog._status = AsyncMock(side_effect=RuntimeError("boom"))

    await cog._safe_tick()  # must not raise

    assert mod_log.send.await_count == 1
    assert "Heartbeat hit an error" in mod_log.send.await_args.args[0]


async def test_tick_failure_alert_is_rate_limited(cog, bot, mod_log):
    cog._status = AsyncMock(side_effect=RuntimeError("boom"))

    await cog._safe_tick()
    await cog._safe_tick()  # same clock tick — must not re-alert

    assert mod_log.send.await_count == 1

    cog._clock.t = 1000.0
    await cog._safe_tick()
    assert mod_log.send.await_count == 2


async def test_error_handler_alerts_and_restarts(cog, bot, mod_log):
    cog.beat.restart = MagicMock()

    await cog._on_error(RuntimeError("loop died"))

    cog.beat.restart.assert_called_once()
    assert mod_log.send.await_count == 1


async def test_alert_error_never_raises_on_a_non_http_failure(cog, bot, mod_log):
    """A last-resort alert must never itself kill the loop — even a DB/other error is swallowed."""
    mod_log.send = AsyncMock(side_effect=RuntimeError("db is down"))
    cog._status = AsyncMock(side_effect=RuntimeError("tick boom"))

    await cog._safe_tick()  # must not raise despite the alert path also failing
