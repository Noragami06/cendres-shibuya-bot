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
    rct INTEGER DEFAULT 0,
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
    created_at TEXT,
    is_at_risk INTEGER DEFAULT 0,
    deletion_deadline TEXT
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

CREATE TABLE IF NOT EXISTS shop_categories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE
);

CREATE TABLE IF NOT EXISTS item_definitions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE,
    description TEXT,
    classe TEXT,
    valeur_base INTEGER,
    categorie_id INTEGER
);

CREATE TABLE IF NOT EXISTS character_inventory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    character_id INTEGER,
    item_id INTEGER,
    quantity INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS pending_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    proposer_user_id INTEGER,
    proposer_character_id INTEGER,
    target_user_id INTEGER,
    target_character_id INTEGER,
    status TEXT DEFAULT 'awaiting_response',
    offered_item_id INTEGER,
    offered_quantity INTEGER,
    request_type TEXT,
    request_amount INTEGER,
    request_item_id INTEGER,
    request_item_quantity INTEGER,
    channel_id INTEGER,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS character_virtual_roles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    character_id INTEGER,
    role_id INTEGER,
    UNIQUE(character_id, role_id)
);

-- force_level/vitesse_level/defense_level et leurs xp_actuel/xp_max ne sont plus utilisés :
-- Force/Vitesse/Défense sont maintenant entièrement dérivées de character_stats
-- (force_pts/vitesse_pts/endurance_pts + buffs), calculées à la volée à chaque affichage.
CREATE TABLE IF NOT EXISTS character_profiles (
    character_id INTEGER PRIMARY KEY,
    pv_actuel INTEGER DEFAULT 100,
    pv_max INTEGER DEFAULT 100,
    eo_actuel INTEGER DEFAULT 100,
    eo_max INTEGER DEFAULT 100,
    level INTEGER DEFAULT 1,
    xp_actuel INTEGER DEFAULT 0,
    xp_max INTEGER DEFAULT 1000,
    force_level INTEGER DEFAULT 1,
    force_xp_actuel INTEGER DEFAULT 0,
    force_xp_max INTEGER DEFAULT 1000,
    vitesse_level INTEGER DEFAULT 1,
    vitesse_xp_actuel INTEGER DEFAULT 0,
    vitesse_xp_max INTEGER DEFAULT 1000,
    defense_level INTEGER DEFAULT 1,
    defense_xp_actuel INTEGER DEFAULT 0,
    defense_xp_max INTEGER DEFAULT 1000,
    maitrise_eo_level INTEGER DEFAULT 1,
    victoires INTEGER DEFAULT 0,
    defaites INTEGER DEFAULT 0,
    nuls INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS character_backgrounds (
    character_id INTEGER PRIMARY KEY,
    image_path TEXT,
    uploaded_at TEXT
);

CREATE TABLE IF NOT EXISTS character_stats (
    character_id INTEGER PRIMARY KEY,
    force_pts INTEGER DEFAULT 0,
    rct_pts INTEGER DEFAULT 0,
    vitesse_pts INTEGER DEFAULT 0,
    territoire_pts INTEGER DEFAULT 0,
    endurance_pts INTEGER DEFAULT 0,
    sorts_pts INTEGER DEFAULT 0,
    armes_maudites_pts INTEGER DEFAULT 0,
    energie_occulte_pts INTEGER DEFAULT 0,
    points_restants INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS character_buffs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    character_id INTEGER,
    buff_name TEXT
);

CREATE TABLE IF NOT EXISTS character_buff_effects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    buff_id INTEGER,
    stat_key TEXT,
    points INTEGER
);

CREATE TABLE IF NOT EXISTS character_relations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    character_id INTEGER,
    related_character_id INTEGER,
    category TEXT,
    label TEXT
);

CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chef_character_id INTEGER UNIQUE,
    type TEXT,                      -- 'educatif', 'direct', 'hybride'
    name TEXT,
    solde_courant INTEGER DEFAULT 0,
    iban TEXT,                      -- unicité garantie par idx_orders_iban (créé dans init_db)
    pin_code TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS order_bank_sessions (
    user_id INTEGER,
    order_id INTEGER,
    verified_at TEXT,
    PRIMARY KEY (user_id, order_id)
);

CREATE TABLE IF NOT EXISTS order_members (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER,
    character_id INTEGER,
    role_label TEXT                 -- 'Sous-chef', 'Formateur', 'Chef d''équipe', 'Membre d''équipe', 'Corps administratif'
);

CREATE TABLE IF NOT EXISTS order_salons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER,
    channel_id INTEGER,
    status TEXT,                    -- 'Acheté', 'Louée', 'Location'
    linked_order_id INTEGER,        -- ordre source (Louée) ou ordre cible (Location)
    location_expiry TEXT            -- date de fin si statut = Location, sinon NULL
);

CREATE TABLE IF NOT EXISTS order_transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER,
    label TEXT,
    amount INTEGER,
    date TEXT
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
    ("rct", "INTEGER DEFAULT 0"),
]

# Colonnes de bank_accounts ajoutées après coup.
_BANK_EXTRA_COLUMNS = [
    ("is_at_risk", "INTEGER DEFAULT 0"),
    ("deletion_deadline", "TEXT"),
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


def _ensure_bank_columns(conn):
    """Ajoute à bank_accounts les colonnes manquantes (DB existante)."""
    cols = _column_names(conn, "bank_accounts")
    if not cols:
        return
    for name, decl in _BANK_EXTRA_COLUMNS:
        if name not in cols:
            conn.execute(f"ALTER TABLE bank_accounts ADD COLUMN {name} {decl}")


def _migrate_item_categorie_id(conn):
    """Migre item_definitions de l'ancienne colonne texte `categorie` vers `categorie_id`
    (référence shop_categories.id). Pour chaque valeur texte distincte, crée la catégorie
    correspondante si besoin puis remplit categorie_id. L'ancienne colonne `categorie` est
    conservée (SQLite ne supprime pas facilement une colonne) mais n'est plus lue nulle part :
    tout le code référence désormais categorie_id."""
    cols = _column_names(conn, "item_definitions")
    if not cols:
        return
    if "categorie_id" not in cols:
        conn.execute("ALTER TABLE item_definitions ADD COLUMN categorie_id INTEGER")
        cols.append("categorie_id")
    # Migration des anciennes catégories texte, uniquement si l'ancienne colonne existe encore.
    if "categorie" in cols:
        rows = conn.execute(
            "SELECT DISTINCT categorie FROM item_definitions "
            "WHERE categorie IS NOT NULL AND TRIM(categorie) <> '' AND categorie_id IS NULL"
        ).fetchall()
        for r in rows:
            cat_name = r["categorie"]
            conn.execute("INSERT OR IGNORE INTO shop_categories (name) VALUES (?)", (cat_name,))
            cat = conn.execute(
                "SELECT id FROM shop_categories WHERE name = ?", (cat_name,)
            ).fetchone()
            conn.execute(
                "UPDATE item_definitions SET categorie_id = ? "
                "WHERE categorie = ? AND categorie_id IS NULL",
                (cat["id"], cat_name),
            )


def _ensure_order_columns(conn):
    """Ajoute iban / pin_code à une table orders préexistante (créée avant l'ajout du système
    bancaire des ordres). SQLite interdit d'ajouter une colonne UNIQUE via ALTER : l'unicité de
    l'IBAN est assurée par un index unique créé juste après (idx_orders_iban)."""
    cols = _column_names(conn, "orders")
    if not cols:
        return
    if "iban" not in cols:
        conn.execute("ALTER TABLE orders ADD COLUMN iban TEXT")
    if "pin_code" not in cols:
        conn.execute("ALTER TABLE orders ADD COLUMN pin_code TEXT")


def init_db():
    """Crée les tables manquantes et applique les migrations légères. N'efface jamais de données."""
    with get_connection() as conn:
        needs_vc_copy = _pre_migrate_validated_characters(conn)
        conn.executescript(SCHEMA)
        if needs_vc_copy:
            _copy_validated_characters_from_old(conn)
        _ensure_progress_columns(conn)
        _ensure_validated_columns(conn)
        _ensure_bank_columns(conn)
        _ensure_order_columns(conn)
        # Unicité de l'IBAN d'ordre (fonctionne aussi sur une base migrée ; NULL multiples autorisés).
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_orders_iban ON orders(iban)")
        _migrate_item_categorie_id(conn)


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
                               grade, portrait_path, validated_at, rct=0):
    """Insère un personnage validé (un joueur peut en avoir plusieurs, un par slot)."""
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO validated_characters
               (user_id, guild_id, slot_number, discord_username, character_name,
                camp, clan, sort, eo_classe, eo_value, nature, hybride_type, grade, rct,
                portrait_path, validated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (user_id, guild_id, slot_number, discord_username, character_name,
             camp, clan, sort, eo_classe, eo_value, nature, hybride_type, grade, rct,
             portrait_path, validated_at),
        )


def count_clan_members(guild_id: int, clan: str) -> int:
    """Nombre de personnages VALIDÉS d'un clan (réels ET virtuels, TOUS slots confondus).
    Source de vérité des places de clan : validated_characters, jamais les rôles Discord (les slots
    2/3 n'ont que des rôles virtuels enregistrés en base)."""
    if not clan:
        return 0
    with get_connection() as conn:
        return conn.execute(
            "SELECT COUNT(*) AS n FROM validated_characters WHERE clan = ? AND guild_id = ?",
            (clan, guild_id),
        ).fetchone()["n"]


def heir_exists(guild_id: int, clan: str) -> bool:
    """True si un personnage validé de ce clan porte déjà le grade 'Héritier' (réel OU virtuel).
    Source de vérité : la base, pas les rôles Discord (un héritier de slot 2/3 n'a pas de rôle)."""
    if not clan:
        return False
    with get_connection() as conn:
        row = conn.execute(
            "SELECT 1 FROM validated_characters WHERE clan = ? AND grade = 'Héritier' AND guild_id = ? LIMIT 1",
            (clan, guild_id),
        ).fetchone()
    return row is not None


def add_virtual_role(character_id: int, role_id: int) -> bool:
    """Associe (virtuellement) un rôle Discord à un personnage, en base uniquement (aucun rôle réel
    n'est posé). La contrainte UNIQUE(character_id, role_id) empêche les doublons. Retourne True si
    la ligne a été réellement insérée (False si le couple existait déjà). Aucune limite au nombre de
    rôles par personnage."""
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO character_virtual_roles (character_id, role_id) VALUES (?, ?)",
            (character_id, role_id),
        )
        return cur.rowcount > 0


def get_virtual_roles(character_id: int):
    """Rôles virtuels enregistrés pour un personnage (liste de role_id)."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT role_id FROM character_virtual_roles WHERE character_id = ?", (character_id,)
        ).fetchall()
    return [r["role_id"] for r in rows]


# ---------- Profils de personnage (/profil) ----------
# Colonnes modifiables de character_profiles (character_id exclu : c'est la clé).
_PROFILE_COLUMNS = frozenset({
    "pv_actuel", "pv_max", "eo_actuel", "eo_max", "level", "xp_actuel", "xp_max",
    "force_level", "force_xp_actuel", "force_xp_max",
    "vitesse_level", "vitesse_xp_actuel", "vitesse_xp_max",
    "defense_level", "defense_xp_actuel", "defense_xp_max",
    "maitrise_eo_level", "victoires", "defaites", "nuls",
})


def get_or_create_profile(character_id: int):
    """Retourne la ligne character_profiles du personnage, en la créant avec les valeurs par défaut
    de la table si elle n'existe pas encore (premier affichage du profil)."""
    with get_connection() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO character_profiles (character_id) VALUES (?)", (character_id,)
        )
        return conn.execute(
            "SELECT * FROM character_profiles WHERE character_id = ?", (character_id,)
        ).fetchone()


def update_profile(character_id: int, **fields):
    """Met à jour des colonnes de character_profiles (crée la ligne au besoin). Seules les colonnes
    connues sont acceptées."""
    fields = {k: v for k, v in fields.items() if k in _PROFILE_COLUMNS}
    if not fields:
        return
    with get_connection() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO character_profiles (character_id) VALUES (?)", (character_id,)
        )
        assignments = ", ".join(f"{k} = ?" for k in fields)
        conn.execute(
            f"UPDATE character_profiles SET {assignments} WHERE character_id = ?",
            (*fields.values(), character_id),
        )


def create_profile_from_fiche(character_id: int, eo_value):
    """Crée (ou remplace) la ligne character_profiles d'un personnage avec les VRAIES valeurs issues
    du parcours /depart, au lieu de laisser les DEFAULT génériques de la table s'appliquer.

    - eo_actuel/eo_max = eo_value (réserve pleine au départ). Si eo_value est None (Humain / Hybride
      chez les humains, sans réserve tirée) : 0/0 au lieu de planter sur NULL.
    - Le reste (PV, level/XP, Force/Vitesse/Défense, maîtrise, combats) reste neutre tant que les
      systèmes correspondants ne sont pas développés (cf. TODO.md).
    INSERT OR REPLACE : idempotent si une ligne existait déjà (sécurité, pas d'erreur de PK)."""
    eo = int(eo_value) if eo_value is not None else 0
    with get_connection() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO character_profiles (
                   character_id,
                   pv_actuel, pv_max,
                   eo_actuel, eo_max,
                   level, xp_actuel, xp_max,
                   force_level, force_xp_actuel, force_xp_max,
                   vitesse_level, vitesse_xp_actuel, vitesse_xp_max,
                   defense_level, defense_xp_actuel, defense_xp_max,
                   maitrise_eo_level,
                   victoires, defaites, nuls
               ) VALUES (?, 100, 100, ?, ?, 1, 0, 1000,
                         1, 0, 1000, 1, 0, 1000, 1, 0, 1000, 1, 0, 0, 0)""",
            (character_id, eo, eo),
        )


def get_background(character_id: int):
    """Fond d'écran propre à CE personnage (jamais partagé entre personnages), ou None."""
    with get_connection() as conn:
        return conn.execute(
            "SELECT image_path, uploaded_at FROM character_backgrounds WHERE character_id = ?",
            (character_id,),
        ).fetchone()


def set_background(character_id: int, image_path: str, uploaded_at: str):
    """Enregistre/actualise le fond d'un personnage (INSERT OR REPLACE : un seul fond par personnage)."""
    with get_connection() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO character_backgrounds (character_id, image_path, uploaded_at) "
            "VALUES (?, ?, ?)",
            (character_id, image_path, uploaded_at),
        )


# ---------- Statistiques de personnage (points liés + buffs) ----------
_STAT_KEYS_DB = ("force", "rct", "vitesse", "territoire", "endurance", "sorts", "armes_maudites", "energie_occulte")


def _stat_col(stat_key: str) -> str:
    """Nom de colonne _pts sûr (whitelist) pour éviter toute injection dans un SQL dynamique."""
    if stat_key not in _STAT_KEYS_DB:
        raise ValueError(f"stat inconnue : {stat_key}")
    return f"{stat_key}_pts"


def create_stats_default(character_id: int):
    """Crée la ligne character_stats par défaut (tout à 0, points_restants à 0 pour l'instant).
    Appelé à la validation de fiche. Idempotent."""
    with get_connection() as conn:
        conn.execute("INSERT OR IGNORE INTO character_stats (character_id) VALUES (?)", (character_id,))


def get_or_create_stats(character_id: int):
    with get_connection() as conn:
        conn.execute("INSERT OR IGNORE INTO character_stats (character_id) VALUES (?)", (character_id,))
        return conn.execute(
            "SELECT * FROM character_stats WHERE character_id = ?", (character_id,)
        ).fetchone()


def get_stat_base_pts(character_id: int, stat_key: str) -> int:
    col = _stat_col(stat_key)
    with get_connection() as conn:
        conn.execute("INSERT OR IGNORE INTO character_stats (character_id) VALUES (?)", (character_id,))
        row = conn.execute(
            f"SELECT {col} AS v FROM character_stats WHERE character_id = ?", (character_id,)
        ).fetchone()
    return row["v"] if row else 0


def set_stat_base_pts(character_id: int, stat_key: str, value: int):
    """Écriture ABSOLUE de la base d'une stat (remplacement, pas additif)."""
    col = _stat_col(stat_key)
    with get_connection() as conn:
        conn.execute("INSERT OR IGNORE INTO character_stats (character_id) VALUES (?)", (character_id,))
        conn.execute(
            f"UPDATE character_stats SET {col} = ? WHERE character_id = ?", (int(value), character_id)
        )


def add_stat_points_from_pool(character_id: int, stat_key: str, n: int):
    """Répartition joueur : +n sur la stat et -n sur points_restants, ATOMIQUE. Retourne le nouveau
    points_restants, ou None si le solde de points est insuffisant (aucune modification faite)."""
    col = _stat_col(stat_key)
    with get_connection() as conn:
        conn.execute("INSERT OR IGNORE INTO character_stats (character_id) VALUES (?)", (character_id,))
        row = conn.execute(
            "SELECT points_restants FROM character_stats WHERE character_id = ?", (character_id,)
        ).fetchone()
        if row is None or row["points_restants"] < n:
            return None
        conn.execute(
            f"UPDATE character_stats SET {col} = {col} + ?, points_restants = points_restants - ? "
            "WHERE character_id = ?",
            (n, n, character_id),
        )
        new = conn.execute(
            "SELECT points_restants FROM character_stats WHERE character_id = ?", (character_id,)
        ).fetchone()
    return new["points_restants"]


def set_points_restants(character_id: int, value: int):
    with get_connection() as conn:
        conn.execute("INSERT OR IGNORE INTO character_stats (character_id) VALUES (?)", (character_id,))
        conn.execute(
            "UPDATE character_stats SET points_restants = ? WHERE character_id = ?", (int(value), character_id)
        )


def sum_buff_points(character_id: int, stat_key: str) -> int:
    """Somme des effets de buffs actifs pour une stat précise (0 si aucun)."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(cbe.points), 0) AS s FROM character_buff_effects cbe "
            "JOIN character_buffs cb ON cbe.buff_id = cb.id "
            "WHERE cb.character_id = ? AND cbe.stat_key = ?",
            (character_id, stat_key),
        ).fetchone()
    return row["s"] if row else 0


def add_buff(character_id: int, buff_name: str) -> int:
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO character_buffs (character_id, buff_name) VALUES (?, ?)", (character_id, buff_name)
        )
        return cur.lastrowid


def add_buff_effect(buff_id: int, stat_key: str, points: int):
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO character_buff_effects (buff_id, stat_key, points) VALUES (?, ?, ?)",
            (buff_id, stat_key, int(points)),
        )


def get_buff_names(character_id: int):
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT DISTINCT buff_name FROM character_buffs WHERE character_id = ? ORDER BY buff_name",
            (character_id,),
        ).fetchall()
    return [r["buff_name"] for r in rows]


def get_buffs_with_effects(character_id: int):
    """Pour l'affichage : [(buff_name, [(stat_key, points), ...]), ...], regroupé par buff_name."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT cb.buff_name, cbe.stat_key, cbe.points FROM character_buffs cb "
            "LEFT JOIN character_buff_effects cbe ON cbe.buff_id = cb.id "
            "WHERE cb.character_id = ? ORDER BY cb.id, cbe.id",
            (character_id,),
        ).fetchall()
    grouped, order = {}, []
    for r in rows:
        name = r["buff_name"]
        if name not in grouped:
            grouped[name] = []
            order.append(name)
        if r["stat_key"] is not None:
            grouped[name].append((r["stat_key"], r["points"]))
    return [(name, grouped[name]) for name in order]


def remove_buff(character_id: int, buff_name: str):
    """Supprime un buff (par nom) et tous ses effets."""
    with get_connection() as conn:
        conn.execute(
            "DELETE FROM character_buff_effects WHERE buff_id IN "
            "(SELECT id FROM character_buffs WHERE character_id = ? AND buff_name = ?)",
            (character_id, buff_name),
        )
        conn.execute(
            "DELETE FROM character_buffs WHERE character_id = ? AND buff_name = ?", (character_id, buff_name)
        )


# ---------- Relations / liens entre personnages (/profil -> 🤝 Relation) ----------
_RELATION_CATEGORIES = ("Famille", "Amis", "Autres")


def add_relation(character_id: int, related_character_id: int, category: str, label: str) -> int:
    """Crée un lien orienté de character_id vers related_character_id (catégorie + intitulé libre).
    Retourne l'id de la ligne créée. Un même couple peut avoir plusieurs liens (catégories/labels
    différents) : c'est volontaire, on n'impose pas d'unicité."""
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO character_relations (character_id, related_character_id, category, label) "
            "VALUES (?, ?, ?, ?)",
            (character_id, related_character_id, category, label),
        )
        return cur.lastrowid


def get_relations(character_id: int):
    """Liste des liens du personnage : [(id, related_character_id, category, label, related_name), ...]
    dans l'ordre de création. related_name vient d'un JOIN sur validated_characters (None si le
    personnage lié a été supprimé — ne devrait plus arriver grâce au nettoyage cascade)."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT r.id, r.related_character_id, r.category, r.label, v.character_name AS related_name "
            "FROM character_relations r "
            "LEFT JOIN validated_characters v ON v.id = r.related_character_id "
            "WHERE r.character_id = ? ORDER BY r.id ASC",
            (character_id,),
        ).fetchall()
    return [
        (r["id"], r["related_character_id"], r["category"], r["label"], r["related_name"])
        for r in rows
    ]


def has_relations(character_id: int) -> bool:
    """True si le personnage possède au moins un lien (pour n'afficher '➖ Retirer un lien' que si utile)."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT 1 FROM character_relations WHERE character_id = ? LIMIT 1", (character_id,)
        ).fetchone()
    return row is not None


def get_relations_between(character_id: int, related_character_id: int):
    """Tous les liens existants de character_id VERS related_character_id (peut y en avoir plusieurs) :
    [(id, category, label), ...]."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, category, label FROM character_relations "
            "WHERE character_id = ? AND related_character_id = ? ORDER BY id ASC",
            (character_id, related_character_id),
        ).fetchall()
    return [(r["id"], r["category"], r["label"]) for r in rows]


def get_linked_characters_of_user(character_id: int, target_user_id: int, guild_id: int):
    """Personnages validés de target_user_id qui ont AU MOINS UN lien reçu depuis character_id.
    Retourne [(related_character_id, character_name, slot_number, nb_liens), ...]. Sert au flux
    '➖ Retirer un lien' : on ne propose que les personnages réellement liés."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT v.id AS cid, v.character_name AS name, v.slot_number AS slot, COUNT(r.id) AS n "
            "FROM validated_characters v "
            "JOIN character_relations r ON r.related_character_id = v.id AND r.character_id = ? "
            "WHERE v.user_id = ? AND v.guild_id = ? "
            "GROUP BY v.id ORDER BY v.slot_number ASC",
            (character_id, target_user_id, guild_id),
        ).fetchall()
    return [(r["cid"], r["name"], r["slot"], r["n"]) for r in rows]


def delete_relation_by_id(relation_id: int):
    """Supprime un lien précis (par son id)."""
    with get_connection() as conn:
        conn.execute("DELETE FROM character_relations WHERE id = ?", (relation_id,))


def delete_relations_between(character_id: int, related_character_id: int):
    """Supprime TOUS les liens de character_id vers related_character_id."""
    with get_connection() as conn:
        conn.execute(
            "DELETE FROM character_relations WHERE character_id = ? AND related_character_id = ?",
            (character_id, related_character_id),
        )


# ---------- Ordres (/ordre) ----------
def get_order(order_id: int):
    with get_connection() as conn:
        return conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()


def get_order_by_chief(chef_character_id: int):
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM orders WHERE chef_character_id = ?", (chef_character_id,)
        ).fetchone()


def create_order(chef_character_id: int, type_: str, name: str, created_at: str) -> int:
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO orders (chef_character_id, type, name, solde_courant, created_at) "
            "VALUES (?, ?, ?, 0, ?)",
            (chef_character_id, type_, name, created_at),
        )
        return cur.lastrowid


def adjust_order_solde(order_id: int, delta: int) -> int:
    """Ajoute delta (peut être négatif) au solde de l'ordre, retourne le nouveau solde."""
    with get_connection() as conn:
        conn.execute("UPDATE orders SET solde_courant = solde_courant + ? WHERE id = ?", (delta, order_id))
        return conn.execute(
            "SELECT solde_courant FROM orders WHERE id = ?", (order_id,)
        ).fetchone()["solde_courant"]


def add_order_transaction(order_id: int, label: str, amount: int, date: str):
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO order_transactions (order_id, label, amount, date) VALUES (?, ?, ?, ?)",
            (order_id, label, amount, date),
        )


def get_order_transactions_since(order_id: int, since_iso: str):
    """Transactions de l'ordre depuis une date ISO (pour le graphe de profit hebdo)."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT amount, date FROM order_transactions WHERE order_id = ? AND date >= ?",
            (order_id, since_iso),
        ).fetchall()
    return [(r["amount"], r["date"]) for r in rows]


def get_order_members(order_id: int):
    """Membres de l'ordre (hors chef) avec les infos du personnage."""
    with get_connection() as conn:
        return conn.execute(
            "SELECT m.id, m.character_id, m.role_label, v.character_name, v.user_id, v.slot_number "
            "FROM order_members m LEFT JOIN validated_characters v ON v.id = m.character_id "
            "WHERE m.order_id = ? ORDER BY m.id ASC",
            (order_id,),
        ).fetchall()


def get_order_member(order_id: int, character_id: int):
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM order_members WHERE order_id = ? AND character_id = ?",
            (order_id, character_id),
        ).fetchone()


def add_order_member(order_id: int, character_id: int, role_label: str):
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO order_members (order_id, character_id, role_label) VALUES (?, ?, ?)",
            (order_id, character_id, role_label),
        )


def remove_order_member(order_id: int, character_id: int):
    with get_connection() as conn:
        conn.execute(
            "DELETE FROM order_members WHERE order_id = ? AND character_id = ?", (order_id, character_id)
        )


def update_order_member_role(order_id: int, character_id: int, role_label: str):
    with get_connection() as conn:
        conn.execute(
            "UPDATE order_members SET role_label = ? WHERE order_id = ? AND character_id = ?",
            (role_label, order_id, character_id),
        )


def get_order_salons(order_id: int):
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM order_salons WHERE order_id = ? ORDER BY id ASC", (order_id,)
        ).fetchall()


def add_order_salon(order_id: int, channel_id: int, status: str,
                    linked_order_id=None, location_expiry=None) -> int:
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO order_salons (order_id, channel_id, status, linked_order_id, location_expiry) "
            "VALUES (?, ?, ?, ?, ?)",
            (order_id, channel_id, status, linked_order_id, location_expiry),
        )
        return cur.lastrowid


def get_salon_owner(channel_id: int, status: str = "Acheté"):
    """Order_salon (n'importe quel ordre) possédant ce salon avec ce statut, ou None. Sert à détecter
    les conflits d'achat (un salon déjà possédé par un ordre ne peut pas être racheté)."""
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM order_salons WHERE channel_id = ? AND status = ?", (channel_id, status)
        ).fetchone()


def remove_order_salon_by_channel(order_id: int, channel_id: int, status: str = "Acheté"):
    with get_connection() as conn:
        conn.execute(
            "DELETE FROM order_salons WHERE order_id = ? AND channel_id = ? AND status = ?",
            (order_id, channel_id, status),
        )


def transfer_salon(channel_id: int, from_order_id: int, to_order_id: int):
    """Transfère la propriété d'un salon 'Acheté' d'un ordre à un autre (revente à un joueur)."""
    with get_connection() as conn:
        conn.execute(
            "UPDATE order_salons SET order_id = ? "
            "WHERE order_id = ? AND channel_id = ? AND status = 'Acheté'",
            (to_order_id, from_order_id, channel_id),
        )


def get_orders_in_guild(guild_id: int, exclude_order_id=None):
    """Ordres du serveur (le guild est déterminé via le personnage chef) :
    [(order_id, name, chef_name), ...]."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT o.id, o.name, v.character_name AS chef_name FROM orders o "
            "JOIN validated_characters v ON v.id = o.chef_character_id "
            "WHERE v.guild_id = ? AND (? IS NULL OR o.id != ?) ORDER BY o.id ASC",
            (guild_id, exclude_order_id, exclude_order_id),
        ).fetchall()
    return [(r["id"], r["name"], r["chef_name"]) for r in rows]


# ---------- Banque des ordres ----------
# NB : le jour où une suppression d'ordre sera implémentée, penser à nettoyer TOUTES les tables liées
# à l'ordre : orders, order_members, order_salons, order_transactions ET order_bank_sessions.
def get_order_by_iban(iban: str):
    with get_connection() as conn:
        return conn.execute("SELECT * FROM orders WHERE iban = ?", (iban,)).fetchone()


def set_order_bank_creds(order_id: int, iban: str, pin_code: str):
    with get_connection() as conn:
        conn.execute("UPDATE orders SET iban = ?, pin_code = ? WHERE id = ?", (iban, pin_code, order_id))


def get_order_bank_session(user_id: int, order_id: int):
    with get_connection() as conn:
        return conn.execute(
            "SELECT verified_at FROM order_bank_sessions WHERE user_id = ? AND order_id = ?",
            (user_id, order_id),
        ).fetchone()


def set_order_bank_session(user_id: int, order_id: int, verified_at: str):
    with get_connection() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO order_bank_sessions (user_id, order_id, verified_at) VALUES (?, ?, ?)",
            (user_id, order_id, verified_at),
        )


def get_recent_order_transactions(order_id: int, limit: int = 4):
    with get_connection() as conn:
        return conn.execute(
            "SELECT label, amount, date FROM order_transactions WHERE order_id = ? ORDER BY id DESC LIMIT ?",
            (order_id, limit),
        ).fetchall()


def count_order_salons(order_id: int, status: str) -> int:
    with get_connection() as conn:
        return conn.execute(
            "SELECT COUNT(*) AS n FROM order_salons WHERE order_id = ? AND status = ?", (order_id, status)
        ).fetchone()["n"]


def renumber_character_slots(user_id: int, guild_id: int):
    """Renumérote les slots restants d'un joueur pour qu'ils soient consécutifs à partir de 1, dans
    l'ordre croissant des slot_number actuels. Ne touche QUE la colonne slot_number (aucun rôle
    Discord). Retourne la liste des changements [(character_id, ancien_slot, nouveau_slot), ...] pour
    les personnages dont le numéro a réellement changé (permet de notifier le joueur)."""
    changes = []
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, slot_number FROM validated_characters "
            "WHERE user_id = ? AND guild_id = ? ORDER BY slot_number ASC",
            (user_id, guild_id),
        ).fetchall()
        for new_slot, row in enumerate(rows, start=1):
            if row["slot_number"] != new_slot:
                conn.execute(
                    "UPDATE validated_characters SET slot_number = ? WHERE id = ?",
                    (new_slot, row["id"]),
                )
                changes.append((row["id"], row["slot_number"], new_slot))
    return changes


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
