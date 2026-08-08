# RÈGLES DE ROBUSTESSE PERMANENTES DU PROJET — voir cogs/shop.py (règles 1 à 6). En particulier :
# revérif du rôle/chef AU CLIC, isolation des flux textuels par utilisateur, revérif des soldes en
# temps réel juste avant débit, anti double-clic sur tout bouton de confirmation/exécution.

import asyncio
import os
import uuid
from datetime import datetime, timedelta

import discord
from discord import app_commands
from discord.ext import commands, tasks

from cogs.utils import database as db
from cogs.utils.image_gen import (
    generate_ordre_image, generate_ordre_educatif_image, generate_contrats_educatifs_image,
    generate_staff_image, generate_contrats_direct_image,
    generate_pin_image, generate_tresorerie_ordre_image, generate_salons_ordre_image,
    STAFF_ROLE_ORDER,
)
# Helpers bancaires déjà existants (personnages / comptes / couleur). apply_debit est une MÉTHODE
# du cog Banque (protection découvert + compte à rebours) : on la récupère via get_cog("Banque").
# On réutilise aussi tels quels l'IBAN/PIN, l'affichage du clavier PIN, la fenêtre de session (1 h)
# et le format de date, pour ne pas dupliquer la logique de /banque.
from cogs.banque import (
    get_characters, get_character, get_account, PHOENIX_COLOR,
    generate_unique_iban, generate_pin_code, _pin_values, _within_1h, _fmt_date, OWNER_ID,
    credit_account, add_transaction,
)
# Méthode standard du projet : rôle appliqué à un PERSONNAGE (réel slot 1 / virtuel slot 2-3).
from cogs.profil import character_has_role

# ---------- Constantes ----------
WAIT_TIMEOUT = 300
DM_TIMEOUT = 300  # 5 minutes pour la négociation de revente à un joueur
MAX_DASHBOARD_SALONS = 6  # nb de salons montrés sur le dashboard principal (la pillow dédiée les affiche tous)

ORDER_TYPES = {
    "educatif": {"label": "Ordre Éducatif", "prix": 84300},
    "direct": {"label": "Ordre Direct", "prix": 284944},
    "hybride": {"label": "Ordre Hybride", "prix": 633822},
}
TAXE_SALON = 15000  # taxe hebdomadaire ET prix d'achat/vente au gouvernement, par salon
STAFF_MANAGER_ROLE_ID = 1522182819462381729  # rôle (réel ou virtuel) autorisant la gestion staff

ROLE_COLORS = {
    "Chef d'ordre": (255, 165, 60),
    "Sous-chef": (230, 90, 90),
    "Formateur": (100, 200, 150),
    "Chef d'équipe": (90, 150, 240),
    "Membre d'équipe": (170, 170, 180),
    "Corps administratif": (190, 100, 240),
}
# Rôles attribuables à un membre (le chef n'en fait pas partie : c'est le propriétaire de l'ordre).
ASSIGNABLE_ROLES = ["Sous-chef", "Formateur", "Chef d'équipe", "Membre d'équipe", "Corps administratif"]
FR_DAYS = ["Lun", "Mar", "Mer", "Jeu", "Ven", "Sam", "Dim"]

ORDRE_IMG_DIR = os.path.join(os.path.dirname(__file__), "..", "temp", "ordre_images")


def _now() -> str:
    return datetime.utcnow().isoformat()


def _fmt(n: int) -> str:
    return f"{n:,}".replace(",", " ")


def _tmp(prefix: str) -> str:
    os.makedirs(ORDRE_IMG_DIR, exist_ok=True)
    return os.path.join(ORDRE_IMG_DIR, f"{prefix}_{uuid.uuid4().hex}.png")


def _rm(path):
    try:
        os.remove(path)
    except OSError:
        pass


def _parse_int(raw: str):
    c = raw.strip().replace(" ", "").replace(",", "")
    if c.lstrip("-").isdigit():
        return int(c)
    return None


def _plus_months(iso: str, months: int) -> str:
    """Ajoute `months` mois (approximés à 30 jours) à une date ISO. Utilisé pour les échéances de
    verrou (grâce 2 mois), les avertissements (1 mois) et les bans (2 mois)."""
    try:
        base = datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        base = datetime.utcnow()
    return (base + timedelta(days=30 * months)).isoformat()


# Rang hiérarchique d'un rôle (0 = plus haut gradé). Réutilise l'ordre déjà défini pour la pillow staff.
ORDER_HIERARCHY_RANK = {role: i for i, role in enumerate(STAFF_ROLE_ORDER)}


# =====================================================================
# VUES EN SESSION (view.wait) — utilisées à l'intérieur de flux déjà verrouillés
# =====================================================================
class OrdreCharacterSelect(discord.ui.Select):
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


class OrdreCharacterSelectView(discord.ui.View):
    def __init__(self, chars, invoker_id):
        super().__init__(timeout=WAIT_TIMEOUT)
        self.result = None
        self.add_item(OrdreCharacterSelect(chars, invoker_id))


class SimpleSelectView(discord.ui.View):
    """Menu déroulant générique renvoyant la value choisie (rôles, ordres...)."""

    def __init__(self, placeholder, options, invoker_id):
        super().__init__(timeout=WAIT_TIMEOUT)
        self.result = None
        self.invoker_id = invoker_id
        self._sel = discord.ui.Select(
            placeholder=placeholder, min_values=1, max_values=1,
            options=[discord.SelectOption(label=lbl[:100], value=str(val)) for lbl, val in options],
        )
        self._sel.callback = self._cb
        self.add_item(self._sel)

    async def _cb(self, interaction):
        if interaction.user.id != self.invoker_id:
            await interaction.response.send_message("Ce menu ne t'appartient pas.", ephemeral=True)
            return
        self.result = self._sel.values[0]
        await interaction.response.edit_message(view=None)
        self.stop()


class TwoChoiceView(discord.ui.View):
    """2 boutons renvoyant une valeur, avec anti double-clic (règle #6)."""

    def __init__(self, owner_id, a_label, a_value, b_label, b_value, a_style=discord.ButtonStyle.danger, b_style=discord.ButtonStyle.primary):
        super().__init__(timeout=WAIT_TIMEOUT)
        self.owner_id = owner_id
        self.result = None
        self._done = False
        a = discord.ui.Button(label=a_label, style=a_style)
        b = discord.ui.Button(label=b_label, style=b_style)
        a.callback = self._mk(a_value)
        b.callback = self._mk(b_value)
        self.add_item(a)
        self.add_item(b)

    def _mk(self, value):
        async def cb(interaction):
            if interaction.user.id != self.owner_id:
                await interaction.response.send_message("Ce choix ne t'appartient pas.", ephemeral=True)
                return
            if self._done:
                try:
                    await interaction.response.defer()
                except discord.HTTPException:
                    pass
                return
            self._done = True
            self.result = value
            for it in self.children:
                it.disabled = True
            await interaction.response.edit_message(view=self)
            self.stop()
        return cb


class NegotiationView(discord.ui.View):
    """Proposition de revente en DM : Accepter / Refuser / Négocier (anti double-clic)."""

    def __init__(self, owner_id):
        super().__init__(timeout=DM_TIMEOUT)
        self.owner_id = owner_id
        self.result = None
        self._done = False
        for label, emoji, value, style in [
            ("Accepter", "✅", "accept", discord.ButtonStyle.success),
            ("Refuser", "❌", "refuse", discord.ButtonStyle.danger),
            ("Négocier", "💬", "negotiate", discord.ButtonStyle.primary),
        ]:
            btn = discord.ui.Button(label=label, emoji=emoji, style=style)
            btn.callback = self._mk(value)
            self.add_item(btn)

    def _mk(self, value):
        async def cb(interaction):
            if interaction.user.id != self.owner_id:
                await interaction.response.send_message("Cette proposition ne t'est pas destinée.", ephemeral=True)
                return
            if self._done:
                try:
                    await interaction.response.defer()
                except discord.HTTPException:
                    pass
                return
            self._done = True
            self.result = value
            for it in self.children:
                it.disabled = True
            await interaction.response.edit_message(view=self)
            self.stop()
        return cb


# =====================================================================
# VUES PERSISTANTES (custom_id dynamiques -> listener on_interaction)
# =====================================================================
class SalonPageView(discord.ui.View):
    """Sous la pillow de TOUS les salons : pagination persistante (ligne 0, seulement si plusieurs
    pages, page encodée dans le custom_id) + menu Revendre / Louer (ligne 1), tous persistants."""

    def __init__(self, order_id, user_id, page, total_pages):
        super().__init__(timeout=None)
        if total_pages > 1:
            self.add_item(discord.ui.Button(
                label="Page précédente", emoji="◀️", style=discord.ButtonStyle.secondary,
                custom_id=f"ordre_salon_prev:{order_id}:{user_id}:{page}", disabled=(page <= 1), row=0))
            self.add_item(discord.ui.Button(
                label="Page suivante", emoji="▶️", style=discord.ButtonStyle.secondary,
                custom_id=f"ordre_salon_next:{order_id}:{user_id}:{page}", disabled=(page >= total_pages), row=0))
        self.add_item(discord.ui.Select(
            placeholder="Action sur les salons...", min_values=1, max_values=1,
            custom_id=f"ordre_salon_action:{order_id}:{user_id}",
            options=[
                discord.SelectOption(label="💸 Revendre un salon", value="revendre"),
                discord.SelectOption(label="🏠 Louer un salon", value="louer"),
            ], row=1))



class OrdreCreateConfirmView(discord.ui.View):
    def __init__(self, character_id, user_id):
        super().__init__(timeout=None)
        self.add_item(discord.ui.Button(
            label="Créer", emoji="✅", style=discord.ButtonStyle.success,
            custom_id=f"ordre_create_yes:{character_id}:{user_id}"))
        self.add_item(discord.ui.Button(
            label="Annuler", emoji="❌", style=discord.ButtonStyle.danger,
            custom_id=f"ordre_create_no:{user_id}"))


class OrdreTypeView(discord.ui.View):
    def __init__(self, character_id, user_id):
        super().__init__(timeout=None)
        for key, emoji in [("educatif", "🎓"), ("direct", "⚔️"), ("hybride", "🌗")]:
            self.add_item(discord.ui.Button(
                label=ORDER_TYPES[key]["label"].replace("Ordre ", ""), emoji=emoji,
                style=discord.ButtonStyle.primary,
                custom_id=f"ordre_type_{key}:{character_id}:{user_id}"))


class OrdreDashboardView(discord.ui.View):
    """Boutons d'un ordre Direct / Hybride."""

    def __init__(self, order_id, user_id):
        super().__init__(timeout=None)
        self.add_item(discord.ui.Button(
            label="Avoir des salons", emoji="🏠", style=discord.ButtonStyle.primary,
            custom_id=f"ordre_salons_buy:{order_id}:{user_id}", row=0))
        self.add_item(discord.ui.Button(
            label="Staff", emoji="👥", style=discord.ButtonStyle.secondary,
            custom_id=f"ordre_staff:{order_id}:{user_id}", row=0))
        self.add_item(discord.ui.Button(
            label="Contrat", emoji="📄", style=discord.ButtonStyle.secondary,
            custom_id=f"ordre_contrat:{order_id}:{user_id}", row=1))
        self.add_item(discord.ui.Button(
            label="Trésorerie", emoji="💰", style=discord.ButtonStyle.secondary,
            custom_id=f"ordre_tresorerie:{order_id}:{user_id}", row=1))
        self.add_item(discord.ui.Button(
            label="Salon", emoji="🏘️", style=discord.ButtonStyle.secondary,
            custom_id=f"ordre_salon:{order_id}:{user_id}", row=1))


class OrdreEducatifView(discord.ui.View):
    def __init__(self, order_id, user_id):
        super().__init__(timeout=None)
        self.add_item(discord.ui.Button(
            label="Voir les contrats", emoji="📄", style=discord.ButtonStyle.secondary,
            custom_id=f"ordre_contrats_view:{order_id}:{user_id}"))
        self.add_item(discord.ui.Button(
            label="Staff", emoji="👥", style=discord.ButtonStyle.secondary,
            custom_id=f"ordre_staff:{order_id}:{user_id}"))


class ContratsPageView(discord.ui.View):
    """Pagination persistante sous la pillow des contrats éducatifs (même pattern que /inventaire,
    /shop et la page Relation de /profil : page encodée dans le custom_id, désactivée en bout)."""

    def __init__(self, order_id, user_id, page, total_pages):
        super().__init__(timeout=None)
        self.add_item(discord.ui.Button(
            label="Page précédente", emoji="◀️", style=discord.ButtonStyle.secondary,
            custom_id=f"ordre_contrats_prev:{order_id}:{user_id}:{page}", disabled=(page <= 1)))
        self.add_item(discord.ui.Button(
            label="Page suivante", emoji="▶️", style=discord.ButtonStyle.secondary,
            custom_id=f"ordre_contrats_next:{order_id}:{user_id}:{page}", disabled=(page >= total_pages)))


class ContratsDirectPageView(discord.ui.View):
    """Pagination persistante sous la pillow des contrats côté ordre employeur (Direct/Hybride)."""

    def __init__(self, order_id, user_id, page, total_pages):
        super().__init__(timeout=None)
        self.add_item(discord.ui.Button(
            label="Page précédente", emoji="◀️", style=discord.ButtonStyle.secondary,
            custom_id=f"ordre_cdir_prev:{order_id}:{user_id}:{page}", disabled=(page <= 1)))
        self.add_item(discord.ui.Button(
            label="Page suivante", emoji="▶️", style=discord.ButtonStyle.secondary,
            custom_id=f"ordre_cdir_next:{order_id}:{user_id}:{page}", disabled=(page >= total_pages)))


class OrdreStaffView(discord.ui.View):
    """Sous la pillow du staff : pagination (ligne 0, seulement si plusieurs pages, page encodée dans
    le custom_id) + actions Ajouter / Virer / Muter (ligne 1)."""

    def __init__(self, order_id, user_id, page=1, total_pages=1):
        super().__init__(timeout=None)
        if total_pages > 1:
            self.add_item(discord.ui.Button(
                label="Page précédente", emoji="◀️", style=discord.ButtonStyle.secondary,
                custom_id=f"ordre_staff_prev:{order_id}:{user_id}:{page}", disabled=(page <= 1), row=0))
            self.add_item(discord.ui.Button(
                label="Page suivante", emoji="▶️", style=discord.ButtonStyle.secondary,
                custom_id=f"ordre_staff_next:{order_id}:{user_id}:{page}", disabled=(page >= total_pages), row=0))
        self.add_item(discord.ui.Button(
            label="Ajouter", emoji="➕", style=discord.ButtonStyle.success,
            custom_id=f"ordre_staff_add:{order_id}:{user_id}", row=1))
        self.add_item(discord.ui.Button(
            label="Virer", emoji="➖", style=discord.ButtonStyle.danger,
            custom_id=f"ordre_staff_fire:{order_id}:{user_id}", row=1))
        self.add_item(discord.ui.Button(
            label="Muter", emoji="🔄", style=discord.ButtonStyle.primary,
            custom_id=f"ordre_staff_mute:{order_id}:{user_id}", row=1))


# --- Banque de l'ordre (réutilise le principe du clavier PIN de /banque) ---
class OrdrePinOpenView(discord.ui.View):
    def __init__(self, order_id, user_id):
        super().__init__(timeout=None)
        self.add_item(discord.ui.Button(
            label="Entrer le code", emoji="🔓", style=discord.ButtonStyle.primary,
            custom_id=f"ordre_pin_open:{order_id}:{user_id}"))


class OrdrePinKeypadView(discord.ui.View):
    """Clavier numérique de saisie du code de l'ordre (mêmes boutons/logique que /banque)."""

    def __init__(self, order_id, user_id):
        super().__init__(timeout=None)
        base = f"{order_id}:{user_id}"
        for row, digits in enumerate((["1", "2", "3"], ["4", "5", "6"], ["7", "8", "9"])):
            for dg in digits:
                self.add_item(discord.ui.Button(
                    label=dg, style=discord.ButtonStyle.secondary,
                    custom_id=f"ordre_pin_digit:{base}:{dg}", row=row))
        self.add_item(discord.ui.Button(
            label="⌫", style=discord.ButtonStyle.danger, custom_id=f"ordre_pin_clear:{base}", row=3))
        self.add_item(discord.ui.Button(
            label="0", style=discord.ButtonStyle.secondary, custom_id=f"ordre_pin_digit:{base}:0", row=3))
        self.add_item(discord.ui.Button(
            label="✅", style=discord.ButtonStyle.success, custom_id=f"ordre_pin_confirm:{base}", row=3))


class OrdrePinErrorView(discord.ui.View):
    def __init__(self, order_id, user_id):
        super().__init__(timeout=None)
        self.add_item(discord.ui.Button(
            label="Réessayer", emoji="🔁", style=discord.ButtonStyle.danger,
            custom_id=f"ordre_pin_retry:{order_id}:{user_id}"))
        self.add_item(discord.ui.Button(
            label="Recevoir mon code", emoji="📩", style=discord.ButtonStyle.secondary,
            custom_id=f"ordre_resend_pin:{order_id}:{user_id}"))


class TresorerieView(discord.ui.View):
    def __init__(self, order_id, user_id):
        super().__init__(timeout=None)
        self.add_item(discord.ui.Button(
            label="IBAN", emoji="📇", style=discord.ButtonStyle.secondary,
            custom_id=f"ordre_iban:{order_id}"))
        self.add_item(discord.ui.Button(
            label="Virement", emoji="💸", style=discord.ButtonStyle.primary,
            custom_id=f"ordre_virement:{order_id}:{user_id}"))
        self.add_item(discord.ui.Button(
            label="Salaire", emoji="💰", style=discord.ButtonStyle.secondary,
            custom_id=f"ordre_salaire:{order_id}:{user_id}"))


class SalaireView(discord.ui.View):
    """Sous la trésorerie (déjà authentifiée), chef uniquement : ajouter ou retirer des salaires."""

    def __init__(self, order_id, user_id):
        super().__init__(timeout=None)
        self.add_item(discord.ui.Button(
            label="Ajouter", emoji="➕", style=discord.ButtonStyle.success,
            custom_id=f"ordre_sal_add:{order_id}:{user_id}"))
        self.add_item(discord.ui.Button(
            label="Retirer", emoji="➖", style=discord.ButtonStyle.danger,
            custom_id=f"ordre_sal_remove:{order_id}:{user_id}"))


class LockRecoveryView(discord.ui.View):
    """DM au chef quand la trésorerie remonte au dessus de 0 alors que le verrou est actif : garder le
    verrou (protection, désactivation auto dans 2 mois) ou le désactiver tout de suite."""

    def __init__(self, order_id, user_id):
        super().__init__(timeout=None)
        self.add_item(discord.ui.Button(
            label="Garder actif", emoji="🔒", style=discord.ButtonStyle.primary,
            custom_id=f"ordre_lockkeep:{order_id}:{user_id}"))
        self.add_item(discord.ui.Button(
            label="Désactiver maintenant", emoji="🔓", style=discord.ButtonStyle.danger,
            custom_id=f"ordre_lockoff:{order_id}:{user_id}"))


# =====================================================================
# COG
# =====================================================================
class Ordre(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._active_users = set()   # isolation des flux textuels par joueur
        self._creating = set()       # anti double-clic sur la création (par character_id)
        self._pin_buffers = {}       # saisie du code de la banque d'ordre, clé (user_id, order_id)
        self._grace_prompt_pending = set()  # ordres à qui le DM de choix de verrou a déjà été envoyé
                                            # (évite de le renvoyer chaque jour tant que le chef n'a pas répondu)

    async def cog_load(self):
        # La tâche quotidienne des salaires (paiements le lundi + suivi des verrous/échéances).
        if not self.salary_loop.is_running():
            self.salary_loop.start()

    async def cog_unload(self):
        self.salary_loop.cancel()

    # ---------- verrou de flux ----------
    def _acquire(self, user_id) -> bool:
        if user_id in self._active_users:
            return False
        self._active_users.add(user_id)
        return True

    def _release(self, user_id):
        self._active_users.discard(user_id)

    async def wait_message(self, channel, author, timeout=WAIT_TIMEOUT):
        def check(m):
            return m.channel.id == channel.id and m.author.id == author.id and not m.author.bot
        try:
            return await self.bot.wait_for("message", check=check, timeout=timeout)
        except asyncio.TimeoutError:
            return None

    # ---------- sélection de personnage (principe de banque.select_character_flow, adapté pour
    # renvoyer un character_id à réutiliser dans le flux /ordre) ----------
    async def _select_character(self, channel, user):
        chars = get_characters(user.id, channel.guild.id)
        if not chars:
            await channel.send("Tu n'as aucun personnage validé.")
            return None
        if len(chars) == 1:
            return chars[0]["id"]
        view = OrdreCharacterSelectView(chars, user.id)
        await channel.send("Sélectionne le personnage concerné :", view=view)
        await view.wait()
        return view.result

    async def _select_character_of(self, channel, invoker, target, none_msg):
        chars = get_characters(target.id, channel.guild.id)
        if not chars:
            await channel.send(none_msg)
            return None
        if len(chars) == 1:
            return chars[0]["id"]
        view = OrdreCharacterSelectView(chars, invoker.id)
        await channel.send("Sélectionne le personnage :", view=view)
        await view.wait()
        return view.result

    async def _await_mention(self, channel, actor):
        while True:
            m = await self.wait_message(channel, actor)
            if m is None:
                await channel.send("⏳ Annulé.")
                return None
            if m.mentions:
                return m.mentions[0]
            await channel.send("Merci de **mentionner** un joueur (ex : @Pseudo).")

    async def _ask_positive_int(self, channel, user, prompt, maximum=None):
        await channel.send(prompt)
        while True:
            m = await self.wait_message(channel, user)
            if m is None:
                await channel.send("⏳ Annulé.")
                return None
            v = _parse_int(m.content)
            if v is None or v < 1:
                await channel.send("Entre un nombre entier positif.")
                continue
            if maximum is not None and v > maximum:
                await channel.send(f"Maximum {maximum}.")
                continue
            return v

    async def _await_choice(self, channel, user, valid):
        while True:
            m = await self.wait_message(channel, user)
            if m is None:
                await channel.send("⏳ Annulé.")
                return None
            c = m.content.strip()
            if c in valid:
                return c
            await channel.send(f"Réponds par {' ou '.join(valid)}.")

    async def _pick_role(self, channel, user, roles, prompt="Quel rôle attribuer ?"):
        view = SimpleSelectView("Choisis un rôle...", [(r, r) for r in roles], user.id)
        await channel.send(prompt, view=view)
        await view.wait()
        return view.result

    async def _pick_educator(self, channel, user, order_id, guild):
        """Sélection d'un éducateur (Formateur) de l'ordre pour rattacher un disciple. Retourne :
        - "NO_EDUCATOR" s'il n'existe aucun formateur (l'appelant doit annuler l'ajout) ;
        - un educator_character_id (int) si choisi ;
        - None si annulé / délai dépassé."""
        formateurs = db.get_order_members_by_role(order_id, "Formateur")
        if not formateurs:
            return "NO_EDUCATOR"
        options, lines = [], []
        for f in formateurs:
            prenom = (f["character_name"] or "?").split()[0] if f["character_name"] else "?"
            m = guild.get_member(f["user_id"]) if guild else None
            mention = m.mention if m else f"<@{f['user_id']}>"
            disp = m.display_name if m else str(f["user_id"])
            options.append((f"{prenom} ({disp})", f["character_id"]))
            lines.append(f"• **{prenom}** ({mention})")
        view = SimpleSelectView("Choisis un éducateur...", options, user.id)
        await channel.send(
            embed=discord.Embed(
                title="Chez quel éducateur veux tu envoyer ce disciple ?",
                description="\n".join(lines), color=PHOENIX_COLOR),
            view=view)
        await view.wait()
        return int(view.result) if view.result is not None else None

    # ---------- garde "chef uniquement" (à appliquer EN PREMIER dans chaque bouton) ----------
    def _is_chief(self, order_id, user_id) -> bool:
        order = db.get_order(order_id)
        if not order:
            return False
        chef = get_character(order["chef_character_id"])
        return chef is not None and chef["user_id"] == user_id

    async def _require_chief(self, interaction, order_id) -> bool:
        if not self._is_chief(order_id, interaction.user.id):
            await interaction.response.send_message("Seul le chef de l'ordre peut faire ça.", ephemeral=True)
            return False
        return True

    async def _require_staff_manager(self, interaction, order_id) -> bool:
        """Chef ET rôle STAFF_MANAGER (réel slot 1 / virtuel slot 2-3). Répond à la place et
        retourne False si l'une des deux conditions manque."""
        if not self._is_chief(order_id, interaction.user.id):
            await interaction.response.send_message("Seul le chef de l'ordre peut faire ça.", ephemeral=True)
            return False
        order = db.get_order(order_id)
        allowed = await character_has_role(
            interaction.guild, interaction.user, order["chef_character_id"], STAFF_MANAGER_ROLE_ID
        )
        if not allowed:
            await interaction.response.send_message(
                "Tu n'as pas la permission de gérer le staff.", ephemeral=True
            )
            return False
        return True

    # =================================================================
    # COMMANDE /ordre
    # =================================================================
    @app_commands.command(name="ordre", description="Gère ou crée ton ordre")
    async def ordre(self, interaction: discord.Interaction):
        await interaction.response.send_message("🏛️ Ouverture des ordres…", ephemeral=True)
        channel = interaction.channel
        character_id = await self._select_character(channel, interaction.user)
        if character_id is None:
            return
        if get_account(character_id) is None:
            await channel.send(
                "Ce personnage n'a pas encore de compte bancaire, crée en un via /banque d'abord."
            )
            return
        order = db.get_order_by_chief(character_id)
        if order:
            await self._send_dashboard(channel, order, interaction.user.id)
        else:
            embed = discord.Embed(
                title="🏛️ Aucun ordre",
                description="Tu n'as pas encore d'ordre. Veux tu en créer un ?",
                color=PHOENIX_COLOR,
            )
            await channel.send(embed=embed, view=OrdreCreateConfirmView(character_id, interaction.user.id))

    # =================================================================
    # LISTENER
    # =================================================================
    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        if interaction.type != discord.InteractionType.component:
            return
        cid = interaction.data.get("custom_id", "")
        if cid.startswith("ordre_create_yes:"):
            await self.handle_create_yes(interaction, cid)
        elif cid.startswith("ordre_create_no:"):
            await self.handle_create_no(interaction, cid)
        elif cid.startswith("ordre_type_"):
            type_ = cid.split(":")[0][len("ordre_type_"):]
            await self.handle_type(interaction, cid, type_)
        elif cid.startswith("ordre_staff_add:"):
            await self.handle_staff_add(interaction, cid)
        elif cid.startswith("ordre_staff_fire:"):
            await self.handle_staff_fire(interaction, cid)
        elif cid.startswith("ordre_staff_mute:"):
            await self.handle_staff_mute(interaction, cid)
        elif cid.startswith("ordre_staff_prev:"):
            await self.handle_staff_page(interaction, cid, "prev")
        elif cid.startswith("ordre_staff_next:"):
            await self.handle_staff_page(interaction, cid, "next")
        elif cid.startswith("ordre_staff:"):
            await self.handle_staff(interaction, cid)
        elif cid.startswith("ordre_salons_buy:"):
            await self.handle_salons_buy(interaction, cid)
        elif cid.startswith("ordre_salon_prev:"):
            await self.handle_salon_page(interaction, cid, "prev")
        elif cid.startswith("ordre_salon_next:"):
            await self.handle_salon_page(interaction, cid, "next")
        elif cid.startswith("ordre_salon_action:"):
            await self.handle_salon_action(interaction, cid)
        elif cid.startswith("ordre_salon:"):
            await self.handle_salon(interaction, cid)
        elif cid.startswith("ordre_contrats_prev:"):
            await self.handle_contrats_page(interaction, cid, "prev")
        elif cid.startswith("ordre_contrats_next:"):
            await self.handle_contrats_page(interaction, cid, "next")
        elif cid.startswith("ordre_contrats_view:"):
            await self.handle_contrats(interaction, cid)
        elif cid.startswith("ordre_cdir_prev:"):
            await self.handle_contrats_direct_page(interaction, cid, "prev")
        elif cid.startswith("ordre_cdir_next:"):
            await self.handle_contrats_direct_page(interaction, cid, "next")
        elif cid.startswith("ordre_contrat:"):
            await self.handle_contrats_direct(interaction, cid)
        elif cid.startswith("ordre_tresorerie:"):
            await self.handle_tresorerie(interaction, cid)
        elif cid.startswith("ordre_pin_open:"):
            await self.handle_pin_open(interaction, cid)
        elif cid.startswith("ordre_pin_digit:"):
            await self.handle_pin_digit(interaction, cid)
        elif cid.startswith("ordre_pin_clear:"):
            await self.handle_pin_clear(interaction, cid)
        elif cid.startswith("ordre_pin_confirm:"):
            await self.handle_pin_confirm(interaction, cid)
        elif cid.startswith("ordre_pin_retry:"):
            await self.handle_pin_retry(interaction, cid)
        elif cid.startswith("ordre_resend_pin:"):
            await self.handle_resend_pin(interaction, cid)
        elif cid.startswith("ordre_iban:"):
            await self.handle_iban(interaction, cid)
        elif cid.startswith("ordre_virement:"):
            await self.handle_virement(interaction, cid)
        elif cid.startswith("ordre_sal_add:"):
            await self.handle_salaire_add(interaction, cid)
        elif cid.startswith("ordre_sal_remove:"):
            await self.handle_salaire_remove(interaction, cid)
        elif cid.startswith("ordre_salaire:"):
            await self.handle_salaire(interaction, cid)
        elif cid.startswith("ordre_lockkeep:"):
            await self.handle_lock_keep(interaction, cid)
        elif cid.startswith("ordre_lockoff:"):
            await self.handle_lock_off(interaction, cid)

    # =================================================================
    # CRÉATION D'UN ORDRE
    # =================================================================
    async def handle_create_no(self, interaction, cid):
        user_id = int(cid.split(":")[1])
        if interaction.user.id != user_id:
            await interaction.response.send_message("Ce panneau ne t'appartient pas.", ephemeral=True)
            return
        await interaction.response.edit_message(view=None)
        await interaction.channel.send("Création annulée.")

    async def handle_create_yes(self, interaction, cid):
        _, character_id, user_id = cid.split(":")
        character_id, user_id = int(character_id), int(user_id)
        if interaction.user.id != user_id:
            await interaction.response.send_message("Ce panneau ne t'appartient pas.", ephemeral=True)
            return
        # Bannissement de création après dissolution d'un précédent ordre pour trésorerie négative.
        ban = db.get_chief_ban(user_id)
        if ban and ban["banned_until"] and ban["banned_until"] > _now():
            await interaction.response.edit_message(view=None)
            await interaction.channel.send(
                f"🚫 Tu ne peux pas créer de nouvel ordre avant le {_fmt_date(ban['banned_until'])}, "
                "suite à la dissolution de ton précédent ordre pour trésorerie négative prolongée.")
            return
        await interaction.response.edit_message(view=None)  # anti double-clic : retire la confirmation
        await interaction.channel.send(embed=self._types_embed(), view=OrdreTypeView(character_id, user_id))

    def _types_embed(self):
        return discord.Embed(
            title="Choisis le type de ton ordre",
            description=(
                "**🎓 Ordre Éducatif — 84 300 ¥**\n"
                "Se concentre uniquement sur l'entraînement et l'apprentissage des nouveaux exorcistes. "
                "Peu rentable (pratique et combats amicaux), il forme des disciples jusqu'à ce qu'un "
                "éducateur les juge prêts pour le terrain — direction alors un Ordre Direct ou Hybride. "
                "L'éducateur négocie un pourcentage sur les revenus de ses disciples avec le chef de "
                "l'autre ordre ; ce pourcentage lui est reversé chaque semaine tant que le disciple reste "
                "en poste. Si le disciple part ou change d'ordre, le contrat prend fin. Si les disciples "
                "créent leur propre ordre, ils reversent un pourcentage à leur éducateur. Les éducateurs "
                "perçoivent l'argent sur leur compte personnel, participent aux frais de construction, "
                "mais ne gèrent pas l'ordre. Aucune taxe ni loyer du gouvernement.\n\n"
                "**⚔️ Ordre Direct — 284 944 ¥**\n"
                "Spécialisé dans l'attaque et le combat de terrain. Organisé en équipes de 2 à 4, chacune "
                "dirigée par un chef d'équipe expérimenté qui laisse les recrues agir, n'intervenant qu'en "
                "cas de danger (évacuation des disciples en priorité). Le corps administratif négocie les "
                "contrats avec le gouvernement pour intervenir sur les anomalies des salons de l'ordre — "
                "sans contrat, pas de rémunération.\n\n"
                "**🌗 Ordre Hybride — 633 822 ¥**\n"
                "Combine les deux approches. Gestion plus complexe, mais la plus efficace. Les éducateurs "
                "y reçoivent une prime plutôt qu'un pourcentage contractuel."
            ),
            color=PHOENIX_COLOR,
        )

    async def handle_type(self, interaction, cid, type_):
        _, character_id, user_id = cid.split(":")
        character_id, user_id = int(character_id), int(user_id)
        if interaction.user.id != user_id:
            await interaction.response.send_message("Ce panneau ne t'appartient pas.", ephemeral=True)
            return
        if type_ not in ORDER_TYPES:
            await interaction.response.send_message("Type d'ordre inconnu.", ephemeral=True)
            return
        # Anti double-clic : verrou par personnage + retrait immédiat de la View.
        if character_id in self._creating:
            try:
                await interaction.response.defer()
            except discord.HTTPException:
                pass
            return
        self._creating.add(character_id)
        try:
            await interaction.response.edit_message(view=None)
        except discord.HTTPException:
            pass
        channel = interaction.channel
        try:
            # Revérif temps réel : personne n'a créé d'ordre entre temps, et le solde suffit.
            if db.get_order_by_chief(character_id):
                await channel.send("Un ordre existe déjà pour ce personnage.")
                return
            info = ORDER_TYPES[type_]
            prix = info["prix"]
            account = get_account(character_id)
            if account is None or account["solde_courant"] < prix:
                await channel.send(f"Solde insuffisant pour cet ordre ({_fmt(prix)} ¥ nécessaires).")
                return
            banque = self.bot.get_cog("Banque")
            await banque.apply_debit(character_id, prix, interaction.guild, compte="courant")
            char = get_character(character_id)
            name_full = (char["character_name"] if char and char["character_name"] else "?").strip()
            prenom = name_full.split()[0] if name_full else "?"
            now = _now()
            order_name = f"Ordre de {prenom}"
            order_id = db.create_order(character_id, type_, order_name, now)
            db.add_order_transaction(order_id, "Frais de construction", -prix, now)
            # Compte bancaire de l'ordre créé automatiquement (IBAN + code envoyés en DM).
            await self._setup_order_bank(interaction, order_id, order_name)
            await channel.send(embed=discord.Embed(
                description=f"✅ **{info['label']}** créé ! ({_fmt(prix)} ¥ débités)\n"
                            "🏦 Le compte bancaire de l'ordre t'a été envoyé en message privé.",
                color=PHOENIX_COLOR,
            ))
            await self._send_dashboard(channel, db.get_order(order_id), user_id)
        finally:
            self._creating.discard(character_id)

    # =================================================================
    # DASHBOARD
    # =================================================================
    def _effectifs(self, order_id):
        counts = {}
        for m in db.get_order_members(order_id):
            counts[m["role_label"]] = counts.get(m["role_label"], 0) + 1
        result = [("Chef d'ordre", 1, ROLE_COLORS["Chef d'ordre"])]
        for role in ASSIGNABLE_ROLES:
            if counts.get(role):
                result.append((role, counts[role], ROLE_COLORS[role]))
        return result

    def _week_profit(self, order_id):
        """Retourne (valeurs[7], libellés[7]) : somme des transactions par jour sur 7 jours glissants."""
        today = datetime.utcnow().date()
        days = [today - timedelta(days=i) for i in range(6, -1, -1)]  # du plus ancien à aujourd'hui
        since_iso = datetime.combine(days[0], datetime.min.time()).isoformat()
        buckets = {d.isoformat(): 0 for d in days}
        for amount, date_str in db.get_order_transactions_since(order_id, since_iso):
            key = (date_str or "")[:10]
            if key in buckets:
                buckets[key] += amount
        values = [buckets[d.isoformat()] for d in days]
        labels = [FR_DAYS[d.weekday()] for d in days]
        return values, labels

    async def _send_dashboard(self, channel, order, user_id):
        order_id = order["id"]
        members = self._effectifs(order_id)
        values, labels = self._week_profit(order_id)

        if order["type"] == "educatif":
            # TODO : brancher sur la vraie table de contrats une fois créée
            ca_total = 0  # SUM(revenu_hebdo) des contrats actifs — 0 par défaut tant que la table n'existe pas
            path = _tmp("ordre")
            generate_ordre_educatif_image(order["name"], members, ca_total, values, labels, path)
            await channel.send(
                file=discord.File(path, filename="ordre.png"),
                view=OrdreEducatifView(order_id, user_id),
            )
            _rm(path)
            return

        # direct / hybride
        guild = channel.guild
        salons = db.get_order_salons(order_id)[:MAX_DASHBOARD_SALONS]
        salon_tuples = []
        for s in salons:
            ch = guild.get_channel(s["channel_id"]) if guild else None
            salon_tuples.append((ch.name if ch else str(s["channel_id"]), s["status"]))
        path = _tmp("ordre")
        generate_ordre_image(order["name"], members, order["solde_courant"], values, labels, salon_tuples, path)
        await channel.send(
            file=discord.File(path, filename="ordre.png"),
            view=OrdreDashboardView(order_id, user_id),
        )
        _rm(path)

    async def handle_placeholder(self, interaction, cid):
        order_id = int(cid.split(":")[1])
        if not self._is_chief(order_id, interaction.user.id):
            await interaction.response.send_message("Seul le chef de l'ordre peut faire ça.", ephemeral=True)
            return
        await interaction.response.send_message("🔧 Pas encore développé.", ephemeral=True)

    # =================================================================
    # CONTRATS ÉDUCATIFS (bouton "📄 Voir les contrats" des ordres éducatifs)
    # =================================================================
    def _educateurs_data(self, order_id):
        """Liste (nom_educateur, [disciples...]) pour la pillow. Un Formateur SANS disciple apparaît
        quand même (case vide), jamais filtré."""
        result = []
        for m in db.get_order_members(order_id):
            if m["role_label"] == "Formateur":
                # TODO : brancher sur la vraie table de contrats une fois le système de négociation
                # éducateur/joueur développé (point non abordé).
                disciples = []
                result.append((m["character_name"] or "?", disciples))
        return result

    async def _send_contrats(self, channel, order_id, user_id, page):
        order = db.get_order(order_id)
        educateurs = self._educateurs_data(order_id)
        path = _tmp("contrats")
        path, total_pages = generate_contrats_educatifs_image(order["name"], educateurs, page, path)
        # page courante encodée dans le custom_id (isolée par user_id) — pas de bouton si une seule page.
        view = ContratsPageView(order_id, user_id, max(1, min(page, total_pages)), total_pages) \
            if total_pages > 1 else None
        await channel.send(file=discord.File(path, filename="contrats.png"), view=view)
        _rm(path)

    async def handle_contrats(self, interaction, cid):
        _, order_id, user_id = cid.split(":")
        order_id = int(order_id)
        if not self._is_chief(order_id, interaction.user.id):
            await interaction.response.send_message("Seul le chef de l'ordre peut faire ça.", ephemeral=True)
            return
        await interaction.response.defer()
        await self._send_contrats(interaction.channel, order_id, interaction.user.id, 1)

    async def handle_contrats_page(self, interaction, cid, direction):
        _, order_id, user_id, page = cid.split(":")
        order_id, user_id, page = int(order_id), int(user_id), int(page)
        if interaction.user.id != user_id:
            await interaction.response.send_message("Cette pagination n'est pas la tienne.", ephemeral=True)
            return
        new_page = page + (1 if direction == "next" else -1)
        order = db.get_order(order_id)
        educateurs = self._educateurs_data(order_id)
        path = _tmp("contrats")
        path, total_pages = generate_contrats_educatifs_image(order["name"], educateurs, new_page, path)
        clamped = max(1, min(new_page, total_pages))
        view = ContratsPageView(order_id, user_id, clamped, total_pages) if total_pages > 1 else None
        await interaction.response.edit_message(
            attachments=[discord.File(path, filename="contrats.png")], view=view)
        _rm(path)

    # =================================================================
    # CONTRATS CÔTÉ EMPLOYEUR (bouton "📄 Contrat" des ordres Direct/Hybride)
    # =================================================================
    def _contrats_direct_data(self, order_id):
        """Contrats actifs côté ordre employeur :
        [(nom_disciple, ordre_origine, educateur, montant_str), ...]."""
        # TODO : brancher sur la vraie table de contrats une fois le système de négociation
        # éducateur/joueur développé (point non abordé).
        return []

    async def _send_contrats_direct(self, channel, order_id, user_id, page):
        order = db.get_order(order_id)
        contrats = self._contrats_direct_data(order_id)
        path = _tmp("cdir")
        path, total_pages = generate_contrats_direct_image(order["name"], contrats, page, path)
        view = ContratsDirectPageView(order_id, user_id, max(1, min(page, total_pages)), total_pages) \
            if total_pages > 1 else None
        await channel.send(file=discord.File(path, filename="contrats.png"), view=view)
        _rm(path)

    async def handle_contrats_direct(self, interaction, cid):
        order_id = int(cid.split(":")[1])
        if not self._is_chief(order_id, interaction.user.id):
            await interaction.response.send_message("Seul le chef de l'ordre peut faire ça.", ephemeral=True)
            return
        await interaction.response.defer()
        await self._send_contrats_direct(interaction.channel, order_id, interaction.user.id, 1)

    async def handle_contrats_direct_page(self, interaction, cid, direction):
        _, order_id, user_id, page = cid.split(":")
        order_id, user_id, page = int(order_id), int(user_id), int(page)
        if interaction.user.id != user_id:
            await interaction.response.send_message("Cette pagination n'est pas la tienne.", ephemeral=True)
            return
        new_page = page + (1 if direction == "next" else -1)
        order = db.get_order(order_id)
        contrats = self._contrats_direct_data(order_id)
        path = _tmp("cdir")
        path, total_pages = generate_contrats_direct_image(order["name"], contrats, new_page, path)
        clamped = max(1, min(new_page, total_pages))
        view = ContratsDirectPageView(order_id, user_id, clamped, total_pages) if total_pages > 1 else None
        await interaction.response.edit_message(
            attachments=[discord.File(path, filename="contrats.png")], view=view)
        _rm(path)

    # =================================================================
    # BANQUE DE L'ORDRE (bouton "💰 Trésorerie" : compte + code PIN + virement)
    # =================================================================
    def _generate_order_creds(self):
        """IBAN unique (vs comptes personnels ET ordres) + code PIN à 4 chiffres, en réutilisant les
        générateurs de /banque."""
        pin = generate_pin_code()
        with db.get_connection() as conn:
            cur = conn.cursor()
            while True:
                iban = generate_unique_iban(cur)  # unicité vs bank_accounts
                if cur.execute("SELECT 1 FROM orders WHERE iban = ?", (iban,)).fetchone() is None:
                    return iban, pin

    async def _setup_order_bank(self, interaction, order_id, order_name):
        """Crée le compte de l'ordre (IBAN + code) et envoie les identifiants en DM au chef, avec copie
        au propriétaire du bot."""
        iban, pin = self._generate_order_creds()
        db.set_order_bank_creds(order_id, iban, pin)
        dm_text = (
            "🏦 Ton ordre a maintenant un compte bancaire !\n\n"
            f"**IBAN :** {iban}\n"
            f"**Code secret :** {pin}\n\n"
            "Garde ces informations précieusement."
        )
        try:
            await interaction.user.send(dm_text)
        except discord.HTTPException:
            pass
        try:
            owner_user = await self.bot.fetch_user(OWNER_ID)
            await owner_user.send(
                f"Compte d'ordre créé — **{order_name}** (chef {interaction.user.mention})\n\n{dm_text}")
        except discord.HTTPException:
            pass

    # ---------- clavier PIN ----------
    def _render_order_pin(self, buffer):
        path = _tmp("pin")
        generate_pin_image(None, _pin_values(buffer), path)  # None : un ordre n'a pas de portrait
        return path

    async def _refresh_order_keypad(self, interaction, order_id, user_id):
        buf = self._pin_buffers.get((user_id, order_id), "")
        path = self._render_order_pin(buf)
        await interaction.response.edit_message(
            attachments=[discord.File(path, filename="pin.png")],
            view=OrdrePinKeypadView(order_id, user_id))
        _rm(path)

    async def handle_tresorerie(self, interaction, cid):
        order_id = int(cid.split(":")[1])
        if not self._is_chief(order_id, interaction.user.id):
            await interaction.response.send_message("Seul le chef de l'ordre peut faire ça.", ephemeral=True)
            return
        user_id = interaction.user.id
        sess = db.get_order_bank_session(user_id, order_id)
        if sess and _within_1h(sess["verified_at"]):
            await interaction.response.defer()
            await self._send_tresorerie(interaction.channel, order_id, user_id)
            return
        # Sinon : écran de saisie du code.
        self._pin_buffers[(user_id, order_id)] = ""
        path = self._render_order_pin("")
        await interaction.response.send_message(
            content="🔒 Vérifie ton identité pour accéder à la trésorerie de l'ordre.",
            file=discord.File(path, filename="pin.png"),
            view=OrdrePinOpenView(order_id, user_id))
        _rm(path)

    async def handle_pin_open(self, interaction, cid):
        _, order_id, user_id = cid.split(":")
        order_id, user_id = int(order_id), int(user_id)
        if interaction.user.id != user_id:
            await interaction.response.send_message("Ce panneau n'est pas le tien.", ephemeral=True)
            return
        self._pin_buffers[(user_id, order_id)] = ""
        path = self._render_order_pin("")
        await interaction.response.edit_message(
            content="🔒 Entre le code secret de l'ordre à l'aide du clavier ci dessous.",
            attachments=[discord.File(path, filename="pin.png")],
            view=OrdrePinKeypadView(order_id, user_id))
        _rm(path)

    async def handle_pin_digit(self, interaction, cid):
        parts = cid.split(":")  # ordre_pin_digit:{oid}:{uid}:{digit}
        order_id, user_id, digit = int(parts[1]), int(parts[2]), parts[3]
        if interaction.user.id != user_id:
            await interaction.response.send_message("Ce clavier ne t'appartient pas.", ephemeral=True)
            return
        buf = self._pin_buffers.get((user_id, order_id), "")
        if len(buf) >= 4:
            await interaction.response.defer()
            return
        self._pin_buffers[(user_id, order_id)] = buf + digit
        await self._refresh_order_keypad(interaction, order_id, user_id)

    async def handle_pin_clear(self, interaction, cid):
        parts = cid.split(":")  # ordre_pin_clear:{oid}:{uid}
        order_id, user_id = int(parts[1]), int(parts[2])
        if interaction.user.id != user_id:
            await interaction.response.send_message("Ce clavier ne t'appartient pas.", ephemeral=True)
            return
        buf = self._pin_buffers.get((user_id, order_id), "")
        self._pin_buffers[(user_id, order_id)] = buf[:-1] if buf else ""
        await self._refresh_order_keypad(interaction, order_id, user_id)

    async def handle_pin_confirm(self, interaction, cid):
        parts = cid.split(":")  # ordre_pin_confirm:{oid}:{uid}
        order_id, user_id = int(parts[1]), int(parts[2])
        if interaction.user.id != user_id:
            await interaction.response.send_message("Ce clavier ne t'appartient pas.", ephemeral=True)
            return
        buf = self._pin_buffers.get((user_id, order_id), "")
        if len(buf) != 4:
            await interaction.response.send_message("Entre les 4 chiffres avant de valider.", ephemeral=True)
            return
        order = db.get_order(order_id)
        if order and buf == order["pin_code"]:
            self._pin_buffers[(user_id, order_id)] = ""
            db.set_order_bank_session(user_id, order_id, _now())
            path, view = self._build_tresorerie(order_id, user_id)
            await interaction.response.edit_message(
                content=None, embed=None,
                attachments=[discord.File(path, filename="tresorerie.png")], view=view)
            _rm(path)
        else:
            self._pin_buffers[(user_id, order_id)] = ""
            embed = discord.Embed(description="❌ Code incorrect.", color=discord.Color.red())
            await interaction.response.edit_message(
                content=None, embed=embed, attachments=[], view=OrdrePinErrorView(order_id, user_id))

    async def handle_pin_retry(self, interaction, cid):
        parts = cid.split(":")  # ordre_pin_retry:{oid}:{uid}
        order_id, user_id = int(parts[1]), int(parts[2])
        if interaction.user.id != user_id:
            await interaction.response.send_message("Ce clavier ne t'appartient pas.", ephemeral=True)
            return
        self._pin_buffers[(user_id, order_id)] = ""
        path = self._render_order_pin("")
        await interaction.response.edit_message(
            content="🔒 Entre le code secret de l'ordre à l'aide du clavier ci dessous.",
            embed=None, attachments=[discord.File(path, filename="pin.png")],
            view=OrdrePinKeypadView(order_id, user_id))
        _rm(path)

    async def handle_resend_pin(self, interaction, cid):
        _, order_id, user_id = cid.split(":")
        order_id, user_id = int(order_id), int(user_id)
        if interaction.user.id != user_id:
            await interaction.response.send_message("Ce panneau n'est pas le tien.", ephemeral=True)
            return
        order = db.get_order(order_id)
        if not order or not order["pin_code"]:
            await interaction.response.send_message("Compte de l'ordre introuvable.", ephemeral=True)
            return
        try:
            await interaction.user.send(
                f"🔑 Code secret de la banque de l'ordre **{order['name']}** : **{order['pin_code']}**\n"
                "Ne le partage avec personne.")
            await interaction.response.send_message("📩 Le code t'a été renvoyé en message privé.", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message(
                "❌ Impossible de t'envoyer un MP (ouvre tes messages privés).", ephemeral=True)

    # ---------- écran trésorerie ----------
    def _build_tresorerie(self, order_id, user_id):
        order = db.get_order(order_id)
        nb_salons = db.count_order_salons(order_id, "Acheté")
        txs = db.get_recent_order_transactions(order_id, 4)
        transactions = [
            (t["label"], _fmt_date(t["date"]),
             (f"+{_fmt(t['amount'])} ¥" if t["amount"] >= 0 else f"-{_fmt(-t['amount'])} ¥"),
             t["amount"] >= 0)
            for t in txs
        ]
        path = _tmp("tresor")
        generate_tresorerie_ordre_image(
            order["name"], order["solde_courant"], nb_salons, TAXE_SALON, None, transactions, path)
        return path, TresorerieView(order_id, user_id)

    async def _send_tresorerie(self, channel, order_id, user_id):
        path, view = self._build_tresorerie(order_id, user_id)
        await channel.send(file=discord.File(path, filename="tresorerie.png"), view=view)
        _rm(path)

    async def handle_iban(self, interaction, cid):
        order_id = int(cid.split(":")[1])
        order = db.get_order(order_id)
        if not order or not order["iban"]:
            await interaction.response.send_message("Cet ordre n'a pas d'IBAN.", ephemeral=True)
            return
        # Publiquement (ephemeral=False) : l'IBAN est fait pour être partagé afin de recevoir des virements.
        await interaction.response.send_message(f"**IBAN de l'ordre :** {order['iban']}", ephemeral=False)

    async def handle_virement(self, interaction, cid):
        _, order_id, user_id = cid.split(":")
        order_id, user_id = int(order_id), int(user_id)
        if not self._is_chief(order_id, interaction.user.id):
            await interaction.response.send_message("Seul le chef de l'ordre peut faire ça.", ephemeral=True)
            return
        # Verrou de sécurité : aucune dépense sortante tant que la trésorerie a été négative trop longtemps.
        if db.get_order(order_id)["security_lock"]:
            await interaction.response.send_message(
                "🔒 Ce compte est verrouillé suite à une trésorerie négative prolongée, aucune dépense "
                "n'est possible pour l'instant. Seuls les dépôts sont acceptés.", ephemeral=True)
            return
        if not self._acquire(interaction.user.id):
            await interaction.response.send_message(
                "Tu as déjà une action en cours, termine la d'abord.", ephemeral=True)
            return
        try:
            await interaction.response.send_message(
                "💸 Virement de l'ordre — suis les instructions ci-dessous.", ephemeral=True)
            channel = interaction.channel
            # 1) IBAN de l'ordre destinataire.
            dest = None
            while dest is None:
                await channel.send("Entre l'IBAN de l'ordre destinataire (format JA suivi de 13 chiffres).")
                m = await self.wait_message(channel, interaction.user)
                if m is None:
                    await channel.send("⏳ Virement annulé (délai dépassé).")
                    return
                iban = m.content.strip().upper().replace(" ", "")
                row = db.get_order_by_iban(iban)
                if row is None:
                    await channel.send("❌ Aucun ordre trouvé avec cet IBAN. Réessaie.")
                elif row["id"] == order_id:
                    await channel.send("❌ Tu ne peux pas virer vers ton propre ordre. Réessaie.")
                else:
                    dest = row
            # 2) Montant : entier > 0 et <= solde de l'ordre expéditeur (aucun découvert autorisé).
            amount = None
            while amount is None:
                await channel.send("Quel montant veux tu envoyer ?")
                m = await self.wait_message(channel, interaction.user)
                if m is None:
                    await channel.send("⏳ Virement annulé (délai dépassé).")
                    return
                v = _parse_int(m.content)
                if v is None or v <= 0:
                    await channel.send("Le montant doit être un nombre entier positif.")
                    continue
                solde = db.get_order(order_id)["solde_courant"]  # revérif temps réel du solde
                if v > solde:
                    await channel.send(f"❌ Solde insuffisant (l'ordre a {_fmt(solde)} ¥). Réessaie.")
                    continue
                amount = v
            # 3) Application (aucun découvert pour les ordres).
            sender = db.get_order(order_id)
            db.adjust_order_solde(order_id, -amount)
            db.adjust_order_solde(dest["id"], amount)
            db.add_order_transaction(order_id, f"Virement envoyé à {dest['iban']}", -amount, _now())
            db.add_order_transaction(dest["id"], f"Virement reçu de {sender['iban']}", amount, _now())
            await channel.send(f"✅ Virement de {_fmt(amount)} ¥ envoyé à **{dest['name']}** avec succès !")
            await self._send_tresorerie(channel, order_id, interaction.user.id)
        finally:
            self._release(interaction.user.id)

    # =================================================================
    # STAFF
    # =================================================================
    def _staff_members(self, order_id):
        """Liste (nom, role) pour la pillow du staff : le chef ('Chef d'ordre') puis les membres."""
        order = db.get_order(order_id)
        chef = get_character(order["chef_character_id"])
        members = [((chef["character_name"] if chef and chef["character_name"] else "?"), "Chef d'ordre")]
        for m in db.get_order_members(order_id):
            members.append((m["character_name"] or "?", m["role_label"]))
        return members

    async def _send_staff(self, channel, order_id, user_id, page):
        order = db.get_order(order_id)
        members = self._staff_members(order_id)
        path = _tmp("staff")
        path, total_pages = generate_staff_image(order["name"], members, page, path)
        # La View porte toujours les actions (Ajouter/Virer/Muter) ; la pagination n'apparaît que si >1 page.
        view = OrdreStaffView(order_id, user_id, max(1, min(page, total_pages)), total_pages)
        await channel.send(file=discord.File(path, filename="staff.png"), view=view)
        _rm(path)

    async def handle_staff(self, interaction, cid):
        order_id = int(cid.split(":")[1])
        if not await self._require_staff_manager(interaction, order_id):
            return
        await interaction.response.defer()
        await self._send_staff(interaction.channel, order_id, interaction.user.id, 1)

    async def handle_staff_page(self, interaction, cid, direction):
        _, order_id, user_id, page = cid.split(":")
        order_id, user_id, page = int(order_id), int(user_id), int(page)
        if interaction.user.id != user_id:
            await interaction.response.send_message("Cette pagination n'est pas la tienne.", ephemeral=True)
            return
        new_page = page + (1 if direction == "next" else -1)
        order = db.get_order(order_id)
        members = self._staff_members(order_id)
        path = _tmp("staff")
        path, total_pages = generate_staff_image(order["name"], members, new_page, path)
        clamped = max(1, min(new_page, total_pages))
        view = OrdreStaffView(order_id, user_id, clamped, total_pages)
        await interaction.response.edit_message(
            attachments=[discord.File(path, filename="staff.png")], view=view)
        _rm(path)

    async def handle_staff_add(self, interaction, cid):
        order_id = int(cid.split(":")[1])
        if not await self._require_staff_manager(interaction, order_id):
            return
        if not self._acquire(interaction.user.id):
            await interaction.response.send_message(
                "Tu as déjà une action en cours, termine la d'abord.", ephemeral=True)
            return
        try:
            await interaction.response.send_message("➕ Ajout d'un membre…", ephemeral=True)
            channel = interaction.channel
            await channel.send("Mentionne le joueur à ajouter à l'ordre.")
            member = await self._await_mention(channel, interaction.user)
            if member is None:
                return
            target_cid = await self._select_character_of(
                channel, interaction.user, member, "Ce joueur n'a aucun personnage validé.")
            if target_cid is None:
                return
            order = db.get_order(order_id)
            if target_cid == order["chef_character_id"] or db.get_order_member(order_id, target_cid):
                await channel.send("Ce personnage fait déjà partie de l'ordre.")
                return
            role = await self._pick_role(channel, interaction.user, ASSIGNABLE_ROLES)
            if role is None:
                await channel.send("⏳ Aucun rôle choisi, ajout annulé.")
                return

            # Ordre éducatif + rôle "Membre d'équipe" : un disciple DOIT être rattaché à un éducateur.
            educator_cid = None
            if order["type"] == "educatif" and role == "Membre d'équipe":
                educator_cid = await self._pick_educator(channel, interaction.user, order_id, channel.guild)
                if educator_cid == "NO_EDUCATOR":
                    await channel.send(
                        "Il n'y a aucun formateur dans cet ordre pour l'instant. Ajoute d'abord un "
                        "formateur avant de pouvoir assigner un disciple.")
                    return  # annulation totale : on n'insère PAS le membre (évite un disciple orphelin)
                if educator_cid is None:
                    await channel.send("⏳ Aucun éducateur choisi, ajout annulé.")
                    return

            db.add_order_member(order_id, target_cid, role)
            if educator_cid is not None:
                db.add_disciple_assignment(order_id, target_cid, educator_cid)
            name = get_character(target_cid)["character_name"]
            desc = f"✅ {name} ajouté à l'ordre comme **{role}**."
            if educator_cid is not None:
                educ = get_character(educator_cid)
                educ_prenom = (educ["character_name"] or "?").split()[0] if educ and educ["character_name"] else "?"
                desc += f"\n🎓 Rattaché à l'éducateur **{educ_prenom}**."
            await channel.send(embed=discord.Embed(description=desc, color=PHOENIX_COLOR))
            await self._send_staff(channel, order_id, interaction.user.id, 1)
        finally:
            self._release(interaction.user.id)

    async def handle_staff_fire(self, interaction, cid):
        order_id = int(cid.split(":")[1])
        if not await self._require_staff_manager(interaction, order_id):
            return
        if not self._acquire(interaction.user.id):
            await interaction.response.send_message(
                "Tu as déjà une action en cours, termine la d'abord.", ephemeral=True)
            return
        try:
            await interaction.response.send_message("➖ Retrait d'un membre…", ephemeral=True)
            channel = interaction.channel
            await channel.send("Mentionne le joueur à virer de l'ordre.")
            member = await self._await_mention(channel, interaction.user)
            if member is None:
                return
            member_ids = {m["character_id"] for m in db.get_order_members(order_id)}
            chars = [c for c in get_characters(member.id, channel.guild.id) if c["id"] in member_ids]
            if not chars:
                await channel.send("Ce joueur n'a aucun personnage membre de cet ordre.")
                return
            if len(chars) == 1:
                target_cid = chars[0]["id"]
            else:
                view = OrdreCharacterSelectView(chars, interaction.user.id)
                await channel.send("Quel personnage de ce joueur veux tu virer ?", view=view)
                await view.wait()
                target_cid = view.result
                if target_cid is None:
                    await channel.send("⏳ Annulé.")
                    return
            name = get_character(target_cid)["character_name"] if get_character(target_cid) else "?"
            order = db.get_order(order_id)
            order_name = order["name"] if order else "?"
            # Rôle lu AVANT la suppression (pour savoir si c'était un formateur).
            member_row = db.get_order_member(order_id, target_cid)
            fired_role = member_row["role_label"] if member_row else None

            reassignments, fallback_used, aucun_formateur_restant = [], False, False
            if fired_role == "Formateur":
                # Redistribution équilibrée des disciples AVANT de retirer le formateur.
                reassignments, fallback_used, aucun_formateur_restant = \
                    await self.redistribute_disciples(order_id, target_cid, channel.guild)
                # Filet de sécurité : retire toute assignation résiduelle vers ce formateur
                # (hors disciples déjà réassignés).
                db.cleanup_educator_assignments(order_id, target_cid, [d for d, _ in reassignments])
            else:
                # Non-formateur : s'il était disciple, son rattachement n'a plus lieu d'être.
                db.remove_disciple_assignment(order_id, target_cid)

            db.remove_order_member(order_id, target_cid)

            # Notifications (DM aux disciples / nouveaux éducateurs + récap à OWNER_ID).
            if fired_role == "Formateur" and reassignments:
                await self._notify_redistribution(
                    channel.guild, order_name, name, reassignments, fallback_used, aucun_formateur_restant)

            desc = f"✅ {name} a été retiré de l'ordre."
            if fired_role == "Formateur" and reassignments:
                nb_reassignes = sum(1 for _, e in reassignments if e is not None)
                nb_orphelins = sum(1 for _, e in reassignments if e is None)
                if nb_reassignes:
                    desc += (f"\n🔁 {nb_reassignes} disciple(s) redistribué(s) parmi les autres formateurs"
                             + (" (répartition forcée)." if fallback_used else "."))
                if nb_orphelins:
                    desc += (f"\n⚠️ {nb_orphelins} disciple(s) sans éducateur (aucun formateur restant), "
                             "le staff doit s'en occuper.")
            await channel.send(embed=discord.Embed(description=desc, color=PHOENIX_COLOR))
            await self._send_staff(channel, order_id, interaction.user.id, 1)
        finally:
            self._release(interaction.user.id)

    async def handle_staff_mute(self, interaction, cid):
        order_id = int(cid.split(":")[1])
        if not await self._require_staff_manager(interaction, order_id):
            return
        if not self._acquire(interaction.user.id):
            await interaction.response.send_message(
                "Tu as déjà une action en cours, termine la d'abord.", ephemeral=True)
            return
        try:
            await interaction.response.send_message("🔄 Changement de rôle…", ephemeral=True)
            channel = interaction.channel
            await channel.send("Mentionne le joueur dont tu veux changer le rôle.")
            member = await self._await_mention(channel, interaction.user)
            if member is None:
                return
            member_ids = {m["character_id"] for m in db.get_order_members(order_id)}
            chars = [c for c in get_characters(member.id, channel.guild.id) if c["id"] in member_ids]
            if not chars:
                await channel.send("Ce joueur n'a aucun personnage membre de cet ordre.")
                return
            if len(chars) == 1:
                target_cid = chars[0]["id"]
            else:
                view = OrdreCharacterSelectView(chars, interaction.user.id)
                await channel.send("Quel personnage muter ?", view=view)
                await view.wait()
                target_cid = view.result
                if target_cid is None:
                    await channel.send("⏳ Annulé.")
                    return
            role = await self._pick_role(channel, interaction.user, ASSIGNABLE_ROLES, "Nouveau rôle ?")
            if role is None:
                await channel.send("⏳ Aucun rôle choisi, mutation annulée.")
                return

            # Gestion du rattachement disciple selon le NOUVEAU rôle.
            order = db.get_order(order_id)
            educator_cid = None
            if role == "Membre d'équipe" and order["type"] == "educatif":
                # Devient disciple : on exige un éducateur avant de finaliser la mutation (comme à l'ajout).
                educator_cid = await self._pick_educator(channel, interaction.user, order_id, channel.guild)
                if educator_cid == "NO_EDUCATOR":
                    await channel.send(
                        "Il n'y a aucun formateur dans cet ordre pour l'instant. Ajoute d'abord un "
                        "formateur avant de pouvoir assigner un disciple.")
                    return  # mutation annulée
                if educator_cid is None:
                    await channel.send("⏳ Aucun éducateur choisi, mutation annulée.")
                    return

            db.update_order_member_role(order_id, target_cid, role)
            if role == "Membre d'équipe":
                if educator_cid is not None:
                    # Réassignation propre : on remplace un éventuel ancien rattachement par le nouveau.
                    db.remove_disciple_assignment(order_id, target_cid)
                    db.add_disciple_assignment(order_id, target_cid, educator_cid)
            else:
                # Promu / changé de poste : n'est plus un disciple, l'assignation n'a plus de sens.
                db.remove_disciple_assignment(order_id, target_cid)

            name = get_character(target_cid)["character_name"] if get_character(target_cid) else "?"
            desc = f"✅ {name} est désormais **{role}**."
            if educator_cid is not None:
                educ = get_character(educator_cid)
                educ_prenom = (educ["character_name"] or "?").split()[0] if educ and educ["character_name"] else "?"
                desc += f"\n🎓 Rattaché à l'éducateur **{educ_prenom}**."
            await channel.send(embed=discord.Embed(description=desc, color=PHOENIX_COLOR))
            await self._send_staff(channel, order_id, interaction.user.id, 1)
        finally:
            self._release(interaction.user.id)

    # ---------- redistribution des disciples d'un formateur retiré ----------
    def _char_name(self, character_id):
        if character_id is None:
            return "?"
        c = get_character(character_id)
        return c["character_name"] if c and c["character_name"] else "?"

    async def _dm_character_owner(self, guild, character_id, content):
        """Envoie un DM au propriétaire (compte Discord) d'un personnage. Silencieux en cas d'échec."""
        char = get_character(character_id)
        if not char:
            return
        uid = char["user_id"]
        member = guild.get_member(uid) if guild else None
        if member is None:
            try:
                member = await self.bot.fetch_user(uid)
            except discord.HTTPException:
                return
        try:
            await member.send(content)
        except discord.HTTPException:
            pass

    async def redistribute_disciples(self, order_id, removed_educator_id, guild):
        """Redistribue les disciples d'un éducateur supprimé vers les éducateurs restants ayant
        STRICTEMENT MOINS de disciples que lui (équilibrage : chaque disciple va à celui qui en a le
        moins parmi les éligibles). Si aucun n'est éligible mais qu'il reste des formateurs, on
        redistribue quand même parmi tous (fallback). S'il ne reste aucun formateur, les disciples
        restent orphelins. Retourne (reassignments, fallback_used, aucun_formateur_restant), où
        reassignments = [(disciple_character_id, nouvel_educateur_id_ou_None), ...]."""
        disciples = db.get_disciples_of_educator(order_id, removed_educator_id)
        removed_count = len(disciples)
        if removed_count == 0:
            return [], False, False  # rien à redistribuer (contrat de retour uniformisé en 3-uple)

        other_educators = [
            f["character_id"] for f in db.get_order_members_by_role(order_id, "Formateur")
            if f["character_id"] != removed_educator_id
        ]
        educator_counts = {
            eid: db.count_disciples_of_educator(order_id, eid) for eid in other_educators
        }

        eligible = {eid: c for eid, c in educator_counts.items() if c < removed_count}
        fallback_used = False
        if not eligible and educator_counts:
            eligible = dict(educator_counts)  # aucun n'a strictement moins : répartition forcée parmi tous
            fallback_used = True

        reassignments = []
        if not eligible:
            # Aucun formateur ne reste dans l'ordre : les disciples deviennent orphelins.
            for row in disciples:
                db.set_assignment_educator(row["id"], None)
                reassignments.append((row["disciple_character_id"], None))
            return reassignments, fallback_used, True

        for row in disciples:
            target = min(eligible, key=lambda k: eligible[k])
            db.set_assignment_educator(row["id"], target)
            # Redirige aussi les contrats actifs du disciple vers le nouvel éducateur.
            # Le virement hebdomadaire (système de salaire, pas encore développé) devra toujours lire
            # educator_character_id depuis educator_contracts au moment du paiement, jamais une valeur
            # mise en cache, pour que ce transfert automatique de bénéficiaire reste correct.
            db.transfer_active_contracts_educator(row["disciple_character_id"], removed_educator_id, target)
            reassignments.append((row["disciple_character_id"], target))
            eligible[target] += 1

        return reassignments, fallback_used, False

    async def _notify_redistribution(self, guild, order_name, old_educ_name, reassignments,
                                     fallback_used, aucun_formateur_restant):
        """DM à chaque disciple (et à son nouvel éducateur), puis récap complet à OWNER_ID."""
        for disciple_cid, new_educ_cid in reassignments:
            disc_name = self._char_name(disciple_cid)
            if new_educ_cid is not None:
                new_name = self._char_name(new_educ_cid)
                await self._dm_character_owner(
                    guild, disciple_cid,
                    f"📋 Ton éducateur ({old_educ_name}) a quitté l'ordre {order_name}. Tu es maintenant "
                    f"sous la responsabilité de {new_name}.")
                await self._dm_character_owner(
                    guild, new_educ_cid,
                    f"📋 Tu as reçu un nouveau disciple suite au départ de {old_educ_name} : {disc_name}.")
            else:
                await self._dm_character_owner(
                    guild, disciple_cid,
                    f"⚠️ Ton éducateur ({old_educ_name}) a quitté l'ordre {order_name}, et il ne reste "
                    "plus aucun formateur pour te reprendre. Un membre du staff doit s'en occuper.")

        # Récap complet au propriétaire du bot pour vérification.
        lines = []
        for disciple_cid, new_educ_cid in reassignments:
            target = self._char_name(new_educ_cid) if new_educ_cid is not None else "AUCUN (orphelin)"
            lines.append(f"• {self._char_name(disciple_cid)} → {target}")
        recap = (f"📋 Redistribution des disciples suite au retrait de **{old_educ_name}** dans l'ordre "
                 f"**{order_name}** :\n" + "\n".join(lines))
        if fallback_used:
            recap += ("\n\n⚠️ fallback_used : aucun éducateur n'avait strictement moins de disciples, "
                      "répartition forcée parmi tous les formateurs restants.")
        if aucun_formateur_restant:
            recap += ("\n\n⚠️ aucun_formateur_restant : plus aucun formateur dans l'ordre, disciples "
                      "laissés orphelins (à réassigner manuellement).")
        try:
            owner_user = await self.bot.fetch_user(OWNER_ID)
            await owner_user.send(recap)
        except discord.HTTPException:
            pass

    # =================================================================
    # SALAIRES (Direct / Hybride)
    # =================================================================
    def rank_of(self, character_id, order_id) -> int:
        """Rang hiérarchique d'un personnage dans un ordre (0 = plus haut gradé, 99 = inconnu / non
        membre). Sert à prioriser les paiements quand le compte est déjà négatif."""
        m = db.get_order_member(order_id, character_id)
        role = m["role_label"] if m else None
        return ORDER_HIERARCHY_RANK.get(role, 99)

    def _next_monday_or_today(self) -> str:
        """Date d'entrée en vigueur d'un salaire : aujourd'hui si lundi, sinon le prochain lundi
        (format YYYY-MM-DD)."""
        today = datetime.utcnow().date()
        if today.weekday() == 0:  # lundi
            return today.isoformat()
        return (today + timedelta(days=7 - today.weekday())).isoformat()

    async def handle_salaire(self, interaction, cid):
        _, order_id, user_id = cid.split(":")
        order_id, user_id = int(order_id), int(user_id)
        if not self._is_chief(order_id, interaction.user.id):
            await interaction.response.send_message("Seul le chef de l'ordre peut faire ça.", ephemeral=True)
            return
        await interaction.response.send_message(
            embed=discord.Embed(
                title="💰 Salaires", description="Que veux tu faire ?", color=PHOENIX_COLOR),
            view=SalaireView(order_id, user_id))

    async def handle_salaire_add(self, interaction, cid):
        _, order_id, user_id = cid.split(":")
        order_id, user_id = int(order_id), int(user_id)
        if interaction.user.id != user_id or not self._is_chief(order_id, interaction.user.id):
            await interaction.response.send_message("Seul le chef de l'ordre peut faire ça.", ephemeral=True)
            return
        # 1) Verrou : impossible d'ajouter des salaires tant que le compte est verrouillé.
        if db.get_order(order_id)["security_lock"]:
            await interaction.response.send_message(
                "🔒 Ce compte est verrouillé (trésorerie négative), impossible d'ajouter des salaires "
                "pour l'instant.", ephemeral=True)
            return
        if not self._acquire(interaction.user.id):
            await interaction.response.send_message(
                "Tu as déjà une action en cours, termine la d'abord.", ephemeral=True)
            return
        try:
            await interaction.response.send_message("💰 Ajout de salaires…", ephemeral=True)
            channel = interaction.channel
            n = await self._ask_positive_int(
                channel, interaction.user, "Combien de personnes veux tu ajouter ?")
            if n is None:
                return
            parsed = await self._collect_salary_lines(channel, interaction.user, n, order_id)
            if parsed is None:
                return
            eff = self._next_monday_or_today()
            now = _now()
            recap = []
            for character_id, montant in parsed:
                existing = db.get_salary(order_id, character_id)
                db.upsert_salary(order_id, character_id, montant, eff, now)
                recap.append((character_id, montant, existing is not None))
            lines = []
            for character_id, montant, updated in recap:
                verbe = "mis à jour" if updated else "ajouté"
                lines.append(f"• **{self._char_name(character_id)}** — {_fmt(montant)} ¥ ({verbe})")
            await channel.send(embed=discord.Embed(
                title="✅ Salaires enregistrés",
                description="\n".join(lines) + f"\n\n📅 Entrée en vigueur : **{_fmt_date(eff)}**.",
                color=PHOENIX_COLOR))
        finally:
            self._release(interaction.user.id)

    async def _collect_salary_lines(self, channel, user, n, order_id):
        """Collecte n lignes « IBAN montant », valide chaque IBAN (compte courant existant) et chaque
        montant, et ne redemande QUE les lignes en erreur. Retourne [(character_id, montant), ...] ou
        None si annulé."""
        results = [None] * n
        pending = list(range(n))  # indices encore à (re)saisir
        first = True
        while pending:
            if first:
                await channel.send(
                    f"Colle **{n}** ligne(s) au format `IBAN montant` (une par ligne).")
                first = False
            else:
                nums = ", ".join(str(i + 1) for i in pending)
                await channel.send(
                    f"Recolle uniquement la/les ligne(s) en erreur (ligne(s) {nums}), "
                    f"soit **{len(pending)}** ligne(s), au format `IBAN montant`.")
            m = await self.wait_message(channel, user)
            if m is None:
                await channel.send("⏳ Annulé.")
                return None
            raw_lines = [ln.strip() for ln in m.content.splitlines() if ln.strip()]
            if len(raw_lines) != len(pending):
                await channel.send(
                    f"❌ Il faut exactement **{len(pending)}** ligne(s), tu en as fourni {len(raw_lines)}.")
                continue
            errors = []      # (numéro_ligne_affiché, raison)
            still_pending = []
            for slot, ln in zip(pending, raw_lines):
                parts = ln.split()
                if len(parts) != 2:
                    errors.append((slot + 1, "format attendu : `IBAN montant`"))
                    still_pending.append(slot)
                    continue
                iban_raw, montant_raw = parts[0].strip().upper(), parts[1]
                acct = db.get_bank_account_by_courant_iban(iban_raw)
                montant = _parse_int(montant_raw)
                if acct is None:
                    errors.append((slot + 1, f"IBAN `{iban_raw}` introuvable"))
                    still_pending.append(slot)
                    continue
                if montant is None or montant <= 0:
                    errors.append((slot + 1, "montant invalide (entier positif attendu)"))
                    still_pending.append(slot)
                    continue
                results[slot] = (acct["character_id"], montant)
            if errors:
                detail = "\n".join(f"• Ligne {num} : {reason}" for num, reason in errors)
                await channel.send(embed=discord.Embed(
                    title="❌ Lignes en erreur", description=detail, color=discord.Color.red()))
            pending = still_pending
        return results

    async def handle_salaire_remove(self, interaction, cid):
        _, order_id, user_id = cid.split(":")
        order_id, user_id = int(order_id), int(user_id)
        if interaction.user.id != user_id or not self._is_chief(order_id, interaction.user.id):
            await interaction.response.send_message("Seul le chef de l'ordre peut faire ça.", ephemeral=True)
            return
        if not self._acquire(interaction.user.id):
            await interaction.response.send_message(
                "Tu as déjà une action en cours, termine la d'abord.", ephemeral=True)
            return
        try:
            await interaction.response.send_message("💰 Retrait d'un salaire…", ephemeral=True)
            channel = interaction.channel
            while True:
                await channel.send("Entre l'IBAN du salarié à retirer.")
                m = await self.wait_message(channel, interaction.user)
                if m is None:
                    await channel.send("⏳ Annulé.")
                    return
                iban = m.content.strip().upper()
                acct = db.get_bank_account_by_courant_iban(iban)
                if acct is None:
                    await channel.send("❌ Aucun compte trouvé avec cet IBAN. Réessaie.")
                    continue
                character_id = acct["character_id"]
                if db.get_salary(order_id, character_id) is None:
                    await channel.send(
                        "❌ Ce personnage ne perçoit aucun salaire dans cet ordre. Réessaie.")
                    continue
                db.remove_salary(order_id, character_id)
                await channel.send(embed=discord.Embed(
                    description=f"✅ Salaire de **{self._char_name(character_id)}** retiré.",
                    color=PHOENIX_COLOR))
                return
        finally:
            self._release(interaction.user.id)

    # ---------- boutons du DM de choix de verrou ----------
    async def handle_lock_keep(self, interaction, cid):
        _, order_id, user_id = cid.split(":")
        order_id, user_id = int(order_id), int(user_id)
        if interaction.user.id != user_id or not self._is_chief(order_id, interaction.user.id):
            await interaction.response.send_message("Ce panneau ne t'appartient pas.", ephemeral=True)
            return
        db.set_order_lock_grace_until(order_id, _plus_months(_now(), 2))
        self._grace_prompt_pending.discard(order_id)
        await interaction.response.edit_message(
            content="🔒 Verrou conservé. Il se désactivera automatiquement dans 2 mois.", view=None)

    async def handle_lock_off(self, interaction, cid):
        _, order_id, user_id = cid.split(":")
        order_id, user_id = int(order_id), int(user_id)
        if interaction.user.id != user_id or not self._is_chief(order_id, interaction.user.id):
            await interaction.response.send_message("Ce panneau ne t'appartient pas.", ephemeral=True)
            return
        db.clear_order_lock(order_id)
        self._grace_prompt_pending.discard(order_id)
        await interaction.response.edit_message(
            content="🔓 Verrou désactivé. Les dépenses de l'ordre sont de nouveau possibles.", view=None)

    # =================================================================
    # TÂCHE PLANIFIÉE : SALAIRES + VERROUS + ÉCHÉANCES
    # =================================================================
    @tasks.loop(hours=24)
    async def salary_loop(self):
        today = datetime.utcnow().date()
        is_monday = today.weekday() == 0
        for order in db.get_orders_of_types(("direct", "hybride")):
            try:
                await self._process_order_salary_cycle(order["id"], today, is_monday)
            except Exception as e:  # un ordre en erreur ne doit pas bloquer les autres
                print(f"[salary_loop] erreur sur l'ordre {order['id']} : {e}")

    @salary_loop.before_loop
    async def _before_salary_loop(self):
        await self.bot.wait_until_ready()

    def _guild_of_order(self, order):
        chef = get_character(order["chef_character_id"])
        gid = chef["guild_id"] if chef else None
        return self.bot.get_guild(gid) if gid else None

    async def _process_order_salary_cycle(self, order_id, today, is_monday):
        order = db.get_order(order_id)
        if order is None:
            return
        guild = self._guild_of_order(order)
        # A) Le lundi : paiement des salaires.
        if is_monday:
            await self._pay_salaries(order_id, today, guild)
        # B) Tous les jours : suivi des verrous et des échéances (peut dissoudre l'ordre).
        await self._check_locks_and_deadlines(order_id, guild)

    async def _pay_salaries(self, order_id, today, guild):
        order = db.get_order(order_id)
        if order is None or order["security_lock"]:
            return  # verrouillé : personne n'est payé cette semaine
        salaries = db.get_salaries_effective(order_id, today.isoformat())
        if not salaries:
            return
        solde_start = order["solde_courant"]
        if solde_start >= 0:
            to_pay = list(salaries)  # tout le monde, quitte à passer sous zéro après coup
        else:
            # Déjà négatif AVANT de payer : seuls les 5 plus hauts gradés, puis verrouillage.
            to_pay = sorted(salaries, key=lambda s: self.rank_of(s["character_id"], order_id))[:5]
            db.set_order_security_lock(order_id, 1)
            if order["negative_since"] is None:
                db.set_order_negative_since(order_id, _now())
        for s in to_pay:
            montant, character_id = s["montant"], s["character_id"]
            db.adjust_order_solde(order_id, -montant)
            db.add_order_transaction(order_id, "Salaire hebdomadaire", -montant, _now())
            credit_account(character_id, "courant", montant)
            add_transaction(character_id, "Salaire hebdomadaire", montant)

    async def _check_locks_and_deadlines(self, order_id, guild):
        order = db.get_order(order_id)
        if order is None or not order["security_lock"]:
            return
        now = _now()
        if order["solde_courant"] >= 0:
            # Compte remonté à l'équilibre.
            if order["lock_grace_until"] is None:
                if order_id not in self._grace_prompt_pending:
                    self._grace_prompt_pending.add(order_id)
                    await self._send_lock_recovery_prompt(order, guild)
            elif now >= order["lock_grace_until"]:
                db.clear_order_lock(order_id)
                self._grace_prompt_pending.discard(order_id)
                await self._dm_character_owner(
                    guild, order["chef_character_id"],
                    "🔓 Le verrou de sécurité s'est désactivé automatiquement après 2 mois.")
        else:
            # Toujours dans le négatif : le retour à l'équilibre n'a pas eu lieu, on repart à zéro
            # côté prompt (au cas où il était remonté puis redescendu).
            self._grace_prompt_pending.discard(order_id)
            neg = order["negative_since"]
            if not neg:
                return
            if now >= _plus_months(neg, 2):
                await self.dissolve_order(order_id, guild)
                return
            if now >= _plus_months(neg, 1) and not order["warning_sent"]:
                db.set_order_warning_sent(order_id, 1)
                await self._dm_character_owner(
                    guild, order["chef_character_id"],
                    "⚠️ Ton ordre est dans le négatif depuis 1 mois. Il te reste 1 mois pour redresser "
                    "la situation avant la suppression automatique de l'ordre.")

    async def _send_lock_recovery_prompt(self, order, guild):
        char = get_character(order["chef_character_id"])
        if not char:
            return
        uid = char["user_id"]
        member = guild.get_member(uid) if guild else None
        if member is None:
            try:
                member = await self.bot.fetch_user(uid)
            except discord.HTTPException:
                return
        try:
            await member.send(
                content=(
                    "✅ Ton ordre est remonté à l'équilibre. Le verrou de sécurité empêche actuellement "
                    "tout débit (dépenses, virements, salons, salaires). Veux tu :\n\n"
                    "• Garder le verrou actif (protection contre un retour dans le négatif, il se "
                    "désactivera automatiquement dans 2 mois)\n"
                    "• Désactiver le verrou maintenant (risque de retomber dans le négatif)"),
                view=LockRecoveryView(order["id"], uid))
        except discord.HTTPException:
            pass

    # =================================================================
    # DISSOLUTION D'UN ORDRE
    # =================================================================
    async def dissolve_order(self, order_id, guild):
        """Dissolution pour trésorerie négative prolongée : indemnité de 2 mois (montant x8) aux membres
        salariés, et bannissement de création d'ordre du chef pendant 2 mois."""
        await self._dissolve_common(
            order_id, guild, indemnity_weeks=8, ban_chief=True,
            tx_label="Indemnité de dissolution d'ordre",
            member_text=("⚠️ L'ordre **{name}** a été dissous suite à une trésorerie négative "
                         "prolongée. Tu n'en fais plus partie."),
            chef_text=("⚠️ Ton ordre **{name}** a été dissous suite à une trésorerie négative prolongée "
                       "pendant 2 mois. En tant que chef, tu ne reçois pas d'indemnité, et tu ne pourras "
                       "pas créer de nouvel ordre avant 2 mois."))

    async def dissolve_order_member_left(self, order_id, guild):
        """Dissolution suite au départ du chef du serveur : indemnité de 1 mois (montant x4) aux membres
        salariés, et AUCUN bannissement (le chef n'a rien fait de répréhensible)."""
        await self._dissolve_common(
            order_id, guild, indemnity_weeks=4, ban_chief=False,
            tx_label="Indemnité de dissolution d'ordre (départ du chef)",
            member_text=("⚠️ L'ordre **{name}** a été dissous suite au départ de son chef du serveur. "
                         "Tu n'en fais plus partie."),
            chef_text=None)  # le chef a quitté le serveur : inutile de le notifier

    async def _dissolve_common(self, order_id, guild, *, indemnity_weeks, ban_chief,
                               tx_label, member_text, chef_text):
        """Logique commune de dissolution. IMPORTANT : les libérations croisées (salons/contrats liés à
        d'AUTRES ordres) et les résolutions de noms se font AVANT la suppression finale, pendant que les
        données existent encore."""
        order = db.get_order(order_id)
        if order is None:
            return
        order_name = order["name"]
        chef_cid = order["chef_character_id"]

        # 1) Indemnités + notifications des membres (hors chef).
        for m in db.get_order_members(order_id):
            cid = m["character_id"]
            if cid == chef_cid:
                continue
            sal = db.get_salary(order_id, cid)
            indemnite = sal["montant"] * indemnity_weeks if sal else 0
            if indemnite > 0:
                credit_account(cid, "courant", indemnite)
                add_transaction(cid, tx_label, indemnite)
            txt = member_text.format(name=order_name)
            if indemnite > 0:
                txt += f"\n💰 Tu as reçu une indemnité de **{_fmt(indemnite)} ¥**."
            await self._dm_character_owner(guild, cid, txt)

        # 2) Notification du chef (sauf départ du serveur : chef_text=None).
        if chef_text is not None:
            await self._dm_character_owner(guild, chef_cid, chef_text.format(name=order_name))

        # 3) Ban éventuel du chef.
        if ban_chief:
            chef = get_character(chef_cid)
            if chef:
                db.set_chief_ban(chef["user_id"], _plus_months(_now(), 2))

        # 4) Libération des salons liés à d'autres ordres (miroirs chez la contrepartie).
        await self._release_linked_salons(order_id, order_name, guild)

        # 5) Clôture + notification des contrats (défensif : no-op tant que la table n'existe pas).
        await self._end_order_contracts(order_id, order_name, guild)

        # 6) Suppression finale de l'ordre et de ses données propres.
        db.delete_order_cascade(order_id)

    async def _release_linked_salons(self, order_id, order_name, guild):
        """Pour chaque salon de l'ordre lié à un autre ordre (Louée/Location) : supprime l'entrée miroir
        chez la contrepartie et prévient son chef que le salon est désormais libre."""
        for s in db.get_linked_salons(order_id):
            other_id = s["linked_order_id"]
            if not other_id:
                continue
            ch = guild.get_channel(s["channel_id"]) if guild else None
            salon_name = ch.name if ch else str(s["channel_id"])
            db.remove_order_salon_any(other_id, s["channel_id"])
            other = db.get_order(other_id)
            if not other:
                continue
            # Notre statut sur ce salon détermine le rôle de la contrepartie :
            #  - nous 'Location' (propriétaire qui louait) -> eux locataires -> ils « louaient »
            #  - nous 'Louée' (locataire) -> eux propriétaires -> ils « avaient en location »
            verbe = "louiez" if s["status"] == "Location" else "aviez en location avec eux"
            await self._dm_character_owner(
                guild, other["chef_character_id"],
                f"⚠️ L'ordre **{order_name}** a été dissous. Le salon #{salon_name} que vous {verbe} "
                "n'est plus lié à aucun contrat, il est maintenant libre de toute location de votre côté.")

    async def _end_order_contracts(self, order_id, order_name, guild):
        """Clôt (status='ended') les contrats actifs liés à l'ordre et prévient les deux parties.
        Défensif : la table educator_contracts n'existe pas encore, donc no-op silencieux dans ce cas."""
        try:
            ended = db.end_order_contracts(order_id)  # [] si table absente
        except Exception:
            return
        for disciple_cid, educator_cid in ended:
            msg = (f"⚠️ Suite à la dissolution de l'ordre **{order_name}**, le contrat entre "
                   f"{self._char_name(disciple_cid)} et {self._char_name(educator_cid)} a pris fin "
                   "automatiquement.")
            await self._dm_character_owner(guild, disciple_cid, msg)
            await self._dm_character_owner(guild, educator_cid, msg)

    # =================================================================
    # SALONS — ACQUISITION (section 8)
    # =================================================================
    async def handle_salons_buy(self, interaction, cid):
        order_id = int(cid.split(":")[1])
        if not await self._require_chief(interaction, order_id):
            return
        order = db.get_order(order_id)
        if order["type"] not in ("direct", "hybride"):
            await interaction.response.send_message(
                "Seuls les ordres Direct/Hybride peuvent posséder des salons.", ephemeral=True)
            return
        # Verrou de sécurité : achat de salons = dépense, bloqué tant que le compte est verrouillé.
        if order["security_lock"]:
            await interaction.response.send_message(
                "🔒 Ce compte est verrouillé suite à une trésorerie négative prolongée, aucune dépense "
                "n'est possible pour l'instant. Seuls les dépôts sont acceptés.", ephemeral=True)
            return
        if not self._acquire(interaction.user.id):
            await interaction.response.send_message(
                "Tu as déjà une action en cours, termine la d'abord.", ephemeral=True)
            return
        try:
            await interaction.response.send_message("🏠 Acquisition de salons…", ephemeral=True)
            channel = interaction.channel
            n = await self._ask_positive_int(
                channel, interaction.user,
                f"Combien de salons veux tu acquérir ? (taxe de {_fmt(TAXE_SALON)} ¥ par salon et par semaine)")
            if n is None:
                return
            channels = await self._collect_new_salons(channel, interaction.user, n, order_id)
            if channels is None:
                return
            if not channels:
                await channel.send("Aucun salon valide à acquérir.")
                return
            acquired = []
            for ch in channels:
                fresh = db.get_order(order_id)
                if fresh["solde_courant"] < TAXE_SALON:  # revérif temps réel du solde de l'ordre
                    await channel.send(
                        f"⚠️ Solde de l'ordre insuffisant, achat interrompu. "
                        f"{len(acquired)}/{len(channels)} salon(s) acquis.")
                    break
                db.adjust_order_solde(order_id, -TAXE_SALON)
                db.add_order_salon(order_id, ch.id, "Acheté")
                db.add_order_transaction(order_id, f"Achat salon #{ch.name}", -TAXE_SALON, _now())
                acquired.append(ch)
            recap = ", ".join(f"#{c.name}" for c in acquired) if acquired else "aucun"
            await channel.send(embed=discord.Embed(
                description=f"✅ Salons acquis : {recap}.", color=PHOENIX_COLOR))
            # TODO : tâche @tasks.loop hebdomadaire pour débiter automatiquement 15k par salon Acheté,
            # pas encore implémentée.
            await self._send_dashboard(channel, db.get_order(order_id), interaction.user.id)
        finally:
            self._release(interaction.user.id)

    async def _collect_new_salons(self, channel, user, n, order_id):
        """Collecte n salons NON possédés, en gérant les conflits (Annuler / Changer). Retourne la
        liste finale de channels (peut être plus courte si annulation partielle), ou None si annulé."""
        await channel.send(f"Mentionne les {n} salons en une seule fois (ex : #salon1 #salon2 ...).")
        final = []
        seen = set()
        need = n
        while need > 0:
            m = await self.wait_message(channel, user)
            if m is None:
                await channel.send("⏳ Annulé.")
                return None
            mentions = m.channel_mentions
            if len(mentions) < need:
                await channel.send(
                    f"Il faut mentionner {need} salon(s), tu en as mentionné {len(mentions)}. Réessaie.")
                continue
            batch = mentions[:need]
            conflicts = []
            for ch in batch:
                if ch.id in seen or db.get_salon_owner(ch.id, "Acheté") is not None:
                    conflicts.append(ch)
                else:
                    final.append(ch)
                    seen.add(ch.id)
                    need -= 1
            if not conflicts:
                break
            names = ", ".join(f"#{c.name}" for c in conflicts)
            view = TwoChoiceView(
                user.id, "❌ Annuler ces salons", "cancel", "🔄 Changer", "change",
                a_style=discord.ButtonStyle.danger, b_style=discord.ButtonStyle.primary)
            await channel.send(
                embed=discord.Embed(
                    title="Salons en conflit",
                    description=f"Déjà possédés (ou en double) : {names}.\n\n"
                                "« Annuler ces salons » les retire et garde les autres. "
                                "« Changer » te laisse en mentionner de nouveaux à la place.",
                    color=PHOENIX_COLOR),
                view=view)
            await view.wait()
            if view.result == "change":
                await channel.send(f"Mentionne {len(conflicts)} nouveau(x) salon(s).")
                continue  # need == nombre de conflits restants
            break  # cancel ou timeout : on finalise avec les salons déjà valides
        return final

    # =================================================================
    # SALONS — GESTION (section 9)
    # =================================================================
    def _salon_tuples(self, order_id, guild):
        """[(nom_salon, statut), ...] pour la pillow — nom résolu via guild.get_channel."""
        result = []
        for s in db.get_order_salons(order_id):
            ch = guild.get_channel(s["channel_id"]) if guild else None
            result.append((ch.name if ch else str(s["channel_id"]), s["status"]))
        return result

    async def _send_salons(self, channel, order_id, user_id, page):
        order = db.get_order(order_id)
        salons = self._salon_tuples(order_id, channel.guild)
        path = _tmp("salons")
        path, total_pages = generate_salons_ordre_image(order["name"], salons, page, path)
        view = SalonPageView(order_id, user_id, max(1, min(page, total_pages)), total_pages)
        await channel.send(file=discord.File(path, filename="salons.png"), view=view)
        _rm(path)

    async def handle_salon(self, interaction, cid):
        order_id = int(cid.split(":")[1])
        if not await self._require_chief(interaction, order_id):
            return
        order = db.get_order(order_id)
        if order["type"] not in ("direct", "hybride"):
            await interaction.response.send_message(
                "Seuls les ordres Direct/Hybride gèrent des salons.", ephemeral=True)
            return
        await interaction.response.defer()
        await self._send_salons(interaction.channel, order_id, interaction.user.id, 1)

    async def handle_salon_page(self, interaction, cid, direction):
        _, order_id, user_id, page = cid.split(":")
        order_id, user_id, page = int(order_id), int(user_id), int(page)
        if interaction.user.id != user_id:
            await interaction.response.send_message("Cette pagination n'est pas la tienne.", ephemeral=True)
            return
        new_page = page + (1 if direction == "next" else -1)
        order = db.get_order(order_id)
        salons = self._salon_tuples(order_id, interaction.guild)
        path = _tmp("salons")
        path, total_pages = generate_salons_ordre_image(order["name"], salons, new_page, path)
        clamped = max(1, min(new_page, total_pages))
        view = SalonPageView(order_id, user_id, clamped, total_pages)
        await interaction.response.edit_message(
            attachments=[discord.File(path, filename="salons.png")], view=view)
        _rm(path)

    async def handle_salon_action(self, interaction, cid):
        _, order_id, user_id = cid.split(":")
        order_id = int(order_id)
        if not self._is_chief(order_id, interaction.user.id):
            await interaction.response.send_message("Seul le chef de l'ordre peut faire ça.", ephemeral=True)
            return
        value = (interaction.data.get("values") or [None])[0]
        if value not in ("revendre", "louer"):
            await interaction.response.send_message("Action inconnue.", ephemeral=True)
            return
        if not self._acquire(interaction.user.id):
            await interaction.response.send_message(
                "Tu as déjà une action en cours, termine la d'abord.", ephemeral=True)
            return
        try:
            await interaction.response.defer()
            channel = interaction.channel
            if value == "revendre":
                await self._salon_resell(channel, interaction.user, order_id)
            else:
                await self._salon_rent(channel, interaction.user, order_id)
        finally:
            self._release(interaction.user.id)

    async def _collect_owned_salons(self, channel, user, n, order_id):
        """Collecte n salons possédés par CET ordre (statut Acheté). Retourne la liste ou None."""
        await channel.send(f"Mentionne les {n} salon(s) concerné(s) en un seul message.")
        while True:
            m = await self.wait_message(channel, user)
            if m is None:
                await channel.send("⏳ Annulé.")
                return None
            mentions = m.channel_mentions
            if len(mentions) < n:
                await channel.send(f"Il faut mentionner {n} salon(s). Réessaie.")
                continue
            batch = mentions[:n]
            if len({c.id for c in batch}) != len(batch):
                await channel.send("Tu as mentionné deux fois le même salon. Réessaie.")
                continue
            owned_ids = {s["channel_id"] for s in db.get_order_salons(order_id) if s["status"] == "Acheté"}
            bad = [c for c in batch if c.id not in owned_ids]
            if bad:
                names = ", ".join(f"#{c.name}" for c in bad)
                await channel.send(
                    f"Ces salons n'appartiennent pas à ton ordre (statut Acheté) : {names}. Réessaie.")
                continue
            return batch

    async def _salon_resell(self, channel, user, order_id):
        n = await self._ask_positive_int(channel, user, "Combien de salons veux tu vendre ?")
        if n is None:
            return
        channels = await self._collect_owned_salons(channel, user, n, order_id)
        if channels is None:
            return
        await channel.send(
            "Où veux tu vendre ?\n**1.** Vendre au gouvernement\n**2.** Vendre à un joueur\n\nRéponds 1 ou 2.")
        choix = await self._await_choice(channel, user, ("1", "2"))
        if choix is None:
            return
        if choix == "1":
            total = TAXE_SALON * len(channels)
            for ch in channels:
                db.remove_order_salon_by_channel(order_id, ch.id, "Acheté")
                db.add_order_transaction(order_id, f"Vente salon #{ch.name} (gouvernement)", TAXE_SALON, _now())
            db.adjust_order_solde(order_id, total)
            await channel.send(embed=discord.Embed(
                description=f"✅ {len(channels)} salon(s) vendus au gouvernement pour {_fmt(total)} ¥.",
                color=PHOENIX_COLOR))
        else:
            await self._resell_to_player(channel, user, order_id, channels)
        await self._send_dashboard(channel, db.get_order(order_id), user.id)

    async def _resell_to_player(self, channel, seller_user, order_id, channels):
        await channel.send("Mentionne le joueur acheteur.")
        buyer_member = await self._await_mention(channel, seller_user)
        if buyer_member is None:
            return
        buyer_cid = await self._select_character_of(
            channel, seller_user, buyer_member, "Ce joueur n'a aucun personnage validé.")
        if buyer_cid is None:
            return
        buyer_order = db.get_order_by_chief(buyer_cid)
        if not buyer_order:
            await channel.send("Ce joueur n'a pas d'ordre.")
            return
        price = await self._ask_positive_int(
            channel, seller_user,
            f"Prix TOTAL pour ces {len(channels)} salon(s) ? (minimum {_fmt(TAXE_SALON)} ¥)")
        if price is None:
            return
        if price < TAXE_SALON:
            await channel.send(f"Le prix ne peut jamais être inférieur à {_fmt(TAXE_SALON)} ¥.")
            return
        seller_order = db.get_order(order_id)
        seller_char = get_character(seller_order["chef_character_id"])
        await channel.send("📨 Proposition envoyée en message privé, en attente de réponse…")
        final_price = await self._negotiate(seller_user, buyer_member, seller_char, len(channels), price)
        if final_price is None:
            await channel.send("La transaction n'a pas abouti (refus ou délai dépassé).")
            return
        # Exécution : débite l'ordre B, crédite l'ordre A, transfère les salons.
        buyer_order = db.get_order_by_chief(buyer_cid)  # relecture
        if buyer_order["solde_courant"] < final_price:
            await channel.send("❌ L'ordre acheteur n'a pas un solde suffisant, transaction annulée.")
            await self._safe_dm(buyer_member, "❌ Ton ordre n'a pas assez de fonds, la transaction est annulée.")
            return
        db.adjust_order_solde(buyer_order["id"], -final_price)
        db.adjust_order_solde(order_id, final_price)
        for ch in channels:
            db.transfer_salon(ch.id, order_id, buyer_order["id"])
        db.add_order_transaction(order_id, f"Vente de {len(channels)} salon(s) à un joueur", final_price, _now())
        db.add_order_transaction(buyer_order["id"], f"Achat de {len(channels)} salon(s) à un joueur", -final_price, _now())
        await channel.send(embed=discord.Embed(
            description=f"✅ {len(channels)} salon(s) cédés pour {_fmt(final_price)} ¥.", color=PHOENIX_COLOR))
        await self._safe_dm(buyer_member, f"✅ Transaction conclue : {len(channels)} salon(s) pour {_fmt(final_price)} ¥.")

    async def _safe_dm(self, member, content):
        try:
            await member.send(content)
        except discord.HTTPException:
            pass

    async def _await_dm_int(self, member, dm):
        def check(msg):
            return msg.author.id == member.id and msg.channel.id == dm.id and not msg.author.bot
        try:
            m = await self.bot.wait_for("message", check=check, timeout=DM_TIMEOUT)
        except asyncio.TimeoutError:
            return None
        return _parse_int(m.content)

    async def _negotiate(self, seller_user, buyer_member, seller_char, n_salons, price):
        """Boucle de négociation en DM. Retourne le prix final accepté, ou None (refus / expiration).
        Le destinataire courant décide ; s'il négocie, il propose un nouveau montant et la main passe
        à l'autre partie."""
        seller_name = seller_char["character_name"] if seller_char else "?"
        recipient, proposer = buyer_member, seller_user
        current_price = price
        first_round = True
        for _ in range(20):  # garde-fou anti boucle infinie
            try:
                dm = await recipient.create_dm()
            except discord.HTTPException:
                return None
            if first_round:
                text = (f"{seller_user.mention} ({seller_name}) souhaite te céder {n_salons} salon(s) "
                        f"pour {_fmt(current_price)} ¥.")
            else:
                text = (f"Nouvelle proposition de {proposer.mention} : {n_salons} salon(s) "
                        f"pour {_fmt(current_price)} ¥.")
            view = NegotiationView(recipient.id)
            try:
                await dm.send(content=text, view=view)
            except discord.HTTPException:
                return None
            await view.wait()
            if view.result is None:  # timeout
                await self._safe_dm(seller_user, "⏳ La proposition de vente de salons a expiré.")
                await self._safe_dm(buyer_member, "⏳ La proposition de vente de salons a expiré.")
                return None
            if view.result == "accept":
                return current_price
            if view.result == "refuse":
                await self._safe_dm(proposer, "❌ Ta proposition a été refusée.")
                return None
            # negotiate : le destinataire propose un nouveau montant
            await dm.send(f"Propose un nouveau montant TOTAL (minimum {_fmt(TAXE_SALON)} ¥).")
            newp = await self._await_dm_int(recipient, dm)
            if newp is None:
                await self._safe_dm(proposer, "⏳ La négociation a expiré ou été annulée.")
                return None
            if newp < TAXE_SALON:
                await dm.send(f"Montant trop bas (minimum {_fmt(TAXE_SALON)} ¥), négociation annulée.")
                await self._safe_dm(proposer, "❌ La contre-proposition était invalide, négociation annulée.")
                return None
            current_price = newp
            recipient, proposer = proposer, recipient  # la main passe à l'autre partie
            first_round = False
        return None

    async def _salon_rent(self, channel, user, order_id):
        n = await self._ask_positive_int(channel, user, "Combien de salons veux tu louer ?")
        if n is None:
            return
        channels = await self._collect_owned_salons(channel, user, n, order_id)
        if channels is None:
            return
        others = db.get_orders_in_guild(channel.guild.id, exclude_order_id=order_id)
        if not others:
            await channel.send("Aucun autre ordre disponible pour la location.")
            return
        view = SimpleSelectView(
            "Choisis l'ordre locataire...",
            [(f"{name} — chef {chef}", oid) for oid, name, chef in others], user.id)
        await channel.send("À quel ordre veux tu louer ces salons ?", view=view)
        await view.wait()
        if view.result is None:
            await channel.send("⏳ Annulé.")
            return
        tenant_order_id = int(view.result)
        weeks = await self._ask_positive_int(
            channel, user,
            f"Durée de la location en semaines ? (loyer fixé à {_fmt(TAXE_SALON)} ¥ par salon et par semaine)")
        if weeks is None:
            return
        expiry = (datetime.utcnow() + timedelta(weeks=weeks)).isoformat()
        for ch in channels:
            # Côté propriétaire : le salon passe en 'Location' (loué à l'ordre locataire).
            db.add_order_salon(order_id, ch.id, "Location", linked_order_id=tenant_order_id, location_expiry=expiry)
            # Entrée miroir côté locataire : 'Louée' (loué depuis l'ordre propriétaire).
            db.add_order_salon(tenant_order_id, ch.id, "Louée", linked_order_id=order_id, location_expiry=expiry)
        # TODO : tâche @tasks.loop pour gérer les débits de location et l'expiration automatique,
        # pas encore implémentée.
        await channel.send(embed=discord.Embed(
            description=f"✅ {len(channels)} salon(s) mis en location pour {weeks} semaine(s).",
            color=PHOENIX_COLOR))
        await self._send_dashboard(channel, db.get_order(order_id), user.id)


async def setup(bot):
    await bot.add_cog(Ordre(bot))
