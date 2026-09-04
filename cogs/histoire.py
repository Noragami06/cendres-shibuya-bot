import asyncio
import difflib
import re
from datetime import datetime

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

from cogs.utils import database as db

# Rôle staff global (défini localement, comme dans les autres cogs).
FICHE_STAFF_ROLE_ID = 1521229332075512039
EMBED_COLOR = discord.Color.blurple()

HISTORY_PER_PAGE = 15          # entrées par page dans le flux staff
DISCORD_CHUNK = 1900           # marge sous la limite Discord de 2000 caractères
TITLE_SIMILARITY_THRESHOLD = 0.4
CONTEXT_WINDOW = 800           # caractères avant/après une occurrence (recherche par mots clés)


# =====================================================================
# UTILITAIRES GOOGLE DOC
# =====================================================================
def extract_gdoc_id(url: str):
    """Extrait l'ID d'un Google Doc depuis n'importe quel format d'URL courant
    (/edit, /view, /export, avec ou sans paramètres après)."""
    match = re.search(r"/document/d/([a-zA-Z0-9_-]+)", url)
    return match.group(1) if match else None


async def verify_gdoc_accessible(gdoc_id: str) -> bool:
    """Vérifie que le document est bien accessible publiquement en tentant l'export texte.
    Retourne False si le document est privé/inaccessible ou si l'ID est invalide."""
    export_url = f"https://docs.google.com/document/d/{gdoc_id}/export?format=txt"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(export_url, allow_redirects=True,
                                   timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    return False
                content_type = resp.headers.get("Content-Type", "")
                if "text/plain" not in content_type:
                    return False  # Google redirige vers une page de connexion/erreur HTML si privé
                text = await resp.text()
                return len(text.strip()) > 0
    except Exception:
        return False


async def fetch_gdoc_text(gdoc_id: str) -> str:
    export_url = f"https://docs.google.com/document/d/{gdoc_id}/export?format=txt"
    async with aiohttp.ClientSession() as session:
        async with session.get(export_url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            return await resp.text()


# =====================================================================
# RECHERCHE "AU MIEUX" DANS LE DOCUMENT
# =====================================================================
def _looks_like_title(line: str) -> bool:
    """Titre potentiel : ligne courte (< 100 car.), non vide, pas uniquement de la ponctuation."""
    s = line.strip()
    return 0 < len(s) < 100 and any(ch.isalnum() for ch in s)


def _search_gdoc_section(text: str, query: str):
    # La recherche est 'au mieux' : sans convention de mise en forme imposée dans le Google Doc
    # (pas de vrais titres/headers structurés côté Google Docs export texte brut), la détection de section
    # repose sur une heuristique (lignes courtes = titres potentiels + comparaison de similarité). Plus le
    # document est bien structuré avec de vraies lignes de titre courtes et distinctes, plus la recherche
    # sera précise. Pas de garantie à 100% de trouver la bonne section sur un document mal structuré.
    lines = text.split("\n")
    q = query.strip().lower()

    # a/b. Titres potentiels + meilleure correspondance de similarité avec la requête.
    title_idxs = [i for i, l in enumerate(lines) if _looks_like_title(l)]
    best_i, best_score = None, 0.0
    for i in title_idxs:
        score = difflib.SequenceMatcher(None, q, lines[i].strip().lower()).ratio()
        if score > best_score:
            best_score, best_i = score, i

    # c. Titre suffisamment proche -> section depuis ce titre jusqu'au prochain titre (ou fin).
    if best_i is not None and best_score >= TITLE_SIMILARITY_THRESHOLD:
        following = [j for j in title_idxs if j > best_i]
        end = following[0] if following else len(lines)
        section = "\n".join(lines[best_i:end]).strip()
        if section:
            return section

    # d. Sinon, recherche simple par mots clés + fenêtre de contexte autour de la 1ère occurrence.
    words = [w for w in re.findall(r"\w+", q) if len(w) >= 2]
    low = text.lower()
    pos = -1
    for w in words:
        p = low.find(w)
        if p != -1:
            pos = p
            break
    if pos != -1:
        start = max(0, pos - CONTEXT_WINDOW)
        end = min(len(text), pos + CONTEXT_WINDOW)
        return text[start:end].strip()

    # e. Rien trouvé.
    return None


def _chunk_text(s: str, size: int = DISCORD_CHUNK):
    """Découpe en morceaux <= size (pour rester sous la limite Discord)."""
    return [s[i:i + size] for i in range(0, len(s), size)]


# =====================================================================
# VUES INTERACTIVES (en session, pilotées par view.wait())
# =====================================================================
class _CharSelectView(discord.ui.View):
    """Menu déroulant de sélection d'un personnage (réservé au demandeur)."""

    def __init__(self, chars, owner_id):
        super().__init__(timeout=180)
        self.owner_id = owner_id
        self.result = None
        options = [
            discord.SelectOption(
                label=(c["character_name"] or f"Slot {c['slot_number']}")[:100],
                value=str(c["id"]),
                description=f"Slot {c['slot_number']}")
            for c in chars[:25]
        ]
        self.select = discord.ui.Select(placeholder="Choisis le personnage", options=options)
        self.select.callback = self._on_select
        self.add_item(self.select)

    async def _on_select(self, interaction: discord.Interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Ce choix ne t'appartient pas.", ephemeral=True)
            return
        self.result = int(self.select.values[0])
        await interaction.response.defer()
        self.stop()


class _StaffChoiceView(discord.ui.View):
    """📝 Poster mon histoire / 🔍 Consulter une fiche existante."""

    def __init__(self, owner_id):
        super().__init__(timeout=180)
        self.owner_id = owner_id
        self.result = None

    async def _guard(self, interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Ce menu ne t'appartient pas.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Poster mon histoire", emoji="📝", style=discord.ButtonStyle.primary)
    async def post(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._guard(interaction):
            return
        self.result = "post"
        await interaction.response.defer()
        self.stop()

    @discord.ui.button(label="Consulter une fiche existante", emoji="🔍", style=discord.ButtonStyle.secondary)
    async def consult(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._guard(interaction):
            return
        self.result = "consult"
        await interaction.response.defer()
        self.stop()


class _HistoryPageView(discord.ui.View):
    """Pagination ◀️/▶️ de la liste des histoires (15/page). La sélection se fait par un NUMÉRO tapé
    en parallèle (validé contre la liste complète), pas par ces boutons."""

    def __init__(self, lines, owner_id, per_page=HISTORY_PER_PAGE):
        super().__init__(timeout=300)
        self.lines = lines
        self.owner_id = owner_id
        self.per_page = per_page
        self.page = 0
        self.pages = max(1, (len(lines) + per_page - 1) // per_page)

    def embed(self) -> discord.Embed:
        start = self.page * self.per_page
        chunk = self.lines[start:start + self.per_page]
        e = discord.Embed(
            title="🔍 Histoires enregistrées",
            description="\n".join(chunk),
            color=EMBED_COLOR)
        e.set_footer(text=f"Page {self.page + 1}/{self.pages} — réponds avec le NUMÉRO du personnage")
        return e

    async def _guard(self, interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Cette liste ne t'appartient pas.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Page précédente", emoji="◀️", style=discord.ButtonStyle.secondary)
    async def prev(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._guard(interaction):
            return
        self.page = (self.page - 1) % self.pages
        await interaction.response.edit_message(embed=self.embed(), view=self)

    @discord.ui.button(label="Page suivante", emoji="▶️", style=discord.ButtonStyle.secondary)
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._guard(interaction):
            return
        self.page = (self.page + 1) % self.pages
        await interaction.response.edit_message(embed=self.embed(), view=self)


class _SearchButtonView(discord.ui.View):
    """Bouton 🔍 Rechercher sous le lever de doute d'une histoire consultée."""

    def __init__(self, owner_id):
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.clicked = False

    @discord.ui.button(label="Rechercher", emoji="🔍", style=discord.ButtonStyle.primary)
    async def search(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Ce bouton ne t'appartient pas.", ephemeral=True)
            return
        self.clicked = True
        for it in self.children:
            it.disabled = True
        await interaction.response.edit_message(view=self)
        self.stop()


# =====================================================================
# COG
# =====================================================================
class Histoire(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def _wait_text(self, channel, user, timeout: int = 180):
        """Attend un message texte de `user` dans `channel`. None si délai."""
        def check(m):
            return m.channel.id == channel.id and m.author.id == user.id and not m.author.bot
        try:
            return await self.bot.wait_for("message", check=check, timeout=timeout)
        except asyncio.TimeoutError:
            await channel.send("⏳ Temps écoulé.")
            return None

    async def _select_own_character(self, channel, user):
        """Sélection d'un personnage parmi CEUX DU joueur (auto si un seul, menu sinon). id ou None."""
        chars = db.get_validated_characters_for_user(user.id, channel.guild.id)
        if not chars:
            await channel.send("Tu n'as aucun personnage validé.")
            return None
        if len(chars) == 1:
            return chars[0]["id"]
        view = _CharSelectView(chars, user.id)
        await channel.send("Sélectionne le personnage :", view=view)
        await view.wait()
        return view.result

    # -----------------------------------------------------------------
    # FLUX JOUEUR — poster son histoire
    # -----------------------------------------------------------------
    async def _player_flow(self, channel, user):
        character_id = await self._select_own_character(channel, user)
        if character_id is None:
            return
        while True:
            await channel.send(
                "Envoie le lien Google Doc de l'histoire de ce personnage. (ou « annuler »)")
            m = await self._wait_text(channel, user)
            if m is None:
                return
            content = m.content.strip()
            if content.lower() in ("annuler", "cancel"):
                await channel.send("Annulé.")
                return
            gdoc_id = extract_gdoc_id(content)
            if not gdoc_id:
                await channel.send("❌ Ce n'est pas un lien Google Doc valide.")
                continue
            if not await verify_gdoc_accessible(gdoc_id):
                await channel.send(
                    "❌ Je n'arrive pas à lire ce document. Vérifie que le partage est bien réglé sur "
                    "'Public sur le web' ou 'Toute personne disposant du lien', puis réessaie.")
                continue
            db.set_character_history(character_id, content, gdoc_id, datetime.utcnow().isoformat())
            await channel.send("✅ C'est bon, ton document est bien accessible et enregistré.")
            return

    # -----------------------------------------------------------------
    # FLUX STAFF — consulter une fiche existante
    # -----------------------------------------------------------------
    async def _staff_consult_flow(self, channel, staff):
        rows = db.get_all_character_histories()
        if not rows:
            await channel.send("Aucune histoire n'a encore été enregistrée.")
            return

        # Une ligne PAR PERSONNAGE (jamais groupée par joueur).
        lines = [
            f"**{i}.** <@{r['user_id']}> — {r['character_name']} (Slot {r['slot_number']})"
            for i, r in enumerate(rows, 1)
        ]
        view = _HistoryPageView(lines, staff.id)
        await channel.send(
            embed=view.embed(),
            view=view if view.pages > 1 else None,
            allowed_mentions=discord.AllowedMentions.none())
        await channel.send(
            "Réponds avec le **numéro** du personnage dont tu veux consulter l'histoire. (ou « annuler »)")

        # Sélection par numéro validée contre la liste COMPLÈTE (pas seulement la page affichée).
        while True:
            m = await self._wait_text(channel, staff)
            if m is None:
                view.stop()
                return
            content = m.content.strip().lower()
            if content in ("annuler", "cancel"):
                view.stop()
                await channel.send("Annulé.")
                return
            if content.isdigit() and 1 <= int(content) <= len(rows):
                target = rows[int(content) - 1]
                break
            await channel.send(f"Réponds par un numéro entre **1** et **{len(rows)}**.")
        view.stop()

        # Lien + proposition de recherche.
        await channel.send(f"🔗 {target['gdoc_url']}")
        search_view = _SearchButtonView(staff.id)
        await channel.send(
            embed=discord.Embed(
                description="🔍 Si tu cherches une information précise dans ce document, clique sur le "
                            "bouton ci-dessous et indique ce que tu recherches.",
                color=EMBED_COLOR),
            view=search_view)
        await search_view.wait()
        if not search_view.clicked:
            return

        await channel.send(
            "Que recherches-tu dans ce document ? (ex: 'partie histoire/développement du personnage')")
        qm = await self._wait_text(channel, staff)
        if qm is None:
            return
        query = qm.content.strip()

        try:
            text = await fetch_gdoc_text(target["gdoc_id"])
        except Exception:
            await channel.send("❌ Impossible de récupérer le document (réessaie plus tard).")
            return

        result = _search_gdoc_section(text, query)
        if not result:
            await channel.send("❌ Aucune information correspondante trouvée dans le document.")
            return
        for chunk in _chunk_text(result):
            await channel.send(chunk)

    # -----------------------------------------------------------------
    # COMMANDE
    # -----------------------------------------------------------------
    @app_commands.command(name="histoire", description="Gère ou consulte l'histoire d'un personnage")
    async def histoire(self, interaction: discord.Interaction):
        if interaction.guild is None:
            await interaction.response.send_message(
                "Cette commande s'utilise sur le serveur.", ephemeral=True)
            return

        is_staff = any(role.id == FICHE_STAFF_ROLE_ID for role in interaction.user.roles)

        if not is_staff:
            # Joueur normal : directement le flux « poster son histoire » sur SES personnages.
            await interaction.response.send_message("📖 Ouverture de l'histoire…", ephemeral=True)
            await self._player_flow(interaction.channel, interaction.user)
            return

        # Staff : choix entre poster sa propre histoire ou consulter une fiche existante.
        view = _StaffChoiceView(interaction.user.id)
        await interaction.response.send_message(
            embed=discord.Embed(title="📖 Histoire", description="Que veux-tu faire ?", color=EMBED_COLOR),
            view=view)
        await view.wait()
        if view.result == "post":
            await self._player_flow(interaction.channel, interaction.user)
        elif view.result == "consult":
            await self._staff_consult_flow(interaction.channel, interaction.user)


async def setup(bot):
    await bot.add_cog(Histoire(bot))
