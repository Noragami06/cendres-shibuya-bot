# /giveaway — Phase 1 : catalogue de récompenses numéroté + chaîne de modals + stockage temporaire.
# /giveaway — Phase 2 : lancement réel (pillow + bouton Participer), persistance en base, boucle de
# rafraîchissement toutes les 5 min, et mécanisme discret réservé à l'owner. Le tirage/distribution
# (avec apply_vip_booster_multiplier) arrivera en Phase 3 : ici _close_giveaway se contente de clôturer.

import asyncio
import json
import os
import random
import re
import uuid
from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands

from cogs.utils import database as db
from cogs.utils.image_gen import generate_giveaway_image
from cogs.utils.rewards import BOOSTER_ROLE_ID, VIP_ROLE_ID

FICHE_STAFF_ROLE_ID = 1521229332075512039
GIVEAWAY_MAX_REWARDS = 20  # garde-fou sur le nombre de récompenses d'un giveaway

# Salon FIXE où la pillow + le bouton Participer sont TOUJOURS postés (jamais le salon d'exécution).
GIVEAWAY_CHANNEL_ID = 1521562694891868180

# Owner du serveur : seul concerné par le mécanisme discret (§5). Jamais exposé publiquement.
OWNER_ID = 396615332346855428

# Délai de choix d'une récompense (claim), unique et réglable. 5400 s = 1h30.
GIVEAWAY_CLAIM_TIMEOUT_SECONDS = 5400
# Exclusion des participants du giveaway d'origine lors d'un reroll : "none" | "winners" | "all".
GIVEAWAY_REROLL_EXCLUSION_MODE = "none"
# Clé bot_state du heartbeat (date UTC écrite régulièrement tant que le bot tourne) : sert à GELER le
# chrono des claims pendant un arrêt du bot.
GIVEAWAY_HEARTBEAT_KEY = "giveaway_heartbeat"


def _fmt_claim_delay() -> str:
    """Délai de claim formaté façon « 1h30 » / « 45min » / « 2h » depuis la constante."""
    total_min = GIVEAWAY_CLAIM_TIMEOUT_SECONDS // 60
    h, m = divmod(total_min, 60)
    if h and m:
        return f"{h}h{m:02d}"
    if h:
        return f"{h}h"
    return f"{m}min"

# Dossier temporaire des images de giveaway (généré à la volée, nettoyé après envoi).
GIVEAWAY_IMG_DIR = os.path.join(os.path.dirname(__file__), "..", "temp", "giveaway_images")


def _giveaway_tmp_path() -> str:
    os.makedirs(GIVEAWAY_IMG_DIR, exist_ok=True)
    return os.path.join(GIVEAWAY_IMG_DIR, f"giveaway_{uuid.uuid4().hex}.png")


def _giveaway_file_path(giveaway_id: int) -> str:
    """Fichier temporaire DÉDIÉ à un giveaway (temp/giveaway_{id}.png) : évite les collisions entre
    rafraîchissements simultanés de plusieurs giveaways."""
    os.makedirs(GIVEAWAY_IMG_DIR, exist_ok=True)
    return os.path.join(GIVEAWAY_IMG_DIR, f"giveaway_{giveaway_id}.png")


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(value: str) -> datetime:
    """Parse un datetime ISO stocké en base ; garantit un objet timezone-aware (UTC par défaut)."""
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _fmt_hms(total_seconds) -> str:
    """Formate une durée en HH:MM:SS (jamais négative, heures non bornées à 24)."""
    s = max(0, int(total_seconds))
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def _get_user_characters(user_id: int, guild_id: int):
    """Personnages validés d'un joueur (lecture directe en base, 1 seul par participation)."""
    with db.get_connection() as conn:
        return conn.execute(
            "SELECT id, slot_number, character_name FROM validated_characters "
            "WHERE user_id = ? AND guild_id = ? ORDER BY slot_number",
            (user_id, guild_id)).fetchall()

# Récompenses « fixes » (non-objets), dans l'ordre imposé. 'key' sert à la Phase 2 (distribution).
GIVEAWAY_FIXED_REWARDS = [
    ("xp", "XP"),
    ("points", "Points de stats (à répartir librement)"),
    ("argent", "Argent (¥)"),
    ("stats_force", "Stats Force (fixe)"),
    ("stats_vitesse", "Stats Vitesse (fixe)"),
    ("stats_endurance", "Stats Endurance (fixe)"),
    ("mastery_arme", "Stats Arme Maudite (niveaux de Maîtrise)"),
    ("mastery_rct", "Stats RCT (niveaux de Maîtrise)"),
    ("mastery_territoire", "Stats Territoire (niveaux de Maîtrise)"),
    ("mastery_sort", "Stats Sort (niveaux de Maîtrise)"),
    ("mastery_eo", "Stats Énergie Occulte (niveaux de Maîtrise)"),
]
# Ordre d'affichage des objets réels (par catégorie), à la suite des 11 fixes.
GIVEAWAY_ITEM_CATEGORY_ORDER = ["Potion", "Arme maudite", "Relique", "Parchemin", "Token", "Coffre"]


def _is_staff(member) -> bool:
    return any(r.id == FICHE_STAFF_ROLE_ID for r in getattr(member, "roles", []))


def _get_catalog_items():
    """Tous les objets réels (item_definitions), triés par catégorie (ordre imposé) puis nom. Récupéré
    EN BASE (jamais codé en dur)."""
    with db.get_connection() as conn:
        rows = conn.execute(
            "SELECT d.id, d.name, s.name AS cat_name FROM item_definitions d "
            "LEFT JOIN shop_categories s ON s.id = d.categorie_id").fetchall()
    prio = {name.lower(): i for i, name in enumerate(GIVEAWAY_ITEM_CATEGORY_ORDER)}
    return sorted(rows, key=lambda r: (prio.get((r["cat_name"] or "").lower(), 999),
                                       (r["name"] or "").lower()))


def build_catalog():
    """Catalogue numéroté complet : 1-11 fixes, puis les objets réels à la suite. Retourne une liste de
    dicts {num, kind ('fixed'/'item'), nom, key?/item_id?, categorie?}."""
    catalog = []
    n = 1
    for key, label in GIVEAWAY_FIXED_REWARDS:
        catalog.append({"num": n, "kind": "fixed", "key": key, "nom": label})
        n += 1
    for row in _get_catalog_items():
        catalog.append({"num": n, "kind": "item", "item_id": row["id"], "nom": row["name"],
                        "categorie": row["cat_name"]})
        n += 1
    return catalog


def _catalog_embeds(catalog):
    """Découpe le catalogue en embeds (max 40 lignes chacun) pour un envoi en MP (un seul message,
    plusieurs embeds)."""
    lignes = [f"`{e['num']:>3}` — {e['nom']}" for e in catalog]
    embeds = []
    for i in range(0, len(lignes), 40):
        chunk = lignes[i:i + 40]
        embeds.append(discord.Embed(
            title="🎁 Catalogue des récompenses" + (f" ({i // 40 + 1})" if len(lignes) > 40 else ""),
            description="\n".join(chunk), color=discord.Color.purple()))
    embeds[-1].set_footer(text="Utilise ces numéros dans les modals de récompenses.")
    return embeds


def parse_mentions(text, guild):
    """Extrait (role_ids, user_ids) d'un texte libre : markup <@&id> / <@id> / <@!id>, ET IDs bruts
    (résolus via la guilde : rôle en priorité, sinon membre). Dé-doublonné, ordre préservé."""
    text = text or ""
    role_ids = [int(m) for m in re.findall(r"<@&(\d+)>", text)]
    user_ids = [int(m) for m in re.findall(r"<@!?(\d+)>", text)]
    consumed = set(role_ids) | set(user_ids)
    for m in re.findall(r"(?<![<@&!/\d])(\d{15,25})(?![\d>])", text):
        iid = int(m)
        if iid in consumed:
            continue
        if guild is not None and guild.get_role(iid) is not None:
            role_ids.append(iid)
        elif guild is not None and guild.get_member(iid) is not None:
            user_ids.append(iid)
    return list(dict.fromkeys(role_ids)), list(dict.fromkeys(user_ids))


def parse_duration_hours(text):
    """Parse 'J=24h, H=1h' cumulable (ex '2j12h' -> 60). Retourne un entier d'heures > 0, ou None."""
    t = (text or "").lower().replace(" ", "")
    jours = sum(int(x) for x in re.findall(r"(\d+)\s*j", t))
    heures = sum(int(x) for x in re.findall(r"(\d+)\s*h", t))
    total = jours * 24 + heures
    return total if total > 0 else None


def _fmt_duration(heures):
    j, h = divmod(heures, 24)
    if j and h:
        return f"{j}j{h}h ({heures}h)"
    if j:
        return f"{j}j ({heures}h)"
    return f"{h}h"


class _CharChoiceView(discord.ui.View):
    """Vue en session (le cliqueur est présent) pour choisir avec quel personnage participer.
    options : liste de (character_id, label). Premier clic du propriétaire -> result + stop."""

    def __init__(self, owner_id, options, timeout=120):
        super().__init__(timeout=timeout)
        self.owner_id = owner_id
        self.result = None
        for char_id, label in options:
            btn = discord.ui.Button(label=label[:80], style=discord.ButtonStyle.secondary)
            btn.callback = self._make_cb(char_id)
            self.add_item(btn)

    def _make_cb(self, char_id):
        async def cb(interaction: discord.Interaction):
            if interaction.user.id != self.owner_id:
                await interaction.response.send_message("Ce choix ne t'appartient pas.", ephemeral=True)
                return
            self.result = char_id
            try:
                await interaction.response.edit_message(
                    content="Personnage sélectionné.", view=None)
            except discord.HTTPException:
                pass
            self.stop()
        return cb


class _LaunchView(discord.ui.View):
    """Bouton « ✅ Lancer » ajouté au récapitulatif (Phase 2). custom_id porteur du staff_id : la
    persistance réelle est assurée par le listener on_interaction (brouillon en mémoire volatile)."""

    def __init__(self, staff_id):
        super().__init__(timeout=None)
        self.add_item(discord.ui.Button(
            label="Lancer", emoji="✅", style=discord.ButtonStyle.success,
            custom_id=f"giveaway_launch:{staff_id}"))


def _participate_view(giveaway_id: int) -> discord.ui.View:
    """Vue persistante du message public : « 🎉 Participer » + « 👥 Voir les participants » (staff)."""
    view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.Button(
        label="Participer", emoji="🎉", style=discord.ButtonStyle.success,
        custom_id=f"giveaway_join:{giveaway_id}"))
    view.add_item(discord.ui.Button(
        label="Voir les participants", emoji="👥", style=discord.ButtonStyle.secondary,
        custom_id=f"giveaway_view:{giveaway_id}"))
    return view


def _remove_members_view(giveaway_id: int) -> discord.ui.View:
    """Vue MP (staff) sous la liste des participants : bouton « 🗑️ Retirer des membres »."""
    view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.Button(
        label="Retirer des membres", emoji="🗑️", style=discord.ButtonStyle.danger,
        custom_id=f"giveaway_remove:{giveaway_id}"))
    return view


def _owner_decision_view(giveaway_id: int) -> discord.ui.View:
    """Vue DM discrète réservée à l'owner : Oui / Non (custom_id porteur de l'id et du choix)."""
    view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.Button(
        label="Oui", style=discord.ButtonStyle.success,
        custom_id=f"giveaway_owner:{giveaway_id}:oui"))
    view.add_item(discord.ui.Button(
        label="Non", style=discord.ButtonStyle.secondary,
        custom_id=f"giveaway_owner:{giveaway_id}:non"))
    return view


def _reroll_view(giveaway_id: int) -> discord.ui.View:
    """Vue MP (owner) : bouton « 🔁 Lancer le reroll » (custom_id porteur de l'id du giveaway rerollé)."""
    view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.Button(
        label="Lancer le reroll", emoji="🔁", style=discord.ButtonStyle.primary,
        custom_id=f"giveaway_reroll:{giveaway_id}"))
    return view


def _relaunch_claims_view(giveaway_id: int) -> discord.ui.View:
    """Vue MP owner (§2 cas C/D) : reroll + relancer les claims (custom_id persistants)."""
    view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.Button(
        label="Lancer le reroll", emoji="🔁", style=discord.ButtonStyle.primary,
        custom_id=f"giveaway_reroll:{giveaway_id}"))
    view.add_item(discord.ui.Button(
        label="Relancer les claims", emoji="🔄", style=discord.ButtonStyle.secondary,
        custom_id=f"giveaway_claimrelaunch:{giveaway_id}"))
    return view


class _GiveawayCancel(Exception):
    """Levée dès que le staff répond « annuler » pendant la configuration (flux Q/R en MP)."""


# =====================================================================
# COG
# =====================================================================
class Giveaway(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # Brouillons de configuration en cours, par staff : {staff_id: draft}. Mémoire volatile
        # (Phase 1) — le lancement réel et la persistance arriveront en Phase 2.
        self.drafts = {}
        # Tâches de distribution (Phase 4) en cours : on garde une référence forte pour qu'elles ne
        # soient pas ramassées par le GC tant qu'elles tournent.
        self._distribution_tasks = set()
        # Tâche de la boucle de rafraîchissement du pillow (Point 3).
        self._update_task = None
        # §5 : tâches de distribution persistante (reprise au boot, heartbeat, boucle d'échéances).
        self._claim_boot_task = None
        self._heartbeat_task = None
        self._deadline_task = None

    @app_commands.command(name="giveaway", description="Lance un giveaway (staff uniquement)")
    async def giveaway(self, interaction: discord.Interaction):
        if not _is_staff(interaction.user):
            await interaction.response.send_message("❌ Réservé au staff.", ephemeral=True)
            return
        channel = self.bot.get_channel(GIVEAWAY_CHANNEL_ID)
        if channel is None:
            await interaction.response.send_message(
                "❌ Salon de lancement des giveaways introuvable. Préviens un administrateur.",
                ephemeral=True)
            return
        catalog = build_catalog()
        # Le salon de lancement est TOUJOURS GIVEAWAY_CHANNEL_ID, jamais celui d'exécution.
        self.drafts[interaction.user.id] = {
            "catalog": catalog, "channel_id": GIVEAWAY_CHANNEL_ID,
            "guild_id": interaction.guild.id if interaction.guild else None}
        # 1) Catalogue en MP (un seul message, plusieurs embeds) : il reste affiché comme référence.
        try:
            dm = await interaction.user.create_dm()
            await dm.send(embeds=_catalog_embeds(catalog))
        except discord.HTTPException:
            await interaction.response.send_message(
                "❌ Je n'ai pas pu t'écrire en MP. Ouvre tes messages privés puis relance /giveaway.",
                ephemeral=True)
            self.drafts.pop(interaction.user.id, None)
            return
        await interaction.response.send_message(
            f"✅ Configuration en cours (voir tes MP), le giveaway sera lancé dans {channel.mention}.",
            ephemeral=True)
        # 2) Flux question/réponse en MP (sans timeout, « annuler » disponible partout). Lancé en tâche
        # de fond : la commande a déjà répondu, et le flux peut durer longtemps.
        task = asyncio.create_task(
            self._config_flow(interaction.user, dm, interaction.guild, interaction.user.id))
        self._distribution_tasks.add(task)
        task.add_done_callback(self._distribution_tasks.discard)

    # ---------- flux de configuration (questions/réponses en MP) ----------
    async def _safe_delete(self, *messages):
        for m in messages:
            if m is None:
                continue
            try:
                await m.delete()
            except discord.HTTPException:
                pass

    async def _ask(self, dm, user, question, validate):
        """Pose `question` en message texte, attend la réponse du staff (SANS timeout), la valide via
        `validate(texte, message) -> (ok, valeur, erreur)`. Redemande UNIQUEMENT cette question en cas
        d'échec (sans jamais repartir du début). Nettoie question + réponse (+ erreur) à chaque échange.
        « annuler » (insensible à la casse) interrompt tout via _GiveawayCancel."""
        def check(m):
            return m.channel.id == dm.id and m.author.id == user.id and not m.author.bot

        q_msg = await dm.send(question)
        while True:
            reply = await self.bot.wait_for("message", check=check)  # aucun timeout : temps libre
            texte = reply.content.strip()
            if texte.lower() == "annuler":
                await self._safe_delete(q_msg, reply)
                raise _GiveawayCancel()
            ok, valeur, erreur = validate(texte, reply)
            if ok:
                await self._safe_delete(q_msg, reply)
                return valeur
            # Échec : on supprime la mauvaise réponse + le message d'erreur, la question reste affichée.
            err_msg = await dm.send(f"❌ {erreur}")
            await self._safe_delete(reply, err_msg)

    async def _config_flow(self, user, dm, guild, staff_id):
        """Collecte toute la configuration en MP puis envoie le récap + bouton « ✅ Lancer »."""
        draft = self.drafts.get(staff_id)
        if draft is None:
            return
        catalog = draft["catalog"]

        def v_titre(t, m):
            return (True, t[:100], "") if t else (False, None, "Le titre ne peut pas être vide.")

        def v_entier_positif(t, m):
            if t.isdigit() and int(t) > 0:
                return True, int(t), ""
            return False, None, "Donne un entier positif."

        def v_nb_rewards(t, m):
            if t.isdigit() and 1 <= int(t) <= GIVEAWAY_MAX_REWARDS:
                return True, int(t), ""
            return False, None, f"Donne un entier entre 1 et {GIVEAWAY_MAX_REWARDS}."

        def v_exclusions(t, m):
            if t.lower() == "aucun":
                return True, {"role_ids": [], "user_ids": []}, ""
            roles, users = parse_mentions(t, guild)
            if not roles and not users:
                return False, None, "Mentionne un rôle et/ou des membres (ou écris « aucun »)."
            return True, {"role_ids": roles, "user_ids": users}, ""

        def v_role_requis(t, m):
            if t.lower() == "aucun":
                return True, [], ""
            roles, _ = parse_mentions(t, guild)
            if not roles:
                return False, None, "Mentionne au moins un rôle (ou écris « aucun »)."
            return True, roles, ""

        def v_duree(t, m):
            h = parse_duration_hours(t)
            if h is None:
                return False, None, "Durée invalide (ex : `2j`, `12h`, `3j12h`)."
            return True, h, ""

        def v_num_catalogue(t, m):
            if t.isdigit() and 1 <= int(t) <= len(catalog):
                return True, catalog[int(t) - 1], ""
            return False, None, f"Numéro invalide (catalogue : 1 à {len(catalog)})."

        try:
            titre = await self._ask(dm, user, "Quel est le titre du giveaway ?", v_titre)
            nb_gagnants = await self._ask(dm, user, "Combien de gagnants ?", v_entier_positif)
            exclusions = await self._ask(
                dm, user,
                "Veux-tu exclure des joueurs ? (mentionne un rôle et/ou des membres, ou écris « aucun »)",
                v_exclusions)
            roles_requis = await self._ask(
                dm, user,
                "Un rôle est-il requis pour participer ? (mentionne le rôle, ou écris « aucun »)",
                v_role_requis)
            duree_heures = await self._ask(
                dm, user, "Quelle est la durée ? (ex : 2j, 12h, 3j12h)", v_duree)
            nb_recompenses = await self._ask(
                dm, user, "Combien de récompenses à distribuer au total ?", v_nb_rewards)

            rewards = []
            for i in range(1, nb_recompenses + 1):
                entry = await self._ask(
                    dm, user, f"Récompense {i}/{nb_recompenses} — numéro du catalogue ?", v_num_catalogue)
                quantite = await self._ask(
                    dm, user, f"Récompense {i}/{nb_recompenses} — quantité ?", v_entier_positif)
                rewards.append({
                    "num": entry["num"], "nom": entry["nom"], "kind": entry["kind"],
                    "key": entry.get("key"), "item_id": entry.get("item_id"), "quantite": quantite})
        except _GiveawayCancel:
            self.drafts.pop(staff_id, None)
            try:
                await dm.send("❌ Création du giveaway annulée.")
            except discord.HTTPException:
                pass
            return

        # Toutes les réponses sont valides -> on complète le brouillon et on envoie le récap.
        draft.update({
            "titre": titre, "nb_gagnants": nb_gagnants, "exclusions": exclusions,
            "roles_requis": roles_requis, "duree_heures": duree_heures,
            "nb_recompenses": nb_recompenses, "rewards": rewards})
        await self._send_recap(dm, guild, staff_id)

    async def _send_recap(self, dm, guild, staff_id):
        """Récapitulatif complet en MP (noms résolus) + bouton « ✅ Lancer »."""
        draft = self.drafts.get(staff_id, {})
        excl = draft.get("exclusions", {"role_ids": [], "user_ids": []})
        excl_txt = []
        for rid in excl["role_ids"]:
            role = guild.get_role(rid) if guild else None
            excl_txt.append(f"@{role.name}" if role else f"rôle {rid}")
        for uid in excl["user_ids"]:
            excl_txt.append(f"<@{uid}>")
        excl_str = ", ".join(excl_txt) if excl_txt else "aucune"
        req_txt = []
        for rid in draft.get("roles_requis", []):
            role = guild.get_role(rid) if guild else None
            req_txt.append(f"@{role.name}" if role else f"rôle {rid}")
        req_str = (" OU ".join(req_txt)) if req_txt else "aucun"
        rewards = draft.get("rewards", [])
        rw_lines = [f"• **{r['nom']}** × {r['quantite']}" for r in rewards] or ["(aucune)"]

        embed = discord.Embed(
            title=f"📋 Récapitulatif — {draft.get('titre', '(sans titre)')}",
            description=(
                f"**Gagnants :** {draft.get('nb_gagnants', '?')}\n"
                f"**Durée :** {_fmt_duration(draft.get('duree_heures', 0))}\n"
                f"**Exclusions :** {excl_str}\n"
                f"**Rôle(s) requis :** {req_str}\n"
                f"**Récompenses déclarées :** {draft.get('nb_recompenses', len(rewards))}\n\n"
                + "\n".join(rw_lines)
            ),
            color=discord.Color.purple())
        embed.set_footer(text="Vérifie la configuration puis clique sur « ✅ Lancer » pour démarrer le giveaway.")
        if excl["user_ids"]:
            embed.add_field(
                name="⚠️ Priorité d'exclusion",
                value="Un joueur exclu individuellement ne pourra jamais participer, même s'il a le rôle requis.",
                inline=False)
        try:
            await dm.send(embed=embed, view=_LaunchView(staff_id))
        except discord.HTTPException:
            pass

    # =================================================================
    # PHASE 2 — cycle de vie (boucle 5 min) + dispatch des boutons
    # =================================================================
    async def cog_load(self):
        if self._update_task is None or self._update_task.done():
            self._update_task = asyncio.create_task(self.giveaway_update_loop())
        if self._claim_boot_task is None or self._claim_boot_task.done():
            self._claim_boot_task = asyncio.create_task(self._distribution_boot())

    async def cog_unload(self):
        for t in (self._update_task, self._claim_boot_task, self._heartbeat_task, self._deadline_task):
            if t is not None:
                t.cancel()
        self._update_task = self._claim_boot_task = self._heartbeat_task = self._deadline_task = None

    # ---------- §5 : boot (reprise), heartbeat, boucle d'échéances ----------
    async def _distribution_boot(self):
        """Au démarrage : attend que le bot soit prêt, GÈLE le chrono (décalage = temps hors-ligne),
        ré-enregistre les vues persistantes, relance les MP de claim perdus, puis démarre les boucles."""
        await self.bot.wait_until_ready()
        try:
            await self._resume_distributions()
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"[giveaway] Reprise des distributions : erreur non bloquante : {e!r}")
        if self._heartbeat_task is None or self._heartbeat_task.done():
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        if self._deadline_task is None or self._deadline_task.done():
            self._deadline_task = asyncio.create_task(self._claim_deadline_loop())

    async def _heartbeat_loop(self):
        """Écrit la date UTC courante dans bot_state toutes les 15 s (sert à geler le chrono hors-ligne)."""
        while not self.bot.is_closed():
            try:
                db.set_bot_state(GIVEAWAY_HEARTBEAT_KEY, _now_utc().isoformat())
            except Exception:
                pass
            await asyncio.sleep(15)

    async def _claim_deadline_loop(self):
        """Toutes les 10 s : fait avancer tout claim dont l'échéance est dépassée (gagnant sans réponse)."""
        while not self.bot.is_closed():
            try:
                for state in db.giveaway_claim_state_all():
                    dl = state["claim_deadline_at"]
                    if dl and _now_utc() >= _parse_iso(dl):
                        await self._advance_claim(state["giveaway_id"])
            except Exception:
                import traceback
                traceback.print_exc()
            await asyncio.sleep(10)

    async def _resume_distributions(self):
        """Gel du chrono + ré-enregistrement des vues + relance des MP manquants (voir §5)."""
        # 1) Décalage = temps écoulé hors-ligne (jamais négatif, 0 si pas de heartbeat).
        hb = db.get_bot_state(GIVEAWAY_HEARTBEAT_KEY)
        downtime = 0.0
        if hb:
            try:
                downtime = max(0.0, (_now_utc() - _parse_iso(hb)).total_seconds())
            except (ValueError, TypeError):
                downtime = 0.0
        states = db.giveaway_claim_state_all()
        # 2) Repousse chaque échéance du temps hors-ligne (chrono gelé pendant l'arrêt).
        for state in states:
            if state["claim_deadline_at"]:
                try:
                    new_dl = (_parse_iso(state["claim_deadline_at"])
                              + timedelta(seconds=downtime)).isoformat()
                    db.giveaway_claim_state_set_deadline(state["giveaway_id"], new_dl)
                except (ValueError, TypeError):
                    pass
        # 3) Ré-enregistre les vues persistantes (menus de claim + boutons owner + Participer/Voir).
        self._register_persistent_views()
        # 4) MP de claim introuvable -> on renvoie un nouveau MP SANS toucher à l'échéance.
        for state in states:
            await self._resend_claim_dm_if_missing(state)
        if states:
            print(f"🎁 [giveaway] {len(states)} distribution(s) reprise(s), "
                  f"décalage de {int(downtime // 60)} min (chrono gelé hors-ligne).")

    def _register_persistent_views(self):
        """bot.add_view pour tout ce qui est persistant : menus de claim en cours + boutons owner, et
        les vues publiques Participer/Voir (ré-enregistrées par giveaway_id des giveaways actifs)."""
        try:
            for state in db.giveaway_claim_state_all():
                g = db.giveaway_get(state["giveaway_id"])
                if g is None:
                    continue
                remaining = self._rewards_remaining(g)
                self.bot.add_view(self._claim_view(g["id"], state["current_participant_id"], remaining))
                self.bot.add_view(_relaunch_claims_view(g["id"]))
            for g in db.giveaway_get_active():
                self.bot.add_view(_participate_view(g["id"]))
        except Exception:
            import traceback
            traceback.print_exc()

    async def _resend_claim_dm_if_missing(self, state):
        """Si le MP du claim courant est introuvable, renvoie un nouveau MP (échéance INCHANGÉE).
        Relit l'état FRAIS en base (échéance déjà gelée) pour ne jamais ré-écraser le décalage."""
        giveaway_id = state["giveaway_id"]
        fresh = db.giveaway_claim_state_get(giveaway_id)  # échéance À JOUR (après gel)
        if fresh is None:
            return
        pid = fresh["current_participant_id"]
        g = db.giveaway_get(giveaway_id)
        participant = db.giveaway_get_participant(pid)
        if g is None or participant is None:
            return
        try:
            user = self.bot.get_user(participant["user_id"]) or await self.bot.fetch_user(participant["user_id"])
        except discord.HTTPException:
            user = None
        if user is None:
            return
        try:
            dm = await user.create_dm()
        except discord.HTTPException:
            return
        # Le MP existe-t-il encore ? (on interroge le salon MP du joueur, jamais get_channel qui ne
        # connaît pas les DM.)
        if fresh["dm_message_id"]:
            try:
                await dm.fetch_message(fresh["dm_message_id"])
                return  # toujours présent : rien à faire
            except discord.HTTPException:
                pass
        remaining = self._rewards_remaining(g)
        reste_s = (max(0, int((_parse_iso(fresh["claim_deadline_at"]) - _now_utc()).total_seconds()))
                   if fresh["claim_deadline_at"] else GIVEAWAY_CLAIM_TIMEOUT_SECONDS)
        embed = discord.Embed(
            title="🎉 Félicitations ! Tu as gagné ce giveaway !",
            description=(f"**{g['titre']}**\n\nChoisis ta récompense dans le menu ci-dessous.\n"
                         f"⏱️ Il te reste environ **{max(1, reste_s // 60)} min**."),
            color=discord.Color.gold())
        try:
            msg = await dm.send(embed=embed, view=self._claim_view(g["id"], pid, remaining))
            db.giveaway_claim_state_set(
                giveaway_id, fresh["session"], fresh["current_index"], pid,
                fresh["claim_deadline_at"], dm.id, msg.id)  # échéance gelée INCHANGÉE
        except discord.HTTPException:
            pass

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        if interaction.type != discord.InteractionType.component:
            return
        cid = interaction.data.get("custom_id", "") if interaction.data else ""
        if cid.startswith("giveaway_launch:"):
            await self._handle_launch(interaction, cid)
        elif cid.startswith("giveaway_join:"):
            await self._handle_join(interaction, cid)
        elif cid.startswith("giveaway_owner:"):
            await self._handle_owner(interaction, cid)
        elif cid.startswith("giveaway_reroll:"):
            await self._handle_reroll(interaction, cid)
        elif cid.startswith("giveaway_view:"):
            await self._handle_view_participants(interaction, cid)
        elif cid.startswith("giveaway_remove:"):
            await self._handle_remove_members(interaction, cid)
        elif cid.startswith("giveaway_claim:"):
            await self._handle_claim_select(interaction, cid)
        elif cid.startswith("giveaway_claimrelaunch:"):
            await self._handle_claim_relaunch(interaction, cid)

    # ---------- rendu pillow ----------
    @staticmethod
    def _pillow_rewards(rewards_list):
        """Convertit les récompenses stockées -> liste de tuples (nom, quantité) pour la pillow."""
        return [(r.get("nom_resolu", "?"), f"x{r.get('quantite', '')}") for r in rewards_list]

    async def _render_giveaway(self, titre, rewards_list, participants_normaux, participants_boost,
                               nb_gagnants, organisateur, temps_restant_str, pct, historique_num,
                               out_path=None) -> str:
        path = out_path or _giveaway_tmp_path()
        # Génération dans un thread : la pillow ne bloque jamais la boucle d'événements du bot.
        await asyncio.to_thread(
            generate_giveaway_image, titre, self._pillow_rewards(rewards_list),
            participants_normaux, participants_boost, nb_gagnants, organisateur,
            temps_restant_str, pct, historique_num, path)
        return path

    def _count_participants(self, giveaway_id, guild):
        """(normaux, boost) : Booster/VIP comptés à part. 1 ligne = 1 participant (1 perso/joueur)."""
        normaux = boost = 0
        for p in db.giveaway_get_participants(giveaway_id):
            member = guild.get_member(p["user_id"]) if guild else None
            rids = {r.id for r in getattr(member, "roles", [])}
            if BOOSTER_ROLE_ID in rids or VIP_ROLE_ID in rids:
                boost += 1
            else:
                normaux += 1
        return normaux, boost

    @staticmethod
    def _is_boost_user(user_id, guild) -> bool:
        """True si le joueur possède le rôle Booster OU VIP (prioritaires au tirage)."""
        member = guild.get_member(user_id) if guild else None
        rids = {r.id for r in getattr(member, "roles", [])}
        return BOOSTER_ROLE_ID in rids or VIP_ROLE_ID in rids

    # =================================================================
    # PHASE 4 — distribution séquentielle persistante (1 gagnant à la fois, 1h30, 2 tours)
    # =================================================================
    def _rewards_remaining(self, g):
        """Récompenses ENCORE disponibles, recalculées EN TEMPS RÉEL. Modèle 1 ligne = 1 prix : chaque
        ligne (objet OU valeur) est réclamable UNE seule fois, avec sa quantité ENTIÈRE (« Coffre ×2 »
        donne 2 coffres), puis disparaît du menu des gagnants suivants. Retourne {numero: reward}."""
        rewards = json.loads(g["rewards_json"] or "[]")
        pris = set()
        for p in db.giveaway_get_participants_full(g["id"]):
            if not p["reward_claimed_json"]:
                continue
            try:
                pris.add(json.loads(p["reward_claimed_json"]).get("numero"))
            except (ValueError, TypeError):
                continue
        return {r.get("numero"): r for r in rewards if r.get("numero") not in pris}

    # ---------- distribution PERSISTANTE (état en base + échéances, chrono gelé hors-ligne) ----------
    def _claim_options(self, remaining):
        return [discord.SelectOption(
                    label=(r.get("nom_resolu") or "?")[:100], value=str(num),
                    description=f"Quantité : {r.get('quantite', '')}"[:100])
                for num, r in remaining.items()]

    def _claim_view(self, giveaway_id, participant_id, remaining):
        """Vue PERSISTANTE du menu de claim : custom_id fixe -> dispatché par on_interaction, survit au
        redémarrage (ré-enregistrée via bot.add_view au démarrage)."""
        view = discord.ui.View(timeout=None)
        select = discord.ui.Select(
            placeholder=f"Choisis ta récompense ({_fmt_claim_delay()} pour répondre)",
            options=self._claim_options(remaining) or [discord.SelectOption(label="—", value="0")],
            custom_id=f"giveaway_claim:{giveaway_id}:{participant_id}")
        view.add_item(select)
        return view

    async def _prompt_from(self, giveaway_id, session, start_index):
        """Sollicite le PREMIER gagnant non servi à partir de start_index (ordre claim_order). Persiste
        l'état et l'échéance, puis s'arrête (pas d'attente bloquante). Fin de session -> tour2 puis §2."""
        g = db.giveaway_get(giveaway_id)
        if g is None:
            return
        gagnants = db.giveaway_get_winners(giveaway_id)  # triés par claim_order
        remaining = self._rewards_remaining(g)
        if not remaining:
            await self._finalize_tour2(giveaway_id)
            return
        i = start_index
        while i < len(gagnants):
            w = gagnants[i]
            if w["reward_claimed_json"]:
                i += 1
                continue
            sent = await self._send_claim_dm(g, w, session, i)
            if sent:
                return  # on attend son choix ou l'échéance (géré par la boucle / le callback)
            i += 1  # MP impossible : on passe au suivant
        # Fin de session.
        if session == "tour1":
            await self._prompt_from(giveaway_id, "tour2", 0)
        else:
            await self._finalize_tour2(giveaway_id)

    async def _send_claim_dm(self, g, w, session, index):
        """Envoie le MP + menu persistant au gagnant `w`, écrit l'état (échéance = maintenant + délai).
        Retourne True si le MP est parti, False sinon."""
        try:
            user = self.bot.get_user(w["user_id"]) or await self.bot.fetch_user(w["user_id"])
        except discord.HTTPException:
            user = None
        if user is None:
            return False
        remaining = self._rewards_remaining(g)
        embed = discord.Embed(
            title="🎉 Félicitations ! Tu as gagné ce giveaway !",
            description=(f"**{g['titre']}**\n\nChoisis ta récompense dans le menu ci-dessous.\n"
                         f"⏱️ Tu as **{_fmt_claim_delay()}** pour répondre."),
            color=discord.Color.gold())
        view = self._claim_view(g["id"], w["id"], remaining)
        try:
            dm = await user.create_dm()
            msg = await dm.send(embed=embed, view=view)
        except discord.HTTPException:
            return False
        deadline = (_now_utc() + timedelta(seconds=GIVEAWAY_CLAIM_TIMEOUT_SECONDS)).isoformat()
        db.giveaway_claim_state_set(g["id"], session, index, w["id"], deadline, dm.id, msg.id)
        return True

    async def _advance_claim(self, giveaway_id):
        """Passe au gagnant suivant (après un choix OU une échéance dépassée)."""
        state = db.giveaway_claim_state_get(giveaway_id)
        if state is None:
            return
        await self._prompt_from(giveaway_id, state["session"], state["current_index"] + 1)

    async def _handle_claim_select(self, interaction, cid):
        """Callback persistant du menu de claim (custom_id giveaway_claim:gid:pid)."""
        parts = cid.split(":")
        giveaway_id, participant_id = int(parts[1]), int(parts[2])
        state = db.giveaway_claim_state_get(giveaway_id)
        # Ce n'est plus (ou pas) le tour de ce gagnant -> refus.
        if state is None or state["current_participant_id"] != participant_id:
            await interaction.response.edit_message(
                content="⏱️ Ce n'est plus ton tour (temps écoulé).", embed=None, view=None)
            return
        if state["claim_deadline_at"] and _now_utc() > _parse_iso(state["claim_deadline_at"]):
            await interaction.response.edit_message(
                content="⏱️ Temps écoulé pour ce tour.", embed=None, view=None)
            await self._advance_claim(giveaway_id)
            return
        values = (interaction.data or {}).get("values") or []
        if not values:
            await interaction.response.defer()
            return
        g = db.giveaway_get(giveaway_id)
        remaining = self._rewards_remaining(g)
        num = int(values[0])
        reward = remaining.get(num)
        if reward is None:
            await interaction.response.edit_message(
                content="Cette récompense vient d'être prise. Choisis-en une autre…", view=None)
            # On re-sollicite le même gagnant avec le menu à jour.
            await self._prompt_from(giveaway_id, state["session"], state["current_index"])
            return
        participant = db.giveaway_get_participant(participant_id)
        db.giveaway_set_reward_claimed(participant_id, json.dumps(reward))
        montant = await self._apply_giveaway_reward(participant, reward)
        nom = reward.get("nom_resolu", "?")
        txt = (f"✅ Tu as reçu : **{nom} × {montant}** !" if montant is not None
               else f"✅ Choix enregistré : **{nom}** (attribution en cours).")
        try:
            await interaction.response.edit_message(content=txt, embed=None, view=None)
        except discord.HTTPException:
            pass
        await self._advance_claim(giveaway_id)

    async def _handle_claim_relaunch(self, interaction, cid):
        """§3 — Bouton « 🔄 Relancer les claims » (owner). Aucun nouveau tirage : re-sollicite seulement
        les gagnants sans réclamation, un tour1 puis tour2, avec le même mécanisme persistant."""
        giveaway_id = int(cid.split(":")[1])
        if interaction.user.id != OWNER_ID:
            await interaction.response.send_message(
                "Action réservée au lanceur du giveaway.", ephemeral=True)
            return
        g = db.giveaway_get(giveaway_id)
        if g is None:
            await interaction.response.edit_message(content="Ce giveaway n'existe plus.", view=None)
            return
        # Anti double-clic : les boutons disparaissent dès le premier clic.
        try:
            await interaction.response.edit_message(content="🔄 Relance des claims lancée.", view=None)
        except discord.HTTPException:
            pass
        db.giveaway_increment_claim_relaunch(giveaway_id)
        db.giveaway_set_status(giveaway_id, "en_distribution")
        await self._prompt_from(giveaway_id, "tour1", 0)

    # =================================================================
    # PHASE 5 — application réelle, récapitulatif, reroll
    # =================================================================
    def _unclaimed_reward_lines(self, g):
        """Lignes de récompense qu'AUCUN gagnant n'a prises (0 réclamation), telles que configurées
        (quantité d'origine). Base du récap et du reroll."""
        rewards = json.loads(g["rewards_json"] or "[]")
        pris = set()
        for p in db.giveaway_get_participants_full(g["id"]):
            if p["reward_claimed_json"]:
                try:
                    pris.add(json.loads(p["reward_claimed_json"]).get("numero"))
                except (ValueError, TypeError):
                    pass
        return [r for r in rewards if r.get("numero") not in pris]

    async def _apply_mastery_levels(self, guild, character_id, key, niveaux):
        """Ajoute `niveaux` niveaux à la Maîtrise visée, PLAFONNÉE au maximum effectif du personnage
        (override staff sinon constante ; RCT selon le stade). Même logique que les Tokens Stats."""
        from cogs.profil import (
            points_to_level_xp_capped, STAT_XP_RATIO, get_effective_max_level, get_current_rct_stage,
            MASTERY_EO_MAX_LEVEL, MASTERY_SORT_MAX_LEVEL, MASTERY_TERRITOIRE_MAX_LEVEL,
            MASTERY_ARME_MAX_LEVEL, RCT_STAGES)
        mapping = {
            "mastery_eo": ("energie_occulte", "eo", MASTERY_EO_MAX_LEVEL),
            "mastery_sort": ("sorts", "sort", MASTERY_SORT_MAX_LEVEL),
            "mastery_territoire": ("territoire", "territoire", MASTERY_TERRITOIRE_MAX_LEVEL),
            "mastery_arme": ("armes_maudites", "arme", MASTERY_ARME_MAX_LEVEL),
        }
        if key == "mastery_rct":
            stat_key, mkey = "rct", "rct"
            stage = await get_current_rct_stage(guild, character_id) if guild else None
            default_cap = (RCT_STAGES[stage]["max_level"] if stage in RCT_STAGES
                           else max(s["max_level"] for s in RCT_STAGES.values()))
        else:
            stat_key, mkey, default_cap = mapping[key]
        cap = await get_effective_max_level(character_id, mkey, default_cap)

        def _points_for_level(lvl):
            lvl = min(lvl, cap)
            total_xp = sum(db.xp_required_for_level(l) for l in range(1, lvl))
            return -(-total_xp // STAT_XP_RATIO)  # division plafond : assez de points pour ATTEINDRE lvl

        base = db.get_stat_base_pts(character_id, stat_key)
        level, _, _ = points_to_level_xp_capped(base, cap)
        target = min(cap, level + int(niveaux))
        if target > level:
            db.add_stat_base_pts(character_id, stat_key,
                                 _points_for_level(target) - _points_for_level(level))

    async def _apply_one_reward(self, guild, character_id, user_id, reward):
        """Applique UNE récompense dans le bon emplacement, après le ×2 VIP/Booster universel. Retourne
        le montant réellement attribué (après multiplicateur), ou 0 si rien n'a pu l'être."""
        if character_id is None:
            return 0
        from cogs.utils.rewards import apply_vip_booster_multiplier
        member = guild.get_member(user_id) if guild else None
        montant = apply_vip_booster_multiplier(member, reward.get("quantite", 0) or 0)
        if montant <= 0:
            return 0
        if reward.get("kind") == "item":
            item_id = reward.get("item_id")
            if item_id is not None:
                from cogs.shop import inv_give  # reçu gratuitement (gifted_quantity)
                inv_give(character_id, item_id, montant)
            return montant
        key = reward.get("key")
        if key == "xp":
            await db.grant_character_xp(character_id, montant)  # XP : inchangé, comme demandé
        elif key == "points":
            db.add_points_restants(character_id, montant)
        elif key == "argent":
            from cogs.banque import credit_compte_courant
            credit_compte_courant(character_id, montant, "Gain de giveaway", category="revenu")
        elif key in ("stats_force", "stats_vitesse", "stats_endurance"):
            stat_key = {"stats_force": "force", "stats_vitesse": "vitesse",
                        "stats_endurance": "endurance"}[key]
            db.add_stat_base_pts(character_id, stat_key, montant)
        elif key in ("mastery_arme", "mastery_rct", "mastery_territoire", "mastery_sort", "mastery_eo"):
            await self._apply_mastery_levels(guild, character_id, key, montant)
        return montant

    async def _apply_giveaway_reward(self, participant, reward):
        """Applique la récompense d'UN gagnant, une seule fois (verrou atomique reward_applied).
        Retourne le montant attribué (après ×2), ou None si déjà appliqué / échec. En cas d'échec, le
        verrou est relâché pour que le filet de sécurité (_finalize) puisse réessayer."""
        if not reward:
            return None
        # Verrou atomique : seul le premier à le prendre applique (anti double-clic / double-don).
        if not db.giveaway_try_mark_applied(participant["id"]):
            return None
        g = db.giveaway_get(participant["giveaway_id"])
        guild = self.bot.get_guild(g["guild_id"]) if g and g["guild_id"] else None
        try:
            return await self._apply_one_reward(
                guild, participant["character_id"], participant["user_id"], reward)
        except Exception:
            db.giveaway_set_reward_applied(participant["id"], 0)  # échec -> réessai possible plus tard
            return None

    async def _owner_dm(self):
        try:
            owner = self.bot.get_user(OWNER_ID) or await self.bot.fetch_user(OWNER_ID)
        except discord.HTTPException:
            return None
        if owner is None:
            return None
        try:
            return await owner.create_dm()
        except discord.HTTPException:
            return None

    async def _finalize_tour2(self, giveaway_id):
        """Fin de la distribution (après le tour 2 ou dès l'épuisement des lignes). Applique §2 :
        cas A (rien), B (exactement 1 -> don auto), C (≥2 -> reroll/relance), D (anomalie)."""
        g = db.giveaway_get(giveaway_id)
        if g is None:
            return
        db.giveaway_claim_state_delete(giveaway_id)  # plus aucun claim en cours
        # Filet de sécurité : applique les choix enregistrés mais pas encore appliqués.
        for p in db.giveaway_get_winners(giveaway_id):
            if p["reward_claimed_json"] and not p["reward_applied"]:
                try:
                    await self._apply_giveaway_reward(p, json.loads(p["reward_claimed_json"]))
                except (ValueError, TypeError):
                    pass

        non_reclamees = self._unclaimed_reward_lines(g)
        gagnants = db.giveaway_get_winners(giveaway_id)
        sans = [p for p in gagnants if not p["reward_claimed_json"]]
        titre = g["titre"]
        dm = await self._owner_dm()

        # Cas A : tout réclamé.
        if len(non_reclamees) == 0:
            db.giveaway_set_status(giveaway_id, "termine")
            if dm:
                try:
                    await dm.send(f"✅ Giveaway « {titre} » terminé — toutes les récompenses ont été réclamées.")
                except discord.HTTPException:
                    pass
            return

        # Cas D : incohérence de comptage (récompenses restantes ≠ gagnants sans réclamation).
        if len(non_reclamees) != len(sans):
            await self._owner_unclaimed_buttons(
                giveaway_id, g, non_reclamees, sans, dm, anomalie=True)
            return

        # Cas B : exactement 1 restante (et 1 gagnant sans réclamation) -> don automatique.
        if len(non_reclamees) == 1:
            reward = non_reclamees[0]
            winner = sans[0]
            db.giveaway_set_reward_claimed(winner["id"], json.dumps(reward))
            montant = await self._apply_giveaway_reward(winner, reward)
            db.giveaway_set_status(giveaway_id, "termine")
            nom = reward.get("nom_resolu", "?")
            qte = montant if montant is not None else reward.get("quantite", "")
            try:
                wu = self.bot.get_user(winner["user_id"]) or await self.bot.fetch_user(winner["user_id"])
                if wu is not None:
                    wdm = await wu.create_dm()
                    await wdm.send(
                        f"🎁 Tu n'avais pas choisi de récompense pour « {titre} », voici la tienne : "
                        f"**{nom} × {qte}**")
            except discord.HTTPException:
                pass
            if dm:
                try:
                    await dm.send(
                        f"ℹ️ Giveaway « {titre} » : 1 récompense non réclamée attribuée automatiquement à "
                        f"<@{winner['user_id']}> (**{nom} × {qte}**). Terminé, aucun reroll.")
                except discord.HTTPException:
                    pass
            return

        # Cas C : 2 récompenses non réclamées ou plus -> l'owner décide (reroll / relance).
        await self._owner_unclaimed_buttons(giveaway_id, g, non_reclamees, sans, dm, anomalie=False)

    async def _owner_unclaimed_buttons(self, giveaway_id, g, non_reclamees, sans, dm, anomalie):
        """MP owner avec la liste des récompenses restantes + boutons reroll / relancer les claims."""
        db.giveaway_set_status(giveaway_id, "attente_owner")
        if dm is None:
            return
        lignes = "\n".join(f"• {r.get('nom_resolu', '?')} × {r.get('quantite', '')}" for r in non_reclamees)
        mentions = ", ".join(f"<@{p['user_id']}>" for p in sans) if sans else "aucun"
        texte = (f"⚠️ Giveaway « {g['titre']} » — {len(non_reclamees)} récompense(s) non réclamée(s) :\n"
                 f"{lignes}\n\nGagnants sans réclamation : {mentions}")
        if anomalie:
            texte += ("\n\n❗ Anomalie de comptage : le nombre de récompenses restantes ne correspond pas "
                      "au nombre de gagnants sans réclamation. Rien n'a été attribué automatiquement.")
        if (g["reroll_count"] or 0) >= 3:
            texte += "\n\n🗑️ 3 reroll déjà enchaînés : le reroll ne rendra plus la main au-delà."
        try:
            await dm.send(texte, view=_relaunch_claims_view(giveaway_id))
        except discord.HTTPException:
            pass

    async def _launch_persisted_giveaway(self, giveaway_id, channel):
        """Poste la pillow + bouton Participer d'un giveaway déjà enregistré en base (réutilisé par le
        lancement normal et par le reroll) puis mémorise message_id/channel_id."""
        g = db.giveaway_get(giveaway_id)
        if g is None or channel is None:
            return None
        guild = getattr(channel, "guild", None)
        organisateur = "Staff"
        if guild is not None:
            m = guild.get_member(g["organisateur_id"])
            if m:
                organisateur = m.display_name
        historique_num = db.giveaway_ordinal(g["guild_id"], giveaway_id)
        rewards_list = json.loads(g["rewards_json"] or "[]")
        path = await self._render_giveaway(
            g["titre"], rewards_list, 0, 0, g["nb_gagnants"], organisateur,
            _fmt_hms((g["duree_heures"] or 0) * 3600), 1.0, historique_num,
            out_path=_giveaway_file_path(giveaway_id))
        try:
            msg = await channel.send(
                file=discord.File(path, filename="giveaway.png"), view=_participate_view(giveaway_id))
        finally:
            try:
                os.remove(path)
            except OSError:
                pass
        db.giveaway_set_message(giveaway_id, msg.id, channel.id)
        return msg

    async def _handle_reroll(self, interaction: discord.Interaction, cid):
        """Bouton « 🔁 Lancer le reroll » (owner uniquement) : recrée un giveaway de 12 h avec les seules
        récompenses non réclamées, excluant TOUS les participants de la chaîne d'origine."""
        g_id = int(cid.split(":")[1])
        if interaction.user.id != OWNER_ID:
            await interaction.response.send_message(
                "Action réservée au lanceur du giveaway.", ephemeral=True)
            return
        g = db.giveaway_get(g_id)
        if g is None:
            await interaction.response.send_message("Ce giveaway n'existe plus.", ephemeral=True)
            return
        non_reclamees = self._unclaimed_reward_lines(g)
        if not non_reclamees:
            await interaction.response.edit_message(
                content="✅ Plus aucune récompense à reroll.", view=None)
            return
        if (g["reroll_count"] or 0) >= 3:
            await interaction.response.edit_message(
                content="🗑️ Limite de 3 reroll atteinte : récompenses perdues.", view=None)
            return
        channel = self.bot.get_channel(g["channel_id"]) if g["channel_id"] else None
        if channel is None:
            await interaction.response.edit_message(
                content="⚠️ Salon d'origine introuvable : reroll impossible.", view=None)
            return
        await interaction.response.defer()
        # Renumérote les récompenses restantes (1..N) pour le nouveau giveaway.
        new_rewards = []
        for i, r in enumerate(non_reclamees, start=1):
            nr = dict(r)
            nr["numero"] = i
            new_rewards.append(nr)
        # §4 — exclusion des anciens participants selon GIVEAWAY_REROLL_EXCLUSION_MODE.
        if GIVEAWAY_REROLL_EXCLUSION_MODE == "all":
            excl_users = set(json.loads(g["exclusion_users_json"] or "[]"))
            excl_users |= {p["user_id"] for p in db.giveaway_get_participants(g_id)}
        elif GIVEAWAY_REROLL_EXCLUSION_MODE == "winners":
            excl_users = {w["user_id"] for w in db.giveaway_get_winners(g_id)}
        else:  # "none" (défaut) : aucun ancien participant exclu, un participant peut re-jouer.
            excl_users = set()
        now = _now_utc()
        ends = now + timedelta(hours=12)
        origin_id = g["origin_giveaway_id"] or g["id"]
        new_id = db.giveaway_create(
            guild_id=g["guild_id"], titre=f"{g['titre']} (Reroll)", nb_gagnants=len(new_rewards),
            exclusion_roles_json=g["exclusion_roles_json"] or "[]",
            exclusion_users_json=json.dumps(sorted(excl_users)),
            role_requis_json=g["role_requis_json"] or "[]", duree_heures=12,
            rewards_json=json.dumps(new_rewards), started_at=now.isoformat(), ends_at=ends.isoformat(),
            organisateur_id=OWNER_ID,  # owner_cheat_decision reste NULL : pas de triche sur un reroll.
            reroll_count=(g["reroll_count"] or 0) + 1, origin_giveaway_id=origin_id)
        await self._launch_persisted_giveaway(new_id, channel)
        try:
            await interaction.edit_original_response(
                content=f"🔁 Reroll lancé dans {channel.mention} (12 h).", view=None)
        except discord.HTTPException:
            pass

    # =================================================================
    # PHASE 3 — clôture, tirage pondéré, triche discrète owner, annonce
    # =================================================================
    async def _close_giveaway(self, giveaway_id):
        """À l'expiration (appelée par la boucle 5 min) : clôture, tire les gagnants (Booster/VIP
        prioritaires), applique la triche discrète de l'owner si décidée, enregistre, annonce."""
        g = db.giveaway_get(giveaway_id)
        if g is None:
            return
        db.giveaway_set_status(giveaway_id, "cloture")

        channel = self.bot.get_channel(g["channel_id"]) if g["channel_id"] else None
        guild = getattr(channel, "guild", None)
        if guild is None and g["guild_id"]:
            guild = self.bot.get_guild(g["guild_id"])

        participants = db.giveaway_get_participants_full(giveaway_id)

        # --- Aucun participant : clôture sèche, aucun gagnant. ---
        if len(participants) == 0:
            await self._mark_message_closed(
                g, guild, banniere="❌ Giveaway clôturé — aucun participant, aucun gagnant tiré.")
            db.giveaway_set_status(giveaway_id, "termine")
            return

        nb_gagnants = min(g["nb_gagnants"], len(participants))  # jamais plus de gagnants que de présents

        # --- §2 Tirage pondéré : Booster/VIP toujours tirés EN PREMIER. ---
        normaux = [p for p in participants if not self._is_boost_user(p["user_id"], guild)]
        boost = [p for p in participants if self._is_boost_user(p["user_id"], guild)]
        gagnants = []
        random.shuffle(boost)
        gagnants.extend(boost[:nb_gagnants])
        places_restantes = nb_gagnants - len(gagnants)
        if places_restantes > 0:
            random.shuffle(normaux)
            gagnants.extend(normaux[:places_restantes])

        # --- §3 Triche discrète de l'owner (remplace un gagnant, jamais un gagnant EN PLUS). ---
        if g["owner_cheat_decision"] == "oui":
            owner_participant = next((p for p in participants if p["user_id"] == OWNER_ID), None)
            gagnant_ids = {p["id"] for p in gagnants}
            if owner_participant is not None and owner_participant["id"] not in gagnant_ids:
                # Priorité : évincer un gagnant NORMAL (non-Booster/VIP) pour perturber le moins
                # possible le tirage légitime des Booster/VIP ; sinon un gagnant quelconque.
                normaux_gagnants = [p for p in gagnants
                                    if not self._is_boost_user(p["user_id"], guild)]
                cible = random.choice(normaux_gagnants) if normaux_gagnants else random.choice(gagnants)
                gagnants.remove(cible)
                gagnants.append(owner_participant)
            # Aucune trace de la substitution nulle part : l'évincé est traité comme un simple perdant.

        # --- §4 Ordre de distribution (claim_order) : TOUS les Booster/VIP avant TOUS les normaux. ---
        gagnants_vip_boost = [p for p in gagnants if self._is_boost_user(p["user_id"], guild)]
        gagnants_normaux = [p for p in gagnants if p not in gagnants_vip_boost]
        if len(gagnants_vip_boost) >= 1:
            random.shuffle(gagnants_vip_boost)   # ordre aléatoire entre eux
            random.shuffle(gagnants_normaux)     # ordre aléatoire entre eux
            ordre_final = gagnants_vip_boost + gagnants_normaux
        else:
            random.shuffle(gagnants)
            ordre_final = gagnants

        # --- §4 Enregistrement des gagnants (is_winner + claim_order). ---
        db.giveaway_set_winners([p["id"] for p in ordre_final])
        db.giveaway_set_claim_orders([(p["id"], i) for i, p in enumerate(ordre_final, start=1)])

        # --- §5 Annonce dans le salon + passage en distribution. ---
        await self._mark_message_closed(g, guild, banniere="🎉 Giveaway terminé !")
        await self._announce_winners(g, guild, channel, ordre_final)
        db.giveaway_set_status(giveaway_id, "en_distribution")
        # Distribution PERSISTANTE (état en base, échéances) : on amorce le tour 1 (envoi d'UN MP, pas
        # d'attente bloquante). La boucle d'échéances et les callbacks de menu font avancer la suite.
        await self._prompt_from(giveaway_id, "tour1", 0)

    async def _mark_message_closed(self, g, guild, banniere):
        """Régénère la pillow en état CLÔTURÉ (temps 00:00:00, jauge à 0) et édite le message existant,
        en retirant le bouton Participer. Ne poste jamais de nouveau message."""
        if not g["message_id"] or not g["channel_id"]:
            return
        channel = self.bot.get_channel(g["channel_id"])
        if channel is None:
            return
        normaux, boost = self._count_participants(g["id"], guild)
        rewards_list = json.loads(g["rewards_json"] or "[]")
        organisateur = "Staff"
        if guild is not None:
            m = guild.get_member(g["organisateur_id"])
            if m:
                organisateur = m.display_name
        historique_num = db.giveaway_ordinal(g["guild_id"], g["id"])
        path = await self._render_giveaway(
            g["titre"], rewards_list, normaux, boost, g["nb_gagnants"], organisateur,
            "00:00:00", 0.0, historique_num, out_path=_giveaway_file_path(g["id"]))
        try:
            msg = await channel.fetch_message(g["message_id"])
            # view=None : le bouton Participer disparaît une fois le giveaway clôturé.
            await msg.edit(content=banniere,
                           attachments=[discord.File(path, filename="giveaway.png")], view=None)
        except discord.HTTPException:
            pass
        finally:
            try:
                os.remove(path)
            except OSError:
                pass

    async def _announce_winners(self, g, guild, channel, ordre_final):
        """Poste un nouvel embed d'annonce JUSTE SOUS le message du giveaway (mentions + personnages)."""
        if channel is None:
            return
        lignes = []
        for p in ordre_final:
            perso = None
            if p["character_id"]:
                with db.get_connection() as conn:
                    row = conn.execute(
                        "SELECT character_name FROM validated_characters WHERE id = ?",
                        (p["character_id"],)).fetchone()
                perso = row["character_name"] if row else None
            perso_txt = f" — *{perso}*" if perso else ""
            lignes.append(f"• <@{p['user_id']}>{perso_txt}")
        embed = discord.Embed(
            title="🎉 Giveaway terminé !",
            description=(f"**{g['titre']}**\n\nFélicitations à :\n" + "\n".join(lignes)
                        + "\n\nLes gagnants ont été contactés en MP pour choisir leur récompense."),
            color=discord.Color.gold())
        try:
            await channel.send(embed=embed)
        except discord.HTTPException:
            pass

    # ---------- boucle de rafraîchissement ----------
    async def giveaway_update_loop(self):
        """Boucle de rafraîchissement : cadence aléatoire 5/10/15/20 s (retirée à chaque tour). Clôture
        les giveaways expirés, sinon régénère et édite leur pillow. Une erreur par giveaway n'arrête
        jamais la boucle."""
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            for g in db.giveaway_get_active():
                try:
                    now = _now_utc()
                    ends = _parse_iso(g["ends_at"])
                    if now >= ends:
                        await self._close_giveaway(g["id"])
                    else:
                        await self._refresh_message(g, now, ends)
                except Exception:
                    continue
            await asyncio.sleep(random.choice([5, 10, 15, 20]))  # nouveau tirage à chaque tour

    async def _refresh_message(self, g, now, ends):
        """Régénère la pillow et ÉDITE le message existant (jamais de nouveau message)."""
        if not g["message_id"] or not g["channel_id"]:
            return
        channel = self.bot.get_channel(g["channel_id"])
        if channel is None:
            return
        guild = getattr(channel, "guild", None)
        normaux, boost = self._count_participants(g["id"], guild)
        remaining = (ends - now).total_seconds()
        total = max(1, (g["duree_heures"] or 0) * 3600)
        pct = max(0.0, min(1.0, remaining / total))
        rewards_list = json.loads(g["rewards_json"] or "[]")
        organisateur = "Staff"
        if guild is not None:
            m = guild.get_member(g["organisateur_id"])
            if m:
                organisateur = m.display_name
        historique_num = db.giveaway_ordinal(g["guild_id"], g["id"])
        path = await self._render_giveaway(
            g["titre"], rewards_list, normaux, boost, g["nb_gagnants"], organisateur,
            _fmt_hms(remaining), pct, historique_num, out_path=_giveaway_file_path(g["id"]))
        try:
            msg = await channel.fetch_message(g["message_id"])
            await msg.edit(attachments=[discord.File(path, filename="giveaway.png")],
                           view=_participate_view(g["id"]))
        except discord.HTTPException:
            pass
        finally:
            try:
                os.remove(path)
            except OSError:
                pass

    # ---------- bouton « ✅ Lancer » ----------
    async def _handle_launch(self, interaction: discord.Interaction, cid):
        staff_id = int(cid.split(":")[1])
        if interaction.user.id != staff_id:
            await interaction.response.send_message("Ce giveaway ne t'appartient pas.", ephemeral=True)
            return
        draft = self.drafts.get(staff_id)
        if not draft or "rewards" not in draft:
            await interaction.response.send_message(
                "❌ Configuration expirée. Relance `/giveaway`.", ephemeral=True)
            return
        # Le clic a lieu en MP (pas de interaction.guild) : le salon de lancement est TOUJOURS le salon
        # fixe. La génération de la pillow peut dépasser 3 s -> on diffère la réponse d'abord.
        await interaction.response.defer()
        channel = self.bot.get_channel(draft.get("channel_id", GIVEAWAY_CHANNEL_ID))
        if channel is None:
            await interaction.followup.send(
                "❌ Salon de lancement introuvable : giveaway non lancé.", ephemeral=True)
            return
        now = _now_utc()
        duree_heures = draft["duree_heures"]
        ends = now + timedelta(hours=duree_heures)
        excl = draft.get("exclusions", {"role_ids": [], "user_ids": []})
        rewards = draft.get("rewards", [])
        rewards_json_list = [
            {"numero": i + 1, "nom_resolu": r["nom"], "quantite": r["quantite"],
             "kind": r.get("kind"), "key": r.get("key"), "item_id": r.get("item_id")}
            for i, r in enumerate(rewards)]
        g_id = db.giveaway_create(
            guild_id=draft.get("guild_id"), titre=draft["titre"], nb_gagnants=draft["nb_gagnants"],
            exclusion_roles_json=json.dumps(excl["role_ids"]),
            exclusion_users_json=json.dumps(excl["user_ids"]),
            role_requis_json=json.dumps(draft.get("roles_requis", [])), duree_heures=duree_heures,
            rewards_json=json.dumps(rewards_json_list), started_at=now.isoformat(),
            ends_at=ends.isoformat(), organisateur_id=staff_id)
        await self._launch_persisted_giveaway(g_id, channel)
        # §5 — mécanisme DISCRET réservé à l'owner (MP uniquement, jamais de log public).
        if staff_id == OWNER_ID:
            try:
                dm = await interaction.user.create_dm()
                await dm.send("Veux tu être garanti à 100% parmi les gagnants de ce giveaway ?",
                              view=_owner_decision_view(g_id))
            except discord.HTTPException:
                pass
        self.drafts.pop(staff_id, None)
        try:
            await interaction.edit_original_response(view=None)
        except discord.HTTPException:
            pass
        await interaction.followup.send(f"✅ Giveaway lancé dans {channel.mention} !", ephemeral=True)

    # ---------- bouton « 🎉 Participer » ----------
    async def _select_character(self, interaction: discord.Interaction, user):
        """Sélection du personnage participant (1 seul par joueur). Répond TOUJOURS à l'interaction
        (defer ou prompt éphémère) ; la suite passe par interaction.followup. Retourne l'id ou None."""
        chars = _get_user_characters(user.id, interaction.guild.id) if interaction.guild else []
        if not chars:
            await interaction.response.send_message("❌ Tu n'as aucun personnage validé.", ephemeral=True)
            return None
        if len(chars) == 1:
            await interaction.response.defer(ephemeral=True)
            return chars[0]["id"]
        view = _CharChoiceView(
            user.id, [(c["id"], c["character_name"] or f"#{c['id']}") for c in chars[:3]])
        await interaction.response.send_message(
            "Avec quel personnage veux-tu participer ?", view=view, ephemeral=True)
        await view.wait()
        return view.result

    async def _handle_join(self, interaction: discord.Interaction, cid):
        g_id = int(cid.split(":")[1])
        g = db.giveaway_get(g_id)
        if g is None or g["status"] != "actif":
            await interaction.response.send_message("❌ Ce giveaway est terminé.", ephemeral=True)
            return
        user = interaction.user
        # 1) Sélection du personnage (répond à l'interaction).
        character_id = await self._select_character(interaction, user)
        if character_id is None:
            return
        # 2) Exclusions — l'exclusion individuelle est TOUJOURS prioritaire sur le rôle requis.
        member = interaction.guild.get_member(user.id) if interaction.guild else None
        role_ids = {r.id for r in getattr(member, "roles", [])}
        excl_users = set(json.loads(g["exclusion_users_json"] or "[]"))
        excl_roles = set(json.loads(g["exclusion_roles_json"] or "[]"))
        req_roles = set(json.loads(g["role_requis_json"] or "[]"))
        # Joueur retiré par le staff : blocage absolu, jamais de réinscription.
        if db.giveaway_is_removed(g_id, user.id):
            await interaction.followup.send(
                "❌ Tu ne peux pas participer à ce giveaway.", ephemeral=True)
            return
        if user.id in excl_users:
            await interaction.followup.send(
                "❌ Tu ne peux pas participer à ce giveaway.", ephemeral=True)
            return
        if role_ids & excl_roles:
            await interaction.followup.send(
                "❌ Tu ne peux pas participer à ce giveaway.", ephemeral=True)
            return
        if req_roles and not (role_ids & req_roles):
            await interaction.followup.send(
                "❌ Tu n'as pas le rôle requis pour participer à ce giveaway.", ephemeral=True)
            return
        # 3) Déjà inscrit ?
        if db.giveaway_participant_exists(g_id, user.id):
            await interaction.followup.send("ℹ️ Tu participes déjà à ce giveaway.", ephemeral=True)
            return
        # 4) Enregistrement + 5) confirmation.
        db.giveaway_add_participant(g_id, user.id, character_id, _now_utc().isoformat())
        await interaction.followup.send("✅ Tu participes au giveaway !", ephemeral=True)

    # ---------- staff : voir / retirer des participants ----------
    def _staff_member(self, interaction, g):
        """Résout le Member staff que le clic vienne du salon public (déjà un Member) ou d'un MP
        (on le retrouve via la guilde du giveaway)."""
        user = interaction.user
        if isinstance(user, discord.Member):
            return user
        guild = self.bot.get_guild(g["guild_id"]) if g and g["guild_id"] else None
        return guild.get_member(user.id) if guild else None

    def _build_participant_embeds(self, g, guild):
        """Embeds listant tous les participants (numéro, mention, personnage, slot, tag Booster/VIP).
        Paginé par blocs de 20 lignes."""
        parts = db.giveaway_get_participants_full(g["id"])
        titre = f"👥 Participants — {g['titre']} ({len(parts)})"
        lignes = []
        for i, p in enumerate(parts, 1):
            brief = db.giveaway_character_brief(p["character_id"])
            perso = (brief["character_name"] if brief and brief["character_name"] else "?")
            slot = brief["slot_number"] if brief else "?"
            member = guild.get_member(p["user_id"]) if guild else None
            rids = {r.id for r in getattr(member, "roles", [])}
            tag = " — ⭐ Booster/VIP" if (BOOSTER_ROLE_ID in rids or VIP_ROLE_ID in rids) else ""
            lignes.append(f"`{i:>2}` <@{p['user_id']}> — *{perso}* (slot {slot}){tag}")
        if not lignes:
            return [discord.Embed(title=titre, description="Aucun participant pour l'instant.",
                                  color=discord.Color.blurple())]
        embeds = []
        for k in range(0, len(lignes), 20):
            suffixe = f" [{k // 20 + 1}]" if len(lignes) > 20 else ""
            embeds.append(discord.Embed(title=titre + suffixe, description="\n".join(lignes[k:k + 20]),
                                        color=discord.Color.blurple()))
        return embeds

    async def _send_participant_list(self, dm, g, guild):
        """Envoie la liste (paginée) en MP ; le bouton « Retirer » est sous le dernier message."""
        embeds = self._build_participant_embeds(g, guild)
        for idx in range(0, len(embeds), 10):  # Discord : max 10 embeds par message
            group = embeds[idx:idx + 10]
            is_last = idx + 10 >= len(embeds)
            await dm.send(embeds=group, view=_remove_members_view(g["id"]) if is_last else None)

    async def _handle_view_participants(self, interaction: discord.Interaction, cid):
        g_id = int(cid.split(":")[1])
        g = db.giveaway_get(g_id)
        if g is None or g["status"] != "actif":
            await interaction.response.send_message("❌ Ce giveaway n'est plus actif.", ephemeral=True)
            return
        member = self._staff_member(interaction, g)
        if member is None or not _is_staff(member):
            await interaction.response.send_message("Réservé au staff.", ephemeral=True)
            return
        guild = self.bot.get_guild(g["guild_id"]) if g["guild_id"] else getattr(interaction, "guild", None)
        try:
            dm = await interaction.user.create_dm()
            await self._send_participant_list(dm, g, guild)
        except discord.HTTPException:
            await interaction.response.send_message(
                "❌ Je n'ai pas pu t'écrire en MP. Ouvre tes messages privés.", ephemeral=True)
            return
        await interaction.response.send_message("📩 Liste des participants envoyée en MP.", ephemeral=True)

    async def _handle_remove_members(self, interaction: discord.Interaction, cid):
        g_id = int(cid.split(":")[1])
        g = db.giveaway_get(g_id)
        if g is None or g["status"] != "actif":
            await interaction.response.send_message("❌ Ce giveaway n'est plus actif.", ephemeral=True)
            return
        member = self._staff_member(interaction, g)
        if member is None or not _is_staff(member):
            await interaction.response.send_message("Réservé au staff.", ephemeral=True)
            return
        await interaction.response.defer()  # clic en MP : on enchaîne le flux par messages
        guild = self.bot.get_guild(g["guild_id"]) if g["guild_id"] else None
        dm = interaction.channel or await interaction.user.create_dm()
        task = asyncio.create_task(self._remove_flow(interaction.user, dm, guild, g_id))
        self._distribution_tasks.add(task)
        task.add_done_callback(self._distribution_tasks.discard)

    async def _remove_flow(self, user, dm, guild, g_id):
        """Flux Q/R (nettoyage, « annuler », sans timeout, reprise sur erreur) pour retirer des membres."""
        def v_count(t, m):
            n_part = len(db.giveaway_get_participants_full(g_id))
            if n_part == 0:
                return False, None, "Il n'y a aucun participant à retirer."
            if t.isdigit() and 1 <= int(t) <= n_part:
                return True, int(t), ""
            return False, None, f"Donne un entier entre 1 et {n_part} (participants actuels)."

        try:
            n = await self._ask(dm, user, "Combien de membres veux-tu retirer ?", v_count)

            def v_members(t, m):
                _, users = parse_mentions(t, guild)
                uniq = list(dict.fromkeys(users))
                if len(uniq) != n:
                    return False, None, f"Donne exactement {n} joueur(s) (mentions ou IDs) en un message."
                current = {p["user_id"] for p in db.giveaway_get_participants_full(g_id)}
                if any(u not in current for u in uniq):
                    return False, None, "Tous doivent être des participants actuels du giveaway."
                return True, uniq, ""

            targets = await self._ask(
                dm, user,
                f"Mentionne ou donne l'ID de ces {n} joueur(s) (un seul message).", v_members)
        except _GiveawayCancel:
            try:
                await dm.send("❌ Retrait annulé.")
            except discord.HTTPException:
                pass
            return

        db.giveaway_remove_participants(g_id, targets)  # DELETE + inscription dans giveaway_removed
        try:
            await dm.send(f"✅ {len(targets)} membre(s) retiré(s). Ils ne pourront plus se réinscrire.")
            g = db.giveaway_get(g_id)
            await self._send_participant_list(dm, g, guild)  # liste à jour
        except discord.HTTPException:
            pass
        await self._refresh_now(g_id)  # pillow mis à jour immédiatement

    async def _refresh_now(self, g_id):
        """Régénère et édite le pillow immédiatement (hors cadence de la boucle)."""
        g = db.giveaway_get(g_id)
        if g is None or g["status"] != "actif":
            return
        try:
            await self._refresh_message(g, _now_utc(), _parse_iso(g["ends_at"]))
        except Exception:
            pass

    # ---------- mécanisme discret owner (Oui / Non) ----------
    async def _handle_owner(self, interaction: discord.Interaction, cid):
        parts = cid.split(":")
        g_id = int(parts[1])
        decision = parts[2] if len(parts) > 2 else "non"
        # Discrétion absolue : seul l'owner peut cliquer, et uniquement en MP.
        if interaction.user.id != OWNER_ID:
            await interaction.response.send_message("Action non autorisée.", ephemeral=True)
            return
        g = db.giveaway_get(g_id)
        if g is None or g["status"] != "actif":
            try:
                await interaction.response.edit_message(content="Ce giveaway n'est plus actif.", view=None)
            except discord.HTTPException:
                await interaction.response.send_message("Ce giveaway n'est plus actif.", ephemeral=True)
            return
        db.giveaway_set_owner_decision(g_id, decision)
        try:
            await interaction.response.edit_message(content="Entendu, c'est noté.", view=None)
        except discord.HTTPException:
            await interaction.response.send_message("Entendu, c'est noté.", ephemeral=True)


async def setup(bot):
    await bot.add_cog(Giveaway(bot))
