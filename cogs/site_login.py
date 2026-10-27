"""Commande /login-site : crée un identifiant et un mot de passe TEMPORAIRES pour se connecter au site.

Principe :
- Le joueur lance /login-site (la réponse n'est visible que par lui : ephemeral).
- Le bot crée un identifiant de 15 caractères (chiffres + majuscules + minuscules) et un mot de passe.
- Les deux sont à usage unique et expirent vite. Le site (étape suivante) les vérifiera, puis demandera
  au joueur de choisir ses propres identifiants.
- Le bot NE recopie PAS les infos du personnage : il retient seulement à quel joueur appartiennent ces
  identifiants. Le site ira lire les infos à jour dans la base (le premier personnage = le plus petit slot).
- Le mot de passe n'est JAMAIS stocké en clair : on garde uniquement son empreinte (hachage).
"""
import hashlib
import hmac
import math
import secrets
import sqlite3
import string
from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands

from cogs.utils import database as db
from cogs.banque import get_characters, PHOENIX_COLOR

# ---------- Réglages (tu peux les modifier ici) ----------
LOGIN_ID_LENGTH = 15           # longueur de l'identifiant temporaire
TEMP_PASSWORD_LENGTH = 16      # longueur du mot de passe temporaire
TEMP_VALIDITY_MINUTES = 30     # durée de vie des identifiants temporaires
COOLDOWN_SECONDS = 60          # délai minimum entre deux /login-site pour un même joueur
PBKDF2_ITERATIONS = 200_000    # solidité du hachage du mot de passe

_LOWER = string.ascii_lowercase
_UPPER = string.ascii_uppercase
_DIGITS = string.digits
_ALL = _LOWER + _UPPER + _DIGITS


def _now() -> datetime:
    return datetime.utcnow()


# =====================================================================
# GÉNÉRATION ET HACHAGE
# =====================================================================
def _random_mix(length: int) -> str:
    """Texte aléatoire avec au moins 1 minuscule, 1 majuscule et 1 chiffre."""
    chars = [secrets.choice(_LOWER), secrets.choice(_UPPER), secrets.choice(_DIGITS)]
    chars += [secrets.choice(_ALL) for _ in range(length - 3)]
    secrets.SystemRandom().shuffle(chars)
    return "".join(chars)


def generate_login_id() -> str:
    return _random_mix(LOGIN_ID_LENGTH)


def generate_temp_password() -> str:
    return _random_mix(TEMP_PASSWORD_LENGTH)


def hash_password(password: str, salt=None):
    """Retourne (empreinte_hex, sel_hex). Le sel est un texte aléatoire propre à chaque mot de passe."""
    if salt is None:
        salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return digest.hex(), salt.hex()


def verify_password(password: str, digest_hex: str, salt_hex: str) -> bool:
    """Vérifie un mot de passe saisi contre l'empreinte enregistrée (utilisé par le site à l'étape suivante)."""
    digest, _ = hash_password(password, bytes.fromhex(salt_hex))
    return hmac.compare_digest(digest, digest_hex)


# =====================================================================
# BASE DE DONNÉES
# =====================================================================
def init_site_tables():
    with db.get_connection() as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS site_temp_credentials ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "user_id INTEGER NOT NULL, "
            "guild_id INTEGER NOT NULL, "
            "temp_login_id TEXT NOT NULL UNIQUE, "
            "temp_password_hash TEXT NOT NULL, "
            "temp_password_salt TEXT NOT NULL, "
            "created_at TEXT NOT NULL, "
            "expires_at TEXT NOT NULL, "
            "used INTEGER NOT NULL DEFAULT 0)"
        )


def seconds_until_next_allowed(user_id: int, guild_id: int) -> int:
    """0 si le joueur peut refaire la commande, sinon le nombre de secondes à attendre."""
    with db.get_connection() as conn:
        row = conn.execute(
            "SELECT created_at FROM site_temp_credentials WHERE user_id = ? AND guild_id = ? "
            "ORDER BY id DESC LIMIT 1",
            (user_id, guild_id),
        ).fetchone()
    if not row:
        return 0
    elapsed = (_now() - datetime.fromisoformat(row["created_at"])).total_seconds()
    return max(0, math.ceil(COOLDOWN_SECONDS - elapsed))


def create_temp_credentials(user_id: int, guild_id: int):
    """Crée de nouveaux identifiants temporaires. Retourne (login_id, mot_de_passe, date_expiration).
    Les anciens identifiants non utilisés de ce joueur sont annulés : seuls les derniers fonctionnent."""
    password = generate_temp_password()
    digest, salt = hash_password(password)
    now = _now()
    expires = now + timedelta(minutes=TEMP_VALIDITY_MINUTES)
    with db.get_connection() as conn:
        conn.execute(
            "UPDATE site_temp_credentials SET used = 1 WHERE user_id = ? AND guild_id = ? AND used = 0",
            (user_id, guild_id),
        )
        for _ in range(10):
            login_id = generate_login_id()
            try:
                conn.execute(
                    "INSERT INTO site_temp_credentials "
                    "(user_id, guild_id, temp_login_id, temp_password_hash, temp_password_salt, "
                    "created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (user_id, guild_id, login_id, digest, salt, now.isoformat(), expires.isoformat()),
                )
                break
            except sqlite3.IntegrityError:
                continue  # identifiant déjà pris (très rare) -> on en tire un autre
        else:
            raise RuntimeError("Impossible de générer un identifiant unique.")
    return login_id, password, expires


# =====================================================================
# COG
# =====================================================================
class SiteLogin(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        init_site_tables()

    @app_commands.command(
        name="login-site",
        description="Crée tes identifiants temporaires pour te connecter au site",
    )
    async def login_site(self, interaction: discord.Interaction):
        if interaction.guild is None:
            await interaction.response.send_message(
                "Cette commande s'utilise sur le serveur.", ephemeral=True)
            return

        chars = get_characters(interaction.user.id, interaction.guild.id)
        if not chars:
            await interaction.response.send_message(
                "Tu n'as aucun personnage validé : tu ne peux pas te connecter au site.", ephemeral=True)
            return

        wait = seconds_until_next_allowed(interaction.user.id, interaction.guild.id)
        if wait > 0:
            await interaction.response.send_message(
                f"Patiente encore {wait} seconde(s) avant de refaire la commande.", ephemeral=True)
            return

        login_id, password, expires = create_temp_credentials(interaction.user.id, interaction.guild.id)

        first = chars[0]  # get_characters trie par slot : le premier est le plus petit slot
        if len(chars) == 1:
            persos = f"Ton personnage : **{first['character_name']}**"
        else:
            liste = "\n".join(f"Slot {c['slot_number']} — {c['character_name']}" for c in chars)
            persos = (f"Tu as **{len(chars)}** personnages :\n{liste}\n\n"
                      f"Le site s'ouvrira d'abord sur ton premier personnage : "
                      f"**{first['character_name']}** (Slot {first['slot_number']}).")

        expire_ts = int(expires.replace(tzinfo=timezone.utc).timestamp())
        embed = discord.Embed(
            title="🔐 Tes identifiants temporaires",
            description=(f"{persos}\n\nSur le site, colle ces deux informations dans la page de connexion. "
                         f"Ensuite, le site te demandera de choisir ton propre identifiant et mot de passe."),
            color=PHOENIX_COLOR,
        )
        embed.add_field(name="Identifiant", value=f"```{login_id}```", inline=False)
        embed.add_field(name="Mot de passe", value=f"```{password}```", inline=False)
        embed.add_field(
            name="Validité",
            value=f"À usage unique. Expire <t:{expire_ts}:R>. Si tu refais la commande, les précédents ne marchent plus.",
            inline=False,
        )
        embed.set_footer(text="Message visible par toi seul. Ne partage jamais ces informations.")
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot):
    await bot.add_cog(SiteLogin(bot))
