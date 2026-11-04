// One-shot script that publishes the slash commands in commands/ to
// Discord. Run via `npm run register` after editing commands.
//
// Guild scope (DISCORD_GUILD_ID set): commands appear instantly,
// scoped to that one server. Use during dev.
// Global scope (DISCORD_GUILD_ID empty): commands appear in every
// server the bot is in, but Discord caches them for ~1h.

import "dotenv/config";
import { REST, Routes } from "discord.js";
import { readdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const HERE = dirname(fileURLToPath(import.meta.url));

const TOKEN = process.env.DISCORD_TOKEN;
const CLIENT_ID = process.env.DISCORD_CLIENT_ID;
const GUILD_ID = process.env.DISCORD_GUILD_ID;

if (!TOKEN || !CLIENT_ID) {
  console.error("DISCORD_TOKEN and DISCORD_CLIENT_ID required.");
  process.exit(1);
}

const commands = [];
const commandsDir = join(HERE, "commands");
for (const file of await readdir(commandsDir)) {
  if (!file.endsWith(".js")) continue;
  const mod = await import(`./commands/${file}`);
  if (mod.data) commands.push(mod.data.toJSON());
}

const rest = new REST().setToken(TOKEN);
const route = GUILD_ID
  ? Routes.applicationGuildCommands(CLIENT_ID, GUILD_ID)
  : Routes.applicationCommands(CLIENT_ID);

console.log(
  `registering ${commands.length} command(s) ` +
    (GUILD_ID ? `to guild ${GUILD_ID}` : "globally")
);

const data = await rest.put(route, { body: commands });
console.log(`registered ${data.length} command(s).`);
