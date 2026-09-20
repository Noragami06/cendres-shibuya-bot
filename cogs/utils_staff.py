import asyncio

import discord
from discord import app_commands
from discord.ext import commands

OWNER_ID = 396615332346855428  # owner du bot (réutilisée ailleurs dans le projet)
WAIT_TIMEOUT = 180             # secondes d'attente de la réponse de l'owner
CATS_PER_PAGE = 15            # catégories listées par message
MSG_LIMIT = 1900              # marge sous la limite Discord de 2000 caractères


def _chunk_lines(lines, limit=MSG_LIMIT):
    """Regroupe des lignes en messages <= limit caractères, sans jamais couper une ligne."""
    chunks, cur = [], ""
    for line in lines:
        add = line if not cur else "\n" + line
        if len(cur) + len(add) > limit:
            if cur:
                chunks.append(cur)
            cur = line
        else:
            cur += add
    if cur:
        chunks.append(cur)
    return chunks


class UtilsStaff(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def _wait_owner_message(self, channel, user):
        def check(m):
            return m.channel.id == channel.id and m.author.id == user.id and not m.author.bot
        try:
            return await self.bot.wait_for("message", check=check, timeout=WAIT_TIMEOUT)
        except asyncio.TimeoutError:
            return None

    @app_commands.command(
        name="id-salon",
        description="Affiche le nom et l'ID de tous les salons d'une ou plusieurs catégories")
    async def id_salon(self, interaction: discord.Interaction):
        # 1. Réservé à l'owner.
        if interaction.user.id != OWNER_ID:
            await interaction.response.send_message(
                "❌ Cette commande est réservée à l'owner du bot.", ephemeral=True)
            return
        if interaction.guild is None:
            await interaction.response.send_message(
                "Cette commande s'utilise sur un serveur.", ephemeral=True)
            return
        # 2. Defer immédiat (le listing peut être long).
        await interaction.response.defer(ephemeral=True)

        # === 2. Sélection de la/des catégorie(s) ===
        categories = interaction.guild.categories  # ordre de position
        if not categories:
            await interaction.followup.send("Ce serveur n'a aucune catégorie.", ephemeral=True)
            return

        listing = [f"{i}. {c.name}" for i, c in enumerate(categories, 1)]
        # Pagination du listing par 15 (messages ephemeral successifs).
        for page in range(0, len(listing), CATS_PER_PAGE):
            await interaction.followup.send("\n".join(listing[page:page + CATS_PER_PAGE]), ephemeral=True)
        await interaction.followup.send(
            "Écris le(s) numéro(s) de la ou des catégories voulues, séparés par des virgules "
            "(ex: `1,3,5`), ou `toutes` pour absolument tout le serveur.",
            ephemeral=True)

        m = await self._wait_owner_message(interaction.channel, interaction.user)
        if m is None:
            await interaction.followup.send("⏳ Temps écoulé, commande annulée.", ephemeral=True)
            return

        reponse = m.content.strip().lower()
        if reponse in ("toutes", "tout", "all"):
            selection = list(categories)
        else:
            selection = []
            seen = set()
            invalides = []
            for token in reponse.split(","):
                token = token.strip()
                if not token:
                    continue
                if token.isdigit() and 1 <= int(token) <= len(categories):
                    idx = int(token)
                    if idx not in seen:  # dé-doublonne en gardant l'ordre saisi
                        seen.add(idx)
                        selection.append(categories[idx - 1])
                else:
                    invalides.append(token)
            if invalides:
                await interaction.followup.send(
                    f"❌ Entrée(s) invalide(s) : {', '.join(invalides)}. "
                    f"Utilise des numéros entre 1 et {len(categories)}, ou `toutes`.", ephemeral=True)
                return
            if not selection:
                await interaction.followup.send("❌ Aucune catégorie valide sélectionnée.", ephemeral=True)
                return

        # === 3-4. Affichage des salons et de leurs ID ===
        lines = []
        for cat in selection:
            lines.append(f"**📁 {cat.name}**")
            if not cat.channels:
                lines.append("(catégorie vide)")
            else:
                for ch in cat.channels:  # ordre de position
                    lines.append(f"{ch.name} — `{ch.id}`")
            lines.append("")  # ligne vide de séparation entre catégories

        # Message TEXTE classique, découpé sous 2000 caractères (jamais au milieu d'une ligne).
        for chunk in _chunk_lines(lines):
            await interaction.channel.send(chunk)


async def setup(bot):
    await bot.add_cog(UtilsStaff(bot))
