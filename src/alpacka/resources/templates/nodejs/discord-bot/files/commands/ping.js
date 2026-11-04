import { SlashCommandBuilder } from "discord.js";

export const data = new SlashCommandBuilder()
  .setName("ping")
  .setDescription("Pong + round-trip latency.");

export async function execute(interaction) {
  const sent = await interaction.reply({ content: "Pinging...", fetchReply: true });
  const latency = sent.createdTimestamp - interaction.createdTimestamp;
  await interaction.editReply(
    `pong — ${latency}ms round trip, ${Math.round(interaction.client.ws.ping)}ms websocket.`
  );
}
