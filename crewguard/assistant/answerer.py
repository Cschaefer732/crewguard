"""Q&A over the shared Ollama client. Pure: no Discord, no DB (see AGENTS.md)."""

import json
from dataclasses import dataclass

from crewguard.assistant.prompts import (
    ANSWER_SCHEMA,
    INTERJECT_SCHEMA,
    build_answer_messages,
    build_interject_messages,
)
from crewguard.ollama import OllamaClient


@dataclass(frozen=True)
class Answer:
    kind: str  # answer | not_in_kb | not_work_related
    topic: str
    answer: str


@dataclass(frozen=True)
class Interjection:
    speak: bool
    topic: str
    answer: str


SILENT = Interjection(speak=False, topic="", answer="")


class Answerer:
    def __init__(self, llm: OllamaClient, kb_text: str) -> None:
        self._llm = llm
        self._kb = kb_text

    async def answer(self, question: str, audience: str = "crew") -> Answer:
        raw = await self._llm.chat(
            build_answer_messages(self._kb, question, audience),
            schema=ANSWER_SCHEMA,
            num_predict=400,
            temperature=0.2,
        )
        try:
            d = json.loads(raw)
            return Answer(kind=d["kind"], topic=d.get("topic", ""), answer=d.get("answer", ""))
        except (json.JSONDecodeError, KeyError, TypeError):
            # Unintelligible model output on a DIRECT question -> honest refusal, never a guess.
            return Answer(kind="not_in_kb", topic="", answer="")

    async def consider_interjecting(self, message: str) -> Interjection:
        raw = await self._llm.chat(
            build_interject_messages(self._kb, message),
            schema=INTERJECT_SCHEMA,
            num_predict=300,
            temperature=0.0,
        )
        try:
            d = json.loads(raw)
            return Interjection(
                speak=bool(d["speak"]), topic=d.get("topic", ""), answer=d.get("answer", "")
            )
        except (json.JSONDecodeError, KeyError, TypeError):
            return SILENT  # unintelligible -> stay quiet; butting in wrongly is the worse failure
