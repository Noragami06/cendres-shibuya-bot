# -*- coding: utf-8 -*-
"""
suggestion.py — Commande /suggestion, ouverte à TOUS (aucun rôle, aucun personnage requis).

Flux :
1. /suggestion : réponse privée « Regarde tes messages privés » + MP d'invitation. La demande est
   enregistrée en base (suggestion_pending), SANS limite de temps et SANS timeout : elle survit à un
   redémarrage du bot. Relancer /suggestion alors qu'une demande est déjà en attente ne crée pas de
   doublon : on renvoie simplement le MP de rappel.
2. Le PROCHAIN message que ce joueur écrit au bot en MP est traité comme sa suggestion :
   - « annuler » : supprime la demande, confirme en MP.
   - texte vide (image seule) : redemande du texte, garde la demande.
   - texte > 4096 caractères : trop long, garde la demande.
   - sinon : publie un embed dans SUGGESTION_CHANNEL_ID (mentions neutralisées), ajoute ✅ puis ❌,
     journalise (table suggestions), supprime la demande, confirme en MP.
Un MP d'un joueur SANS demande en attente n'est JAMAIS traité comme une suggestion.
"""

from datetime import datetime

import discord
from discord import app_commands
from discord.ext import commands

from cogs.utils import database as db

# ---- Constantes ----
SUGGESTION_CHANNEL_ID = 1521819104674054175   # salon où sont publiées les suggestions
EMBED_DESC_MAX = 4096                          # limite Discord d'une description d'embed
VOTE_POUR = "✅"                                # réaction « pour » (ajoutée en premier)
VOTE_CONTRE = "❌"                              # réaction « contre » (ajoutée ensuite)
SUGGESTION_COLOR = discord.Color.blurple()

DM_PROMPT = ("Écris ta suggestion complète en un seul message. Il n'y a pas de limite de temps. "
             "Écris **annuler** pour abandonner.")
DM_DMS_CLOSED = "Active tes messages privés pour envoyer une suggestion."
REPLY_CHECK_DMS = "Regarde tes messages privés 📩"
DM_CANCELLED = "❌ Ta demande de suggestion a été annulée."
DM_SENT = "✅ Ta suggestion a été envoyée."
DM_NO_TEXT = "Ta suggestion doit contenir du **texte**. Réécris-la en un seul message (ou « annuler »)."
DM_TOO_LONG = (f"Ta suggestion est trop longue (maximum {EMBED_DESC_MAX} caractères). "
               "Raccourcis-la et renvoie-la (ou « annuler »).")


class Suggestion(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # Garde-fou de concurrence (mémoire) : évite de traiter deux MP du même joueur en parallèle.
        self._processing = set()

    @app_commands.command(name="suggestion",
                          description="Propose une suggestion (elle sera envoyée via tes messages privés).")
    async def suggestion(self, interaction: discord.Interaction):
        # Ouverte à tous : aucun contrôle de rôle ni de personnage.
        user = interaction.user
        try:
            dm = await user.create_dm()
            await dm.send(DM_PROMPT)
        except discord.HTTPException:
            # MP fermés : on n'enregistre rien, on explique en privé.
            await interaction.response.send_message(DM_DMS_CLOSED, ephemeral=True)
            return
        # Enregistre la demande (idempotent : pas de doublon si déjà en attente).
        db.suggestion_pending_add(user.id, datetime.utcnow().isoformat())
        await interaction.response.send_message(REPLY_CHECK_DMS, ephemeral=True)

    @commands.Cog.listener("on_message")
    async def on_dm_suggestion(self, message: discord.Message):
        # Uniquement les MP de vrais utilisateurs ayant une demande en attente.
        if message.author.bot:
            return
        if message.guild is not None:  # pas un MP
            return
        user_id = message.author.id
        if user_id in self._processing:
            return
        if not db.suggestion_pending_exists(user_id):
            return  # aucun MP sans demande en attente n'est jamais traité comme une suggestion
        self._processing.add(user_id)
        try:
            await self._handle_suggestion_dm(message)
        finally:
            self._processing.discard(user_id)

    async def _handle_suggestion_dm(self, message: discord.Message):
        content = (message.content or "").strip()

        # Annulation (insensible à la casse).
        if content.lower() == "annuler":
            db.suggestion_pending_delete(message.author.id)
            await message.channel.send(DM_CANCELLED)
            return

        # Message sans texte (image seule) : on garde la demande en attente.
        if not content:
            await message.channel.send(DM_NO_TEXT)
            return

        # Trop long pour un embed : on garde la demande en attente.
        if len(content) > EMBED_DESC_MAX:
            await message.channel.send(DM_TOO_LONG)
            return

        channel = self.bot.get_channel(SUGGESTION_CHANNEL_ID)
        if channel is None:
            # Salon introuvable : on NE perd pas la demande (le joueur pourra réessayer).
            await message.channel.send(
                "⚠️ Le salon des suggestions est introuvable pour le moment. Réessaie plus tard.")
            return

        author = message.author
        embed = discord.Embed(description=content, color=SUGGESTION_COLOR,
                              timestamp=datetime.utcnow())
        embed.set_author(name=author.display_name, icon_url=author.display_avatar.url)
        embed.set_footer(text="Suggestion")
        try:
            sent = await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
            await sent.add_reaction(VOTE_POUR)
            await sent.add_reaction(VOTE_CONTRE)
        except discord.HTTPException as e:
            # Publication impossible : on garde la demande en attente, on prévient le joueur.
            await message.channel.send(
                f"⚠️ Je n'ai pas réussi à publier ta suggestion ({e!r}). Réessaie plus tard.")
            return

        db.suggestion_log_add(sent.id, author.id, content, datetime.utcnow().isoformat())
        db.suggestion_pending_delete(author.id)
        await message.channel.send(DM_SENT)


async def setup(bot):
    await bot.add_cog(Suggestion(bot))
