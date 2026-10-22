"""discord.py object mocks.

Note: MagicMock(name=x) does NOT give the mock a `.name` string — the constructor
consumes `name` for the repr. Every mock here sets `.name` explicitly instead.
"""

from unittest.mock import AsyncMock, MagicMock


def make_role(name: str, role_id: int = 0, position: int = 0):
    role = MagicMock()
    role.name = name
    role.id = role_id or abs(hash(name)) % 10**9
    role.position = position
    role.edit = AsyncMock()
    return role


def make_channel(name: str = "general", channel_id: int = 100):
    ch = MagicMock()
    ch.name = name
    ch.id = channel_id
    ch.send = AsyncMock(return_value=MagicMock(id=999))
    ch.set_permissions = AsyncMock()
    ch.edit = AsyncMock()
    ch.overwrites = {}  # provisioning reads this to find/clear stale overwrites
    return ch


def make_guild(roles=(), channels=()):
    guild = MagicMock()
    guild.roles = list(roles)
    guild.text_channels = list(channels)
    guild.categories = []

    default = make_role("@everyone", role_id=1, position=0)
    guild.default_role = default
    guild.id = 1

    # The bot's own top role — provisioning must create roles below this.
    guild.me = MagicMock()
    guild.me.top_role = make_role("crewguard-bot", role_id=2, position=99)

    async def _create_role(*, name, **kwargs):
        role = make_role(name, position=0)
        guild.roles.append(role)
        return role

    async def _create_text_channel(name, **kwargs):
        ch = make_channel(name, channel_id=100 + len(guild.text_channels))
        guild.text_channels.append(ch)
        return ch

    guild.create_role = AsyncMock(side_effect=_create_role)
    guild.create_text_channel = AsyncMock(side_effect=_create_text_channel)
    guild.create_automod_rule = AsyncMock()
    guild.fetch_automod_rules = AsyncMock(return_value=[])
    guild.edit_role_positions = AsyncMock()
    return guild


def make_message(content: str = "hi", author=None, channel=None):
    msg = MagicMock()
    msg.id = 555
    msg.content = content
    msg.author = author or make_member("someone")
    msg.channel = channel or make_channel()
    msg.guild = MagicMock()
    msg.delete = AsyncMock()
    return msg


def make_member(name: str = "crew1", member_id: int = 42, role_names=(), is_bot: bool = False):
    member = MagicMock()
    member.name = name
    member.display_name = name
    member.id = member_id
    member.bot = is_bot
    member.roles = [make_role(n) for n in role_names]
    member.send = AsyncMock()
    member.mention = f"<@{member_id}>"
    return member


def make_bot(engine=None, channel=None):
    bot = MagicMock()
    bot.engine = engine
    bot.get_channel = MagicMock(return_value=channel)
    bot.fetch_user = AsyncMock()
    bot.get_guild = MagicMock()
    bot.user = make_member("crewguard-bot", member_id=7, is_bot=True)
    return bot
