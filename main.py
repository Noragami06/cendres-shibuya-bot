import discord
from discord.ext import commands, tasks
from dotenv import load_dotenv
import os

from cogs.clans import build_clans_report
from cogs.depart import (
    retroactive_departure_check, backfill_role_points, backfill_fiche_message_ids,
    fix_detached_fiche_images, backfill_nom_frozen_to_clan,
)
from cogs.utils.database import get_bot_state, set_bot_state
from cogs.profil import (
    backfill_pv_system, backfill_secondary_sort_values, backfill_sort_unlock_status,
    backfill_territoire_defaults, backfill_fiche_record,
)
from cogs.utils.database import init_db

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.presences = True

bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

# Garde : les rattrapages profil (PV / sorts) ne doivent tourner qu'une fois par process.
_profil_backfills_done = False


@bot.event
async def setup_hook():
    init_db()

    await bot.load_extension("cogs.ticket")
    await bot.load_extension("cogs.salon")
    await bot.load_extension("cogs.informations")
    await bot.load_extension("cogs.depart")
    await bot.load_extension("cogs.banque")
    await bot.load_extension("cogs.inventaire")
    await bot.load_extension("cogs.shop")
    await bot.load_extension("cogs.profil")
    await bot.load_extension("cogs.histoire")
    await bot.load_extension("cogs.ordre")
    await bot.load_extension("cogs.reservation")
    await bot.load_extension("cogs.welcome")


@bot.event
async def on_ready():
    await bot.tree.sync()
    # Rattrapage des joueurs partis avant l'ajout du listener on_member_remove (sans risque de
    # le relancer à chaque démarrage : ne touche que les joueurs encore présents en base).
    await retroactive_departure_check(bot)
    # Rattrapage du barème de points de rôle pour les personnages validés avant ce système (sans
    # risque à relancer : ne traite que ceux sans ligne dans character_role_point_grants).
    for guild in bot.guilds:
        await backfill_role_points(guild)
    # Rattrapage UNIQUE (DB seule, global) : remet à NULL tout `nom` figé au nom de son clan actuel
    # (bug historique de création). Sa propre clé bot_state 'nom_frozen_backfill_done' garantit l'unicité.
    await backfill_nom_frozen_to_clan()
    # Rattrapage UNIQUE des fiche_message_id manquants (personnages validés avant cette colonne) :
    # scan complet de l'historique du salon des fiches validées, coûteux -> une seule fois par base.
    if get_bot_state("fiche_message_backfill_done") != "1":
        for guild in bot.guilds:
            await backfill_fiche_message_ids(guild)
        set_bot_state("fiche_message_backfill_done", "1")
    # Correction rétroactive UNIQUE des embeds de fiche à image détachée (après le backfill des ids,
    # dont elle dépend). Sa propre clé bot_state 'fiche_image_fix_done' évite tout re-scan.
    for guild in bot.guilds:
        await fix_detached_fiche_images(guild)
    # Rattrapages PV / valeurs de sorts secondaires / statut de déblocage des sorts principaux. Exécutés
    # UNE SEULE FOIS par process (on_ready peut se redéclencher aux reconnexions ; le rattrapage du
    # verrouillage étant un UPDATE inconditionnel, on le protège d'un re-déclenchement).
    global _profil_backfills_done
    if not _profil_backfills_done:
        _profil_backfills_done = True
        await backfill_pv_system()
        await backfill_secondary_sort_values()
        await backfill_sort_unlock_status()
        await backfill_territoire_defaults()
        # Rattrapage UNIQUE de fiche_record (source de vérité permanente de l'EO). Ensuite, la
        # resynchronisation est VIVANTE via sync_eo_with_fiche() à chaque affichage du profil.
        await backfill_fiche_record()
    if not status_loop.is_running():
        status_loop.start()


@tasks.loop(seconds=120)
async def status_loop():
    os.system('cls' if os.name == 'nt' else 'clear')

    guild = bot.guilds[0] if bot.guilds else None
    ping = round(bot.latency * 1000)

    if ping < 100:
        ping_status = "Excellent"
    elif ping < 200:
        ping_status = "Bon"
    elif ping < 350:
        ping_status = "Moyen"
    else:
        ping_status = "Mauvais"

    member_count = len([m for m in guild.members if not m.bot]) if guild else 0

    prefix_commands = [f"!{cmd.name}" for cmd in bot.commands]
    slash_commands = [f"/{cmd.name}" for cmd in bot.tree.get_commands()]

    print("=" * 50)
    print(f"✅ Connecté en tant que {bot.user}")
    print(f"📡 Ping : {ping} ms ({ping_status})")
    print(f"👥 Membres (hors bots) : {member_count}")
    print("=" * 50)
    print(f"📜 Commandes préfixe (!) — {len(prefix_commands)}")
    for c in prefix_commands:
        print(f"   {c}")
    print("-" * 50)
    print(f"⚡ Commandes slash (/) — {len(slash_commands)}")
    for c in slash_commands:
        print(f"   {c}")
    # build_clans_report se termine désormais par la section « === INCOHÉRENCES === » et son propre
    # séparateur de clôture ("=" * 50) : plus besoin d'en imprimer un second ici.
    print(build_clans_report(guild))


bot.run(TOKEN)