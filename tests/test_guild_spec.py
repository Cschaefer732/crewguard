"""The permission matrix, asserted cell by cell. If the spec drifts, this fails.

Access model: nobody reads anything until a manager assigns a rank. Verified is a consent badge,
not an access key. assistant manager is treated as a service supervisor.
"""

import pytest

from crewguard.guild_spec import (
    APPRENTICE,
    CHANNELS,
    CREW,
    CT_BOH,
    CT_FOH,
    GM,
    KITCHEN_LEADER,
    MANAGERS,
    MEMBERS,
    ROLES,
    SERVER_ADMIN,
    SERVICE_LEADER,
    VERIFIED,
    can_send,
    can_view,
)

CREW_LEVEL = (CREW, CT_FOH, CT_BOH)  # members, not managers
MEMBER_CHANNELS = ("general", "casual", "schedule", "announcements", "resources")
GATED = MEMBER_CHANNELS + ("managers", "mod-log", "message-removal-log")
ALL_CHANNELS = tuple(c.name for c in CHANNELS)


def test_nine_roles():
    assert len(ROLES) == 9


def test_members_are_crew_and_up_not_the_consent_badge():
    assert set(MEMBERS) == {CREW, CT_FOH, CT_BOH, KITCHEN_LEADER, SERVICE_LEADER, APPRENTICE, GM}
    assert VERIFIED not in MEMBERS


def test_certified_trainers_are_not_managers():
    assert CT_FOH not in MANAGERS
    assert CT_BOH not in MANAGERS


# --- the gate: nothing until a manager assigns a rank -----------------------


@pytest.mark.parametrize("ch", GATED)
def test_new_joiner_and_consent_badge_see_nothing_but_welcome(ch):
    """@everyone (no rank) is denied at the channel level, and a bare Verified badge (agreed but not
    yet approved) grants no access either."""
    spec_ch = next(c for c in CHANNELS if c.name == ch)
    assert spec_ch.everyone_view is False
    assert not can_view(ch, VERIFIED)  # agreeing does not unlock anything


def test_welcome_is_world_readable():
    """The entry point must stay readable pre-approval, or nobody can read the rules and agree."""
    assert can_view("welcome-and-rules", CREW)
    assert can_view("welcome-and-rules", VERIFIED)


@pytest.mark.parametrize("ch", MEMBER_CHANNELS)
def test_verified_is_explicitly_revoked_in_member_channels(ch):
    """Not merely inherited-deny: Verified used to be the access key, so an explicit False is
    required to overwrite the stale grant when re-provisioning (which never removes overwrites)."""
    spec_ch = next(c for c in CHANNELS if c.name == ch)
    assert spec_ch.role_overrides.get(VERIFIED) == (False, False)


# --- crew-level members ------------------------------------------------------


@pytest.mark.parametrize("role", CREW_LEVEL)
@pytest.mark.parametrize("ch", ("general", "casual"))
def test_crew_reads_and_writes_crew_chat(role, ch):
    assert can_view(ch, role)
    assert can_send(ch, role)


@pytest.mark.parametrize("role", CREW_LEVEL)
@pytest.mark.parametrize("ch", ("schedule", "announcements", "resources"))
def test_crew_reads_but_cannot_write_info_channels(role, ch):
    assert can_view(ch, role)
    assert not can_send(ch, role)


@pytest.mark.parametrize("role", CREW_LEVEL)
@pytest.mark.parametrize("ch", ("managers", "mod-log", "message-removal-log"))
def test_crew_cannot_see_manager_or_moderation_channels(role, ch):
    assert not can_view(ch, role)


# --- managers ----------------------------------------------------------------


@pytest.mark.parametrize("role", MANAGERS)
def test_managers_see_and_post_in_managers_channel(role):
    assert can_view("managers", role)
    assert can_send("managers", role)


@pytest.mark.parametrize("role", MANAGERS)
@pytest.mark.parametrize("ch", ("announcements", "resources"))
def test_managers_post_announcements_and_resources(role, ch):
    assert can_send(ch, role)


# --- schedule: GM posts, everyone else read-only (including the assistant manager) ---


def test_only_gm_posts_the_schedule():
    assert can_send("schedule", GM)
    for role in (APPRENTICE, SERVICE_LEADER, KITCHEN_LEADER, *CREW_LEVEL):
        assert not can_send("schedule", role)
        assert can_view("schedule", role)  # still read-only visible


# --- assistant manager is treated as a service supervisor -------------------------------


def test_apprentice_matches_service_leader_in_every_channel():
    for ch in ALL_CHANNELS:
        assert can_view(ch, APPRENTICE) == can_view(ch, SERVICE_LEADER), f"view {ch}"
        assert can_send(ch, APPRENTICE) == can_send(ch, SERVICE_LEADER), f"send {ch}"


def test_apprentice_lost_its_elevated_access():
    assert not can_view("mod-log", APPRENTICE)
    assert not can_view("message-removal-log", APPRENTICE)
    assert not can_send("schedule", APPRENTICE)


# --- moderation channels: GM only --------------------------------------------


@pytest.mark.parametrize("ch", ("mod-log", "message-removal-log"))
def test_moderation_channels_visible_to_gm_only(ch):
    assert can_view(ch, GM)
    assert not can_send(ch, GM)  # GM reads; only the bot/admin writes
    for role in (APPRENTICE, SERVICE_LEADER, KITCHEN_LEADER, *CREW_LEVEL):
        assert not can_view(ch, role)


# --- welcome posting is GM only now ------------------------------------------


@pytest.mark.parametrize("role", (APPRENTICE, SERVICE_LEADER, KITCHEN_LEADER, *CREW_LEVEL))
def test_only_gm_posts_in_welcome(role):
    assert not can_send("welcome-and-rules", role)


def test_gm_posts_in_welcome():
    assert can_send("welcome-and-rules", GM)


# --- roster + server admin ---------------------------------------------------


def test_channel_roster():
    assert {c.name for c in CHANNELS} == {
        "welcome-and-rules",
        "announcements",
        "resources",
        "general",
        "casual",
        "managers",
        "schedule",
        "mod-log",
        "message-removal-log",
    }


@pytest.mark.parametrize("ch", ALL_CHANNELS)
def test_server_admin_can_view_and_send_everywhere(ch):
    """The bot's operator goes anywhere the bot goes."""
    assert can_view(ch, SERVER_ADMIN)
    assert can_send(ch, SERVER_ADMIN)


def test_server_admin_outranks_gm():
    assert ROLES.index(SERVER_ADMIN) > ROLES.index(GM)


def test_server_admin_is_not_a_manager():
    assert SERVER_ADMIN not in MANAGERS
