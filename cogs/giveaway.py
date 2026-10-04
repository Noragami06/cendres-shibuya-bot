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
from discord.ext import commands, tasks

from cogs.utils import database as db
from cogs.utils.image_gen import generate_giveaway_image
from cogs.utils.rewards import BOOSTER_ROLE_ID, VIP_ROLE_ID

FICHE_STAFF_ROLE_ID = 1521229332075512039
GIVEAWAY_MAX_REWARDS = 20  # garde-fou sur le nombre de récompenses d'un giveaway

# Salon FIXE où la pillow + le bouton Participer sont TOUJOURS postés (jamais le salon d'exécution).
GIVEAWAY_CHANNEL_ID = 1521562694891868180

# Owner du serveur : seul concerné par le mécanisme discret (§5). Jamais exposé publiquement.
OWNER_ID = 396615332346855428

# Dossier temporaire des images de giveaway (généré à la volée, nettoyé après envoi).
GIVEAWAY_IMG_DIR = os.path.join(os.path.dirname(__file__), "..", "temp", "giveaway_images")


def _giveaway_tmp_path() -> str:
    os.makedirs(GIVEAWAY_IMG_DIR, exist_ok=True)
    return os.path.join(GIVEAWAY_IMG_DIR, f"giveaway_{uuid.uuid4().hex}.png")


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
    """Vue persistante du message public : bouton « 🎉 Participer » (custom_id porteur de l'id)."""
    view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.Button(
        label="Participer", emoji="🎉", style=discord.ButtonStyle.success,
        custom_id=f"giveaway_join:{giveaway_id}"))
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


class _RewardSelectView(discord.ui.View):
    """Menu déroulant de choix de récompense envoyé en MP à un gagnant (Phase 4). La résolution passe
    par un Future attendu avec asyncio.wait_for (plafond de 30 min côté appelant). timeout=None : c'est
    l'appelant qui borne l'attente, le callback arrête la vue dès qu'un choix est fait."""

    def __init__(self, options, owner_id):
        super().__init__(timeout=None)
        self.owner_id = owner_id
        self.future = asyncio.get_running_loop().create_future()
        self.select = discord.ui.Select(
            placeholder="Choisis ta récompense (30 minutes pour répondre)",
            options=options, min_values=1, max_values=1)
        self.select.callback = self._on_select
        self.add_item(self.select)

    async def _on_select(self, interaction: discord.Interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Ce choix ne t'appartient pas.", ephemeral=True)
            return
        valeur = self.select.values[0]
        if not self.future.done():
            self.future.set_result(valeur)
        try:
            await interaction.response.edit_message(
                content="✅ Choix enregistré.", view=None)
        except discord.HTTPException:
            pass
        self.stop()


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
        if not self.giveaway_update_loop.is_running():
            self.giveaway_update_loop.start()

    async def cog_unload(self):
        self.giveaway_update_loop.cancel()

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

    # ---------- rendu pillow ----------
    @staticmethod
    def _pillow_rewards(rewards_list):
        """Convertit les récompenses stockées -> liste de tuples (nom, quantité) pour la pillow."""
        return [(r.get("nom_resolu", "?"), f"x{r.get('quantite', '')}") for r in rewards_list]

    def _render_giveaway(self, titre, rewards_list, participants_normaux, participants_boost,
                         nb_gagnants, organisateur, temps_restant_str, pct, historique_num) -> str:
        path = _giveaway_tmp_path()
        generate_giveaway_image(
            titre, self._pillow_rewards(rewards_list), participants_normaux, participants_boost,
            nb_gagnants, organisateur, temps_restant_str, pct, historique_num, path)
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
    # PHASE 4 — distribution séquentielle (1 gagnant à la fois, 30 min, 2 tours)
    # =================================================================
    def _rewards_remaining(self, g):
        """Récompenses ENCORE disponibles, recalculées EN TEMPS RÉEL à partir des choix déjà faits.
        Retourne un dict ordonné {numero: (reward_dict, quantite_restante)}. Une ligne reste présente
        tant qu'il en reste ≥ 1 ; chaque choix d'un gagnant en consomme 1 unité."""
        rewards = json.loads(g["rewards_json"] or "[]")
        pris = {}
        for p in db.giveaway_get_participants_full(g["id"]):
            if not p["reward_claimed_json"]:
                continue
            try:
                rc = json.loads(p["reward_claimed_json"])
            except (ValueError, TypeError):
                continue
            num = rc.get("numero")
            pris[num] = pris.get(num, 0) + 1
        remaining = {}
        for r in rewards:
            num = r.get("numero")
            # Objet : lot de N exemplaires, réclamable par N gagnants (1 chacun). Récompense « valeur »
            # (xp/argent/stats/maîtrise) : un seul prix dont la quantité est le montant attribué à UN
            # gagnant -> réclamable une seule fois (dupliquer la ligne pour plusieurs gagnants).
            lot = (r.get("quantite", 0) or 0) if r.get("kind") == "item" else 1
            reste = lot - pris.get(num, 0)
            if reste >= 1:
                remaining[num] = (r, reste)
        return remaining

    async def _start_reward_distribution(self, giveaway_id, tour=1):
        """Lance (ou relance au tour 2) la distribution séquentielle. S'arrête dès qu'il n'y a plus
        rien à distribuer ou que tous les gagnants ont choisi."""
        g = db.giveaway_get(giveaway_id)
        if g is None:
            return
        gagnants = db.giveaway_get_winners(giveaway_id)
        if not gagnants:
            await self._finalize_giveaway_rewards(giveaway_id)
            return
        remaining = self._rewards_remaining(g)
        tous_ont_choisi = all(p["reward_claimed_json"] for p in gagnants)
        if len(remaining) == 0 or tous_ont_choisi:
            await self._finalize_giveaway_rewards(giveaway_id)
            return
        db.giveaway_set_status(giveaway_id, f"distribution_tour{tour}")
        await self._process_next_claimant(giveaway_id, tour, 0)

    async def _process_next_claimant(self, giveaway_id, tour, index):
        """Traite un gagnant à la fois (ordre claim_order). Passe au suivant immédiatement après un
        choix, ou après l'expiration des 30 min. Enchaîne le tour 2 puis la Phase 5."""
        g = db.giveaway_get(giveaway_id)
        if g is None:
            return
        gagnants = db.giveaway_get_winners(giveaway_id)
        if index >= len(gagnants):
            if tour == 1:
                await self._start_reward_distribution(giveaway_id, tour=2)
            else:
                await self._finalize_giveaway_rewards(giveaway_id)
            return
        gagnant = gagnants[index]
        # Déjà servi (au tour 1 ou avant) : on ne le re-sollicite jamais.
        if gagnant["reward_claimed_json"]:
            await self._process_next_claimant(giveaway_id, tour, index + 1)
            return
        remaining = self._rewards_remaining(g)
        if len(remaining) == 0:
            # Plus rien à distribuer : inutile de solliciter qui que ce soit.
            await self._finalize_giveaway_rewards(giveaway_id)
            return
        chosen_num = await self._prompt_claimant(g, gagnant, remaining)
        if chosen_num is not None:
            reward = dict(remaining[chosen_num][0])
            # Objet : 1 seul exemplaire attribué même si le lot en comptait plusieurs (le ×2 VIP/Booster
            # agira à l'application). Récompense « valeur » : on conserve le montant/points/niveaux plein.
            if reward.get("kind") == "item":
                reward["quantite"] = 1
            db.giveaway_set_reward_claimed(gagnant["id"], json.dumps(reward))
        await self._process_next_claimant(giveaway_id, tour, index + 1)

    async def _prompt_claimant(self, g, gagnant, remaining):
        """Envoie le MP + menu déroulant au gagnant et attend son choix (max 30 min). Retourne le
        numéro de la récompense choisie, ou None (MP impossible / délai dépassé)."""
        try:
            user = self.bot.get_user(gagnant["user_id"]) or await self.bot.fetch_user(gagnant["user_id"])
        except discord.HTTPException:
            user = None
        if user is None:
            return None
        options = []
        for num, (r, reste) in remaining.items():
            if r.get("kind") == "item":
                desc = f"Exemplaire(s) restant(s) : {reste}"
            else:
                desc = f"Quantité : {r.get('quantite', '')}"
            options.append(discord.SelectOption(
                label=(r.get("nom_resolu") or "?")[:100], value=str(num), description=desc[:100]))
        embed = discord.Embed(
            title="🎉 Félicitations ! Tu as gagné ce giveaway !",
            description=(f"**{g['titre']}**\n\nChoisis ta récompense dans le menu ci-dessous.\n"
                         "⏱️ Tu as **30 minutes** pour répondre."),
            color=discord.Color.gold())
        view = _RewardSelectView(options, gagnant["user_id"])
        try:
            dm = await user.create_dm()
            msg = await dm.send(embed=embed, view=view)
        except discord.HTTPException:
            return None
        try:
            valeur = await asyncio.wait_for(view.future, timeout=1800)  # 30 minutes
        except asyncio.TimeoutError:
            view.stop()
            try:
                await msg.edit(content="⏱️ Délai écoulé — tu n'as pas choisi à temps pour ce tour.",
                               embed=None, view=None)
            except discord.HTTPException:
                pass
            return None
        num = int(valeur)
        reward = remaining.get(num)
        nom = reward[0].get("nom_resolu") if reward else "?"
        try:
            await dm.send(f"✅ Tu as choisi : **{nom}** !")
        except discord.HTTPException:
            pass
        return num

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
        """Applique UNE récompense choisie dans le bon emplacement, après le ×2 VIP/Booster universel."""
        if character_id is None:
            return
        from cogs.utils.rewards import apply_vip_booster_multiplier
        member = guild.get_member(user_id) if guild else None
        montant = apply_vip_booster_multiplier(member, reward.get("quantite", 0) or 0)
        if montant <= 0:
            return
        if reward.get("kind") == "item":
            item_id = reward.get("item_id")
            if item_id is not None:
                from cogs.shop import inv_give  # reçu gratuitement (gifted_quantity)
                inv_give(character_id, item_id, montant)
            return
        key = reward.get("key")
        if key == "xp":
            await db.grant_character_xp(character_id, montant)
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

    async def _finalize_giveaway_rewards(self, giveaway_id):
        """Applique réellement toutes les récompenses choisies, puis envoie le récap + la décision de
        reroll à l'owner. Idempotent (ne ré-applique jamais un giveaway déjà terminé)."""
        g = db.giveaway_get(giveaway_id)
        if g is None or g["status"] == "termine":
            return
        guild = self.bot.get_guild(g["guild_id"]) if g["guild_id"] else None
        for p in db.giveaway_get_winners(giveaway_id):
            if not p["reward_claimed_json"]:
                continue
            try:
                reward = json.loads(p["reward_claimed_json"])
            except (ValueError, TypeError):
                continue
            try:
                await self._apply_one_reward(guild, p["character_id"], p["user_id"], reward)
            except Exception:
                # Une récompense qui échoue ne doit pas bloquer l'attribution des autres.
                continue
        db.giveaway_set_status(giveaway_id, "termine")
        await self._reward_recap_and_reroll(g)

    async def _reward_recap_and_reroll(self, g):
        """Récap FINAL en MP à l'OWNER (toujours, peu importe qui a lancé). Bouton reroll si des lignes
        n'ont été prises par personne et que la limite de 3 reroll n'est pas atteinte."""
        try:
            owner = self.bot.get_user(OWNER_ID) or await self.bot.fetch_user(OWNER_ID)
        except discord.HTTPException:
            owner = None
        if owner is None:
            return
        titre = g["titre"]
        non_reclamees = self._unclaimed_reward_lines(g)
        try:
            dm = await owner.create_dm()
        except discord.HTTPException:
            return
        if not non_reclamees:
            try:
                await dm.send(f"✅ Giveaway « {titre} » terminé — toutes les récompenses ont été réclamées.")
            except discord.HTTPException:
                pass
            return
        lignes = "\n".join(f"• {r.get('nom_resolu', '?')} × {r.get('quantite', '')}" for r in non_reclamees)
        gagnants = db.giveaway_get_winners(g["id"])
        sans = [p for p in gagnants if not p["reward_claimed_json"]]
        mentions = ", ".join(f"<@{p['user_id']}>" for p in sans) if sans else "aucun"
        texte = (f"⚠️ Giveaway « {titre} » terminé — récompenses non réclamées :\n{lignes}"
                 f"\n\nJoueurs n'ayant rien réclamé : {mentions}")
        if (g["reroll_count"] or 0) >= 3:
            texte += ("\n\n🗑️ 3 reroll déjà enchaînés : les récompenses restantes partent définitivement "
                      "à la poubelle (plus assez de joueurs éligibles pour continuer).")
            try:
                await dm.send(texte)
            except discord.HTTPException:
                pass
            return
        try:
            await dm.send(texte, view=_reroll_view(g["id"]))
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
        path = self._render_giveaway(
            g["titre"], rewards_list, 0, 0, g["nb_gagnants"], organisateur,
            _fmt_hms((g["duree_heures"] or 0) * 3600), 1.0, historique_num)
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
        # Exclusion cumulée : exclusions existantes + TOUS les participants du giveaway rerollé
        # (l'accumulation le long de la chaîne exclut bien tout le monde depuis l'origine).
        excl_users = set(json.loads(g["exclusion_users_json"] or "[]"))
        excl_users |= {p["user_id"] for p in db.giveaway_get_participants(g_id)}
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
        # Phase 4 : la distribution (jusqu'à 2 tours × N gagnants × 30 min) ne doit JAMAIS bloquer la
        # boucle de clôture -> on la lance dans une tâche de fond dont on garde une référence.
        task = asyncio.create_task(self._start_reward_distribution(giveaway_id))
        self._distribution_tasks.add(task)
        task.add_done_callback(self._distribution_tasks.discard)

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
        path = self._render_giveaway(
            g["titre"], rewards_list, normaux, boost, g["nb_gagnants"], organisateur,
            "00:00:00", 0.0, historique_num)
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
    @tasks.loop(minutes=5)
    async def giveaway_update_loop(self):
        for g in db.giveaway_get_active():
            try:
                now = _now_utc()
                ends = _parse_iso(g["ends_at"])
                if now >= ends:
                    await self._close_giveaway(g["id"])
                    continue
                await self._refresh_message(g, now, ends)
            except Exception:
                # Une erreur sur un giveaway ne doit jamais interrompre la boucle globale.
                continue

    @giveaway_update_loop.before_loop
    async def _before_giveaway_loop(self):
        await self.bot.wait_until_ready()

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
        path = self._render_giveaway(
            g["titre"], rewards_list, normaux, boost, g["nb_gagnants"], organisateur,
            _fmt_hms(remaining), pct, historique_num)
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
