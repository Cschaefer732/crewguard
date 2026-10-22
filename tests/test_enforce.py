from unittest.mock import MagicMock

import discord
import pytest
from sqlmodel import select

from crewguard import config_store as cfg
from crewguard.models import ModerationLog
from crewguard.moderation.enforce import Removal, enforce

from .discord_mocks import make_bot, make_channel, make_member, make_message


@pytest.fixture
def mod_log():
    return make_channel("mod-log", channel_id=900)


@pytest.fixture
def bot(engine, mod_log, session):
    cfg.set_setting(session, cfg.channel_key("mod-log"), "900")
    return make_bot(engine, channel=mod_log)


def _removal(author, message=None, **kw):
    return Removal(
        tier=kw.get("tier", "classifier"),
        author=author,
        channel_id=kw.get("channel_id", 100),
        content=kw.get("content", "nobody here can stand you"),
        category=kw.get("category", "poor_attitude"),
        reason=kw.get("reason", "demeaning a coworker"),
        message=message,
    )


async def test_deletes_dms_and_logs(bot, session, mod_log):
    author = make_member("crew1")
    msg = make_message("nobody here can stand you", author=author)

    row = await enforce(bot, session, _removal(author, msg))

    msg.delete.assert_awaited_once()
    author.send.assert_awaited_once()
    mod_log.send.assert_awaited_once()
    assert row.deleted and row.dm_sent


async def test_dm_quotes_the_message_and_cites_the_category(bot, session):
    author = make_member("crew1")
    msg = make_message("nobody here can stand you", author=author)

    await enforce(bot, session, _removal(author, msg))

    dm = author.send.await_args.args[0]
    assert "nobody here can stand you" in dm
    assert dm.startswith("Removed")
    assert "1-on-1" not in dm


async def test_closed_dms_do_not_break_the_delete_or_the_log(bot, session, mod_log):
    """A user with DMs off must still get their message removed and logged."""
    author = make_member("crew1")
    author.send.side_effect = discord.Forbidden(_resp(403), "cannot send")
    msg = make_message("do your own job", author=author)

    row = await enforce(bot, session, _removal(author, msg))

    msg.delete.assert_awaited_once()
    mod_log.send.assert_awaited_once()
    assert row.deleted
    assert row.dm_sent is False


async def test_mod_log_notes_when_the_dm_failed(bot, session, mod_log):
    author = make_member("crew1")
    author.send.side_effect = discord.Forbidden(_resp(403), "nope")
    await enforce(bot, session, _removal(author, make_message(author=author)))

    embed = mod_log.send.await_args.kwargs["embed"]
    assert "DMs are closed" in embed.footer.text


async def test_automod_removal_has_no_message_to_delete_but_still_dms_and_logs(
    bot, session, mod_log
):
    """AutoMod blocks before creation — there is no Message object. The user must still hear why."""
    author = make_member("crew1")

    row = await enforce(
        bot, session, _removal(author, message=None, tier="automod", category="profanity")
    )

    author.send.assert_awaited_once()
    mod_log.send.assert_awaited_once()
    assert row.tier == "automod"
    assert row.message_id is None
    assert row.deleted is False  # nothing to delete; it never existed


async def test_row_is_persisted_with_the_evidence(bot, session):
    author = make_member("crew1", member_id=77)
    await enforce(bot, session, _removal(author, make_message(author=author)))

    rows = session.exec(select(ModerationLog)).all()
    assert len(rows) == 1
    assert rows[0].author_id == 77
    assert rows[0].content == "nobody here can stand you"
    assert rows[0].category == "poor_attitude"


async def test_missing_mod_log_channel_does_not_crash_enforcement(engine, session):
    """No mod-log configured yet: still delete, still DM, still persist."""
    bot = make_bot(engine, channel=None)
    author = make_member("crew1")
    msg = make_message(author=author)

    row = await enforce(bot, session, _removal(author, msg))

    msg.delete.assert_awaited_once()
    assert row.deleted


def _resp(status):
    class R:
        def __init__(self):
            self.status = status
            self.reason = "Forbidden"

    return R()


async def test_third_removal_in_a_week_pings_managers(bot, session, mod_log):
    """No strikes — but a pattern gets surfaced to humans."""
    author = make_member("repeat", member_id=88)
    for _ in range(3):
        await enforce(bot, session, _removal(author, make_message(author=author)))

    texts = [c.args[0] for c in mod_log.send.await_args_list if c.args]
    pattern_msgs = [t for t in texts if "Pattern" in t]
    assert len(pattern_msgs) == 1
    assert "3 removals" in pattern_msgs[0]


async def test_under_threshold_no_ping(bot, session, mod_log):
    author = make_member("once", member_id=89)
    await enforce(bot, session, _removal(author, make_message(author=author)))
    await enforce(bot, session, _removal(author, make_message(author=author)))

    texts = [c.args[0] for c in mod_log.send.await_args_list if c.args]
    assert not [t for t in texts if "Pattern" in t]


async def test_removal_posts_to_removal_log_not_mod_log(engine, session):
    """Removed messages go to #message-removal-log; #mod-log is for changes/status only."""
    modlog = make_channel("mod-log", channel_id=900)
    removallog = make_channel("message-removal-log", channel_id=901)
    cfg.set_setting(session, cfg.channel_key("mod-log"), "900")
    cfg.set_setting(session, cfg.channel_key("message-removal-log"), "901")
    bot = make_bot(engine)
    bot.get_channel = MagicMock(side_effect=lambda cid: {900: modlog, 901: removallog}.get(cid))
    author = make_member("crew1")

    await enforce(bot, session, _removal(author, make_message(author=author)))

    removallog.send.assert_awaited()  # the removal embed
    modlog.send.assert_not_awaited()  # no per-message content in the changes/status channel


async def test_redirect_categories_get_the_not_for_this_dm(bot, session):
    author = make_member("gossiper")
    msg = make_message("shes dating the new guy", author=author)
    await enforce(bot, session, _removal(author, msg, category="gossip"))

    dm = author.send.await_args.args[0]
    assert "Don't use the crew server for this" in dm
    assert "1-on-1" not in dm
