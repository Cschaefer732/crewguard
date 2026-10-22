import logging
from pathlib import Path

import discord
from discord.ext import commands

from crewguard.assistant.answerer import Answerer
from crewguard.assistant.kb import load_kb, render_kb
from crewguard.config import Settings, load_settings
from crewguard.db import init_db, make_engine
from crewguard.governance import load_constitution
from crewguard.moderation.classifier import Classifier
from crewguard.ollama import OllamaClient

log = logging.getLogger(__name__)

COGS = (
    "crewguard.cogs.moderation",
    "crewguard.cogs.assistant",
    "crewguard.cogs.admin",
    "crewguard.cogs.heartbeat",
    "crewguard.cogs.consent",
)

KB_DIR = Path(__file__).parent.parent / "docs" / "kb"


def make_intents() -> discord.Intents:
    intents = discord.Intents.default()
    # Privileged — must ALSO be enabled in the Developer Portal (Bot -> Privileged Gateway
    # Intents). Without it message.content is empty and the classifier sees nothing.
    intents.message_content = True
    intents.members = True
    # Required to receive on_automod_action. Without it, AutoMod blocks are invisible to us:
    # no DM to the author, nothing in #mod-log.
    intents.auto_moderation_execution = True
    return intents


class CrewGuardBot(commands.Bot):
    def __init__(self, engine, settings: Settings) -> None:
        super().__init__(command_prefix="!", intents=make_intents())
        self.engine = engine
        self.settings = settings
        self.classifier = Classifier(settings.classifier_backends, settings.classifier_model)
        # Answers are longer generations than verdicts — generous read timeout.
        self._answer_llm = OllamaClient(
            settings.classifier_backends, settings.classifier_model, read_timeout=120.0
        )
        kb = load_kb(KB_DIR)
        self.answerer = Answerer(self._answer_llm, render_kb(kb))
        self.kb_topics = tuple(t.name for t in kb)
        # Leadership guidance: bigger, always-on model, anchored by the constitution (the immutable
        # core). Guide-only — it explains how to manage CrewGuard; the deterministic `policy` DM commands
        # make the actual changes. Generous read timeout: the 30B is slower than the classifier.
        self._leadership_llm = OllamaClient(
            settings.leadership_backends, settings.leadership_model, read_timeout=180.0
        )
        self.leadership_answerer = Answerer(self._leadership_llm, load_constitution())

    async def setup_hook(self) -> None:
        from crewguard.consent_ui import ConsentButton
        from crewguard.feedback_ui import FeedbackButton

        self.add_dynamic_items(FeedbackButton, ConsentButton)
        for cog in COGS:
            await self.load_extension(cog)
        log.info(
            "classifier: %s via %s | kb topics: %s",
            self.settings.classifier_model,
            ", ".join(self.settings.classifier_backends),
            ", ".join(self.kb_topics) or "(none)",
        )

    async def close(self) -> None:
        await self.classifier.aclose()
        await self._answer_llm.aclose()
        await self._leadership_llm.aclose()
        await super().close()


def make_bot(engine, settings: Settings) -> CrewGuardBot:
    return CrewGuardBot(engine, settings)


def invite_url(app_id: int) -> str:
    from crewguard.provisioning import bot_permissions

    return discord.utils.oauth_url(app_id, permissions=bot_permissions())


def run_bot() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = load_settings()
    engine = make_engine(settings.db_path)
    init_db(engine)
    make_bot(engine, settings).run(settings.discord_token)
