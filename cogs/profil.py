import asyncio
import os
import uuid
from datetime import datetime

import discord
from discord import app_commands
from discord.ext import commands

from cogs.utils import database as db
from cogs.utils.image_gen import generate_profil_image
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

# Les 6 sections encore non développées (boutons "à venir" sous le profil).
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
# RÈGLES TEMPORAIRES DE NIVEAU / XP
# =====================================================================
# TODO TEMPORAIRE : cette formule sera remplacée par le vrai système de niveaux plus tard.
# Règle actuelle : XP nécessaire pour atteindre le niveau N = N * 1000.
def compute_xp_max_for_level(level: int) -> int:
    return level * 1000


def sync_level_and_xp(level=None, xp_actuel=None, xp_max=None,
                      cur_level=1, cur_xp_actuel=0, cur_xp_max=1000):
    """Recalcule (level, xp_actuel, xp_max) de façon cohérente selon ce qui a été fourni. Les cur_*
    portent les valeurs actuelles pour les cas partiels.
    - level fourni  : xp_max = compute_xp_max_for_level(level), xp_actuel clampé entre 0 et xp_max.
    - xp_max fourni (sans level) : level = round(xp_max / 1000), au minimum 1 ; xp_actuel reclampé.
    - seulement xp_actuel : garde level/xp_max existants, clamp xp_actuel entre 0 et xp_max existant.
    Utilisable tel quel pour le groupe global ET pour Force/Vitesse/Défense (colonnes *_level/*_xp_*)."""
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
# MÉTHODE STANDARD DU PROJET : rôle appliqué à un PERSONNAGE précis
# =====================================================================
async def character_has_role(guild, member, character_id, role_id) -> bool:
    """MÉTHODE STANDARD du projet pour vérifier si un rôle (camp / clan / grade / RCT / ...) s'applique
    à un PERSONNAGE précis :
      - slot_number == 1 : le personnage porte les VRAIS rôles Discord -> on inspecte member.roles ;
      - slot_number 2/3  : rôles seulement virtuels -> on lit character_virtual_roles pour ce personnage.
    Tout futur système du bot (combat, etc.) DOIT passer par cette fonction plutôt que d'inspecter
    member.roles directement, sinon les personnages de slot 2/3 seraient traités comme sans rôle."""
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
}

STAT_BASES = ("force", "vitesse", "defense")


def match_params(text: str):
    """Résolution d'un nom de paramètre : match exact d'un alias, sinon match par préfixe. Retourne
    la liste des clés candidates (0 = inconnu, 1 = direct, 2+ = ambigu à départager)."""
    t = text.strip().lower()
    exact = [k for k, aliases in PARAM_ALIASES.items() if any(a == t for a in aliases)]
    if exact:
        return exact
    return [k for k, aliases in PARAM_ALIASES.items() if any(a.startswith(t) for a in aliases)]


def _parse_int(raw: str, minimum=0):
    c = raw.strip().replace(" ", "").replace(",", "")
    if c.lstrip("-").isdigit() and int(c) >= minimum:
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


class StaffActionView(discord.ui.View):
    """Menu d'action staff après sélection d'un personnage à gérer."""

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
        # Isolation des flux textuels par joueur (mémoire : les flux wait_for ne survivent pas au reboot).
        self._active_users = set()

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

    # ---------- attente de saisie (isolation stricte auteur + salon) ----------
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
    def _render_profile(self, character_id) -> str:
        p = db.get_or_create_profile(character_id)
        char = get_character(character_id)
        name = char["character_name"] if char else "?"
        clan_key = char["clan"] if char else None
        if clan_key and clan_key != "sans_clan":
            clan = clan_key.capitalize()
        else:
            clan = "Sans clan"
        rang = (char["grade"] if char and char["grade"] else None) or "///"
        portrait_path = char["portrait_path"] if char else None
        bg = db.get_background(character_id)
        background_path = bg["image_path"] if bg else None

        def pct(a, b):
            return round(a / b * 100) if b else 0

        stats = [
            ("Force", p["force_level"], pct(p["force_xp_actuel"], p["force_xp_max"]),
             (p["force_xp_actuel"], p["force_xp_max"])),
            ("Vitesse", p["vitesse_level"], pct(p["vitesse_xp_actuel"], p["vitesse_xp_max"]),
             (p["vitesse_xp_actuel"], p["vitesse_xp_max"])),
            ("Défense", p["defense_level"], pct(p["defense_xp_actuel"], p["defense_xp_max"]),
             (p["defense_xp_actuel"], p["defense_xp_max"])),
        ]
        maitrises = [
            ("Maîtrise EO", p["maitrise_eo_level"], min(100, p["maitrise_eo_level"] * 10)),
            ("Maîtrise Sort", 1, 0),
            ("Maîtrise Territoire", 1, 0),
            ("RCT", 1, 0),
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
        """Envoie l'image de profil + les boutons persistants (mode consultation joueur)."""
        char = get_character(character_id)
        slot = char["slot_number"] if char else 1
        path = self._render_profile(character_id)
        await channel.send(
            file=discord.File(path, filename="profil.png"),
            view=ProfileView(character_id, user_id, slot),
        )
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
            if character_id is None:
                return
        finally:
            self._release(user_id)

        # Menu d'action staff (hors verrou : plus aucune saisie texte n'est en attente ici).
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
            description="\n".join(f"• {n}" for n in names),
            color=PHOENIX_COLOR,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

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
        """Attend une image (pièce jointe/lien, JPG/PNG, pas de GIF), la télécharge, la compresse en
        JPEG (même logique que l'upload de portrait de /depart) et l'écrit dans dest_dir/filename.
        Redemande tant que l'image n'est pas valide. Retourne le chemin sauvegardé, ou None (timeout)."""
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
                if ok is None:  # timeout
                    await channel.send("⏳ Session expirée, modifications interrompues.")
                    return
            await channel.send("Toutes les modifications ont été appliquées.")
        finally:
            self._release(user_id)

    def _params_embed(self) -> discord.Embed:
        lines = []
        for key, aliases in PARAM_ALIASES.items():
            lines.append(f"**{key}** — {aliases[0]}")
        return discord.Embed(
            title="Paramètres modifiables",
            description="\n".join(lines) + "\n\nÉcris le nom du paramètre à modifier.",
            color=PHOENIX_COLOR,
        )

    async def _pick_param(self, channel, user):
        """Fait choisir un paramètre par nom (préfixe, numéroté si ambigu). None si timeout."""
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
        """Une modification complète. Retourne True si traitée (même invalide→réessai résolu), None si
        timeout dur (pour interrompre proprement)."""
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
            # portrait_path lu à chaque appel par generate_slots_image ET par le profil -> l'UPDATE
            # se répercute partout automatiquement, aucune autre action nécessaire.
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
                return True
            # invalide : on redemande la valeur (le paramètre reste choisi)

    def _apply_scalar(self, character_id, param, raw):
        """Valide + applique un paramètre non-image. Retourne (ok: bool, message)."""
        p = db.get_or_create_profile(character_id)

        # --- PV / EO (avec clamp de l'actuel sous son max) ---
        if param in ("pv_max", "eo_max"):
            v = _parse_int(raw, minimum=1)
            if v is None:
                return False, "❌ Valeur invalide (entier positif attendu)."
            base = param.split("_")[0]  # "pv" / "eo"
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

        # --- XP global (level / xp_max / xp_actuel cohérents) ---
        if param in ("level", "xp_max", "xp_actuel"):
            v = _parse_int(raw, minimum=(1 if param == "level" else 0))
            if v is None:
                return False, "❌ Valeur invalide (entier positif attendu)."
            lvl, xa, xm = sync_level_and_xp(
                cur_level=p["level"], cur_xp_actuel=p["xp_actuel"], cur_xp_max=p["xp_max"],
                **{param: v},
            )
            db.update_profile(character_id, level=lvl, xp_actuel=xa, xp_max=xm)
            return True, f"✅ Niveau {lvl} — {xa}/{xm} XP."

        # --- Force / Vitesse / Défense ---
        for base in STAT_BASES:
            if param == f"{base}_pct":
                v = _parse_int(raw, minimum=0)
                if v is None or v > 100:
                    return False, "❌ Pourcentage invalide (0 à 100)."
                xm = p[f"{base}_xp_max"]
                xa = max(0, min(round(v / 100 * xm), xm))
                db.update_profile(character_id, **{f"{base}_xp_actuel": xa})
                return True, f"✅ {base.capitalize()} : {v}% ({xa}/{xm} XP)."
            if param in (f"{base}_level", f"{base}_xp_max", f"{base}_xp_actuel"):
                field = param[len(base) + 1:]  # level / xp_max / xp_actuel
                v = _parse_int(raw, minimum=(1 if field == "level" else 0))
                if v is None:
                    return False, "❌ Valeur invalide (entier positif attendu)."
                lvl, xa, xm = sync_level_and_xp(
                    cur_level=p[f"{base}_level"], cur_xp_actuel=p[f"{base}_xp_actuel"],
                    cur_xp_max=p[f"{base}_xp_max"], **{field: v},
                )
                db.update_profile(character_id, **{
                    f"{base}_level": lvl, f"{base}_xp_actuel": xa, f"{base}_xp_max": xm,
                })
                return True, f"✅ {base.capitalize()} : niveau {lvl} — {xa}/{xm} XP."

        # --- Compteurs de combat ---
        if param in ("victoires", "defaites", "nuls"):
            v = _parse_int(raw, minimum=0)
            if v is None:
                return False, "❌ Valeur invalide (entier positif ou nul attendu)."
            db.update_profile(character_id, **{param: v})
            return True, f"✅ {param} = {v}."

        # --- Maîtrise EO ---
        if param == "maitrise_eo_level":
            v = _parse_int(raw, minimum=1)
            if v is None:
                return False, "❌ Valeur invalide (entier positif attendu)."
            db.update_profile(character_id, maitrise_eo_level=v)
            return True, f"✅ Niveau de Maîtrise EO = {v}."

        # --- Clan (validation contre les clans existants + sans_clan) ---
        if param == "clan":
            key = raw.strip().lower()
            valid = set(db.load_clan_state()["clans"].keys()) | {"sans_clan"}
            if key not in valid:
                dispo = ", ".join(sorted(valid))
                return False, f"❌ Clan inconnu. Clans valides : {dispo}."
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
