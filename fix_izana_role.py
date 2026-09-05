"""Rattrapage PONCTUEL du rôle de clan d'Izana Zenin (Slot 1).

Sa fiche dit déjà « gojo » en base, mais son rôle Discord était resté « Zenin » (l'ancien /modification
avalait silencieusement l'erreur de rôle). Ce script retire le rôle Zenin et attribue Gojo + le rôle
marqueur « membre de clan », pour aligner Discord sur la fiche.

À lancer UNE FOIS depuis la racine du projet :  python fix_izana_role.py
(nécessite DISCORD_TOKEN dans .env et la permission « Gérer les rôles » pour le bot, le rôle du bot
devant être AU-DESSUS des rôles de clan dans la hiérarchie du serveur). Supprime ce fichier ensuite.
"""
import asyncio
import os

import discord
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

GUILD_ID = 1514876233735868527
USER_ID = 567670077995089922          # Izana Zenin (Slot 1)
ROLE_ZENIN = 1521961743729819799      # ancien rôle à retirer
ROLE_GOJO = 1521961741141934101       # nouveau rôle à attribuer
ROLE_CLAN_MEMBER = 1521961709517148220  # marqueur « appartient à un clan »

intents = discord.Intents.default()
intents.members = True
client = discord.Client(intents=intents)


@client.event
async def on_ready():
    try:
        guild = client.get_guild(GUILD_ID) or await client.fetch_guild(GUILD_ID)
        if guild is None:
            print("❌ Serveur introuvable.")
            return
        member = guild.get_member(USER_ID)
        if member is None:
            try:
                member = await guild.fetch_member(USER_ID)
            except discord.NotFound:
                member = None
        if member is None:
            print("❌ Membre Izana Zenin introuvable sur le serveur.")
            return

        role_zenin = guild.get_role(ROLE_ZENIN)
        role_gojo = guild.get_role(ROLE_GOJO)
        role_marker = guild.get_role(ROLE_CLAN_MEMBER)

        to_remove = [role_zenin] if (role_zenin and role_zenin in member.roles) else []
        to_add = []
        if role_gojo and role_gojo not in member.roles:
            to_add.append(role_gojo)
        if role_marker and role_marker not in member.roles:
            to_add.append(role_marker)

        try:
            if to_remove:
                await member.remove_roles(*to_remove, reason="Rattrapage clan Izana : Zenin -> Gojo")
            if to_add:
                await member.add_roles(*to_add, reason="Rattrapage clan Izana : Zenin -> Gojo")
        except discord.Forbidden:
            print("❌ Forbidden : le bot n'a pas la permission « Gérer les rôles », ou les rôles de clan "
                  "sont plus hauts que le rôle du bot dans la hiérarchie. Remonte le rôle du bot et relance.")
            return
        except discord.HTTPException as e:
            print(f"❌ Erreur Discord : {e}")
            return

        print(f"✅ Rattrapage effectué pour {member.display_name} : "
              f"retiré {[r.name for r in to_remove]}, ajouté {[r.name for r in to_add]}.")
        print("   (Si les deux listes sont vides, son état Discord était déjà correct.)")
    finally:
        await client.close()


if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("DISCORD_TOKEN manquant dans .env")
    client.run(TOKEN)
