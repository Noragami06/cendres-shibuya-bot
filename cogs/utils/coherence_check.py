# -*- coding: utf-8 -*-
"""
coherence_check.py — Logique PARTAGÉE de détection d'incohérences (lecture seule).

Source de vérité UNIQUE des vérifications de cohérence : utilisée à la fois par le script autonome
check_coherence.py et par le status_loop (rapport périodique). Ne modifie jamais la base.

Point d'entrée principal :
    run_coherence_check() -> list[str]
        Retourne la liste des lignes d'incohérence (préfixées « ❌ » pour les erreurs, « ℹ️  » pour
        les points à vérifier manuellement). Liste vide si tout est cohérent.

Les primitives (ouverture base, chargement barème, collecte des rôles du code) sont aussi exportées
pour que check_coherence.py affiche son rapport détaillé SANS dupliquer la logique de détection.
"""

import os
import sqlite3
import sys
from collections import Counter, defaultdict

# Racine du projet (…/cogs/utils/coherence_check.py -> racine), pour importer les cogs et trouver la base.
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

DB_PATH = os.path.join(_ROOT, "data", "bot.db")
PCT_TOLERANCE = 0.05  # tolérance d'arrondi pour les sommes de pourcentages

# Types de rôles « sémantiques » attendus dans le barème (role_point_values).
SEMANTIC_KINDS = {"camp", "clan", "grade"}

# Valeurs validées le 2026-08-21 : coût en % de la réserve d'énergie occulte du joueur (jamais sous 5%,
# jamais au dessus de 40%), fourchette de dégâts par classe. La progression de niveau (+50 à +100 dégâts
# par niveau, pas encore figée) s'applique EN PLUS de cette fourchette de base, à l'intérieur d'un même sort.
SPELL_CLASS_VALUES = {
    "4": {"label": "Passif", "cout_pct": 5, "degats_min": 10, "degats_max": 50},
    "3": {"label": "Normal", "cout_pct": 13, "degats_min": 50, "degats_max": 200},
    "2": {"label": "Avancé", "cout_pct": 21, "degats_min": 200, "degats_max": 600},
    "1": {"label": "Avancé+", "cout_pct": 29, "degats_min": 600, "degats_max": 1500},
    "S": {"label": "Ultime", "cout_pct": 40, "degats_min": 1500, "degats_max": 5000},
}
# Ordre croissant de puissance des classes de sorts (4 = plus faible … S = ultime).
SPELL_CLASS_ORDER = ["4", "3", "2", "1", "S"]


# =====================================================================
# PRIMITIVES PARTAGÉES
# =====================================================================
def pct_sum_ok(total) -> bool:
    return abs(float(total) - 100.0) <= PCT_TOLERANCE


def import_cogs() -> dict:
    """Importe les cogs porteurs de constantes. Silencieux : un cog manquant est simplement absent
    du dict (les vérifications qui en dépendent le signaleront)."""
    mods = {}
    for name in ("depart", "ticket", "ordre"):
        try:
            mods[name] = __import__(f"cogs.{name}", fromlist=[name])
        except Exception:
            pass
    return mods


def open_db():
    """Connexion SQLite EN LECTURE SEULE à data/bot.db, ou None si la base est absente."""
    if not os.path.exists(DB_PATH):
        return None
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def load_role_point_values(conn):
    """{role_id: (category, points)} depuis role_point_values, ou None si la table n'existe pas."""
    if conn is None:
        return None
    try:
        rows = conn.execute("SELECT role_id, category, points FROM role_point_values").fetchall()
    except sqlite3.OperationalError:
        return None
    return {r["role_id"]: (r["category"], r["points"]) for r in rows}


def collect_code_roles(mods) -> list:
    """Rôles codés en dur dans les cogs : liste de tuples (role_id, kind, label, source)."""
    roles = []

    def add(role_id, kind, label, source):
        if isinstance(role_id, int):
            roles.append((role_id, kind, label, source))

    depart = mods.get("depart")
    if depart is not None:
        add(getattr(depart, "ROLE_EXORCISTE", None), "camp", "Exorciste", "depart.ROLE_EXORCISTE")
        add(getattr(depart, "ROLE_HYBRIDE", None), "camp", "Hybride", "depart.ROLE_HYBRIDE")
        add(getattr(depart, "ROLE_HUMAIN", None), "camp", "Humain", "depart.ROLE_HUMAIN")
        default_state = getattr(depart, "DEFAULT_CLAN_STATE", None)
        if isinstance(default_state, dict):
            for clan_key, info in default_state.get("clans", {}).items():
                add(info.get("role_id"), "clan", clan_key.capitalize(),
                    f"depart.DEFAULT_CLAN_STATE[{clan_key}]")
        add(getattr(depart, "SANS_CLAN_ROLE_ID", None), "clan", "Sans clan", "depart.SANS_CLAN_ROLE_ID")
        for name, rid in getattr(depart, "GRADE_ROLES", []) or []:
            add(rid, "grade", name, "depart.GRADE_ROLES")
        add(getattr(depart, "HERITIER_ROLE_ID", None), "grade", "Héritier (alias)",
            "depart.HERITIER_ROLE_ID")
        add(getattr(depart, "MEMBRES_PRINCIPAUX_ROLE_ID", None), "grade", "Membres principaux (alias)",
            "depart.MEMBRES_PRINCIPAUX_ROLE_ID")
        add(getattr(depart, "CLAN_MEMBER_ROLE_ID", None), "marker", "Appartient à un clan",
            "depart.CLAN_MEMBER_ROLE_ID")
        add(getattr(depart, "RCT_POSSEDE_ROLE_ID", None), "rct", "RCT possédée",
            "depart.RCT_POSSEDE_ROLE_ID")
        add(getattr(depart, "RCT_NON_POSSEDE_ROLE_ID", None), "rct", "RCT non possédée",
            "depart.RCT_NON_POSSEDE_ROLE_ID")
        add(getattr(depart, "DEPART_ROLE_ID", None), "depart", "Accès /départ", "depart.DEPART_ROLE_ID")
        add(getattr(depart, "FICHE_STAFF_ROLE_ID", None), "staff", "Staff fiche",
            "depart.FICHE_STAFF_ROLE_ID")

    ticket = mods.get("ticket")
    if ticket is not None:
        add(getattr(ticket, "STAFF_ROLE_ID", None), "staff", "Staff ticket", "ticket.STAFF_ROLE_ID")
        add(getattr(ticket, "TICKET_ACCESS_ROLE_ID", None), "access", "Accès tickets",
            "ticket.TICKET_ACCESS_ROLE_ID")
        add(getattr(ticket, "DEPART_ROLE_ID", None), "depart", "Accès /départ", "ticket.DEPART_ROLE_ID")

    ordre = mods.get("ordre")
    if ordre is not None:
        add(getattr(ordre, "STAFF_MANAGER_ROLE_ID", None), "manager", "Gestion staff d'ordre",
            "ordre.STAFF_MANAGER_ROLE_ID")
        add(getattr(ordre, "FICHE_STAFF_ROLE_ID", None), "staff", "Staff fiche",
            "ordre.FICHE_STAFF_ROLE_ID")

    return roles


def group_roles_by_id(code_roles) -> dict:
    """role_id -> [(kind, label, source), ...]."""
    by_id = defaultdict(list)
    for role_id, kind, label, source in code_roles:
        by_id[role_id].append((kind, label, source))
    return by_id


# =====================================================================
# VÉRIFICATIONS (chacune ajoute des messages PLAINS à errors / notes)
# =====================================================================
REWARD_TABLE_NAMES = [
    "REWARD_TABLE",
    "REWARD_TABLE_HYBRIDE_EXORCISTE",
    "REWARD_TABLE_HYBRIDE_FLEAUX",
    "REWARD_TABLE_HYBRIDE_SEUL",
]
# Tables de % supplémentaires du projet censées valoir 100 (dictionnaires clé -> %).
EXTRA_PCT_DICTS = ["SPELL_TABLE_BASE", "SPELL_TABLE_PARTIAL", "EO_NATURE_TABLE"]


def _check_reward_tables(depart, errors):
    if depart is None:
        errors.append("cogs.depart non importé : tables de récompenses non vérifiées.")
        return
    for name in REWARD_TABLE_NAMES:
        table = getattr(depart, name, None)
        if table is None:
            errors.append(f"Table de récompenses {name} introuvable dans cogs/depart.py.")
            continue
        total = round(sum(float(e.get("pct", 0)) for e in table), 4)
        if not pct_sum_ok(total):
            errors.append(f"{name} : somme des % = {total:.2f} (attendu 100 ± {PCT_TOLERANCE}).")
    # Tables de % annexes (sort / nature).
    for name in EXTRA_PCT_DICTS:
        table = getattr(depart, name, None)
        if isinstance(table, dict):
            total = round(sum(float(v) for v in table.values()), 4)
            if not pct_sum_ok(total):
                errors.append(f"{name} : somme des % = {total:.2f} (attendu 100).")


def _check_eo(depart, errors):
    if depart is None:
        return  # déjà signalé par _check_reward_tables
    table = getattr(depart, "EO_CLASS_TABLE", None)
    if not table:
        errors.append("EO_CLASS_TABLE absente de cogs/depart.py.")
        return
    items = list(table.items())  # ordre du dict = ordre des classes
    total = round(sum(float(info.get("pct", 0)) for _, info in items), 4)
    if not pct_sum_ok(total):
        errors.append(f"EO_CLASS_TABLE : somme des % = {total:.2f} (attendu 100).")
    for (k1, i1), (k2, i2) in zip(items, items[1:]):
        if i1.get("max") != i2.get("min"):
            errors.append(
                f"EO : {k1}.max ({i1.get('max')}) ≠ {k2}.min ({i2.get('min')}) — trou ou chevauchement.")


def _check_clan_base(depart, errors):
    if depart is None:
        return
    state = getattr(depart, "DEFAULT_CLAN_STATE", None)
    if not isinstance(state, dict):
        errors.append("DEFAULT_CLAN_STATE absent de cogs/depart.py.")
        return
    total = sum(float(info.get("base_pct", 0)) for info in state.get("clans", {}).values())
    total += float(state.get("sans_clan_pct", 0))
    total = round(total, 4)
    if not pct_sum_ok(total):
        errors.append(f"Pourcentages de base des clans (+ sans_clan) : somme = {total:.2f} (attendu 100).")


def _check_spell_classes(errors):
    """Classes de sorts (technique) : bornes du coût EO, progression stricte du coût, et enchaînement
    des fourchettes de dégâts (même logique que _check_eo). Basé sur la constante SPELL_CLASS_VALUES."""
    ordered = [(cle, SPELL_CLASS_VALUES[cle]) for cle in SPELL_CLASS_ORDER if cle in SPELL_CLASS_VALUES]

    # 1. Chaque coût dans la plage autorisée 5-40 % inclus.
    for cle, info in ordered:
        cout = info["cout_pct"]
        if not (5 <= cout <= 40):
            errors.append(f"Classe {cle} : coût de {cout}% hors des bornes autorisées (5-40%).")

    # 2. Coûts strictement croissants dans l'ordre 4 → 3 → 2 → 1 → S.
    for (k1, i1), (k2, i2) in zip(ordered, ordered[1:]):
        if i2["cout_pct"] <= i1["cout_pct"]:
            errors.append(
                f"Classe {k2} : coût de {i2['cout_pct']}% inférieur ou égal à la classe précédente, "
                "la progression doit être strictement croissante.")

    # 3. Fourchettes de dégâts sans trou ni chevauchement entre classes consécutives (max == min suivant).
    for (k1, i1), (k2, i2) in zip(ordered, ordered[1:]):
        if i1["degats_max"] != i2["degats_min"]:
            errors.append(f"Fourchette de dégâts incohérente entre classe {k1} et {k2}.")


def _check_barometer_presence(barometer, errors):
    if barometer is None:
        errors.append("La table role_point_values est introuvable dans la base.")
        return
    if not barometer:
        errors.append("role_point_values est vide : aucun barème de points chargé.")
        return
    for role_id, (cat, _pts) in barometer.items():
        if cat not in SEMANTIC_KINDS:
            errors.append(
                f"Catégorie inconnue '{cat}' pour le rôle {role_id} dans role_point_values "
                "(attendu camp/clan/grade).")


def _check_id_conflicts(by_id, errors):
    for role_id, entries in by_id.items():
        sem = sorted({k for k, _, _ in entries if k in SEMANTIC_KINDS})
        if len(sem) > 1:
            srcs = ", ".join(sorted({s for _, _, s in entries}))
            errors.append(
                f"Le rôle {role_id} est utilisé pour plusieurs catégories différentes {sem} "
                f"(sources : {srcs}).")


def _cross_checks(by_id, barometer, errors, notes):
    if barometer is None:
        return
    code_ids = set(by_id.keys())

    # a) Rôle camp/clan/grade codé en dur MAIS absent du barème -> probablement oublié.
    for role_id, entries in by_id.items():
        sem_kinds = {k for k, _, _ in entries if k in SEMANTIC_KINDS}
        if sem_kinds and role_id not in barometer:
            label = sorted({lbl for _, lbl, _ in entries})[0]
            errors.append(
                f"Rôle {role_id} ({'/'.join(sorted(sem_kinds))} — {label}) absent de role_point_values "
                "(probablement oublié dans le barème).")

    # b) Catégorie du barème différente du 'kind' attendu dans le code.
    for role_id, entries in by_id.items():
        sem_kinds = {k for k, _, _ in entries if k in SEMANTIC_KINDS}
        if role_id in barometer and sem_kinds:
            cat = barometer[role_id][0]
            if cat not in sem_kinds:
                errors.append(
                    f"Rôle {role_id} : catégorie '{cat}' dans le barème mais utilisé comme "
                    f"{sorted(sem_kinds)} dans le code.")

    # c) Rôle du barème référencé nulle part dans le code -> barème orphelin.
    for role_id, (cat, pts) in barometer.items():
        if role_id not in code_ids:
            errors.append(
                f"Rôle {role_id} présent dans role_point_values ({cat}, {pts}pts) mais référencé nulle "
                "part dans le code (barème orphelin ?).")

    # d) Valeurs « presque uniformes » par catégorie -> NOTE de vérification manuelle (pas une erreur).
    by_cat = defaultdict(list)
    for role_id, (cat, pts) in barometer.items():
        by_cat[cat].append((role_id, pts))
    for cat, entries in by_cat.items():
        values = [p for _, p in entries]
        if len(values) < 3:
            continue
        counts = Counter(values)
        majority_val, majority_n = counts.most_common(1)[0]
        outliers = [(rid, p) for rid, p in entries if p != majority_val]
        if outliers and majority_n >= len(values) - max(1, len(values) // 4):
            outlier_str = ", ".join(f"{rid}={p}pts" for rid, p in outliers)
            notes.append(
                f"Barème '{cat}' : {majority_n}/{len(values)} rôles valent {majority_val}pts, "
                f"exception(s) : {outlier_str}. À confirmer manuellement "
                "(ex: 'Sans clan' volontairement plus bas).")


# =====================================================================
# POINT D'ENTRÉE PARTAGÉ
# =====================================================================
def run_coherence_check() -> list:
    """Exécute toutes les vérifications et retourne la liste des lignes d'incohérence (❌ erreurs,
    ℹ️ notes). Liste vide si tout est cohérent. Ne lève jamais : toute défaillance interne devient
    une ligne d'erreur."""
    errors = []
    notes = []

    mods = import_cogs()
    depart = mods.get("depart")

    conn = None
    barometer = None
    try:
        conn = open_db()
        barometer = load_role_point_values(conn)
    except Exception as e:
        errors.append(f"Accès à la base impossible : {e}")

    # Vérifications basées sur les constantes (indépendantes de la base).
    _check_reward_tables(depart, errors)
    _check_eo(depart, errors)
    _check_clan_base(depart, errors)
    _check_spell_classes(errors)

    # Vérifications barème <-> code.
    _check_barometer_presence(barometer, errors)
    try:
        by_id = group_roles_by_id(collect_code_roles(mods))
    except Exception as e:
        errors.append(f"Collecte des rôles du code impossible : {e}")
        by_id = {}
    _check_id_conflicts(by_id, errors)
    _cross_checks(by_id, barometer, errors, notes)

    if conn is not None:
        conn.close()

    return [f"❌ {m}" for m in errors] + [f"ℹ️  {m}" for m in notes]
