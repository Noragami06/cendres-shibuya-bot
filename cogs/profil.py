import asyncio
import io
import math
import os
import random
import uuid
from datetime import datetime

from PIL import Image
import discord
from discord import app_commands
from discord.ext import commands

from cogs.utils import database as db
from cogs.utils.image_gen import (
    generate_profil_image, generate_stats_image, generate_relations_image, generate_technique_image,
    generate_technique_detail_image, TECHDET_CLASS_COLORS, generate_territoire_image,
)
# Barème validé des classes de sorts + stades de Maîtrise RCT + plafonds de maîtrise (source unique
# partagée avec le check de cohérence).
from cogs.utils.coherence_check import (
    SPELL_CLASS_VALUES, RCT_STAGES, MASTERY_EO_MAX_LEVEL, MASTERY_SORT_MAX_LEVEL,
)
# Réutilise les helpers déjà en place (personnages / comptes / couleur).
from cogs.banque import get_characters, get_character, PHOENIX_COLOR
# Réutilise la validation + téléchargement + compression d'image du parcours /depart, et le rôle staff.
from cogs.depart import (
    FICHE_STAFF_ROLE_ID, PORTRAIT_DIR, compress_portrait, _download_image_bytes, _resolve_portrait_url,
    SANS_CLAN_ROLE_ID, GRADE_LABEL_TO_ROLE_ID, GRADE_ROLES, resolve_role_point_ids, sync_role_points,
)

# ---------- Constantes ----------
WAIT_TIMEOUT = 300  # secondes d'attente d'une réponse texte
BACKGROUND_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "backgrounds")
TERRITOIRE_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "territoires")
TERRITOIRE_MAX_BYTES = 8 * 1024 * 1024  # 8 Mo : même limite que /réserv-appa (GIF jamais recompressé)
# Appellations possibles du Territoire (numéro affiché -> TEXTE EXACT stocké en base).
TERRITOIRE_APPELLATIONS = {
    "1": "Extension du Territoire",  # VF
    "2": "Ryōiki Tenkai",           # VO
    "3": "Domain Expansion",         # VA
}
# Phrases d'activation reconnues en chat (les 3 appellations, fixes). Cache construit au démarrage du cog.
TERRITOIRE_PHRASES = set(TERRITOIRE_APPELLATIONS.values())
# Valeurs de BASE fixes (niveau 1) attribuées à TOUT territoire à sa création (ensuite modifiables par le
# staff). La progression par paliers de niveau s'applique par-dessus À L'AFFICHAGE, jamais stockée.
TERRITOIRE_DEFAULT_COUT_EO_PCT = 45  # 45 % de la réserve (base)
TERRITOIRE_DUREE_MIN = 3             # minimum absolu (aussi la valeur de base)
TERRITOIRE_DEFAULT_DUREE_TOURS = TERRITOIRE_DUREE_MIN  # 3 tours
# Maîtrise Territoire : dérivée des points de stat « territoire » (comme EO/Sort/RCT), plafond 105.
MASTERY_TERRITOIRE_MAX_LEVEL = 105
# Progression par paliers : tous les 15 niveaux de Maîtrise Territoire, +1 tour et (selon la réserve d'EO)
# une réduction du coût. Le coût ne descend jamais sous TERRITOIRE_MIN_COUT_PCT.
TERRITOIRE_LEVEL_STEP = 15
TERRITOIRE_MIN_COUT_PCT = 5
PROFILE_IMG_DIR = os.path.join(os.path.dirname(__file__), "..", "temp", "profil_images")

# Boutons sous le profil. "stats"/"relation"/"technique" ont un vrai écran ; "📜 Sorts" a été retiré
# (doublon avec ⚡ Technique). Restent en placeholder : Territoire et Armes maudites.
TODO_SECTIONS = [
    ("stats", "📊 Stats"),
    ("relation", "🤝 Relation"),
    ("technique", "⚡ Technique"),
    ("territoire", "🗺️ Territoire"),
    ("armes", "🗡️ Armes maudites"),
]


# Couleur de repli d'un sort principal sans couleur enregistrée (or, cohérent avec le pillow Technique).
TECHNIQUE_DEFAULT_COLOR = (232, 197, 121)
# Palette de couleurs distinctes attribuées automatiquement aux sorts principaux (1 par slot, max 4),
# pour rester cohérent avec le pillow d'ensemble generate_technique_image.
TECHNIQUE_SORT_PALETTE = [
    (220, 90, 60),    # braise
    (90, 150, 240),   # azur
    (170, 90, 240),   # améthyste
    (60, 200, 150),   # jade
]
# Les 5 classes de sorts valides (saisies par le joueur lors du flux guidé).
TECHNIQUE_VALID_CLASSES = ("4", "3", "2", "1", "S")
# Seul cet utilisateur précis peut valider une demande de création de techniques (bouton ✅ Confirmer).
TECHNIQUE_VALIDATOR_ID = 396615332346855428
# Sentinelle renvoyée par les helpers de saisie quand le joueur tape « cancel » (distinct de None =
# timeout). Permet d'annuler le flux de création de techniques à n'importe quelle étape.
_CANCELLED = object()
# Limites de longueur des champs texte libres du flux de création guidée des techniques.
TECHNIQUE_NAME_MAX = 50        # nom d'un sort principal OU secondaire
TECHNIQUE_DESC_MAX = 300       # description d'un sort secondaire
TECHNIQUE_FAIBLESSE_MAX = 300  # faiblesse d'un sort secondaire


def _is_staff(member) -> bool:
    return any(r.id == FICHE_STAFF_ROLE_ID for r in getattr(member, "roles", []))


def _tmp_profile(prefix: str) -> str:
    os.makedirs(PROFILE_IMG_DIR, exist_ok=True)
    return os.path.join(PROFILE_IMG_DIR, f"{prefix}_{uuid.uuid4().hex}.png")


def _now() -> str:
    return datetime.utcnow().isoformat()


# =====================================================================
# RÈGLES TEMPORAIRES DE NIVEAU / XP (niveau GÉNÉRAL du profil, inchangé)
# =====================================================================
def compute_xp_max_for_level(level: int) -> int:
    # Formule exponentielle partagée (source unique : database.xp_required_for_level).
    return db.xp_required_for_level(level)


def _level_from_xp_max(xp_max: int) -> int:
    """Inverse (approché) de compute_xp_max_for_level : retrouve le niveau dont l'xp_max se rapproche
    le plus de la valeur donnée. Utilisé quand le staff fixe directement xp_max dans « Modifier le profil »."""
    if xp_max <= db.XP_BASE:
        return 1
    return max(1, round(1 + math.log(xp_max / db.XP_BASE) / math.log(db.XP_GROWTH)))


def sync_level_and_xp(level=None, xp_actuel=None, xp_max=None,
                      cur_level=1, cur_xp_actuel=0, cur_xp_max=1000):
    """Recalcule (level, xp_actuel, xp_max) du NIVEAU GÉNÉRAL du profil de façon cohérente. Utilisé
    uniquement pour les colonnes character_profiles.level/xp_actuel/xp_max (indépendant des stats)."""
    if level is not None:
        level = max(1, int(level))
        new_max = compute_xp_max_for_level(level)
        base = cur_xp_actuel if xp_actuel is None else int(xp_actuel)
        return level, max(0, min(base, new_max)), new_max
    if xp_max is not None:
        new_max = max(1, int(xp_max))
        new_level = _level_from_xp_max(new_max)
        base = cur_xp_actuel if xp_actuel is None else int(xp_actuel)
        return new_level, max(0, min(base, new_max)), new_max
    if xp_actuel is not None:
        return cur_level, max(0, min(int(xp_actuel), cur_xp_max)), cur_xp_max
    return cur_level, cur_xp_actuel, cur_xp_max


# =====================================================================
# STATISTIQUES : clés, noms, conversions points <-> niveau/XP (utilitaire partagé)
# =====================================================================
STAT_KEYS = ["force", "rct", "vitesse", "territoire", "endurance", "sorts", "armes_maudites", "energie_occulte"]
STAT_DISPLAY_NAMES = {
    "force": "Force", "rct": "RCT", "vitesse": "Vitesse", "territoire": "Territoire",
    "endurance": "Endurance", "sorts": "Sorts", "armes_maudites": "Armes maudites",
    "energie_occulte": "Énergie occulte",
}
# sorts et energie_occulte exclus pour la répartition JOUEUR (le staff peut tout viser via les buffs).
PLAYER_DISTRIBUABLE_STATS = ["force", "rct", "vitesse", "territoire", "endurance", "armes_maudites"]

# Couleurs des 8 stats pour generate_stats_image.
STAT_COLORS = {
    "force": (215, 80, 80), "rct": (100, 220, 150), "vitesse": (90, 150, 240),
    "territoire": (190, 100, 240), "endurance": (230, 170, 60), "sorts": (230, 220, 70),
    "armes_maudites": (230, 140, 60), "energie_occulte": (100, 160, 230),
}


# =====================================================================
# CONVERSION POINTS <-> NIVEAU/XP DES STATS Force / Vitesse / Défense (Endurance)
# Courbe exponentielle partagée (db.xp_required_for_level), avec ratio de conversion et plafond dur.
# Remplace l'ancien modèle plat (1000 pts/niveau). Les 5 autres stats (RCT, Territoire, Sorts, Armes
# maudites, Énergie occulte) NE passent pas par ici.
# =====================================================================
STAT_XP_RATIO = 25                 # 1 point de Stats = 25 XP
STAT_MAX_LEVEL = 150               # plafond dur
STAT_LEVEL_BONUS_MULTIPLIER = 50   # Option B validée : bonus = niveau * 50
# Seules ces 3 stats utilisent la courbe exponentielle + bonus de niveau (Défense = Endurance).
STAT_LEVEL_BONUS_KEYS = ("force", "vitesse", "endurance")


def points_to_level_xp_capped(total_points: int, max_level: int, ratio: int = STAT_XP_RATIO) -> tuple:
    """Convertit un total de points en (level, xp_actuel, xp_max) sur la courbe exponentielle partagée,
    plafonné à `max_level`. Générique : sert aux stats (plafond 150) ET aux Maîtrises EO/Sort/RCT
    (plafonds propres)."""
    total_xp = total_points * ratio
    level = 1
    remaining = total_xp
    while level < max_level and remaining >= db.xp_required_for_level(level):
        remaining -= db.xp_required_for_level(level)
        level += 1
    if level >= max_level:
        level = max_level
        xp_max = db.xp_required_for_level(max_level)
        xp_actuel = xp_max  # barre pleine, plafond atteint
    else:
        xp_actuel = remaining
        xp_max = db.xp_required_for_level(level)
    return level, xp_actuel, xp_max


def points_to_level_xp_stat(total_points: int) -> tuple:
    """Force / Vitesse / Défense : conversion plafonnée à STAT_MAX_LEVEL (wrapper générique)."""
    return points_to_level_xp_capped(total_points, STAT_MAX_LEVEL)


def compute_points_manquants(xp_actuel: int, xp_max: int, level: int, max_level: int,
                             ratio: int = STAT_XP_RATIO) -> str:
    """Retourne le texte à afficher sous la jauge : 'MAX' si le plafond est atteint,
    sinon 'X point(s) manquant(s)' calculé depuis l'XP manquant converti en points."""
    if level >= max_level:
        return "MAX"
    xp_manquant = xp_max - xp_actuel
    points_manquants = max(1, -(-xp_manquant // ratio))  # arrondi au point supérieur, jamais 0 tant que ce n'est pas MAX
    return f"{points_manquants} point{'s' if points_manquants > 1 else ''} manquant{'s' if points_manquants > 1 else ''}"


def level_xp_to_points_stat(level: int, xp_actuel: int) -> int:
    """Conversion inverse (utilisée quand le staff édite Level/XP force directement)."""
    level = min(level, STAT_MAX_LEVEL)
    total_xp = xp_actuel
    for lvl in range(1, level):
        total_xp += db.xp_required_for_level(lvl)
    return round(total_xp / STAT_XP_RATIO)


def compute_stat_level_bonus(level: int) -> int:
    return level * STAT_LEVEL_BONUS_MULTIPLIER


def compute_tranche(base_pts: int):
    """(numero_tranche, pct_dans_la_tranche, texte) selon le PALIER de base_pts (hors buffs)."""
    brackets = [(1, 0, 10), (2, 10, 100), (3, 100, 1000), (4, 1000, 10000), (5, 10000, 100000)]
    for num, lo, hi in brackets:
        if base_pts < hi or num == brackets[-1][0]:
            pct = round((base_pts - lo) / (hi - lo) * 100) if hi > lo else 0
            pct = max(0, min(100, pct))
            return num, pct, f"Tranche {num} : {lo}-{hi}"


async def get_stat_base(character_id, stat_key) -> int:
    return db.get_stat_base_pts(character_id, stat_key)


async def get_stat_total(character_id, stat_key) -> int:
    """base_pts + somme des buffs actifs pour cette stat précise."""
    return db.get_stat_base_pts(character_id, stat_key) + db.sum_buff_points(character_id, stat_key)


# =====================================================================
# MÉTHODE STANDARD DU PROJET : rôle appliqué à un PERSONNAGE précis
# =====================================================================
async def character_has_role(guild, member, character_id, role_id) -> bool:
    """MÉTHODE STANDARD pour vérifier si un rôle (camp / clan / grade / RCT / ...) s'applique à un
    PERSONNAGE précis : slot 1 -> vrais rôles Discord (member.roles) ; slots 2/3 -> rôles virtuels
    (character_virtual_roles). Tout futur système DOIT passer par ici plutôt que member.roles."""
    with db.get_connection() as conn:
        row = conn.execute(
            "SELECT slot_number FROM validated_characters WHERE id = ?", (character_id,)
        ).fetchone()
    if row is None:
        return False
    if row["slot_number"] == 1:
        return member is not None and any(r.id == role_id for r in getattr(member, "roles", []))
    with db.get_connection() as conn:
        vr = conn.execute(
            "SELECT 1 FROM character_virtual_roles WHERE character_id = ? AND role_id = ?",
            (character_id, role_id),
        ).fetchone()
    return vr is not None


async def get_current_rct_stage(guild, character_id):
    """Retourne 'avancee', 'bonne', 'moyenne', ou None si aucun rôle RCT détecté. Vérifie du stade le
    plus haut au plus bas (un joueur en 'avancee' pourrait techniquement avoir gardé un ancien rôle
    'moyenne', on privilégie le plus haut). Réutilise character_has_role (réel slot 1 / virtuel slot 2-3)."""
    # Résout le membre Discord propriétaire (nécessaire pour un personnage slot 1 : vrais rôles Discord).
    with db.get_connection() as conn:
        row = conn.execute(
            "SELECT user_id FROM validated_characters WHERE id = ?", (character_id,)
        ).fetchone()
    member = None
    if row is not None and guild is not None:
        member = guild.get_member(row["user_id"])
    for stage in ("avancee", "bonne", "moyenne"):
        role_id = RCT_STAGES[stage]["role_id"]
        if await character_has_role(guild, member, character_id, role_id):
            return stage
    return None


# =====================================================================
# MAÎTRISES EO / Sort / RCT — DÉRIVÉES À LA VOLÉE DES POINTS DE STATS
# (energie_occulte_pts / sorts_pts / rct_pts), exactement comme Force/Vitesse/Défense.
# Plus aucun niveau de maîtrise n'est stocké séparément (colonnes mastery_*_level obsolètes).
# =====================================================================
async def get_mastery_eo(character_id) -> tuple:
    total = await get_stat_total(character_id, "energie_occulte")
    return points_to_level_xp_capped(total, MASTERY_EO_MAX_LEVEL)


async def get_mastery_sort(character_id) -> tuple:
    total = await get_stat_total(character_id, "sorts")
    return points_to_level_xp_capped(total, MASTERY_SORT_MAX_LEVEL)


async def get_mastery_rct(guild, character_id) -> tuple:
    stage = await get_current_rct_stage(guild, character_id)
    if stage is None:
        return 1, 0, db.xp_required_for_level(1)  # aucun rôle RCT détecté, comme avant
    total = await get_stat_total(character_id, "rct")
    max_level = RCT_STAGES[stage]["max_level"]
    level, xp_actuel, xp_max = points_to_level_xp_capped(total, max_level)
    # Quête de progression débloquée au niveau max du stade (sauf 'avancee', le sommet). Simple flag
    # stocké : AUCUNE notification/DM n'est envoyée — le staff consulte l'info manuellement (RP).
    if level >= max_level and RCT_STAGES[stage]["next"] is not None:
        db.update_profile(character_id, rct_quest_available=1)
    return level, xp_actuel, xp_max


async def get_mastery_territoire(character_id) -> tuple:
    """Maîtrise Territoire dérivée des points de stat « territoire », exactement comme EO/Sort/RCT.
    Retourne (level, xp_actuel, xp_max), plafonnée à MASTERY_TERRITOIRE_MAX_LEVEL."""
    total = await get_stat_total(character_id, "territoire")
    return points_to_level_xp_capped(total, MASTERY_TERRITOIRE_MAX_LEVEL)


def compute_territoire_tours(base_tours: int, level: int) -> int:
    """Durée finale = durée de base + 1 tour par palier de 15 niveaux de Maîtrise Territoire."""
    paliers = level // TERRITOIRE_LEVEL_STEP
    return base_tours + paliers


def compute_territoire_cout(base_cout_pct: int, level: int, eo_reserve: int) -> int:
    """Coût EO final = coût de base réduit par palier de 15 niveaux, selon la taille de la réserve d'EO :
    réserve > 1000 -> -10 %/palier ; réserve ≥ 100 -> -5 %/palier ; réserve < 100 -> aucune baisse
    (seuls les tours augmentent). Ne descend jamais sous TERRITOIRE_MIN_COUT_PCT."""
    paliers = level // TERRITOIRE_LEVEL_STEP
    if eo_reserve > 1000:
        reduction_par_palier = 10
    elif eo_reserve >= 100:
        reduction_par_palier = 5
    else:
        reduction_par_palier = 0  # réserve < 100 : aucune baisse de coût, seuls les tours augmentent
    cout = base_cout_pct - (reduction_par_palier * paliers)
    return max(TERRITOIRE_MIN_COUT_PCT, cout)


# =====================================================================
# PARAMÈTRES MODIFIABLES (staff)
# =====================================================================
PARAM_ALIASES = {
    "pv_max": ["pv maximum", "pvmax", "pv max"],
    "pv_actuel": ["pv minimum", "pvmin", "pv min", "pv actuel"],
    "eo_max": ["eo maximum", "eomax", "energie occulte maximum"],
    "eo_actuel": ["eo minimum", "eomin", "energie occulte minimum", "eo actuel"],
    "level": ["level", "niveau"],
    "xp_max": ["xp maximum", "xpmax"],
    "xp_actuel": ["xp minimum", "xpmin", "xp actuel"],
    "force_pct": ["% force", "pourcentage force"],
    "force_level": ["level force", "niveau force"],
    "force_xp_max": ["xp maximum force", "xp max force"],
    "force_xp_actuel": ["xp minimum force", "xp min force"],
    "vitesse_pct": ["% vitesse", "pourcentage vitesse"],
    "vitesse_level": ["level vitesse", "niveau vitesse"],
    "vitesse_xp_max": ["xp maximum vitesse", "xp max vitesse"],
    "vitesse_xp_actuel": ["xp minimum vitesse", "xp min vitesse"],
    "defense_pct": ["% defense", "pourcentage defense"],
    "defense_level": ["level defense", "niveau defense"],
    "defense_xp_max": ["xp maximum defense", "xp max defense"],
    "defense_xp_actuel": ["xp minimum defense", "xp min defense"],
    "clan": ["clan"],
    "rang": ["rang", "grade"],
    "victoires": ["victoire", "victoires"],
    "defaites": ["defaite", "defaites"],
    "nuls": ["nul", "nuls"],
    "maitrise_eo_level": ["level maitrise eo", "niveau maitrise eo"],
    "image": ["image", "portrait"],
    "fond": ["fond", "arriere plan", "background"],
    # --- Stats (points) ---
    "stats_force": ["stats force"],
    "stats_vitesse": ["stats vitesse"],
    "stats_endurance": ["stats endurance"],
    "stats_armes_maudites": ["stats armes maudites"],
    "stats_rct": ["stats rct"],
    "stats_territoire": ["stats territoire"],
    "stats_sorts": ["stats sorts"],
    "stats_energie_occulte": ["stats energie occulte"],
    "buff": ["buff"],
    "points_stats": ["points de stats", "points restants"],
    # --- Rôles (réels slot 1 / virtuels slot 2-3, avec synchro du barème de points) ---
    "roles_ajouter": ["ajouter des roles", "ajouter roles", "roles ajouter"],
    "roles_retirer": ["retirer des roles", "retirer roles", "roles retirer"],
    # --- Techniques (sorts principaux / secondaires) ---
    "nom_sort_principal": ["nom sort principal", "renommer sort principal"],
    "ajouter_xp_sort": ["ajouter xp", "ajouter xp sort"],
    "retirer_xp_sort": ["retirer xp", "retirer xp sort"],
    "modifier_level_sort": ["modifier level", "modifier level sort", "modifier niveau sort"],
    "ajouter_sort_max": ["ajouter sort maximum", "ajouter sort max"],
    "retirer_sort_max": ["retirer sort maximum", "retirer sort max"],
    "debloquer_sort_principal": ["debloquer sort principal", "débloquer sort principal"],
    "bloquer_sort_principal": ["bloquer sort principal", "verrouiller sort principal"],
    "nom_sort_secondaire": ["nom sort secondaire", "renommer sort secondaire"],
    "niveau_requis_secondaire": ["niveau requis", "niveau requis secondaire"],
    "cout_eo_secondaire": ["cout eo", "coût eo", "cout eo secondaire"],
    "degats_secondaire": ["degats", "dégâts", "degats secondaire"],
    "debloquer_sort_secondaire": ["debloquer sort secondaire", "débloquer sort secondaire"],
    "bloquer_sort_secondaire": ["bloquer sort secondaire", "verrouiller sort secondaire"],
    # --- Territoire (déblocage / verrouillage + valeurs de combat, staff) ---
    "territoire_debloquer": ["débloquer", "debloquer"],
    "territoire_bloquer": ["bloquer"],
    "territoire_cout_eo": ["coût eo territoire", "cout eo territoire"],
    "territoire_duree": ["durée en tours", "duree en tours"],
}

# Paramètres du menu staff qui passent par le flux dédié « Techniques ».
TECHNIQUE_EDIT_PARAMS = {
    "nom_sort_principal", "ajouter_xp_sort", "retirer_xp_sort", "modifier_level_sort",
    "ajouter_sort_max", "retirer_sort_max", "debloquer_sort_principal", "bloquer_sort_principal",
    "nom_sort_secondaire", "niveau_requis_secondaire", "cout_eo_secondaire", "degats_secondaire",
    "debloquer_sort_secondaire", "bloquer_sort_secondaire",
}

# Paramètres du menu staff qui passent par le flux dédié « Territoire ».
TERRITOIRE_EDIT_PARAMS = {
    "territoire_debloquer", "territoire_bloquer", "territoire_cout_eo", "territoire_duree",
}

# "stats_X" -> clé de stat (écriture ABSOLUE dans character_stats).
STATS_PARAM_MAP = {
    "stats_force": "force", "stats_vitesse": "vitesse", "stats_endurance": "endurance",
    "stats_armes_maudites": "armes_maudites", "stats_rct": "rct", "stats_territoire": "territoire",
    "stats_sorts": "sorts", "stats_energie_occulte": "energie_occulte",
}
# Anciens paramètres level/xp/% -> clé de stat (Défense = Endurance). Convertis en points.
LEGACY_STAT_MAP = {"force": "force", "vitesse": "vitesse", "defense": "endurance"}
# Paramètres dont la modification impacte l'écran Stats (pour régénérer aussi le pillow Stats).
_STATS_AFFECTING = set(STATS_PARAM_MAP) | {"points_stats"} | {
    f"{lg}_{sfx}" for lg in LEGACY_STAT_MAP for sfx in ("pct", "level", "xp_max", "xp_actuel")
}


def match_params(text: str):
    """Résolution d'un nom de paramètre : match exact d'un alias, sinon match par préfixe."""
    t = text.strip().lower()
    exact = [k for k, aliases in PARAM_ALIASES.items() if any(a == t for a in aliases)]
    if exact:
        return exact
    return [k for k, aliases in PARAM_ALIASES.items() if any(a.startswith(t) for a in aliases)]


def match_stat(text: str, allowed_keys):
    """Résolution d'une stat par nom d'affichage ou clé (exact puis préfixe), parmi allowed_keys."""
    t = text.strip().lower()
    exact = [k for k in allowed_keys if STAT_DISPLAY_NAMES[k].lower() == t or k == t]
    if exact:
        return exact
    return [k for k in allowed_keys if STAT_DISPLAY_NAMES[k].lower().startswith(t) or k.startswith(t)]


def _parse_int(raw: str, minimum=0):
    c = raw.strip().replace(" ", "").replace(",", "")
    if c.lstrip("-").isdigit() and int(c) >= minimum:
        return int(c)
    return None


def _parse_any_int(raw: str):
    """Entier signé (autorise les valeurs négatives, ex: malus de buff)."""
    c = raw.strip().replace(" ", "").replace(",", "")
    if c not in ("", "-") and c.lstrip("-").isdigit():
        return int(c)
    return None


def _is_cancel(text: str) -> bool:
    """Vrai si le staff a écrit exactement « cancel » (casse et espaces ignorés) : annulation du flux."""
    return (text or "").strip().lower() == "cancel"


# =====================================================================
# VUES EN SESSION
# =====================================================================
class ProfilCharacterSelect(discord.ui.Select):
    def __init__(self, chars, invoker_id):
        self.invoker_id = invoker_id
        options = [
            discord.SelectOption(label=f"Slot {c['slot_number']} — {c['character_name']}", value=str(c["id"]))
            for c in chars
        ]
        super().__init__(placeholder="Choisis un personnage...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.invoker_id:
            await interaction.response.send_message("Ce menu ne t'appartient pas.", ephemeral=True)
            return
        self.view.result = int(self.values[0])
        await interaction.response.edit_message(view=None)
        self.view.stop()


class ProfilCharacterSelectView(discord.ui.View):
    def __init__(self, chars, invoker_id):
        super().__init__(timeout=WAIT_TIMEOUT)
        self.result = None
        self.add_item(ProfilCharacterSelect(chars, invoker_id))


class BuffChoiceView(discord.ui.View):
    """Ajouter / Retirer un buff — vue en session (le staff est déjà dans son flux d'édition verrouillé)."""

    def __init__(self, owner_id: int):
        super().__init__(timeout=WAIT_TIMEOUT)
        self.owner_id = owner_id
        self.result = None
        add = discord.ui.Button(label="Ajouter", emoji="➕", style=discord.ButtonStyle.success)
        rem = discord.ui.Button(label="Retirer", emoji="➖", style=discord.ButtonStyle.danger)
        add.callback = self._add
        rem.callback = self._rem
        self.add_item(add)
        self.add_item(rem)

    async def _guard(self, interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Ce choix ne t'appartient pas.", ephemeral=True)
            return False
        return True

    async def _add(self, interaction):
        if not await self._guard(interaction):
            return
        self.result = "add"
        await interaction.response.edit_message(view=None)
        self.stop()

    async def _rem(self, interaction):
        if not await self._guard(interaction):
            return
        self.result = "remove"
        await interaction.response.edit_message(view=None)
        self.stop()


# =====================================================================
# VUES PERSISTANTES (custom_id dynamiques -> listener on_interaction)
# =====================================================================
class ProfilStaffChoiceView(discord.ui.View):
    def __init__(self, user_id: int):
        super().__init__(timeout=None)
        self.add_item(discord.ui.Button(
            label="Gérer mon profil", emoji="👤", style=discord.ButtonStyle.primary,
            custom_id=f"profil_self:{user_id}"))
        self.add_item(discord.ui.Button(
            label="Gérer celui d'un autre", emoji="🔧", style=discord.ButtonStyle.secondary,
            custom_id=f"profil_other:{user_id}"))


class ProfileView(discord.ui.View):
    """Boutons persistants sous l'image de profil (mode consultation joueur)."""

    def __init__(self, character_id: int, user_id: int, slot_number: int):
        super().__init__(timeout=None)
        if slot_number in (2, 3):
            self.add_item(discord.ui.Button(
                label="Voir rôles", emoji="🎭", style=discord.ButtonStyle.secondary,
                custom_id=f"profil_roles:{character_id}:{user_id}", row=0))
        self.add_item(discord.ui.Button(
            label="Ajouter un fond", emoji="🖼️", style=discord.ButtonStyle.primary,
            custom_id=f"profil_fond:{character_id}:{user_id}", row=0))
        for i, (key, label) in enumerate(TODO_SECTIONS):
            self.add_item(discord.ui.Button(
                label=label, style=discord.ButtonStyle.secondary,
                custom_id=f"profil_todo_{key}:{character_id}:{user_id}", row=1 + i // 3))


class StatsPageView(discord.ui.View):
    """Bouton de répartition sous l'image Stats (présent seulement s'il reste des points)."""

    def __init__(self, character_id: int, user_id: int):
        super().__init__(timeout=None)
        self.add_item(discord.ui.Button(
            label="Répartir les points", emoji="🎯", style=discord.ButtonStyle.success,
            custom_id=f"stats_repartir:{character_id}:{user_id}"))


class TechniqueOverviewView(discord.ui.View):
    """Boutons '🔍 {nom}' sous le pillow d'ensemble ⚡ Technique : un par sort principal REMPLI (max 4),
    menant à la vue détaillée de ses 8 sorts secondaires. Aucun bouton pour un slot vide/verrouillé."""

    def __init__(self, character_id: int, user_id: int, principals: list):
        super().__init__(timeout=None)
        # principals : liste de (sort_id, nom) des slots principaux réellement présents en base.
        for sort_id, name in principals[:4]:
            label = name if len(name) <= 40 else name[:37] + "..."
            self.add_item(discord.ui.Button(
                label=label, emoji="🔍", style=discord.ButtonStyle.secondary,
                custom_id=f"tech_detail:{character_id}:{user_id}:{sort_id}"))


class TechniqueCreationRequestView(discord.ui.View):
    """Bouton '✅ Confirmer' persistant sous la demande de création de techniques, publiée DANS LE SALON
    DU JOUEUR (avec ping staff). Cliquable UNIQUEMENT par un membre ayant FICHE_STAFF_ROLE_ID — jamais
    le joueur lui même. Le custom_id porte le personnage ciblé et le user_id du joueur d'origine."""

    def __init__(self, character_id: int, player_user_id: int):
        super().__init__(timeout=None)
        self.add_item(discord.ui.Button(
            label="Confirmer", emoji="✅", style=discord.ButtonStyle.success,
            custom_id=f"tech_create_confirm:{character_id}:{player_user_id}"))


class TechniqueDetailView(discord.ui.View):
    """Boutons sous le pillow détaillé : '◀️ Retour' (revient à la vue d'ensemble ⚡ Technique) et
    '🔍 Afficher plus' (détail texte complet d'un sort secondaire choisi par numéro)."""

    def __init__(self, character_id: int, user_id: int, sort_id: int):
        super().__init__(timeout=None)
        self.add_item(discord.ui.Button(
            label="Retour", emoji="◀️", style=discord.ButtonStyle.secondary,
            custom_id=f"tech_back:{character_id}:{user_id}"))
        self.add_item(discord.ui.Button(
            label="Afficher plus", emoji="🔍", style=discord.ButtonStyle.primary,
            custom_id=f"tech_more:{sort_id}:{user_id}"))


class ParamsPageView(discord.ui.View):
    """Pagination de l'embed des paramètres staff (visible seulement s'il y a plusieurs pages). Les
    boutons reflètent une page à l'autre ; la sélection du paramètre se fait toujours en TEXTE."""

    def __init__(self, user_id: int, page: int, total_pages: int):
        super().__init__(timeout=None)
        self.add_item(discord.ui.Button(
            label="Page précédente", emoji="◀️", style=discord.ButtonStyle.secondary,
            custom_id=f"param_page_prev:{user_id}:{page}", disabled=(page <= 0)))
        self.add_item(discord.ui.Button(
            label="Page suivante", emoji="▶️", style=discord.ButtonStyle.secondary,
            custom_id=f"param_page_next:{user_id}:{page}", disabled=(page >= total_pages - 1)))


class RelationsPageView(discord.ui.View):
    """Boutons persistants sous l'image Relations :
    - ligne 0 : pagination (uniquement si plusieurs pages ; page courante encodée dans le custom_id) ;
    - ligne 1 : '➕ Créer un lien' (toujours) et '➖ Retirer un lien' (seulement s'il existe au moins
      un lien pour ce personnage)."""

    def __init__(self, character_id: int, user_id: int, page: int, total_pages: int, has_rel: bool):
        super().__init__(timeout=None)
        if total_pages > 1:
            self.add_item(discord.ui.Button(
                label="Page précédente", emoji="◀️", style=discord.ButtonStyle.secondary,
                custom_id=f"rel_page_prev:{character_id}:{user_id}:{page}", disabled=(page <= 1), row=0))
            self.add_item(discord.ui.Button(
                label="Page suivante", emoji="▶️", style=discord.ButtonStyle.secondary,
                custom_id=f"rel_page_next:{character_id}:{user_id}:{page}", disabled=(page >= total_pages), row=0))
        self.add_item(discord.ui.Button(
            label="Créer un lien", emoji="➕", style=discord.ButtonStyle.success,
            custom_id=f"rel_create:{character_id}:{user_id}", row=1))
        if has_rel:
            self.add_item(discord.ui.Button(
                label="Retirer un lien", emoji="➖", style=discord.ButtonStyle.danger,
                custom_id=f"rel_remove:{character_id}:{user_id}", row=1))


class RelationCategoryView(discord.ui.View):
    """Choix du type de relation (Famille / Amis / Autres) — vue en session (le flux de création est
    déjà verrouillé). Au clic, les 3 boutons sont désactivés pour figer visuellement le choix."""

    CATS = [("Famille", "👨‍👩‍👧"), ("Amis", "🤝"), ("Autres", "❓")]

    def __init__(self, owner_id: int):
        super().__init__(timeout=WAIT_TIMEOUT)
        self.owner_id = owner_id
        self.result = None
        for label, emoji in self.CATS:
            btn = discord.ui.Button(label=label, emoji=emoji, style=discord.ButtonStyle.secondary)
            btn.callback = self._make_cb(label)
            self.add_item(btn)

    def _make_cb(self, category):
        async def cb(interaction):
            if interaction.user.id != self.owner_id:
                await interaction.response.send_message("Ce choix ne t'appartient pas.", ephemeral=True)
                return
            self.result = category
            for item in self.children:
                item.disabled = True
            await interaction.response.edit_message(view=self)
            self.stop()
        return cb


class ReplaceRelationView(discord.ui.View):
    """Confirmation de remplacement d'une relation existante — vue en session (flux déjà verrouillé).
    result vaut "replace" ou "cancel"."""

    def __init__(self, owner_id: int):
        super().__init__(timeout=WAIT_TIMEOUT)
        self.owner_id = owner_id
        self.result = None
        self._done = False  # anti double clic (notamment sur "✅ Remplacer")
        rep = discord.ui.Button(label="Remplacer", emoji="✅", style=discord.ButtonStyle.success)
        can = discord.ui.Button(label="Annuler", emoji="❌", style=discord.ButtonStyle.danger)
        rep.callback = self._make_cb("replace")
        can.callback = self._make_cb("cancel")
        self.add_item(rep)
        self.add_item(can)

    def _make_cb(self, result):
        async def cb(interaction):
            if interaction.user.id != self.owner_id:
                await interaction.response.send_message("Ce choix ne t'appartient pas.", ephemeral=True)
                return
            # Anti double clic : le premier clic fige le choix (retrait des boutons) ; les suivants
            # sont absorbés silencieusement pendant que le flux traite le résultat.
            if self._done:
                try:
                    await interaction.response.defer()
                except discord.HTTPException:
                    pass
                return
            self._done = True
            self.result = result
            for item in self.children:
                item.disabled = True
            await interaction.response.edit_message(view=self)
            self.stop()
        return cb


class StaffActionView(discord.ui.View):
    def __init__(self, character_id: int, user_id: int):
        super().__init__(timeout=None)
        self.add_item(discord.ui.Button(
            label="Modifier le profil", emoji="✏️", style=discord.ButtonStyle.primary,
            custom_id=f"profil_edit:{character_id}:{user_id}"))


# =====================================================================
# COG
# =====================================================================
class Profil(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._active_users = set()  # isolation des flux textuels par joueur (mémoire)
        # Répartitions de points en cours (character_id) : anti double clic sur "🎯 Répartir les points".
        self._repartir_lock = set()
        # Flux de liens en cours, clés (user_id, character_id) : anti double clic sur les boutons
        # "➕ Créer un lien" / "➖ Retirer un lien" (même principe que /shop et Répartir les points).
        self._relation_lock = set()
        # Flux "Ajouter/Retirer des rôles" en cours, clés (user_id, character_id) : empêche deux membres
        # du staff de modifier EN MÊME TEMPS les rôles du MÊME personnage (verrou par personnage).
        self._role_flow_locks = set()

    # ---------- verrou de flux ----------
    def _acquire(self, *user_ids) -> bool:
        ids = [u for u in user_ids if u is not None]
        if any(u in self._active_users for u in ids):
            return False
        self._active_users.update(ids)
        return True

    def _release(self, *user_ids):
        for u in user_ids:
            if u is not None:
                self._active_users.discard(u)

    async def wait_message(self, channel, author, timeout: int = WAIT_TIMEOUT):
        def check(m):
            return m.channel.id == channel.id and m.author.id == author.id and not m.author.bot
        try:
            return await self.bot.wait_for("message", check=check, timeout=timeout)
        except asyncio.TimeoutError:
            return None

    async def select_character_await(self, channel, target_user, invoker_id, none_msg):
        chars = get_characters(target_user.id, channel.guild.id)
        if not chars:
            await channel.send(none_msg)
            return None
        if len(chars) == 1:
            return chars[0]["id"]
        view = ProfilCharacterSelectView(chars, invoker_id)
        await channel.send("Sélectionne le personnage :", view=view)
        await view.wait()
        return view.result

    # ---------- rendu de l'image de profil ----------
    async def _render_profile(self, character_id, guild=None) -> str:
        p = db.get_or_create_profile(character_id)
        char = get_character(character_id)
        name = char["character_name"] if char else "?"
        clan_key = char["clan"] if char else None
        clan = clan_key.capitalize() if clan_key and clan_key != "sans_clan" else "Sans clan"
        rang = (char["grade"] if char and char["grade"] else None) or "///"
        portrait_path = char["portrait_path"] if char else None
        bg = db.get_background(character_id)
        background_path = bg["image_path"] if bg else None

        # Force / Vitesse / Défense sont DÉRIVÉES de character_stats (Défense = Endurance).
        # Level/pct via la courbe exponentielle des stats (points_to_level_xp_stat) : les cercles du
        # pillow principal reflètent donc mécaniquement le nouveau système.
        stats = []
        for stat_key, display_name in [("force", "Force"), ("vitesse", "Vitesse"), ("endurance", "Défense")]:
            total = await get_stat_total(character_id, stat_key)
            level, xp_actuel, xp_max = points_to_level_xp_stat(total)
            pct = round(xp_actuel / xp_max * 100)
            stats.append((display_name, level, pct, (xp_actuel, xp_max)))

        # Maîtrises EO / Sort / RCT : DÉRIVÉES à la volée des points de stats (energie_occulte / sorts /
        # rct), comme Force/Vitesse/Défense. « MAX » (is_max) au plafond, sinon le pourcentage d'XP.
        # Territoire reste à 1 / 0 % (système différé).
        def _maitrise(nom, level, xp_actuel, xp_max):
            is_max = xp_max and xp_actuel >= xp_max
            pct = 0 if is_max else round(xp_actuel / xp_max * 100) if xp_max else 0
            return (nom, level, pct, bool(is_max))

        eo_lvl, eo_xa, eo_xm = await get_mastery_eo(character_id)
        sort_lvl, sort_xa, sort_xm = await get_mastery_sort(character_id)
        rct_lvl, rct_xa, rct_xm = await get_mastery_rct(guild, character_id)
        maitrises = [
            _maitrise("Maîtrise EO", eo_lvl, eo_xa, eo_xm),
            _maitrise("Maîtrise Sort", sort_lvl, sort_xa, sort_xm),
            ("Maîtrise Territoire", 1, 0, False),  # TODO : système Territoire différé
            _maitrise("RCT", rct_lvl, rct_xa, rct_xm),
        ]
        path = _tmp_profile("profil")
        generate_profil_image(
            name, (p["pv_actuel"], p["pv_max"]), (p["eo_actuel"], p["eo_max"]), p["level"],
            (p["xp_actuel"], p["xp_max"]), stats, maitrises, clan, rang,
            p["victoires"], p["defaites"], p["nuls"], path,
            portrait_path=portrait_path, background_path=background_path,
        )
        return path

    async def send_profile(self, channel, character_id, user_id):
        char = get_character(character_id)
        slot = char["slot_number"] if char else 1
        path = await self._render_profile(character_id, getattr(channel, "guild", None))
        await channel.send(
            file=discord.File(path, filename="profil.png"),
            view=ProfileView(character_id, user_id, slot),
        )
        try:
            os.remove(path)
        except OSError:
            pass

    # ---------- rendu de l'image Stats ----------
    async def _render_stats(self, character_id, guild=None):
        """Retourne (chemin_image, points_restants)."""
        s = db.get_or_create_stats(character_id)
        char = get_character(character_id)
        name = char["character_name"] if char else "?"
        portrait_path = char["portrait_path"] if char else None
        stats = []
        for key in STAT_KEYS:
            base = s[f"{key}_pts"]
            total = await get_stat_total(character_id, key)  # base + buffs
            show_tranche_text = True  # armes_maudites le passera à False (barre pleine, aucun texte)

            if key in STAT_LEVEL_BONUS_KEYS:
                # Force / Vitesse / Défense (Endurance) : courbe de stats, plafond STAT_MAX_LEVEL.
                # Texte « X point(s) manquant(s) » / « MAX » ; le total affiché inclut le bonus de niveau.
                level, xp_actuel, xp_max = points_to_level_xp_stat(total)
                tranche = compute_points_manquants(xp_actuel, xp_max, level, STAT_MAX_LEVEL)
                pct = round(xp_actuel / xp_max * 100) if level < STAT_MAX_LEVEL else 100
                total = total + compute_stat_level_bonus(level)

            elif key == "energie_occulte":
                level, xp_actuel, xp_max = await get_mastery_eo(character_id)
                tranche = compute_points_manquants(xp_actuel, xp_max, level, MASTERY_EO_MAX_LEVEL)
                pct = round(xp_actuel / xp_max * 100) if level < MASTERY_EO_MAX_LEVEL else 100

            elif key == "sorts":
                level, xp_actuel, xp_max = await get_mastery_sort(character_id)
                tranche = compute_points_manquants(xp_actuel, xp_max, level, MASTERY_SORT_MAX_LEVEL)
                pct = round(xp_actuel / xp_max * 100) if level < MASTERY_SORT_MAX_LEVEL else 100

            elif key == "rct":
                # Plafond = max du stade RCT actuel ; sans rôle RCT, aucun plafond atteint (jamais « MAX »).
                stage = await get_current_rct_stage(guild, character_id)
                level, xp_actuel, xp_max = await get_mastery_rct(guild, character_id)
                max_level = RCT_STAGES[stage]["max_level"] if stage else level + 1
                tranche = compute_points_manquants(xp_actuel, xp_max, level, max_level)
                pct = round(xp_actuel / xp_max * 100) if level < max_level else 100

            elif key == "armes_maudites":
                # Jauge fixe pleine, aucun texte sous la barre (système à définir plus tard).
                pct = 100
                tranche = ""
                show_tranche_text = False

            else:
                # Territoire : système de tranches par palier x10 inchangé.
                _num, pct, tranche = compute_tranche(base)

            stats.append((STAT_DISPLAY_NAMES[key], STAT_COLORS[key], base, total, pct, tranche,
                          show_tranche_text))
        buffs = []
        for bname, effects in db.get_buffs_with_effects(character_id):
            parts = " · ".join(
                f"{STAT_DISPLAY_NAMES.get(k, k)} {'+' if pts >= 0 else ''}{pts}" for k, pts in effects
            )
            buffs.append(f"{bname}  →  {parts}" if parts else bname)
        bg = db.get_background(character_id)
        background_path = bg["image_path"] if bg else None
        path = _tmp_profile("stats")
        generate_stats_image(
            name, stats, buffs, s["points_restants"], path,
            portrait_path=portrait_path, background_path=background_path,
        )
        return path, s["points_restants"]

    async def send_stats(self, channel, character_id, user_id):
        path, points_restants = await self._render_stats(character_id, getattr(channel, "guild", None))
        view = StatsPageView(character_id, user_id) if points_restants > 0 else None
        await channel.send(file=discord.File(path, filename="stats.png"), view=view)
        try:
            os.remove(path)
        except OSError:
            pass
        # Dette de points : le prochain gain lié à un rôle sera amputé de ce montant. Affichée en texte
        # sous l'image (près du badge "Points restants").
        # TODO: intégrer directement cette mention dans le pillow generate_stats_image plutôt qu'en texte.
        debt = db.get_or_create_stats(character_id)["points_debt"]
        if debt and debt > 0:
            await channel.send(f"⚠️ Dette de points : **-{debt}** sur le prochain gain.")

    # ---------- écran Techniques Occultes (⚡ Technique) ----------
    # TODO (points NON abordés) : aucun bouton de gestion pour l'instant (ajouter un sort, monter de
    # niveau...). Le système d'XP des techniques, les seuils de niveau/maîtrise pour la promotion en
    # « Technique Maximum » et les buffs de dégâts par niveau ne sont pas encore définis. La table
    # character_sorts reste donc VIDE par défaut (tous les slots affichés « verrouillés »), sans aucun
    # moyen de la remplir depuis le bot. À compléter quand ces règles seront décidées.
    async def _render_technique(self, character_id):
        """Rend le pillow d'ensemble et retourne (chemin, principals) où principals est la liste des
        (sort_id, nom) des slots principaux réellement remplis — sert à construire les boutons 🔍."""
        char = get_character(character_id)
        name = char["character_name"] if char else "?"
        camp = (char["camp"] if char else None) or "—"
        portrait_path = char["portrait_path"] if char else None
        # La grille affiche EXACTEMENT les sorts principaux réels (aucun padding). Chaque entrée est un
        # 7-tuple (nom, niveau, couleur, xp_actuel, xp_max, locked, unlock_level). Un sort verrouillé
        # (is_unlocked == 0) s'affiche « ??? (niveau requis unlock_level) » et n'a pas de bouton 🔍.
        # Séparation grille / Technique Maximum : les sorts promus (is_technique_maximum) quittent la
        # grille « GRANDES CATÉGORIES » et sont listés dans l'encadré du bas.
        sorts = []
        principals = []
        technique_maximum_list = []
        for row in db.get_character_sorts(character_id):
            if row["is_technique_maximum"]:
                technique_maximum_list.append(row["name"])
                continue
            color = (row["color_r"], row["color_g"], row["color_b"]) \
                if row["color_r"] is not None else None
            locked = not row["is_unlocked"]
            sorts.append((row["name"], row["level"], color, row["xp_actuel"], row["xp_max"],
                          locked, row["unlock_level"]))
            if not locked:
                principals.append((row["id"], row["name"]))  # bouton 🔍 seulement si débloqué
        bg = db.get_background(character_id)
        background_path = bg["image_path"] if bg else None
        path = _tmp_profile("technique")
        generate_technique_image(
            name, camp, sorts, path,
            portrait_path=portrait_path, background_path=background_path,
            technique_maximum_list=technique_maximum_list,
        )
        return path, principals

    async def send_technique(self, channel, character_id, user_id):
        path, principals = await self._render_technique(character_id)
        # Un bouton 🔍 par sort principal rempli (vers la vue détaillée). Aucun bouton de GESTION
        # (cf. TODO ci-dessus) : on ne peut ni ajouter ni éditer un sort depuis le bot pour l'instant.
        view = TechniqueOverviewView(character_id, user_id, principals) if principals else None
        await channel.send(file=discord.File(path, filename="technique.png"), view=view)
        try:
            os.remove(path)
        except OSError:
            pass

    async def handle_technique(self, interaction, cid):
        _, character_id, user_id = cid.split(":")  # profil_todo_technique:{cid}:{uid}
        character_id, user_id = int(character_id), int(user_id)
        if interaction.user.id != user_id:
            await interaction.response.send_message("Ce panneau n'est pas le tien.", ephemeral=True)
            return
        # À l'entrée : si le personnage a déjà au moins un sort principal, on affiche le pillow d'ensemble.
        # Sinon, on lance le flux de CRÉATION guidée (validation staff préalable).
        if db.count_character_sorts(character_id) > 0:
            await interaction.response.defer()
            await self.send_technique(interaction.channel, character_id, user_id)
            return
        await interaction.response.defer()
        await self._request_technique_creation(interaction, character_id, user_id)

    async def _render_territoire(self, character_id):
        """Construit le pillow Territoire. La Maîtrise Territoire est DÉRIVÉE des points de stat (comme
        EO/Sort/RCT) ; la progression par paliers de 15 niveaux s'applique au coût EO et à la durée À
        L'AFFICHAGE (jamais stockée). Retourne le chemin de l'image."""
        terr = db.get_territoire(character_id)
        char = get_character(character_id)
        portrait_path = char["portrait_path"] if char else None
        char_name = (char["character_name"] if char else None) or "—"
        bg = db.get_background(character_id)
        background_path = bg["image_path"] if bg else None

        # Maîtrise dérivée + règle « MAX » au plafond.
        level, xp_actuel, xp_max = await get_mastery_territoire(character_id)
        is_max = level >= MASTERY_TERRITOIRE_MAX_LEVEL
        pct = 100 if is_max else round(xp_actuel / xp_max * 100)

        # Valeurs finales = base (stockée) + progression par paliers de niveau.
        eo_reserve = (char["eo_value"] if char else 0) or 0
        base_cout = terr["cout_eo_pct"] or TERRITOIRE_DEFAULT_COUT_EO_PCT
        base_tours = terr["duree_tours"] or TERRITOIRE_DEFAULT_DUREE_TOURS
        cout_final = compute_territoire_cout(base_cout, level, eo_reserve)
        tours_final = compute_territoire_tours(base_tours, level)

        path = _tmp_profile("territoire")
        generate_territoire_image(
            char_name,
            terr["name"] or "—",
            terr["type"] or "—",
            level, pct,
            cout_final, tours_final,
            terr["description"] or "—",
            terr["effets"] or "—",
            path, portrait_path=portrait_path, background_path=background_path,
            is_max=is_max,
        )
        return path

    async def send_territoire(self, channel, character_id):
        path = await self._render_territoire(character_id)
        await channel.send(file=discord.File(path, filename="territoire.png"))
        try:
            os.remove(path)
        except OSError:
            pass

    async def handle_territoire(self, interaction, cid):
        _, character_id, user_id = cid.split(":")  # profil_todo_territoire:{cid}:{uid}
        character_id, user_id = int(character_id), int(user_id)
        if interaction.user.id != user_id:
            await interaction.response.send_message("Ce panneau n'est pas le tien.", ephemeral=True)
            return
        terr = db.get_territoire(character_id)
        # 1-2. Aucune ligne OU verrouillé staff -> refus (pas encore débloqué).
        if terr is None or not terr["is_unlocked"]:
            await interaction.response.send_message(
                "🔒 Tu n'as pas le niveau requis ou ni les éléments qui te permettent de débloquer ton "
                "territoire.", ephemeral=True
            )
            return
        # 3. Débloqué mais pas encore créé (name NULL) -> questionnaire de création guidée.
        if terr["name"] is None:
            if not self._acquire(user_id):
                await interaction.response.send_message(
                    "Tu as déjà une action en cours, termine la d'abord.", ephemeral=True
                )
                return
            try:
                await interaction.response.defer()
                await self._run_territoire_creation(interaction.channel, interaction.user, character_id)
            finally:
                self._release(user_id)
            return
        # 4. Déjà créé -> pillow normal avec les vraies données.
        await interaction.response.defer()
        await self.send_territoire(interaction.channel, character_id)

    # =================================================================
    # CRÉATION GUIDÉE DU TERRITOIRE (isolation par joueur, « cancel » à chaque étape)
    # =================================================================
    async def _run_territoire_creation(self, channel, player, character_id):
        """Questionnaire de création du Territoire (déclenché quand débloqué mais pas encore créé).
        Écrit tout en base à la fin, puis affiche le pillow. Isolation déjà posée par l'appelant."""
        await channel.send(
            f"{player.mention} — la création de ton **Territoire** commence ! Réponds ici, une question à "
            "la fois. Tape « cancel » à tout moment pour tout annuler."
        )

        async def _stop(result) -> bool:
            if result is _CANCELLED:
                await channel.send("❌ Création du territoire annulée.")
                return True
            if result is None:
                await channel.send("⏳ Création annulée (aucune réponse).")
                return True
            return False

        # 1. Nom (aucune limite de caractères).
        nom = await self._ask_bounded_text(channel, player, "Quel est le nom de ton territoire ?", 10 ** 9)
        if await _stop(nom):
            return

        # 2. Appellation (choix strict 1/2/3 -> texte exact stocké).
        appellation = await self._ask_territoire_appellation(channel, player)
        if await _stop(appellation):
            return

        # 3. Description visuelle.
        description = await self._ask_bounded_text(
            channel, player, "Décris visuellement ton territoire.", 10 ** 9
        )
        if await _stop(description):
            return

        # 4. Type de départ : information, pas une question. Stocké directement.
        await channel.send("📋 Ton territoire commence au type **Incomplet**.")
        type_ = "Incomplet"

        # 5. Effet.
        effets = await self._ask_bounded_text(
            channel, player, "Quel est l'effet de ton territoire (ce qu'il permet de faire) ?", 10 ** 9
        )
        if await _stop(effets):
            return

        # 6. Image ou GIF du territoire (même logique de téléchargement que /réserv-appa).
        image_path = await self._await_and_save_territoire_media(channel, player, character_id)
        if await _stop(image_path):
            return

        # 7. Enregistrement (la ligne « coquille » existe déjà depuis le déblocage staff). cout_eo_pct et
        # duree_tours reçoivent leur valeur de base fixe (60 % / 3 tours), ensuite modifiables par le staff.
        db.save_territoire(
            character_id, nom, appellation, type_, description, effets, image_path,
            cout_eo_pct=TERRITOIRE_DEFAULT_COUT_EO_PCT,
            duree_tours=TERRITOIRE_DEFAULT_DUREE_TOURS,
        )

        # 8. Confirmation + pillow.
        await channel.send("✅ Ton territoire a été créé !")
        await self.send_territoire(channel, character_id)

    async def _ask_territoire_appellation(self, channel, player):
        """Choix strict de l'appellation (1/2/3). Renvoie le TEXTE EXACT ; None si timeout ; _CANCELLED
        si « cancel »."""
        await channel.send(embed=discord.Embed(
            title="Choisis l'appellation de ton territoire",
            description="**1.** Extension du Territoire (VF)\n"
                        "**2.** Ryōiki Tenkai (VO)\n"
                        "**3.** Domain Expansion (VA)\n\nRéponds avec 1, 2 ou 3.",
            color=PHOENIX_COLOR,
        ))
        while True:
            m = await self.wait_message(channel, player)
            if m is None:
                return None
            if _is_cancel(m.content):
                return _CANCELLED
            choix = m.content.strip()
            if choix in TERRITOIRE_APPELLATIONS:
                return TERRITOIRE_APPELLATIONS[choix]
            await channel.send("Réponds avec **1**, **2** ou **3**.")

    async def _await_and_save_territoire_media(self, channel, player, character_id):
        """Attend une image ou un GIF (pièce jointe), reprend EXACTEMENT la logique de /réserv-appa :
        GIF (≤ 8 Mo, jamais recompressé) / image statique (compressée comme les portraits), puis vérifie
        que le fichier s'ouvre bien via PIL. Redemande si invalide. None si timeout ; _CANCELLED si « cancel »."""
        await channel.send("Envoie l'image ou le GIF de ton territoire.")
        os.makedirs(TERRITOIRE_DIR, exist_ok=True)
        while True:
            m = await self.wait_message(channel, player)
            if m is None:
                return None
            if _is_cancel(m.content):
                return _CANCELLED
            if not m.attachments:
                await channel.send("Envoie une **pièce jointe** (image ou GIF).")
                continue
            att = m.attachments[0]
            ctype = (att.content_type or "").split(";")[0].strip().lower()
            if not ctype.startswith("image/"):
                await channel.send("❌ Ce fichier n'a pas pu être traité, envoie une image ou un GIF valide.")
                continue

            if ctype == "image/gif":
                # GIF : contrôle STRICT de la taille, jamais recompressé (préserve l'animation).
                if att.size and att.size > TERRITOIRE_MAX_BYTES:
                    taille_mo = round(att.size / (1024 * 1024), 2)
                    await channel.send(f"❌ Ce GIF dépasse la limite de 8 Mo ({taille_mo} Mo). Réduis sa taille.")
                    continue
                try:
                    data = await att.read()
                except discord.HTTPException:
                    await channel.send("❌ Le téléchargement a échoué, réessaie.")
                    continue
                if len(data) > TERRITOIRE_MAX_BYTES:
                    taille_mo = round(len(data) / (1024 * 1024), 2)
                    await channel.send(f"❌ Ce GIF dépasse la limite de 8 Mo ({taille_mo} Mo). Réduis sa taille.")
                    continue
                ext = "gif"
            else:
                # Image statique : compressée comme les portraits de fiche (redimension + JPEG 85).
                try:
                    raw = await att.read()
                    data = compress_portrait(raw, max_dimension=1600, quality=85)
                except discord.HTTPException:
                    await channel.send("❌ Le téléchargement a échoué, réessaie.")
                    continue
                except Exception:
                    await channel.send("❌ Ce fichier n'a pas pu être traité, envoie une image ou un GIF valide.")
                    continue
                ext = "jpg"

            # Vérification PIL explicite : le fichier téléchargé doit s'ouvrir correctement.
            try:
                with Image.open(io.BytesIO(data)) as im:
                    im.verify()
            except Exception:
                await channel.send("❌ Ce fichier n'a pas pu être traité, envoie une image ou un GIF valide.")
                continue

            filename = f"{character_id}_{uuid.uuid4().hex}.{ext}"
            dest = os.path.join(TERRITOIRE_DIR, filename)
            try:
                with open(dest, "wb") as f:
                    f.write(data)
            except OSError:
                await channel.send("❌ Impossible d'enregistrer le fichier côté serveur, réessaie.")
                continue
            return dest

    async def _edit_territoire_param(self, channel, staff, character_id, param):
        """Déblocage / verrouillage staff du Territoire. Retourne True (traité) ou None (annulé/timeout)."""
        if param == "territoire_debloquer":
            db.unlock_territoire(character_id)
            await channel.send(
                "✅ Territoire **débloqué**. Le joueur pourra le créer (ou le consulter) via le bouton "
                "🗺️ Territoire de son profil."
            )
            return True
        if param == "territoire_bloquer":
            db.lock_territoire(character_id)
            await channel.send(
                "✅ Territoire **verrouillé**. Les données déjà créées restent intactes mais deviennent "
                "inaccessibles au joueur."
            )
            return True
        # Ces valeurs sont la BASE (niveau 1), la progression par paliers de 15 niveaux de Maîtrise
        # Territoire s'applique automatiquement par dessus au moment de l'affichage, jamais stockée directement.
        if param == "territoire_cout_eo":
            if db.get_territoire(character_id) is None:
                await channel.send("Ce personnage n'a pas encore de territoire (rien à modifier).")
                return True
            valeur = await self._tech_ask_int(
                channel, staff, "Nouveau **coût EO** du territoire, en % de la réserve ?", minimum=0
            )
            if valeur is None:
                return None
            with db.get_connection() as conn:
                conn.execute(
                    "UPDATE character_territoire SET cout_eo_pct = ? WHERE character_id = ?",
                    (valeur, character_id),
                )
            await channel.send(f"✅ Coût EO du territoire mis à **{valeur} %**.")
            return True
        if param == "territoire_duree":
            if db.get_territoire(character_id) is None:
                await channel.send("Ce personnage n'a pas encore de territoire (rien à modifier).")
                return True
            valeur = await self._tech_ask_int(
                channel, staff,
                f"Nouvelle **durée en tours** du territoire ? (minimum {TERRITOIRE_DUREE_MIN})",
                minimum=TERRITOIRE_DUREE_MIN,
            )
            if valeur is None:
                return None
            with db.get_connection() as conn:
                conn.execute(
                    "UPDATE character_territoire SET duree_tours = ? WHERE character_id = ?",
                    (valeur, character_id),
                )
            await channel.send(f"✅ Durée du territoire mise à **{valeur} tours**.")
            return True
        return None

    # =================================================================
    # CRÉATION GUIDÉE DES TECHNIQUES (validation staff préalable puis flux guidé côté joueur)
    # =================================================================
    async def _request_technique_creation(self, interaction, character_id, player_user_id):
        """Publie la demande de création DANS LE SALON DU JOUEUR (le même où il a cliqué ⚡ Technique),
        avec un ping du rôle staff et un bouton ✅ Confirmer réservé au staff. Un seul message, visible
        du joueur ET du staff."""
        char = get_character(character_id)
        character_name = char["character_name"] if char else "?"
        player_mention = f"<@{player_user_id}>"
        # 1) Message TEXTE CLASSIQUE (permanent, sans embed ni ephemeral) : le modèle de fiche, pour que
        # le joueur prépare ses réponses à l'avance pendant l'attente de validation staff.
        await interaction.channel.send(
            "📋 **Modèle de fiche Technique — prépare tes réponses à l'avance !**\n\n"
            "Voici toutes les questions que le bot te posera une fois que le staff aura validé ta demande. "
            "Tu peux préparer tes réponses dès maintenant.\n\n"
            "**1. Combien de Sorts Principaux veux-tu créer ?** (minimum 1, maximum 4)\n"
            "Un Sort Principal est la grande catégorie de ta technique occulte (exemple : « Katon » dans Naruto).\n\n"
            "**2. Le nom de chaque Sort Principal** (un par un, selon le nombre choisi à l'étape 1)\n\n"
            "**3. Pour CHAQUE Sort Principal, dans l'ordre :**\n"
            "- Combien de Sorts Secondaires veux-tu pour ce Sort Principal ? (minimum 1, maximum 8)\n"
            "Un Sort Secondaire est une compétence précise qui découle de ce Sort Principal "
            "(exemple : « Katon : Boule de feu suprême »).\n"
            "- Pour chaque Sort Secondaire : son nom, sa description, sa faiblesse, et sa classe (4, 3, 2, 1, ou S)\n\n"
            "⚠️ Un membre du staff doit valider ta demande ci-dessous avant que ces questions ne te soient "
            "réellement posées."
        )
        # 2) Embed récapitulatif + bouton ✅ Confirmer (ping staff), juste en dessous, même salon.
        embed = discord.Embed(
            title="📋 Demande de création de techniques",
            description=(
                f"📋 Demande de création de techniques — {player_mention}\n\n"
                "Le joueur va être guidé pour créer :\n"
                "- 1 à 4 **Sorts Principaux** (les grandes catégories de sa technique, ex: « Katon » dans Naruto)\n"
                "- Pour chaque Sort Principal : 1 à 8 **Sorts Secondaires** (les compétences précises qui en "
                "découlent, ex: « Katon : Boule de feu suprême »), chacun avec nom, description, faiblesse, "
                "et classe (4/3/2/1/S)\n\n"
                "⚠️ Un membre du staff doit cliquer sur **Confirmer** ci dessous pour lancer le processus."
            ),
            color=PHOENIX_COLOR,
        )
        embed.add_field(name="Personnage", value=character_name, inline=True)
        # Ping du rôle staff dans le CONTENU (pas seulement l'embed) pour notifier même le staff absent du salon.
        await interaction.channel.send(
            content=f"<@&{FICHE_STAFF_ROLE_ID}>",
            embed=embed,
            view=TechniqueCreationRequestView(character_id, player_user_id),
        )
        await interaction.followup.send(
            "📋 Ta demande de création de techniques a été envoyée au staff pour validation.",
            ephemeral=True,
        )

    async def handle_technique_create_confirm(self, interaction, cid):
        # tech_create_confirm:{character_id}:{player_user_id}
        _, character_id, player_user_id = cid.split(":")
        character_id, player_user_id = int(character_id), int(player_user_id)
        # Restriction stricte : seul un utilisateur précis peut valider (si c'est le joueur d'origine, il
        # peut donc valider sa propre demande — c'est voulu, pas une faille).
        if interaction.user.id != TECHNIQUE_VALIDATOR_ID:
            await interaction.response.send_message(
                "❌ Seul ce membre précis peut valider une demande de création de techniques.",
                ephemeral=True,
            )
            return
        # Le joueur d'origine doit toujours être présent sur le serveur pour être guidé.
        player = interaction.guild.get_member(player_user_id) if interaction.guild else None
        if player is None:
            await interaction.response.edit_message(
                content="❌ Le joueur a quitté le serveur, création annulée.", embed=None, view=None
            )
            return
        # Verrou d'isolation standard : refuse si le joueur a déjà un flux textuel en cours.
        if not self._acquire(player_user_id):
            await interaction.response.send_message(
                "Ce joueur a déjà une action en cours, réessaie quand elle sera terminée.", ephemeral=True
            )
            return
        # Retire le bouton et marque la demande comme confirmée.
        try:
            await interaction.response.edit_message(
                content=f"✅ Confirmé par {interaction.user.mention}, création en cours.", view=None
            )
        except discord.HTTPException:
            pass
        # Le flux se déroule dans LE SALON DU JOUEUR (celui de la demande = interaction.channel), avec le
        # JOUEUR D'ORIGINE comme interlocuteur (jamais le staff qui vient de cliquer).
        try:
            await self._run_technique_creation(interaction.channel, player, character_id)
        finally:
            self._release(player_user_id)

    async def _run_technique_creation(self, channel, player, character_id):
        """Flux guidé : N sorts principaux (1-4), leurs noms, puis pour chacun ses M sorts secondaires
        (1-8) avec nom/description/faiblesse/classe. Écrit tout en base à la fin (tout ou rien), puis
        affiche le pillow d'ensemble. Isolation par utilisateur déjà posée par l'appelant."""
        await channel.send(
            f"{player.mention} — la création de tes techniques commence ! Réponds ici, une question à la "
            "fois. Tape « cancel » à tout moment pour tout annuler."
        )

        # Contrôle d'arrêt commun : True s'il faut arrêter le flux (aucune écriture en base). Distingue
        # l'annulation volontaire (« cancel ») du timeout (plus de réponse).
        async def _stop(result) -> bool:
            if result is _CANCELLED:
                await channel.send("❌ Création de techniques annulée.")
                return True
            if result is None:
                await channel.send("⏳ Création annulée (aucune réponse).")
                return True
            return False

        # --- Étape 1 : nombre de sorts principaux (1-4) ---
        nb_principaux = await self._ask_bounded_int(
            channel, player,
            "Combien de **Sorts Principaux** veux tu créer ? Un Sort Principal est la grande catégorie de "
            "ta technique occulte (par exemple, dans Naruto, « Katon » serait un Sort Principal). "
            "Minimum 1, maximum 4.",
            1, 4,
        )
        if await _stop(nb_principaux):
            return

        # --- Traitement sort principal par sort principal : chacun est ENTIÈREMENT complété (nom +
        # nombre de secondaires + tous ses secondaires) avant de passer au suivant. ---
        # Structure collectée avant tout écrit : [{"name":..., "secondaires":[{name,description,faiblesse,classe}]}]
        plan = []
        for i in range(1, nb_principaux + 1):
            # a. Nom du sort principal n°i.
            nom_principal = await self._ask_bounded_text(
                channel, player, f"Quel est le nom du Sort Principal n°{i} ?", TECHNIQUE_NAME_MAX
            )
            if await _stop(nom_principal):
                return

            # b. Nombre de sorts secondaires POUR CE sort principal.
            nb_secondaires = await self._ask_bounded_int(
                channel, player,
                f"Combien de **Sorts Secondaires** veux tu pour « {nom_principal} » ? Un Sort Secondaire "
                "est une compétence précise qui découle de ce Sort Principal (par exemple, dans Naruto, "
                "« Katon : Boule de feu suprême » serait un Sort Secondaire de « Katon »). Minimum 1, maximum 8.",
                1, 8,
            )
            if await _stop(nb_secondaires):
                return

            # c. Les M secondaires de CE sort principal (nom / description / faiblesse / classe).
            secondaires = []
            for j in range(1, nb_secondaires + 1):
                nom_sec = await self._ask_bounded_text(
                    channel, player,
                    f"Nom de la technique {j}/{nb_secondaires} pour « {nom_principal} » :",
                    TECHNIQUE_NAME_MAX,
                )
                if await _stop(nom_sec):
                    return

                description = await self._ask_bounded_text(
                    channel, player, f"Description de « {nom_sec} » :", TECHNIQUE_DESC_MAX
                )
                if await _stop(description):
                    return

                faiblesse = await self._ask_bounded_text(
                    channel, player, f"Faiblesse de « {nom_sec} » :", TECHNIQUE_FAIBLESSE_MAX
                )
                if await _stop(faiblesse):
                    return

                classe = await self._ask_spell_class(channel, player, nom_sec)
                if await _stop(classe):
                    return

                secondaires.append({
                    "name": nom_sec, "description": description,
                    "faiblesse": faiblesse, "classe": classe,
                })

            # d. CE sort principal est entièrement traité : on passe au suivant.
            plan.append({"name": nom_principal, "secondaires": secondaires})

        # --- Répartition automatique des seuils de déblocage (niveau_requis) et du niveau de déblocage
        # du sort principal (unlock_level), calculée avant l'écriture. running_level enchaîne les paliers
        # d'un sort principal au suivant ; max_niveau_requis est conservé LOCALEMENT à chaque principal
        # (seuil de fin de Phase 1 = max_level_threshold). ---
        LEVEL_STEP = 5
        running_level = 1
        for principal in plan:
            principal["unlock_level"] = running_level
            secondaires = principal["secondaires"]
            total_secondaires = len(secondaires)
            default_unlocked = max(1, total_secondaires // 4)
            locked_index = 0
            max_niveau_requis = running_level
            for j, sec in enumerate(secondaires):
                if j < default_unlocked:
                    sec["niveau_requis"] = running_level
                else:
                    locked_index += 1
                    sec["niveau_requis"] = running_level + LEVEL_STEP * locked_index
                max_niveau_requis = max(max_niveau_requis, sec["niveau_requis"])
            # Seuil de fin de Phase 1 propre à CE sort principal (max local, pas la variable de chaînage).
            principal["max_level_threshold"] = max_niveau_requis
            running_level = max_niveau_requis + LEVEL_STEP

        # --- Écriture en base (une fois tout le flux terminé) ---
        for slot_index, principal in enumerate(plan):
            color = TECHNIQUE_SORT_PALETTE[slot_index % len(TECHNIQUE_SORT_PALETTE)]
            # Seul le PREMIER sort principal (slot 0) est débloqué d'office ; les 2e/3e/4e naissent
            # verrouillés. unlock_level reste calculé et affiché à titre INDICATIF, sans déblocage auto.
            # TODO : Le déblocage manuel d'un sort principal (passer is_unlocked à 1) ainsi que le
            # reverrouillage en cas d'erreur sont des actions STAFF, pas encore construites (point non
            # abordé : outil staff de déblocage/verrouillage des sorts principaux).
            is_unlocked = 1 if slot_index == 0 else 0
            sort_id = db.insert_principal_sort(
                character_id, slot_index, principal["name"], color,
                level=1, xp_actuel=0, xp_max=db.xp_required_for_level(1),
                unlock_level=principal["unlock_level"],
                max_level_threshold=principal["max_level_threshold"],
                is_unlocked=is_unlocked,
            )
            for sec_index, sec in enumerate(principal["secondaires"]):
                # Coût en % résolu depuis le barème validé (source unique SPELL_CLASS_VALUES).
                cout_pct = SPELL_CLASS_VALUES[sec["classe"]]["cout_pct"]
                # Ce tirage donne la valeur de dégâts DE BASE pour ce sort précis, fixée une seule fois à
                # la création. La progression de dégâts par niveau (+55/niveau via grant_sort_xp) s'ajoute
                # ensuite. La conversion du coût % en EO fixe au premier usage réel reste un point non
                # abordé (aucun système de combat existant pour le déclencher).
                degats_min = SPELL_CLASS_VALUES[sec["classe"]]["degats_min"]
                degats_max = SPELL_CLASS_VALUES[sec["classe"]]["degats_max"]
                degats = random.randint(degats_min, degats_max)
                # TODO : le seuil des 40% de réserve minimum pour pouvoir déclencher une technique encore
                # en % n'est pas implémenté (aucun système de combat pour le vérifier).
                db.insert_secondary_sort(
                    sort_id, sec_index, sec["name"], sec["classe"], cout_pct,
                    sec["description"], sec["faiblesse"], degats=degats,
                    niveau_requis=sec["niveau_requis"],
                )

        await channel.send("✅ Tes techniques ont été créées avec succès !")
        # Affiche directement le pillow d'ensemble (comme un re-clic sur ⚡ Technique).
        await self.send_technique(channel, character_id, player.id)

    async def _ask_bounded_text(self, channel, player, question, max_len):
        """Pose `question`, renvoie la réponse (nettoyée) tant qu'elle ne dépasse pas `max_len`
        caractères ; sinon message clair et redemande la même question sans avancer. None si le joueur
        ne répond plus (timeout) ; _CANCELLED si le joueur tape « cancel »."""
        await channel.send(question)
        while True:
            m = await self.wait_message(channel, player)
            if m is None:
                return None
            if _is_cancel(m.content):
                return _CANCELLED
            reponse = m.content.strip()
            if len(reponse) > max_len:
                await channel.send(
                    f"❌ Ce champ ne doit pas dépasser {max_len} caractères "
                    f"(tu en as écrit {len(reponse)})."
                )
                await channel.send(question)
                continue
            return reponse

    async def _ask_bounded_int(self, channel, player, question, minimum, maximum):
        """Pose `question`, valide un entier dans [minimum, maximum], redemande sinon. Retourne None si
        le joueur ne répond plus (timeout) ; _CANCELLED si le joueur tape « cancel »."""
        await channel.send(question)
        while True:
            m = await self.wait_message(channel, player)
            if m is None:
                return None
            if _is_cancel(m.content):
                return _CANCELLED
            n = _parse_int(m.content, minimum=minimum)
            if n is None or n > maximum:
                await channel.send(f"Entre un nombre entier entre {minimum} et {maximum}.")
                continue
            return n

    async def _ask_spell_class(self, channel, player, nom_sec):
        """Valide strictement une classe parmi 4/3/2/1/S, redemande sinon. None si plus de réponse ;
        _CANCELLED si le joueur tape « cancel »."""
        await channel.send(f"Classe de « {nom_sec} » ? (4, 3, 2, 1, ou S)")
        while True:
            m = await self.wait_message(channel, player)
            if m is None:
                return None
            if _is_cancel(m.content):
                return _CANCELLED
            choix = m.content.strip().upper()
            if choix in TECHNIQUE_VALID_CLASSES:
                return choix
            await channel.send("Classe invalide. Réponds exactement par 4, 3, 2, 1 ou S.")

    # ---------- écran détaillé d'un sort principal (⚡ Technique → 🔍) ----------
    # Les sorts secondaires sont désormais renseignés par le flux de création guidée (_run_technique_
    # creation). TODO (points NON abordés) : aucun bouton de GESTION ultérieure ici (rééditer un sort,
    # convertir le coût % en coût EO fixe, calculer les dégâts) — ces règles ne sont pas encore définies.
    async def _render_technique_detail(self, character_id, principal):
        """principal : ligne character_sorts (le sort principal). Retourne le chemin de l'image détaillée."""
        color = (principal["color_r"], principal["color_g"], principal["color_b"]) \
            if principal["color_r"] is not None else TECHNIQUE_DEFAULT_COLOR
        # Slots secondaires -> tuples (nom, classe, niveau_requis, debloque, cout_pct, degats). L'image
        # complète elle-même jusqu'à 8 slots (None, None, 999, False, None, None). Un slot sans nom est
        # considéré verrouillé/vide ; cout_pct et degats viennent de character_secondary_sorts.
        secondaires = []
        for row in db.get_secondary_sorts(principal["id"]):
            debloque = row["name"] is not None
            niveau = row["niveau_requis"] if row["niveau_requis"] is not None else 999
            secondaires.append((row["name"], row["classe"], niveau, debloque,
                                row["cout_pct"], row["degats"]))
        bg = db.get_background(character_id)
        background_path = bg["image_path"] if bg else None
        char = get_character(character_id)
        portrait_path = char["portrait_path"] if char else None
        path = _tmp_profile("technique_detail")
        generate_technique_detail_image(
            principal["name"], principal["level"], color, secondaires, path,
            background_path=background_path, portrait_path=portrait_path,
        )
        return path

    async def send_technique_detail(self, channel, character_id, user_id, sort_id):
        principal = db.get_character_sort(sort_id)
        # Sécurité : le sort doit exister ET appartenir au personnage de ce panneau.
        if principal is None or principal["character_id"] != character_id:
            await channel.send("Ce sort n'existe plus.")
            return
        path = await self._render_technique_detail(character_id, principal)
        await channel.send(
            file=discord.File(path, filename="technique_detail.png"),
            view=TechniqueDetailView(character_id, user_id, sort_id),
        )
        try:
            os.remove(path)
        except OSError:
            pass

    async def handle_technique_detail(self, interaction, cid):
        _, character_id, user_id, sort_id = cid.split(":")  # tech_detail:{cid}:{uid}:{sort_id}
        character_id, user_id, sort_id = int(character_id), int(user_id), int(sort_id)
        if interaction.user.id != user_id:
            await interaction.response.send_message("Ce panneau n'est pas le tien.", ephemeral=True)
            return
        await interaction.response.defer()
        await self.send_technique_detail(interaction.channel, character_id, user_id, sort_id)

    async def handle_technique_back(self, interaction, cid):
        _, character_id, user_id = cid.split(":")  # tech_back:{cid}:{uid}
        character_id, user_id = int(character_id), int(user_id)
        if interaction.user.id != user_id:
            await interaction.response.send_message("Ce panneau n'est pas le tien.", ephemeral=True)
            return
        await interaction.response.defer()
        await self.send_technique(interaction.channel, character_id, user_id)

    # ---------- '🔍 Afficher plus' : détail texte complet d'un sort secondaire ----------
    async def handle_technique_more(self, interaction, cid):
        _, sort_id, user_id = cid.split(":")  # tech_more:{sort_id}:{uid}
        sort_id, user_id = int(sort_id), int(user_id)
        if interaction.user.id != user_id:
            await interaction.response.send_message("Ce panneau n'est pas le tien.", ephemeral=True)
            return
        principal = db.get_character_sort(sort_id)
        if principal is None:
            await interaction.response.send_message("Ce sort n'existe plus.", ephemeral=True)
            return
        # Uniquement les slots secondaires réellement nommés (on ignore les slots vides).
        named = [r for r in db.get_secondary_sorts(sort_id) if r["name"]]
        if not named:
            await interaction.response.send_message(
                "Aucun sort secondaire à afficher pour l'instant.", ephemeral=True
            )
            return
        # Isolation par utilisateur standardisée (comme les autres flux textuels).
        if not self._acquire(user_id):
            await interaction.response.send_message(
                "Tu as déjà une action en cours, termine la d'abord.", ephemeral=True
            )
            return
        try:
            await interaction.response.defer()
            channel = interaction.channel
            listing = "\n".join(f"{i}. {r['name']}" for i, r in enumerate(named, 1))
            await channel.send(embed=discord.Embed(
                title=f"🔍 Sorts secondaires de « {principal['name']} »",
                description=listing + "\n\nRéponds avec le **numéro** du sort à afficher.",
                color=PHOENIX_COLOR,
            ))
            choix = None
            while choix is None:
                m = await self.wait_message(channel, interaction.user)
                if m is None:
                    await channel.send("⏳ Affichage annulé.")
                    return
                c = m.content.strip()
                if c.isdigit() and 1 <= int(c) <= len(named):
                    choix = named[int(c) - 1]
                else:
                    await channel.send(f"Réponds avec un numéro entre 1 et {len(named)}.")
            await channel.send(embed=self._build_secondary_sort_embed(choix, principal))
        finally:
            self._release(user_id)

    def _build_secondary_sort_embed(self, sec, principal):
        """Embed structuré et complet d'un sort secondaire (classe, coût EO, dégâts, point faible,
        maîtrise du sort principal parent). Couleur = couleur de la classe (cohérente avec le pillow)."""
        classe = sec["classe"]
        info = SPELL_CLASS_VALUES.get(classe, {})
        label = info.get("label", "?")
        rgb = TECHDET_CLASS_COLORS.get(classe, (150, 148, 160))
        embed = discord.Embed(
            title=sec["name"],
            description=sec["description"] or "—",
            color=discord.Color.from_rgb(*rgb),
        )
        embed.add_field(name="Classe", value=f"{classe} ({label})", inline=True)
        # Coût : le complément « converti en X points d'EO fixes… » n'apparaît que si la conversion a eu
        # lieu (cout_eo_fixe non NULL) — ce qui n'arrive jamais tant qu'aucun système de combat n'existe.
        cout = f"{sec['cout_pct']}% de la réserve"
        if sec["cout_eo_fixe"] is not None:
            cout += (f", converti en {sec['cout_eo_fixe']} points d'EO fixes une fois utilisé pour la "
                     "première fois")
        embed.add_field(name="Coût en énergie occulte", value=cout, inline=True)
        degats = f"{sec['degats']} pts" if sec["degats"] is not None else "—"
        embed.add_field(name="Dégâts", value=degats, inline=True)
        embed.add_field(name="Point faible", value=sec["faiblesse"] or "—", inline=False)
        embed.add_field(
            name="Maîtrise du Sort Principal",
            value=(f"{principal['name']} — Niveau {principal['level']} "
                   f"({principal['xp_actuel']}/{principal['xp_max']} XP)"),
            inline=False,
        )
        return embed

    # ---------- commande ----------
    @app_commands.command(name="profil", description="Consulte un profil de personnage")
    async def profil(self, interaction: discord.Interaction):
        if _is_staff(interaction.user):
            embed = discord.Embed(title="👤 Profil", description="Que veux tu faire ?", color=PHOENIX_COLOR)
            await interaction.response.send_message(embed=embed, view=ProfilStaffChoiceView(interaction.user.id))
        else:
            await interaction.response.send_message("👤 Ouverture du profil…", ephemeral=True)
            character_id = await self.select_character_await(
                interaction.channel, interaction.user, interaction.user.id, "Tu n'as aucun personnage validé."
            )
            if character_id is not None:
                await self.send_profile(interaction.channel, character_id, interaction.user.id)

    # =================================================================
    # LISTENER
    # =================================================================
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Déclencheur d'activation du Territoire en chat : si un joueur écrit EXACTEMENT l'appellation de
        son territoire (éventuellement suivie d'un numéro de slot pour lever l'ambiguïté), on joue la
        séquence de révélation (appellation -> nom du territoire -> image)."""
        if message.author.bot or message.guild is None:
            return
        content = message.content.strip()
        # Sortie ULTRA-rapide : le message doit correspondre à une phrase connue (avec ou sans numéro final)
        # pour éviter d'alourdir le traitement de CHAQUE message.
        base_phrase, slot_suffix = content, None
        parts = content.rsplit(" ", 1)
        if len(parts) == 2 and parts[1].isdigit():
            base_phrase, slot_suffix = parts[0], int(parts[1])
        if base_phrase not in TERRITOIRE_PHRASES:
            return

        matches = db.find_territoires_by_appellation(message.author.id, message.guild.id, base_phrase)
        if not matches:
            return
        if len(matches) == 1:
            target = matches[0]
        else:
            # Plusieurs territoires portent la même appellation : le numéro de slot est requis.
            if slot_suffix is None:
                return  # ambigu sans numéro, ignore silencieusement
            target = next((m for m in matches if m["slot_number"] == slot_suffix), None)
            if target is None:
                return

        # Séquence de révélation.
        await message.channel.send(embed=discord.Embed(title=base_phrase, color=PHOENIX_COLOR))
        await asyncio.sleep(2)
        await message.channel.send(embed=discord.Embed(title=target["name"], color=PHOENIX_COLOR))
        await asyncio.sleep(1)
        image_path = target["image_path"]
        if image_path and os.path.exists(image_path):
            await message.channel.send(file=discord.File(image_path))

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        if interaction.type != discord.InteractionType.component:
            return
        cid = interaction.data.get("custom_id", "")
        if cid.startswith("profil_self:"):
            await self.handle_self(interaction, cid)
        elif cid.startswith("profil_other:"):
            await self.handle_other(interaction, cid)
        elif cid.startswith("profil_roles:"):
            await self.handle_roles(interaction, cid)
        elif cid.startswith("profil_fond:"):
            await self.handle_fond(interaction, cid)
        elif cid.startswith("profil_edit:"):
            await self.handle_edit(interaction, cid)
        elif cid.startswith("profil_todo_technique:"):
            await self.handle_technique(interaction, cid)
        elif cid.startswith("tech_create_confirm:"):
            await self.handle_technique_create_confirm(interaction, cid)
        elif cid.startswith("tech_detail:"):
            await self.handle_technique_detail(interaction, cid)
        elif cid.startswith("tech_back:"):
            await self.handle_technique_back(interaction, cid)
        elif cid.startswith("tech_more:"):
            await self.handle_technique_more(interaction, cid)
        elif cid.startswith("profil_todo_stats:"):
            await self.handle_stats(interaction, cid)
        elif cid.startswith("stats_repartir:"):
            await self.handle_repartir(interaction, cid)
        elif cid.startswith("profil_todo_relation:"):
            await self.handle_relations(interaction, cid)
        elif cid.startswith("rel_page_prev:"):
            await self.handle_rel_page(interaction, cid, "prev")
        elif cid.startswith("rel_page_next:"):
            await self.handle_rel_page(interaction, cid, "next")
        elif cid.startswith("param_page_prev:"):
            await self.handle_params_page(interaction, cid, "prev")
        elif cid.startswith("param_page_next:"):
            await self.handle_params_page(interaction, cid, "next")
        elif cid.startswith("rel_create:"):
            await self.handle_rel_create(interaction, cid)
        elif cid.startswith("rel_remove:"):
            await self.handle_rel_remove(interaction, cid)
        elif cid.startswith("profil_todo_territoire:"):
            await self.handle_territoire(interaction, cid)
        elif cid.startswith("profil_todo_"):
            await interaction.response.send_message(
                "🔧 Cette section n'est pas encore développée.", ephemeral=True
            )

    async def handle_self(self, interaction, cid):
        user_id = int(cid.split(":")[1])
        if interaction.user.id != user_id:
            await interaction.response.send_message("Ce panneau ne t'appartient pas.", ephemeral=True)
            return
        await interaction.response.send_message("👤 Ouverture de ton profil…", ephemeral=True)
        character_id = await self.select_character_await(
            interaction.channel, interaction.user, interaction.user.id, "Tu n'as aucun personnage validé."
        )
        if character_id is not None:
            await self.send_profile(interaction.channel, character_id, interaction.user.id)

    async def handle_other(self, interaction, cid):
        user_id = int(cid.split(":")[1])
        if interaction.user.id != user_id or not _is_staff(interaction.user):
            await interaction.response.send_message("Action réservée au staff.", ephemeral=True)
            return
        if not self._acquire(user_id):
            await interaction.response.send_message(
                "Tu as déjà une action en cours, termine la d'abord.", ephemeral=True
            )
            return
        character_id = None
        try:
            await interaction.response.send_message("🔧 Gestion d'un profil…", ephemeral=True)
            channel = interaction.channel
            await channel.send("Mentionne le joueur dont tu veux gérer le profil.")
            target = None
            while target is None:
                m = await self.wait_message(channel, interaction.user)
                if m is None:
                    await channel.send("⏳ Annulé.")
                    return
                if m.mentions:
                    target = m.mentions[0]
                else:
                    await channel.send("Merci de **mentionner** un joueur (ex : @Pseudo).")
            character_id = await self.select_character_await(
                channel, target, interaction.user.id, "Ce joueur n'a aucun personnage validé."
            )
        finally:
            self._release(user_id)

        if character_id is not None:
            embed = discord.Embed(
                title="🔧 Gestion du profil", description="Que veux tu faire pour ce profil ?",
                color=PHOENIX_COLOR,
            )
            await interaction.channel.send(embed=embed, view=StaffActionView(character_id, interaction.user.id))

    async def handle_roles(self, interaction, cid):
        _, character_id, user_id = cid.split(":")
        character_id, user_id = int(character_id), int(user_id)
        role_ids = db.get_virtual_roles(character_id)
        if not role_ids:
            await interaction.response.send_message(
                "Aucun rôle virtuel n'est enregistré pour ce personnage.", ephemeral=True
            )
            return
        guild = interaction.guild
        names = []
        for rid in role_ids:
            role = guild.get_role(rid) if guild else None
            names.append(role.mention if role else f"`{rid}` (rôle supprimé)")
        embed = discord.Embed(
            title="🎭 Rôles virtuels du personnage",
            description="\n".join(f"• {n}" for n in names), color=PHOENIX_COLOR,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # =================================================================
    # PAGE RELATIONS (Famille / Amis / Autres)
    # =================================================================
    def _get_relations(self, character_id):
        """Relations du personnage {"Famille": [(nom, lien), ...], "Amis": [...], "Autres": [...]},
        construites depuis character_relations (JOIN sur validated_characters pour le nom). Une
        catégorie inconnue (données legacy) retombe dans "Autres"."""
        result = {"Famille": [], "Amis": [], "Autres": []}
        for _rid, _related_cid, category, label, related_name in db.get_relations(character_id):
            cat = category if category in result else "Autres"
            result[cat].append((related_name or "Personnage supprimé", label or ""))
        return result

    async def _render_relations(self, character_id, page):
        """Retourne (chemin_image, total_pages, page_clampée)."""
        char = get_character(character_id)
        name = char["character_name"] if char else "?"
        portrait_path = char["portrait_path"] if char else None
        relations = self._get_relations(character_id)
        bg = db.get_background(character_id)
        background_path = bg["image_path"] if bg else None
        path = _tmp_profile("relations")
        path, total_pages = generate_relations_image(
            name, relations, page, path,
            portrait_path=portrait_path, background_path=background_path,
        )
        clamped = max(1, min(page, total_pages))
        return path, total_pages, clamped

    async def send_relations(self, channel, character_id, user_id, page=1):
        """Génère et envoie la page Relations d'un personnage, avec les boutons persistants
        (pagination + créer / retirer). user_id = joueur autorisé à utiliser ces boutons (le
        propriétaire du personnage affiché)."""
        path, total_pages, clamped = await self._render_relations(character_id, page)
        view = RelationsPageView(character_id, user_id, clamped, total_pages, db.has_relations(character_id))
        await channel.send(file=discord.File(path, filename="relations.png"), view=view)
        try:
            os.remove(path)
        except OSError:
            pass

    async def handle_relations(self, interaction, cid):
        _, character_id, user_id = cid.split(":")  # profil_todo_relation:{cid}:{uid}
        character_id, user_id = int(character_id), int(user_id)
        if interaction.user.id != user_id:
            await interaction.response.send_message("Ce panneau n'est pas le tien.", ephemeral=True)
            return
        await interaction.response.defer()
        await self.send_relations(interaction.channel, character_id, user_id, page=1)

    async def handle_rel_page(self, interaction, cid, direction):
        _, character_id, user_id, page = cid.split(":")
        character_id, user_id, page = int(character_id), int(user_id), int(page)
        if interaction.user.id != user_id:
            await interaction.response.send_message("Cette pagination n'est pas la tienne.", ephemeral=True)
            return
        new_page = page + (1 if direction == "next" else -1)
        path, total_pages, clamped = await self._render_relations(character_id, new_page)
        view = RelationsPageView(character_id, user_id, clamped, total_pages, db.has_relations(character_id))
        await interaction.response.edit_message(
            attachments=[discord.File(path, filename="relations.png")], view=view
        )
        try:
            os.remove(path)
        except OSError:
            pass

    # -----------------------------------------------------------------
    # CRÉATION / RETRAIT D'UN LIEN (joueur propriétaire, ou staff sur n'importe qui)
    # -----------------------------------------------------------------
    async def _resolve_ref_character(self, channel, actor, button_character_id, is_staff):
        """Détermine le personnage de RÉFÉRENCE sur lequel porte l'ajout/retrait de lien.
        - Joueur : c'est directement le personnage du bouton (le sien).
        - Staff : étape préalable — on lui demande de quel joueur gérer les liens, puis quel
          personnage. Retourne (ref_character_id, ref_owner_id) ou (None, None) si annulé."""
        if not is_staff:
            char = get_character(button_character_id)
            return button_character_id, (char["user_id"] if char else actor.id)
        await channel.send("De quel joueur veux tu gérer les liens ? Mentionne le.")
        target = await self._await_mention(channel, actor)
        if target is None:
            return None, None
        ref_cid = await self.select_character_await(
            channel, target, actor.id, "Ce joueur n'a aucun personnage validé."
        )
        if ref_cid is None:
            return None, None
        return ref_cid, target.id

    async def _await_mention(self, channel, actor):
        """Attend un message mentionnant un joueur. Retourne le Member mentionné, ou None si timeout.
        (Isolation : wait_message filtre déjà strictement sur actor + salon courant.)"""
        while True:
            m = await self.wait_message(channel, actor)
            if m is None:
                await channel.send("⏳ Annulé.")
                return None
            if m.mentions:
                return m.mentions[0]
            await channel.send("Merci de **mentionner** un joueur (ex : @Pseudo).")

    async def _choose_link_target(self, channel, actor, ref_cid):
        """Étape « Mentionne le joueur avec qui tu veux créer ce lien. » : attend un message, extrait le
        membre RÉELLEMENT mentionné (message.mentions[0], jamais une variable réutilisée ni actor), puis
        décide s'il s'agit d'une auto-mention en comparant CE membre au propriétaire Discord du
        personnage ref_cid (validated_characters.user_id — récupéré ici même, jamais interaction.user :
        crucial en mode staff où l'acteur n'est pas le propriétaire). Retourne le related_cid ou None."""
        # Propriétaire Discord du personnage sur lequel on travaille (source de vérité : la base).
        ref_char = get_character(ref_cid)
        owner_user_id = ref_char["user_id"] if ref_char else None

        mentioned_member = None
        while mentioned_member is None:
            message = await self.wait_message(channel, actor)
            if message is None:
                await channel.send("⏳ Annulé.")
                return None
            mentioned_member = message.mentions[0] if message.mentions else None

            # --- Diagnostic temporaire ---
            print(f"🔍 [liens] Auteur du message : {message.author.id}")
            print(f"🔍 [liens] Mentions détectées dans le message : {[m.id for m in message.mentions]}")
            print(f"🔍 [liens] Membre mentionné retenu : {mentioned_member.id if mentioned_member else 'AUCUN'}")
            print(f"🔍 [liens] Propriétaire du personnage actuel (character_id {ref_cid}) : {owner_user_id}")
            print(f"🔍 [liens] Comparaison auto-mention : mentioned_member.id == owner_user_id ? "
                  f"{mentioned_member.id == owner_user_id if mentioned_member else 'N/A'}")
            # --- fin diagnostic ---

            if mentioned_member is None:
                await channel.send("Merci de **mentionner** un joueur (ex : @Pseudo).")

        # Auto-mention UNIQUEMENT si le membre mentionné EST le propriétaire du personnage de référence.
        if mentioned_member.id == owner_user_id:
            others = [c for c in get_characters(owner_user_id, channel.guild.id) if c["id"] != ref_cid]
            if not others:
                await channel.send(
                    "Tu n'as pas d'autre personnage, impossible de créer un lien avec toi même."
                )
                return None
            if len(others) == 1:
                return others[0]["id"]
            view = ProfilCharacterSelectView(others, actor.id)
            await channel.send("Avec lequel de tes autres personnages veux tu créer ce lien ?", view=view)
            await view.wait()
            return view.result

        # Joueur DIFFÉRENT : sélection standard parmi SES personnages.
        return await self.select_character_await(
            channel, mentioned_member, actor.id, "Ce joueur n'a aucun personnage."
        )

    async def handle_rel_create(self, interaction, cid):
        _, character_id, user_id = cid.split(":")
        character_id, user_id = int(character_id), int(user_id)
        is_staff = _is_staff(interaction.user)
        if interaction.user.id != user_id and not is_staff:
            await interaction.response.send_message("Ce panneau n'est pas le tien.", ephemeral=True)
            return
        # Anti double clic (même principe que /shop) : verrou mémoire (utilisateur, personnage) +
        # retrait immédiat de la View. Tout clic redondant est absorbé silencieusement.
        lock_key = (interaction.user.id, character_id)
        if lock_key in self._relation_lock:
            try:
                await interaction.response.defer()
            except discord.HTTPException:
                pass
            return
        self._relation_lock.add(lock_key)
        try:
            await interaction.response.edit_message(view=None)
        except discord.HTTPException:
            pass
        if not self._acquire(interaction.user.id):
            await interaction.followup.send(
                "Tu as déjà une action en cours, termine la d'abord.", ephemeral=True
            )
            self._relation_lock.discard(lock_key)
            return
        try:
            channel = interaction.channel
            ref_cid, ref_owner = await self._resolve_ref_character(
                channel, interaction.user, character_id, is_staff
            )
            if ref_cid is None:
                return

            # 1) Catégorie (vue en session, choix figé au clic).
            catview = RelationCategoryView(interaction.user.id)
            await channel.send(
                embed=discord.Embed(
                    title="Quel type de relation veux tu ajouter ?",
                    description="Choisis une catégorie.", color=PHOENIX_COLOR,
                ),
                view=catview,
            )
            await catview.wait()
            if catview.result is None:
                await channel.send("⏳ Aucun choix, création annulée.")
                return
            category = catview.result

            # 2) Personnage cible du lien (extraction + comparaison d'auto-mention faites au bon endroit).
            await channel.send("Mentionne le joueur avec qui tu veux créer ce lien.")
            related_cid = await self._choose_link_target(channel, interaction.user, ref_cid)
            if related_cid is None:
                return
            # Garde fou : jamais un lien d'un personnage vers lui même.
            if related_cid == ref_cid:
                await channel.send("Impossible de créer un lien avec soi même.")
                return

            # 3) Intitulé du lien.
            await channel.send("Écris le nom de cette relation (ex : Père, Meilleur ami, Rival).")
            m = await self.wait_message(channel, interaction.user)
            if m is None:
                await channel.send("⏳ Annulé.")
                return
            label = m.content.strip()
            if not label:
                await channel.send("Intitulé vide, création annulée.")
                return

            related = get_character(related_cid)
            related_name = related["character_name"] if related else "?"

            # 4) Anti doublon + écriture finale, protégés par un filet de sécurité : si un personnage
            # disparaît entre temps (FOREIGN KEY, character_id devenu invalide...), on abandonne
            # proprement sans casser l'interaction.
            try:
                existing = db.get_relations_between(ref_cid, related_cid)
                if existing:
                    rel_id, cur_cat, cur_label = existing[0]
                    confirm = ReplaceRelationView(interaction.user.id)
                    await channel.send(
                        embed=discord.Embed(
                            title="Une relation existe déjà",
                            description=(
                                f"Une relation existe déjà avec **{related_name}** : "
                                f"**{cur_cat}** — {cur_label}.\n\n"
                                f"Veux tu la remplacer par la nouvelle (**{category}** — {label}) ?"
                            ),
                            color=PHOENIX_COLOR,
                        ),
                        view=confirm,
                    )
                    await confirm.wait()
                    if confirm.result != "replace":
                        await channel.send("Opération annulée, l'ancienne relation est conservée.")
                        return
                    with db.get_connection() as conn:
                        conn.execute(
                            "UPDATE character_relations SET category = ?, label = ? WHERE id = ?",
                            (category, label, rel_id),
                        )
                    await channel.send(embed=discord.Embed(
                        description=f"✅ Relation remplacée — **{category}** : {related_name} ({label}).",
                        color=PHOENIX_COLOR,
                    ))
                else:
                    db.add_relation(ref_cid, related_cid, category, label)
                    await channel.send(embed=discord.Embed(
                        description=f"✅ Lien créé — **{category}** : {related_name} ({label}).",
                        color=PHOENIX_COLOR,
                    ))
                await self.send_relations(channel, ref_cid, ref_owner, page=1)
            except Exception:
                await interaction.followup.send(
                    "❌ Une erreur d'interaction est survenue, réessaie.", ephemeral=True
                )
                return
        finally:
            self._release(interaction.user.id)
            self._relation_lock.discard(lock_key)

    async def handle_rel_remove(self, interaction, cid):
        _, character_id, user_id = cid.split(":")
        character_id, user_id = int(character_id), int(user_id)
        is_staff = _is_staff(interaction.user)
        if interaction.user.id != user_id and not is_staff:
            await interaction.response.send_message("Ce panneau n'est pas le tien.", ephemeral=True)
            return
        # Anti double clic (même principe que /shop) : verrou mémoire (utilisateur, personnage) +
        # retrait immédiat de la View.
        lock_key = (interaction.user.id, character_id)
        if lock_key in self._relation_lock:
            try:
                await interaction.response.defer()
            except discord.HTTPException:
                pass
            return
        self._relation_lock.add(lock_key)
        try:
            await interaction.response.edit_message(view=None)
        except discord.HTTPException:
            pass
        if not self._acquire(interaction.user.id):
            await interaction.followup.send(
                "Tu as déjà une action en cours, termine la d'abord.", ephemeral=True
            )
            self._relation_lock.discard(lock_key)
            return
        try:
            channel = interaction.channel
            ref_cid, ref_owner = await self._resolve_ref_character(
                channel, interaction.user, character_id, is_staff
            )
            if ref_cid is None:
                return

            await channel.send("Mentionne le joueur avec qui tu veux retirer un lien.")
            target = await self._await_mention(channel, interaction.user)
            if target is None:
                return

            linked = db.get_linked_characters_of_user(ref_cid, target.id, channel.guild.id)
            if not linked:
                await channel.send("Aucun lien trouvé avec ce joueur.")
                return
            if len(linked) == 1:
                related_cid = linked[0][0]
            else:
                chars = [
                    {"id": lc[0], "character_name": lc[1], "slot_number": lc[2]} for lc in linked
                ]
                view = ProfilCharacterSelectView(chars, interaction.user.id)
                await channel.send("Plusieurs personnages de ce joueur sont liés au tien. Lequel ?", view=view)
                await view.wait()
                related_cid = view.result
                if related_cid is None:
                    await channel.send("⏳ Annulé.")
                    return

            # Si plusieurs liens (catégories/labels) existent entre les deux, on demande lequel retirer.
            links = db.get_relations_between(ref_cid, related_cid)
            if not links:
                await channel.send("Ce lien n'existe déjà plus.")
                return
            if len(links) == 1:
                relation_id = links[0][0]
                _cat, _label = links[0][1], links[0][2]
            else:
                lines = [f"**{i + 1}.** {cat} — {label}" for i, (_id, cat, label) in enumerate(links)]
                await channel.send(embed=discord.Embed(
                    title="Plusieurs liens existent avec ce personnage",
                    description="\n".join(lines) + "\n\nRéponds avec le numéro du lien à retirer.",
                    color=PHOENIX_COLOR,
                ))
                relation_id = None
                while relation_id is None:
                    mm = await self.wait_message(channel, interaction.user)
                    if mm is None:
                        await channel.send("⏳ Annulé.")
                        return
                    c = mm.content.strip()
                    if c.isdigit() and 1 <= int(c) <= len(links):
                        relation_id, _cat, _label = links[int(c) - 1]
                    else:
                        await channel.send(f"Réponds avec un numéro entre 1 et {len(links)}.")

            db.delete_relation_by_id(relation_id)
            related = get_character(related_cid)
            related_name = related["character_name"] if related else "?"
            await channel.send(embed=discord.Embed(
                description=f"✅ Lien retiré avec {related_name} (**{_cat}** — {_label}).",
                color=PHOENIX_COLOR,
            ))
            await self.send_relations(channel, ref_cid, ref_owner, page=1)
        finally:
            self._release(interaction.user.id)
            self._relation_lock.discard(lock_key)

    # =================================================================
    # PAGE STATS + RÉPARTITION (joueur)
    # =================================================================
    async def handle_stats(self, interaction, cid):
        _, character_id, user_id = cid.split(":")  # profil_todo_stats:{cid}:{uid}
        character_id, user_id = int(character_id), int(user_id)
        if interaction.user.id != user_id:
            await interaction.response.send_message("Ce panneau n'est pas le tien.", ephemeral=True)
            return
        await interaction.response.defer()
        await self.send_stats(interaction.channel, character_id, user_id)

    async def handle_repartir(self, interaction, cid):
        _, character_id, user_id = cid.split(":")
        character_id, user_id = int(character_id), int(user_id)
        if interaction.user.id != user_id:
            await interaction.response.send_message("Ce panneau n'est pas le tien.", ephemeral=True)
            return

        # Anti double clic (même protection que les achats /shop) : verrou mémoire par character_id ;
        # un clic redondant pendant qu'un traitement est en cours est absorbé silencieusement.
        if character_id in self._repartir_lock:
            try:
                await interaction.response.defer()
            except discord.HTTPException:
                pass
            return
        self._repartir_lock.add(character_id)
        # Premier clic : on retire immédiatement la View (plus aucun bouton actif pendant le traitement).
        try:
            await interaction.response.edit_message(view=None)
        except discord.HTTPException:
            pass

        if not self._acquire(user_id):
            await interaction.followup.send(
                "Tu as déjà une action en cours, termine la d'abord.", ephemeral=True
            )
            self._repartir_lock.discard(character_id)
            return
        try:
            channel = interaction.channel
            s = db.get_or_create_stats(character_id)
            if s["points_restants"] <= 0:
                await channel.send("Tu n'as aucun point à répartir.")
                return
            stat = await self._pick_stat(channel, interaction.user, PLAYER_DISTRIBUABLE_STATS)
            if stat is None:
                await channel.send("⏳ Répartition annulée.")
                return
            new_rest = None
            while new_rest is None:
                await channel.send("Combien de points veux tu y mettre ?")
                m = await self.wait_message(channel, interaction.user)
                if m is None:
                    await channel.send("⏳ Répartition annulée.")
                    return
                n = _parse_int(m.content, minimum=1)
                if n is None:
                    await channel.send("Entre un nombre entier positif.")
                    continue
                remaining = db.get_or_create_stats(character_id)["points_restants"]
                if n > remaining:
                    await channel.send(f"Tu n'as que {remaining} points restants.")
                    continue
                new_rest = db.add_stat_points_from_pool(character_id, stat, n)
                if new_rest is None:  # course : le solde a bougé entre temps
                    await channel.send(
                        f"Tu n'as que {db.get_or_create_stats(character_id)['points_restants']} points restants."
                    )
            await channel.send(embed=discord.Embed(
                description=f"{n} points répartis dans {STAT_DISPLAY_NAMES[stat]}. Points restants : {new_rest}.",
                color=PHOENIX_COLOR,
            ))
            await self.send_stats(channel, character_id, user_id)
        finally:
            # Répartition terminée (pillow régénéré) : on libère le verrou utilisateur ET le verrou
            # anti double clic de ce personnage.
            self._release(user_id)
            self._repartir_lock.discard(character_id)

    async def _pick_stat(self, channel, user, allowed_keys):
        await channel.send(embed=discord.Embed(
            title="Choisis une statistique",
            description="\n".join(f"• {STAT_DISPLAY_NAMES[k]}" for k in allowed_keys)
                        + "\n\nÉcris le nom (ou le début) de la stat.",
            color=PHOENIX_COLOR,
        ))
        while True:
            m = await self.wait_message(channel, user)
            if m is None:
                return None
            cands = match_stat(m.content, allowed_keys)
            if not cands:
                await channel.send("Statistique inconnue, réessaie.")
                continue
            if len(cands) == 1:
                return cands[0]
            lines = [f"**{i + 1}.** {STAT_DISPLAY_NAMES[k]}" for i, k in enumerate(cands)]
            await channel.send(embed=discord.Embed(
                title="Plusieurs stats correspondent",
                description="\n".join(lines) + "\n\nRéponds avec le numéro.", color=PHOENIX_COLOR,
            ))
            while True:
                mm = await self.wait_message(channel, user)
                if mm is None:
                    return None
                c = mm.content.strip()
                if c.isdigit() and 1 <= int(c) <= len(cands):
                    return cands[int(c) - 1]
                await channel.send(f"Réponds avec un numéro entre 1 et {len(cands)}.")

    # =================================================================
    # AJOUT / REMPLACEMENT DU FOND (joueur propriétaire OU staff via /profil)
    # =================================================================
    async def handle_fond(self, interaction, cid):
        _, character_id, user_id = cid.split(":")
        character_id, user_id = int(character_id), int(user_id)
        if interaction.user.id != user_id:
            await interaction.response.send_message("Ce panneau n'est pas le tien.", ephemeral=True)
            return
        if not self._acquire(user_id):
            await interaction.response.send_message(
                "Tu as déjà une action en cours, termine la d'abord.", ephemeral=True
            )
            return
        try:
            await interaction.response.send_message("🖼️ Ajout d'un fond…", ephemeral=True)
            await self._run_fond(interaction.channel, interaction.user, character_id)
        finally:
            self._release(user_id)

    async def _run_fond(self, channel, user, character_id):
        await channel.send("Envoie l'image à utiliser comme fond (JPG, PNG, ou lien direct, pas de GIF).")
        saved = await self._await_and_save_image(channel, user, BACKGROUND_DIR, f"{character_id}.jpg")
        if saved is None:
            await channel.send("⏳ Ajout du fond annulé.")
            return
        # Chaque personnage a SON fond : fichier nommé par character_id, jamais partagé entre persos.
        db.set_background(character_id, saved, _now())
        await channel.send("✅ Fond mis à jour.")
        await self.send_profile(channel, character_id, user.id)

    async def _await_and_save_image(self, channel, user, dest_dir, filename):
        """Attend une image (JPG/PNG, pas de GIF), la télécharge, la compresse en JPEG (même logique
        que /depart) et l'écrit dans dest_dir/filename. Redemande si invalide. None si timeout."""
        while True:
            m = await self.wait_message(channel, user)
            if m is None:
                return None
            url = await _resolve_portrait_url(m)
            if url is None:
                await channel.send(
                    "Format non accepté, envoie une image en JPG ou PNG (pièce jointe ou lien direct), pas de GIF."
                )
                continue
            raw = await _download_image_bytes(url)
            if raw is None:
                await channel.send("Impossible de télécharger l'image, réessaie avec un autre fichier ou lien.")
                continue
            try:
                jpeg = compress_portrait(raw)
            except Exception:
                await channel.send("Cette image est illisible ou corrompue, réessaie avec un autre fichier.")
                continue
            os.makedirs(dest_dir, exist_ok=True)
            dest = os.path.join(dest_dir, filename)
            with open(dest, "wb") as f:
                f.write(jpeg)
            return dest

    # =================================================================
    # MODIFICATION DU PROFIL (staff)
    # =================================================================
    async def handle_edit(self, interaction, cid):
        _, character_id, user_id = cid.split(":")
        character_id, user_id = int(character_id), int(user_id)
        if interaction.user.id != user_id or not _is_staff(interaction.user):
            await interaction.response.send_message("Action réservée au staff.", ephemeral=True)
            return
        if not self._acquire(user_id):
            await interaction.response.send_message(
                "Tu as déjà une action en cours, termine la d'abord.", ephemeral=True
            )
            return
        try:
            await interaction.response.send_message("✏️ Modification du profil…", ephemeral=True)
            channel = interaction.channel
            n = None
            while n is None:
                await channel.send("Combien de modifications veux tu faire ?")
                m = await self.wait_message(channel, interaction.user)
                if m is None:
                    await channel.send("⏳ Annulé.")
                    return
                n = _parse_int(m.content, minimum=1)
                if n is None:
                    await channel.send("Entre un nombre entier positif.")
            for i in range(n):
                await channel.send(f"**Modification {i + 1}/{n}**")
                ok = await self._one_edit(channel, interaction.user, character_id)
                if ok is None:
                    await channel.send("⏳ Session expirée, modifications interrompues.")
                    return
            await channel.send("Toutes les modifications ont été appliquées.")
        finally:
            self._release(user_id)

    # Catégories du menu staff (titre, contenu). Découpées en pages si l'embed dépasse la limite Discord.
    _PARAM_CATEGORY_BLOCKS = [
        ("PROFIL",
         "PV maximum, PV minimum, EO maximum, EO minimum, Level, XP maximum, XP minimum, "
         "Clan, Rang, Victoires, Défaites, Nuls, Level maîtrise EO, Image, Fond"),
        ("STATS",
         "Stats Force, Stats Vitesse, Stats Endurance, Stats Armes maudites, Stats RCT, "
         "Stats Territoire, Stats Sorts, Stats Énergie occulte, Buff, Points de stats"),
        ("RÔLES", "Ajouter des rôles, Retirer des rôles"),
        ("TECHNIQUES",
         "Nom sort principal, Ajouter XP, Retirer XP, Modifier level, Ajouter sort maximum, "
         "Retirer sort maximum, Débloquer sort principal, Bloquer sort principal, Nom sort secondaire, "
         "Niveau requis, Coût EO, Dégâts, Débloquer sort secondaire, Bloquer sort secondaire"),
        ("TERRITOIRE", "Débloquer, Bloquer, Coût EO territoire, Durée en tours"),
    ]
    _PARAM_FOOTER = "\n\nÉcris le nom du paramètre à modifier."

    def _params_pages(self) -> list:
        """Découpe la liste des catégories en pages ≤ ~3900 caractères (limite embed Discord = 4096)."""
        blocks = [f"**{title} :**\n{content}" for title, content in self._PARAM_CATEGORY_BLOCKS]
        pages, cur = [], ""
        for b in blocks:
            candidate = (cur + "\n\n" + b) if cur else b
            if cur and len(candidate) + len(self._PARAM_FOOTER) > 3900:
                pages.append(cur + self._PARAM_FOOTER)
                cur = b
            else:
                cur = candidate
        if cur:
            pages.append(cur + self._PARAM_FOOTER)
        return pages

    async def _send_params_prompt(self, channel, user):
        pages = self._params_pages()
        total = len(pages)
        embed = discord.Embed(title="Paramètres modifiables", description=pages[0], color=PHOENIX_COLOR)
        # Pagination par boutons SEULEMENT si le contenu déborde sur plusieurs pages.
        if total > 1:
            embed.set_footer(text=f"Page 1/{total}")
            await channel.send(embed=embed, view=ParamsPageView(user.id, 0, total))
        else:
            await channel.send(embed=embed)

    async def handle_params_page(self, interaction, cid, direction):
        _, user_id, page = cid.split(":")
        user_id, page = int(user_id), int(page)
        if interaction.user.id != user_id:
            await interaction.response.send_message("Ce panneau n'est pas le tien.", ephemeral=True)
            return
        pages = self._params_pages()
        total = len(pages)
        new_page = max(0, min(total - 1, page - 1 if direction == "prev" else page + 1))
        embed = discord.Embed(title="Paramètres modifiables", description=pages[new_page], color=PHOENIX_COLOR)
        embed.set_footer(text=f"Page {new_page + 1}/{total}")
        await interaction.response.edit_message(embed=embed, view=ParamsPageView(user_id, new_page, total))

    async def _pick_param(self, channel, user):
        while True:
            m = await self.wait_message(channel, user)
            if m is None:
                return None
            candidates = match_params(m.content)
            if not candidates:
                await channel.send("Paramètre inconnu, réessaie.")
                continue
            if len(candidates) == 1:
                return candidates[0]
            lines = [f"**{i + 1}.** {k} ({PARAM_ALIASES[k][0]})" for i, k in enumerate(candidates)]
            await channel.send(embed=discord.Embed(
                title="Plusieurs paramètres correspondent",
                description="\n".join(lines) + "\n\nRéponds avec le numéro correspondant.",
                color=PHOENIX_COLOR,
            ))
            while True:
                mm = await self.wait_message(channel, user)
                if mm is None:
                    return None
                c = mm.content.strip()
                if c.isdigit() and 1 <= int(c) <= len(candidates):
                    return candidates[int(c) - 1]
                await channel.send(f"Réponds avec un numéro entre 1 et {len(candidates)}.")

    async def _one_edit(self, channel, staff, character_id):
        """Une modification. Retourne True si traitée, None si timeout dur (interruption propre)."""
        await self._send_params_prompt(channel, staff)
        param = await self._pick_param(channel, staff)
        if param is None:
            return None

        # Flux dédié Techniques (sorts principaux / secondaires).
        if param in TECHNIQUE_EDIT_PARAMS:
            return await self._edit_technique_param(channel, staff, character_id, param)

        # Flux dédié Territoire (déblocage / verrouillage).
        if param in TERRITOIRE_EDIT_PARAMS:
            return await self._edit_territoire_param(channel, staff, character_id, param)

        if param == "image":
            await channel.send("Envoie la nouvelle image du personnage (JPG/PNG, pièce jointe ou lien, pas de GIF).")
            char = get_character(character_id)
            owner_uid = char["user_id"] if char else character_id
            slot = char["slot_number"] if char else 1
            saved = await self._await_and_save_image(channel, staff, PORTRAIT_DIR, f"{owner_uid}_{slot}.jpg")
            if saved is None:
                return None
            with db.get_connection() as conn:
                conn.execute("UPDATE validated_characters SET portrait_path = ? WHERE id = ?", (saved, character_id))
            await channel.send("✅ Portrait mis à jour (visible partout : sélection de personnage et profil).")
            await self.send_profile(channel, character_id, staff.id)
            return True

        if param == "fond":
            await channel.send("Envoie l'image à utiliser comme fond (JPG, PNG, ou lien direct, pas de GIF).")
            saved = await self._await_and_save_image(channel, staff, BACKGROUND_DIR, f"{character_id}.jpg")
            if saved is None:
                return None
            db.set_background(character_id, saved, _now())
            await channel.send("✅ Fond mis à jour.")
            await self.send_profile(channel, character_id, staff.id)
            return True

        if param == "buff":
            return await self._edit_buff(channel, staff, character_id)

        # Clan / rang : changer aussi le VRAI rôle (réel ou virtuel) + resynchroniser les points de stats.
        if param == "clan":
            return await self._edit_clan(channel, staff, character_id)
        if param == "rang":
            return await self._edit_rang(channel, staff, character_id)

        # Rôles : ajout / retrait en masse (réels slot 1 / virtuels slot 2-3) + synchro du barème.
        if param in ("roles_ajouter", "roles_retirer"):
            return await self._edit_roles(channel, staff, character_id, add=(param == "roles_ajouter"))

        # Paramètres à valeur textuelle / numérique.
        await channel.send(f"Nouvelle valeur pour **{param}** ({PARAM_ALIASES[param][0]}) ?")
        while True:
            m = await self.wait_message(channel, staff)
            if m is None:
                return None
            ok, msg = self._apply_scalar(character_id, param, m.content)
            await channel.send(msg)
            if ok:
                await self.send_profile(channel, character_id, staff.id)
                if param in _STATS_AFFECTING:
                    await self.send_stats(channel, character_id, staff.id)
                return True
            # invalide : on redemande la valeur

    # =================================================================
    # ÉDITION STAFF DES TECHNIQUES (sorts principaux / secondaires)
    # Isolation déjà posée par handle_edit ; « cancel » disponible à chaque étape.
    # =================================================================
    async def select_principal_sort(self, channel, staff, character_id, prompt_extra=""):
        """Liste les sorts principaux, demande d'en choisir un par son NOM ACTUEL. Gère « cancel ».
        Retourne l'id du sort choisi, ou None (annulé / aucun sort / timeout)."""
        sorts = db.get_character_sorts(character_id)
        if not sorts:
            await channel.send("Ce personnage n'a aucun sort principal.")
            return None
        liste = "\n".join(f"• {s['name']}" for s in sorts)
        await channel.send(f"Sorts principaux existants :\n{liste}\n\nÉcris le NOM ACTUEL du sort à "
                           f"modifier (ou « cancel »). {prompt_extra}")
        while True:
            m = await self.wait_message(channel, staff)
            if m is None or _is_cancel(m.content):
                return None
            target = m.content.strip().lower()
            match = next((s for s in sorts if (s["name"] or "").lower() == target), None)
            if match:
                return match["id"]
            await channel.send("Nom introuvable parmi les sorts existants, réessaie (ou « cancel »).")

    async def _tech_ask_int(self, channel, staff, question, minimum=1, maximum=None):
        """Entier ≥ minimum (et ≤ maximum si fourni). None si « cancel » ou timeout."""
        await channel.send(question + " (ou « cancel »)")
        while True:
            m = await self.wait_message(channel, staff)
            if m is None or _is_cancel(m.content):
                return None
            v = _parse_int(m.content, minimum=minimum)
            if v is None or (maximum is not None and v > maximum):
                borne = f"entre {minimum} et {maximum}" if maximum is not None else f"supérieur ou égal à {minimum}"
                await channel.send(f"Entre un nombre entier {borne}.")
                continue
            return v

    async def _tech_pick_numbered(self, channel, staff, items, prompt):
        """items : liste de (id, label). Retourne l'id choisi, ou None (vide / cancel / timeout)."""
        if not items:
            return None
        lines = "\n".join(f"**{i + 1}.** {lbl}" for i, (_id, lbl) in enumerate(items))
        await channel.send(embed=discord.Embed(
            description=prompt + "\n\n" + lines + "\n\nRéponds avec le numéro (ou « cancel »).",
            color=PHOENIX_COLOR,
        ))
        while True:
            m = await self.wait_message(channel, staff)
            if m is None or _is_cancel(m.content):
                return None
            c = m.content.strip()
            if c.isdigit() and 1 <= int(c) <= len(items):
                return items[int(c) - 1][0]
            await channel.send(f"Réponds avec un numéro entre 1 et {len(items)}.")

    def _tech_secondary_items(self, sort_id, principal_level, only_locked=None):
        """(id, label) des sorts secondaires NOMMÉS d'un sort principal, avec état débloqué/verrouillé.
        only_locked=True -> seulement verrouillés ; False -> seulement débloqués ; None -> tous."""
        items = []
        for r in db.get_secondary_sorts(sort_id):
            if r["name"] is None:
                continue
            niveau = r["niveau_requis"] if r["niveau_requis"] is not None else 999
            locked = principal_level < niveau
            if only_locked is True and not locked:
                continue
            if only_locked is False and locked:
                continue
            state = f"verrouillé, niveau requis {niveau}" if locked else "débloqué"
            items.append((r["id"], f"{r['name']} ({state})"))
        return items

    async def _edit_technique_param(self, channel, staff, character_id, param):
        """Dispatcher des 14 flux d'édition Techniques. Retourne True (traité ou annulé proprement) ou
        None (timeout : on laisse l'appelant décider — ici on renvoie True pour ne pas casser la session,
        l'annulation étant volontaire ou par inactivité)."""

        async def refresh():
            # Régénère le pillow d'ensemble ⚡ Technique pour refléter la modification.
            if db.count_character_sorts(character_id) > 0:
                await self.send_technique(channel, character_id, staff.id)
            return True

        # ---- Sorts principaux ----
        if param == "nom_sort_principal":
            sort_id = await self.select_principal_sort(channel, staff, character_id)
            if sort_id is None:
                return True
            nom = await self._ask_bounded_text(channel, staff, "Nouveau nom du sort principal :", TECHNIQUE_NAME_MAX)
            if nom is None or nom is _CANCELLED:
                return True
            with db.get_connection() as conn:
                conn.execute("UPDATE character_sorts SET name = ? WHERE id = ?", (nom, sort_id))
            await channel.send(f"✅ Sort principal renommé en « {nom} ».")
            return await refresh()

        if param == "ajouter_xp_sort":
            sort_id = await self.select_principal_sort(channel, staff, character_id)
            if sort_id is None:
                return True
            xp = await self._tech_ask_int(channel, staff, "Combien d'XP ajouter ?", minimum=1)
            if xp is None:
                return True
            ups = await db.grant_sort_xp(sort_id, xp)
            await channel.send(f"✅ {xp} XP ajoutés ({ups} montée(s) de niveau).")
            return await refresh()

        if param == "retirer_xp_sort":
            sort_id = await self.select_principal_sort(channel, staff, character_id)
            if sort_id is None:
                return True
            xp = await self._tech_ask_int(channel, staff, "Combien d'XP retirer ?", minimum=1)
            if xp is None:
                return True
            new_level = await db.revoke_sort_xp(sort_id, xp)
            await channel.send(f"✅ {xp} XP retirés (niveau du sort désormais {new_level}).")
            return await refresh()

        if param == "modifier_level_sort":
            sort_id = await self.select_principal_sort(channel, staff, character_id)
            if sort_id is None:
                return True
            row = db.get_character_sort(sort_id)
            phase = row["phase"]
            # En phase 1, le plafond cohérent est le seuil de fin de Phase 1 ; en phase 2, 100.
            max_lv = 100 if phase == 2 else (row["max_level_threshold"] or 999)
            new_level = await self._tech_ask_int(
                channel, staff, f"Nouveau niveau ? (1 à {max_lv})", minimum=1, maximum=max_lv)
            if new_level is None:
                return True
            xp_max = db.PHASE2_XP_PER_LEVEL if phase == 2 else db.xp_required_for_level(new_level)
            with db.get_connection() as conn:
                conn.execute(
                    "UPDATE character_sorts SET level = ?, xp_actuel = 0, xp_max = ? WHERE id = ?",
                    (new_level, xp_max, sort_id),
                )
            await channel.send(f"✅ Niveau du sort principal fixé à {new_level} "
                               "(carte et hexagone se mettent à jour automatiquement).")
            return await refresh()

        if param == "ajouter_sort_max":
            sort_id = await self.select_principal_sort(
                channel, staff, character_id, prompt_extra="(il sera classé en Technique Maximum)")
            if sort_id is None:
                return True
            with db.get_connection() as conn:
                conn.execute("UPDATE character_sorts SET is_technique_maximum = 1 WHERE id = ?", (sort_id,))
            await channel.send("✅ Sort classé en Technique Maximum.")
            return await refresh()

        if param == "retirer_sort_max":
            items = [(s["id"], s["name"]) for s in db.get_character_sorts(character_id)
                     if s["is_technique_maximum"]]
            if not items:
                await channel.send("Aucun sort en Technique Maximum.")
                return True
            sort_id = await self._tech_pick_numbered(
                channel, staff, items, "Quel sort retirer de la Technique Maximum ?")
            if sort_id is None:
                return True
            with db.get_connection() as conn:
                conn.execute("UPDATE character_sorts SET is_technique_maximum = 0 WHERE id = ?", (sort_id,))
            await channel.send("✅ Sort retiré de la Technique Maximum.")
            return await refresh()

        if param == "debloquer_sort_principal":
            locked = [s for s in db.get_character_sorts(character_id) if not s["is_unlocked"]]
            if not locked:
                await channel.send("Aucun sort principal verrouillé.")
                return True
            combien = await self._tech_ask_int(
                channel, staff, f"Combien veux tu en débloquer ? (max {len(locked)})",
                minimum=1, maximum=len(locked))
            if combien is None:
                return True
            remaining = list(locked)
            for _ in range(combien):
                items = [(s["id"], s["name"]) for s in remaining]
                sort_id = await self._tech_pick_numbered(channel, staff, items, "Sort à débloquer :")
                if sort_id is None:
                    return True
                with db.get_connection() as conn:
                    conn.execute("UPDATE character_sorts SET is_unlocked = 1 WHERE id = ?", (sort_id,))
                    # Force le déblocage du 1er secondaire (slot_index le plus bas) si son niveau_requis
                    # dépasse le niveau actuel du sort principal.
                    lvl = conn.execute("SELECT level FROM character_sorts WHERE id = ?",
                                       (sort_id,)).fetchone()["level"]
                    first = conn.execute(
                        "SELECT id, niveau_requis FROM character_secondary_sorts WHERE sort_id = ? "
                        "AND name IS NOT NULL ORDER BY slot_index LIMIT 1", (sort_id,)).fetchone()
                    if first is not None and (first["niveau_requis"] or 0) > lvl:
                        conn.execute("UPDATE character_secondary_sorts SET niveau_requis = ? WHERE id = ?",
                                     (lvl, first["id"]))
                remaining = [s for s in remaining if s["id"] != sort_id]
            await channel.send(f"✅ {combien} sort(s) principal(aux) débloqué(s).")
            return await refresh()

        if param == "bloquer_sort_principal":
            items = [(s["id"], s["name"]) for s in db.get_character_sorts(character_id)
                     if s["is_unlocked"] and s["slot_index"] != 0]
            if not items:
                await channel.send("Aucun sort principal verrouillable (le tout premier reste toujours débloqué).")
                return True
            sort_id = await self._tech_pick_numbered(channel, staff, items, "Quel sort principal bloquer ?")
            if sort_id is None:
                return True
            with db.get_connection() as conn:
                conn.execute("UPDATE character_sorts SET is_unlocked = 0 WHERE id = ?", (sort_id,))
            await channel.send("✅ Sort principal verrouillé.")
            return await refresh()

        # ---- Sorts secondaires ----
        if param == "nom_sort_secondaire":
            sort_id = await self.select_principal_sort(channel, staff, character_id)
            if sort_id is None:
                return True
            named = [r for r in db.get_secondary_sorts(sort_id) if r["name"] is not None]
            if not named:
                await channel.send("Ce sort principal n'a aucun sort secondaire nommé.")
                return True
            combien = await self._tech_ask_int(
                channel, staff, f"Combien de sorts secondaires renommer ? (1 à {len(named)})",
                minimum=1, maximum=len(named))
            if combien is None:
                return True
            for _ in range(combien):
                items = [(r["id"], r["name"]) for r in named]
                sec_id = await self._tech_pick_numbered(channel, staff, items, "Sort secondaire à renommer :")
                if sec_id is None:
                    return True
                nom = await self._ask_bounded_text(channel, staff, "Nouveau nom :", TECHNIQUE_NAME_MAX)
                if nom is None or nom is _CANCELLED:
                    return True
                with db.get_connection() as conn:
                    conn.execute("UPDATE character_secondary_sorts SET name = ? WHERE id = ?", (nom, sec_id))
                named = [r for r in db.get_secondary_sorts(sort_id) if r["name"] is not None]
            await channel.send(f"✅ {combien} sort(s) secondaire(s) renommé(s).")
            return await refresh()

        if param in ("niveau_requis_secondaire", "cout_eo_secondaire", "degats_secondaire"):
            sort_id = await self.select_principal_sort(channel, staff, character_id)
            if sort_id is None:
                return True
            principal = db.get_character_sort(sort_id)
            items = self._tech_secondary_items(sort_id, principal["level"], only_locked=None)
            if not items:
                await channel.send("Ce sort principal n'a aucun sort secondaire nommé.")
                return True
            sec_id = await self._tech_pick_numbered(channel, staff, items, "Sort secondaire à modifier :")
            if sec_id is None:
                return True
            if param == "niveau_requis_secondaire":
                v = await self._tech_ask_int(channel, staff, "Nouveau niveau requis ?", minimum=1)
                if v is None:
                    return True
                with db.get_connection() as conn:
                    conn.execute("UPDATE character_secondary_sorts SET niveau_requis = ? WHERE id = ?",
                                 (v, sec_id))
                    # Recalcule le seuil de fin de Phase 1 de ce sort principal, et l'unlock_level
                    # INDICATIF du sort principal suivant (aucun déblocage automatique).
                    threshold = conn.execute(
                        "SELECT MAX(niveau_requis) AS m FROM character_secondary_sorts "
                        "WHERE sort_id = ? AND name IS NOT NULL", (sort_id,)).fetchone()["m"]
                    if threshold is not None:
                        conn.execute("UPDATE character_sorts SET max_level_threshold = ? WHERE id = ?",
                                     (threshold, sort_id))
                        nxt = conn.execute(
                            "SELECT id FROM character_sorts WHERE character_id = ? AND slot_index = ?",
                            (principal["character_id"], principal["slot_index"] + 1)).fetchone()
                        if nxt is not None:
                            conn.execute("UPDATE character_sorts SET unlock_level = ? WHERE id = ?",
                                         (threshold + 5, nxt["id"]))
                await channel.send(f"✅ Niveau requis fixé à {v} (seuils recalculés).")
            elif param == "cout_eo_secondaire":
                v = await self._tech_ask_int(channel, staff, "Nouveau coût EO (en %) ?", minimum=0)
                if v is None:
                    return True
                with db.get_connection() as conn:
                    conn.execute("UPDATE character_secondary_sorts SET cout_pct = ? WHERE id = ?", (v, sec_id))
                await channel.send(f"✅ Coût EO fixé à {v}%.")
            else:  # degats_secondaire
                v = await self._tech_ask_int(channel, staff, "Nouvelle valeur de dégâts ?", minimum=1)
                if v is None:
                    return True
                with db.get_connection() as conn:
                    conn.execute("UPDATE character_secondary_sorts SET degats = ? WHERE id = ?", (v, sec_id))
                await channel.send(f"✅ Dégâts fixés à {v} pts.")
            return await refresh()

        if param == "debloquer_sort_secondaire":
            sort_id = await self.select_principal_sort(channel, staff, character_id)
            if sort_id is None:
                return True
            principal = db.get_character_sort(sort_id)
            items = self._tech_secondary_items(sort_id, principal["level"], only_locked=None)
            if not items:
                await channel.send("Ce sort principal n'a aucun sort secondaire nommé.")
                return True
            sec_id = await self._tech_pick_numbered(
                channel, staff, items, "Sort secondaire à débloquer (niveau requis = niveau actuel du sort principal) :")
            if sec_id is None:
                return True
            with db.get_connection() as conn:
                conn.execute("UPDATE character_secondary_sorts SET niveau_requis = ? WHERE id = ?",
                             (principal["level"], sec_id))
            await channel.send("✅ Sort secondaire débloqué (niveau requis aligné sur le sort principal).")
            return await refresh()

        if param == "bloquer_sort_secondaire":
            sort_id = await self.select_principal_sort(channel, staff, character_id)
            if sort_id is None:
                return True
            principal = db.get_character_sort(sort_id)
            items = self._tech_secondary_items(sort_id, principal["level"], only_locked=None)
            if not items:
                await channel.send("Ce sort principal n'a aucun sort secondaire nommé.")
                return True
            sec_id = await self._tech_pick_numbered(channel, staff, items, "Sort secondaire à reverrouiller :")
            if sec_id is None:
                return True
            v = await self._tech_ask_int(
                channel, staff,
                f"À quel niveau le reverrouiller ? (strictement supérieur à {principal['level']})",
                minimum=principal["level"] + 1)
            if v is None:
                return True
            with db.get_connection() as conn:
                conn.execute("UPDATE character_secondary_sorts SET niveau_requis = ? WHERE id = ?", (v, sec_id))
            await channel.send(f"✅ Sort secondaire reverrouillé (niveau requis {v}).")
            return await refresh()

        return True

    # ---------- gestion des buffs (staff) ----------
    async def _edit_buff(self, channel, staff, character_id):
        view = BuffChoiceView(staff.id)
        await channel.send(
            embed=discord.Embed(description="Gestion des buffs : ajouter ou retirer ?", color=PHOENIX_COLOR),
            view=view,
        )
        await view.wait()
        if view.result == "remove":
            return await self._buff_remove(channel, staff, character_id)
        if view.result == "add":
            return await self._buff_add(channel, staff, character_id)
        await channel.send("Aucun choix, modification ignorée.")
        return True

    async def _buff_remove(self, channel, staff, character_id):
        names = db.get_buff_names(character_id)
        if not names:
            await channel.send("Ce personnage n'a aucun buff actif, rien à retirer.")
            return True
        lines = [f"**{i + 1}.** {nm}" for i, nm in enumerate(names)]
        await channel.send(embed=discord.Embed(
            title="Buffs actifs", description="\n".join(lines) + "\n\nÉcris le nom ou le numéro du buff à retirer.",
            color=PHOENIX_COLOR,
        ))
        chosen = None
        while chosen is None:
            m = await self.wait_message(channel, staff)
            if m is None:
                return None
            c = m.content.strip()
            if c.isdigit() and 1 <= int(c) <= len(names):
                chosen = names[int(c) - 1]
                break
            low = c.lower()
            exact = [nm for nm in names if nm.lower() == low]
            pref = [nm for nm in names if nm.lower().startswith(low)]
            if exact:
                chosen = exact[0]
            elif len(pref) == 1:
                chosen = pref[0]
            else:
                await channel.send("Buff introuvable, réponds par le nom exact ou le numéro.")
        db.remove_buff(character_id, chosen)
        await channel.send(f"✅ Buff « {chosen} » retiré.")
        await self.send_stats(channel, character_id, staff.id)
        await self.send_profile(channel, character_id, staff.id)
        return True

    async def _buff_add(self, channel, staff, character_id):
        await channel.send("Quel est le nom de ce buff ?")
        m = await self.wait_message(channel, staff)
        if m is None:
            return None
        buff_name = m.content.strip()
        if not buff_name:
            await channel.send("Nom vide, buff annulé.")
            return True
        buff_id = db.add_buff(character_id, buff_name)

        n = None
        while n is None:
            await channel.send("Combien de stats ce buff concerne t il ?")
            m = await self.wait_message(channel, staff)
            if m is None:
                return None
            n = _parse_int(m.content, minimum=1)
            if n is None:
                await channel.send("Entre un nombre entier positif.")

        recap = []
        for i in range(n):
            await channel.send(f"**Stat {i + 1}/{n}** — laquelle ? (parmi les 8, aucune restriction)")
            stat = await self._pick_stat(channel, staff, list(STAT_KEYS))
            if stat is None:
                return None
            pts = None
            while pts is None:
                await channel.send(
                    f"Combien de points **{STAT_DISPLAY_NAMES[stat]}** donne ce buff ? (entier positif)"
                )
                mm = await self.wait_message(channel, staff)
                if mm is None:
                    return None
                pts = _parse_int(mm.content, minimum=1)  # strictement positif : jamais 0 ni négatif
                if pts is None:
                    await channel.send("Le nombre de points d'un buff doit être positif.")
            db.add_buff_effect(buff_id, stat, pts)
            recap.append(f"{STAT_DISPLAY_NAMES[stat]} +{pts}")

        await channel.send(f"✅ Buff « {buff_name} » créé : " + " · ".join(recap))
        await self.send_stats(channel, character_id, staff.id)
        await self.send_profile(channel, character_id, staff.id)
        return True

    # ---------- clan / rang (change le vrai rôle + resynchronise les points) ----------
    async def _swap_role(self, guild, character_id, slot, owner_uid, old_role_id, new_role_id):
        """Retire l'ancien rôle et attribue le nouveau : rôle RÉEL Discord pour le slot 1, rôle VIRTUEL
        (character_virtual_roles) pour les slots 2/3. Silencieux si permissions manquantes."""
        if old_role_id == new_role_id:
            return
        if slot == 1:
            member = guild.get_member(owner_uid) if guild else None
            if member is None:
                return
            try:
                if old_role_id:
                    r = guild.get_role(old_role_id)
                    if r and r in member.roles:
                        await member.remove_roles(r, reason="Modification staff /profil")
                if new_role_id:
                    r = guild.get_role(new_role_id)
                    if r and r not in member.roles:
                        await member.add_roles(r, reason="Modification staff /profil")
            except discord.Forbidden:
                print(f"[profil] Permission manquante pour changer le rôle de {owner_uid}.")
        else:
            if old_role_id:
                db.remove_virtual_role(character_id, old_role_id)
            if new_role_id:
                db.add_virtual_role(character_id, new_role_id)

    # ---------- rôles (ajout / retrait en masse) ----------
    async def _apply_single_role(self, character_id, slot, member, role, add):
        """Applique l'ajout OU le retrait d'UN rôle : rôle RÉEL Discord pour le slot 1, rôle VIRTUEL
        (character_virtual_roles) pour les slots 2/3. Retourne True si un changement réel a eu lieu,
        False si l'état était déjà celui voulu (ignoré silencieusement) ou si l'action a échoué."""
        if slot == 1:
            if member is None:
                return False
            has = any(r.id == role.id for r in member.roles)
            try:
                if add and not has:
                    await member.add_roles(role, reason="Modification staff /profil (rôles)")
                    return True
                if not add and has:
                    await member.remove_roles(role, reason="Modification staff /profil (rôles)")
                    return True
            except discord.Forbidden:
                print(f"[profil] Permission manquante pour modifier le rôle {role.id} de {member.id}.")
            return False
        # Slots 2/3 : rôle virtuel en base (add_virtual_role/remove_virtual_role renvoient True si la
        # ligne a réellement été insérée / supprimée, False si l'état était déjà celui voulu).
        if add:
            return db.add_virtual_role(character_id, role.id)
        return db.remove_virtual_role(character_id, role.id)

    async def _edit_roles(self, channel, staff, character_id, add: bool):
        char = get_character(character_id)
        if char is None:
            await channel.send("❌ Personnage introuvable.")
            return True

        # §1) Verrou par (staff, personnage) pour TOUTE la durée du flux. Si un AUTRE membre du staff
        # modifie déjà les rôles de CE personnage, on refuse (évite deux flux concurrents sur la même
        # cible). Le même (staff, personnage) « ne devrait pas arriver » (un seul flux par staff, garanti
        # par _active_users) : set.add étant idempotent, on l'ignore simplement.
        if any(cid == character_id and uid != staff.id for (uid, cid) in self._role_flow_locks):
            await channel.send(
                "Un autre membre du staff est déjà en train de modifier les rôles de ce personnage, "
                "réessaie dans un instant.")
            return True
        lock_key = (staff.id, character_id)
        self._role_flow_locks.add(lock_key)
        # try/finally : le verrou est TOUJOURS libéré à la fin — succès, cancel, timeout ou exception.
        try:
            return await self._edit_roles_locked(channel, staff, character_id, char, add)
        finally:
            self._role_flow_locks.discard(lock_key)

    async def _edit_roles_locked(self, channel, staff, character_id, char, add: bool):
        """Corps du flux « Ajouter/Retirer des rôles », exécuté SOUS le verrou (staff, personnage).
        Toutes les attentes de réponse passent par self.wait_message, déjà filtré sur CE staff ET CE
        salon (isolation standard du bot)."""
        slot = char["slot_number"]
        verbe = "ajouter" if add else "retirer"

        # 1) Nombre de rôles (entier strictement positif). Annulable via « cancel ».
        await channel.send(f"Combien de rôles veux tu {verbe} ? (ou écris `cancel` pour annuler)")
        n = None
        while n is None:
            m = await self.wait_message(channel, staff)
            if m is None:
                return None
            if _is_cancel(m.content):
                await channel.send("❌ Opération annulée.")
                return True
            n = _parse_int(m.content, minimum=1)
            if n is None:
                await channel.send("Entre un entier positif (au moins 1), ou `cancel` pour annuler.")

        # 2-3) Collecte de N rôles UNIQUES : déduplication (1re occurrence, ordre préservé) et complétion
        # INCRÉMENTALE des rôles manquants (le staff n'a pas à tout recommencer). AUCUNE modification en
        # base ni sur Discord tant que la collecte n'est pas complète -> annuler à n'importe quelle étape
        # ne laisse jamais d'état partiel.
        await channel.send(f"Mentionne les {n} rôles en un seul message. (ou `cancel` pour annuler)")
        collected, collected_ids = [], set()
        while True:
            m = await self.wait_message(channel, staff)
            if m is None:
                return None
            if _is_cancel(m.content):
                await channel.send("❌ Opération annulée.")
                return True
            raw = list(m.role_mentions)  # ordre d'apparition, doublons éventuels
            if not raw:
                await channel.send("Aucune mention de rôle détectée. Mentionne au moins un rôle "
                                   "(ou `cancel`).")
                continue
            # Comptage par rôle DANS ce message (pour signaler précisément les doublons).
            counts = {}
            for r in raw:
                counts[r.id] = counts.get(r.id, 0) + 1
            # Déduplication 1re occurrence (dict.fromkeys) + accumulation, en ignorant ce qui est déjà
            # collecté lors d'un message précédent (dédoublonnage aussi entre messages, par role_id).
            for r in dict.fromkeys(raw):
                if r.id not in collected_ids:
                    collected_ids.add(r.id)
                    collected.append(r)
            if len(collected) > n:
                # Trop de rôles distincts : on repart proprement de la sélection (rien n'a été modifié).
                collected, collected_ids = [], set()
                await channel.send(
                    f"Tu as mentionné plus de {n} rôle(s) distinct(s). Recommence : mentionne "
                    f"EXACTEMENT les {n} rôles voulus (ou `cancel`).")
                continue
            if len(collected) < n:
                manque = n - len(collected)
                dups = [(rid, c) for rid, c in counts.items() if c > 1]
                if dups:
                    details = ", ".join(f"<@&{rid}> {c} fois" for rid, c in dups)
                    await channel.send(
                        f"Il manque {manque} rôle(s) : tu as mentionné {details}. "
                        "Mentionne les rôles manquants (ou `cancel`).")
                else:
                    await channel.send(
                        f"Il manque {manque} rôle(s). Mentionne les rôles manquants (ou `cancel`).")
                continue
            break  # len(collected) == n : collecte complète

        # §2) Revérification de l'existence du personnage JUSTE avant l'écriture (il a pu être supprimé
        # pendant la collecte, qui attend des messages du staff). character_id étant constant pour tous
        # les rôles, un seul contrôle avant TOUTE écriture garantit le « tout ou rien » : si le personnage
        # n'existe plus, on n'applique AUCUNE modification de la liste.
        if get_character(character_id) is None:
            await channel.send("❌ Ce personnage n'existe plus, l'opération a été annulée.")
            return True

        # 4-5) Application rôle par rôle, chacun de façon TOTALEMENT INDÉPENDANTE : AUCUN effet de cascade
        # entre clan et grade (ni ailleurs). Le rôle est ajouté/retiré (réel slot 1 / virtuel slot 2-3)
        # et sync_role_points est appelé UNIQUEMENT pour CE rôle et SA propre catégorie, sans aucune
        # propagation vers les autres. Une incohérence visuelle temporaire (ex: Héritier sans clan) est
        # un état accepté, à corriger par un retrait explicite et séparé si le staff le souhaite.
        guild = channel.guild
        member = guild.get_member(char["user_id"]) if guild else None
        done, ignored = [], []
        for role in collected:
            changed = await self._apply_single_role(character_id, slot, member, role, add)
            (done if changed else ignored).append(role.name)
            category = db.get_role_point_category(role.id)
            if category:
                if add:
                    # Rôle du barème ajouté : (re)synchronise CETTE catégorie sur CE rôle (idempotent).
                    await sync_role_points(character_id, category, role.id)
                elif changed:
                    # Retrait effectif d'un rôle du barème : reprise complète des points de CETTE
                    # catégorie (new_role_id=None). Conditionné à `changed` pour ne PAS remettre à zéro
                    # une catégorie quand le rôle retiré n'appartenait pas réellement au personnage.
                    await sync_role_points(character_id, category, None)

        # 6) Récapitulatif (mentionne les rôles ignorés car déjà dans l'état voulu).
        verbe_pp = "ajouté(s)" if add else "retiré(s)"
        recap = f"✅ {len(done)} rôle(s) {verbe_pp}" + (f" : {', '.join(done)}" if done else "")
        recap += "." if not ignored else ""
        if ignored:
            etat = "déjà présents" if add else "déjà absents"
            recap += f".\nℹ️ {len(ignored)} ignoré(s) ({etat}) : {', '.join(ignored)}"
        await channel.send(recap)
        await self.send_profile(channel, character_id, staff.id)
        await self.send_stats(channel, character_id, staff.id)
        return True

    async def _edit_clan(self, channel, staff, character_id):
        char = get_character(character_id)
        if char is None:
            await channel.send("❌ Personnage introuvable.")
            return True
        valid = set(db.load_clan_state()["clans"].keys()) | {"sans_clan"}
        await channel.send("Nouveau clan ? (nom du clan, ou `sans_clan`)")
        m = await self.wait_message(channel, staff)
        if m is None:
            return None
        key = m.content.strip().lower()
        if key not in valid:
            await channel.send(f"❌ Clan inconnu. Clans valides : {', '.join(sorted(valid))}.")
            return True
        old_role_id = resolve_role_point_ids(None, char["clan"], None)[1]
        new_role_id = resolve_role_point_ids(None, key, None)[1]
        # 1) Change le vrai rôle (réel/virtuel) AVANT l'UPDATE en base.
        await self._swap_role(channel.guild, character_id, char["slot_number"], char["user_id"],
                              old_role_id, new_role_id)
        # 2) Met à jour la fiche.
        with db.get_connection() as conn:
            conn.execute("UPDATE validated_characters SET clan = ? WHERE id = ?", (key, character_id))
        # 3) Resynchronise les points de stats de la catégorie clan.
        await sync_role_points(character_id, "clan", new_role_id)
        await channel.send(f"✅ Clan = {key.capitalize() if key != 'sans_clan' else 'Sans clan'} "
                           "(rôle et points de stats mis à jour).")
        await self.send_profile(channel, character_id, staff.id)
        await self.send_stats(channel, character_id, staff.id)
        return True

    async def _edit_rang(self, channel, staff, character_id):
        char = get_character(character_id)
        if char is None:
            await channel.send("❌ Personnage introuvable.")
            return True
        noms = ", ".join(name for name, _ in GRADE_ROLES)
        await channel.send(f"Nouveau rang / grade ? (grades du barème : {noms})")
        m = await self.wait_message(channel, staff)
        if m is None:
            return None
        val = m.content.strip()
        if not val:
            await channel.send("❌ Le rang ne peut pas être vide.")
            return True
        new_role_id = GRADE_LABEL_TO_ROLE_ID.get(val)
        old_role_id = GRADE_LABEL_TO_ROLE_ID.get(char["grade"]) if char["grade"] else None
        if new_role_id is not None:
            # Grade reconnu du barème : change le vrai rôle + resynchronise les points.
            await self._swap_role(channel.guild, character_id, char["slot_number"], char["user_id"],
                                  old_role_id, new_role_id)
        with db.get_connection() as conn:
            conn.execute("UPDATE validated_characters SET grade = ? WHERE id = ?", (val, character_id))
        if new_role_id is not None:
            await sync_role_points(character_id, "grade", new_role_id)
            await channel.send(f"✅ Rang = {val} (rôle et points de stats mis à jour).")
        else:
            # Grade libre hors barème : on met à jour le texte sans toucher aux rôles ni aux points.
            await channel.send(f"✅ Rang = {val} (grade hors barème : rôle et points inchangés).")
        await self.send_profile(channel, character_id, staff.id)
        await self.send_stats(channel, character_id, staff.id)
        return True

    # ---------- application d'un paramètre scalaire ----------
    def _apply_scalar(self, character_id, param, raw):
        """Valide + applique un paramètre non-image/non-buff. Retourne (ok: bool, message)."""
        p = db.get_or_create_profile(character_id)

        # --- PV / EO (clamp de l'actuel sous son max) ---
        if param in ("pv_max", "eo_max"):
            v = _parse_int(raw, minimum=1)
            if v is None:
                return False, "❌ Valeur invalide (entier positif attendu)."
            base = param.split("_")[0]
            actuel_col = f"{base}_actuel"
            new_actuel = min(p[actuel_col], v)
            db.update_profile(character_id, **{param: v, actuel_col: new_actuel})
            return True, f"✅ {param} = {v} ({actuel_col} ajusté à {new_actuel})."
        if param in ("pv_actuel", "eo_actuel"):
            v = _parse_int(raw, minimum=0)
            if v is None:
                return False, "❌ Valeur invalide (entier positif ou nul attendu)."
            base = param.split("_")[0]
            v = min(v, p[f"{base}_max"])
            db.update_profile(character_id, **{param: v})
            return True, f"✅ {param} = {v}."

        # --- Niveau GÉNÉRAL du profil (character_profiles) ---
        if param in ("level", "xp_max", "xp_actuel"):
            v = _parse_int(raw, minimum=(1 if param == "level" else 0))
            if v is None:
                return False, "❌ Valeur invalide (entier positif attendu)."
            lvl, xa, xm = sync_level_and_xp(
                cur_level=p["level"], cur_xp_actuel=p["xp_actuel"], cur_xp_max=p["xp_max"], **{param: v},
            )
            db.update_profile(character_id, level=lvl, xp_actuel=xa, xp_max=xm)
            return True, f"✅ Niveau {lvl} — {xa}/{xm} XP."

        # --- Stats (points) : écriture ABSOLUE dans character_stats ---
        if param in STATS_PARAM_MAP:
            v = _parse_int(raw, minimum=0)
            if v is None:
                return False, "❌ Valeur invalide (entier positif ou nul attendu)."
            key = STATS_PARAM_MAP[param]
            db.set_stat_base_pts(character_id, key, v)
            return True, f"✅ {STAT_DISPLAY_NAMES[key]} (base) = {v} pts."
        if param == "points_stats":
            v = _parse_int(raw, minimum=0)
            if v is None:
                return False, "❌ Valeur invalide (entier positif ou nul attendu)."
            db.set_points_restants(character_id, v)
            return True, f"✅ Points de stats restants = {v}."

        # --- Anciens paramètres Force/Vitesse/Défense : convertis en points de character_stats ---
        # (Défense = Endurance). base = total_voulu(level/xp) - buffs actuels, clampé à 0.
        for legacy, stat_key in LEGACY_STAT_MAP.items():
            if param == f"{legacy}_pct" or param in (f"{legacy}_level", f"{legacy}_xp_max", f"{legacy}_xp_actuel"):
                v = _parse_int(raw, minimum=0)
                if v is None:
                    return False, "❌ Valeur invalide (entier positif attendu)."
                field = "pct" if param.endswith("_pct") else param[len(legacy) + 1:]
                buffs = db.sum_buff_points(character_id, stat_key)
                total_now = db.get_stat_base_pts(character_id, stat_key) + buffs
                # Courbe exponentielle des stats : xp_max dépend désormais du niveau (plus le 1000 plat).
                level, xp, xp_max_cur = points_to_level_xp_stat(total_now)
                if field == "level":
                    level = max(1, min(v, STAT_MAX_LEVEL))
                    xp_max_cur = db.xp_required_for_level(level)
                    xp = min(xp, xp_max_cur)
                elif field == "xp_actuel":
                    xp = max(0, min(v, xp_max_cur))
                elif field == "pct":
                    xp = max(0, min(round(v / 100 * xp_max_cur), xp_max_cur))
                elif field == "xp_max":  # borne l'XP courant (l'xp_max réel est dérivé du niveau)
                    xp = max(0, min(xp, v))
                target_total = level_xp_to_points_stat(level, xp)
                new_base = max(0, target_total - buffs)
                db.set_stat_base_pts(character_id, stat_key, new_base)
                lvl_disp = points_to_level_xp_stat(new_base + buffs)[0]
                return True, (f"✅ {STAT_DISPLAY_NAMES[stat_key]} : base {new_base} pts "
                              f"(buffs {'+' if buffs >= 0 else ''}{buffs}, niveau total {lvl_disp}).")

        # --- Compteurs de combat ---
        if param in ("victoires", "defaites", "nuls"):
            v = _parse_int(raw, minimum=0)
            if v is None:
                return False, "❌ Valeur invalide (entier positif ou nul attendu)."
            db.update_profile(character_id, **{param: v})
            return True, f"✅ {param} = {v}."

        # --- Maîtrise EO (stockée, mais l'affichage reste 0 % tant que le système n'existe pas) ---
        if param == "maitrise_eo_level":
            v = _parse_int(raw, minimum=1)
            if v is None:
                return False, "❌ Valeur invalide (entier positif attendu)."
            db.update_profile(character_id, maitrise_eo_level=v)
            return True, f"✅ Niveau de Maîtrise EO = {v}."

        # NB : "clan" et "rang" ne passent plus par ici : ils sont traités par _edit_clan / _edit_rang
        # (méthodes async) car ils modifient aussi le vrai rôle Discord et resynchronisent les points.
        return False, "❌ Paramètre non pris en charge."


# =====================================================================
# RATTRAPAGES RÉTROACTIFS (exécutés une seule fois au démarrage, à la suite des autres backfills).
# PV et valeurs de sorts secondaires ne touchent que ce qui n'a pas encore été corrigé (relançables sans
# danger) ; le statut de déblocage réaligne systématiquement slot 0 = débloqué / slots ≥ 1 = verrouillés.
# =====================================================================
async def backfill_pv_system():
    """PV des personnages créés avant le système de PV (pv_max < 5000) : recalcule 5000 + 500*(level-1)
    et remet pv_actuel au max. N'affecte que les profils encore sous l'ancienne valeur."""
    with db.get_connection() as conn:
        characters = conn.execute(
            "SELECT character_id, level, pv_max FROM character_profiles WHERE pv_max < 5000"
        ).fetchall()
        for char in characters:
            nouveau_pv_max = 5000 + 500 * (char["level"] - 1)
            conn.execute(
                "UPDATE character_profiles SET pv_max = ?, pv_actuel = ? WHERE character_id = ?",
                (nouveau_pv_max, nouveau_pv_max, char["character_id"]),
            )
            print(f"🔍 [backfill PV] Personnage {char['character_id']} : PV mis à jour à "
                  f"{nouveau_pv_max} (niveau {char['level']})")


async def backfill_secondary_sort_values():
    """Coût % et dégâts des sorts secondaires insérés sans ces valeurs, résolus depuis SPELL_CLASS_VALUES
    (dégâts tirés une fois dans la fourchette de la classe). Ignore une classe invalide."""
    with db.get_connection() as conn:
        secondaires = conn.execute(
            "SELECT id, classe, cout_pct, degats FROM character_secondary_sorts "
            "WHERE classe IS NOT NULL AND (cout_pct IS NULL OR cout_pct = 0 OR degats IS NULL OR degats = 0)"
        ).fetchall()
        for s in secondaires:
            if s["classe"] not in SPELL_CLASS_VALUES:
                print(f"⚠️ [backfill sorts] Sort {s['id']} a une classe invalide ('{s['classe']}'), ignoré.")
                continue
            cout_pct = SPELL_CLASS_VALUES[s["classe"]]["cout_pct"]
            degats_min = SPELL_CLASS_VALUES[s["classe"]]["degats_min"]
            degats_max = SPELL_CLASS_VALUES[s["classe"]]["degats_max"]
            degats = random.randint(degats_min, degats_max)
            conn.execute(
                "UPDATE character_secondary_sorts SET cout_pct = ?, degats = ? WHERE id = ?",
                (cout_pct, degats, s["id"]),
            )
            print(f"🔍 [backfill sorts] Sort {s['id']} (classe {s['classe']}) : coût {cout_pct}%, dégâts {degats}")


async def backfill_sort_unlock_status():
    """Statut de déblocage des sorts principaux créés avant is_unlocked : slot 0 débloqué, slots ≥ 1
    verrouillés (déblocage manuel staff à construire — cf. TODO dans _run_technique_creation)."""
    with db.get_connection() as conn:
        conn.execute("UPDATE character_sorts SET is_unlocked = 1 WHERE slot_index = 0")
        conn.execute("UPDATE character_sorts SET is_unlocked = 0 WHERE slot_index >= 1")
    print("🔍 [backfill sorts] Statuts de déblocage corrigés : seul le 1er sort principal de chaque "
          "personnage reste débloqué.")


async def backfill_territoire_defaults():
    """Territoires CRÉÉS mais figés à 0/NULL sur cout_eo_pct ou duree_tours (créés avant les valeurs de
    base) : remet le coût à 45 % et la durée à 3 tours (base). N'affecte que les lignes concernées."""
    with db.get_connection() as conn:
        territoires = conn.execute(
            "SELECT character_id FROM character_territoire WHERE name IS NOT NULL AND "
            "(cout_eo_pct IS NULL OR cout_eo_pct = 0 OR duree_tours IS NULL OR duree_tours = 0)"
        ).fetchall()
        for t in territoires:
            conn.execute(
                "UPDATE character_territoire SET cout_eo_pct = ?, duree_tours = ? WHERE character_id = ?",
                (TERRITOIRE_DEFAULT_COUT_EO_PCT, TERRITOIRE_DEFAULT_DUREE_TOURS, t["character_id"]),
            )
            print(f"🔍 [backfill territoire] Personnage {t['character_id']} : coût remis à "
                  f"{TERRITOIRE_DEFAULT_COUT_EO_PCT}%, durée à {TERRITOIRE_DEFAULT_DUREE_TOURS} tours (base).")


async def setup(bot):
    await bot.add_cog(Profil(bot))
