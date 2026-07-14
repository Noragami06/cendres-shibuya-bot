import discord
from discord.ext import commands

from cogs.utils import database as db

MAX_PAGE_LENGTH = 4000

DEFAULT_EMOJI = "📘"

KEYWORD_EMOJIS = {
    "exorciste": "⚔️",
    "fléau": "👹",
    "hybride": "🧬",
    "énergie": "🔮",
    "occulte": "🔮",
    "territoire": "🗺️",
    "rct": "❤️‍🩹",
    "grade": "🎖️",
    "clan": "🏯",
    "interdit": "🚫",
    "pacte": "📜",
    "black flash": "⚡",
    "raid": "⚔️",
    "ordre": "🏛️",
}


def get_emoji_for_title(title: str) -> str:
    lowered = title.lower()
    for keyword, emoji in KEYWORD_EMOJIS.items():
        if keyword in lowered:
            return emoji
    return DEFAULT_EMOJI


def load_data():
    """Reconstruit la structure historique des informations depuis SQLite.

    Entrée simple    : {"title": ..., "content": ...}
    Entrée catégorie : {"title": ..., "type": "category", "clans": {sub_key: {...}}}
    L'ordre numérique des entrées et l'ordre des sous-entrées sont garantis par SQL.
    """
    data = {}
    for row in db.get_all_informations():
        info_key = row["info_key"]

        if row["is_category"]:
            clans = {}
            for sub in db.get_information_subitems(info_key):
                clans[sub["sub_key"]] = {"title": sub["title"], "content": sub["content"]}
            data[info_key] = {
                "title": row["title"],
                "type": "category",
                "clans": clans,
            }
        else:
            data[info_key] = {"title": row["title"], "content": row["content"]}

    return data


def paginate_content(content: str, max_length: int = MAX_PAGE_LENGTH):
    """Découpe le contenu en pages <= max_length, en coupant de préférence aux retours à la ligne."""
    if len(content) <= max_length:
        return [content]

    pages = []
    remaining = content
    while len(remaining) > max_length:
        window = remaining[:max_length]
        cut = window.rfind("\n")
        if cut == -1:
            # Aucun retour à la ligne : coupe brute à la longueur max
            cut = max_length
        pages.append(remaining[:cut].rstrip("\n"))
        remaining = remaining[cut:].lstrip("\n")
    if remaining:
        pages.append(remaining)
    return pages


class PaginationView(discord.ui.View):
    def __init__(self, title: str, pages: list, color: discord.Color):
        super().__init__(timeout=None)
        self.title = title
        self.pages = pages
        self.color = color
        self.index = 0
        self._update_buttons()

    def build_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title=self.title,
            description=self.pages[self.index],
            color=self.color,
        )
        embed.set_footer(text=f"Page {self.index + 1}/{len(self.pages)}")
        return embed

    def _update_buttons(self):
        self.previous_page.disabled = self.index == 0
        self.next_page.disabled = self.index == len(self.pages) - 1

    @discord.ui.button(emoji="◀️", style=discord.ButtonStyle.secondary)
    async def previous_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.index > 0:
            self.index -= 1
        self._update_buttons()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(emoji="▶️", style=discord.ButtonStyle.secondary)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.index < len(self.pages) - 1:
            self.index += 1
        self._update_buttons()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)


async def send_entry(destination, entry, *, is_interaction_response=False):
    """Envoie l'embed d'une entrée simple (title/content) avec pagination si nécessaire.
    `destination` est soit un salon (.send), soit une interaction (.response.send_message)."""
    title = f"{get_emoji_for_title(entry['title'])} {entry['title']}"
    pages = paginate_content(entry["content"])

    if len(pages) == 1:
        embed = discord.Embed(title=title, description=pages[0], color=discord.Color.blurple())
        view = None
    else:
        pv = PaginationView(title, pages, discord.Color.blurple())
        embed = pv.build_embed()
        view = pv

    if is_interaction_response:
        await destination.response.send_message(embed=embed, view=view)
    else:
        await destination.send(embed=embed, view=view)


class ClanSelect(discord.ui.Select):
    def __init__(self, number: str, clans: dict):
        self.number = number
        options = [
            discord.SelectOption(label=clan["title"], value=key)
            for key, clan in clans.items()
        ]
        super().__init__(
            placeholder="Choisis un clan...",
            min_values=1,
            max_values=1,
            options=options,
            custom_id=f"info_category_select_{number}",
        )

    async def callback(self, interaction: discord.Interaction):
        data = load_data()
        entry = data.get(self.number)
        if not entry or entry.get("type") != "category":
            await interaction.response.send_message("Cette catégorie n'existe plus.", ephemeral=True)
            return

        clan = entry.get("clans", {}).get(self.values[0])
        if not clan:
            await interaction.response.send_message("Cette sous-entrée n'existe plus.", ephemeral=True)
            return

        await send_entry(interaction, clan, is_interaction_response=True)


class ClanSelectView(discord.ui.View):
    def __init__(self, number: str, clans: dict):
        super().__init__(timeout=None)
        self.add_item(ClanSelect(number, clans))


class InformationsView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Voir une information", style=discord.ButtonStyle.primary, custom_id="informations_view", emoji="📖")
    async def view_info(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(
            title="📖 Consulter une information",
            description="Quel numéro d'information veux-tu consulter ?\n\n⏱️ Tu as 60 secondes pour répondre.",
            color=discord.Color.blurple(),
        )
        await interaction.response.send_message(embed=embed)

        def check_author(m: discord.Message):
            return m.author.id == interaction.user.id and m.channel.id == interaction.channel.id

        answer = await interaction.client.wait_for("message", check=check_author, timeout=None)

        number = answer.content.strip()
        data = load_data()

        if number not in data:
            await interaction.channel.send(
                f"❌ {interaction.user.mention} Aucune information ne porte le numéro **{number}**. Reclique sur le bouton pour réessayer."
            )
            return

        entry = data[number]

        if entry.get("type") == "category":
            clans = entry.get("clans", {})
            if not clans:
                await interaction.channel.send(
                    f"❌ {interaction.user.mention} Cette catégorie ne contient aucune entrée pour le moment."
                )
                return
            embed = discord.Embed(
                title=f"{get_emoji_for_title(entry['title'])} {entry['title']}",
                description="Sélectionne un clan dans le menu ci dessous pour consulter ses informations.",
                color=discord.Color.blurple(),
            )
            await interaction.channel.send(embed=embed, view=ClanSelectView(number, clans))
            return

        await send_entry(interaction.channel, entry)


class Informations(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        self.bot.add_view(InformationsView())

        # Enregistre une vue persistante par entrée de type "category"
        data = load_data()
        for number, entry in data.items():
            if entry.get("type") == "category":
                self.bot.add_view(ClanSelectView(number, entry.get("clans", {})))

    @commands.command(name="informations")
    async def informations(self, ctx: commands.Context):
        data = load_data()

        if not data:
            await ctx.send("Aucune information n'est disponible pour le moment.")
            return

        entries = sorted(data.items(), key=lambda item: int(item[0]))
        summary = "\n".join(
            f"{num}) {get_emoji_for_title(info['title'])} {info['title']}" for num, info in entries
        )

        embed = discord.Embed(
            title="📖 Informations disponibles",
            description=summary,
            color=discord.Color.blurple(),
        )
        embed.set_footer(text="Clique sur le bouton ci-dessous pour consulter une information.")

        await ctx.send(embed=embed, view=InformationsView())


async def setup(bot):
    await bot.add_cog(Informations(bot))
