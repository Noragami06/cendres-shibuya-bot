import discord
from discord.ext import commands
from discord import app_commands
import asyncio

# ---------- IDs ----------
DEPART_ROLE_ID = 1521961072334999663  # Rôle requis pour utiliser /départ

# Rôles de camp (un seul à la fois par joueur)
ROLE_EXORCISTE = 1521961288618479829
ROLE_HYBRIDE = 1521961393614749707
ROLE_HUMAIN = 1521961499730645153
CAMP_ROLES = [ROLE_EXORCISTE, ROLE_HYBRIDE, ROLE_HUMAIN]

DEPART_IMAGE_URL = "https://c.tenor.com/4fjag09ZNgEAAAAC/tenor.gif"

DEPART_DESCRIPTION = (
    "Avant de commencer, lis chaque texte qui va suivre avec la plus grande attention. "
    "Ce parcours va façonner l'identité entière de ton personnage. Chaque choix que tu feras "
    "aura un impact direct et durable sur la suite, alors ne clique jamais à la légère.\n\n"
    "──────────────────\n\n"
    "**📖 Voici le programme qui t'attend :**\n\n"
    "**🎭 1. Choix du camp**\n"
    "Tu devras d'abord choisir la nature profonde de ton personnage : exorciste ou hybride. "
    "Un rapide résumé de chacun te sera présenté avant que tu ne tranches. Ce choix conditionnera "
    "directement les possibilités et les récompenses qui te seront proposées à chaque étape "
    "suivante.\n\n"
    "**🎲 2. Tirage du clan et du sort**\n"
    "Si tu es exorciste, ton clan d'appartenance et ton sort seront déterminés par le hasard, "
    "avec toutes les probabilités affichées sous tes yeux. Si tu es hybride, tu devras à la place "
    "indiquer dans quel environnement ton personnage a grandi : parmi les fléaux, parmi les "
    "exorcistes, ou parmi les humains, un choix tout aussi déterminant pour la suite.\n\n"
    "**🎁 3. Choix de récompense**\n"
    "Deux récompenses te seront proposées, A ou B. Prends le temps de bien peser chaque option, "
    "ce choix n'est pas anodin et pourra influencer durablement ton personnage.\n\n"
    "**⚡ 4. Réserve d'énergie occulte**\n"
    "La quantité d'énergie occulte que possède ton personnage sera tirée au sort selon sa classe, "
    "avec un classement te situant face aux autres joueurs de la même fourchette.\n\n"
    "**❤️‍🩹 5. RCT**\n"
    "Il sera déterminé si ton personnage maîtrise le RCT dès sa création, ou s'il devra l'apprendre "
    "plus tard en jeu.\n\n"
    "**📜 6. La fiche**\n"
    "Pour clore ce parcours, l'ensemble des informations obtenues sera rassemblé pour donner "
    "naissance à la fiche officielle de ton personnage.\n\n"
    "──────────────────\n\n"
    "*Prends une grande inspiration, prépare toi, et clique sur Commencer quand tu es prêt "
    "à débuter cette aventure.*"
)


CAMP_DESCRIPTION = (
    "Ton personnage va suivre un chemin bien précis selon la nature que tu lui donnes aujourd'hui. "
    "Prends le temps de bien lire chaque camp avant de faire ton choix, tu pourras en changer à "
    "tout moment avant de valider la suite.\n\n"
    "──────────────────\n\n"
    "**⚔️ Exorciste**\n"
    "Un exorciste est un individu capable de produire et de manipuler l'énergie occulte pour "
    "combattre les fléaux. Il descend le plus souvent d'un des sept clans, mais peut aussi être un "
    "cas exceptionnel né hors lignée. C'est la voie du combat reconnu, encadrée par les ordres et "
    "l'autorité mondiale. En choisissant ce camp, tu passeras par le tirage de ton clan et de ton "
    "sort, puis par toutes les étapes suivantes de ce parcours.\n\n"
    "**🧬 Hybride**\n"
    "Un hybride est un humain génétiquement modifié, porteur de gènes de fléau, tout en conservant "
    "une pleine conscience humaine. Son corps s'adapte à l'environnement dans lequel il grandit. "
    "En choisissant ce camp, tu devras indiquer où ton personnage a été élevé, avant de poursuivre "
    "toi aussi les étapes suivantes de ce parcours.\n\n"
    "**🧑 Humain**\n"
    "Un humain ne possède aucune énergie occulte à sa naissance, et son parcours de création "
    "s'arrête ici : il passera directement à l'étape de la fiche, sans tirage de clan, de sort, de "
    "récompense, de réserve d'énergie, ni de RCT. Cela ne veut pas dire qu'il est sans importance : "
    "rien ne l'empêche de diriger un ordre et de constituer sa propre équipe, même sans le moindre "
    "pouvoir. Dans de très rares cas, un humain peut tout de même acquérir de l'énergie occulte, en "
    "devenant un réceptacle, ou en concluant un pacte avec un exorciste capable de la lui "
    "transmettre.\n\n"
    "──────────────────\n\n"
    "*Clique sur le camp qui correspond à ton personnage.*"
)


def has_depart_role(member: discord.Member) -> bool:
    return any(role.id == DEPART_ROLE_ID for role in member.roles)


def build_camp_embed() -> discord.Embed:
    return discord.Embed(
        title="🎭 Choix du camp",
        description=CAMP_DESCRIPTION,
        color=discord.Color.blurple(),
    )


async def handle_camp_choice(interaction: discord.Interaction, camp_role_id: int, response_text: str):
    """Applique le rôle de camp choisi (un seul à la fois) puis confirme publiquement."""
    member: discord.Member = interaction.user
    new_role = interaction.guild.get_role(camp_role_id)

    if new_role is None:
        await interaction.response.send_message(
            "❌ Le rôle de ce camp est introuvable sur le serveur, préviens le staff.", ephemeral=True
        )
        return

    current_camp_roles = [role for role in member.roles if role.id in CAMP_ROLES]

    # Si le joueur a déjà exactement ce rôle : simple reconfirmation, aucun changement de rôles.
    if new_role not in current_camp_roles:
        try:
            if current_camp_roles:
                await member.remove_roles(*current_camp_roles)
            await member.add_roles(new_role)
        except discord.Forbidden:
            await interaction.response.send_message(
                "❌ Je n'ai pas la permission de gérer tes rôles, préviens le staff.", ephemeral=True
            )
            return

    await interaction.response.send_message(response_text.format(mention=member.mention), ephemeral=False)


class CampView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Exorciste", emoji="⚔️", style=discord.ButtonStyle.primary, custom_id="depart_camp_exorciste")
    async def exorciste(self, interaction: discord.Interaction, button: discord.ui.Button):
        # TODO: étape suivante — tirage du clan et du sort (sera codée dans une prochaine étape).
        await handle_camp_choice(
            interaction,
            ROLE_EXORCISTE,
            "{mention} a choisi la voie d'exorciste ! ⚔️ La suite (tirage du clan et du sort) arrive bientôt.",
        )

    @discord.ui.button(label="Hybride", emoji="🧬", style=discord.ButtonStyle.danger, custom_id="depart_camp_hybride")
    async def hybride(self, interaction: discord.Interaction, button: discord.ui.Button):
        # TODO: étape suivante — choix du lieu d'éducation (sera codée dans une prochaine étape).
        await handle_camp_choice(
            interaction,
            ROLE_HYBRIDE,
            "{mention} a choisi la voie d'hybride ! 🧬 La suite (choix du lieu d'éducation) arrive bientôt.",
        )

    @discord.ui.button(label="Humain", emoji="🧑", style=discord.ButtonStyle.secondary, custom_id="depart_camp_humain")
    async def humain(self, interaction: discord.Interaction, button: discord.ui.Button):
        # TODO: étape suivante — génération directe de la fiche (sera codée dans une prochaine étape).
        await handle_camp_choice(
            interaction,
            ROLE_HUMAIN,
            "{mention} a choisi la voie d'humain ! 🧑 Direction directe vers la fiche de personnage, cette étape arrive bientôt.",
        )


class DepartView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Commencer", style=discord.ButtonStyle.success, custom_id="depart_commencer")
    async def commencer(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Nouveau message public : l'embed de départ précédent reste intact.
        # Le bouton ne se désactive volontairement jamais : il reste réutilisable indéfiniment.
        await interaction.response.send_message(embed=build_camp_embed(), view=CampView(), ephemeral=False)


class Depart(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        self.bot.add_view(DepartView())
        self.bot.add_view(CampView())

    @app_commands.command(name="départ", description="Démarre la création de ton personnage")
    async def depart(self, interaction: discord.Interaction):
        if not has_depart_role(interaction.user):
            # TODO: comportement à définir plus tard (point A), pour l'instant message temporaire
            await interaction.response.send_message(
                "Tu n'as pas encore accès à cette commande.", ephemeral=False
            )
            return

        embed = discord.Embed(
            title="🕯️ Départ — Le commencement",
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
