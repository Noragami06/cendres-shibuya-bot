from discord.ext import commands

# Cog d'accueil des nouveaux membres.
#
# L'embed « 🚧 Serveur en construction » envoyé à chaque arrivée (via on_member_join) a été RETIRÉ : le
# serveur est officiellement ouvert, ce message n'a plus lieu d'être. Ce listener ne faisait QUE cet envoi
# (aucune autre action : ni rôle, ni salon), il est donc supprimé entièrement.
#
# Le cog est conservé (vide) pour ne pas modifier la séquence de chargement de main.py et rester prêt si
# un futur message d'accueil doit être rebranché ici. Le message de bienvenue « principal » reste géré par
# le bot Koya, indépendamment de nous.


class Welcome(commands.Cog):
    def __init__(self, bot):
        self.bot = bot


async def setup(bot):
    await bot.add_cog(Welcome(bot))
