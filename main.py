import discord
from discord.ext import commands
from dotenv import load_dotenv
import os

# Charge les variables du fichier .env
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

# Configuration des intents (permissions du bot)
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.presences = True

# Création du bot avec le préfixe de commande "!"
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"✅ Connecté en tant que {bot.user} (ID: {bot.user.id})")
    print("------")

@bot.command()
async def ping(ctx):
    await ctx.send("🏓 Pong !")

# Lancement du bot
bot.run(TOKEN)