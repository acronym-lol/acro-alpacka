// Discord bot entry — connects, listens for slash commands, dispatches
// to handlers in commands/. Run register.js once after editing commands
// to publish them to Discord.

import "dotenv/config";
import { Client, Collection, Events, GatewayIntentBits } from "discord.js";
import { readdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const HERE = dirname(fileURLToPath(import.meta.url));

const TOKEN = process.env.DISCORD_TOKEN;
if (!TOKEN) {
  console.error("DISCORD_TOKEN missing — set it in .env or alpacka.yml.");
  process.exit(1);
}

const client = new Client({ intents: [GatewayIntentBits.Guilds] });

// Auto-load every command module from ./commands/
client.commands = new Collection();
const commandsDir = join(HERE, "commands");
for (const file of await readdir(commandsDir)) {
  if (!file.endsWith(".js")) continue;
  const mod = await import(`./commands/${file}`);
  if (mod.data && mod.execute) {
    client.commands.set(mod.data.name, mod);
  }
}

client.once(Events.ClientReady, (c) => {
  console.log(`logged in as ${c.user.tag} — ${client.commands.size} command(s) loaded`);
});

client.on(Events.InteractionCreate, async (interaction) => {
  if (!interaction.isChatInputCommand()) return;
  const command = client.commands.get(interaction.commandName);
  if (!command) return;
  try {
    await command.execute(interaction);
  } catch (err) {
    console.error(`error in /${interaction.commandName}:`, err);
    const reply = { content: "Command failed. Check the logs.", ephemeral: true };
    if (interaction.replied || interaction.deferred) {
      await interaction.followUp(reply);
    } else {
      await interaction.reply(reply);
    }
  }
});

// Graceful shutdown so Stop on the project card disconnects cleanly.
function shutdown(reason) {
  console.log(`\nshutting down (${reason})...`);
  client.destroy();
  process.exit(0);
}
process.on("SIGINT", () => shutdown("SIGINT"));
process.on("SIGTERM", () => shutdown("SIGTERM"));

client.login(TOKEN);
