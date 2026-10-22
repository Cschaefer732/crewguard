from datetime import UTC, datetime

from sqlmodel import Field, SQLModel


def _now() -> datetime:
    return datetime.now(UTC)


class Config(SQLModel, table=True):
    """Settings KV — provisioned role/channel IDs live here."""

    key: str = Field(primary_key=True)
    value: str


class LeadershipNote(SQLModel, table=True):
    """What GM-role members say in channels — raw material for future KB docs."""

    id: int | None = Field(default=None, primary_key=True)
    ts: datetime = Field(default_factory=_now, index=True)
    author_id: int = Field(index=True)
    author_name: str
    channel_id: int
    content: str


class UnansweredQuestion(SQLModel, table=True):
    """Questions CrewGuard couldn't answer — the docs backlog."""

    id: int | None = Field(default=None, primary_key=True)
    ts: datetime = Field(default_factory=_now, index=True)
    author_id: int
    question: str
    surface: str  # "dm" | "mention" | "interject"


class PolicyDirective(SQLModel, table=True):
    """Leadership instructions injected into the classifier prompt. DM-only, GM/Server-Admin-only."""

    id: int | None = Field(default=None, primary_key=True)
    ts: datetime = Field(default_factory=_now)
    author_name: str
    text: str


class ConsentRecord(SQLModel, table=True):
    """Proof a member accepted the participation agreement: who, which version, when. Kept while
    they remain a member; removed on a data-deletion request. NOT message content, so the
    retention purge never touches it."""

    user_id: int = Field(primary_key=True)
    user_name: str
    version: str
    ts: datetime = Field(default_factory=_now)


class ModerationLog(SQLModel, table=True):
    """Audit trail. One row per removal, whichever tier caught it."""

    id: int | None = Field(default=None, primary_key=True)
    ts: datetime = Field(default_factory=_now, index=True)
    tier: str  # "automod" | "classifier"
    author_id: int = Field(index=True)
    author_name: str
    channel_id: int
    message_id: int | None = None
    content: str
    category: str
    reason: str
    deleted: bool = True
    dm_sent: bool = True
    # GM/Server-Admin review verdict: None (unreviewed) | "good_call" | "too_strict".
    # too_strict rows feed back into the classifier prompt as allow-examples.
    feedback: str | None = None
    feedback_by: str | None = None
