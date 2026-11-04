"""Discord bot entry — auto-loads cogs from ./cogs/, syncs slash commands.

Run via `python bot.py`. Reads DISCORD_TOKEN and (optionally)
DISCORD_GUILD_ID from env (alpacka.yml's variables: section feeds them
in at launch time; .env also works for local secrets).
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

import discord
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("bot")

TOKEN = os.environ.get("DISCORD_TOKEN")
GUILD_ID = os.environ.get("DISCORD_GUILD_ID") or None

if not TOKEN:
    sys.exit("DISCORD_TOKEN missing — set it in .env or alpacka.yml.")

intents = discord.Intents.default()
# Add intents.message_content = True if you need to read non-slash messages.
# It's a privileged intent — also enable it in the Developer Portal.

bot = commands.Bot(command_prefix="!", intents=intents)


@bot.event
async def on_ready() -> None:
    log.info("logged in as %s (id=%s)", bot.user, bot.user.id)
    # Sync slash commands. Guild-scoped sync is instant; global sync
    # takes ~1h to propagate. Sync once after editing commands.
    if GUILD_ID:
        guild = discord.Object(id=int(GUILD_ID))
        bot.tree.copy_global_to(guild=guild)
        synced = await bot.tree.sync(guild=guild)
        log.info("synced %d command(s) to guild %s", len(synced), GUILD_ID)
    else:
        synced = await bot.tree.sync()
        log.info("synced %d command(s) globally", len(synced))


async def load_cogs() -> None:
    """Auto-load every .py module under ./cogs/ as a discord.py extension."""
    cogs_dir = Path(__file__).parent / "cogs"
    for path in cogs_dir.glob("*.py"):
        if path.name.startswith("_"):
            continue
        ext = f"cogs.{path.stem}"
        try:
            await bot.load_extension(ext)
            log.info("loaded %s", ext)
        except Exception as exc:  # noqa: BLE001
            log.error("failed to load %s: %s", ext, exc)


async def main() -> None:
    async with bot:
        await load_cogs()
        await bot.start(TOKEN)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
