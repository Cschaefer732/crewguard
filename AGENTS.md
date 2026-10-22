# crewguard

Discord bot that provisions a workplace server for a quick-service restaurant crew and moderates its chat.

## Commands

- Test: `.venv/bin/pytest`
- Live classifier eval (needs Ollama): `.venv/bin/pytest -m eval -v`
- Provision: `.venv/bin/crewguard setup-guild`
- Run: `.venv/bin/crewguard run`

## Invariant

`crewguard/moderation/classifier.py`, `crewguard/moderation/prompt.py`, and `crewguard/guild_spec.py` stay free of Discord and database imports, along with the Ollama client, text chunking, and the assistant modules listed in `tests/test_purity.py`. That is what lets the decision logic test without a gateway.

## Failure behavior

If every classifier backend is down, the bot stays up, lets messages through, and posts a rate-limited warning to `#mod-log`. Malformed verdict JSON is flagged for a person to review. It is not treated as allow.
