import discord
from discord.ext import commands
from discord import app_commands
import asyncio
import os
import random

from cogs.utils import database as db
from cogs.utils.image_gen import generate_clan_sort_image

# ---------- IDs ----------
DEPART_ROLE_ID = 1521961072334999663  # Rôle requis pour utiliser /départ

# Rôles de camp (un seul à la fois par joueur)
ROLE_EXORCISTE = 1521961288618479829
ROLE_HYBRIDE = 1521961393614749707
ROLE_HUMAIN = 1521961499730645153
CAMP_ROLES = [ROLE_EXORCISTE, ROLE_HYBRIDE, ROLE_HUMAIN]

# Rôle marqueur "appartient à un clan" (identique à cogs/clans.py)
CLAN_MEMBER_ROLE_ID = 1521961709517148220
HERITIER_ROLE_ID = 1521963035898548455

# Grades de clan (ordre d'affichage)
GRADE_ROLES = [
    ("Chef du clan", 1521963027925172344),
    ("Bras droit", 1521963034434601040),
    ("Bras gauche", 1521963034736726158),
    ("Héritier", 1521963035898548455),
    ("Bras droit héritier", 1521963040155766835),
    ("Bras gauche héritier", 1521963040809943120),
    ("Membres principaux", 1521963104903233658),
    ("Membres secondaires", 1521963107918807140),
]

# Utilisateur bénéficiant du flux spécial en message privé
SPECIAL_USER_ID = 396615332346855428

DEPART_IMAGE_URL = "https://c.tenor.com/4fjag09ZNgEAAAAC/tenor.gif"

# État initial des clans, utilisé uniquement pour amorcer la base si la table est vide.
# L'ordre de ce dictionnaire fait foi partout où la liste des clans est affichée.
DEFAULT_CLAN_STATE = {
    "clans": {
        "zenin":   {"base_pct": 20, "current_pct": 20, "cap": 15, "closed": False, "partial_heredit": False, "role_id": 1521961743729819799},
        "kamo":    {"base_pct": 15, "current_pct": 15, "cap": 15, "closed": False, "partial_heredit": False, "role_id": 1521961748838613143},
        "inumaki": {"base_pct": 10, "current_pct": 10, "cap": 15, "closed": False, "partial_heredit": False, "role_id": 1521961746196070400},
        "gojo":    {"base_pct": 8,  "current_pct": 8,  "cap": 15, "closed": False, "partial_heredit": True,  "role_id": 1521961741141934101},
        "geto":    {"base_pct": 6,  "current_pct": 6,  "cap": 15, "closed": False, "partial_heredit": False, "role_id": 1521961753141841921},
        "kashimo": {"base_pct": 4,  "current_pct": 4,  "cap": 15, "closed": False, "partial_heredit": False, "role_id": 1521961744908550166},
        "ryomen":  {"base_pct": 3,  "current_pct": 3,  "cap": 15, "closed": False, "partial_heredit": True,  "role_id": 1521961746615504926},
    },
    "sans_clan_pct": 34,
}

SORT_LABELS = {
    "sort_inne": "Sort inné",
    "sort_heredit": "Sort héréditaire",
    "sort_heredit_partiel": "Sort héréditaire partiel",
    "restriction": "Restriction céleste",
}

# Tables de sort de base
SPELL_TABLE_BASE = {"sort_inne": 55, "sort_heredit": 10, "restriction": 35}
SPELL_TABLE_PARTIAL = {"sort_inne": 40, "sort_heredit": 5, "sort_heredit_partiel": 30, "restriction": 25}


# La persistance passe désormais par SQLite (tables clan_roll_state / clan_roll_meta
# et depart_pending_choices). Les structures en mémoire restent identiques.
load_clan_state = db.load_clan_state
save_clan_state = db.save_clan_state


def get_forced_choice(user_id: int):
    """Retourne le choix forcé (clan + sort) d'un joueur, ou None s'il n'a pas complété le flux DM."""
    row = db.get_pending_choice(user_id)
    if row and row["clan"] and row["sort"]:
        return {
            "clan": row["clan"],
            "sort": row["sort"],
            "origin_channel_id": row["origin_channel_id"],
        }
    return None


def clear_forced_choice(user_id: int):
    db.delete_pending_choice(user_id)


# ---------- Tirage & redistribution ----------
def weighted_choice(options: dict) -> str:
    """Tire une clé au hasard selon les poids fournis."""
    keys = list(options.keys())
    weights = [options[key] for key in keys]
    return random.choices(keys, weights=weights, k=1)[0]


def redistribute_pct(table: dict, removed_key: str) -> dict:
    """Retire une clé d'une table de pourcentages et redistribue sa part.

    Divisible par 2 : moitié/moitié sur les 2 clés les plus faibles.
    Sinon : intégralité sur la seule clé la plus faible.
    Le total de la table est conservé.
    """
    new_table = dict(table)
    pct = new_table.pop(removed_key, 0)

    if pct <= 0 or not new_table:
        return new_table

    ordered = sorted(new_table, key=lambda key: new_table[key])

    if pct % 2 == 0 and len(ordered) >= 2:
        half = pct // 2
        new_table[ordered[0]] += half
        new_table[ordered[1]] += half
    else:
        new_table[ordered[0]] += pct

    return new_table


# ---------- Comptage en direct ----------
def get_clan_member_count(guild: discord.Guild, clan_role_id: int) -> int:
    """Compte les membres possédant à la fois le rôle marqueur de clan et le rôle du clan visé."""
    if guild is None:
        return 0
    count = 0
    for member in guild.members:
        role_ids = {role.id for role in member.roles}
        if CLAN_MEMBER_ROLE_ID in role_ids and clan_role_id in role_ids:
            count += 1
    return count


def is_heredit_taken(guild: discord.Guild, clan_role_id: int) -> bool:
    """Le sort héréditaire d'un clan est pris si un membre du clan porte déjà le rôle Héritier."""
    if guild is None:
        return False
    for member in guild.members:
        role_ids = {role.id for role in member.roles}
        if clan_role_id in role_ids and HERITIER_ROLE_ID in role_ids:
            return True
    return False


# ---------- Fermeture / réouverture des clans ----------
def close_clan_and_redistribute(data, clan_key: str):
    """Ferme un clan et redistribue son pourcentage via redistribute_pct (source de vérité unique)."""
    clans = data["clans"]

    # Table des clans ouverts (le clan à fermer en fait encore partie)
    table = {key: info["current_pct"] for key, info in clans.items() if not info["closed"]}
    new_table = redistribute_pct(table, clan_key)

    for key, pct in new_table.items():
        clans[key]["current_pct"] = pct

    clans[clan_key]["current_pct"] = 0
    clans[clan_key]["closed"] = True

    save_clan_state(data)


def check_full_reopen(data):
    """Si les 7 clans sont fermés : reset des pourcentages de base, réouverture, et cap +5."""
    clans = data["clans"]
    if all(info["closed"] for info in clans.values()):
        for info in clans.values():
            info["current_pct"] = info["base_pct"]
            info["closed"] = False
            info["cap"] += 5
        save_clan_state(data)


def update_clan_state_after_join(guild: discord.Guild, clan_key: str):
    """Ferme le clan s'il atteint son cap, puis vérifie la réouverture générale."""
    data = load_clan_state()
    info = data["clans"][clan_key]

    count = get_clan_member_count(guild, info["role_id"])
    if not info["closed"] and count >= info["cap"]:
        close_clan_and_redistribute(data, clan_key)

    check_full_reopen(data)


# ---------- Textes ----------
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

CLAN_TABLE_INTRO = (
    "Ton clan d'appartenance et ton sort vont maintenant être déterminés par le hasard. "
    "Rien n'est caché : voici les probabilités exactes de chaque clan au moment où tu joues, "
    "ainsi que le nombre de places encore disponibles.\n\n"
    "Un clan qui atteint sa capacité maximale est **fermé**, et son pourcentage est alors "
    "redistribué vers les clans les moins peuplés. Tu peux aussi ne tomber dans **aucun clan**, "
    "et naître exorciste hors lignée.\n\n"
    "──────────────────"
)


def has_depart_role(member: discord.Member) -> bool:
    return any(role.id == DEPART_ROLE_ID for role in member.roles)


def build_depart_embed() -> discord.Embed:
    embed = discord.Embed(
        title="🕯️ Départ — Le commencement",
        description=DEPART_DESCRIPTION,
        color=discord.Color.blurple(),
    )
    embed.set_image(url=DEPART_IMAGE_URL)
    return embed


def build_camp_embed() -> discord.Embed:
    return discord.Embed(
        title="🎭 Choix du camp",
        description=CAMP_DESCRIPTION,
        color=discord.Color.blurple(),
    )


def build_clan_table_embed(guild: discord.Guild) -> discord.Embed:
    """Embed de l'étape 2 : tableau des clans avec pourcentages, occupation et sort héréditaire partiel."""
    data = load_clan_state()
    clans = data["clans"]

    embed = discord.Embed(
        title="🎲 Étape 2 — Tirage du clan et du sort",
        description=CLAN_TABLE_INTRO,
        color=discord.Color.blurple(),
    )

    for clan_key, info in clans.items():
        count = get_clan_member_count(guild, info["role_id"])
        partiel = "Oui" if info["partial_heredit"] else "Non"
        name = clan_key.capitalize()

        if info["closed"]:
            field_name = f"🔒 {name} — **FERMÉ**"
        else:
            field_name = f"🏯 {name} — {info['current_pct']}%"

        embed.add_field(
            name=field_name,
            value=(
                f"Occupation : **{count}/{info['cap']}**\n"
                f"Sort héréditaire partiel : {partiel}"
            ),
            inline=False,
        )

    embed.add_field(
        name=f"🚫 Sans clan — {data['sans_clan_pct']}%",
        value="Occupation : **Illimité**\nExorciste né hors lignée.",
        inline=False,
    )

    embed.set_footer(text="Clique sur le bouton ci-dessous pour lancer ton tirage.")
    return embed


# ---------- Construction des données d'image ----------
def build_clan_image_data(state: dict, result_key: str) -> dict:
    rows = [
        {"label": key.capitalize(), "pct": info["current_pct"], "selected": key == result_key}
        for key, info in state["clans"].items()
    ]
    rows.append(
        {"label": "Sans clan", "pct": state["sans_clan_pct"], "selected": result_key == "sans_clan"}
    )
    title = "Sans clan" if result_key == "sans_clan" else result_key.capitalize()
    return {"title": title, "rows": rows}


def build_spell_image_data(base_table: dict, final_table: dict, sort_key: str, result_label: str) -> dict:
    """Les options retirées de final_table (indisponibles) sont affichées barrées avec leur pct d'origine."""
    rows = []
    for key, base_pct in base_table.items():
        if key in final_table:
            rows.append({
                "label": SORT_LABELS[key],
                "pct": final_table[key],
                "selected": key == sort_key,
                "unavailable": False,
            })
        else:
            rows.append({
                "label": SORT_LABELS[key],
                "pct": base_pct,
                "selected": False,
                "unavailable": True,
            })
    return {"result": result_label, "rows": rows}


def build_sans_clan_spell_data() -> dict:
    # Affichage purement informatif : aucun sort n'est réellement tiré.
    return {
        "result": "Aucun",
        "rows": [
            {"label": "Sort inné", "pct": 60, "selected": False, "unavailable": False},
            {"label": "Restriction céleste", "pct": 40, "selected": False, "unavailable": False},
        ],
    }


def build_grades_text(guild: discord.Guild, clan_role_id: int) -> str:
    lines = []
    for grade_name, grade_role_id in GRADE_ROLES:
        holders = [
            member.mention
            for member in guild.members
            if {clan_role_id, grade_role_id} <= {role.id for role in member.roles}
        ]
        if holders:
            lines.append(f"🔒 {grade_name} — Occupé par {', '.join(holders)}")
        else:
            lines.append(f"🟢 {grade_name} — Vacant")

    text = "\n".join(lines)
    return text if len(text) <= 1024 else text[:1021] + "..."


# ---------- Attribution des rôles ----------
async def assign_clan_roles(interaction: discord.Interaction, clan_role_id: int, heir: bool = False) -> bool:
    member: discord.Member = interaction.user
    guild = interaction.guild

    role_ids = [clan_role_id, CLAN_MEMBER_ROLE_ID]
    if heir:
        role_ids.append(HERITIER_ROLE_ID)

    roles = [guild.get_role(rid) for rid in role_ids]
    roles = [role for role in roles if role is not None]

    try:
        if roles:
            await member.add_roles(*roles)
    except discord.Forbidden:
        await interaction.followup.send(
            "❌ Je n'ai pas la permission de gérer tes rôles, préviens le staff.", ephemeral=True
        )
        return False
    return True


# ---------- Envoi du résultat final ----------
async def send_roll_result(
    interaction: discord.Interaction,
    state: dict,
    result_key: str,
    sort_key,
    result_label: str,
    base_table: dict,
    final_table: dict,
):
    clan_data = build_clan_image_data(state, result_key)

    if result_key == "sans_clan":
        spell_data = build_sans_clan_spell_data()
        grades_text = "Aucun clan, aucun grade applicable."
    else:
        spell_data = build_spell_image_data(base_table, final_table, sort_key, result_label)
        grades_text = build_grades_text(interaction.guild, state["clans"][result_key]["role_id"])

    path = generate_clan_sort_image(clan_data, spell_data)
    filename = os.path.basename(path)

    embed = discord.Embed(title="🎲 Résultat du tirage", color=discord.Color.gold())
    embed.set_image(url=f"attachment://{filename}")
    embed.add_field(name="Grades du clan", value=grades_text, inline=False)

    await interaction.followup.send(embed=embed, file=discord.File(path, filename=filename))

    try:
        os.remove(path)
    except OSError:
        pass


# ---------- Vues ----------
class HeirView(discord.ui.View):
    """Accepter / refuser de devenir l'héritier du clan. Réservé au joueur qui a tiré le sort."""

    def __init__(self, user_id: int, clan_key: str, base_table: dict, final_table: dict, state: dict):
        super().__init__(timeout=None)
        self.user_id = user_id
        self.clan_key = clan_key
        self.base_table = base_table
        self.final_table = final_table
        self.state = state

        self.accept.custom_id = f"depart_heir_accept:{user_id}"
        self.refuse.custom_id = f"depart_heir_refuse:{user_id}"

    async def _check_owner(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Ce tirage ne te concerne pas.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Accepter", emoji="✅", style=discord.ButtonStyle.success)
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_owner(interaction):
            return

        await interaction.response.edit_message(view=None)

        info = self.state["clans"][self.clan_key]
        if not await assign_clan_roles(interaction, info["role_id"], heir=True):
            return

        update_clan_state_after_join(interaction.guild, self.clan_key)

        await send_roll_result(
            interaction,
            self.state,
            self.clan_key,
            "sort_heredit",
            "Sort héréditaire (complet)",
            self.base_table,
            self.final_table,
        )

    @discord.ui.button(label="Refuser", emoji="❌", style=discord.ButtonStyle.danger)
    async def refuse(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_owner(interaction):
            return

        await interaction.response.edit_message(view=None)

        # Table réduite : le sort héréditaire est retiré et son pct redistribué.
        reduced_table = redistribute_pct(self.final_table, "sort_heredit")
        new_sort = weighted_choice(reduced_table)

        info = self.state["clans"][self.clan_key]
        # Pas de rôle Héritier ici : seulement le clan + le marqueur de clan.
        if not await assign_clan_roles(interaction, info["role_id"], heir=False):
            return

        # TODO: attribution du grade (Membres principaux/secondaires) à définir plus tard avec l'utilisateur

        update_clan_state_after_join(interaction.guild, self.clan_key)

        await send_roll_result(
            interaction,
            self.state,
            self.clan_key,
            new_sort,
            SORT_LABELS[new_sort],
            self.base_table,
            reduced_table,
        )


class ClanRollView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Roll clan/sort", emoji="🎲", style=discord.ButtonStyle.primary, custom_id="depart_roll_clan_sort")
    async def roll(self, interaction: discord.Interaction, button: discord.ui.Button):
        # La génération d'image prend un instant : on diffère la réponse.
        await interaction.response.defer()

        guild = interaction.guild
        state = load_clan_state()

        # a) Choix manipulé via le flux DM : on saute le tirage aléatoire.
        forced = get_forced_choice(interaction.user.id)
        if forced:
            result_key = forced["clan"]
            sort_key = forced["sort"]
            clear_forced_choice(interaction.user.id)
        else:
            # b) Tirage aléatoire classique : sans_clan + clans non fermés
            pool = {"sans_clan": state["sans_clan_pct"]}
            for clan_key, info in state["clans"].items():
                if not info["closed"]:
                    pool[clan_key] = info["current_pct"]

            result_key = weighted_choice(pool)
            sort_key = None

        # ----- Cas "Sans clan" -----
        if result_key == "sans_clan":
            # Aucun rôle attribué, aucun sort réel, aucune section de grades.
            await send_roll_result(interaction, state, "sans_clan", None, "Aucun", {}, {})
            return

        # ----- Cas "Clan obtenu" -----
        info = state["clans"][result_key]

        heredit_taken = is_heredit_taken(guild, info["role_id"])
        base_table = dict(SPELL_TABLE_PARTIAL if info["partial_heredit"] else SPELL_TABLE_BASE)
        final_table = redistribute_pct(base_table, "sort_heredit") if heredit_taken else dict(base_table)

        if sort_key is None:
            sort_key = weighted_choice(final_table)

        # Sort héréditaire tiré : le joueur doit accepter ou refuser de devenir héritier.
        if sort_key == "sort_heredit":
            embed = discord.Embed(
                title="👑 Sort héréditaire",
                description=(
                    f"{interaction.user.mention}, tu as obtenu le **Sort héréditaire** du clan "
                    f"**{result_key.capitalize()}** ! Deviens-tu l'héritier ?"
                ),
                color=discord.Color.gold(),
            )
            await interaction.followup.send(
                embed=embed,
                view=HeirView(interaction.user.id, result_key, base_table, final_table, state),
            )
            return

        # Sort classique : attribution directe du clan + marqueur de clan.
        if not await assign_clan_roles(interaction, info["role_id"], heir=False):
            return

        # TODO: attribution du grade (Membres principaux/secondaires) à définir plus tard avec l'utilisateur

        update_clan_state_after_join(guild, result_key)

        await send_roll_result(
            interaction, state, result_key, sort_key, SORT_LABELS[sort_key], base_table, final_table
        )


class DMSortView(discord.ui.View):
    """Boutons de choix du sort, envoyés en DM. Le bouton 'partiel' n'apparaît que si le clan le permet."""

    def __init__(self, show_partial: bool = True):
        super().__init__(timeout=None)
        if not show_partial:
            self.remove_item(self.sort_heredit_partiel)

    @discord.ui.button(label="Sort inné", style=discord.ButtonStyle.primary, custom_id="depart_dm_sort_inne")
    async def sort_inne(self, interaction: discord.Interaction, button: discord.ui.Button):
        await finalize_dm_choice(interaction, "sort_inne")

    @discord.ui.button(label="Sort héréditaire", style=discord.ButtonStyle.primary, custom_id="depart_dm_sort_heredit")
    async def sort_heredit(self, interaction: discord.Interaction, button: discord.ui.Button):
        await finalize_dm_choice(interaction, "sort_heredit")

    @discord.ui.button(label="Restriction céleste", style=discord.ButtonStyle.secondary, custom_id="depart_dm_sort_restriction")
    async def sort_restriction(self, interaction: discord.Interaction, button: discord.ui.Button):
        await finalize_dm_choice(interaction, "restriction")

    @discord.ui.button(label="Sort héréditaire partiel", style=discord.ButtonStyle.success, custom_id="depart_dm_sort_heredit_partiel")
    async def sort_heredit_partiel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await finalize_dm_choice(interaction, "sort_heredit_partiel")


class DMClanSelect(discord.ui.Select):
    def __init__(self):
        clans = load_clan_state()["clans"]
        options = [
            discord.SelectOption(label=key.capitalize(), value=key)
            for key in clans  # ordre du JSON, sans l'option "Sans clan"
        ]
        super().__init__(
            placeholder="Choisis un clan...",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="depart_dm_clan_select",
        )

    async def callback(self, interaction: discord.Interaction):
        clan_key = self.values[0]
        info = load_clan_state()["clans"].get(clan_key)
        if info is None:
            await interaction.response.send_message("Clan introuvable.", ephemeral=True)
            return

        db.set_pending_clan(interaction.user.id, clan_key)

        embed = discord.Embed(
            title="✨ Choix du sort",
            description=f"Clan retenu : **{clan_key.capitalize()}**\n\nQuel sort veux-tu pour ce personnage ?",
            color=discord.Color.blurple(),
        )
        await interaction.response.send_message(
            embed=embed, view=DMSortView(show_partial=info["partial_heredit"])
        )


class DMClanSelectView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(DMClanSelect())


class DMClanQuestionView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Oui", style=discord.ButtonStyle.success, custom_id="depart_dm_clan_oui")
    async def oui(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(
            title="🏯 Choix du clan",
            description="Sélectionne le clan que tu veux pour ce personnage.",
            color=discord.Color.blurple(),
        )
        await interaction.response.send_message(embed=embed, view=DMClanSelectView())

    @discord.ui.button(label="Non", style=discord.ButtonStyle.secondary, custom_id="depart_dm_clan_non")
    async def non(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Pas de choix forcé : on purge l'entrée, le tirage aléatoire classique s'appliquera.
        # Le tableau des clans est déjà dans le salon, rien à renvoyer ici.
        db.delete_pending_choice(interaction.user.id)
        await interaction.response.send_message(
            "Très bien, le tirage se fera normalement. Clique sur « 🎲 Roll clan/sort » dans le salon quand tu es prêt."
        )


async def finalize_dm_choice(interaction: discord.Interaction, sort_key: str):
    """Enregistre le sort choisi. Le tableau du salon a déjà été envoyé au clic sur le camp :
    on ne renvoie donc rien ici, on confirme simplement le choix en DM. Le choix forcé ne sera
    pris en compte que si le joueur clique "Roll clan/sort" APRÈS avoir terminé ce flux."""
    row = db.get_pending_choice(interaction.user.id)

    if not row or not row["clan"]:
        await interaction.response.send_message(
            "Aucun clan en attente, relance la procédure depuis le salon.", ephemeral=True
        )
        return

    db.set_pending_sort(interaction.user.id, sort_key)

    await interaction.response.send_message(
        f"Choix enregistré : **{row['clan'].capitalize()}** — **{SORT_LABELS[sort_key]}**. "
        "Retourne dans le salon et clique sur « 🎲 Roll clan/sort »."
    )


async def apply_camp_role(interaction: discord.Interaction, camp_role_id: int) -> bool:
    """Applique le rôle de camp choisi (un seul à la fois). Retourne False si l'opération a échoué."""
    member: discord.Member = interaction.user
    new_role = interaction.guild.get_role(camp_role_id)

    if new_role is None:
        await interaction.response.send_message(
            "❌ Le rôle de ce camp est introuvable sur le serveur, préviens le staff.", ephemeral=True
        )
        return False

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
            return False

    return True


class CampView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Exorciste", emoji="⚔️", style=discord.ButtonStyle.primary, custom_id="depart_camp_exorciste")
    async def exorciste(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await apply_camp_role(interaction, ROLE_EXORCISTE):
            return

        # Dans TOUS les cas : le tableau des clans + bouton Roll part immédiatement dans le salon.
        # Ce message ne dépend de rien et n'attend jamais quoi que ce soit.
        await interaction.response.send_message(
            embed=build_clan_table_embed(interaction.guild), view=ClanRollView(), ephemeral=False
        )

        # En PLUS, uniquement pour l'utilisateur spécial : un DM indépendant avec la question
        # "clan spécifique ?". Deux envois successifs, aucun wait_for ni await bloquant entre eux :
        # le joueur peut cliquer "Roll clan/sort" à tout moment (tirage aléatoire tant que son
        # flux DM n'est pas terminé). set_pending_origin crée la ligne pour que le flux DM
        # (set_pending_clan/set_pending_sort) puisse ensuite s'y greffer.
        if interaction.user.id == SPECIAL_USER_ID:
            db.set_pending_origin(interaction.user.id, interaction.channel.id)

            dm_embed = discord.Embed(
                title="🎭 Veux-tu un clan spécifique pour ce personnage ?",
                description="Réponds ci-dessous. En cas de refus, le tirage se fera normalement.",
                color=discord.Color.blurple(),
            )
            try:
                await interaction.user.send(embed=dm_embed, view=DMClanQuestionView())
            except discord.Forbidden:
                await interaction.followup.send(
                    "❌ Je n'arrive pas à t'envoyer un message privé (ouvre tes MP si tu veux choisir "
                    "ton clan). Le tirage reste disponible ci-dessus.",
                    ephemeral=True,
                )

    @discord.ui.button(label="Hybride", emoji="🧬", style=discord.ButtonStyle.danger, custom_id="depart_camp_hybride")
    async def hybride(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await apply_camp_role(interaction, ROLE_HYBRIDE):
            return
        # TODO: étape suivante — choix du lieu d'éducation (sera codée dans une prochaine étape).
        await interaction.response.send_message(
            f"{interaction.user.mention} a choisi la voie d'hybride ! 🧬 "
            "La suite (choix du lieu d'éducation) arrive bientôt.",
            ephemeral=False,
        )

    @discord.ui.button(label="Humain", emoji="🧑", style=discord.ButtonStyle.secondary, custom_id="depart_camp_humain")
    async def humain(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await apply_camp_role(interaction, ROLE_HUMAIN):
            return
        # TODO: étape suivante — génération directe de la fiche (sera codée dans une prochaine étape).
        await interaction.response.send_message(
            f"{interaction.user.mention} a choisi la voie d'humain ! 🧑 "
            "Direction directe vers la fiche de personnage, cette étape arrive bientôt.",
            ephemeral=False,
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
        # Amorce l'état des clans si la base est vide (nouvelle installation).
        db.seed_clan_state(DEFAULT_CLAN_STATE)

        self.bot.add_view(DepartView())
        self.bot.add_view(CampView())
        self.bot.add_view(ClanRollView())
        self.bot.add_view(DMClanQuestionView())
        self.bot.add_view(DMClanSelectView())
        # Enregistrée avec les 4 boutons pour couvrir tous les custom_id après redémarrage,
        # même si le message réellement envoyé n'en affichait que 3.
        self.bot.add_view(DMSortView(show_partial=True))

    @app_commands.command(name="départ", description="Démarre la création de ton personnage")
    async def depart(self, interaction: discord.Interaction):
        if not has_depart_role(interaction.user):
            # TODO: comportement à définir plus tard (point A), pour l'instant message temporaire
            await interaction.response.send_message(
                "Tu n'as pas encore accès à cette commande.", ephemeral=False
            )
            return

        embed = build_depart_embed()

        # Premier envoi : aucun bouton
        await interaction.response.send_message(embed=embed)

        # Laisse au joueur le temps de lire avant de révéler le bouton
        await asyncio.sleep(5)

        # Édite le même message pour y ajouter le bouton persistant
        await interaction.edit_original_response(embed=embed, view=DepartView())


async def setup(bot):
    await bot.add_cog(Depart(bot))
