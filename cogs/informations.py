import discord
from discord.ext import commands
import json
import os

DATA_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "informations.json")

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
    "clan": "🏯",
    "grade": "🎖️",
    "pacte": "📜",
    "interdit": "🚫",
    "black flash": "⚡",
}


def get_emoji_for_title(title: str) -> str:
    lowered = title.lower()
    for keyword, emoji in KEYWORD_EMOJIS.items():
        if keyword in lowered:
            return emoji
    return DEFAULT_EMOJI

DEFAULT_DATA = {
    "1": {
        "title": "Exorcistes",
        "content": (
            "Un exorciste est un individu capable de produire et manipuler de l'énergie occulte (EO), la force née des émotions négatives humaines, à l'origine des fléaux. C'est une capacité qui se détecte physiquement : l'énergie occulte se sent, elle ne peut pas être simulée ou falsifiée. Quelqu'un qui prétend être exorciste sans pouvoir en produire est immédiatement démasqué comme un fraudeur et voit son dossier rejeté.\n\n"
            "**QUI SONT LES EXORCISTES**\n\n"
            "Deux origines possibles :\n\n"
            "• Par lignée, la voie la plus commune. Les 7 clans (Gojo, Zenin, Inumaki, Kamo, Kashimo, Ryomen, Geto) transmettent l'énergie occulte de génération en génération. Dès qu'un clan met un enfant au monde, il est présumé exorciste.\n\n"
            "• Par cas exceptionnel, à l'image de Yuji historiquement, certains individus hors lignée peuvent développer une énergie occulte suite à un événement particulier (contact avec un objet maudit, un fléau puissant, etc.). Ces cas restent rares mais sont reconnus au même titre qu'un exorciste de clan.\n\n"
            "La manifestation est brutale et immédiate : vers 6 ans, l'enfant sait d'un coup qu'il a toujours eu cette capacité, comme un flash de conscience. Mais cette prise de conscience ne donne aucune maîtrise : tout le travail pratique (contrôle, technique, combat) reste entièrement à apprendre par la suite.\n\n"
            "Le cas des enfants de clan sans énergie (type Maki) : leur traitement dépend entièrement du clan concerné, certains les désavouent, d'autres les gardent avec un statut réduit. Il n'y a pas de règle universelle, chaque clan gère ça selon ses propres traditions.\n\n"
            "Pour un individu hors clan qui se déclare de lui-même (parce qu'un ancêtre avait de l'énergie occulte par exemple), la seule vérification qui compte est la démonstration physique de l'énergie. Cas particulier : une personne sous restriction céleste ne peut généralement pas produire d'énergie occulte offensive, mais garde un sixième sens qui lui permet de ressentir la présence des fléaux, ce qui peut aussi servir de preuve partielle de légitimité.\n\n"
            "Formation : il n'existe plus d'écoles au sens classique, elles ont été remplacées par les ordres (guildes). Les clans peuvent former leurs enfants en interne, mais ce n'est jamais une obligation légale. En revanche, un enfant de clan n'a pas le droit de partir en mission sans un ordre officiel émis par son clan, c'est ce cadre là qui légalise son activité de terrain.\n\n"
            "**LE BUT DE LEUR EXISTENCE**\n\n"
            "Leur mission est double :\n\n"
            "• Terrain : se rendre sur les zones d'apparition de fléaux, les éliminer, et récupérer les matériaux ainsi que les pierres d'énergie occulte qu'ils dégagent.\n\n"
            "• Recherche : comprendre pourquoi l'apparition des fléaux a autant évolué depuis Shibuya, un mystère toujours activement étudié.\n\n"
            "Le système des ordres structure tout leur travail concret. Un ordre fonctionne un peu comme une entreprise artisanale : il recrute qui il veut, sans charge de formation obligatoire, et peut opérer de trois façons différentes :\n\n"
            "• Ordres spécialisés uniquement dans la formation des nouveaux (qui touchent un pourcentage en renvoyant leurs recrues formées vers d'autres ordres)\n\n"
            "• Ordres qui envoient directement sur le terrain sans formation payante (les nouveaux ne perdent pas d'argent, mais risquent davantage leur vie)\n\n"
            "• Ordres hybrides qui font les deux, plus coûteux à rejoindre\n\n"
            "Un exorciste indépendant peut aussi exister en solo, sans ordre ni clan, à condition d'être en activité déclarée (en privé), un peu comme un artisan du BTP dans notre monde : il choisit qui il forme (ou pas), n'a pas de charges de formation à payer, et peut se lier ponctuellement à un clan pour exécuter des missions en échange d'un pourcentage sur les bénéfices.\n\n"
            "L'autorité mondiale des exorcistes ne gère pas le travail au quotidien : son rôle est de maintenir l'ordre et la paix malgré les tensions inter clans, et de distribuer les missions aux différents ordres. Elle n'intervient pas tant que la situation ne l'exige pas, sauf en cas de crise majeure (fléau de grade supérieur, catastrophe), où elle a alors le pouvoir de réquisitionner tous les exorcistes disponibles, peu importe leur puissance ou leur grade.\n\n"
            "**COMMENT ILS VIVENT**\n\n"
            "Répartition géographique des clans :\n\n"
            "• Tokyo : Gojo, Zenin, Geto\n\n"
            "• Kyoto : Kamo, Inumaki\n\n"
            "• Shinjuku : Ryomen, Kashimo\n\n"
            "Rémunération : chaque classe de mission a un salaire de base fixe, qui varie ensuite selon les matériaux récupérés et la quantité de pierres d'EO obtenues, plus la mission est difficile, plus ces bonus grimpent. Le salaire final est calculé après déduction des charges (frais d'ordre, etc.) et versé à la fin de la mission.\n\n"
            "Rejoindre un ordre : chaque ordre fixe ses propres conditions d'entrée, certains exigent un type de pouvoir précis, d'autres sont plus ouverts. Un exorciste refusé par un ordre peut toujours candidater ailleurs.\n\n"
            "Changer d'ordre : totalement possible en cours de carrière. En cas de démission, l'exorciste doit payer des frais de pénalité à l'ordre qu'il quitte. En cas de licenciement, c'est l'ordre qui doit lui verser des indemnités.\n\n"
            "La retraite : un exorciste peut arrêter son activité à tout moment (démission ou licenciement), mais pour toucher une pension de retraite, il doit avoir accompli une carrière complète : 30 ans d'activité s'il a exercé uniquement sur des missions de classe S ou 1 (les plus dangereuses), ou 40 ans d'activité dans les autres cas. Un départ précoce signifie renoncer à cette pension.\n\n"
            "**IMPORTANCE ET PERCEPTION SOCIALE**\n\n"
            "Les exorcistes sont vus comme des héros par la population, avec un statut hiérarchique officiel supérieur à la police et à l'armée, un respect comparable à celui accordé aux militaires dans notre monde.\n\n"
            "Privilèges légaux officiels :\n\n"
            "• Port d'objets maudits autorisé\n\n"
            "• Accès à des zones restreintes interdites au public\n\n"
            "• Immunités partielles liées à l'exercice de leur métier\n\n"
            "Couverture médiatique : ce ne sont pas des célébrités au quotidien, seuls les très hauts grades et les exploits notables font l'objet d'une médiatisation. Le grand public connaît donc surtout les figures marquantes, pas l'ensemble de la profession.\n\n"
            "Influence publique et politique : réservée aux chefs de clan et aux exorcistes exceptionnellement puissants, qui peuvent avoir un poids visible en dehors même de leur activité de terrain.\n\n"
            "Tensions et alliances entre clans : totalement dynamiques et ouvertes, ce sera aux joueurs incarnant les chefs de clan de décider des rapports de force, alliances, et rivalités entre les 7 familles. Rien n'est figé côté lore de base."
        ),
    }
}


def load_data():
    if not os.path.exists(DATA_FILE):
        os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_DATA, f, indent=4, ensure_ascii=False)
        return DEFAULT_DATA
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)


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
        title = f"{get_emoji_for_title(entry['title'])} {entry['title']}"
        pages = paginate_content(entry["content"])

        if len(pages) == 1:
            embed = discord.Embed(title=title, description=pages[0], color=discord.Color.blurple())
            await interaction.channel.send(embed=embed)
        else:
            view = PaginationView(title, pages, discord.Color.blurple())
            await interaction.channel.send(embed=view.build_embed(), view=view)


class Informations(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        self.bot.add_view(InformationsView())

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
