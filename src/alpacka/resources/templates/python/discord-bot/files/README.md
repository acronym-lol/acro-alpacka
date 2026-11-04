# {{PROJECT_NAME}}

discord.py bot with cogs-based slash commands.

## Setup

1. Create a Discord application + bot at <https://discord.com/developers/applications>:
   - Copy the bot **Token** → `DISCORD_TOKEN`
   - Invite the bot to a server with the `applications.commands` scope.

2. (Optional, for dev): grab a server's **Guild ID** (Discord → server icon
   → right-click → Copy Server ID; needs Developer Mode enabled in
   Discord settings) → `DISCORD_GUILD_ID`. Guild commands sync instantly;
   global commands take ~1h to propagate.

3. Install + run:

   ```
   python -m venv .venv
   .venv/Scripts/activate         # Windows
   source .venv/bin/activate      # macOS / Linux
   pip install -e .
   cp .env.example .env           # fill in token, guild ID
   ```

## Run

Click Launch in ALPACKA, or:

```
python bot.py
```

Slash commands sync on first connect (logged in the terminal). In your
Discord server, type `/` and you'll see `/ping` and `/hello`.

## Add a command

1. Drop a new file in `cogs/`, defining a `Cog` subclass with
   `@app_commands.command` methods. See `cogs/hello.py` for the shape.
2. Restart the bot — `bot.py` auto-loads cogs and re-syncs commands.

## Customizing

- For message-content access (responding to non-slash messages),
  uncomment `intents.message_content = True` in `bot.py` AND enable
  the privileged intent in your bot's Developer Portal page.
- For voice / music: `pip install discord.py[voice]` + a system ffmpeg
  install.
- For database persistence: drop in `aiosqlite` or pair with the
  `docker-compose/postgres` template via `asyncpg`.
