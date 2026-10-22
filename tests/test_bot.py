import discord

from crewguard.bot import invite_url, make_intents


def test_message_content_intent_is_on():
    """Without it, message.content is empty and the classifier sees nothing at all."""
    assert make_intents().message_content is True


def test_automod_execution_intent_is_on():
    """Without it, AutoMod blocks never reach us: no DM to the author, nothing in #mod-log."""
    assert make_intents().auto_moderation_execution is True


def test_invite_asks_for_what_moderation_needs():
    perms = discord.Permissions(int(invite_url(123).split("permissions=")[1].split("&")[0]))
    assert perms.manage_roles  # provision roles
    assert perms.manage_channels  # provision channels
    assert perms.manage_guild  # AutoMod rules
    assert perms.manage_messages  # delete
    assert perms.send_messages  # #mod-log


def test_invite_does_not_ask_to_time_people_out():
    """No strikes, no timeouts. Don't request a power we never use."""
    perms = discord.Permissions(int(invite_url(123).split("permissions=")[1].split("&")[0]))
    assert not perms.moderate_members
    assert not perms.ban_members
    assert not perms.kick_members
