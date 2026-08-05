import asyncio
import os
import uuid
from datetime import datetime

import discord
from discord import app_commands
from discord.ext import commands

from cogs.utils import database as db
from cogs.utils.image_gen import generate_profil_image, generate_stats_image, generate_relations_image
# Réutilise les helpers déjà en place (personnages / comptes / couleur).
from cogs.banque import get_characters, get_character, PHOENIX_COLOR
# Réutilise la validation + téléchargement + compression d'image du parcours /depart, et le rôle staff.
from cogs.depart import (
    FICHE_STAFF_ROLE_ID, PORTRAIT_DIR, compress_portrait, _download_image_bytes, _resolve_portrait_url,
)

# ---------- Constantes ----------
WAIT_TIMEOUT = 300  # secondes d'attente d'une réponse texte
BACKGROUND_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "backgrounds")
PROFILE_IMG_DIR = os.path.join(os.path.dirname(__file__), "..", "temp", "profil_images")

# Les 6 sections encore non développées (boutons sous le profil). "stats" a désormais un vrai écran.
TODO_SECTIONS = [
    ("stats", "📊 Stats"),
    ("relation", "🤝 Relation"),
    ("technique", "⚡ Technique"),
    ("territoire", "🗺️ Territoire"),
    ("sorts", "📜 Sorts"),
    ("armes", "🗡️ Armes maudites"),
]


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
# TODO TEMPORAIRE : cette formule sera remplacée par le vrai système de niveaux plus tard.
def compute_xp_max_for_level(level: int) -> int:
    return level * 1000


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
        new_level = max(1, round(new_max / 1000))
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


def points_to_level_xp(total_points: int):
    level = total_points // 1000 + 1
    xp_actuel = total_points % 1000
    return level, xp_actuel, 1000


def level_xp_to_points(level: int, xp_actuel: int) -> int:
    return (level - 1) * 1000 + xp_actuel


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
    async def _render_profile(self, character_id) -> str:
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
        stats = []
        for stat_key, display_name in [("force", "Force"), ("vitesse", "Vitesse"), ("endurance", "Défense")]:
            total = await get_stat_total(character_id, stat_key)
            level, xp_actuel, xp_max = points_to_level_xp(total)
            pct = round(xp_actuel / xp_max * 100)
            stats.append((display_name, level, pct, (xp_actuel, xp_max)))

        # Les 4 maîtrises restent à niveau 1 / 0 % pour tout le monde tant que leur système n'existe pas.
        maitrises = [
            ("Maîtrise EO", 1, 0), ("Maîtrise Sort", 1, 0),
            ("Maîtrise Territoire", 1, 0), ("RCT", 1, 0),
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
        path = await self._render_profile(character_id)
        await channel.send(
            file=discord.File(path, filename="profil.png"),
            view=ProfileView(character_id, user_id, slot),
        )
        try:
            os.remove(path)
        except OSError:
            pass

    # ---------- rendu de l'image Stats ----------
    async def _render_stats(self, character_id):
        """Retourne (chemin_image, points_restants)."""
        s = db.get_or_create_stats(character_id)
        char = get_character(character_id)
        name = char["character_name"] if char else "?"
        portrait_path = char["portrait_path"] if char else None
        stats = []
        for key in STAT_KEYS:
            base = s[f"{key}_pts"]
            total = await get_stat_total(character_id, key)
            _num, pct, tranche = compute_tranche(base)
            stats.append((STAT_DISPLAY_NAMES[key], STAT_COLORS[key], base, total, pct, tranche))
        buffs = []
        for bname, effects in db.get_buffs_with_effects(character_id):
            parts = " · ".join(
                f"{STAT_DISPLAY_NAMES.get(k, k)} {'+' if pts >= 0 else ''}{pts}" for k, pts in effects
            )
            buffs.append(f"{bname}  →  {parts}" if parts else bname)
        path = _tmp_profile("stats")
        generate_stats_image(name, stats, buffs, s["points_restants"], path, portrait_path=portrait_path)
        return path, s["points_restants"]

    async def send_stats(self, channel, character_id, user_id):
        path, points_restants = await self._render_stats(character_id)
        view = StatsPageView(character_id, user_id) if points_restants > 0 else None
        await channel.send(file=discord.File(path, filename="stats.png"), view=view)
        try:
            os.remove(path)
        except OSError:
            pass

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
        elif cid.startswith("rel_create:"):
            await self.handle_rel_create(interaction, cid)
        elif cid.startswith("rel_remove:"):
            await self.handle_rel_remove(interaction, cid)
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
        path = _tmp_profile("relations")
        path, total_pages = generate_relations_image(name, relations, page, path, portrait_path=portrait_path)
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

    async def _select_related_character(self, channel, actor, target, ref_cid, ref_owner):
        """Détermine le personnage CIBLE d'un lien à partir du joueur mentionné.
        Gère l'auto-mention : si le joueur mentionné est le propriétaire du personnage de référence,
        on ne propose QUE ses autres personnages (jamais ref_cid lui même). Retourne le related_cid,
        ou None (annulation / aucun candidat, message déjà envoyé)."""
        if target.id == ref_owner:
            others = [c for c in get_characters(ref_owner, channel.guild.id) if c["id"] != ref_cid]
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
        # Joueur différent : sélection standard parmi SES personnages.
        return await self.select_character_await(
            channel, target, actor.id, "Ce joueur n'a aucun personnage."
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

            # 2) Joueur puis personnage cible du lien (avec gestion de l'auto-mention).
            await channel.send("Mentionne le joueur avec qui tu veux créer ce lien.")
            target = await self._await_mention(channel, interaction.user)
            if target is None:
                return
            related_cid = await self._select_related_character(
                channel, interaction.user, target, ref_cid, ref_owner
            )
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

    def _params_embed(self) -> discord.Embed:
        desc = (
            "**PROFIL :**\n"
            "PV maximum, PV minimum, EO maximum, EO minimum, Level, XP maximum, XP minimum, "
            "Clan, Rang, Victoires, Défaites, Nuls, Level maîtrise EO, Image, Fond\n\n"
            "**STATS :**\n"
            "Stats Force, Stats Vitesse, Stats Endurance, Stats Armes maudites, Stats RCT, "
            "Stats Territoire, Stats Sorts, Stats Énergie occulte, Buff, Points de stats\n\n"
            "Écris le nom du paramètre à modifier."
        )
        return discord.Embed(title="Paramètres modifiables", description=desc, color=PHOENIX_COLOR)

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
        await channel.send(embed=self._params_embed())
        param = await self._pick_param(channel, staff)
        if param is None:
            return None

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
                level, xp, _ = points_to_level_xp(total_now)
                if field == "level":
                    level = max(1, v)
                elif field == "xp_actuel":
                    xp = max(0, min(v, 1000))
                elif field == "pct":
                    xp = max(0, min(round(v / 100 * 1000), 1000))
                elif field == "xp_max":  # legacy : borne l'XP courant (xp_max vaut 1000 dans ce modèle)
                    xp = max(0, min(xp, v))
                target_total = level_xp_to_points(level, xp)
                new_base = max(0, target_total - buffs)
                db.set_stat_base_pts(character_id, stat_key, new_base)
                lvl_disp = points_to_level_xp(new_base + buffs)[0]
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

        # --- Clan ---
        if param == "clan":
            key = raw.strip().lower()
            valid = set(db.load_clan_state()["clans"].keys()) | {"sans_clan"}
            if key not in valid:
                return False, f"❌ Clan inconnu. Clans valides : {', '.join(sorted(valid))}."
            with db.get_connection() as conn:
                conn.execute("UPDATE validated_characters SET clan = ? WHERE id = ?", (key, character_id))
            return True, f"✅ Clan = {key.capitalize() if key != 'sans_clan' else 'Sans clan'}."

        # --- Rang / grade (texte libre) ---
        if param == "rang":
            val = raw.strip()
            if not val:
                return False, "❌ Le rang ne peut pas être vide."
            with db.get_connection() as conn:
                conn.execute("UPDATE validated_characters SET grade = ? WHERE id = ?", (val, character_id))
            return True, f"✅ Rang = {val}."

        return False, "❌ Paramètre non pris en charge."


async def setup(bot):
    await bot.add_cog(Profil(bot))
