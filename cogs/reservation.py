import asyncio
import difflib
import os
import uuid
from datetime import datetime

import discord
from discord import app_commands
from discord.ext import commands

from cogs.utils import database as db
from cogs.banque import PHOENIX_COLOR

# =====================================================================
# CONSTANTES
# =====================================================================
RESERVATION_STAFF_CHANNEL_ID = 1521243474371022939
APPEARANCE_VALIDATED_CHANNEL_ID = 1521817345314783292  # salon public des apparences validées
FICHE_STAFF_ROLE_ID = 1521229332075512039  # rôle staff (déjà utilisé ailleurs) : seul autorisé à Accepter/Refuser

RESERVATION_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "reservations")
MAX_IMAGE_BYTES = 8 * 1024 * 1024  # 8 Mo : limite standard des pièces jointes Discord
WAIT_TIMEOUT = 300                 # délai d'attente d'une réponse texte (raison de refus)


# =====================================================================
# HELPERS
# =====================================================================
def _now() -> str:
    return datetime.utcnow().isoformat()


def normalize_name(name: str) -> str:
    return (name or "").lower().strip()


def is_similar(name_a: str, name_b: str, threshold: float = 0.85) -> bool:
    ratio = difflib.SequenceMatcher(None, normalize_name(name_a), normalize_name(name_b)).ratio()
    return ratio >= threshold


def _is_fiche_staff(member) -> bool:
    return any(r.id == FICHE_STAFF_ROLE_ID for r in getattr(member, "roles", []))


def _image_extension(attachment: discord.Attachment) -> str:
    """Extension d'origine (sans point), déduite du nom de fichier puis du content_type. 'png' par défaut."""
    ext = os.path.splitext(attachment.filename or "")[1].lower().lstrip(".")
    if ext:
        return ext
    ctype = (attachment.content_type or "").split(";")[0].strip().lower()  # ex : "image/gif"
    sub = ctype.split("/")[-1] if "/" in ctype else ""
    return sub or "png"


def _available_slots(user_id: int, guild_id: int):
    """Slots du joueur qui n'ont PAS encore d'apparence validée (triés croissant). Sert au message
    d'erreur listant les slots encore disponibles."""
    with db.get_connection() as conn:
        chars = conn.execute(
            "SELECT id, slot_number FROM validated_characters WHERE user_id = ? AND guild_id = ? "
            "ORDER BY slot_number ASC",
            (user_id, guild_id),
        ).fetchall()
        if not chars:
            return []
        ids = [c["id"] for c in chars]
        placeholders = ",".join("?" * len(ids))
        accepted = {
            r["character_id"] for r in conn.execute(
                f"SELECT DISTINCT character_id FROM appearance_reservations "
                f"WHERE status = 'accepted' AND character_id IN ({placeholders})",
                ids,
            ).fetchall()
        }
    return [c["slot_number"] for c in chars if c["id"] not in accepted]


# =====================================================================
# VUES
# =====================================================================
class SimilarWarnView(discord.ui.View):
    """Avertissement éphémère « une apparence similaire existe déjà » : Annuler / Continuer quand même.
    Vue en session (view.wait()), réservée au joueur qui a lancé la demande, avec anti double-clic."""

    def __init__(self, owner_id):
        super().__init__(timeout=WAIT_TIMEOUT)
        self.owner_id = owner_id
        self.result = None
        self._done = False

    async def _choose(self, interaction, value):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Ce choix ne t'appartient pas.", ephemeral=True)
            return
        if self._done:
            try:
                if not interaction.response.is_done():
                    await interaction.response.defer()
            except discord.HTTPException:
                pass
            return
        self._done = True
        self.result = value
        self.stop()
        for it in self.children:
            it.disabled = True
        try:
            await interaction.response.edit_message(view=self)
        except discord.HTTPException:
            pass

    @discord.ui.button(label="Annuler", emoji="❌", style=discord.ButtonStyle.danger)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._choose(interaction, "cancel")

    @discord.ui.button(label="Continuer quand même", emoji="✅", style=discord.ButtonStyle.success)
    async def cont(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._choose(interaction, "continue")


class ReservationStaffView(discord.ui.View):
    """Boutons Accepter / Refuser sous la demande envoyée au staff. Persistante (timeout=None) : le clic
    est routé par le listener on_interaction du cog via le préfixe du custom_id (mécanisme de persistance
    utilisé partout dans ce bot pour les custom_id dynamiques — voir cog_load)."""

    def __init__(self, reservation_id):
        super().__init__(timeout=None)
        self.add_item(discord.ui.Button(
            label="Accepter", emoji="✅", style=discord.ButtonStyle.success,
            custom_id=f"reserv_accept:{reservation_id}"))
        self.add_item(discord.ui.Button(
            label="Refuser", emoji="❌", style=discord.ButtonStyle.danger,
            custom_id=f"reserv_refuse:{reservation_id}"))


# =====================================================================
# COG
# =====================================================================
class Reservation(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._processing = set()  # ids de réservation en cours de traitement (anti double-clic Accepter/Refuser)

    async def cog_load(self):
        os.makedirs(RESERVATION_DIR, exist_ok=True)
        # Persistance des boutons Accepter/Refuser : leurs custom_id sont DYNAMIQUES (id de la réservation),
        # donc — comme partout ailleurs dans ce bot — la reprise après redémarrage passe par le listener
        # on_interaction (dispatch par préfixe), qui couvre toutes les réservations passées et futures.
        # bot.add_view() ne peut pas enregistrer un custom_id dynamique arbitraire ; on l'appelle malgré
        # tout pour l'id 0 afin de garder la trace explicite de l'intention de persistance.
        self.bot.add_view(ReservationStaffView(0))

    # ---------- DM au joueur ----------
    async def _dm_user(self, user_id, content):
        try:
            user = await self.bot.fetch_user(user_id)
            await user.send(content)
        except discord.HTTPException:
            pass

    # ---------- Publication dans le salon des apparences validées ----------
    async def _publish_validated(self, res):
        """Publie l'embed complet (+ image/GIF) de la réservation acceptée dans le salon public des
        apparences validées. Silencieux si le salon est introuvable ou l'envoi échoue."""
        channel = self.bot.get_channel(APPEARANCE_VALIDATED_CHANNEL_ID)
        if channel is None:
            return
        with db.get_connection() as conn:
            crow = conn.execute(
                "SELECT character_name, slot_number FROM validated_characters WHERE id = ?",
                (res["character_id"],),
            ).fetchone()
        name = (crow["character_name"] if crow and crow["character_name"] else "?")
        slot = crow["slot_number"] if crow else "?"
        mention = f"<@{res['user_id']}>"
        description = (
            f"**Réservation de {mention}**\n\n"
            f"**Joueur :** {mention}\n"
            f"**Personnage :** {name} (Slot {slot})\n"
            f"**Nom original :** {res['nom_original']}\n"
            f"**Univers :** {res['univers']}")
        embed = discord.Embed(
            title="🎭 Apparence validée", description=description, color=PHOENIX_COLOR)
        image_path = res["image_path"]
        try:
            if image_path and os.path.exists(image_path):
                fn = os.path.basename(image_path)
                embed.set_image(url=f"attachment://{fn}")
                await channel.send(
                    embed=embed, file=discord.File(image_path, filename=fn),
                    allowed_mentions=discord.AllowedMentions.none())
            else:
                await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:
            pass

    # =================================================================
    # COMMANDE SLASH
    # =================================================================
    @app_commands.command(name="réserv-appa", description="Réserver l'apparence d'un personnage pour ta fiche")
    @app_commands.describe(
        personnage="Quel personnage (slot) concerné",
        nom_original="Nom original du personnage dans son œuvre",
        univers="Nom de l'univers/œuvre d'origine",
        image="Image du personnage (tout format accepté, GIF inclus)",
    )
    @app_commands.choices(personnage=[
        app_commands.Choice(name="Slot 1", value=1),
        app_commands.Choice(name="Slot 2", value=2),
        app_commands.Choice(name="Slot 3", value=3),
    ])
    async def reserv_appa(self, interaction: discord.Interaction,
                          personnage: app_commands.Choice[int], nom_original: str, univers: str,
                          image: discord.Attachment):
        # Le traitement (téléchargement d'image, vérifications) prend un peu de temps -> défère tout de suite.
        await interaction.response.defer(ephemeral=True)

        if interaction.guild is None:
            await interaction.followup.send("Cette commande s'utilise sur le serveur.", ephemeral=True)
            return
        slot = personnage.value

        # 4) Validation stricte côté serveur — le personnage existe-t-il dans ce slot ?
        char = db.get_validated_character_slot(interaction.user.id, interaction.guild.id, slot)
        if char is None:
            await interaction.followup.send(
                f"Tu n'as pas de personnage validé dans le Slot {slot}.", ephemeral=True)
            return
        character_id = char["id"]
        character_name = char["character_name"] or "?"

        # ... et ce slot n'a-t-il pas DÉJÀ une apparence validée ? (avec la liste des slots encore libres)
        with db.get_connection() as conn:
            already_validated = conn.execute(
                "SELECT 1 FROM appearance_reservations WHERE character_id = ? AND status = 'accepted' LIMIT 1",
                (character_id,),
            ).fetchone()
        if already_validated:
            libres = _available_slots(interaction.user.id, interaction.guild.id)
            restants = ", ".join(f"Slot {n}" for n in libres) if libres else "aucun"
            await interaction.followup.send(
                f"❌ Le Slot {slot} a déjà une apparence validée. Slots encore disponibles pour toi : "
                f"{restants}.", ephemeral=True)
            return

        # 5) Validation + téléchargement de l'image (TOUS formats image, GIF inclus, SANS recompression).
        if not (image.content_type or "").startswith("image/"):
            await interaction.followup.send(
                "❌ Le fichier fourni n'est pas une image. Envoie une image (PNG, JPG, GIF, WebP…).",
                ephemeral=True)
            return
        if image.size and image.size > MAX_IMAGE_BYTES:
            await interaction.followup.send(
                "❌ Image trop lourde (max 8 Mo). Envoie une version plus légère.", ephemeral=True)
            return
        try:
            data = await image.read()  # bytes bruts, sans recompression (un GIF garde son animation)
        except discord.HTTPException:
            await interaction.followup.send(
                "❌ Le téléchargement de l'image a échoué, réessaie.", ephemeral=True)
            return
        if len(data) > MAX_IMAGE_BYTES:
            await interaction.followup.send(
                "❌ Image trop lourde (max 8 Mo). Envoie une version plus légère.", ephemeral=True)
            return

        os.makedirs(RESERVATION_DIR, exist_ok=True)
        filename = f"{character_id}_{uuid.uuid4().hex}.{_image_extension(image)}"
        image_path = os.path.join(RESERVATION_DIR, filename)
        try:
            with open(image_path, "wb") as f:
                f.write(data)
        except OSError:
            await interaction.followup.send(
                "❌ Impossible d'enregistrer l'image côté serveur, réessaie plus tard.", ephemeral=True)
            return

        # 6) Détection de doublon : MÊME univers (exact, insensible casse/espaces) ET nom similaire (fuzzy).
        #    Deux personnages homonymes dans des œuvres différentes ne s'alertent donc jamais.
        warned = None  # (existing_user_id, existing_name)
        with db.get_connection() as conn:
            accepted_rows = conn.execute(
                "SELECT id, user_id, nom_original, univers FROM appearance_reservations "
                "WHERE status = 'accepted'"
            ).fetchall()
        for row in accepted_rows:
            if normalize_name(univers) == normalize_name(row["univers"]) \
                    and is_similar(nom_original, row["nom_original"]):
                warned = (row["user_id"], row["nom_original"])
                break

        # 7) Avertissement éphémère si similarité (le joueur peut annuler ou continuer).
        if warned:
            view = SimilarWarnView(interaction.user.id)
            await interaction.followup.send(
                f"⚠️ <@{warned[0]}> a déjà cette apparence ({warned[1]}). (Mais je peux me tromper, donc "
                "attention.) Veux tu quand même envoyer ta demande ?",
                view=view, ephemeral=True)
            await view.wait()
            if view.result != "continue":
                # Annulé (ou délai) : on nettoie le fichier orphelin déjà téléchargé, rien n'est enregistré.
                try:
                    os.remove(image_path)
                except OSError:
                    pass
                await interaction.followup.send("Demande annulée.", ephemeral=True)
                return

        # 8) Enregistrement (pending) + envoi de la demande au staff.
        reservation_id = db.create_appearance_reservation(
            character_id, interaction.user.id, nom_original, univers, image_path, _now())

        description = (
            f"**Joueur :** {interaction.user.mention}\n"
            f"**Personnage :** {character_name} (Slot {slot})\n"
            f"**Nom original :** {nom_original}\n"
            f"**Univers :** {univers}")
        if warned:
            description += (
                f"\n\n⚠️ **Attention :** <@{warned[0]}> a déjà une apparence similaire ({warned[1]}), "
                "le joueur a choisi de continuer quand même malgré l'avertissement.")
        embed = discord.Embed(
            title="🎭 Demande de réservation d'apparence", description=description, color=PHOENIX_COLOR)
        embed.set_image(url=f"attachment://{filename}")

        staff_channel = self.bot.get_channel(RESERVATION_STAFF_CHANNEL_ID)
        if staff_channel is None:
            await interaction.followup.send(
                "❌ Le salon de validation du staff est introuvable, préviens un administrateur.",
                ephemeral=True)
            return
        try:
            await staff_channel.send(
                content=f"<@&{FICHE_STAFF_ROLE_ID}>",
                embed=embed,
                file=discord.File(image_path, filename=filename),
                view=ReservationStaffView(reservation_id),
                allowed_mentions=discord.AllowedMentions(roles=True))
        except discord.HTTPException:
            await interaction.followup.send(
                "❌ Impossible d'envoyer la demande au staff (image peut-être trop lourde). Réessaie.",
                ephemeral=True)
            return

        await interaction.followup.send(
            "✅ Ta demande a été envoyée au staff pour validation.", ephemeral=True)

    # =================================================================
    # LISTENER : boutons Accepter / Refuser (custom_id dynamique, persistant)
    # =================================================================
    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        if interaction.type != discord.InteractionType.component:
            return
        cid = interaction.data.get("custom_id", "")
        if cid.startswith("reserv_accept:"):
            await self.handle_accept(interaction, cid)
        elif cid.startswith("reserv_refuse:"):
            await self.handle_refuse(interaction, cid)

    async def handle_accept(self, interaction: discord.Interaction, cid: str):
        reservation_id = int(cid.split(":")[1])
        if not _is_fiche_staff(interaction.user):
            await interaction.response.send_message("Tu n'as pas la permission.", ephemeral=True)
            return
        # Anti double-clic : verrou en mémoire.
        if reservation_id in self._processing:
            try:
                if not interaction.response.is_done():
                    await interaction.response.defer()
            except discord.HTTPException:
                pass
            return
        res = db.get_appearance_reservation(reservation_id)
        if res is None or res["status"] != "pending":
            await interaction.response.send_message("Cette demande a déjà été traitée.", ephemeral=True)
            return
        self._processing.add(reservation_id)
        try:
            db.set_appearance_reservation_status(reservation_id, "accepted")
            content = interaction.message.content or ""
            # Retrait immédiat de la View + trace de qui a validé.
            await interaction.response.edit_message(
                content=f"{content}\n✅ Acceptée par {interaction.user.mention}.", view=None)
            # Publication dans le salon public des apparences validées (embed + image/GIF).
            await self._publish_validated(res)
            # DM au joueur.
            await self._dm_user(
                res["user_id"],
                f"✅ Ta réservation d'apparence pour {res['nom_original']} a été acceptée !")
        finally:
            self._processing.discard(reservation_id)

    async def handle_refuse(self, interaction: discord.Interaction, cid: str):
        reservation_id = int(cid.split(":")[1])
        if not _is_fiche_staff(interaction.user):
            await interaction.response.send_message("Tu n'as pas la permission.", ephemeral=True)
            return
        if reservation_id in self._processing:
            try:
                if not interaction.response.is_done():
                    await interaction.response.defer()
            except discord.HTTPException:
                pass
            return
        res = db.get_appearance_reservation(reservation_id)
        if res is None or res["status"] != "pending":
            await interaction.response.send_message("Cette demande a déjà été traitée.", ephemeral=True)
            return
        self._processing.add(reservation_id)
        try:
            original = interaction.message.content or ""
            # Retrait immédiat de la View (anti double-clic), puis demande de la raison dans le salon.
            await interaction.response.edit_message(view=None)
            channel = interaction.channel
            await channel.send(f"{interaction.user.mention}, quelle est la raison du refus ?")

            # Isolation par utilisateur (standard du bot) : uniquement CE staff, dans CE salon.
            def check(m):
                return (m.channel.id == channel.id and m.author.id == interaction.user.id
                        and not m.author.bot)
            try:
                msg = await self.bot.wait_for("message", check=check, timeout=WAIT_TIMEOUT)
            except asyncio.TimeoutError:
                # Personne n'a donné de raison : on rétablit les boutons pour permettre une nouvelle tentative.
                await channel.send("⏳ Délai dépassé, refus annulé. Les boutons ont été rétablis.")
                try:
                    await interaction.message.edit(view=ReservationStaffView(reservation_id))
                except discord.HTTPException:
                    pass
                return
            reason = msg.content.strip() or "(aucune raison précisée)"

            # Nettoyage COMPLET : suppression de la ligne en base ET du fichier image sur le disque
            # (aucune trace conservée après un refus).
            image_path = res["image_path"]
            with db.get_connection() as conn:
                conn.execute("DELETE FROM appearance_reservations WHERE id = ?", (reservation_id,))
            if image_path and os.path.exists(image_path):
                try:
                    os.remove(image_path)
                except OSError:
                    pass

            try:
                await interaction.message.edit(
                    content=f"{original}\n❌ Refusée par {interaction.user.mention} : {reason}")
            except discord.HTTPException:
                pass
            await self._dm_user(
                res["user_id"],
                f"❌ Ta réservation d'apparence pour {res['nom_original']} a été refusée : {reason}")
        finally:
            self._processing.discard(reservation_id)


async def setup(bot):
    await bot.add_cog(Reservation(bot))
