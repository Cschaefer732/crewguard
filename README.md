# crewguard

A Discord bot that stands up a workplace server for a quick-service restaurant crew and moderates the chat, including hostility that contains no banned words.

The sample setting in this repository is **Example Restaurant**. It is not affiliated with any real chain.

## The problem

A crew's previous group chat was shut down because abuse could not be moderated. A word list catches slurs. It does not catch "nobody here can stand you." This bot builds a server where every removal is logged, so a manager can see what was taken down and overrule it.

## What it does

- Provisions the server from a declarative spec: 9 roles, 9 channels, permission overwrites, and one Discord AutoMod keyword-preset rule (profanity, sexual content, and slurs).
- Classifies each message with a local model through Ollama. The default model is `qwen3:8b`. `CLASSIFIER_BACKENDS` is an ordered list; the first healthy backend wins.
- Removes a violating message, DMs the author their own text and the reason, and writes a row to `#message-removal-log`. Classifier removals include Good call / Too strict buttons. There are no strikes, timeouts, kicks, or bans.
- Lets a general manager or server admin add standing rules by DM (`policy add`, `policy list`, `policy remove`). A "too strict" review is fed back into the classifier as an allow-example.
- Answers questions from the markdown files in `docs/kb` when `CREW_QA_ENABLED=true`, and always sends a join greeting. Unanswered work questions are logged for a manager.
- Records an I Agree click, deletes stored message content after 30 days (`crewguard purge`), and can delete one person's rows (`crewguard forget USER_ID`).

## Architecture

1. Discord AutoMod blocks a keyword-preset hit before the message exists. The bot listens for `on_automod_action` so that block still gets a DM and a log line.
2. `on_message` sends other text to the local classifier. Both paths call the same `enforce()`: delete, DM, log.
3. If every backend is down, the bot stays up, lets the message through, and posts a rate-limited "classifier offline" warning to `#mod-log`. Malformed verdict JSON is held for a person to review. It is not treated as allow.
4. A watcher re-scans recent history on a timer. A pinned heartbeat in `#mod-log` shows whether the classifier answered a probe.
5. `crewguard/moderation/classifier.py`, `crewguard/moderation/prompt.py`, and `crewguard/guild_spec.py` do not import Discord or the database, so that logic tests without a gateway.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
cp .env.example .env   # Discord token, application id, guild id
```

Ollama needs to be serving `CLASSIFIER_MODEL` (default `qwen3:8b`). Point `CLASSIFIER_BACKENDS` at it. `http://127.0.0.1:11434` is the default when Ollama is on the same machine.

In the Discord Developer Portal, enable Message Content and Server Members. The bot also requests the auto-moderation execution intent so AutoMod blocks are visible to it.

```bash
.venv/bin/crewguard initdb
.venv/bin/crewguard invite
.venv/bin/crewguard check
.venv/bin/crewguard setup-guild
.venv/bin/crewguard post-welcome
.venv/bin/crewguard run
```

After `setup-guild`, drag the bot's role above the nine crew roles. Server Admin is the highest of the nine and must still sit below the bot. New roles are created at the bottom, and the bot can only manage roles below its own. Skip the drag and permission overwrites do not apply.

User systemd units are in `deploy/`. See `deploy/README-deploy.md`.

## Tests

```bash
.venv/bin/pytest -q          # hermetic; does not call Discord or Ollama
.venv/bin/pytest -m eval -v  # held-out classifier set; needs a live Ollama
```

The default run deselects the `eval` marker. `EVAL_BACKENDS` defaults to `http://127.0.0.1:11434`.

## Design decisions

**Gentle, auditable enforcement.** The bot deletes the message, tells the author why, and logs it. On the third removal in seven days, and on each later removal in that window, it posts a pattern warning in `#message-removal-log` and mentions the general manager. It still does not punish the member.

**Human escalation.** Managers overrule a classifier removal from the log. Standing rules are DM commands applied by code. The model does not edit the server. `docs/governance/constitution.md` is loaded as read-only text. The bot has no tool that writes that file. AutoMod removals are logged without the overrule buttons, because the keyword presets do not read that feedback.

**Local model.** Classification and Q&A call Ollama over HTTP. This code does not send message text to a hosted LLM API. Discord itself still sees the message, and you need a machine that can run the model. `qwen3:8b` is a hybrid-reasoning model; the client sends `think=false` so the verdict is not spent on a hidden chain of thought.

**Declarative provisioning.** Roles, channels, and overwrites live in `crewguard/guild_spec.py`. `setup-guild` is safe to re-run. Re-running replaces overwrites with the spec. That is intentional. The bot cannot place its own role above the roles it creates; a person drags it once.

**Retention and consent.** `RETENTION_DAYS` (default 30) bounds stored removal text, leadership notes, and unanswered questions. `crewguard purge` deletes older rows. The daily timer unit runs that command. Consent records survive the time purge. `crewguard forget USER_ID` deletes that person's content rows and their consent record. A new member sees `#welcome-and-rules` until they press I Agree. A manager then assigns a rank. The agree click is an acknowledgement of the house rules, not a contract.

## Sample docs

`docs/kb/` has three generic articles: house rules, a shift pocket checklist, and a data-privacy note. The Q&A assistant cites whatever markdown files are in that directory. `crewguard post-welcome` posts the checklist and the privacy note into `#welcome-and-rules` and pins the rules with the I Agree button. They are placeholders so the feature can be demonstrated. They are not a real employee handbook. There is no forum channel in the spec. `#resources` is a manager-posted text channel for links you add yourself.

## Limitations

- Crew Q&A, @mention answers, and unprompted interjections stay off unless `CREW_QA_ENABLED=true`. The join greeting and manager guidance stay on either way.
- This repository does not include a recorded live-eval score. The held-out set is in `tests/eval_messages.py`. It was not run as part of publishing this copy.
- AutoMod covers Discord's keyword presets only. Hostility in clean words depends on the local model and on the bot process being up.
- The classifier prompt is written for a restaurant crew chat: venting about the job is allowed, contempt aimed at a person is not. It is not a general-purpose moderator.
- There is no strike ladder and no appeal path beyond the removal-log buttons and a manager.

## Status

The unit suite covers provisioning, enforcement, consent, retention, the assistant's refusal paths, and the import boundary of the classifier modules. It does not talk to Discord or Ollama. Run `crewguard check` and the eval marker against your own Ollama before you rely on a live server.
