#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
check_coherence.py — Audit LECTURE SEULE des valeurs numériques critiques du bot.

Vérifie les barèmes de points, les tables de récompenses, les classes d'énergie occulte et les
pourcentages de clans, puis liste toute incohérence dans une section finale « INCOHÉRENCES »
(toujours affichée, jamais silencieuse).

Usage :
    python check_coherence.py

Ne modifie JAMAIS la base : ouverture SQLite en lecture, aucune écriture. Se termine proprement
même si une table ou une constante manque (chaque section est isolée dans un try/except).
"""

import os
import sqlite3
import sys
import traceback
from collections import Counter, defaultdict

# S'assure que « import cogs... » fonctionne quel que soit le dossier d'exécution.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "bot.db")
PCT_TOLERANCE = 0.05  # tolérance d'arrondi pour les sommes de pourcentages

# Collecteurs globaux remplis au fil des sections.
INCOHERENCES = []   # problèmes durs (erreurs)
NOTES = []          # points à vérifier manuellement (pas des erreurs automatiques)


# =====================================================================
# OUTILS D'AFFICHAGE
# =====================================================================
def header(title):
    print()
    print("=" * 70)
    print(f"  {title}")
    print("=" * 70)


def section_error(title, exc):
    """Affiche proprement l'échec d'une section sans interrompre le reste de l'audit."""
    header(title)
    print(f"  ⚠️  Section ignorée (données/constantes indisponibles) : {exc}")
    INCOHERENCES.append(f"[{title}] n'a pas pu être auditée : {exc}")


def pct_sum_ok(total):
    return abs(total - 100.0) <= PCT_TOLERANCE


# =====================================================================
# IMPORTS DES COGS (chacun isolé : un cog manquant n'empêche pas les autres)
# =====================================================================
def _try_import():
    mods = {}
    try:
        import cogs.depart as depart
        mods["depart"] = depart
    except Exception as e:
        print(f"⚠️  Import de cogs.depart impossible : {e}")
    try:
        import cogs.ticket as ticket
        mods["ticket"] = ticket
    except Exception as e:
        print(f"⚠️  Import de cogs.ticket impossible : {e}")
    try:
        import cogs.ordre as ordre
        mods["ordre"] = ordre
    except Exception as e:
        print(f"⚠️  Import de cogs.ordre impossible : {e}")
    return mods


# =====================================================================
# ACCÈS BASE (lecture seule)
# =====================================================================
def open_db():
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


# =====================================================================
# COLLECTE DES RÔLES CODÉS EN DUR
# =====================================================================
# Chaque entrée : (role_id, kind, label, source). `kind` dans le groupe {camp, clan, grade} est
# "sémantique" et attendu dans le barème ; les autres (marker/rct/depart/staff/access/manager) non.
SEMANTIC_KINDS = {"camp", "clan", "grade"}


def collect_code_roles(mods):
    roles = []  # liste de tuples (role_id, kind, label, source)

    def add(role_id, kind, label, source):
        if isinstance(role_id, int):
            roles.append((role_id, kind, label, source))

    depart = mods.get("depart")
    if depart is not None:
        # Camp
        add(getattr(depart, "ROLE_EXORCISTE", None), "camp", "Exorciste", "depart.ROLE_EXORCISTE")
        add(getattr(depart, "ROLE_HYBRIDE", None), "camp", "Hybride", "depart.ROLE_HYBRIDE")
        add(getattr(depart, "ROLE_HUMAIN", None), "camp", "Humain", "depart.ROLE_HUMAIN")
        # Clans (via l'état par défaut : source de vérité de l'ordre + des role_id)
        default_state = getattr(depart, "DEFAULT_CLAN_STATE", None)
        if isinstance(default_state, dict):
            for clan_key, info in default_state.get("clans", {}).items():
                add(info.get("role_id"), "clan", clan_key.capitalize(), f"depart.DEFAULT_CLAN_STATE[{clan_key}]")
        add(getattr(depart, "SANS_CLAN_ROLE_ID", None), "clan", "Sans clan", "depart.SANS_CLAN_ROLE_ID")
        # Grades
        for name, rid in getattr(depart, "GRADE_ROLES", []) or []:
            add(rid, "grade", name, "depart.GRADE_ROLES")
        # Alias de grades (mêmes ID que dans GRADE_ROLES : ne doivent PAS être vus comme un conflit)
        add(getattr(depart, "HERITIER_ROLE_ID", None), "grade", "Héritier (alias)", "depart.HERITIER_ROLE_ID")
        add(getattr(depart, "MEMBRES_PRINCIPAUX_ROLE_ID", None), "grade", "Membres principaux (alias)",
            "depart.MEMBRES_PRINCIPAUX_ROLE_ID")
        # Marqueurs / RCT / rôles techniques (hors barème par nature)
        add(getattr(depart, "CLAN_MEMBER_ROLE_ID", None), "marker", "Appartient à un clan",
            "depart.CLAN_MEMBER_ROLE_ID")
        add(getattr(depart, "RCT_POSSEDE_ROLE_ID", None), "rct", "RCT possédée", "depart.RCT_POSSEDE_ROLE_ID")
        add(getattr(depart, "RCT_NON_POSSEDE_ROLE_ID", None), "rct", "RCT non possédée",
            "depart.RCT_NON_POSSEDE_ROLE_ID")
        add(getattr(depart, "DEPART_ROLE_ID", None), "depart", "Accès /départ", "depart.DEPART_ROLE_ID")
        add(getattr(depart, "FICHE_STAFF_ROLE_ID", None), "staff", "Staff fiche", "depart.FICHE_STAFF_ROLE_ID")

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
        add(getattr(ordre, "FICHE_STAFF_ROLE_ID", None), "staff", "Staff fiche", "ordre.FICHE_STAFF_ROLE_ID")

    return roles


# =====================================================================
# SECTIONS DU RAPPORT
# =====================================================================
def section_bareme(barometer):
    header("1. BARÈME DE POINTS (rôles)")
    if barometer is None:
        print("  ⚠️  Table role_point_values introuvable (base absente ou non initialisée).")
        INCOHERENCES.append("La table role_point_values est introuvable dans la base.")
        return
    if not barometer:
        print("  (aucune ligne dans role_point_values)")
        INCOHERENCES.append("role_point_values est vide : aucun barème de points chargé.")
        return
    by_cat = defaultdict(list)
    for role_id, (cat, pts) in barometer.items():
        by_cat[cat].append((role_id, pts))
    for cat in ("camp", "clan", "grade"):
        entries = sorted(by_cat.get(cat, []), key=lambda x: (-x[1], x[0]))
        print(f"\n  [{cat}] — {len(entries)} rôle(s)")
        for role_id, pts in entries:
            print(f"      {role_id}  →  {pts} pts")
    # Catégories inattendues éventuelles
    for cat, entries in by_cat.items():
        if cat not in ("camp", "clan", "grade"):
            print(f"\n  [{cat}] (catégorie inattendue) — {len(entries)} rôle(s)")
            for role_id, pts in entries:
                print(f"      {role_id}  →  {pts} pts")
            INCOHERENCES.append(f"Catégorie inconnue '{cat}' dans role_point_values (attendu camp/clan/grade).")


def section_reward_tables(depart):
    header("2. TABLES DE RÉCOMPENSES /depart")
    if depart is None:
        raise RuntimeError("cogs.depart non importé")
    table_names = [
        "REWARD_TABLE",
        "REWARD_TABLE_HYBRIDE_EXORCISTE",
        "REWARD_TABLE_HYBRIDE_FLEAUX",
        "REWARD_TABLE_HYBRIDE_SEUL",
    ]
    found = 0
    for name in table_names:
        table = getattr(depart, name, None)
        if table is None:
            print(f"  {name:32s} : (absente)")
            INCOHERENCES.append(f"Table de récompenses {name} introuvable dans cogs/depart.py.")
            continue
        found += 1
        total = round(sum(float(e.get("pct", 0)) for e in table), 4)
        flag = "OK" if pct_sum_ok(total) else "≠ 100 !"
        print(f"  {name:32s} : {len(table):2d} entrées, somme = {total:.2f} %  [{flag}]")
        if not pct_sum_ok(total):
            INCOHERENCES.append(f"{name} : somme des % = {total:.2f} (attendu 100 ± {PCT_TOLERANCE}).")
    if found == 0:
        raise RuntimeError("aucune table de récompenses trouvée")


def section_eo_classes(depart):
    header("3. CLASSES D'ÉNERGIE OCCULTE")
    if depart is None:
        raise RuntimeError("cogs.depart non importé")
    table = getattr(depart, "EO_CLASS_TABLE", None)
    if not table:
        raise RuntimeError("EO_CLASS_TABLE absente")
    items = list(table.items())  # l'ordre du dict = ordre des classes (classe_4 -> classe_s)
    total = 0.0
    for key, info in items:
        total += float(info.get("pct", 0))
        print(f"  {key:10s} : {info.get('min'):>8} → {info.get('max'):>8}   {info.get('pct')} %")
    total = round(total, 4)
    print(f"\n  Somme des pourcentages : {total:.2f} %  [{'OK' if pct_sum_ok(total) else '≠ 100 !'}]")
    if not pct_sum_ok(total):
        INCOHERENCES.append(f"EO_CLASS_TABLE : somme des % = {total:.2f} (attendu 100).")

    # Chaînage : le max d'une classe doit égaler le min de la suivante (sans trou ni chevauchement).
    print("\n  Chaînage min/max :")
    ok_chain = True
    for (k1, i1), (k2, i2) in zip(items, items[1:]):
        aligned = i1.get("max") == i2.get("min")
        mark = "OK" if aligned else "TROU/CHEVAUCHEMENT"
        print(f"      {k1}.max ({i1.get('max')})  vs  {k2}.min ({i2.get('min')})  →  {mark}")
        if not aligned:
            ok_chain = False
            INCOHERENCES.append(
                f"EO : {k1}.max ({i1.get('max')}) ≠ {k2}.min ({i2.get('min')}) — trou ou chevauchement.")
    if ok_chain:
        print("      (chaînage cohérent)")


def section_clan_base(depart):
    header("4. POURCENTAGES DE BASE DES CLANS")
    if depart is None:
        raise RuntimeError("cogs.depart non importé")
    state = getattr(depart, "DEFAULT_CLAN_STATE", None)
    if not isinstance(state, dict):
        raise RuntimeError("DEFAULT_CLAN_STATE absent")
    clans = state.get("clans", {})
    total = 0.0
    for clan_key, info in clans.items():
        base = float(info.get("base_pct", 0))
        total += base
        print(f"  {clan_key.capitalize():10s} : {base:g} %")
    sans_clan = float(state.get("sans_clan_pct", 0))
    total += sans_clan
    print(f"  {'Sans clan':10s} : {sans_clan:g} %")
    total = round(total, 4)
    print(f"\n  Somme totale (clans + sans_clan) : {total:.2f} %  [{'OK' if pct_sum_ok(total) else '≠ 100 !'}]")
    if not pct_sum_ok(total):
        INCOHERENCES.append(
            f"Pourcentages de base des clans : somme = {total:.2f} (attendu 100).")


def section_code_roles(code_roles, barometer):
    header("5. RÔLES UTILISÉS DANS LE CODE")
    if not code_roles:
        print("  (aucun rôle codé en dur collecté — imports de cogs indisponibles ?)")
        INCOHERENCES.append("Aucun rôle codé en dur n'a pu être collecté (imports de cogs manquants).")
        return {}

    # Regroupe par role_id.
    by_id = defaultdict(list)  # role_id -> [(kind, label, source), ...]
    for role_id, kind, label, source in code_roles:
        by_id[role_id].append((kind, label, source))

    for role_id in sorted(by_id):
        entries = by_id[role_id]
        kinds = sorted({k for k, _, _ in entries})
        labels = ", ".join(sorted({lbl for _, lbl, _ in entries}))
        bar = ""
        if barometer and role_id in barometer:
            cat, pts = barometer[role_id]
            bar = f"  [barème: {cat} {pts}pts]"
        print(f"  {role_id}  ({'/'.join(kinds)})  {labels}{bar}")

    # Conflit : un même ID utilisé pour DEUX rôles sémantiques différents (camp/clan/grade).
    for role_id, entries in by_id.items():
        sem = sorted({k for k, _, _ in entries if k in SEMANTIC_KINDS})
        if len(sem) > 1:
            srcs = ", ".join(sorted({s for _, _, s in entries}))
            INCOHERENCES.append(
                f"Le rôle {role_id} est utilisé pour plusieurs catégories différentes {sem} "
                f"(sources : {srcs}).")
    return by_id


def cross_checks(code_roles_by_id, barometer):
    """Vérifications croisées barème <-> code (remplit INCOHERENCES / NOTES)."""
    if barometer is None:
        return

    code_ids = set(code_roles_by_id.keys())

    # a) Rôle sémantique (camp/clan/grade) codé en dur MAIS absent du barème -> probablement oublié.
    for role_id, entries in code_roles_by_id.items():
        sem_kinds = {k for k, _, _ in entries if k in SEMANTIC_KINDS}
        if sem_kinds and role_id not in barometer:
            label = sorted({lbl for _, lbl, _ in entries})[0]
            INCOHERENCES.append(
                f"Rôle {role_id} ({'/'.join(sorted(sem_kinds))} — {label}) absent de role_point_values "
                "(probablement oublié dans le barème).")

    # b) Catégorie du barème différente du 'kind' attendu dans le code.
    for role_id, entries in code_roles_by_id.items():
        sem_kinds = {k for k, _, _ in entries if k in SEMANTIC_KINDS}
        if role_id in barometer and sem_kinds:
            cat = barometer[role_id][0]
            if cat not in sem_kinds:
                INCOHERENCES.append(
                    f"Rôle {role_id} : catégorie '{cat}' dans le barème mais utilisé comme "
                    f"{sorted(sem_kinds)} dans le code.")

    # c) Rôle du barème référencé nulle part dans le code -> barème orphelin.
    for role_id in barometer:
        if role_id not in code_ids:
            cat, pts = barometer[role_id]
            INCOHERENCES.append(
                f"Rôle {role_id} présent dans role_point_values ({cat}, {pts}pts) mais référencé nulle "
                "part dans le code (barème orphelin ?).")

    # d) Valeurs de points « presque uniformes » par catégorie -> note de vérification manuelle.
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
        # Cas « tous pareils sauf une petite minorité »
        if outliers and majority_n >= len(values) - max(1, len(values) // 4):
            outlier_str = ", ".join(f"{rid}={p}pts" for rid, p in outliers)
            NOTES.append(
                f"Barème '{cat}' : {majority_n}/{len(values)} rôles valent {majority_val}pts, "
                f"exception(s) : {outlier_str}. À confirmer manuellement (ex: 'Sans clan' volontairement plus bas).")


def bonus_checks(depart):
    """Vérifications logiques supplémentaires pertinentes pour ce projet (tables de %=100)."""
    if depart is None:
        return
    extra = {
        "SPELL_TABLE_BASE": getattr(depart, "SPELL_TABLE_BASE", None),
        "SPELL_TABLE_PARTIAL": getattr(depart, "SPELL_TABLE_PARTIAL", None),
        "EO_NATURE_TABLE": getattr(depart, "EO_NATURE_TABLE", None),
    }
    for name, table in extra.items():
        if not isinstance(table, dict):
            continue
        total = round(sum(float(v) for v in table.values()), 4)
        if not pct_sum_ok(total):
            INCOHERENCES.append(f"{name} : somme des % = {total:.2f} (attendu 100).")


# =====================================================================
# POINT D'ENTRÉE
# =====================================================================
def main():
    print("Audit de cohérence — cendres-shibuya-bot (lecture seule)")
    print(f"Base : {DB_PATH}")

    mods = _try_import()
    depart = mods.get("depart")

    conn = None
    try:
        conn = open_db()
        if conn is None:
            print(f"\n⚠️  Base introuvable à {DB_PATH} : les vérifications liées au barème seront ignorées.")
    except Exception as e:
        print(f"\n⚠️  Ouverture de la base impossible ({e}) : vérifications barème ignorées.")

    barometer = None
    try:
        barometer = load_role_point_values(conn)
    except Exception as e:
        print(f"⚠️  Lecture de role_point_values impossible : {e}")

    # 1. Barème
    try:
        section_bareme(barometer)
    except Exception as e:
        section_error("1. BARÈME DE POINTS (rôles)", e)

    # 2. Tables de récompenses
    try:
        section_reward_tables(depart)
    except Exception as e:
        section_error("2. TABLES DE RÉCOMPENSES /depart", e)

    # 3. Classes d'énergie occulte
    try:
        section_eo_classes(depart)
    except Exception as e:
        section_error("3. CLASSES D'ÉNERGIE OCCULTE", e)

    # 4. Pourcentages de base des clans
    try:
        section_clan_base(depart)
    except Exception as e:
        section_error("4. POURCENTAGES DE BASE DES CLANS", e)

    # 5. Rôles codés en dur + collecte
    code_roles_by_id = {}
    try:
        code_roles = collect_code_roles(mods)
        code_roles_by_id = section_code_roles(code_roles, barometer)
    except Exception as e:
        section_error("5. RÔLES UTILISÉS DANS LE CODE", e)

    # Vérifications croisées + bonus (n'affichent rien, alimentent INCOHERENCES / NOTES).
    try:
        cross_checks(code_roles_by_id, barometer)
    except Exception as e:
        INCOHERENCES.append(f"Vérifications croisées interrompues : {e}")
    try:
        bonus_checks(depart)
    except Exception as e:
        INCOHERENCES.append(f"Vérifications supplémentaires interrompues : {e}")

    # SECTION FINALE OBLIGATOIRE
    header("=== INCOHÉRENCES ===")
    if INCOHERENCES:
        for line in INCOHERENCES:
            print(f"  ❌ {line}")
    else:
        print("  Aucune incohérence détectée.")
    if NOTES:
        print()
        print("  Notes (à vérifier manuellement, pas des erreurs automatiques) :")
        for line in NOTES:
            print(f"  ℹ️  {line}")

    if conn is not None:
        conn.close()

    # Code de sortie : 1 si des incohérences dures, 0 sinon (utile en CI, sans casser l'affichage).
    return 1 if INCOHERENCES else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        print("\n💥 Erreur inattendue pendant l'audit :")
        traceback.print_exc()
        sys.exit(2)
