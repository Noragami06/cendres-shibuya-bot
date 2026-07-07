import discord
from discord.ext import commands
from discord import app_commands
import re

STAFF_ROLE_ID = 1521228799302307967


def has_staff_role(member: discord.Member) -> bool:
    return any(role.id == STAFF_ROLE_ID for role in member.roles)


class Salon(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="salon", description="Crée un ou plusieurs salons dans une catégorie donnée")
    async def salon(self, interaction: discord.Interaction):
        if not has_staff_role(interaction.user):
            await interaction.response.send_message("Tu n'as pas la permission d'utiliser cette commande.", ephemeral=True)
            return

        embed = discord.Embed(
            title="📁 Création de salons — Étape 1/2",
            description="Envoie dans ce salon l'**ID de la catégorie** dans laquelle créer les salons.\n\n⏱️ Tu as 2 minutes pour répondre.",
            color=discord.Color.blurple(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

        def check_author(m: discord.Message):
            return m.author.id == interaction.user.id and m.channel.id == interaction.channel.id

        try:
            category_msg = await self.bot.wait_for("message", check=check_author, timeout=120)
        except TimeoutError:
            await interaction.followup.send("⏱️ Temps écoulé, commande annulée.", ephemeral=True)
            return

        category_id_text = category_msg.content.strip()
        try:
            await category_msg.delete()
        except discord.Forbidden:
            pass

        if not category_id_text.isdigit():
            await interaction.followup.send("❌ ID de catégorie invalide (doit être un nombre). Relance la commande.", ephemeral=True)
            return

        category = interaction.guild.get_channel(int(category_id_text))
        if category is None or not isinstance(category, discord.CategoryChannel):
            await interaction.followup.send("❌ Aucune catégorie trouvée avec cet ID. Relance la commande.", ephemeral=True)
            return

        embed2 = discord.Embed(
            title="📁 Création de salons — Étape 2/2",
            description=(
                f"Catégorie sélectionnée : **{category.name}**\n\n"
                "Envoie maintenant la liste des salons à créer, un par ligne, au format :\n"
                "```\n1 ❘・quartier-historique\n2 ❘・pont-sumida\n```\n"
                "⏱️ Tu as 3 minutes."
            ),
            color=discord.Color.blurple(),
        )
        await interaction.followup.send(embed=embed2, ephemeral=True)

        try:
            list_msg = await self.bot.wait_for("message", check=check_author, timeout=180)
        except TimeoutError:
            await interaction.followup.send("⏱️ Temps écoulé, commande annulée.", ephemeral=True)
            return

        raw_lines = list_msg.content.strip().split("\n")
        try:
            await list_msg.delete()
        except discord.Forbidden:
            pass

        parsed = []
        for line in raw_lines:
            match = re.match(r"^\s*(\d+)\s+(.+)$", line)
            if match:
                number = int(match.group(1))
                name = match.group(2).strip()
                parsed.append((number, name))

        if not parsed:
            await interaction.followup.send("❌ Aucun salon valide détecté. Format attendu : `1 ❘・nom-du-salon`", ephemeral=True)
            return

        parsed.sort(key=lambda x: x[0])

        building_embed = discord.Embed(
            title="⏳ Construction en cours",
            description=f"Création de **{len(parsed)}** salon(s) dans la catégorie **{category.name}**...",
            color=discord.Color.orange(),
        )
        await interaction.followup.send(embed=building_embed, ephemeral=True)

        created = []
        for number, name in parsed:
            channel = await category.create_text_channel(name=name, sync_permissions=True)
            created.append(channel.mention)

        result_embed = discord.Embed(
            title="✅ Salons créés avec succès",
            description="\n".join(created),
            color=discord.Color.green(),
        )
        result_embed.set_footer(text=f"Catégorie : {category.name} • {len(created)} salon(s) créé(s)")
        await interaction.channel.send(embed=result_embed)


async def setup(bot):
    await bot.add_cog(Salon(bot))