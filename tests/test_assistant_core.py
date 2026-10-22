import json

import httpx
import pytest

from crewguard.assistant.answerer import Answerer
from crewguard.assistant.kb import load_kb, render_kb
from crewguard.assistant.prompts import looks_like_question
from crewguard.ollama import OllamaClient


def test_kb_loads_topics_with_titles(tmp_path):
    (tmp_path / "a.md").write_text("# Checking your hours\n\nUse the schedule app.")
    (tmp_path / "b.md").write_text("no heading here")
    (tmp_path / "empty.md").write_text("")

    topics = load_kb(tmp_path)

    assert [t.name for t in topics] == ["Checking your hours", "b"]
    assert "the schedule app" in render_kb(topics)


def test_empty_kb_is_valid(tmp_path):
    topics = load_kb(tmp_path)
    assert topics == ()
    assert "empty" in render_kb(topics)


def test_real_kb_seeds_load():
    from pathlib import Path

    topics = load_kb(Path(__file__).parent.parent / "docs" / "kb")
    names = {t.name for t in topics}
    assert names == {"House rules", "Shift pocket checklist", "Data and privacy"}


@pytest.mark.parametrize(
    "text,expected",
    [
        ("how do i see my hours", True),
        ("where is the handbook?", True),
        ("can i request time off in the app", True),
        ("im so tired today", False),
        ("great shift everyone", False),
        ("that was brutal", False),
    ],
)
def test_question_prefilter(text, expected):
    assert looks_like_question(text) is expected


def _answerer(payload):
    def handler(request):
        return httpx.Response(200, json={"message": {"content": json.dumps(payload)}})

    llm = OllamaClient(
        ("http://x:11434",), "m", client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    return Answerer(llm, kb_text="# Checking your hours\nUse the schedule app.")


async def test_answer_parses():
    a = await _answerer(
        {"kind": "answer", "topic": "Checking your hours", "answer": "Open the schedule app."}
    ).answer("how do i see my hours")
    assert a.kind == "answer"
    assert a.topic == "Checking your hours"


async def test_malformed_answer_refuses_not_guesses():
    def handler(request):
        return httpx.Response(200, json={"message": {"content": "garbage"}})

    llm = OllamaClient(
        ("http://x:11434",), "m", client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    a = await Answerer(llm, "kb").answer("how much do i get paid?")
    assert a.kind == "not_in_kb"


async def test_malformed_interjection_stays_silent():
    def handler(request):
        return httpx.Response(200, json={"message": {"content": "garbage"}})

    llm = OllamaClient(
        ("http://x:11434",), "m", client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    i = await Answerer(llm, "kb").consider_interjecting("how does the schedule work?")
    assert i.speak is False
