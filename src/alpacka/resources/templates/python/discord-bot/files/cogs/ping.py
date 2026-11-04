"""Cog with /ping — useful sanity-check command.

Cogs are discord.py's way of grouping related commands. Each file in
./cogs/ is auto-loaded by bot.py via `load_extension`.
"""
from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands


class Ping(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="ping", description="Pong + websocket latency.")
    async def ping(self, interaction: discord.Interaction) -> None:
        latency_ms = round(self.bot.latency * 1000)
        await interaction.response.send_message(
            f"pong — {latency_ms}ms websocket latency.", ephemeral=False
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Ping(bot))
