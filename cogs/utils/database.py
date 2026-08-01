import json
import os
import sqlite3
from datetime import datetime

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

CREATE TABLE IF NOT EXISTS validated_characters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    guild_id INTEGER,
    slot_number INTEGER,
    discord_username TEXT,
    character_name TEXT,
    camp TEXT,
    clan TEXT,
    sort TEXT,
    eo_classe TEXT,
    eo_value INTEGER,
    nature TEXT,
    hybride_type TEXT,
    grade TEXT,
    portrait_path TEXT,
    validated_at TEXT
);

CREATE TABLE IF NOT EXISTS depart_character_progress (
    user_id INTEGER PRIMARY KEY,
    guild_id INTEGER,
    slot_number INTEGER,
    camp TEXT,
    path TEXT,
    hybride_type TEXT,
    clan TEXT,
    sort TEXT,
    sera_heritier INTEGER DEFAULT 0,
    grade_choisi TEXT,
    eo_classe TEXT,
    eo_value INTEGER,
    nature TEXT,
    items_json TEXT,
    pending_rerolls_json TEXT,
    reroll_rct_charges INTEGER DEFAULT 0,
    reroll_energie_charges INTEGER DEFAULT 0,
    parchemins_territoire INTEGER DEFAULT 0,
    parchemins_rct INTEGER DEFAULT 0,
    parchemins_nature INTEGER DEFAULT 0,
    rct INTEGER DEFAULT 0,
    recompense TEXT,
    argent_recompense INTEGER DEFAULT 0,
    nom TEXT,
    prenom TEXT,
    age INTEGER,
    histoire TEXT,
    portrait_path TEXT,
    fiche_status TEXT DEFAULT 'not_started',
    fiche_stage TEXT,
    fiche_deadline TEXT,
    fiche_question_msg_id INTEGER,
    origin_channel_id INTEGER,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS depart_pending_rewards (
    user_id INTEGER PRIMARY KEY,
    option_a_json TEXT,
    option_b_json TEXT
);

CREATE TABLE IF NOT EXISTS depart_pending_reserve_choice (
    user_id INTEGER PRIMARY KEY,
    classe TEXT
);

CREATE TABLE IF NOT EXISTS bank_accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    character_id INTEGER UNIQUE,
    user_id INTEGER,
    guild_id INTEGER,
    iban_courant TEXT UNIQUE,
    iban_livret TEXT UNIQUE,
    pin_code TEXT,
    solde_courant INTEGER DEFAULT 0,
    solde_livret INTEGER DEFAULT 0,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS bank_sessions (
    user_id INTEGER,
    character_id INTEGER,
    verified_at TEXT,
    PRIMARY KEY (user_id, character_id)
);

CREATE TABLE IF NOT EXISTS bank_transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    character_id INTEGER,
    label TEXT,
    amount INTEGER,
    date TEXT,
    related_iban TEXT
);
"""


def get_connection() -> sqlite3.Connection:
    """Connexion à data/bot.db, avec accès aux colonnes par nom."""
    os.makedirs(os.path.dirname(DB_FILE), exist_ok=True)
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def _column_names(conn, table: str):
    return [row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()]


def _pre_migrate_validated_characters(conn) -> bool:
    """Si l'ancienne table validated_characters (user_id PK, sans colonne id) existe et contient
    des données, on la renomme en _old pour que le nouveau schéma puisse être créé. Retourne True
    si une copie des données devra être faite après création du nouveau schéma."""
    cols = _column_names(conn, "validated_characters")
    if cols and "id" not in cols:
        conn.execute("ALTER TABLE validated_characters RENAME TO validated_characters_old")
        return True
    return False


def _copy_validated_characters_from_old(conn):
    """Recopie les anciennes lignes dans le nouveau schéma : slot_number=1,
    discord_username=display_name, character_name=NULL. Conserve _old comme sauvegarde."""
    conn.execute(
        """INSERT INTO validated_characters
           (user_id, guild_id, slot_number, discord_username, character_name,
            camp, clan, sort, eo_classe, eo_value, nature, validated_at)
           SELECT user_id, guild_id, 1, display_name, NULL,
                  camp, clan, sort, eo_classe, eo_value, nature, validated_at
           FROM validated_characters_old"""
    )


# Colonnes de depart_character_progress ajoutées après coup (migration des DB existantes).
_PROGRESS_EXTRA_COLUMNS = [
    ("slot_number", "INTEGER"),
    ("hybride_type", "TEXT"),
    ("sera_heritier", "INTEGER DEFAULT 0"),
    ("grade_choisi", "TEXT"),
    ("reroll_rct_charges", "INTEGER DEFAULT 0"),
    ("reroll_energie_charges", "INTEGER DEFAULT 0"),
    ("parchemins_territoire", "INTEGER DEFAULT 0"),
    ("parchemins_rct", "INTEGER DEFAULT 0"),
    ("parchemins_nature", "INTEGER DEFAULT 0"),
    ("rct", "INTEGER DEFAULT 0"),
    ("recompense", "TEXT"),
    ("argent_recompense", "INTEGER DEFAULT 0"),
    ("nom", "TEXT"),
    ("prenom", "TEXT"),
    ("age", "INTEGER"),
    ("histoire", "TEXT"),
    ("portrait_path", "TEXT"),
    ("fiche_status", "TEXT DEFAULT 'not_started'"),
    ("fiche_stage", "TEXT"),
    ("fiche_deadline", "TEXT"),
    ("fiche_question_msg_id", "INTEGER"),
    ("origin_channel_id", "INTEGER"),
]

# Colonnes de validated_characters ajoutées après coup.
_VALIDATED_EXTRA_COLUMNS = [
    ("portrait_path", "TEXT"),
    ("hybride_type", "TEXT"),
    ("grade", "TEXT"),
]


def _ensure_progress_columns(conn):
    """Ajoute à depart_character_progress les colonnes manquantes (DB existante)."""
    cols = _column_names(conn, "depart_character_progress")
    if not cols:
        return
    for name, decl in _PROGRESS_EXTRA_COLUMNS:
        if name not in cols:
            conn.execute(f"ALTER TABLE depart_character_progress ADD COLUMN {name} {decl}")


def _ensure_validated_columns(conn):
    """Ajoute à validated_characters les colonnes manquantes (DB existante)."""
    cols = _column_names(conn, "validated_characters")
    if not cols:
        return
    for name, decl in _VALIDATED_EXTRA_COLUMNS:
        if name not in cols:
            conn.execute(f"ALTER TABLE validated_characters ADD COLUMN {name} {decl}")


def init_db():
    """Crée les tables manquantes et applique les migrations légères. N'efface jamais de données."""
    with get_connection() as conn:
        needs_vc_copy = _pre_migrate_validated_characters(conn)
        conn.executescript(SCHEMA)
        if needs_vc_copy:
            _copy_validated_characters_from_old(conn)
        _ensure_progress_columns(conn)
        _ensure_validated_columns(conn)


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


# =====================================================================
# DEPART CHARACTER PROGRESS
# =====================================================================
_PROGRESS_SCALAR_COLS = (
    "guild_id", "slot_number", "camp", "path", "hybride_type", "clan", "sort",
    "sera_heritier", "grade_choisi", "eo_classe", "eo_value", "nature",
    "reroll_rct_charges", "reroll_energie_charges",
    "parchemins_territoire", "parchemins_rct", "parchemins_nature", "rct",
    "recompense", "argent_recompense", "nom", "prenom", "age", "histoire", "portrait_path",
    "fiche_status", "fiche_stage", "fiche_deadline", "fiche_question_msg_id", "origin_channel_id",
)

# Colonnes compteurs que l'on incrémente/décrémente atomiquement.
_PROGRESS_COUNTER_COLS = {
    "reroll_rct_charges", "reroll_energie_charges",
    "parchemins_territoire", "parchemins_rct", "parchemins_nature",
}


def get_character_progress(user_id: int) -> dict:
    """Reconstruit la progression sous la forme du dict historique (items/pending_rerolls en listes)."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM depart_character_progress WHERE user_id = ?", (user_id,)
        ).fetchone()
    if row is None:
        return {}
    return {
        "guild_id": row["guild_id"],
        "slot_number": row["slot_number"],
        "camp": row["camp"],
        "path": row["path"],
        "hybride_type": row["hybride_type"],
        "clan": row["clan"],
        "sort": row["sort"],
        "sera_heritier": row["sera_heritier"] or 0,
        "grade_choisi": row["grade_choisi"],
        "eo_classe": row["eo_classe"],
        "eo_value": row["eo_value"],
        "nature": row["nature"],
        "items": json.loads(row["items_json"]) if row["items_json"] else [],
        "pending_rerolls": json.loads(row["pending_rerolls_json"]) if row["pending_rerolls_json"] else [],
        "reroll_rct_charges": row["reroll_rct_charges"] or 0,
        "reroll_energie_charges": row["reroll_energie_charges"] or 0,
        "parchemins_territoire": row["parchemins_territoire"] or 0,
        "parchemins_rct": row["parchemins_rct"] or 0,
        "parchemins_nature": row["parchemins_nature"] or 0,
        "rct": row["rct"] or 0,
        "recompense": row["recompense"],
        "argent_recompense": row["argent_recompense"] or 0,
        "nom": row["nom"],
        "prenom": row["prenom"],
        "age": row["age"],
        "histoire": row["histoire"],
        "portrait_path": row["portrait_path"],
        "fiche_status": row["fiche_status"],
        "fiche_stage": row["fiche_stage"],
        "fiche_deadline": row["fiche_deadline"],
        "fiche_question_msg_id": row["fiche_question_msg_id"],
        "origin_channel_id": row["origin_channel_id"],
    }


def get_expired_fiches(now_iso: str):
    """Fiches en cours dont la deadline est dépassée."""
    with get_connection() as conn:
        return conn.execute(
            """SELECT user_id, guild_id, origin_channel_id, slot_number
               FROM depart_character_progress
               WHERE fiche_status = 'in_progress'
                 AND fiche_deadline IS NOT NULL AND fiche_deadline < ?""",
            (now_iso,),
        ).fetchall()


def insert_validated_character(user_id, guild_id, slot_number, discord_username, character_name,
                               camp, clan, sort, eo_classe, eo_value, nature, hybride_type,
                               grade, portrait_path, validated_at):
    """Insère un personnage validé (un joueur peut en avoir plusieurs, un par slot)."""
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO validated_characters
               (user_id, guild_id, slot_number, discord_username, character_name,
                camp, clan, sort, eo_classe, eo_value, nature, hybride_type, grade, portrait_path, validated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (user_id, guild_id, slot_number, discord_username, character_name,
             camp, clan, sort, eo_classe, eo_value, nature, hybride_type, grade, portrait_path, validated_at),
        )


def count_validated_grade(guild_id: int, clan: str, grade: str) -> int:
    """Nombre de personnages VALIDÉS occupant un grade précis dans un clan précis.
    Source de vérité pour les places de grade (les rôles Discord ne sont posés qu'à la validation)."""
    with get_connection() as conn:
        return conn.execute(
            "SELECT COUNT(*) AS n FROM validated_characters WHERE guild_id = ? AND clan = ? AND grade = ?",
            (guild_id, clan, grade),
        ).fetchone()["n"]


def adjust_progress_counter(user_id: int, column: str, delta: int):
    """Incrémente/décrémente atomiquement une colonne compteur (crée la ligne au besoin)."""
    if column not in _PROGRESS_COUNTER_COLS:
        raise ValueError(f"Colonne compteur inconnue : {column}")
    with get_connection() as conn:
        conn.execute("INSERT OR IGNORE INTO depart_character_progress (user_id) VALUES (?)", (user_id,))
        conn.execute(
            f"UPDATE depart_character_progress SET {column} = COALESCE({column}, 0) + ?, updated_at = ? "
            "WHERE user_id = ?",
            (delta, datetime.utcnow().isoformat(), user_id),
        )


def upsert_character_progress(user_id: int, fields: dict):
    """Fusionne les champs scalaires fournis (crée la ligne si besoin), sans écraser le reste."""
    with get_connection() as conn:
        conn.execute("INSERT OR IGNORE INTO depart_character_progress (user_id) VALUES (?)", (user_id,))
        assignments, values = [], []
        for col in _PROGRESS_SCALAR_COLS:
            if col in fields:
                assignments.append(f"{col} = ?")
                values.append(fields[col])
        assignments.append("updated_at = ?")
        values.append(datetime.utcnow().isoformat())
        values.append(user_id)
        conn.execute(
            f"UPDATE depart_character_progress SET {', '.join(assignments)} WHERE user_id = ?",
            values,
        )


def add_character_item(user_id: int, name: str):
    with get_connection() as conn:
        conn.execute("INSERT OR IGNORE INTO depart_character_progress (user_id) VALUES (?)", (user_id,))
        row = conn.execute(
            "SELECT items_json FROM depart_character_progress WHERE user_id = ?", (user_id,)
        ).fetchone()
        items = json.loads(row["items_json"]) if row and row["items_json"] else []
        items.append(name)
        conn.execute(
            "UPDATE depart_character_progress SET items_json = ?, updated_at = ? WHERE user_id = ?",
            (json.dumps(items, ensure_ascii=False), datetime.utcnow().isoformat(), user_id),
        )


def add_character_pending_reroll(user_id: int, key: str):
    with get_connection() as conn:
        conn.execute("INSERT OR IGNORE INTO depart_character_progress (user_id) VALUES (?)", (user_id,))
        row = conn.execute(
            "SELECT pending_rerolls_json FROM depart_character_progress WHERE user_id = ?", (user_id,)
        ).fetchone()
        rerolls = json.loads(row["pending_rerolls_json"]) if row and row["pending_rerolls_json"] else []
        rerolls.append(key)
        conn.execute(
            "UPDATE depart_character_progress SET pending_rerolls_json = ?, updated_at = ? WHERE user_id = ?",
            (json.dumps(rerolls, ensure_ascii=False), datetime.utcnow().isoformat(), user_id),
        )


# =====================================================================
# DEPART PENDING REWARDS
# =====================================================================
def set_pending_rewards(user_id: int, option_a: dict, option_b: dict):
    with get_connection() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO depart_pending_rewards (user_id, option_a_json, option_b_json)
               VALUES (?, ?, ?)""",
            (user_id, json.dumps(option_a, ensure_ascii=False), json.dumps(option_b, ensure_ascii=False)),
        )


def get_pending_rewards(user_id: int):
    """Retourne {'option_a': {...}, 'option_b': {...}} ou None."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT option_a_json, option_b_json FROM depart_pending_rewards WHERE user_id = ?", (user_id,)
        ).fetchone()
    if row is None:
        return None
    return {
        "option_a": json.loads(row["option_a_json"]),
        "option_b": json.loads(row["option_b_json"]),
    }


def delete_pending_rewards(user_id: int):
    with get_connection() as conn:
        conn.execute("DELETE FROM depart_pending_rewards WHERE user_id = ?", (user_id,))


# =====================================================================
# DEPART PENDING RESERVE CHOICE (choix manuel de la classe de réserve)
# =====================================================================
def set_pending_reserve_choice(user_id: int, classe: str):
    with get_connection() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO depart_pending_reserve_choice (user_id, classe) VALUES (?, ?)",
            (user_id, classe),
        )


def get_pending_reserve_choice(user_id: int):
    with get_connection() as conn:
        row = conn.execute(
            "SELECT classe FROM depart_pending_reserve_choice WHERE user_id = ?", (user_id,)
        ).fetchone()
    return row["classe"] if row else None


def delete_pending_reserve_choice(user_id: int):
    with get_connection() as conn:
        conn.execute("DELETE FROM depart_pending_reserve_choice WHERE user_id = ?", (user_id,))


# =====================================================================
# VALIDATED CHARACTERS (personnages validés, jusqu'à 3 slots par joueur)
# =====================================================================
def get_validated_characters(user_id: int, guild_id: int):
    """Slots occupés d'un joueur sur un serveur, triés par numéro de slot croissant."""
    with get_connection() as conn:
        return conn.execute(
            """SELECT slot_number, character_name, camp, clan, hybride_type, portrait_path
               FROM validated_characters WHERE user_id = ? AND guild_id = ?
               ORDER BY slot_number ASC""",
            (user_id, guild_id),
        ).fetchall()


def delete_validated_character(user_id: int, guild_id: int, slot_number: int) -> int:
    """Supprime définitivement le personnage d'un slot. Renvoie le nombre de lignes supprimées."""
    with get_connection() as conn:
        cur = conn.execute(
            "DELETE FROM validated_characters WHERE user_id = ? AND guild_id = ? AND slot_number = ?",
            (user_id, guild_id, slot_number),
        )
        return cur.rowcount


def get_class_ranking(eo_classe: str):
    """Classement (pseudo Discord + valeur EO) des personnages validés d'une classe donnée."""
    with get_connection() as conn:
        return conn.execute(
            "SELECT discord_username, eo_value FROM validated_characters WHERE eo_classe = ? ORDER BY eo_value DESC",
            (eo_classe,),
        ).fetchall()
