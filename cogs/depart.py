import discord
from discord.ext import commands
from discord import app_commands
import asyncio
import os
import random
import uuid

from cogs.utils import database as db
from cogs.utils.image_gen import (
    generate_clan_sort_image,
    generate_reserve_image,
    generate_recompense_image,
    generate_slots_image,
)


# ---------- Chemins temporaires des images générées ----------
IMAGE_TEMP_DIR = os.path.join(os.path.dirname(__file__), "..", "temp", "depart_images")


def _tmp_image_path(prefix: str) -> str:
    """Chemin PNG temporaire unique (remplace l'ancien make_output_path retiré de image_gen)."""
    os.makedirs(IMAGE_TEMP_DIR, exist_ok=True)
    return os.path.join(IMAGE_TEMP_DIR, f"{prefix}_{uuid.uuid4().hex}.png")


def _render_clan_sort_image(clan_data: dict, spell_data: dict) -> str:
    """Adapte les structures internes (clan_data/spell_data) à la signature positionnelle de
    generate_clan_sort_image, et retourne le chemin du PNG généré. Aucune logique de dessin ici :
    tout le rendu vient de cogs.utils.image_gen."""
    clans_table = [
        (row["label"], f"{row['pct']}%", row["selected"])
        for row in clan_data["rows"]
    ]
    spells_table = [
        (row["label"], f"{row['pct']}%", row["selected"], row.get("unavailable", False))
        for row in spell_data["rows"]
    ]
    out_path = _tmp_image_path("clan_sort")
    generate_clan_sort_image(clan_data["title"], clans_table, spell_data["result"], spells_table, out_path)
    return out_path

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
MEMBRES_PRINCIPAUX_ROLE_ID = 1521963104903233658  # Grade attribué d'office à l'hybride élevé chez les exorcistes

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

# ---------- Réserve d'énergie occulte ----------
EO_CLASS_TABLE = {
    "classe_4": {"min": 100, "max": 1000, "pct": 45},
    "classe_3": {"min": 1000, "max": 5000, "pct": 30},
    "classe_2": {"min": 5000, "max": 15000, "pct": 15},
    "classe_1": {"min": 15000, "max": 40000, "pct": 7},
    "classe_s": {"min": 40000, "max": 2000000, "pct": 3},
}

# Fourchette spéciale de la classe S quand elle provient d'un choix manuel (utilisateur privilégié).
EO_CLASSE_S_MANUAL_MIN, EO_CLASSE_S_MANUAL_MAX = 1700000, 2000000

EO_NATURE_TABLE = {
    "sans_nature": 65,
    "brute": 20,
    "electrique": 10,
    "raffinee": 5,
}

NATURE_DISPLAY_NAMES = {
    "sans_nature": "Sans nature",
    "brute": "Nature brute",
    "electrique": "Nature électrique",
    "raffinee": "Nature raffinée",
}

# ---------- Suivi de progression du parcours (SQLite : depart_character_progress) ----------
def update_progress(user_id: int, **fields):
    """Fusionne les champs fournis dans la progression de l'utilisateur (sans écraser le reste)."""
    db.upsert_character_progress(user_id, fields)


def get_progress(user_id: int) -> dict:
    return db.get_character_progress(user_id)


def add_progress_item(user_id: int, name: str):
    """Ajoute un objet obtenu (future fiche/inventaire)."""
    db.add_character_item(user_id, name)


def add_progress_pending_reroll(user_id: int, key: str):
    """Mémorise un reroll gagné mais pas encore applicable (territoire/RCT)."""
    db.add_character_pending_reroll(user_id, key)


# ---------- Récompenses ----------
REWARD_TABLE = [
    {"key": "argent", "label": "Argent", "pct": 20.75, "category": "currency"},
    {"key": "xp", "label": "XP", "pct": 15.56, "category": "currency"},
    {"key": "reroll_clan", "label": "Reroll Clan", "pct": 11.41, "category": "reroll"},
    {"key": "reroll_sort", "label": "Reroll Sort", "pct": 9.34, "category": "reroll"},
    {"key": "reroll_energie_qte", "label": "Reroll Quantité d'énergie", "pct": 7.78, "category": "reroll"},
    {"key": "reroll_energie_nature", "label": "Reroll Nature d'énergie", "pct": 6.74, "category": "reroll"},
    {"key": "reroll_territoire", "label": "Reroll Territoire", "pct": 5.71, "category": "reroll_todo"},
    {"key": "reroll_rct", "label": "Reroll RCT", "pct": 4.67, "category": "reroll_todo"},
    {"key": "relique_4", "label": "Relique de classe 4", "pct": 3.94, "category": "item"},
    {"key": "relique_3", "label": "Relique de classe 3", "pct": 3.32, "category": "item"},
    {"key": "relique_2", "label": "Relique de classe 2", "pct": 2.70, "category": "item"},
    {"key": "arme_4", "label": "Arme de classe 4", "pct": 2.18, "category": "item"},
    {"key": "arme_3", "label": "Arme de classe 3", "pct": 1.76, "category": "item"},
    {"key": "arme_2", "label": "Arme de classe 2", "pct": 1.35, "category": "item"},
    {"key": "arme_1", "label": "Arme de classe 1", "pct": 1.04, "category": "item"},
    {"key": "relique_1", "label": "Relique de classe 1", "pct": 0.78, "category": "item"},
    {"key": "arme_s", "label": "Arme de classe S", "pct": 0.57, "category": "item"},
    {"key": "relique_s", "label": "Relique de classe S", "pct": 0.40, "category": "item"},
]

ARGENT_MIN, ARGENT_MAX = 10000, 100000
XP_MIN, XP_MAX = 1000, 10000


def resolve_reward(reward_def: dict) -> dict:
    """Retourne un dict complet {key, name, qty, amount} prêt à afficher ET à appliquer."""
    if reward_def["key"] == "argent":
        amount = random.randint(ARGENT_MIN, ARGENT_MAX)
        return {"key": "argent", "name": "Argent", "qty": f"{amount:,} yens".replace(",", " "), "amount": amount}
    if reward_def["key"] == "xp":
        amount = random.randint(XP_MIN, XP_MAX)
        return {"key": "xp", "name": "XP", "qty": f"{amount} XP", "amount": amount}
    return {"key": reward_def["key"], "name": reward_def["label"], "qty": "x1", "amount": None}


def pick_two_distinct_rewards():
    """Tire deux récompenses différentes selon les poids de REWARD_TABLE."""
    keys = [r["key"] for r in REWARD_TABLE]
    weights = [r["pct"] for r in REWARD_TABLE]
    first_key = random.choices(keys, weights=weights, k=1)[0]
    remaining = [(k, w) for k, w in zip(keys, weights) if k != first_key]
    second_key = random.choices([k for k, w in remaining], weights=[w for k, w in remaining], k=1)[0]
    first_def = next(r for r in REWARD_TABLE if r["key"] == first_key)
    second_def = next(r for r in REWARD_TABLE if r["key"] == second_key)
    return resolve_reward(first_def), resolve_reward(second_def)


# ---------- Choix de récompense en attente (SQLite : depart_pending_rewards) ----------
def store_pending_rewards(user_id: int, option_a: dict, option_b: dict):
    db.set_pending_rewards(user_id, option_a, option_b)


def get_pending_rewards(user_id: int):
    return db.get_pending_rewards(user_id)


def clear_pending_reward(user_id: int):
    db.delete_pending_rewards(user_id)


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


def build_hybride_spell_data(partial_heredit: bool) -> dict:
    """Table de sort d'un hybride élevé chez les exorcistes : toujours Sort inné à 100%,
    tout le reste verrouillé (barré) pour montrer que c'est inaccessible."""
    rows = [
        {"label": "Restriction céleste", "pct": 0, "selected": False, "unavailable": True},
        {"label": "Sort inné", "pct": 100, "selected": True, "unavailable": False},
        {"label": "Sort héréditaire", "pct": 0, "selected": False, "unavailable": True},
    ]
    if partial_heredit:
        rows.append({"label": "Sort héréditaire partiel", "pct": 0, "selected": False, "unavailable": True})
    return {"result": "Sort inné", "rows": rows}


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
async def assign_clan_roles(interaction: discord.Interaction, clan_role_id: int, heir: bool = False,
                            extra_role_ids: list = None) -> bool:
    member: discord.Member = interaction.user
    guild = interaction.guild

    role_ids = [clan_role_id, CLAN_MEMBER_ROLE_ID]
    if heir:
        role_ids.append(HERITIER_ROLE_ID)
    if extra_role_ids:
        role_ids.extend(extra_role_ids)

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
    spell_data_override: dict = None,
):
    clan_data = build_clan_image_data(state, result_key)

    if result_key == "sans_clan":
        spell_data = spell_data_override or build_sans_clan_spell_data()
        grades_text = "Aucun clan, aucun grade applicable."
    else:
        spell_data = spell_data_override or build_spell_image_data(base_table, final_table, sort_key, result_label)
        grades_text = build_grades_text(interaction.guild, state["clans"][result_key]["role_id"])

    path = _render_clan_sort_image(clan_data, spell_data)

    # 1er message : l'image seule, en pièce jointe, sans embed autour.
    await interaction.followup.send(file=discord.File(path, filename="clan_sort.png"))

    try:
        os.remove(path)
    except OSError:
        pass

    # 2e message : un embed sans image, avec l'état des 8 grades + bouton "Continuer"
    # (étape suivante : réserve d'énergie occulte).
    embed = discord.Embed(title="🎲 Résultat du tirage", color=discord.Color.gold())
    embed.add_field(name="Grades du clan", value=grades_text, inline=False)
    await interaction.followup.send(embed=embed, view=ContinueEnergyView())

    # En PLUS, pour l'utilisateur privilégié : DM lui proposant de choisir la classe de sa réserve.
    # Envoi indépendant, sans wait_for ni blocage du reste.
    if interaction.user.id == SPECIAL_USER_ID:
        await offer_reserve_choice_dm(interaction.user)


async def offer_reserve_choice_dm(user: discord.User):
    embed = discord.Embed(
        title="🔮 Veux-tu choisir la classe de ta réserve d'énergie occulte ?",
        description="Sélectionne une classe dans le menu ci-dessous, ou ignore ce message pour un tirage aléatoire.",
        color=discord.Color.blurple(),
    )
    try:
        await user.send(embed=embed, view=ReserveClassView())
    except discord.Forbidden:
        pass


class ReserveClassSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="Classe 4", value="classe_4"),
            discord.SelectOption(label="Classe 3", value="classe_3"),
            discord.SelectOption(label="Classe 2", value="classe_2"),
            discord.SelectOption(label="Classe 1", value="classe_1"),
            discord.SelectOption(label="Classe S", value="classe_s"),
        ]
        super().__init__(
            placeholder="Choisis la classe de ta réserve...",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="depart_reserve_class_select",
        )

    async def callback(self, interaction: discord.Interaction):
        classe = self.values[0]
        db.set_pending_reserve_choice(interaction.user.id, classe)
        label = classe.replace("classe_", "").upper()  # "classe_s" -> "S", "classe_4" -> "4"
        await interaction.response.send_message(f"Classe {label} enregistrée pour ta réserve.")


class ReserveClassView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(ReserveClassSelect())


async def roll_and_send_reserve(source, member: discord.Member, guild: discord.Guild, with_nature: bool):
    """Tire la classe d'énergie occulte, sa valeur, éventuellement sa nature, génère l'image
    et l'envoie. `source` peut être une Interaction (qui sera acquittée) ou directement un salon."""
    if isinstance(source, discord.Interaction):
        if not source.response.is_done():
            await source.response.defer()
        channel = source.channel
    else:
        channel = source

    # Choix manuel de la classe (flux DM spécial) prioritaire sur le tirage aléatoire.
    forced_classe = db.get_pending_reserve_choice(member.id)
    if forced_classe and forced_classe in EO_CLASS_TABLE:
        eo_classe = forced_classe
        info = EO_CLASS_TABLE[eo_classe]
        if eo_classe == "classe_s":
            # Fourchette spéciale réservée au choix manuel de la classe S.
            value = random.randint(EO_CLASSE_S_MANUAL_MIN, EO_CLASSE_S_MANUAL_MAX)
        else:
            value = random.randint(info["min"], info["max"])
        db.delete_pending_reserve_choice(member.id)  # usage unique
    else:
        # Tirage aléatoire classique de la classe puis de la valeur.
        class_pool = {key: info["pct"] for key, info in EO_CLASS_TABLE.items()}
        eo_classe = weighted_choice(class_pool)
        info = EO_CLASS_TABLE[eo_classe]
        value = random.randint(info["min"], info["max"])

    nature = weighted_choice(EO_NATURE_TABLE) if with_nature else None

    # 1er message : l'image de réserve (classement + nature si applicable).
    await render_and_send_reserve_image(channel, member, eo_classe, value, nature, with_nature)

    # 2e message (uniquement avec nature) : la nature obtenue.
    if with_nature:
        await channel.send(embed=build_nature_embed(member, nature))

        # 3e message : bouton "Continuer" vers l'étape récompense (persistant).
        await channel.send(
            embed=discord.Embed(
                description="Clique pour continuer ton parcours.", color=discord.Color.blurple()
            ),
            view=RewardContinueView(),
        )

    update_progress(member.id, eo_classe=eo_classe, eo_value=value, nature=nature)


def build_nature_embed(member: discord.Member, nature: str) -> discord.Embed:
    return discord.Embed(
        title="🔮 Nature de l'énergie occulte",
        description=f"{member.mention} possède une **{NATURE_DISPLAY_NAMES[nature]}** !",
        color=discord.Color.purple(),
    )


async def render_and_send_reserve_image(channel, member, eo_classe, value, nature, with_nature):
    """Génère et envoie l'image de réserve pour des valeurs déjà déterminées (réutilisé aux rerolls)."""
    info = EO_CLASS_TABLE[eo_classe]

    # Classement réel de la classe : personnages déjà validés + le joueur actuel (pas encore en base).
    # TODO: l'insertion dans validated_characters se fera uniquement à l'étape de validation
    # de la fiche par le staff (point 7 du parcours, pas encore développée). Tant que la table
    # est vide, chaque joueur qui teste se retrouve seul en 1ère position, ce qui est normal.
    validated = db.get_class_ranking(eo_classe)

    merged = [(member.name, value, True)]
    merged += [(row["discord_username"], row["eo_value"], False) for row in validated]
    merged.sort(key=lambda entry: entry[1], reverse=True)  # tri numérique AVANT le formatage
    # Valeurs formatées avec virgules (séparateur de milliers) pour l'affichage.
    ranking = [(rank, name, f"{val:,}", hit) for rank, (name, val, hit) in enumerate(merged[:4], start=1)]

    if with_nature and nature is not None:
        energy_table = [
            (NATURE_DISPLAY_NAMES[key], f"{pct}%", key == nature)
            for key, pct in EO_NATURE_TABLE.items()
        ]
    else:
        energy_table = []

    classe_display = eo_classe.replace("classe_", "").upper()  # "classe_4" -> "4", "classe_s" -> "S"
    path = _tmp_image_path("reserve")
    generate_reserve_image(classe_display, value, info["min"], info["max"], ranking, energy_table, path)

    await channel.send(file=discord.File(path, filename="reserve.png"))
    try:
        os.remove(path)
    except OSError:
        pass


# ---------- Récompenses : rendu & application ----------
def build_result_spell_data(state, clan_key, sort_key, path, guild):
    """Reconstruit le spell_data d'un résultat clan/sort déjà déterminé (pour régénérer un pillow)."""
    if clan_key == "sans_clan":
        return build_hybride_spell_data(False) if path == "hybride_exorciste" else build_sans_clan_spell_data()

    info = state["clans"][clan_key]
    if path == "hybride_exorciste":
        return build_hybride_spell_data(info["partial_heredit"])

    heredit_taken = is_heredit_taken(guild, info["role_id"])
    base_table = dict(SPELL_TABLE_PARTIAL if info["partial_heredit"] else SPELL_TABLE_BASE)
    final_table = redistribute_pct(base_table, "sort_heredit") if heredit_taken else dict(base_table)
    label = "Sort héréditaire (complet)" if sort_key == "sort_heredit" else SORT_LABELS.get(sort_key, "Sort inné")
    return build_spell_image_data(base_table, final_table, sort_key, label)


async def send_clan_sort_pillow(channel, state, clan_key, spell_data):
    clan_data = build_clan_image_data(state, clan_key)
    path = _render_clan_sort_image(clan_data, spell_data)
    await channel.send(file=discord.File(path, filename="clan_sort.png"))
    try:
        os.remove(path)
    except OSError:
        pass


def _reward_embed(text: str) -> discord.Embed:
    return discord.Embed(description=text, color=discord.Color.gold())


async def apply_reward(interaction: discord.Interaction, reward: dict):
    """Applique l'effet de la récompense choisie selon reward['key']."""
    member = interaction.user
    channel = interaction.channel
    uid = member.id
    key = reward["key"]
    progress = get_progress(uid)

    if key in ("argent", "xp"):
        # TODO: intégrer réellement ce montant dans le futur système économique/XP une fois développé.
        await channel.send(embed=_reward_embed(f"{member.mention} a choisi **{reward['qty']}** !"))
        return

    if key == "reroll_clan":
        await reward_reroll_clan(interaction, progress)
        return
    if key == "reroll_sort":
        await reward_reroll_sort(interaction, progress)
        return
    if key == "reroll_energie_qte":
        await reward_reroll_energie_qte(interaction, progress)
        return
    if key == "reroll_energie_nature":
        await reward_reroll_energie_nature(interaction, progress)
        return

    if key in ("reroll_territoire", "reroll_rct"):
        # TODO: aucun effet pour l'instant, les étapes Territoire et RCT ne sont pas encore
        # développées ; cette récompense sera appliquée plus tard (manuellement ou automatiquement
        # une fois ces étapes codées).
        add_progress_pending_reroll(uid, key)
        await channel.send(embed=_reward_embed(
            f"{member.mention} a obtenu **{reward['name']}**. Elle est enregistrée et sera appliquée "
            "quand cette étape sera disponible."
        ))
        return

    # Objets (relique_X / arme_X)
    # TODO: pas de système d'inventaire pour l'instant, juste enregistré pour la future fiche.
    add_progress_item(uid, reward["name"])
    await channel.send(embed=_reward_embed(f"{member.mention} a obtenu : **{reward['name']}** !"))


async def reward_reroll_clan(interaction, progress):
    member = interaction.user
    guild = interaction.guild
    channel = interaction.channel
    uid = member.id
    path = progress.get("path")
    sort_key = progress.get("sort")
    old_clan = progress.get("clan")

    state = load_clan_state()

    remove_roles = []
    if old_clan and old_clan != "sans_clan":
        old_info = state["clans"].get(old_clan)
        if old_info:
            r = guild.get_role(old_info["role_id"])
            if r:
                remove_roles.append(r)

    # Nouveau tirage de clan, identique au parcours normal.
    pool = {"sans_clan": state["sans_clan_pct"]}
    for clan_key, inf in state["clans"].items():
        if not inf["closed"]:
            pool[clan_key] = inf["current_pct"]
    new_clan = weighted_choice(pool)

    if new_clan == "sans_clan":
        marker = guild.get_role(CLAN_MEMBER_ROLE_ID)
        if marker and marker in member.roles:
            remove_roles.append(marker)
        if remove_roles:
            try:
                await member.remove_roles(*remove_roles)
            except discord.Forbidden:
                pass
    else:
        if remove_roles:
            try:
                await member.remove_roles(*remove_roles)
            except discord.Forbidden:
                pass
        new_info = state["clans"][new_clan]
        extra = [MEMBRES_PRINCIPAUX_ROLE_ID] if path == "hybride_exorciste" else None
        await assign_clan_roles(interaction, new_info["role_id"], extra_role_ids=extra)
        update_clan_state_after_join(guild, new_clan)

    update_progress(uid, clan=new_clan)
    # TODO: cas limite si le nouveau clan ne permet pas le sort déjà obtenu (ex: sort héréditaire
    # partiel sur un clan qui ne le propose pas), à vérifier manuellement pour l'instant.

    state = load_clan_state()
    spell_data = build_result_spell_data(state, new_clan, sort_key, path, guild)
    await send_clan_sort_pillow(channel, state, new_clan, spell_data)

    label = "Sans clan" if new_clan == "sans_clan" else new_clan.capitalize()
    await channel.send(embed=_reward_embed(f"{member.mention} a rerollé son clan : **{label}** !"))


async def reward_reroll_sort(interaction, progress):
    member = interaction.user
    guild = interaction.guild
    channel = interaction.channel
    uid = member.id
    path = progress.get("path")

    if path == "hybride_exorciste":
        # TODO: ce reroll n'a aucun effet pour un hybride chez les exorcistes puisqu'il est
        # toujours forcé à Sort inné, à gérer/exclure plus tard si besoin.
        await channel.send(embed=_reward_embed(
            f"{member.mention} est un hybride élevé chez les exorcistes : son sort reste **Sort inné**, "
            "ce reroll n'a aucun effet."
        ))
        return

    clan_key = progress.get("clan")
    state = load_clan_state()
    if not clan_key or clan_key == "sans_clan":
        await channel.send(embed=_reward_embed(
            f"{member.mention} n'a aucun clan : il n'y a pas de sort à reroll."
        ))
        return

    info = state["clans"][clan_key]
    heredit_taken = is_heredit_taken(guild, info["role_id"])
    base_table = dict(SPELL_TABLE_PARTIAL if info["partial_heredit"] else SPELL_TABLE_BASE)
    final_table = redistribute_pct(base_table, "sort_heredit") if heredit_taken else dict(base_table)
    new_sort = weighted_choice(final_table)

    # TODO: si new_sort == "sort_heredit", la validation accepter/refuser héritier n'est pas
    # re-déclenchée ici ; le reroll attribue directement le sort héréditaire complet (rôle Héritier).
    if new_sort == "sort_heredit":
        await assign_clan_roles(interaction, info["role_id"], heir=True)
        label = "Sort héréditaire (complet)"
    else:
        label = SORT_LABELS[new_sort]

    update_progress(uid, sort=new_sort)

    spell_data = build_spell_image_data(base_table, final_table, new_sort, label)
    await send_clan_sort_pillow(channel, state, clan_key, spell_data)
    await channel.send(embed=_reward_embed(f"{member.mention} a rerollé son sort : **{label}** !"))


async def reward_reroll_energie_qte(interaction, progress):
    member = interaction.user
    channel = interaction.channel
    uid = member.id

    class_pool = {k: v["pct"] for k, v in EO_CLASS_TABLE.items()}
    eo_classe = weighted_choice(class_pool)
    value = random.randint(EO_CLASS_TABLE[eo_classe]["min"], EO_CLASS_TABLE[eo_classe]["max"])
    nature = progress.get("nature")  # nature inchangée

    update_progress(uid, eo_classe=eo_classe, eo_value=value)
    await render_and_send_reserve_image(channel, member, eo_classe, value, nature, nature is not None)
    await channel.send(embed=_reward_embed(f"{member.mention} a rerollé sa quantité d'énergie occulte !"))


async def reward_reroll_energie_nature(interaction, progress):
    member = interaction.user
    channel = interaction.channel
    new_nature = weighted_choice(EO_NATURE_TABLE)
    update_progress(member.id, nature=new_nature)
    await channel.send(embed=build_nature_embed(member, new_nature))


# ---------- Vues ----------
class RewardContinueView(discord.ui.View):
    """Bouton "Continuer" affiché après l'étape nature, qui déclenche l'étape récompense
    (exorciste / hybride-exorciste) ou un message temporaire (livré à soi même)."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Continuer", emoji="➡️", style=discord.ButtonStyle.success, custom_id="depart_continuer_recompense")
    async def continuer(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        progress = get_progress(interaction.user.id)
        path = progress.get("path")

        if path in ("exorciste", "hybride_exorciste"):
            option_a, option_b = pick_two_distinct_rewards()
            store_pending_rewards(interaction.user.id, option_a, option_b)

            img = _tmp_image_path("recompense")
            generate_recompense_image(option_a, option_b, img)
            await interaction.channel.send(file=discord.File(img, filename="recompense.png"))
            try:
                os.remove(img)
            except OSError:
                pass

            embed = discord.Embed(
                title="🎁 Choix de récompense",
                description=(
                    "Il est temps de faire un choix. Deux récompenses s'offrent à toi, mais tu ne "
                    "peux en garder qu'une seule. Prends le temps de bien réfléchir avant de cliquer, "
                    "ce choix est définitif une fois validé."
                ),
                color=discord.Color.gold(),
            )
            await interaction.channel.send(embed=embed, view=RewardChoiceView(interaction.user.id))

        elif path == "hybride_seul":
            # TODO: système de récompense propre à Livré à soi même, pas encore défini
            await interaction.channel.send("La suite arrive dans une prochaine étape.")
        else:
            await interaction.channel.send("La suite arrive dans une prochaine étape.")


class RewardChoiceView(discord.ui.View):
    """Deux boutons Récompense A / B, réservés au joueur concerné (custom_id suffixé de son id)."""

    def __init__(self, user_id: int):
        super().__init__(timeout=None)
        self.user_id = user_id
        self.reward_a.custom_id = f"depart_reward_a:{user_id}"
        self.reward_b.custom_id = f"depart_reward_b:{user_id}"

    async def _check_owner(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Ce choix ne t'appartient pas.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Récompense A", style=discord.ButtonStyle.primary)
    async def reward_a(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_owner(interaction):
            return
        await handle_reward_choice(interaction, "option_a")

    @discord.ui.button(label="Récompense B", style=discord.ButtonStyle.primary)
    async def reward_b(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_owner(interaction):
            return
        await handle_reward_choice(interaction, "option_b")


async def handle_reward_choice(interaction: discord.Interaction, which: str):
    uid = interaction.user.id
    pending = get_pending_rewards(uid)
    if not pending or which not in pending:
        await interaction.response.send_message(
            "Cette récompense n'est plus disponible (déjà utilisée).", ephemeral=True
        )
        return

    reward = pending[which]

    # Retire les boutons du message de choix (plus rien de cliquable), applique, puis purge.
    await interaction.response.edit_message(view=None)
    await apply_reward(interaction, reward)
    clear_pending_reward(uid)


class ContinueEnergyView(discord.ui.View):
    """Bouton "Continuer" affiché après le tirage clan/sort (exorciste ou hybride-exorciste),
    qui déclenche l'étape réserve + nature."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Continuer", emoji="➡️", style=discord.ButtonStyle.success, custom_id="depart_continuer_energie")
    async def continuer(self, interaction: discord.Interaction, button: discord.ui.Button):
        await roll_and_send_reserve(interaction, interaction.user, interaction.guild, with_nature=True)


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
        update_progress(interaction.user.id, camp="exorciste", path="exorciste", clan=self.clan_key, sort="sort_heredit")

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
        update_progress(interaction.user.id, camp="exorciste", path="exorciste", clan=self.clan_key, sort=new_sort)

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
            update_progress(interaction.user.id, camp="exorciste", path="exorciste", clan="sans_clan", sort=None)
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
        update_progress(interaction.user.id, camp="exorciste", path="exorciste", clan=result_key, sort=sort_key)

        await send_roll_result(
            interaction, state, result_key, sort_key, SORT_LABELS[sort_key], base_table, final_table
        )


class ClanRollHybrideView(discord.ui.View):
    """Tirage du clan pour un hybride élevé chez les exorcistes.
    Même tirage de clan que l'exorciste classique, mais le sort est TOUJOURS Sort inné
    (aucun héritier, aucune restriction), et le grade Membres principaux est attribué d'office."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Roll clan (Hybride)", emoji="🎲", style=discord.ButtonStyle.primary, custom_id="depart_roll_clan_hybride")
    async def roll(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()

        guild = interaction.guild
        state = load_clan_state()

        # a) Tirage aléatoire du clan, identique à l'exorciste classique (pas de choix forcé ici).
        pool = {"sans_clan": state["sans_clan_pct"]}
        for clan_key, info in state["clans"].items():
            if not info["closed"]:
                pool[clan_key] = info["current_pct"]
        result_key = weighted_choice(pool)

        # d) Sans clan : aucun rôle attribué (ni clan, ni marqueur de clan).
        if result_key == "sans_clan":
            update_progress(interaction.user.id, camp="hybride", path="hybride_exorciste", clan="sans_clan", sort="sort_inne")
            spell_data = build_hybride_spell_data(partial_heredit=False)
            await send_roll_result(
                interaction, state, "sans_clan", None, "Sort inné", {}, {}, spell_data_override=spell_data
            )
            return

        # c) Clan précis : clan + marqueur + Membres principaux (automatique, sans confirmation).
        info = state["clans"][result_key]
        if not await assign_clan_roles(
            interaction, info["role_id"], extra_role_ids=[MEMBRES_PRINCIPAUX_ROLE_ID]
        ):
            return

        # L'hybride occupe une vraie place du clan : mêmes règles de fermeture/réouverture.
        update_clan_state_after_join(guild, result_key)
        update_progress(interaction.user.id, camp="hybride", path="hybride_exorciste", clan=result_key, sort="sort_inne")

        # b/e) Sort toujours "Sort inné", table verrouillée pour l'affichage.
        spell_data = build_hybride_spell_data(info["partial_heredit"])
        await send_roll_result(
            interaction, state, result_key, "sort_inne", "Sort inné", {}, {}, spell_data_override=spell_data
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


EDUCATION_DESCRIPTION = (
    "L'endroit où ton personnage a grandi a façonné en profondeur ce qu'il est devenu. "
    "Chaque environnement a laissé une empreinte différente sur son corps et sur ses capacités. "
    "Choisis avec soin.\n\n"
    "──────────────────\n\n"
    "**🏙️ 1. Chez les humains**\n"
    "Élevé au sein d'une famille ordinaire, ton personnage a grandi sans jamais toucher à sa "
    "véritable nature. Ses gènes de fléau sont restés profondément endormis, et rien ne le "
    "distingue extérieurement d'un simple civil. Ce choix mène directement à la création de ta "
    "fiche, sans étape supplémentaire.\n\n"
    "✅ Avantages :\n"
    "- Idéal pour un rp sans prise de tête, sans pouvoir, avec la possibilité de changer plus tard\n"
    "- Possède de l'énergie occulte, stable dans le corps, sans connaissance ni méthode d'utilisation\n"
    "- Peut apprendre à l'utiliser plus tard avec un sensei si le rp devient monotone\n"
    "- Bénéficie d'un boost physique naturel, supérieur à n'importe quel humain\n"
    "- Ce boost est encore renforcé si combiné à de l'énergie occulte\n\n"
    "❌ Inconvénients :\n"
    "- Très en retard et bien moins compétent qu'un exorciste au départ\n"
    "- Gènes de fléau totalement disparus, incapable de se soigner avec son énergie occulte\n"
    "- Doit apprendre le RCT classiquement, ce qui n'est pas donné à tout le monde\n"
    "- Quasi impossible d'obtenir un sort\n"
    "- Peut seulement se renforcer, enduire ses poings, son corps, ou des objets\n\n"
    "**⚔️ 2. Chez les exorcistes**\n"
    "Élevé parmi des sorciers, le corps de ton personnage s'est adapté pour devenir un véritable "
    "exorciste à part entière. Il suivra exactement le même parcours qu'un exorciste de sang : "
    "tirage d'un clan, réserve d'énergie occulte, et RCT. Seule différence, son sort sera toujours "
    "un Sort inné, jamais un sort héréditaire ou une restriction céleste, son sang n'étant pas "
    "celui du clan qui l'a recueilli.\n\n"
    "✅ Avantages :\n"
    "- Éduqué dès le plus jeune âge, dans un clan ou recueilli par un exorciste sans clan\n"
    "- Reçoit un véritable enseignement de l'énergie occulte\n"
    "- Sort inné garanti à 100%\n"
    "- Automatiquement classé Membre principal si intégré à un clan\n"
    "- Peut évoluer jusqu'à héritier voire chef de clan en se montrant utile\n\n"
    "❌ Inconvénients :\n"
    "- Ne peut jamais obtenir de restriction céleste ni de sort héréditaire\n"
    "- Toujours limité à un sort 100% inné\n"
    "- Chances réduites de devenir héritier ou chef de clan (mais pas impossible)\n\n"
    "**👹 3. Chez les fléaux**\n"
    "Élevé dans l'ombre des fléaux eux mêmes, ses gènes de fléau ont pris le dessus. Il évolue "
    "plus vite qu'un exorciste normal et n'a besoin d'aucun RCT pour se soigner, son énergie brute "
    "suffit à le régénérer. En contrepartie, il n'appartient à aucun clan et ne pourra jamais "
    "apprendre de sort héréditaire, seule la nature de son énergie occulte sera déterminée.\n\n"
    "✅ Avantages :\n"
    "- Peut régénérer un membre directement grâce à son énergie occulte\n"
    "- Apparence humaine facilitant la tromperie d'un adversaire\n"
    "- Peut développer des membres de fléau (tentacule, etc.) déployables à volonté\n\n"
    "❌ Inconvénients :\n"
    "- Usage du RCT totalement impossible\n"
    "- Corps de plus en plus marqué par les gènes de fléau\n\n"
    "**🌪️ 4. Livré à soi même**\n"
    "Personne ne l'a guidé. Personne ne lui a rien appris. Livré à lui même depuis l'enfance, il "
    "n'a eu que son corps pour survivre, et cela se voit : une force, une vitesse et une endurance "
    "largement supérieures à celles d'un hybride ordinaire. Son énergie occulte reste en revanche "
    "brute et inexploitée, faute d'avoir jamais eu de maître pour la canaliser en un véritable "
    "sort.\n\n"
    "✅ Avantages :\n"
    "- Force physique et sens surdéveloppés, proches d'une restriction céleste (mais inférieurs)\n"
    "- Nettement supérieur à un humain ou même un exorciste renforcé à l'énergie occulte\n"
    "- Plus agile, plus rapide, plus féroce\n\n"
    "❌ Inconvénients :\n"
    "- Aucune connaissance du monde extérieur, ou très peu\n"
    "- Aucune connaissance de ses propres facultés énergétiques\n"
    "- Incapable d'utiliser son énergie occulte ou d'avoir un sort tant qu'il n'a pas de mentor\n\n"
    "──────────────────\n\n"
    "*Clique sur l'environnement qui correspond à l'histoire de ton personnage.*"
)


def build_education_embed() -> discord.Embed:
    return discord.Embed(
        title="🧬 Lieu d'éducation",
        description=EDUCATION_DESCRIPTION,
        color=discord.Color.red(),
    )


class EducationView(discord.ui.View):
    """4 boutons du lieu d'éducation de l'hybride. Persistante (custom_id fixes).
    Placeholder pour l'instant : la vraie logique de chaque voie viendra dans une prochaine étape."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Chez les humains", emoji="🏙️", style=discord.ButtonStyle.secondary, custom_id="depart_edu_humains")
    async def humains(self, interaction: discord.Interaction, button: discord.ui.Button):
        # TODO: voie "humains" — passage direct à la création de la fiche.
        await interaction.response.send_message(
            f"{interaction.user.mention} a grandi 🏙️ chez les humains. La suite arrive bientôt.",
            ephemeral=False,
        )

    @discord.ui.button(label="Chez les exorcistes", emoji="⚔️", style=discord.ButtonStyle.primary, custom_id="depart_hybride_exorcistes")
    async def exorcistes(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Même tableau des clans que l'exorciste classique, mais avec le bouton de roll hybride.
        await interaction.response.send_message(
            embed=build_clan_table_embed(interaction.guild), view=ClanRollHybrideView(), ephemeral=False
        )

    @discord.ui.button(label="Chez les fléaux", emoji="👹", style=discord.ButtonStyle.danger, custom_id="depart_hybride_fleaux")
    async def fleaux(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Voie "fléaux" : pas de clan, réserve d'énergie occulte SANS nature, déclenchée directement.
        update_progress(interaction.user.id, camp="hybride", path="hybride_fleaux")
        await roll_and_send_reserve(interaction, interaction.user, interaction.guild, with_nature=False)

    @discord.ui.button(label="Livré à soi même", emoji="🌪️", style=discord.ButtonStyle.secondary, custom_id="depart_hybride_seul")
    async def livre(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Voie "livré à soi même" : réserve d'énergie occulte AVEC nature, déclenchée directement.
        update_progress(interaction.user.id, camp="hybride", path="hybride_seul")
        await roll_and_send_reserve(interaction, interaction.user, interaction.guild, with_nature=True)


class CampView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Exorciste", emoji="⚔️", style=discord.ButtonStyle.primary, custom_id="depart_camp_exorciste")
    async def exorciste(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await apply_camp_role(interaction, ROLE_EXORCISTE):
            return

        update_progress(interaction.user.id, camp="exorciste")

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
        update_progress(interaction.user.id, camp="hybride")
        await interaction.response.send_message(
            embed=build_education_embed(), view=EducationView(), ephemeral=False
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


class StartCreationView(discord.ui.View):
    """Bouton unique "Créer le Xème perso" de l'écran de sélection. Persistant, une variante par slot."""

    _LABELS = {1: "Créer le 1er perso", 2: "Créer le 2ème perso", 3: "Créer le 3ème perso"}

    def __init__(self, slot_number: int):
        super().__init__(timeout=None)
        self.slot_number = slot_number
        self.start.label = self._LABELS.get(slot_number, "Créer un perso")
        self.start.custom_id = f"depart_start_creation:{slot_number}"

    @discord.ui.button(label="Créer un perso", style=discord.ButtonStyle.success)
    async def start(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Slot lu depuis le custom_id (fiable même après un redémarrage du bot).
        slot_number = int(interaction.data["custom_id"].split(":", 1)[1])
        update_progress(interaction.user.id, slot_number=slot_number, guild_id=interaction.guild.id)

        # Embed de lecture (Étape 1), déplacé ici : comportement inchangé (envoi, 5s, bouton Commencer).
        embed = build_depart_embed()
        await interaction.response.send_message(embed=embed)
        await asyncio.sleep(5)
        await interaction.edit_original_response(embed=embed, view=DepartView())


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

        # Écran de sélection : une vue persistante par slot (custom_id depart_start_creation:{1,2,3}).
        for slot in (1, 2, 3):
            self.bot.add_view(StartCreationView(slot))

        self.bot.add_view(DepartView())
        self.bot.add_view(CampView())
        self.bot.add_view(EducationView())
        self.bot.add_view(ClanRollView())
        self.bot.add_view(ClanRollHybrideView())
        self.bot.add_view(ContinueEnergyView())
        self.bot.add_view(RewardContinueView())
        self.bot.add_view(ReserveClassView())
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

        # Écran de sélection de personnage (3 slots).
        rows = db.get_validated_characters(interaction.user.id, interaction.guild.id)
        by_slot = {row["slot_number"]: row for row in rows}

        slots = []
        for n in (1, 2, 3):
            row = by_slot.get(n)
            if row:
                camp = row["camp"] or ""
                clan = row["clan"]
                camp_clan = f"{camp} — {clan}" if clan else camp
                slots.append({"filled": True, "name": row["character_name"], "camp_clan": camp_clan})
            else:
                slots.append({"filled": False})

        path = _tmp_image_path("slots")
        generate_slots_image(interaction.user.name, slots, path)

        # 1er message : l'image de sélection seule, sans embed.
        await interaction.response.send_message(file=discord.File(path, filename="slots.png"))
        try:
            os.remove(path)
        except OSError:
            pass

        # Tous les slots pris : on s'arrête, aucun bouton.
        if all(s["filled"] for s in slots):
            await interaction.channel.send(
                "Tous tes emplacements sont pris. Tu ne peux pas créer de nouveau personnage pour le moment."
            )
            return

        # Sinon : bouton vers le premier slot libre.
        first_free = next(n for n in (1, 2, 3) if not slots[n - 1]["filled"])
        await interaction.channel.send(
            embed=discord.Embed(
                description="Prêt à créer ton personnage ? Clique sur le bouton ci-dessous.",
                color=discord.Color.blurple(),
            ),
            view=StartCreationView(first_free),
        )


async def setup(bot):
    await bot.add_cog(Depart(bot))
