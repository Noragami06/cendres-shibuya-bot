import discord
from discord.ext import commands, tasks
from dotenv import load_dotenv
import os

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.presences = True

bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    await bot.tree.sync()  # synchronise les commandes slash "/"
    status_loop.start()

@tasks.loop(seconds=5)
async def status_loop():
    os.system('cls' if os.name == 'nt' else 'clear')

    guild = bot.guilds[0] if bot.guilds else None
    ping = round(bot.latency * 1000)
    member_count = len([m for m in guild.members if not m.bot]) if guild else 0

    prefix_commands = [f"!{cmd.name}" for cmd in bot.commands]
    slash_commands = [f"/{cmd.name}" for cmd in bot.tree.get_commands()]

    print("=" * 50)
    print(f"✅ Connecté en tant que {bot.user}")
    print(f"📡 Ping : {ping} ms")
    print(f"👥 Membres (hors bots) : {member_count}")
    print("=" * 50)
    print(f"📜 Commandes préfixe (!) — {len(prefix_commands)}")
    for c in prefix_commands:
        print(f"   {c}")
    print("-" * 50)
    print(f"⚡ Commandes slash (/) — {len(slash_commands)}")
    for c in slash_commands:
        print(f"   {c}")
    print("=" * 50)

# --- Exemple de commande préfixe ---
@bot.command()
async def ping(ctx):
    await ctx.send("🏓 Pong !")

# --- Exemple de commande slash ---
@bot.tree.command(name="ping", description="Vérifie le ping du bot")
async def slash_ping(interaction: discord.Interaction):
    await interaction.response.send_message("🏓 Pong !")

bot.run(TOKEN)