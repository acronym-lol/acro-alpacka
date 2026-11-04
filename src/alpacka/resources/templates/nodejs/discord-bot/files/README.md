# {{PROJECT_NAME}}

discord.js bot with slash commands.

## Setup

1. Create a Discord application + bot at <https://discord.com/developers/applications>:
   - Copy the bot **Token** → `DISCORD_TOKEN`
   - Copy the application **Client ID** → `DISCORD_CLIENT_ID`
   - Invite the bot to a server with the `applications.commands` scope.

2. (Optional, for dev): grab a server's **Guild ID** (Discord → server icon
   → right-click → Copy Server ID; needs Developer Mode enabled in
   Discord settings) → `DISCORD_GUILD_ID`. Guild commands appear
   instantly; global commands take ~1h to propagate.

3. Install dependencies and register the commands:

   ```
   npm install
   cp .env.example .env       # fill in token, client ID, guild ID
   npm run register           # publishes commands to Discord
   ```

## Run

Click Launch in ALPACKA, or:

```
npm run dev      # auto-reloads on file change
npm start        # one-shot
```

In the Discord server, type `/` and you'll see `/ping` and `/hello`.

## Add a command

1. Drop a new file in `commands/`, exporting `data` (a `SlashCommandBuilder`)
   and `execute` (an async function). See `commands/hello.js` for the shape.
2. Run `npm run register` again to publish the new command list.
3. Restart the bot (Stop + Launch in ALPACKA).

## Customizing

- For message-content access (responding to non-slash messages), add the
  `MessageContent` intent in `bot.js` AND enable it in your bot's
  Developer Portal page (it's a privileged intent — Discord requires
  approval for bots in 100+ servers).
- For voice / music: add `@discordjs/voice` + `ffmpeg-static` and use
  `joinVoiceChannel`.
- For database persistence: drop in better-sqlite3 or pair with the
  `docker-compose/postgres` template via `pg`.
