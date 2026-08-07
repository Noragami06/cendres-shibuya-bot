# RÈGLES DE ROBUSTESSE PERMANENTES DU PROJET — voir cogs/shop.py (règles 1 à 6). En particulier :
# revérif du rôle/chef AU CLIC, isolation des flux textuels par utilisateur, revérif des soldes en
# temps réel juste avant débit, anti double-clic sur tout bouton de confirmation/exécution.

import asyncio
import os
import uuid
from datetime import datetime, timedelta

import discord
from discord import app_commands
from discord.ext import commands

from cogs.utils import database as db
from cogs.utils.image_gen import generate_ordre_image, generate_ordre_educatif_image
# Helpers bancaires déjà existants (personnages / comptes / couleur). apply_debit est une MÉTHODE
# du cog Banque (protection découvert + compte à rebours) : on la récupère via get_cog("Banque").
from cogs.banque import get_characters, get_character, get_account, PHOENIX_COLOR
# Méthode standard du projet : rôle appliqué à un PERSONNAGE (réel slot 1 / virtuel slot 2-3).
from cogs.profil import character_has_role

# ---------- Constantes ----------
WAIT_TIMEOUT = 300
DM_TIMEOUT = 300  # 5 minutes pour la négociation de revente à un joueur
SALONS_PER_PAGE = 8
MAX_DASHBOARD_SALONS = 6

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


class SalonListView(discord.ui.View):
    """Liste paginée des salons + menu d'action (revendre / louer). Les flèches paginent (sans stop),
    le menu fixe le résultat et arrête la vue (le flux verrouillé enchaîne alors)."""

    def __init__(self, owner_id, pages):
        super().__init__(timeout=WAIT_TIMEOUT)
        self.owner_id = owner_id
        self.pages = pages or ["Aucun salon pour l'instant."]
        self.page = 0
        self.result = None
        self._prev = discord.ui.Button(emoji="◀️", style=discord.ButtonStyle.secondary)
        self._next = discord.ui.Button(emoji="▶️", style=discord.ButtonStyle.secondary)
        self._prev.callback = self._go_prev
        self._next.callback = self._go_next
        self.add_item(self._prev)
        self.add_item(self._next)
        self._sel = discord.ui.Select(
            placeholder="Action sur les salons...", min_values=1, max_values=1,
            options=[
                discord.SelectOption(label="💸 Revendre un salon", value="revendre"),
                discord.SelectOption(label="🏠 Louer un salon", value="louer"),
            ],
        )
        self._sel.callback = self._sel_cb
        self.add_item(self._sel)
        self._refresh()

    def embed(self):
        return discord.Embed(
            title="🏘️ Salons de l'ordre", description=self.pages[self.page], color=PHOENIX_COLOR,
        ).set_footer(text=f"Page {self.page + 1}/{len(self.pages)}")

    def _refresh(self):
        self._prev.disabled = self.page <= 0
        self._next.disabled = self.page >= len(self.pages) - 1

    async def _guard(self, interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Ce menu ne t'appartient pas.", ephemeral=True)
            return False
        return True

    async def _go_prev(self, interaction):
        if not await self._guard(interaction):
            return
        self.page = max(0, self.page - 1)
        self._refresh()
        await interaction.response.edit_message(embed=self.embed(), view=self)

    async def _go_next(self, interaction):
        if not await self._guard(interaction):
            return
        self.page = min(len(self.pages) - 1, self.page + 1)
        self._refresh()
        await interaction.response.edit_message(embed=self.embed(), view=self)

    async def _sel_cb(self, interaction):
        if not await self._guard(interaction):
            return
        self.result = self._sel.values[0]
        await interaction.response.edit_message(view=None)
        self.stop()


# =====================================================================
# VUES PERSISTANTES (custom_id dynamiques -> listener on_interaction)
# =====================================================================
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


class OrdreStaffView(discord.ui.View):
    def __init__(self, order_id, user_id):
        super().__init__(timeout=None)
        self.add_item(discord.ui.Button(
            label="Ajouter", emoji="➕", style=discord.ButtonStyle.success,
            custom_id=f"ordre_staff_add:{order_id}:{user_id}"))
        self.add_item(discord.ui.Button(
            label="Virer", emoji="➖", style=discord.ButtonStyle.danger,
            custom_id=f"ordre_staff_fire:{order_id}:{user_id}"))
        self.add_item(discord.ui.Button(
            label="Muter", emoji="🔄", style=discord.ButtonStyle.primary,
            custom_id=f"ordre_staff_mute:{order_id}:{user_id}"))


# =====================================================================
# COG
# =====================================================================
class Ordre(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._active_users = set()   # isolation des flux textuels par joueur
        self._creating = set()       # anti double-clic sur la création (par character_id)

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
        elif cid.startswith("ordre_staff:"):
            await self.handle_staff(interaction, cid)
        elif cid.startswith("ordre_salons_buy:"):
            await self.handle_salons_buy(interaction, cid)
        elif cid.startswith("ordre_salon:"):
            await self.handle_salon(interaction, cid)
        elif cid.startswith("ordre_contrats_view:") or cid.startswith("ordre_contrat:") \
                or cid.startswith("ordre_tresorerie:"):
            await self.handle_placeholder(interaction, cid)

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
            order_id = db.create_order(character_id, type_, f"Ordre de {prenom}", now)
            db.add_order_transaction(order_id, "Frais de construction", -prix, now)
            await channel.send(embed=discord.Embed(
                description=f"✅ **{info['label']}** créé ! ({_fmt(prix)} ¥ débités)", color=PHOENIX_COLOR,
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
    # STAFF
    # =================================================================
    def _members_embed(self, order_id):
        order = db.get_order(order_id)
        chef = get_character(order["chef_character_id"])
        lines = [f"👑 **Chef d'ordre** — {chef['character_name'] if chef else '?'}"]
        for m in db.get_order_members(order_id):
            lines.append(f"• **{m['role_label']}** — {m['character_name'] or '?'}")
        if len(lines) == 1:
            lines.append("*(Aucun autre membre pour l'instant.)*")
        return discord.Embed(title="👥 Staff de l'ordre", description="\n".join(lines), color=PHOENIX_COLOR)

    async def handle_staff(self, interaction, cid):
        order_id = int(cid.split(":")[1])
        if not await self._require_staff_manager(interaction, order_id):
            return
        await interaction.response.send_message(
            embed=self._members_embed(order_id), view=OrdreStaffView(order_id, interaction.user.id)
        )

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
            db.add_order_member(order_id, target_cid, role)
            name = get_character(target_cid)["character_name"]
            await channel.send(embed=discord.Embed(
                description=f"✅ {name} ajouté à l'ordre comme **{role}**.", color=PHOENIX_COLOR))
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
            db.remove_order_member(order_id, target_cid)
            name = get_character(target_cid)["character_name"] if get_character(target_cid) else "?"
            await channel.send(embed=discord.Embed(
                description=f"✅ {name} a été retiré de l'ordre.", color=PHOENIX_COLOR))
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
            db.update_order_member_role(order_id, target_cid, role)
            name = get_character(target_cid)["character_name"] if get_character(target_cid) else "?"
            await channel.send(embed=discord.Embed(
                description=f"✅ {name} est désormais **{role}**.", color=PHOENIX_COLOR))
        finally:
            self._release(interaction.user.id)

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
    def _salon_pages(self, salons, guild):
        if not salons:
            return ["Aucun salon pour l'instant."]
        lines = []
        for s in salons:
            ch = guild.get_channel(s["channel_id"]) if guild else None
            name = ch.name if ch else str(s["channel_id"])
            extra = ""
            if s["status"] == "Location" and s["location_expiry"]:
                extra = f" (jusqu'au {s['location_expiry'][:10]})"
            lines.append(f"• #{name} — **{s['status']}**{extra}")
        return ["\n".join(lines[i:i + SALONS_PER_PAGE]) for i in range(0, len(lines), SALONS_PER_PAGE)]

    async def handle_salon(self, interaction, cid):
        order_id = int(cid.split(":")[1])
        if not await self._require_chief(interaction, order_id):
            return
        order = db.get_order(order_id)
        if order["type"] not in ("direct", "hybride"):
            await interaction.response.send_message(
                "Seuls les ordres Direct/Hybride gèrent des salons.", ephemeral=True)
            return
        if not self._acquire(interaction.user.id):
            await interaction.response.send_message(
                "Tu as déjà une action en cours, termine la d'abord.", ephemeral=True)
            return
        try:
            await interaction.response.send_message("🏘️ Gestion des salons…", ephemeral=True)
            channel = interaction.channel
            pages = self._salon_pages(db.get_order_salons(order_id), channel.guild)
            view = SalonListView(interaction.user.id, pages)
            await channel.send(embed=view.embed(), view=view)
            await view.wait()
            if view.result == "revendre":
                await self._salon_resell(channel, interaction.user, order_id)
            elif view.result == "louer":
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
