"""Cog with /hello — example slash command with a parameter."""
from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands


class Hello(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="hello", description="Say hello.")
    @app_commands.describe(name="Who to greet")
    async def hello(
        self, interaction: discord.Interaction, name: str | None = None
    ) -> None:
        target = name or interaction.user.display_name
        await interaction.response.send_message(f"hello, {target}!")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Hello(bot))
