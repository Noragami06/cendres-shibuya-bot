# /récompense-add — commande staff : donne leur récompense de DÉPART (celle affichée sur la fiche) à
# tous les personnages validés qui ne l'ont pas encore reçue, et trace qui a reçu quoi.
#
# Exclusions (diagnostic) : l'argent est DÉJÀ déposé à la création du compte bancaire (banque.py) -> on
# ne le redonne jamais (statut 'deja_applique'). Les rerolls sont consommés pendant la création -> ignorés.
# XP, parchemins, reliques, armes : jamais appliqués jusqu'ici -> à donner, via les fonctions existantes.

import re
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from cogs.utils import database as db

FICHE_STAFF_ROLE_ID = 1521229332075512039

# Clé de récompense de départ -> nom exact de l'item_definition (parchemins).
PARCHEMIN_ITEM_NAMES = {
    "parchemin_territoire": "Parchemin Territoire",
    "parchemin_rct": "Parchemin RCT",
    "parchemin_nature": "Parchemin Nature d'EO",
}


def _is_staff(member) -> bool:
    return any(r.id == FICHE_STAFF_ROLE_ID for r in getattr(member, "roles", []))


def _detail_after_dash(detail: str) -> str:
    """Partie après le séparateur « — » du détail (ex: 'XP — 3604 XP' -> '3604 XP')."""
    return (detail or "").split("—")[-1].strip()


def _parse_amount(detail: str) -> int:
    """Premier entier trouvé (espaces/espaces insécables ignorés) — pour l'XP (ou l'argent)."""
    m = re.search(r"\d[\d  ]*", detail or "")
    return int(re.sub(r"[  ]", "", m.group(0))) if m else 0


def _parse_qty(detail: str) -> int:
    """Quantité 'xN' du détail (parchemins), défaut 1."""
    m = re.search(r"x\s*(\d+)", detail or "", re.IGNORECASE)
    return int(m.group(1)) if m else 1


class _ConfirmView(discord.ui.View):
    """Aperçu MP : « ✅ Confirmer et distribuer » (désactivé dès le 1er clic) + « ✖️ Annuler ».
    Re-vérifie le staff à chaque clic (membre résolu via la guilde, car en MP pas de rôles)."""

    def __init__(self, owner_id, guild, timeout=600):
        super().__init__(timeout=timeout)
        self.owner_id = owner_id
        self.guild = guild
        self.result = None
        import asyncio
        self.future = asyncio.get_running_loop().create_future()

    async def _guard(self, interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Ce n'est pas ta commande.", ephemeral=True)
            return False
        member = self.guild.get_member(interaction.user.id) if self.guild else None
        if member is None or not _is_staff(member):
            await interaction.response.send_message("Réservé au staff.", ephemeral=True)
            return False
        return True

    def _finish(self, result):
        self.result = result
        if not self.future.done():
            self.future.set_result(result)
        self.stop()

    @discord.ui.button(label="Confirmer et distribuer", emoji="✅", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._guard(interaction):
            return
        self._finish("confirm")
        try:
            await interaction.response.edit_message(content="⏳ Distribution en cours…", view=None)
        except discord.HTTPException:
            pass

    @discord.ui.button(label="Annuler", emoji="✖️", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._guard(interaction):
            return
        self._finish("cancel")
        try:
            await interaction.response.edit_message(content="❌ Distribution annulée.", view=None)
        except discord.HTTPException:
            pass

    async def on_timeout(self):
        if not self.future.done():
            self.future.set_result("__timeout__")


class Recompense(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._busy = False  # verrou : interdit deux exécutions simultanées

    # ---------- classification ----------
    def _classify(self, char):
        """Retourne (status, plan, label). status : a_donner / ignoree_reroll / sans_recompense /
        deja_applique / erreur. plan : ('xp', None, montant) | ('item', item_id, qty) | None."""
        rtype = char["recompense_type"]
        detail = char["recompense_detail"] or ""
        if rtype is None:
            return "sans_recompense", None, "Aucune récompense"
        if rtype.startswith("reroll"):
            return "ignoree_reroll", None, (detail or rtype)
        if rtype == "argent":
            # Déjà crédité à la création du compte bancaire -> jamais redonné ici.
            return "deja_applique", None, (detail or "Argent")
        if rtype == "xp":
            amount = _parse_amount(_detail_after_dash(detail))
            if amount <= 0:
                return "erreur", None, f"XP illisible : « {detail} »"
            return "a_donner", ("xp", None, amount), f"XP — {amount}"
        if rtype.startswith("parchemin"):
            name = PARCHEMIN_ITEM_NAMES.get(rtype)
            item = db.get_item_by_name(name) if name else None
            if item is None:
                return "erreur", None, f"Parchemin introuvable : {rtype}"
            qty = _parse_qty(detail)
            return "a_donner", ("item", item["id"], qty), f"{name} ×{qty}"
        if rtype.startswith("relique") or rtype.startswith("arme"):
            classe = rtype.split("_")[-1].upper()        # '4' ; 's' -> 'S'
            cat = "Relique" if rtype.startswith("relique") else "Arme maudite"
            item = db.get_item_by_category_classe(cat, classe)
            if item is None:
                return "erreur", None, f"{cat} classe {classe} introuvable"
            return "a_donner", ("item", item["id"], 1), f"{item['name']} ×1"
        return "erreur", None, f"Type inconnu : {rtype}"

    # ---------- application (SANS multiplicateur) ----------
    def _apply_reward(self, character_id, plan):
        kind, item_id, amount = plan
        # Seule exception (pour l'instant) à la règle VIP/Booster ×2 : le don de départ reste exact pour tous.
        if kind == "xp":
            db.apply_xp_cascade(character_id, amount)            # cascade, jamais de ×2
        elif kind == "item":
            from cogs.shop import inv_give                        # reçu gratuitement (gifted_quantity)
            inv_give(character_id, item_id, amount)

    # ---------- rendu ----------
    @staticmethod
    def _line(char, label):
        nom = char["character_name"] or f"#{char['id']}"
        return f"• <@{char['user_id']}> — **{nom}** (slot {char['slot_number']}) → {label}"

    def _preview_embeds(self, a_donner, reroll, sans, argent, err):
        lignes = [self._line(c, lbl) for (c, _s, _p, lbl) in a_donner] or ["(aucun)"]
        embeds = []
        for i in range(0, len(lignes), 15):
            embeds.append(discord.Embed(
                title="🎁 Aperçu — récompenses de départ à distribuer"
                      + (f" ({i // 15 + 1})" if len(lignes) > 15 else ""),
                description="\n".join(lignes[i:i + 15]), color=discord.Color.purple()))
        if err:
            detail = "\n".join(f"• <@{c['user_id']}> — {lbl}" for (c, _s, _p, lbl) in err[:15])
            embeds.append(discord.Embed(
                title="⚠️ À vérifier (non distribués)", description=detail, color=discord.Color.red()))
        recap = (f"**{len(a_donner)}** à donner · **{len(reroll)}** ignorés (reroll) · "
                 f"**{len(sans)}** sans récompense · **{len(argent)}** argent déjà donné · "
                 f"**{len(err)}** erreur(s)")
        embeds[-1].add_field(name="Compteur", value=recap, inline=False)
        embeds[-1].set_footer(text="Rien n'est donné tant que tu n'as pas cliqué « Confirmer ».")
        return embeds

    # ---------- commande ----------
    @app_commands.command(name="récompense-add",
                          description="Distribue la récompense de départ manquante (staff)")
    async def recompense_add(self, interaction: discord.Interaction):
        if not _is_staff(interaction.user):
            await interaction.response.send_message("❌ Réservé au staff.", ephemeral=True)
            return
        if self._busy:
            await interaction.response.send_message(
                "⏳ Une distribution est déjà en cours. Réessaie quand elle est terminée.", ephemeral=True)
            return
        self._busy = True
        try:
            await interaction.response.send_message(
                "✅ Aperçu envoyé en MP (rien n'est distribué avant ta confirmation).", ephemeral=True)
            try:
                dm = await interaction.user.create_dm()
            except discord.HTTPException:
                await interaction.followup.send(
                    "❌ Je n'ai pas pu t'écrire en MP. Ouvre tes messages privés puis relance.",
                    ephemeral=True)
                return

            classified = [(c, *self._classify(c)) for c in db.reward_start_candidates()]
            a_donner = [x for x in classified if x[1] == "a_donner"]
            reroll = [x for x in classified if x[1] == "ignoree_reroll"]
            sans = [x for x in classified if x[1] == "sans_recompense"]
            argent = [x for x in classified if x[1] == "deja_applique"]
            err = [x for x in classified if x[1] == "erreur"]

            if not classified:
                await dm.send("✅ Rien à faire : tous les personnages validés ont déjà été traités.")
                return

            view = _ConfirmView(interaction.user.id, interaction.guild)
            embeds = self._preview_embeds(a_donner, reroll, sans, argent, err)
            for idx in range(0, len(embeds), 10):
                grp = embeds[idx:idx + 10]
                last = idx + 10 >= len(embeds)
                await dm.send(embeds=grp, view=view if last else None)

            choix = await view.future
            if choix != "confirm":
                if choix == "__timeout__":
                    try:
                        await dm.send("⏱️ Délai dépassé — distribution annulée, rien n'a été donné.")
                    except discord.HTTPException:
                        pass
                return

            await self._distribute(interaction, dm)
        finally:
            self._busy = False

    # ---------- distribution + compte rendu ----------
    async def _distribute(self, interaction, dm):
        staff_id = interaction.user.id
        now = datetime.now(timezone.utc).isoformat()
        # Recalcul FRAIS au moment de la confirmation (évite tout état périmé / double).
        classified = [(c, *self._classify(c)) for c in db.reward_start_candidates()]
        served, errors = [], []
        reroll = sans = argent = 0

        for (char, status, plan, label) in classified:
            if status == "a_donner":
                try:
                    self._apply_reward(char["id"], plan)                       # fonctions existantes
                    db.reward_start_insert(char["id"], now, staff_id, label, "donnee")
                    served.append((char, label))
                except Exception as e:
                    # Pas de try/except silencieux : l'échec n'est PAS enregistré (repris au prochain run)
                    # et apparaît dans le compte rendu.
                    errors.append((char, label, repr(e)))
            elif status == "ignoree_reroll":
                db.reward_start_insert(char["id"], now, staff_id, label, "ignoree_reroll")
                reroll += 1
            elif status == "sans_recompense":
                db.reward_start_insert(char["id"], now, staff_id, label, "sans_recompense")
                sans += 1
            elif status == "deja_applique":
                db.reward_start_insert(char["id"], now, staff_id, label, "deja_applique")
                argent += 1
            else:  # erreur de classification (item introuvable, etc.) -> non enregistré
                errors.append((char, label, "classification"))

        await self._send_report(interaction, dm, served, reroll, sans, argent, errors)

    async def _send_report(self, interaction, dm, served, reroll, sans, argent, errors):
        lignes = [f"• <@{c['user_id']}> — **{c['character_name'] or '#' + str(c['id'])}** "
                  f"(slot {c['slot_number']}) → {lbl}" for (c, lbl) in served]
        blocs = []
        if lignes:
            blocs.append("**🎁 Récompenses distribuées :**\n" + "\n".join(lignes))
        else:
            blocs.append("**🎁 Récompenses distribuées :** aucune.")
        blocs.append(f"**Ignorés :** {reroll} reroll · {sans} sans récompense · {argent} argent déjà donné.")
        if errors:
            blocs.append("**⚠️ Erreurs (non distribués, repris au prochain run) :**\n"
                         + "\n".join(f"• <@{c['user_id']}> — {lbl} ({raison})"
                                     for (c, lbl, raison) in errors))
        blocs.append(f"**Total servi :** {len(served)} · **erreurs :** {len(errors)}")

        # Découpe en messages <= 1900 caractères.
        messages, cur = [], ""
        for b in blocs:
            if len(cur) + len(b) + 2 > 1900 and cur:
                messages.append(cur)
                cur = ""
            cur += (("\n\n" if cur else "") + b)
        if cur:
            messages.append(cur)

        sent_ok = True
        for m in messages:
            try:
                await dm.send(m)
            except discord.HTTPException:
                sent_ok = False
                break
        if not sent_ok:
            # MP fermés en cours de route : bascule le compte rendu en éphémère.
            try:
                await interaction.followup.send("\n\n".join(messages)[:1900], ephemeral=True)
            except discord.HTTPException:
                pass


async def setup(bot):
    await bot.add_cog(Recompense(bot))
