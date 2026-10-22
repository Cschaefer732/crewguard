# Deploy

Run the bot as a user systemd service next to Ollama, so the classifier call stays on localhost.

## Prerequisites

1. Create an application at <https://discord.com/developers/applications>. Add a bot.
2. Enable the privileged intents the process needs:
   - **Message Content Intent** — without it `message.content` is empty and the classifier sees nothing.
   - **Server Members Intent**.
3. Create an empty Discord server, or use one you administer.
4. Copy the bot token, application id, and server (guild) id into `.env`. Do not commit that file.

## Install

```bash
git clone <your-fork-url> ~/crewguard
cd ~/crewguard
python3 -m venv .venv && .venv/bin/pip install -e .
cp .env.example .env   # token, application id, guild id
```

Ollama must be serving `qwen3:8b` on `127.0.0.1:11434` before you rely on the classifier. Pull the leadership model too if you want manager guidance: `ollama pull qwen3-coder:30b`.

## Provision the server

```bash
.venv/bin/crewguard initdb
.venv/bin/crewguard invite        # open the printed URL and add the bot
.venv/bin/crewguard check         # each backend classifies a known-hostile message
.venv/bin/crewguard setup-guild   # roles, channels, permissions, AutoMod
.venv/bin/crewguard post-welcome  # sample docs + the I Agree button
```

Then, in Discord, drag the bot's role above all 9 roles. Server Admin is the highest of those nine and must still sit below the bot. A bot can only manage roles below its own, and new roles are created at the bottom. If you skip the drag, permission overwrites do not apply.

## Run it

```bash
mkdir -p ~/.config/systemd/user
cp deploy/crewguard.service deploy/crewguard-purge.service deploy/crewguard-purge.timer \
  ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now crewguard.service
systemctl --user enable --now crewguard-purge.timer
```

Enable lingering if the user service should survive logout: `sudo loginctl enable-linger "$USER"`.

## Operate

```bash
systemctl --user status crewguard
journalctl --user -u crewguard -f
systemctl --user restart crewguard
```

The timer runs `crewguard purge` daily and deletes stored message content older than `RETENTION_DAYS`.

## Checks that fail when something is wrong

- `crewguard check` prints `WRONG` when a backend answers but does not flag the known-hostile sentence, and `DOWN` when it is unreachable.
- Post `nobody here can stand you` in `#general`. It should be deleted, DM'd, and logged to `#message-removal-log`.
- A profanity hit should be blocked by AutoMod and still produce a DM and a removal-log line (`on_automod_action`). If the DM or the log is missing, the intent is off.
- Stop Ollama. The bot should stay up and post a classifier-offline warning to `#mod-log`. Start Ollama again afterward.
