"""
Script de vérification de la migration JSON -> SQLite pour les tickets et informations.
Lecture seule, ne modifie rien. Lance avec : python check_migration.py
"""

import sqlite3
import json
import os

DB_PATH = "data/bot.db"

JSON_FILES = {
    "tickets": "data/tickets.json",
    "informations": "data/informations.json",
}

def get_connection():
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn

def section(title):
    print()
    print(f"=== {title} ===")

def check_bak_files():
    section("FICHIERS JSON D'ORIGINE")
    for name, path in JSON_FILES.items():
        bak_path = path + ".bak"
        if os.path.exists(path):
            print(f"⚠️  {path} existe ENCORE (pas renommé en .bak) — la migration n'a peut être jamais tourné, ou a échoué avant de renommer.")
        elif os.path.exists(bak_path):
            print(f"✅ {path} a bien été renommé en {bak_path} (migration exécutée).")
        else:
            print(f"ℹ️  Ni {path} ni {bak_path} trouvé — soit jamais créé, soit déjà nettoyé manuellement.")
    return

def load_bak_json(name):
    bak_path = JSON_FILES[name] + ".bak"
    if not os.path.exists(bak_path):
        return None
    try:
        with open(bak_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"❌ Impossible de lire {bak_path} : {e}")
        return None

def check_tickets(conn):
    section("TABLE tickets")
    try:
        rows = conn.execute("SELECT COUNT(*) as c FROM tickets").fetchone()
        print(f"Lignes dans la table tickets : {rows['c']}")
    except Exception as e:
        print(f"❌ Table tickets introuvable ou erreur : {e}")
        return

    try:
        counters = conn.execute("SELECT * FROM ticket_counters").fetchall()
        print(f"Compteurs de ticket enregistrés : {len(counters)}")
        for c in counters:
            print(f"   • {c['counter_key']} = {c['value']}")
    except Exception as e:
        print(f"❌ Table ticket_counters introuvable ou erreur : {e}")

    try:
        pending = conn.execute("SELECT COUNT(*) as c FROM pending_ticket_requests").fetchone()
        print(f"Demandes en attente : {pending['c']}")
    except Exception as e:
        print(f"❌ Table pending_ticket_requests introuvable ou erreur : {e}")

    old_data = load_bak_json("tickets")
    if old_data:
        old_tickets_count = len(old_data.get("tickets", {}))
        old_counters_count = len(old_data.get("counters", {}))
        old_pending_count = len(old_data.get("pending_requests", {}))
        print()
        print("Comparaison avec l'ancien fichier .bak :")
        print(f"   Tickets : ancien={old_tickets_count} / actuel={rows['c']} {'✅' if old_tickets_count <= rows['c'] else '❌ PERTE DE DONNÉES POSSIBLE'}")
        print(f"   Compteurs : ancien={old_counters_count} / actuel={len(counters)} {'✅' if old_counters_count <= len(counters) else '❌ PERTE DE DONNÉES POSSIBLE'}")
        print(f"   Demandes en attente : ancien={old_pending_count} / actuel={pending['c']} {'✅' if old_pending_count <= pending['c'] else '❌ PERTE DE DONNÉES POSSIBLE'}")

def check_informations(conn):
    section("TABLE informations")
    try:
        rows = conn.execute("SELECT COUNT(*) as c FROM informations").fetchone()
        print(f"Entrées dans la table informations : {rows['c']}")
        entries = conn.execute("SELECT info_key, title, is_category FROM informations ORDER BY CAST(info_key AS INTEGER)").fetchall()
        for e in entries:
            tag = "📁 catégorie" if e["is_category"] else "📄"
            print(f"   {tag} {e['info_key']} — {e['title']}")
    except Exception as e:
        print(f"❌ Table informations introuvable ou erreur : {e}")
        return

    try:
        subitems = conn.execute("SELECT COUNT(*) as c FROM information_subitems").fetchone()
        print(f"Sous-entrées (ex: clans) : {subitems['c']}")
    except Exception as e:
        print(f"❌ Table information_subitems introuvable ou erreur : {e}")

    old_data = load_bak_json("informations")
    if old_data:
        old_count = len(old_data)
        print()
        print("Comparaison avec l'ancien fichier .bak :")
        print(f"   Entrées : ancien={old_count} / actuel={rows['c']} {'✅' if old_count <= rows['c'] else '❌ PERTE DE DONNÉES POSSIBLE'}")

        # vérifie l'entrée "8" (Clans) et ses sous-entrées si elle existe
        if "8" in old_data and isinstance(old_data["8"], dict) and "clans" in old_data["8"]:
            old_clans_count = len(old_data["8"]["clans"])
            print(f"   Sous-entrées de Clans : ancien={old_clans_count} / actuel={subitems['c']} {'✅' if old_clans_count <= subitems['c'] else '❌ PERTE DE DONNÉES POSSIBLE'}")

def check_leftover_json_usage():
    section("RÉFÉRENCES RÉSIDUELLES AU JSON DANS LE CODE")
    suspicious_files = ["cogs/ticket.py", "cogs/informations.py"]
    found_any = False
    for filepath in suspicious_files:
        if not os.path.exists(filepath):
            print(f"ℹ️  {filepath} introuvable, ignoré.")
            continue
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
        suspicious_patterns = ["tickets.json", "informations.json", "json.load", "json.dump"]
        hits = [p for p in suspicious_patterns if p in content]
        if hits:
            found_any = True
            print(f"⚠️  {filepath} contient encore des traces suspectes : {', '.join(hits)}")
        else:
            print(f"✅ {filepath} ne contient plus aucune trace de lecture/écriture JSON.")
    if not found_any:
        print()
        print("Aucune référence résiduelle au JSON trouvée dans les fichiers vérifiés.")

def main():
    print("Vérification de la migration JSON -> SQLite (tickets & informations)")
    print("=" * 60)

    if not os.path.exists(DB_PATH):
        print(f"❌ Base de données introuvable : {DB_PATH}")
        return

    check_bak_files()

    conn = get_connection()
    try:
        check_tickets(conn)
        check_informations(conn)
    finally:
        conn.close()

    check_leftover_json_usage()

    print()
    print("=" * 60)
    print("Vérification terminée.")

if __name__ == "__main__":
    main()
