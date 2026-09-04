import discord
from discord.ext import commands

from cogs.utils import database as db
from cogs.utils.coherence_check import run_coherence_check

# ---------- IDs ----------
# Rôle marqueur : indique qu'un membre appartient à un clan
CLAN_MEMBER_ROLE_ID = 1521961709517148220

# Rôles spécifiques à chaque clan (l'ordre est celui d'affichage)
CLAN_ROLES = {
    "Gojo": 1521961741141934101,
    "Zenin": 1521961743729819799,
    "Kashimo": 1521961744908550166,
    "Inumaki": 1521961746196070400,
    "Ryomen": 1521961746615504926,
    "Kamo": 1521961748838613143,
    "Geto": 1521961753141841921,
}

# Rôles de grade : (nom affiché, ID du rôle, émoji, limite [1 = poste unique, None = illimité])
GRADE_ROLES = [
    ("Chef du clan", 1521963027925172344, "👑", 1),
    ("Bras droit", 1521963034434601040, "🛡️", 1),
    ("Bras gauche", 1521963034736726158, "🛡️", 1),
    ("Héritier", 1521963035898548455, "⭐", 1),
    ("Bras droit héritier", 1521963040155766835, "⭐", 1),
    ("Bras gauche héritier", 1521963040809943120, "⭐", 1),
    ("Membres principaux", 1521963104903233658, "⚔️", None),
    ("Membres secondaires", 1521963107918807140, "🔸", None),
]

SEPARATOR = "═" * 48
HEADER_WIDTH = 48
GRADE_WIDTH = 44


def _pad_line(left: str, right: str, width: int) -> str:
    """Aligne `right` à droite en gardant `left` à gauche, sur une largeur donnée."""
    pad = max(1, width - len(left) - len(right))
    return f"{left}{' ' * pad}{right}"


def build_clans_report(guild) -> str:
    """Construit le rapport texte des 7 clans à partir des rôles Discord des membres."""
    if guild is None:
        return "Aucun serveur détecté pour le rapport des clans."

    # Structure : clan -> grade -> liste de pseudos
    clan_data = {clan: {grade[0]: [] for grade in GRADE_ROLES} for clan in CLAN_ROLES}

    for member in guild.members:
        role_ids = {role.id for role in member.roles}

        # Le membre doit porter le rôle marqueur "appartient à un clan"
        if CLAN_MEMBER_ROLE_ID not in role_ids:
            continue

        # Détermine le clan (premier rôle de clan trouvé, dans l'ordre)
        member_clan = None
        for clan_name, clan_role_id in CLAN_ROLES.items():
            if clan_role_id in role_ids:
                member_clan = clan_name
                break
        if member_clan is None:
            continue

        # Détermine le grade (premier rôle de grade trouvé, dans l'ordre de priorité)
        member_grade = None
        for grade_name, grade_role_id, _emoji, _limit in GRADE_ROLES:
            if grade_role_id in role_ids:
                member_grade = grade_name
                break
        if member_grade is None:
            continue

        clan_data[member_clan][member_grade].append(member.display_name)

    # SOURCE UNIQUE d'occupation (validated_characters, tous slots + cap courant), partagée avec l'embed
    # d'étape de /depart : l'en-tête « occupés / cap » ne dépend PLUS des rôles Discord (qui ignorent les
    # slots 2/3 virtuels) ni d'un cap codé en dur — plus aucun écart avec le message du bot.
    occupancy = db.get_clan_occupancy(guild.id)

    lines = []
    for index, clan_name in enumerate(CLAN_ROLES):
        grades = clan_data[clan_name]
        occ, cap = occupancy.get(clan_name.lower(), (0, 0))

        if index > 0:
            lines.append("")  # ligne vide entre chaque clan

        lines.append(SEPARATOR)
        lines.append(_pad_line(f"🏯  CLAN {clan_name.upper()}", f"{occ} / {cap}", HEADER_WIDTH))
        lines.append(SEPARATOR)

        for grade_name, _grade_role_id, emoji, limit in GRADE_ROLES:
            members = grades[grade_name]
            count = len(members)
            limit_str = str(limit) if limit is not None else "∞"

            lines.append(_pad_line(f"{emoji} {grade_name}", f"({count}/{limit_str})", GRADE_WIDTH))

            if count == 0:
                lines.append("   • (vacant)" if limit == 1 else "   • (aucun)")
            else:
                for name in members:
                    lines.append(f"   • {name}")

            lines.append("")  # ligne vide entre chaque grade

        lines.append(SEPARATOR)

    # Rapport d'incohérences intégré au status_loop (imprimé toutes les 120 s après le rapport clans).
    # La détection est déléguée au module partagé (même code que le script check_coherence.py, jamais
    # dupliqué). Enveloppée dans un try/except : une panne de la vérification (base inaccessible, import
    # manquant…) ne doit JAMAIS empêcher l'affichage du rapport des clans.
    try:
        lignes_incoherence = run_coherence_check()
    except Exception as e:
        lignes_incoherence = [f"❌ Erreur lors de la vérification de cohérence : {e}"]

    lines.append("")
    lines.append("=== INCOHÉRENCES ===")
    if lignes_incoherence:
        lines.extend(lignes_incoherence)
    else:
        lines.append("Aucune incohérence détectée.")
    lines.append("=" * 50)

    return "\n".join(lines)


class Clans(commands.Cog):
    def __init__(self, bot):
        self.bot = bot


async def setup(bot):
    await bot.add_cog(Clans(bot))
