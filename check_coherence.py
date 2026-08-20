#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
check_coherence.py — Audit LECTURE SEULE des valeurs numériques critiques du bot.

Affiche un rapport détaillé (barème, tables de récompenses, EO, clans, rôles) puis une section
« INCOHÉRENCES » finale. La DÉTECTION d'incohérences n'est PAS codée ici : elle vient entièrement
de cogs/utils/coherence_check.run_coherence_check(), exactement la même fonction que celle utilisée
par le status_loop — les deux ne peuvent donc jamais diverger.

Usage :
    python check_coherence.py
"""

import os
import sys
import traceback
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from cogs.utils.coherence_check import (  # noqa: E402  (import après ajustement de sys.path)
    open_db, load_role_point_values, import_cogs, collect_code_roles, group_roles_by_id,
    pct_sum_ok, run_coherence_check, DB_PATH,
)


# =====================================================================
# AFFICHAGE (purement descriptif : aucune logique de détection ici)
# =====================================================================
def header(title):
    print()
    print("=" * 70)
    print(f"  {title}")
    print("=" * 70)


def section_bareme(barometer):
    header("1. BARÈME DE POINTS (rôles)")
    if barometer is None:
        print("  ⚠️  Table role_point_values introuvable (base absente ou non initialisée).")
        return
    if not barometer:
        print("  (aucune ligne dans role_point_values)")
        return
    by_cat = defaultdict(list)
    for role_id, (cat, pts) in barometer.items():
        by_cat[cat].append((role_id, pts))
    for cat in ("camp", "clan", "grade"):
        entries = sorted(by_cat.get(cat, []), key=lambda x: (-x[1], x[0]))
        print(f"\n  [{cat}] — {len(entries)} rôle(s)")
        for role_id, pts in entries:
            print(f"      {role_id}  →  {pts} pts")
    for cat, entries in by_cat.items():
        if cat not in ("camp", "clan", "grade"):
            print(f"\n  [{cat}] (catégorie inattendue) — {len(entries)} rôle(s)")
            for role_id, pts in entries:
                print(f"      {role_id}  →  {pts} pts")


def section_reward_tables(depart):
    header("2. TABLES DE RÉCOMPENSES /depart")
    if depart is None:
        print("  ⚠️  cogs.depart indisponible.")
        return
    for name in ("REWARD_TABLE", "REWARD_TABLE_HYBRIDE_EXORCISTE",
                 "REWARD_TABLE_HYBRIDE_FLEAUX", "REWARD_TABLE_HYBRIDE_SEUL"):
        table = getattr(depart, name, None)
        if table is None:
            print(f"  {name:32s} : (absente)")
            continue
        total = round(sum(float(e.get('pct', 0)) for e in table), 4)
        flag = "OK" if pct_sum_ok(total) else "≠ 100 !"
        print(f"  {name:32s} : {len(table):2d} entrées, somme = {total:.2f} %  [{flag}]")


def section_eo_classes(depart):
    header("3. CLASSES D'ÉNERGIE OCCULTE")
    table = getattr(depart, "EO_CLASS_TABLE", None) if depart else None
    if not table:
        print("  ⚠️  EO_CLASS_TABLE indisponible.")
        return
    items = list(table.items())
    total = round(sum(float(i.get('pct', 0)) for _, i in items), 4)
    for key, info in items:
        print(f"  {key:10s} : {info.get('min'):>8} → {info.get('max'):>8}   {info.get('pct')} %")
    print(f"\n  Somme des pourcentages : {total:.2f} %  [{'OK' if pct_sum_ok(total) else '≠ 100 !'}]")
    print("\n  Chaînage min/max :")
    for (k1, i1), (k2, i2) in zip(items, items[1:]):
        aligned = i1.get("max") == i2.get("min")
        print(f"      {k1}.max ({i1.get('max')})  vs  {k2}.min ({i2.get('min')})  →  "
              f"{'OK' if aligned else 'TROU/CHEVAUCHEMENT'}")


def section_clan_base(depart):
    header("4. POURCENTAGES DE BASE DES CLANS")
    state = getattr(depart, "DEFAULT_CLAN_STATE", None) if depart else None
    if not isinstance(state, dict):
        print("  ⚠️  DEFAULT_CLAN_STATE indisponible.")
        return
    total = 0.0
    for clan_key, info in state.get("clans", {}).items():
        base = float(info.get("base_pct", 0))
        total += base
        print(f"  {clan_key.capitalize():10s} : {base:g} %")
    sans_clan = float(state.get("sans_clan_pct", 0))
    total = round(total + sans_clan, 4)
    print(f"  {'Sans clan':10s} : {sans_clan:g} %")
    print(f"\n  Somme totale (clans + sans_clan) : {total:.2f} %  "
          f"[{'OK' if pct_sum_ok(total) else '≠ 100 !'}]")


def section_code_roles(mods, barometer):
    header("5. RÔLES UTILISÉS DANS LE CODE")
    by_id = group_roles_by_id(collect_code_roles(mods))
    if not by_id:
        print("  (aucun rôle codé en dur collecté — imports de cogs indisponibles ?)")
        return
    for role_id in sorted(by_id):
        entries = by_id[role_id]
        kinds = sorted({k for k, _, _ in entries})
        labels = ", ".join(sorted({lbl for _, lbl, _ in entries}))
        bar = ""
        if barometer and role_id in barometer:
            cat, pts = barometer[role_id]
            bar = f"  [barème: {cat} {pts}pts]"
        print(f"  {role_id}  ({'/'.join(kinds)})  {labels}{bar}")


# =====================================================================
# POINT D'ENTRÉE
# =====================================================================
def main():
    print("Audit de cohérence — cendres-shibuya-bot (lecture seule)")
    print(f"Base : {DB_PATH}")

    mods = import_cogs()
    depart = mods.get("depart")

    conn = None
    barometer = None
    try:
        conn = open_db()
        if conn is None:
            print(f"\n⚠️  Base introuvable à {DB_PATH} : sections liées au barème limitées.")
        barometer = load_role_point_values(conn)
    except Exception as e:
        print(f"\n⚠️  Accès à la base impossible ({e}).")

    # Sections descriptives (isolées : une erreur d'affichage n'interrompt pas le reste).
    for fn, arg in ((section_bareme, barometer), (section_reward_tables, depart),
                    (section_eo_classes, depart), (section_clan_base, depart)):
        try:
            fn(arg)
        except Exception as e:
            print(f"  ⚠️  Section ignorée : {e}")
    try:
        section_code_roles(mods, barometer)
    except Exception as e:
        print(f"  ⚠️  Section 5 ignorée : {e}")

    if conn is not None:
        conn.close()

    # SECTION FINALE — logique DÉLÉGUÉE au module partagé (identique au status_loop).
    header("=== INCOHÉRENCES ===")
    lignes = run_coherence_check()
    if lignes:
        for ligne in lignes:
            print(f"  {ligne}")
    else:
        print("  Aucune incohérence détectée.")

    # Code de sortie : 1 s'il existe au moins une erreur dure (❌), 0 sinon.
    return 1 if any(l.startswith("❌") for l in lignes) else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        print("\n💥 Erreur inattendue pendant l'audit :")
        traceback.print_exc()
        sys.exit(2)
