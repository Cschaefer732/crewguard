"""Data retention. Two jobs:

- purge(): delete stored message content older than the retention window. Run on a schedule.
- forget_user(): delete everything tied to one person on a deletion request. Discord's Developer
  Terms require a working data-deletion mechanism for any bot that stores user data.

Only content-bearing tables are in scope. Config and PolicyDirective are operational settings, not
message content, so they are never purged. A ConsentRecord is the proof-of-acceptance and survives
the time purge, but forget_user() removes it too — a deletion request means the person is gone.
"""

from datetime import UTC, datetime, timedelta

from sqlmodel import Session, select

from crewguard.models import (
    ConsentRecord,
    LeadershipNote,
    ModerationLog,
    UnansweredQuestion,
)

# Every one carries user-authored content, a `ts`, and an `author_id`.
CONTENT_MODELS = (ModerationLog, LeadershipNote, UnansweredQuestion)


def cutoff(now: datetime, days: int) -> datetime:
    return now - timedelta(days=days)


def purge(session: Session, days: int, now: datetime | None = None) -> dict[str, int]:
    """Delete content rows older than `days`. Returns per-table delete counts."""
    edge = cutoff(now or datetime.now(UTC), days)
    counts: dict[str, int] = {}
    for model in CONTENT_MODELS:
        rows = session.exec(select(model).where(model.ts < edge)).all()
        for row in rows:
            session.delete(row)
        counts[model.__name__] = len(rows)
    session.commit()
    return counts


def forget_user(session: Session, user_id: int) -> dict[str, int]:
    """Delete everything tied to one user, including their consent record."""
    counts: dict[str, int] = {}
    for model in CONTENT_MODELS:
        rows = session.exec(select(model).where(model.author_id == user_id)).all()
        for row in rows:
            session.delete(row)
        counts[model.__name__] = len(rows)
    consent = session.get(ConsentRecord, user_id)
    if consent:
        session.delete(consent)
    counts["ConsentRecord"] = 1 if consent else 0
    session.commit()
    return counts
