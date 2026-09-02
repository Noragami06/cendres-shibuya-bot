import discord
from discord.ext import commands, tasks
from discord import app_commands
import asyncio
import io
import os
import random
import re
import uuid
from datetime import datetime, timedelta

import aiohttp
from PIL import Image

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
SANS_CLAN_ROLE_ID = 1539169032324907048           # Rôle "Sans clan" (barème : clan à 125 points)

# Rôles RCT attribués à la validation de la fiche (selon depart_character_progress.rct)
RCT_POSSEDE_ROLE_ID = 1522181335337402408
RCT_NON_POSSEDE_ROLE_ID = 1522181321961635964

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

GRADE_LABEL_TO_ROLE_ID = {name: rid for name, rid in GRADE_ROLES}

# Grades sélectionnables à l'étape "grade" de la fiche (Héritier exclu : réservé au sort héréditaire ;
# Chef exclu). Les 4 premiers sont à capacité 1 (disparaissent s'ils sont occupés), les 2 derniers
# sont illimités (toujours proposés).
GRADE_SINGLE_CAP = [
    ("Bras droit", 1521963034434601040),
    ("Bras gauche", 1521963034736726158),
    ("Bras droit héritier", 1521963040155766835),
    ("Bras gauche héritier", 1521963040809943120),
]
GRADE_UNLIMITED = [
    ("Membres principaux", 1521963104903233658),
    ("Membres secondaires", 1521963107918807140),
]

# Capacité de chaque grade (None = illimité). Sert de source unique si une limite est ajoutée un jour.
GRADE_CAPS = {
    "Bras droit": 1,
    "Bras gauche": 1,
    "Bras droit héritier": 1,
    "Bras gauche héritier": 1,
    "Membres principaux": None,
    "Membres secondaires": None,
}


def compute_vacant_grades(guild_id: int, clan_key: str):
    """Liste (label, role_id) des grades encore disponibles pour ce clan.
    La disponibilité est calculée UNIQUEMENT depuis validated_characters (jamais depuis les rôles
    Discord en direct), puisque les rôles ne sont attribués qu'à la validation de la fiche."""
    vacant = []
    for name, rid in GRADE_SINGLE_CAP:
        cap = GRADE_CAPS.get(name, 1)
        taken = db.count_validated_grade(guild_id, clan_key, name) if (guild_id and clan_key) else 0
        if cap is None or taken < cap:
            vacant.append((name, rid))
    vacant += GRADE_UNLIMITED
    return vacant


def compute_auto_grade_hybride(clan: str, guild_id: int = None) -> str:
    """Grade automatique d'un hybride élevé chez les exorcistes (aucune question posée).
    Renvoie "Membres principaux" tant que ce grade n'a pas atteint une éventuelle limite pour ce clan
    (illimité aujourd'hui), sinon "Membres secondaires" (cas théorique)."""
    principaux = "Membres principaux"
    cap = GRADE_CAPS.get(principaux)  # None aujourd'hui = illimité
    taken = db.count_validated_grade(guild_id, clan, principaux) if (guild_id and clan) else 0
    if cap is None or taken < cap:
        return principaux
    return "Membres secondaires"

# Utilisateur bénéficiant du flux spécial en message privé
SPECIAL_USER_ID = 396615332346855428

# ---------- Fiche de personnage ----------
FICHE_STAFF_CHANNEL_ID = 1521243474371022939       # salon où la fiche arrive pour validation
FICHE_STAFF_ROLE_ID = 1521229332075512039          # rôle staff (mention + seul autorisé à valider/refuser)
FICHE_VALIDATED_CHANNEL_ID = 1521817179954221066   # salon des fiches validées

PORTRAIT_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "portraits")
FICHE_TIMEOUT_MINUTES = 5
IMAGE_EXT_OK = (".jpg", ".jpeg", ".png")

FICHE_INTRO_TEXT = (
    "Tes rolls sont finis. Après ce message, ta fiche va commencer. Tu auras 5 minutes par question, "
    "au delà de ce délai ta fiche sera annulée, il te suffira de recliquer sur le bouton ci dessous "
    "pour recommencer.\n\n"
    "Avant de te lancer, prépare :\n"
    "- Une image\n"
    "- Un nom de famille (si tu es sans clan ou que tu as déserté ton clan)\n"
    "- Un prénom\n"
    "- Un âge\n\n"
    "Une fois tout prêt, clique sur le bouton pour commencer ta fiche."
)

QUESTION_TEXTS = {
    "nom": "Quel est le nom de famille de ton personnage ?",
    "prenom": "Quel est le prénom de ton personnage ?",
    "age": "Quel âge a ton personnage ?",
    "histoire_text": "Envoie l'histoire de ton personnage dans ce salon.",
    "portrait": "Envoie l'image de ton personnage (JPG ou PNG uniquement, en pièce jointe ou en lien direct, pas de GIF).",
}

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
    "classe_4": {"min": 10000, "max": 40000, "pct": 45},
    "classe_3": {"min": 40001, "max": 150000, "pct": 30},
    "classe_2": {"min": 150001, "max": 400000, "pct": 15},
    "classe_1": {"min": 400001, "max": 900000, "pct": 7},
    "classe_s": {"min": 900001, "max": 2000000, "pct": 3},
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
    {"key": "argent", "label": "Argent", "pct": 13.76, "category": "currency"},
    {"key": "xp", "label": "XP", "pct": 11.99, "category": "currency"},
    {"key": "reroll_clan", "label": "Reroll Clan", "pct": 10.44, "category": "reroll"},
    {"key": "reroll_sort", "label": "Reroll Sort", "pct": 9.10, "category": "reroll"},
    {"key": "reroll_energie_qte", "label": "Reroll Quantité d'énergie", "pct": 7.92, "category": "reroll"},
    {"key": "reroll_energie_nature", "label": "Reroll Nature d'énergie", "pct": 6.90, "category": "reroll"},
    {"key": "reroll_rct", "label": "Reroll RCT", "pct": 6.01, "category": "reroll"},
    {"key": "parchemin_territoire", "label": "Parchemin de territoire", "pct": 5.24, "category": "parchemin"},
    {"key": "parchemin_rct", "label": "Parchemin de RCT", "pct": 4.56, "category": "parchemin"},
    {"key": "parchemin_nature", "label": "Parchemin de nature d'énergie", "pct": 3.97, "category": "parchemin"},
    {"key": "relique_4", "label": "Relique de classe 4", "pct": 3.46, "category": "item"},
    {"key": "relique_3", "label": "Relique de classe 3", "pct": 3.02, "category": "item"},
    {"key": "relique_2", "label": "Relique de classe 2", "pct": 2.63, "category": "item"},
    {"key": "arme_4", "label": "Arme de classe 4", "pct": 2.29, "category": "item"},
    {"key": "arme_3", "label": "Arme de classe 3", "pct": 1.99, "category": "item"},
    {"key": "arme_2", "label": "Arme de classe 2", "pct": 1.74, "category": "item"},
    {"key": "arme_1", "label": "Arme de classe 1", "pct": 1.51, "category": "item"},
    {"key": "relique_1", "label": "Relique de classe 1", "pct": 1.32, "category": "item"},
    {"key": "arme_s", "label": "Arme de classe S", "pct": 1.15, "category": "item"},
    {"key": "relique_s", "label": "Relique de classe S", "pct": 1.00, "category": "item"},
]

# Tables spécifiques par chemin (pas de reroll_clan/reroll_sort/RCT quand le chemin n'en a pas).
REWARD_TABLE_HYBRIDE_EXORCISTE = [
    {"key": "argent", "label": "Argent", "pct": 15.14, "category": "currency"},
    {"key": "xp", "label": "XP", "pct": 13.19, "category": "currency"},
    {"key": "reroll_clan", "label": "Reroll Clan", "pct": 11.49, "category": "reroll"},
    {"key": "reroll_energie_qte", "label": "Reroll Quantité d'énergie", "pct": 8.71, "category": "reroll"},
    {"key": "reroll_energie_nature", "label": "Reroll Nature d'énergie", "pct": 7.59, "category": "reroll"},
    {"key": "reroll_rct", "label": "Reroll RCT", "pct": 6.61, "category": "reroll"},
    {"key": "parchemin_territoire", "label": "Parchemin de territoire", "pct": 5.76, "category": "parchemin"},
    {"key": "parchemin_rct", "label": "Parchemin de RCT", "pct": 5.02, "category": "parchemin"},
    {"key": "parchemin_nature", "label": "Parchemin de nature d'énergie", "pct": 4.37, "category": "parchemin"},
    {"key": "relique_4", "label": "Relique de classe 4", "pct": 3.81, "category": "item"},
    {"key": "relique_3", "label": "Relique de classe 3", "pct": 3.32, "category": "item"},
    {"key": "relique_2", "label": "Relique de classe 2", "pct": 2.89, "category": "item"},
    {"key": "arme_4", "label": "Arme de classe 4", "pct": 2.52, "category": "item"},
    {"key": "arme_3", "label": "Arme de classe 3", "pct": 2.19, "category": "item"},
    {"key": "arme_2", "label": "Arme de classe 2", "pct": 1.91, "category": "item"},
    {"key": "arme_1", "label": "Arme de classe 1", "pct": 1.66, "category": "item"},
    {"key": "relique_1", "label": "Relique de classe 1", "pct": 1.45, "category": "item"},
    {"key": "arme_s", "label": "Arme de classe S", "pct": 1.27, "category": "item"},
    {"key": "relique_s", "label": "Relique de classe S", "pct": 1.10, "category": "item"},
]

REWARD_TABLE_HYBRIDE_FLEAUX = [
    {"key": "xp", "label": "XP", "pct": 21.36, "category": "currency"},
    {"key": "reroll_energie_qte", "label": "Reroll Quantité d'énergie", "pct": 14.11, "category": "reroll"},
    {"key": "reroll_energie_nature", "label": "Reroll Nature d'énergie", "pct": 12.29, "category": "reroll"},
    {"key": "parchemin_territoire", "label": "Parchemin de territoire", "pct": 9.34, "category": "parchemin"},
    {"key": "parchemin_nature", "label": "Parchemin de nature d'énergie", "pct": 7.07, "category": "parchemin"},
    {"key": "relique_4", "label": "Relique de classe 4", "pct": 6.16, "category": "item"},
    {"key": "relique_3", "label": "Relique de classe 3", "pct": 5.38, "category": "item"},
    {"key": "relique_2", "label": "Relique de classe 2", "pct": 4.69, "category": "item"},
    {"key": "arme_4", "label": "Arme de classe 4", "pct": 4.08, "category": "item"},
    {"key": "arme_3", "label": "Arme de classe 3", "pct": 3.55, "category": "item"},
    {"key": "arme_2", "label": "Arme de classe 2", "pct": 3.10, "category": "item"},
    {"key": "arme_1", "label": "Arme de classe 1", "pct": 2.69, "category": "item"},
    {"key": "relique_1", "label": "Relique de classe 1", "pct": 2.35, "category": "item"},
    {"key": "arme_s", "label": "Arme de classe S", "pct": 2.05, "category": "item"},
    {"key": "relique_s", "label": "Relique de classe S", "pct": 1.78, "category": "item"},
]

REWARD_TABLE_HYBRIDE_SEUL = [
    {"key": "argent", "label": "Argent", "pct": 17.10, "category": "currency"},
    {"key": "xp", "label": "XP", "pct": 14.90, "category": "currency"},
    {"key": "reroll_energie_qte", "label": "Reroll Quantité d'énergie", "pct": 9.84, "category": "reroll"},
    {"key": "reroll_energie_nature", "label": "Reroll Nature d'énergie", "pct": 8.58, "category": "reroll"},
    {"key": "reroll_rct", "label": "Reroll RCT", "pct": 7.47, "category": "reroll"},
    {"key": "parchemin_territoire", "label": "Parchemin de territoire", "pct": 6.51, "category": "parchemin"},
    {"key": "parchemin_rct", "label": "Parchemin de RCT", "pct": 5.67, "category": "parchemin"},
    {"key": "parchemin_nature", "label": "Parchemin de nature d'énergie", "pct": 4.93, "category": "parchemin"},
    {"key": "relique_4", "label": "Relique de classe 4", "pct": 4.30, "category": "item"},
    {"key": "relique_3", "label": "Relique de classe 3", "pct": 3.75, "category": "item"},
    {"key": "relique_2", "label": "Relique de classe 2", "pct": 3.27, "category": "item"},
    {"key": "arme_4", "label": "Arme de classe 4", "pct": 2.85, "category": "item"},
    {"key": "arme_3", "label": "Arme de classe 3", "pct": 2.47, "category": "item"},
    {"key": "arme_2", "label": "Arme de classe 2", "pct": 2.16, "category": "item"},
    {"key": "arme_1", "label": "Arme de classe 1", "pct": 1.88, "category": "item"},
    {"key": "relique_1", "label": "Relique de classe 1", "pct": 1.64, "category": "item"},
    {"key": "arme_s", "label": "Arme de classe S", "pct": 1.43, "category": "item"},
    {"key": "relique_s", "label": "Relique de classe S", "pct": 1.25, "category": "item"},
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
    if reward_def.get("category") == "parchemin":
        # Quantité tirée dès l'offre pour que la carte et le gain réel correspondent.
        amount = random.randint(1, 5)
        return {"key": reward_def["key"], "name": reward_def["label"], "qty": f"x{amount}", "amount": amount}
    return {"key": reward_def["key"], "name": reward_def["label"], "qty": "x1", "amount": None}


def pick_two_distinct_rewards(table=REWARD_TABLE, exclude_keys: set = None):
    """Tire deux récompenses différentes selon les poids de la table fournie.
    exclude_keys : clés de récompense à retirer du pool avant tirage (ex: {"reroll_clan"})."""
    if exclude_keys:
        table = [r for r in table if r["key"] not in exclude_keys]
    keys = [r["key"] for r in table]
    weights = [r["pct"] for r in table]
    first_key = random.choices(keys, weights=weights, k=1)[0]
    remaining = [(k, w) for k, w in zip(keys, weights) if k != first_key]
    second_key = random.choices([k for k, w in remaining], weights=[w for k, w in remaining], k=1)[0]
    first_def = next(r for r in table if r["key"] == first_key)
    second_def = next(r for r in table if r["key"] == second_key)
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
def get_clan_member_count(guild: discord.Guild, clan_key: str) -> int:
    """Compte les personnages VALIDÉS d'un clan (réels ET virtuels, TOUS slots confondus) depuis la
    base — plus depuis les rôles Discord, car les slots 2/3 n'ont que des rôles virtuels enregistrés.
    Reçoit désormais la CLÉ du clan (telle que stockée dans validated_characters.clan), pas un role_id."""
    guild_id = guild.id if guild else None
    return db.count_clan_members(guild_id, clan_key)


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

    count = get_clan_member_count(guild, clan_key)
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
        count = get_clan_member_count(guild, clan_key)
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

    update_progress(member.id, eo_classe=eo_classe, eo_value=value, nature=nature)

    # 3e message : bouton "Continuer" vers l'étape récompense, propre au chemin.
    path = get_progress(member.id).get("path")
    if path == "hybride_fleaux":
        continue_view = ContinueRecompenseFleauxView()
    elif path == "hybride_seul":
        continue_view = ContinueRecompenseSeulView()
    else:
        continue_view = RewardContinueView()
    await channel.send(
        embed=discord.Embed(
            description="Clique pour continuer ton parcours.", color=discord.Color.blurple()
        ),
        view=continue_view,
    )


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


# =====================================================================
# ÉTAPE RCT (point 5) — construite à partir de la spec du parcours.
# =====================================================================
RCT_DESCRIPTION = (
    "Le moment est venu de savoir si ton personnage maîtrise le **RCT** (Reverse Cursed Technique) "
    "dès sa création. C'est une aptitude extrêmement rare.\n\n"
    "Clique sur le bouton ci-dessous pour tenter ta chance."
)

# Textes de résultat (inventés — à ajuster librement).
RCT_SUCCESS_TEXT = "✅ Incroyable ! {mention} maîtrise le **RCT** dès sa création, une aptitude rarissime."
RCT_FAILURE_TEXT = "❌ {mention} ne maîtrise pas le **RCT** pour l'instant. Il faudra l'apprendre plus tard en jeu."


def build_rct_embed() -> discord.Embed:
    return discord.Embed(title="❤️‍🩹 Tentative de RCT", description=RCT_DESCRIPTION, color=discord.Color.red())


async def send_rct_step(channel, member: discord.Member):
    """Envoie l'embed de l'étape RCT avec le bouton Roll RCT. Appelé après CHAQUE récompense."""
    await channel.send(embed=build_rct_embed(), view=RollRctView(member.id))


class RollRctView(discord.ui.View):
    """Bouton "Roll RCT" (conteneur simple, custom_id par joueur -> géré par le listener)."""

    def __init__(self, user_id: int):
        super().__init__(timeout=None)
        self.add_item(discord.ui.Button(
            label="Roll RCT", emoji="🎲", style=discord.ButtonStyle.primary,
            custom_id=f"depart_roll_rct:{user_id}",
        ))


class RerollRctView(discord.ui.View):
    """Bouton "Reroll (X)" affiché après un échec s'il reste des charges (custom_id par joueur)."""

    def __init__(self, user_id: int, charges: int):
        super().__init__(timeout=None)
        self.add_item(discord.ui.Button(
            label=f"Reroll ({charges})", emoji="🔁", style=discord.ButtonStyle.primary,
            custom_id=f"depart_reroll_rct:{user_id}",
        ))


def build_vers_la_fiche_embed() -> discord.Embed:
    return discord.Embed(title="📜 Vers la fiche", description=FICHE_INTRO_TEXT, color=discord.Color.blurple())


async def send_vers_la_fiche(channel, member: discord.Member):
    """Enchaîne directement vers l'écran "Vers la fiche" (chemins sans RCT / sans tirage)."""
    update_progress(member.id, origin_channel_id=channel.id)
    await channel.send(embed=build_vers_la_fiche_embed(), view=FaireFicheView(member.id))


class ContinueFicheView(discord.ui.View):
    """Bouton "Continuer" vers l'étape fiche après le RCT (custom_id fixe, persistant via add_view)."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Continuer", emoji="➡️", style=discord.ButtonStyle.success, custom_id="depart_continuer_fiche")
    async def continuer(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Mémorise le salon d'origine (où arriveront les questions) puis envoie l'écran "Vers la fiche".
        update_progress(interaction.user.id, origin_channel_id=interaction.channel.id)
        await interaction.response.send_message(
            embed=build_vers_la_fiche_embed(), view=FaireFicheView(interaction.user.id)
        )


class ContinueFicheDirectView(discord.ui.View):
    """Bouton "Continuer" direct vers la fiche pour les chemins Humain / Hybride chez les humains
    (aucun clan/sort/réserve/nature/RCT/récompense). custom_id fixe, persistant via add_view."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Continuer", emoji="➡️", style=discord.ButtonStyle.success, custom_id="depart_continuer_fiche_direct")
    async def continuer(self, interaction: discord.Interaction, button: discord.ui.Button):
        update_progress(interaction.user.id, origin_channel_id=interaction.channel.id)
        await interaction.response.send_message(
            embed=build_vers_la_fiche_embed(), view=FaireFicheView(interaction.user.id)
        )


# =====================================================================
# FICHE DE PERSONNAGE
# =====================================================================
def has_clan_from_progress(progress: dict) -> bool:
    clan = progress.get("clan")
    return clan is not None and clan != "sans_clan"


# =====================================================================
# BARÈME : points de stats liés aux rôles (camp / clan / grade)
# =====================================================================
def resolve_role_point_ids(camp, clan, grade):
    """Résout les role_id (camp, clan/sans-clan, grade) d'un personnage à partir de ses attributs de
    fiche (camp, clan, grade) — mêmes valeurs que celles attribuées en rôles réels/virtuels. Un clan
    absent ('sans_clan' ou None) donne le rôle Sans clan. grade None -> pas de grade."""
    camp_role_id = {"exorciste": ROLE_EXORCISTE, "hybride": ROLE_HYBRIDE,
                    "humain": ROLE_HUMAIN}.get((camp or "").lower())
    if clan and clan != "sans_clan":
        info = db.load_clan_state()["clans"].get(clan)
        clan_role_id = info["role_id"] if info else None
    else:
        clan_role_id = SANS_CLAN_ROLE_ID
    grade_role_id = GRADE_LABEL_TO_ROLE_ID.get(grade) if grade else None
    return camp_role_id, clan_role_id, grade_role_id


async def sync_role_points(character_id: int, category: str, new_role_id, bot=None):
    """Wrapper asynchrone de db.sync_role_points (les appels du projet utilisent `await`). La logique
    (delta + dette + mémorisation du rôle de référence) est atomique côté base. Si `bot` est fourni et
    qu'une dette a été NOUVELLEMENT appliquée (points repris au-delà des points libres), notifie le
    joueur par MP (échec silencieux si ses MP sont fermés)."""
    remaining_debt = db.sync_role_points(character_id, category, new_role_id)
    if bot is not None and remaining_debt and remaining_debt > 0:
        await _notify_points_debt(bot, character_id, remaining_debt)


async def _notify_points_debt(bot, character_id: int, remaining_debt: int):
    """Envoie un MP au propriétaire du personnage pour l'informer d'une dette de points appliquée.
    Silencieux si le joueur a fermé ses MP ou si l'utilisateur est introuvable."""
    char = db.get_character(character_id) if hasattr(db, "get_character") else None
    if char is None:
        with db.get_connection() as conn:
            char = conn.execute(
                "SELECT user_id FROM validated_characters WHERE id = ?", (character_id,)
            ).fetchone()
    if char is None:
        return
    user_id = char["user_id"]
    try:
        user = bot.get_user(user_id) or await bot.fetch_user(user_id)
        if user is not None:
            await user.send(
                f"⚠️ Suite à une modification de rôle, une dette de {remaining_debt} points a été "
                "appliquée. Elle sera déduite de ton prochain gain de points de stats."
            )
    except (discord.Forbidden, discord.HTTPException):
        pass  # MP fermés / utilisateur inaccessible : on ignore silencieusement


async def grant_initial_role_points(character_id: int, camp, clan, grade):
    """Grant initial (ou re-synchronisation) des points de stats des 3 catégories pour un personnage,
    d'après ses attributs. Ne fait rien pour un role_id absent du barème (cf. db.sync_role_points)."""
    camp_rid, clan_rid, grade_rid = resolve_role_point_ids(camp, clan, grade)
    if camp_rid:
        await sync_role_points(character_id, "camp", camp_rid)
    if clan_rid:
        await sync_role_points(character_id, "clan", clan_rid)
    if grade_rid:  # pas de grade pour un Humain / hybride sans clan
        await sync_role_points(character_id, "grade", grade_rid)


async def backfill_role_points(guild):
    """Applique le grant initial à tous les personnages validés du serveur qui n'ont pas encore de
    ligne dans character_role_point_grants, d'après leurs attributs actuels (camp/clan/grade en base,
    qui sont la source de vérité de leurs rôles réels ou virtuels). Déclenché une fois au démarrage :
    sans danger à relancer, il ne traite que les personnages absents de character_role_point_grants."""
    to_grant = db.characters_without_role_point_grant()
    if not to_grant:
        return
    for character_id in to_grant:
        with db.get_connection() as conn:
            row = conn.execute(
                "SELECT guild_id, camp, clan, grade FROM validated_characters WHERE id = ?",
                (character_id,),
            ).fetchone()
        if row is None or (guild is not None and row["guild_id"] != guild.id):
            continue  # traité lors de l'itération de son propre serveur
        await grant_initial_role_points(character_id, row["camp"], row["clan"], row["grade"])
        print(f"🔍 [barème] Points de rôle rattrapés pour le personnage {character_id}.")


def get_fiche_steps(progress: dict) -> list:
    """Étapes du questionnaire de fiche pour ce joueur.
    - "nom" seulement si pas de clan (avec clan, le nom = celui du clan).
    - "grade" seulement pour l'EXORCISTE CLASSIQUE avec un clan précis ET non-héritier
      (l'héritier a déjà son grade). L'hybride-exorciste n'a PAS de question de grade :
      son grade est déterminé automatiquement (compute_auto_grade_hybride) au tirage du clan."""
    has_clan = has_clan_from_progress(progress)
    path = progress.get("path")
    steps = []
    if not has_clan:
        steps.append("nom")
    steps += ["prenom", "age", "histoire_ask"]
    if path == "exorciste" and has_clan and not progress.get("sera_heritier"):
        steps.append("grade")
    steps.append("portrait")
    return steps


def next_fiche_stage(current: str, progress: dict):
    """Étape suivante. 'histoire_text' n'est pas dans la liste : après elle on va à ce qui suit
    'histoire_ask' (grade ou portrait)."""
    steps = get_fiche_steps(progress)
    if current == "histoire_text":
        idx = steps.index("histoire_ask")
        return steps[idx + 1] if idx + 1 < len(steps) else None
    if current in steps:
        idx = steps.index(current)
        return steps[idx + 1] if idx + 1 < len(steps) else None
    return None


def _fiche_deadline_iso() -> str:
    return (datetime.utcnow() + timedelta(minutes=FICHE_TIMEOUT_MINUTES)).isoformat()


def _is_fiche_staff(member) -> bool:
    return any(role.id == FICHE_STAFF_ROLE_ID for role in getattr(member, "roles", []))


def compute_recommended_grade(guild, member, clan_role_id) -> str:
    if clan_role_id is None:
        return "Aucun (sans clan)"
    if member is None:
        return "Membre principal ou secondaire (à discuter avec le staff en MP)"
    role_ids = {role.id for role in member.roles}
    # Les 6 premiers grades nommés (Chef, Bras droit/gauche, Héritier, Bras droit/gauche héritier).
    for name, rid in GRADE_ROLES[:6]:
        if rid in role_ids:
            return name
    if MEMBRES_PRINCIPAUX_ROLE_ID in role_ids:
        return "Membre principal"
    return "Membre principal ou secondaire (à discuter avec le staff en MP)"


# ---------- Vues (conteneurs simples, custom_id par joueur -> listener) ----------
class FaireFicheView(discord.ui.View):
    def __init__(self, user_id: int):
        super().__init__(timeout=None)
        self.add_item(discord.ui.Button(
            label="Faire ma fiche", emoji="📝", style=discord.ButtonStyle.success,
            custom_id=f"depart_start_fiche:{user_id}",
        ))


class HistoireAskView(discord.ui.View):
    def __init__(self, user_id: int):
        super().__init__(timeout=None)
        self.add_item(discord.ui.Button(
            label="Oui", style=discord.ButtonStyle.success, custom_id=f"depart_histoire_oui:{user_id}",
        ))
        self.add_item(discord.ui.Button(
            label="Non", style=discord.ButtonStyle.secondary, custom_id=f"depart_histoire_non:{user_id}",
        ))


class GradeSelectView(discord.ui.View):
    """Menu déroulant des grades vacants d'un clan (étape "grade" de la fiche).
    custom_id par joueur -> géré par le listener on_interaction."""

    def __init__(self, user_id: int, vacant):
        super().__init__(timeout=None)
        options = [discord.SelectOption(label=name, value=name) for name, rid in vacant][:25]
        self.add_item(discord.ui.Select(
            placeholder="Choisis ton grade...",
            min_values=1, max_values=1, options=options,
            custom_id=f"depart_grade_select:{user_id}",
        ))


async def handle_grade_select(interaction: discord.Interaction, custom_id: str):
    owner_id = int(custom_id.split(":", 1)[1])
    if interaction.user.id != owner_id:
        await interaction.response.send_message("Ce menu ne t'appartient pas.", ephemeral=True)
        return

    progress = get_progress(interaction.user.id)
    if progress.get("fiche_status") != "in_progress" or progress.get("fiche_stage") != "grade":
        await interaction.response.send_message("Cette étape n'est plus active.", ephemeral=True)
        return

    grade = interaction.data.get("values", [None])[0]
    update_progress(interaction.user.id, grade_choisi=grade)

    await interaction.response.defer()
    await _delete_fiche_question(interaction.client, progress)

    nxt = next_fiche_stage("grade", progress)  # -> portrait
    update_progress(interaction.user.id, fiche_stage=nxt, fiche_deadline=_fiche_deadline_iso())
    await interaction.followup.send("Enregistré.", ephemeral=True)
    await send_fiche_question(interaction.client, interaction.user.id)


class FicheReviewView(discord.ui.View):
    def __init__(self, user_id: int, slot_number: int):
        super().__init__(timeout=None)
        self.add_item(discord.ui.Button(
            label="Validé", emoji="✅", style=discord.ButtonStyle.success,
            custom_id=f"depart_fiche_valide:{user_id}:{slot_number}",
        ))
        self.add_item(discord.ui.Button(
            label="Refusé", emoji="❌", style=discord.ButtonStyle.danger,
            custom_id=f"depart_fiche_refuse:{user_id}",
        ))


# ---------- Envoi des questions ----------
async def _delete_fiche_question(client, progress: dict):
    """Supprime le dernier message de question du bot (si connu et accessible)."""
    channel = client.get_channel(progress.get("origin_channel_id"))
    qid = progress.get("fiche_question_msg_id")
    if channel and qid:
        try:
            msg = await channel.fetch_message(qid)
            await msg.delete()
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass


async def send_fiche_question(client, user_id: int):
    """Envoie la question correspondant au fiche_stage courant dans le salon d'origine."""
    progress = get_progress(user_id)
    channel = client.get_channel(progress.get("origin_channel_id"))
    if channel is None:
        return
    stage = progress.get("fiche_stage")

    if stage == "histoire_ask":
        embed = discord.Embed(
            description="Souhaites tu ajouter une histoire à ton personnage ? (facultatif)",
            color=discord.Color.blurple(),
        )
        msg = await channel.send(embed=embed, view=HistoireAskView(user_id))
    elif stage == "grade":
        clan_key = progress.get("clan")
        guild_id = getattr(getattr(channel, "guild", None), "id", None)
        vacant = compute_vacant_grades(guild_id, clan_key)
        embed = discord.Embed(
            description=f"Quel grade souhaites tu occuper dans le clan **{(clan_key or '').capitalize()}** ?",
            color=discord.Color.blurple(),
        )
        msg = await channel.send(embed=embed, view=GradeSelectView(user_id, vacant))
    else:
        text = QUESTION_TEXTS.get(stage)
        if text is None:
            return
        msg = await channel.send(text)

    update_progress(user_id, fiche_question_msg_id=msg.id)


# ---------- Handlers de boutons ----------
async def handle_start_fiche(interaction: discord.Interaction, custom_id: str):
    owner_id = int(custom_id.split(":", 1)[1])
    if interaction.user.id != owner_id:
        await interaction.response.send_message("Ce bouton ne t'appartient pas.", ephemeral=True)
        return

    progress = get_progress(interaction.user.id)
    steps = get_fiche_steps(progress)

    # Repart toujours de zéro (même après annulation ou refus).
    update_progress(
        interaction.user.id,
        nom=None, prenom=None, age=None, histoire=None, portrait_path=None, grade_choisi=None,
        fiche_status="in_progress", fiche_stage=steps[0],
        fiche_deadline=_fiche_deadline_iso(),
        origin_channel_id=interaction.channel.id,
    )
    await interaction.response.send_message("C'est parti ! Réponds aux questions ci-dessous.", ephemeral=True)
    await send_fiche_question(interaction.client, interaction.user.id)


async def handle_histoire(interaction: discord.Interaction, custom_id: str, choice: str):
    owner_id = int(custom_id.split(":", 1)[1])
    if interaction.user.id != owner_id:
        await interaction.response.send_message("Ce bouton ne t'appartient pas.", ephemeral=True)
        return

    progress = get_progress(interaction.user.id)
    if progress.get("fiche_status") != "in_progress" or progress.get("fiche_stage") != "histoire_ask":
        await interaction.response.send_message("Cette étape n'est plus active.", ephemeral=True)
        return

    await interaction.response.defer()
    await _delete_fiche_question(interaction.client, progress)  # retire l'embed Oui/Non

    # L'histoire n'est plus capturée pendant le questionnaire : que le joueur clique Oui ou Non, on
    # enregistre histoire = NULL et on passe DIRECTEMENT à l'étape suivante (grade ou portrait), sans
    # jamais passer par le stage "histoire_text". Sur "Oui", on lui rappelle simplement de la poster
    # dans ce salon une fois sa fiche validée.
    nxt = next_fiche_stage("histoire_ask", progress)  # -> grade ou portrait
    update_progress(interaction.user.id, histoire=None, fiche_stage=nxt, fiche_deadline=_fiche_deadline_iso())
    if choice == "oui":
        await interaction.followup.send("Une fois ta fiche validée, poste ton histoire dans ce salon.")
    else:
        await interaction.followup.send("Très bien.", ephemeral=True)

    await send_fiche_question(interaction.client, interaction.user.id)


# ---------- Réponses texte / image ----------
async def handle_fiche_text_answer(client, message: discord.Message, progress: dict, stage: str):
    uid = message.author.id

    if stage == "age":
        content = message.content.strip()
        if not content.isdigit() or int(content) <= 0:
            # Âge invalide : on ne supprime rien, on ne change pas d'étape, la deadline n'est PAS reset.
            await message.channel.send("Merci d'entrer un âge valide (nombre entier).")
            return
        update_progress(uid, age=int(content))
    elif stage == "nom":
        update_progress(uid, nom=message.content.strip())
    elif stage == "prenom":
        update_progress(uid, prenom=message.content.strip())
    elif stage == "histoire_text":
        update_progress(uid, histoire=message.content.strip())

    await _delete_fiche_question(client, progress)
    try:
        await message.delete()
    except (discord.Forbidden, discord.HTTPException):
        pass

    nxt = next_fiche_stage(stage, progress)
    if nxt is None:
        await finalize_fiche(client, uid)
        return
    update_progress(uid, fiche_stage=nxt, fiche_deadline=_fiche_deadline_iso())
    await send_fiche_question(client, uid)


async def _head_content_type(url: str):
    try:
        async with aiohttp.ClientSession() as session:
            async with session.head(url, allow_redirects=True, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                return (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
    except Exception:
        return None


async def _download_image_bytes(url: str):
    """Télécharge l'image et retourne ses bytes bruts, ou None en cas d'échec."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                if resp.status != 200:
                    return None
                return await resp.read()
    except Exception:
        return None


def compress_portrait(image_bytes: bytes, max_dimension: int = 1024, quality: int = 85) -> bytes:
    """Redimensionne (max 1024 px) et recompresse l'image en JPEG pour limiter le poids sur disque."""
    img = Image.open(io.BytesIO(image_bytes))
    img = img.convert("RGB")  # nécessaire pour sauvegarder en JPEG même si l'original est PNG avec transparence
    img.thumbnail((max_dimension, max_dimension), Image.LANCZOS)
    output = io.BytesIO()
    img.save(output, format="JPEG", quality=quality, optimize=True)
    return output.getvalue()


async def _resolve_portrait_url(message: discord.Message):
    """Retourne une URL d'image JPG/PNG valide (pièce jointe ou lien), ou None. Refuse les GIF."""
    for att in message.attachments:
        name = (att.filename or "").lower()
        if name.endswith(".gif"):
            return None
        if name.endswith(IMAGE_EXT_OK):
            return att.url

    match = re.search(r"https?://\S+", message.content or "")
    if match:
        link = match.group(0)
        low = link.lower().split("?")[0]
        if low.endswith(".gif"):
            return None
        if low.endswith(IMAGE_EXT_OK):
            return link
        ctype = await _head_content_type(link)
        if ctype in ("image/jpeg", "image/png"):
            return link
    return None


async def handle_fiche_portrait(client, message: discord.Message, progress: dict):
    uid = message.author.id
    url = await _resolve_portrait_url(message)

    if url is None:
        await message.channel.send(
            "Format non accepté, envoie une image en JPG ou PNG (pièce jointe ou lien direct), pas de GIF."
        )
        update_progress(uid, fiche_deadline=_fiche_deadline_iso())  # laisse le temps de rectifier
        return

    os.makedirs(PORTRAIT_DIR, exist_ok=True)
    slot = progress.get("slot_number") or 1

    raw = await _download_image_bytes(url)
    if raw is None:
        await message.channel.send("Impossible de télécharger l'image, réessaie avec un autre fichier ou lien.")
        update_progress(uid, fiche_deadline=_fiche_deadline_iso())
        return

    # Compression + conversion JPEG systématique : le portrait est TOUJOURS sauvegardé en .jpg.
    try:
        jpeg_bytes = compress_portrait(raw)
    except Exception:
        await message.channel.send("Cette image est illisible ou corrompue, réessaie avec un autre fichier.")
        update_progress(uid, fiche_deadline=_fiche_deadline_iso())
        return

    dest = os.path.join(PORTRAIT_DIR, f"{uid}_{slot}.jpg")
    with open(dest, "wb") as f:
        f.write(jpeg_bytes)

    update_progress(uid, portrait_path=dest)
    await _delete_fiche_question(client, progress)
    try:
        await message.delete()
    except (discord.Forbidden, discord.HTTPException):
        pass

    await finalize_fiche(client, uid)


# ---------- Construction & envoi de la fiche ----------
def _fiche_portrait_filename(uid: int, slot: int) -> str:
    return f"portrait_{uid}_{slot}.jpg"


ORIGINE_LABELS = {
    "hybride_exorciste": "Chez les exorcistes",
    "hybride_fleaux": "Chez les fléaux",
    "hybride_seul": "Livré à soi-même",
    "hybride_humains": "Chez les humains",
}

# Libellé du sous-type d'hybride pour l'affichage "Hybride (…)".
HYBRIDE_TYPE_LABELS = {
    "humains": "Humain",
    "exorciste": "Exorciste",
    "fleaux": "Fléaux",
    "seul": "Livré à soi même",
}


def _hybride_type_of(progress: dict):
    """Sous-type d'hybride : colonne dédiée si présente, sinon déduit du chemin (hybride_xxx)."""
    ht = progress.get("hybride_type")
    if ht:
        return ht
    path = progress.get("path") or ""
    if path.startswith("hybride_"):
        return path[len("hybride_"):]
    return None


def format_camp_label(camp, hybride_type=None) -> str:
    """Affichage du camp : "Hybride (Type)" pour un hybride, sinon "Exorciste"/"Humain"."""
    camp = (camp or "").lower()
    if camp == "hybride":
        label = HYBRIDE_TYPE_LABELS.get(hybride_type)
        return f"Hybride ({label})" if label else "Hybride"
    if camp == "exorciste":
        return "Exorciste"
    if camp == "humain":
        return "Humain"
    return camp.capitalize() if camp else "—"


def format_camp_display(progress: dict) -> str:
    return format_camp_label(progress.get("camp"), _hybride_type_of(progress))


def build_fiche_embed(progress: dict, guild, member, uid: int,
                      statut_display: str = "🕒 En attente de validation",
                      valide_par_display: str = "—") -> discord.Embed:
    clan_key = progress.get("clan")
    has_clan = has_clan_from_progress(progress)
    clan_display = clan_key.capitalize() if has_clan else "Sans clan"
    nom_final = clan_key.capitalize() if has_clan else (progress.get("nom") or "—")
    prenom = progress.get("prenom") or "—"
    age = progress.get("age")
    age_display = str(age) if age is not None else "—"
    slot = progress.get("slot_number") or 1
    camp = format_camp_display(progress)

    # Grade : Héritier si désigné, sinon le grade choisi via la fiche, sinon selon présence d'un clan.
    if progress.get("sera_heritier"):
        grade = "Héritier"
    elif progress.get("grade_choisi"):
        grade = progress.get("grade_choisi")
    elif has_clan:
        grade = "À définir avec le staff"
    else:
        grade = "Aucun (sans clan)"

    sort_key = progress.get("sort")
    sort_display = SORT_LABELS.get(sort_key, "Aucun") if sort_key else "Aucun"
    nature = progress.get("nature")
    nature_display = NATURE_DISPLAY_NAMES.get(nature, "Aucune") if nature else "Aucune"

    eo_classe = progress.get("eo_classe")
    eo_value = progress.get("eo_value")
    if eo_classe and eo_value is not None:
        reserve_display = f"Classe {eo_classe.replace('classe_', '').upper()} — {eo_value:,} EO"
    else:
        reserve_display = "Aucune"

    rct_display = "Maîtrisé" if progress.get("rct") else "Non maîtrisé"
    reco_display = progress.get("recompense") or "Aucune"
    histoire = progress.get("histoire")

    # Chemins Humain / Hybride chez les humains : aucun tirage n'a eu lieu -> "///" partout,
    # sans tenter de calculer un grade/une réserve inexistants (évite tout crash sur des champs vides).
    no_roll = progress.get("path") in ("humain", "hybride_humains") or (
        progress.get("clan") is None and progress.get("eo_classe") is None
    )
    if no_roll:
        clan_display = grade = sort_display = "///"
        nature_display = reserve_display = rct_display = reco_display = "///"

    # Valeurs de mise en page (tout passe par la description, aucun field).
    slot_number = slot
    camp_display = camp
    camp_key = (progress.get("camp") or "").lower()
    grade_display = grade
    recompense_display = reco_display
    origine_display = ORIGINE_LABELS.get(progress.get("path"), "—")
    date_creation = datetime.utcnow().strftime("%d/%m/%Y")
    nom_fichier_image = _fiche_portrait_filename(uid, slot)

    description = (
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"✦ IDENTITÉ ✦\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"**Prénom :** {prenom}\n"
        f"**Nom :** {nom_final}\n"
        f"**Âge :** {age_display}\n"
        f"**Emplacement :** Slot {slot_number}\n"
        f"**Camp :** {camp_display}\n"
    )

    if camp_key == "hybride":
        description += f"**Origine :** {origine_display}\n"

    description += (
        f"\n━━━━━━━━━━━━━━━━━━━━\n"
        f"✦ APPARTENANCE ✦\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"**Clan :** {clan_display}\n"
        f"**Grade :** {grade_display}\n"
        f"\n━━━━━━━━━━━━━━━━━━━━\n"
        f"✦ POUVOIRS ✦\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"**Sort :** {sort_display}\n"
        f"**Nature :** {nature_display}\n"
        f"**Réserve :** {reserve_display}\n"
        f"**RCT :** {rct_display}\n"
        f"\n━━━━━━━━━━━━━━━━━━━━\n"
        f"✦ RÉCOMPENSE DE DÉPART ✦\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"**Récompense :** {recompense_display}\n"
    )

    description += (
        f"\n━━━━━━━━━━━━━━━━━━━━\n"
        f"✦ STATUT ✦\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"**Statut :** {statut_display}\n"
        f"**Validé par :** {valide_par_display}\n"
        f"**Créée le :** {date_creation}\n"
    )

    embed = discord.Embed(
        title="📜 Fiche de personnage",
        description=description,
        color=discord.Color.from_rgb(201, 165, 92),
    )
    portrait_path = progress.get("portrait_path")
    if portrait_path and os.path.exists(portrait_path):
        embed.set_image(url=f"attachment://{nom_fichier_image}")
    embed.set_footer(text="Cendres de Shibuya — Fiche de personnage")

    return embed


async def finalize_fiche(client, uid: int):
    progress = get_progress(uid)
    guild = client.get_guild(progress.get("guild_id")) if progress.get("guild_id") else None
    member = guild.get_member(uid) if guild else None

    update_progress(uid, fiche_status="pending_review", fiche_stage=None)

    embed = build_fiche_embed(progress, guild, member, uid)
    slot = progress.get("slot_number") or 1
    portrait_path = progress.get("portrait_path")
    filename = _fiche_portrait_filename(uid, slot)

    member_mention = member.mention if member else f"<@{uid}>"
    content = f"<@&{FICHE_STAFF_ROLE_ID}> Fiche de {member_mention}"
    view = FicheReviewView(uid, slot)

    staff_channel = client.get_channel(FICHE_STAFF_CHANNEL_ID)
    if staff_channel:
        if portrait_path and os.path.exists(portrait_path):
            await staff_channel.send(content=content, embed=embed, file=discord.File(portrait_path, filename=filename), view=view)
        else:
            await staff_channel.send(content=content, embed=embed, view=view)

        # Histoire envoyée en message séparé (texte brut), pour ne pas alourdir l'embed.
        histoire = progress.get("histoire")
        if histoire:
            await staff_channel.send(f"📖 Histoire de {member_mention} :\n{histoire}")

    origin = client.get_channel(progress.get("origin_channel_id"))
    if origin:
        await origin.send(f"{member_mention} Ta fiche a été envoyée au staff pour validation.")


async def assign_validation_roles(guild: discord.Guild, member: discord.Member, progress: dict,
                                  slot_number: int, heir_conflict: bool) -> None:
    """Attribue les VRAIS rôles Discord au moment de la validation (camp, clan, grade/héritier, RCT)
    — UNIQUEMENT pour le SLOT 1. Ne lève jamais : en cas d'erreur, logue et continue (l'insertion en
    base ne doit pas être bloquée).

    heir_conflict est déterminé EN AMONT depuis la base (cf. handle_fiche_valide) : s'il vaut True et
    que le personnage devait être héritier, on pose 'Membres principaux' au lieu du rôle Héritier.

    Le comptage / la fermeture de clan ne sont plus faits ici : ils le sont APRÈS l'insertion en base
    dans handle_fiche_valide (comptage basé sur validated_characters, tous slots confondus)."""
    if slot_number != 1:
        # Ce personnage (slot 2/3) a des rôles uniquement enregistrés en base, jamais attribués sur
        # Discord, pour éviter les rôles contradictoires avec le slot 1 du même joueur.
        return
    if guild is None or member is None:
        print(f"[fiche] Rôles non attribués : guild/member introuvable (uid={member.id if member else '?'}).")
        return

    camp = (progress.get("camp") or "").lower()
    path = progress.get("path")
    clan_key = progress.get("clan")

    roles_to_add = []

    def collect(role_id):
        role = guild.get_role(role_id)
        if role is None:
            print(f"[fiche] Rôle introuvable sur le serveur : {role_id}")
        else:
            roles_to_add.append(role)

    # 1) Rôle de camp
    camp_role_id = {"exorciste": ROLE_EXORCISTE, "hybride": ROLE_HYBRIDE, "humain": ROLE_HUMAIN}.get(camp)
    if camp_role_id:
        collect(camp_role_id)

    # 2) Rôle de clan (exorciste classique ou hybride chez les exorcistes, avec un clan précis)
    if path in ("exorciste", "hybride_exorciste") and clan_key and clan_key != "sans_clan":
        info = load_clan_state()["clans"].get(clan_key)
        clan_role_id = info["role_id"] if info else None
        if clan_role_id:
            # Exception "Reroll Clan" : si le membre porte DÉJÀ le rôle du clan (attribué au moment
            # du reroll), on ne le réattribue pas.
            already_has_clan = any(r.id == clan_role_id for r in member.roles)
            if not already_has_clan:
                collect(clan_role_id)
                collect(CLAN_MEMBER_ROLE_ID)
            # Grade / héritier : attribués dans tous les cas (idempotent si déjà présent). Le conflit
            # d'héritier a déjà été tranché en base (heir_conflict) : repli sur Membres principaux.
            if progress.get("sera_heritier"):
                collect(MEMBRES_PRINCIPAUX_ROLE_ID if heir_conflict else HERITIER_ROLE_ID)
            elif progress.get("grade_choisi"):
                grade_rid = GRADE_LABEL_TO_ROLE_ID.get(progress.get("grade_choisi"))
                if grade_rid:
                    collect(grade_rid)
                else:
                    print(f"[fiche] Grade inconnu, non attribué : {progress.get('grade_choisi')!r}")
    else:
        # Pas de clan (Humain, hybride chez les humains, ou clan 'sans_clan') : rôle "Sans clan",
        # attribué exactement comme n'importe quel autre rôle de clan.
        collect(SANS_CLAN_ROLE_ID)

    # 3) Rôle RCT
    collect(RCT_POSSEDE_ROLE_ID if progress.get("rct") else RCT_NON_POSSEDE_ROLE_ID)

    try:
        if roles_to_add:
            await member.add_roles(*roles_to_add, reason="Validation de fiche /depart")
    except discord.Forbidden:
        print(f"[fiche] Permission manquante pour attribuer les rôles à {member} ({member.id}). "
              "Rôles à corriger manuellement.")
    except Exception as e:
        import traceback
        print(f"[fiche] Erreur d'attribution des rôles à {member} ({member.id}) : {e}")
        traceback.print_exc()


async def handle_fiche_valide(interaction: discord.Interaction, custom_id: str):
    parts = custom_id.split(":")
    target_uid = int(parts[1])
    slot = int(parts[2])

    if not _is_fiche_staff(interaction.user):
        await interaction.response.send_message("Tu n'as pas la permission.", ephemeral=True)
        return

    progress = get_progress(target_uid)
    guild = interaction.guild
    member = guild.get_member(target_uid) if guild else None

    # 1) Retire les boutons + note (acquitte l'interaction rapidement).
    original_content = interaction.message.content or ""
    await interaction.response.edit_message(
        content=f"{original_content}\n✅ Validée par {interaction.user.mention}", view=None
    )

    # 2) Conflit d'héritier basé sur la BASE (slots réels ET virtuels) : un personnage validé de ce
    # clan porte-t-il déjà le grade 'Héritier' ? Calculé AVANT l'insertion pour figer le grade réel.
    heir_conflict = bool(progress.get("sera_heritier")) and db.heir_exists(
        progress.get("guild_id"), progress.get("clan")
    )

    # Grade effectivement attribué (pour validated_characters + affichage).
    if progress.get("sera_heritier"):
        effective_grade = "Membres principaux" if heir_conflict else "Héritier"
    else:
        effective_grade = progress.get("grade_choisi")

    # Attribution réelle des rôles Discord : UNIQUEMENT pour le slot 1 (les slots 2/3 n'ont que des
    # rôles virtuels enregistrés en base). Le comptage de clan est fait après l'insertion ci-dessous.
    await assign_validation_roles(guild, member, progress, slot, heir_conflict)

    # 3) Insertion dans validated_characters (avec le grade effectif : source de vérité des places).
    # On enregistre TOUTES les infos, y compris pour les slots 2/3 : camp, clan, sort, eo, nature,
    # grade (Héritier / Membres principaux / grade choisi / None selon les cas), rct, hybride_type.
    has_clan = has_clan_from_progress(progress)
    nom_final = progress.get("clan").capitalize() if has_clan else (progress.get("nom") or "")
    prenom = progress.get("prenom") or ""
    character_name = f"{prenom} {nom_final}".strip()
    discord_username = member.name if member else str(target_uid)
    db.insert_validated_character(
        user_id=target_uid, guild_id=progress.get("guild_id"), slot_number=slot,
        discord_username=discord_username, character_name=character_name,
        camp=progress.get("camp"), clan=progress.get("clan"), sort=progress.get("sort"),
        eo_classe=progress.get("eo_classe"), eo_value=progress.get("eo_value"),
        nature=progress.get("nature"), hybride_type=_hybride_type_of(progress),
        grade=effective_grade, rct=1 if progress.get("rct") else 0,
        portrait_path=progress.get("portrait_path"),
        validated_at=datetime.utcnow().isoformat(),
    )
    update_progress(target_uid, fiche_status="validated")

    # 3 ter) Crée le profil (/profil) avec les VRAIES valeurs de la fiche plutôt que les DEFAULT
    # génériques : la réserve d'énergie occulte réellement tirée devient eo_actuel = eo_max (réserve
    # pleine). eo_value est NULL pour Humain / Hybride chez les humains -> 0/0 géré côté DB.
    with db.get_connection() as conn:
        prof_row = conn.execute(
            "SELECT id FROM validated_characters WHERE user_id = ? AND guild_id = ? AND slot_number = ?",
            (target_uid, progress.get("guild_id"), slot),
        ).fetchone()
    if prof_row is not None:
        character_id = prof_row["id"]
        db.create_profile_from_fiche(character_id, progress.get("eo_value"))
        # Source de vérité PERMANENTE de la réserve d'EO : même valeur que validated_characters.eo_value.
        # Resynchronisée à chaque affichage du profil (sync_eo_with_fiche), robuste aux redémarrages.
        db.set_fiche_record(character_id, progress.get("eo_value"))
        # Ligne de stats par défaut (tout à 0). Points de départ à définir (cf. TODO.md).
        db.create_stats_default(character_id)

        # Slots 2/3 sans clan : le rôle "Sans clan" est enregistré VIRTUELLEMENT (aucun rôle réel posé
        # pour ces slots ; les slots 1 l'ont reçu en vrai rôle via assign_validation_roles).
        if slot in (2, 3) and not has_clan:
            db.add_virtual_role(character_id, SANS_CLAN_ROLE_ID)

        # Grant initial des points de stats liés aux rôles (camp, clan/sans-clan, grade), une fois tous
        # les rôles attribués (réels pour le slot 1, virtuels/enregistrés pour les slots 2/3).
        await grant_initial_role_points(
            character_id, progress.get("camp"), progress.get("clan"), effective_grade)

    # 3 bis) Place de clan : comptage basé sur la base (TOUS les personnages du clan, slots réels ET
    # virtuels), APRÈS l'insertion pour inclure ce nouveau personnage. Ferme / redistribue si le cap
    # est atteint. S'applique quel que soit le slot (un slot 2/3 occupe aussi une place de clan).
    clan_key = progress.get("clan")
    if clan_key and clan_key != "sans_clan":
        try:
            update_clan_state_after_join(guild, clan_key)
        except Exception as e:
            print(f"[fiche] Erreur fermeture/redistribution du clan {clan_key} : {e}")

    # 3) Renvoie le même embed (même image) dans le salon des fiches validées, sans boutons.
    #    Sur cette copie uniquement : statut « ✅ Validée » et mention du valideur.
    embed = build_fiche_embed(
        progress, guild, member, target_uid,
        statut_display="✅ Validée", valide_par_display=interaction.user.mention,
    )
    member_mention = member.mention if member else f"<@{target_uid}>"

    # Conflit d'héritier : l'embed a affiché "Héritier" mais le joueur est en fait "Membre principal".
    if heir_conflict:
        embed.description = embed.description.replace("**Grade :** Héritier", "**Grade :** Membre principal")
        staff_channel = interaction.client.get_channel(FICHE_STAFF_CHANNEL_ID)
        if staff_channel:
            clan_name = (progress.get("clan") or "").capitalize()
            await staff_channel.send(
                f"⚠️ Conflit détecté : {member_mention} avait obtenu le Sort héréditaire du clan "
                f"{clan_name}, mais un héritier existe déjà pour ce clan. Il a été automatiquement "
                "placé en Membre principal à la place. Une intervention manuelle du staff peut être "
                "nécessaire si besoin."
            )

    portrait_path = progress.get("portrait_path")
    filename = _fiche_portrait_filename(target_uid, slot)
    validated_channel = interaction.client.get_channel(FICHE_VALIDATED_CHANNEL_ID)
    if validated_channel:
        # Le "Voici la fiche de @user" est dans le MÊME message que l'embed (paramètre content),
        # comme pour le salon staff, afin d'identifier immédiatement le propriétaire de la fiche.
        content = f"Voici la fiche de {member_mention}"
        if portrait_path and os.path.exists(portrait_path):
            await validated_channel.send(
                content=content, embed=embed, file=discord.File(portrait_path, filename=filename)
            )
        else:
            await validated_channel.send(content=content, embed=embed)

    # 4) Notifie le joueur.
    origin = interaction.client.get_channel(progress.get("origin_channel_id"))
    if origin:
        await origin.send(f"{member_mention} Ta fiche a été validée par le staff ! Bienvenue.")

    # 5) Slots 2/3 : aucun rôle réel n'a été posé (cf. assign_validation_roles). Le staff enregistre
    # en masse, via mentions de rôles, les rôles à associer VIRTUELLEMENT à ce personnage.
    if slot in (2, 3):
        await _collect_virtual_roles_for_slot(interaction, target_uid, slot)


async def _collect_virtual_roles_for_slot(interaction: discord.Interaction, target_uid: int, slot: int):
    """Slot 2/3 : demande au staff qui vient de valider de mentionner tous les rôles à associer
    virtuellement au personnage, puis les enregistre en base (character_virtual_roles) — aucun rôle
    réel n'est posé sur Discord. Isolation STRICTE (même staff + même salon), sans limite de temps ni
    de nombre de rôles ; redemande tant qu'aucune mention de rôle valide n'est fournie."""
    staff_channel = interaction.client.get_channel(FICHE_STAFF_CHANNEL_ID)
    if staff_channel is None:
        return
    guild_id = interaction.guild.id if interaction.guild else None
    with db.get_connection() as conn:
        row = conn.execute(
            "SELECT id FROM validated_characters WHERE user_id = ? AND guild_id = ? AND slot_number = ?",
            (target_uid, guild_id, slot),
        ).fetchone()
    if row is None:
        return
    character_id = row["id"]

    await staff_channel.send(
        f"{interaction.user.mention}, ce personnage est en slot {slot}, ses rôles ne sont donc pas "
        "attribués sur Discord. Mentionne tous les rôles à associer virtuellement à ce personnage "
        "(autant que tu veux, en un seul message)."
    )

    # Isolation : uniquement le staff qui vient de valider, dans CE salon.
    def check(m):
        return (m.channel.id == staff_channel.id and m.author.id == interaction.user.id
                and not m.author.bot)

    while True:
        message = await interaction.client.wait_for("message", check=check)  # aucune limite de temps
        roles = message.role_mentions
        if not roles:
            await staff_channel.send(
                "Aucune mention de rôle détectée. Mentionne au moins un rôle (ex : @Rôle) à associer "
                "à ce personnage."
            )
            continue
        # Dédoublonnage si le même rôle est mentionné plusieurs fois ; INSERT OR IGNORE en base aussi.
        seen = set()
        for role in roles:
            if role.id in seen:
                continue
            seen.add(role.id)
            db.add_virtual_role(character_id, role.id)
        await staff_channel.send(
            f"{len(seen)} rôles enregistrés pour ce personnage. Le staff pourra en ajouter d'autres "
            "plus tard si besoin via la commande /profil."
        )
        return


async def handle_fiche_refuse(interaction: discord.Interaction, custom_id: str):
    target_uid = int(custom_id.split(":")[1])

    if not _is_fiche_staff(interaction.user):
        await interaction.response.send_message("Tu n'as pas la permission.", ephemeral=True)
        return

    progress = get_progress(target_uid)
    original_content = interaction.message.content or ""
    await interaction.response.edit_message(
        content=f"{original_content}\n❌ Refusée par {interaction.user.mention}", view=None
    )

    # Repart de zéro pour la prochaine tentative.
    update_progress(
        target_uid, fiche_status="not_started", fiche_stage=None,
        nom=None, prenom=None, age=None, histoire=None, portrait_path=None,
    )

    origin = interaction.client.get_channel(progress.get("origin_channel_id"))
    if origin:
        await origin.send(
            f"<@{target_uid}> Ta fiche a été refusée par le staff. Tu peux recommencer en cliquant "
            "sur le bouton \"Faire ma fiche\" ci dessus."
        )


async def _run_rct_attempt(interaction: discord.Interaction):
    """Animation d'analyse (7 cycles, 1 s) sur le message courant, puis tirage 1%/99% et résultat.
    Édite toujours le message existant, n'en crée jamais de nouveau."""
    message = interaction.message

    for i in range(7):
        dots = "." * (i % 4)  # "", ".", "..", "..." en boucle
        anim = discord.Embed(
            title="❤️‍🩹 Tentative de RCT",
            description=f"🔎 Analyse en cours {dots}".rstrip(),
            color=discord.Color.red(),
        )
        await message.edit(embed=anim, view=None)
        await asyncio.sleep(1)

    success = random.random() < 0.01  # 1% — probabilités jamais affichées ni modifiées

    if success:
        update_progress(interaction.user.id, rct=1)
        result = discord.Embed(
            title="❤️‍🩹 Résultat du RCT",
            description=RCT_SUCCESS_TEXT.format(mention=interaction.user.mention),
            color=discord.Color.green(),
        )
        await message.edit(embed=result, view=ContinueFicheView())
        return

    # Échec : bouton Reroll s'il reste des charges, sinon bouton Continuer.
    charges = get_progress(interaction.user.id).get("reroll_rct_charges") or 0
    result = discord.Embed(
        title="❤️‍🩹 Résultat du RCT",
        description=RCT_FAILURE_TEXT.format(mention=interaction.user.mention),
        color=discord.Color.dark_red(),
    )
    if charges > 0:
        await message.edit(embed=result, view=RerollRctView(interaction.user.id, charges))
    else:
        await message.edit(embed=result, view=ContinueFicheView())


async def handle_roll_rct(interaction: discord.Interaction, custom_id: str):
    owner_id = int(custom_id.split(":", 1)[1])
    if interaction.user.id != owner_id:
        await interaction.response.send_message("Ce bouton ne t'appartient pas.", ephemeral=True)
        return
    await interaction.response.defer()
    await _run_rct_attempt(interaction)


async def handle_reroll_rct(interaction: discord.Interaction, custom_id: str):
    owner_id = int(custom_id.split(":", 1)[1])
    if interaction.user.id != owner_id:
        await interaction.response.send_message("Ce bouton ne t'appartient pas.", ephemeral=True)
        return
    charges = get_progress(interaction.user.id).get("reroll_rct_charges") or 0
    if charges <= 0:
        await interaction.response.send_message("Tu n'as plus de tentative de reroll RCT.", ephemeral=True)
        return
    db.adjust_progress_counter(interaction.user.id, "reroll_rct_charges", -1)
    await interaction.response.defer()
    await _run_rct_attempt(interaction)


async def apply_reward(interaction: discord.Interaction, reward: dict):
    """Applique l'effet de la récompense choisie selon reward['key']."""
    member = interaction.user
    channel = interaction.channel
    uid = member.id
    key = reward["key"]
    progress = get_progress(uid)

    # Mémorise la récompense choisie pour l'afficher plus tard dans la fiche.
    qty = reward.get("qty")
    reco_display = reward["name"] if (not qty or qty == "x1") else f"{reward['name']} — {qty}"
    update_progress(uid, recompense=reco_display)

    # --- Effet propre à chaque récompense (aucun return : l'enchaînement RCT est commun, plus bas) ---
    if key in ("argent", "xp"):
        # L'argent est mémorisé pour être déposé sur le futur compte bancaire (cog banque).
        if key == "argent":
            update_progress(uid, argent_recompense=reward.get("amount") or 0)
        # TODO: intégrer réellement l'XP dans le futur système une fois développé.
        await channel.send(embed=_reward_embed(f"{member.mention} a choisi **{reward['qty']}** !"))

    elif key == "reroll_clan":
        await reward_reroll_clan(interaction, progress)

    elif key == "reroll_sort":
        await reward_reroll_sort(interaction, progress)

    elif key == "reroll_energie_nature":
        await reward_reroll_energie_nature(interaction, progress)

    elif key == "reroll_energie_qte":
        # Charge stockée + bouton de reroll (action bonus indépendante, ne bloque PAS la progression).
        db.adjust_progress_counter(uid, "reroll_energie_charges", 1)
        await channel.send(
            embed=_reward_embed(f"{member.mention} a obtenu un Reroll de sa quantité d'énergie !"),
            view=RerollEnergieView(uid),
        )

    elif key == "reroll_rct":
        # Charge stockée, utilisée automatiquement à l'étape RCT ci-dessous. Aucun bouton ici.
        db.adjust_progress_counter(uid, "reroll_rct_charges", 1)
        await channel.send(embed=_reward_embed(
            f"{member.mention} a obtenu une tentative supplémentaire pour le RCT !"
        ))

    elif key in ("parchemin_territoire", "parchemin_rct", "parchemin_nature"):
        col = {
            "parchemin_territoire": "parchemins_territoire",
            "parchemin_rct": "parchemins_rct",
            "parchemin_nature": "parchemins_nature",
        }[key]
        qty = reward.get("amount") or 1  # quantité 1-5 tirée par resolve_reward
        db.adjust_progress_counter(uid, col, qty)
        await channel.send(embed=_reward_embed(
            f"{member.mention} a obtenu **{reward['qty']} {reward['name']}** !"
        ))

    else:
        # Objets (relique_X / arme_X)
        # TODO: pas de système d'inventaire pour l'instant, juste enregistré pour la future fiche.
        add_progress_item(uid, reward["name"])
        await channel.send(embed=_reward_embed(f"{member.mention} a obtenu : **{reward['name']}** !"))

    # --- Enchaînement selon le chemin ---
    # Fléaux : pas de RCT (régénération par l'énergie), on va directement vers la fiche.
    # Exorciste / Hybride-exorciste / Livré à soi même : étape RCT.
    path = get_progress(uid).get("path")
    if path == "hybride_fleaux":
        await send_vers_la_fiche(channel, member)
    else:
        await send_rct_step(channel, member)


async def reward_reroll_clan(interaction, progress):
    """EXCEPTION au système différé : le Reroll Clan (récompense) attribue immédiatement le vrai
    rôle du clan + le comptage/fermeture qui va avec (contrairement au tirage initial, différé)."""
    member = interaction.user
    guild = interaction.guild
    channel = interaction.channel
    uid = member.id
    path = progress.get("path")
    sort_key = progress.get("sort")
    old_clan = progress.get("clan")

    state = load_clan_state()

    # Retire le rôle de l'ancien clan (d'un reroll précédent) si le membre le porte encore.
    remove_roles = []
    if old_clan and old_clan != "sans_clan":
        old_info = state["clans"].get(old_clan)
        if old_info:
            r = guild.get_role(old_info["role_id"])
            if r and r in member.roles:
                remove_roles.append(r)

    # Nouveau tirage de clan, identique au parcours normal.
    pool = {"sans_clan": state["sans_clan_pct"]}
    for clan_key, inf in state["clans"].items():
        if not inf["closed"]:
            pool[clan_key] = inf["current_pct"]
    new_clan = weighted_choice(pool)

    auto_grade = None
    if new_clan == "sans_clan":
        # Aucun rôle de clan : on retire aussi le marqueur "appartient à un clan" si présent.
        marker = guild.get_role(CLAN_MEMBER_ROLE_ID)
        if marker and marker in member.roles:
            remove_roles.append(marker)
        try:
            if remove_roles:
                await member.remove_roles(*remove_roles, reason="Reroll Clan (récompense)")
        except discord.Forbidden:
            print(f"[reroll_clan] Permission manquante pour retirer les rôles à {member} ({uid}).")
    else:
        new_info = state["clans"][new_clan]
        add_roles = [role for role in (guild.get_role(new_info["role_id"]),
                                       guild.get_role(CLAN_MEMBER_ROLE_ID)) if role is not None]
        # Hybride chez les exorcistes : grade auto (Membres principaux) recalculé pour le nouveau clan.
        if path == "hybride_exorciste":
            auto_grade = compute_auto_grade_hybride(new_clan, guild.id if guild else None)
            grade_role = guild.get_role(GRADE_LABEL_TO_ROLE_ID.get(auto_grade))
            if grade_role is not None:
                add_roles.append(grade_role)
        try:
            if remove_roles:
                await member.remove_roles(*remove_roles, reason="Reroll Clan (récompense)")
            if add_roles:
                await member.add_roles(*add_roles, reason="Reroll Clan (récompense)")
        except discord.Forbidden:
            print(f"[reroll_clan] Permission manquante pour modifier les rôles à {member} ({uid}).")
        # La place est maintenant réellement occupée : fermeture / redistribution si cap atteint.
        update_clan_state_after_join(guild, new_clan)

    # Base : nouveau clan, plus héritier (un nouveau sort héréditaire devrait être re-accepté).
    fields = {"clan": new_clan, "sera_heritier": 0}
    if path == "hybride_exorciste":
        fields["grade_choisi"] = auto_grade  # None si sans_clan
    update_progress(uid, **fields)
    # TODO: cas limite si le nouveau clan ne permet pas le sort déjà obtenu (ex: sort héréditaire
    # partiel sur un clan qui ne le propose pas), à vérifier manuellement pour l'instant.

    # Points de stats liés au rôle de clan : si ce joueur a DÉJÀ un personnage validé sur ce slot,
    # resynchronise ses points (gain/reprise). Avant validation, le grant initial s'en chargera.
    char_id = db.get_validated_character_id(uid, progress.get("guild_id"), progress.get("slot_number"))
    if char_id:
        _, new_clan_role_id, _ = resolve_role_point_ids(None, new_clan, None)
        await sync_role_points(char_id, "clan", new_clan_role_id)

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

    # Rôle Héritier différé à la validation : on ne fait que mémoriser l'état en base.
    # TODO: la validation accepter/refuser héritier n'est pas re-déclenchée ici ; un reroll
    # tombant sur le sort héréditaire désigne directement le joueur comme futur héritier.
    if new_sort == "sort_heredit":
        update_progress(uid, sort=new_sort, sera_heritier=1)
        label = "Sort héréditaire (complet)"
    else:
        update_progress(uid, sort=new_sort, sera_heritier=0)
        label = SORT_LABELS[new_sort]

    spell_data = build_spell_image_data(base_table, final_table, new_sort, label)
    await send_clan_sort_pillow(channel, state, clan_key, spell_data)
    await channel.send(embed=_reward_embed(f"{member.mention} a rerollé son sort : **{label}** !"))


class RerollEnergieView(discord.ui.View):
    """Bouton "Reroll énergie" (conteneur simple). Le clic est traité par le listener on_interaction
    du cog : custom_id dynamique par joueur -> vraie persistance après un redémarrage du bot."""

    def __init__(self, user_id: int):
        super().__init__(timeout=None)
        self.add_item(discord.ui.Button(
            label="Reroll énergie",
            emoji="🔄",
            style=discord.ButtonStyle.primary,
            custom_id=f"depart_reroll_energie:{user_id}",
        ))


# Montée de classe garantie à chaque reroll d'énergie (sauf S qui reste S).
ENERGIE_LADDER = {"classe_4": "classe_3", "classe_3": "classe_2", "classe_2": "classe_1", "classe_1": "classe_s"}


async def handle_reroll_energie(interaction: discord.Interaction, custom_id: str):
    owner_id = int(custom_id.split(":", 1)[1])
    if interaction.user.id != owner_id:
        await interaction.response.send_message("Ce bouton ne t'appartient pas.", ephemeral=True)
        return

    progress = get_progress(interaction.user.id)
    charges = progress.get("reroll_energie_charges") or 0
    if charges <= 0:
        await interaction.response.send_message("Tu n'as plus de charge de reroll disponible.", ephemeral=True)
        return

    eo_classe = progress.get("eo_classe")
    eo_value = progress.get("eo_value")
    nature = progress.get("nature")  # nature totalement inchangée
    if not eo_classe:
        await interaction.response.send_message("Tu n'as pas encore de réserve d'énergie à reroll.", ephemeral=True)
        return

    if eo_classe == "classe_s":
        new_classe = "classe_s"
        # Garantit une valeur >= à l'actuelle, jamais inférieure.
        new_value = random.randint(eo_value, EO_CLASS_TABLE["classe_s"]["max"])
    else:
        new_classe = ENERGIE_LADDER[eo_classe]
        info = EO_CLASS_TABLE[new_classe]
        new_value = random.randint(info["min"], info["max"])

    db.adjust_progress_counter(interaction.user.id, "reroll_energie_charges", -1)
    update_progress(interaction.user.id, eo_classe=new_classe, eo_value=new_value)

    await interaction.response.defer()
    channel = interaction.channel
    await render_and_send_reserve_image(channel, interaction.user, new_classe, new_value, nature, nature is not None)
    label = new_classe.replace("classe_", "").upper()
    await channel.send(embed=_reward_embed(
        f"{interaction.user.mention} a rerollé sa réserve : nouvelle classe {label}, {new_value:,} EO !"
    ))

    # Plus de charge : on retire le bouton du message d'origine. Sinon on le laisse actif.
    if charges - 1 <= 0:
        try:
            await interaction.message.edit(view=None)
        except discord.HTTPException:
            pass


async def reward_reroll_energie_nature(interaction, progress):
    member = interaction.user
    channel = interaction.channel
    new_nature = weighted_choice(EO_NATURE_TABLE)
    update_progress(member.id, nature=new_nature)
    await channel.send(embed=build_nature_embed(member, new_nature))


# Table de récompenses associée à chaque chemin.
REWARD_TABLE_BY_PATH = {
    "exorciste": REWARD_TABLE,
    "hybride_exorciste": REWARD_TABLE_HYBRIDE_EXORCISTE,
    "hybride_fleaux": REWARD_TABLE_HYBRIDE_FLEAUX,
    "hybride_seul": REWARD_TABLE_HYBRIDE_SEUL,
}


async def start_reward_choice(interaction: discord.Interaction, table):
    """Tire deux récompenses distinctes dans `table`, affiche la carte A/B + les boutons de choix."""
    if not interaction.response.is_done():
        await interaction.response.defer()
    uid = interaction.user.id
    # Héritier désigné : "Reroll Clan" retiré du pool (il perdrait son statut d'héritier).
    exclude = {"reroll_clan"} if get_progress(uid).get("sera_heritier") else None
    option_a, option_b = pick_two_distinct_rewards(table, exclude_keys=exclude)
    store_pending_rewards(uid, option_a, option_b)

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
    await interaction.channel.send(embed=embed, view=RewardChoiceView(uid))


# ---------- Vues ----------
class RewardContinueView(discord.ui.View):
    """Bouton "Continuer" après l'étape nature (chemins exorciste / hybride-exorciste),
    qui déclenche l'étape récompense avec la table propre au chemin."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Continuer", emoji="➡️", style=discord.ButtonStyle.success, custom_id="depart_continuer_recompense")
    async def continuer(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        path = get_progress(interaction.user.id).get("path")
        table = REWARD_TABLE_BY_PATH.get(path)
        if table is not None:
            await start_reward_choice(interaction, table)
        else:
            await interaction.channel.send("La suite arrive dans une prochaine étape.")


class ContinueRecompenseFleauxView(discord.ui.View):
    """Bouton "Continuer" (chemin Hybride chez les fléaux) → tirage récompense fléaux."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Continuer", emoji="➡️", style=discord.ButtonStyle.success, custom_id="depart_continuer_recompense_fleaux")
    async def continuer(self, interaction: discord.Interaction, button: discord.ui.Button):
        await start_reward_choice(interaction, REWARD_TABLE_HYBRIDE_FLEAUX)


class ContinueRecompenseSeulView(discord.ui.View):
    """Bouton "Continuer" (chemin Livré à soi même) → tirage récompense seul."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Continuer", emoji="➡️", style=discord.ButtonStyle.success, custom_id="depart_continuer_recompense_seul")
    async def continuer(self, interaction: discord.Interaction, button: discord.ui.Button):
        await start_reward_choice(interaction, REWARD_TABLE_HYBRIDE_SEUL)


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

        # Rôles (clan + Héritier) différés à la validation : on mémorise seulement "sera héritier".
        update_progress(interaction.user.id, camp="exorciste", path="exorciste", clan=self.clan_key, sort="sort_heredit", sera_heritier=1)

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

        # Rôles différés à la validation ; grade choisi via le questionnaire de fiche.
        update_progress(interaction.user.id, camp="exorciste", path="exorciste", clan=self.clan_key, sort=new_sort, sera_heritier=0)

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

        # Sort classique : rôles (clan + marqueur) différés à la validation. Grade choisi via la fiche.
        # Le comptage/fermeture du clan se fait aussi à la validation (place réellement occupée à ce moment).
        update_progress(interaction.user.id, camp="exorciste", path="exorciste", clan=result_key, sort=sort_key, sera_heritier=0)

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
            update_progress(interaction.user.id, camp="hybride", path="hybride_exorciste", hybride_type="exorciste", clan="sans_clan", sort="sort_inne")
            spell_data = build_hybride_spell_data(partial_heredit=False)
            await send_roll_result(
                interaction, state, "sans_clan", None, "Sort inné", {}, {}, spell_data_override=spell_data
            )
            return

        # c) Clan précis : rôles différés à la validation, comptage/fermeture à la validation.
        # Le grade est déterminé AUTOMATIQUEMENT (aucune question de grade dans la fiche pour ce chemin).
        info = state["clans"][result_key]
        auto_grade = compute_auto_grade_hybride(result_key, guild.id if guild else None)
        update_progress(
            interaction.user.id, camp="hybride", path="hybride_exorciste", hybride_type="exorciste",
            clan=result_key, sort="sort_inne", sera_heritier=0, grade_choisi=auto_grade,
        )

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
        # Aucun tirage : passage direct à la fiche. (Rôles non attribués pour l'instant.)
        update_progress(
            interaction.user.id, camp="hybride", path="hybride_humains", hybride_type="humains",
            clan=None, sort=None, eo_classe=None, eo_value=None, nature=None, rct=None, recompense=None,
        )
        embed = discord.Embed(
            title="🏙️ Hybride élevé chez les humains",
            description=(
                f"{interaction.user.mention} a grandi parmi les humains, sans jamais découvrir le "
                "monde de l'énergie occulte. Aucun tirage n'est nécessaire.\n\n"
                "Clique sur **Continuer** pour passer à la création de ta fiche."
            ),
            color=discord.Color.blurple(),
        )
        await interaction.response.send_message(embed=embed, view=ContinueFicheDirectView())

    @discord.ui.button(label="Chez les exorcistes", emoji="⚔️", style=discord.ButtonStyle.primary, custom_id="depart_hybride_exorcistes")
    async def exorcistes(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Même tableau des clans que l'exorciste classique, mais avec le bouton de roll hybride.
        await interaction.response.send_message(
            embed=build_clan_table_embed(interaction.guild), view=ClanRollHybrideView(), ephemeral=False
        )

    @discord.ui.button(label="Chez les fléaux", emoji="👹", style=discord.ButtonStyle.danger, custom_id="depart_hybride_fleaux")
    async def fleaux(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Voie "fléaux" : pas de clan, réserve d'énergie occulte SANS nature, déclenchée directement.
        update_progress(interaction.user.id, camp="hybride", path="hybride_fleaux", hybride_type="fleaux")
        await roll_and_send_reserve(interaction, interaction.user, interaction.guild, with_nature=False)

    @discord.ui.button(label="Livré à soi même", emoji="🌪️", style=discord.ButtonStyle.secondary, custom_id="depart_hybride_seul")
    async def livre(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Voie "livré à soi même" : réserve d'énergie occulte AVEC nature, déclenchée directement.
        update_progress(interaction.user.id, camp="hybride", path="hybride_seul", hybride_type="seul")
        await roll_and_send_reserve(interaction, interaction.user, interaction.guild, with_nature=True)


class CampView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Exorciste", emoji="⚔️", style=discord.ButtonStyle.primary, custom_id="depart_camp_exorciste")
    async def exorciste(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Rôle de camp NON attribué ici : différé à la validation de la fiche.
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
        # Rôle de camp NON attribué ici : différé à la validation de la fiche.
        update_progress(interaction.user.id, camp="hybride")
        await interaction.response.send_message(
            embed=build_education_embed(), view=EducationView(), ephemeral=False
        )

    @discord.ui.button(label="Humain", emoji="🧑", style=discord.ButtonStyle.secondary, custom_id="depart_camp_humain")
    async def humain(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Rôles de camp NON attribués pour l'instant (traités globalement plus tard).
        update_progress(
            interaction.user.id, camp="humain", path="humain", hybride_type=None,
            clan=None, sort=None, eo_classe=None, eo_value=None, nature=None, rct=None, recompense=None,
        )
        embed = discord.Embed(
            title="🧑 Humain",
            description=(
                f"{interaction.user.mention} a choisi la voie de l'humain. Aucun tirage n'est "
                "nécessaire.\n\nClique sur **Continuer** pour passer à la création de ta fiche."
            ),
            color=discord.Color.blurple(),
        )
        await interaction.response.send_message(embed=embed, view=ContinueFicheDirectView())


class SelectionView(discord.ui.View):
    """Écran de sélection : bouton « Créer le Xème perso » (si un slot est libre) et/ou
    « 🗑️ Supprimer un personnage » (si au moins un slot est occupé).

    Les deux boutons ont un custom_id dynamique (slot pour la création, user_id pour la
    suppression) et sont gérés par le listener on_interaction du cog — c'est le mécanisme
    de persistance utilisé partout dans ce bot, bot.add_view() ne pouvant pas enregistrer
    un custom_id contenant un user_id inconnu à l'avance.
    """

    _CREATE_LABELS = {1: "Créer le 1er perso", 2: "Créer le 2ème perso", 3: "Créer le 3ème perso"}

    def __init__(self, user_id: int, first_free=None, show_delete: bool = False):
        super().__init__(timeout=None)
        if first_free is not None:
            self.add_item(discord.ui.Button(
                label=self._CREATE_LABELS.get(first_free, "Créer un perso"),
                style=discord.ButtonStyle.success,
                custom_id=f"depart_start_creation:{first_free}",
            ))
        if show_delete:
            self.add_item(discord.ui.Button(
                label="🗑️ Supprimer un personnage",
                style=discord.ButtonStyle.danger,
                custom_id=f"depart_delete_char:{user_id}",
            ))


# RÈGLE PERMANENTE DU PROJET : delete_character_cascade() doit TOUJOURS être mise à jour
# dès qu'une nouvelle table référence character_id (ex: un futur système de profil, de
# quêtes, de compétences, etc.). Avant de considérer un nouveau système "terminé",
# vérifie systématiquement s'il doit être ajouté ici pour éviter les données orphelines
# quand un personnage est supprimé.
def delete_character_cascade(character_id):
    """Nettoyage en cascade des données liées à un personnage (banque, inventaire, échanges...)."""
    with db.get_connection() as conn:
        # Banque
        conn.execute("DELETE FROM bank_accounts WHERE character_id = ?", (character_id,))
        conn.execute("DELETE FROM bank_transactions WHERE character_id = ?", (character_id,))
        conn.execute("DELETE FROM bank_sessions WHERE character_id = ?", (character_id,))
        # Inventaire
        conn.execute("DELETE FROM character_inventory WHERE character_id = ?", (character_id,))
        # Échanges (le personnage peut être proposeur OU cible)
        conn.execute(
            "DELETE FROM pending_trades WHERE proposer_character_id = ? OR target_character_id = ?",
            (character_id, character_id),
        )
        # Rôles virtuels enregistrés pour les personnages de slot 2/3
        conn.execute("DELETE FROM character_virtual_roles WHERE character_id = ?", (character_id,))
        # Profil (/profil) et fond d'écran propres au personnage
        conn.execute("DELETE FROM character_profiles WHERE character_id = ?", (character_id,))
        conn.execute("DELETE FROM character_backgrounds WHERE character_id = ?", (character_id,))
        # Techniques Occultes (/profil → ⚡ Technique) propres au personnage : sorts secondaires d'abord
        # (référencent character_sorts.id), puis les sorts principaux.
        conn.execute(
            "DELETE FROM character_secondary_sorts WHERE sort_id IN "
            "(SELECT id FROM character_sorts WHERE character_id = ?)",
            (character_id,),
        )
        conn.execute("DELETE FROM character_sorts WHERE character_id = ?", (character_id,))
        # Territoire (/profil → 🗺️ Territoire) propre au personnage
        conn.execute("DELETE FROM character_territoire WHERE character_id = ?", (character_id,))
        # Armes maudites (/profil → 🗡️ Armes maudites) propres au personnage
        conn.execute("DELETE FROM character_armes_maudites WHERE character_id = ?", (character_id,))
        # Plafonds de niveau personnalisés (staff) propres au personnage
        conn.execute("DELETE FROM character_mastery_overrides WHERE character_id = ?", (character_id,))
        # Statistiques + buffs (et effets de buffs via sous requête sur buff_id)
        conn.execute("DELETE FROM character_stats WHERE character_id = ?", (character_id,))
        conn.execute(
            "DELETE FROM character_buff_effects WHERE buff_id IN "
            "(SELECT id FROM character_buffs WHERE character_id = ?)",
            (character_id,),
        )
        conn.execute("DELETE FROM character_buffs WHERE character_id = ?", (character_id,))
        # Relations : le personnage disparaît de SES liens ET des liens que d'autres avaient créés vers lui.
        conn.execute(
            "DELETE FROM character_relations WHERE character_id = ? OR related_character_id = ?",
            (character_id, character_id),
        )
        # Ordres (/ordre) : si ce personnage est CHEF d'un ordre, on NE supprime PAS l'ordre
        # automatiquement (log clair pour le staff), on le retire seulement de order_members ailleurs.
        chef_order = conn.execute(
            "SELECT id, name FROM orders WHERE chef_character_id = ?", (character_id,)
        ).fetchone()
        if chef_order is not None:
            # TODO : décider quoi faire d'un ordre dont le chef est supprimé, à ajouter aux points non abordés
            print(
                f"[cascade] Personnage {character_id} supprimé alors qu'il est CHEF de l'ordre "
                f"#{chef_order['id']} « {chef_order['name']} » : l'ordre n'est PAS supprimé automatiquement, "
                f"à traiter manuellement."
            )
        conn.execute("DELETE FROM order_members WHERE character_id = ?", (character_id,))
        # Rattachements disciple ↔ éducateur : le personnage disparaît qu'il soit disciple OU éducateur.
        conn.execute(
            "DELETE FROM order_disciple_assignments "
            "WHERE disciple_character_id = ? OR educator_character_id = ?",
            (character_id, character_id),
        )
        # Salaires perçus dans un ordre : le personnage supprimé n'est plus payé.
        # TODO : le retrait automatique si le JOUEUR quitte le serveur Discord (sans forcément supprimer
        # son personnage) est désormais géré par handle_player_departure / on_member_remove ci-dessous.
        conn.execute("DELETE FROM order_salaries WHERE character_id = ?", (character_id,))
        # Contrats éducateur ↔ employeur : le personnage disparaît qu'il soit disciple OU éducateur.
        # (Les notifications de fin de contrat sont envoyées EN AMONT par handle_order_departure, appelé
        # avant cette cascade dans tous les points de départ.)
        conn.execute(
            "DELETE FROM educator_contracts WHERE disciple_character_id = ? OR educator_character_id = ?",
            (character_id, character_id),
        )
        # Réservations d'apparence (/réserv-appa) liées à ce personnage.
        conn.execute("DELETE FROM appearance_reservations WHERE character_id = ?", (character_id,))


# =====================================================================
# DÉPART D'UN JOUEUR DU SERVEUR (nettoyage automatique)
# =====================================================================
async def handle_player_departure(bot, user_id: int, guild):
    """Un joueur quitte le serveur : au lieu d'une suppression immédiate, on le MET EN RÉSERVE 15 jours
    (table player_departures). Discord retire déjà rôles/accès tout seul en quittant ; officieusement,
    RIEN n'est touché en base pendant 15 jours (purge par la tâche planifiée _purge_expired_departures).
    Le départ d'un CHEF D'ORDRE reste géré indépendamment par check_chief_presence (cogs/ordre.py) :
    ce système de réserve/restauration ne concerne jamais l'ordre."""
    print(f"🔍 [depart] handle_player_departure appelée pour user_id={user_id}")  # DIAG temporaire
    with db.get_connection() as conn:
        characters = conn.execute(
            "SELECT id FROM validated_characters WHERE user_id = ? AND guild_id = ?",
            (user_id, guild.id),
        ).fetchall()
    if not characters:
        # DIAG temporaire : cause la plus fréquente d'une table player_departures vide -> le joueur
        # n'avait AUCUN personnage validé, donc rien n'est mis en réserve (comportement voulu).
        print(f"🔍 [depart] user_id={user_id} sans personnage validé : aucune mise en réserve.")
        return  # rien à faire, ce joueur n'avait pas de personnage
    db.record_departure(user_id, guild.id, datetime.utcnow().isoformat())


async def purge_player_completely(bot, user_id: int, guild):
    """VRAIE suppression complète des personnages d'un joueur (après 15 jours d'absence, ou décision
    « ❌ Non » de l'owner). Réutilise la logique historique : conséquences côté ordre puis cascade +
    retrait de validated_characters, en laissant les personnages CHEFS d'ordre à check_chief_presence."""
    with db.get_connection() as conn:
        characters = conn.execute(
            "SELECT id, slot_number FROM validated_characters WHERE user_id = ? AND guild_id = ?",
            (user_id, guild.id),
        ).fetchall()
    if not characters:
        return

    ordre_cog = bot.get_cog("Ordre")
    for char in characters:
        char_id = char["id"]
        # Un personnage chef d'ordre n'est JAMAIS traité instantanément au départ du joueur.
        # La dissolution de son ordre passe uniquement par la vérification programmée 4x/jour (voir
        # check_chief_presence), pour laisser un vrai délai avant une action aussi impactante pour les
        # autres membres.
        with db.get_connection() as conn:
            is_chief = conn.execute(
                "SELECT 1 FROM orders WHERE chef_character_id = ?", (char_id,)
            ).fetchone()
        if is_chief:
            continue

        # Conséquences côté ordre (redistribution des disciples si le perso était éducateur, clôture de
        # son contrat s'il était disciple) via le point d'entrée UNIQUE de cogs/ordre.py, AVANT la
        # cascade — les liens (order_members, order_disciple_assignments, contrats) doivent encore exister.
        if ordre_cog is not None:
            try:
                await ordre_cog.handle_order_departure(char_id, guild)
            except Exception as e:
                print(f"[départ] conséquences ordre pour le perso {char_id} : {e}")

        # Cascade classique (banque, inventaire, relations, order_members, order_salaries,
        # order_disciple_assignments, etc.), puis retrait définitif du personnage.
        delete_character_cascade(char_id)
        with db.get_connection() as conn:
            conn.execute("DELETE FROM validated_characters WHERE id = ?", (char_id,))


async def restore_player_roles(bot, user_id: int, guild):
    """Réattribue les VRAIS rôles Discord des personnages SLOT 1 d'un joueur de retour, depuis les
    attributs déjà stockés dans validated_characters (camp, clan/sans-clan + rôle membre de clan, grade).
    Discord les avait retirés automatiquement quand il a quitté. Les rôles VIRTUELS (slots 2/3) n'ont
    jamais bougé (aucune action). Ne lève jamais : logue et continue en cas d'erreur."""
    if guild is None:
        return
    member = guild.get_member(user_id)
    if member is None:
        try:
            member = await guild.fetch_member(user_id)
        except (discord.NotFound, discord.HTTPException):
            member = None
    if member is None:
        print(f"[retour] Membre {user_id} introuvable, rôles non réattribués.")
        return
    with db.get_connection() as conn:
        chars = conn.execute(
            "SELECT camp, clan, grade FROM validated_characters "
            "WHERE user_id = ? AND guild_id = ? AND slot_number = 1",
            (user_id, guild.id),
        ).fetchall()
    role_ids = set()
    for ch in chars:
        camp_rid, clan_rid, grade_rid = resolve_role_point_ids(ch["camp"], ch["clan"], ch["grade"])
        for rid in (camp_rid, clan_rid, grade_rid):
            if rid:
                role_ids.add(rid)
        if ch["clan"] and ch["clan"] != "sans_clan":
            role_ids.add(CLAN_MEMBER_ROLE_ID)  # rôle générique « membre de clan »
    roles = [r for r in (guild.get_role(rid) for rid in role_ids) if r is not None]
    if not roles:
        return
    try:
        await member.add_roles(*roles, reason="Retour de joueur : restauration des rôles")
    except discord.HTTPException as e:
        print(f"[retour] Échec de réattribution des rôles pour {user_id} : {e}")


def _build_departure_roles_lines(user_id: int, guild_id: int) -> str:
    """Pour le DM de décision : une ligne par personnage (ORDER BY slot_number) avec ses rôles en mentions.
    Slot 1 : rôles RÉELS résolus depuis les attributs de fiche (camp/clan + membre de clan/grade). Slots
    2/3 : rôles VIRTUELS enregistrés (character_virtual_roles), qui n'ont jamais bougé au départ."""
    with db.get_connection() as conn:
        chars = conn.execute(
            "SELECT id, slot_number, character_name, camp, clan, grade FROM validated_characters "
            "WHERE user_id = ? AND guild_id = ? ORDER BY slot_number",
            (user_id, guild_id),
        ).fetchall()
    lignes = []
    for ch in chars:
        if ch["slot_number"] == 1:
            camp_rid, clan_rid, grade_rid = resolve_role_point_ids(ch["camp"], ch["clan"], ch["grade"])
            role_ids = [rid for rid in (camp_rid, clan_rid, grade_rid) if rid]
            if ch["clan"] and ch["clan"] != "sans_clan":
                role_ids.append(CLAN_MEMBER_ROLE_ID)
        else:
            role_ids = db.get_virtual_roles(ch["id"])
        # Dé-doublonne en gardant l'ordre.
        seen, uniques = set(), []
        for rid in role_ids:
            if rid not in seen:
                seen.add(rid)
                uniques.append(rid)
        mentions = " ".join(f"<@&{rid}>" for rid in uniques) if uniques else "_(aucun rôle)_"
        lignes.append(f"**Slot {ch['slot_number']} — {ch['character_name']}**\n{mentions}")
    return "\n\n".join(lignes)


class PlayerReturnDecisionView(discord.ui.View):
    """Décision de l'owner au retour d'un joueur mis en réserve. Boutons à custom_id DYNAMIQUE (user_id /
    guild_id encodés) : persistants et gérés par le listener on_interaction (comme DeleteConfirmView),
    donc cliquables même après un redémarrage sans reconstruire l'instance avec les bons ids."""

    def __init__(self, user_id: int = 0, guild_id: int = 0):
        super().__init__(timeout=None)
        self.add_item(discord.ui.Button(
            label="✅ Oui, tout restaurer", style=discord.ButtonStyle.success,
            custom_id=f"player_return_yes:{user_id}:{guild_id}",
        ))
        self.add_item(discord.ui.Button(
            label="❌ Non, il perd tout", style=discord.ButtonStyle.danger,
            custom_id=f"player_return_no:{user_id}:{guild_id}",
        ))


async def retroactive_departure_check(bot):
    """Rattrapage des joueurs partis AVANT l'ajout du listener on_member_remove. Déclenché à chaque
    démarrage. Au lieu de purger, on MET EN RÉSERVE (ensure_departure, sans réinitialiser un départ déjà
    enregistré) : la purge effective viendra 15 jours plus tard via la tâche planifiée. Idempotent : ne
    réinitialise jamais un timer existant ni un gel de décision."""
    for guild in bot.guilds:
        with db.get_connection() as conn:
            rows = conn.execute(
                "SELECT DISTINCT user_id FROM validated_characters WHERE guild_id = ?", (guild.id,)
            ).fetchall()
        for row in rows:
            user_id = row["user_id"]
            member = guild.get_member(user_id)
            if member is None:
                try:
                    member = await guild.fetch_member(user_id)
                except discord.NotFound:
                    member = None
                except discord.HTTPException:
                    continue  # erreur réseau temporaire : on retentera au prochain redémarrage
            if member is None:
                print(f"🔍 [rattrapage] Joueur {user_id} absent du serveur : mise en réserve (purge dans "
                      "15 jours s'il ne revient pas).")
                db.ensure_departure(user_id, guild.id, datetime.utcnow().isoformat())
            await asyncio.sleep(0.5)  # évite de spam l'API Discord sur un gros serveur


class DeleteConfirmView(discord.ui.View):
    """Confirmation ✅/❌ avant la suppression définitive d'un personnage.
    custom_id dynamique par joueur -> géré par le listener on_interaction (persistant)."""

    def __init__(self, user_id: int, slot: int):
        super().__init__(timeout=None)
        self.add_item(discord.ui.Button(
            label="✅ Confirmer", style=discord.ButtonStyle.danger,
            custom_id=f"depart_delete_confirm:{user_id}:{slot}",
        ))
        self.add_item(discord.ui.Button(
            label="❌ Annuler", style=discord.ButtonStyle.secondary,
            custom_id=f"depart_delete_cancel:{user_id}",
        ))


async def handle_start_creation(interaction: discord.Interaction, custom_id: str):
    # Slot lu depuis le custom_id (fiable même après un redémarrage du bot).
    slot_number = int(custom_id.split(":", 1)[1])
    update_progress(interaction.user.id, slot_number=slot_number, guild_id=interaction.guild.id)

    # Embed de lecture (Étape 1) : comportement inchangé (envoi, 5s, bouton Commencer).
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
        # user_id -> channel_id : joueurs à qui l'on demande le numéro de slot à supprimer.
        self._awaiting_delete = {}

    async def cog_load(self):
        # Amorce l'état des clans si la base est vide (nouvelle installation).
        db.seed_clan_state(DEFAULT_CLAN_STATE)

        # Les boutons de l'écran de sélection (création/suppression) ont un custom_id
        # dynamique : ils sont gérés par le listener on_interaction, pas par add_view.
        self.bot.add_view(DepartView())
        self.bot.add_view(CampView())
        self.bot.add_view(EducationView())
        self.bot.add_view(ClanRollView())
        self.bot.add_view(ClanRollHybrideView())
        self.bot.add_view(ContinueEnergyView())
        self.bot.add_view(RewardContinueView())
        self.bot.add_view(ContinueRecompenseFleauxView())
        self.bot.add_view(ContinueRecompenseSeulView())
        self.bot.add_view(ContinueFicheView())
        self.bot.add_view(ContinueFicheDirectView())
        self.bot.add_view(ReserveClassView())
        self.bot.add_view(DMClanQuestionView())
        self.bot.add_view(DMClanSelectView())
        # Enregistrée avec les 4 boutons pour couvrir tous les custom_id après redémarrage,
        # même si le message réellement envoyé n'en affichait que 3.
        self.bot.add_view(DMSortView(show_partial=True))

        # Vue de décision au retour d'un joueur (custom_id DYNAMIQUE : la persistance réelle passe par le
        # listener on_interaction ; add_view couvre l'enregistrement du composant côté discord.py).
        self.bot.add_view(PlayerReturnDecisionView())

        # Tâche d'annulation des fiches expirées.
        if not self.fiche_expiry_loop.is_running():
            self.fiche_expiry_loop.start()
        # Tâche de purge définitive des départs en réserve depuis ≥ 15 jours.
        if not self.departure_purge_loop.is_running():
            self.departure_purge_loop.start()

    async def cog_unload(self):
        self.fiche_expiry_loop.cancel()
        self.departure_purge_loop.cancel()

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        # Un joueur quitte le serveur : mise en RÉSERVE 15 jours (aucune suppression immédiate).
        await handle_player_departure(self.bot, member.id, member.guild)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        # Retour d'un joueur mis en réserve.
        print(f"🔍 [retour] on_member_join déclenché pour {member.id} ({member.name})")  # DIAG temporaire
        if member.bot:
            return
        departure = db.get_departure(member.id, member.guild.id)
        if departure is None:
            return  # jamais parti (ou déjà purgé après 15 j), rien à faire

        try:
            departed_dt = datetime.fromisoformat(departure["departed_at"])
        except (TypeError, ValueError):
            departed_dt = datetime.utcnow()
        elapsed = datetime.utcnow() - departed_dt

        # §3 : arrivé APRÈS le délai exact de 15 jours -> aucune pitié, suppression définitive, aucun DM.
        if elapsed >= timedelta(days=15):
            await purge_player_completely(self.bot, member.id, member.guild)
            db.delete_departure(member.id)
            print(f"🔍 [retour joueur] {member.id} revenu après le délai de 15 jours, suppression "
                  "définitive sans décision.")
            return

        # Retour DANS les temps : gèle la purge ET ajoute 10 jours de marge (le temps que l'owner échange
        # avec le joueur), puis envoie le DM de décision.
        new_departed = (departed_dt + timedelta(days=10)).isoformat()
        db.freeze_and_extend_departure(member.id, new_departed)

        jours_absence = max(0, elapsed.days)
        date_lisible = departed_dt.strftime("%d/%m/%Y")
        roles_field = _build_departure_roles_lines(member.id, member.guild.id)

        embed = discord.Embed(
            title="🔄 Retour d'un joueur parti",
            description=(
                f"{member.mention} a quitté le serveur le {date_lisible}, et vient de rejoindre à "
                f"nouveau après {jours_absence} jour(s) d'absence.\n\n"
                "Veux tu lui redonner tout ce qu'il avait (personnage, rôles, banque, techniques, "
                "territoire, armes maudites) — **sauf l'ordre**, qui suit sa propre logique déjà "
                "existante ?"
            ),
            color=discord.Color.gold(),
        )
        if roles_field:
            embed.add_field(name="Ce qu'il avait, personnage par personnage", value=roles_field, inline=False)
        view = PlayerReturnDecisionView(user_id=member.id, guild_id=member.guild.id)

        owner = self.bot.get_user(SPECIAL_USER_ID) or await self.bot.fetch_user(SPECIAL_USER_ID)
        if owner is None:
            return

        # Une décision précédente était-elle DÉJÀ en attente pour ce joueur ? (`departure` a été lu AVANT
        # freeze_and_extend, il reflète donc l'état d'avant ce retour-ci.) Si oui, désactive son ancien DM
        # avant d'en créer un nouveau : plus de bouton actif, message annoté « n'est plus valide ».
        if departure["awaiting_owner_decision"] == 1 and departure["last_decision_message_id"]:
            try:
                old_channel = (self.bot.get_channel(departure["last_decision_channel_id"])
                               or await self.bot.fetch_channel(departure["last_decision_channel_id"]))
                old_message = await old_channel.fetch_message(departure["last_decision_message_id"])
                await old_message.edit(
                    view=None,
                    content=((old_message.content or "")
                             + "\n\n⚠️ Cette demande n'est plus valide, une plus récente l'a remplacée."),
                )
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                pass  # message déjà supprimé ou inaccessible : tant pis, on continue

        # §1 : retry sur les erreurs transitoires (HTTPException), jamais sur Forbidden (DMs fermés).
        sent_message = None
        for tentative in range(3):
            try:
                sent_message = await owner.send(embed=embed, view=view)
                break
            except discord.Forbidden:
                break  # DMs fermés, rien à faire, accepté tel quel
            except discord.HTTPException:
                if tentative == 2:
                    print(f"⚠️ [retour joueur] Échec d'envoi du DM à l'owner après 3 tentatives pour "
                          f"{member.id}.")
                else:
                    await asyncio.sleep(2)

        # Mémorise le DM réellement envoyé : référence utilisée pour désactiver / rejeter les anciens.
        if sent_message is not None:
            db.set_departure_decision_message(member.id, sent_message.id, sent_message.channel.id)

    @tasks.loop(hours=6)
    async def departure_purge_loop(self):
        """Purge définitive des joueurs en réserve depuis ≥ 15 jours, toujours absents et non gelés.
        Même rythme que le scheduler d'ordre (4×/jour). check_chief_presence reste indépendant."""
        cutoff = (datetime.utcnow() - timedelta(days=15)).isoformat()
        for row in db.get_departures_to_purge(cutoff):
            user_id, guild_id = row["user_id"], row["guild_id"]
            guild = self.bot.get_guild(guild_id)
            if guild is None:
                continue
            # Sécurité : ne purge que si le joueur n'est TOUJOURS PAS sur le serveur.
            member = guild.get_member(user_id)
            if member is None:
                try:
                    member = await guild.fetch_member(user_id)
                except discord.NotFound:
                    member = None
                except discord.HTTPException:
                    continue  # incertitude réseau : on retentera au prochain passage
            if member is not None:
                continue  # il est revenu sans passer par la décision : on ne purge pas
            print(f"🔍 [purge départ] Joueur {user_id} absent depuis ≥ 15 jours : suppression définitive.")
            await purge_player_completely(self.bot, user_id, guild)
            db.delete_departure(user_id)

    @departure_purge_loop.before_loop
    async def _before_departure_purge(self):
        await self.bot.wait_until_ready()

    async def handle_player_return_decision(self, interaction: discord.Interaction, custom_id: str, restore: bool):
        # Réservé à l'owner du bot.
        if interaction.user.id != SPECIAL_USER_ID:
            await interaction.response.send_message("Cette décision ne t'appartient pas.", ephemeral=True)
            return
        parts = custom_id.split(":")
        user_id, guild_id = int(parts[1]), int(parts[2])

        # Filet de sécurité : même si la désactivation de l'ancien DM (dans on_member_join) a échoué
        # silencieusement, un clic sur un VIEUX bouton ne doit jamais agir sur des données périmées.
        # Seul le dernier DM mémorisé est valide.
        departure = db.get_departure(user_id, guild_id)
        if departure is None or departure["last_decision_message_id"] != interaction.message.id:
            await interaction.response.send_message(
                "⚠️ Cette demande n'est plus valide (une plus récente existe).", ephemeral=True
            )
            return

        guild = self.bot.get_guild(guild_id)
        mention = f"<@{user_id}>"
        if restore:
            if guild is not None:
                await restore_player_roles(self.bot, user_id, guild)
            db.delete_departure(user_id)
            await interaction.response.edit_message(
                content=f"✅ Tout a été restauré pour {mention}.", embed=None, view=None
            )
            try:
                player = self.bot.get_user(user_id) or await self.bot.fetch_user(user_id)
                if player is not None:
                    await player.send("✅ Bon retour ! Tout ce que tu avais a été restauré.")
            except (discord.Forbidden, discord.HTTPException):
                pass
        else:
            if guild is not None:
                await purge_player_completely(self.bot, user_id, guild)
            db.delete_departure(user_id)
            await interaction.response.edit_message(
                content=f"❌ Tout a été supprimé pour {mention}.", embed=None, view=None
            )

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        # Boutons à custom_id dynamique (par joueur) : dispatch par préfixe, persistant après redémarrage.
        if interaction.type != discord.InteractionType.component:
            return
        custom_id = interaction.data.get("custom_id", "")
        if custom_id.startswith("depart_start_creation:"):
            await handle_start_creation(interaction, custom_id)
        elif custom_id.startswith("depart_delete_char:"):
            await self.handle_delete_char(interaction, custom_id)
        elif custom_id.startswith("depart_delete_confirm:"):
            await self.handle_delete_confirm(interaction, custom_id)
        elif custom_id.startswith("depart_delete_cancel:"):
            await self.handle_delete_cancel(interaction, custom_id)
        elif custom_id.startswith("depart_reroll_energie:"):
            await handle_reroll_energie(interaction, custom_id)
        elif custom_id.startswith("depart_roll_rct:"):
            await handle_roll_rct(interaction, custom_id)
        elif custom_id.startswith("depart_reroll_rct:"):
            await handle_reroll_rct(interaction, custom_id)
        elif custom_id.startswith("depart_start_fiche:"):
            await handle_start_fiche(interaction, custom_id)
        elif custom_id.startswith("depart_histoire_oui:"):
            await handle_histoire(interaction, custom_id, "oui")
        elif custom_id.startswith("depart_histoire_non:"):
            await handle_histoire(interaction, custom_id, "non")
        elif custom_id.startswith("depart_grade_select:"):
            await handle_grade_select(interaction, custom_id)
        elif custom_id.startswith("depart_fiche_valide:"):
            await handle_fiche_valide(interaction, custom_id)
        elif custom_id.startswith("depart_fiche_refuse:"):
            await handle_fiche_refuse(interaction, custom_id)
        elif custom_id.startswith("player_return_yes:"):
            await self.handle_player_return_decision(interaction, custom_id, restore=True)
        elif custom_id.startswith("player_return_no:"):
            await self.handle_player_return_decision(interaction, custom_id, restore=False)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # Réponses texte/image aux questions de la fiche.
        if message.author.bot or message.guild is None:
            return

        # Suppression de personnage : on attend le numéro de slot dans le même salon.
        pending_channel = self._awaiting_delete.get(message.author.id)
        if pending_channel is not None and message.channel.id == pending_channel:
            await self.handle_delete_slot_answer(message)
            return

        progress = get_progress(message.author.id)
        if progress.get("fiche_status") != "in_progress":
            return
        if message.channel.id != progress.get("origin_channel_id"):
            return

        stage = progress.get("fiche_stage")
        if stage in ("nom", "prenom", "age", "histoire_text"):
            await handle_fiche_text_answer(self.bot, message, progress, stage)
        elif stage == "portrait":
            await handle_fiche_portrait(self.bot, message, progress)
        # stage == "histoire_ask" : on attend un clic de bouton, on ignore le texte.

    async def handle_delete_char(self, interaction: discord.Interaction, custom_id: str):
        target_uid = int(custom_id.split(":", 1)[1])
        if interaction.user.id != target_uid:
            await interaction.response.send_message("Ce bouton ne t'est pas destiné.", ephemeral=True)
            return

        rows = db.get_validated_characters(interaction.user.id, interaction.guild.id)
        if not rows:
            await interaction.response.send_message(
                "Tu n'as aucun personnage à supprimer.", ephemeral=True
            )
            return

        lines = "\n".join(f"**Slot {r['slot_number']}** — {r['character_name']}" for r in rows)
        embed = discord.Embed(
            title="🗑️ Supprimer un personnage",
            description=(
                f"{lines}\n\n"
                "Quel numéro de slot veux-tu supprimer ? Réponds avec **1**, **2** ou **3**."
            ),
            color=discord.Color.red(),
        )
        self._awaiting_delete[interaction.user.id] = interaction.channel.id
        await interaction.response.send_message(embed=embed)

    async def handle_delete_slot_answer(self, message: discord.Message):
        content = message.content.strip()
        rows = db.get_validated_characters(message.author.id, message.guild.id)
        filled = {r["slot_number"] for r in rows}

        if not content.isdigit() or int(content) not in (1, 2, 3):
            await message.channel.send("Merci de répondre avec **1**, **2** ou **3**.")
            return

        num = int(content)
        if num not in filled:
            await message.channel.send(
                f"Aucun personnage sur le slot {num}. Réponds avec le numéro d'un slot occupé."
            )
            return

        # Slot valide : on demande confirmation avant toute suppression définitive.
        self._awaiting_delete.pop(message.author.id, None)
        char = next((r for r in rows if r["slot_number"] == num), None)
        char_name = char["character_name"] if char else "?"
        embed = discord.Embed(
            title="⚠️ Confirmation de suppression",
            description=(
                f"Es-tu sûr de vouloir supprimer définitivement le personnage du slot "
                f"{num} (**{char_name}**) ? Cette action est irréversible."
            ),
            color=discord.Color.red(),
        )
        await message.channel.send(embed=embed, view=DeleteConfirmView(message.author.id, num))

    async def handle_delete_confirm(self, interaction: discord.Interaction, custom_id: str):
        parts = custom_id.split(":")
        target_uid = int(parts[1])
        slot = int(parts[2])
        if interaction.user.id != target_uid:
            await interaction.response.send_message("Ce bouton ne t'est pas destiné.", ephemeral=True)
            return
        # Récupère l'id du personnage AVANT suppression, pour le nettoyage en cascade.
        with db.get_connection() as conn:
            row = conn.execute(
                "SELECT id FROM validated_characters WHERE user_id = ? AND guild_id = ? AND slot_number = ?",
                (target_uid, interaction.guild.id, slot),
            ).fetchone()
        character_id = row["id"] if row else None

        # Conséquences côté ordre AVANT la cascade (mêmes règles que le départ du serveur / le clic
        # « Virer ») : si le perso était éducateur, redistribue ses disciples ; s'il était disciple,
        # clôt son contrat. Point d'entrée unique de cogs/ordre.py.
        if character_id is not None:
            ordre_cog = self.bot.get_cog("Ordre")
            if ordre_cog is not None:
                try:
                    await ordre_cog.handle_order_departure(character_id, interaction.guild)
                except Exception as e:
                    print(f"[suppression] conséquences ordre pour le perso {character_id} : {e}")

        db.delete_validated_character(target_uid, interaction.guild.id, slot)
        if character_id is not None:
            delete_character_cascade(character_id)

        # Renumérotation automatique des slots restants pour qu'ils soient consécutifs à partir de 1.
        # La renumérotation des slots est automatique, mais la réattribution des VRAIS rôles Discord
        # reste manuelle, gérée par le staff via un futur outil de la commande /profil.
        slot_changes = db.renumber_character_slots(target_uid, interaction.guild.id)

        await interaction.response.edit_message(
            content=f"Le personnage du slot {slot} a été supprimé définitivement.",
            embed=None, view=None,
        )

        # Informe le joueur des slots qui ont été renumérotés (aucun rôle Discord n'est touché).
        for _cid, old_slot, new_slot in slot_changes:
            try:
                await interaction.channel.send(
                    f"<@{target_uid}> Ton personnage du slot {old_slot} devient maintenant le slot "
                    f"{new_slot}. Contacte le staff si tu veux qu'il récupère les rôles Discord réels."
                )
            except discord.HTTPException:
                pass

    async def handle_delete_cancel(self, interaction: discord.Interaction, custom_id: str):
        target_uid = int(custom_id.split(":")[1])
        if interaction.user.id != target_uid:
            await interaction.response.send_message("Ce bouton ne t'est pas destiné.", ephemeral=True)
            return
        await interaction.response.edit_message(content="Suppression annulée.", embed=None, view=None)

    @tasks.loop(seconds=30)
    async def fiche_expiry_loop(self):
        now_iso = datetime.utcnow().isoformat()
        for row in db.get_expired_fiches(now_iso):
            uid = row["user_id"]
            update_progress(
                uid, fiche_status="not_started", fiche_stage=None,
                nom=None, prenom=None, age=None, histoire=None, portrait_path=None,
            )
            channel = self.bot.get_channel(row["origin_channel_id"])
            if channel:
                try:
                    await channel.send(
                        f"<@{uid}> Le temps est écoulé, ta fiche a été annulée. Tu peux recommencer en "
                        "cliquant sur le bouton \"Faire ma fiche\" ci dessus."
                    )
                except discord.HTTPException:
                    pass

    @fiche_expiry_loop.before_loop
    async def _before_fiche_expiry(self):
        await self.bot.wait_until_ready()

    @app_commands.command(name="départ", description="Démarre la création de ton personnage")
    async def depart(self, interaction: discord.Interaction):
        # Aucune condition de rôle : la commande est ouverte à tous les membres du serveur.
        # (Le rôle DEPART_ROLE_ID reste utilisé par cogs/ticket.py pour les permissions d'upload.)

        # Écran de sélection de personnage (3 slots).
        rows = db.get_validated_characters(interaction.user.id, interaction.guild.id)
        by_slot = {row["slot_number"]: row for row in rows}

        slots = []
        for n in (1, 2, 3):
            row = by_slot.get(n)
            if row:
                htype = row["hybride_type"] if "hybride_type" in row.keys() else None
                camp_label = format_camp_label(row["camp"], htype)
                clan = row["clan"]
                camp_clan = f"{camp_label} — {clan}" if clan else camp_label
                portrait_path = row["portrait_path"] if "portrait_path" in row.keys() else None
                slots.append({
                    "filled": True,
                    "name": row["character_name"],
                    "camp_clan": camp_clan,
                    "portrait_path": portrait_path,
                })
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

        any_filled = any(s["filled"] for s in slots)

        # Tous les slots pris : pas de création possible, mais on propose la suppression.
        if all(s["filled"] for s in slots):
            await interaction.channel.send(
                "Tous tes emplacements sont pris. Tu ne peux pas créer de nouveau personnage pour le moment.",
                view=SelectionView(interaction.user.id, first_free=None, show_delete=True),
            )
            return

        # Sinon : bouton vers le premier slot libre (+ suppression si au moins un slot occupé).
        first_free = next(n for n in (1, 2, 3) if not slots[n - 1]["filled"])
        await interaction.channel.send(
            embed=discord.Embed(
                description="Prêt à créer ton personnage ? Clique sur le bouton ci-dessous.",
                color=discord.Color.blurple(),
            ),
            view=SelectionView(interaction.user.id, first_free=first_free, show_delete=any_filled),
        )


async def setup(bot):
    await bot.add_cog(Depart(bot))
