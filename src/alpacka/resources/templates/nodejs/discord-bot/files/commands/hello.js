import { SlashCommandBuilder } from "discord.js";

export const data = new SlashCommandBuilder()
  .setName("hello")
  .setDescription("Say hello.")
  .addStringOption((opt) =>
    opt.setName("name").setDescription("Who to greet").setRequired(false)
  );

export async function execute(interaction) {
  const name = interaction.options.getString("name") ?? interaction.user.username;
  await interaction.reply(`hello, ${name}!`);
}
