import asyncio

import discord
from discord.ext import commands

# ---------- IDs ----------
# Bot Koya : poste le message de bienvenue « principal » à l'arrivée d'un membre. On complète le sien
# (on ne le remplace pas) — d'où le petit délai avant notre propre embed, pour rester APRÈS le sien.
KOYA_BOT_ID = 276060004262477825
# Salon où Koya poste ses messages de bienvenue (et où nous postons le complément).
WELCOME_CHANNEL_ID = 1521818957193937026


class Welcome(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if member.bot:
            return  # ignore les autres bots qui rejoindraient

        channel = member.guild.get_channel(WELCOME_CHANNEL_ID)
        if channel is None:
            return

        await asyncio.sleep(3)  # laisse le temps au message de Koya de s'afficher en premier

        embed = discord.Embed(
            title="🚧 Serveur en construction",
            description=(
                f"Bienvenue {member.mention} !\n\n"
                "Le serveur est actuellement en construction, il est donc normal qu'il ne soit "
                "pas encore pleinement actif. Merci de ta patience, et n'hésite pas à explorer "
                "les salons déjà disponibles en attendant !"
            ),
            color=discord.Color.gold(),
        )
        await channel.send(embed=embed)


async def setup(bot):
    await bot.add_cog(Welcome(bot))
