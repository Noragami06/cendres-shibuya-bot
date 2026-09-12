# RÈGLES DE ROBUSTESSE PERMANENTES DU PROJET (rappel) : boutons revérifiés au clic, flux textuels isolés
# par utilisateur, transactions revérifiées en temps réel, anti double-clic. /daily les applique.
#
# TODO (POINT NON ABORDÉ, §10) : le SYSTÈME DE COFFRES (contenu, ouverture, loot par rareté) n'est PAS
# construit. /daily ne fait qu'AFFICHER/mentionner les % d'obtention par classe (DAILY_COFFRE_ACCESS) ;
# la distribution effective d'un coffre à la victoire est différée à un système ultérieur.

import asyncio
import random
from datetime import datetime, timedelta

import discord
from discord import app_commands
from discord.ext import commands

from cogs.utils import database as db
from cogs.utils.image_gen import generate_daily_enemy_image
from cogs.banque import get_characters, get_character, PHOENIX_COLOR

# =====================================================================
# 0. VALEURS DE RÉFÉRENCE
# =====================================================================
DAILY_STATS_TABLE = {
    "4": {"pv_pct": 0.60, "eo_pct": 0.50, "stat_pct": 0.60, "variance": None},
    "3": {"pv_pct": 0.75, "eo_pct": 0.65, "stat_pct": 0.75, "variance": None},
    "2": {"pv_pct": 1.00, "eo_pct": 1.00, "stat_pct": 1.00, "variance": 0.10},
    "1": {"pv_pct": 1.30, "eo_pct": 1.40, "stat_pct": 1.30, "variance": None},
    "S": {"pv_pct": 1.70, "eo_pct": 2.00, "stat_pct": 1.70, "variance": None},
}

DAILY_COFFRE_ACCESS = {
    "4": {"commun": 80, "rare": 20},
    "3": {"commun": 60, "rare": 30, "epic": 10},
    "2": {"rare": 55, "epic": 35, "legendaire": 10},
    "1": {"epic": 55, "legendaire": 35, "mythique": 10},
    "S": {"legendaire": 70, "mythique": 30},
}
DAILY_COFFRE_KEYS = ["commun", "rare", "epic", "legendaire", "mythique"]
DAILY_COFFRE_LABELS = {"commun": "Commun", "rare": "Rare", "epic": "Épique",
                       "legendaire": "Légendaire", "mythique": "Mythique"}

DAILY_POTION_TABLE = {
    "4": {"nombre": 3, "pct_restaure": 60},
    "3": {"nombre": 2, "pct_restaure": 50},
    "2": {"nombre": 1, "pct_restaure": 45},
    "1": {"nombre": 0, "pct_restaure": 0},
    "S": {"nombre": 0, "pct_restaure": 0},
}

DAILY_REWARD_POINTS = {"4": 5, "3": 10, "2": 16, "1": 32, "S": 65}

DAILY_DAMAGE_RATIO = 0.03  # dégâts = force_actuelle * 0.03, aucun tirage aléatoire

DAILY_BLOCK_CHANCES = [100, 100, 90, 80, 70, 65, 60, 55, 50]  # blocages 1 à 9
DAILY_BLOCK_DECREMENT_AFTER_9 = 2  # -2% par blocage au delà du 9e, SANS PLANCHER (peut atteindre 0%)

DAILY_SPELL_BLOCK_CHANCE = 30
DAILY_SPELL_BLOCK_REDUCTION = 50

DAILY_EO_URGENCE_SEUIL = 500

OWNER_ID = 396615332346855428
FICHE_STAFF_ROLE_ID = 1521229332075512039
DAILY_COOLDOWN_HOURS_JOUEUR = 24
DAILY_COOLDOWN_HOURS_STAFF = 12
# L'owner (OWNER_ID) n'a AUCUN cooldown, peu importe son statut staff ou non.

DAILY_MAX_ADVERSAIRE_REROLL = 3

DAILY_PV_FLOOR = 100  # le combat s'arrête dès qu'un camp atteint 100 PV ou moins

# IA du PNJ : pondération des actions par classe (jamais 100% d'un seul choix). Plus la classe est haute,
# plus l'IA privilégie l'attaque et le renforcement.
DAILY_PNJ_WEIGHTS = {
    "4": {"attaquer": 60, "bloquer": 35, "renforcement": 5},
    "3": {"attaquer": 60, "bloquer": 30, "renforcement": 10},
    "2": {"attaquer": 60, "bloquer": 25, "renforcement": 15},
    "1": {"attaquer": 65, "bloquer": 15, "renforcement": 20},
    "S": {"attaquer": 70, "bloquer": 10, "renforcement": 20},
}

# Potion du JOUEUR en combat : le système de potions existant (soin/force/force_sort) n'a pas de potion
# « restaure EO ». Décision provisoire documentée : utiliser une potion possédée en consomme 1 et restaure
# l'EO d'un pourcentage de l'EO max. (À réconcilier si une vraie potion d'EO est ajoutée plus tard.)
DAILY_PLAYER_POTION_EO_PCT = 50

CLASSES_ORDRE = ["4", "3", "2", "1", "S"]

# Petit générateur de noms de PNJ cohérents JJK (varie un minimum d'un tirage à l'autre).
_PNJ_PREFIX = ["Fléau", "Esprit", "Ombre", "Malédiction", "Spectre", "Fantôme", "Vestige", "Aberration"]
_PNJ_MID = ["errant", "affamé", "rancunier", "déchu", "corrompu", "sans visage", "hurlant", "oublié"]
_PNJ_SUFFIX = ["de Shibuya", "des Cendres", "du Crépuscule", "de la Faille", "de minuit", "du Vide", ""]


# =====================================================================
# HELPERS PURS (testables sans Discord)
# =====================================================================
def generate_pnj_name() -> str:
    parts = [random.choice(_PNJ_PREFIX), random.choice(_PNJ_MID), random.choice(_PNJ_SUFFIX)]
    return " ".join(p for p in parts if p).strip()


def complete_coffres(classe: str) -> dict:
    """Complète les 5 clés de coffres à 0% pour les raretés non accessibles à cette classe."""
    base = DAILY_COFFRE_ACCESS.get(classe, {})
    return {k: base.get(k, 0) for k in DAILY_COFFRE_KEYS}


def block_chance(compteur: int) -> int:
    """Chance (%) de bloquer une attaque PHYSIQUE au n-ième blocage. <=9 : table fixe ; au delà : -2%/blocage
    SANS plancher artificiel (peut atteindre 0). Clampée à 0 minimum."""
    if compteur <= 9:
        chance = DAILY_BLOCK_CHANCES[compteur - 1]
    else:
        chance = DAILY_BLOCK_CHANCES[-1] - DAILY_BLOCK_DECREMENT_AFTER_9 * (compteur - 9)
    return max(0, chance)


def generate_pnj(player_stats: dict, classe: str) -> dict:
    """player_stats : {pv, eo, force, vitesse, arme, rct, territoire}. Applique DAILY_STATS_TABLE[classe].
    Variance (classe 2) : chaque valeur *= uniform(0.90, 1.10) indépendamment. Sinon déterministe."""
    tbl = DAILY_STATS_TABLE[classe]
    variance = tbl["variance"]

    def scale(value, pct):
        if variance is None:
            return round(value * pct)
        return round(value * pct * random.uniform(1 - variance, 1 + variance))

    return {
        "name": generate_pnj_name(),
        "classe": classe,
        "pv_max": max(1, scale(player_stats["pv"], tbl["pv_pct"])),
        "eo_max": max(0, scale(player_stats["eo"], tbl["eo_pct"])),
        "force": max(0, scale(player_stats["force"], tbl["stat_pct"])),
        "vitesse": max(0, scale(player_stats["vitesse"], tbl["stat_pct"])),
        "arme": max(0, scale(player_stats["arme"], tbl["stat_pct"])),
        "rct": max(0, scale(player_stats["rct"], tbl["stat_pct"])),
        "territoire": max(0, scale(player_stats["territoire"], tbl["stat_pct"])),
        "potions": DAILY_POTION_TABLE[classe]["nombre"],
        "potion_pct": DAILY_POTION_TABLE[classe]["pct_restaure"],
    }


def weighted_pick(weights: dict):
    """Choix pondéré parmi {clé: poids}. Retourne une clé."""
    total = sum(weights.values())
    r = random.uniform(0, total)
    acc = 0
    for k, w in weights.items():
        acc += w
        if r <= acc:
            return k
    return next(iter(weights))


def current_force(force_base: int, pv_actuel: int, pv_max: int, bonus_temp: int = 0) -> int:
    """force_actuelle = force_de_base * (pv_actuel / pv_max) + bonus_temporaire (renforcement du tour)."""
    ratio = (pv_actuel / pv_max) if pv_max > 0 else 0
    return round(force_base * ratio) + bonus_temp


def physical_damage(force_actuelle: int) -> int:
    return round(force_actuelle * DAILY_DAMAGE_RATIO)


def best_spell_ratio(spells: list):
    """spells : liste de dicts {name, cost, damage, ...}. Retourne le sort au meilleur ratio dégâts/coût
    (coût 0 -> ratio infini favorisé), ou None si liste vide."""
    if not spells:
        return None
    def ratio(s):
        return s["damage"] / s["cost"] if s["cost"] > 0 else float("inf")
    return max(spells, key=ratio)


def _daily_bar(cur: int, mx: int, n: int = 10) -> str:
    """Barre de progression textuelle à blocs (█ pleins / ░ vides), longueur n. Sûre si mx <= 0."""
    if mx <= 0:
        filled = 0
    else:
        filled = max(0, min(n, round(cur / mx * n)))
    return "█" * filled + "░" * (n - filled)


def _time_left_str(delta: timedelta) -> str:
    total = int(delta.total_seconds())
    h = total // 3600
    m = (total % 3600) // 60
    if h > 0:
        return f"{h}h {m}min"
    return f"{m}min"


# =====================================================================
# VUES EN SESSION
# =====================================================================
class DailyChoiceView(discord.ui.View):
    """Boutons en session : le premier clic (par le propriétaire) fixe self.result et arrête la vue.
    options : liste de (key, label, emoji, style)."""

    def __init__(self, owner_id, options, timeout=180):
        super().__init__(timeout=timeout)
        self.owner_id = owner_id
        self.result = None
        for key, label, emoji, style in options:
            btn = discord.ui.Button(label=label, emoji=emoji, style=style)
            btn.callback = self._make_cb(key)
            self.add_item(btn)

    def _make_cb(self, key):
        async def cb(interaction: discord.Interaction):
            if interaction.user.id != self.owner_id:
                await interaction.response.send_message("Ce combat ne t'appartient pas.", ephemeral=True)
                return
            self.result = key
            await interaction.response.edit_message(view=None)
            self.stop()
        return cb


# =====================================================================
# COG
# =====================================================================
class Daily(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._active_users = set()  # isolation de flux par joueur (mémoire, vidée au redémarrage)

    # ---------- verrou / attente (mêmes patterns que shop/inventaire) ----------
    def _acquire(self, user_id) -> bool:
        inv = self.bot.get_cog("Inventaire")
        lock = inv._active_users if (inv and hasattr(inv, "_active_users")) else self._active_users
        if user_id in lock:
            return False
        lock.add(user_id)
        return True

    def _release(self, user_id):
        inv = self.bot.get_cog("Inventaire")
        lock = inv._active_users if (inv and hasattr(inv, "_active_users")) else self._active_users
        lock.discard(user_id)

    async def wait_message(self, channel, author, timeout=180):
        def check(m):
            return m.channel.id == channel.id and m.author.id == author.id and not m.author.bot
        try:
            return await self.bot.wait_for("message", check=check, timeout=timeout)
        except asyncio.TimeoutError:
            return None

    async def _ask_int(self, channel, user, prompt, minimum, maximum):
        while True:
            await channel.send(prompt)
            m = await self.wait_message(channel, user)
            if m is None:
                return None
            c = m.content.strip().replace(" ", "")
            if c.lower() in ("cancel", "annuler"):
                return None
            if not c.isdigit():
                await channel.send(f"Entre un entier entre {minimum} et {maximum}.")
                continue
            v = int(c)
            if v < minimum or v > maximum:
                await channel.send(f"Valeur hors limites ({minimum}-{maximum}). Réessaie.")
                continue
            return v

    async def _select_character(self, channel, user):
        """Menu déroulant si plusieurs personnages, auto si un seul, None si aucun."""
        chars = get_characters(user.id, channel.guild.id)
        if not chars:
            await channel.send("❌ Tu n'as aucun personnage validé.")
            return None
        if len(chars) == 1:
            return chars[0]["id"]
        options = [(str(c["id"]), f"Slot {c['slot_number']} — {c['character_name']}", None,
                    discord.ButtonStyle.secondary) for c in chars[:5]]
        view = DailyChoiceView(user.id, options)
        await channel.send("Choisis le personnage :", view=view)
        await view.wait()
        return int(view.result) if view.result else None

    # ---------- stats réelles du joueur ----------
    def _player_stats(self, character_id) -> dict:
        prof = db.get_or_create_profile(character_id)

        def total(key):
            return db.get_stat_base_pts(character_id, key) + db.sum_buff_points(character_id, key)

        return {
            "pv": prof["pv_max"], "eo": prof["eo_max"],
            "force": total("force"), "vitesse": total("vitesse"),
            "arme": total("armes_maudites"), "rct": total("rct"),
            "territoire": total("territoire"),
        }

    def _unlocked_spells(self, character_id, eo_max) -> list:
        """Sorts secondaires DÉBLOQUÉS utilisables en combat, avec coût EO concret et dégâts. Le coût =
        cout_eo_fixe si converti, sinon cout_pct % de l'EO max. Garde le sort_id du PRINCIPAL (pour l'XP)."""
        spells = []
        for principal in db.get_character_sorts(character_id):
            plevel = principal["level"] or 0
            for sec in db.get_secondary_sorts(principal["id"]):
                if not sec["name"]:
                    continue
                niveau_requis = sec["niveau_requis"] if sec["niveau_requis"] is not None else 999
                if plevel < niveau_requis:
                    continue
                if sec["cout_eo_fixe"] is not None:
                    cost = sec["cout_eo_fixe"]
                else:
                    cost = round((sec["cout_pct"] or 0) / 100 * eo_max)
                spells.append({
                    "name": sec["name"], "cost": int(cost), "damage": int(sec["degats"] or 0),
                    "principal_id": principal["id"], "principal_name": principal["name"],
                })
        return spells

    # =================================================================
    # COMMANDE /daily
    # =================================================================
    @app_commands.command(name="daily", description="Affronte un adversaire du jour (combat InRP compté)")
    async def daily(self, interaction: discord.Interaction):
        user = interaction.user
        if not self._acquire(user.id):
            await interaction.response.send_message(
                "Tu as déjà une action en cours, termine la d'abord.", ephemeral=True)
            return
        try:
            await interaction.response.send_message("⚔️ Lancement de /daily…", ephemeral=True)
            channel = interaction.channel

            # §1 : sélection du personnage.
            character_id = await self._select_character(channel, user)
            if character_id is None:
                return

            # §1 : cooldown (owner exempté, staff 12h, joueur 24h).
            if user.id != OWNER_ID:
                is_staff = any(r.id == FICHE_STAFF_ROLE_ID for r in getattr(user, "roles", []))
                seuil_h = DAILY_COOLDOWN_HOURS_STAFF if is_staff else DAILY_COOLDOWN_HOURS_JOUEUR
                row = db.get_daily_cooldown(character_id)
                if row and row["last_daily_at"]:
                    try:
                        last = datetime.fromisoformat(row["last_daily_at"])
                    except ValueError:
                        last = None
                    if last is not None:
                        ecoule = datetime.utcnow() - last
                        if ecoule < timedelta(hours=seuil_h):
                            reste = timedelta(hours=seuil_h) - ecoule
                            await channel.send(
                                f"⏳ Tu pourras refaire /daily avec ce personnage dans {_time_left_str(reste)}.")
                            return

            # §2-4 : sélection difficulté + preview PNJ + boutons (boucle reroll / changer difficulté).
            pnj, classe = await self._difficulty_and_preview(channel, user, character_id)
            if pnj is None:
                return

            # §5-9 : combat + récompenses.
            await self._run_combat(channel, user, character_id, classe, pnj)
        finally:
            self._release(user.id)

    # =================================================================
    # §2-4 : DIFFICULTÉ + PREVIEW ADVERSAIRE
    # =================================================================
    async def _difficulty_and_preview(self, channel, user, character_id):
        """Retourne (pnj, classe) prêt au combat, ou (None, None) si annulé."""
        player_stats = self._player_stats(character_id)
        while True:
            # §2 : choix de la difficulté (réponse 1-5).
            await channel.send(embed=discord.Embed(
                title="🎯 Choisis la difficulté",
                description="**1.** Classe 4\n**2.** Classe 3\n**3.** Classe 2\n**4.** Classe 1\n**5.** Classe S",
                color=PHOENIX_COLOR))
            choix = await self._ask_int(channel, user, "Réponds par un numéro de 1 à 5 (ou « annuler »).", 1, 5)
            if choix is None:
                await channel.send("⏳ /daily annulé.")
                return None, None
            classe = CLASSES_ORDRE[choix - 1]

            # §2 : embed avantages / malus.
            if classe in ("4", "3"):
                adv = "nettement plus faible que toi"
            elif classe == "2":
                adv = "à ton niveau (parfois plus fort, parfois plus faible)"
            else:
                adv = "plus fort que toi"
            coffres = ", ".join(DAILY_COFFRE_LABELS[k] for k in DAILY_COFFRE_KEYS
                                if DAILY_COFFRE_ACCESS.get(classe, {}).get(k))
            await channel.send(embed=discord.Embed(
                title=f"📋 Classe {classe} sélectionnée",
                description=(f"🎁 Récompense par action réussie : **+{DAILY_REWARD_POINTS[classe]} points**\n"
                             f"⚔️ Adversaire : **{adv}**\n"
                             f"🎁 Coffres accessibles : {coffres}"),
                color=PHOENIX_COLOR))

            # §3-4 : génération PNJ + pillow + 3 boutons, avec reroll (max 3).
            pnj = generate_pnj(player_stats, classe)
            rerolls = 0
            while True:
                await self._send_pnj_pillow(channel, pnj)
                restants = DAILY_MAX_ADVERSAIRE_REROLL - rerolls
                view = DailyChoiceView(user.id, [
                    ("reroll", f"Changer d'adversaire ({restants}/{DAILY_MAX_ADVERSAIRE_REROLL})", "🔄",
                     discord.ButtonStyle.secondary),
                    ("difficulte", "Changer de difficulté", "🔀", discord.ButtonStyle.primary),
                    ("start", "Commencer le combat", "⚔️", discord.ButtonStyle.success),
                ])
                await channel.send("Que veux tu faire ?", view=view)
                await view.wait()
                if view.result is None:
                    await channel.send("⏳ /daily annulé (temps écoulé).")
                    return None, None
                if view.result == "start":
                    return pnj, classe
                if view.result == "difficulte":
                    break  # retourne au choix de difficulté
                # reroll
                if rerolls >= DAILY_MAX_ADVERSAIRE_REROLL:
                    await channel.send("❌ Tu as déjà changé d'adversaire 3 fois : plus de changement possible.")
                    continue
                rerolls += 1
                pnj = generate_pnj(player_stats, classe)

    async def _send_pnj_pillow(self, channel, pnj):
        import os
        import uuid
        os.makedirs("temp", exist_ok=True)
        path = os.path.join("temp", f"daily_enemy_{uuid.uuid4().hex}.png")
        generate_daily_enemy_image(
            pnj["name"], pnj["classe"], pnj["pv_max"], pnj["eo_max"],
            pnj["force"], pnj["vitesse"], pnj["arme"], pnj["rct"], pnj["territoire"],
            complete_coffres(pnj["classe"]), path)
        await channel.send(file=discord.File(path, filename="daily_enemy.png"))
        try:
            os.remove(path)
        except OSError:
            pass

    # =================================================================
    # §5-9 : COMBAT
    # =================================================================
    async def _run_combat(self, channel, user, character_id, classe, pnj):
        prof = db.get_or_create_profile(character_id)
        stats = self._player_stats(character_id)
        char = get_character(character_id)

        # État de combat.
        st = {
            "name_j": (char["character_name"] if char else None) or "Toi",
            "name_p": pnj["name"],
            "pv_j": prof["pv_actuel"], "pv_max_j": prof["pv_max"],
            "eo_j": prof["eo_actuel"], "eo_max_j": prof["eo_max"],
            "force_base_j": stats["force"],
            "pv_p": pnj["pv_max"], "pv_max_p": pnj["pv_max"],
            "eo_p": pnj["eo_max"], "eo_max_p": pnj["eo_max"],
            "force_base_p": pnj["force"],
            "bloc_j": 0, "bloc_p": 0,          # compteurs de blocage (chance dégressive)
            "potions_p": pnj["potions"], "potion_pct_p": pnj["potion_pct"],
        }
        gains = {"force": 0, "endurance": 0, "energie_occulte": 0, "sorts": 0}
        sort_xp = {}  # principal_id -> xp total à accorder

        # §5 : règlement.
        await channel.send(embed=discord.Embed(
            title="⚔️ RÈGLEMENT DU COMBAT",
            description=(
                "Ce combat est un **vrai combat InRP** : tes PV et ton Énergie occulte réels seront "
                "affectés, et le résultat sera enregistré dans ton historique de combats.\n\n"
                "Chaque tour, choisis une action :\n"
                "- **Attaquer** : dégâts = Force actuelle × 0,03 (déterministe)\n"
                "- **Bloquer** : chance dégressive de bloquer une attaque physique ; contre un sort, "
                "30% de chance d'absorber 50% des dégâts.\n"
                "- **Renforcement maudit** : puise dans ton énergie occulte pour booster ta Force "
                "(n'utilise PAS ton tour).\n"
                "- **Utiliser un sort** : consomme de l'énergie occulte pour des dégâts fixes.\n"
                "- **Utiliser une potion** (si tu en as) : restaure de l'énergie occulte.\n\n"
                f"Le combat se termine dès qu'un camp atteint **{DAILY_PV_FLOOR} PV ou moins**."),
            color=discord.Color.red()))

        # §6 : qui commence (n'influe que sur l'ordre d'affichage/priorité en cas d'égalité de total).
        joueur_priorite = random.choice([True, False])
        await channel.send(
            f"🎲 {'Tu commences' if joueur_priorite else 'Ton adversaire commence'} ce combat.")

        tour = 0
        issue = None  # "victoire" / "defaite"
        while True:
            tour += 1
            # --- Action du JOUEUR (peut inclure un renforcement qui ne consomme pas le tour). ---
            action_j = await self._player_turn(channel, user, character_id, st, gains, sort_xp)
            if action_j is None:
                await channel.send("⏳ Combat interrompu (temps écoulé). Aucune récompense enregistrée.")
                return
            # --- Action du PNJ. ---
            action_p = self._pnj_turn(classe, st)

            # --- §7 : résolution simultanée du tour (retourne texte de résultat + couleur). ---
            result_text, color_key = self._resolve_round(st, action_j, action_p, gains, joueur_priorite)

            # §3 : embed UNIQUE et structuré, identique pour toutes les actions.
            await channel.send(embed=self._round_embed(tour, st, result_text, color_key))

            # --- §8 : fin de combat. ---
            if st["pv_p"] <= DAILY_PV_FLOOR:
                issue = "victoire"
                break
            if st["pv_j"] <= DAILY_PV_FLOOR:
                issue = "defaite"
                break

        # §9 : application des récompenses + persistance.
        await self._finish_combat(channel, user, character_id, classe, st, gains, sort_xp, issue)

    # ---------- tour joueur ----------
    async def _player_turn(self, channel, user, character_id, st, gains, sort_xp):
        """Retourne un dict d'action {kind, attacking, damage, dtype, blocking} ou None (timeout).
        Le renforcement maudit boucle sans consommer le tour."""
        bonus_force = 0
        potions = db.get_owned_potions(character_id)
        while True:
            options = [
                ("attaquer", "Attaquer", "⚔️", discord.ButtonStyle.danger),
                ("bloquer", "Bloquer", "🛡️", discord.ButtonStyle.secondary),
                ("renfort", "Renforcement maudit", "🔮", discord.ButtonStyle.primary),
                ("sort", "Utiliser un sort", "✨", discord.ButtonStyle.primary),
            ]
            if potions:
                options.append(("potion", "Utiliser une potion", "🧪", discord.ButtonStyle.success))
            view = DailyChoiceView(user.id, options)
            await channel.send(
                embed=discord.Embed(
                    title="🌀 Ton tour",
                    description=f"PV : **{max(st['pv_j'], DAILY_PV_FLOOR):,}** · Énergie occulte : **{st['eo_j']:,}**"
                    + (f"\n🔮 Renforcement actif ce tour : +{bonus_force} Force" if bonus_force else ""),
                    color=PHOENIX_COLOR),
                view=view)
            await view.wait()
            if view.result is None:
                return None
            act = view.result
            f_act = current_force(st["force_base_j"], st["pv_j"], st["pv_max_j"], bonus_force)

            if act == "renfort":
                # Ne consomme PAS le tour : puise dans l'EO, ajoute à la Force du tour, puis redemande.
                if st["eo_j"] <= 0:
                    await channel.send("Tu n'as plus d'énergie occulte à investir.")
                    continue
                montant = await self._ask_int(
                    channel, user,
                    f"Ta réserve actuelle est de {st['eo_j']} points exacts. Combien veux tu en utiliser "
                    "pour renforcer ta Force ce tour-ci ?", 1, st["eo_j"])
                if montant is None:
                    continue
                st["eo_j"] -= montant
                bonus_force += montant
                gains["energie_occulte"] += 1  # §9 : renforcement utilisé = +points EO (compté à la fin)
                await channel.send(f"🔮 +{montant} Force ce tour (renforcement maudit).")
                continue

            if act == "potion":
                if not potions:
                    await channel.send("Tu n'as aucune potion.")
                    continue
                pot = potions[0]
                self._consume_one_potion(character_id, pot["item_id"])
                restore = round(st["eo_max_j"] * DAILY_PLAYER_POTION_EO_PCT / 100)
                st["eo_j"] = min(st["eo_max_j"], st["eo_j"] + restore)
                await channel.send(
                    f"🧪 Potion utilisée : +{restore} énergie occulte (EO : {st['eo_j']:,}/{st['eo_max_j']:,}).")
                potions = db.get_owned_potions(character_id)
                return {"kind": "potion", "attacking": False, "damage": 0, "dtype": None, "blocking": False}

            if act == "sort":
                spell = await self._pick_spell(channel, user, character_id, st)
                if spell is None:
                    continue  # pas de sort / annulé : redemande une action
                st["eo_j"] -= spell["cost"]
                gains["sorts"] += 1  # §9 : sort utilisé = +points Sorts
                sort_xp[spell["principal_id"]] = sort_xp.get(spell["principal_id"], 0) + spell["damage"]
                return {"kind": "sort", "attacking": True, "damage": spell["damage"], "dtype": "spell",
                        "blocking": False, "spell_name": spell["name"], "force_actuelle": f_act}

            if act == "attaquer":
                return {"kind": "attaquer", "attacking": True, "damage": physical_damage(f_act),
                        "dtype": "phys", "blocking": False, "force_actuelle": f_act}

            if act == "bloquer":
                return {"kind": "bloquer", "attacking": False, "damage": 0, "dtype": None,
                        "blocking": True, "force_actuelle": f_act}

    async def _pick_spell(self, channel, user, character_id, st):
        """§6 : liste TEXTE (pas embed) des sorts débloqués + suggestion du meilleur ratio. Retourne le
        sort choisi (EO vérifié) ou None."""
        spells = self._unlocked_spells(character_id, st["eo_max_j"])
        if not spells:
            await channel.send("Tu n'as aucun sort débloqué utilisable.")
            return None
        lignes = ["📜 Sorts disponibles :"]
        for i, s in enumerate(spells, 1):
            lignes.append(f"{i}. **{s['name']}** — coût {s['cost']:,} EO · {s['damage']:,} dégâts")
        best = best_spell_ratio(spells)
        lignes.append(f"\n💡 Meilleur ratio dégâts/coût : **{best['name']}**.")
        lignes.append("Réponds par le **numéro** du sort (ou « annuler »).")
        await channel.send("\n".join(lignes))
        while True:
            m = await self.wait_message(channel, user)
            if m is None:
                return None
            c = m.content.strip()
            if c.lower() in ("cancel", "annuler"):
                return None
            if c.isdigit() and 1 <= int(c) <= len(spells):
                chosen = spells[int(c) - 1]
                if chosen["cost"] > st["eo_j"]:
                    await channel.send(
                        f"Énergie occulte insuffisante ({st['eo_j']:,} < {chosen['cost']:,}). Choisis un autre sort.")
                    continue
                return chosen
            await channel.send(f"Réponds par un numéro entre 1 et {len(spells)}.")

    def _consume_one_potion(self, character_id, item_id):
        with db.get_connection() as conn:
            r = conn.execute(
                "SELECT id, quantity FROM character_inventory WHERE character_id = ? AND item_id = ?",
                (character_id, item_id)).fetchone()
            if not r:
                return
            if r["quantity"] - 1 > 0:
                conn.execute("UPDATE character_inventory SET quantity = quantity - 1 WHERE id = ?", (r["id"],))
            else:
                conn.execute("DELETE FROM character_inventory WHERE id = ?", (r["id"],))

    # ---------- tour PNJ ----------
    def _pnj_turn(self, classe, st):
        """Retourne l'action finale du PNJ (jamais de sort). Potion d'urgence prioritaire (ne consomme pas
        le tour) ; renforcement maudit possible (ne consomme pas le tour) suivi d'attaquer/bloquer."""
        # §6 : potion d'urgence (priorité absolue, ne consomme pas le tour).
        if st["eo_p"] < DAILY_EO_URGENCE_SEUIL and st["potions_p"] > 0:
            restore = round(st["eo_max_p"] * st["potion_pct_p"] / 100)
            st["eo_p"] = min(st["eo_max_p"], st["eo_p"] + restore)
            st["potions_p"] -= 1

        bonus_force = 0
        choix = weighted_pick(DAILY_PNJ_WEIGHTS[classe])
        if choix == "renforcement" and st["eo_p"] > 0:
            montant = random.randint(1, st["eo_p"])
            st["eo_p"] -= montant
            bonus_force = montant
            # Après renforcement (ne consomme pas le tour) : attaquer ou bloquer.
            choix = weighted_pick({"attaquer": DAILY_PNJ_WEIGHTS[classe]["attaquer"],
                                   "bloquer": DAILY_PNJ_WEIGHTS[classe]["bloquer"]})
        elif choix == "renforcement":
            choix = "attaquer"

        f_act = current_force(st["force_base_p"], st["pv_p"], st["pv_max_p"], bonus_force)
        if choix == "attaquer":
            return {"kind": "attaquer", "attacking": True, "damage": physical_damage(f_act),
                    "dtype": "phys", "blocking": False, "force_actuelle": f_act}
        return {"kind": "bloquer", "attacking": False, "damage": 0, "dtype": None, "blocking": True,
                "force_actuelle": f_act}

    # ---------- §7 : résolution d'un tour ----------
    def _resolve_round(self, st, aj, ap, gains, joueur_priorite):
        """Applique le tour et retourne (texte_de_résultat, clé_couleur) pour l'embed unique.
        clé_couleur : 'green' (le joueur a placé une action offensive), 'red' (le joueur a subi des
        dégâts), 'blue' (neutre : blocage, potion, esquive sans dégât)."""
        nj, npnj = st["name_j"], st["name_p"]
        pvj0, pvp0 = st["pv_j"], st["pv_p"]  # PV AVANT dégâts (pour l'affichage du clash)
        f_j = aj.get("force_actuelle", current_force(st["force_base_j"], pvj0, st["pv_max_j"]))
        f_p = ap.get("force_actuelle", current_force(st["force_base_p"], pvp0, st["pv_max_p"]))
        player_dealt = 0
        player_took = 0

        # §2 / §7.2 : les DEUX attaquent -> clash sur (force_actuelle + PV), seul le plus haut inflige.
        if aj["attacking"] and ap["attacking"]:
            total_j, total_p = f_j + pvj0, f_p + pvp0
            joueur_gagne = total_j > total_p or (total_j == total_p and joueur_priorite)
            if joueur_gagne:
                st["pv_p"] -= aj["damage"]
                player_dealt = aj["damage"]
                gains["force"] += 1
                gagnant, perdant, deg = nj, npnj, aj["damage"]
            else:
                st["pv_j"] -= ap["damage"]
                player_took = ap["damage"]
                gagnant, perdant, deg = npnj, nj, ap["damage"]
            text = (
                "⚔️ **Les deux camps attaquent !**\n\n"
                f"**{nj}** : {f_j:,} Force + {pvj0:,} PV = **{total_j:,} points de puissance**\n"
                f"**{npnj}** : {f_p:,} Force + {pvp0:,} PV = **{total_p:,} points de puissance**\n\n"
                f"🏆 **{gagnant}** remporte le clash et inflige **{deg:,}** dégâts à {perdant} !\n"
                f"{perdant} ne riposte pas ce tour-ci.")
            return text, ("green" if joueur_gagne else "red")

        # Sinon : au plus un camp attaque -> résolution indépendante avec blocage éventuel.
        parts = []
        if aj["kind"] == "potion":
            parts.append(f"🧪 **{nj}** utilise une potion et récupère de l'énergie occulte.")

        if aj["attacking"]:  # le joueur attaque le PNJ ; le PNJ bloque-t-il ?
            dealt, _ = self._apply_attack(st, aj, defender="p", defender_blocking=ap["blocking"])
            if dealt > 0:
                player_dealt += dealt
                gains["force"] += 1
                if aj["dtype"] == "spell":
                    sn = aj.get("spell_name") or "un sort"
                    parts.append(f"✨ **{nj}** lance **{sn}** et inflige **{dealt:,}** dégâts à {npnj} !")
                else:
                    parts.append(f"🗡️ **{nj}** attaque et inflige **{dealt:,}** dégâts à {npnj} !")
            else:
                parts.append(f"🛡️ {npnj} bloque l'attaque de **{nj}** — aucun dégât.")

        if ap["attacking"]:  # le PNJ attaque le joueur ; le joueur bloque-t-il ?
            dealt, block_ok = self._apply_attack(st, ap, defender="j", defender_blocking=aj["blocking"])
            if block_ok:
                gains["endurance"] += 1
                parts.append(f"🛡️ **Blocage réussi !** {nj} n'a subi aucun dégât de {npnj}.")
            elif dealt > 0:
                player_took += dealt
                parts.append(f"💥 **{npnj}** attaque et inflige **{dealt:,}** dégâts à {nj} !")

        if not aj["attacking"] and not ap["attacking"] and aj["kind"] != "potion":
            parts.append(f"🌀 **{nj}** se met en garde tandis que **{npnj}** temporise.")

        text = "\n".join(parts) if parts else f"{nj} et {npnj} s'observent."
        if player_dealt > 0:
            color = "green"
        elif player_took > 0:
            color = "red"
        else:
            color = "blue"
        return text, color

    def _apply_attack(self, st, attack, defender, defender_blocking):
        """Applique `attack` sur le défenseur 'p' (PNJ) ou 'j' (joueur). Retourne (dégâts_infligés,
        blocage_réussi). blocage_réussi = le défenseur bloquait ET l'attaque a été annulée/réduite."""
        dmg = attack["damage"]
        block_ok = False
        if defender_blocking:
            if attack["dtype"] == "spell":
                # §7.5 : sort contre blocage -> 30% de chance d'absorber 50% des dégâts.
                if random.randint(1, 100) <= DAILY_SPELL_BLOCK_CHANCE:
                    dmg = round(dmg * (100 - DAILY_SPELL_BLOCK_REDUCTION) / 100)
                    block_ok = True
            else:
                # §7.4 : attaque physique contre blocage -> chance dégressive de tout bloquer.
                key = "bloc_p" if defender == "p" else "bloc_j"
                st[key] += 1
                if random.randint(1, 100) <= block_chance(st[key]):
                    dmg = 0
                    block_ok = True
        if defender == "p":
            st["pv_p"] -= dmg
        else:
            st["pv_j"] -= dmg
        return dmg, block_ok

    def _round_embed(self, tour, st, result_text, color_key):
        """§3 : embed UNIQUE et de structure FIXE pour tous les tours (attaque / blocage / renforcement /
        sort / potion / clash). Barres textuelles à blocs (aucune image). Couleur selon color_key."""
        colors = {"green": discord.Color.green(), "red": discord.Color.red(),
                  "blue": discord.Color.blue()}
        pvj = max(st["pv_j"], DAILY_PV_FLOOR)   # §8 : jamais moins de 100 côté joueur
        pvp = max(st["pv_p"], 0)                # le PNJ peut réellement tomber à 0
        eoj = max(st["eo_j"], 0)
        eop = max(st["eo_p"], 0)
        desc = (
            f"**{st['name_j']}**\n"
            f"❤️ PV : {_daily_bar(pvj, st['pv_max_j'])} {pvj:,} / {st['pv_max_j']:,}\n"
            f"🔵 EO : {_daily_bar(eoj, st['eo_max_j'])} {eoj:,} / {st['eo_max_j']:,}\n\n"
            f"**{st['name_p']}**\n"
            f"❤️ PV : {_daily_bar(pvp, st['pv_max_p'])} {pvp:,} / {st['pv_max_p']:,}\n"
            f"🔵 EO : {_daily_bar(eop, st['eo_max_p'])} {eop:,} / {st['eo_max_p']:,}\n\n"
            "━━━━━━━━━━━━━━━\n\n"
            f"📢 **Résultat de ce tour :**\n{result_text}")
        return discord.Embed(title=f"⚔️ Tour {tour}", description=desc,
                             color=colors.get(color_key, discord.Color.blue()))

    # ---------- §9 : fin + récompenses ----------
    async def _finish_combat(self, channel, user, character_id, classe, st, gains, sort_xp, issue):
        pts = DAILY_REWARD_POINTS[classe]
        # Multiplie chaque compteur de réussite par le barème de points de la classe.
        applied = {}
        for key, count in gains.items():
            if count > 0:
                db.add_stat_base_pts(character_id, key, count * pts)
                applied[key] = count * pts
        # XP de Maîtrise Sort = somme des dégâts infligés par chaque sort (par principal concerné).
        for principal_id, xp in sort_xp.items():
            if xp > 0:
                await db.grant_sort_xp(principal_id, xp)

        # PV/EO réels appliqués. §8 : le PV joueur affiché/enregistré est clampé à 100 minimum.
        pv_final_j = max(st["pv_j"], DAILY_PV_FLOOR)
        db.update_profile(character_id, pv_actuel=pv_final_j, eo_actuel=max(0, st["eo_j"]))

        # Compteur victoires/défaites.
        prof = db.get_or_create_profile(character_id)
        if issue == "victoire":
            db.update_profile(character_id, victoires=(prof["victoires"] or 0) + 1)
        else:
            db.update_profile(character_id, defaites=(prof["defaites"] or 0) + 1)

        # Cooldown.
        db.set_daily_cooldown(character_id, datetime.utcnow().isoformat())

        # Récapitulatif.
        stat_labels = {"force": "Force", "endurance": "Endurance", "energie_occulte": "Énergie occulte",
                       "sorts": "Sorts"}
        recap = "\n".join(f"• +{v} points {stat_labels[k]}" for k, v in applied.items()) or "Aucun point gagné."
        if issue == "victoire":
            coffres = ", ".join(DAILY_COFFRE_LABELS[k] for k in DAILY_COFFRE_KEYS
                                if DAILY_COFFRE_ACCESS.get(classe, {}).get(k))
            embed = discord.Embed(
                title="🏆 VICTOIRE",
                description=(f"Tu as vaincu ton adversaire de Classe {classe} !\n\n"
                             f"**Points gagnés :**\n{recap}\n\n"
                             f"🎁 Coffre à débloquer (accès Classe {classe}) : {coffres} "
                             "*(distribution différée — système de coffres à venir)*\n\n"
                             "⚠️ Combat InRP : tes PV et ton énergie occulte réels ont été mis à jour."),
                color=discord.Color.green())
        else:
            embed = discord.Embed(
                title="💀 DÉFAITE",
                description=(f"Ton adversaire de Classe {classe} a eu le dessus.\n\n"
                             f"**Points gagnés malgré tout :**\n{recap}\n\n"
                             f"Tes PV ont été ramenés à {DAILY_PV_FLOOR} (jamais moins).\n\n"
                             "⚠️ Combat InRP : tes PV et ton énergie occulte réels ont été mis à jour."),
                color=discord.Color.dark_red())
        await channel.send(embed=embed)


async def setup(bot):
    await bot.add_cog(Daily(bot))
