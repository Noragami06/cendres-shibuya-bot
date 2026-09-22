# -*- coding: utf-8 -*-
"""
combat_engine.py — Moteur de combat PARTAGÉ entre /daily et /raid.

Deux responsabilités centralisées ici (plus de logique dupliquée dans les cogs) :

1. Progression du critique « Black Flash » (crit_next) — voir §1.
2. Résolution d'UN round en ALTERNANCE STRICTE (apply_actor_action) : un seul camp agit par round,
   l'autre a déjà vu ce choix avant de décider au round suivant. Le blocage est une POSTURE qui reste
   en attente jusqu'à ce qu'une vraie attaque adverse la teste (le compteur dégressif ne monte qu'à ce
   moment là) — voir §2/§4.

Le clash « Force vs Force » de l'ancien modèle SIMULTANÉ n'existe plus : sans simultanéité, chaque
attaque porte seule (contre-attaque = on attaque à son tour, ce qui inflige ses dégâts), et la défense
passe entièrement par la posture de blocage.

Import paresseux des constantes de /daily (block_chance, seuils de blocage/sort, ×10 du critique) DANS
les fonctions, pour éviter tout cycle d'import au chargement (daily importe ce module au niveau module).
"""

import random

# =====================================================================
# §1 : PROGRESSION DU CRITIQUE (BLACK FLASH)
# =====================================================================
DAILY_CRIT_RESET_VALUE = 1     # chance après un critique réussi (et valeur de départ d'un combat)
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
# ÉTAT DE COMBAT — champs d'alternance/critique/blocage
# =====================================================================
def init_combat_fields(st):
    """Initialise (sans écraser l'existant) les champs de l'alternance stricte + critique dans l'état st."""
    st.setdefault("crit_chance_j", DAILY_CRIT_RESET_VALUE)
    st.setdefault("crit_echecs_j", 0)
    st.setdefault("block_pending_j", False)  # posture de blocage du joueur en attente d'une attaque
    st.setdefault("block_pending_p", False)  # posture de blocage du PNJ/boss en attente d'une attaque
    st.setdefault("bloc_j", 0)               # compteur de blocages RÉELLEMENT testés (chance dégressive)
    st.setdefault("bloc_p", 0)
    st.setdefault("sort_bonus_j", 0)
    st.setdefault("last_action_j", None)
    st.setdefault("last_action_p", None)


def _apply_hit(st, attack, defender, defender_blocking):
    """Applique une attaque sur le défenseur ('p' PNJ / 'j' joueur). Le compteur de blocage dégressif
    n'est incrémenté QUE si une posture de blocage est réellement testée (§4). Retourne (dégâts, bloqué)."""
    from cogs.daily import block_chance, DAILY_SPELL_BLOCK_CHANCE, DAILY_SPELL_BLOCK_REDUCTION
    dmg = attack["damage"]
    block_ok = False
    if defender_blocking:
        if attack["dtype"] == "spell":
            if random.randint(1, 100) <= DAILY_SPELL_BLOCK_CHANCE:
                dmg = round(dmg * (100 - DAILY_SPELL_BLOCK_REDUCTION) / 100)
                block_ok = True
        else:
            key = "bloc_p" if defender == "p" else "bloc_j"
            st[key] = st.get(key, 0) + 1  # testé -> le compteur monte (chance dégressive)
            if random.randint(1, 100) <= block_chance(st[key]):
                dmg = 0
                block_ok = True
    if defender == "p":
        st["pv_p"] -= dmg
    else:
        st["pv_j"] -= dmg
    return dmg, block_ok


def apply_actor_action(st, action, actor_is_player, gains=None):
    """§2 : résout l'action d'UN SEUL camp pour ce round (alternance stricte).
    Retourne (texte, clé_couleur, crit_reussi).
    - Le joueur qui attaque : critique Black Flash possible (attaque physique), imblocable ×10 ; sinon
      dégâts soumis à la posture de blocage EN COURS de l'adversaire (consommée par la tentative).
    - Le joueur qui bloque : arme sa posture (block_pending_j), aucun dégât ce round.
    - Le PNJ/Boss : attaque (soumise à la posture du joueur) ou arme sa posture (block_pending_p).
    gains (optionnel) : compteur /daily (force sur un coup porté par le joueur, endurance sur un blocage
    réussi du joueur)."""
    from cogs.daily import DAILY_CRIT_MULTIPLIER
    nj, npnj = st["name_j"], st["name_p"]
    crit_reussi = False
    crit_text = ""

    if actor_is_player:
        aj = dict(action)  # copie : ne jamais muter le dict d'action de l'appelant
        if aj.get("attacking"):
            # Critique : action « Attaquer » (physique) uniquement.
            if aj.get("kind") == "attaquer":
                chance = st.get("crit_chance_j", DAILY_CRIT_RESET_VALUE)
                echecs = st.get("crit_echecs_j", 0)
                hit = random.randint(1, 100) <= chance
                st["crit_chance_j"], st["crit_echecs_j"] = crit_next(chance, hit, echecs)
                if hit:
                    crit_reussi = True
                    aj["damage"] = aj["damage"] * DAILY_CRIT_MULTIPLIER
                    crit_text = ("\n💥 **BLACK FLASH !** Le coup critique inflige **×10 dégâts**, "
                                 "imblocable !")
            blocking = st.get("block_pending_p", False) and not crit_reussi
            dealt, block_ok = _apply_hit(st, aj, defender="p", defender_blocking=blocking)
            st["block_pending_p"] = False  # posture consommée par cette tentative d'attaque
            if block_ok:
                return f"🛡️ **{npnj}** bloque l'attaque de **{nj}** — aucun dégât." + crit_text, "blue", crit_reussi
            if dealt > 0 and gains is not None:
                gains["force"] = gains.get("force", 0) + 1
            if aj["dtype"] == "spell":
                sn = aj.get("spell_name") or "un sort"
                verbe = "frappe avec" if aj.get("kind") == "arme" else "lance"
                text = f"✨ **{nj}** {verbe} **{sn}** et inflige **{dealt:,}** dégâts à {npnj} !"
            else:
                text = f"🗡️ **{nj}** attaque et inflige **{dealt:,}** dégâts à {npnj} !"
            return text.replace(",", " ") + crit_text, "green", crit_reussi
        # Bloquer : arme la posture.
        st["block_pending_j"] = True
        return f"🌀 **{nj}** se met en garde, prêt à parer la prochaine attaque.", "blue", False

    # --- Camp PNJ / Boss ---
    ap = action
    if ap.get("attacking"):
        blocking = st.get("block_pending_j", False)
        dealt, block_ok = _apply_hit(st, ap, defender="j", defender_blocking=blocking)
        st["block_pending_j"] = False  # posture consommée par l'attaque adverse
        if block_ok:
            if gains is not None:
                gains["endurance"] = gains.get("endurance", 0) + 1
            return f"🛡️ **Blocage réussi !** {nj} n'a subi aucun dégât de {npnj}.", "blue", False
        return (f"💥 **{npnj}** attaque et inflige **{dealt:,}** dégâts à {nj} !".replace(",", " "),
                "red", False)
    # Bloquer : arme la posture.
    st["block_pending_p"] = True
    return f"🛡️ **{npnj}** se met en garde.", "blue", False
