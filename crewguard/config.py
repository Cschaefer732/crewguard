from dataclasses import dataclass, field

from dotenv import dotenv_values

DEFAULT_BACKENDS = ("http://127.0.0.1:11434",)
# qwen3:8b is a hybrid-reasoning model — the classifier MUST send think=false, or it spends
# its whole token budget thinking and returns empty content. See classifier.py.
DEFAULT_MODEL = "qwen3:8b"

# Leadership (GM / Server Admin) guidance uses a larger model on its own backend list.
# Those calls are rare and can afford the slower 30B; the per-message classifier cannot,
# which is why it stays on qwen3:8b. Localhost is the default, same as the classifier.
DEFAULT_LEADERSHIP_MODEL = "qwen3-coder:30b"
DEFAULT_LEADERSHIP_BACKENDS = ("http://127.0.0.1:11434",)


DEFAULT_RETENTION_DAYS = 30  # keep stored message content this long, then auto-delete

# The watcher re-scans recent channel history on a timer and removes anything that now violates the
# rules — the safety net for messages edited into hostility after posting, or let through while the
# classifier was briefly down. on_message still catches everything at post time; this is backstop.
DEFAULT_WATCH_INTERVAL_MINUTES = 30


@dataclass(frozen=True)
class Settings:
    discord_token: str
    app_id: int | None
    guild_id: int | None
    db_path: str
    classifier_model: str = DEFAULT_MODEL
    # Ordered. First healthy backend wins.
    classifier_backends: tuple[str, ...] = field(default=DEFAULT_BACKENDS)
    # Leadership guidance model (GM / Server Admin) — bigger, slower, always-on. Separate from the classifier.
    leadership_model: str = DEFAULT_LEADERSHIP_MODEL
    leadership_backends: tuple[str, ...] = field(default=DEFAULT_LEADERSHIP_BACKENDS)
    retention_days: int = DEFAULT_RETENTION_DAYS
    # Crew-facing Q&A (DM answers, @mention, interjection). OFF at launch — the feature lives on
    # branch feature/crew-assistant. Leadership guidance and the join greeting are unaffected.
    crew_qa_enabled: bool = False
    # Periodic re-scan of existing messages. ON by default — it's a moderation backstop.
    watcher_enabled: bool = True
    watch_interval_minutes: int = DEFAULT_WATCH_INTERVAL_MINUTES


def _int_or_none(v: str | None) -> int | None:
    if not v or v.startswith("paste-"):
        return None
    return int(v)


def _bool(v: str | None, default: bool = False) -> bool:
    if v is None or not v.strip():
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def _backends(v: str | None, default: tuple[str, ...] = DEFAULT_BACKENDS) -> tuple[str, ...]:
    if not v or v.startswith("paste-"):
        return default
    return tuple(u.strip().rstrip("/") for u in v.split(",") if u.strip())


def load_settings(env_path: str | None = None) -> Settings:
    vals = dotenv_values(env_path) if env_path else dotenv_values()
    return Settings(
        discord_token=vals.get("DISCORD_TOKEN", ""),
        app_id=_int_or_none(vals.get("APP_ID")),
        guild_id=_int_or_none(vals.get("GUILD_ID")),
        db_path=vals.get("DB_PATH", "crewguard.db"),
        classifier_model=vals.get("CLASSIFIER_MODEL") or DEFAULT_MODEL,
        classifier_backends=_backends(vals.get("CLASSIFIER_BACKENDS")),
        leadership_model=vals.get("LEADERSHIP_MODEL") or DEFAULT_LEADERSHIP_MODEL,
        leadership_backends=_backends(vals.get("LEADERSHIP_BACKENDS"), DEFAULT_LEADERSHIP_BACKENDS),
        retention_days=_int_or_none(vals.get("RETENTION_DAYS")) or DEFAULT_RETENTION_DAYS,
        crew_qa_enabled=_bool(vals.get("CREW_QA_ENABLED")),
        watcher_enabled=_bool(vals.get("WATCHER_ENABLED"), default=True),
        watch_interval_minutes=_int_or_none(vals.get("WATCH_INTERVAL_MINUTES"))
        or DEFAULT_WATCH_INTERVAL_MINUTES,
    )
