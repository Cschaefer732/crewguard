from datetime import UTC, datetime, timedelta

from sqlmodel import Session, select

from crewguard.models import (
    ConsentRecord,
    LeadershipNote,
    ModerationLog,
    PolicyDirective,
    UnansweredQuestion,
)
from crewguard.retention import cutoff, forget_user, purge

NOW = datetime(2026, 7, 15, tzinfo=UTC)


def _log(session, author_id=42, age_days=0):
    row = ModerationLog(
        tier="classifier",
        author_id=author_id,
        author_name="crew1",
        channel_id=100,
        content="something removed",
        category="poor_attitude",
        reason="x",
        ts=NOW - timedelta(days=age_days),
    )
    session.add(row)
    session.commit()
    return row


def test_cutoff():
    assert cutoff(NOW, 30) == NOW - timedelta(days=30)


def test_purge_deletes_old_keeps_recent(engine):
    with Session(engine) as s:
        _log(s, age_days=45)  # older than 30d -> gone
        _log(s, age_days=10)  # within window -> kept
        s.add(
            LeadershipNote(
                author_id=7,
                author_name="boss",
                channel_id=1,
                content="do the thing",
                ts=NOW - timedelta(days=60),
            )
        )
        s.add(
            UnansweredQuestion(
                author_id=8, question="pto?", surface="dm", ts=NOW - timedelta(days=5)
            )
        )
        s.commit()

        counts = purge(s, days=30, now=NOW)

    assert counts == {"ModerationLog": 1, "LeadershipNote": 1, "UnansweredQuestion": 0}
    with Session(engine) as s:
        assert len(s.exec(select(ModerationLog)).all()) == 1
        assert s.exec(select(LeadershipNote)).all() == []
        assert len(s.exec(select(UnansweredQuestion)).all()) == 1


def test_purge_never_touches_config_or_directives_or_consent(engine):
    with Session(engine) as s:
        s.add(
            PolicyDirective(author_name="boss", text="allow selling", ts=NOW - timedelta(days=365))
        )
        s.add(
            ConsentRecord(user_id=42, user_name="crew1", version="v1", ts=NOW - timedelta(days=365))
        )
        s.commit()

        purge(s, days=30, now=NOW)

    with Session(engine) as s:
        assert len(s.exec(select(PolicyDirective)).all()) == 1
        assert s.get(ConsentRecord, 42) is not None  # proof-of-consent survives the time purge


def test_forget_user_deletes_only_that_user(engine):
    with Session(engine) as s:
        _log(s, author_id=42, age_days=1)
        _log(s, author_id=99, age_days=1)
        s.add(ConsentRecord(user_id=42, user_name="crew1", version="v1"))
        s.commit()

        counts = forget_user(s, 42)

    assert counts["ModerationLog"] == 1
    assert counts["ConsentRecord"] == 1
    with Session(engine) as s:
        remaining = s.exec(select(ModerationLog)).all()
        assert [r.author_id for r in remaining] == [99]  # the other user is untouched
        assert s.get(ConsentRecord, 42) is None
