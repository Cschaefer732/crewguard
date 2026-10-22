"""Live classifier eval. Needs a reachable Ollama backend.

    pytest -m eval -v

Deselected by default so the unit suite stays hermetic.
"""

import os
import time

import pytest

from crewguard.config import DEFAULT_MODEL
from crewguard.moderation.classifier import Classifier

from .eval_messages import (
    ALL_ALLOW,
    EXPLICIT,
    GOSSIP_OFFTOPIC,
    HOSTILE_CLEAN,
    SPANISH_HOSTILE,
)

BACKENDS = tuple(
    b.strip()
    for b in os.environ.get("EVAL_BACKENDS", "http://127.0.0.1:11434").split(",")
    if b.strip()
)
MODEL = os.environ.get("EVAL_MODEL", DEFAULT_MODEL)

pytestmark = pytest.mark.eval


@pytest.fixture
async def classifier():
    # Function-scoped: httpx.AsyncClient binds to the running loop, and pytest-asyncio
    # gives each test a fresh one. A module-scoped client dies with the first loop.
    c = Classifier(BACKENDS, MODEL, connect_timeout=5.0, read_timeout=60.0)
    yield c
    await c.aclose()


@pytest.mark.parametrize("msg,category", HOSTILE_CLEAN)
async def test_catches_hostility_with_zero_banned_words(classifier, msg, category):
    """THE test. If this fails, the project has failed at its one job."""
    v = await classifier.classify(msg)
    assert v.flagged, f"MISSED hostile message: {msg!r} -> {v}"


@pytest.mark.parametrize("msg,category", SPANISH_HOSTILE)
async def test_catches_spanish_hostility(classifier, msg, category):
    """J-1 crew accommodation cuts both ways: their chat is moderated too."""
    v = await classifier.classify(msg)
    assert v.flagged, f"MISSED Spanish hostile message: {msg!r} -> {v}"


@pytest.mark.parametrize("msg,category", GOSSIP_OFFTOPIC)
async def test_catches_gossip_and_off_topic(classifier, msg, category):
    v = await classifier.classify(msg)
    assert v.flagged, f"MISSED {category}: {msg!r} -> {v}"


@pytest.mark.parametrize("msg,category", EXPLICIT)
async def test_catches_explicit_abuse(classifier, msg, category):
    v = await classifier.classify(msg)
    assert v.flagged, f"MISSED: {msg!r} -> {v}"


@pytest.mark.parametrize("msg,_", ALL_ALLOW)
async def test_does_not_flag_normal_crew_talk(classifier, msg, _):
    """A bot that deletes 'this rush is killing me' gets turned off, and then nothing is moderated."""
    v = await classifier.classify(msg)
    assert not v.flagged, f"FALSE POSITIVE on normal crew talk: {msg!r} -> {v}"


async def test_latency_under_two_seconds(classifier):
    times = []
    for msg, _ in HOSTILE_CLEAN[:5]:
        t0 = time.monotonic()
        await classifier.classify(msg)
        times.append(time.monotonic() - t0)
    worst = max(times)
    assert worst < 2.0, f"p-max {worst:.2f}s exceeds the 2s budget: {[f'{t:.2f}' for t in times]}"
