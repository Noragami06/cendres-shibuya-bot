import discord
from discord.ext import commands
from discord import app_commands
import asyncio

# ---------- IDs ----------
DEPART_ROLE_ID = 1521961072334999663  # Rôle requis pour utiliser /départ

DEPART_IMAGE_URL = "https://c.tenor.com/4fjag09ZNgEAAAAC/tenor.gif"

DEPART_DESCRIPTION = (
    "Avant de commencer, lis chaque texte qui va suivre avec la plus grande attention. "
    "Chaque choix que tu feras aura un impact direct sur la suite, prends le temps de bien "
    "comprendre chaque étape avant de valider quoi que ce soit.\n\n"
    "**Voici le programme qui t'attend :**\n\n"
    "**1. Choix du camp** — tu devras choisir si tu incarnes un exorciste ou un hybride. "
    "Un rapide résumé de chacun te sera présenté, et ce choix influencera directement les "
    "récompenses qui te seront proposées par la suite.\n\n"
    "**2. Tirage du clan et du sort (ou lieu d'éducation pour un hybride)** — si tu es exorciste, "
    "ton clan et ton sort seront tirés au sort. Si tu es hybride, tu devras indiquer dans quel "
    "environnement ton personnage a grandi : parmi les fléaux, les exorcistes, ou les humains.\n\n"
    "**3. Choix de récompense** — tu devras choisir entre deux récompenses, A ou B. "
    "Ne prends aucun de ces choix à la légère.\n\n"
    "**4. Réserve d'énergie occulte** — la quantité d'énergie occulte de ton personnage sera "
    "tirée au sort.\n\n"
    "**5. RCT** — il sera déterminé si ton personnage possède le RCT dès le départ ou non.\n\n"
    "**6. La fiche** — pour finir, ta fiche de personnage sera générée avec toutes les "
    "informations obtenues au fil de ce parcours.\n\n"
    "*Prends une grande inspiration, prépare toi, et clique sur Commencer quand tu es prêt "
    "à débuter cette aventure.*"
)


def has_depart_role(member: discord.Member) -> bool:
    return any(role.id == DEPART_ROLE_ID for role in member.roles)


class DepartView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Commencer", style=discord.ButtonStyle.success, custom_id="depart_commencer")
    async def commencer(self, interaction: discord.Interaction, button: discord.ui.Button):
        # TODO: la vraie logique de la suite (étape 1 — choix du camp : exorciste ou hybride)
        # sera ajoutée ici dans une prochaine étape.
        # Le bouton ne se désactive volontairement jamais : il reste réutilisable indéfiniment.
        await interaction.response.send_message("La suite arrive bientôt.", ephemeral=False)


class Depart(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        self.bot.add_view(DepartView())

    @app_commands.command(name="départ", description="Démarre la création de ton personnage")
    async def depart(self, interaction: discord.Interaction):
        if not has_depart_role(interaction.user):
            # TODO: comportement à définir plus tard (point A), pour l'instant message temporaire
            await interaction.response.send_message(
                "Tu n'as pas encore accès à cette commande.", ephemeral=False
            )
            return

        embed = discord.Embed(
            title="🕯️ Départ",
            description=DEPART_DESCRIPTION,
            color=discord.Color.blurple(),
        )
        embed.set_image(url=DEPART_IMAGE_URL)

        # Premier envoi : aucun bouton
        await interaction.response.send_message(embed=embed)

        # Laisse au joueur le temps de lire avant de révéler le bouton
        await asyncio.sleep(5)

        # Édite le même message pour y ajouter le bouton persistant
        await interaction.edit_original_response(embed=embed, view=DepartView())


async def setup(bot):
    await bot.add_cog(Depart(bot))
