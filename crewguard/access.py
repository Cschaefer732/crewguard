"""Who has agreed but isn't in yet — the approval worklist and the grant-failure backstop.

Reads live guild roles + the ConsentRecord table. Two buckets:
- waiting:    holds Verified but no rank -> a manager needs to assign Crew.
- unverified: agreed (has a ConsentRecord) but no Verified role and no rank -> the grant failed.
"""

from dataclasses import dataclass, field

import discord
from sqlmodel import Session, select

from crewguard import config_store as cfg
from crewguard import guild_spec as spec
from crewguard.models import ConsentRecord

# Any of these means the member is already in — they are not "waiting".
RANKS = spec.MEMBERS + (spec.SERVER_ADMIN,)


@dataclass
class AccessBuckets:
    waiting: list = field(default_factory=list)
    unverified: list = field(default_factory=list)


def access_buckets(guild: discord.Guild | None, session: Session) -> AccessBuckets:
    buckets = AccessBuckets()
    if guild is None:
        return buckets
    verified_id = cfg.get_id(session, cfg.role_key(spec.VERIFIED))
    rank_ids = {rid for r in RANKS if (rid := cfg.get_id(session, cfg.role_key(r)))}
    consented = {r.user_id for r in session.exec(select(ConsentRecord)).all()}
    for member in guild.members:
        if getattr(member, "bot", False):
            continue
        role_ids = {r.id for r in getattr(member, "roles", ())}
        if role_ids & rank_ids:
            continue  # already in
        if verified_id and verified_id in role_ids:
            buckets.waiting.append(member)
        elif member.id in consented:
            buckets.unverified.append(member)
    return buckets
