"""Ordered-backend Ollama chat client. Pure of Discord and DB.

Backends are tried in order and the first healthy one wins. A backend that fails is benched for a
cooldown so a box that is off is not re-dialed on every request.
"""

import time

import httpx


class AllBackendsDown(RuntimeError):
    """Every backend is unreachable. Callers must degrade loudly, never fail open silently."""


class OllamaClient:
    def __init__(
        self,
        backends: tuple[str, ...],
        model: str,
        *,
        connect_timeout: float = 0.3,
        read_timeout: float = 60.0,
        cooldown: float = 60.0,
        client: httpx.AsyncClient | None = None,
        clock=time.monotonic,
    ) -> None:
        self._backends = backends
        self._model = model
        self._cooldown = cooldown
        self._clock = clock
        self._benched: dict[str, float] = {}
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(read_timeout, connect=connect_timeout)
        )

    def _available(self) -> list[str]:
        now = self._clock()
        return [b for b in self._backends if self._benched.get(b, 0.0) <= now]

    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        schema: dict,
        num_predict: int = 100,
        temperature: float = 0.0,
    ) -> str:
        """Returns the raw content string of the model reply, schema-constrained."""
        candidates = self._available() or list(self._backends)  # all benched -> try anyway
        last_error: Exception | None = None

        for backend in candidates:
            try:
                raw = await self._ask(backend, messages, schema, num_predict, temperature)
            except (httpx.HTTPError, httpx.TimeoutException) as e:
                self._benched[backend] = self._clock() + self._cooldown
                last_error = e
                continue
            self._benched.pop(backend, None)
            return raw

        raise AllBackendsDown(f"all {len(self._backends)} backend(s) unreachable: {last_error}")

    async def _ask(
        self,
        backend: str,
        messages: list[dict[str, str]],
        schema: dict,
        num_predict: int,
        temperature: float,
    ) -> str:
        resp = await self._client.post(
            f"{backend}/api/chat",
            json={
                "model": self._model,
                "messages": messages,
                "format": schema,
                "stream": False,
                # Hybrid-reasoning models (e.g. qwen3:8b) otherwise spend the whole num_predict
                # budget on thinking tokens and return an EMPTY content string. Ignored by
                # non-thinking models, so it is free insurance.
                "think": False,
                "options": {"temperature": temperature, "num_predict": num_predict},
            },
        )
        resp.raise_for_status()
        return resp.json()["message"]["content"]

    async def aclose(self) -> None:
        await self._client.aclose()
