# /xp — commande staff : ajoute ou retire de l'XP à un personnage, en cascade (montée ET descente),
# via database.apply_xp_cascade. AUCUN multiplicateur VIP/Booster (le staff saisit une valeur exacte).
# Flux question/réponse en MP, même style que /giveaway : nettoyage des messages, « annuler » partout,
# pas de timeout, reprise sur la seule question échouée.

import asyncio
import re

import discord
from discord import app_commands
from discord.ext import commands

from cogs.utils import database as db

FICHE_STAFF_ROLE_ID = 1521229332075512039


def _is_staff(member) -> bool:
    return any(r.id == FICHE_STAFF_ROLE_ID for r in getattr(member, "roles", []))


def _first_user_id(text: str):
    """Premier identifiant de joueur trouvé : mention <@id> / <@!id> ou ID brut (17-20 chiffres)."""
    m = re.search(r"<@!?(\d{15,25})>", text or "")
    if m:
        return int(m.group(1))
    m = re.search(r"(?<!\d)(\d{15,25})(?!\d)", text or "")
    return int(m.group(1)) if m else None


def _get_user_characters(user_id: int, guild_id: int):
    with db.get_connection() as conn:
        return conn.execute(
            "SELECT id, slot_number, character_name FROM validated_characters "
            "WHERE user_id = ? AND guild_id = ? ORDER BY slot_number", (user_id, guild_id)).fetchall()


class _XpCancel(Exception):
    """Levée dès que le staff répond/clique « annuler »."""


class _ChoiceView(discord.ui.View):
    """Boutons en session (MP) + « Annuler ». Premier clic du staff -> result + stop. Re-vérifie le staff
    à chaque clic (membre résolu via la guilde, car en MP interaction.user est un User sans rôles)."""

    def __init__(self, owner_id, guild, options):  # options : [(key, label, emoji, style)]
        super().__init__(timeout=None)
        self.owner_id = owner_id
        self.guild = guild
        self.result = None
        self.future = asyncio.get_running_loop().create_future()
        for key, label, emoji, style in options:
            b = discord.ui.Button(label=label, emoji=emoji, style=style)
            b.callback = self._cb(key)
            self.add_item(b)
        c = discord.ui.Button(label="Annuler", emoji="✖️", style=discord.ButtonStyle.secondary)
        c.callback = self._cb("__cancel__")
        self.add_item(c)

    def _cb(self, key):
        async def cb(interaction: discord.Interaction):
            if interaction.user.id != self.owner_id:
                await interaction.response.send_message("Ce choix ne t'appartient pas.", ephemeral=True)
                return
            member = self.guild.get_member(interaction.user.id) if self.guild else None
            if member is None or not _is_staff(member):
                await interaction.response.send_message("Réservé au staff.", ephemeral=True)
                return
            self.result = key
            if not self.future.done():
                self.future.set_result(key)
            try:
                await interaction.response.edit_message(view=None)
            except discord.HTTPException:
                pass
            self.stop()
        return cb


class _SelectView(discord.ui.View):
    """Menu déroulant de personnages (slot + nom) + « Annuler ». Re-vérifie le staff à chaque clic."""

    def __init__(self, owner_id, guild, chars):
        super().__init__(timeout=None)
        self.owner_id = owner_id
        self.guild = guild
        self.result = None
        self.future = asyncio.get_running_loop().create_future()
        options = [
            discord.SelectOption(
                label=f"Slot {c['slot_number']} · {c['character_name'] or '#' + str(c['id'])}"[:100],
                value=str(c["id"]))
            for c in chars[:25]]
        self.select = discord.ui.Select(placeholder="Choisis le personnage", options=options)
        self.select.callback = self._on_select
        self.add_item(self.select)
        cancel = discord.ui.Button(label="Annuler", emoji="✖️", style=discord.ButtonStyle.secondary)
        cancel.callback = self._on_cancel
        self.add_item(cancel)

    async def _guard(self, interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Ce choix ne t'appartient pas.", ephemeral=True)
            return False
        member = self.guild.get_member(interaction.user.id) if self.guild else None
        if member is None or not _is_staff(member):
            await interaction.response.send_message("Réservé au staff.", ephemeral=True)
            return False
        return True

    async def _on_select(self, interaction: discord.Interaction):
        if not await self._guard(interaction):
            return
        self.result = int(self.select.values[0])
        if not self.future.done():
            self.future.set_result(self.result)
        try:
            await interaction.response.edit_message(view=None)
        except discord.HTTPException:
            pass
        self.stop()

    async def _on_cancel(self, interaction: discord.Interaction):
        if not await self._guard(interaction):
            return
        self.result = "__cancel__"
        if not self.future.done():
            self.future.set_result("__cancel__")
        try:
            await interaction.response.edit_message(view=None)
        except discord.HTTPException:
            pass
        self.stop()


class Xp(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # Tâches de flux en cours : référence forte pour éviter le ramassage par le GC.
        self._flows = set()

    @app_commands.command(name="xp", description="Ajoute ou retire de l'XP à un personnage (staff)")
    async def xp(self, interaction: discord.Interaction):
        if not _is_staff(interaction.user):
            await interaction.response.send_message("❌ Réservé au staff.", ephemeral=True)
            return
        try:
            dm = await interaction.user.create_dm()
            await dm.send("🛠️ **Édition d'XP** — réponds aux questions ci-dessous. Tape « annuler » à tout moment.")
        except discord.HTTPException:
            await interaction.response.send_message(
                "❌ Je n'ai pas pu t'écrire en MP. Ouvre tes messages privés puis relance /xp.",
                ephemeral=True)
            return
        await interaction.response.send_message(
            "✅ Édition d'XP en cours (voir tes MP).", ephemeral=True)
        task = asyncio.create_task(self._flow(interaction.user, dm, interaction.guild))
        self._flows.add(task)
        task.add_done_callback(self._flows.discard)

    # ---------- primitives de flux ----------
    async def _safe_delete(self, *messages):
        for m in messages:
            if m is None:
                continue
            try:
                await m.delete()
            except discord.HTTPException:
                pass

    async def _ask_text(self, dm, user, question, validate):
        """Pose `question`, attend la réponse (SANS timeout), valide via validate(texte, msg) ->
        (ok, valeur, erreur). Redemande uniquement cette question en cas d'échec. « annuler » -> _XpCancel.
        Nettoie question + réponse (+ erreur) à chaque échange."""
        def check(m):
            return m.channel.id == dm.id and m.author.id == user.id and not m.author.bot

        q_msg = await dm.send(question)
        while True:
            reply = await self.bot.wait_for("message", check=check)  # aucun timeout
            texte = reply.content.strip()
            if texte.lower() == "annuler":
                await self._safe_delete(q_msg, reply)
                raise _XpCancel()
            ok, valeur, erreur = validate(texte, reply)
            if ok:
                await self._safe_delete(q_msg, reply)
                return valeur
            err_msg = await dm.send(f"❌ {erreur}")
            await self._safe_delete(reply, err_msg)

    async def _ask_buttons(self, dm, user, guild, question, options):
        view = _ChoiceView(user.id, guild, options)
        q_msg = await dm.send(question, view=view)
        await view.future  # sans timeout
        await self._safe_delete(q_msg)
        if view.result == "__cancel__":
            raise _XpCancel()
        return view.result

    async def _ask_character(self, dm, user, guild, member):
        chars = _get_user_characters(member.id, guild.id)
        if len(chars) == 1:
            return chars[0]
        view = _SelectView(user.id, guild, chars)
        q_msg = await dm.send("Avec quel personnage ?", view=view)
        await view.future
        await self._safe_delete(q_msg)
        if view.result == "__cancel__":
            raise _XpCancel()
        return next(c for c in chars if c["id"] == view.result)

    # ---------- flux complet ----------
    async def _flow(self, user, dm, guild):
        if guild is None:
            await dm.send("❌ Commande à lancer depuis un serveur.")
            return

        def v_member(t, m):
            uid = _first_user_id(t)
            if uid is None:
                return False, None, "Mentionne un joueur ou donne un ID valide."
            mem = guild.get_member(uid)
            if mem is None:
                return False, None, "Joueur introuvable sur le serveur."
            if not _get_user_characters(uid, guild.id):
                return False, None, "Ce joueur n'a aucun personnage validé."
            return True, mem, ""

        def v_valeur(t, m):
            if t.isdigit() and int(t) > 0:
                return True, int(t), ""
            return False, None, "Donne un entier positif."

        try:
            action = await self._ask_buttons(
                dm, user, guild, "Veux-tu **ajouter** ou **retirer** de l'XP ?",
                [("add", "Ajouter", "➕", discord.ButtonStyle.success),
                 ("remove", "Retirer", "➖", discord.ButtonStyle.danger)])
            member = await self._ask_text(dm, user, "Mentionne le joueur ou donne son ID.", v_member)
            char = await self._ask_character(dm, user, guild, member)
            valeur = await self._ask_text(dm, user, "Quelle valeur d'XP ?", v_valeur)
        except _XpCancel:
            try:
                await dm.send("❌ Édition d'XP annulée.")
            except discord.HTTPException:
                pass
            return

        delta = valeur if action == "add" else -valeur
        res = db.apply_xp_cascade(char["id"], delta)  # JAMAIS de ×2 VIP/Booster
        if not res.get("found"):
            await dm.send("❌ Ce personnage n'a pas de profil (niveau/XP) exploitable.")
            return
        await dm.send(embed=self._result_embed(char, action, valeur, res))

    @staticmethod
    def _result_embed(char, action, valeur, res):
        nom = char["character_name"] or f"#{char['id']}"
        avant = f"Niveau {res['level_before']} · {res['xp_before']}/{res['xp_max_before']}"
        apres = f"Niveau {res['level_after']} · {res['xp_after']}/{res['xp_max_after']}"
        gained, lost = res["levels_gained"], res["levels_lost"]
        if gained:
            niveaux = f"🔼 +{gained} niveau(x)"
        elif lost:
            niveaux = f"🔽 −{lost} niveau(x)"
        else:
            niveaux = "Aucun changement de niveau"
        verbe = "ajouté" if action == "add" else "retiré"
        pts, pv = res["points_delta"], res["pv_delta"]
        if pts or pv:
            signe_pts = f"{'+' if pts >= 0 else '−'}{abs(pts)}"
            signe_pv = f"{'+' if pv >= 0 else '−'}{abs(pv)}"
            recompenses = f"{signe_pts} points à répartir · {signe_pv} PV max"
        else:
            recompenses = "Aucune"
        embed = discord.Embed(
            title=f"⚡ XP — {nom}",
            description=f"**{valeur} XP {verbe}** (valeur exacte, sans multiplicateur).",
            color=discord.Color.green() if action == "add" else discord.Color.orange())
        embed.add_field(name="Avant → Après", value=f"{avant}\n→ {apres}", inline=False)
        embed.add_field(name="Niveaux", value=niveaux, inline=True)
        embed.add_field(name="Récompenses", value=recompenses, inline=True)
        return embed


async def setup(bot):
    await bot.add_cog(Xp(bot))
