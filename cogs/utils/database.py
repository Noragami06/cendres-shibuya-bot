import os
import sqlite3

DB_FILE = os.path.join(os.path.dirname(__file__), "..", "..", "data", "bot.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS tickets (
    id INTEGER PRIMARY KEY,
    channel_id INTEGER,
    user_id INTEGER,
    type TEXT,
    reason TEXT,
    status TEXT,
    created_at TEXT,
    transcript_path TEXT
);

CREATE TABLE IF NOT EXISTS ticket_counters (
    counter_key TEXT PRIMARY KEY,
    value INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS pending_ticket_requests (
    request_id TEXT PRIMARY KEY,
    requester_id INTEGER,
    ticket_type TEXT,
    reason_text TEXT
);

CREATE TABLE IF NOT EXISTS informations (
    info_key TEXT PRIMARY KEY,
    title TEXT,
    content TEXT,
    is_category INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS information_subitems (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    parent_key TEXT,
    sub_key TEXT,
    title TEXT,
    content TEXT,
    sort_order INTEGER,
    FOREIGN KEY (parent_key) REFERENCES informations(info_key)
);

CREATE TABLE IF NOT EXISTS clan_roll_state (
    clan_key TEXT PRIMARY KEY,
    base_pct INTEGER,
    current_pct INTEGER,
    cap INTEGER,
    closed INTEGER DEFAULT 0,
    partial_heredit INTEGER DEFAULT 0,
    role_id INTEGER,
    sort_order INTEGER
);

CREATE TABLE IF NOT EXISTS clan_roll_meta (
    meta_key TEXT PRIMARY KEY,
    meta_value INTEGER
);

CREATE TABLE IF NOT EXISTS depart_pending_choices (
    user_id INTEGER PRIMARY KEY,
    clan TEXT,
    sort TEXT,
    origin_channel_id INTEGER
);
"""


def get_connection() -> sqlite3.Connection:
    """Connexion à data/bot.db, avec accès aux colonnes par nom."""
    os.makedirs(os.path.dirname(DB_FILE), exist_ok=True)
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Crée les tables manquantes. N'efface jamais de données existantes."""
    with get_connection() as conn:
        conn.executescript(SCHEMA)


# =====================================================================
# TICKETS
# =====================================================================
COUNTER_KEYS = ("global", "fiche", "partenariat", "autre")


def next_ticket_numbers(ticket_type: str):
    """Incrémente le compteur global et celui du type, retourne (global_id, type_number)."""
    with get_connection() as conn:
        for key in ("global", ticket_type):
            conn.execute(
                "INSERT OR IGNORE INTO ticket_counters (counter_key, value) VALUES (?, 0)", (key,)
            )
            conn.execute(
                "UPDATE ticket_counters SET value = value + 1 WHERE counter_key = ?", (key,)
            )

        global_id = conn.execute(
            "SELECT value FROM ticket_counters WHERE counter_key = 'global'"
        ).fetchone()["value"]
        type_number = conn.execute(
            "SELECT value FROM ticket_counters WHERE counter_key = ?", (ticket_type,)
        ).fetchone()["value"]

    return global_id, type_number


def insert_ticket(ticket_id, channel_id, user_id, ticket_type, reason, status, created_at):
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO tickets (id, channel_id, user_id, type, reason, status, created_at, transcript_path)
               VALUES (?, ?, ?, ?, ?, ?, ?, NULL)""",
            (ticket_id, channel_id, user_id, ticket_type, reason, status, created_at),
        )


def get_ticket_by_channel(channel_id: int):
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM tickets WHERE channel_id = ?", (channel_id,)
        ).fetchone()


def update_ticket_status(ticket_id: int, status: str):
    with get_connection() as conn:
        conn.execute("UPDATE tickets SET status = ? WHERE id = ?", (status, ticket_id))


def update_ticket_transcript(ticket_id: int, status: str, transcript_path: str):
    with get_connection() as conn:
        conn.execute(
            "UPDATE tickets SET status = ?, transcript_path = ? WHERE id = ?",
            (status, transcript_path, ticket_id),
        )


def add_pending_request(request_id, requester_id, ticket_type, reason_text):
    with get_connection() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO pending_ticket_requests
               (request_id, requester_id, ticket_type, reason_text) VALUES (?, ?, ?, ?)""",
            (request_id, requester_id, ticket_type, reason_text),
        )


def get_pending_request(request_id: str):
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM pending_ticket_requests WHERE request_id = ?", (request_id,)
        ).fetchone()


def delete_pending_request(request_id: str):
    with get_connection() as conn:
        conn.execute("DELETE FROM pending_ticket_requests WHERE request_id = ?", (request_id,))


# =====================================================================
# INFORMATIONS
# =====================================================================
def get_all_informations():
    """Toutes les entrées, triées numériquement (10 après 9, pas après 1)."""
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM informations ORDER BY CAST(info_key AS INTEGER)"
        ).fetchall()


def get_information(info_key: str):
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM informations WHERE info_key = ?", (info_key,)
        ).fetchone()


def get_information_subitems(parent_key: str):
    """Sous-entrées d'une catégorie, dans l'ordre d'insertion voulu."""
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM information_subitems WHERE parent_key = ? ORDER BY sort_order",
            (parent_key,),
        ).fetchall()


def get_information_subitem(parent_key: str, sub_key: str):
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM information_subitems WHERE parent_key = ? AND sub_key = ?",
            (parent_key, sub_key),
        ).fetchone()


def get_category_keys():
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT info_key FROM informations WHERE is_category = 1"
        ).fetchall()
    return [row["info_key"] for row in rows]


# =====================================================================
# CLAN ROLL STATE
# =====================================================================
def seed_clan_state(default: dict):
    """Insère l'état initial des clans si la table est vide. Ne touche jamais aux lignes existantes."""
    with get_connection() as conn:
        for order, (clan_key, info) in enumerate(default["clans"].items()):
            conn.execute(
                """INSERT OR IGNORE INTO clan_roll_state
                   (clan_key, base_pct, current_pct, cap, closed, partial_heredit, role_id, sort_order)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    clan_key,
                    info["base_pct"],
                    info["current_pct"],
                    info["cap"],
                    int(info["closed"]),
                    int(info["partial_heredit"]),
                    info["role_id"],
                    order,
                ),
            )
        conn.execute(
            "INSERT OR IGNORE INTO clan_roll_meta (meta_key, meta_value) VALUES ('sans_clan_pct', ?)",
            (default["sans_clan_pct"],),
        )


def load_clan_state() -> dict:
    """Reconstruit la structure historique {"clans": {...}, "sans_clan_pct": int}.

    L'ordre des clans est garanti par sort_order.
    """
    with get_connection() as conn:
        rows = conn.execute("SELECT * FROM clan_roll_state ORDER BY sort_order").fetchall()
        meta = conn.execute(
            "SELECT meta_value FROM clan_roll_meta WHERE meta_key = 'sans_clan_pct'"
        ).fetchone()

    clans = {}
    for row in rows:
        clans[row["clan_key"]] = {
            "base_pct": row["base_pct"],
            "current_pct": row["current_pct"],
            "cap": row["cap"],
            "closed": bool(row["closed"]),
            "partial_heredit": bool(row["partial_heredit"]),
            "role_id": row["role_id"],
        }

    return {"clans": clans, "sans_clan_pct": meta["meta_value"] if meta else 0}


def save_clan_state(data: dict):
    """Réécrit l'état des clans. sort_order est préservé via l'ordre du dict fourni."""
    with get_connection() as conn:
        for order, (clan_key, info) in enumerate(data["clans"].items()):
            conn.execute(
                """UPDATE clan_roll_state
                   SET base_pct = ?, current_pct = ?, cap = ?, closed = ?,
                       partial_heredit = ?, role_id = ?, sort_order = ?
                   WHERE clan_key = ?""",
                (
                    info["base_pct"],
                    info["current_pct"],
                    info["cap"],
                    int(info["closed"]),
                    int(info["partial_heredit"]),
                    info["role_id"],
                    order,
                    clan_key,
                ),
            )
        conn.execute(
            "INSERT OR REPLACE INTO clan_roll_meta (meta_key, meta_value) VALUES ('sans_clan_pct', ?)",
            (data["sans_clan_pct"],),
        )


# =====================================================================
# DEPART PENDING CHOICES
# =====================================================================
def get_pending_choice(user_id: int):
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM depart_pending_choices WHERE user_id = ?", (user_id,)
        ).fetchone()


def set_pending_origin(user_id: int, origin_channel_id: int):
    """Démarre (ou réinitialise) le flux DM : on ne garde que le salon d'origine."""
    with get_connection() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO depart_pending_choices
               (user_id, clan, sort, origin_channel_id) VALUES (?, NULL, NULL, ?)""",
            (user_id, origin_channel_id),
        )


def set_pending_clan(user_id: int, clan: str):
    with get_connection() as conn:
        conn.execute(
            "UPDATE depart_pending_choices SET clan = ? WHERE user_id = ?", (clan, user_id)
        )


def set_pending_sort(user_id: int, sort: str):
    with get_connection() as conn:
        conn.execute(
            "UPDATE depart_pending_choices SET sort = ? WHERE user_id = ?", (sort, user_id)
        )


def delete_pending_choice(user_id: int):
    with get_connection() as conn:
        conn.execute("DELETE FROM depart_pending_choices WHERE user_id = ?", (user_id,))
