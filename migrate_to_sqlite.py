"""Migration unique des fichiers JSON vers SQLite (data/bot.db).

Usage : python migrate_to_sqlite.py

Idempotent : utilise INSERT OR IGNORE, peut être relancé sans créer de doublons.
Les JSON d'origine ne sont pas supprimés, seulement renommés en .bak après succès.
"""

import json
import os

from cogs.utils.database import get_connection, init_db

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")

TICKETS_JSON = os.path.join(DATA_DIR, "tickets.json")
INFORMATIONS_JSON = os.path.join(DATA_DIR, "informations.json")
CLAN_STATE_JSON = os.path.join(DATA_DIR, "clan_roll_state.json")
PENDING_CHOICES_JSON = os.path.join(DATA_DIR, "depart_pending_choices.json")

CATEGORY_KEY = "8"  # L'entrée "Clans" est la seule catégorie actuelle


def read_json(path):
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def backup(path):
    if os.path.exists(path):
        backup_path = path + ".bak"
        if os.path.exists(backup_path):
            os.remove(backup_path)
        os.rename(path, backup_path)
        return backup_path
    return None


def migrate_tickets(conn, stats):
    data = read_json(TICKETS_JSON)
    if data is None:
        return

    for key, value in data.get("counters", {}).items():
        conn.execute(
            "INSERT OR IGNORE INTO ticket_counters (counter_key, value) VALUES (?, ?)",
            (key, value),
        )
        stats["ticket_counters"] += 1

    for ticket_id, info in data.get("tickets", {}).items():
        conn.execute(
            """INSERT OR IGNORE INTO tickets
               (id, channel_id, user_id, type, reason, status, created_at, transcript_path)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                int(ticket_id),
                info.get("channel_id"),
                info.get("user_id"),
                info.get("type"),
                info.get("reason"),
                info.get("status"),
                info.get("created_at"),
                info.get("transcript"),
            ),
        )
        stats["tickets"] += 1

    for request_id, info in data.get("pending_requests", {}).items():
        conn.execute(
            """INSERT OR IGNORE INTO pending_ticket_requests
               (request_id, requester_id, ticket_type, reason_text) VALUES (?, ?, ?, ?)""",
            (
                request_id,
                info.get("requester_id"),
                info.get("ticket_type"),
                info.get("reason_text"),
            ),
        )
        stats["pending_ticket_requests"] += 1


def migrate_informations(conn, stats):
    data = read_json(INFORMATIONS_JSON)
    if data is None:
        return

    for info_key, entry in data.items():
        is_category = 1 if entry.get("type") == "category" else 0

        conn.execute(
            """INSERT OR IGNORE INTO informations (info_key, title, content, is_category)
               VALUES (?, ?, ?, ?)""",
            (
                info_key,
                entry.get("title"),
                None if is_category else entry.get("content"),
                is_category,
            ),
        )
        stats["informations"] += 1

        if is_category:
            # sort_order = position dans le dict JSON (l'ordre d'insertion est préservé)
            for order, (sub_key, sub) in enumerate(entry.get("clans", {}).items()):
                existing = conn.execute(
                    "SELECT id FROM information_subitems WHERE parent_key = ? AND sub_key = ?",
                    (info_key, sub_key),
                ).fetchone()
                if existing:
                    continue  # déjà migré
                conn.execute(
                    """INSERT INTO information_subitems
                       (parent_key, sub_key, title, content, sort_order) VALUES (?, ?, ?, ?, ?)""",
                    (info_key, sub_key, sub.get("title"), sub.get("content"), order),
                )
                stats["information_subitems"] += 1


def migrate_clan_state(conn, stats):
    data = read_json(CLAN_STATE_JSON)
    if data is None:
        return

    for order, (clan_key, info) in enumerate(data.get("clans", {}).items()):
        conn.execute(
            """INSERT OR IGNORE INTO clan_roll_state
               (clan_key, base_pct, current_pct, cap, closed, partial_heredit, role_id, sort_order)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                clan_key,
                info.get("base_pct"),
                info.get("current_pct"),
                info.get("cap"),
                int(info.get("closed", False)),
                int(info.get("partial_heredit", False)),
                info.get("role_id"),
                order,
            ),
        )
        stats["clan_roll_state"] += 1

    conn.execute(
        "INSERT OR IGNORE INTO clan_roll_meta (meta_key, meta_value) VALUES ('sans_clan_pct', ?)",
        (data.get("sans_clan_pct", 0),),
    )
    stats["clan_roll_meta"] += 1


def migrate_pending_choices(conn, stats):
    data = read_json(PENDING_CHOICES_JSON)
    if not data:
        return

    for user_id, entry in data.items():
        conn.execute(
            """INSERT OR IGNORE INTO depart_pending_choices
               (user_id, clan, sort, origin_channel_id) VALUES (?, ?, ?, ?)""",
            (
                int(user_id),
                entry.get("clan"),
                entry.get("sort"),
                entry.get("origin_channel_id"),
            ),
        )
        stats["depart_pending_choices"] += 1


def main():
    print("=" * 55)
    print("Migration JSON -> SQLite (data/bot.db)")
    print("=" * 55)

    init_db()
    print("Tables verifiees / creees.\n")

    stats = {
        "tickets": 0,
        "ticket_counters": 0,
        "pending_ticket_requests": 0,
        "informations": 0,
        "information_subitems": 0,
        "clan_roll_state": 0,
        "clan_roll_meta": 0,
        "depart_pending_choices": 0,
    }

    with get_connection() as conn:
        migrate_tickets(conn, stats)
        migrate_informations(conn, stats)
        migrate_clan_state(conn, stats)
        migrate_pending_choices(conn, stats)

    print("Lignes traitees par table :")
    for table, count in stats.items():
        print(f"  {table:28} {count}")

    # Etat reel en base apres migration
    print("\nContenu final de la base :")
    with get_connection() as conn:
        for table in stats:
            total = conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
            print(f"  {table:28} {total} ligne(s)")

    print("\nSauvegarde des JSON d'origine :")
    for path in (TICKETS_JSON, INFORMATIONS_JSON, CLAN_STATE_JSON, PENDING_CHOICES_JSON):
        backup_path = backup(path)
        if backup_path:
            print(f"  {os.path.basename(path)} -> {os.path.basename(backup_path)}")

    print("\nMigration terminee.")


if __name__ == "__main__":
    main()
