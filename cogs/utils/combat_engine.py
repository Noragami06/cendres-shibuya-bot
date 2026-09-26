# -*- coding: utf-8 -*-
"""
combat_engine.py — Moteur de combat PARTAGÉ entre /daily et /raid.

Modèle : ALTERNANCE CONTINUE avec RÉACTION EN DIRECT.
Un côté DÉCLARE une action (menu complet). L'autre côté, qui a VU ce choix, REND une réaction en direct
(menu de réaction). L'échange est ensuite résolu ensemble :
  - Attaquer (physique) vs Attaquer (physique) -> CLASH (comparaison de Force ; égalité = annulation ;
    un critique Black Flash remporte automatiquement le clash) ;
  - Attaque vs Défendre -> blocage classique (chance dégressive) + renforcement défensif optionnel (§5) ;
  - Sort/Arme vs Défendre -> blocage de sort (30 % / -50 %) + renforcement défensif éventuel ;
  - sinon (deux actions offensives non-clash, potion/renfort, etc.) -> résolution indépendante.
Puis les rôles déclarant/réagissant s'inversent : celui qui vient de réagir déclare l'échange suivant.

Ce module centralise :
- §4 la progression du critique (crit_next + constantes) ;
- la résolution d'UN échange (resolve_exchange, pure/testable) ;
- §1 l'estimation de taux de victoire (estimate_win_rate) ;
- §6 la boucle 1v1 complète (run_combat) qui pilote déclaration/réaction/rythme/inversion des rôles.

Import PARESSEUX des constantes de /daily dans les fonctions (block_chance, seuils, ×10 du critique),
pour éviter tout cycle d'import au chargement (daily importe ce module au niveau module).
"""

import asyncio
import inspect
import random

import discord

# =====================================================================
# §4 : PROGRESSION DU CRITIQUE (BLACK FLASH)
# =====================================================================
DAILY_CRIT_RESET_VALUE = 1     # chance de départ ET après un critique réussi
DAILY_CRIT_STEP_1 = 5          # après le 1er échec consécutif
DAILY_CRIT_STEP_2 = 10         # après le 2e échec consécutif
# à partir du 3e échec : +1 %/échec (11, 12, 13...)


def crit_next(chance, hit, echecs_consecutifs):
    """Nouvelle (chance, echecs_consecutifs) après une tentative de critique.
    - hit=True  : retombe à DAILY_CRIT_RESET_VALUE, compteur d'échecs remis à 0 ;
    - hit=False : 1er échec -> 5 %, 2e -> 10 %, puis +1 %/échec (11, 12, ...)."""
    if hit:
        return DAILY_CRIT_RESET_VALUE, 0
    echecs_consecutifs += 1
    if echecs_consecutifs == 1:
        nouvelle_chance = DAILY_CRIT_STEP_1
    elif echecs_consecutifs == 2:
        nouvelle_chance = DAILY_CRIT_STEP_2
    else:
        nouvelle_chance = DAILY_CRIT_STEP_2 + (echecs_consecutifs - 2)
    return nouvelle_chance, echecs_consecutifs


# =====================================================================
# §1 : ESTIMATION DU TAUX DE VICTOIRE (conseil de difficulté)
# =====================================================================
# Le taux est dérivé de l'écart de classe (gap = rang[classe visée] - rang[classe RP du joueur]), qui
# porte déjà toute la calibration de difficulté (multiplicateur de référence × facteur d'écart). Plus le
# gap est négatif (adversaire d'une classe inférieure à la sienne), plus la victoire est probable.
_WIN_RATE_BY_GAP = {-4: 97, -3: 96, -2: 92, -1: 82, 0: 58, 1: 32, 2: 14, 3: 4, 4: 2}


def estimate_win_rate(gap: int) -> int:
    """Taux de victoire estimé (%) pour un écart de classe donné (borné à [-4, 4])."""
    return _WIN_RATE_BY_GAP[max(-4, min(4, gap))]


# =====================================================================
# ÉTAT DE COMBAT — champs communs
# =====================================================================
def init_combat_fields(st):
    """Initialise (sans écraser l'existant) les champs de critique/blocage dans l'état st."""
    st.setdefault("crit_chance_j", DAILY_CRIT_RESET_VALUE)
    st.setdefault("crit_echecs_j", 0)
    st.setdefault("bloc_j", 0)   # compteur de blocages physiques RÉELLEMENT testés (chance dégressive)
    st.setdefault("bloc_p", 0)
    st.setdefault("sort_bonus_j", 0)


def _apply_hit(st, attack, defender, defender_action, crit_bypass=False):
    """Applique `attack` sur le défenseur ('p' PNJ / 'j' joueur), en tenant compte d'une éventuelle
    DÉFENSE du défenseur (defender_action de kind 'defendre', avec renforcement défensif optionnel §5).
    - Le compteur de blocage physique dégressif n'est incrémenté QUE si une défense est réellement testée.
    - Renforcement défensif (§5) : l'EO n'est débitée QUE si le blocage classique n'a pas déjà tout annulé.
    Retourne (dégâts_infligés, blocage_total_ok, renfort_defensif_utilisé)."""
    from cogs.daily import block_chance, DAILY_SPELL_BLOCK_CHANCE, DAILY_SPELL_BLOCK_REDUCTION
    dmg = attack["damage"]
    block_ok = False
    def_used = False
    defending = (defender_action is not None
                 and defender_action.get("kind") == "defendre" and not crit_bypass)
    if defending:
        if attack["dtype"] == "spell":
            if random.randint(1, 100) <= DAILY_SPELL_BLOCK_CHANCE:
                dmg = round(dmg * (100 - DAILY_SPELL_BLOCK_REDUCTION) / 100)
                block_ok = (dmg == 0)
        else:
            key = "bloc_p" if defender == "p" else "bloc_j"
            st[key] = st.get(key, 0) + 1  # testé -> le compteur monte (chance dégressive)
            if random.randint(1, 100) <= block_chance(st[key]):
                dmg = 0
                block_ok = True
        # §5 : renforcement défensif — appliqué UNIQUEMENT s'il reste des dégâts (blocage insuffisant).
        if dmg > 0 and defender_action.get("def_reinforce", 0) > 0:
            amount = defender_action["def_reinforce"]
            eo_key = "eo_p" if defender == "p" else "eo_j"
            st[eo_key] = max(0, st.get(eo_key, 0) - amount)  # débité maintenant (le blocage a échoué)
            boosted = defender_action.get("def_endurance", 0) + amount
            def_used = True
            if boosted >= dmg:
                dmg = 0
            else:
                dmg = max(0, dmg - boosted)
    if defender == "p":
        st["pv_p"] -= dmg
    else:
        st["pv_j"] -= dmg
    return dmg, block_ok, def_used


def resolve_exchange(st, aj, ap, gains=None):
    """Résout UN échange : action du joueur (aj) + action du PNJ/boss (ap), collectées en ordre
    déclaration/réaction par l'appelant. Retourne (texte, clé_couleur, crit_reussi) et mute st.
    j = joueur (peut critiquer), p = PNJ/boss (ne critique jamais)."""
    from cogs.daily import DAILY_CRIT_MULTIPLIER
    nj, npnj = st["name_j"], st["name_p"]
    crit_text = ""
    crit_reussi = False
    player_dealt = 0
    player_took = 0

    # Critique Black Flash : action « Attaquer » (physique) du JOUEUR uniquement.
    if aj.get("kind") == "attaquer":
        aj = dict(aj)  # copie : ne jamais muter le dict de l'appelant
        chance = st.get("crit_chance_j", DAILY_CRIT_RESET_VALUE)
        echecs = st.get("crit_echecs_j", 0)
        hit = random.randint(1, 100) <= chance
        st["crit_chance_j"], st["crit_echecs_j"] = crit_next(chance, hit, echecs)
        if hit:
            crit_reussi = True
            aj["damage"] = aj["damage"] * DAILY_CRIT_MULTIPLIER
            crit_text = "\n💥 **BLACK FLASH !** Le coup critique inflige **×10 dégâts**, imblocable !"

    f_j = aj.get("force_actuelle", 0)
    f_p = ap.get("force_actuelle", 0)

    # CLASH : les DEUX déclarent une attaque PHYSIQUE pure.
    if aj.get("kind") == "attaquer" and ap.get("kind") == "attaquer":
        entete = ("⚔️ **Choc frontal !**\n"
                  f"**{nj}** : **{f_j:,}** de Force\n"
                  f"**{npnj}** : **{f_p:,}** de Force\n\n").replace(",", " ")
        if crit_reussi:  # le critique remporte AUTOMATIQUEMENT le clash
            st["pv_p"] -= aj["damage"]
            if gains is not None:
                gains["force"] = gains.get("force", 0) + 1
            return (entete + f"🏆 **{nj}** pulvérise la garde et inflige **{aj['damage']:,}** dégâts à {npnj} !".replace(",", " ") + crit_text,
                    "green", True)
        if f_j == f_p:
            return (entete + f"⚔️ **Clash égal !** Le choc s'annule, aucun dégât." + crit_text, "blue", False)
        if f_j > f_p:
            st["pv_p"] -= aj["damage"]
            if gains is not None:
                gains["force"] = gains.get("force", 0) + 1
            txt = f"🏆 **{nj}** remporte le choc et inflige **{aj['damage']:,}** dégâts à {npnj} !"
            return (entete + txt.replace(",", " ") + crit_text, "green", False)
        st["pv_j"] -= ap["damage"]
        txt = f"🏆 **{npnj}** remporte le choc et inflige **{ap['damage']:,}** dégâts à {nj} !"
        return (entete + txt.replace(",", " ") + crit_text, "red", False)

    # Sinon : résolution indépendante (attaque-vs-défense, sort/arme-vs-défense, trade, ou neutre).
    parts = []
    if aj.get("kind") == "potion":
        parts.append(f"🧪 **{nj}** utilise une potion.")
    if aj.get("attacking"):  # le joueur attaque le PNJ ; le PNJ défend-il ?
        pnj_def = ap if ap.get("kind") == "defendre" else None
        dealt, block_ok, _ = _apply_hit(st, aj, "p", pnj_def, crit_bypass=crit_reussi)
        if dealt > 0:
            player_dealt += dealt
            if gains is not None:
                gains["force"] = gains.get("force", 0) + 1
            if aj["dtype"] == "spell":
                sn = aj.get("spell_name") or "un sort"
                verbe = "frappe avec" if aj.get("kind") == "arme" else "lance"
                parts.append(f"✨ **{nj}** {verbe} **{sn}** et inflige **{dealt:,}** dégâts à {npnj} !".replace(",", " "))
            else:
                parts.append(f"🗡️ **{nj}** attaque et inflige **{dealt:,}** dégâts à {npnj} !".replace(",", " "))
        else:
            parts.append(f"🛡️ **{npnj}** encaisse : l'attaque de {nj} est neutralisée.")
    if ap.get("attacking"):  # le PNJ attaque le joueur ; le joueur défend-il ?
        player_def = aj if aj.get("kind") == "defendre" else None
        dealt, block_ok, def_used = _apply_hit(st, ap, "j", player_def)
        if dealt <= 0 and player_def is not None:
            if gains is not None:
                gains["endurance"] = gains.get("endurance", 0) + 1
            extra = " (renforcement défensif)" if def_used else ""
            parts.append(f"🛡️ **Défense réussie !** {nj} n'a subi aucun dégât{extra}.")
        elif dealt > 0:
            player_took += dealt
            parts.append(f"💥 **{npnj}** attaque et inflige **{dealt:,}** dégâts à {nj} !".replace(",", " "))

    if not aj.get("attacking") and not ap.get("attacking") and aj.get("kind") != "potion":
        parts.append(f"🌀 **{nj}** et **{npnj}** se jaugent, prêts à réagir.")

    text = ("\n".join(parts) if parts else f"{nj} et {npnj} s'observent.") + crit_text
    color = "green" if player_dealt > 0 else ("red" if player_took > 0 else "blue")
    return text, color, crit_reussi


# =====================================================================
# §6 : BOUCLE 1v1 — ALTERNANCE CONTINUE + RÉACTION EN DIRECT
# =====================================================================
def _potential_text(action):
    if action.get("attacking"):
        if action.get("dtype") == "spell":
            sn = action.get("spell_name") or "une technique"
            return f"prépare **{sn}** (~{action.get('damage', 0):,} dégâts)".replace(",", " ")
        return f"s'élance à l'attaque (~{action.get('damage', 0):,} dégâts)".replace(",", " ")
    if action.get("kind") == "defendre":
        return "se met en **garde**"
    if action.get("kind") == "renfort":
        return "canalise son énergie occulte"
    if action.get("kind") == "potion":
        return "boit une potion"
    return "temporise"


def _reaction_text(rea, incoming):
    """Libellé de la réaction, contextualisé par l'action reçue (incoming)."""
    kind = rea.get("kind")
    incoming_is_spell = incoming.get("dtype") == "spell"
    incoming_is_attack = incoming.get("attacking")
    if kind == "attaquer":
        return "**contre-attaque !**" if incoming_is_attack else "**attaque !**"
    if kind == "defendre":
        if incoming_is_spell:
            return "**tente de bloquer le sort !**"
        return "**se met en garde !**"
    if kind in ("sort", "arme"):
        sn = rea.get("spell_name") or ("une arme maudite" if kind == "arme" else "un sort")
        return f"**riposte avec {sn} !**"
    return "**réagit.**"


async def _maybe_await(value):
    if inspect.isawaitable(value):
        return await value
    return value


async def run_combat(*, channel, st, gains, is_player_vip,
                     player_declare, player_react, pnj_declare, pnj_react,
                     make_round_embed, play_crit, pv_floor,
                     phoenix_color, on_exchange=None):
    """Pilote un combat 1v1 en alternance continue. Retourne 'victoire' (PNJ à terre), 'defaite'
    (joueur à terre) ou 'interrupt' (timeout d'un choix joueur).

    Collecteurs fournis par l'appelant (thin, propres à /daily ou /raid) :
      - player_declare()            -> action complète (gère le sous-menu Renfort/Potion sans consommer
                                       le tour), ou None (timeout) ;
      - player_react(incoming)      -> réaction (Attaquer/Défendre[+renfort défensif]/Sort/Arme), ou None ;
      - pnj_declare() / pnj_react(incoming) -> action IA (sync ou coroutine).
    make_round_embed(text, color)   -> discord.Embed du verdict.
    play_crit(nom)                  -> coroutine d'animation Black Flash.
    on_exchange()                   -> hook optionnel après chaque échange (trackers /raid, etc.)."""
    seuil = 70 if is_player_vip else 50
    declarant = "j" if random.randint(1, 100) <= seuil else "p"

    while True:
        reactant = "p" if declarant == "j" else "j"

        # 1. Déclaration.
        if declarant == "j":
            decl = await player_declare()
            if decl is None:
                return "interrupt"
        else:
            decl = await _maybe_await(pnj_declare())

        await asyncio.sleep(1)
        # 3. Annonce de la déclaration.
        nom_decl = st["name_j"] if declarant == "j" else st["name_p"]
        try:
            await channel.send(embed=discord.Embed(
                description=f"🔹 **{nom_decl}** {_potential_text(decl)}…", color=phoenix_color))
        except discord.HTTPException:
            pass
        await asyncio.sleep(1)

        # 4. Réaction (menu complet en direct).
        if reactant == "j":
            rea = await player_react(decl)
            if rea is None:
                return "interrupt"
        else:
            rea = await _maybe_await(pnj_react(decl))
        # 5. Annonce de la réaction : embed DÉDIÉ et TOUJOURS visible (joueur comme PNJ), distinct de
        # l'annonce de déclaration (étape 3) et du verdict (étape 7).
        nom_rea = st["name_j"] if reactant == "j" else st["name_p"]
        try:
            await channel.send(embed=discord.Embed(
                description=f"⚡ **{nom_rea}** réagit : {_reaction_text(rea, decl)}", color=phoenix_color))
        except discord.HTTPException:
            pass
        await asyncio.sleep(1)

        # 6. Résolution de l'échange (j = joueur, p = PNJ).
        aj = decl if declarant == "j" else rea
        ap = rea if declarant == "j" else decl
        text, color, crit = resolve_exchange(st, aj, ap, gains)
        if crit and play_crit is not None:
            await play_crit(st["name_j"])

        # 7. Verdict + PV à jour.
        try:
            await channel.send(embed=make_round_embed(text, color))
        except discord.HTTPException:
            pass
        await asyncio.sleep(1)

        if on_exchange is not None:
            await _maybe_await(on_exchange())

        # Fin de combat (un seul échange peut faire tomber un camp).
        if st["pv_p"] <= pv_floor:
            return "victoire"
        if st["pv_j"] <= pv_floor:
            return "defaite"

        # 9. Inversion des rôles : celui qui vient de réagir déclare l'échange suivant.
        declarant = reactant
