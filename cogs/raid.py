# RAIDS — Phase 1 : cycle d'apparition, génération, annonce, système d'amende + saisie/blacklist.
#
# Règles de robustesse du projet appliquées : rôle revérifié au clic, tâches planifiées idempotentes et
# basées sur de vrais timestamps (rattrapage hors-ligne), boutons persistants dispatchés par le listener
# central on_interaction (survivent au redémarrage sans add_view, custom_id porteur de l'id d'amende).
#
# TODO Phase 2+ : bouton « Participer », détection de non-réponse, effectif/puissance réel, MVP (+1,5%),
# distribution effective des récompenses/pierres. Les points concernés sont balisés « TODO Phase 2 ».

import asyncio
import json
import random
from datetime import datetime, timedelta

import discord
from discord import app_commands
from discord.ext import commands, tasks

from cogs.utils import database as db
from cogs.banque import get_character, get_characters
# Réutilisation directe des primitives de combat de /daily (mêmes dégâts/blocage/Black Flash/clash).
from cogs.daily import (
    generate_pnj, current_force, physical_damage, block_chance,
    get_player_rp_classe, weighted_pick as daily_weighted_pick,
    _add_role_real_or_virtual, _remove_role_real_or_virtual,
    DAILY_PV_FLOOR, DAILY_CRIT_BASE, DAILY_DAMAGE_RATIO, DAILY_PNJ_WEIGHTS,
)

# =====================================================================
# 0. CONSTANTES
# =====================================================================
RAID_CYCLE_HOURS = 3
RAID_DELAY_AFTER_OVERFLOW_HOURS = 6  # si la phase de participation déborde au delà de 3h (Phase 2)

RAID_CHANNELS = [
    # Tokyo (20 salons)
    1523985481707028510, 1523985528884695070, 1523985579107422238, 1523985753997054053, 1523985810464968825,
    1523985921542852730, 1523985970234396672, 1523986053415960648, 1523986108373794888, 1523986181140910170,
    1523986366319562762, 1523986456371007608, 1523986544766095490, 1523991548411379724, 1523991550256746629,
    1523991553163661332, 1523991554577141862, 1523991556024041592, 1523991557261234277, 1523991559333478440,
    # Kyoto (20 salons)
    1523992741673963634, 1523992742877728849, 1523992744387809451, 1523992745826324601, 1523992747290263592,
    1523992749798330378, 1523992751153090790, 1523992752537206784, 1523992754001154129, 1523992755255382038,
    1523992758249984100, 1523992759764123698, 1523992761374605363, 1523992762414923892, 1523992763836792903,
    1523992767825711154, 1523992769469616290, 1523992770698678454, 1523992772649025708, 1523992774515359827,
    # Shinjuku (20 salons)
    1523993545592279212, 1523993547487842334, 1523993549413154937, 1523993550671577161, 1523993552722329663,
    1523993555805278301, 1523993557768339477, 1523993559408316506, 1523993560997822547, 1523993563573256342,
    1523993566924505208, 1523993568165892148, 1523993569470447698, 1523993571168878693, 1523993572959850507,
    1523993575690338355, 1523993576940376094, 1523993578139943134, 1523993579637309540, 1523993581658832926,
    # Shibuya (20 salons)
    1523994668067586079, 1523994669334266006, 1523994670529642637, 1523994671720693860, 1523994673067196466,
    1523994675730714644, 1523994676980617287, 1523994678339567768, 1523994680000253994, 1523994681770377246,
    1523994685360836649, 1523994687524962335, 1523994688670011412, 1523994690167508992, 1523994691899494430,
    1523994694315544596, 1523994695917899857, 1523994696920203266, 1523994698870423632, 1523994699969466470,
]

RAID_ANNOUNCE_CHANNEL_ID = 1551383186720694272
RAID_MANAGER_ROLE_ID = 1522182819462381729
RAID_GIF_URL = "https://c.tenor.com/HvgKNijieJ4AAAAd/tenor.gif"

FICHE_STAFF_ROLE_ID = 1521229332075512039  # rôle staff global (défini localement, comme les autres cogs)

# 4=assez commun, 3=quasi aussi accessible, 2=assez rare, 1=très rare, S=occasionnel
RAID_CLASSE_WEIGHTS = {"4": 40, "3": 35, "2": 15, "1": 8, "S": 2}

RAID_STONE_TABLE = {
    "4": {"4": 85, "3": 15},
    "3": {"4": 50, "3": 35, "2": 15},
    "2": {"4": 10, "3": 40, "2": 50},
    "1": {"3": 15, "2": 35, "1": 50},
    "S": {"2": 10, "1": 30, "S": 60},
}
RAID_STONE_QUANTITY = {"4": (20, 40), "3": (30, 55), "2": (45, 75), "1": (65, 100), "S": (90, 150)}
RAID_STONE_PRICE = {"4": (2000, 5000), "3": (8000, 15000), "2": (30000, 60000),
                    "1": (120000, 250000), "S": (500000, 1000000)}

RAID_MONSTER_COUNT = {"4": (15, 25), "3": (20, 35), "2": (30, 50), "1": (45, 70), "S": (65, 100)}

# Phase 2 : effectif suggéré (borne basse, borne haute) par classe. La participation se ferme d'office
# quand on atteint la BORNE HAUTE. TODO Phase 2/3 : remplacer par un calcul basé sur la puissance réelle
# (somme des burst_power des participants) face à la classe du raid — fonction commune avec l'effectif
# affiché à l'annonce et le calcul de participation/combat.
RAID_EFFECTIF_SUGGERE = {"4": (1, 3), "3": (2, 4), "2": (3, 6), "1": (5, 8), "S": (7, 12)}

# Fenêtres de la phase de participation (depuis le déclenchement du raid).
RAID_PARTICIPATION_INITIAL_HOURS = 2   # délai initial : 0 participant à l'échéance -> fermeture + amende
RAID_PARTICIPATION_EXTENDED_HOURS = 3  # +1h si >= 1 participant mais effectif incomplet -> fermeture au combat

RAID_REWARDS = {
    "4": {"xp": (500, 1000), "stats_libre": (100, 200), "coffres": ["commun", "rare"]},
    "3": {"xp": (1000, 2500), "stats_libre": (200, 450), "coffres": ["commun", "rare", "epic"]},
    "2": {"xp": (2500, 6000), "stats_libre": (450, 900), "coffres": ["rare", "epic", "legendaire"]},
    "1": {"xp": (6000, 15000), "stats_libre": (900, 1800), "coffres": ["epic", "legendaire", "mythique"]},
    "S": {"xp": (15000, 35000), "stats_libre": (1800, 3500), "coffres": ["legendaire", "mythique"]},
}
RAID_MVP_BONUS_PCT = 1.5  # % de bonus multiplicatif sur les gains du MVP (Phase 4-5)

RAID_AMENDE = {"4": (20000, 50000), "3": (60000, 150000), "2": (200000, 500000),
               "1": (600000, 1500000), "S": (2000000, 5000000)}
RAID_AMENDE_PENALITE_JOURNALIERE = {"4": 3000, "3": 10000, "2": 30000, "1": 100000, "S": 300000}
RAID_AMENDE_DELAI_PENALITE_JOURS = 3   # à partir de quand la pénalité journalière commence
RAID_AMENDE_DELAI_SAISIE_JOURS = 10    # à partir de quand le salon est saisi si toujours impayé

RAID_LABELS_COFFRE = {"commun": "Commun", "rare": "Rare", "epic": "Épique",
                      "legendaire": "Légendaire", "mythique": "Mythique"}
RAID_COLOR = discord.Color.red()

OWNER_ID = 396615332346855428  # propriétaire du bot (décisions de sauvetage / permadéath)

# Couleurs distinctes des rôles « RAID N » du pool (cycle si plus de slots que de couleurs).
RAID_ROLE_COLORS = [0xE74C3C, 0x3498DB, 0x9B59B6, 0x2ECC71, 0xE67E22, 0xF1C40F, 0x1ABC9C, 0xE84393]
RAID_BOSS_ROLE_COLOR = 0x992D22


# =====================================================================
# HELPERS PURS
# =====================================================================
def _now():
    return datetime.utcnow().isoformat()


def _parse(iso):
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso)
    except ValueError:
        return None


def _fmt(n) -> str:
    return f"{int(n):,}".replace(",", " ")


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


def distribute_stones(classe: str) -> dict:
    """Répartit une quantité totale tirée dans RAID_STONE_QUANTITY[classe] selon les % de
    RAID_STONE_TABLE[classe]. Arrondis, puis le reste est ajusté sur la classe la PLUS représentée
    pour que la somme soit EXACTEMENT égale au total tiré."""
    dist = RAID_STONE_TABLE[classe]
    total = random.randint(*RAID_STONE_QUANTITY[classe])
    result = {k: round(total * pct / 100) for k, pct in dist.items()}
    diff = total - sum(result.values())
    if result:
        cle_majoritaire = max(dist, key=lambda k: dist[k])
        result[cle_majoritaire] = max(0, result[cle_majoritaire] + diff)
    return result


def _stones_text(stones: dict) -> str:
    return ", ".join(f"{qty} pierre(s) Classe {c}" for c, qty in stones.items() if qty > 0) or "—"


class _OwnerChoiceView(discord.ui.View):
    """Vue en session (le cliqueur est présent) : premier clic du propriétaire -> result + stop.
    options : liste de (key, label, emoji, style)."""

    def __init__(self, owner_id, options, timeout=120):
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


class _FinishHimView(discord.ui.View):
    """Bouton « 💥 FINISH HIM » cliquable par n'importe quel participant vivant ; premier clic valide
    déclenche la séquence pour tout le monde puis se désactive."""

    def __init__(self, allowed_user_ids, timeout=180):
        super().__init__(timeout=timeout)
        self.allowed = set(allowed_user_ids)
        self.triggered = False
        btn = discord.ui.Button(label="FINISH HIM", emoji="💥", style=discord.ButtonStyle.danger)
        btn.callback = self._cb
        self.add_item(btn)

    async def _cb(self, interaction: discord.Interaction):
        if interaction.user.id not in self.allowed:
            await interaction.response.send_message(
                "Seul un participant vivant peut porter le coup de grâce.", ephemeral=True)
            return
        if self.triggered:
            try:
                await interaction.response.defer()
            except discord.HTTPException:
                pass
            return
        self.triggered = True
        try:
            await interaction.response.edit_message(view=None)
        except discord.HTTPException:
            pass
        self.stop()


def _new_monster_runtime(stats: dict, classe: str) -> dict:
    """Génère un monstre (mêmes règles que /daily, SANS filtre de classe RP) et l'augmente de champs
    d'état runtime (pv/eo/bloc courants) sérialisables JSON pour la file et les transferts."""
    mon = generate_pnj(stats, classe)  # {name, pv_max, eo_max, force, ..., potions, potion_pct}
    mon["pv"] = mon["pv_max"]
    mon["eo"] = mon["eo_max"]
    mon["bloc"] = 0
    return mon


class _Combatant:
    """État runtime d'un participant pendant le combat de raid (en mémoire ; les champs durables —
    file/monstre courant/is_alive/dégâts/thread — sont aussi persistés en base pour les transferts)."""

    def __init__(self, prow, member, char, stats, profile, burst):
        self.participant_id = prow["id"]
        self.character_id = prow["character_id"]
        self.user_id = prow["user_id"]
        self.member = member
        self.char = char
        self.name = (char["character_name"] if char else None) or f"#{self.character_id}"
        self.role_slot = prow["role_slot"]
        self.is_raid_chief = bool(prow["is_raid_chief"])
        self.burst = burst
        # PV/EO réels de départ (comme /daily : combat InRP).
        self.pv = profile["pv_actuel"]
        self.pv_max = profile["pv_max"]
        self.eo = profile["eo_actuel"]
        self.eo_max = profile["eo_max"]
        self.force_base = stats["force"]
        self.crit_chance = DAILY_CRIT_BASE
        self.sort_bonus = 0
        self.bloc = 0
        self.total_damage = 0
        self.alive = True
        self.finished = False   # a terminé sa propre vague
        self.thread = None      # fil de combat courant
        self.queue = []         # monstres restants (après le courant)
        self.current = None     # monstre en cours (dict runtime)


class _RaidSession:
    """Session de combat de raid en mémoire (orchestration). Les champs durables sont aussi persistés
    en base ; l'objet est perdu en cas de redémarrage en plein combat (limitation assumée Phase 3)."""

    def __init__(self, raid_id, guild, classe, raid_channel):
        self.raid_id = raid_id
        self.guild = guild
        self.classe = classe
        self.raid_channel = raid_channel
        self.combatants = {}       # character_id -> _Combatant
        self.tracker_msg = None    # message de suivi global (salon principal)
        self.lock = asyncio.Lock()  # sérialise les changements structurels (transferts entre fils)
        # Phase 4 : boss.
        self.boss_started = False
        self.finish_started = False
        self.boss = None           # dict runtime du boss (pv/eo/force/bloc/…)
        self.boss_thread = None    # fil « RAID BOSS »
        self.boss_blocking = False # posture de blocage du boss (jusqu'à son prochain tour)

    def alive(self):
        return [c for c in self.combatants.values() if c.alive]

    def thread_members(self, owner):
        """Combattants vivants présents dans le fil de `owner` (owner inclus)."""
        return [c for c in self.combatants.values() if c.alive and c.thread is not None
                and owner.thread is not None and c.thread.id == owner.thread.id]


# =====================================================================
# COG
# =====================================================================
class Raid(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._sessions = {}  # raid_id -> _RaidSession (combats en cours, en mémoire)

    async def cog_load(self):
        if not self.raid_cycle_check.is_running():
            self.raid_cycle_check.start()
        if not self.raid_amende_daily_check.is_running():
            self.raid_amende_daily_check.start()
        if not self.raid_permadeath_check.is_running():
            self.raid_permadeath_check.start()

    async def cog_unload(self):
        self.raid_cycle_check.cancel()
        self.raid_amende_daily_check.cancel()
        self.raid_permadeath_check.cancel()

    # =================================================================
    # COMMANDE /raid — activation / désactivation du cycle (staff)
    # =================================================================
    @app_commands.command(
        name="raid",
        description="Active ou désactive le cycle d'apparition des raids (staff uniquement)")
    async def raid(self, interaction: discord.Interaction):
        if not any(r.id == FICHE_STAFF_ROLE_ID for r in getattr(interaction.user, "roles", [])):
            await interaction.response.send_message(
                "❌ Cette commande est réservée au staff.", ephemeral=True)
            return
        if interaction.guild is None:
            await interaction.response.send_message("Commande à utiliser sur un serveur.", ephemeral=True)
            return
        state = db.raid_get_cycle_state(interaction.guild.id)
        if state is None or not state["active"]:
            # TEMPORAIRE — déclenchement immédiat pour phase de test, à retirer/remettre le délai normal
            # une fois les tests terminés (revenir à next_announce = maintenant + RAID_CYCLE_HOURS sans
            # appel direct à _trigger_raid).
            now = datetime.utcnow()
            db.raid_set_cycle_active(interaction.guild.id, 1, now.isoformat(), None)
            await interaction.response.send_message(
                "✅ Cycle de raids activé. Annonce de test déclenchée immédiatement.", ephemeral=True)
            await self._trigger_raid(interaction.guild.id)  # déclenche tout de suite (Phase 1)
            # Programme quand même le prochain cycle normalement après ce test.
            next_announce = (now + timedelta(hours=RAID_CYCLE_HOURS)).isoformat()
            db.raid_update_cycle_next(interaction.guild.id, next_announce, now.isoformat())
        else:
            db.raid_set_cycle_inactive(interaction.guild.id)
            await interaction.response.send_message(
                "🛑 Cycle de raids désactivé. Plus aucune nouvelle annonce ne sera programmée (les raids "
                "déjà en cours ne sont PAS affectés).", ephemeral=True)

    # =================================================================
    # TÂCHE : cycle d'apparition (toutes les 5 min) + expiration délai chef
    # =================================================================
    @tasks.loop(minutes=5)
    async def raid_cycle_check(self):
        now = datetime.utcnow()
        for state in db.raid_get_active_cycles():
            due = _parse(state["next_announce_at"])
            if due is not None and now >= due:
                try:
                    await self._trigger_raid(state["guild_id"])
                except Exception as e:  # une annonce ratée ne doit jamais casser le cycle
                    print(f"[raid] _trigger_raid a échoué pour la guilde {state['guild_id']} : {e}")
                # Rattrapage : quelle que soit l'ampleur du retard (même 24h+), UNE SEULE annonce est
                # déclenchée, puis le cycle repart du moment présent.
                nxt = (now + timedelta(hours=RAID_CYCLE_HOURS)).isoformat()
                db.raid_update_cycle_next(state["guild_id"], nxt, now.isoformat())
        # Fermeture de la phase de participation (échéances 2h / 3h, débordement).
        await self._process_participation(now)

    @raid_cycle_check.before_loop
    async def _before_cycle(self):
        await self.bot.wait_until_ready()

    async def _process_participation(self, now):
        """FERMETURE DE LA PARTICIPATION (évaluée à chaque tick) pour chaque raid encore ouvert :
        - effectif atteint (borne haute) -> fermeture immédiate + combat ;
        - 2h écoulées sans AUCUN participant -> fermeture « hauts gradés » + amende ;
        - >= 1 participant mais incomplet -> +1h ; à 3h, fermeture avec les présents + combat ;
        - au delà de 3h -> report du PROCHAIN cycle à now + RAID_DELAY_AFTER_OVERFLOW_HOURS (sans toucher
          au raid en cours)."""
        for raid in db.raid_get_open_instances():
            created = _parse(raid["created_at"])
            if created is None:
                continue
            elapsed = now - created
            n = db.raid_count_participants(raid["id"])
            eff_hi = self._suggested_effectif(raid["classe"])[1]

            # 1) Effectif suggéré atteint : fermeture immédiate -> combat.
            if n >= eff_hi:
                await self._close_and_start_combat(raid["id"])
                continue

            # 2) Débordement au delà de 3h : report du prochain cycle à +6h, puis on ferme le raid.
            if elapsed >= timedelta(hours=RAID_PARTICIPATION_EXTENDED_HOURS):
                state = db.raid_get_cycle_state(raid["guild_id"])
                if state is not None and state["active"]:
                    overflow_next = (now + timedelta(hours=RAID_DELAY_AFTER_OVERFLOW_HOURS)).isoformat()
                    db.raid_update_cycle_next(raid["guild_id"], overflow_next, state["last_announce_at"])
                if n >= 1:
                    await self._close_and_start_combat(raid["id"])
                else:
                    await self._close_no_response(raid["id"])
                continue

            # 3) Échéance initiale (2h) : si aucun participant, fermeture « hauts gradés » + amende.
            if elapsed >= timedelta(hours=RAID_PARTICIPATION_INITIAL_HOURS) and n == 0:
                await self._close_no_response(raid["id"])
                # (>= 1 participant : on laisse courir jusqu'à 3h, cf. branche 2.)

    async def _close_and_start_combat(self, raid_id):
        """Ferme la participation et déclenche le combat (Phase 3, stub)."""
        db.raid_set_instance_status(raid_id, "en_cours")
        raid = db.raid_get_instance(raid_id)
        channel = self.bot.get_channel(RAID_ANNOUNCE_CHANNEL_ID)
        if raid is not None and raid["announce_message_id"] and channel is not None:
            try:
                msg = await channel.fetch_message(raid["announce_message_id"])
                embed = self._build_announce_embed(raid)
                embed.title = "⚔️ Participation fermée — Combat en préparation"
                await msg.edit(embed=embed, view=None)
            except discord.HTTPException:
                pass
        await self._start_raid_combat(raid_id)

    async def _close_no_response(self, raid_id):
        """Ferme un raid resté sans participant : « Les hauts gradés s'en sont occupés » + amende."""
        db.raid_set_instance_status(raid_id, "clos_sans_reponse")
        raid = db.raid_get_instance(raid_id)
        channel = self.bot.get_channel(RAID_ANNOUNCE_CHANNEL_ID)
        if raid is not None and raid["announce_message_id"] and channel is not None:
            try:
                msg = await channel.fetch_message(raid["announce_message_id"])
                embed = discord.Embed(
                    title="🏛️ Raid clos",
                    description="Les hauts gradés s'en sont occupés.", color=RAID_COLOR)
                await msg.edit(content=None, embed=embed, view=None)
            except discord.HTTPException:
                pass
        await self._raid_check_and_apply_amende(raid_id)

    # =================================================================
    # PHASE 3 : COMBAT DE RAID
    # =================================================================
    async def get_or_create_raid_role(self, guild, slot_number):
        """Rôle réutilisable « RAID {slot} » (ou « RAID BOSS »). Réutilise l'ID du pool s'il existe encore,
        recrée sinon. Couleur distincte par slot."""
        existing = db.raid_get_role_pool(guild.id, slot_number)
        if existing is not None:
            role = guild.get_role(existing)
            if role is not None:
                return role
        if str(slot_number) == "boss":
            name, color = "RAID BOSS", RAID_BOSS_ROLE_COLOR
        else:
            name = f"RAID {slot_number}"
            color = RAID_ROLE_COLORS[(int(slot_number) - 1) % len(RAID_ROLE_COLORS)]
        try:
            role = await guild.create_role(name=name, colour=discord.Colour(color),
                                           reason="Pool de rôles de raid")
        except discord.HTTPException:
            return None
        db.raid_set_role_pool(guild.id, slot_number, role.id)
        return role

    def _burst_power(self, daily, character_id, stats) -> float:
        """Même « burst » que /daily : max(dégât physique, meilleur sort débloqué, meilleure arme)."""
        phys = stats["force"] * DAILY_DAMAGE_RATIO
        best_spell = max((s["damage"] for s in daily._unlocked_spells(character_id, stats["eo"])), default=0)
        best_arme = max((a["degats_actuel"] or 0 for a in db.get_character_armes(character_id)), default=0)
        return max(phys, best_spell, best_arme, 1)

    async def _resolve_member(self, guild, user_id):
        m = guild.get_member(user_id) if guild else None
        if m is None and guild is not None:
            try:
                m = await guild.fetch_member(user_id)
            except discord.HTTPException:
                m = None
        return m

    async def _start_raid_combat(self, raid_id):
        """§3 : ferme la participation et lance le combat. Mentionne les participants, attribue les rôles
        du pool, crée un fil par participant, répartit les monstres au prorata du burst, génère les
        monstres (règles /daily, sans filtre de classe RP) et démarre le combat de chaque fil."""
        raid = db.raid_get_instance(raid_id)
        daily = self.bot.get_cog("Daily")
        if raid is None or daily is None:
            return
        guild = self.bot.get_guild(raid["guild_id"])
        raid_channel = self.bot.get_channel(raid["channel_id"])
        if guild is None or raid_channel is None:
            return
        prows = db.raid_get_participants(raid_id)
        if not prows:
            return

        session = _RaidSession(raid_id, guild, raid["classe"], raid_channel)
        self._sessions[raid_id] = session

        # 1) Construit les combattants + burst.
        for prow in prows:
            member = await self._resolve_member(guild, prow["user_id"])
            char = get_character(prow["character_id"])
            stats = daily._player_stats(prow["character_id"])
            profile = db.get_or_create_profile(prow["character_id"])
            burst = self._burst_power(daily, prow["character_id"], stats)
            comb = _Combatant(prow, member, char, stats, profile, burst)
            comb._stats = stats  # conservé pour la génération de monstres
            session.combatants[comb.character_id] = comb

        # 1bis) Mention de tous les participants dans le salon du raid.
        mentions = " ".join(f"<@{c.user_id}>" for c in session.combatants.values())
        try:
            await raid_channel.send(f"⚔️ **Le raid commence !** {mentions}")
        except discord.HTTPException:
            pass

        # 2-3) Répartition des monstres au prorata du burst_power.
        total_burst = sum(c.burst for c in session.combatants.values()) or 1
        monster_total = raid["monster_count"]
        for comb in session.combatants.values():
            part = comb.burst / total_burst
            n = max(1, round(monster_total * part))
            monstres = [_new_monster_runtime(comb._stats, raid["classe"]) for _ in range(n)]
            comb.current = monstres[0]
            comb.queue = monstres[1:]
            # 2) Rôle du pool + fil individuel.
            role = await self.get_or_create_raid_role(guild, comb.role_slot)
            if role is not None and comb.char is not None:
                await _add_role_real_or_virtual(guild, comb.char, role.id, "Participation à un raid")
            comb.thread = await self._create_thread(raid_channel, f"RAID {comb.role_slot}")
            # 5) Persiste l'état de combat durable.
            db.raid_update_participant(
                comb.participant_id,
                monster_queue_json=json.dumps(comb.queue),
                current_monster_state_json=json.dumps(comb.current),
                is_alive=1, total_damage_dealt=0,
                thread_id=(comb.thread.id if comb.thread else None))

        # 7) Embed de suivi global dans le salon principal.
        await self._update_global_tracker(raid_id)

        # 6) Démarre le combat de chaque fil en tâche de fond (fils indépendants et parallèles).
        for comb in session.combatants.values():
            asyncio.create_task(self._run_thread(raid_id, comb.character_id))

    async def _create_thread(self, channel, name):
        try:
            return await channel.create_thread(name=name, type=discord.ChannelType.public_thread)
        except (discord.HTTPException, AttributeError):
            return None

    # ---------- construction / relecture de l'état /daily ----------
    def _build_st(self, comb, monster):
        return {
            "name_j": comb.name, "name_p": monster["name"],
            "pv_j": comb.pv, "pv_max_j": comb.pv_max, "eo_j": comb.eo, "eo_max_j": comb.eo_max,
            "force_base_j": comb.force_base,
            "pv_p": monster["pv"], "pv_max_p": monster["pv_max"],
            "eo_p": monster["eo"], "eo_max_p": monster["eo_max"], "force_base_p": monster["force"],
            "bloc_j": comb.bloc, "bloc_p": monster["bloc"],
            "potions_p": monster["potions"], "potion_pct_p": monster["potion_pct"],
            "crit_chance_j": comb.crit_chance, "sort_bonus_j": comb.sort_bonus,
        }

    def _writeback_st(self, st, comb, monster):
        comb.pv = st["pv_j"]; comb.eo = st["eo_j"]
        comb.crit_chance = st["crit_chance_j"]; comb.sort_bonus = st["sort_bonus_j"]
        comb.bloc = st["bloc_j"]; comb.force_base = st["force_base_j"]
        monster["pv"] = st["pv_p"]; monster["eo"] = st["eo_p"]; monster["bloc"] = st["bloc_p"]
        monster["potions"] = st["potions_p"]; monster["force"] = st["force_base_p"]

    # ---------- boucle de combat d'un fil ----------
    async def _run_thread(self, raid_id, owner_cid):
        """Combat d'un fil. Rotation des tours entre tous les combattants vivants présents dans CE fil
        (multi-joueurs après entraide) contre le monstre courant du propriétaire du fil. Réutilise les
        primitives de /daily pour chaque tour (1v1 joueur actif vs monstre)."""
        session = self._sessions.get(raid_id)
        daily = self.bot.get_cog("Daily")
        if session is None or daily is None:
            return
        owner = session.combatants.get(owner_cid)
        if owner is None or owner.thread is None:
            return
        my_thread_id = owner.thread.id
        tour = 0
        while True:
            # Le fil s'arrête si son propriétaire a été relocalisé (secours accepté) ou est mort sans
            # secours : ses monstres ont alors été transférés/redistribués ailleurs.
            if not owner.alive or owner.thread is None or owner.thread.id != my_thread_id:
                return
            # Le propriétaire a-t-il encore des monstres ?
            if owner.current is None:
                if not owner.finished:
                    await self._on_wave_finished(raid_id, owner)
                return
            members = session.thread_members(owner)
            if not members:
                return  # plus personne de vivant dans ce fil (géré par les transferts/défaites)
            for comb in members:
                if not comb.alive or owner.current is None:
                    break
                tour += 1
                monster = owner.current
                st = self._build_st(comb, monster)
                gains = {"force": 0, "endurance": 0, "energie_occulte": 0, "sorts": 0}
                sort_xp = {}
                pvp_before = st["pv_p"]
                action = await daily._player_turn(
                    owner.thread, comb.member, comb.character_id, st, gains, sort_xp)
                if action is None:
                    # Timeout : le joueur se met en garde d'office (le combat continue).
                    f_act = current_force(comb.force_base, comb.pv, comb.pv_max)
                    action = {"kind": "bloquer", "attacking": False, "damage": 0, "dtype": None,
                              "blocking": True, "force_actuelle": f_act}
                ap = daily._pnj_turn(session.classe, st)
                text, color, crit = daily._resolve_round(st, action, ap, gains, True)
                if crit:
                    await daily._play_black_flash(owner.thread, comb.name)
                self._writeback_st(st, comb, monster)
                # §4 : cumul des dégâts infligés au monstre (pour le MVP Phase 4).
                dealt = max(0, pvp_before - monster["pv"])
                comb.total_damage += dealt
                db.raid_add_participant_damage(comb.participant_id, dealt)
                try:
                    await owner.thread.send(embed=daily._round_embed(tour, st, text, color))
                except discord.HTTPException:
                    pass
                db.raid_update_participant(
                    owner.participant_id, current_monster_state_json=json.dumps(monster))
                await self._update_global_tracker(raid_id)

                # Monstre vaincu -> suivant.
                if monster["pv"] <= DAILY_PV_FLOOR:
                    owner.current = owner.queue.pop(0) if owner.queue else None
                    db.raid_update_participant(
                        owner.participant_id,
                        monster_queue_json=json.dumps(owner.queue),
                        current_monster_state_json=json.dumps(owner.current) if owner.current else None)
                    if owner.current is None:
                        await self._on_wave_finished(raid_id, owner)
                        return
                    else:
                        try:
                            await owner.thread.send(embed=discord.Embed(
                                title="⚔️ Nouvel adversaire",
                                description=f"**{owner.current['name']}** surgit !", color=RAID_COLOR))
                        except discord.HTTPException:
                            pass
                    continue

                # Joueur tombé -> secours (§5).
                if comb.pv <= DAILY_PV_FLOOR:
                    stop = await self._on_player_defeated(raid_id, comb)
                    if stop:
                        return  # wipe complet : la session est close ailleurs

    # ---------- §5 : défaite d'un joueur + entraide ----------
    async def _on_player_defeated(self, raid_id, loser) -> bool:
        """Retourne True si le combat doit s'arrêter (wipe complet géré)."""
        session = self._sessions.get(raid_id)
        if session is None:
            return True
        async with session.lock:
            loser.alive = False
            db.raid_update_participant(loser.participant_id, is_alive=0)
            try:
                await loser.thread.send(embed=discord.Embed(
                    title="💀 Vaincu", description=f"**{loser.name}** est tombé au combat.",
                    color=discord.Color.dark_red()))
            except (discord.HTTPException, AttributeError):
                pass
            # Candidats sauveurs = autres vivants, triés par burst décroissant.
            savers = sorted([c for c in session.alive() if c.character_id != loser.character_id],
                            key=lambda c: c.burst, reverse=True)
            for saver in savers:
                accepted = await self._ask_rescue(saver, loser)
                if accepted:
                    await self._transfer_loser_to_saver(raid_id, loser, saver)
                    return False
            # Personne n'accepte.
            remaining = [c for c in session.alive() if c.character_id != loser.character_id]
            if not remaining:
                await self._handle_wipe(raid_id)
                return True
            # Redistribue les monstres restants du perdant entre les vivants.
            pool = ([loser.current] if loser.current else []) + list(loser.queue)
            loser.current = None
            loser.queue = []
            for i, mon in enumerate(pool):
                if mon is None:
                    continue
                target = remaining[i % len(remaining)]
                target.queue.append(mon)
                db.raid_update_participant(target.participant_id,
                                           monster_queue_json=json.dumps(target.queue))
            # Ferme le fil (désormais vide) du perdant.
            await self._archive_thread(loser)
            return False

    async def _ask_rescue(self, saver, loser) -> bool:
        view = _OwnerChoiceView(saver.user_id, [
            ("yes", "Voler à son secours", "🆘", discord.ButtonStyle.success),
            ("no", "Refuser", "❌", discord.ButtonStyle.secondary)], timeout=120)
        sent = await self._dm_user(
            saver.user_id,
            content=f"🆘 **{loser.name}** vient de tomber face à ses adversaires. Veux-tu voler à son secours ?",
            view=view)
        if not sent:
            return False
        await view.wait()
        return view.result == "yes"

    async def _transfer_loser_to_saver(self, raid_id, loser, saver):
        """Le perdant rejoint le fil du sauveur ; son monstre courant (état EXACT) est ajouté à la file
        du sauveur ; le perdant redevient actif dans ce fil ; son ancien fil est archivé."""
        if loser.current is not None:
            saver.queue.append(loser.current)
            db.raid_update_participant(saver.participant_id, monster_queue_json=json.dumps(saver.queue))
        # Le perdant conserve le reste de sa file : ajouté aussi à la file du sauveur.
        for mon in loser.queue:
            saver.queue.append(mon)
        db.raid_update_participant(saver.participant_id, monster_queue_json=json.dumps(saver.queue))
        old_thread = loser.thread
        loser.current = None
        loser.queue = []
        loser.alive = True
        loser.thread = saver.thread
        # Rôle/fil : retire le rôle du perdant, lui attribue celui du sauveur.
        await self._swap_role(loser, saver)
        db.raid_update_participant(loser.participant_id, is_alive=1,
                                   thread_id=(saver.thread.id if saver.thread else None),
                                   monster_queue_json=json.dumps([]),
                                   current_monster_state_json=None)
        try:
            await saver.thread.send(embed=discord.Embed(
                description=f"🏃 **{saver.name}** se précipite pour aider **{loser.name}** !",
                color=RAID_COLOR))
        except (discord.HTTPException, AttributeError):
            pass
        await self._archive_thread_obj(old_thread)

    # ---------- §6 : fin de vague + aide proactive ----------
    async def _on_wave_finished(self, raid_id, comb):
        session = self._sessions.get(raid_id)
        if session is None:
            return
        comb.finished = True
        # Reste-t-il un participant vivant qui n'a pas fini sa vague ?
        others = [c for c in session.alive()
                  if c.character_id != comb.character_id and not c.finished and c.current is not None]
        if not others:
            try:
                await comb.thread.send(embed=discord.Embed(
                    title="✅ Vague terminée", description=f"**{comb.name}** a nettoyé sa vague.",
                    color=discord.Color.green()))
            except (discord.HTTPException, AttributeError):
                pass
            await self._check_completion(raid_id)
            return
        # Propose d'aider le plus FAIBLE encore actif (burst croissant).
        weakest = min(others, key=lambda c: c.burst)
        view = _OwnerChoiceView(comb.user_id, [
            ("yes", f"Aider {weakest.name}", "🤝", discord.ButtonStyle.success),
            ("no", "Non merci", "❌", discord.ButtonStyle.secondary)], timeout=120)
        try:
            await comb.thread.send(
                content=f"✅ Tu as terminé ta vague. Veux-tu aider **{weakest.name}** ?", view=view)
        except (discord.HTTPException, AttributeError):
            return
        await view.wait()
        if view.result != "yes":
            await self._check_completion(raid_id)
            return
        session = self._sessions.get(raid_id)
        if session is None or not weakest.alive or weakest.current is None:
            return
        async with session.lock:
            # Progrès du helper sauvegardés (total_damage jamais réinitialisé). Bascule vers le fil aidé.
            comb.finished = False
            old_thread = comb.thread
            comb.thread = weakest.thread
            await self._swap_role(comb, weakest)
            db.raid_update_participant(comb.participant_id,
                                       thread_id=(weakest.thread.id if weakest.thread else None))
            try:
                await weakest.thread.send(embed=discord.Embed(
                    description="⏸️ Réorganisation en cours, patientez quelques instants...",
                    color=RAID_COLOR))
            except (discord.HTTPException, AttributeError):
                pass
            await self._archive_thread_obj(old_thread)
        await asyncio.sleep(5)
        # Le fil aidé devient multi-joueurs : sa propre boucle _run_thread itère déjà sur thread_members,
        # donc le helper est automatiquement intégré à la rotation au tour suivant. Rien d'autre à lancer.

    async def _check_completion(self, raid_id):
        """Quand TOUS les vivants ont vidé leur file de monstres individuels : bascule vers le boss
        (Phase 4). Garde-fou anti double-démarrage via le verrou + le flag boss_started."""
        session = self._sessions.get(raid_id)
        if session is None:
            return
        alive = session.alive()
        if not alive:
            return  # wipe géré ailleurs
        if any(c.current is not None or c.queue for c in alive):
            return  # il reste des monstres individuels
        async with session.lock:
            if session.boss_started:
                return
            session.boss_started = True
        await self._start_raid_boss_phase(raid_id)

    # =================================================================
    # PHASE 4 : BOSS
    # =================================================================
    async def _start_raid_boss_phase(self, raid_id):
        """§1 : réunit tous les vivants sous le rôle/fil RAID BOSS, calcule les stats du boss (×1,5 de la
        référence) et lance la boucle de combat du boss."""
        session = self._sessions.get(raid_id)
        daily = self.bot.get_cog("Daily")
        if session is None or daily is None:
            return
        guild = session.guild
        alive = session.alive()
        if not alive:
            return
        # 2) Retire à chaque vivant son rôle/fil individuel + archive son fil.
        for comb in alive:
            old_role = await self.get_or_create_raid_role(guild, comb.role_slot)
            if old_role is not None and comb.char is not None:
                await _remove_role_real_or_virtual(guild, comb.char, old_role.id, "Passage au boss de raid")
            await self._archive_thread_obj(comb.thread)
        # 3) Rôle BOSS attribué à tous les vivants.
        role_boss = await self.get_or_create_raid_role(guild, "boss")
        for comb in alive:
            if role_boss is not None and comb.char is not None:
                await _add_role_real_or_virtual(guild, comb.char, role_boss.id, "Boss de raid")
        # 4) Fil commun RAID BOSS.
        session.boss_thread = await self._create_thread(session.raid_channel, "RAID BOSS")
        for comb in alive:
            comb.thread = session.boss_thread
            comb.finished = False
            comb.pending_block = False
            db.raid_update_participant(
                comb.participant_id, thread_id=(session.boss_thread.id if session.boss_thread else None))
        # 5) Stats du boss : référence = plus fort de MÊME classe RP que le raid, sinon plus fort global.
        classe = session.classe
        rp = {}
        for comb in alive:
            try:
                rp[comb.character_id] = await get_player_rp_classe(guild, comb.character_id)
            except Exception:
                rp[comb.character_id] = None
        same = [c for c in alive if rp.get(c.character_id) == classe]
        pool = same or alive
        reference = max(pool, key=lambda c: (c.force_base, c.pv_max, c.eo_max))
        ref_force_actuelle = current_force(reference.force_base, reference.pv, reference.pv_max)
        boss = {
            "name": f"Fléau Suprême — Classe {classe}",
            "pv_max": max(1, round(reference.pv_max * 1.5)), "eo_max": max(0, round(reference.eo_max * 1.5)),
            "force": max(1, round(ref_force_actuelle * 1.5)),
            "bloc": 0, "potions": 0, "potion_pct": 0,
        }
        boss["pv"] = boss["pv_max"]
        boss["eo"] = boss["eo_max"]
        session.boss = boss
        session.boss_blocking = False
        # 6) Embed d'introduction du boss.
        if session.boss_thread is not None:
            mentions = " ".join(f"<@{c.user_id}>" for c in alive)
            try:
                await session.boss_thread.send(
                    content=f"👹 **LE BOSS APPARAÎT !** {mentions}",
                    embed=discord.Embed(
                        title=f"👹 {boss['name']}",
                        description=(f"❤️ PV : **{boss['pv_max']:,}**\n"
                                     f"🔵 EO : **{boss['eo_max']:,}**\n"
                                     f"💪 Force : **{boss['force']:,}**".replace(",", " ")),
                        color=RAID_COLOR))
            except (discord.HTTPException, AttributeError):
                pass
        await self._update_global_tracker(raid_id)
        asyncio.create_task(self._run_boss(raid_id))

    async def _run_boss(self, raid_id):
        """§2 : rotation — 2 joueurs (burst croissant, cyclique) puis 1 tour du boss, jusqu'à Finish Him
        (< 30% PV boss) ou wipe."""
        session = self._sessions.get(raid_id)
        if session is None:
            return
        ordre = sorted(session.alive(), key=lambda c: c.burst)  # plus faible -> plus fort
        idx = 0
        while True:
            if session.finish_started:
                return
            if not [c for c in ordre if c.alive]:
                await self._handle_wipe(raid_id)
                return
            # 2 tours de joueurs d'affilée.
            for _ in range(2):
                living = [c for c in ordre if c.alive]
                if not living:
                    break
                comb = living[idx % len(living)]
                idx += 1
                res = await self._boss_player_turn(raid_id, comb)
                if res == "finish" or session.finish_started:
                    return
            # Tour du boss.
            if [c for c in ordre if c.alive]:
                await self._boss_turn(raid_id)
            if not [c for c in ordre if c.alive]:
                await self._handle_wipe(raid_id)
                return

    async def _boss_player_turn(self, raid_id, comb):
        session = self._sessions.get(raid_id)
        daily = self.bot.get_cog("Daily")
        if session is None or daily is None or session.boss is None:
            return None
        boss = session.boss
        st = self._build_st(comb, boss)
        cf_boss = current_force(boss["force"], boss["pv"], boss["pv_max"])
        # Posture du boss pendant le tour du joueur : bloque (si en garde) ou temporise (n'attaque pas).
        if session.boss_blocking:
            ap = {"kind": "bloquer", "attacking": False, "damage": 0, "dtype": None,
                  "blocking": True, "force_actuelle": cf_boss}
        else:
            ap = {"kind": "attente", "attacking": False, "damage": 0, "dtype": None,
                  "blocking": False, "force_actuelle": cf_boss}
        gains = {"force": 0, "endurance": 0, "energie_occulte": 0, "sorts": 0}
        sort_xp = {}
        pvp_before = boss["pv"]
        action = await daily._player_turn(session.boss_thread, comb.member, comb.character_id, st, gains, sort_xp)
        if action is None:
            f_act = current_force(comb.force_base, comb.pv, comb.pv_max)
            action = {"kind": "bloquer", "attacking": False, "damage": 0, "dtype": None,
                      "blocking": True, "force_actuelle": f_act}
        comb.pending_block = (action["kind"] == "bloquer")
        text, color, crit = daily._resolve_round(st, action, ap, gains, True)
        if crit:
            await daily._play_black_flash(session.boss_thread, comb.name)
        self._writeback_st(st, comb, boss)
        dealt = max(0, pvp_before - boss["pv"])
        comb.total_damage += dealt
        db.raid_add_participant_damage(comb.participant_id, dealt)
        try:
            await session.boss_thread.send(embed=daily._round_embed(0, st, text, color))
        except (discord.HTTPException, AttributeError):
            pass
        await self._update_global_tracker(raid_id)
        # Joueur tombé pendant la phase de boss.
        if comb.pv <= DAILY_PV_FLOOR:
            comb.alive = False
            db.raid_update_participant(comb.participant_id, is_alive=0)
            try:
                await session.boss_thread.send(embed=discord.Embed(
                    description=f"💀 **{comb.name}** est tombé face au boss.",
                    color=discord.Color.dark_red()))
            except (discord.HTTPException, AttributeError):
                pass
        # §4 : Finish Him dès que le boss passe sous 30% de ses PV max.
        if boss["pv"] <= round(boss["pv_max"] * 0.30) and not session.finish_started:
            session.finish_started = True
            await self._run_finish_him(raid_id)
            return "finish"
        return None

    async def _boss_turn(self, raid_id):
        """§3 : le boss agit (via la même IA que les PNJ /daily). Attaque 1 ou 2 cibles (30% les deux),
        ou se met en garde / se renforce. Le blocage éventuel d'une cible dépend de SA dernière action."""
        session = self._sessions.get(raid_id)
        daily = self.bot.get_cog("Daily")
        if session is None or daily is None or session.boss is None:
            return
        boss = session.boss
        # Décision du boss via _pnj_turn (renforcement plafonné + potion d'urgence gérés côté /daily).
        st_boss = {
            "eo_p": boss["eo"], "eo_max_p": boss["eo_max"], "potions_p": boss["potions"],
            "potion_pct_p": boss["potion_pct"], "force_base_p": boss["force"],
            "pv_p": boss["pv"], "pv_max_p": boss["pv_max"],
        }
        ba = daily._pnj_turn(session.classe, st_boss)
        boss["eo"] = st_boss["eo_p"]
        boss["potions"] = st_boss["potions_p"]

        alive = session.alive()
        if not alive:
            return
        if not ba["attacking"]:
            # Blocage : posture maintenue jusqu'au prochain tour du boss.
            session.boss_blocking = True
            try:
                await session.boss_thread.send(embed=discord.Embed(
                    description=f"🛡️ **{boss['name']}** se met en garde.", color=RAID_COLOR))
            except (discord.HTTPException, AttributeError):
                pass
            return
        session.boss_blocking = False
        # Choix des cibles.
        if len(alive) >= 2:
            deux = sorted(alive, key=lambda c: c.pv, reverse=True)[:2]
            if random.randint(1, 100) <= 30:
                targets = deux
            else:
                targets = [max(alive, key=lambda c: c.pv)]
        else:
            targets = [alive[0]]
        parts = []
        for t in targets:
            st_t = {"pv_j": t.pv, "bloc_j": t.bloc}
            attack = {"damage": ba["damage"], "dtype": "phys"}
            dealt, block_ok = daily._apply_attack(st_t, attack, defender="j",
                                                  defender_blocking=t.pending_block)
            t.pv = st_t["pv_j"]
            t.bloc = st_t["bloc_j"]
            if block_ok:
                parts.append(f"🛡️ **{t.name}** bloque l'attaque du boss.")
            else:
                parts.append(f"💥 Le boss inflige **{dealt:,}** dégâts à **{t.name}**.".replace(",", " "))
        # Réinitialise les postures de blocage des joueurs après le tour du boss.
        for c in alive:
            c.pending_block = False
        try:
            await session.boss_thread.send(embed=discord.Embed(
                title=f"👹 Tour du boss", description="\n".join(parts), color=RAID_COLOR))
        except (discord.HTTPException, AttributeError):
            pass
        for t in targets:
            if t.pv <= DAILY_PV_FLOOR and t.alive:
                t.alive = False
                db.raid_update_participant(t.participant_id, is_alive=0)
                try:
                    await session.boss_thread.send(embed=discord.Embed(
                        description=f"💀 **{t.name}** est tombé face au boss.",
                        color=discord.Color.dark_red()))
                except (discord.HTTPException, AttributeError):
                    pass
        await self._update_global_tracker(raid_id)

    # ---------- §4 : séquence Finish Him ----------
    async def _run_finish_him(self, raid_id):
        session = self._sessions.get(raid_id)
        if session is None or session.boss_thread is None:
            return
        alive = sorted(session.alive(), key=lambda c: c.burst)  # plus faible -> plus fort
        if not alive:
            await self._finish_boss_victory(raid_id)
            return
        # 1) Bouton FINISH HIM (n'importe quel vivant, un seul clic).
        view = _FinishHimView({c.user_id for c in alive})
        try:
            msg = await session.boss_thread.send(
                content="💥 **Le boss vacille…** Un participant vivant peut porter le coup de grâce !",
                view=view)
        except (discord.HTTPException, AttributeError):
            msg = None
        await view.wait()
        if msg is not None:
            try:
                await msg.edit(view=None)
            except discord.HTTPException:
                pass
        # 2) Séquence individuelle (MP), dans l'ordre croissant de burst.
        finals = {}
        for comb in alive:
            choix = await self._dm_choice(
                comb.user_id, "Choisis ton action finale :",
                [("attaquer", "⚔️ Attaquer"), ("sort", "✨ Utiliser un sort"), ("black_flash", "⚡ Black Flash")])
            gif = await self._dm_text(
                comb.user_id,
                "Envoie un lien GIF pour illustrer ton attaque. ⚠️ Les GIFs de Territoire sont interdits.")
            replique = await self._dm_text(
                comb.user_id, "Écris une réplique à dire (ou envoie // pour ne rien dire).")
            finals[comb.character_id] = (choix, gif, replique)
        # 3) Restitution dans le fil, MÊME ORDRE.
        for comb in alive:
            choix, gif, replique = finals.get(comb.character_id, (None, None, None))
            if replique and replique.strip() and replique.strip() != "//":
                try:
                    await session.boss_thread.send(embed=discord.Embed(
                        description=f"💢 **{comb.name}** : \"{replique.strip()}\"",
                        color=discord.Color.dark_red()))
                except (discord.HTTPException, AttributeError):
                    pass
                await asyncio.sleep(2)
            if gif and gif.strip().lower().startswith("http"):
                e = discord.Embed(color=RAID_COLOR)
                e.set_image(url=gif.strip())
                try:
                    await session.boss_thread.send(embed=e)
                except discord.HTTPException:
                    pass
        # 4) Le boss meurt automatiquement (cinématique de conclusion).
        if session.boss is not None:
            session.boss["pv"] = 0
        await self._finish_boss_victory(raid_id)

    async def _finish_boss_victory(self, raid_id):
        """Mort garantie du boss : annonce la victoire + le MVP, puis enchaîne le nettoyage, la
        distribution des récompenses et le récapitulatif owner (Phase 5), sans intervention manuelle."""
        session = self._sessions.get(raid_id)
        if session is None:
            return
        mvp_cid = await self.get_raid_mvp(raid_id)
        mvp_char = get_character(mvp_cid) if mvp_cid else None
        mvp_txt = (mvp_char["character_name"] if mvp_char and mvp_char["character_name"]
                   else (f"#{mvp_cid}" if mvp_cid else "—"))
        embed = discord.Embed(
            title="🏆 BOSS VAINCU !",
            description=("Le boss du raid s'effondre. La menace est écartée.\n\n"
                         f"🥇 **MVP : {mvp_txt}** (plus gros total de dégâts du raid)."),
            color=discord.Color.gold())
        for target in (session.boss_thread, session.raid_channel):
            try:
                await target.send(embed=embed)
            except (discord.HTTPException, AttributeError):
                pass
        await self._end_raid(raid_id)

    async def get_raid_mvp(self, raid_id):
        """§5 : MVP = plus haut total_damage_dealt cumulé (monstres + boss). Retourne le character_id."""
        return db.raid_get_mvp(raid_id)

    # =================================================================
    # PHASE 5 : NETTOYAGE + RÉCOMPENSES + RÉCAP OWNER
    # =================================================================
    def _pool_role(self, guild, slot_number):
        """Rôle du pool s'il existe ENCORE sur le serveur, sans jamais en créer (nettoyage de fin)."""
        rid = db.raid_get_role_pool(guild.id, slot_number)
        return guild.get_role(rid) if (rid is not None and guild is not None) else None

    async def _delete_thread_obj(self, thread):
        if thread is None:
            return
        try:
            await thread.delete()
        except (discord.HTTPException, AttributeError):
            # À défaut de suppression (permissions), on archive/verrouille au moins.
            await self._archive_thread_obj(thread)

    async def _end_raid(self, raid_id):
        """§1 : nettoyage post-combat (fils + rôles retirés, jamais supprimés du serveur), §2 :
        distribution des récompenses, §3 : récap MP à l'owner."""
        session = self._sessions.get(raid_id)
        # 1) Nettoyage des fils + retrait des rôles (RAID BOSS + tout RAID N résiduel), sans jamais
        #    supprimer les rôles eux-mêmes (ils restent dans raid_role_pool pour les prochains raids).
        if session is not None:
            guild = session.guild
            role_boss = self._pool_role(guild, "boss")
            threads = set()
            if session.boss_thread is not None:
                threads.add(session.boss_thread)
            for comb in session.combatants.values():
                if comb.thread is not None:
                    threads.add(comb.thread)
                if comb.char is None:
                    continue
                if role_boss is not None:
                    await _remove_role_real_or_virtual(guild, comb.char, role_boss.id, "Fin de raid")
                slot_role = self._pool_role(guild, comb.role_slot)
                if slot_role is not None:
                    await _remove_role_real_or_virtual(guild, comb.char, slot_role.id, "Fin de raid")
            for th in threads:
                await self._delete_thread_obj(th)
        # 3) Statut terminé.
        db.raid_set_instance_status(raid_id, "termine")
        # 2) Récompenses + 3) récap owner.
        await self._distribute_rewards_and_recap(raid_id)
        # Fin de session.
        self._sessions.pop(raid_id, None)

    def _weighted_pick_list(self, items, weights):
        """Tirage pondéré sur deux listes parallèles (items, weights). Retourne un item."""
        total = sum(weights) or 1
        r = random.uniform(0, total)
        acc = 0
        for it, w in zip(items, weights):
            acc += w
            if r <= acc:
                return it
        return items[-1]

    async def _distribute_rewards_and_recap(self, raid_id):
        """§2-3 : pour chaque participant VIVANT à la fin (survivants + sauvés par l'owner), applique
        XP / points à répartir / coffre (ajouté DIRECTEMENT à l'inventaire), avec bonus MVP +1,5%, puis
        envoie le récapitulatif complet en MP à l'owner (découpé sous 2000 caractères sans couper une
        ligne participant)."""
        raid = db.raid_get_instance(raid_id)
        if raid is None:
            return
        classe = raid["classe"]
        reward = RAID_REWARDS.get(classe)
        if reward is None:
            return
        mvp_cid = db.raid_get_mvp(raid_id)
        alive = db.raid_get_alive_participants(raid_id)  # ordre = role_slot (ordre d'arrivée)
        mvp_mult = 1 + RAID_MVP_BONUS_PCT / 100
        lignes = []
        for p in alive:
            cid = p["character_id"]
            est_mvp = (cid == mvp_cid)
            mult = mvp_mult if est_mvp else 1.0
            xp = round(random.randint(*reward["xp"]) * mult)
            stats_libre = round(random.randint(*reward["stats_libre"]) * mult)
            await db.grant_character_xp(cid, xp)
            db.add_points_restants(cid, stats_libre)
            # Coffre : poids égaux, le plus rare légèrement favorisé pour le MVP.
            coffres = list(reward["coffres"])
            poids = [1.0] * len(coffres)
            if est_mvp and poids:
                poids[-1] *= mvp_mult
            rarete = self._weighted_pick_list(coffres, poids)
            item = db.get_coffre_item_by_rarete(rarete)
            coffre_nom = item["name"] if item else f"Coffre {RAID_LABELS_COFFRE.get(rarete, rarete)}"
            if item is not None:
                db.inv_add_item(cid, item["id"], 1)  # ajouté DIRECTEMENT (jamais de choix stocker/ouvrir)
            char = get_character(cid)
            nom = (char["character_name"] if char and char["character_name"] else f"#{cid}")
            marqueur = " ⭐ MVP" if est_mvp else ""
            lignes.append(f"{nom}{marqueur} — +{xp} XP, +{stats_libre} stats, {coffre_nom}")

        mvp_char = get_character(mvp_cid) if mvp_cid else None
        mvp_nom = (mvp_char["character_name"] if mvp_char and mvp_char["character_name"]
                   else (f"#{mvp_cid}" if mvp_cid else "—"))
        header = (f"📋 **Raid Classe {classe} terminé** — <#{raid['channel_id']}>\n"
                  f"**MVP :** {mvp_nom} (+{RAID_MVP_BONUS_PCT}% bonus sur ses gains)")
        if not lignes:
            header += "\n\n_Aucun survivant récompensé._"
        # Découpe sous 2000 caractères, sans jamais couper une ligne participant.
        for chunk in self._chunk_recap(header, lignes):
            await self._dm_user(OWNER_ID, content=chunk)

    def _chunk_recap(self, header, lignes, limit=1950):
        """Regroupe header + lignes en messages <= limit, sans couper une ligne. Le header ouvre le
        premier message ; les suivants ne contiennent que des lignes."""
        chunks = []
        cur = header
        for ligne in lignes:
            add = "\n\n" + ligne if cur == header else "\n" + ligne
            if len(cur) + len(add) > limit:
                chunks.append(cur)
                cur = ligne
            else:
                cur += add
        if cur:
            chunks.append(cur)
        return chunks

    # ---------- prompts en MP (Finish Him) ----------
    async def _dm_choice(self, user_id, prompt, options):
        u = self.bot.get_user(user_id) or await self._safe_fetch_user(user_id)
        if u is None:
            return None
        try:
            dm = await u.create_dm()
        except discord.HTTPException:
            return None
        view = _OwnerChoiceView(user_id, [(k, lbl, None, discord.ButtonStyle.secondary) for k, lbl in options])
        try:
            await dm.send(prompt, view=view)
        except discord.HTTPException:
            return None
        await view.wait()
        return view.result

    async def _dm_text(self, user_id, prompt):
        u = self.bot.get_user(user_id) or await self._safe_fetch_user(user_id)
        if u is None:
            return None
        try:
            dm = await u.create_dm()
            await dm.send(prompt)
        except discord.HTTPException:
            return None

        def check(m):
            return m.author.id == user_id and m.channel.id == dm.id and not m.author.bot
        try:
            m = await self.bot.wait_for("message", check=check, timeout=180)
            return m.content
        except asyncio.TimeoutError:
            return None

    async def _safe_fetch_user(self, user_id):
        try:
            return await self.bot.fetch_user(user_id)
        except discord.HTTPException:
            return None

    # ---------- §7 : wipe complet ----------
    async def _handle_wipe(self, raid_id):
        session = self._sessions.get(raid_id)
        raid = db.raid_get_instance(raid_id)
        if session is None or raid is None:
            return
        db.raid_set_instance_status(raid_id, "termine")
        fallen = list(session.combatants.values())
        view = discord.ui.View(timeout=None)
        view.add_item(discord.ui.Button(label="Sauver tout le monde", emoji="✅",
                                        style=discord.ButtonStyle.success, custom_id=f"raid_wipe_all:{raid_id}"))
        view.add_item(discord.ui.Button(label="Sauver certains", emoji="🎯",
                                        style=discord.ButtonStyle.primary, custom_id=f"raid_wipe_some:{raid_id}"))
        view.add_item(discord.ui.Button(label="Ne pas intervenir", emoji="❌",
                                        style=discord.ButtonStyle.danger, custom_id=f"raid_wipe_none:{raid_id}"))
        salon = f"<#{raid['channel_id']}>"
        await self._dm_user(
            OWNER_ID,
            content=None,
            embed=discord.Embed(
                title="💀 Wipe complet",
                description=(f"Le raid **Classe {raid['classe']}** dans {salon} s'est soldé par la mort de "
                             "TOUS les participants. Veux-tu envoyer des hauts gradés les sauver ?"),
                color=discord.Color.dark_red()),
            view=view)
        # On garde la session en mémoire jusqu'à la décision de l'owner (les boutons la relisent).

    async def _handle_wipe_decision(self, interaction, cid, mode):
        raid_id = int(cid.split(":")[1])
        if interaction.user.id != OWNER_ID:
            await interaction.response.send_message("Décision réservée à l'owner du bot.", ephemeral=True)
            return
        session = self._sessions.get(raid_id)
        # Deux contextes : wipe EN DIRECT (session vivante) ou RAPPEL de permadéath (session déjà close).
        if session is not None:
            fallen = [(c.character_id, c.name) for c in session.combatants.values() if not c.alive]
        else:
            fallen = [(p["character_id"],
                       (get_character(p["character_id"])["character_name"]
                        if get_character(p["character_id"]) else f"#{p['character_id']}"))
                      for p in db.raid_permadeath_by_raid(raid_id)]
        if mode == "all":
            for cid_, _nom in fallen:
                await self._revive_any(raid_id, cid_)
            await interaction.response.edit_message(
                content="✅ Tous les participants concernés ont été sauvés.", embed=None, view=None)
            await self._check_completion(raid_id)
        elif mode == "none":
            if session is not None:
                for cid_, _nom in fallen:
                    db.raid_permadeath_add(cid_, raid_id, _now())
                self._sessions.pop(raid_id, None)
            await interaction.response.edit_message(
                content="❌ Aucune intervention. Les tombés restent en attente de permadéath (10 jours).",
                embed=None, view=None)
        else:  # some -> menu de sélection multiple
            if not fallen:
                await interaction.response.edit_message(content="Aucun participant concerné.", embed=None, view=None)
                return
            options = [discord.SelectOption(label=nom[:100], value=str(cid_)) for cid_, nom in fallen[:25]]
            sel = discord.ui.Select(placeholder="Choisis qui sauver…", min_values=1,
                                    max_values=len(options), options=options,
                                    custom_id=f"raid_wipe_pick:{raid_id}")
            view = discord.ui.View(timeout=None)
            view.add_item(sel)
            await interaction.response.edit_message(
                content="🎯 Sélectionne les participants à sauver (les autres partiront en permadéath).",
                embed=None, view=view)

    async def _handle_wipe_pick(self, interaction, cid):
        raid_id = int(cid.split(":")[1])
        if interaction.user.id != OWNER_ID:
            await interaction.response.send_message("Décision réservée à l'owner du bot.", ephemeral=True)
            return
        chosen = set(int(v) for v in (interaction.data.get("values") or []))
        session = self._sessions.get(raid_id)
        if session is not None:
            fallen_ids = [c.character_id for c in session.combatants.values() if not c.alive]
        else:
            fallen_ids = [p["character_id"] for p in db.raid_permadeath_by_raid(raid_id)]
        for cid_ in fallen_ids:
            if cid_ in chosen:
                await self._revive_any(raid_id, cid_)
            elif session is not None:
                db.raid_permadeath_add(cid_, raid_id, _now())
            # (contexte rappel : les non-choisis restent simplement dans raid_permadeath_pending)
        if session is None:
            self._sessions.pop(raid_id, None)
        await interaction.response.edit_message(
            content=f"✅ {len(chosen)} sauvé(s). Les autres partent en permadéath (10 jours).", view=None)
        await self._check_completion(raid_id)

    async def _revive_any(self, raid_id, character_id):
        """Sauvetage unifié : en combat (session vivante) -> _revive + reprise ; sinon (rappel permadéath)
        -> restaure les PV du personnage et le retire de la file de permadéath."""
        db.raid_permadeath_remove(character_id)
        session = self._sessions.get(raid_id)
        if session is not None and character_id in session.combatants:
            await self._revive(raid_id, session.combatants[character_id])
            return
        profile = db.get_or_create_profile(character_id)
        db.update_profile(character_id, pv_actuel=profile["pv_max"])

    async def _revive(self, raid_id, comb):
        """Restaure un participant tombé (PV pleins) et relance son fil s'il lui reste des monstres."""
        comb.alive = True
        comb.pv = comb.pv_max
        db.update_profile(comb.character_id, pv_actuel=comb.pv_max)
        db.raid_update_participant(comb.participant_id, is_alive=1)
        if comb.current is not None:
            if comb.thread is not None:
                try:
                    await comb.thread.edit(archived=False, locked=False)
                except (discord.HTTPException, AttributeError):
                    comb.thread = None
            if comb.thread is None:
                session = self._sessions.get(raid_id)
                if session is not None:
                    comb.thread = await self._create_thread(
                        session.raid_channel, f"RAID {comb.role_slot}")
                    db.raid_update_participant(
                        comb.participant_id, thread_id=(comb.thread.id if comb.thread else None))
            asyncio.create_task(self._run_thread(raid_id, comb.character_id))
        else:
            comb.finished = True

    # =================================================================
    # §8 : TÂCHE — RAPPELS / PERMADÉATH (24h)
    # =================================================================
    @tasks.loop(hours=24)
    async def raid_permadeath_check(self):
        from cogs.depart import delete_character_cascade  # import local : évite tout cycle au chargement
        now = datetime.utcnow()
        for pending in db.raid_permadeath_all():
            started = _parse(pending["started_at"])
            if started is None:
                continue
            jours = (now - started).days
            char = get_character(pending["character_id"])
            nom = (char["character_name"] if char else None) or f"#{pending['character_id']}"
            if jours >= 10:
                # Suppression DÉFINITIVE et immédiate (jamais via la réserve de 15 jours). Même pattern
                # que les autres appelants : cascade des données liées PUIS suppression du personnage.
                delete_character_cascade(pending["character_id"])
                with db.get_connection() as conn:
                    conn.execute("DELETE FROM validated_characters WHERE id = ?", (pending["character_id"],))
                db.raid_permadeath_remove(pending["character_id"])
                await self._dm_user(
                    OWNER_ID,
                    content=f"💀 **{nom}** est mort définitivement (permadéath de raid, 10 jours écoulés).")
                continue
            last = _parse(pending["last_reminder_at"])
            if last is None or (now - last) >= timedelta(hours=24):
                view = discord.ui.View(timeout=None)
                view.add_item(discord.ui.Button(
                    label="Sauver tout le monde", emoji="✅", style=discord.ButtonStyle.success,
                    custom_id=f"raid_wipe_all:{pending['raid_id']}"))
                view.add_item(discord.ui.Button(
                    label="Sauver certains", emoji="🎯", style=discord.ButtonStyle.primary,
                    custom_id=f"raid_wipe_some:{pending['raid_id']}"))
                view.add_item(discord.ui.Button(
                    label="Ne pas intervenir", emoji="❌", style=discord.ButtonStyle.danger,
                    custom_id=f"raid_wipe_none:{pending['raid_id']}"))
                await self._dm_user(
                    OWNER_ID,
                    content=f"⏳ **{nom}** en attente depuis {jours}/10 jours. Sauver maintenant ou laisser mourir ?",
                    view=view)
                db.raid_permadeath_set_reminder(pending["character_id"], now.isoformat())

    @raid_permadeath_check.before_loop
    async def _before_permadeath(self):
        await self.bot.wait_until_ready()

    # ---------- rôles / fils utilitaires ----------
    async def _swap_role(self, comb, target):
        """Retire le rôle du slot de `comb` et lui attribue le rôle du slot de `target` (réel/virtuel)."""
        if comb.char is None:
            return
        old_role = await self.get_or_create_raid_role(comb.thread.guild if comb.thread else target.thread.guild,
                                                      comb.role_slot)
        new_role = await self.get_or_create_raid_role(target.thread.guild if target.thread else comb.thread.guild,
                                                      target.role_slot)
        guild = self.bot.get_guild(self._sessions_guild_id(comb))
        if guild is None:
            return
        if old_role is not None:
            await _remove_role_real_or_virtual(guild, comb.char, old_role.id, "Réorganisation de raid")
        if new_role is not None:
            await _add_role_real_or_virtual(guild, comb.char, new_role.id, "Réorganisation de raid")

    def _sessions_guild_id(self, comb):
        for session in self._sessions.values():
            if comb.character_id in session.combatants:
                return session.guild.id
        return None

    async def _archive_thread(self, comb):
        await self._archive_thread_obj(comb.thread if comb else None)

    async def _archive_thread_obj(self, thread):
        if thread is None:
            return
        try:
            await thread.edit(archived=True, locked=True)
        except (discord.HTTPException, AttributeError):
            pass

    # ---------- §3.7 : embed de suivi global ----------
    async def _update_global_tracker(self, raid_id):
        session = self._sessions.get(raid_id)
        if session is None:
            return
        lignes = []
        for c in sorted(session.combatants.values(), key=lambda x: x.role_slot):
            if not c.alive:
                lignes.append(f"💀 **{c.name}** — hors combat")
                continue
            reste = (1 if c.current else 0) + len(c.queue)
            lignes.append(
                f"• **{c.name}** — PV {max(c.pv, 0):,} / {c.pv_max:,} · EO {max(c.eo, 0):,} · "
                f"crit {c.crit_chance}% · monstres restants : {reste}".replace(",", " "))
        if session.boss is not None:
            b = session.boss
            lignes.append(
                f"\n👹 **{b['name']}** — PV {max(b['pv'], 0):,} / {b['pv_max']:,} · "
                f"EO {max(b['eo'], 0):,} / {b['eo_max']:,}".replace(",", " "))
        embed = discord.Embed(title="📊 Suivi du raid", description="\n".join(lignes) or "—",
                              color=RAID_COLOR)
        try:
            if session.tracker_msg is None:
                session.tracker_msg = await session.raid_channel.send(embed=embed)
            else:
                await session.tracker_msg.edit(embed=embed)
        except (discord.HTTPException, AttributeError):
            pass

    # =================================================================
    # GÉNÉRATION DU RAID
    # =================================================================
    async def _trigger_raid(self, guild_id):
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return
        channel_id = random.choice(RAID_CHANNELS)
        classe = weighted_pick(RAID_CLASSE_WEIGHTS)
        stones = distribute_stones(classe)
        monster_count = random.randint(*RAID_MONSTER_COUNT[classe])

        # Propriétaire du salon (système /ordre) : ligne 'Acheté'/'Location' faisant foi.
        owner_row = db.resolve_salon_true_owner(channel_id)
        ordre_id = owner_row["order_id"] if owner_row else None
        status = "attente_reponse" if ordre_id else "ouvert"
        chief_deadline = (datetime.utcnow() + timedelta(hours=2)).isoformat() if ordre_id else None

        raid_id = db.raid_create_instance(
            guild_id, channel_id, classe, json.dumps(stones), monster_count,
            status, ordre_id, chief_deadline, _now())

        # EMBED 1 : dans le salon où le raid apparaît.
        raid_channel = self.bot.get_channel(channel_id)
        if raid_channel is not None:
            try:
                await raid_channel.send(embed=discord.Embed(
                    title="🚨 Fracture occulte",
                    description=(f"Une fracture occulte s'est ouverte ici. **Classe {classe}**. "
                                 f"**{monster_count}** entités détectées. Une intervention est requise."),
                    color=RAID_COLOR))
            except discord.HTTPException:
                pass

        announce_channel = self.bot.get_channel(RAID_ANNOUNCE_CHANNEL_ID)
        raid = db.raid_get_instance(raid_id)
        embed = self._build_announce_embed(raid)

        if ordre_id:
            # MP au chef : 2h pour répondre avant relais du Gouvernement.
            await self._dm_chef(guild, ordre_id, embed=discord.Embed(
                title="⚠️ Raid dans votre salon",
                description=(f"Un raid **Classe {classe}** est apparu dans votre salon <#{channel_id}>. "
                             "Vous avez **2h** pour répondre avant que le Gouvernement ne prenne le relais."),
                color=RAID_COLOR))
            chef_mention = await self._chef_mention(guild, ordre_id)
            content = chef_mention or f"<@&{RAID_MANAGER_ROLE_ID}>"
        else:
            content = f"<@&{RAID_MANAGER_ROLE_ID}>"

        if announce_channel is not None:
            try:
                msg = await announce_channel.send(content=content, embed=embed,
                                                  view=self._participation_view(raid))
                db.raid_set_instance_announce_msg(raid_id, msg.id)
            except discord.HTTPException:
                pass

    def _suggested_effectif(self, classe):
        """(borne_basse, borne_haute) de l'effectif suggéré pour cette classe. La borne haute déclenche
        la fermeture immédiate de la participation. TODO Phase 2/3 : basculer sur la puissance réelle
        (somme des burst_power des participants) face à la classe."""
        return RAID_EFFECTIF_SUGGERE[classe]

    def _participants_section(self, raid_id):
        """Section « 👥 Participants actuels » de l'EMBED 2 (recalculée à chaque changement)."""
        parts = db.raid_get_participants(raid_id)
        if not parts:
            return "👥 **PARTICIPANTS**\nAucun participant pour l'instant."
        lignes = []
        chief_txt = "—"
        for p in parts:
            char = get_character(p["character_id"])
            nom = (char["character_name"] if char else None) or f"#{p['character_id']}"
            if p["is_raid_chief"]:
                chief_txt = f"<@{p['user_id']}> ({nom})"
                lignes.append(f"• 👑 **{nom}** (chef du raid)")
            else:
                lignes.append(f"• {nom}")
        return "👥 **PARTICIPANTS ACTUELS**\n" + "\n".join(lignes) + f"\nChef du raid : {chief_txt}"

    def _build_announce_embed(self, raid):
        """EMBED 2 (annonce officielle) : 3 catégories 📍 SITUATION / 💎 BUTIN & EFFECTIF / 🏆 RÉCOMPENSES
        + 👥 PARTICIPANTS, couleur rouge, GIF en pied de page. Récompenses = RAID_REWARDS[classe] (XP /
        stats à répartir / coffres uniquement). MVP +1,5% : TODO Phase 4-5 (non fonctionnel ici)."""
        classe = raid["classe"]
        monster_count = raid["monster_count"]
        channel_id = raid["channel_id"]
        stones = json.loads(raid["stones_json"] or "{}")
        r = RAID_REWARDS[classe]
        prix = RAID_STONE_PRICE[classe]
        eff_lo, eff_hi = self._suggested_effectif(classe)
        coffres = ", ".join(RAID_LABELS_COFFRE[c] for c in r["coffres"])
        sep = "━━━━━━━━━━━━━━━━━━━━"
        reserve = ""
        if raid["ordre_id"] and not raid["is_public"]:
            reserve = "\n🔒 Raid réservé à l'Ordre propriétaire du salon (non public)."
        desc = (
            f"📍 **SITUATION**\n"
            f"Salon touché : <#{channel_id}>\n"
            f"Classe du raid : **{classe}**\n"
            f"Entités détectées : **{monster_count}**{reserve}\n\n"
            f"{sep}\n"
            f"💎 **BUTIN & EFFECTIF**\n"
            f"Pierres occultes : {_stones_text(stones)}\n"
            f"Valeur estimée d'une pierre : {_fmt(prix[0])} – {_fmt(prix[1])} ¥\n"
            f"Effectif suggéré : **{eff_lo} à {eff_hi}** participant(s).\n\n"
            f"{sep}\n"
            f"🏆 **RÉCOMPENSES**\n"
            f"XP : {_fmt(r['xp'][0])} – {_fmt(r['xp'][1])}\n"
            f"Points à répartir : {_fmt(r['stats_libre'][0])} – {_fmt(r['stats_libre'][1])}\n"
            f"Coffres possibles : {coffres}\n\n"  # TODO Phase 4-5 : bonus MVP +1,5%
            f"{sep}\n"
            f"{self._participants_section(raid['id'])}"
        )
        embed = discord.Embed(title="🚨 Alerte — Fracture occulte détectée", description=desc,
                              color=RAID_COLOR)
        embed.set_footer(text="Intervention requise — Gouvernement des Exorcistes")
        embed.set_image(url=RAID_GIF_URL)
        return embed

    def _participation_view(self, raid):
        """Boutons sous l'EMBED 2 : « ⚔️ Participer » (+ « 🌍 Mettre en public » si salon d'Ordre)."""
        view = discord.ui.View(timeout=None)
        view.add_item(discord.ui.Button(
            label="Participer", emoji="⚔️", style=discord.ButtonStyle.success,
            custom_id=f"raid_join:{raid['id']}"))
        if raid["ordre_id"]:
            view.add_item(discord.ui.Button(
                label="Mettre en public", emoji="🌍", style=discord.ButtonStyle.secondary,
                custom_id=f"raid_public:{raid['id']}"))
        return view

    async def _refresh_announce(self, raid_id):
        """Réédite l'EMBED 2 (participants à jour) sur le message d'annonce."""
        raid = db.raid_get_instance(raid_id)
        if raid is None or not raid["announce_message_id"]:
            return
        channel = self.bot.get_channel(RAID_ANNOUNCE_CHANNEL_ID)
        if channel is None:
            return
        try:
            msg = await channel.fetch_message(raid["announce_message_id"])
            await msg.edit(embed=self._build_announce_embed(raid))
        except discord.HTTPException:
            pass

    # =================================================================
    # AMENDE (déclenchée par la fermeture sans réponse — Phase 2)
    # =================================================================
    def _ordre_a_puissance_suffisante(self, raid) -> bool:
        """TODO Phase 2/3 (FONCTION COMMUNE À CONSTRUIRE ENSEMBLE) : compare la somme des burst_power réels
        des membres de l'Ordre au seuil recommandé pour la classe du raid. Partagée avec l'effectif suggéré
        et le calcul de participation. Placeholder Phase 1 : considère l'Ordre comme capable (True) afin que
        l'amende s'applique bien à un Ordre qui « pouvait mais n'a rien fait » une fois la Phase 2 branchée."""
        return True

    async def _raid_check_and_apply_amende(self, raid_id):
        """Section 7 : à appeler à la fermeture d'un raid sans réponse (branché en Phase 2). Applique une
        amende à l'Ordre propriétaire s'il avait la puissance suffisante mais n'a rien fait."""
        raid = db.raid_get_instance(raid_id)
        if raid is None or raid["ordre_id"] is None:
            return  # salon libre : aucun Ordre à sanctionner
        if not self._ordre_a_puissance_suffisante(raid):
            return  # puissance insuffisante : aucune amende
        montant = random.randint(*RAID_AMENDE[raid["classe"]])
        amende_id = db.raid_create_amende(raid_id, raid["ordre_id"], raid["classe"], montant, _now())
        guild = self.bot.get_guild(raid["guild_id"])
        await self._dm_chef(
            guild, raid["ordre_id"],
            embed=discord.Embed(
                title="⚠️ Amende — Non-intervention",
                description=(f"Pour avoir pénalisé le Gouvernement en ne s'occupant pas du raid "
                             f"**Classe {raid['classe']}** dans votre salon, une amende de "
                             f"**{_fmt(montant)} ¥** vous est due."),
                color=RAID_COLOR),
            view=self._pay_view(amende_id))

    def _pay_view(self, amende_id):
        view = discord.ui.View(timeout=None)
        view.add_item(discord.ui.Button(
            label="Payer", emoji="💳", style=discord.ButtonStyle.success,
            custom_id=f"raid_pay:{amende_id}"))
        return view

    # =================================================================
    # RAPPELS + PÉNALITÉS JOURNALIÈRES + SAISIE (tâche quotidienne)
    # =================================================================
    @tasks.loop(hours=24)
    async def raid_amende_daily_check(self):
        now = datetime.utcnow()
        for amende in db.raid_get_unpaid_amendes():
            created = _parse(amende["created_at"])
            if created is None:
                continue
            jours = (now - created).days
            classe = amende["classe"] or "4"
            # Pénalité journalière (à partir du 3e jour).
            if jours >= RAID_AMENDE_DELAI_PENALITE_JOURS:
                db.raid_add_amende_penalite(amende["id"], RAID_AMENDE_PENALITE_JOURNALIERE.get(classe, 0))
            raid = db.raid_get_instance(amende["raid_id"])
            guild = self.bot.get_guild(raid["guild_id"]) if raid else None
            if jours >= RAID_AMENDE_DELAI_SAISIE_JOURS:
                # Saisie du salon (une seule fois : évite de re-saisir/re-DM chaque jour).
                if raid is not None and not db.raid_blacklist_exists_for_amende(amende["id"]):
                    await self._seize_salon(guild, raid, amende)
            else:
                # Rappel quotidien standard, avec le montant_du à jour + bouton Payer.
                fresh = db.raid_get_amende(amende["id"])
                if raid is not None:
                    await self._dm_chef(
                        guild, amende["ordre_id"],
                        embed=discord.Embed(
                            title="💳 Amende de raid impayée",
                            description=(f"Rappel : une amende de **{_fmt(fresh['montant_du'])} ¥** reste "
                                         f"due (raid Classe {classe} dans <#{raid['channel_id']}>)."),
                            color=RAID_COLOR),
                        view=self._pay_view(amende["id"]))
            db.raid_set_amende_reminder(amende["id"], now.isoformat())

    @raid_amende_daily_check.before_loop
    async def _before_amende(self):
        await self.bot.wait_until_ready()

    async def _seize_salon(self, guild, raid, amende):
        fresh = db.raid_get_amende(amende["id"])
        chef_cid = None
        ordre = db.get_order(amende["ordre_id"])
        if ordre is not None:
            chef_cid = ordre["chef_character_id"]
        # Retire la propriété du salon (redevient libre) puis inscrit la blacklist.
        db.raid_salon_seize(raid["channel_id"])
        db.raid_add_salon_blacklist(raid["channel_id"], amende["ordre_id"], chef_cid, amende["id"], _now())
        await self._dm_chef(
            guild, amende["ordre_id"],
            embed=discord.Embed(
                title="🔒 Salon saisi",
                description=(
                    f"L'amende de **{_fmt(fresh['montant_du'])} ¥** n'a jamais été payée après "
                    f"{RAID_AMENDE_DELAI_SAISIE_JOURS} jours. Le salon <#{raid['channel_id']}> a été repris "
                    "par le Gouvernement et retiré de votre Ordre. Votre Ordre ne pourra plus jamais le "
                    f"racheter tant que cette dette (**{_fmt(fresh['montant_du'])} ¥**) n'est pas réglée "
                    "intégralement — y compris par un rachat indirect via un autre Ordre, qui reste interdit "
                    "dans tous les cas."),
                color=RAID_COLOR),
            view=self._pay_view(amende["id"]))

    # =================================================================
    # BOUTON « 💳 Payer » (persistant)
    # =================================================================
    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        if interaction.type != discord.InteractionType.component:
            return
        cid = interaction.data.get("custom_id", "")
        if cid.startswith("raid_pay:"):
            await self._handle_pay(interaction, cid)
        elif cid.startswith("raid_join:"):
            await self._handle_join(interaction, cid)
        elif cid.startswith("raid_public:"):
            await self._handle_public(interaction, cid)
        elif cid.startswith("raid_approve:"):
            await self._handle_approval(interaction, cid, accepted=True)
        elif cid.startswith("raid_refuse:"):
            await self._handle_approval(interaction, cid, accepted=False)
        elif cid.startswith("raid_wipe_all:"):
            await self._handle_wipe_decision(interaction, cid, mode="all")
        elif cid.startswith("raid_wipe_some:"):
            await self._handle_wipe_decision(interaction, cid, mode="some")
        elif cid.startswith("raid_wipe_none:"):
            await self._handle_wipe_decision(interaction, cid, mode="none")
        elif cid.startswith("raid_wipe_pick:"):
            await self._handle_wipe_pick(interaction, cid)

    async def _handle_pay(self, interaction, cid):
        amende_id = int(cid.split(":")[1])
        amende = db.raid_get_amende(amende_id)
        if amende is None:
            await interaction.response.send_message("Cette amende n'existe plus.", ephemeral=True)
            return
        if amende["paye"]:
            await interaction.response.send_message("✅ Cette amende est déjà réglée.", ephemeral=True)
            return
        ordre = db.get_order(amende["ordre_id"])
        if ordre is None:
            await interaction.response.send_message("L'Ordre concerné n'existe plus.", ephemeral=True)
            return
        # Seul le chef de l'Ordre débiteur peut payer (revérif au clic).
        chef = get_character(ordre["chef_character_id"])
        if chef is None or chef["user_id"] != interaction.user.id:
            await interaction.response.send_message(
                "Seul le chef de l'Ordre concerné peut régler cette amende.", ephemeral=True)
            return
        montant = amende["montant_du"]
        if ordre["solde_courant"] < montant:
            await interaction.response.send_message(
                f"❌ Le Trésor de l'Ordre ({_fmt(ordre['solde_courant'])} ¥) ne couvre pas l'amende "
                f"({_fmt(montant)} ¥). Le bouton reste actif : réessaie plus tard.", ephemeral=True)
            return
        # Prélèvement + clôture + déblocage de tout salon lié à cette amende.
        db.adjust_order_solde(amende["ordre_id"], -montant)
        db.add_order_transaction(amende["ordre_id"], "Amende de raid (non-intervention)", -montant, _now())
        db.raid_set_amende_paid(amende_id)
        db.raid_remove_salon_blacklist_by_amende(amende_id)
        try:
            await interaction.response.edit_message(
                content=f"✅ Amende de **{_fmt(montant)} ¥** réglée. Blocage éventuel levé.", view=None)
        except discord.HTTPException:
            await interaction.response.send_message(
                f"✅ Amende de **{_fmt(montant)} ¥** réglée. Blocage éventuel levé.", ephemeral=True)

    # =================================================================
    # PHASE 2 : PARTICIPATION (boutons Participer / Mettre en public / Accepter-Refuser)
    # =================================================================
    def _order_chef_uid(self, order_id):
        o = db.get_order(order_id)
        if not o:
            return None
        c = get_character(o["chef_character_id"])
        return c["user_id"] if c else None

    def _is_in_order(self, character_id, order_id) -> bool:
        o = db.get_character_order(character_id)
        return o is not None and o["id"] == order_id

    async def _select_character(self, interaction, user):
        """Sélection du personnage participant (1 seul par joueur). Répond TOUJOURS à l'interaction (defer
        ou prompt ephemeral) : les messages suivants passent par interaction.followup. Retourne le
        character_id, ou None (aucun perso / annulé)."""
        chars = get_characters(user.id, interaction.guild.id) if interaction.guild else []
        if not chars:
            await interaction.response.send_message("❌ Tu n'as aucun personnage validé.", ephemeral=True)
            return None
        if len(chars) == 1:
            await interaction.response.defer(ephemeral=True)
            return chars[0]["id"]
        view = _OwnerChoiceView(user.id, [
            (str(c["id"]), (c["character_name"] or f"#{c['id']}")[:80], None, discord.ButtonStyle.secondary)
            for c in chars[:3]])
        await interaction.response.send_message(
            "Avec quel personnage veux-tu participer ?", view=view, ephemeral=True)
        await view.wait()
        return int(view.result) if view.result else None

    async def _handle_join(self, interaction, cid):
        raid_id = int(cid.split(":")[1])
        raid = db.raid_get_instance(raid_id)
        if raid is None or raid["status"] not in ("attente_reponse", "ouvert"):
            await interaction.response.send_message(
                "La participation à ce raid est terminée.", ephemeral=True)
            return
        user = interaction.user
        character_id = await self._select_character(interaction, user)
        if character_id is None:
            return
        # Salon d'Ordre non public : réservé aux membres de l'Ordre propriétaire (prioritaire).
        if raid["ordre_id"] and not raid["is_public"] and not self._is_in_order(character_id, raid["ordre_id"]):
            owner = db.get_order(raid["ordre_id"])
            nom = owner["name"] if owner else "?"
            chef_mention = await self._chef_mention(interaction.guild, raid["ordre_id"]) or "son chef"
            await interaction.followup.send(
                f"❌ Ce raid est apparu dans un salon détenu par l'Ordre **{nom}**, dirigé par {chef_mention}. "
                f"Cet Ordre est prioritaire. Demande à {chef_mention} de rendre ce raid public si tu veux y "
                "participer.", ephemeral=True)
            return
        await self._do_join(interaction, raid, character_id, user)

    async def _do_join(self, interaction, raid, character_id, user):
        """Logique commune (salon libre / public / membre prioritaire) : chef du raid = premier cliqueur,
        auto-join des membres d'un Ordre déjà accepté ou de l'Ordre propriétaire, sinon demande au chef."""
        raid_id = raid["id"]
        if db.raid_participant_exists(raid_id, character_id):
            await interaction.followup.send("Tu participes déjà à ce raid.", ephemeral=True)
            return
        char_order = db.get_character_order(character_id)
        char_order_id = char_order["id"] if char_order else None

        # Première participation valide : annule le fallback des 2h (statut -> 'ouvert').
        if raid["status"] == "attente_reponse":
            db.raid_set_instance_status(raid_id, "ouvert")

        participants = db.raid_get_participants(raid_id)
        if not participants:
            # 1er cliqueur = chef du raid.
            db.raid_add_participant(raid_id, character_id, user.id, char_order_id, 1, 1, _now())
            await interaction.followup.send("👑 Tu es le **chef du raid** !", ephemeral=True)
            await self._maybe_bring_members(interaction, raid_id, character_id, user, char_order)
            await self._refresh_announce(raid_id)
            return

        # Auto-join : membre de l'Ordre propriétaire (toujours prioritaire) OU membre d'un Ordre dont le
        # chef est déjà accepté. Sinon : demande d'approbation au chef du raid.
        owning = raid["ordre_id"] is not None and char_order_id == raid["ordre_id"]
        chef_accepted = db.raid_is_ordre_chief_accepted(raid_id, char_order_id) if char_order_id else False
        if owning or chef_accepted:
            if char_order_id and db.raid_count_participants_for_ordre(raid_id, char_order_id) >= 4:
                await interaction.followup.send(
                    "Ton Ordre a déjà le maximum de participants (4).", ephemeral=True)
                return
            db.raid_add_participant(raid_id, character_id, user.id, char_order_id,
                                    len(participants) + 1, 0, _now())
            await interaction.followup.send("✅ Tu as rejoint le raid !", ephemeral=True)
            await self._maybe_bring_members(interaction, raid_id, character_id, user, char_order)
            await self._refresh_announce(raid_id)
            return

        await self._request_chief_approval(interaction, raid_id, character_id, user)

    async def _maybe_bring_members(self, interaction, raid_id, character_id, user, char_order):
        """Si le participant est CHEF d'un Ordre : lui demander toi-même / tes membres / les deux, et
        ajouter jusqu'à 3 membres (cap total 4 par Ordre, chef inclus), sans approbation individuelle.
        Simplification assumée : le chef reste toujours participant (chef du raid ou membre déjà inscrit) ;
        « mes membres » ajoute les membres EN PLUS de lui, « les deux » est équivalent ici."""
        if char_order is None or char_order["chef_character_id"] != character_id:
            return
        members = db.get_order_members(char_order["id"])
        if not members:
            return
        view = _OwnerChoiceView(user.id, [
            ("self", "Moi-même", "🧍", discord.ButtonStyle.secondary),
            ("members", "Mes membres", "👥", discord.ButtonStyle.primary),
            ("both", "Les deux", "⚔️", discord.ButtonStyle.success)])
        await interaction.followup.send(
            "Veux-tu participer toi-même, faire participer tes membres, ou les deux ?",
            view=view, ephemeral=True)
        await view.wait()
        mode = view.result or "self"
        if mode == "self":
            return
        order_id = char_order["id"]
        added = 0
        for m in members:
            if db.raid_count_participants_for_ordre(raid_id, order_id) >= 4:
                break
            if db.raid_participant_exists(raid_id, m["character_id"]):
                continue
            n = db.raid_count_participants(raid_id)
            db.raid_add_participant(raid_id, m["character_id"], m["user_id"], order_id, n + 1, 0, _now())
            added += 1
        await interaction.followup.send(
            f"👥 {added} membre(s) de ton Ordre ont rejoint le raid.", ephemeral=True)

    async def _request_chief_approval(self, interaction, raid_id, character_id, user):
        chief = db.raid_get_chief(raid_id)
        char = get_character(character_id)
        nom = (char["character_name"] if char else None) or f"#{character_id}"
        if chief is None:
            await interaction.followup.send("Impossible de trouver le chef du raid.", ephemeral=True)
            return
        view = discord.ui.View(timeout=None)
        view.add_item(discord.ui.Button(
            label="Accepter", emoji="✅", style=discord.ButtonStyle.success,
            custom_id=f"raid_approve:{raid_id}:{character_id}"))
        view.add_item(discord.ui.Button(
            label="Refuser", emoji="❌", style=discord.ButtonStyle.danger,
            custom_id=f"raid_refuse:{raid_id}:{character_id}"))
        sent = await self._dm_user(
            chief["user_id"],
            content=f"🔔 <@{user.id}> (**{nom}**) souhaite participer au raid. Accepter / Refuser ?",
            view=view)
        if sent:
            await interaction.followup.send(
                "📨 Demande envoyée au chef du raid, en attente de sa réponse.", ephemeral=True)
        else:
            await interaction.followup.send(
                "❌ Impossible de contacter le chef du raid.", ephemeral=True)

    async def _handle_approval(self, interaction, cid, accepted):
        _, raid_id, req_cid = cid.split(":")
        raid_id, req_cid = int(raid_id), int(req_cid)
        raid = db.raid_get_instance(raid_id)
        chief = db.raid_get_chief(raid_id)
        if chief is None or interaction.user.id != chief["user_id"]:
            await interaction.response.send_message(
                "Seul le chef du raid peut répondre à cette demande.", ephemeral=True)
            return
        char = get_character(req_cid)
        nom = (char["character_name"] if char else None) or f"#{req_cid}"
        requester_uid = char["user_id"] if char else None
        if raid is None or raid["status"] not in ("attente_reponse", "ouvert"):
            await interaction.response.edit_message(content="La participation est terminée.", view=None)
            return
        if db.raid_participant_exists(raid_id, req_cid):
            await interaction.response.edit_message(content=f"{nom} participe déjà.", view=None)
            return
        if not accepted:
            await interaction.response.edit_message(content=f"❌ {nom} a été refusé.", view=None)
            if requester_uid:
                await self._dm_user(requester_uid, content="❌ Ta demande de participation au raid a été refusée.")
            return
        req_order = db.get_character_order(req_cid)
        req_oid = req_order["id"] if req_order else None
        if req_oid and db.raid_count_participants_for_ordre(raid_id, req_oid) >= 4:
            await interaction.response.edit_message(
                content=f"L'Ordre de {nom} est déjà au maximum de participants (4).", view=None)
            return
        n = db.raid_count_participants(raid_id)
        db.raid_add_participant(raid_id, req_cid, requester_uid, req_oid, n + 1, 0, _now())
        await interaction.response.edit_message(content=f"✅ {nom} a été accepté dans le raid.", view=None)
        await self._refresh_announce(raid_id)
        if requester_uid:
            await self._dm_user(requester_uid, content="✅ Ta demande de participation au raid a été acceptée !")
        # TODO Phase 2 (nuance) : proposer le choix toi-même/membres à un chef d'Ordre accepté par ce
        # chemin d'approbation (ici, il rejoint seul ; l'auto-amenée de membres reste dispo via son propre
        # clic « Participer »).

    async def _handle_public(self, interaction, cid):
        raid_id = int(cid.split(":")[1])
        raid = db.raid_get_instance(raid_id)
        if raid is None or raid["status"] not in ("attente_reponse", "ouvert"):
            await interaction.response.send_message(
                "La participation à ce raid est terminée.", ephemeral=True)
            return
        if raid["ordre_id"] is None:
            await interaction.response.send_message("Ce raid est déjà public.", ephemeral=True)
            return
        if interaction.user.id != self._order_chef_uid(raid["ordre_id"]):
            await interaction.response.send_message(
                "❌ Seul le chef de cet Ordre peut rendre ce raid public.", ephemeral=True)
            return
        db.raid_set_instance_public(raid_id, 1)
        await interaction.response.send_message(
            "🌍 Raid rendu public : tout le monde peut désormais participer (les membres de ton Ordre "
            "restent prioritaires).", ephemeral=True)
        await self._refresh_announce(raid_id)

    async def _dm_user(self, user_id, content=None, view=None) -> bool:
        u = self.bot.get_user(user_id)
        if u is None:
            try:
                u = await self.bot.fetch_user(user_id)
            except discord.HTTPException:
                return False
        try:
            await u.send(content=content, view=view)
            return True
        except discord.HTTPException:
            return False

    # =================================================================
    # HELPERS DM CHEF
    # =================================================================
    async def _resolve_chef_member(self, guild, ordre_id):
        ordre = db.get_order(ordre_id)
        if ordre is None:
            return None
        chef = get_character(ordre["chef_character_id"])
        if chef is None:
            return None
        uid = chef["user_id"]
        member = guild.get_member(uid) if guild else None
        if member is None:
            try:
                member = await self.bot.fetch_user(uid)
            except discord.HTTPException:
                return None
        return member

    async def _chef_mention(self, guild, ordre_id):
        ordre = db.get_order(ordre_id)
        if ordre is None:
            return None
        chef = get_character(ordre["chef_character_id"])
        return f"<@{chef['user_id']}>" if chef else None

    async def _dm_chef(self, guild, ordre_id, embed=None, view=None, content=None):
        member = await self._resolve_chef_member(guild, ordre_id)
        if member is None:
            return
        try:
            await member.send(content=content, embed=embed, view=view)
        except discord.HTTPException:
            pass


async def setup(bot):
    await bot.add_cog(Raid(bot))
