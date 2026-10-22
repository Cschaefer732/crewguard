"""Typed get/set over the Config KV table. Provisioned IDs live here so later runs are
ID-based rather than name-based (staff can rename things in the UI).
"""

from sqlmodel import Session, select

from crewguard.models import Config


def _slug(name: str) -> str:
    return name.lower().replace(" ", "_").replace("-", "_")


def role_key(role_name: str) -> str:
    return f"role_id.{_slug(role_name)}"


def channel_key(channel_name: str) -> str:
    return f"channel_id.{_slug(channel_name)}"


def get_setting(session: Session, key: str) -> str | None:
    return session.get(Config, key).value if session.get(Config, key) else None


def set_setting(session: Session, key: str, value: str) -> None:
    row = session.get(Config, key)
    if row:
        row.value = value
    else:
        session.add(Config(key=key, value=value))
    session.commit()


def get_id(session: Session, key: str) -> int | None:
    v = get_setting(session, key)
    return int(v) if v else None


def all_settings(session: Session) -> dict[str, str]:
    return {c.key: c.value for c in session.exec(select(Config)).all()}
