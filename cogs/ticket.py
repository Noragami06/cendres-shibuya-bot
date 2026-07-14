import discord
from discord.ext import commands
from discord import app_commands
import os
import uuid
from datetime import datetime

from cogs.utils import database as db

# ---------- IDs ----------
STAFF_ROLE_ID = 1521228799302307967          # Peut utiliser /ticket + accepter/refuser
TICKET_ACCESS_ROLE_ID = 1521229332075512039  # A accès aux salons de tickets créés
PANEL_CHANNEL_ID = 1523648386878672982        # Salon où le panel est envoyé
CONFIRM_CHANNEL_ID = 1523649007753236491      # Salon des demandes de confirmation
TICKET_CATEGORY_ID = 1523648386052653056      # Catégorie des salons de tickets
OWNER_DM_ID = 396615332346855428              # Toi - reçoit le MP à la suppression

REASONS = {
    "fiche": {"label": "Fiche", "color": discord.Color.green(), "emoji": "📄"},
    "partenariat": {"label": "Partenariat", "color": discord.Color.red(), "emoji": "🤝"},
    "autre": {"label": "Autre", "color": discord.Color.blue(), "emoji": "❓"},
}


def has_staff_role(member: discord.Member) -> bool:
    return any(role.id == STAFF_ROLE_ID for role in member.roles)


async def save_transcript(channel: discord.TextChannel, ticket_id: str) -> str:
    folder = os.path.join(os.path.dirname(__file__), "..", "data", "transcripts")
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f"ticket_{ticket_id}.txt")

    lines = []
    async for message in channel.history(limit=None, oldest_first=True):
        timestamp = message.created_at.strftime("%Y-%m-%d %H:%M:%S")
        lines.append(f"[{timestamp}] {message.author} : {message.content}")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    return path


class AutreReasonModal(discord.ui.Modal, title="Précise ta raison"):
    reason_input = discord.ui.TextInput(
        label="Pourquoi ouvres-tu ce ticket ?",
        style=discord.TextStyle.paragraph,
        max_length=300,
        required=True,
    )

    async def on_submit(self, interaction: discord.Interaction):
        await send_confirmation_request(interaction, "autre", self.reason_input.value)


class ConfirmView(discord.ui.View):
    """Vue "one-shot" servant uniquement à générer les boutons avec des custom_id dynamiques.
    Toute la logique de callback est gérée par le listener on_interaction du cog, ce qui
    rend les boutons persistants même après un redémarrage du bot."""

    def __init__(self, request_id: str):
        super().__init__(timeout=None)
        self.add_item(discord.ui.Button(
            label="Accepter",
            style=discord.ButtonStyle.success,
            custom_id=f"confirm_accept:{request_id}",
        ))
        self.add_item(discord.ui.Button(
            label="Refuser",
            style=discord.ButtonStyle.danger,
            custom_id=f"confirm_deny:{request_id}",
        ))


async def send_confirmation_request(interaction: discord.Interaction, ticket_type: str, reason_text: str):
    confirm_channel = interaction.guild.get_channel(CONFIRM_CHANNEL_ID)
    info = REASONS[ticket_type]

    embed = discord.Embed(
        title="Nouvelle demande de ticket",
        description=f"{interaction.user.mention} souhaite ouvrir un ticket.",
        color=info["color"],
    )
    embed.add_field(name="Raison", value=info["label"], inline=True)
    if ticket_type == "autre":
        embed.add_field(name="Détail", value=reason_text, inline=False)
    embed.set_footer(text=f"ID utilisateur : {interaction.user.id}")

    request_id = uuid.uuid4().hex
    db.add_pending_request(request_id, interaction.user.id, ticket_type, reason_text)

    view = ConfirmView(request_id)
    await confirm_channel.send(embed=embed, view=view)

    await interaction.response.send_message(
        "Ta demande a été envoyée au staff, tu seras notifié une fois traitée.", ephemeral=True
    )


class TicketOpenView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Fiche", style=discord.ButtonStyle.success, custom_id="ticket_open_fiche", emoji="📄")
    async def fiche(self, interaction: discord.Interaction, button: discord.ui.Button):
        await send_confirmation_request(interaction, "fiche", "Fiche")

    @discord.ui.button(label="Partenariat", style=discord.ButtonStyle.danger, custom_id="ticket_open_partenariat", emoji="🤝")
    async def partenariat(self, interaction: discord.Interaction, button: discord.ui.Button):
        await send_confirmation_request(interaction, "partenariat", "Partenariat")

    @discord.ui.button(label="Autre", style=discord.ButtonStyle.primary, custom_id="ticket_open_autre", emoji="❓")
    async def autre(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(AutreReasonModal())


class FicheStartView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Commencer", style=discord.ButtonStyle.primary, custom_id="ticket_fiche_start", emoji="🚀")
    async def start(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(
            title="Instructions",
            description=(
                "Puisque tu n'as pas de question, voici ce qu'il te faut préparer avant de commencer :\n\n"
                "- Une image représentant ton personnage\n"
                "- Un prénom pour ton personnage\n"
                "- Un nom de famille, uniquement si ton personnage n'appartient à aucun clan\n\n"
                "Une fois que tout est prêt, utilise la commande /départ avec moi pour démarrer la création de ta fiche."
            ),
            color=discord.Color.blurple(),
        )
        await interaction.response.send_message(embed=embed)


async def create_ticket_channel(interaction, requester, ticket_type, reason_text):
    global_id, type_number = db.next_ticket_numbers(ticket_type)

    guild = interaction.guild
    category = guild.get_channel(TICKET_CATEGORY_ID)
    access_role = guild.get_role(TICKET_ACCESS_ROLE_ID)

    safe_name = requester.name.lower().replace(" ", "-")
    channel_name = f"{ticket_type}-{safe_name}-{type_number}"

    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        requester: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
        access_role: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
        guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True, manage_messages=True),
    }

    channel = await category.create_text_channel(name=channel_name, overwrites=overwrites)

    db.insert_ticket(
        ticket_id=global_id,
        channel_id=channel.id,
        user_id=requester.id,
        ticket_type=ticket_type,
        reason=reason_text,
        status="open",
        created_at=datetime.utcnow().isoformat(),
    )

    info = REASONS[ticket_type]
    embed = discord.Embed(
        title=f"Ticket #{global_id}",
        description=f"Voici le ticket de {requester.mention} pour **{info['label']}**" +
                     (f"\n\n> {reason_text}" if ticket_type == "autre" else ""),
        color=info["color"],
    )
    embed.set_footer(text=f"Ticket #{global_id}")

    view = TicketControlView()
    msg = await channel.send(content=requester.mention, embed=embed, view=view)
    await msg.pin()

    if ticket_type == "fiche":
        welcome_embed = discord.Embed(
            title="Bienvenue",
            description=(
                f"Bonjour {requester.mention}, bienvenue dans ton ticket, celui où ton destin va être décidé.\n\n"
                "Si tu as la moindre question, tu peux te rendre dans <#1521818990412955669> ou contacter le staff en message privé.\n\n"
                "Si tu n'as aucune question, clique sur le bouton ci-dessous pour recevoir les instructions."
            ),
            color=REASONS["fiche"]["color"],
        )
        await channel.send(embed=welcome_embed, view=FicheStartView())


class TicketControlView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Fermer", style=discord.ButtonStyle.secondary, custom_id="ticket_close", emoji="🔒")
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button):
        ticket = db.get_ticket_by_channel(interaction.channel.id)
        if not ticket:
            await interaction.response.send_message("Ticket introuvable dans la base de données.", ephemeral=True)
            return

        member = interaction.guild.get_member(ticket["user_id"])
        if member:
            await interaction.channel.set_permissions(member, view_channel=False)

        db.update_ticket_status(ticket["id"], "closed")

        embed = discord.Embed(
            description=f"🔒 Ticket fermé par {interaction.user.mention}. Le joueur n'a plus accès au salon.",
            color=discord.Color.orange(),
        )
        await interaction.response.send_message(embed=embed)

    @discord.ui.button(label="Supprimer", style=discord.ButtonStyle.danger, custom_id="ticket_delete", emoji="🗑️")
    async def delete(self, interaction: discord.Interaction, button: discord.ui.Button):
        ticket = db.get_ticket_by_channel(interaction.channel.id)
        if not ticket:
            await interaction.response.send_message("Ticket introuvable dans la base de données.", ephemeral=True)
            return

        ticket_id = ticket["id"]
        await interaction.response.send_message("Suppression en cours, sauvegarde de la conversation...")

        transcript_path = await save_transcript(interaction.channel, ticket_id)
        db.update_ticket_transcript(ticket_id, "deleted", transcript_path)

        owner = interaction.client.get_user(OWNER_DM_ID)
        if owner:
            info = REASONS[ticket["type"]]
            embed = discord.Embed(
                title=f"Ticket #{ticket_id} supprimé",
                description=f"Type : {info['label']}\nOuvert par : <@{ticket['user_id']}>\nSupprimé par : {interaction.user.mention}",
                color=discord.Color.dark_grey(),
            )
            try:
                await owner.send(embed=embed, file=discord.File(transcript_path))
            except discord.Forbidden:
                pass

        await interaction.channel.delete()


class Ticket(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        self.bot.add_view(TicketOpenView())
        self.bot.add_view(TicketControlView())
        self.bot.add_view(FicheStartView())

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        if interaction.type != discord.InteractionType.component:
            return
        custom_id = interaction.data.get("custom_id", "")
        if not (custom_id.startswith("confirm_accept:") or custom_id.startswith("confirm_deny:")):
            return

        if not has_staff_role(interaction.user):
            await interaction.response.send_message("Tu n'as pas la permission de faire ça.", ephemeral=True)
            return

        request_id = custom_id.split(":", 1)[1]
        pending = db.get_pending_request(request_id)

        if pending is None:
            await interaction.response.send_message(
                "Cette demande a déjà été traitée ou n'existe plus.", ephemeral=True
            )
            return

        requester_id = pending["requester_id"]
        ticket_type = pending["ticket_type"]
        reason_text = pending["reason_text"]
        requester = interaction.guild.get_member(requester_id)

        if custom_id.startswith("confirm_accept:"):
            if requester is None:
                await interaction.response.send_message(
                    "Le membre à l'origine de la demande est introuvable (a-t-il quitté le serveur ?).",
                    ephemeral=True,
                )
                return
            db.delete_pending_request(request_id)
            await create_ticket_channel(interaction, requester, ticket_type, reason_text)
            embed = discord.Embed(
                description=f"✅ La demande de {requester.mention} a été **acceptée** par {interaction.user.mention}",
                color=discord.Color.green(),
            )
        else:
            db.delete_pending_request(request_id)
            mention = requester.mention if requester else f"<@{requester_id}>"
            embed = discord.Embed(
                description=f"❌ La demande de {mention} a été **refusée** par {interaction.user.mention}",
                color=discord.Color.red(),
            )

        await interaction.response.edit_message(embed=embed, view=None)

    @app_commands.command(name="ticket", description="Envoie le panel d'ouverture de tickets")
    async def ticket(self, interaction: discord.Interaction):
        if not has_staff_role(interaction.user):
            await interaction.response.send_message("Tu n'as pas la permission d'utiliser cette commande.", ephemeral=True)
            return

        channel = interaction.guild.get_channel(PANEL_CHANNEL_ID)
        embed = discord.Embed(
            title="🎫 Ouvrir un ticket",
            description="Bienvenue ! Clique sur l'un des boutons ci-dessous selon ce que tu désires.",
            color=discord.Color.blurple(),
        )
        embed.add_field(name="📄 Fiche", value="Ouvrir un ticket pour ta fiche", inline=False)
        embed.add_field(name="🤝 Partenariat", value="Ouvrir un ticket pour un partenariat", inline=False)
        embed.add_field(name="❓ Autre", value="Ouvrir un ticket pour toute autre demande", inline=False)

        await channel.send(embed=embed, view=TicketOpenView())
        await interaction.response.send_message(f"Panel envoyé dans {channel.mention} ✅", ephemeral=True)


async def setup(bot):
    await bot.add_cog(Ticket(bot))