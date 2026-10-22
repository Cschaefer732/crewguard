import json

import httpx
import pytest

from crewguard.moderation.classifier import (
    Classifier,
    ClassifierUnavailable,
    Verdict,
)

PRIMARY = "http://fast.example:11434"
SECONDARY = "http://fallback.example:11434"


class FakeClock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def _verdict_body(action="flag", category="poor_attitude", reason="aimed at a person"):
    return {
        "message": {
            "content": json.dumps({"action": action, "category": category, "reason": reason})
        }
    }


def _classifier(handler, clock=None, **kw):
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    return Classifier((PRIMARY, SECONDARY), "m", client=client, clock=clock or FakeClock(), **kw)


async def test_returns_verdict_from_first_backend():
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200, json=_verdict_body())

    c = _classifier(handler)
    v = await c.classify("nobody here can stand you")

    assert v == Verdict("flag", "poor_attitude", "aimed at a person")
    assert v.flagged
    assert calls == [f"{PRIMARY}/api/chat"]  # fallback never touched


async def test_falls_back_when_primary_is_down():
    """The first backend is down. Moderation must not skip a beat."""
    calls = []

    def handler(request):
        calls.append(str(request.url))
        if PRIMARY in str(request.url):
            raise httpx.ConnectError("connection refused")
        return httpx.Response(200, json=_verdict_body())

    v = await _classifier(handler).classify("do your own job for once")

    assert v.flagged
    assert calls == [f"{PRIMARY}/api/chat", f"{SECONDARY}/api/chat"]


async def test_dead_backend_is_benched_and_not_redialed_every_message():
    calls = []

    def handler(request):
        calls.append(str(request.url))
        if PRIMARY in str(request.url):
            raise httpx.ConnectError("refused")
        return httpx.Response(200, json=_verdict_body())

    clock = FakeClock()
    c = _classifier(handler, clock=clock, cooldown=60)

    await c.classify("one")
    await c.classify("two")
    await c.classify("three")

    assert calls.count(f"{PRIMARY}/api/chat") == 1  # dialed once, then benched
    assert calls.count(f"{SECONDARY}/api/chat") == 3


async def test_benched_backend_is_retried_after_cooldown():
    calls = []

    def handler(request):
        calls.append(str(request.url))
        if PRIMARY in str(request.url):
            raise httpx.ConnectError("refused")
        return httpx.Response(200, json=_verdict_body())

    clock = FakeClock()
    c = _classifier(handler, clock=clock, cooldown=60)

    await c.classify("one")
    clock.t = 61.0
    await c.classify("two")

    assert calls.count(f"{PRIMARY}/api/chat") == 2  # primary came back up; we check again


async def test_recovered_backend_is_unbenched():
    state = {"primary_up": False}

    def handler(request):
        if PRIMARY in str(request.url) and not state["primary_up"]:
            raise httpx.ConnectError("refused")
        return httpx.Response(200, json=_verdict_body())

    clock = FakeClock()
    c = _classifier(handler, clock=clock, cooldown=60)

    await c.classify("one")
    state["primary_up"] = True
    clock.t = 61.0
    await c.classify("two")
    # primary answered, so it must be off the bench immediately
    assert c._available() == [PRIMARY, SECONDARY]


async def test_all_backends_down_raises_rather_than_failing_open():
    def handler(request):
        raise httpx.ConnectError("refused")

    with pytest.raises(ClassifierUnavailable):
        await _classifier(handler).classify("anything")


async def test_malformed_json_is_reviewed_not_deleted():
    """Constrained decoding makes this unlikely, but a parse error must never delete a message."""

    def handler(request):
        return httpx.Response(200, json={"message": {"content": "not json at all"}})

    v = await _classifier(handler).classify("hello")

    assert v.needs_review
    assert not v.flagged  # crucially: does NOT trigger a delete


async def test_http_500_fails_over():
    def handler(request):
        if PRIMARY in str(request.url):
            return httpx.Response(500)
        return httpx.Response(200, json=_verdict_body(action="allow", category="none"))

    v = await _classifier(handler).classify("can anyone cover saturday")
    assert not v.flagged


async def test_sends_schema_and_zero_temperature():
    seen = {}

    def handler(request):
        seen.update(json.loads(request.content))
        return httpx.Response(200, json=_verdict_body())

    await _classifier(handler).classify("hi")

    assert seen["format"]["properties"]["action"]["enum"] == ["allow", "flag"]
    assert seen["options"]["temperature"] == 0
    assert seen["stream"] is False
