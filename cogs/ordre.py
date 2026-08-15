# RÈGLES DE ROBUSTESSE PERMANENTES DU PROJET — voir cogs/shop.py (règles 1 à 6). En particulier :
# revérif du rôle/chef AU CLIC, isolation des flux textuels par utilisateur, revérif des soldes en
# temps réel juste avant débit, anti double-clic sur tout bouton de confirmation/exécution.

import asyncio
import os
import unicodedata
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

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
    credit_account, add_transaction, credit_compte_courant,
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
FICHE_STAFF_ROLE_ID = 1521229332075512039    # rôle staff global (défini localement, comme dans les autres cogs)

# Paramètres modifiables en « 🔧 Mode staff » (résolution par préfixe, accents ignorés).
STAFF_EDIT_PARAMS = ["Nom", "Effectif ajouter", "Effectif retiré", "Trésorerie ajouter",
                     "Trésorerie retirer", "Salon ajouté", "Salon retiré", "Salon modifié"]

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

# Contrats éducateur ↔ employeur : unités de durée déterminée -> nombre de jours (semaines*7, mois*30,
# années*365, comme spécifié).
CONTRAT_UNITS = [("Jours", "jours"), ("Semaines", "semaines"), ("Mois", "mois"), ("Années", "annees")]
CONTRAT_UNIT_DAYS = {"jours": 1, "semaines": 7, "mois": 30, "annees": 365}
CONTRAT_UNIT_LABEL = {"jours": "jour(s)", "semaines": "semaine(s)", "mois": "mois", "annees": "année(s)"}

# Vérification de présence des chefs d'ordre : 4x/jour, à l'heure de Paris (DST géré par zoneinfo).
# Sous Windows, zoneinfo a besoin du paquet `tzdata` (pip install tzdata) : en son absence, on retombe
# sur un UTC+1 fixe (sans bascule été/hiver) plutôt que de faire planter le chargement du cog.
try:
    PARIS_TZ = ZoneInfo("Europe/Paris")
except ZoneInfoNotFoundError:
    print("[ordre] tzdata introuvable : bascule sur UTC+1 fixe pour l'heure de Paris "
          "(installe `tzdata` pour la gestion DST).")
    PARIS_TZ = timezone(timedelta(hours=1))
CHECK_HOURS = [0, 6, 12, 18]

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


def _is_staff(member) -> bool:
    return any(r.id == FICHE_STAFF_ROLE_ID for r in getattr(member, "roles", []))


def _fold(s: str) -> str:
    """Minuscule + sans accents (pour la résolution par préfixe, insensible casse/accents)."""
    return "".join(
        c for c in unicodedata.normalize("NFD", (s or "").lower().strip())
        if unicodedata.category(c) != "Mn"
    )


def _match_staff_param(raw: str):
    """Résout un nom de paramètre staff : correspondance exacte sinon par préfixe (accents ignorés).
    Retourne la liste des correspondances (0 = inconnu, 1 = résolu, >1 = ambigu)."""
    t = _fold(raw)
    if not t:
        return []
    exact = [p for p in STAFF_EDIT_PARAMS if _fold(p) == t]
    if exact:
        return exact
    return [p for p in STAFF_EDIT_PARAMS if _fold(p).startswith(t)]


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
    """2 boutons renvoyant une valeur, avec anti double-clic (règle #6).

    Anti-bug « Unknown interaction » (404, code 10062) : une interaction ne peut recevoir qu'UNE SEULE
    réponse initiale (interaction.response.*). Le callback n'appelle donc `interaction.response.edit_message`
    qu'à UN seul endroit, et une seule fois. Si cette fenêtre de réponse n'est plus disponible (déjà
    répondue, ou token expiré → 10062), on retombe sur `interaction.message.edit(...)`, qui édite le message
    via l'API message normale (indépendante du token d'interaction). On ne rappelle JAMAIS
    `interaction.response.send_message` une seconde fois. De plus, le résultat est figé et la View est
    arrêtée AVANT tout appel réseau, pour que `view.wait()` du flux appelant se débloque même si l'édition
    échoue (sinon le flux resterait bloqué jusqu'au timeout)."""

    def __init__(self, owner_id, a_label, a_value, b_label, b_value,
                 a_style=discord.ButtonStyle.danger, b_style=discord.ButtonStyle.primary):
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

    async def _freeze_message(self, interaction):
        """Grise les boutons (état figé). UNE seule réponse d'interaction : `edit_message` si la fenêtre
        de réponse est encore ouverte, sinon `interaction.message.edit` (repli sans le token)."""
        for it in self.children:
            it.disabled = True
        try:
            if not interaction.response.is_done():
                await interaction.response.edit_message(view=self)   # 1re (et unique) réponse d'interaction
                return
        except discord.HTTPException:
            pass  # token expiré/inconnu (10062) ou déjà répondue : on bascule sur l'édition directe
        try:
            await interaction.message.edit(view=self)                # édition via l'API message normale
        except discord.HTTPException:
            pass

    def _mk(self, value):
        async def cb(interaction):
            if interaction.user.id != self.owner_id:
                await interaction.response.send_message("Ce choix ne t'appartient pas.", ephemeral=True)
                return
            if self._done:
                # Clic surnuméraire : on accuse réception sans re-répondre au message (jamais send_message).
                try:
                    if not interaction.response.is_done():
                        await interaction.response.defer()
                except discord.HTTPException:
                    pass
                return
            # On fige le résultat et on arrête la View AVANT tout appel réseau : ainsi view.wait() se
            # débloque même si l'édition du message échoue.
            self._done = True
            self.result = value
            self.stop()
            await self._freeze_message(interaction)
        return cb


class ButtonChoiceView(discord.ui.View):
    """N boutons renvoyant une valeur, avec anti double-clic (règle #6). Générique (ex : unité de durée
    d'un contrat). En session : utilisé avec view.wait() dans un flux déjà verrouillé."""

    def __init__(self, owner_id, choices):  # choices = [(label, value), ...]
        super().__init__(timeout=WAIT_TIMEOUT)
        self.owner_id = owner_id
        self.result = None
        self._done = False
        for label, value in choices:
            btn = discord.ui.Button(label=label, style=discord.ButtonStyle.primary)
            btn.callback = self._mk(value)
            self.add_item(btn)

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
                discord.SelectOption(label="🛒 Acheter", value="acheter"),
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
    """Sous la pillow des contrats côté ordre employeur (Direct/Hybride) : pagination (si plusieurs pages)
    + bouton « ➕ Créer un contrat » (toujours présent)."""

    def __init__(self, order_id, user_id, page, total_pages):
        super().__init__(timeout=None)
        if total_pages > 1:
            self.add_item(discord.ui.Button(
                label="Page précédente", emoji="◀️", style=discord.ButtonStyle.secondary,
                custom_id=f"ordre_cdir_prev:{order_id}:{user_id}:{page}", disabled=(page <= 1), row=0))
            self.add_item(discord.ui.Button(
                label="Page suivante", emoji="▶️", style=discord.ButtonStyle.secondary,
                custom_id=f"ordre_cdir_next:{order_id}:{user_id}:{page}", disabled=(page >= total_pages), row=0))
        self.add_item(discord.ui.Button(
            label="Créer un contrat", emoji="➕", style=discord.ButtonStyle.success,
            custom_id=f"ordre_contrat_create:{order_id}:{user_id}", row=1))


class ContratOfferView(discord.ui.View):
    """DM à l'éducateur : accepter ou refuser un LOT de contrats proposé par un ordre employeur.
    Persistant (le batch_id suffit à tout retrouver)."""

    def __init__(self, batch_id):
        super().__init__(timeout=None)
        self.add_item(discord.ui.Button(
            label="Accepter", emoji="✅", style=discord.ButtonStyle.success,
            custom_id=f"ordre_contrat_accept:{batch_id}"))
        self.add_item(discord.ui.Button(
            label="Refuser", emoji="❌", style=discord.ButtonStyle.danger,
            custom_id=f"ordre_contrat_refuse:{batch_id}"))


class ExternalSalaryExpiryView(discord.ui.View):
    """DM au chef quand un salaire externe arrive à terme : Annuler (retrait définitif) ou Renouveler
    (nouvelle durée). Persistant : l'id de la ligne order_salaries suffit à tout retrouver."""

    def __init__(self, salary_id):
        super().__init__(timeout=None)
        self.add_item(discord.ui.Button(
            label="Annuler", emoji="❌", style=discord.ButtonStyle.danger,
            custom_id=f"ordre_salext_cancel:{salary_id}"))
        self.add_item(discord.ui.Button(
            label="Renouveler", emoji="🔄", style=discord.ButtonStyle.success,
            custom_id=f"ordre_salext_renew:{salary_id}"))


class OrdreStaffChoiceView(discord.ui.View):
    """Écran d'accueil /ordre pour un membre du staff : consulter son ordre ou entrer en mode staff."""

    def __init__(self, user_id: int):
        super().__init__(timeout=None)
        self.add_item(discord.ui.Button(
            label="Afficher mon ordre", emoji="👤", style=discord.ButtonStyle.primary,
            custom_id=f"ordre_self:{user_id}"))
        self.add_item(discord.ui.Button(
            label="Mode staff", emoji="🔧", style=discord.ButtonStyle.secondary,
            custom_id=f"ordre_staff_mode:{user_id}"))


class OrdreStaffView(discord.ui.View):
    """Sous la pillow du staff : pagination (ligne 0, seulement si plusieurs pages, page encodée dans
    le custom_id) + actions Ajouter / Virer / Muter (ligne 1)."""

    def __init__(self, order_id, user_id, page=1, total_pages=1, is_educatif=False):
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
        # Gestion manuelle des disciples : réservée aux ordres éducatifs (le bouton n'apparaît qu'eux).
        if is_educatif:
            self.add_item(discord.ui.Button(
                label="Gérer le staff", emoji="🎓", style=discord.ButtonStyle.secondary,
                custom_id=f"ordre_staff_manage:{order_id}:{user_id}", row=2))


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
        self._contract_lock = set()  # anti double-clic sur l'acceptation/refus d'un lot de contrats (par batch_id)
        self._contract_timeout_tasks = set()  # tâches de timeout (2 min) des propositions de contrat en attente
        self._ext_salary_prompt_pending = set()  # salaires externes déjà notifiés au chef (évite le rappel quotidien)
        self._contracts_resumed = False  # garde : reprise des minuteries de contrat une seule fois au démarrage

    async def cog_load(self):
        # Tâche planifiée UNIQUE (présence des chefs + expiration des locations + cycle hebdomadaire
        # taxe/salaires + verrous), déterministe et idempotente.
        if not self.ordre_scheduler.is_running():
            self.ordre_scheduler.start()

    async def cog_unload(self):
        self.ordre_scheduler.cancel()

    @commands.Cog.listener()
    async def on_ready(self):
        # Reprise UNE SEULE FOIS (on_ready peut se redéclencher lors des reconnexions) des minuteries de
        # proposition de contrat encore en attente après un redémarrage.
        if self._contracts_resumed:
            return
        self._contracts_resumed = True
        try:
            await self.resume_pending_contract_timeouts()
        except Exception as e:
            print(f"[ordre] resume_pending_contract_timeouts : {e}")

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

    async def _select_character_of(self, channel, invoker, target, none_msg,
                                   prompt="Sélectionne le personnage :"):
        chars = get_characters(target.id, channel.guild.id)
        if not chars:
            await channel.send(none_msg)
            return None
        if len(chars) == 1:
            return chars[0]["id"]
        view = OrdreCharacterSelectView(chars, invoker.id)
        await channel.send(prompt, view=view)
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
        # Staff : écran de choix (afficher son ordre / mode staff). Sinon : accès direct comme avant.
        if _is_staff(interaction.user):
            embed = discord.Embed(title="🏛️ Ordres", description="Que veux tu faire ?", color=PHOENIX_COLOR)
            await interaction.response.send_message(embed=embed, view=OrdreStaffChoiceView(interaction.user.id))
            return
        await interaction.response.send_message("🏛️ Ouverture des ordres…", ephemeral=True)
        await self._open_own_order(interaction.channel, interaction.user)

    async def _open_own_order(self, channel, user):
        """Flux « consultation de SON propre ordre » : sélection de personnage puis dashboard (ou
        proposition de création). Partagé entre la commande /ordre et le bouton « 👤 Afficher mon ordre »."""
        character_id = await self._select_character(channel, user)
        if character_id is None:
            return
        if get_account(character_id) is None:
            await channel.send(
                "Ce personnage n'a pas encore de compte bancaire, crée en un via /banque d'abord."
            )
            return
        order = db.get_order_by_chief(character_id)
        if order:
            await self._send_dashboard(channel, order, user.id)
        else:
            embed = discord.Embed(
                title="🏛️ Aucun ordre",
                description="Tu n'as pas encore d'ordre. Veux tu en créer un ?",
                color=PHOENIX_COLOR,
            )
            await channel.send(embed=embed, view=OrdreCreateConfirmView(character_id, user.id))

    # =================================================================
    # LISTENER
    # =================================================================
    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        if interaction.type != discord.InteractionType.component:
            return
        cid = interaction.data.get("custom_id", "")
        if cid.startswith("ordre_self:"):
            await self.handle_ordre_self(interaction, cid)
        elif cid.startswith("ordre_staff_mode:"):
            await self.handle_ordre_staff_mode(interaction, cid)
        elif cid.startswith("ordre_create_yes:"):
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
        elif cid.startswith("ordre_staff_manage:"):
            await self.handle_staff_manage(interaction, cid)
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
        elif cid.startswith("ordre_contrat_create:"):
            await self.handle_contrat_create(interaction, cid)
        elif cid.startswith("ordre_contrat_accept:"):
            await self.handle_contrat_accept(interaction, cid)
        elif cid.startswith("ordre_contrat_refuse:"):
            await self.handle_contrat_refuse(interaction, cid)
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
        elif cid.startswith("ordre_salext_cancel:"):
            await self.handle_salext_cancel(interaction, cid)
        elif cid.startswith("ordre_salext_renew:"):
            await self.handle_salext_renew(interaction, cid)

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
        # Un personnage ne peut diriger qu'un seul ordre à la fois (vérifié AVANT le ban de création).
        existing_order = db.get_order_by_chief(character_id)
        if existing_order:
            await interaction.response.edit_message(view=None)
            await interaction.channel.send(
                f"❌ Ce personnage est déjà chef de l'ordre {existing_order['name']}. Un personnage ne "
                "peut diriger qu'un seul ordre à la fois.")
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
            ca_total = self._order_ca_total(order_id)  # somme des parts éducateur des contrats actifs
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
        """Pillow éducatif : (nom_educateur, [(nom_disciple, ordre_employeur, revenu_str), ...]) à partir
        des contrats ACTIFS sourcés par cet ordre. Un Formateur SANS contrat apparaît quand même (vide)."""
        by_educ = {}
        for c in db.get_active_contracts_for_source(order_id):
            by_educ.setdefault(c["educator_character_id"], []).append(c)
        result = []
        for m in db.get_order_members(order_id):
            if m["role_label"] != "Formateur":
                continue
            disciples = []
            for c in by_educ.get(m["character_id"], []):
                employer = db.get_order(c["employer_order_id"])
                emp_name = employer["name"] if employer else "?"
                # Le % ne s'applique QUE sur salaire_fixe, jamais sur une prime/bonus, à respecter dans
                # le futur système de versement des salaires.
                montant_reverse = round(c["salaire_fixe"] * c["pct"] / 100)
                disciples.append((self._char_name(c["disciple_character_id"]), emp_name,
                                  f"{_fmt(montant_reverse)} ¥"))
            result.append((m["character_name"] or "?", disciples))
        return result

    def _order_ca_total(self, order_id):
        """Somme des montants reversés (part éducateur) des contrats actifs sourcés par cet ordre éducatif."""
        return sum(round(c["salaire_fixe"] * c["pct"] / 100)
                   for c in db.get_active_contracts_for_source(order_id))

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
        result = []
        for c in db.get_active_contracts_for_employer(order_id):
            source = db.get_order(c["source_order_id"])
            src_name = source["name"] if source else "?"
            # Le % ne s'applique QUE sur salaire_fixe, jamais sur une prime/bonus, à respecter dans le
            # futur système de versement des salaires.
            montant_reverse = round(c["salaire_fixe"] * c["pct"] / 100)
            result.append((self._char_name(c["disciple_character_id"]), src_name,
                           self._char_name(c["educator_character_id"]), f"{_fmt(montant_reverse)} ¥"))
        return result

    async def _send_contrats_direct(self, channel, order_id, user_id, page):
        order = db.get_order(order_id)
        contrats = self._contrats_direct_data(order_id)
        path = _tmp("cdir")
        path, total_pages = generate_contrats_direct_image(order["name"], contrats, page, path)
        # La View (bouton « ➕ Créer un contrat » + pagination si besoin) est TOUJOURS présente.
        view = ContratsDirectPageView(order_id, user_id, max(1, min(page, total_pages)), total_pages)
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
        view = ContratsDirectPageView(order_id, user_id, clamped, total_pages)
        await interaction.response.edit_message(
            attachments=[discord.File(path, filename="contrats.png")], view=view)
        _rm(path)

    # =================================================================
    # CRÉATION D'UN CONTRAT (bouton "➕ Créer un contrat", ordres Direct/Hybride)
    # =================================================================
    async def _ask_int_range(self, channel, user, prompt, lo, hi):
        if prompt:
            await channel.send(prompt)
        while True:
            m = await self.wait_message(channel, user)
            if m is None:
                await channel.send("⏳ Annulé.")
                return None
            v = _parse_int(m.content)
            if v is None or v < lo or v > hi:
                await channel.send(f"Entre un nombre entre {lo} et {hi}.")
                continue
            return v

    async def _pick_from_list(self, channel, user, options, prompt, numbered=False):
        """Choix dans une liste [(label, value_str), ...]. Menu déroulant si <10 éléments (sauf
        `numbered=True` qui force la liste numérotée), sinon liste numérotée. Retourne la value (str)
        choisie ou None."""
        if not options:
            return None
        if not numbered and len(options) < 10:
            view = SimpleSelectView("Fais ton choix...", options, user.id)
            await channel.send(prompt, view=view)
            await view.wait()
            return view.result
        lines = [f"**{i}.** {lbl}" for i, (lbl, _) in enumerate(options, 1)]
        await channel.send(prompt + "\n" + "\n".join(lines) + "\n\nRéponds par le **numéro**.")
        idx = await self._ask_int_range(channel, user, None, 1, len(options))
        if idx is None:
            return None
        return options[idx - 1][1]

    async def _pick_formateur_numbered(self, channel, user, guild, formateurs):
        """Sélection numérotée d'un formateur : « 1. {prénom} ({mention}) ». Retourne son character_id."""
        options, lines = [], []
        for f in formateurs:
            prenom = (f["character_name"] or "?").split()[0] if f["character_name"] else "?"
            m = guild.get_member(f["user_id"]) if guild else None
            mention = m.mention if m else f"<@{f['user_id']}>"
            options.append((f"{prenom} ({mention})", str(f["character_id"])))
            lines.append(None)
        val = await self._pick_from_list(
            channel, user, options, "Quel éducateur (formateur) de cet ordre éducatif ?", numbered=True)
        return int(val) if val is not None else None

    async def _ask_contract_params(self, channel, user):
        """Demande durée (déterminée/indéterminée + unité), % (10-40) et salaire fixe pour UN contrat.
        Retourne un dict ou None si annulé."""
        # Durée déterminée / indéterminée.
        dv = TwoChoiceView(user.id, "Déterminée", "determine", "Indéterminée", "indetermine",
                           a_style=discord.ButtonStyle.primary, b_style=discord.ButtonStyle.secondary)
        await channel.send("Durée du contrat : déterminée ou indéterminée ?", view=dv)
        await dv.wait()
        if dv.result is None:
            await channel.send("⏳ Annulé.")
            return None
        duree_type, duree_value, duree_unit = dv.result, None, None
        if duree_type == "determine":
            duree_value = await self._ask_positive_int(channel, user, "Durée : combien ?")
            if duree_value is None:
                return None
            uv = ButtonChoiceView(user.id, CONTRAT_UNITS)
            await channel.send("Unité de la durée ?", view=uv)
            await uv.wait()
            if uv.result is None:
                await channel.send("⏳ Annulé.")
                return None
            duree_unit = uv.result
        # % reversé à l'éducateur (10 à 40 inclus).
        pct = await self._ask_int_range(
            channel, user, "% reversé à l'éducateur (entre 10 et 40 inclus) ?", 10, 40)
        if pct is None:
            return None
        # Salaire fixe hebdomadaire.
        salaire = await self._ask_positive_int(channel, user, "Salaire fixe hebdomadaire ?")
        if salaire is None:
            return None
        return {"duree_type": duree_type, "duree_value": duree_value, "duree_unit": duree_unit,
                "pct": pct, "salaire_fixe": salaire}

    def _contract_duree_str(self, c):
        if c["duree_type"] == "determine":
            return f"{c['duree_value']} {CONTRAT_UNIT_LABEL.get(c['duree_unit'], c['duree_unit'])}"
        return "indéterminée"

    def _contract_recap_embed(self, employer_name, edu_order_name, educ_name, batch):
        lines = []
        for i, c in enumerate(batch, 1):
            montant = round(c["salaire_fixe"] * c["pct"] / 100)
            lines.append(
                f"**{i}.** {self._char_name(c['disciple_cid'])} — durée : {self._contract_duree_str(c)} — "
                f"{c['pct']}% — salaire {_fmt(c['salaire_fixe'])} ¥/sem "
                f"(part éducateur ≈ {_fmt(montant)} ¥)")
        return discord.Embed(
            title="📄 Récapitulatif du/des contrat(s)",
            description=(f"Éducateur : **{educ_name}** (ordre **{edu_order_name}**)\n"
                         f"Employeur : **{employer_name}**\n\n" + "\n".join(lines)),
            color=PHOENIX_COLOR)

    async def _contract_recap_loop(self, channel, user, employer_name, edu_order_name, educ_name, batch):
        """Affiche le récap, gère « ✅ Tout est bon » / « ✏️ Modifier » (anti double-clic via TwoChoiceView).
        Retourne True si confirmé, False sinon."""
        while True:
            tv = TwoChoiceView(user.id, "✅ Tout est bon", "ok", "✏️ Modifier", "edit",
                               a_style=discord.ButtonStyle.success, b_style=discord.ButtonStyle.secondary)
            await channel.send(
                embed=self._contract_recap_embed(employer_name, edu_order_name, educ_name, batch), view=tv)
            await tv.wait()
            if tv.result != "edit":
                return tv.result == "ok"
            # Modification d'une ligne.
            line = await self._ask_int_range(
                channel, user, f"Quelle ligne veux-tu modifier ? (1 à {len(batch)})", 1, len(batch))
            if line is None:
                continue
            field = await self._await_choice(channel, user, ("duree", "pct", "salaire"))
            if field is None:
                continue
            c = batch[line - 1]
            if field == "duree":
                dv = TwoChoiceView(user.id, "Déterminée", "determine", "Indéterminée", "indetermine",
                                   a_style=discord.ButtonStyle.primary, b_style=discord.ButtonStyle.secondary)
                await channel.send("Nouvelle durée : déterminée ou indéterminée ?", view=dv)
                await dv.wait()
                if dv.result is None:
                    continue
                if dv.result == "determine":
                    val = await self._ask_positive_int(channel, user, "Durée : combien ?")
                    if val is None:
                        continue
                    uv = ButtonChoiceView(user.id, CONTRAT_UNITS)
                    await channel.send("Unité de la durée ?", view=uv)
                    await uv.wait()
                    if uv.result is None:
                        continue
                    c["duree_type"], c["duree_value"], c["duree_unit"] = "determine", val, uv.result
                else:
                    c["duree_type"], c["duree_value"], c["duree_unit"] = "indetermine", None, None
            elif field == "pct":
                v = await self._ask_int_range(channel, user, "Nouveau % (10 à 40) ?", 10, 40)
                if v is None:
                    continue
                c["pct"] = v
            elif field == "salaire":
                v = await self._ask_positive_int(channel, user, "Nouveau salaire fixe hebdomadaire ?")
                if v is None:
                    continue
                c["salaire_fixe"] = v

    async def handle_contrat_create(self, interaction, cid):
        _, order_id, user_id = cid.split(":")
        order_id, user_id = int(order_id), int(user_id)
        # Garde chef + verrou EN PREMIER (engager un futur salaire pendant l'insolvabilité n'a pas de sens).
        if not self._is_chief(order_id, interaction.user.id):
            await interaction.response.send_message("Seul le chef de l'ordre peut faire ça.", ephemeral=True)
            return
        order = db.get_order(order_id)
        if order["type"] not in ("direct", "hybride"):
            await interaction.response.send_message(
                "Seuls les ordres Direct/Hybride emploient des disciples sous contrat.", ephemeral=True)
            return
        if order["security_lock"]:
            await interaction.response.send_message(
                "🔒 Ce compte est verrouillé suite à une trésorerie négative prolongée, impossible de "
                "créer un contrat (futur salaire) pour l'instant.", ephemeral=True)
            return
        if not self._acquire(interaction.user.id):
            await interaction.response.send_message(
                "Tu as déjà une action en cours, termine la d'abord.", ephemeral=True)
            return
        try:
            await interaction.response.send_message("➕ Création de contrat(s)…", ephemeral=True)
            channel, user, guild = interaction.channel, interaction.user, interaction.channel.guild

            # 1) Ordre éducatif.
            educatifs = db.get_orders_in_guild_of_types(guild.id, ("educatif",))
            if not educatifs:
                await channel.send("Aucun ordre éducatif n'existe sur le serveur.")
                return
            edu_val = await self._pick_from_list(
                channel, user, [(o["name"], str(o["id"])) for o in educatifs],
                "Avec quel ordre éducatif veux-tu contractualiser ?")
            if edu_val is None:
                await channel.send("⏳ Annulé.")
                return
            edu_order_id = int(edu_val)
            edu_order = db.get_order(edu_order_id)

            # 2) Éducateur (formateur), liste numérotée.
            formateurs = db.get_order_members_by_role(edu_order_id, "Formateur")
            if not formateurs:
                await channel.send("Cet ordre éducatif n'a aucun formateur.")
                return
            educator_cid = await self._pick_formateur_numbered(channel, user, guild, formateurs)
            if educator_cid is None:
                await channel.send("⏳ Annulé.")
                return

            # 3) Nombre de disciples à contractualiser.
            disciples = db.get_disciples_of_educator(edu_order_id, educator_cid)
            if not disciples:
                await channel.send("Cet éducateur n'a aucun disciple assigné.")
                return
            n = await self._ask_positive_int(
                channel, user,
                f"Combien de disciples de cet éducateur veux-tu mettre sous contrat ? (max {len(disciples)})",
                maximum=len(disciples))
            if n is None:
                return

            # 4-5) Choix des disciples (sans doublon) + paramètres de chaque contrat.
            remaining = [(self._char_name(d["disciple_character_id"]), str(d["disciple_character_id"]))
                         for d in disciples]
            batch = []
            for i in range(n):
                disc_val = await self._pick_from_list(
                    channel, user, remaining, f"Disciple {i + 1}/{n} — lequel mettre sous contrat ?")
                if disc_val is None:
                    await channel.send("⏳ Annulé.")
                    return
                remaining = [(lbl, v) for lbl, v in remaining if v != disc_val]
                params = await self._ask_contract_params(channel, user)
                if params is None:
                    return
                batch.append({"disciple_cid": int(disc_val), **params})

            # 6) Récapitulatif + validation.
            educ_name = self._char_name(educator_cid)
            confirmed = await self._contract_recap_loop(
                channel, user, order["name"], edu_order["name"], educ_name, batch)
            if not confirmed:
                await channel.send("Création annulée.")
                return

            # 7) INSERT du lot (status='pending').
            batch_id = uuid.uuid4().hex
            now = _now()
            for c in batch:
                db.create_contract(batch_id, c["disciple_cid"], educator_cid, edu_order_id, order_id,
                                   c["duree_type"], c["duree_value"], c["duree_unit"], c["pct"],
                                   c["salaire_fixe"], now)

            # 8) DM à l'éducateur avec accepter / refuser.
            await self._send_contract_offer(guild, batch_id, order["name"], edu_order["name"],
                                            educator_cid, batch)
            await channel.send(embed=discord.Embed(
                description="✅ Contrat(s) envoyé(s) à l'éducateur pour validation (en attente de sa réponse).",
                color=PHOENIX_COLOR))
        finally:
            self._release(interaction.user.id)

    async def _send_contract_offer(self, guild, batch_id, employer_name, edu_order_name, educator_cid, batch):
        """DM à l'éducateur : récap du lot + boutons Accepter / Refuser, PUIS lance un timer de 2 min qui
        expire la proposition si l'éducateur n'a pas répondu."""
        educ = get_character(educator_cid)
        if not educ:
            return
        embed = self._contract_recap_embed(employer_name, edu_order_name, self._char_name(educator_cid), batch)
        embed.title = "📄 Proposition de contrat(s)"
        embed.description = (f"L'ordre **{employer_name}** te propose le(s) contrat(s) suivant(s). "
                             "Acceptes-tu ? (⏱️ tu as **2 minutes** pour répondre)\n\n" + embed.description)
        uid = educ["user_id"]
        member = guild.get_member(uid) if guild else None
        if member is None:
            try:
                member = await self.bot.fetch_user(uid)
            except discord.HTTPException:
                return
        dm_message = None
        try:
            dm_message = await member.send(embed=embed, view=ContratOfferView(batch_id))
        except discord.HTTPException:
            pass
        # Timer de 2 minutes : tâche différée (référence conservée pour éviter le ramasse-miettes). Un
        # redémarrage pendant la fenêtre est rattrapé par resume_pending_contract_timeouts() (au démarrage).
        task = asyncio.create_task(self._contract_offer_timeout(batch_id, dm_message))
        self._contract_timeout_tasks.add(task)
        task.add_done_callback(self._contract_timeout_tasks.discard)

    async def _contract_offer_timeout(self, batch_id, dm_message, delay=120):
        """Attend `delay` secondes puis expire la proposition si elle est toujours en attente."""
        await asyncio.sleep(delay)
        await self._expire_contract_offer_if_pending(batch_id, dm_message)

    async def _expire_contract_offer_if_pending(self, batch_id, dm_message):
        """Si le lot est TOUJOURS 'pending' (statut relu en base, jamais mis en cache), l'expire, prévient
        les deux parties et retire les boutons du DM (si retrouvable). Si l'éducateur a déjà répondu
        (active / refused) entre-temps, ne fait rien."""
        # Ne pas écraser un traitement Accepter/Refuser en cours.
        if batch_id in self._contract_lock:
            return
        contracts = db.get_contracts_by_batch(batch_id)
        if not contracts or contracts[0]["status"] != "pending":
            return  # déjà accepté / refusé / expiré : rien à faire
        db.set_batch_expired(batch_id)
        educator_cid = contracts[0]["educator_character_id"]
        employer = db.get_order(contracts[0]["employer_order_id"])
        # Retire les boutons du DM d'origine et signale l'expiration dans le message même (si retrouvable).
        if dm_message is not None:
            try:
                await dm_message.edit(content="⏱️ Cette proposition a expiré.", view=None)
            except discord.HTTPException:
                pass
        # DM à l'éducateur (nouveau message, en plus de l'édition ci-dessus).
        await self._dm_character_owner(
            None, educator_cid,
            "⏱️ Le temps pour répondre à la proposition de contrat est écoulé, elle a été annulée.")
        # DM au chef employeur.
        if employer:
            await self._dm_character_owner(
                None, employer["chef_character_id"],
                "⏱️ L'éducateur n'a pas répondu à temps (2 minutes), la proposition de contrat a expiré. "
                "Tu peux recommencer si besoin.")

    async def resume_pending_contract_timeouts(self):
        """Au démarrage : rattrape les propositions encore 'pending' dont la minuterie de 2 min a été
        perdue par un redémarrage. Regroupe par lot ; pour chaque lot, si 120 s se sont déjà écoulées
        depuis le plus ancien created_at → expiration immédiate, sinon relance une tâche pour le temps
        restant. Le DM d'origine n'étant pas retrouvable après redémarrage, l'édition du message est
        simplement sautée (les DM d'expiration, eux, partent bien)."""
        rows = db.get_all_pending_contracts()
        by_batch = {}
        for r in rows:
            by_batch.setdefault(r["batch_id"], []).append(r)
        now_dt = datetime.fromisoformat(_now())
        for batch_id, crows in by_batch.items():
            oldest = min(c["created_at"] for c in crows if c["created_at"]) if any(
                c["created_at"] for c in crows) else None
            elapsed = (now_dt - datetime.fromisoformat(oldest)).total_seconds() if oldest else 120
            if elapsed >= 120:
                await self._expire_contract_offer_if_pending(batch_id, None)
            else:
                task = asyncio.create_task(
                    self._contract_offer_timeout(batch_id, None, delay=120 - elapsed))
                self._contract_timeout_tasks.add(task)
                task.add_done_callback(self._contract_timeout_tasks.discard)

    async def handle_contrat_refuse(self, interaction, cid):
        batch_id = cid.split(":", 1)[1]
        contracts = db.get_contracts_by_batch(batch_id)
        if not contracts:
            await interaction.response.send_message("Ce lot de contrats est introuvable.", ephemeral=True)
            return
        educ = get_character(contracts[0]["educator_character_id"])
        if not educ or educ["user_id"] != interaction.user.id:
            await interaction.response.send_message("Ce contrat ne t'est pas destiné.", ephemeral=True)
            return
        if contracts[0]["status"] != "pending":
            await interaction.response.send_message("Ce lot a déjà été traité.", ephemeral=True)
            return
        if batch_id in self._contract_lock:
            try:
                await interaction.response.defer()
            except discord.HTTPException:
                pass
            return
        self._contract_lock.add(batch_id)
        try:
            db.set_batch_refused(batch_id)
            await interaction.response.edit_message(
                content="❌ Tu as refusé ce(s) contrat(s).", embed=None, view=None)
            employer = db.get_order(contracts[0]["employer_order_id"])
            if employer:
                # DM en dehors d'un contexte serveur (le clic vient d'un MP) : guild=None -> fetch_user.
                await self._dm_character_owner(
                    None, employer["chef_character_id"],
                    f"❌ L'éducateur {self._char_name(contracts[0]['educator_character_id'])} a REFUSÉ "
                    "le(s) contrat(s) que tu lui as proposé(s).")
        finally:
            self._contract_lock.discard(batch_id)

    async def handle_contrat_accept(self, interaction, cid):
        batch_id = cid.split(":", 1)[1]
        contracts = db.get_contracts_by_batch(batch_id)
        if not contracts:
            await interaction.response.send_message("Ce lot de contrats est introuvable.", ephemeral=True)
            return
        educ = get_character(contracts[0]["educator_character_id"])
        if not educ or educ["user_id"] != interaction.user.id:
            await interaction.response.send_message("Ce contrat ne t'est pas destiné.", ephemeral=True)
            return
        if contracts[0]["status"] != "pending":
            await interaction.response.send_message("Ce lot a déjà été traité.", ephemeral=True)
            return
        if batch_id in self._contract_lock:
            try:
                await interaction.response.defer()
            except discord.HTTPException:
                pass
            return
        self._contract_lock.add(batch_id)
        try:
            # Vérifications RÉACTIVES au moment du clic (pas de nettoyage proactif à la dissolution pour les
            # 'pending', c'est voulu). NB : plus de contrôle « éducateur toujours Formateur » — la
            # réassignation automatique garantit que celui qui reçoit l'offre est déjà le bon.
            #
            # (2b) L'ordre employeur a été dissous entre-temps : tout le lot (même employeur) est annulé.
            employer = db.get_order(contracts[0]["employer_order_id"])
            if not employer:
                db.set_batch_cancelled(batch_id)
                await interaction.response.edit_message(
                    content="❌ Ce contrat a été annulé : l'ordre employeur a été dissous entre temps, "
                            "impossible d'accepter.",
                    embed=None, view=None)
                return
            now = _now()
            activated, missing = [], []
            for c in contracts:
                # (2c) Le disciple n'existe plus (parti / supprimé) : on annule ce contrat précis.
                if get_character(c["disciple_character_id"]) is None:
                    db.cancel_contract(c["id"])
                    missing.append(c)
                    continue
                end_date = None
                if c["duree_type"] == "determine":
                    days = (c["duree_value"] or 0) * CONTRAT_UNIT_DAYS.get(c["duree_unit"], 1)
                    end_date = (datetime.fromisoformat(now) + timedelta(days=days)).isoformat()
                db.activate_contract(c["id"], now, end_date)
                activated.append(c)

            # Aucun contrat activable : tous les disciples ont disparu → lot entièrement annulé.
            if not activated:
                await interaction.response.edit_message(
                    content="❌ Ce contrat a été annulé : le disciple n'existe plus.", embed=None, view=None)
                return

            note = (f"\n⚠️ {len(missing)} contrat(s) annulé(s) : le disciple n'existe plus." if missing else "")
            await interaction.response.edit_message(
                content="✅ Tu as accepté ce(s) contrat(s). Ils sont désormais actifs." + note,
                embed=None, view=None)
            # DM aux deux parties (l'éducateur qui vient d'accepter + le chef de l'ordre employeur).
            educ_name = self._char_name(contracts[0]["educator_character_id"])
            await self._dm_character_owner(
                None, employer["chef_character_id"],
                f"✅ L'éducateur {educ_name} a ACCEPTÉ le(s) contrat(s). Ils sont désormais actifs.")
            await self._dm_character_owner(
                None, contracts[0]["educator_character_id"],
                f"✅ Contrat(s) accepté(s) et actif(s). Tu percevras la part convenue tant qu'ils courent.")
        finally:
            self._contract_lock.discard(batch_id)

    async def _expire_contracts(self, guild):
        """Clôt (status='ended') les contrats à durée déterminée du serveur arrivés à échéance et
        prévient les deux parties. Filtré au serveur via l'ordre employeur (comme _expire_locations)."""
        for c in db.get_expired_determinate_contracts(_now()):
            ref = db.get_order(c["employer_order_id"]) or db.get_order(c["source_order_id"])
            chef = get_character(ref["chef_character_id"]) if ref else None
            if not chef or chef["guild_id"] != guild.id:
                continue  # autre serveur : traité lors de son itération
            db.end_contract(c["id"])
            disc, educ = self._char_name(c["disciple_character_id"]), self._char_name(c["educator_character_id"])
            msg = f"📋 Le contrat entre {disc} et {educ} est arrivé à son terme et a pris fin."
            await self._dm_character_owner(guild, c["disciple_character_id"], msg)
            await self._dm_character_owner(guild, c["educator_character_id"], msg)

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
        view = OrdreStaffView(order_id, user_id, max(1, min(page, total_pages)), total_pages,
                              is_educatif=(order["type"] == "educatif"))
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
        view = OrdreStaffView(order_id, user_id, clamped, total_pages,
                              is_educatif=(order["type"] == "educatif"))
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
            # Retrait complet (redistribution disciples + contrats + notifications), logique partagée.
            desc = await self._remove_member_full(order_id, target_cid, channel.guild)
            await channel.send(embed=discord.Embed(description=desc, color=PHOENIX_COLOR))
            await self._send_staff(channel, order_id, interaction.user.id, 1)
        finally:
            self._release(interaction.user.id)

    async def _remove_member_full(self, order_id, target_cid, guild):
        """Retrait COMPLET d'un membre d'un ordre — EXACTEMENT la logique du clic « ➖ Virer » (point
        d'entrée unique de départ d'éducateur/redistribution, clôture des contrats du disciple,
        notifications), puis retrait de order_members. Retourne le récapitulatif à afficher.
        Partagé entre le bouton « ➖ Virer » et le « 🔧 Mode staff » (Effectif retiré)."""
        name = get_character(target_cid)["character_name"] if get_character(target_cid) else "?"
        # Rôle lu AVANT le retrait (pour savoir si c'était un disciple « Membre d'équipe »).
        member_row = db.get_order_member(order_id, target_cid)
        fired_role = member_row["role_label"] if member_row else None
        # Conséquences côté ordre via le POINT D'ENTRÉE UNIQUE (redistribution + notifications), AVANT
        # de retirer le membre (les liens doivent encore exister).
        reassignments, fallback_used, aucun_formateur_restant = [], False, False
        edu_result = await self.handle_educator_departure_if_applicable(
            target_cid, guild, order_id_hint=order_id)
        if edu_result is not None:
            _, reassignments, fallback_used, aucun_formateur_restant = edu_result
        else:
            # Non-formateur : détache son rattachement de disciple.
            db.remove_disciple_assignment(order_id, target_cid)
            fired_order = db.get_order(order_id)
            if fired_order and fired_order["type"] == "educatif":
                # Disciple (« Membre d'équipe ») viré de son ordre éducatif d'origine : ses contrats
                # sourcés ici prennent fin (DM disciple + éducateur + chef employeur).
                if fired_role == "Membre d'équipe":
                    await self._end_disciple_source_contracts(
                        target_cid, order_id, fired_order["name"], guild)
            else:
                # Ordre employeur (Direct/Hybride) : clôture générique du contrat où il travaillait.
                await self.handle_disciple_departure_if_applicable(target_cid, guild)

        db.remove_order_member(order_id, target_cid)

        desc = f"✅ {name} a été retiré de l'ordre."
        if edu_result is not None and reassignments:
            nb_reassignes = sum(1 for _, e in reassignments if e is not None)
            nb_orphelins = sum(1 for _, e in reassignments if e is None)
            if nb_reassignes:
                desc += (f"\n🔁 {nb_reassignes} disciple(s) redistribué(s) parmi les autres formateurs"
                         + (" (répartition forcée)." if fallback_used else "."))
            if nb_orphelins:
                desc += (f"\n⚠️ {nb_orphelins} disciple(s) sans éducateur (aucun formateur restant), "
                         "le staff doit s'en occuper.")
        return desc

    # =================================================================
    # MODE STAFF /ordre (afficher son ordre / éditer n'importe quel ordre)
    # =================================================================
    async def handle_ordre_self(self, interaction, cid):
        user_id = int(cid.split(":")[1])
        if interaction.user.id != user_id:
            await interaction.response.send_message("Ce panneau ne t'appartient pas.", ephemeral=True)
            return
        await interaction.response.send_message("🏛️ Ouverture de ton ordre…", ephemeral=True)
        await self._open_own_order(interaction.channel, interaction.user)

    async def handle_ordre_staff_mode(self, interaction, cid):
        user_id = int(cid.split(":")[1])
        if interaction.user.id != user_id or not _is_staff(interaction.user):
            await interaction.response.send_message("Ce panneau ne t'appartient pas.", ephemeral=True)
            return
        if not self._acquire(interaction.user.id):
            await interaction.response.send_message(
                "Tu as déjà une action en cours, termine la d'abord.", ephemeral=True)
            return
        try:
            await interaction.response.send_message("🔧 Mode staff…", ephemeral=True)
            channel, user, guild = interaction.channel, interaction.user, interaction.channel.guild

            # 1-2) Liste numérotée de TOUS les ordres du serveur.
            orders = db.get_orders_in_guild_full(guild.id)
            if not orders:
                await channel.send("Aucun ordre n'existe pour l'instant.")
                return
            lines = []
            for i, o in enumerate(orders, 1):
                chef = get_character(o["chef_character_id"])
                prenom = (chef["character_name"] or "?").split()[0] if chef and chef["character_name"] else "?"
                lines.append(f"**{i}.** {o['name']} — dirigé par {prenom} (<@{o['owner_user_id']}>)")
            await channel.send(embed=discord.Embed(
                title="🔧 Mode staff — choisis un ordre",
                description="\n".join(lines) + "\n\nRéponds par le **numéro** de l'ordre à modifier.",
                color=PHOENIX_COLOR))
            idx = await self._ask_int_range(channel, user, None, 1, len(orders))
            if idx is None:
                await channel.send("⏳ Annulé.")
                return
            order_id = orders[idx - 1]["id"]
            await self._staff_edit_menu(channel, user, guild, order_id)
        finally:
            self._release(interaction.user.id)

    async def _staff_edit_menu(self, channel, user, guild, order_id):
        """Menu des catégories + boucle de N modifications, puis régénération du dashboard."""
        await channel.send(embed=discord.Embed(
            title="🔧 Modifications disponibles",
            description=("**PRINCIPAL :**\nNom, Effectif ajouter, Effectif retiré, Trésorerie ajouter, "
                         "Trésorerie retirer, Salon ajouté, Salon retiré, Salon modifié"),
            color=PHOENIX_COLOR))
        n = await self._ask_positive_int(channel, user, "Combien de modifications veux tu faire ?")
        if n is None:
            return
        for i in range(n):
            param = await self._staff_pick_param(channel, user, i + 1, n)
            if param is None:
                await channel.send("⏳ Modifications interrompues.")
                break
            await self._staff_apply_param(channel, user, guild, order_id, param)

        # 5) Régénération du dashboard à jour de l'ordre concerné.
        order = db.get_order(order_id)
        if order:
            await channel.send(embed=discord.Embed(
                description="✅ Terminé. Dashboard à jour ci dessous :", color=PHOENIX_COLOR))
            await self._send_dashboard(channel, order, user.id)

    async def _staff_pick_param(self, channel, user, i, n):
        """Demande le nom d'un paramètre (résolution par préfixe, accents ignorés). Redemande si inconnu
        ou ambigu. Retourne le nom canonique, ou None si annulé."""
        while True:
            await channel.send(
                f"Modification {i}/{n} — écris le nom du paramètre (ex : « Nom », « Effectif ajouter », "
                "« Trésorerie retirer »…).")
            m = await self.wait_message(channel, user)
            if m is None:
                await channel.send("⏳ Annulé.")
                return None
            matches = _match_staff_param(m.content)
            if len(matches) == 1:
                return matches[0]
            if not matches:
                await channel.send("Paramètre inconnu. Réessaie.")
            else:
                await channel.send("Ambigu (" + ", ".join(matches) + "). Précise davantage.")

    async def _staff_apply_param(self, channel, user, guild, order_id, param):
        if param == "Nom":
            await self._staff_edit_nom(channel, user, order_id)
        elif param == "Effectif ajouter":
            await self._staff_effectif_add(channel, user, guild, order_id)
        elif param == "Effectif retiré":
            await self._staff_effectif_remove(channel, user, guild, order_id)
        elif param == "Trésorerie ajouter":
            await self._staff_tresorerie(channel, user, order_id, sign=1)
        elif param == "Trésorerie retirer":
            await self._staff_tresorerie(channel, user, order_id, sign=-1)
        elif param == "Salon ajouté":
            await self._staff_salon_add(channel, user, order_id)
        elif param == "Salon retiré":
            await self._staff_salon_remove(channel, user, order_id)
        elif param == "Salon modifié":
            await self._staff_salon_modify(channel, user, order_id)

    async def _await_channel_mention(self, channel, user, prompt):
        """Attend un message mentionnant un salon (#salon) et retourne ce salon, ou None si annulé."""
        while True:
            await channel.send(prompt)
            m = await self.wait_message(channel, user)
            if m is None:
                await channel.send("⏳ Annulé.")
                return None
            if m.channel_mentions:
                return m.channel_mentions[0]
            await channel.send("Merci de **mentionner** un salon (ex : #salon).")

    # ---------- paramètres staff ----------
    async def _staff_edit_nom(self, channel, user, order_id):
        await channel.send("Quel est le nouveau nom ?")
        m = await self.wait_message(channel, user)
        if m is None:
            await channel.send("⏳ Annulé.")
            return
        new_name = m.content.strip()
        if not new_name:
            await channel.send("Nom vide, modification ignorée.")
            return
        full = f"Ordre de {new_name}"
        with db.get_connection() as conn:
            conn.execute("UPDATE orders SET name = ? WHERE id = ?", (full, order_id))
        await channel.send(embed=discord.Embed(
            description=f"✅ Ordre renommé en **{full}**.", color=PHOENIX_COLOR))

    async def _staff_tresorerie(self, channel, user, order_id, sign):
        verbe = "ajouter" if sign > 0 else "retirer"
        montant = await self._ask_positive_int(channel, user, f"Quel montant veux tu {verbe} ?")
        if montant is None:
            return
        old = db.get_order(order_id)["solde_courant"]
        new = db.adjust_order_solde(order_id, sign * montant)  # ADDITIF (peut passer en négatif si retrait)
        label = "Ajustement staff (ajout)" if sign > 0 else "Ajustement staff (retrait)"
        db.add_order_transaction(order_id, label, sign * montant, _now())
        await channel.send(embed=discord.Embed(
            description=(f"✅ Trésorerie : {verbe} **{_fmt(montant)} ¥**.\n"
                         f"Ancien solde : **{_fmt(old)} ¥**\nNouveau solde : **{_fmt(new)} ¥**"),
            color=PHOENIX_COLOR))

    async def _staff_salon_add(self, channel, user, order_id):
        ch = await self._await_channel_mention(channel, user, "Mentionne le salon à ajouter.")
        if ch is None:
            return
        owner = db.resolve_salon_true_owner(ch.id)
        if owner is not None:
            owner_order = db.get_order(owner["order_id"])
            await channel.send(
                f"❌ Ce salon appartient déjà à l'ordre {owner_order['name'] if owner_order else '?'}.")
            return
        # Correction de désynchronisation : ajout sans AUCUNE transaction financière.
        db.add_order_salon(order_id, ch.id, "Acheté")
        await channel.send(embed=discord.Embed(
            description=f"✅ Salon #{ch.name} ajouté à l'ordre (aucune transaction).", color=PHOENIX_COLOR))

    async def _staff_salon_remove(self, channel, user, order_id):
        ch = await self._await_channel_mention(channel, user, "Mentionne le salon à retirer.")
        if ch is None:
            return
        mine = next((r for r in db.get_all_salon_rows(ch.id) if r["order_id"] == order_id), None)
        if mine is None:
            await channel.send("❌ Ce salon n'appartient pas à cet ordre.")
            return
        with db.get_connection() as conn:
            conn.execute("DELETE FROM order_salons WHERE id = ?", (mine["id"],))
        await channel.send(embed=discord.Embed(
            description=f"✅ Salon #{ch.name} retiré de l'ordre (aucune transaction).", color=PHOENIX_COLOR))

    async def _staff_salon_modify(self, channel, user, order_id):
        old_ch = await self._await_channel_mention(
            channel, user, "Mentionne l'ANCIEN salon (celui actuellement enregistré, potentiellement mal renseigné).")
        if old_ch is None:
            return
        mine = next((r for r in db.get_all_salon_rows(old_ch.id) if r["order_id"] == order_id), None)
        if mine is None:
            await channel.send("❌ Cet ancien salon n'appartient pas à cet ordre.")
            return
        new_ch = await self._await_channel_mention(channel, user, "Mentionne le NOUVEAU salon (le bon).")
        if new_ch is None:
            return
        owner = db.resolve_salon_true_owner(new_ch.id)
        if owner is not None:
            owner_order = db.get_order(owner["order_id"])
            await channel.send(
                f"❌ Le nouveau salon appartient déjà à l'ordre {owner_order['name'] if owner_order else '?'}.")
            return
        # Transfert direct de la ligne : l'ancien salon redevient libre, le nouveau est possédé par cet ordre.
        with db.get_connection() as conn:
            conn.execute("UPDATE order_salons SET channel_id = ? WHERE id = ?", (new_ch.id, mine["id"]))
        await channel.send(embed=discord.Embed(
            description=f"✅ Salon corrigé : #{old_ch.name} → #{new_ch.name}.", color=PHOENIX_COLOR))

    async def _staff_effectif_remove(self, channel, user, guild, order_id):
        await channel.send("Écris le nom du personnage à retirer.")
        m = await self.wait_message(channel, user)
        if m is None:
            await channel.send("⏳ Annulé.")
            return
        query = _fold(m.content)
        if not query:
            await channel.send("Nom vide, annulé.")
            return
        members = db.get_order_members(order_id)
        exact = [mem for mem in members if _fold(mem["character_name"] or "") == query]
        cand = exact or [mem for mem in members if _fold(mem["character_name"] or "").startswith(query)]
        if not cand:
            await channel.send("Aucun membre de cet ordre ne correspond à ce nom.")
            return
        if len(cand) > 1:
            lines = [f"**{i}.** {mem['character_name']} ({mem['role_label']})" for i, mem in enumerate(cand, 1)]
            await channel.send(embed=discord.Embed(
                title="Plusieurs correspondances",
                description="\n".join(lines) + "\n\nRéponds par le **numéro**.", color=PHOENIX_COLOR))
            k = await self._ask_int_range(channel, user, None, 1, len(cand))
            if k is None:
                await channel.send("⏳ Annulé.")
                return
            target = cand[k - 1]
        else:
            target = cand[0]
        # RÉUTILISE EXACTEMENT la logique du clic « ➖ Virer ».
        desc = await self._remove_member_full(order_id, target["character_id"], guild)
        await channel.send(embed=discord.Embed(description=desc, color=PHOENIX_COLOR))

    async def _staff_effectif_add(self, channel, user, guild, order_id):
        # 1-2) Rôle numéroté (STAFF_ROLE_ORDER, couleurs déjà fixées ailleurs).
        lines = [f"**{i}.** {r}" for i, r in enumerate(STAFF_ROLE_ORDER, 1)]
        await channel.send(embed=discord.Embed(
            title="Quel rôle attribuer ?", description="\n".join(lines) + "\n\nRéponds par le **numéro**.",
            color=PHOENIX_COLOR))
        idx = await self._ask_int_range(channel, user, None, 1, len(STAFF_ROLE_ORDER))
        if idx is None:
            await channel.send("⏳ Annulé.")
            return
        role = STAFF_ROLE_ORDER[idx - 1]

        # 3) Mentions de toutes les personnes à ajouter, en un seul message.
        await channel.send("Mentionne toutes les personnes à ajouter avec ce rôle, en un seul message.")
        m = await self.wait_message(channel, user)
        if m is None:
            await channel.send("⏳ Annulé.")
            return
        people, seen = [], set()
        for u in m.mentions:  # ordre d'apparition préservé, dédoublonné
            if u.id not in seen:
                seen.add(u.id)
                people.append(u)
        if not people:
            await channel.send("Aucune personne mentionnée, ajout annulé.")
            return

        order = db.get_order(order_id)
        added = []
        for member in people:
            # 4) Sélection du personnage de CE joueur (auto si un seul), traitée AVANT la personne suivante.
            target_cid = await self._select_character_of(
                channel, user, member, f"{member.mention} n'a aucun personnage validé, ignoré.",
                prompt=f"Pour {member.mention}, sélectionne son personnage :")
            if target_cid is None:
                continue
            if target_cid == order["chef_character_id"] or db.get_order_member(order_id, target_cid):
                await channel.send(f"{self._char_name(target_cid)} fait déjà partie de l'ordre, ignoré.")
                continue
            educator_cid = None
            if role == "Membre d'équipe" and order["type"] == "educatif":
                educator_cid = await self._pick_educator(channel, user, order_id, guild)
                if educator_cid == "NO_EDUCATOR":
                    await channel.send(
                        "Aucun formateur dans cet ordre : impossible d'assigner ce disciple, "
                        f"{self._char_name(target_cid)} ignoré.")
                    continue
                if educator_cid is None:
                    await channel.send(f"Aucun éducateur choisi, {self._char_name(target_cid)} ignoré.")
                    continue
            db.add_order_member(order_id, target_cid, role)
            if educator_cid is not None:
                db.add_disciple_assignment(order_id, target_cid, educator_cid)
            added.append((target_cid, role, educator_cid))

        # 5) Récapitulatif.
        if not added:
            await channel.send("Personne n'a finalement été ajouté.")
            return
        recap = []
        for cid_, role_, educ_ in added:
            line = f"• {self._char_name(cid_)} — **{role_}**"
            if educ_ is not None:
                line += f" (rattaché à {self._char_name(educ_)})"
            recap.append(line)
        await channel.send(embed=discord.Embed(
            title="✅ Membres ajoutés", description="\n".join(recap), color=PHOENIX_COLOR))

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
            # Ancien rôle lu AVANT la mutation (pour détecter un départ DEPUIS « Membre d'équipe »).
            old_row = db.get_order_member(order_id, target_cid)
            was_disciple = old_row is not None and old_row["role_label"] == "Membre d'équipe"
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
                # Passer DEPUIS « Membre d'équipe » VERS un autre rôle dans un ordre éducatif = quitter son
                # statut de disciple : ses contrats sourcés par cet ordre prennent fin (DM disciple +
                # éducateur + chef employeur).
                if order["type"] == "educatif" and was_disciple:
                    await self._end_disciple_source_contracts(
                        target_cid, order_id, order["name"], channel.guild)

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

    # =================================================================
    # « 🎓 GÉRER LE STAFF » — gestion manuelle des disciples (ordres éducatifs)
    # =================================================================
    async def handle_staff_manage(self, interaction, cid):
        _, order_id, user_id = cid.split(":")
        order_id = int(order_id)
        # Chef uniquement + ordre éducatif uniquement (le bouton n'apparaît qu'ici, mais on revérifie).
        if not self._is_chief(order_id, interaction.user.id):
            await interaction.response.send_message("Seul le chef de l'ordre peut faire ça.", ephemeral=True)
            return
        order = db.get_order(order_id)
        if not order or order["type"] != "educatif":
            await interaction.response.send_message(
                "🎓 Ce menu n'existe que pour les ordres éducatifs.", ephemeral=True)
            return
        if not self._acquire(interaction.user.id):
            await interaction.response.send_message(
                "Tu as déjà une action en cours, termine la d'abord.", ephemeral=True)
            return
        try:
            await interaction.response.send_message("🎓 Gestion du staff…", ephemeral=True)
            channel, user, guild = interaction.channel, interaction.user, interaction.channel.guild
            # Menu principal NUMÉROTÉ (extensible : garder la structure même avec une seule option).
            options = [("Gérer les disciples", "disciples")]
            lines = [f"**{i}.** {lbl}" for i, (lbl, _) in enumerate(options, 1)]
            await channel.send(embed=discord.Embed(
                title="🎓 Gestion du staff",
                description="Que veux-tu faire ?\n\n" + "\n".join(lines) + "\n\nRéponds par le **numéro**.",
                color=PHOENIX_COLOR))
            idx = await self._ask_int_range(channel, user, None, 1, len(options))
            if idx is None:
                await channel.send("⏳ Annulé.")
                return
            if options[idx - 1][1] == "disciples":
                await self._manage_disciples_flow(channel, user, guild, order)
        finally:
            self._release(interaction.user.id)

    async def _manage_disciples_flow(self, channel, user, guild, order):
        """Option « Gérer les disciples » : choisir un disciple puis « 🔄 Changer d'éducateur » / « ❌ Virer »."""
        order_id = order["id"]
        disciples = db.get_disciples_of_order(order_id)
        if not disciples:
            await channel.send("Aucun disciple à gérer pour l'instant.")
            return
        options = [(self._char_name(d["disciple_character_id"]), str(d["disciple_character_id"]))
                   for d in disciples]
        # Menu déroulant si < 10, sinon liste numérotée (convention du cog, gérée par _pick_from_list).
        disc_val = await self._pick_from_list(channel, user, options, "Quel disciple veux-tu gérer ?")
        if disc_val is None:
            await channel.send("⏳ Annulé.")
            return
        disciple_cid = int(disc_val)
        disc_name = self._char_name(disciple_cid)

        av = TwoChoiceView(user.id, "🔄 Changer d'éducateur", "change", "❌ Virer", "fire",
                           a_style=discord.ButtonStyle.primary, b_style=discord.ButtonStyle.danger)
        await channel.send(
            embed=discord.Embed(description=f"Que veux-tu faire avec **{disc_name}** ?", color=PHOENIX_COLOR),
            view=av)
        await av.wait()
        if av.result is None:
            await channel.send("⏳ Annulé.")
            return
        if av.result == "fire":
            # Réutilise EXACTEMENT la mécanique du clic « ➖ Virer » (via la fonction partagée).
            await self._fire_disciple_from_educatif(order_id, disciple_cid, order["name"], guild)
            await channel.send(embed=discord.Embed(
                description=f"✅ {disc_name} a été retiré de l'ordre.", color=PHOENIX_COLOR))
            await self._send_staff(channel, order_id, user.id, 1)
        else:
            await self._change_disciple_educator_flow(channel, user, guild, order, disciple_cid)

    async def _change_disciple_educator_flow(self, channel, user, guild, order, disciple_cid):
        """« 🔄 Changer d'éducateur » : liste des formateurs + le chef (« … (Chef) »), moins l'éducateur
        actuel ; réassigne, transfère les contrats actifs, ré-oriente les propositions pending, notifie."""
        order_id = order["id"]
        disc_name = self._char_name(disciple_cid)
        old_educ = db.get_disciple_educator(order_id, disciple_cid)

        # Formateurs de l'ordre + le chef (référent possible), en retirant l'éducateur actuel.
        candidates = []  # (label, character_id)
        for f in db.get_order_members_by_role(order_id, "Formateur"):
            prenom = (f["character_name"] or "?").split()[0] if f["character_name"] else "?"
            candidates.append((prenom, f["character_id"]))
        chef_cid = order["chef_character_id"]
        chef_prenom = (self._char_name(chef_cid) or "?").split()[0]
        candidates.append((f"{chef_prenom} (Chef)", chef_cid))
        candidates = [(lbl, c) for lbl, c in candidates if c != old_educ]
        if not candidates:
            await channel.send("Aucun autre éducateur disponible vers qui réassigner ce disciple.")
            return

        options = [(lbl, str(c)) for lbl, c in candidates]
        new_val = await self._pick_from_list(
            channel, user, options, f"Vers quel éducateur veux-tu envoyer {disc_name} ?")
        if new_val is None:
            await channel.send("⏳ Annulé.")
            return
        new_educ = int(new_val)

        # b) Réassignation. c) Contrats actifs -> nouvel éducateur (helper existant).
        db.set_disciple_educator(order_id, disciple_cid, new_educ)
        db.transfer_active_contracts_educator(disciple_cid, old_educ, new_educ)

        # d) Propositions PENDING de ce disciple auprès de l'ancien éducateur : re-scindées vers le nouveau
        #    (même principe que _reassign_pending_contract_offers, ciblé sur ce disciple et le choix manuel).
        pending = db.get_pending_contracts_of_disciple_and_educator(disciple_cid, old_educ)
        had_pending = bool(pending)
        employer_ids = set()
        by_batch = {}
        for r in pending:
            by_batch.setdefault(r["batch_id"], []).append(r)
        for old_batch, rows in by_batch.items():
            new_batch_id = uuid.uuid4().hex
            db.rebatch_pending_contracts([r["id"] for r in rows], new_batch_id, new_educ)
            employer = db.get_order(rows[0]["employer_order_id"])
            edu_order = db.get_order(rows[0]["source_order_id"])
            employer_name = employer["name"] if employer else "?"
            edu_order_name = edu_order["name"] if edu_order else order["name"]
            batch = [{"disciple_cid": r["disciple_character_id"], "duree_type": r["duree_type"],
                      "duree_value": r["duree_value"], "duree_unit": r["duree_unit"],
                      "pct": r["pct"], "salaire_fixe": r["salaire_fixe"]} for r in rows]
            await self._send_contract_offer(guild, new_batch_id, employer_name, edu_order_name, new_educ, batch)
            employer_ids.add(rows[0]["employer_order_id"])

        # e) Notifications.
        active = db.get_active_contract_full_of_disciple(disciple_cid)
        if active:
            employer_ids.add(active["employer_order_id"])
        # Ancien éducateur.
        if old_educ is not None:
            await self._dm_character_owner(
                guild, old_educ,
                f"📋 Ton disciple {disc_name} a été réassigné à un autre éducateur par le chef de l'ordre.")
        # Nouvel éducateur (+ détails du contrat s'il existe).
        new_dm = f"📋 Tu as un nouveau disciple : {disc_name}."
        if active:
            new_dm += (f" Un contrat est actif : {active['pct']}% d'un salaire fixe de "
                       f"{_fmt(active['salaire_fixe'])} ¥/sem (durée {self._contract_duree_str(active)}).")
        elif had_pending:
            new_dm += " Une proposition de contrat vient de t'être transmise (à accepter ou refuser)."
        await self._dm_character_owner(guild, new_educ, new_dm)
        # Chef(s) de l'ordre employeur si un contrat actif OU pending existe.
        for emp_id in employer_ids:
            employer = db.get_order(emp_id)
            if employer:
                await self._dm_character_owner(
                    guild, employer["chef_character_id"],
                    f"📋 Le tuteur référent de {disc_name} a changé : "
                    f"{self._mention_of_character(guild, old_educ)} → "
                    f"{self._mention_of_character(guild, new_educ)}.")

        # f) Confirmation au chef.
        await channel.send(embed=discord.Embed(
            description=f"✅ {disc_name} est désormais rattaché à **{self._char_name(new_educ)}**.",
            color=PHOENIX_COLOR))
        await self._send_staff(channel, order_id, user.id, 1)

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
        redistribue quand même parmi tous (fallback). S'il ne reste AUCUN formateur, le CHEF de l'ordre
        éducatif devient le référent par défaut (plus de valeur NULL). Retourne
        (reassignments, fallback_used, aucun_formateur_restant), où
        reassignments = [(disciple_character_id, nouvel_educateur_id), ...] (jamais None : au pire le
        chef). aucun_formateur_restant=True signale le repli sur le chef (messages adaptés en aval)."""
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
            # Aucun formateur ne reste dans l'ordre : le CHEF de l'ordre reprend les disciples par défaut
            # (au lieu de les laisser orphelins avec educator_character_id = NULL), en attendant qu'un
            # nouveau formateur soit recruté.
            order = db.get_order(order_id)
            chef_cid = order["chef_character_id"] if order else None
            for row in disciples:
                db.set_assignment_educator(row["id"], chef_cid)
                # Les contrats actifs du disciple suivent aussi le référent vers le chef (cohérence :
                # le virement hebdomadaire lit educator_character_id au moment du paiement).
                db.transfer_active_contracts_educator(
                    row["disciple_character_id"], removed_educator_id, chef_cid)
                reassignments.append((row["disciple_character_id"], chef_cid))
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
        """DM à chaque disciple (et à son nouvel éducateur / au chef si repli), puis récap à OWNER_ID."""
        for disciple_cid, new_educ_cid in reassignments:
            disc_name = self._char_name(disciple_cid)
            if aucun_formateur_restant:
                # Aucun formateur restant : le chef de l'ordre reprend le disciple par défaut.
                await self._dm_character_owner(
                    guild, disciple_cid,
                    f"📋 Ton éducateur ({old_educ_name}) a quitté l'ordre {order_name}. En l'absence "
                    f"d'autre formateur disponible, tu es temporairement pris en charge directement par "
                    f"le chef de l'ordre, {self._mention_of_character(guild, new_educ_cid)}.")
                await self._dm_character_owner(
                    guild, new_educ_cid,
                    f"📋 Aucun formateur disponible pour reprendre {disc_name} suite au départ de "
                    f"{old_educ_name}. Tu es maintenant son référent par défaut, en attendant qu'un "
                    "nouveau formateur soit recruté.")
            else:
                new_name = self._char_name(new_educ_cid)
                await self._dm_character_owner(
                    guild, disciple_cid,
                    f"📋 Ton éducateur ({old_educ_name}) a quitté l'ordre {order_name}. Tu es maintenant "
                    f"sous la responsabilité de {new_name}.")
                await self._dm_character_owner(
                    guild, new_educ_cid,
                    f"📋 Tu as reçu un nouveau disciple suite au départ de {old_educ_name} : {disc_name}.")

        # Récap complet au propriétaire du bot pour vérification.
        lines = []
        for disciple_cid, new_educ_cid in reassignments:
            suffix = " (chef, par défaut)" if aucun_formateur_restant else ""
            lines.append(f"• {self._char_name(disciple_cid)} → {self._char_name(new_educ_cid)}{suffix}")
        recap = (f"📋 Redistribution des disciples suite au retrait de **{old_educ_name}** dans l'ordre "
                 f"**{order_name}** :\n" + "\n".join(lines))
        if fallback_used:
            recap += ("\n\n⚠️ fallback_used : aucun éducateur n'avait strictement moins de disciples, "
                      "répartition forcée parmi tous les formateurs restants.")
        if aucun_formateur_restant:
            recap += ("\n\n⚠️ aucun_formateur_restant : plus aucun formateur dans l'ordre, disciples "
                      "repris par défaut par le chef de l'ordre (à réassigner à un formateur dès qu'un "
                      "nouveau est recruté).")
        try:
            owner_user = await self.bot.fetch_user(OWNER_ID)
            await owner_user.send(recap)
        except discord.HTTPException:
            pass

    # =================================================================
    # DÉPART D'UN PERSONNAGE — CONSÉQUENCES CÔTÉ ORDRE (point d'entrée UNIQUE)
    # =================================================================
    # Ces méthodes centralisent TOUT ce qui doit arriver côté ordre quand un personnage part, quel que
    # soit le contexte (clic « ➖ Virer », départ du joueur du serveur, suppression manuelle via /depart).
    # Elles doivent être appelées AVANT la suppression effective du personnage, pendant que ses liens
    # (order_members, order_disciple_assignments, contrats) existent encore.
    def _mention_of_character(self, guild, character_id):
        """Mention Discord (<@id>) du propriétaire d'un personnage, sinon son nom."""
        if character_id is None:
            return "aucun (à réassigner)"
        c = get_character(character_id)
        if not c:
            return self._char_name(character_id)
        return f"<@{c['user_id']}>"

    async def handle_educator_departure_if_applicable(self, character_id, guild, order_id_hint=None):
        """POINT D'ENTRÉE UNIQUE du départ d'un ÉDUCATEUR. Si `character_id` est Formateur dans un ordre,
        redistribue ses disciples (algorithme équilibré déjà en place) et envoie toutes les notifications
        (redistribution + transfert/notif des contrats + DM au chef de l'ordre éducatif). Ne fait rien et
        retourne None si le personnage n'est pas formateur. Sinon retourne
        (order_id, reassignments, fallback_used, aucun_formateur_restant) pour l'appelant (ex : le recap
        du bouton Virer)."""
        order_id = None
        if order_id_hint is not None:
            row = db.get_order_member(order_id_hint, character_id)
            if row and row["role_label"] == "Formateur":
                order_id = order_id_hint
        if order_id is None:
            order_id = db.find_educator_order(character_id)
        if order_id is None:
            return None  # pas un formateur : rien à faire

        order = db.get_order(order_id)
        order_name = order["name"] if order else "?"
        old_name = self._char_name(character_id)
        # Contrats actifs de l'éducateur AVANT redistribution (défensif : [] tant que la table n'existe pas).
        contracts = db.get_active_contracts_of_educator(character_id)

        # Redistribution équilibrée des disciples (redirige aussi les contrats vers le nouvel éducateur).
        reassignments, fallback_used, aucun_formateur_restant = \
            await self.redistribute_disciples(order_id, character_id, guild)
        db.cleanup_educator_assignments(order_id, character_id, [d for d, _ in reassignments])

        # Notifications de redistribution (disciples + nouveaux éducateurs + récap OWNER_ID).
        if reassignments:
            await self._notify_redistribution(
                guild, order_name, old_name, reassignments, fallback_used, aucun_formateur_restant)
        # Notifications spécifiques aux CONTRATS ACTIFS (chef employeur + disciple), défensives.
        await self._notify_educator_contract_transfer(
            guild, order_name, old_name, reassignments, contracts)
        # Propositions ENCORE en attente (pending) : elles suivent aussi le disciple vers son nouvel
        # éducateur (ré-émission de l'offre Accepter/Refuser), au lieu d'être perdues. En l'absence de
        # formateur, le chef de l'ordre reçoit l'offre (chef_fallback).
        await self._reassign_pending_contract_offers(
            guild, order_name, old_name, character_id, reassignments,
            chef_fallback=aucun_formateur_restant)
        # Confirmation explicite au chef de l'ordre ÉDUCATIF (celui qui employait l'éducateur parti).
        if order:
            await self._dm_character_owner(
                guild, order["chef_character_id"],
                f"📋 L'éducateur {old_name} a quitté votre ordre {order_name}. Ses disciples ont été "
                "redistribués automatiquement.")
        return order_id, reassignments, fallback_used, aucun_formateur_restant

    async def _notify_educator_contract_transfer(self, guild, order_name, old_name, reassignments, contracts):
        """Pour chaque contrat actif de l'éducateur parti : prévient le chef de l'ordre employeur ET le
        disciple du nouveau référent (déterminé par la redistribution). Le transfert du champ
        educator_character_id est déjà fait par redistribute_disciples ; ici on ne fait QUE les DM.
        Défensif : `contracts` est vide tant que la table educator_contracts n'existe pas → no-op."""
        if not contracts:
            return
        new_by_disciple = {d: e for d, e in reassignments}
        for c in contracts:
            disciple_cid = c["disciple_character_id"]
            new_educ = new_by_disciple.get(disciple_cid)
            new_mention = self._mention_of_character(guild, new_educ)
            employer = db.get_order(c["employer_order_id"])
            if employer:
                await self._dm_character_owner(
                    guild, employer["chef_character_id"],
                    f"📋 L'éducateur {old_name} n'est plus dans l'ordre {order_name}. Le nouveau référent "
                    f"pour {self._char_name(disciple_cid)} est maintenant {new_mention}.")
            await self._dm_character_owner(
                guild, disciple_cid,
                f"📋 L'éducateur {old_name} n'est plus dans l'ordre {order_name}. Ton nouveau référent "
                f"est maintenant {new_mention}.")

    async def _reassign_pending_contract_offers(self, guild, order_name, old_educ_name, old_educ_cid,
                                                reassignments, chef_fallback=False):
        """Les propositions de contrat ENCORE 'pending' de l'éducateur parti suivent chaque disciple vers
        son nouvel éducateur (comme les contrats actifs), au lieu d'être annulées. Comme une proposition
        (lot) peut désormais concerner PLUSIEURS nouveaux éducateurs (redistribution différente selon les
        disciples), on re-scinde chaque lot par nouvel éducateur (nouveau batch_id), on renvoie un DM
        Accepter/Refuser au bon éducateur (fenêtre de 2 min ré-armée par _send_contract_offer) et on
        prévient le chef de l'ordre EMPLOYEUR.

        Quand `chef_fallback` est vrai (aucun formateur restant), la redistribution a mis le CHEF de
        l'ordre éducatif comme référent : ces disciples suivent donc le MÊME chemin de réémission (l'offre
        part vers le chef, qui reçoit le DM Accepter/Refuser comme n'importe quel éducateur), seul le texte
        d'information au chef employeur diffère. Plus aucune annulation ici : `db.cancel_contract` ne sert
        plus qu'aux vraies annulations réactives (2b ordre employeur dissous / 2c disciple disparu)."""
        pending = db.get_pending_contracts_of_educator(old_educ_cid)
        if not pending:
            return
        new_by_disciple = {d: e for d, e in reassignments}
        # Regroupe par (lot d'origine, nouvel éducateur) — que ce soit un vrai formateur ou le chef (repli).
        groups = {}  # (old_batch_id, new_educ) -> [rows] ; dict standard : ordre d'insertion garanti (3.7+)
        for r in pending:
            new_educ = new_by_disciple.get(r["disciple_character_id"])
            if new_educ is None:
                # Ne devrait plus arriver (le chef sert toujours de repli) : on ignore par prudence
                # plutôt que d'annuler une proposition qui pourrait encore être reprise.
                continue
            groups.setdefault((r["batch_id"], new_educ), []).append(r)

        for (old_batch_id, new_educ), rows in groups.items():
            new_batch_id = uuid.uuid4().hex
            db.rebatch_pending_contracts([r["id"] for r in rows], new_batch_id, new_educ)
            employer = db.get_order(rows[0]["employer_order_id"])
            edu_order = db.get_order(rows[0]["source_order_id"])
            employer_name = employer["name"] if employer else "?"
            edu_order_name = edu_order["name"] if edu_order else order_name
            # Reconstruit le récap (mêmes champs que la création) pour renvoyer l'offre au nouvel éducateur.
            batch = [{"disciple_cid": r["disciple_character_id"], "duree_type": r["duree_type"],
                      "duree_value": r["duree_value"], "duree_unit": r["duree_unit"],
                      "pct": r["pct"], "salaire_fixe": r["salaire_fixe"]} for r in rows]
            await self._send_contract_offer(
                guild, new_batch_id, employer_name, edu_order_name, new_educ, batch)
            if employer:
                noms = ", ".join(self._char_name(r["disciple_character_id"]) for r in rows)
                if chef_fallback:
                    msg = (f"📋 Aucun formateur n'était disponible pour reprendre {noms}, la proposition "
                           f"de contrat a été redirigée vers {self._mention_of_character(guild, new_educ)}.")
                else:
                    msg = (f"📋 L'éducateur initialement proposé pour le contrat de {noms} (lot "
                           f"{new_batch_id[:8]}) a quitté son ordre. La proposition est maintenant adressée "
                           f"à {self._mention_of_character(guild, new_educ)}.")
                await self._dm_character_owner(guild, employer["chef_character_id"], msg)

    async def handle_disciple_departure_if_applicable(self, character_id, guild):
        """Départ d'un DISCIPLE sous contrat actif : prévient l'éducateur et le chef de l'ordre employeur,
        puis clôt le contrat (status='ended'). Défensif : no-op tant que la table educator_contracts
        n'existe pas."""
        contract = db.get_active_contract_of_disciple(character_id)
        if not contract:
            return
        disc_name = self._char_name(character_id)
        educ_cid = contract["educator_character_id"]
        # DM à l'éducateur.
        await self._dm_character_owner(
            guild, educ_cid,
            f"📋 {disc_name} n'est plus disponible (a quitté / été renvoyé / personnage supprimé), le "
            "contrat qui vous liait a pris fin.")
        # DM au chef de l'ordre EMPLOYEUR.
        employer = db.get_order(contract["employer_order_id"])
        if employer:
            await self._dm_character_owner(
                guild, employer["chef_character_id"],
                f"📋 {disc_name} n'est plus dans votre ordre, son contrat avec {self._char_name(educ_cid)} "
                "a pris fin.")
        db.end_contract(contract["id"])

    async def _end_disciple_source_contracts(self, disciple_cid, source_order_id, source_order_name, guild):
        """Un disciple quitte son ordre éducatif d'ORIGINE (Virer / Muter hors « Membre d'équipe » /
        « 🎓 Gérer le staff ») : TOUS ses contrats ACTIFS sourcés par cet ordre prennent fin ET ses
        propositions ENCORE 'pending' sont annulées (elles n'ont plus lieu d'être). DM au disciple, à
        l'éducateur, et au chef de l'ordre EMPLOYEUR (potentiellement différent de l'ordre éducatif)."""
        disc_name = self._char_name(disciple_cid)
        for c in db.get_active_contracts_of_disciple_in_source(disciple_cid, source_order_id):
            db.end_contract(c["id"])
            educ_cid = c["educator_character_id"]
            educ_name = self._char_name(educ_cid)
            await self._dm_character_owner(
                guild, disciple_cid,
                f"📋 Tu as quitté l'ordre éducatif {source_order_name}, ton contrat avec {educ_name} a "
                "pris fin. Tu es libre de rejoindre un autre ordre.")
            await self._dm_character_owner(
                guild, educ_cid,
                f"📋 {disc_name} a quitté l'ordre {source_order_name}, le contrat qui vous liait a pris fin.")
            employer = db.get_order(c["employer_order_id"])
            if employer:
                await self._dm_character_owner(
                    guild, employer["chef_character_id"],
                    f"📋 {disc_name} n'est plus disciple de l'ordre éducatif {source_order_name}, son "
                    f"contrat avec {educ_name} a pris fin. Il peut rester dans votre ordre normalement, "
                    "mais sans contrat actif avec cet éducateur.")
        # Propositions encore en attente : annulées (le disciple quitte l'ordre source, l'offre est caduque).
        for c in db.get_pending_contracts_of_disciple_in_source(disciple_cid, source_order_id):
            db.cancel_contract(c["id"])
            educ_cid = c["educator_character_id"]
            await self._dm_character_owner(
                guild, educ_cid,
                f"📋 La proposition de contrat concernant {disc_name} est annulée : il a quitté l'ordre "
                f"éducatif {source_order_name}.")
            employer = db.get_order(c["employer_order_id"])
            if employer:
                await self._dm_character_owner(
                    guild, employer["chef_character_id"],
                    f"📋 La proposition de contrat pour {disc_name} est annulée : il a quitté l'ordre "
                    f"éducatif {source_order_name}.")

    async def _fire_disciple_from_educatif(self, order_id, disciple_cid, order_name, guild):
        """Retrait COMPLET d'un disciple d'un ordre éducatif — logique partagée entre le clic « ➖ Virer »
        classique et « 🎓 Gérer le staff » (pas de duplication : la clôture des contrats + notifications
        vit dans _end_disciple_source_contracts). Nettoie order_disciple_assignments, clôt ses contrats
        actifs ET pending sourcés par cet ordre, puis le retire de order_members s'il y figure comme
        « Membre d'équipe »."""
        db.remove_disciple_assignment(order_id, disciple_cid)
        await self._end_disciple_source_contracts(disciple_cid, order_id, order_name, guild)
        member_row = db.get_order_member(order_id, disciple_cid)
        if member_row and member_row["role_label"] == "Membre d'équipe":
            db.remove_order_member(order_id, disciple_cid)

    async def handle_order_departure(self, character_id, guild, order_id_hint=None):
        """Regroupe les conséquences côté ordre du départ d'un personnage (éducateur ET disciple), pour
        les points d'entrée qui n'ont pas besoin de la valeur de retour (départ du serveur, suppression
        manuelle). Le bouton Virer, lui, appelle handle_educator_departure_if_applicable directement pour
        récupérer le détail de la redistribution et l'afficher."""
        await self.handle_educator_departure_if_applicable(character_id, guild, order_id_hint)
        await self.handle_disciple_departure_if_applicable(character_id, guild)

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
            recap = []  # dicts : {cid, montant, updated, state, days}
            for character_id, montant in parsed:
                # 1) Appartenance de l'IBAN : le personnage est-il membre de l'ordre ?
                if db.get_order_member(order_id, character_id) is not None:
                    # 2) Membre : comportement inchangé (is_external=0, expiry=NULL).
                    existing = db.get_salary(order_id, character_id)
                    db.upsert_salary(order_id, character_id, montant, eff, now)
                    recap.append({"cid": character_id, "montant": montant,
                                  "updated": existing is not None, "state": "member", "days": None})
                    continue
                # 3) Externe : confirmation explicite (embed + boutons), puis durée 7-30 j si confirmé.
                char = get_character(character_id)
                name = char["character_name"] if char and char["character_name"] else "?"
                mention = f"<@{char['user_id']}>" if char else "?"
                acct = get_account(character_id)
                iban = acct["iban_courant"] if acct else "?"
                confirm = TwoChoiceView(
                    interaction.user.id, "✅ Confirmer", "yes", "❌ Refuser", "no",
                    a_style=discord.ButtonStyle.success, b_style=discord.ButtonStyle.danger)
                await channel.send(
                    embed=discord.Embed(
                        description=(f"⚠️ L'IBAN {iban} n'appartient pas à un membre de cet ordre "
                                     f"({name} — {mention}). Confirmer quand même ?"),
                        color=discord.Color.orange()),
                    view=confirm)
                await confirm.wait()
                if confirm.result != "yes":
                    # Refusé (ou délai) : cet IBAN est retiré du lot, les autres continuent.
                    recap.append({"cid": character_id, "montant": montant,
                                  "updated": None, "state": "refused", "days": None})
                    continue
                days = await self._ask_int_range(
                    channel, interaction.user, "Pour combien de jours ? (minimum 7, maximum 30)", 7, 30)
                if days is None:
                    recap.append({"cid": character_id, "montant": montant,
                                  "updated": None, "state": "refused", "days": None})
                    continue
                expiry = (datetime.fromisoformat(now) + timedelta(days=days)).isoformat()
                existing = db.get_salary(order_id, character_id)
                db.upsert_salary(order_id, character_id, montant, eff, now, is_external=1, expiry_date=expiry)
                recap.append({"cid": character_id, "montant": montant,
                              "updated": existing is not None, "state": "external", "days": days})

            lines = []
            for r in recap:
                nom = self._char_name(r["cid"])
                if r["state"] == "refused":
                    lines.append(f"• **{nom}** — ignoré (IBAN externe non confirmé)")
                    continue
                verbe = "mis à jour" if r["updated"] else "ajouté"
                if r["state"] == "external":
                    lines.append(f"• **{nom}** — {_fmt(r['montant'])} ¥ ({verbe}, ⚠️ externe {r['days']} j)")
                else:
                    lines.append(f"• **{nom}** — {_fmt(r['montant'])} ¥ ({verbe})")
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

    # ---------- boutons du DM d'expiration d'un salaire externe ----------
    async def handle_salext_cancel(self, interaction, cid):
        salary_id = int(cid.split(":")[1])
        row = db.get_salary_by_id(salary_id)
        if row is None:
            await interaction.response.edit_message(
                content="Ce salaire externe n'existe plus.", view=None)
            return
        if not self._is_chief(row["order_id"], interaction.user.id):
            await interaction.response.send_message("Ce panneau ne t'appartient pas.", ephemeral=True)
            return
        db.delete_salary_by_id(salary_id)
        self._ext_salary_prompt_pending.discard(salary_id)
        await interaction.response.edit_message(
            content=f"❌ Salaire externe de **{self._char_name(row['character_id'])}** retiré "
                    "définitivement. Tu devras le remettre manuellement si tu le souhaites.", view=None)

    async def handle_salext_renew(self, interaction, cid):
        salary_id = int(cid.split(":")[1])
        row = db.get_salary_by_id(salary_id)
        if row is None:
            await interaction.response.edit_message(
                content="Ce salaire externe n'existe plus.", view=None)
            return
        if not self._is_chief(row["order_id"], interaction.user.id):
            await interaction.response.send_message("Ce panneau ne t'appartient pas.", ephemeral=True)
            return
        if not self._acquire(interaction.user.id):
            await interaction.response.send_message(
                "Tu as déjà une action en cours, termine la d'abord.", ephemeral=True)
            return
        try:
            await interaction.response.edit_message(content="🔄 Renouvellement du salaire externe…", view=None)
            channel = interaction.channel  # DM : wait_message filtre déjà par auteur + salon
            days = await self._ask_int_range(
                channel, interaction.user, "Pour combien de jours ? (minimum 7, maximum 30)", 7, 30)
            if days is None:
                await channel.send("⏳ Renouvellement annulé.")
                return
            new_expiry = (datetime.fromisoformat(_now()) + timedelta(days=days)).isoformat()
            db.update_salary_expiry(salary_id, new_expiry)
            self._ext_salary_prompt_pending.discard(salary_id)
            await channel.send(embed=discord.Embed(
                description=f"✅ Salaire externe de **{self._char_name(row['character_id'])}** renouvelé "
                            f"pour **{days} jour(s)**.", color=PHOENIX_COLOR))
        finally:
            self._release(interaction.user.id)

    # =================================================================
    # TÂCHE PLANIFIÉE UNIQUE : présence des chefs + expiration + cycle hebdo + verrous
    # =================================================================
    @tasks.loop(minutes=15)
    async def ordre_scheduler(self):
        """Tâche unique remplaçant les deux anciennes boucles (salary_loop + check_chief_presence),
        déterministe et idempotente. Ne fait rien hors des 4 créneaux (0/6/12/18 h, heure de Paris) et
        du premier quart d'heure suivant chaque créneau :
        - à chaque créneau : dissolution des ordres dont le chef a quitté le serveur ;
        - à minuit uniquement : expiration des locations, puis (le lundi, une seule fois grâce à
          bot_state) taxe + salaires, puis vérification des verrous/échéances."""
        now_paris = datetime.now(PARIS_TZ)
        if now_paris.hour not in CHECK_HOURS or now_paris.minute >= 15:
            return

        for guild in self.bot.guilds:
            # Dissolution des ordres dont le chef a quitté (à chacun des 4 créneaux, comme avant).
            await self._dissolve_absent_chiefs(guild)
            # Présence des disciples sous contrat : à CHAQUE créneau (4-5x/jour), pas seulement à minuit.
            await self._check_contract_disciples_presence(guild)

            # Bloc de minuit uniquement.
            if now_paris.hour == 0:
                # 1) Expiration des locations D'ABORD (avant tout calcul de taxe/loyer) : un salon dont la
                #    location expire ce jour redevient 'Acheté' et sera donc taxé plutôt que loué.
                await self._expire_locations(guild)

                # 1bis) Salaires externes arrivés à terme : DM au chef (Annuler / Renouveler).
                await self._expire_external_salaries(guild)

                # 2) Le lundi, une SEULE fois par lundi : cycle hebdomadaire (taxe + salaires).
                #    Idempotence garantie par bot_state : un redémarrage le même lundi ne rejoue rien.
                #    Clé PAR SERVEUR (une clé globale sauterait les serveurs suivants une fois le premier
                #    traité, puisque ce bloc est dans la boucle des serveurs).
                if now_paris.weekday() == 0:
                    today_str = now_paris.date().isoformat()
                    state_key = f"last_weekly_orders_run:{guild.id}"
                    if db.get_bot_state(state_key) != today_str:
                        await self._charge_weekly_taxes(guild)
                        await self._pay_salaries(guild)
                        db.set_bot_state(state_key, today_str)

                # 3) Vérification quotidienne des verrous/échéances (peut dissoudre un ordre).
                await self._check_locks_and_deadlines(guild)

                # 4) Expiration des contrats à durée déterminée arrivés à échéance.
                await self._expire_contracts(guild)

    @ordre_scheduler.before_loop
    async def _before_ordre_scheduler(self):
        await self.bot.wait_until_ready()

    # ---------- Sous-étapes du planificateur (toutes PAR SERVEUR) ----------
    async def _dissolve_absent_chiefs(self, guild):
        """Dissout les ordres du serveur dont le chef a quitté le serveur Discord."""
        for order in db.get_orders_in_guild_full(guild.id):
            owner_user_id = order["owner_user_id"]
            member = guild.get_member(owner_user_id)
            if member is None:
                try:
                    member = await guild.fetch_member(owner_user_id)
                except discord.NotFound:
                    member = None
                except discord.HTTPException:
                    continue  # erreur réseau temporaire : on retentera au prochain passage
            if member is None:
                print(f"🔍 [chef-absent] Le chef de l'ordre {order['name']} (id {order['id']}) a "
                      "quitté le serveur, procédure de dissolution lancée.")
                try:
                    await self.dissolve_order_chief_departed(order["id"], guild)
                except Exception as e:  # un ordre en erreur ne doit pas bloquer les autres
                    print(f"[ordre_scheduler] échec dissolution ordre {order['id']} : {e}")

    async def _check_contract_disciples_presence(self, guild):
        """Filet de sécurité (4-5x/jour) : clôt les contrats actifs dont le disciple n'est plus présent
        dans son ordre (viré/parti sans passer par les flux déjà gérés).

        NB DÉVIATION vs libellé de la consigne : dans ce projet, un disciple sous contrat n'est JAMAIS
        ajouté à order_members de l'ordre EMPLOYEUR (il reste « Membre d'équipe » de son ordre ÉDUCATIF
        d'origine = source_order_id ; l'acceptation d'un contrat n'insère aucun membre côté employeur).
        Vérifier l'appartenance à l'employeur clôturerait donc TOUS les contrats. On vérifie l'appartenance
        à l'ordre source (là où le disciple existe réellement) ; les DM référencent cet ordre."""
        for c in db.get_all_active_contracts():
            # Filtrage par serveur via l'ordre employeur (comme _expire_contracts), fallback sur la source.
            ref = db.get_order(c["employer_order_id"]) or db.get_order(c["source_order_id"])
            chef = get_character(ref["chef_character_id"]) if ref else None
            if not chef or chef["guild_id"] != guild.id:
                continue  # autre serveur : traité lors de son itération
            if db.get_order_member(c["source_order_id"], c["disciple_character_id"]) is not None:
                continue  # toujours membre de son ordre d'origine : contrat maintenu
            db.end_contract(c["id"])
            source = db.get_order(c["source_order_id"])
            source_name = source["name"] if source else "?"
            disc_name = self._char_name(c["disciple_character_id"])
            msg = (f"📋 Le contrat de {disc_name} a pris fin : il n'est plus membre de l'ordre "
                   f"{source_name}.")
            await self._dm_character_owner(guild, c["disciple_character_id"], msg)
            await self._dm_character_owner(guild, c["educator_character_id"], msg)
            employer = db.get_order(c["employer_order_id"])
            if employer:
                await self._dm_character_owner(guild, employer["chef_character_id"], msg)

    async def _expire_external_salaries(self, guild):
        """Salaires externes (is_external=1) arrivés à terme : DM au chef de l'ordre avec « ❌ Annuler » /
        « 🔄 Renouveler ». Dédoublonnage en mémoire (_ext_salary_prompt_pending) pour ne pas re-notifier
        chaque nuit tant que le chef n'a pas tranché (remis à zéro à la reprise ou après action)."""
        for row in db.get_expired_external_salaries(_now()):
            order = db.get_order(row["order_id"])
            chef = get_character(order["chef_character_id"]) if order else None
            if not chef or chef["guild_id"] != guild.id:
                continue  # autre serveur
            if row["id"] in self._ext_salary_prompt_pending:
                continue  # déjà notifié, en attente de décision du chef
            char = get_character(row["character_id"])
            name = char["character_name"] if char and char["character_name"] else "?"
            pseudo = f"<@{char['user_id']}>" if char else "?"
            self._ext_salary_prompt_pending.add(row["id"])
            await self._dm_character_owner_view(
                guild, order["chef_character_id"],
                f"📋 Le salaire externe de {name} ({pseudo}) arrive à terme.",
                ExternalSalaryExpiryView(row["id"]))

    async def _dm_character_owner_view(self, guild, character_id, content, view):
        """Comme _dm_character_owner mais avec une View (boutons persistants). Silencieux en cas d'échec."""
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
            await member.send(content, view=view)
        except discord.HTTPException:
            pass

    async def _expire_locations(self, guild):
        """Résilie les locations DU SERVEUR arrivées à terme : le salon redevient 'Acheté' chez le
        propriétaire (de nouveau taxé), la ligne miroir disparaît chez le locataire, les deux chefs sont
        prévenus. Comparé à _now() (UTC, base de location_expiry) : on capture tout ce qui a expiré depuis
        le dernier passage quotidien."""
        for row in db.get_expired_locations(_now()):
            owner_order = db.get_order(row["order_id"])  # 'Location' = côté propriétaire
            if owner_order is None:
                continue
            chef = get_character(owner_order["chef_character_id"])
            if not chef or chef["guild_id"] != guild.id:
                continue  # appartient à un autre serveur : traité lors de l'itération de CE serveur
            tenant_id = row["linked_order_id"]  # côté locataire ('Louée')
            ch = guild.get_channel(row["channel_id"])
            name = ch.name if ch else str(row["channel_id"])

            # Le propriétaire récupère son salon en 'Acheté' ; le locataire perd sa ligne miroir.
            # IMPORTANT : le retour d'un salon à son propriétaire après expiration de location ne coûte
            # RIEN et ne génère AUCUNE transaction. Le propriétaire n'a jamais cessé de posséder ce salon,
            # il était juste temporairement loué. Aucun débit, aucun crédit, aucun INSERT dans
            # order_transactions à cet endroit.
            db.revert_location_to_bought(row["id"])
            if tenant_id:
                db.remove_order_salon_any(tenant_id, row["channel_id"])
                tenant_order = db.get_order(tenant_id)
                if tenant_order:
                    await self._dm_character_owner(
                        guild, tenant_order["chef_character_id"],
                        f"📋 La location du salon #{name} est arrivée à son terme, vous n'y avez plus accès.")
            await self._dm_character_owner(
                guild, owner_order["chef_character_id"],
                f"📋 La location du salon #{name} est arrivée à son terme, il redevient un salon "
                "'Acheté' normal chez vous et sera de nouveau taxé chaque semaine.")

    async def _charge_weekly_taxes(self, guild):
        """Taxe hebdomadaire + débit des loyers, pour tous les ordres Direct/Hybride du serveur."""
        for order in db.get_orders_in_guild_of_types(guild.id, ("direct", "hybride")):
            try:
                await self._charge_weekly_taxes_one(order["id"], guild)
            except Exception as e:  # un ordre en erreur ne doit pas bloquer les autres
                print(f"[ordre_scheduler] taxe ordre {order['id']} : {e}")

    async def _pay_salaries(self, guild):
        """Paie les salaires de tous les ordres Direct/Hybride du serveur. Date de référence = date de
        Paris (cohérente avec le créneau de minuit lundi qui déclenche le cycle ; utiliser utcnow() ici
        donnerait le dimanche, car à minuit à Paris on est encore la veille en UTC)."""
        today = datetime.now(PARIS_TZ).date()
        for order in db.get_orders_in_guild_of_types(guild.id, ("direct", "hybride")):
            try:
                await self._pay_salaries_one(order["id"], today, guild)
            except Exception as e:
                print(f"[ordre_scheduler] salaires ordre {order['id']} : {e}")

    async def _check_locks_and_deadlines(self, guild):
        """Vérifie les verrous et échéances de tous les ordres Direct/Hybride du serveur."""
        for order in db.get_orders_in_guild_of_types(guild.id, ("direct", "hybride")):
            try:
                await self._check_locks_and_deadlines_one(order["id"], guild)
            except Exception as e:
                print(f"[ordre_scheduler] verrous ordre {order['id']} : {e}")

    async def _charge_weekly_taxes_one(self, order_id, guild):
        """Le lundi : taxe des salons possédés ('Acheté' uniquement — les salons prêtés à un autre ordre
        en sont exemptés) + débit des loyers pour les salons que CET ordre loue à un autre.

        NB modèle de données : dans ce projet, le statut 'Location' est porté par le PROPRIÉTAIRE (salon
        loué À un autre ordre) et 'Louée' par le LOCATAIRE (salon loué DEPUIS un autre ordre). Le loyer
        est donc prélevé sur les lignes 'Louée' de cet ordre (locataire), crédité au propriétaire lié —
        l'inverse du schéma supposé dans l'instruction, adapté au modèle réel pour ne pas facturer à
        l'envers."""
        order = db.get_order(order_id)
        if order is None or order["security_lock"]:
            return  # verrouillé : aucune taxe ni loyer prélevés cette semaine (cohérent avec le blocage)

        # 2) Taxe des salons 'Acheté'.
        nb = db.count_order_salons(order_id, "Acheté")
        taxe = nb * TAXE_SALON
        if taxe > 0:
            db.adjust_order_solde(order_id, -taxe)
            db.add_order_transaction(order_id, f"Taxe hebdomadaire — {nb} salon(s)", -taxe, _now())

        # 3) Loyers : pour chaque salon que CET ordre loue (statut 'Louée'), il paie le propriétaire lié.
        # TODO : si l'ordre locataire est verrouillé, il est entièrement ignoré ci-dessus et ne paie pas
        # son loyer cette semaine ; le retard de loyer côté propriétaire n'est pas encore géré (à traiter
        # plus tard : accumulation de la dette, relance, ou résiliation automatique).
        for s in db.get_order_salons(order_id):
            if s["status"] != "Louée" or not s["linked_order_id"]:
                continue
            owner_id = s["linked_order_id"]
            ch = guild.get_channel(s["channel_id"]) if guild else None
            name = ch.name if ch else str(s["channel_id"])
            db.adjust_order_solde(order_id, -TAXE_SALON)
            db.add_order_transaction(order_id, f"Paiement location salon #{name}", -TAXE_SALON, _now())
            # Loyer reçu par le propriétaire (un dépôt entrant reste autorisé même s'il est verrouillé).
            db.adjust_order_solde(owner_id, TAXE_SALON)
            db.add_order_transaction(owner_id, f"Loyer reçu salon #{name}", TAXE_SALON, _now())

    async def _pay_salaries_one(self, order_id, today, guild):
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
            # Crédit du salarié via le point d'entrée unique (revenu -> compte pour l'épargne auto).
            credit_compte_courant(character_id, montant, "Salaire hebdomadaire", category="revenu")

    async def _check_locks_and_deadlines_one(self, order_id, guild):
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

    async def dissolve_order_chief_departed(self, order_id, guild):
        """Dissolution suite au départ CONFIRMÉ du chef du serveur (détecté par la tâche planifiée
        ordre_scheduler). Indemnité de 1 mois (montant x4), annonce à tous les membres
        AVANT toute suppression, suppression du personnage du chef lui même (sautée au moment du départ),
        et AUCUN bannissement (le chef est parti, il n'est pas fautif)."""
        await self._dissolve_common(
            order_id, guild, indemnity_weeks=4, ban_chief=False,
            tx_label="Indemnité de dissolution d'ordre (départ du chef)",
            member_text=("📢 L'ordre **{name}** a été dissous suite au départ de son chef du serveur. "
                         "Tu as été exclu de l'ordre."),
            chef_text=None,               # le chef a quitté le serveur : injoignable
            delete_chief_character=True)  # on supprime enfin son personnage (sauté à son départ)

    async def _dissolve_common(self, order_id, guild, *, indemnity_weeks, ban_chief,
                               tx_label, member_text, chef_text, delete_chief_character=False):
        """Logique commune de dissolution. IMPORTANT : les libérations croisées (salons/contrats liés à
        d'AUTRES ordres), les résolutions de noms, les indemnités et les annonces se font AVANT toute
        suppression, pendant que les données existent encore."""
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
                # Indemnité = vrai gain pour le joueur (pas un remboursement) -> category='revenu'.
                credit_compte_courant(cid, indemnite, tx_label, category="revenu")
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

        # 6) Suppression finale de l'ordre et de ses données propres. On la fait AVANT la suppression du
        # personnage du chef : ainsi delete_character_cascade ne voit plus d'ordre rattaché au chef et
        # n'émet pas son log trompeur « ordre pas supprimé automatiquement, à traiter manuellement ».
        # (Les indemnités et annonces des étapes 1-5 ont déjà eu lieu : rien n'est supprimé « avant »
        # d'avoir prévenu les membres.)
        db.delete_order_cascade(order_id)

        # 7) Suppression du personnage du chef (uniquement au départ du serveur : sautée à son départ
        # pour laisser le délai de la vérification programmée, effectuée seulement maintenant).
        if delete_chief_character:
            from cogs.depart import delete_character_cascade  # import local : évite tout cycle au chargement
            delete_character_cascade(chef_cid)
            with db.get_connection() as conn:
                conn.execute("DELETE FROM validated_characters WHERE id = ?", (chef_cid,))

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
        """Clôt (status='ended') les contrats actifs liés à l'ordre (employeur OU source) et prévient les
        deux parties (disciple + éducateur). Utilisé par les 3 chemins de dissolution via _dissolve_common.
        Colonnes réelles : disciple_character_id, educator_character_id, employer_order_id, source_order_id,
        status (cf. end_order_contracts en base)."""
        try:
            ended = db.end_order_contracts(order_id)
        except Exception:
            return
        for disciple_cid, educator_cid in ended:
            msg = (f"⚠️ Suite à la dissolution de l'ordre **{order_name}**, le contrat entre "
                   f"{self._char_name(disciple_cid)} et {self._char_name(educator_cid)} a pris fin "
                   "automatiquement.")
            await self._dm_character_owner(guild, disciple_cid, msg)
            await self._dm_character_owner(guild, educator_cid, msg)

    # =================================================================
    # SALONS — ACHAT (bouton "🏠 Avoir des salons" ET option "🛒 Acheter" du menu Salon)
    # =================================================================
    async def handle_salons_buy(self, interaction, cid):
        """Point d'entrée « 🏠 Avoir des salons » du dashboard : délègue au flux d'achat partagé (même
        aperçu de coût + confirmation + avertissement de découvert que l'option « 🛒 Acheter »)."""
        order_id = int(cid.split(":")[1])
        if not await self._require_chief(interaction, order_id):
            return
        if not self._acquire(interaction.user.id):
            await interaction.response.send_message(
                "Tu as déjà une action en cours, termine la d'abord.", ephemeral=True)
            return
        try:
            await interaction.response.send_message("🏠 Achat de salons…", ephemeral=True)
            await self._purchase_salons_flow(interaction.channel, interaction.user, order_id)
        finally:
            self._release(interaction.user.id)

    async def _purchase_salons_flow(self, channel, user, order_id):
        """Flux d'achat partagé : aperçu du coût, confirmation, avertissement de découvert (l'achat reste
        possible à découvert, cf. verrou de sécurité), collecte des salons avec gestion des conflits,
        puis débit du prix d'achat (15k/salon) — la taxe hebdomadaire ne démarrera qu'au prochain lundi.
        Suppose que le chef est déjà vérifié et le verrou de flux déjà acquis par l'appelant."""
        order = db.get_order(order_id)
        if order is None or order["type"] not in ("direct", "hybride"):
            await channel.send("Seuls les ordres Direct/Hybride peuvent posséder des salons.")
            return
        # Verrou de sécurité : achat = dépense, bloqué tant que le compte est verrouillé.
        if order["security_lock"]:
            await channel.send(
                "🔒 Ce compte est verrouillé suite à une trésorerie négative prolongée, aucune dépense "
                "n'est possible pour l'instant. Seuls les dépôts sont acceptés.")
            return

        # 1) Nombre de salons.
        n = await self._ask_positive_int(channel, user, "Combien de salons veux tu acheter ?")
        if n is None:
            return
        # 2-3) Aperçu du coût + confirmation.
        prix_achat = n * TAXE_SALON
        taxe_mensuelle = n * TAXE_SALON * 4
        confirm = TwoChoiceView(
            user.id, "✅ Confirmer", "confirm", "❌ Annuler", "cancel",
            a_style=discord.ButtonStyle.success, b_style=discord.ButtonStyle.danger)
        await channel.send(
            embed=discord.Embed(
                title="🛒 Récapitulatif de l'achat",
                description=(
                    f"Pour **{n}** salon(s) : **{_fmt(prix_achat)} ¥** d'achat immédiat, puis environ "
                    f"**{_fmt(taxe_mensuelle)} ¥/mois** de taxe ({_fmt(TAXE_SALON)} ¥/semaine/salon)."),
                color=PHOENIX_COLOR),
            view=confirm)
        await confirm.wait()
        if confirm.result != "confirm":
            await channel.send("Achat annulé.")
            return
        # 5a) Avertissement de découvert (revérif temps réel du solde).
        fresh = db.get_order(order_id)
        if fresh["solde_courant"] < prix_achat:
            warn = TwoChoiceView(
                user.id, "✅ Continuer", "continue", "❌ Annuler", "cancel",
                a_style=discord.ButtonStyle.danger, b_style=discord.ButtonStyle.secondary)
            await channel.send(
                embed=discord.Embed(
                    title="⚠️ Solde insuffisant",
                    description=(
                        f"Ton solde actuel ({_fmt(fresh['solde_courant'])} ¥) ne couvre pas entièrement "
                        f"cet achat ({_fmt(prix_achat)} ¥). L'achat reste possible, mais ta trésorerie "
                        "passera en négatif — tu auras alors 2 mois pour redresser la situation avant "
                        "blocage puis dissolution de l'ordre (voir le système de verrou déjà en place). "
                        "Continuer quand même ?"),
                    color=discord.Color.orange()),
                view=warn)
            await warn.wait()
            if warn.result != "continue":
                await channel.send("Achat annulé.")
                return
        # 5b-c) Collecte des salons avec système de conquête (avertissement par salon déjà possédé).
        channels = await self._collect_salons_conquest(channel, user, n, order_id)
        if channels is None:
            return
        if not channels:
            await channel.send("Aucun salon retenu.")
            return
        # 5d) Débit du prix d'achat + enregistrement (découvert autorisé : on ne s'interrompt plus).
        # Les salons conquis ont déjà été retirés de leur ancien ordre dans _collect_salons_conquest.
        for ch in channels:
            db.adjust_order_solde(order_id, -TAXE_SALON)
            db.add_order_salon(order_id, ch.id, "Acheté")
            db.add_order_transaction(order_id, f"Achat salon #{ch.name}", -TAXE_SALON, _now())
        # 5e) Récapitulatif final.
        recap = ", ".join(f"#{c.name}" for c in channels)
        await channel.send(embed=discord.Embed(
            description=f"✅ Salons achetés : {recap}.", color=PHOENIX_COLOR))
        await self._send_dashboard(channel, db.get_order(order_id), user.id)

    async def _collect_salons_conquest(self, channel, user, n, order_id):
        """Collecte n salons pour l'achat avec SYSTÈME DE CONQUÊTE (par salon, pas de blocage global) :
        - salon libre → retenu directement pour l'achat ;
        - salon déjà possédé (par cet ordre ou un autre, tout statut) → avertissement dédié à CE salon
          avec « ✅ Conquérir » / « ❌ Ignorer ce salon ». « Ignorer » le retire du lot, les autres
          continuent ; « Conquérir » le retire de son ordre actuel (nettoyage croisé des locations + DM
          aux chefs concernés) puis le retient pour l'achat.
        Retourne la liste des channels retenus (peut être vide), ou None si annulé (délai / mentions)."""
        await channel.send(f"Mentionne les {n} salons en une seule fois (ex : #salon1 #salon2 ...).")
        while True:
            m = await self.wait_message(channel, user)
            if m is None:
                await channel.send("⏳ Annulé.")
                return None
            mentions = m.channel_mentions
            if len(mentions) < n:
                await channel.send(
                    f"Il faut mentionner {n} salon(s), tu en as mentionné {len(mentions)}. Réessaie.")
                continue
            break
        # Dédoublonnage en conservant l'ordre de mention.
        batch, seen = [], set()
        for ch in mentions[:n]:
            if ch.id not in seen:
                seen.add(ch.id)
                batch.append(ch)

        buyer_order = db.get_order(order_id)
        buyer_name = buyer_order["name"] if buyer_order else "?"
        final = []
        for ch in batch:
            # Résolution SANS AMBIGUÏTÉ du vrai propriétaire : seule une ligne 'Acheté'/'Location' fait
            # foi (une 'Louée' n'est qu'un miroir chez l'emprunteur, jamais autoritaire).
            owner_row = db.resolve_salon_true_owner(ch.id)
            if owner_row is None:
                final.append(ch)  # aucun propriétaire réel : salon libre, achat direct sans avertissement
                continue
            rows = db.get_all_salon_rows(ch.id)  # toutes les lignes, pour le nettoyage croisé à la conquête
            owner_order = db.get_order(owner_row["order_id"])
            owner_name = owner_order["name"] if owner_order else "un autre ordre"
            view = TwoChoiceView(
                user.id, "✅ Conquérir", "conquer", "❌ Ignorer ce salon", "ignore",
                a_style=discord.ButtonStyle.danger, b_style=discord.ButtonStyle.secondary)
            await channel.send(
                embed=discord.Embed(
                    title="⚠️ Salon déjà possédé",
                    description=(
                        f"Le salon #{ch.name} appartient déjà à l'ordre **{owner_name}** "
                        f"(statut : {owner_row['status']}). L'acheter quand même le conquerra et le "
                        "retirera de cet ordre. Continuer ?"),
                    color=discord.Color.orange()),
                view=view)
            await view.wait()
            if view.result != "conquer":
                await channel.send(f"Salon #{ch.name} ignoré.")
                continue
            await self._conquer_salon(ch, rows, owner_row, order_id, buyer_name)
            final.append(ch)
        return final

    async def _conquer_salon(self, ch, rows, owner_row, buyer_order_id, buyer_name):
        """Retire un salon à son VRAI propriétaire (owner_row, résolu via resolve_salon_true_owner) avant
        de le réattribuer à l'acheteur : DM au propriétaire (perte de propriété) et, si le salon était en
        'Location' (prêté), rupture explicite du prêt (miroir 'Louée' supprimé + DM à l'emprunteur), puis
        suppression de TOUTES ses lignes (nettoyage croisé identique à dissolve_order())."""
        guild = getattr(ch, "guild", None)
        dmed = set()
        # 1) Vrai propriétaire : perte de propriété (sauf si c'est déjà l'acheteur).
        owner_oid = owner_row["order_id"]
        if owner_oid and owner_oid != buyer_order_id:
            owner_order = db.get_order(owner_oid)
            if owner_order:
                await self._dm_character_owner(
                    guild, owner_order["chef_character_id"],
                    f"⚠️ Le salon #{ch.name} de votre ordre a été conquis par l'ordre **{buyer_name}**.")
                dmed.add(owner_oid)
        # 2) Le vrai propriétaire prêtait ce salon ('Location') : le prêt est rompu par la conquête —
        #    on supprime la ligne miroir 'Louée' chez l'emprunteur et on le prévient.
        if owner_row["status"] == "Location" and owner_row["linked_order_id"]:
            tenant_id = owner_row["linked_order_id"]
            db.remove_order_salon_any(tenant_id, ch.id)  # supprime le miroir 'Louée'
            if tenant_id not in dmed and tenant_id != buyer_order_id:
                tenant = db.get_order(tenant_id)
                if tenant:
                    await self._dm_character_owner(
                        guild, tenant["chef_character_id"],
                        f"⚠️ Le salon #{ch.name} que vous aviez en location a été conquis par un autre "
                        "ordre, cette location a pris fin immédiatement.")
                    dmed.add(tenant_id)
        # 3) Filet de sécurité : toute autre contrepartie liée encore présente est prévenue une seule fois.
        for r in rows:
            linked_id = r["linked_order_id"]
            if r["status"] in ("Location", "Louée") and linked_id \
                    and linked_id not in dmed and linked_id != buyer_order_id:
                linked = db.get_order(linked_id)
                if linked:
                    verbe = "louiez" if r["status"] == "Location" else "aviez en location"
                    await self._dm_character_owner(
                        guild, linked["chef_character_id"],
                        f"⚠️ Le salon #{ch.name} que vous {verbe} a été conquis par un autre ordre, "
                        "cette location a pris fin.")
                    dmed.add(linked_id)
        # 4) Suppression de TOUTES les lignes du salon (chez tous les ordres concernés), y compris celle
        #    du vrai propriétaire (Acheté ou Location).
        db.delete_salon_everywhere(ch.id)

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
        if value not in ("acheter", "revendre", "louer"):
            await interaction.response.send_message("Action inconnue.", ephemeral=True)
            return
        if not self._acquire(interaction.user.id):
            await interaction.response.send_message(
                "Tu as déjà une action en cours, termine la d'abord.", ephemeral=True)
            return
        try:
            await interaction.response.defer()
            channel = interaction.channel
            if value == "acheter":
                await self._purchase_salons_flow(channel, interaction.user, order_id)
            elif value == "revendre":
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
        # Débit hebdomadaire du loyer : _charge_weekly_taxes (chaque lundi). Expiration automatique :
        # _expire_locations (chaque jour à minuit heure de Paris).
        await channel.send(embed=discord.Embed(
            description=f"✅ {len(channels)} salon(s) mis en location pour {weeks} semaine(s).",
            color=PHOENIX_COLOR))
        await self._send_dashboard(channel, db.get_order(order_id), user.id)


async def setup(bot):
    await bot.add_cog(Ordre(bot))
