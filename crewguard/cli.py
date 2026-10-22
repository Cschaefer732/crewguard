import click
from sqlmodel import Session

from crewguard.config import load_settings
from crewguard.db import init_db, make_engine
from crewguard.textutil import chunk_message


@click.group()
@click.option("--db", default=None, help="SQLite path (defaults to DB_PATH in .env)")
@click.pass_context
def main(ctx, db):
    settings = load_settings()
    ctx.obj = {"settings": settings, "engine": make_engine(db or settings.db_path)}


@main.command()
@click.pass_context
def initdb(ctx):
    """Create the tables."""
    init_db(ctx.obj["engine"])
    click.echo("initdb done")


@main.command()
@click.pass_context
def invite(ctx):
    """Print the invite URL with exactly the permissions the bot needs."""
    from crewguard.bot import invite_url

    settings = ctx.obj["settings"]
    if not settings.app_id:
        raise click.ClickException("APP_ID is not set in .env")
    click.echo(invite_url(settings.app_id))


@main.command("setup-guild")
@click.pass_context
def setup_guild(ctx):
    """Create roles, channels, permissions and AutoMod rules. Safe to re-run."""
    from crewguard.bot import make_bot
    from crewguard.provisioning import provision

    settings = ctx.obj["settings"]
    engine = ctx.obj["engine"]
    init_db(engine)
    bot = make_bot(engine, settings)

    @bot.event
    async def on_ready():
        try:
            guild = bot.get_guild(settings.guild_id)
            if guild is None:
                click.echo(f"bot is not in guild {settings.guild_id}", err=True)
                return
            with Session(engine) as s:
                result = await provision(guild, s)
            click.echo(f"provisioned {len(result.stored)} roles/channels")
            if result.roles_repositioned:
                click.echo("role order set: Verified lowest, Server Admin highest")
            else:
                click.echo(
                    "\nACTION NEEDED: the bot's role is too low to order the crew roles.\n"
                    "In Discord: Server Settings -> Roles -> drag the bot's role ABOVE all\n"
                    "9 roles (Server Admin is highest and must also sit below the bot),\n"
                    "then re-run setup-guild. Until then role order (and any overwrite\n"
                    "the bot can't reach) is wrong."
                )
        finally:
            await bot.close()

    bot.run(settings.discord_token)


@main.command()
@click.pass_context
def check(ctx):
    """Ask each classifier backend for a verdict. Use this to prove the smart tier is alive."""
    import asyncio

    from crewguard.moderation.classifier import Classifier

    settings = ctx.obj["settings"]

    async def _run():
        for backend in settings.classifier_backends:
            c = Classifier((backend,), settings.classifier_model, connect_timeout=3.0)
            try:
                v = await c.classify("nobody here can stand you")
                ok = "OK " if v.flagged else "WRONG (should flag)"
                click.echo(f"{ok} {backend} -> {v.action}/{v.category}")
            except Exception as e:  # noqa: BLE001 — any backend failure is DOWN, not a crash
                click.echo(f"DOWN {backend} -> {e}")
            finally:
                await c.aclose()

    asyncio.run(_run())


@main.command("post-welcome")
@click.pass_context
def post_welcome(ctx):
    """Post the sample docs into #welcome-and-rules. Re-runnable: the bot's previous posts
    there are deleted first. House rules are pinned with the I Agree button."""
    from pathlib import Path

    from crewguard import config_store as cfg
    from crewguard.bot import make_bot

    settings = ctx.obj["settings"]
    engine = ctx.obj["engine"]
    init_db(engine)
    bot = make_bot(engine, settings)
    kb = Path(__file__).parent.parent / "docs" / "kb"
    rules = kb / "rules.md"
    docs = sorted(p for p in kb.glob("*.md") if p.name != "rules.md")

    @bot.event
    async def on_ready():
        try:
            with Session(engine) as s:
                channel_id = cfg.get_id(s, cfg.channel_key("welcome-and-rules"))
            channel = bot.get_channel(channel_id) if channel_id else None
            if channel is None:
                click.echo("welcome-and-rules channel not found — run setup-guild first", err=True)
                return
            if not rules.is_file():
                click.echo("docs/kb/rules.md is missing", err=True)
                return
            async for old in channel.history(limit=50):
                if old.author.id == bot.user.id:
                    await old.delete()
            for doc in docs:
                for chunk in chunk_message(doc.read_text()):
                    await channel.send(chunk)
            # The house rules are the consent text. Pin them with the button.
            from crewguard.consent_ui import consent_view

            gate = await channel.send(rules.read_text().strip(), view=consent_view())
            await gate.pin()
            click.echo(f"posted {len(docs)} docs + the rules gate")
        finally:
            await bot.close()

    bot.run(settings.discord_token)


@main.command("post-governance")
@click.pass_context
def post_governance(ctx):
    """Post/refresh CrewGuard's constitution as a pinned message in #mod-log, for the GM to read.
    Re-runnable: the previous pinned constitution is replaced, removal logs are untouched."""
    from pathlib import Path

    import discord

    from crewguard import config_store as cfg
    from crewguard.bot import make_bot

    settings = ctx.obj["settings"]
    engine = ctx.obj["engine"]
    init_db(engine)
    bot = make_bot(engine, settings)
    raw = (
        (Path(__file__).parent.parent / "docs" / "governance" / "constitution.md")
        .read_text()
        .strip()
    )
    lines = raw.splitlines()
    title = lines[0].lstrip("# ").strip() if lines and lines[0].startswith("#") else "Constitution"
    body = "\n".join(lines[1:]).strip()
    marker = "Constitution"

    @bot.event
    async def on_ready():
        try:
            with Session(engine) as s:
                channel_id = cfg.get_id(s, cfg.channel_key("mod-log"))
            channel = bot.get_channel(channel_id) if channel_id else None
            if channel is None:
                click.echo("mod-log channel not found — run setup-guild first", err=True)
                return
            # Replace only the prior constitution pin — never touch the removal logs around it.
            for pin in await channel.pins():
                if (
                    pin.author.id == bot.user.id
                    and pin.embeds
                    and marker in (pin.embeds[0].title or "")
                ):
                    await pin.unpin()
                    await pin.delete()
            embed = discord.Embed(
                title=f"📜 {title}",
                description=body
                + "\n\n*Read-only. For the current standing rules, DM CrewGuard `policy list`.*",
                colour=discord.Colour.blurple(),
            )
            msg = await channel.send(embed=embed)
            await msg.pin()
            click.echo("posted the constitution to #mod-log")
        finally:
            await bot.close()

    bot.run(settings.discord_token)


@main.command()
@click.pass_context
def purge(ctx):
    """Delete stored message content past the retention window (RETENTION_DAYS, default 30).
    Idempotent — safe to run on a timer."""
    from crewguard.retention import purge as run_purge

    settings = ctx.obj["settings"]
    engine = ctx.obj["engine"]
    init_db(engine)
    with Session(engine) as s:
        counts = run_purge(s, settings.retention_days)
    total = sum(counts.values())
    click.echo(f"purged {total} rows older than {settings.retention_days}d: {counts}")


@main.command()
@click.argument("user_id", type=int)
@click.pass_context
def forget(ctx, user_id):
    """Delete all stored data for one user (a data-deletion request)."""
    from crewguard.retention import forget_user

    engine = ctx.obj["engine"]
    init_db(engine)
    with Session(engine) as s:
        counts = forget_user(s, user_id)
    click.echo(f"deleted data for {user_id}: {counts}")


@main.command("kb-dump")
@click.pass_context
def kb_dump(ctx):
    """Print unanswered questions + leadership notes — the raw material for new KB docs."""
    from sqlmodel import select

    from crewguard.models import LeadershipNote, UnansweredQuestion

    init_db(ctx.obj["engine"])
    with Session(ctx.obj["engine"]) as s:
        questions = s.exec(select(UnansweredQuestion)).all()
        notes = s.exec(select(LeadershipNote)).all()

    click.echo(f"=== unanswered questions ({len(questions)}) ===")
    for q in questions:
        click.echo(f"[{q.ts:%m-%d}] ({q.surface}) {q.question}")
    click.echo(f"\n=== leadership notes ({len(notes)}) ===")
    for n in notes:
        click.echo(f"[{n.ts:%m-%d}] {n.author_name}: {n.content}")


@main.command()
def run():
    """Start the bot."""
    from crewguard.bot import run_bot

    run_bot()
