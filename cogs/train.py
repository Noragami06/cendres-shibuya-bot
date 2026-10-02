# /train — mini-jeu Mastermind pour l'entraînement des stats.
#
# Boutons Discord (jamais de réactions emoji) : plus fiable, pas de souci de permissions/rate-limit.
# La partie vit dans une View auto-portée (TrainGameView) qui détient tout l'état (code secret, lignes
# jouées, tentative en cours) et ré-édite le MÊME message pillow à chaque clic.

import os
import random
import uuid
from datetime import datetime, timedelta

import discord
from discord import app_commands
from discord.ext import commands

from cogs.utils import database as db
from cogs.utils.image_gen import generate_entrainement_image
from cogs.banque import get_characters, get_character, PHOENIX_COLOR
from cogs.daily import (
    DAILY_COFFRE_ACCESS, DAILY_COFFRE_LABELS, DAILY_COFFRE_KEYS,
    VIP_ROLE_ID, weighted_pick, _time_left_str,
)

# =====================================================================
# 0. CONSTANTES
# =====================================================================
TRAIN_STATS = ["force", "vitesse", "endurance", "arme_maudite", "sort"]  # jamais EO/RCT/Territoire
TRAIN_STAT_LABELS = {"force": "Force", "vitesse": "Vitesse", "endurance": "Endurance",
                     "arme_maudite": "Arme Maudite", "sort": "Sort"}
# Colonne RÉELLE de character_stats pour chaque stat entraînable (clés /train -> clés DB).
TRAIN_STAT_DB_COL = {"force": "force", "vitesse": "vitesse", "endurance": "endurance",
                     "arme_maudite": "armes_maudites", "sort": "sorts"}

TRAIN_ATTEMPTS_MAX = {"4": 10, "3": 8, "2": 6, "1": 5, "S": 4}

TRAIN_REWARDS = {
    "4": {"points": 40, "xp": 100},
    "3": {"points": 90, "xp": 250},
    "2": {"points": 160, "xp": 500},
    "1": {"points": 260, "xp": 900},
    "S": {"points": 400, "xp": 1500},
}
# Échec (tentatives épuisées sans trouver le code) : 0 point, 0 XP.

TRAIN_PEG_COLORS = [
    (230, 60, 60), (70, 130, 230), (70, 200, 120), (235, 200, 60),
    (170, 80, 230), (240, 140, 40), (235, 235, 240),
]
TRAIN_COLOR_NAMES = ["Rouge", "Bleu", "Vert", "Jaune", "Violet", "Orange", "Blanc"]
TRAIN_COLOR_EMOJIS = ["🔴", "🔵", "🟢", "🟡", "🟣", "🟠", "⚪"]

CLASSES_ORDRE = ["4", "3", "2", "1", "S"]

# Bonus de coffre pour une victoire RAPIDE (1re ou 2e tentative) : Épique nettement plus courant.
TRAIN_FAST_WIN_CHEST_WEIGHTS = {"epic": 75, "legendaire": 25}
TRAIN_FAST_WIN_MAX_ROWS = 2  # victoire en <= 2 tentatives

# Cooldown différencié selon le rôle (le simple fait d'AVOIR le rôle suffit, actif ou non).
TRAIN_COOLDOWN_HOURS_NORMAL = 3
TRAIN_COOLDOWN_HOURS_BOOSTER_VIP = 1
BOOSTER_ROLE_ID = 1521563661204979802
# VIP_ROLE_ID : réutilisée depuis cogs.daily (accès VIP 15 jours).

TRAIN_IMG_DIR = os.path.join(os.path.dirname(__file__), "..", "temp", "train_images")


def _tmp_train() -> str:
    os.makedirs(TRAIN_IMG_DIR, exist_ok=True)
    return os.path.join(TRAIN_IMG_DIR, f"train_{uuid.uuid4().hex}.png")


def compute_feedback(guess, secret):
    """Mastermind standard. vert = bonne couleur + bon emplacement, orange = bonne couleur + mauvais
    emplacement, rouge = absente. Chaque pion du secret n'est compté qu'UNE SEULE fois."""
    fb = [None] * 4
    secret_used = [False] * 4
    # 1) Verts (position exacte) : consomme le pion du secret correspondant.
    for i in range(4):
        if guess[i] == secret[i]:
            fb[i] = "vert"
            secret_used[i] = True
    # 2) Oranges (bonne couleur ailleurs) : consomme un pion non encore utilisé.
    for i in range(4):
        if fb[i] is None:
            for j in range(4):
                if not secret_used[j] and guess[i] == secret[j]:
                    fb[i] = "orange"
                    secret_used[j] = True
                    break
    # 3) Le reste est rouge (couleur absente des pions restants).
    for i in range(4):
        if fb[i] is None:
            fb[i] = "rouge"
    return fb


# =====================================================================
# VUES EN SESSION (sélection)
# =====================================================================
class _ChoiceView(discord.ui.View):
    """Boutons en session : le 1er clic du propriétaire fixe self.result et arrête la vue.
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
                await interaction.response.send_message("Ce choix ne t'appartient pas.", ephemeral=True)
                return
            self.result = key
            try:
                await interaction.response.edit_message(view=None)
            except discord.HTTPException:
                pass
            self.stop()
        return cb


# =====================================================================
# VUE DE JEU (auto-portée)
# =====================================================================
class TrainGameView(discord.ui.View):
    """Détient tout l'état de la partie et gère les clics de couleur jusqu'à victoire / échec."""

    def __init__(self, cog, owner_id, character_id, stat_key, classe, secret):
        super().__init__(timeout=1800)
        self.cog = cog
        self.owner_id = owner_id
        self.character_id = character_id
        self.stat_key = stat_key
        self.classe = classe
        self.secret = secret
        self.rows = []            # [(guess, feedback), ...]
        self.tentative = []       # couleurs posées pour la tentative EN COURS (0..4)
        self.finished = False
        for idx in range(len(TRAIN_PEG_COLORS)):
            btn = discord.ui.Button(
                emoji=TRAIN_COLOR_EMOJIS[idx], style=discord.ButtonStyle.secondary,
                row=idx // 5)
            btn.callback = self._make_color_cb(idx)
            self.add_item(btn)

    # ---------- rendu ----------
    def _display_rows(self):
        """Lignes à afficher : tentatives complètes + la tentative en cours (si des pions sont posés)."""
        display = list(self.rows)
        if self.tentative and not self.finished:
            display.append((list(self.tentative), []))  # ligne « en cours », sans feedback
        return display

    def _attempts_max(self):
        return TRAIN_ATTEMPTS_MAX[self.classe]

    async def _render(self, interaction, extra_embed=None):
        stat_name = TRAIN_STAT_LABELS[self.stat_key]
        path = _tmp_train()
        generate_entrainement_image(stat_name, self._display_rows(), self._attempts_max(), path)
        file = discord.File(path, filename="train.png")
        try:
            await interaction.response.edit_message(attachments=[file], view=self)
        except discord.HTTPException:
            pass
        try:
            os.remove(path)
        except OSError:
            pass
        if extra_embed is not None:
            try:
                await interaction.followup.send(embed=extra_embed)
            except discord.HTTPException:
                pass

    # ---------- clic de couleur ----------
    def _make_color_cb(self, idx):
        async def cb(interaction: discord.Interaction):
            if interaction.user.id != self.owner_id:
                await interaction.response.send_message(
                    "Cette partie ne t'appartient pas.", ephemeral=True)
                return
            if self.finished:
                try:
                    await interaction.response.defer()
                except discord.HTTPException:
                    pass
                return

            # 1. Ajoute la couleur si la tentative n'est pas déjà pleine.
            if len(self.tentative) < 4:
                self.tentative.append(idx)

            # 3. Tentative complète -> feedback + évaluation.
            if len(self.tentative) == 4:
                fb = compute_feedback(self.tentative, self.secret)
                self.rows.append((list(self.tentative), fb))
                self.tentative = []
                if fb == ["vert", "vert", "vert", "vert"]:
                    await self._win(interaction)
                    return
                if len(self.rows) >= self._attempts_max():
                    await self._fail(interaction)
                    return

            # 2. Ré-édite le même message pillow avec l'état courant.
            await self._render(interaction)
        return cb

    # ---------- §5 victoire ----------
    async def _win(self, interaction):
        self.finished = True
        for item in self.children:
            item.disabled = True
        reward = TRAIN_REWARDS[self.classe]
        db.add_stat_base_pts(self.character_id, TRAIN_STAT_DB_COL[self.stat_key], reward["points"])
        await db.grant_character_xp(self.character_id, reward["xp"])
        stat_name = TRAIN_STAT_LABELS[self.stat_key]
        desc = (f"Code trouvé en **{len(self.rows)}/{self._attempts_max()}** tentatives !\n"
                f"**+{reward['points']}** points de {stat_name}\n"
                f"**+{reward['xp']}** XP")
        # §1 : bonus de coffre pour une victoire rapide (1re ou 2e tentative).
        if len(self.rows) <= TRAIN_FAST_WIN_MAX_ROWS:
            rarete = weighted_pick(TRAIN_FAST_WIN_CHEST_WEIGHTS)
            item = db.get_coffre_item_by_rarete(rarete)
            if item is not None:
                db.inv_add_item(self.character_id, item["id"], 1)
                desc += (f"\n\n🎁 **Bonus de rapidité !** Tu as obtenu un "
                         f"{DAILY_COFFRE_LABELS.get(rarete, rarete)} "
                         f"(trouvé en seulement {len(self.rows)} tentative(s)).")
        db.update_profile(self.character_id, last_train_at=datetime.utcnow().isoformat())
        embed = discord.Embed(title="🎉 Code trouvé !", description=desc, color=discord.Color.green())
        await self._render(interaction, extra_embed=embed)
        self.stop()

    # ---------- §6 échec ----------
    async def _fail(self, interaction):
        self.finished = True
        for item in self.children:
            item.disabled = True
        code = " ".join(f"{TRAIN_COLOR_EMOJIS[c]} {TRAIN_COLOR_NAMES[c]}" for c in self.secret)
        n = self._attempts_max()
        db.update_profile(self.character_id, last_train_at=datetime.utcnow().isoformat())
        embed = discord.Embed(
            title="❌ Tentatives épuisées",
            description=(f"Tentatives épuisées (**{n}/{n}**).\n"
                         f"Le code secret était : {code}.\n"
                         "Aucune récompense cette fois."),
            color=discord.Color.red())
        await self._render(interaction, extra_embed=embed)
        self.stop()


# =====================================================================
# COG
# =====================================================================
class Train(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def _select_character(self, channel, user):
        """Auto si un seul personnage, menu si plusieurs, None si aucun."""
        chars = get_characters(user.id, channel.guild.id)
        if not chars:
            await channel.send("❌ Tu n'as aucun personnage validé.")
            return None
        if len(chars) == 1:
            return chars[0]["id"]
        options = [(str(c["id"]), f"Slot {c['slot_number']} — {c['character_name']}", None,
                    discord.ButtonStyle.secondary) for c in chars[:5]]
        view = _ChoiceView(user.id, options)
        await channel.send("Choisis le personnage :", view=view)
        await view.wait()
        return int(view.result) if view.result else None

    @app_commands.command(name="train",
                          description="Mini-jeu Mastermind pour entraîner une de tes stats")
    async def train(self, interaction: discord.Interaction):
        user = interaction.user
        await interaction.response.send_message("🧊 Lancement de l'entraînement…", ephemeral=True)
        channel = interaction.channel

        # 0. Personnage.
        character_id = await self._select_character(channel, user)
        if character_id is None:
            return

        # §2 : cooldown différencié — Booster/VIP (le simple fait d'avoir le rôle) = 1h, sinon 3h.
        roles = {r.id for r in getattr(user, "roles", [])}
        seuil_h = (TRAIN_COOLDOWN_HOURS_BOOSTER_VIP
                   if (BOOSTER_ROLE_ID in roles or VIP_ROLE_ID in roles)
                   else TRAIN_COOLDOWN_HOURS_NORMAL)
        prof = db.get_or_create_profile(character_id)
        last = prof["last_train_at"] if "last_train_at" in prof.keys() else None
        if last:
            try:
                last_dt = datetime.fromisoformat(last)
            except ValueError:
                last_dt = None
            if last_dt is not None:
                ecoule = datetime.utcnow() - last_dt
                if ecoule < timedelta(hours=seuil_h):
                    reste = timedelta(hours=seuil_h) - ecoule
                    await channel.send(
                        f"⏳ Tu pourras refaire /train avec ce personnage dans {_time_left_str(reste)}.")
                    return

        # 1. Choix de la stat (Force/Vitesse/Endurance/Arme Maudite/Sort).
        stat_options = [(k, TRAIN_STAT_LABELS[k], None, discord.ButtonStyle.primary) for k in TRAIN_STATS]
        view = _ChoiceView(user.id, stat_options)
        await channel.send(
            embed=discord.Embed(title="🧊 Entraînement — Choisis la stat",
                                description="\n".join(f"• **{TRAIN_STAT_LABELS[k]}**" for k in TRAIN_STATS),
                                color=PHOENIX_COLOR),
            view=view)
        await view.wait()
        if view.result is None:
            await channel.send("⏳ Entraînement annulé.")
            return
        stat_key = view.result

        # 2. Choix de la difficulté (classe) + rappel des coffres accessibles (réutilise DAILY_COFFRE_ACCESS).
        diff_options = [(c, f"Classe {c}", None, discord.ButtonStyle.secondary) for c in CLASSES_ORDRE]
        lignes = []
        for c in CLASSES_ORDRE:
            coffres = ", ".join(DAILY_COFFRE_LABELS[k] for k in DAILY_COFFRE_KEYS
                                if DAILY_COFFRE_ACCESS.get(c, {}).get(k))
            lignes.append(f"• **Classe {c}** — {TRAIN_ATTEMPTS_MAX[c]} tentatives · coffres : {coffres}")
        view = _ChoiceView(user.id, diff_options)
        await channel.send(
            embed=discord.Embed(title="🧊 Entraînement — Choisis la difficulté",
                                description="\n".join(lignes), color=PHOENIX_COLOR),
            view=view)
        await view.wait()
        if view.result is None:
            await channel.send("⏳ Entraînement annulé.")
            return
        classe = view.result

        # 3. Code secret + affichage initial (pillow vide) + boutons de couleur.
        secret = [random.randint(0, len(TRAIN_PEG_COLORS) - 1) for _ in range(4)]
        game = TrainGameView(self, user.id, character_id, stat_key, classe, secret)
        path = _tmp_train()
        generate_entrainement_image(TRAIN_STAT_LABELS[stat_key], [], TRAIN_ATTEMPTS_MAX[classe], path)
        rappel = ("🧩 Compose un code de **4 couleurs** avec les boutons ci-dessous. Clique 4 couleurs "
                  "pour valider une tentative — le jeu te dira, pour chaque pion : 🟢 bien placé, "
                  "🟠 mal placé, 🔴 absent.")
        await channel.send(content=rappel, file=discord.File(path, filename="train.png"), view=game)
        try:
            os.remove(path)
        except OSError:
            pass


async def setup(bot):
    await bot.add_cog(Train(bot))
