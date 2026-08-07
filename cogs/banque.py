import asyncio
import os
import random
import string
import uuid
from datetime import datetime, timedelta

import discord
from discord import app_commands
from discord.ext import commands, tasks

from cogs.utils import database as db
from cogs.utils.image_gen import generate_economie_image, generate_pin_image
# Réutilise la pagination de !informations pour l'historique complet des transactions.
from cogs.informations import paginate_content, PaginationView

# ---------- Constantes ----------
FICHE_STAFF_ROLE_ID = 1521229332075512039   # rôle staff, déjà utilisé ailleurs dans le bot
OWNER_ID = 396615332346855428               # reçoit une copie de chaque création de compte
PHOENIX_COLOR = discord.Color.from_rgb(255, 140, 40)

GRACE_PERIOD_DAYS = 21          # délai avant suppression d'un compte resté sous le seuil critique
FLOOR_BALANCE = -100000         # plancher absolu du solde courant

BANK_IMG_DIR = os.path.join(os.path.dirname(__file__), "..", "temp", "bank_images")
WAIT_TIMEOUT = 300  # secondes d'attente d'une réponse texte (virement / admin)


# ---------- Utilitaires IBAN / PIN ----------
def generate_iban() -> str:
    return "JA" + "".join(random.choices(string.digits, k=13))


def generate_unique_iban(cursor) -> str:
    while True:
        iban = generate_iban()
        cursor.execute(
            "SELECT 1 FROM bank_accounts WHERE iban_courant = ? OR iban_livret = ?", (iban, iban)
        )
        if not cursor.fetchone():
            return iban


def generate_pin_code() -> str:
    return "".join(random.choices(string.digits, k=4))


# ---------- Petits helpers ----------
def _now() -> str:
    return datetime.utcnow().isoformat()


def _fmt_date(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).strftime("%d/%m/%Y")
    except Exception:
        return iso or ""


def _within_1h(iso: str) -> bool:
    try:
        return (datetime.utcnow() - datetime.fromisoformat(iso)).total_seconds() < 3600
    except Exception:
        return False


def _amount_str(amount: int) -> str:
    return f"+{amount:,} ¥" if amount >= 0 else f"-{abs(amount):,} ¥"


def _tmp_path(prefix: str) -> str:
    os.makedirs(BANK_IMG_DIR, exist_ok=True)
    return os.path.join(BANK_IMG_DIR, f"{prefix}_{uuid.uuid4().hex}.png")


def _display_names(character_row) -> tuple:
    """Prénom + nom de clan pour l'image économie."""
    name = (character_row["character_name"] or "").strip()
    prenom = name.split()[0] if name else "—"
    clan = character_row["clan"]
    nom_clan = clan.capitalize() if clan and clan != "sans_clan" else ""
    return prenom, nom_clan


# ---------- Accès base de données ----------
def get_characters(user_id: int, guild_id: int):
    with db.get_connection() as conn:
        return conn.execute(
            "SELECT id, slot_number, character_name FROM validated_characters "
            "WHERE user_id = ? AND guild_id = ? ORDER BY slot_number",
            (user_id, guild_id),
        ).fetchall()


def get_character(character_id: int):
    with db.get_connection() as conn:
        return conn.execute(
            "SELECT * FROM validated_characters WHERE id = ?", (character_id,)
        ).fetchone()


def get_account(character_id: int):
    with db.get_connection() as conn:
        return conn.execute(
            "SELECT * FROM bank_accounts WHERE character_id = ?", (character_id,)
        ).fetchone()


def find_account_by_any_iban(iban: str):
    """Cherche un compte par IBAN courant OU livret. Retourne une ligne
    (character_id, user_id, type_compte='courant'|'livret') ou None."""
    with db.get_connection() as conn:
        return conn.execute(
            "SELECT character_id, user_id, 'courant' AS type_compte FROM bank_accounts WHERE iban_courant = ? "
            "UNION "
            "SELECT character_id, user_id, 'livret' AS type_compte FROM bank_accounts WHERE iban_livret = ?",
            (iban, iban),
        ).fetchone()


def credit_account(character_id: int, target: str, montant: int):
    """Crédite un solde (courant ou livret) d'un personnage."""
    col = "solde_courant" if target == "courant" else "solde_livret"
    with db.get_connection() as conn:
        conn.execute(
            f"UPDATE bank_accounts SET {col} = {col} + ? WHERE character_id = ?", (montant, character_id)
        )


def get_session(user_id: int, character_id: int):
    with db.get_connection() as conn:
        return conn.execute(
            "SELECT verified_at FROM bank_sessions WHERE user_id = ? AND character_id = ?",
            (user_id, character_id),
        ).fetchone()


def set_session(user_id: int, character_id: int):
    with db.get_connection() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO bank_sessions (user_id, character_id, verified_at) VALUES (?, ?, ?)",
            (user_id, character_id, _now()),
        )


def add_transaction(character_id: int, label: str, amount: int, related_iban=None):
    with db.get_connection() as conn:
        conn.execute(
            "INSERT INTO bank_transactions (character_id, label, amount, date, related_iban) "
            "VALUES (?, ?, ?, ?, ?)",
            (character_id, label, amount, _now(), related_iban),
        )


def get_recent_transactions(character_id: int, limit: int = 4):
    with db.get_connection() as conn:
        return conn.execute(
            "SELECT label, date, amount FROM bank_transactions WHERE character_id = ? "
            "ORDER BY date DESC LIMIT ?",
            (character_id, limit),
        ).fetchall()


def get_all_transactions(character_id: int):
    with db.get_connection() as conn:
        return conn.execute(
            "SELECT label, date, amount FROM bank_transactions WHERE character_id = ? ORDER BY date DESC",
            (character_id,),
        ).fetchall()


def get_argent_recompense(user_id: int) -> int:
    with db.get_connection() as conn:
        row = conn.execute(
            "SELECT argent_recompense FROM depart_character_progress WHERE user_id = ?", (user_id,)
        ).fetchone()
    return (row["argent_recompense"] or 0) if row else 0


def clear_argent_recompense(user_id: int):
    with db.get_connection() as conn:
        conn.execute(
            "UPDATE depart_character_progress SET argent_recompense = 0 WHERE user_id = ?", (user_id,)
        )


def create_account(character_id: int, user_id: int, guild_id: int, solde_courant: int):
    with db.get_connection() as conn:
        cur = conn.cursor()
        iban_c = generate_unique_iban(cur)
        iban_l = generate_unique_iban(cur)
        while iban_l == iban_c:  # évite (extrêmement improbable) deux IBAN identiques
            iban_l = generate_unique_iban(cur)
        pin = generate_pin_code()
        conn.execute(
            "INSERT INTO bank_accounts "
            "(character_id, user_id, guild_id, iban_courant, iban_livret, pin_code, "
            " solde_courant, solde_livret, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?)",
            (character_id, user_id, guild_id, iban_c, iban_l, pin, solde_courant, _now()),
        )
    return iban_c, iban_l, pin


def apply_balance_change(character_id: int, target: str, delta: int):
    col = "solde_courant" if target == "courant" else "solde_livret"
    with db.get_connection() as conn:
        conn.execute(
            f"UPDATE bank_accounts SET {col} = {col} + ? WHERE character_id = ?", (delta, character_id)
        )
    label = "Ajout manuel par le staff" if delta >= 0 else "Retrait manuel par le staff"
    add_transaction(character_id, label, delta)


# =====================================================================
# VUES PERSISTANTES (custom_id dynamiques -> gérées par le listener on_interaction)
# =====================================================================
class StaffChoiceView(discord.ui.View):
    def __init__(self, user_id: int):
        super().__init__(timeout=None)
        self.add_item(discord.ui.Button(
            label="Gérer mon compte", emoji="👤", style=discord.ButtonStyle.primary,
            custom_id=f"banque_self:{user_id}",
        ))
        self.add_item(discord.ui.Button(
            label="Gérer le compte d'un autre", emoji="🛠️", style=discord.ButtonStyle.secondary,
            custom_id=f"banque_other:{user_id}",
        ))


class CreateAccountView(discord.ui.View):
    def __init__(self, character_id: int, user_id: int):
        super().__init__(timeout=None)
        self.add_item(discord.ui.Button(
            label="Créer un compte", emoji="🏦", style=discord.ButtonStyle.success,
            custom_id=f"banque_create:{character_id}:{user_id}",
        ))


class PinOpenView(discord.ui.View):
    def __init__(self, character_id: int, user_id: int):
        super().__init__(timeout=None)
        self.add_item(discord.ui.Button(
            label="Entrer mon code", emoji="🔓", style=discord.ButtonStyle.primary,
            custom_id=f"banque_pin_open:{character_id}:{user_id}",
        ))


class PinErrorView(discord.ui.View):
    def __init__(self, character_id: int, user_id: int):
        super().__init__(timeout=None)
        self.add_item(discord.ui.Button(
            label="Réessayer", emoji="🔁", style=discord.ButtonStyle.danger,
            custom_id=f"banque_pin_retry:{character_id}:{user_id}",
        ))
        self.add_item(discord.ui.Button(
            label="Recevoir mon code", emoji="📩", style=discord.ButtonStyle.secondary,
            custom_id=f"banque_resend_pin:{character_id}:{user_id}",
        ))


class PinKeypadView(discord.ui.View):
    """Clavier numérique de saisie du code (persistant, custom_id dynamiques par personnage/joueur)."""

    def __init__(self, character_id: int, user_id: int):
        super().__init__(timeout=None)
        base = f"{character_id}:{user_id}"
        for row, digits in enumerate((["1", "2", "3"], ["4", "5", "6"], ["7", "8", "9"])):
            for dg in digits:
                self.add_item(discord.ui.Button(
                    label=dg, style=discord.ButtonStyle.secondary,
                    custom_id=f"banque_pin_digit:{base}:{dg}", row=row,
                ))
        self.add_item(discord.ui.Button(
            label="⌫", style=discord.ButtonStyle.danger,
            custom_id=f"banque_pin_clear:{base}", row=3,
        ))
        self.add_item(discord.ui.Button(
            label="0", style=discord.ButtonStyle.secondary,
            custom_id=f"banque_pin_digit:{base}:0", row=3,
        ))
        self.add_item(discord.ui.Button(
            label="✅", style=discord.ButtonStyle.success,
            custom_id=f"banque_pin_confirm:{base}", row=3,
        ))


def _pin_values(buffer: str) -> list:
    """Liste de 4 éléments : "*" pour chaque position saisie, "" pour les positions vides."""
    return ["*" if i < len(buffer) else "" for i in range(4)]


class AccountView(discord.ui.View):
    def __init__(self, character_id: int, owner_id: int):
        super().__init__(timeout=None)
        self.add_item(discord.ui.Button(
            label="IBAN", emoji="📇", style=discord.ButtonStyle.secondary,
            custom_id=f"banque_iban:{character_id}",
        ))
        self.add_item(discord.ui.Button(
            label="Virement", emoji="💸", style=discord.ButtonStyle.primary,
            custom_id=f"banque_virement:{character_id}:{owner_id}",
        ))
        self.add_item(discord.ui.Button(
            label="Informations", emoji="📜", style=discord.ButtonStyle.secondary,
            custom_id=f"banque_info:{character_id}",
        ))


# =====================================================================
# VUES / MODAL EN SESSION (callbacks internes, non persistants)
# =====================================================================
class CharacterSelect(discord.ui.Select):
    def __init__(self, cog, chars, target_user, is_admin_mode, invoker):
        self.cog = cog
        self.target_user = target_user
        self.is_admin_mode = is_admin_mode
        self.invoker = invoker
        options = [
            discord.SelectOption(label=f"Slot {c['slot_number']} — {c['character_name']}", value=str(c["id"]))
            for c in chars
        ]
        super().__init__(placeholder="Choisis un personnage...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.invoker.id:
            await interaction.response.send_message("Ce menu ne t'appartient pas.", ephemeral=True)
            return
        character_id = int(self.values[0])
        await interaction.response.defer()
        await self.cog.after_character_selected(
            interaction.channel, character_id, self.target_user, self.is_admin_mode, self.invoker
        )


class CharacterSelectView(discord.ui.View):
    def __init__(self, cog, chars, target_user, is_admin_mode, invoker):
        super().__init__(timeout=WAIT_TIMEOUT)
        self.add_item(CharacterSelect(cog, chars, target_user, is_admin_mode, invoker))


class AdminTargetView(discord.ui.View):
    """Choix du solde (courant / livret) pour une opération admin. En session, callbacks internes."""

    def __init__(self, cog, character_id: int, delta: int, staff_id: int):
        super().__init__(timeout=WAIT_TIMEOUT)
        self.cog = cog
        self.character_id = character_id
        self.delta = delta
        self.staff_id = staff_id

    async def _apply(self, interaction: discord.Interaction, target: str):
        if interaction.user.id != self.staff_id:
            await interaction.response.send_message("Ce n'est pas ton opération.", ephemeral=True)
            return

        verbe = "Ajout" if self.delta >= 0 else "Retrait"
        libelle = "compte courant" if target == "courant" else "Livret A"

        # Retrait (courant OU livret) : passe par apply_debit (protection découvert + compte à rebours).
        if self.delta < 0:
            add_transaction(self.character_id, "Retrait manuel par le staff", self.delta)
            await self.cog.apply_debit(self.character_id, abs(self.delta), interaction.guild, compte=target)
        else:
            apply_balance_change(self.character_id, target, self.delta)

        account = get_account(self.character_id)
        bal = account["solde_courant"] if target == "courant" else account["solde_livret"]
        msg = f"✅ {verbe} de {abs(self.delta):,} ¥ sur le {libelle}. Nouveau solde : {bal:,} ¥."
        if bal < 0:
            msg += "\n⚠️ Attention : le solde est désormais négatif."
        if account["is_at_risk"] and account["deletion_deadline"]:
            msg += (f"\n⏳ Compte sous surveillance : suppression prévue le "
                    f"{_fmt_date(account['deletion_deadline'])} sans redressement.")
        await interaction.response.edit_message(content=msg, view=None)

    @discord.ui.button(label="Compte courant", style=discord.ButtonStyle.primary, custom_id="banque_admin_target_courant")
    async def courant(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._apply(interaction, "courant")

    @discord.ui.button(label="Livret A", style=discord.ButtonStyle.secondary, custom_id="banque_admin_target_livret")
    async def livret(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._apply(interaction, "livret")


# =====================================================================
# COG
# =====================================================================
class Banque(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # Saisie du code en cours : clé composite (user_id, character_id) -> "chiffres tapés".
        self.pin_buffers = {}

    async def cog_load(self):
        # NOTE : les boutons du clavier ont un custom_id contenant character_id/user_id (inconnus
        # à l'avance), donc bot.add_view() ne peut pas les enregistrer génériquement. La persistance
        # réelle est assurée par le listener on_interaction ci-dessous (comme tous les boutons banque).
        if not self.deletion_loop.is_running():
            self.deletion_loop.start()

    async def cog_unload(self):
        self.deletion_loop.cancel()

    async def _resolve_member(self, guild, user_id):
        """Membre du serveur si en cache, sinon utilisateur récupéré via l'API (ou None)."""
        member = guild.get_member(user_id) if guild is not None else None
        if member is None:
            try:
                member = await self.bot.fetch_user(user_id)
            except discord.HTTPException:
                member = None
        return member

    # ---------- débit + gestion du compte à rebours de suppression ----------
    async def apply_debit(self, character_id, montant, guild, compte: str = "courant") -> int:
        """Retire 'montant' du solde indiqué (courant ou livret). Si compte='courant' et que le
        résultat est négatif, pioche dans le Livret A pour combler jusqu'à 0 (jamais plus). Plafonne
        le solde courant à FLOOR_BALANCE minimum. Lance ou annule le compte à rebours de suppression
        selon le nouveau solde. Retourne le solde_courant final."""
        colonne = "solde_courant" if compte == "courant" else "solde_livret"
        with db.get_connection() as conn:
            conn.execute(
                f"UPDATE bank_accounts SET {colonne} = {colonne} - ? WHERE character_id = ?",
                (montant, character_id),
            )
            row = conn.execute(
                "SELECT solde_courant, solde_livret, user_id, is_at_risk, deletion_deadline "
                "FROM bank_accounts WHERE character_id = ?",
                (character_id,),
            ).fetchone()
        if row is None:
            return 0
        solde_courant = row["solde_courant"]
        solde_livret = row["solde_livret"]
        owner_id = row["user_id"]
        is_at_risk = row["is_at_risk"]

        # Protection découvert : pioche dans le Livret A pour combler le courant jusqu'à 0 (pas plus).
        if compte == "courant" and solde_courant < 0:
            deficit = abs(solde_courant)
            secours = min(deficit, solde_livret)
            if secours > 0:
                with db.get_connection() as conn:
                    conn.execute(
                        "UPDATE bank_accounts SET solde_courant = solde_courant + ?, "
                        "solde_livret = solde_livret - ? WHERE character_id = ?",
                        (secours, secours, character_id),
                    )
                add_transaction(
                    character_id, "Transfert automatique du Livret A (protection découvert)", secours
                )
                solde_courant += secours
                solde_livret -= secours

        # Plancher absolu du solde courant.
        if solde_courant < FLOOR_BALANCE:
            with db.get_connection() as conn:
                conn.execute(
                    "UPDATE bank_accounts SET solde_courant = ? WHERE character_id = ?",
                    (FLOOR_BALANCE, character_id),
                )
            solde_courant = FLOOR_BALANCE

        # Compte à rebours de suppression.
        if solde_courant <= -100:
            if not is_at_risk:
                deadline = (datetime.utcnow() + timedelta(days=GRACE_PERIOD_DAYS)).isoformat()
                with db.get_connection() as conn:
                    conn.execute(
                        "UPDATE bank_accounts SET is_at_risk = 1, deletion_deadline = ? WHERE character_id = ?",
                        (deadline, character_id),
                    )
                member = await self._resolve_member(guild, owner_id)
                if member is not None:
                    try:
                        await member.send(
                            f"⚠️ Ton compte Banque Phénix est passé sous -100 ¥ (solde actuel : "
                            f"{solde_courant} ¥). Tu as jusqu'au {_fmt_date(deadline)} pour redresser "
                            "la situation, sans quoi ton compte sera supprimé définitivement."
                        )
                    except discord.Forbidden:
                        pass
            # Déjà à risque : on ne touche pas à deletion_deadline, le compte à rebours continue.
        else:
            if is_at_risk:
                with db.get_connection() as conn:
                    conn.execute(
                        "UPDATE bank_accounts SET is_at_risk = 0, deletion_deadline = NULL "
                        "WHERE character_id = ?",
                        (character_id,),
                    )
                member = await self._resolve_member(guild, owner_id)
                if member is not None:
                    try:
                        await member.send(
                            "✅ Ton compte Banque Phénix est repassé au dessus du seuil critique, "
                            "la suppression programmée est annulée."
                        )
                    except discord.Forbidden:
                        pass

        return solde_courant

    # ---------- tâche périodique : suppression des comptes en fin de délai ----------
    @tasks.loop(hours=1)
    async def deletion_loop(self):
        now = _now()
        with db.get_connection() as conn:
            rows = conn.execute(
                "SELECT character_id, user_id, guild_id, solde_courant FROM bank_accounts "
                "WHERE is_at_risk = 1 AND deletion_deadline IS NOT NULL AND deletion_deadline < ?",
                (now,),
            ).fetchall()

        for r in rows:
            character_id = r["character_id"]
            owner_id = r["user_id"]
            guild_id = r["guild_id"]
            solde = r["solde_courant"]

            char = get_character(character_id)
            char_name = char["character_name"] if char else "ce personnage"

            with db.get_connection() as conn:
                conn.execute("DELETE FROM bank_accounts WHERE character_id = ?", (character_id,))
                conn.execute("DELETE FROM bank_transactions WHERE character_id = ?", (character_id,))
                conn.execute("DELETE FROM bank_sessions WHERE character_id = ?", (character_id,))

            guild = self.bot.get_guild(guild_id) if guild_id else None
            member = await self._resolve_member(guild, owner_id)
            if member is not None:
                try:
                    await member.send(
                        f"🏦 Ton compte Banque Phénix pour {char_name} a été supprimé définitivement, "
                        "le solde étant resté négatif au delà du délai de 3 semaines."
                    )
                except discord.Forbidden:
                    pass

            owner_mention = getattr(member, "mention", f"<@{owner_id}>")
            try:
                owner_user = await self.bot.fetch_user(OWNER_ID)
                await owner_user.send(
                    f"⚠️ Le compte bancaire de {char_name} (appartenant à {owner_mention}) a été "
                    f"supprimé automatiquement pour insuffisance de solde prolongée (solde final : {solde} ¥)."
                )
            except discord.HTTPException:
                pass

    @deletion_loop.before_loop
    async def _before_deletion_loop(self):
        await self.bot.wait_until_ready()

    # ---------- attente d'une réponse texte dans le salon ----------
    async def wait_message(self, channel, author, timeout: int = WAIT_TIMEOUT):
        def check(m):
            return m.channel.id == channel.id and m.author.id == author.id and not m.author.bot
        try:
            return await self.bot.wait_for("message", check=check, timeout=timeout)
        except asyncio.TimeoutError:
            return None

    # ---------- commande ----------
    @app_commands.command(name="banque", description="Accède à ton compte bancaire")
    async def banque(self, interaction: discord.Interaction):
        is_staff = any(role.id == FICHE_STAFF_ROLE_ID for role in getattr(interaction.user, "roles", []))
        if is_staff:
            embed = discord.Embed(
                title="🏦 Banque Phénix",
                description="Que souhaites tu faire ?",
                color=PHOENIX_COLOR,
            )
            await interaction.response.send_message(embed=embed, view=StaffChoiceView(interaction.user.id))
        else:
            await interaction.response.send_message("🏦 Accès à la Banque Phénix…", ephemeral=True)
            await self.select_character_flow(
                interaction.channel, interaction.user, is_admin_mode=False, invoker=interaction.user
            )

    # ---------- sélection de personnage ----------
    async def select_character_flow(self, channel, target_user, is_admin_mode: bool, invoker):
        chars = get_characters(target_user.id, channel.guild.id)
        if not chars:
            await channel.send(
                "Ce joueur n'a aucun personnage validé." if is_admin_mode
                else "Tu n'as aucun personnage validé."
            )
            return
        if len(chars) == 1:
            await self.after_character_selected(channel, chars[0]["id"], target_user, is_admin_mode, invoker)
            return
        await channel.send(
            "Sélectionne le personnage concerné :",
            view=CharacterSelectView(self, chars, target_user, is_admin_mode, invoker),
        )

    async def after_character_selected(self, channel, character_id, target_user, is_admin_mode, invoker):
        if is_admin_mode:
            await self.enter_admin(channel, character_id, invoker)
        else:
            await self.enter_normal(channel, character_id, target_user)

    # ---------- mode normal : compte / création / PIN ----------
    async def enter_normal(self, channel, character_id, member):
        account = get_account(character_id)
        if not account:
            embed = discord.Embed(
                description="Tu n'as pas encore de compte pour ce personnage.",
                color=PHOENIX_COLOR,
            )
            await channel.send(embed=embed, view=CreateAccountView(character_id, member.id))
            return

        session = get_session(member.id, character_id)
        if session and _within_1h(session["verified_at"]):
            await self.show_account_screen(channel, character_id)
            return

        await self.send_pin_screen(channel, character_id, member.id)

    async def send_pin_screen(self, channel, character_id, user_id):
        char = get_character(character_id)
        path = _tmp_path("pin")
        generate_pin_image(char["portrait_path"] if char else None, ["", "", "", ""], path)
        await channel.send(
            content="🔒 Vérifie ton identité pour accéder à ton compte.",
            file=discord.File(path, filename="pin.png"),
            view=PinOpenView(character_id, user_id),
        )
        try:
            os.remove(path)
        except OSError:
            pass

    # ---------- rendu d'images ----------
    def _render_pin(self, character_id, buffer: str) -> str:
        """Génère l'image du clavier PIN reflétant le buffer courant, retourne le chemin temp."""
        char = get_character(character_id)
        path = _tmp_path("pin")
        generate_pin_image(char["portrait_path"] if char else None, _pin_values(buffer), path)
        return path

    def _build_account_image(self, character_id):
        """Génère l'image de l'écran de compte, retourne (chemin, AccountView)."""
        account = get_account(character_id)
        char = get_character(character_id)
        prenom, nom_clan = _display_names(char)
        txs = get_recent_transactions(character_id, 4)
        transactions = [
            (t["label"], _fmt_date(t["date"]), _amount_str(t["amount"]), t["amount"] >= 0)
            for t in txs
        ]
        path = _tmp_path("eco")
        generate_economie_image(
            prenom, nom_clan, account["solde_courant"], account["solde_livret"], transactions, path
        )
        return path, AccountView(character_id, account["user_id"])

    # ---------- écran de compte ----------
    async def show_account_screen(self, channel, character_id):
        if not get_account(character_id) or not get_character(character_id):
            await channel.send("Compte introuvable.")
            return
        path, view = self._build_account_image(character_id)
        await channel.send(file=discord.File(path, filename="compte.png"), view=view)
        try:
            os.remove(path)
        except OSError:
            pass

    # ---------- mode admin ----------
    async def enter_admin(self, channel, character_id, staff_member):
        account = get_account(character_id)
        if not account:
            char = get_character(character_id)
            owner_id = char["user_id"] if char else 0
            embed = discord.Embed(
                description="Ce personnage n'a pas encore de compte. Tu peux le créer :",
                color=PHOENIX_COLOR,
            )
            await channel.send(embed=embed, view=CreateAccountView(character_id, owner_id))
            return

        embed = discord.Embed(
            title="🛠️ Gestion du compte",
            description="**Actions disponibles :**\n• ajouter de l'argent\n• retirer de l'argent\n\n"
                        "Écris l'action exacte pour la sélectionner.",
            color=PHOENIX_COLOR,
        )
        await channel.send(embed=embed)

        action = None
        while action is None:
            m = await self.wait_message(channel, staff_member)
            if m is None:
                await channel.send("⏳ Session de gestion expirée. Relance /banque si besoin.")
                return
            norm = m.content.strip().lower().replace("’", "'").replace(" ", "")
            if norm == "ajouterdel'argent":
                action = "add"
            elif norm == "retirerdel'argent":
                action = "remove"
            else:
                await channel.send("Action non reconnue. Écris « ajouter de l'argent » ou « retirer de l'argent ».")

        amount = None
        while amount is None:
            await channel.send("Quel montant ?")
            m = await self.wait_message(channel, staff_member)
            if m is None:
                await channel.send("⏳ Session de gestion expirée. Relance /banque si besoin.")
                return
            content = m.content.strip().replace(" ", "")
            if not content.isdigit() or int(content) <= 0:
                await channel.send("❌ Montant invalide. Entre un nombre entier positif.")
                continue
            amount = int(content)

        delta = amount if action == "add" else -amount
        await channel.send(
            "Sur quel solde appliquer l'opération ?",
            view=AdminTargetView(self, character_id, delta, staff_member.id),
        )

    # =================================================================
    # LISTENER : boutons persistants (custom_id dynamiques)
    # =================================================================
    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        if interaction.type != discord.InteractionType.component:
            return
        cid = interaction.data.get("custom_id", "")
        if cid.startswith("banque_self:"):
            await self.handle_staff_self(interaction, cid)
        elif cid.startswith("banque_other:"):
            await self.handle_staff_other(interaction, cid)
        elif cid.startswith("banque_create:"):
            await self.handle_create(interaction, cid)
        elif cid.startswith("banque_pin_open:"):
            await self.handle_pin_open(interaction, cid)
        elif cid.startswith("banque_pin_digit:"):
            await self.handle_pin_digit(interaction, cid)
        elif cid.startswith("banque_pin_clear:"):
            await self.handle_pin_clear(interaction, cid)
        elif cid.startswith("banque_pin_confirm:"):
            await self.handle_pin_confirm(interaction, cid)
        elif cid.startswith("banque_pin_retry:"):
            await self.handle_pin_retry(interaction, cid)
        elif cid.startswith("banque_resend_pin:"):
            await self.handle_resend_pin(interaction, cid)
        elif cid.startswith("banque_iban:"):
            await self.handle_iban(interaction, cid)
        elif cid.startswith("banque_virement:"):
            await self.handle_virement(interaction, cid)
        elif cid.startswith("banque_info:"):
            await self.handle_info(interaction, cid)

    async def handle_staff_self(self, interaction, cid):
        owner = int(cid.split(":")[1])
        if interaction.user.id != owner:
            await interaction.response.send_message("Ce panneau ne t'appartient pas.", ephemeral=True)
            return
        await interaction.response.send_message("👤 Ouverture de ton espace bancaire…", ephemeral=True)
        await self.select_character_flow(interaction.channel, interaction.user, False, interaction.user)

    async def handle_staff_other(self, interaction, cid):
        owner = int(cid.split(":")[1])
        if interaction.user.id != owner:
            await interaction.response.send_message("Ce panneau ne t'appartient pas.", ephemeral=True)
            return
        await interaction.response.send_message(
            "Mentionne le joueur dont tu veux gérer le compte.", ephemeral=True
        )
        channel = interaction.channel
        await channel.send("🛠️ Mentionne le joueur dont tu veux gérer le compte.")
        target = None
        while target is None:
            m = await self.wait_message(channel, interaction.user)
            if m is None:
                await channel.send("⏳ Session expirée. Relance /banque si besoin.")
                return
            if m.mentions:
                target = m.mentions[0]
            else:
                await channel.send("Merci de **mentionner** un joueur (ex : @Pseudo).")
        await self.select_character_flow(channel, target, True, interaction.user)

    async def handle_create(self, interaction, cid):
        _, character_id, user_id = cid.split(":")
        character_id, user_id = int(character_id), int(user_id)

        if get_account(character_id):
            await interaction.response.send_message(
                "Un compte existe déjà pour ce personnage.", ephemeral=True
            )
            return

        char = get_character(character_id)
        if not char:
            await interaction.response.send_message("Personnage introuvable.", ephemeral=True)
            return

        owner_id = char["user_id"]
        guild_id = char["guild_id"]
        argent = get_argent_recompense(owner_id)

        iban_c, iban_l, pin = create_account(character_id, owner_id, guild_id, argent)
        if argent > 0:
            add_transaction(character_id, "Récompense de départ", argent)
            clear_argent_recompense(owner_id)

        dm_text = (
            "🏦 Ton compte Banque Phénix a été créé !\n\n"
            f"**IBAN compte courant :** {iban_c}\n"
            f"**IBAN Livret A :** {iban_l}\n"
            f"**Code secret :** {pin}\n\n"
            "Garde ces informations précieusement."
        )

        # DM au titulaire du compte.
        owner_obj = None
        if interaction.guild:
            owner_obj = interaction.guild.get_member(owner_id)
        if owner_obj is None:
            try:
                owner_obj = await self.bot.fetch_user(owner_id)
            except discord.HTTPException:
                owner_obj = None
        if owner_obj is not None:
            try:
                await owner_obj.send(dm_text)
            except discord.Forbidden:
                pass

        # Copie exacte au propriétaire du bot, avec le nom du joueur et du personnage.
        owner_mention = getattr(owner_obj, "mention", f"<@{owner_id}>")
        char_name = char["character_name"]
        try:
            owner_user = await self.bot.fetch_user(OWNER_ID)
            await owner_user.send(f"Compte créé pour {owner_mention} — personnage {char_name}\n\n{dm_text}")
        except discord.HTTPException:
            pass

        await interaction.response.send_message(
            "Compte créé avec succès ! Consulte tes messages privés.", ephemeral=False
        )

        # Si c'est un staff qui crée le compte d'un AUTRE joueur : enchaîne sur la gestion admin.
        is_staff = any(r.id == FICHE_STAFF_ROLE_ID for r in getattr(interaction.user, "roles", []))
        if is_staff and interaction.user.id != owner_id:
            await self.enter_admin(interaction.channel, character_id, interaction.user)

    async def handle_pin_open(self, interaction, cid):
        _, character_id, user_id = cid.split(":")
        character_id, user_id = int(character_id), int(user_id)
        if interaction.user.id != user_id:
            await interaction.response.send_message("Ce compte n'est pas le tien.", ephemeral=True)
            return
        # Ouvre le clavier : buffer vide + image PIN vierge, sur le message existant.
        self.pin_buffers[(user_id, character_id)] = ""
        path = self._render_pin(character_id, "")
        await interaction.response.edit_message(
            content="🔒 Entre ton code secret à l'aide du clavier ci dessous.",
            attachments=[discord.File(path, filename="pin.png")],
            view=PinKeypadView(character_id, user_id),
        )
        try:
            os.remove(path)
        except OSError:
            pass

    async def _refresh_keypad(self, interaction, character_id, user_id):
        """Régénère l'image du clavier avec le buffer courant et édite le message."""
        buf = self.pin_buffers.get((user_id, character_id), "")
        path = self._render_pin(character_id, buf)
        await interaction.response.edit_message(
            attachments=[discord.File(path, filename="pin.png")],
            view=PinKeypadView(character_id, user_id),
        )
        try:
            os.remove(path)
        except OSError:
            pass

    async def handle_pin_digit(self, interaction, cid):
        parts = cid.split(":")  # banque_pin_digit:{cid}:{uid}:{digit}
        character_id, user_id, digit = int(parts[1]), int(parts[2]), parts[3]
        if interaction.user.id != user_id:
            await interaction.response.send_message("Ce clavier ne t'appartient pas.", ephemeral=True)
            return
        buf = self.pin_buffers.get((user_id, character_id), "")
        if len(buf) >= 4:
            await interaction.response.defer()  # buffer plein : on ignore le clic
            return
        self.pin_buffers[(user_id, character_id)] = buf + digit
        await self._refresh_keypad(interaction, character_id, user_id)

    async def handle_pin_clear(self, interaction, cid):
        parts = cid.split(":")  # banque_pin_clear:{cid}:{uid}
        character_id, user_id = int(parts[1]), int(parts[2])
        if interaction.user.id != user_id:
            await interaction.response.send_message("Ce clavier ne t'appartient pas.", ephemeral=True)
            return
        buf = self.pin_buffers.get((user_id, character_id), "")
        self.pin_buffers[(user_id, character_id)] = buf[:-1] if buf else ""
        await self._refresh_keypad(interaction, character_id, user_id)

    async def handle_pin_confirm(self, interaction, cid):
        parts = cid.split(":")  # banque_pin_confirm:{cid}:{uid}
        character_id, user_id = int(parts[1]), int(parts[2])
        if interaction.user.id != user_id:
            await interaction.response.send_message("Ce clavier ne t'appartient pas.", ephemeral=True)
            return
        buf = self.pin_buffers.get((user_id, character_id), "")
        if len(buf) != 4:
            await interaction.response.send_message("Entre les 4 chiffres avant de valider.", ephemeral=True)
            return

        account = get_account(character_id)
        if account and buf == account["pin_code"]:
            self.pin_buffers[(user_id, character_id)] = ""
            set_session(user_id, character_id)
            # Passe directement à l'écran de compte, sur le même message.
            path, view = self._build_account_image(character_id)
            await interaction.response.edit_message(
                content=None, embed=None,
                attachments=[discord.File(path, filename="compte.png")], view=view,
            )
            try:
                os.remove(path)
            except OSError:
                pass
        else:
            self.pin_buffers[(user_id, character_id)] = ""
            embed = discord.Embed(description="❌ Code incorrect.", color=discord.Color.red())
            await interaction.response.edit_message(
                content=None, embed=embed, attachments=[], view=PinErrorView(character_id, user_id)
            )

    async def handle_pin_retry(self, interaction, cid):
        parts = cid.split(":")  # banque_pin_retry:{cid}:{uid}
        character_id, user_id = int(parts[1]), int(parts[2])
        if interaction.user.id != user_id:
            await interaction.response.send_message("Ce clavier ne t'appartient pas.", ephemeral=True)
            return
        # Relance un buffer vide et réaffiche le clavier avec le pillow remis à zéro.
        self.pin_buffers[(user_id, character_id)] = ""
        path = self._render_pin(character_id, "")
        await interaction.response.edit_message(
            content="🔒 Entre ton code secret à l'aide du clavier ci dessous.",
            embed=None,
            attachments=[discord.File(path, filename="pin.png")],
            view=PinKeypadView(character_id, user_id),
        )
        try:
            os.remove(path)
        except OSError:
            pass

    async def handle_resend_pin(self, interaction, cid):
        _, character_id, user_id = cid.split(":")
        character_id, user_id = int(character_id), int(user_id)
        if interaction.user.id != user_id:
            await interaction.response.send_message("Ce compte n'est pas le tien.", ephemeral=True)
            return
        account = get_account(character_id)
        if not account:
            await interaction.response.send_message("Compte introuvable.", ephemeral=True)
            return
        try:
            await interaction.user.send(
                f"🔑 Ton code secret Banque Phénix : **{account['pin_code']}**\n"
                "Ne le partage avec personne."
            )
            await interaction.response.send_message(
                "📩 Ton code t'a été renvoyé en message privé.", ephemeral=True
            )
        except discord.Forbidden:
            await interaction.response.send_message(
                "❌ Impossible de t'envoyer un MP (ouvre tes messages privés).", ephemeral=True
            )

    async def handle_iban(self, interaction, cid):
        character_id = int(cid.split(":")[1])
        account = get_account(character_id)
        if not account:
            await interaction.response.send_message("Compte introuvable.", ephemeral=True)
            return
        await interaction.response.send_message(
            f"**IBAN compte courant :** {account['iban_courant']}\n"
            f"**IBAN Livret A :** {account['iban_livret']}",
            ephemeral=False,
        )

    async def handle_virement(self, interaction, cid):
        _, character_id, owner_id = cid.split(":")
        character_id, owner_id = int(character_id), int(owner_id)
        if interaction.user.id != owner_id:
            await interaction.response.send_message("Ce compte n'est pas le tien.", ephemeral=True)
            return
        account = get_account(character_id)
        if not account:
            await interaction.response.send_message("Compte introuvable.", ephemeral=True)
            return

        await interaction.response.send_message("💸 Virement — suis les instructions ci-dessous.", ephemeral=True)
        channel = interaction.channel

        # 1) IBAN destinataire : compte personnel (courant OU livret, y compris son propre Livret A)
        # OU compte d'un ORDRE (orders.iban).
        dest = None
        dest_order = None
        dest_iban = None
        while dest is None and dest_order is None:
            await channel.send("Entre l'IBAN du destinataire (format JA suivi de 13 chiffres).")
            m = await self.wait_message(channel, interaction.user)
            if m is None:
                await channel.send("⏳ Virement annulé (délai dépassé).")
                return
            iban = m.content.strip().upper().replace(" ", "")
            found = find_account_by_any_iban(iban)
            if found is not None:
                if found["character_id"] == character_id and found["type_compte"] == "courant":
                    await channel.send(
                        "❌ Tu ne peux pas te virer de l'argent sur ton propre compte courant "
                        "(utilise ton IBAN Livret A pour épargner). Réessaie."
                    )
                else:
                    dest = found
                    dest_iban = iban
            else:
                order_row = db.get_order_by_iban(iban)
                if order_row is not None:
                    dest_order = order_row
                    dest_iban = iban
                else:
                    await channel.send("❌ Aucun compte trouvé avec cet IBAN. Réessaie.")

        # 2) Montant : entier STRICTEMENT positif et <= solde_courant de l'expéditeur.
        amount = None
        while amount is None:
            await channel.send("Quel montant veux tu envoyer ?")
            m = await self.wait_message(channel, interaction.user)
            if m is None:
                await channel.send("⏳ Virement annulé (délai dépassé).")
                return
            content = m.content.strip().replace(" ", "")
            if not content.isdigit() or int(content) <= 0:
                await channel.send("Le montant doit être un nombre positif.")
                continue
            val = int(content)
            account = get_account(character_id)  # solde à jour
            if val > account["solde_courant"]:
                await channel.send(
                    f"❌ Solde insuffisant (tu as {account['solde_courant']:,} ¥). Réessaie."
                )
                continue
            amount = val

        # 3) Application : débite toujours le compte courant de l'expéditeur.
        sender_iban = account["iban_courant"]

        # Cas destinataire = ORDRE : on crédite orders.solde_courant + order_transactions.
        if dest_order is not None:
            db.adjust_order_solde(dest_order["id"], amount)
            db.add_order_transaction(dest_order["id"], f"Virement reçu de {sender_iban}", amount, _now())
            add_transaction(character_id, f"Virement envoyé à l'ordre {dest_order['name']}", -amount, dest_iban)
            await self.apply_debit(character_id, amount, interaction.guild, compte="courant")
            await channel.send(
                f"✅ Virement de {amount:,} ¥ envoyé à l'ordre **{dest_order['name']}** avec succès !"
            )
            await self.show_account_screen(channel, character_id)
            return

        # Cas destinataire = compte personnel : crédite le bon solde (courant OU livret).
        dest_target = dest["type_compte"]  # "courant" | "livret"
        credit_account(dest["character_id"], dest_target, amount)
        if dest_target == "livret":
            add_transaction(dest["character_id"], f"Virement reçu sur Livret A de {sender_iban}",
                            amount, sender_iban)
        else:
            add_transaction(dest["character_id"], f"Virement reçu de {sender_iban}", amount, sender_iban)
        add_transaction(character_id, f"Virement envoyé à {dest_iban}", -amount, dest_iban)

        # Débit du compte courant de l'expéditeur (avec protection découvert + compte à rebours).
        await self.apply_debit(character_id, amount, interaction.guild, compte="courant")

        await channel.send(f"✅ Virement de {amount:,} ¥ envoyé à **{dest_iban}** avec succès !")
        await self.show_account_screen(channel, character_id)

    async def handle_info(self, interaction, cid):
        character_id = int(cid.split(":")[1])
        txs = get_all_transactions(character_id)
        if not txs:
            await interaction.response.send_message(
                "Aucune transaction pour ce compte.", ephemeral=False
            )
            return
        lines = [
            f"**{_fmt_date(t['date'])}** — {t['label']} — {_amount_str(t['amount'])}"
            for t in txs
        ]
        content = "\n".join(lines)
        pages = paginate_content(content)
        if len(pages) == 1:
            embed = discord.Embed(
                title="📜 Historique des transactions", description=pages[0], color=PHOENIX_COLOR
            )
            await interaction.response.send_message(embed=embed)
        else:
            pv = PaginationView("📜 Historique des transactions", pages, PHOENIX_COLOR)
            await interaction.response.send_message(embed=pv.build_embed(), view=pv)


async def setup(bot):
    await bot.add_cog(Banque(bot))
