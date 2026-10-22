from unittest.mock import MagicMock

from crewguard import config_store as cfg
from crewguard import guild_spec as spec
from crewguard.access import access_buckets
from crewguard.models import ConsentRecord

from .discord_mocks import make_member, make_role


def test_none_guild_gives_empty_buckets(session):
    b = access_buckets(None, session)
    assert b.waiting == [] and b.unverified == []


def test_buckets_split_waiting_from_unverified(session):
    verified = make_role(spec.VERIFIED, role_id=500)
    crew = make_role(spec.CREW, role_id=501)
    cfg.set_setting(session, cfg.role_key(spec.VERIFIED), "500")
    cfg.set_setting(session, cfg.role_key(spec.CREW), "501")

    alice = make_member("alice", member_id=10)
    alice.roles = [verified]  # agreed, no rank -> waiting
    bob = make_member("bob", member_id=11)
    bob.roles = [verified, crew]  # already in -> excluded
    carol = make_member("carol", member_id=12)
    carol.roles = []  # agreed but no Verified -> unverified
    dave = make_member("dave", member_id=13)
    dave.roles = []  # never agreed -> excluded
    botu = make_member("botu", member_id=14, is_bot=True)
    botu.roles = []
    erin = make_member("erin", member_id=15)
    erin.roles = [verified]  # agreed AND has ConsentRecord -> still waiting, never unverified

    session.add(ConsentRecord(user_id=12, user_name="carol", version="v1"))
    session.add(ConsentRecord(user_id=15, user_name="erin", version="v1"))
    session.commit()

    guild = MagicMock()
    guild.members = [alice, bob, carol, dave, botu, erin]

    b = access_buckets(guild, session)

    assert [m.name for m in b.waiting] == ["alice", "erin"]
    assert [m.name for m in b.unverified] == ["carol"]
    # Verified + consented resolves via the `if` branch, never falls through to the `elif`.
    assert "erin" in [m.name for m in b.waiting]
    assert "erin" not in [m.name for m in b.unverified]
