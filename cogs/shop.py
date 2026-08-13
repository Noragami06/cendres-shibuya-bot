# RÈGLES DE ROBUSTESSE PERMANENTES DU PROJET — à appliquer systématiquement à CHAQUE nouvelle
# commande impliquant des boutons/flux textuels/transactions, sans attendre qu'on te le demande :
# 1. Tout bouton réservé à un rôle doit revérifier ce rôle AU CLIC, pas seulement à l'affichage.
# 2. Toute étape attendant une réponse textuelle doit être isolée par utilisateur (jamais un
#    simple wait_for global qui pourrait capter le message du mauvais joueur).
# 3. Toute transaction (argent, objets, quantités) doit revérifier les données en temps réel
#    juste avant de s'exécuter, jamais se fier uniquement à des valeurs capturées plus tôt.
# 4. Tout bouton de confirmation déclenchant une action irréversible doit se désactiver
#    immédiatement au premier clic pour empêcher les doubles exécutions.
# 5. Toute suppression de personnage/objet/compte doit être ajoutée à delete_character_cascade()
#    si une nouvelle table le référence.
# 6. Toute action déclenchée par un bouton de confirmation/exécution doit systématiquement avoir
#    une protection anti double-clic (retrait immédiat de la View + verrou en mémoire), appliquée
#    par défaut à chaque nouveau bouton de ce type, sans attendre qu'on te le demande.

import asyncio
import os
import re
import uuid

import discord
from discord import app_commands
from discord.ext import commands

from cogs.utils import database as db
from cogs.utils.image_gen import generate_shop_image
# Réutilise les helpers bancaires déjà existants (personnages, comptes, transactions, format date).
# apply_debit reste une MÉTHODE du cog Banque (protection Livret A + compte à rebours) : on la
# récupère via self.bot.get_cog("Banque") pour appeler la VRAIE fonction.
from cogs.banque import (
    get_characters, get_character, get_account, add_transaction, credit_compte_courant,
    _fmt_date, PHOENIX_COLOR,
)

# ---------- Constantes ----------
FICHE_STAFF_ROLE_ID = 1521229332075512039
WAIT_TIMEOUT = 300   # secondes d'attente d'une réponse texte
ITEMS_PER_PAGE = 8   # objets affichés par page (2 colonnes x 4 lignes)

SHOP_IMG_DIR = os.path.join(os.path.dirname(__file__), "..", "temp", "shop_images")

NO_PERM_MSG = "Tu n'as pas la permission d'utiliser ce bouton."


def _is_staff(member) -> bool:
    return any(r.id == FICHE_STAFF_ROLE_ID for r in getattr(member, "roles", []))


def _tmp_shop(prefix: str) -> str:
    os.makedirs(SHOP_IMG_DIR, exist_ok=True)
    return os.path.join(SHOP_IMG_DIR, f"{prefix}_{uuid.uuid4().hex}.png")


def _chunk_message(text: str, limit: int = 1900):
    """Découpe un texte en morceaux < limit caractères, sans couper au milieu d'une ligne."""
    chunks, cur = [], ""
    for line in text.split("\n"):
        if cur and len(cur) + len(line) + 1 > limit:
            chunks.append(cur)
            cur = line
        else:
            cur = line if not cur else f"{cur}\n{line}"
    if cur:
        chunks.append(cur)
    return chunks or [text]


# =====================================================================
# ACCÈS BASE DE DONNÉES
# =====================================================================
def get_shop_categories():
    with db.get_connection() as conn:
        return conn.execute("SELECT id, name FROM shop_categories ORDER BY name").fetchall()


def get_shop_category(cat_id: int):
    with db.get_connection() as conn:
        return conn.execute("SELECT * FROM shop_categories WHERE id = ?", (cat_id,)).fetchone()


def get_category_by_name_ci(name: str):
    with db.get_connection() as conn:
        return conn.execute(
            "SELECT * FROM shop_categories WHERE name = ? COLLATE NOCASE", (name,)
        ).fetchone()


def create_category(name: str) -> int:
    with db.get_connection() as conn:
        cur = conn.execute("INSERT INTO shop_categories (name) VALUES (?)", (name,))
        return cur.lastrowid


def rename_category(cat_id: int, new_name: str):
    with db.get_connection() as conn:
        conn.execute("UPDATE shop_categories SET name = ? WHERE id = ?", (new_name, cat_id))


def get_items_in_category(cat_id: int):
    with db.get_connection() as conn:
        return conn.execute(
            "SELECT * FROM item_definitions WHERE categorie_id = ? ORDER BY name", (cat_id,)
        ).fetchall()


def search_items_in_category(cat_id: int, query: str):
    """Items d'une catégorie dont le nom commence par 'query' (insensible à la casse)."""
    with db.get_connection() as conn:
        return conn.execute(
            "SELECT * FROM item_definitions WHERE categorie_id = ? AND name LIKE ? COLLATE NOCASE "
            "ORDER BY name",
            (cat_id, query + "%"),
        ).fetchall()


def get_item(item_id: int):
    with db.get_connection() as conn:
        return conn.execute("SELECT * FROM item_definitions WHERE id = ?", (item_id,)).fetchone()


def get_item_by_name_ci(name: str):
    with db.get_connection() as conn:
        return conn.execute(
            "SELECT * FROM item_definitions WHERE name = ? COLLATE NOCASE", (name,)
        ).fetchone()


def create_item(name: str, description: str, classe: str, prix: int, categorie_id: int) -> int:
    with db.get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO item_definitions (name, description, classe, valeur_base, categorie_id) "
            "VALUES (?, ?, ?, ?, ?)",
            (name, description, classe, prix, categorie_id),
        )
        return cur.lastrowid


def update_item(item_id: int, **fields):
    if not fields:
        return
    assignments = ", ".join(f"{k} = ?" for k in fields)
    with db.get_connection() as conn:
        conn.execute(
            f"UPDATE item_definitions SET {assignments} WHERE id = ?", (*fields.values(), item_id)
        )


def inv_add(character_id: int, item_id: int, qty: int):
    """Ajoute qty exemplaires d'un item à l'inventaire d'un personnage (crée la ligne au besoin)."""
    with db.get_connection() as conn:
        r = conn.execute(
            "SELECT id, quantity FROM character_inventory WHERE character_id = ? AND item_id = ?",
            (character_id, item_id),
        ).fetchone()
        if r:
            conn.execute(
                "UPDATE character_inventory SET quantity = quantity + ? WHERE id = ?", (qty, r["id"])
            )
        else:
            conn.execute(
                "INSERT INTO character_inventory (character_id, item_id, quantity) VALUES (?, ?, ?)",
                (character_id, item_id, qty),
            )


def _refund_character(character_id: int, montant: int, label: str) -> bool:
    """Rembourse un personnage sur son compte courant (si le compte existe). Retourne True si
    le remboursement a bien été crédité."""
    if not get_account(character_id):
        return False
    # Remboursement d'un achat déjà payé par le joueur : category='remboursement' -> ne compte JAMAIS
    # dans l'épargne automatique et ne la déclenche jamais.
    credit_compte_courant(character_id, montant, label, category="remboursement")
    return True


# =====================================================================
# PARSEUR DE CRÉATION EN MASSE
# =====================================================================
NAME_ALIASES = ["n", "ndi", "nom", "nom de l'item"]
DESC_ALIASES = ["descr", "desc", "description"]
PRIX_ALIASES = ["prix", "price"]
CLASSE_ALIASES = ["classe", "class"]


def _marker_regex(aliases):
    """Regex d'un marqueur de champ : un des alias (le plus long d'abord), suivi de ':'.
    Le lookbehind évite de capturer un alias collé à un mot précédent (ex: le « n » final de
    « description » ne doit pas être vu comme un marqueur de nom)."""
    body = "|".join(re.escape(a) for a in sorted(aliases, key=len, reverse=True))
    return re.compile(r"(?<![\w'])(?:" + body + r")\s*:", re.IGNORECASE)


_NAME_RE = _marker_regex(NAME_ALIASES)
_DESC_RE = _marker_regex(DESC_ALIASES)
_PRIX_RE = _marker_regex(PRIX_ALIASES)
_CLASSE_RE = _marker_regex(CLASSE_ALIASES)


def _clean_val(s: str) -> str:
    """Nettoie une valeur : espaces et séparateurs « / » en bordure."""
    return s.strip().strip("/").strip()


def parse_bulk_items(text: str):
    """Retourne (items_valides, erreurs). items_valides = liste de dicts
    {order, name, description, prix, classe}. erreurs = liste de tuples (numero_ordre, message).

    En plus de la validation champ par champ, détecte les DOUBLONS DE NOM au sein du même lot
    (insensible à la casse) : la 2e occurrence et les suivantes sont marquées en erreur. La
    vérification contre les noms déjà présents EN BASE se fait, elle, au moment de l'insertion."""
    valid, errors = [], []
    name_markers = list(_NAME_RE.finditer(text))
    if not name_markers:
        return valid, errors

    seen_names = set()  # noms déjà rencontrés DANS CE LOT (minuscule), tous items confondus

    for idx, m in enumerate(name_markers):
        order = idx + 1
        seg_start = m.end()
        seg_end = name_markers[idx + 1].start() if idx + 1 < len(name_markers) else len(text)
        segment = text[seg_start:seg_end]

        # Marqueurs de champ (desc/prix/classe) présents dans ce segment, triés par position.
        field_hits = []
        for kind, rgx in (("desc", _DESC_RE), ("prix", _PRIX_RE), ("classe", _CLASSE_RE)):
            for fm in rgx.finditer(segment):
                field_hits.append((fm.start(), fm.end(), kind))
        field_hits.sort(key=lambda t: t[0])

        # Le nom va du début du segment au premier marqueur de champ.
        first_field_pos = field_hits[0][0] if field_hits else len(segment)
        name = _clean_val(segment[:first_field_pos])

        # Chaque valeur va de la fin de son marqueur au marqueur suivant (quel qu'il soit).
        values = {}
        for i, (fs, fe, kind) in enumerate(field_hits):
            next_pos = field_hits[i + 1][0] if i + 1 < len(field_hits) else len(segment)
            if kind not in values:  # on ne garde que la première occurrence
                values[kind] = _clean_val(segment[fe:next_pos])

        item_errors = []
        if not name:
            item_errors.append("nom manquant")
        else:
            # Doublon de nom dans le même lot : la 1re occurrence alimente le set, les suivantes
            # sont des erreurs. On enregistre le nom même si l'item comporte d'autres erreurs, pour
            # que « apparaît déjà plus haut » reste cohérent quel que soit l'état du 1er item.
            if name.lower() in seen_names:
                item_errors.append(f'Un objet nommé "{name}" apparaît déjà plus haut dans ce même lot.')
            seen_names.add(name.lower())

        raw_prix = values.get("prix")
        prix = None
        if not raw_prix:
            item_errors.append("prix manquant")
        else:
            cleaned = raw_prix.replace(" ", "").replace(",", "").replace("¥", "")
            if not cleaned.isdigit() or int(cleaned) <= 0:
                item_errors.append(f"prix invalide (« {raw_prix} »)")
            else:
                prix = int(cleaned)

        raw_classe = values.get("classe")
        classe = None
        if not raw_classe:
            item_errors.append("classe manquante")
        elif "sans" in raw_classe.lower():
            classe = "sans"
        else:
            norm = raw_classe.strip().upper()
            if norm in ("S", "1", "2", "3", "4"):
                classe = norm
            else:
                item_errors.append(f"classe invalide (« {raw_classe} »)")

        description = values.get("desc", "") or ""

        if item_errors:
            errors.append((order, ", ".join(item_errors)))
        else:
            valid.append({"order": order, "name": name, "description": description,
                          "prix": prix, "classe": classe})

    return valid, errors


# =====================================================================
# VUES EN SESSION (callbacks internes, non persistants)
# =====================================================================
class ShopCharacterSelect(discord.ui.Select):
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


class ShopCharacterSelectView(discord.ui.View):
    def __init__(self, chars, invoker_id):
        super().__init__(timeout=WAIT_TIMEOUT)
        self.result = None
        self.add_item(ShopCharacterSelect(chars, invoker_id))


class CategorySelect(discord.ui.Select):
    def __init__(self, cats, invoker_id, require_staff=False):
        self.invoker_id = invoker_id
        self.require_staff = require_staff
        options = [discord.SelectOption(label=c["name"], value=str(c["id"])) for c in cats[:25]]
        super().__init__(placeholder="Choisis une catégorie...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        # Règle de robustesse #1 : un menu réservé au staff revérifie le rôle AU CLIC.
        if self.require_staff and not _is_staff(interaction.user):
            await interaction.response.send_message(NO_PERM_MSG, ephemeral=True)
            return
        if interaction.user.id != self.invoker_id:
            await interaction.response.send_message("Ce menu ne t'appartient pas.", ephemeral=True)
            return
        self.view.result = int(self.values[0])
        await interaction.response.edit_message(view=None)
        self.view.stop()


class CategorySelectView(discord.ui.View):
    def __init__(self, cats, invoker_id, require_staff=False):
        super().__init__(timeout=WAIT_TIMEOUT)
        self.result = None
        self.add_item(CategorySelect(cats, invoker_id, require_staff))


class ConfirmView(discord.ui.View):
    """Deux boutons oui/non en session. self.result vaut True/False/None (timeout).

    - require_staff : revérifie le rôle staff AU CLIC (règle de robustesse #1).
    - Le premier clic retire immédiatement la View et verrouille (self._done), pour empêcher
      toute double exécution en cas de double clic ultra rapide (règle de robustesse #4)."""

    def __init__(self, owner_id, yes_label, no_label, yes_emoji=None, no_emoji=None,
                 yes_cid=None, no_cid=None, require_staff=False):
        super().__init__(timeout=WAIT_TIMEOUT)
        self.owner_id = owner_id
        self.require_staff = require_staff
        self.result = None
        self._done = False
        yb = discord.ui.Button(label=yes_label, emoji=yes_emoji,
                               style=discord.ButtonStyle.success, custom_id=yes_cid)
        nb = discord.ui.Button(label=no_label, emoji=no_emoji,
                               style=discord.ButtonStyle.danger, custom_id=no_cid)
        yb.callback = self._yes
        nb.callback = self._no
        self.add_item(yb)
        self.add_item(nb)

    async def _guard(self, interaction) -> bool:
        if self.require_staff and not _is_staff(interaction.user):
            await interaction.response.send_message(NO_PERM_MSG, ephemeral=True)
            return False
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Ce choix ne t'appartient pas.", ephemeral=True)
            return False
        return True

    async def _finish(self, interaction, value):
        # Deuxième clic (ou clic redondant) : la View est déjà consommée, on absorbe silencieusement.
        if self._done:
            try:
                await interaction.response.defer()
            except discord.HTTPException:
                pass
            return
        self._done = True
        self.result = value
        await interaction.response.edit_message(view=None)
        self.stop()

    async def _yes(self, interaction: discord.Interaction):
        if not await self._guard(interaction):
            return
        await self._finish(interaction, True)

    async def _no(self, interaction: discord.Interaction):
        if not await self._guard(interaction):
            return
        await self._finish(interaction, False)


# =====================================================================
# VUES PERSISTANTES (custom_id dynamiques -> listener on_interaction)
# =====================================================================
class MainShopView(discord.ui.View):
    def __init__(self, user_id, is_staff, categories):
        super().__init__(timeout=None)
        if categories:
            self.add_item(discord.ui.Select(
                placeholder="Choisis une catégorie...", min_values=1, max_values=1,
                options=[discord.SelectOption(label=c["name"], value=str(c["id"])) for c in categories[:25]],
                custom_id=f"shop_cat:{user_id}", row=0,
            ))
        if is_staff:
            self.add_item(discord.ui.Button(
                label="Créer une catégorie", emoji="➕", style=discord.ButtonStyle.success,
                custom_id=f"shop_cat_create:{user_id}", row=1))
            self.add_item(discord.ui.Button(
                label="Modifier catégorie", emoji="✏️", style=discord.ButtonStyle.secondary,
                custom_id=f"shop_cat_edit:{user_id}", row=1))
            self.add_item(discord.ui.Button(
                label="Supprimer catégorie", emoji="🗑️", style=discord.ButtonStyle.danger,
                custom_id=f"shop_cat_delete:{user_id}", row=1))
            self.add_item(discord.ui.Button(
                label="Créer un item", emoji="🆕", style=discord.ButtonStyle.primary,
                custom_id=f"shop_item_create:{user_id}", row=2))


class ShopCategoryPageView(discord.ui.View):
    """Boutons sous l'image d'une catégorie (persistants). Pagination cachée s'il n'y a qu'une page,
    boutons de gestion d'item réservés au staff."""

    def __init__(self, cat_id, user_id, page, total_pages, is_staff):
        super().__init__(timeout=None)
        if total_pages > 1:
            self.add_item(discord.ui.Button(
                label="Page précédente", emoji="◀️", style=discord.ButtonStyle.secondary,
                custom_id=f"shop_page_prev:{cat_id}:{user_id}", disabled=(page <= 0), row=0))
            self.add_item(discord.ui.Button(
                label="Page suivante", emoji="▶️", style=discord.ButtonStyle.secondary,
                custom_id=f"shop_page_next:{cat_id}:{user_id}", disabled=(page >= total_pages - 1), row=0))
        self.add_item(discord.ui.Button(
            label="Information", emoji="ℹ️", style=discord.ButtonStyle.secondary,
            custom_id=f"shop_info:{cat_id}:{user_id}", row=1))
        self.add_item(discord.ui.Button(
            label="Achat", emoji="🛒", style=discord.ButtonStyle.success,
            custom_id=f"shop_buy:{cat_id}:{user_id}", row=1))
        if is_staff:
            self.add_item(discord.ui.Button(
                label="Modifier un item", emoji="✏️", style=discord.ButtonStyle.secondary,
                custom_id=f"shop_edit_item:{cat_id}:{user_id}", row=2))
            self.add_item(discord.ui.Button(
                label="Supprimer un item", emoji="🗑️", style=discord.ButtonStyle.danger,
                custom_id=f"shop_delete_item:{cat_id}:{user_id}", row=2))


# =====================================================================
# COG
# =====================================================================
class Shop(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # Verrou de flux LOCAL de secours (si le cog Inventaire n'est pas chargé). En temps normal
        # on partage le verrou d'Inventaire via _shared_lock() — cf. règle de robustesse #2.
        self._active_users = set()
        # État de pagination par (user_id, cat_id) -> {"page": index}. Volatil, reconstruit en
        # ré-ouvrant la catégorie après un redémarrage.
        self._page_state = {}
        # Achats en cours d'exécution, clé (character_id, item_id) : anti double exécution (#4).
        self._processing_purchases = set()
        # Actions staff en cours d'exécution (modif/suppr item/catégorie) : anti double exécution (#4).
        # Clé type ("item_edit"|"item_del"|"cat_edit"|"cat_del", id_cible).
        self._processing_actions = set()

    # ---------- verrou de flux (partagé avec /inventaire) ----------
    def _shared_lock(self):
        """Verrou de flux PARTAGÉ avec cogs/inventaire.py : on réutilise le même set _active_users
        (celui du cog Inventaire) quand il est présent, pour qu'un joueur ne puisse jamais avoir deux
        flux textuels concurrents entre /shop et /inventaire (deux wait_for capteraient sinon le même
        message). Repli sur le set local si le cog Inventaire n'est pas chargé."""
        inv = self.bot.get_cog("Inventaire")
        if inv is not None and hasattr(inv, "_active_users"):
            return inv._active_users
        return self._active_users

    def _acquire(self, *user_ids) -> bool:
        lock = self._shared_lock()
        ids = [u for u in user_ids if u is not None]
        if any(u in lock for u in ids):
            return False
        lock.update(ids)
        return True

    def _release(self, *user_ids):
        lock = self._shared_lock()
        for u in user_ids:
            if u is not None:
                lock.discard(u)

    # ---------- utilitaires d'attente ----------
    async def wait_message(self, channel, author, timeout: int = WAIT_TIMEOUT):
        # Filtre STRICT : uniquement l'auteur attendu à CETTE étape ET le salon d'origine du flux
        # (règle de robustesse #2 : jamais un wait_for global).
        def check(m):
            return m.channel.id == channel.id and m.author.id == author.id and not m.author.bot
        try:
            return await self.bot.wait_for("message", check=check, timeout=timeout)
        except asyncio.TimeoutError:
            return None

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
        view = ShopCharacterSelectView(chars, invoker_id)
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

    # ---------- rendu d'une page de catégorie ----------
    def _render_category_page(self, cat_id, page):
        """Génère l'image de la page demandée. Retourne (chemin, total_pages, page_clampée)."""
        rows = get_items_in_category(cat_id)
        pages = [rows[i:i + ITEMS_PER_PAGE] for i in range(0, len(rows), ITEMS_PER_PAGE)] or [[]]
        total_pages = len(pages)
        page = max(0, min(page, total_pages - 1))
        page_rows = pages[page]
        items = [
            (r["name"], r["description"] or "", r["classe"] or "sans", f"{(r['valeur_base'] or 0):,} ¥")
            for r in page_rows
        ]
        path = _tmp_shop("shop")
        generate_shop_image(items, page + 1, total_pages, path)  # page 1-based pour l'image
        return path, total_pages, page

    # ---------- commande ----------
    @app_commands.command(name="shop", description="Accède à la boutique")
    async def shop(self, interaction: discord.Interaction):
        is_staff = _is_staff(interaction.user)
        categories = get_shop_categories()
        if categories:
            desc = "Pour voir le shop, choisis une catégorie."
        else:
            desc = "Aucune catégorie n'a encore été créée."
        embed = discord.Embed(title="🛒 Boutique — Banque Phénix", description=desc, color=PHOENIX_COLOR)
        view = MainShopView(interaction.user.id, is_staff, categories)
        if not view.children:  # ni menu déroulant ni boutons (aucune catégorie + non-staff)
            view = None
        await interaction.response.send_message(embed=embed, view=view)

    # =================================================================
    # LISTENER : boutons persistants (custom_id dynamiques)
    # =================================================================
    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        if interaction.type != discord.InteractionType.component:
            return
        cid = interaction.data.get("custom_id", "")
        if cid.startswith("shop_cat_create:"):
            await self.handle_cat_create(interaction, cid)
        elif cid.startswith("shop_cat_edit:"):
            await self.handle_cat_edit(interaction, cid)
        elif cid.startswith("shop_cat_delete:"):
            await self.handle_cat_delete(interaction, cid)
        elif cid.startswith("shop_item_create:"):
            await self.handle_item_create(interaction, cid)
        elif cid.startswith("shop_cat:"):
            await self.handle_cat_selected(interaction, cid)
        elif cid.startswith("shop_page_prev:"):
            await self.handle_page(interaction, cid, "prev")
        elif cid.startswith("shop_page_next:"):
            await self.handle_page(interaction, cid, "next")
        elif cid.startswith("shop_info:"):
            await self.handle_info(interaction, cid)
        elif cid.startswith("shop_buy:"):
            await self.handle_buy(interaction, cid)
        elif cid.startswith("shop_edit_item:"):
            await self.handle_edit_item(interaction, cid)
        elif cid.startswith("shop_delete_item:"):
            await self.handle_delete_item(interaction, cid)

    # =================================================================
    # SÉLECTION D'UNE CATÉGORIE + PAGINATION
    # =================================================================
    async def handle_cat_selected(self, interaction, cid):
        user_id = int(cid.split(":")[1])
        if interaction.user.id != user_id:
            await interaction.response.send_message("Ce menu n'est pas le tien.", ephemeral=True)
            return
        cat_id = int(interaction.data.get("values", ["0"])[0])
        await interaction.response.defer()
        cat = get_shop_category(cat_id)
        if not cat:
            await interaction.channel.send("Cette catégorie n'existe plus.")
            return
        if not get_items_in_category(cat_id):
            await interaction.channel.send(
                f"La catégorie **{cat['name']}** ne contient aucun objet pour l'instant."
            )
            return
        self._page_state[(user_id, cat_id)] = {"page": 0}
        path, total_pages, page = self._render_category_page(cat_id, 0)
        view = ShopCategoryPageView(cat_id, user_id, page, total_pages, _is_staff(interaction.user))
        await interaction.channel.send(file=discord.File(path, filename="shop.png"), view=view)
        try:
            os.remove(path)
        except OSError:
            pass

    async def handle_page(self, interaction, cid, direction):
        _, cat_id, user_id = cid.split(":")
        cat_id, user_id = int(cat_id), int(user_id)
        if interaction.user.id != user_id:
            await interaction.response.send_message("Cette pagination n'est pas la tienne.", ephemeral=True)
            return
        state = self._page_state.get((user_id, cat_id))
        if state is None:
            await interaction.response.send_message(
                "Cette pagination a expiré, ré-ouvre la catégorie via le menu.", ephemeral=True
            )
            return
        new_page = state["page"] + (1 if direction == "next" else -1)
        path, total_pages, page = self._render_category_page(cat_id, new_page)
        state["page"] = page
        view = ShopCategoryPageView(cat_id, user_id, page, total_pages, _is_staff(interaction.user))
        await interaction.response.edit_message(
            attachments=[discord.File(path, filename="shop.png")], view=view
        )
        try:
            os.remove(path)
        except OSError:
            pass

    # =================================================================
    # INFORMATION
    # =================================================================
    async def handle_info(self, interaction, cid):
        _, cat_id, user_id = cid.split(":")
        cat_id, user_id = int(cat_id), int(user_id)
        if interaction.user.id != user_id:
            await interaction.response.send_message("Ce panneau n'est pas le tien.", ephemeral=True)
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
                channel, interaction.user,
                "Écris le nom de l'objet que tu veux consulter (ou les premières lettres).",
                lambda q: search_items_in_category(cat_id, q),
            )
            if item is None:
                await channel.send("⏳ Consultation annulée.")
                return
            classe = item["classe"] or "sans"
            embed = discord.Embed(title=f"ℹ️ {item['name']}", color=PHOENIX_COLOR)
            embed.add_field(name="Description", value=item["description"] or "—", inline=False)
            embed.add_field(name="Prix", value=f"{item['valeur_base'] or 0:,} ¥", inline=True)
            embed.add_field(
                name="Classe", value=("Sans classe" if classe == "sans" else f"Classe {classe}"),
                inline=True,
            )
            await channel.send(embed=embed)
        finally:
            self._release(user_id)

    # =================================================================
    # ACHAT
    # =================================================================
    async def handle_buy(self, interaction, cid):
        _, cat_id, user_id = cid.split(":")
        cat_id, user_id = int(cat_id), int(user_id)
        if interaction.user.id != user_id:
            await interaction.response.send_message("Ce panneau n'est pas le tien.", ephemeral=True)
            return
        if not self._acquire(user_id):
            await interaction.response.send_message(
                "Tu as déjà une action en cours, termine la d'abord.", ephemeral=True
            )
            return
        try:
            await interaction.response.send_message("🛒 Achat en cours…", ephemeral=True)
            channel = interaction.channel

            character_id = await self.select_character_await(
                channel, interaction.user, interaction.user.id, "Tu n'as aucun personnage validé."
            )
            if character_id is None:
                await channel.send("⏳ Achat annulé.")
                return

            # Le compte bancaire et l'inventaire de ce personnage sont automatiquement la cible.
            if not get_account(character_id):
                await channel.send(
                    "❌ Ce personnage n'a pas de compte bancaire. Ouvre-en un avec /banque avant d'acheter."
                )
                return

            item = await self.resolve_item(
                channel, interaction.user,
                "Écris le nom de l'objet à acheter (ou les premières lettres).",
                lambda q: search_items_in_category(cat_id, q),
            )
            if item is None:
                await channel.send("⏳ Achat annulé.")
                return

            qty = await self.ask_quantity(channel, interaction.user, "Quelle quantité veux tu acheter ?")
            if qty is None:
                await channel.send("⏳ Achat annulé.")
                return

            # Prix affiché au récapitulatif : INDICATIF. Le prix réellement débité sera relu en temps
            # réel dans _do_purchase (règle de robustesse #3).
            prix_unitaire = item["valeur_base"] or 0
            prix_total = prix_unitaire * qty

            recap = discord.Embed(title="🧾 Récapitulatif de l'achat", color=PHOENIX_COLOR)
            recap.add_field(name="Objet", value=item["name"], inline=False)
            recap.add_field(name="Quantité", value=str(qty), inline=True)
            recap.add_field(name="Prix unitaire", value=f"{prix_unitaire:,} ¥", inline=True)
            recap.add_field(name="Prix total", value=f"{prix_total:,} ¥", inline=True)
            cview = ConfirmView(
                user_id, "Accepter", "Refuser", "✅", "❌",
                yes_cid=f"shop_confirm_buy:{character_id}:{item['id']}:{qty}:{user_id}",
                no_cid=f"shop_cancel_buy:{user_id}",
            )
            await channel.send(embed=recap, view=cview)
            await cview.wait()
            if cview.result is not True:
                await channel.send("Achat annulé.")
                return

            # Vérification du solde au moment de valider.
            account = get_account(character_id)
            if not account:
                await channel.send("❌ Le compte bancaire de ce personnage est introuvable. Achat annulé.")
                return
            solde = account["solde_courant"]
            if solde < prix_total:
                resultant = solde - prix_total
                warn = discord.Embed(
                    description=(
                        f"⚠️ Attention, après cet achat ton solde sera de {resultant:,} ¥. "
                        "Si ce montant descend sous les -100 ¥, le compte à rebours de suppression de "
                        "ton compte bancaire sera automatiquement lancé. Veux tu continuer quand même ?"
                    ),
                    color=PHOENIX_COLOR,
                )
                fview = ConfirmView(
                    user_id, "Continuer", "Annuler", "✅", "❌",
                    yes_cid=f"shop_force_buy:{character_id}:{item['id']}:{qty}:{user_id}",
                    no_cid=f"shop_cancel_buy:{user_id}",
                )
                await channel.send(embed=warn, view=fview)
                await fview.wait()
                if fview.result is not True:
                    await channel.send("Achat annulé.")
                    return

            await self._do_purchase(interaction.guild, channel, character_id, item["id"], qty)
        finally:
            self._release(user_id)

    async def _do_purchase(self, guild, channel, character_id, item_id, qty):
        # Règle de robustesse #4 : verrou en mémoire pour ignorer un double déclenchement ultra rapide.
        key = (character_id, item_id)
        if key in self._processing_purchases:
            return
        self._processing_purchases.add(key)
        try:
            # Règle de robustesse #3 : on relit l'item EN TEMPS RÉEL juste avant le débit et on
            # applique TOUJOURS le prix en vigueur maintenant (pas celui affiché au récapitulatif).
            fresh = get_item(item_id)
            if fresh is None:
                await channel.send("Cet objet n'est plus disponible, l'achat a été annulé.")
                return
            prix_unitaire = fresh["valeur_base"] or 0
            prix_total = prix_unitaire * qty

            # Règle de robustesse #3 : le personnage doit exister TOUJOURS au moment exact du débit
            # (SELECT * FROM validated_characters WHERE id = ?), sinon on n'exécute rien.
            if get_character(character_id) is None:
                await channel.send("Ce personnage n'existe plus, l'achat a été annulé.")
                return

            banque = self.bot.get_cog("Banque")
            if banque is not None:
                # Vraie fonction de banque.py : protection Livret A + compte à rebours -100 / -100000.
                await banque.apply_debit(character_id, prix_total, guild, compte="courant")
            else:  # secours (le cog Banque devrait toujours être chargé)
                with db.get_connection() as conn:
                    conn.execute(
                        "UPDATE bank_accounts SET solde_courant = solde_courant - ? WHERE character_id = ?",
                        (prix_total, character_id),
                    )

            inv_add(character_id, item_id, qty)
            add_transaction(character_id, f"Achat — {fresh['name']} x{qty}", -prix_total)

            char = get_character(character_id)
            name = char["character_name"] if char else "?"
            account = get_account(character_id)
            solde = account["solde_courant"] if account else 0
            msg = (f"✅ **{qty} × {fresh['name']}** ajouté(s) à l'inventaire de **{name}** "
                   f"pour {prix_total:,} ¥.\nNouveau solde : {solde:,} ¥.")
            if account and account["is_at_risk"] and account["deletion_deadline"]:
                msg += (f"\n⏳ Compte sous surveillance : suppression prévue le "
                        f"{_fmt_date(account['deletion_deadline'])} sans redressement.")
            await channel.send(msg)
        finally:
            self._processing_purchases.discard(key)

    # =================================================================
    # STAFF : GESTION DES CATÉGORIES
    # =================================================================
    async def handle_cat_create(self, interaction, cid):
        # Règle de robustesse #1 : revérification du rôle staff AU CLIC, avant toute autre logique.
        if not _is_staff(interaction.user):
            await interaction.response.send_message(NO_PERM_MSG, ephemeral=True)
            return
        user_id = int(cid.split(":")[1])
        if interaction.user.id != user_id:
            await interaction.response.send_message("Ce panneau ne t'appartient pas.", ephemeral=True)
            return
        if not self._acquire(user_id):
            await interaction.response.send_message(
                "Tu as déjà une action en cours, termine la d'abord.", ephemeral=True
            )
            return
        try:
            await interaction.response.send_message("➕ Création d'une catégorie…", ephemeral=True)
            channel = interaction.channel
            await channel.send("Quel est le nom de la nouvelle catégorie ?")
            m = await self.wait_message(channel, interaction.user)
            if m is None:
                await channel.send("⏳ Annulé.")
                return
            name = m.content.strip()
            if not name:
                await channel.send("Nom vide, opération annulée.")
                return
            if get_category_by_name_ci(name):
                await channel.send(f"❌ Une catégorie nommée **{name}** existe déjà.")
                return
            create_category(name)
            await channel.send(f"✅ Catégorie **{name}** créée.")
        finally:
            self._release(user_id)

    async def handle_cat_edit(self, interaction, cid):
        if not _is_staff(interaction.user):
            await interaction.response.send_message(NO_PERM_MSG, ephemeral=True)
            return
        user_id = int(cid.split(":")[1])
        if interaction.user.id != user_id:
            await interaction.response.send_message("Ce panneau ne t'appartient pas.", ephemeral=True)
            return
        if not self._acquire(user_id):
            await interaction.response.send_message(
                "Tu as déjà une action en cours, termine la d'abord.", ephemeral=True
            )
            return
        try:
            cats = get_shop_categories()
            if not cats:
                await interaction.response.send_message("Aucune catégorie à modifier.", ephemeral=True)
                return
            await interaction.response.send_message("✏️ Modification d'une catégorie…", ephemeral=True)
            channel = interaction.channel
            sview = CategorySelectView(cats, user_id, require_staff=True)
            await channel.send("Choisis la catégorie à renommer :", view=sview)
            await sview.wait()
            if sview.result is None:
                await channel.send("⏳ Annulé.")
                return
            cat_id = sview.result
            await channel.send("Écris le nouveau nom.")
            m = await self.wait_message(channel, interaction.user)
            if m is None:
                await channel.send("⏳ Annulé.")
                return
            new_name = m.content.strip()
            if not new_name:
                await channel.send("Nom vide, opération annulée.")
                return
            existing = get_category_by_name_ci(new_name)
            if existing and existing["id"] != cat_id:
                await channel.send(f"❌ Une catégorie nommée **{new_name}** existe déjà.")
                return

            # Anti double exécution + revérification EN TEMPS RÉEL juste avant le UPDATE final (#3/#4).
            key = ("cat_edit", cat_id)
            if key in self._processing_actions:
                return
            self._processing_actions.add(key)
            try:
                if get_shop_category(cat_id) is None:
                    await channel.send(
                        "Cette catégorie a été supprimée entre temps, la modification a été annulée."
                    )
                    return
                rename_category(cat_id, new_name)
                await channel.send(
                    f"✅ Catégorie renommée en **{new_name}**. Ce changement se répercute automatiquement "
                    "partout où la catégorie est affichée (shop et /inventaire), puisque tout référence "
                    "l'identifiant de catégorie et non un texte dupliqué."
                )
            finally:
                self._processing_actions.discard(key)
        finally:
            self._release(user_id)

    async def handle_cat_delete(self, interaction, cid):
        if not _is_staff(interaction.user):
            await interaction.response.send_message(NO_PERM_MSG, ephemeral=True)
            return
        user_id = int(cid.split(":")[1])
        if interaction.user.id != user_id:
            await interaction.response.send_message("Ce panneau ne t'appartient pas.", ephemeral=True)
            return
        if not self._acquire(user_id):
            await interaction.response.send_message(
                "Tu as déjà une action en cours, termine la d'abord.", ephemeral=True
            )
            return
        try:
            cats = get_shop_categories()
            if not cats:
                await interaction.response.send_message("Aucune catégorie à supprimer.", ephemeral=True)
                return
            await interaction.response.send_message("🗑️ Suppression d'une catégorie…", ephemeral=True)
            channel = interaction.channel
            sview = CategorySelectView(cats, user_id, require_staff=True)
            await channel.send("Choisis la catégorie à supprimer :", view=sview)
            await sview.wait()
            if sview.result is None:
                await channel.send("⏳ Annulé.")
                return
            cat_id = sview.result
            cat = get_shop_category(cat_id)
            if not cat:
                await channel.send("Cette catégorie n'existe plus.")
                return
            embed = discord.Embed(
                description=(
                    f"Es tu sûr de vouloir supprimer la catégorie **{cat['name']}** ? "
                    "Tous ses objets seront supprimés, et les joueurs en ayant acheté seront "
                    "intégralement remboursés."
                ),
                color=discord.Color.red(),
            )
            confirm = ConfirmView(user_id, "Confirmer", "Annuler", "✅", "❌", require_staff=True)
            await channel.send(embed=embed, view=confirm)
            await confirm.wait()
            if confirm.result is not True:
                await channel.send("Suppression annulée.")
                return

            # Anti double exécution + revérification EN TEMPS RÉEL avant remboursements + DELETE (#3/#4).
            # La View de confirmation est déjà désactivée dès le premier clic (ConfirmView._finish).
            key = ("cat_del", cat_id)
            if key in self._processing_actions:
                return
            self._processing_actions.add(key)
            try:
                cat = get_shop_category(cat_id)
                if cat is None:
                    await channel.send(
                        "Cette catégorie a déjà été supprimée entre temps, aucune action nécessaire."
                    )
                    return
                summary = self._delete_category_with_refunds(cat_id)
                report = (f"✅ La catégorie **{cat['name']}** a été supprimée, tous les joueurs concernés "
                          "ont été intégralement remboursés.")
                if summary:
                    report += "\n" + summary
                for chunk in _chunk_message(report):
                    await channel.send(chunk)
            finally:
                self._processing_actions.discard(key)
        finally:
            self._release(user_id)

    def _delete_category_with_refunds(self, cat_id) -> str:
        """Rembourse chaque possesseur de chaque item de la catégorie, puis supprime items,
        lignes d'inventaire et la catégorie. Retourne un récapitulatif des remboursements."""
        cat = get_shop_category(cat_id)
        cat_name = cat["name"] if cat else "?"
        items = get_items_in_category(cat_id)
        item_ids = [it["id"] for it in items]
        refund_lines = []
        for it in items:
            with db.get_connection() as conn:
                holders = conn.execute(
                    "SELECT character_id, quantity FROM character_inventory "
                    "WHERE item_id = ? AND quantity > 0",
                    (it["id"],),
                ).fetchall()
            for h in holders:
                montant = (it["valeur_base"] or 0) * h["quantity"]
                if montant > 0 and _refund_character(
                    h["character_id"], montant, f"Remboursement — suppression catégorie {cat_name}"
                ):
                    char = get_character(h["character_id"])
                    cname = char["character_name"] if char else f"#{h['character_id']}"
                    refund_lines.append(f"• {cname} : +{montant:,} ¥ ({h['quantity']} × {it['name']})")
        with db.get_connection() as conn:
            if item_ids:
                placeholders = ",".join("?" * len(item_ids))
                conn.execute(
                    f"DELETE FROM character_inventory WHERE item_id IN ({placeholders})", item_ids
                )
            conn.execute("DELETE FROM item_definitions WHERE categorie_id = ?", (cat_id,))
            conn.execute("DELETE FROM shop_categories WHERE id = ?", (cat_id,))
        return "\n".join(refund_lines)

    # =================================================================
    # STAFF : CRÉATION D'ITEMS EN MASSE
    # =================================================================
    async def handle_item_create(self, interaction, cid):
        if not _is_staff(interaction.user):
            await interaction.response.send_message(NO_PERM_MSG, ephemeral=True)
            return
        user_id = int(cid.split(":")[1])
        if interaction.user.id != user_id:
            await interaction.response.send_message("Ce panneau ne t'appartient pas.", ephemeral=True)
            return
        if not self._acquire(user_id):
            await interaction.response.send_message(
                "Tu as déjà une action en cours, termine la d'abord.", ephemeral=True
            )
            return
        try:
            cats = get_shop_categories()
            if not cats:
                await interaction.response.send_message(
                    "Aucune catégorie n'existe encore. Crée d'abord une catégorie via "
                    "« ➕ Créer une catégorie ».", ephemeral=True,
                )
                return
            await interaction.response.send_message("🆕 Création d'objets…", ephemeral=True)
            channel = interaction.channel
            sview = CategorySelectView(cats, user_id, require_staff=True)
            await channel.send("Pour quelle catégorie veux tu créer ces objets ?", view=sview)
            await sview.wait()
            if sview.result is None:
                await channel.send("⏳ Annulé.")
                return
            cat_id = sview.result
            await channel.send(
                "Colle la liste des objets à créer, au format :\n"
                "`N: nom / descr: description / prix: montant / classe: S,1,2,3,4 ou sans`\n\n"
                "Tu peux en créer plusieurs d'un coup, un par ligne ou à la suite."
            )
            m = await self.wait_message(channel, interaction.user)
            if m is None:
                await channel.send("⏳ Annulé.")
                return
            valid, errors = parse_bulk_items(m.content)
            if not valid and not errors:
                await channel.send(
                    "❌ Format non reconnu : aucun champ « nom: » détecté. Réessaie via le bouton."
                )
                return

            created = 0
            all_errors = list(errors)
            for it in valid:
                # Doublon avec un objet DÉJÀ en base (les doublons INTRA-lot sont gérés par le parseur).
                if get_item_by_name_ci(it["name"]):
                    all_errors.append((it["order"], f"un objet nommé « {it['name']} » existe déjà"))
                    continue
                create_item(it["name"], it["description"], it["classe"], it["prix"], cat_id)
                created += 1

            report = f"✅ {created} objet(s) créé(s) avec succès."
            if all_errors:
                all_errors.sort(key=lambda e: e[0])
                report += "\n⚠️ Les objets suivants n'ont pas pu être créés :\n" + "\n".join(
                    f"• Objet numéro {n} : {msg}" for n, msg in all_errors
                )
            for chunk in _chunk_message(report):
                await channel.send(chunk)
        finally:
            self._release(user_id)

    # =================================================================
    # STAFF : MODIFIER UN ITEM
    # =================================================================
    async def handle_edit_item(self, interaction, cid):
        if not _is_staff(interaction.user):
            await interaction.response.send_message(NO_PERM_MSG, ephemeral=True)
            return
        _, cat_id, user_id = cid.split(":")
        cat_id, user_id = int(cat_id), int(user_id)
        if interaction.user.id != user_id:
            await interaction.response.send_message("Ce panneau ne t'appartient pas.", ephemeral=True)
            return
        if not self._acquire(user_id):
            await interaction.response.send_message(
                "Tu as déjà une action en cours, termine la d'abord.", ephemeral=True
            )
            return
        try:
            await interaction.response.send_message("✏️ Modification d'un item…", ephemeral=True)
            channel = interaction.channel
            item = await self.resolve_item(
                channel, interaction.user,
                "Écris le nom de l'objet à modifier (ou les premières lettres).",
                lambda q: search_items_in_category(cat_id, q),
            )
            if item is None:
                await channel.send("⏳ Annulé.")
                return
            classe = item["classe"] or "sans"
            info = discord.Embed(title=f"✏️ {item['name']}", color=PHOENIX_COLOR)
            info.add_field(name="Description", value=item["description"] or "—", inline=False)
            info.add_field(name="Prix", value=f"{item['valeur_base'] or 0:,} ¥", inline=True)
            info.add_field(
                name="Classe", value=("Sans classe" if classe == "sans" else f"Classe {classe}"),
                inline=True,
            )
            await channel.send(embed=info)

            param = None
            while param is None:
                await channel.send("Quel paramètre veux tu modifier ? (nom / description / prix / classe)")
                m = await self.wait_message(channel, interaction.user)
                if m is None:
                    await channel.send("⏳ Annulé.")
                    return
                p = m.content.strip().lower()
                if p in ("nom", "description", "prix", "classe"):
                    param = p
                else:
                    await channel.send("Réponds par : nom, description, prix ou classe.")

            await channel.send(f"Quelle est la nouvelle valeur pour « {param} » ?")
            m = await self.wait_message(channel, interaction.user)
            if m is None:
                await channel.send("⏳ Annulé.")
                return
            val = m.content.strip()

            # Anti double exécution + revérification EN TEMPS RÉEL juste avant le UPDATE final (#3/#4).
            key = ("item_edit", item["id"])
            if key in self._processing_actions:
                return
            self._processing_actions.add(key)
            try:
                if get_item(item["id"]) is None:
                    await channel.send(
                        "Cet objet a été supprimé entre temps, la modification a été annulée."
                    )
                    return
                if param == "nom":
                    if not val:
                        await channel.send("Nom vide, opération annulée.")
                        return
                    existing = get_item_by_name_ci(val)
                    if existing and existing["id"] != item["id"]:
                        await channel.send(f"❌ Un objet nommé « {val} » existe déjà.")
                        return
                    update_item(item["id"], name=val)
                elif param == "description":
                    update_item(item["id"], description=val)
                elif param == "prix":
                    cleaned = val.replace(" ", "").replace(",", "").replace("¥", "")
                    if not cleaned.isdigit() or int(cleaned) <= 0:
                        await channel.send("❌ Prix invalide (entier positif attendu). Opération annulée.")
                        return
                    update_item(item["id"], valeur_base=int(cleaned))
                else:  # classe
                    if "sans" in val.lower():
                        newc = "sans"
                    else:
                        norm = val.upper()
                        if norm not in ("S", "1", "2", "3", "4"):
                            await channel.send("❌ Classe invalide (S, 1, 2, 3, 4 ou sans). Opération annulée.")
                            return
                        newc = norm
                    update_item(item["id"], classe=newc)

                await channel.send(
                    "✅ Objet mis à jour. La modification est automatiquement visible partout (shop et "
                    "inventaires des joueurs qui le possèdent déjà), puisque tout référence le même item."
                )
            finally:
                self._processing_actions.discard(key)
        finally:
            self._release(user_id)

    # =================================================================
    # STAFF : SUPPRIMER UN ITEM
    # =================================================================
    async def handle_delete_item(self, interaction, cid):
        if not _is_staff(interaction.user):
            await interaction.response.send_message(NO_PERM_MSG, ephemeral=True)
            return
        _, cat_id, user_id = cid.split(":")
        cat_id, user_id = int(cat_id), int(user_id)
        if interaction.user.id != user_id:
            await interaction.response.send_message("Ce panneau ne t'appartient pas.", ephemeral=True)
            return
        if not self._acquire(user_id):
            await interaction.response.send_message(
                "Tu as déjà une action en cours, termine la d'abord.", ephemeral=True
            )
            return
        try:
            await interaction.response.send_message("🗑️ Suppression d'un item…", ephemeral=True)
            channel = interaction.channel
            item = await self.resolve_item(
                channel, interaction.user,
                "Écris le nom de l'objet à supprimer (ou les premières lettres).",
                lambda q: search_items_in_category(cat_id, q),
            )
            if item is None:
                await channel.send("⏳ Annulé.")
                return
            embed = discord.Embed(
                description=(
                    f"Es tu sûr de vouloir supprimer **{item['name']}** ? Tous les joueurs en "
                    "possédant seront remboursés selon leur quantité."
                ),
                color=discord.Color.red(),
            )
            confirm = ConfirmView(user_id, "Confirmer", "Annuler", "✅", "❌", require_staff=True)
            await channel.send(embed=embed, view=confirm)
            await confirm.wait()
            if confirm.result is not True:
                await channel.send("Suppression annulée.")
                return

            # Anti double exécution + revérification EN TEMPS RÉEL avant remboursements + DELETE (#3/#4).
            # La View de confirmation est déjà désactivée dès le premier clic (ConfirmView._finish).
            key = ("item_del", item["id"])
            if key in self._processing_actions:
                return
            self._processing_actions.add(key)
            try:
                fresh = get_item(item["id"])
                if fresh is None:
                    await channel.send("Cet objet a déjà été supprimé entre temps, aucune action nécessaire.")
                    return
                summary = self._delete_item_with_refunds(fresh)
                if summary:
                    report = f"✅ **{fresh['name']}** a été supprimé.\nRemboursements effectués :\n{summary}"
                else:
                    report = f"✅ **{fresh['name']}** a été supprimé.\nAucun joueur ne le possédait."
                for chunk in _chunk_message(report):
                    await channel.send(chunk)
            finally:
                self._processing_actions.discard(key)
        finally:
            self._release(user_id)

    def _delete_item_with_refunds(self, item) -> str:
        """Rembourse chaque personnage possédant l'item (montant = valeur_base × quantité, calculé
        PAR PERSONNAGE sur SON propre compte), puis supprime l'item et ses lignes d'inventaire."""
        with db.get_connection() as conn:
            holders = conn.execute(
                "SELECT character_id, quantity FROM character_inventory "
                "WHERE item_id = ? AND quantity > 0",
                (item["id"],),
            ).fetchall()
        lines = []
        for h in holders:
            montant = (item["valeur_base"] or 0) * h["quantity"]
            if montant > 0 and _refund_character(
                h["character_id"], montant, f"Remboursement — suppression de {item['name']}"
            ):
                char = get_character(h["character_id"])
                cname = char["character_name"] if char else f"#{h['character_id']}"
                lines.append(f"• {cname} : +{montant:,} ¥ ({h['quantity']} × {item['name']})")
        with db.get_connection() as conn:
            conn.execute("DELETE FROM character_inventory WHERE item_id = ?", (item["id"],))
            conn.execute("DELETE FROM item_definitions WHERE id = ?", (item["id"],))
        return "\n".join(lines)


async def setup(bot):
    await bot.add_cog(Shop(bot))
