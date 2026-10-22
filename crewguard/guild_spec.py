"""The server, as pure data. No Discord imports, no DB — see AGENTS.md.

Roles are listed lowest-authority first; provisioning positions them in this order.
Trainers are certifications layered on crew, NOT rank — they get no manager access.
"""

from dataclasses import dataclass, field

# Not a rank — a consent badge. Granted when a member accepts the participation agreement, so a
# manager can see at a glance who has agreed. It no longer unlocks channels: access is
# manager-assigned (see MEMBERS). A new joiner reads nothing until a manager gives them a rank.
VERIFIED = "Verified"

CREW = "Crew"
CT_FOH = "Front of House Trainer"
CT_BOH = "Back of House Trainer"
KITCHEN_LEADER = "Kitchen Supervisor"
SERVICE_LEADER = "Service Supervisor"
APPRENTICE = "Assistant Manager"
GM = "General Manager"
# The bot's operator. Same permission set as the bot itself (the bot is an extension of them);
# sits above GM, directly below the bot's own role.
SERVER_ADMIN = "Server Admin"

# Ascending authority. Index = relative position (higher index sits higher in Discord).
# Verified is lowest — a consent badge, no authority and no access on its own.
ROLES: tuple[str, ...] = (
    VERIFIED,
    CREW,
    CT_FOH,
    CT_BOH,
    KITCHEN_LEADER,
    SERVICE_LEADER,
    APPRENTICE,
    GM,
    SERVER_ADMIN,
)

MANAGERS: tuple[str, ...] = (KITCHEN_LEADER, SERVICE_LEADER, APPRENTICE, GM)
# Only the GM posts the schedule now — the assistant manager is treated as a service supervisor (read-only).
SCHEDULE_POSTERS: tuple[str, ...] = (GM,)
# Approved members — everyone holding a rank (Crew and up). Access is manager-assigned: a brand-new
# joiner has no rank and sees nothing but #welcome-and-rules until a manager gives them Crew.
MEMBERS: tuple[str, ...] = (CREW, CT_FOH, CT_BOH, KITCHEN_LEADER, SERVICE_LEADER, APPRENTICE, GM)

EVERYONE = "@everyone"


@dataclass(frozen=True)
class ChannelSpec:
    name: str
    topic: str
    # None = inherit. Explicit True/False = allow/deny.
    everyone_view: bool
    everyone_send: bool
    # Role name -> (view, send). Applied as overwrites on top of @everyone.
    role_overrides: dict[str, tuple[bool, bool]] = field(default_factory=dict)


def _managers_only(view: bool, send: bool) -> dict[str, tuple[bool, bool]]:
    # Server Admin always gets full access — the bot's operator can go anywhere the bot can.
    return {r: (view, send) for r in MANAGERS} | {SERVER_ADMIN: (True, True)}


def _posters(roles: tuple[str, ...]) -> dict[str, tuple[bool, bool]]:
    return {r: (True, True) for r in roles} | {SERVER_ADMIN: (True, True)}


def _members(send: bool) -> dict[str, tuple[bool, bool]]:
    """Every approved member (Crew and up) can see the channel; `send` sets whether they can post.
    @everyone — a brand-new joiner with no rank — is denied at the channel level, so nobody reads
    anything until a manager assigns them a rank. Server Admin is always exempt.

    Verified is EXPLICITLY denied: it used to be the access key, so these channels carry a stale
    grant for it, and re-provisioning replaces overwrites but never removes them — only an explicit
    False overwrites the old allow. A consent badge must not unlock anything on its own."""
    return {r: (True, send) for r in MEMBERS} | {
        VERIFIED: (False, False),
        SERVER_ADMIN: (True, True),
    }


def _mod_internals() -> dict[str, tuple[bool, bool]]:
    """Moderation internals — GM only. The assistant manager is treated as a service supervisor, so its view is
    EXPLICITLY revoked (not merely omitted): re-provisioning replaces overwrites but never removes
    them, and an earlier spec granted the assistant manager and the supervisors view — only an
    explicit False overwrites that."""
    return {
        GM: (True, False),
        APPRENTICE: (False, False),
        SERVICE_LEADER: (False, False),
        KITCHEN_LEADER: (False, False),
        SERVER_ADMIN: (True, True),
    }


CHANNELS: tuple[ChannelSpec, ...] = (
    ChannelSpec(
        # Matches the channel the server template already created — adopted, not recreated.
        name="welcome-and-rules",
        topic="Start here. Read the rules and the participation agreement, then agree. A manager will give you access to the rest of the server.",
        everyone_view=True,
        everyone_send=False,
        role_overrides=_posters(SCHEDULE_POSTERS),
    ),
    ChannelSpec(
        name="announcements",
        topic="Store updates from management. Managers post; members read.",
        everyone_view=False,
        everyone_send=False,
        role_overrides=_members(send=False) | _managers_only(view=True, send=True),
    ),
    ChannelSpec(
        name="resources",
        topic="Handbooks, training links, station guides, contacts. Managers post; members read.",
        everyone_view=False,
        everyone_send=False,
        role_overrides=_members(send=False) | _managers_only(view=True, send=True),
    ),
    ChannelSpec(
        name="general",
        topic="Crew chat. Messages that break the rules in #welcome-and-rules are removed automatically; the sender gets a DM with the reason. Venting about the job is allowed.",
        everyone_view=False,
        everyone_send=False,
        role_overrides=_members(send=True),
    ),
    ChannelSpec(
        name="casual",
        topic="Off-shift chatter for the crew — memes, plans, whatever. Same rules as #general.",
        everyone_view=False,
        everyone_send=False,
        role_overrides=_members(send=True),
    ),
    ChannelSpec(
        name="managers",
        topic=(
            "Leadership chat — GM, assistant managers, service supervisors, and kitchen "
            "supervisors. Invisible to crew and trainers."
        ),
        everyone_view=False,  # invisible to Crew and CTs, not merely locked
        everyone_send=False,
        role_overrides=_managers_only(view=True, send=True),
    ),
    ChannelSpec(
        name="schedule",
        topic="The weekly schedule. The GM posts it; everyone else is read-only.",
        everyone_view=False,
        everyone_send=False,
        role_overrides=_members(send=False) | _posters(SCHEDULE_POSTERS),
    ),
    ChannelSpec(
        name="mod-log",
        topic=(
            "CrewGuard's control channel — changes and status only. Removed messages live in "
            "#message-removal-log. "
            "📜 = a policy change. "
            "⚠️ classifier offline / ✅ back online. "
            "CrewGuard's live status and its constitution are pinned here. "
            "The GM sets standing rules by DMing CrewGuard: "
            "policy add <rule> · policy list · policy remove <id>."
        ),
        everyone_view=False,
        everyone_send=False,
        role_overrides=_mod_internals(),
    ),
    ChannelSpec(
        name="message-removal-log",
        topic=(
            "Every message CrewGuard removes, numbered, with the reason. "
            "✅ Good call / ❌ Too strict buttons tune CrewGuard: ❌ makes it allow similar messages "
            "from then on, ✅ flips it back. "
            "⚠️ = one person hit 3 removals in 7 days."
        ),
        everyone_view=False,
        everyone_send=False,
        role_overrides=_mod_internals(),
    ),
)


def channel(name: str) -> ChannelSpec:
    for c in CHANNELS:
        if c.name == name:
            return c
    raise KeyError(name)


def can_view(channel_name: str, role_name: str) -> bool:
    c = channel(channel_name)
    if role_name in c.role_overrides:
        return c.role_overrides[role_name][0]
    return c.everyone_view


def can_send(channel_name: str, role_name: str) -> bool:
    c = channel(channel_name)
    if role_name in c.role_overrides:
        return c.role_overrides[role_name][1]
    return c.everyone_send
