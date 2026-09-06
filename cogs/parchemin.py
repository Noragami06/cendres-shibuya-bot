# RÈGLES DE ROBUSTESSE PERMANENTES DU PROJET (rappel) : revérifier les droits au clic, isoler chaque
# flux textuel par utilisateur (jamais un wait_for global), revérifier les données juste avant d'agir,
# et protéger toute action de confirmation contre le double-clic. Appliquées ici par défaut.

import asyncio
import random

import discord
from discord import app_commands
from discord.ext import commands

from cogs.utils import database as db
# Personnages + couleur, comme les autres cogs joueur.
from cogs.banque import get_characters, PHOENIX_COLOR
# Réutilisations SANS duplication : rôles/positionnement RCT, libellés de nature, tirage pondéré,
# et l'identifiant owner déjà défini dans depart (bonus discret partagé).
from cogs.depart import (
    RCT_POSSEDE_ROLE_ID, RCT_NON_POSSEDE_ROLE_ID, NATURE_DISPLAY_NAMES, weighted_choice,
    SPECIAL_USER_ID as OWNER_ID,
)
# Barème des stades RCT (source unique) et détection réel/virtuel standardisée.
from cogs.utils.coherence_check import RCT_STAGES
from cogs.profil import character_has_role, get_current_rct_stage

# =====================================================================
# 1. CONSTANTES
# =====================================================================
PARCHEMIN_BASE_RATE = 10        # % de départ pour RCT et Territoire
PARCHEMIN_MIN_RATE = 1          # plancher, jamais en dessous
PARCHEMIN_DECREMENT = 1         # % perdu à chaque échec

NATURE_PARCHEMIN_TABLE = {
    "sans_nature": 60,
    "brute": 19.5,
    "electrique": 19.5,
    "raffinee": 1,
}

OWNER_FALSIFIED_RATE = 45       # uniquement RCT et Territoire, jamais Nature

WAIT_TIMEOUT = 300              # attente d'une réponse texte
OWNER_DM_TIMEOUT = 20           # délai de réponse au DM owner (falsification)

# Noms EXACTS des parchemins tels que créés dans le shop (catégorie « Parchemin »), vérifiés en base.
PARCHEMIN_ITEMS = {
    "Parchemin RCT": "rct",
    "Parchemin Territoire": "territoire",
    "Parchemin Nature d'EO": "nature",
}
PARCHEMIN_NAMES = list(PARCHEMIN_ITEMS.keys())
TYPE_LABELS = {"rct": "RCT", "territoire": "Territoire", "nature": "Nature d'énergie occulte"}

# Stades de gambling du Territoire (équivalent de RCT_STAGES, propre à /parchemin).
TERRITOIRE_GAMBLE_STAGES = {
    "stage1": {"role_id": 1522181316177563768, "next": "stage2"},
    "stage2": {"role_id": 1522181316718759946, "next": None},
}

# Libellés de repli pour le récapitulatif si le rôle Discord n'est pas résolvable.
_STAGE_FALLBACK_LABELS = {
    RCT_STAGES["moyenne"]["role_id"]: "Maîtrise RCT — Moyenne",
    RCT_STAGES["bonne"]["role_id"]: "Maîtrise RCT — Bonne",
    TERRITOIRE_GAMBLE_STAGES["stage1"]["role_id"]: "Maîtrise Territoire — Stade 1",
    TERRITOIRE_GAMBLE_STAGES["stage2"]["role_id"]: "Maîtrise Territoire — Stade 2",
}


async def get_current_territoire_gamble_stage(guild, character_id):
    """Retourne 'stage2', 'stage1', ou None — même logique que get_current_rct_stage (du plus haut
    stade au plus bas, rôle réel si slot 1, virtuel si slot 2/3)."""
    with db.get_connection() as conn:
        row = conn.execute(
            "SELECT user_id FROM validated_characters WHERE id = ?", (character_id,)
        ).fetchone()
    member = None
    if row is not None and guild is not None:
        member = guild.get_member(row["user_id"])
    for stage in ("stage2", "stage1"):
        role_id = TERRITOIRE_GAMBLE_STAGES[stage]["role_id"]
        if await character_has_role(guild, member, character_id, role_id):
            return stage
    return None


# =====================================================================
# VUES EN SESSION
# =====================================================================
class ParcheminCharacterSelect(discord.ui.Select):
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


class ParcheminCharacterSelectView(discord.ui.View):
    def __init__(self, chars, invoker_id):
        super().__init__(timeout=WAIT_TIMEOUT)
        self.result = None
        self.add_item(ParcheminCharacterSelect(chars, invoker_id))


class _OwnerFalsifyView(discord.ui.View):
    """DM privé owner : boutons Oui/Non pour falsifier le taux. AUCUNE trace ailleurs."""

    def __init__(self, owner_id):
        super().__init__(timeout=OWNER_DM_TIMEOUT)
        self.owner_id = owner_id
        self.value = False
        self._done = False

    async def _finish(self, interaction, value):
        if self._done:
            try:
                await interaction.response.defer()
            except discord.HTTPException:
                pass
            return
        self._done = True
        self.value = value
        try:
            await interaction.response.edit_message(view=None)
        except discord.HTTPException:
            pass
        self.stop()

    @discord.ui.button(label="Oui", style=discord.ButtonStyle.success)
    async def oui(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.owner_id:
            return
        await self._finish(interaction, True)

    @discord.ui.button(label="Non", style=discord.ButtonStyle.danger)
    async def non(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.owner_id:
            return
        await self._finish(interaction, False)


# =====================================================================
# COG
# =====================================================================
class Parchemin(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._active_users = set()  # repli local si le cog Inventaire n'est pas chargé

    # ---------- verrou de flux (partagé avec /inventaire et /shop) ----------
    def _shared_lock(self):
        inv = self.bot.get_cog("Inventaire")
        if inv is not None and hasattr(inv, "_active_users"):
            return inv._active_users
        return self._active_users

    def _acquire(self, user_id) -> bool:
        lock = self._shared_lock()
        if user_id in lock:
            return False
        lock.add(user_id)
        return True

    def _release(self, user_id):
        self._shared_lock().discard(user_id)

    # ---------- utilitaires d'attente (isolés par utilisateur + salon) ----------
    async def wait_message(self, channel, author, timeout: int = WAIT_TIMEOUT):
        def check(m):
            return m.channel.id == channel.id and m.author.id == author.id and not m.author.bot
        try:
            return await self.bot.wait_for("message", check=check, timeout=timeout)
        except asyncio.TimeoutError:
            return None

    async def ask_int(self, channel, user, prompt, minimum=1, maximum=None):
        while True:
            await channel.send(prompt)
            m = await self.wait_message(channel, user)
            if m is None:
                return None
            c = m.content.strip().replace(" ", "")
            if not c.isdigit() or int(c) < minimum:
                await channel.send(f"Entre un nombre entier ≥ {minimum}.")
                continue
            val = int(c)
            if maximum is not None and val > maximum:
                await channel.send(f"Tu ne peux pas en utiliser plus de {maximum}. Réessaie.")
                continue
            return val

    async def select_character_await(self, channel, user):
        chars = get_characters(user.id, channel.guild.id)
        if not chars:
            await channel.send("Tu n'as aucun personnage validé.")
            return None
        if len(chars) == 1:
            return chars[0]["id"]
        view = ParcheminCharacterSelectView(chars, user.id)
        await channel.send("Sélectionne le personnage :", view=view)
        await view.wait()
        return view.result

    # ---------- rôles (réel slot 1 / virtuel slot 2-3), même méthode que /modification ----------
    async def _resolve_member(self, guild, char):
        if guild is None:
            return None
        member = guild.get_member(char["user_id"])
        if member is None:
            try:
                member = await guild.fetch_member(char["user_id"])
            except (discord.NotFound, discord.HTTPException):
                member = None
        return member

    async def _add_role(self, guild, char, role_id, reason):
        """Attribue un rôle à un personnage : réel (Discord) pour le slot 1, virtuel pour les slots 2/3."""
        if char["slot_number"] == 1:
            member = await self._resolve_member(guild, char)
            role = guild.get_role(role_id) if guild else None
            if member is not None and role is not None and role not in member.roles:
                try:
                    await member.add_roles(role, reason=reason)
                except (discord.Forbidden, discord.HTTPException):
                    print(f"[parchemin] Impossible d'attribuer le rôle {role_id} à {char['user_id']}.")
        else:
            db.add_virtual_role(char["id"], role_id)

    async def _swap_role(self, guild, char, old_role_id, new_role_id, reason):
        """Retire old_role_id / attribue new_role_id (réel slot 1 / virtuel slot 2-3)."""
        if char["slot_number"] == 1:
            member = await self._resolve_member(guild, char)
            if member is not None:
                try:
                    r_old = guild.get_role(old_role_id)
                    if r_old and r_old in member.roles:
                        await member.remove_roles(r_old, reason=reason)
                    r_new = guild.get_role(new_role_id)
                    if r_new and r_new not in member.roles:
                        await member.add_roles(r_new, reason=reason)
                except (discord.Forbidden, discord.HTTPException):
                    print(f"[parchemin] Impossible d'échanger les rôles RCT de {char['user_id']}.")
        else:
            db.remove_virtual_role(char["id"], old_role_id)
            db.add_virtual_role(char["id"], new_role_id)

    def _stage_role_name(self, guild, role_id):
        role = guild.get_role(role_id) if guild else None
        return role.name if role is not None else _STAGE_FALLBACK_LABELS.get(role_id, "Nouveau stade")

    # ---------- bonus discret owner (falsification du taux) ----------
    async def _ask_owner_falsify(self, user) -> bool:
        """DM privé à l'owner avec Oui/Non ; True seulement si « Oui » cliqué dans le délai. Toute autre
        issue (Non, timeout, MP fermés) -> False. AUCUNE trace visible où que ce soit."""
        try:
            dm = await user.create_dm()
            view = _OwnerFalsifyView(user.id)
            await dm.send(
                "Veux tu falsifier le taux d'obtention pour ce tirage ? "
                f"({OWNER_FALSIFIED_RATE}% au lieu du taux dégressif normal)",
                view=view,
            )
            await view.wait()  # se termine au clic ou au timeout (OWNER_DM_TIMEOUT)
            return view.value
        except (discord.Forbidden, discord.HTTPException):
            return False

    # ---------- animation de révélation (7 cycles, comme /depart) ----------
    async def _reveal_animation(self, message, titre):
        for i in range(7):
            dots = "." * (i % 4)
            await message.edit(embed=discord.Embed(
                title=titre,
                description=f"🔎 Lecture du parchemin {dots}".rstrip(),
                color=discord.Color.gold(),
            ), view=None)
            await asyncio.sleep(1)

    # =================================================================
    # COMMANDE
    # =================================================================
    @app_commands.command(name="parchemin", description="Utilise un parchemin (RCT, Territoire ou Nature).")
    async def parchemin(self, interaction: discord.Interaction):
        if interaction.guild is None:
            await interaction.response.send_message("Cette commande s'utilise sur le serveur.", ephemeral=True)
            return
        await interaction.response.send_message("📜 Ouverture de tes parchemins…", ephemeral=True)
        channel = interaction.channel
        user = interaction.user
        guild = interaction.guild

        # 1. Personnage.
        character_id = await self.select_character_await(channel, user)
        if character_id is None:
            return

        if not self._acquire(user.id):
            await channel.send("Tu as déjà une action en cours, termine la d'abord.")
            return
        try:
            char = db.get_validated_character_by_id(character_id)
            if char is None:
                await channel.send("Ce personnage n'existe plus.")
                return

            # 2. Parchemins possédés.
            owned = db.get_owned_items_by_names(character_id, PARCHEMIN_NAMES)
            # 3. Aucun parchemin -> arrêt.
            if not owned:
                await channel.send("❌ Tu ne possèdes aucun parchemin.")
                return

            # 4. Plusieurs types -> choix numéroté (isolation par utilisateur).
            if len(owned) == 1:
                parch = owned[0]
            else:
                lignes = "\n".join(f"**{i}.** {r['quantity']}x {r['name']}" for i, r in enumerate(owned, 1))
                await channel.send(embed=discord.Embed(
                    title="📜 Quel parchemin utiliser ?",
                    description=lignes + "\n\nRéponds avec le **numéro** correspondant.",
                    color=PHOENIX_COLOR))
                parch = None
                while parch is None:
                    m = await self.wait_message(channel, user)
                    if m is None:
                        await channel.send("⏳ Annulé.")
                        return
                    c = m.content.strip()
                    if c.isdigit() and 1 <= int(c) <= len(owned):
                        parch = owned[int(c) - 1]
                    else:
                        await channel.send(f"Réponds avec un numéro entre 1 et {len(owned)}.")

            ptype = PARCHEMIN_ITEMS[parch["name"]]
            owned_qty = parch["quantity"]
            item_id = parch["item_id"]

            # Blocage de gambling AVANT toute demande de quantité / consommation (RCT / Territoire).
            if ptype == "rct":
                stage = await get_current_rct_stage(guild, character_id)
                if stage in ("bonne", "avancee"):
                    await channel.send(
                        "❌ Tu as déjà atteint la Maîtrise Bonne (ou supérieure) sur ton RCT — impossible "
                        "de gambler davantage via /parchemin.")
                    return
            elif ptype == "territoire":
                stage = await get_current_territoire_gamble_stage(guild, character_id)
                if stage == "stage2":
                    await channel.send(
                        "❌ Tu as déjà atteint le stade maximum de gambling pour ton Territoire via "
                        "/parchemin.")
                    return

            # 5. Quantité.
            nb = await self.ask_int(
                channel, user,
                f"Combien de parchemins veux tu utiliser ? (tu en as {owned_qty})",
                minimum=1, maximum=owned_qty)
            if nb is None:
                await channel.send("⏳ Annulé.")
                return

            if ptype == "nature":
                await self._run_nature(channel, char, item_id, nb, owned_qty)
            else:
                await self._run_gamble_flow(channel, guild, user, char, ptype, item_id, nb, owned_qty)
        finally:
            self._release(user.id)

    # =================================================================
    # RCT / TERRITOIRE (dégressif, séquentiel)
    # =================================================================
    async def _run_gamble_flow(self, channel, guild, user, char, ptype, item_id, nb, owned_qty):
        character_id = char["id"]

        # 3. Bonus discret owner : proposition de falsification (RCT/Territoire uniquement).
        falsifie = False
        if user.id == OWNER_ID:
            falsifie = await self._ask_owner_falsify(user)

        # 4. Tirage dégressif séquentiel (s'arrête au premier succès).
        taux = OWNER_FALSIFIED_RATE if falsifie else PARCHEMIN_BASE_RATE
        resultats = []          # (numero, taux, succes)
        succes_obtenu = False
        for i in range(1, nb + 1):
            if succes_obtenu:
                break
            roll = random.random() * 100 < taux
            resultats.append((i, taux, roll))
            if roll:
                succes_obtenu = True
            elif not falsifie:
                taux = max(PARCHEMIN_MIN_RATE, taux - PARCHEMIN_DECREMENT)

        consommes = len(resultats)  # < nb si succès obtenu avant la fin
        db.consume_inventory_item(character_id, item_id, consommes)

        # Application de l'effet en cas de succès (+ progression de stade).
        stage_role_id = None
        if succes_obtenu:
            if ptype == "rct":
                stage_role_id = await self._apply_rct_success(guild, char)
            else:
                stage_role_id = await self._apply_territoire_success(guild, char)

        # 7. Récapitulatif, précédé de l'animation de révélation (7 cycles).
        titre = f"📜 Résultats — {TYPE_LABELS[ptype]}"
        msg = await channel.send(embed=discord.Embed(
            title=titre, description="🔎 Lecture du parchemin…", color=discord.Color.gold()))
        await self._reveal_animation(msg, titre)

        lignes = [
            f"Parchemin {num} : {'✅ Réussite !' if ok else '❌ Échec'}"
            for num, _taux, ok in resultats
        ]
        desc = "\n".join(lignes)
        desc += f"\n\n({consommes} parchemin(s) utilisé(s) sur les {nb} demandés)"
        if succes_obtenu:
            if stage_role_id is not None:
                desc += f"\n🎖️ Nouveau stade obtenu : {self._stage_role_name(guild, stage_role_id)}"
            couleur = discord.Color.green()
        else:
            reste = owned_qty - consommes
            desc += f"\n\nAucune réussite cette fois. Il te reste {reste} parchemin(s)."
            couleur = discord.Color.dark_red()
        await msg.edit(embed=discord.Embed(title=titre, description=desc, color=couleur))

    async def _apply_rct_success(self, guild, char):
        """Applique un succès RCT (déblocage + progression de stade). Retourne le role_id du stade
        NOUVELLEMENT attribué, ou None si aucun (cas théorique)."""
        character_id = char["id"]
        stage = await get_current_rct_stage(guild, character_id)
        if stage is None:
            # RCT pas encore possédé : déblocage normal (rct=1 + rôle Possédé) ET rôle « Moyenne ».
            await self._swap_role(guild, char, RCT_NON_POSSEDE_ROLE_ID, RCT_POSSEDE_ROLE_ID,
                                  "Parchemin RCT — déblocage")
            db.update_validated_fields(character_id, rct=1)
            role_id = RCT_STAGES["moyenne"]["role_id"]
            await self._add_role(guild, char, role_id, "Parchemin RCT — Maîtrise Moyenne")
            return role_id
        if stage == "moyenne":
            # Passe à « Bonne ». On ne retire PAS « Moyenne » (détection du plus haut au plus bas).
            role_id = RCT_STAGES["bonne"]["role_id"]
            await self._add_role(guild, char, role_id, "Parchemin RCT — Maîtrise Bonne")
            return role_id
        return None

    async def _apply_territoire_success(self, guild, char):
        """Applique un succès Territoire (déblocage + progression de stade). Retourne le role_id du stade
        NOUVELLEMENT attribué, ou None."""
        character_id = char["id"]
        stage = await get_current_territoire_gamble_stage(guild, character_id)
        if stage is None:
            # Déblocage normal (crée la coquille + is_unlocked=1) ET rôle « stage1 ».
            db.unlock_territoire(character_id)
            role_id = TERRITOIRE_GAMBLE_STAGES["stage1"]["role_id"]
            await self._add_role(guild, char, role_id, "Parchemin Territoire — Stade 1")
            return role_id
        if stage == "stage1":
            role_id = TERRITOIRE_GAMBLE_STAGES["stage2"]["role_id"]
            await self._add_role(guild, char, role_id, "Parchemin Territoire — Stade 2")
            return role_id
        return None

    # =================================================================
    # NATURE (tirage pondéré indépendant, tous consommés)
    # =================================================================
    async def _run_nature(self, channel, char, item_id, nb, owned_qty):
        character_id = char["id"]
        tirages = [weighted_choice(NATURE_PARCHEMIN_TABLE) for _ in range(nb)]
        finale = tirages[-1]  # la dernière valeur du lot prévaut
        db.update_validated_fields(character_id, nature=finale)
        db.consume_inventory_item(character_id, item_id, nb)

        lignes = [
            f"Parchemin {i} : {NATURE_DISPLAY_NAMES.get(nat, nat)}"
            for i, nat in enumerate(tirages, 1)
        ]
        desc = "\n".join(lignes)
        desc += f"\n\nNature finale : **{NATURE_DISPLAY_NAMES.get(finale, finale)}**"
        await channel.send(embed=discord.Embed(
            title="📜 Résultats — Nature d'énergie occulte",
            description=desc,
            color=PHOENIX_COLOR))


async def setup(bot):
    await bot.add_cog(Parchemin(bot))
