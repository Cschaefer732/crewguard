"""Moderation verdicts over the shared Ollama client. Pure: no Discord, no DB (see AGENTS.md)."""

import json
import time
from dataclasses import dataclass

import httpx

from crewguard.moderation.prompt import (
    EXEMPTION_SCHEMA,
    VERDICT_SCHEMA,
    build_exemption_messages,
    build_messages,
)
from crewguard.ollama import AllBackendsDown, OllamaClient


class ClassifierUnavailable(RuntimeError):
    """Every backend is down. Caller must degrade loudly, never fail open silently."""


@dataclass(frozen=True)
class Verdict:
    action: str  # allow | flag | review
    category: str
    reason: str

    @property
    def flagged(self) -> bool:
        return self.action == "flag"

    @property
    def needs_review(self) -> bool:
        """The model answered, but unintelligibly. Surface it; do not delete on a parse error."""
        return self.action == "review"


UNPARSEABLE = Verdict(
    action="review", category="unparseable", reason="classifier returned invalid JSON"
)


class Classifier:
    def __init__(
        self,
        backends: tuple[str, ...],
        model: str,
        *,
        connect_timeout: float = 0.3,
        read_timeout: float = 10.0,
        cooldown: float = 60.0,
        client: httpx.AsyncClient | None = None,
        clock=time.monotonic,
    ) -> None:
        self._llm = OllamaClient(
            backends,
            model,
            connect_timeout=connect_timeout,
            read_timeout=read_timeout,
            cooldown=cooldown,
            client=client,
            clock=clock,
        )
        # Kept for tests/introspection; failover state lives in the shared client.
        self._backends = backends

    def _available(self) -> list[str]:
        return self._llm._available()

    async def classify(
        self,
        text: str,
        extra_allow: tuple[str, ...] = (),
        directives: tuple[str, ...] = (),
    ) -> Verdict:
        try:
            raw = await self._llm.chat(
                build_messages(text, extra_allow, directives),
                schema=VERDICT_SCHEMA,
                num_predict=100,
                temperature=0.0,
            )
        except AllBackendsDown as e:
            raise ClassifierUnavailable(str(e)) from e
        verdict = _parse(raw)

        # Directive check as a second, focused call: in-prompt directives lose to the built-in
        # category definitions for anything but exact wording. Only costs a call on flagged
        # messages, and any error here fails toward the original verdict.
        if verdict.flagged and directives:
            try:
                raw = await self._llm.chat(
                    build_exemption_messages(text, directives),
                    schema=EXEMPTION_SCHEMA,
                    num_predict=80,
                    temperature=0.0,
                )
                d = json.loads(raw)
                if d.get("exempt") is True:
                    return Verdict(
                        action="allow",
                        category="none",
                        reason=f"leadership directive: {d.get('reason', '')}",
                    )
            except (AllBackendsDown, json.JSONDecodeError, KeyError, TypeError):
                pass
        return verdict

    async def aclose(self) -> None:
        await self._llm.aclose()


def _parse(raw: str) -> Verdict:
    try:
        data = json.loads(raw)
        return Verdict(
            action=data["action"], category=data["category"], reason=data.get("reason", "")
        )
    except (json.JSONDecodeError, KeyError, TypeError):
        return UNPARSEABLE
