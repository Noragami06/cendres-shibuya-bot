import asyncio
import os
import uuid
from datetime import datetime

import discord
from discord import app_commands
from discord.ext import commands

from cogs.utils import database as db
from cogs.utils.image_gen import generate_inventaire_image
# Réutilise les helpers bancaires (personnages, comptes, crédit, transactions) déjà existants.
from cogs.banque import (
    get_characters, get_character, get_account, add_transaction, PHOENIX_COLOR,
)

# ---------- Constantes ----------
FICHE_STAFF_ROLE_ID = 1521229332075512039
WAIT_TIMEOUT = 300  # secondes d'attente d'une réponse texte
ITEMS_PER_PAGE = 8  # objets affichés par page d'inventaire (2 colonnes x 4 lignes)

INV_IMG_DIR = os.path.join(os.path.dirname(__file__), "..", "temp", "inv_images")


def _now() -> str:
    return datetime.utcnow().isoformat()


def _tmp_inv(prefix: str) -> str:
    os.makedirs(INV_IMG_DIR, exist_ok=True)
    return os.path.join(INV_IMG_DIR, f"{prefix}_{uuid.uuid4().hex}.png")


def _is_staff(member) -> bool:
    return any(r.id == FICHE_STAFF_ROLE_ID for r in getattr(member, "roles", []))


# =====================================================================
# ACCÈS BASE DE DONNÉES
# =====================================================================
def search_item_definitions(query: str):
    """item_definitions dont le nom commence par 'query' (insensible à la casse)."""
    with db.get_connection() as conn:
        return conn.execute(
            "SELECT * FROM item_definitions WHERE name LIKE ? COLLATE NOCASE ORDER BY name",
            (query + "%",),
        ).fetchall()


def search_owned_items(character_id: int, query: str):
    """Items possédés (quantity > 0) par ce personnage dont le nom commence par 'query'."""
    with db.get_connection() as conn:
        return conn.execute(
            "SELECT d.* FROM item_definitions d "
            "JOIN character_inventory c ON d.id = c.item_id "
            "WHERE c.character_id = ? AND c.quantity > 0 AND d.name LIKE ? COLLATE NOCASE ORDER BY d.name",
            (character_id, query + "%"),
        ).fetchall()


def get_item(item_id: int):
    with db.get_connection() as conn:
        return conn.execute("SELECT * FROM item_definitions WHERE id = ?", (item_id,)).fetchone()


def get_inventory_categories(character_id: int):
    """Catégories (id + nom) présentes dans l'inventaire du personnage. Le nom provient de
    shop_categories (référencé par item_definitions.categorie_id) : un renommage côté /shop se
    répercute donc automatiquement ici, puisque tout référence categorie_id et non un texte dupliqué."""
    with db.get_connection() as conn:
        return conn.execute(
            "SELECT DISTINCT s.id, s.name FROM item_definitions d "
            "JOIN character_inventory c ON d.id = c.item_id "
            "JOIN shop_categories s ON d.categorie_id = s.id "
            "WHERE c.character_id = ? AND c.quantity > 0 "
            "ORDER BY s.name",
            (character_id,),
        ).fetchall()


def get_inventory_items(character_id: int, categorie_id: int):
    with db.get_connection() as conn:
        return conn.execute(
            "SELECT d.name, d.description, d.classe, d.valeur_base, c.quantity "
            "FROM item_definitions d JOIN character_inventory c ON d.id = c.item_id "
            "WHERE c.character_id = ? AND d.categorie_id = ? AND c.quantity > 0 ORDER BY d.name",
            (character_id, categorie_id),
        ).fetchall()


def get_inventory_qty(character_id: int, item_id: int) -> int:
    with db.get_connection() as conn:
        r = conn.execute(
            "SELECT quantity FROM character_inventory WHERE character_id = ? AND item_id = ?",
            (character_id, item_id),
        ).fetchone()
    return r["quantity"] if r else 0


def inv_add(character_id: int, item_id: int, qty: int):
    with db.get_connection() as conn:
        r = conn.execute(
            "SELECT id, quantity FROM character_inventory WHERE character_id = ? AND item_id = ?",
            (character_id, item_id),
        ).fetchone()
        if r:
            conn.execute("UPDATE character_inventory SET quantity = quantity + ? WHERE id = ?", (qty, r["id"]))
        else:
            conn.execute(
                "INSERT INTO character_inventory (character_id, item_id, quantity) VALUES (?, ?, ?)",
                (character_id, item_id, qty),
            )


def inv_remove(character_id: int, item_id: int, qty: int):
    with db.get_connection() as conn:
        r = conn.execute(
            "SELECT id, quantity FROM character_inventory WHERE character_id = ? AND item_id = ?",
            (character_id, item_id),
        ).fetchone()
        if not r:
            return
        new_q = r["quantity"] - qty
        if new_q > 0:
            conn.execute("UPDATE character_inventory SET quantity = ? WHERE id = ?", (new_q, r["id"]))
        else:
            conn.execute("DELETE FROM character_inventory WHERE id = ?", (r["id"],))


# ---------- pending_trades ----------
def create_trade(proposer_user_id, proposer_character_id, target_user_id, channel_id) -> int:
    with db.get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO pending_trades "
            "(proposer_user_id, proposer_character_id, target_user_id, status, channel_id, created_at) "
            "VALUES (?, ?, ?, 'awaiting_response', ?, ?)",
            (proposer_user_id, proposer_character_id, target_user_id, channel_id, _now()),
        )
        return cur.lastrowid


def get_trade(trade_id: int):
    with db.get_connection() as conn:
        return conn.execute("SELECT * FROM pending_trades WHERE id = ?", (trade_id,)).fetchone()


def update_trade(trade_id: int, **fields):
    if not fields:
        return
    assignments = ", ".join(f"{k} = ?" for k in fields)
    with db.get_connection() as conn:
        conn.execute(
            f"UPDATE pending_trades SET {assignments} WHERE id = ?", (*fields.values(), trade_id)
        )


def get_active_trade_for_user(user_id: int):
    """Échange en cours (ni terminé ni annulé) impliquant ce joueur (proposeur OU cible)."""
    with db.get_connection() as conn:
        return conn.execute(
            "SELECT * FROM pending_trades WHERE (proposer_user_id = ? OR target_user_id = ?) "
            "AND status NOT IN ('completed', 'cancelled') LIMIT 1",
            (user_id, user_id),
        ).fetchone()


def try_transition(trade_id: int, from_status: str, to_status: str) -> bool:
    """UPDATE conditionnelle : ne change le statut que s'il vaut encore from_status.
    Retourne True si CE changement a bien été appliqué (aucun autre acteur n'est passé avant)."""
    with db.get_connection() as conn:
        cur = conn.execute(
            "UPDATE pending_trades SET status = ? WHERE id = ? AND status = ?",
            (to_status, trade_id, from_status),
        )
        return cur.rowcount > 0


# =====================================================================
# VUES EN SESSION (callbacks internes)
# =====================================================================
class InvCharacterSelect(discord.ui.Select):
    def __init__(self, chars, invoker_id):
        self.invoker_id = invoker_id
        options = [
            discord.SelectOption(label=f"Slot {c['slot_number']} — {c['character_name']}", value=str(c["id"]))
            for c in chars
        ]
        super().__init__(placeholder="Choisis un personnage...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        view = self.view
        if interaction.user.id != self.invoker_id:
            await interaction.response.send_message("Ce menu ne t'appartient pas.", ephemeral=True)
            return
        view.result = int(self.values[0])
        await interaction.response.edit_message(view=None)
        view.stop()


class InvCharacterSelectView(discord.ui.View):
    def __init__(self, chars, invoker_id):
        super().__init__(timeout=WAIT_TIMEOUT)
        self.result = None
        self.add_item(InvCharacterSelect(chars, invoker_id))


class TradeAcceptView(discord.ui.View):
    def __init__(self, target_user_id, trade_id):
        super().__init__(timeout=120)  # 2 minutes
        self.target_user_id = target_user_id
        self.trade_id = trade_id
        self.result = None       # "accept" / "decline" / "expired" / None (timeout)
        self.timed_out = False   # True seulement si CE timeout a réellement annulé l'échange
        self.accept.custom_id = f"inv_trade_accept:{trade_id}"
        self.decline.custom_id = f"inv_trade_decline:{trade_id}"

    async def _check(self, interaction):
        if interaction.user.id != self.target_user_id:
            await interaction.response.send_message("Cet échange ne te concerne pas.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Accepter", emoji="✅", style=discord.ButtonStyle.success)
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check(interaction):
            return
        # Transition atomique : ne réussit que si le statut est ENCORE 'awaiting_response'
        # (sinon le timeout est passé avant nous).
        if try_transition(self.trade_id, "awaiting_response", "awaiting_item_a"):
            self.result = "accept"
            await interaction.response.edit_message(view=None)
        else:
            self.result = "expired"
            await interaction.response.send_message(
                "Cette demande a expiré, tu ne peux plus l'accepter.", ephemeral=True
            )
        self.stop()

    @discord.ui.button(label="Refuser", emoji="❌", style=discord.ButtonStyle.danger)
    async def decline(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check(interaction):
            return
        if try_transition(self.trade_id, "awaiting_response", "cancelled"):
            self.result = "decline"
            await interaction.response.edit_message(view=None)
        else:
            self.result = "expired"
            await interaction.response.send_message(
                "Cette demande a déjà expiré.", ephemeral=True
            )
        self.stop()

    async def on_timeout(self):
        # Ne marque 'cancelled' que si personne n'a agi entre temps (accept/decline).
        self.timed_out = try_transition(self.trade_id, "awaiting_response", "cancelled")


class TradeTypeView(discord.ui.View):
    def __init__(self, proposer_id, trade_id):
        super().__init__(timeout=WAIT_TIMEOUT)
        self.proposer_id = proposer_id
        self.result = None
        self.argent.custom_id = f"inv_trade_type_argent:{trade_id}"
        self.item.custom_id = f"inv_trade_type_item:{trade_id}"

    async def _check(self, interaction):
        if interaction.user.id != self.proposer_id:
            await interaction.response.send_message("Ce choix ne t'appartient pas.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Argent", emoji="💰", style=discord.ButtonStyle.primary)
    async def argent(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check(interaction):
            return
        self.result = "argent"
        await interaction.response.edit_message(view=None)
        self.stop()

    @discord.ui.button(label="Objet", emoji="📦", style=discord.ButtonStyle.secondary)
    async def item(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check(interaction):
            return
        self.result = "item"
        await interaction.response.edit_message(view=None)
        self.stop()


# =====================================================================
# VUE PRINCIPALE (persistante -> listener on_interaction)
# =====================================================================
class MainInventoryView(discord.ui.View):
    def __init__(self, character_id, user_id, is_staff, categories):
        super().__init__(timeout=None)
        if categories:
            self.add_item(discord.ui.Select(
                placeholder="Choisis une catégorie...", min_values=1, max_values=1,
                options=[discord.SelectOption(label=c["name"], value=str(c["id"])) for c in categories[:25]],
                custom_id=f"inv_cat:{character_id}", row=0,
            ))
        self.add_item(discord.ui.Button(
            label="Information", emoji="ℹ️", style=discord.ButtonStyle.secondary,
            custom_id=f"inv_info:{character_id}:{user_id}", row=1))
        self.add_item(discord.ui.Button(
            label="Échanger", emoji="🔁", style=discord.ButtonStyle.primary,
            custom_id=f"inv_trade:{character_id}:{user_id}", row=1))
        self.add_item(discord.ui.Button(
            label="Vendre", emoji="💰", style=discord.ButtonStyle.success,
            custom_id=f"inv_sell:{character_id}:{user_id}", row=1))
        if is_staff:
            # La création/ajout d'objets se fait maintenant exclusivement via la commande /shop (à venir).
            self.add_item(discord.ui.Button(
                label="Retirer un item", emoji="➖", style=discord.ButtonStyle.danger,
                custom_id=f"inv_remove:{user_id}", row=2))


class InventoryPageView(discord.ui.View):
    """Boutons de pagination sous l'image d'inventaire (persistants). Les états activé/désactivé
    dépendent de la page courante et du nombre total de pages."""

    def __init__(self, character_id, user_id, page, total_pages):
        super().__init__(timeout=None)
        self.add_item(discord.ui.Button(
            label="Page précédente", emoji="◀️", style=discord.ButtonStyle.secondary,
            custom_id=f"inv_page_prev:{character_id}:{user_id}", disabled=(page <= 0),
        ))
        self.add_item(discord.ui.Button(
            label="Page suivante", emoji="▶️", style=discord.ButtonStyle.secondary,
            custom_id=f"inv_page_next:{character_id}:{user_id}", disabled=(page >= total_pages - 1),
        ))


# =====================================================================
# COG
# =====================================================================
class Inventaire(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # Joueurs ayant un flux /inventaire en cours (en attente d'une saisie texte).
        # Garde en mémoire : les flux reposent sur bot.wait_for et NE survivent pas à un redémarrage,
        # donc un verrou SQL persistant deviendrait un verrou fantôme après reboot (aucune coroutine
        # pour le libérer). L'ensemble est vidé au redémarrage, ce qui est exactement le comportement
        # voulu, et chaque flux libère sa place via un bloc finally.
        self._active_users = set()
        # État de pagination d'inventaire par joueur : {(user_id, character_id): {"categorie", "page"}}.
        # Même logique mémoire que l'isolation : l'état est reconstruit en ré-ouvrant l'inventaire
        # après un redémarrage (les boutons persistent, mais l'état volatil non).
        self._page_state = {}

    async def cog_load(self):
        # Les boutons de l'inventaire ont un custom_id contenant character_id/user_id (inconnus à
        # l'avance) : bot.add_view() ne peut pas les enregistrer génériquement. La persistance est
        # assurée par le listener on_interaction ci-dessous (comme dans banque.py / depart.py).
        pass

    # ---------- verrou de flux par utilisateur ----------
    def _acquire(self, *user_ids) -> bool:
        """Réserve un flux pour ces user_id. Échoue (sans rien réserver) si l'un est déjà occupé,
        garantissant qu'un joueur n'a jamais deux flux concurrents (donc jamais deux wait_for
        simultanés qui captureraient le même message)."""
        ids = [u for u in user_ids if u is not None]
        if any(u in self._active_users for u in ids):
            return False
        self._active_users.update(ids)
        return True

    def _release(self, *user_ids):
        for u in user_ids:
            if u is not None:
                self._active_users.discard(u)

    # ---------- petits utilitaires d'attente ----------
    async def wait_message(self, channel, author, timeout: int = WAIT_TIMEOUT):
        # Filtre STRICT : uniquement l'auteur attendu à cette étape ET le salon d'origine du flux.
        def check(m):
            return m.channel.id == channel.id and m.author.id == author.id and not m.author.bot
        try:
            return await self.bot.wait_for("message", check=check, timeout=timeout)
        except asyncio.TimeoutError:
            return None

    async def wait_mention(self, channel, author):
        while True:
            m = await self.wait_message(channel, author)
            if m is None:
                return None
            if m.mentions:
                return m.mentions[0]
            await channel.send("Merci de **mentionner** un joueur (ex : @Pseudo).")

    async def ask_quantity(self, channel, user, prompt, maximum=None):
        while True:
            await channel.send(prompt)
            m = await self.wait_message(channel, user)
            if m is None:
                return None
            c = m.content.strip().replace(" ", "")
            if not c.isdigit() or int(c) <= 0:
                await channel.send("Entre un nombre entier positif.")
                continue
            val = int(c)
            if maximum is not None and val > maximum:
                await channel.send(f"Quantité trop élevée (maximum {maximum}). Réessaie.")
                continue
            return val

    async def select_character_await(self, channel, target_user, invoker_id, none_msg):
        chars = get_characters(target_user.id, channel.guild.id)
        if not chars:
            await channel.send(none_msg)
            return None
        if len(chars) == 1:
            return chars[0]["id"]
        view = InvCharacterSelectView(chars, invoker_id)
        await channel.send("Sélectionne le personnage :", view=view)
        await view.wait()
        return view.result

    async def _pick_numbered(self, channel, user, results):
        lines = [f"**{i + 1}.** {r['name']}" for i, r in enumerate(results)]
        await channel.send(embed=discord.Embed(
            title="Plusieurs objets trouvés",
            description="\n".join(lines) + "\n\nRéponds avec le numéro correspondant.",
            color=PHOENIX_COLOR,
        ))
        while True:
            m = await self.wait_message(channel, user)
            if m is None:
                return None
            c = m.content.strip()
            if c.isdigit() and 1 <= int(c) <= len(results):
                return results[int(c) - 1]
            await channel.send(f"Réponds avec un numéro entre 1 et {len(results)}.")

    async def resolve_item(self, channel, user, prompt, search_fn):
        """Désigne un item par son nom : 0 -> redemande, 1 -> direct, 2+ -> choix numéroté."""
        while True:
            await channel.send(prompt)
            m = await self.wait_message(channel, user)
            if m is None:
                return None
            results = search_fn(m.content.strip())
            if not results:
                await channel.send("Aucun objet trouvé avec ce nom.")
                continue
            if len(results) == 1:
                return results[0]
            picked = await self._pick_numbered(channel, user, results)
            if picked is None:
                return None
            return picked

    # ---------- commande ----------
    @app_commands.command(name="inventaire", description="Consulte ton inventaire")
    async def inventaire(self, interaction: discord.Interaction):
        await interaction.response.send_message("🎒 Ouverture de ton inventaire…", ephemeral=True)
        character_id = await self.select_character_await(
            interaction.channel, interaction.user, interaction.user.id, "Tu n'as aucun personnage validé."
        )
        if character_id is not None:
            await self.show_main(interaction.channel, character_id, interaction.user)

    # ---------- affichage principal ----------
    async def show_main(self, channel, character_id, viewer_member):
        categories = get_inventory_categories(character_id)
        char = get_character(character_id)
        name = char["character_name"] if char else "?"
        if categories:
            desc = "Pour voir ton inventaire, sélectionne une catégorie."
        else:
            desc = "Ton inventaire est vide pour l'instant."
        embed = discord.Embed(title=f"🎒 Inventaire de {name}", description=desc, color=PHOENIX_COLOR)
        await channel.send(
            embed=embed,
            view=MainInventoryView(character_id, viewer_member.id, _is_staff(viewer_member), categories),
        )

    # =================================================================
    # LISTENER
    # =================================================================
    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        if interaction.type != discord.InteractionType.component:
            return
        cid = interaction.data.get("custom_id", "")
        if cid.startswith("inv_cat:"):
            await self.handle_category(interaction, cid)
        elif cid.startswith("inv_page_prev:"):
            await self.handle_page(interaction, cid, "prev")
        elif cid.startswith("inv_page_next:"):
            await self.handle_page(interaction, cid, "next")
        elif cid.startswith("inv_info:"):
            await self.handle_info(interaction, cid)
        elif cid.startswith("inv_trade:"):
            await self.handle_trade(interaction, cid)
        elif cid.startswith("inv_sell:"):
            await self.handle_sell(interaction, cid)
        elif cid.startswith("inv_remove:"):
            await self.handle_remove(interaction, cid)

    def _render_category_page(self, character_id, categorie, page):
        """Génère l'image de la page demandée d'une catégorie. Retourne (chemin, total_pages, page_clampée).
        La valeur totale affichée est celle de TOUTE la catégorie (pas seulement de la page)."""
        rows = get_inventory_items(character_id, categorie)  # tous les objets, sans limite
        pages = [rows[i:i + ITEMS_PER_PAGE] for i in range(0, len(rows), ITEMS_PER_PAGE)] or [[]]
        total_pages = len(pages)
        page = max(0, min(page, total_pages - 1))
        page_rows = pages[page]
        items = [
            (r["name"], r["description"] or "", r["classe"] or "sans", r["quantity"],
             f"{(r['valeur_base'] or 0) * r['quantity']:,} ¥")
            for r in page_rows
        ]
        total_value = sum((r["valeur_base"] or 0) * r["quantity"] for r in rows)
        char = get_character(character_id)
        name = char["character_name"] if char else "?"
        path = _tmp_inv("inv")
        generate_inventaire_image(name, items, f"{total_value:,} ¥", path)
        return path, total_pages, page

    async def handle_category(self, interaction, cid):
        character_id = int(cid.split(":")[1])
        categorie = interaction.data.get("values", [None])[0]
        await interaction.response.defer()
        if not categorie:
            return
        cat_id = int(categorie)
        # Mémorise l'état de pagination pour ce joueur/personnage (page 0). La catégorie est
        # désormais identifiée par son id (categorie_id), pas par un texte.
        self._page_state[(interaction.user.id, character_id)] = {"categorie": cat_id, "page": 0}
        path, total_pages, page = self._render_category_page(character_id, cat_id, 0)
        view = InventoryPageView(character_id, interaction.user.id, page, total_pages) if total_pages > 1 else None
        await interaction.channel.send(file=discord.File(path, filename="inventaire.png"), view=view)
        try:
            os.remove(path)
        except OSError:
            pass

    async def handle_page(self, interaction, cid, direction):
        _, character_id, user_id = cid.split(":")
        character_id, user_id = int(character_id), int(user_id)
        if interaction.user.id != user_id:
            await interaction.response.send_message("Cette pagination n'est pas la tienne.", ephemeral=True)
            return
        state = self._page_state.get((user_id, character_id))
        if state is None:
            await interaction.response.send_message(
                "Cette pagination a expiré, ré-ouvre ton inventaire via le menu des catégories.",
                ephemeral=True,
            )
            return
        new_page = state["page"] + (1 if direction == "next" else -1)
        path, total_pages, page = self._render_category_page(character_id, state["categorie"], new_page)
        state["page"] = page
        view = InventoryPageView(character_id, user_id, page, total_pages) if total_pages > 1 else None
        await interaction.response.edit_message(
            attachments=[discord.File(path, filename="inventaire.png")], view=view
        )
        try:
            os.remove(path)
        except OSError:
            pass

    async def handle_info(self, interaction, cid):
        _, character_id, user_id = cid.split(":")
        character_id, user_id = int(character_id), int(user_id)
        if interaction.user.id != user_id:
            await interaction.response.send_message("Cet inventaire n'est pas le tien.", ephemeral=True)
            return
        if not self._acquire(user_id):
            await interaction.response.send_message(
                "Tu as déjà une action en cours, termine la d'abord.", ephemeral=True
            )
            return
        try:
            await interaction.response.send_message("ℹ️ Consultation d'un objet…", ephemeral=True)
            channel = interaction.channel
            item = await self.resolve_item(
                channel, interaction.user, "Écris le nom de l'objet que tu veux consulter.",
                search_item_definitions,
            )
            if item is None:
                await channel.send("⏳ Consultation annulée.")
                return
            qty = get_inventory_qty(character_id, item["id"])
            classe = item["classe"] or "sans"
            embed = discord.Embed(title=f"ℹ️ {item['name']}", color=PHOENIX_COLOR)
            embed.add_field(name="Description", value=item["description"] or "—", inline=False)
            embed.add_field(name="Classe", value=("Sans classe" if classe == "sans" else f"Classe {classe}"), inline=True)
            embed.add_field(name="Valeur de base", value=f"{item['valeur_base'] or 0:,} ¥", inline=True)
            embed.add_field(name="Quantité possédée", value=str(qty), inline=True)
            await channel.send(embed=embed)
        finally:
            self._release(user_id)

    async def handle_sell(self, interaction, cid):
        await interaction.response.send_message(
            "🔧 Le système de vente est actuellement en maintenance, en attente de la commande /shop.",
            ephemeral=False,
        )

    # ---------- ÉCHANGE ----------
    async def handle_trade(self, interaction, cid):
        _, character_id, user_id = cid.split(":")
        character_id, user_id = int(character_id), int(user_id)
        if interaction.user.id != user_id:
            await interaction.response.send_message("Cet inventaire n'est pas le tien.", ephemeral=True)
            return
        proposer = interaction.user

        # Verrou anti-échange-parallèle côté proposeur (même user_id sur ses différents persos).
        if get_active_trade_for_user(proposer.id):
            await interaction.response.send_message(
                "Tu es déjà en plein échange, termine le d'abord.", ephemeral=True
            )
            return
        # Verrou de flux : une seule action /inventaire à la fois par joueur.
        if not self._acquire(proposer.id):
            await interaction.response.send_message(
                "Tu as déjà une action en cours, termine la d'abord.", ephemeral=True
            )
            return

        acquired = {proposer.id}
        try:
            await self._run_trade(interaction, proposer, character_id, acquired)
        finally:
            self._release(*acquired)

    async def _run_trade(self, interaction, proposer, character_id, acquired):
        await interaction.response.send_message("🔁 Démarrage d'un échange…", ephemeral=True)
        channel = interaction.channel

        # A) Partenaire
        await channel.send("Mentionne le joueur avec qui tu veux échanger (tu peux te mentionner toi même).")
        target_user = await self.wait_mention(channel, proposer)
        if target_user is None:
            await channel.send("⏳ Échange annulé.")
            return

        # Verrou anti-échange-parallèle côté cible (sauf si c'est le proposeur lui même : il vient
        # d'être validé libre ci dessus, et aucun échange n'a encore été créé à ce stade).
        if target_user.id != proposer.id and get_active_trade_for_user(target_user.id):
            await channel.send(f"{target_user.mention} est déjà en plein échange, réessaie plus tard.")
            return
        # Verrou de flux côté cible : elle devra répondre à certaines étapes, elle ne peut donc pas
        # être déjà occupée par un autre flux /inventaire (info, ajout staff, autre échange...).
        if target_user.id != proposer.id:
            if not self._acquire(target_user.id):
                await channel.send(f"{target_user.mention} a déjà une action en cours, réessaie plus tard.")
                return
            acquired.add(target_user.id)

        trade_id = create_trade(proposer.id, character_id, target_user.id, channel.id)

        # Personnage de la cible (c'est la cible qui choisit son propre personnage).
        target_character_id = await self.select_character_await(
            channel, target_user, target_user.id, "Ce joueur n'a aucun personnage validé."
        )
        if target_character_id is None:
            update_trade(trade_id, status="cancelled")
            return
        if target_character_id == character_id:
            update_trade(trade_id, status="cancelled")
            await channel.send("❌ Tu ne peux pas échanger un personnage avec lui même.")
            return
        update_trade(trade_id, target_character_id=target_character_id)

        prop_char = get_character(character_id)
        targ_char = get_character(target_character_id)
        prop_label = f"{proposer.mention} ({prop_char['character_name']})"
        targ_label = f"{target_user.mention} ({targ_char['character_name']})"

        # B) Acceptation (2 minutes)
        embed = discord.Embed(
            description=f"{prop_label} souhaite échanger avec {targ_label}.\nAcceptes tu ?",
            color=PHOENIX_COLOR,
        )
        view = TradeAcceptView(target_user.id, trade_id)
        msg = await channel.send(content=target_user.mention, embed=embed, view=view)
        await view.wait()
        # Les transitions de statut sont gérées ATOMIQUEMENT dans la vue (accept/decline/on_timeout).
        if view.result == "accept":
            pass  # statut déjà passé à 'awaiting_item_a' par la vue
        elif view.result == "decline":
            await channel.send("Échange refusé.")
            return
        else:
            # Timeout (result None) ou clic tardif perdu (result 'expired').
            if view.timed_out:
                await msg.edit(content="⏱️ Cette demande d'échange a expiré.", embed=None, view=None)
            return

        # C) Objet offert par le proposeur
        item = await self.resolve_item(
            channel, proposer, "Quel objet veux tu échanger ? Écris son nom.",
            lambda q: search_owned_items(character_id, q),
        )
        if item is None:
            update_trade(trade_id, status="cancelled")
            await channel.send("⏳ Échange annulé.")
            return
        owned = get_inventory_qty(character_id, item["id"])
        qty = await self.ask_quantity(
            channel, proposer, f"Quelle quantité de **{item['name']}** veux tu échanger ? (max {owned})",
            maximum=owned,
        )
        if qty is None:
            update_trade(trade_id, status="cancelled")
            return
        update_trade(trade_id, offered_item_id=item["id"], offered_quantity=qty, status="awaiting_type")

        # D) Type de contrepartie
        tview = TradeTypeView(proposer.id, trade_id)
        await channel.send(f"{proposer.mention}, que demandes tu en contrepartie ?", view=tview)
        await tview.wait()
        if tview.result is None:
            update_trade(trade_id, status="cancelled")
            await channel.send("⏳ Échange annulé (aucun choix).")
            return

        if tview.result == "argent":
            if get_account(character_id) is None:
                update_trade(trade_id, status="cancelled")
                await channel.send(
                    "❌ Ton personnage n'a pas de compte bancaire pour recevoir de l'argent. Échange annulé."
                )
                return
            amount = await self.ask_quantity(channel, proposer, "Quel montant veux tu demander ?")
            if amount is None:
                update_trade(trade_id, status="cancelled")
                return
            update_trade(trade_id, request_type="argent", request_amount=amount,
                         status="awaiting_confirmation_b")
            final_amount = await self._negotiate_amount(channel, trade_id, target_character_id, target_user, amount)
            if final_amount is None:
                update_trade(trade_id, status="cancelled")
                await channel.send("Échange annulé.")
                return
            update_trade(trade_id, request_amount=final_amount)
        else:
            ritem = await self.resolve_item(
                channel, target_user, f"{targ_label}, quel objet donnes tu en échange ? Écris son nom.",
                lambda q: search_owned_items(target_character_id, q),
            )
            if ritem is None:
                update_trade(trade_id, status="cancelled")
                await channel.send("⏳ Échange annulé.")
                return
            r_owned = get_inventory_qty(target_character_id, ritem["id"])
            r_qty = await self.ask_quantity(
                channel, target_user, f"Quelle quantité de **{ritem['name']}** ? (max {r_owned})",
                maximum=r_owned,
            )
            if r_qty is None:
                update_trade(trade_id, status="cancelled")
                return
            update_trade(trade_id, request_type="item", request_item_id=ritem["id"],
                         request_item_quantity=r_qty, status="awaiting_confirmation_b")

        # F) Exécution
        await self._execute_trade(trade_id)

    async def _negotiate_amount(self, channel, trade_id, target_character_id, target_user, amount):
        """Retourne un montant que la cible peut payer, ou None si annulé."""
        while True:
            account = get_account(target_character_id)
            solde = account["solde_courant"] if account else 0
            if amount <= solde:
                return amount
            await channel.send(
                f"{target_user.mention} Le montant demandé ({amount:,} ¥) dépasse ton solde actuel "
                f"({solde:,} ¥). Écris un nouveau montant à proposer, ou écris « cancel » pour annuler l'échange."
            )
            m = await self.wait_message(channel, target_user)
            if m is None:
                return None
            content = m.content.strip()
            if content.lower() == "cancel":
                return None
            c = content.replace(" ", "")
            if not c.isdigit() or int(c) <= 0:
                await channel.send("Montant invalide.")
                continue
            amount = int(c)
            update_trade(trade_id, request_amount=amount)

    async def _execute_trade(self, trade_id) -> bool:
        trade = get_trade(trade_id)
        proposer_char = trade["proposer_character_id"]
        target_char = trade["target_character_id"]
        offered_item = trade["offered_item_id"]
        offered_qty = trade["offered_quantity"]
        channel = self.bot.get_channel(trade["channel_id"])
        prop_mention = f"<@{trade['proposer_user_id']}>"
        targ_mention = f"<@{trade['target_user_id']}>"

        async def _cancel(msg):
            update_trade(trade_id, status="cancelled")
            if channel:
                await channel.send(msg)
            return False

        # --- Vérifications EN TEMPS RÉEL, sur les données actuelles, juste avant l'exécution. ---
        # 1) Le proposeur possède-t-il toujours l'objet offert en quantité suffisante ?
        offered_def = get_item(offered_item)
        offered_name = offered_def["name"] if offered_def else "cet objet"
        if get_inventory_qty(proposer_char, offered_item) < offered_qty:
            return await _cancel(
                f"❌ L'échange a été annulé : {offered_name} n'est plus disponible en quantité "
                f"suffisante dans l'inventaire de {prop_mention}."
            )

        if trade["request_type"] == "argent":
            # 2) La cible a-t-elle toujours de quoi payer ? (aucun découvert, même de 1 ¥)
            amount = trade["request_amount"]
            account = get_account(target_char)
            solde = account["solde_courant"] if account else 0
            if solde < amount:
                return await _cancel(
                    f"❌ L'échange a été annulé : le solde de {targ_mention} est devenu insuffisant "
                    "entre temps."
                )
        else:
            # 3) La cible possède-t-elle toujours l'objet demandé en quantité suffisante ?
            r_item = trade["request_item_id"]
            r_qty = trade["request_item_quantity"]
            r_def = get_item(r_item)
            r_name = r_def["name"] if r_def else "cet objet"
            if get_inventory_qty(target_char, r_item) < r_qty:
                return await _cancel(
                    f"❌ L'échange a été annulé : {r_name} n'est plus disponible en quantité "
                    f"suffisante dans l'inventaire de {targ_mention}."
                )

        # --- Toutes les vérifications passent : exécution réelle. ---
        # L'objet offert va du proposeur vers la cible.
        inv_remove(proposer_char, offered_item, offered_qty)
        inv_add(target_char, offered_item, offered_qty)

        if trade["request_type"] == "argent":
            amount = trade["request_amount"]
            with db.get_connection() as conn:
                conn.execute(
                    "UPDATE bank_accounts SET solde_courant = solde_courant - ? WHERE character_id = ?",
                    (amount, target_char),
                )
                conn.execute(
                    "UPDATE bank_accounts SET solde_courant = solde_courant + ? WHERE character_id = ?",
                    (amount, proposer_char),
                )
            add_transaction(target_char, "Échange d'objet", -amount)
            add_transaction(proposer_char, "Échange d'objet", amount)
            counterpart = f"{amount:,} ¥"
        else:
            r_item = trade["request_item_id"]
            r_qty = trade["request_item_quantity"]
            inv_remove(target_char, r_item, r_qty)
            inv_add(proposer_char, r_item, r_qty)
            r_def = get_item(r_item)
            counterpart = f"{r_qty} × {r_def['name']}"

        update_trade(trade_id, status="completed")

        offered_def = get_item(offered_item)
        pc = get_character(proposer_char)
        tc = get_character(target_char)
        embed = discord.Embed(
            title="✅ Échange effectué",
            description=(
                f"**{pc['character_name']}** a donné **{offered_qty} × {offered_def['name']}** "
                f"à **{tc['character_name']}**.\n"
                f"**{tc['character_name']}** a donné **{counterpart}** à **{pc['character_name']}**."
            ),
            color=PHOENIX_COLOR,
        )
        channel = self.bot.get_channel(trade["channel_id"])
        if channel:
            await channel.send(embed=embed)
        return True

    # ---------- STAFF : retrait ----------
    # La création/ajout d'objets se fait maintenant exclusivement via la commande /shop (à venir).
    async def handle_remove(self, interaction, cid):
        user_id = int(cid.split(":")[1])
        if interaction.user.id != user_id or not _is_staff(interaction.user):
            await interaction.response.send_message("Action réservée au staff propriétaire du panneau.", ephemeral=True)
            return
        if not self._acquire(user_id):
            await interaction.response.send_message(
                "Tu as déjà une action en cours, termine la d'abord.", ephemeral=True
            )
            return
        try:
            await interaction.response.send_message("➖ Retrait d'un item…", ephemeral=True)
            channel = interaction.channel

            await channel.send("Mentionne le joueur concerné.")
            target = await self.wait_mention(channel, interaction.user)
            if target is None:
                await channel.send("⏳ Annulé.")
                return
            character_id = await self.select_character_await(
                channel, target, interaction.user.id, "Ce joueur n'a aucun personnage validé."
            )
            if character_id is None:
                return

            item = await self.resolve_item(
                channel, interaction.user, "Nom de l'objet à retirer :",
                lambda q: search_owned_items(character_id, q),
            )
            if item is None:
                await channel.send("⏳ Annulé.")
                return
            owned = get_inventory_qty(character_id, item["id"])
            qty = await self.ask_quantity(
                channel, interaction.user, f"Quelle quantité de **{item['name']}** retirer ? (max {owned})",
                maximum=owned,
            )
            if qty is None:
                return
            inv_remove(character_id, item["id"], qty)
            char = get_character(character_id)
            await channel.send(f"✅ {qty} × **{item['name']}** retiré(s) de l'inventaire de **{char['character_name']}**.")
        finally:
            self._release(user_id)


async def setup(bot):
    await bot.add_cog(Inventaire(bot))
