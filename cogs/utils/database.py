import json
import os
import sqlite3
from datetime import datetime

DB_FILE = os.path.join(os.path.dirname(__file__), "..", "..", "data", "bot.db")

# =====================================================================
# SYSTÈME D'XP EXPONENTIEL PARTAGÉ (niveau du personnage ET niveau de chaque sort principal)
# =====================================================================
XP_BASE = 1000
XP_GROWTH = 1.15

# TODO TEMPORAIRE : valeur provisoire en attendant le vrai système de gain d'XP en Phase 2 (mini-jeu ou
# combat, pas encore développé). Cette constante n'est utilisée que pour calculer xp_max à afficher,
# aucune source ne génère encore d'XP réelle pour la Phase 2 actuellement.
PHASE2_XP_PER_LEVEL = 500


def xp_required_for_level(level: int) -> int:
    return round(XP_BASE * (XP_GROWTH ** (level - 1)))


def apply_xp_gain(current_level: int, current_xp: int, xp_gained: int) -> tuple:
    """Retourne (nouveau_level, nouveau_xp_actuel, nombre_de_level_ups). Gère les montées
    multiples en une seule fois si le gain d'XP est important."""
    level = current_level
    xp = current_xp + xp_gained
    level_ups = 0
    while xp >= xp_required_for_level(level):
        xp -= xp_required_for_level(level)
        level += 1
        level_ups += 1
    return level, xp, level_ups


SCHEMA = """
CREATE TABLE IF NOT EXISTS tickets (
    id INTEGER PRIMARY KEY,
    channel_id INTEGER,
    user_id INTEGER,
    type TEXT,
    reason TEXT,
    status TEXT,
    created_at TEXT,
    transcript_path TEXT,
    ticket_uid TEXT UNIQUE,         -- identifiant permanent à 15 chiffres (survit à la suppression du salon)
    base_channel_name TEXT          -- nom d'origine du salon, figé à la création (nom stable des réouvertures)
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
    deletion_deadline TEXT,
    last_savings_trigger_at TEXT,       -- dernier déclenchement de l'épargne auto (fenêtre glissante 3h)
    failed_pin_attempts INTEGER DEFAULT 0,  -- tentatives de code erronées consécutives
    locked_until TEXT                   -- compte verrouillé jusqu'à cette date ISO (trop d'échecs)
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
    related_iban TEXT,
    category TEXT DEFAULT 'autre'        -- 'revenu' | 'remboursement' | 'depense' | 'transfert_sortant' | 'epargne' | 'autre'
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
    pv_actuel INTEGER DEFAULT 5000,
    pv_max INTEGER DEFAULT 5000,
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
    -- Ces colonnes ne sont plus utilisées : les Maîtrises EO/Sort/RCT sont désormais dérivées à la volée
    -- depuis character_stats (energie_occulte_pts, sorts_pts, rct_pts), exactement comme Force/Vitesse/
    -- Défense. Conservées pour compatibilité SQLite (pas de DROP COLUMN). mastery_territoire jamais créé
    -- (système Territoire différé). Seul rct_quest_available reste écrit (flag de quête RCT).
    mastery_eo_level INTEGER DEFAULT 1,
    mastery_sort_level INTEGER DEFAULT 1,
    mastery_rct_level INTEGER DEFAULT 1,
    rct_quest_available INTEGER DEFAULT 0,
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
    points_restants INTEGER DEFAULT 0,
    points_debt INTEGER DEFAULT 0
);

-- Techniques Occultes d'un personnage (/profil → ⚡ Technique). Jusqu'à 4 slots (slot_index 0..3).
-- TODO : reste VIDE par défaut — aucun moyen de la remplir depuis le bot pour l'instant. Le système
-- d'XP des techniques, les seuils de niveau/maîtrise pour la promotion en « Technique Maximum » et les
-- buffs de dégâts par niveau ne sont PAS encore définis (points non abordés).
CREATE TABLE IF NOT EXISTS character_sorts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    character_id INTEGER,
    slot_index INTEGER,       -- 0 à 3
    name TEXT,
    level INTEGER DEFAULT 1,
    xp_actuel INTEGER DEFAULT 0,
    xp_max INTEGER DEFAULT 100,
    color_r INTEGER, color_g INTEGER, color_b INTEGER,
    unlock_level INTEGER DEFAULT 1,       -- niveau requis indicatif (affiché au staff) ; NE déclenche AUCUN déblocage auto
    phase INTEGER DEFAULT 1,              -- 1 = progression normale, 2 = grinding post-max vers Technique Maximum
    max_level_threshold INTEGER,          -- niveau_requis le plus haut parmi ses sorts secondaires (fin de Phase 1)
    is_technique_maximum INTEGER DEFAULT 0,
    is_unlocked INTEGER DEFAULT 1         -- 1 = visible/actif ; 0 = verrouillé (sorts principaux 2/3/4 par défaut, déblocage manuel staff)
);

-- Sorts secondaires d'un sort principal (vue détaillée /profil → ⚡ Technique → 🔍). Jusqu'à 8 slots.
-- TODO : reste VIDE par défaut — aucun moyen de la remplir depuis le bot pour l'instant. L'attribution
-- d'un nom/classe à un slot, le coût EO et les dégâts par sort ne sont PAS encore définis (points non
-- abordés). Tous les slots s'affichent donc verrouillés/vides.
CREATE TABLE IF NOT EXISTS character_secondary_sorts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sort_id INTEGER,          -- référence character_sorts.id (le sort principal parent)
    slot_index INTEGER,       -- 0 à 7
    name TEXT,
    classe TEXT,              -- 'S', '1', '2', '3', '4', ou NULL si slot vide
    niveau_requis INTEGER,
    description TEXT,
    faiblesse TEXT,
    cout_pct INTEGER,         -- coût en % de la réserve d'EO (résolu depuis SPELL_CLASS_VALUES[classe])
    -- TODO : la conversion du coût en % vers un coût EO fixe (au premier usage réel de la technique en
    -- combat) n'est pas encore implémentée, aucun système de combat n'existe pour la déclencher.
    -- cout_eo_fixe et cout_converted_at restent NULL indéfiniment pour l'instant.
    cout_eo_fixe INTEGER DEFAULT NULL,
    cout_converted_at TEXT DEFAULT NULL,
    degats INTEGER DEFAULT NULL  -- dégâts de BASE tirés une seule fois à la création (dans la fourchette de la classe)
);

-- Territoire (Extension du Territoire) propre à un personnage (/profil → 🗺️ Territoire). Le flux de
-- création/staff n'est pas encore construit ; seule la lecture (pillow) est branchée pour l'instant.
CREATE TABLE IF NOT EXISTS character_territoire (
    character_id INTEGER PRIMARY KEY,
    name TEXT,
    appellation TEXT,      -- texte EXACT choisi : "Extension du Territoire" / "Ryōiki Tenkai" / "Domain Expansion"
    type TEXT,
    cout_eo_pct INTEGER,   -- coût en % de la réserve d'EO (valeur fixe, modifiable staff)
    duree_tours INTEGER,   -- durée d'effet en tours (valeur fixe, modifiable staff)
    description TEXT,
    effets TEXT,
    image_path TEXT,       -- image/GIF du territoire (envoyé lors de l'activation en chat)
    is_unlocked INTEGER DEFAULT 0  -- 0 = verrouillé staff, 1 = débloqué (accessible au joueur)
);

-- Armes maudites CRÉÉES par un personnage (/profil → 🗡️ Armes maudites). Les dégâts croissent avec la
-- Maîtrise Arme maudite (dérivée de la stat), via grant_arme_maudite_xp (cogs.profil).
CREATE TABLE IF NOT EXISTS character_armes_maudites (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    character_id INTEGER,
    name TEXT,
    classe TEXT,          -- '4', '3', '2', '1', 'S'
    description TEXT,
    image_path TEXT,
    degats_base INTEGER,  -- tiré une fois à la création, dans la fourchette de sa classe
    degats_actuel INTEGER,
    cout_eo_pct_override INTEGER DEFAULT NULL  -- coût EO % forcé par le staff ; NULL = dérivé de la classe
);

-- Plafonds de niveau des Maîtrises modifiables PAR PERSONNAGE (staff). Absence de ligne = plafond par
-- défaut (constante). mastery_key ∈ 'eo','sort','territoire','rct','arme'.
CREATE TABLE IF NOT EXISTS character_mastery_overrides (
    character_id INTEGER,
    mastery_key TEXT,
    max_level_override INTEGER,
    PRIMARY KEY (character_id, mastery_key)
);

-- Barème : points de stats accordés par rôle (camp / clan / grade).
CREATE TABLE IF NOT EXISTS role_point_values (
    role_id INTEGER PRIMARY KEY,
    category TEXT,       -- 'camp', 'clan', 'grade'
    points INTEGER
);

-- Rôle de référence + points actuellement accordés par catégorie pour chaque personnage.
CREATE TABLE IF NOT EXISTS character_role_point_grants (
    character_id INTEGER PRIMARY KEY,
    camp_role_id INTEGER, camp_points INTEGER DEFAULT 0,
    clan_role_id INTEGER, clan_points INTEGER DEFAULT 0,
    grade_role_id INTEGER, grade_points INTEGER DEFAULT 0
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
    security_lock INTEGER DEFAULT 0,-- 1 = compte verrouillé (trésorerie négative prolongée) : aucun débit
    negative_since TEXT,            -- date ISO de bascule dans le négatif (NULL si à l'équilibre)
    lock_grace_until TEXT,          -- fin des 2 mois de grâce si le chef choisit de garder le verrou
    warning_sent INTEGER DEFAULT 0, -- 1 = avertissement « 1 mois de négatif » déjà envoyé
    status TEXT DEFAULT 'active',    -- 'active' | 'pending_deletion' (période de grâce avant suppression définitive)
    deletion_reason TEXT,           -- raison de la suppression (rempli uniquement pendant la période de grâce)
    deleted_at TEXT,                -- date ISO de mise en attente de suppression (NULL sinon)
    restore_deadline TEXT,          -- date ISO limite de restauration = deleted_at + 15 jours (NULL sinon)
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS order_salaries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER,
    character_id INTEGER,
    montant INTEGER,
    effective_start_date TEXT,      -- date (YYYY-MM-DD) d'entrée en vigueur (prochain lundi)
    added_at TEXT,
    is_external INTEGER DEFAULT 0,  -- 1 = IBAN hors des membres de l'ordre (salaire temporaire)
    expiry_date TEXT                -- date ISO d'expiration d'un salaire externe (NULL pour un membre)
);

CREATE TABLE IF NOT EXISTS order_chief_bans (
    user_id INTEGER PRIMARY KEY,
    banned_until TEXT               -- interdiction de créer un ordre jusqu'à cette date ISO
);

CREATE TABLE IF NOT EXISTS order_bank_sessions (
    user_id INTEGER,
    order_id INTEGER,
    verified_at TEXT,
    PRIMARY KEY (user_id, order_id)
);

CREATE TABLE IF NOT EXISTS order_disciple_assignments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER,
    disciple_character_id INTEGER,
    educator_character_id INTEGER
);

CREATE TABLE IF NOT EXISTS order_members (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER,
    character_id INTEGER,
    role_label TEXT,                -- 'Sous-chef', 'Formateur', 'Chef d''équipe', 'Membre d''équipe', 'Corps administratif'
    joined_at TEXT                  -- date ISO d'entrée effective dans l'ordre (ancienneté -> indemnité graduée)
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

CREATE TABLE IF NOT EXISTS bot_state (
    key TEXT PRIMARY KEY,
    value TEXT                       -- ex: 'last_weekly_orders_run:<guild_id>' -> date ISO du dernier lundi traité
);

-- Source de vérité PERMANENTE de la réserve d'EO tirée à la validation de fiche. Sert à resynchroniser
-- l'EO du profil à chaque affichage (sync_eo_with_fiche), quel que soit l'âge du personnage / redémarrages.
CREATE TABLE IF NOT EXISTS fiche_record (
    character_id INTEGER PRIMARY KEY,
    eo_value INTEGER
);

-- Départs de joueurs mis EN RÉSERVE 15 jours avant purge définitive (au lieu d'une suppression immédiate).
-- awaiting_owner_decision = 1 gèle la purge (le joueur est revenu, l'owner doit trancher).
CREATE TABLE IF NOT EXISTS player_departures (
    user_id INTEGER PRIMARY KEY,
    guild_id INTEGER,
    departed_at TEXT,
    awaiting_owner_decision INTEGER DEFAULT 0,
    -- Référence au DERNIER DM de décision envoyé à l'owner (pour pouvoir le désactiver si un
    -- retour plus récent le remplace). Le DM vit dans le salon privé propre à cette conversation.
    last_decision_message_id INTEGER,
    last_decision_channel_id INTEGER
);

CREATE TABLE IF NOT EXISTS educator_contracts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id TEXT,                   -- regroupe les contrats créés en une seule fois (validation groupée)
    disciple_character_id INTEGER,
    educator_character_id INTEGER,
    source_order_id INTEGER,         -- ordre ÉDUCATIF d'origine (où l'éducateur exerce)
    employer_order_id INTEGER,       -- ordre Direct/Hybride EMPLOYEUR (où le disciple travaille)
    duree_type TEXT,                 -- 'determine' ou 'indetermine'
    duree_value INTEGER,
    duree_unit TEXT,                 -- 'jours' / 'semaines' / 'mois' / 'annees'
    pct INTEGER,                     -- % reversé à l'éducateur (10 à 40), appliqué UNIQUEMENT sur salaire_fixe
    salaire_fixe INTEGER,
    status TEXT DEFAULT 'pending',   -- 'pending' / 'active' / 'refused' / 'ended'
    start_date TEXT,
    end_date TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS appearance_reservations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    character_id INTEGER,             -- DÉCOUPLÉ : optionnel (NULL), la réservation précède le personnage
    user_id INTEGER,
    guild_id INTEGER,                 -- clé de référence (avec user_id + slot_number)
    slot_number INTEGER,             -- slot visé (1/2/3), saisi manuellement, sans personnage validé requis
    nom_original TEXT,
    univers TEXT,
    image_path TEXT,
    status TEXT DEFAULT 'pending',   -- 'pending', 'accepted', 'refused'
    refusal_reason TEXT,
    created_at TEXT
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
    ("last_savings_trigger_at", "TEXT"),
    ("failed_pin_attempts", "INTEGER DEFAULT 0"),
    ("locked_until", "TEXT"),
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


def _ensure_bank_transactions_columns(conn):
    """Ajoute la colonne de catégorisation à bank_transactions (DB existante)."""
    cols = _column_names(conn, "bank_transactions")
    if not cols:
        return
    if "category" not in cols:
        conn.execute("ALTER TABLE bank_transactions ADD COLUMN category TEXT DEFAULT 'autre'")


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
    # Système de salaires + verrou de sécurité (trésorerie négative prolongée).
    if "security_lock" not in cols:
        conn.execute("ALTER TABLE orders ADD COLUMN security_lock INTEGER DEFAULT 0")
    if "negative_since" not in cols:
        conn.execute("ALTER TABLE orders ADD COLUMN negative_since TEXT")
    if "lock_grace_until" not in cols:
        conn.execute("ALTER TABLE orders ADD COLUMN lock_grace_until TEXT")
    if "warning_sent" not in cols:
        conn.execute("ALTER TABLE orders ADD COLUMN warning_sent INTEGER DEFAULT 0")
    # Système de suppression avec période de grâce de 15 jours (soft delete puis suppression différée).
    if "status" not in cols:
        conn.execute("ALTER TABLE orders ADD COLUMN status TEXT DEFAULT 'active'")
    if "deletion_reason" not in cols:
        conn.execute("ALTER TABLE orders ADD COLUMN deletion_reason TEXT")
    if "deleted_at" not in cols:
        conn.execute("ALTER TABLE orders ADD COLUMN deleted_at TEXT")
    if "restore_deadline" not in cols:
        conn.execute("ALTER TABLE orders ADD COLUMN restore_deadline TEXT")


def _ensure_order_members_columns(conn):
    """Ajoute joined_at (ancienneté individuelle) à une table order_members préexistante, et
    rétro-remplit les lignes déjà créées avant cet ajout par la date de création de leur ordre
    (approximation raisonnable : on les suppose présents depuis la création de l'ordre)."""
    cols = _column_names(conn, "order_members")
    if not cols:
        return
    if "joined_at" not in cols:
        conn.execute("ALTER TABLE order_members ADD COLUMN joined_at TEXT")
    conn.execute(
        "UPDATE order_members SET joined_at = "
        "(SELECT created_at FROM orders WHERE orders.id = order_members.order_id) "
        "WHERE joined_at IS NULL")


def _ensure_character_profiles_columns(conn):
    """Ajoute les colonnes de maîtrise (EO / Sort / RCT + quête) à une table character_profiles
    préexistante. TODO : mastery_territoire non ajouté (système Territoire différé)."""
    cols = _column_names(conn, "character_profiles")
    if not cols:
        return
    for name, decl in (
        ("mastery_eo_level", "INTEGER DEFAULT 1"),
        ("mastery_sort_level", "INTEGER DEFAULT 1"),
        ("mastery_rct_level", "INTEGER DEFAULT 1"),
        ("rct_quest_available", "INTEGER DEFAULT 0"),
    ):
        if name not in cols:
            conn.execute(f"ALTER TABLE character_profiles ADD COLUMN {name} {decl}")


def _ensure_character_stats_columns(conn):
    """Ajoute points_debt (dette de points reprise sur les prochains gains) à une table
    character_stats préexistante."""
    cols = _column_names(conn, "character_stats")
    if not cols:
        return
    if "points_debt" not in cols:
        conn.execute("ALTER TABLE character_stats ADD COLUMN points_debt INTEGER DEFAULT 0")


def _ensure_character_sorts_columns(conn):
    """Ajoute les colonnes du système d'XP / progression 2 phases à une table character_sorts
    préexistante (déblocage, phase, seuil max, technique maximum)."""
    cols = _column_names(conn, "character_sorts")
    if not cols:
        return
    if "unlock_level" not in cols:
        conn.execute("ALTER TABLE character_sorts ADD COLUMN unlock_level INTEGER DEFAULT 1")
    if "phase" not in cols:
        conn.execute("ALTER TABLE character_sorts ADD COLUMN phase INTEGER DEFAULT 1")
    if "max_level_threshold" not in cols:
        conn.execute("ALTER TABLE character_sorts ADD COLUMN max_level_threshold INTEGER")
    if "is_technique_maximum" not in cols:
        conn.execute("ALTER TABLE character_sorts ADD COLUMN is_technique_maximum INTEGER DEFAULT 0")
    if "is_unlocked" not in cols:
        conn.execute("ALTER TABLE character_sorts ADD COLUMN is_unlocked INTEGER DEFAULT 1")


def _ensure_character_secondary_sorts_columns(conn):
    """Ajoute les colonnes du flux de création guidée des techniques (description, faiblesse, coût EO)
    à une table character_secondary_sorts préexistante."""
    cols = _column_names(conn, "character_secondary_sorts")
    if not cols:
        return
    if "description" not in cols:
        conn.execute("ALTER TABLE character_secondary_sorts ADD COLUMN description TEXT")
    if "faiblesse" not in cols:
        conn.execute("ALTER TABLE character_secondary_sorts ADD COLUMN faiblesse TEXT")
    if "cout_pct" not in cols:
        conn.execute("ALTER TABLE character_secondary_sorts ADD COLUMN cout_pct INTEGER")
    if "cout_eo_fixe" not in cols:
        conn.execute("ALTER TABLE character_secondary_sorts ADD COLUMN cout_eo_fixe INTEGER DEFAULT NULL")
    if "cout_converted_at" not in cols:
        conn.execute("ALTER TABLE character_secondary_sorts ADD COLUMN cout_converted_at TEXT DEFAULT NULL")
    if "degats" not in cols:
        conn.execute("ALTER TABLE character_secondary_sorts ADD COLUMN degats INTEGER DEFAULT NULL")


# Barème EXACT des points de stats par rôle (camp / clan / grade). INSERT OR REPLACE : relançable.
ROLE_POINT_VALUES_SEED = [
    # Camp
    (1521961288618479829, "camp", 15),    # Exorciste
    (1521961393614749707, "camp", 25),    # Hybride
    (1521961499730645153, "camp", 10),    # Humain
    # Clan (tous à 250, sauf Sans clan à 125)
    (1521961746615504926, "clan", 250),   # Ryomen
    (1521961744908550166, "clan", 250),   # Kashimo
    (1521961753141841921, "clan", 250),   # Geto
    (1521961741141934101, "clan", 250),   # Gojo
    (1521961746196070400, "clan", 250),   # Inumaki
    (1521961748838613143, "clan", 250),   # Kamo
    (1521961743729819799, "clan", 250),   # Zenin
    (1539169032324907048, "clan", 125),   # Sans clan
    # Grade
    (1521963027925172344, "grade", 250),  # Chef du clan
    (1521963035898548455, "grade", 220),  # Héritier
    (1521963034434601040, "grade", 190),  # Bras droit
    (1521963034736726158, "grade", 190),  # Bras gauche
    (1521963040155766835, "grade", 130),  # Bras droit héritier
    (1521963040809943120, "grade", 130),  # Bras gauche héritier
    (1521963104903233658, "grade", 55),   # Membre principal
    (1521963107918807140, "grade", 30),   # Membre secondaire
]


def _seed_role_point_values(conn):
    """Peuple role_point_values avec le barème exact (idempotent : INSERT OR REPLACE)."""
    conn.executemany(
        "INSERT OR REPLACE INTO role_point_values (role_id, category, points) VALUES (?, ?, ?)",
        ROLE_POINT_VALUES_SEED,
    )


def _ensure_character_armes_maudites_columns(conn):
    """Ajoute cout_eo_pct_override (coût EO % forcé par le staff) à une table préexistante."""
    cols = _column_names(conn, "character_armes_maudites")
    if not cols:
        return
    if "cout_eo_pct_override" not in cols:
        conn.execute("ALTER TABLE character_armes_maudites ADD COLUMN cout_eo_pct_override INTEGER DEFAULT NULL")


def _ensure_character_territoire_columns(conn):
    """Ajoute les colonnes du système Territoire complet (appellation, image, verrouillage) à une table
    character_territoire préexistante (créée à l'origine sans ces colonnes)."""
    cols = _column_names(conn, "character_territoire")
    if not cols:
        return
    if "appellation" not in cols:
        conn.execute("ALTER TABLE character_territoire ADD COLUMN appellation TEXT")
    if "image_path" not in cols:
        conn.execute("ALTER TABLE character_territoire ADD COLUMN image_path TEXT")
    if "is_unlocked" not in cols:
        conn.execute("ALTER TABLE character_territoire ADD COLUMN is_unlocked INTEGER DEFAULT 0")


def _ensure_appearance_reservations_columns(conn):
    """Ajoute guild_id / slot_number à une table appearance_reservations préexistante (créée à l'origine
    sans ces colonnes). Découple le système de /réserv-appa des personnages validés : la réservation peut
    désormais exister AVANT le personnage (character_id reste facultatif)."""
    cols = _column_names(conn, "appearance_reservations")
    if not cols:
        return
    if "guild_id" not in cols:
        conn.execute("ALTER TABLE appearance_reservations ADD COLUMN guild_id INTEGER")
    if "slot_number" not in cols:
        conn.execute("ALTER TABLE appearance_reservations ADD COLUMN slot_number INTEGER")


def _ensure_player_departures_columns(conn):
    """Ajoute les colonnes de référence au dernier DM de décision (message + salon) à une table
    player_departures préexistante (créée à l'origine sans ces colonnes)."""
    cols = _column_names(conn, "player_departures")
    if not cols:
        return
    if "last_decision_message_id" not in cols:
        conn.execute("ALTER TABLE player_departures ADD COLUMN last_decision_message_id INTEGER")
    if "last_decision_channel_id" not in cols:
        conn.execute("ALTER TABLE player_departures ADD COLUMN last_decision_channel_id INTEGER")


def _ensure_salary_columns(conn):
    """Ajoute is_external / expiry_date à une table order_salaries préexistante (salaires temporaires
    pour un IBAN externe à l'ordre)."""
    cols = _column_names(conn, "order_salaries")
    if not cols:
        return
    if "is_external" not in cols:
        conn.execute("ALTER TABLE order_salaries ADD COLUMN is_external INTEGER DEFAULT 0")
    if "expiry_date" not in cols:
        conn.execute("ALTER TABLE order_salaries ADD COLUMN expiry_date TEXT")


def _ensure_tickets_columns(conn):
    """Ajoute ticket_uid à une table tickets préexistante. SQLite interdit d'ajouter une colonne UNIQUE
    via ALTER : l'unicité est assurée par un index unique créé juste après (les NULL multiples restent
    autorisés jusqu'au rattrapage rétroactif)."""
    cols = _column_names(conn, "tickets")
    if not cols:
        return
    if "ticket_uid" not in cols:
        conn.execute("ALTER TABLE tickets ADD COLUMN ticket_uid TEXT")
    if "base_channel_name" not in cols:
        conn.execute("ALTER TABLE tickets ADD COLUMN base_channel_name TEXT")


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
        _ensure_bank_transactions_columns(conn)
        _ensure_order_columns(conn)
        _ensure_order_members_columns(conn)
        _ensure_character_stats_columns(conn)
        _ensure_character_profiles_columns(conn)
        _ensure_character_sorts_columns(conn)
        _ensure_character_secondary_sorts_columns(conn)
        _ensure_character_territoire_columns(conn)
        _ensure_character_armes_maudites_columns(conn)
        _seed_role_point_values(conn)
        _ensure_appearance_reservations_columns(conn)
        _ensure_player_departures_columns(conn)
        _ensure_salary_columns(conn)
        _ensure_tickets_columns(conn)
        # Unicité de l'IBAN d'ordre (fonctionne aussi sur une base migrée ; NULL multiples autorisés).
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_orders_iban ON orders(iban)")
        # Unicité du ticket_uid (fonctionne aussi sur une base migrée ; NULL multiples autorisés).
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_tickets_uid ON tickets(ticket_uid)")
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


def insert_ticket(ticket_id, channel_id, user_id, ticket_type, reason, status, created_at,
                  ticket_uid=None, base_channel_name=None):
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO tickets (id, channel_id, user_id, type, reason, status, created_at,
                                    transcript_path, ticket_uid, base_channel_name)
               VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)""",
            (ticket_id, channel_id, user_id, ticket_type, reason, status, created_at,
             ticket_uid, base_channel_name),
        )


def ticket_uid_exists(ticket_uid: str) -> bool:
    """Vrai si un ticket porte déjà cet identifiant permanent (unicité à la génération)."""
    with get_connection() as conn:
        return conn.execute(
            "SELECT 1 FROM tickets WHERE ticket_uid = ?", (ticket_uid,)
        ).fetchone() is not None


def get_ticket_by_uid(ticket_uid: str):
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM tickets WHERE ticket_uid = ?", (ticket_uid,)
        ).fetchone()


def get_ticket_ids_without_uid():
    """ids des tickets créés avant l'ajout de ticket_uid (pour le rattrapage rétroactif)."""
    with get_connection() as conn:
        rows = conn.execute("SELECT id FROM tickets WHERE ticket_uid IS NULL").fetchall()
    return [r["id"] for r in rows]


def set_ticket_uid(ticket_id: int, ticket_uid: str):
    with get_connection() as conn:
        conn.execute(
            "UPDATE tickets SET ticket_uid = ? WHERE id = ?", (ticket_uid, ticket_id)
        )


def reopen_ticket(ticket_uid: str, new_channel_id: int):
    """Rouvre un ticket fermé : statut 'open' + nouveau salon, en conservant le MÊME ticket_uid."""
    with get_connection() as conn:
        conn.execute(
            "UPDATE tickets SET status = 'open', channel_id = ? WHERE ticket_uid = ?",
            (new_channel_id, ticket_uid),
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


def remove_virtual_role(character_id: int, role_id: int) -> bool:
    """Retire un rôle virtuel d'un personnage (slot 2/3). Retourne True si une ligne a été supprimée."""
    with get_connection() as conn:
        cur = conn.execute(
            "DELETE FROM character_virtual_roles WHERE character_id = ? AND role_id = ?",
            (character_id, role_id),
        )
        return cur.rowcount > 0


def get_validated_character_id(user_id: int, guild_id: int, slot_number: int):
    """id du personnage validé pour ce (joueur, serveur, slot), ou None s'il n'est pas encore validé."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id FROM validated_characters WHERE user_id = ? AND guild_id = ? AND slot_number = ?",
            (user_id, guild_id, slot_number),
        ).fetchone()
    return row["id"] if row else None


# ---------- Barème : points de stats liés aux rôles ----------
_ROLE_POINT_CATEGORIES = ("camp", "clan", "grade")


def get_role_point_value(role_id: int):
    """Points accordés par un rôle selon le barème, ou None si le rôle n'y figure pas."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT points FROM role_point_values WHERE role_id = ?", (role_id,)
        ).fetchone()
    return row["points"] if row else None


def get_role_point_category(role_id: int):
    """Catégorie ('camp'/'clan'/'grade') d'un rôle du barème, ou None s'il n'y figure pas."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT category FROM role_point_values WHERE role_id = ?", (role_id,)
        ).fetchone()
    return row["category"] if row else None


def get_role_point_grants(character_id: int):
    """Ligne character_role_point_grants du personnage (ou None)."""
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM character_role_point_grants WHERE character_id = ?", (character_id,)
        ).fetchone()


def sync_role_points(character_id: int, category: str, new_role_id: int = None):
    """Recalcule les points d'UNE catégorie (camp/clan/grade) pour un personnage, applique le delta
    (gain, ou reprise avec dette si les points étaient déjà répartis), et mémorise le nouveau rôle de
    référence.

    - new_role_id présent dans le barème : new_points = sa valeur, rôle de référence = new_role_id.
    - new_role_id == None : RETRAIT complet des points de la catégorie (new_points = 0, sans lookup),
      rôle de référence remis à NULL — même mécanique de dette si les points étaient déjà répartis.
    - new_role_id NON None mais absent du barème : la fonction ne fait STRICTEMENT rien (le delta doit
      toujours reposer sur des valeurs connues du barème). Tout en une transaction."""
    if category not in _ROLE_POINT_CATEGORIES:
        return 0
    remaining_debt = 0  # dette NOUVELLEMENT ajoutée à points_debt (pour notifier le joueur en amont)
    with get_connection() as conn:
        if new_role_id is None:
            new_points = 0  # retrait sans remplacement : aucun lookup, la référence passera à NULL
        else:
            npr = conn.execute(
                "SELECT points FROM role_point_values WHERE role_id = ?", (new_role_id,)
            ).fetchone()
            if npr is None:
                return  # rôle hors barème : on ne touche à rien
            new_points = npr["points"]

        grant = conn.execute(
            f"SELECT {category}_points AS pts FROM character_role_point_grants WHERE character_id = ?",
            (character_id,),
        ).fetchone()
        old_points = (grant["pts"] if grant and grant["pts"] is not None else 0)
        delta = new_points - old_points

        # Garantit l'existence de la ligne de stats.
        conn.execute("INSERT OR IGNORE INTO character_stats (character_id) VALUES (?)", (character_id,))

        if delta > 0:
            row = conn.execute(
                "SELECT points_debt FROM character_stats WHERE character_id = ?", (character_id,)
            ).fetchone()
            debt = (row["points_debt"] if row and row["points_debt"] else 0)
            offset = min(debt, delta)          # une dette existante ampute d'abord le gain
            debt -= offset
            gain = delta - offset
            conn.execute(
                "UPDATE character_stats SET points_restants = points_restants + ?, points_debt = ? "
                "WHERE character_id = ?",
                (gain, debt, character_id),
            )
        elif delta < 0:
            reclaim = -delta
            row = conn.execute(
                "SELECT points_restants FROM character_stats WHERE character_id = ?", (character_id,)
            ).fetchone()
            current = (row["points_restants"] if row and row["points_restants"] else 0)
            if current >= reclaim:
                conn.execute(
                    "UPDATE character_stats SET points_restants = points_restants - ? WHERE character_id = ?",
                    (reclaim, character_id),
                )
            else:
                # Pas assez de points libres : le reste devient une dette reprise sur les prochains gains.
                remaining_debt = reclaim - current
                conn.execute(
                    "UPDATE character_stats SET points_restants = 0, points_debt = points_debt + ? "
                    "WHERE character_id = ?",
                    (remaining_debt, character_id),
                )
                # (remaining_debt renvoyé en fin de fonction pour permettre une notification MP au joueur)

        # Mémorise le nouveau rôle de référence + ses points, SANS toucher aux 2 autres catégories.
        conn.execute(
            "INSERT OR IGNORE INTO character_role_point_grants (character_id) VALUES (?)", (character_id,)
        )
        conn.execute(
            f"UPDATE character_role_point_grants SET {category}_role_id = ?, {category}_points = ? "
            "WHERE character_id = ?",
            (new_role_id, new_points, character_id),
        )
    return remaining_debt


def characters_without_role_point_grant():
    """ids des personnages validés sans ligne dans character_role_point_grants (pour le rattrapage)."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id FROM validated_characters WHERE id NOT IN "
            "(SELECT character_id FROM character_role_point_grants)"
        ).fetchall()
    return [r["id"] for r in rows]


# ---------- Profils de personnage (/profil) ----------
# Colonnes modifiables de character_profiles (character_id exclu : c'est la clé).
_PROFILE_COLUMNS = frozenset({
    "pv_actuel", "pv_max", "eo_actuel", "eo_max", "level", "xp_actuel", "xp_max",
    "force_level", "force_xp_actuel", "force_xp_max",
    "vitesse_level", "vitesse_xp_actuel", "vitesse_xp_max",
    "defense_level", "defense_xp_actuel", "defense_xp_max",
    "maitrise_eo_level", "victoires", "defaites", "nuls",
    # mastery_*_level ne sont plus modifiables (maîtrises dérivées des points de stats). Seul
    # rct_quest_available reste écrit (flag de quête RCT, via get_mastery_rct).
    "rct_quest_available",
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
    - PV de départ : 5000/5000 (base commune ; +500 PV par montée de niveau via grant_character_xp).
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
               ) VALUES (?, 5000, 5000, ?, ?, 1, 0, 1000,
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


def get_character_sorts(character_id: int):
    """Techniques Occultes d'un personnage (/profil → ⚡ Technique), triées par slot_index (0..3).
    Retourne une liste de lignes (name, level, xp_actuel, xp_max, color_r/g/b). Vide par défaut :
    aucun moyen de la remplir depuis le bot pour l'instant (système d'XP des techniques non défini)."""
    with get_connection() as conn:
        return conn.execute(
            "SELECT id, slot_index, name, level, xp_actuel, xp_max, color_r, color_g, color_b, "
            "unlock_level, phase, max_level_threshold, is_technique_maximum, is_unlocked "
            "FROM character_sorts WHERE character_id = ? ORDER BY slot_index",
            (character_id,),
        ).fetchall()


def count_character_sorts(character_id: int) -> int:
    """Nombre de sorts PRINCIPAUX déjà créés pour ce personnage. 0 => le flux de création guidée doit
    se déclencher à l'entrée du bouton ⚡ Technique ; > 0 => on affiche directement le pillow."""
    with get_connection() as conn:
        return conn.execute(
            "SELECT COUNT(*) AS n FROM character_sorts WHERE character_id = ?", (character_id,)
        ).fetchone()["n"]


def get_territoire(character_id: int):
    """Territoire (/profil → 🗺️ Territoire) d'un personnage, ou None si aucune ligne n'existe encore."""
    with get_connection() as conn:
        return conn.execute(
            "SELECT character_id, name, appellation, type, cout_eo_pct, duree_tours, description, "
            "effets, image_path, is_unlocked "
            "FROM character_territoire WHERE character_id = ?",
            (character_id,),
        ).fetchone()


def unlock_territoire(character_id: int):
    """Débloque le Territoire (staff). Met is_unlocked=1 ; crée une ligne « coquille vide » (name NULL)
    si aucune n'existe encore — le joueur passera alors par le questionnaire de création au prochain clic."""
    with get_connection() as conn:
        cur = conn.execute(
            "UPDATE character_territoire SET is_unlocked = 1 WHERE character_id = ?", (character_id,)
        )
        if cur.rowcount == 0:
            conn.execute(
                "INSERT INTO character_territoire (character_id, is_unlocked) VALUES (?, 1)",
                (character_id,),
            )


def lock_territoire(character_id: int):
    """Reverrouille le Territoire (staff). Met is_unlocked=0 sans rien supprimer d'autre : toutes les
    données déjà créées restent intactes, juste inaccessibles au joueur."""
    with get_connection() as conn:
        conn.execute(
            "UPDATE character_territoire SET is_unlocked = 0 WHERE character_id = ?", (character_id,)
        )


def save_territoire(character_id, name, appellation, type_, description, effets, image_path,
                    cout_eo_pct, duree_tours):
    """Enregistre les valeurs saisies au questionnaire de création. La ligne existe déjà (coquille créée
    au déblocage) ; on met à jour ses champs. cout_eo_pct / duree_tours reçoivent une valeur de base fixe
    à la création (60 % / 3 tours), ensuite modifiable par le staff. UPSERT par sécurité si la ligne
    avait disparu."""
    with get_connection() as conn:
        cur = conn.execute(
            "UPDATE character_territoire SET name = ?, appellation = ?, type = ?, description = ?, "
            "effets = ?, image_path = ?, cout_eo_pct = ?, duree_tours = ?, is_unlocked = 1 "
            "WHERE character_id = ?",
            (name, appellation, type_, description, effets, image_path, cout_eo_pct, duree_tours,
             character_id),
        )
        if cur.rowcount == 0:
            conn.execute(
                "INSERT INTO character_territoire (character_id, name, appellation, type, description, "
                "effets, image_path, cout_eo_pct, duree_tours, is_unlocked) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)",
                (character_id, name, appellation, type_, description, effets, image_path,
                 cout_eo_pct, duree_tours),
            )


def find_territoires_by_appellation(user_id: int, guild_id: int, appellation: str):
    """Tous les territoires CRÉÉS et DÉBLOQUÉS d'un joueur portant cette appellation exacte, avec le
    numéro de slot du personnage (pour lever l'ambiguïté en cas de plusieurs correspondances)."""
    with get_connection() as conn:
        return conn.execute(
            "SELECT ct.character_id, vc.slot_number, ct.name, ct.appellation, ct.image_path "
            "FROM character_territoire ct "
            "JOIN validated_characters vc ON vc.id = ct.character_id "
            "WHERE vc.user_id = ? AND vc.guild_id = ? AND ct.appellation = ? "
            "AND ct.is_unlocked = 1 AND ct.name IS NOT NULL",
            (user_id, guild_id, appellation),
        ).fetchall()


def get_character_armes(character_id: int):
    """Armes maudites créées par un personnage (triées par id de création). Vide par défaut : aucun flux
    de création côté joueur n'existe encore (comme les techniques à leurs débuts)."""
    with get_connection() as conn:
        return conn.execute(
            "SELECT id, name, classe, description, image_path, degats_base, degats_actuel, "
            "cout_eo_pct_override FROM character_armes_maudites WHERE character_id = ? ORDER BY id",
            (character_id,),
        ).fetchall()


def add_arme_degats(arme_id: int, bonus: int):
    """Ajoute `bonus` aux dégâts actuels d'une arme précise (croissance liée à la Maîtrise Arme maudite)."""
    with get_connection() as conn:
        conn.execute(
            "UPDATE character_armes_maudites SET degats_actuel = degats_actuel + ? WHERE id = ?",
            (bonus, arme_id),
        )


def get_arme_category_id():
    """id de la catégorie de boutique nommée EXACTEMENT « Arme maudite », ou None si elle n'existe pas
    encore (elle doit être créée manuellement via /shop). Sert à filtrer l'inventaire du joueur."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id FROM shop_categories WHERE name = 'Arme maudite'"
        ).fetchone()
    return row["id"] if row else None


def get_inventory_armes(character_id: int, categorie_id: int):
    """Armes maudites POSSÉDÉES dans l'inventaire (catégorie « Arme maudite », quantité > 0), avec leur
    classe issue de la définition d'objet. Sert de source pour la création d'une arme."""
    with get_connection() as conn:
        return conn.execute(
            "SELECT ci.item_id, ci.quantity, item.name, item.classe "
            "FROM character_inventory ci "
            "JOIN item_definitions item ON item.id = ci.item_id "
            "WHERE ci.character_id = ? AND item.categorie_id = ? AND ci.quantity > 0 "
            "ORDER BY item.name",
            (character_id, categorie_id),
        ).fetchall()


def count_character_armes(character_id: int, classe: str = None) -> int:
    """Nombre d'armes maudites déjà CRÉÉES par ce personnage, éventuellement filtré par classe."""
    with get_connection() as conn:
        if classe is None:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM character_armes_maudites WHERE character_id = ?",
                (character_id,),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM character_armes_maudites WHERE character_id = ? AND classe = ?",
                (character_id, classe),
            ).fetchone()
    return row["n"]


def create_arme_maudite(character_id, name, classe, description, image_path, degats_base):
    """Crée une arme maudite. degats_actuel démarre à degats_base (tiré une fois à la création)."""
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO character_armes_maudites (character_id, name, classe, description, image_path, "
            "degats_base, degats_actuel) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (character_id, name, classe, description, image_path, degats_base, degats_base),
        )
        return cur.lastrowid


def get_arme(arme_id: int):
    """Une arme maudite précise par son id, ou None."""
    with get_connection() as conn:
        return conn.execute(
            "SELECT id, character_id, name, classe, description, image_path, degats_base, degats_actuel, "
            "cout_eo_pct_override FROM character_armes_maudites WHERE id = ?",
            (arme_id,),
        ).fetchone()


def get_mastery_override(character_id: int, mastery_key: str):
    """Plafond de niveau forcé (staff) pour une Maîtrise d'un personnage, ou None si aucun override."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT max_level_override FROM character_mastery_overrides "
            "WHERE character_id = ? AND mastery_key = ?",
            (character_id, mastery_key),
        ).fetchone()
    return row["max_level_override"] if row else None


def set_mastery_override(character_id: int, mastery_key: str, max_level_override: int):
    """Fixe (ou remplace) le plafond de niveau d'une Maîtrise pour un personnage."""
    with get_connection() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO character_mastery_overrides "
            "(character_id, mastery_key, max_level_override) VALUES (?, ?, ?)",
            (character_id, mastery_key, max_level_override),
        )


def get_character_sort(sort_id: int):
    """Un sort PRINCIPAL par son id (character_sorts.id), ou None. Sert à la vue détaillée : on vérifie
    aussi via character_id que le sort appartient bien au personnage du panneau."""
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM character_sorts WHERE id = ?", (sort_id,)
        ).fetchone()


def insert_principal_sort(character_id: int, slot_index: int, name: str,
                          color, level: int = 1, xp_actuel: int = 0,
                          xp_max: int = xp_required_for_level(1), unlock_level: int = 1,
                          max_level_threshold=None, is_unlocked: int = 1) -> int:
    """Crée un sort PRINCIPAL et retourne son id. color = (r, g, b). xp_max par défaut = xp requis pour
    le niveau 1 (formule exponentielle), cohérent dès la création. unlock_level = niveau requis INDICATIF
    (affiché au staff, aucun déblocage auto) ; max_level_threshold = seuil de fin de Phase 1 (plus haut
    niveau_requis de ses secondaires) ; is_unlocked = 1 (visible/actif) ou 0 (verrouillé). phase (=1) et
    is_technique_maximum (=0) prennent leurs défauts de schéma."""
    r, g, b = color
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO character_sorts (character_id, slot_index, name, level, xp_actuel, xp_max, "
            "color_r, color_g, color_b, unlock_level, max_level_threshold, is_unlocked) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (character_id, slot_index, name, level, xp_actuel, xp_max, r, g, b,
             unlock_level, max_level_threshold, is_unlocked),
        )
        return cur.lastrowid


def insert_secondary_sort(sort_id: int, slot_index: int, name: str, classe: str, cout_pct,
                          description: str, faiblesse: str, degats=None, niveau_requis: int = 1):
    """Crée un sort SECONDAIRE rattaché au sort principal sort_id. cout_pct est résolu par l'appelant
    depuis SPELL_CLASS_VALUES[classe] ; degats est tiré à la création dans la fourchette de la classe.
    cout_eo_fixe / cout_converted_at restent NULL (non convertis)."""
    # niveau_requis est désormais calculé à la création (répartition automatique des seuils, cf.
    # _run_technique_creation). Il gate le déblocage d'un sort secondaire par le niveau PROPRE du sort
    # principal parent. TODO : le déblocage du Sort Principal SUIVANT (le rendre réellement utilisable)
    # reste une décision staff à définir — pour l'instant tous les principaux créés restent visibles.
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO character_secondary_sorts (sort_id, slot_index, name, classe, niveau_requis, "
            "description, faiblesse, cout_pct, cout_eo_fixe, cout_converted_at, degats) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?)",
            (sort_id, slot_index, name, classe, niveau_requis, description, faiblesse, cout_pct, degats),
        )


async def grant_character_xp(character_id: int, xp_gained: int) -> int:
    """Accorde de l'XP au NIVEAU GLOBAL du personnage. Chaque montée de niveau octroie +250 points de
    stats à répartir ET +500 PV (max ET actuel, du même montant : pas de soin complet, juste le nouveau
    palier de vie ajouté tel quel). Retourne le nombre de montées de niveau.
    # Aucune source d'XP n'existe encore dans le bot (combat, quêtes...). Cette fonction est prête à
    # être appelée dès qu'un système de gain d'XP sera construit."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT level, xp_actuel FROM character_profiles WHERE character_id = ?", (character_id,)
        ).fetchone()
        if row is None:
            return 0
        new_level, new_xp, level_ups = apply_xp_gain(row["level"], row["xp_actuel"], xp_gained)
        if level_ups > 0:
            conn.execute("INSERT OR IGNORE INTO character_stats (character_id) VALUES (?)", (character_id,))
            conn.execute(
                "UPDATE character_stats SET points_restants = points_restants + ? WHERE character_id = ?",
                (250 * level_ups, character_id),
            )
            # +500 PV par niveau, sur le max ET l'actuel (le nouveau palier s'ajoute à la vie courante).
            conn.execute(
                "UPDATE character_profiles SET pv_max = pv_max + ?, pv_actuel = pv_actuel + ? "
                "WHERE character_id = ?",
                (500 * level_ups, 500 * level_ups, character_id),
            )
        conn.execute(
            "UPDATE character_profiles SET level = ?, xp_actuel = ?, xp_max = ? WHERE character_id = ?",
            (new_level, new_xp, xp_required_for_level(new_level), character_id),
        )
    return level_ups


async def grant_sort_xp(sort_id: int, xp_gained: int) -> int:
    """Accorde de l'XP à un SORT PRINCIPAL, en gérant les 2 phases de progression. Chaque montée de
    niveau octroie +55 dégâts aux sorts secondaires déjà débloqués (déblocage basé sur le niveau PROPRE
    du sort principal). Retourne le nombre de montées de niveau.
    # Aucune source d'XP n'existe encore dans le bot (combat, quêtes...). Cette fonction est prête à
    # être appelée dès qu'un système de gain d'XP sera construit."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT phase, level, xp_actuel, max_level_threshold, character_id "
            "FROM character_sorts WHERE id = ?", (sort_id,)
        ).fetchone()
        if row is None:
            return 0
        phase = row["phase"]
        threshold = row["max_level_threshold"]

        def boost_unlocked(reference_level, mult):
            # +55 * mult aux secondaires débloqués (niveau_requis <= niveau du sort principal). Le garde
            # « degats IS NOT NULL » évite de transformer une valeur NULL héritée en NULL (NULL + x = NULL).
            conn.execute(
                "UPDATE character_secondary_sorts SET degats = degats + ? "
                "WHERE sort_id = ? AND niveau_requis <= ? AND degats IS NOT NULL",
                (55 * mult, sort_id, reference_level),
            )

        if phase == 1:
            new_level, new_xp, level_ups = apply_xp_gain(row["level"], row["xp_actuel"], xp_gained)
            if level_ups > 0:
                boost_unlocked(new_level, level_ups)
            if threshold is not None and new_level >= threshold:
                # Transition vers la Phase 2 : reset complet, on repart sur la nouvelle échelle.
                conn.execute(
                    "UPDATE character_sorts SET phase = 2, level = 0, xp_actuel = 0, xp_max = ? WHERE id = ?",
                    (PHASE2_XP_PER_LEVEL, sort_id),
                )
            else:
                conn.execute(
                    "UPDATE character_sorts SET level = ?, xp_actuel = ?, xp_max = ? WHERE id = ?",
                    (new_level, new_xp, xp_required_for_level(new_level), sort_id),
                )
            return level_ups

        # phase == 2 : formule temporaire plate (PHASE2_XP_PER_LEVEL par niveau), plafonnée à 100.
        total = row["xp_actuel"] + xp_gained
        reste_xp = total % PHASE2_XP_PER_LEVEL
        new_level = min(100, row["level"] + total // PHASE2_XP_PER_LEVEL)
        level_ups = new_level - row["level"]
        if level_ups > 0:
            # En Phase 2 tous les secondaires sont normalement débloqués : on référence le seuil de fin
            # de Phase 1 (tous <= threshold) pour appliquer le boost par sécurité.
            boost_unlocked(threshold if threshold is not None else 10 ** 9, level_ups)
        if new_level >= 100:
            conn.execute(
                "UPDATE character_sorts SET level = 100, xp_actuel = ?, xp_max = ?, "
                "is_technique_maximum = 1 WHERE id = ?",
                (PHASE2_XP_PER_LEVEL, PHASE2_XP_PER_LEVEL, sort_id),
            )
        else:
            conn.execute(
                "UPDATE character_sorts SET level = ?, xp_actuel = ? WHERE id = ?",
                (new_level, reste_xp, sort_id),
            )
        return level_ups


async def revoke_sort_xp(sort_id: int, xp_removed: int) -> int:
    """Retire de l'XP à un SORT PRINCIPAL en recalculant level/xp_actuel en sens inverse (jamais sous le
    niveau plancher — 1 en phase 1, 0 en phase 2 — ni sous xp_actuel=0). Retourne le nouveau niveau.
    # Ne retire PAS rétroactivement les +55 dégâts déjà accordés aux sorts secondaires lors des niveaux
    # précédemment gagnés — seul le niveau/XP du sort principal est corrigé, une correction plus fine
    # serait trop fragile à tracer."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT phase, level, xp_actuel FROM character_sorts WHERE id = ?", (sort_id,)
        ).fetchone()
        if row is None:
            return 0
        phase, level, xp = row["phase"], row["level"], row["xp_actuel"]
        xp -= xp_removed
        if phase == 1:
            while xp < 0 and level > 1:
                level -= 1
                xp += xp_required_for_level(level)
            level = max(1, level)
            xp = max(0, xp)
            xp_max = xp_required_for_level(level)
        else:  # phase 2 : paliers plats
            while xp < 0 and level > 0:
                level -= 1
                xp += PHASE2_XP_PER_LEVEL
            level = max(0, level)
            xp = max(0, xp)
            xp_max = PHASE2_XP_PER_LEVEL
        conn.execute(
            "UPDATE character_sorts SET level = ?, xp_actuel = ?, xp_max = ? WHERE id = ?",
            (level, xp, xp_max, sort_id),
        )
    return level


def get_secondary_sorts(sort_id: int):
    """Sorts SECONDAIRES d'un sort principal (character_secondary_sorts), triés par slot_index (0..7).
    Vide par défaut : aucun moyen de la remplir depuis le bot pour l'instant (règles non définies)."""
    with get_connection() as conn:
        return conn.execute(
            "SELECT id, slot_index, name, classe, niveau_requis, description, faiblesse, cout_pct, "
            "cout_eo_fixe, cout_converted_at, degats "
            "FROM character_secondary_sorts WHERE sort_id = ? ORDER BY slot_index",
            (sort_id,),
        ).fetchall()


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


# ---------- Suppression d'ordre avec période de grâce (soft delete) ----------
def set_order_pending_deletion(order_id: int, reason: str, deleted_at: str, restore_deadline: str):
    """Place un ordre en attente de suppression : rien n'est purgé, tout reste intact en base. L'ordre
    reste bloquant pour la règle « un seul ordre par personnage » tant qu'il n'est pas restauré ou
    définitivement supprimé (get_order_by_chief le renvoie toujours)."""
    with get_connection() as conn:
        conn.execute(
            "UPDATE orders SET status = 'pending_deletion', deletion_reason = ?, deleted_at = ?, "
            "restore_deadline = ? WHERE id = ?",
            (reason, deleted_at, restore_deadline, order_id),
        )


def restore_order(order_id: int) -> int:
    """Annule ATOMIQUEMENT une suppression en attente : la condition `status = 'pending_deletion'` est
    dans l'UPDATE lui-même, donc une seule restauration peut réussir même si deux clics quasi simultanés
    arrivent. Retourne le nombre de lignes affectées (1 = c'est CE clic qui a restauré, 0 = déjà fait /
    plus en attente). Tout redevient comme avant (aucune donnée n'ayant été touchée pendant la grâce)."""
    with get_connection() as conn:
        cur = conn.execute(
            "UPDATE orders SET status = 'active', deletion_reason = NULL, deleted_at = NULL, "
            "restore_deadline = NULL WHERE id = ? AND status = 'pending_deletion'",
            (order_id,),
        )
        return cur.rowcount


def get_orders_pending_deletion_due(guild_id: int, now_iso: str):
    """Ordres du serveur en attente de suppression dont le délai de restauration est écoulé (à purger)."""
    with get_connection() as conn:
        return conn.execute(
            "SELECT o.* FROM orders o JOIN validated_characters v ON v.id = o.chef_character_id "
            "WHERE v.guild_id = ? AND o.status = 'pending_deletion' AND o.restore_deadline IS NOT NULL "
            "AND o.restore_deadline <= ? ORDER BY o.id ASC",
            (guild_id, now_iso),
        ).fetchall()


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
            "SELECT m.id, m.character_id, m.role_label, m.joined_at, v.character_name, v.user_id, "
            "v.slot_number FROM order_members m LEFT JOIN validated_characters v ON v.id = m.character_id "
            "WHERE m.order_id = ? ORDER BY m.id ASC",
            (order_id,),
        ).fetchall()


def get_order_member(order_id: int, character_id: int):
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM order_members WHERE order_id = ? AND character_id = ?",
            (order_id, character_id),
        ).fetchone()


def add_order_member(order_id: int, character_id: int, role_label: str, joined_at: str = None):
    """Ajoute un membre à l'ordre. joined_at = date d'entrée effective (défaut : maintenant) ; elle sert
    au calcul de l'indemnité graduée à la suppression. Tout appelant DOIT laisser le défaut ou passer la
    vraie date d'ajout."""
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO order_members (order_id, character_id, role_label, joined_at) VALUES (?, ?, ?, ?)",
            (order_id, character_id, role_label, joined_at or datetime.utcnow().isoformat()),
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


def get_any_salon_owner(channel_id: int):
    """Order_salon (n'importe quel ordre, n'importe quel statut : Acheté / Louée / Location) portant ce
    salon, ou None. Sert à détecter les conflits d'achat au sens large (un salon déjà engagé nulle part
    ne peut pas être acquis)."""
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM order_salons WHERE channel_id = ? LIMIT 1", (channel_id,)
        ).fetchone()


def get_all_salon_rows(channel_id: int):
    """TOUTES les lignes order_salons portant ce salon (une seule pour un 'Acheté', deux pour une
    location : la 'Location' côté propriétaire et la 'Louée' côté locataire). Sert à la conquête d'un
    salon à l'achat."""
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM order_salons WHERE channel_id = ?", (channel_id,)
        ).fetchall()


def resolve_salon_true_owner(channel_id: int):
    """Le VRAI propriétaire d'un salon est TOUJOURS la ligne avec status IN ('Acheté', 'Location').
    Une ligne 'Louée' n'est jamais autoritaire : c'est juste le miroir chez l'emprunteur. Retourne la
    ligne propriétaire, ou None si aucun ordre ne le possède réellement (salon libre)."""
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM order_salons WHERE channel_id = ? AND status IN ('Acheté', 'Location') LIMIT 1",
            (channel_id,),
        ).fetchone()


def get_salon_louee_mirror(channel_id: int, tenant_order_id: int):
    """Ligne miroir 'Louée' d'un salon chez l'ordre locataire (contrepartie d'une 'Location'). None si
    absente."""
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM order_salons WHERE channel_id = ? AND order_id = ? AND status = 'Louée' LIMIT 1",
            (channel_id, tenant_order_id),
        ).fetchone()


def delete_salon_everywhere(channel_id: int):
    """Supprime toutes les lignes order_salons portant ce salon, quel que soit l'ordre ou le statut
    (conquête : le salon est retiré à son ordre actuel avant d'être réattribué à l'acheteur)."""
    with get_connection() as conn:
        conn.execute("DELETE FROM order_salons WHERE channel_id = ?", (channel_id,))


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


def get_orders_in_guild_full(guild_id: int):
    """Ordres du serveur avec l'id du chef ET le compte Discord propriétaire du chef (le guild est
    déterminé via le personnage chef). Sert à la vérification de présence des chefs."""
    with get_connection() as conn:
        return conn.execute(
            "SELECT o.id, o.chef_character_id, o.name, v.user_id AS owner_user_id FROM orders o "
            "JOIN validated_characters v ON v.id = o.chef_character_id "
            "WHERE v.guild_id = ? ORDER BY o.id ASC",
            (guild_id,),
        ).fetchall()


def get_orders_in_guild_of_types(guild_id: int, types):
    """Ordres d'un serveur dont le type est dans `types` (le serveur est déterminé via le chef). Sert au
    cycle hebdomadaire (taxe / salaires / verrous) par serveur."""
    with get_connection() as conn:
        placeholders = ",".join("?" * len(types))
        return conn.execute(
            f"SELECT o.* FROM orders o JOIN validated_characters v ON v.id = o.chef_character_id "
            f"WHERE v.guild_id = ? AND o.type IN ({placeholders}) ORDER BY o.id ASC",
            (guild_id, *types),
        ).fetchall()


# ---------- État global du bot (idempotence des tâches planifiées) ----------
def get_bot_state(key: str):
    """Valeur associée à une clé d'état (ou None)."""
    with get_connection() as conn:
        row = conn.execute("SELECT value FROM bot_state WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def set_bot_state(key: str, value: str):
    with get_connection() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO bot_state (key, value) VALUES (?, ?)", (key, value)
        )


def record_departure(user_id: int, guild_id: int, departed_at: str):
    """Enregistre (ou RÉINITIALISE) un départ frais : timer à `departed_at`, awaiting_owner_decision=0.
    À utiliser sur un vrai on_member_remove (le joueur vient de partir)."""
    with get_connection() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO player_departures "
            "(user_id, guild_id, departed_at, awaiting_owner_decision) VALUES (?, ?, ?, 0)",
            (user_id, guild_id, departed_at),
        )


def ensure_departure(user_id: int, guild_id: int, departed_at: str):
    """Crée une ligne de départ SEULEMENT si aucune n'existe (ne réinitialise ni le timer ni le gel).
    À utiliser au rattrapage de démarrage pour les joueurs déjà absents."""
    with get_connection() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO player_departures "
            "(user_id, guild_id, departed_at, awaiting_owner_decision) VALUES (?, ?, ?, 0)",
            (user_id, guild_id, departed_at),
        )


def get_departure(user_id: int, guild_id: int):
    """Ligne de départ d'un joueur sur un serveur (ou None)."""
    with get_connection() as conn:
        return conn.execute(
            "SELECT user_id, guild_id, departed_at, awaiting_owner_decision, "
            "last_decision_message_id, last_decision_channel_id "
            "FROM player_departures WHERE user_id = ? AND guild_id = ?",
            (user_id, guild_id),
        ).fetchone()


def set_departure_decision_message(user_id: int, message_id: int, channel_id: int):
    """Mémorise le DERNIER DM de décision envoyé à l'owner pour ce joueur, afin de pouvoir le
    désactiver si un retour plus récent le remplace (et de rejeter un clic sur un vieux bouton)."""
    with get_connection() as conn:
        conn.execute(
            "UPDATE player_departures "
            "SET last_decision_message_id = ?, last_decision_channel_id = ? WHERE user_id = ?",
            (message_id, channel_id, user_id),
        )


def set_departure_awaiting(user_id: int):
    """Gèle la purge (le joueur est revenu, décision de l'owner en attente)."""
    with get_connection() as conn:
        conn.execute(
            "UPDATE player_departures SET awaiting_owner_decision = 1 WHERE user_id = ?", (user_id,)
        )


def freeze_and_extend_departure(user_id: int, new_departed_at: str):
    """Retour DANS les temps : gèle la purge (awaiting=1) ET repousse le timer (departed_at += 10 j)
    pour laisser à l'owner le temps d'échanger avec le joueur avant de trancher."""
    with get_connection() as conn:
        conn.execute(
            "UPDATE player_departures SET awaiting_owner_decision = 1, departed_at = ? WHERE user_id = ?",
            (new_departed_at, user_id),
        )


def delete_departure(user_id: int):
    """Supprime la trace de départ (retour validé, purge effectuée, ou décision tranchée)."""
    with get_connection() as conn:
        conn.execute("DELETE FROM player_departures WHERE user_id = ?", (user_id,))


def get_departures_to_purge(cutoff_iso: str):
    """Départs éligibles à la purge : non gelés (awaiting=0) ET partis depuis au moins 15 jours
    (departed_at <= cutoff_iso). Retourne (user_id, guild_id)."""
    with get_connection() as conn:
        return conn.execute(
            "SELECT user_id, guild_id FROM player_departures "
            "WHERE awaiting_owner_decision = 0 AND departed_at <= ?",
            (cutoff_iso,),
        ).fetchall()


def set_fiche_record(character_id: int, eo_value):
    """Enregistre (ou remplace) la réserve d'EO de la fiche validée — source de vérité permanente.
    Appelé à la validation de fiche, juste après l'insertion dans validated_characters."""
    with get_connection() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO fiche_record (character_id, eo_value) VALUES (?, ?)",
            (character_id, eo_value),
        )


def get_fiche_record(character_id: int):
    """Ligne fiche_record d'un personnage (ou None)."""
    with get_connection() as conn:
        return conn.execute(
            "SELECT character_id, eo_value FROM fiche_record WHERE character_id = ?",
            (character_id,),
        ).fetchone()


def sync_eo_with_fiche(character_id: int):
    """Réaligne eo_actuel/eo_max du profil sur la fiche (fiche_record), source de vérité permanente.
    Appelée à CHAQUE affichage du pillow /profil : garantit l'EO à jour peu importe l'âge du personnage
    ou un redémarrage entre-temps. Personnage sans réserve (eo_value NULL) -> aucune action."""
    with get_connection() as conn:
        fiche = conn.execute(
            "SELECT eo_value FROM fiche_record WHERE character_id = ?", (character_id,)
        ).fetchone()
        if fiche is None or fiche["eo_value"] is None:
            return  # Humain / Hybride chez les humains : pas de réserve à synchroniser
        conn.execute(
            "UPDATE character_profiles SET eo_actuel = ?, eo_max = ? WHERE character_id = ?",
            (fiche["eo_value"], fiche["eo_value"], character_id),
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
# NB : la suppression complète d'un ordre passe par delete_order_cascade() ci-dessous, qui nettoie
# TOUTES les tables liées : orders, order_members, order_salons, order_transactions,
# order_bank_sessions, order_disciple_assignments ET order_salaries.
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


# ---------- Rattachement disciple ↔ éducateur (ordres éducatifs) ----------
def get_order_members_by_role(order_id: int, role_label: str):
    """Membres d'un ordre ayant un rôle précis, avec nom + user_id (ex : lister les formateurs)."""
    with get_connection() as conn:
        return conn.execute(
            "SELECT m.character_id, m.role_label, v.character_name, v.user_id "
            "FROM order_members m LEFT JOIN validated_characters v ON v.id = m.character_id "
            "WHERE m.order_id = ? AND m.role_label = ? ORDER BY m.id ASC",
            (order_id, role_label),
        ).fetchall()


def add_disciple_assignment(order_id: int, disciple_character_id: int, educator_character_id: int):
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO order_disciple_assignments (order_id, disciple_character_id, educator_character_id) "
            "VALUES (?, ?, ?)",
            (order_id, disciple_character_id, educator_character_id),
        )


def remove_disciple_assignment(order_id: int, disciple_character_id: int):
    with get_connection() as conn:
        conn.execute(
            "DELETE FROM order_disciple_assignments WHERE order_id = ? AND disciple_character_id = ?",
            (order_id, disciple_character_id),
        )


def orphan_disciples_of_educator(educator_character_id: int) -> int:
    """Détache tous les disciples d'un formateur (educator_character_id -> NULL). Retourne le nombre
    de disciples devenus orphelins (pour prévenir le staff)."""
    with get_connection() as conn:
        cur = conn.execute(
            "UPDATE order_disciple_assignments SET educator_character_id = NULL WHERE educator_character_id = ?",
            (educator_character_id,),
        )
        return cur.rowcount


def get_disciples_of_educator(order_id: int, educator_character_id: int):
    """Assignations (id, disciple_character_id) des disciples rattachés à cet éducateur dans l'ordre."""
    with get_connection() as conn:
        return conn.execute(
            "SELECT id, disciple_character_id FROM order_disciple_assignments "
            "WHERE order_id = ? AND educator_character_id = ?",
            (order_id, educator_character_id),
        ).fetchall()


def count_disciples_of_educator(order_id: int, educator_character_id: int) -> int:
    with get_connection() as conn:
        return conn.execute(
            "SELECT COUNT(*) AS n FROM order_disciple_assignments "
            "WHERE order_id = ? AND educator_character_id = ?",
            (order_id, educator_character_id),
        ).fetchone()["n"]


def set_assignment_educator(assignment_id: int, educator_character_id):
    """Fixe (ou détache si educator_character_id=None) l'éducateur d'une assignation précise (par id)."""
    with get_connection() as conn:
        conn.execute(
            "UPDATE order_disciple_assignments SET educator_character_id = ? WHERE id = ?",
            (educator_character_id, assignment_id),
        )


def get_disciples_of_order(order_id: int):
    """Tous les disciples rattachés à l'ordre : lignes (disciple_character_id, educator_character_id).
    Sert à la gestion manuelle des disciples (« 🎓 Gérer le staff »)."""
    with get_connection() as conn:
        return conn.execute(
            "SELECT disciple_character_id, educator_character_id FROM order_disciple_assignments "
            "WHERE order_id = ? ORDER BY disciple_character_id",
            (order_id,),
        ).fetchall()


def get_disciple_educator(order_id: int, disciple_character_id: int):
    """Éducateur actuel d'un disciple dans l'ordre (character_id) ou None (non assigné / inconnu)."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT educator_character_id FROM order_disciple_assignments "
            "WHERE order_id = ? AND disciple_character_id = ?",
            (order_id, disciple_character_id),
        ).fetchone()
    return row["educator_character_id"] if row else None


def set_disciple_educator(order_id: int, disciple_character_id: int, educator_character_id):
    """Change l'éducateur d'un disciple, ciblé par (ordre, disciple) — pratique pour la réassignation
    manuelle où l'on n'a pas l'id de l'assignation sous la main."""
    with get_connection() as conn:
        conn.execute(
            "UPDATE order_disciple_assignments SET educator_character_id = ? "
            "WHERE order_id = ? AND disciple_character_id = ?",
            (educator_character_id, order_id, disciple_character_id),
        )


def cleanup_educator_assignments(order_id: int, educator_character_id: int, keep_disciple_ids):
    """Filet de sécurité : supprime les assignations encore rattachées à un éducateur retiré, hors
    disciples déjà réassignés (keep_disciple_ids)."""
    with get_connection() as conn:
        if keep_disciple_ids:
            placeholders = ",".join("?" * len(keep_disciple_ids))
            conn.execute(
                f"DELETE FROM order_disciple_assignments WHERE order_id = ? AND educator_character_id = ? "
                f"AND disciple_character_id NOT IN ({placeholders})",
                (order_id, educator_character_id, *keep_disciple_ids),
            )
        else:
            conn.execute(
                "DELETE FROM order_disciple_assignments WHERE order_id = ? AND educator_character_id = ?",
                (order_id, educator_character_id),
            )


def find_educator_order(character_id: int):
    """order_id du (premier) ordre où ce personnage est membre avec le rôle 'Formateur', ou None.
    Sert au point d'entrée unique de départ d'un éducateur (redistribution de ses disciples)."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT order_id FROM order_members WHERE character_id = ? AND role_label = 'Formateur' "
            "ORDER BY order_id ASC LIMIT 1",
            (character_id,),
        ).fetchone()
    return row["order_id"] if row else None


def _educator_contracts_exists(conn) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'educator_contracts'"
    ).fetchone() is not None


def transfer_active_contracts_educator(disciple_character_id: int, old_educator_id: int, new_educator_id: int):
    """Redirige les contrats ACTIFS d'un disciple de l'ancien vers le nouvel éducateur. Défensif : la
    table educator_contracts n'existe pas encore (système de contrats/salaires à venir), donc on ne
    fait rien tant qu'elle est absente — le jour où elle existera, ce transfert s'appliquera tout seul."""
    with get_connection() as conn:
        if not _educator_contracts_exists(conn):
            return
        conn.execute(
            "UPDATE educator_contracts SET educator_character_id = ? "
            "WHERE disciple_character_id = ? AND educator_character_id = ? AND status = 'active'",
            (new_educator_id, disciple_character_id, old_educator_id),
        )


# --- Lecture/clôture des contrats pour les notifications de départ (tous DÉFENSIFS : la table
# educator_contracts n'existe pas encore ; ces fonctions renvoient vide / ne font rien tant qu'elle
# est absente, et deviendront actives automatiquement le jour où le système de contrats sera créé). ---
def get_active_contracts_of_educator(educator_character_id: int):
    """Contrats actifs dont cet éducateur est le référent : liste de lignes (id, disciple_character_id,
    employer_order_id). [] si la table n'existe pas encore."""
    with get_connection() as conn:
        if not _educator_contracts_exists(conn):
            return []
        return conn.execute(
            "SELECT id, disciple_character_id, employer_order_id FROM educator_contracts "
            "WHERE educator_character_id = ? AND status = 'active'",
            (educator_character_id,),
        ).fetchall()


def get_active_contract_of_disciple(disciple_character_id: int):
    """Contrat actif d'un disciple (id, educator_character_id, employer_order_id), ou None (y compris si
    la table n'existe pas encore)."""
    with get_connection() as conn:
        if not _educator_contracts_exists(conn):
            return None
        return conn.execute(
            "SELECT id, educator_character_id, employer_order_id FROM educator_contracts "
            "WHERE disciple_character_id = ? AND status = 'active'",
            (disciple_character_id,),
        ).fetchone()


def end_contract(contract_id: int):
    """Clôt un contrat (status='ended'). No-op si la table n'existe pas encore."""
    with get_connection() as conn:
        if not _educator_contracts_exists(conn):
            return
        conn.execute("UPDATE educator_contracts SET status = 'ended' WHERE id = ?", (contract_id,))


# ---------- Création / cycle de vie des contrats éducateur ↔ employeur ----------
def create_contract(batch_id, disciple_character_id, educator_character_id, source_order_id,
                    employer_order_id, duree_type, duree_value, duree_unit, pct, salaire_fixe, created_at):
    """Insère un contrat en attente (status='pending'). Retourne son id."""
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO educator_contracts (batch_id, disciple_character_id, educator_character_id, "
            "source_order_id, employer_order_id, duree_type, duree_value, duree_unit, pct, salaire_fixe, "
            "status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)",
            (batch_id, disciple_character_id, educator_character_id, source_order_id, employer_order_id,
             duree_type, duree_value, duree_unit, pct, salaire_fixe, created_at),
        )
        return cur.lastrowid


def get_contracts_by_batch(batch_id: str):
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM educator_contracts WHERE batch_id = ? ORDER BY id ASC", (batch_id,)
        ).fetchall()


def set_batch_refused(batch_id: str):
    """Passe tout un lot encore en attente à 'refused'."""
    with get_connection() as conn:
        conn.execute(
            "UPDATE educator_contracts SET status = 'refused' WHERE batch_id = ? AND status = 'pending'",
            (batch_id,),
        )


def set_batch_expired(batch_id: str):
    """Passe tout un lot encore en attente à 'expired' (délai de réponse de l'éducateur dépassé)."""
    with get_connection() as conn:
        conn.execute(
            "UPDATE educator_contracts SET status = 'expired' WHERE batch_id = ? AND status = 'pending'",
            (batch_id,),
        )


def cancel_contract(contract_id: int):
    """Annule UN contrat précis (status='cancelled'). Sert aux vérifications réactives faites au moment
    du clic « ✅ Accepter » (ordre employeur dissous / disciple disparu entre-temps)."""
    with get_connection() as conn:
        conn.execute("UPDATE educator_contracts SET status = 'cancelled' WHERE id = ?", (contract_id,))


def set_batch_cancelled(batch_id: str):
    """Annule tout un lot ENCORE en attente (status='cancelled'), p.ex. quand l'ordre employeur a été
    dissous avant que l'éducateur ne réponde."""
    with get_connection() as conn:
        conn.execute(
            "UPDATE educator_contracts SET status = 'cancelled' WHERE batch_id = ? AND status = 'pending'",
            (batch_id,),
        )


def get_pending_contracts_of_educator(educator_character_id: int):
    """Contrats ENCORE 'pending' dont cet éducateur est le référent (lignes complètes, triées par lot),
    pour re-proposer la même offre au nouvel éducateur quand l'éducateur initial quitte son ordre.
    [] si la table n'existe pas encore."""
    with get_connection() as conn:
        if not _educator_contracts_exists(conn):
            return []
        return conn.execute(
            "SELECT * FROM educator_contracts WHERE educator_character_id = ? AND status = 'pending' "
            "ORDER BY batch_id, id",
            (educator_character_id,),
        ).fetchall()


def rebatch_pending_contracts(contract_ids, new_batch_id: str, new_educator_id: int):
    """Déplace des contrats ENCORE 'pending' vers un nouveau lot ET un nouvel éducateur (suite au départ
    de l'éducateur initialement proposé). Ne touche que les lignes encore 'pending' (idempotent si un
    Accepter/Refuser est passé entre-temps)."""
    if not contract_ids:
        return
    with get_connection() as conn:
        conn.executemany(
            "UPDATE educator_contracts SET batch_id = ?, educator_character_id = ? "
            "WHERE id = ? AND status = 'pending'",
            [(new_batch_id, new_educator_id, cid) for cid in contract_ids],
        )


def get_active_contracts_of_disciple_in_source(disciple_character_id: int, source_order_id: int):
    """Contrats ACTIFS d'un disciple issus d'un ordre éducatif SOURCE précis (quitter cet ordre éducatif
    met fin à ces contrats, quel que soit l'ordre employeur)."""
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM educator_contracts WHERE disciple_character_id = ? AND source_order_id = ? "
            "AND status = 'active' ORDER BY id ASC",
            (disciple_character_id, source_order_id),
        ).fetchall()


def get_pending_contracts_of_disciple_in_source(disciple_character_id: int, source_order_id: int):
    """Propositions ENCORE 'pending' d'un disciple issues d'un ordre éducatif SOURCE précis (quitter cet
    ordre annule aussi ces propositions non encore acceptées)."""
    with get_connection() as conn:
        if not _educator_contracts_exists(conn):
            return []
        return conn.execute(
            "SELECT * FROM educator_contracts WHERE disciple_character_id = ? AND source_order_id = ? "
            "AND status = 'pending' ORDER BY id ASC",
            (disciple_character_id, source_order_id),
        ).fetchall()


def get_pending_contracts_of_disciple_and_educator(disciple_character_id: int, educator_character_id):
    """Propositions ENCORE 'pending' d'un disciple auprès d'un éducateur donné (réassignation manuelle :
    ces propositions suivent le disciple vers le nouvel éducateur). [] si aucun éducateur (None)."""
    if educator_character_id is None:
        return []
    with get_connection() as conn:
        if not _educator_contracts_exists(conn):
            return []
        return conn.execute(
            "SELECT * FROM educator_contracts WHERE disciple_character_id = ? AND educator_character_id = ? "
            "AND status = 'pending' ORDER BY batch_id, id",
            (disciple_character_id, educator_character_id),
        ).fetchall()


def get_active_contract_full_of_disciple(disciple_character_id: int):
    """Ligne COMPLÈTE du contrat actif d'un disciple (pour afficher les détails), ou None."""
    with get_connection() as conn:
        if not _educator_contracts_exists(conn):
            return None
        return conn.execute(
            "SELECT * FROM educator_contracts WHERE disciple_character_id = ? AND status = 'active' "
            "ORDER BY id DESC LIMIT 1",
            (disciple_character_id,),
        ).fetchone()


def get_all_active_contracts():
    """Tous les contrats ACTIFS (vérification périodique de présence des disciples). [] si la table
    n'existe pas encore."""
    with get_connection() as conn:
        if not _educator_contracts_exists(conn):
            return []
        return conn.execute(
            "SELECT * FROM educator_contracts WHERE status = 'active' ORDER BY id ASC"
        ).fetchall()


def get_all_pending_contracts():
    """Tous les contrats ENCORE 'pending' (reprise des minuteries de proposition au démarrage). [] si la
    table n'existe pas encore."""
    with get_connection() as conn:
        if not _educator_contracts_exists(conn):
            return []
        return conn.execute(
            "SELECT * FROM educator_contracts WHERE status = 'pending' ORDER BY batch_id, id"
        ).fetchall()


def activate_contract(contract_id: int, start_date: str, end_date):
    """Active un contrat accepté (start_date, end_date=None si durée indéterminée)."""
    with get_connection() as conn:
        conn.execute(
            "UPDATE educator_contracts SET status = 'active', start_date = ?, end_date = ? WHERE id = ?",
            (start_date, end_date, contract_id),
        )


def get_active_contracts_for_employer(employer_order_id: int):
    """Contrats ACTIFS dont l'ordre employeur est celui donné (pillow côté Direct/Hybride)."""
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM educator_contracts WHERE employer_order_id = ? AND status = 'active' ORDER BY id ASC",
            (employer_order_id,),
        ).fetchall()


def get_active_contracts_for_source(source_order_id: int):
    """Contrats ACTIFS dont l'ordre éducatif source est celui donné (pillow côté Éducatif)."""
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM educator_contracts WHERE source_order_id = ? AND status = 'active' ORDER BY id ASC",
            (source_order_id,),
        ).fetchall()


def get_expired_determinate_contracts(now_iso: str):
    """Contrats à durée déterminée, actifs, dont l'échéance est atteinte (pour l'expiration planifiée)."""
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM educator_contracts WHERE status = 'active' AND duree_type = 'determine' "
            "AND end_date IS NOT NULL AND end_date <= ? ORDER BY id ASC",
            (now_iso,),
        ).fetchall()


# ---------- Salaires des ordres (Direct / Hybride) ----------
def get_orders_of_types(types):
    """Tous les ordres dont le type est dans `types` (ex : ('direct', 'hybride')). Sert à la tâche
    planifiée des salaires."""
    with get_connection() as conn:
        placeholders = ",".join("?" * len(types))
        return conn.execute(
            f"SELECT * FROM orders WHERE type IN ({placeholders}) ORDER BY id ASC", tuple(types)
        ).fetchall()


def get_bank_account_by_courant_iban(iban: str):
    """Compte bancaire personnel dont l'IBAN COURANT correspond (le salaire vise le compte courant),
    ou None."""
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM bank_accounts WHERE iban_courant = ?", (iban,)
        ).fetchone()


def upsert_salary(order_id: int, character_id: int, montant: int, effective_start_date: str, added_at: str,
                  is_external: int = 0, expiry_date=None):
    """Ajoute ou met à jour (montant + date d'effet) le salaire d'un personnage dans un ordre.
    is_external=1 + expiry_date pour un IBAN hors des membres (salaire temporaire)."""
    with get_connection() as conn:
        existing = conn.execute(
            "SELECT id FROM order_salaries WHERE order_id = ? AND character_id = ?",
            (order_id, character_id),
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE order_salaries SET montant = ?, effective_start_date = ?, added_at = ?, "
                "is_external = ?, expiry_date = ? WHERE id = ?",
                (montant, effective_start_date, added_at, is_external, expiry_date, existing["id"]),
            )
        else:
            conn.execute(
                "INSERT INTO order_salaries (order_id, character_id, montant, effective_start_date, "
                "added_at, is_external, expiry_date) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (order_id, character_id, montant, effective_start_date, added_at, is_external, expiry_date),
            )


def get_salary(order_id: int, character_id: int):
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM order_salaries WHERE order_id = ? AND character_id = ?",
            (order_id, character_id),
        ).fetchone()


def get_salary_by_id(salary_id: int):
    with get_connection() as conn:
        return conn.execute("SELECT * FROM order_salaries WHERE id = ?", (salary_id,)).fetchone()


def update_salary_expiry(salary_id: int, expiry_date):
    """Repousse la date d'expiration d'un salaire externe (renouvellement)."""
    with get_connection() as conn:
        conn.execute("UPDATE order_salaries SET expiry_date = ? WHERE id = ?", (expiry_date, salary_id))


def delete_salary_by_id(salary_id: int):
    with get_connection() as conn:
        conn.execute("DELETE FROM order_salaries WHERE id = ?", (salary_id,))


def get_expired_external_salaries(now_iso: str):
    """Salaires externes (is_external=1) dont la date d'expiration est atteinte. Sert au rappel quotidien
    (DM au chef : Annuler / Renouveler)."""
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM order_salaries WHERE is_external = 1 AND expiry_date IS NOT NULL "
            "AND expiry_date <= ? ORDER BY id ASC",
            (now_iso,),
        ).fetchall()


def remove_salary(order_id: int, character_id: int):
    with get_connection() as conn:
        conn.execute(
            "DELETE FROM order_salaries WHERE order_id = ? AND character_id = ?",
            (order_id, character_id),
        )


def get_salaries_effective(order_id: int, today_iso: str):
    """Salaires de l'ordre dont la date d'effet est atteinte (effective_start_date <= aujourd'hui)."""
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM order_salaries WHERE order_id = ? AND effective_start_date <= ? ORDER BY id ASC",
            (order_id, today_iso),
        ).fetchall()


# ---------- Verrou de sécurité / état de trésorerie d'un ordre ----------
def set_order_security_lock(order_id: int, value: int):
    with get_connection() as conn:
        conn.execute("UPDATE orders SET security_lock = ? WHERE id = ?", (value, order_id))


def set_order_negative_since(order_id: int, iso_or_none):
    with get_connection() as conn:
        conn.execute("UPDATE orders SET negative_since = ? WHERE id = ?", (iso_or_none, order_id))


def set_order_lock_grace_until(order_id: int, iso_or_none):
    with get_connection() as conn:
        conn.execute("UPDATE orders SET lock_grace_until = ? WHERE id = ?", (iso_or_none, order_id))


def set_order_warning_sent(order_id: int, value: int):
    with get_connection() as conn:
        conn.execute("UPDATE orders SET warning_sent = ? WHERE id = ?", (value, order_id))


def clear_order_lock(order_id: int):
    """Remet un ordre à l'état sain : verrou levé, plus de date de négatif, plus de grâce, avertissement
    réinitialisé (prêt pour un futur cycle)."""
    with get_connection() as conn:
        conn.execute(
            "UPDATE orders SET security_lock = 0, negative_since = NULL, lock_grace_until = NULL, "
            "warning_sent = 0 WHERE id = ?",
            (order_id,),
        )


# ---------- Bannissement de création d'ordre (chef d'un ordre dissous) ----------
def set_chief_ban(user_id: int, banned_until: str):
    with get_connection() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO order_chief_bans (user_id, banned_until) VALUES (?, ?)",
            (user_id, banned_until),
        )


def get_chief_ban(user_id: int):
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM order_chief_bans WHERE user_id = ?", (user_id,)
        ).fetchone()


# ---------- Suppression / nettoyage croisé lors de la dissolution ----------
def get_linked_salons(order_id: int):
    """Salons de l'ordre liés à un AUTRE ordre (loués ou en location), pour libérer la contrepartie
    lors d'une dissolution."""
    with get_connection() as conn:
        return conn.execute(
            "SELECT channel_id, status, linked_order_id FROM order_salons "
            "WHERE order_id = ? AND status IN ('Louée', 'Location')",
            (order_id,),
        ).fetchall()


def get_expired_locations(now_iso: str):
    """Salons en cours de location (côté propriétaire, statut 'Location') dont l'échéance est atteinte.
    Chaque ligne : id (de la ligne propriétaire), order_id (propriétaire), linked_order_id (locataire),
    channel_id, location_expiry."""
    with get_connection() as conn:
        return conn.execute(
            "SELECT id, order_id, linked_order_id, channel_id, location_expiry FROM order_salons "
            "WHERE status = 'Location' AND location_expiry IS NOT NULL AND location_expiry <= ?",
            (now_iso,),
        ).fetchall()


def revert_location_to_bought(salon_id: int):
    """À l'expiration d'une location, le salon du propriétaire redevient un salon 'Acheté' normal
    (relien locataire et échéance effacés). Il sera de nouveau taxé chaque semaine."""
    with get_connection() as conn:
        conn.execute(
            "UPDATE order_salons SET status = 'Acheté', linked_order_id = NULL, location_expiry = NULL "
            "WHERE id = ?",
            (salon_id,),
        )


def remove_order_salon_any(order_id: int, channel_id: int):
    """Supprime la ligne salon d'un ordre pour un salon donné, quel que soit son statut (sert à retirer
    l'entrée miroir chez l'ordre contrepartie)."""
    with get_connection() as conn:
        conn.execute(
            "DELETE FROM order_salons WHERE order_id = ? AND channel_id = ?", (order_id, channel_id)
        )


def end_order_contracts(order_id: int):
    """Passe à 'ended' les contrats actifs liés à l'ordre (employeur ou source) et retourne la liste
    [(disciple_character_id, educator_character_id), ...] des contrats concernés pour notification.
    Défensif : la table educator_contracts n'existe pas encore (système de contrats à venir) — on
    retourne [] sans rien faire tant qu'elle est absente."""
    with get_connection() as conn:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'educator_contracts'"
        ).fetchone()
        if not exists:
            return []
        rows = conn.execute(
            "SELECT disciple_character_id, educator_character_id FROM educator_contracts "
            "WHERE (employer_order_id = ? OR source_order_id = ?) AND status = 'active'",
            (order_id, order_id),
        ).fetchall()
        conn.execute(
            "UPDATE educator_contracts SET status = 'ended' "
            "WHERE (employer_order_id = ? OR source_order_id = ?) AND status = 'active'",
            (order_id, order_id),
        )
        return [(r["disciple_character_id"], r["educator_character_id"]) for r in rows]


def delete_order_cascade(order_id: int):
    """Supprime définitivement un ordre et toutes ses données propres. Les contrats (educator_contracts)
    NE sont PAS supprimés ici : ils sont archivés en 'ended' par end_order_contracts() pour garder une
    trace. Les bannissements de chef (order_chief_bans) sont indépendants de l'ordre et conservés."""
    with get_connection() as conn:
        conn.execute("DELETE FROM order_members WHERE order_id = ?", (order_id,))
        conn.execute("DELETE FROM order_salons WHERE order_id = ?", (order_id,))
        conn.execute("DELETE FROM order_salaries WHERE order_id = ?", (order_id,))
        conn.execute("DELETE FROM order_disciple_assignments WHERE order_id = ?", (order_id,))
        conn.execute("DELETE FROM order_transactions WHERE order_id = ?", (order_id,))
        conn.execute("DELETE FROM order_bank_sessions WHERE order_id = ?", (order_id,))
        conn.execute("DELETE FROM orders WHERE id = ?", (order_id,))


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


def get_validated_character_slot(user_id: int, guild_id: int, slot_number: int):
    """Personnage validé d'un joueur dans un slot précis (id + nom), ou None."""
    with get_connection() as conn:
        return conn.execute(
            "SELECT id, character_name FROM validated_characters "
            "WHERE user_id = ? AND guild_id = ? AND slot_number = ?",
            (user_id, guild_id, slot_number),
        ).fetchone()


def delete_validated_character(user_id: int, guild_id: int, slot_number: int) -> int:
    """Supprime définitivement le personnage d'un slot. Renvoie le nombre de lignes supprimées."""
    with get_connection() as conn:
        cur = conn.execute(
            "DELETE FROM validated_characters WHERE user_id = ? AND guild_id = ? AND slot_number = ?",
            (user_id, guild_id, slot_number),
        )
        return cur.rowcount


# ---------- Réservations d'apparence (/réserv-appa) ----------
def create_appearance_reservation(user_id, guild_id, slot_number, nom_original, univers, image_path, created_at):
    """Insère une demande de réservation d'apparence (status='pending'). character_id reste NULL : le
    système est découplé des personnages validés, la clé de référence est (user_id, guild_id, slot_number).
    Retourne son id."""
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO appearance_reservations (character_id, user_id, guild_id, slot_number, "
            "nom_original, univers, image_path, status, created_at) "
            "VALUES (NULL, ?, ?, ?, ?, ?, ?, 'pending', ?)",
            (user_id, guild_id, slot_number, nom_original, univers, image_path, created_at),
        )
        return cur.lastrowid


def has_accepted_appearance_reservation(user_id: int, guild_id: int, slot_number: int) -> bool:
    """Vrai si ce slot a DÉJÀ une apparence validée (status='accepted') pour ce joueur sur ce serveur.
    Clé de référence du système découplé : user_id + guild_id + slot_number (plus character_id)."""
    with get_connection() as conn:
        return conn.execute(
            "SELECT 1 FROM appearance_reservations "
            "WHERE user_id = ? AND guild_id = ? AND slot_number = ? AND status = 'accepted' LIMIT 1",
            (user_id, guild_id, slot_number),
        ).fetchone() is not None


def get_appearance_reservation(reservation_id: int):
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM appearance_reservations WHERE id = ?", (reservation_id,)
        ).fetchone()


def get_accepted_appearance_reservations():
    """Réservations déjà acceptées (id, user_id, nom_original) — base de la détection de doublons."""
    with get_connection() as conn:
        return conn.execute(
            "SELECT id, user_id, nom_original FROM appearance_reservations WHERE status = 'accepted'"
        ).fetchall()


def set_appearance_reservation_status(reservation_id: int, status: str, refusal_reason=None):
    """Passe une réservation à 'accepted' ou 'refused' (+ raison éventuelle)."""
    with get_connection() as conn:
        conn.execute(
            "UPDATE appearance_reservations SET status = ?, refusal_reason = ? WHERE id = ?",
            (status, refusal_reason, reservation_id),
        )


def get_class_ranking(eo_classe: str):
    """Classement (pseudo Discord + valeur EO) des personnages validés d'une classe donnée."""
    with get_connection() as conn:
        return conn.execute(
            "SELECT discord_username, eo_value FROM validated_characters WHERE eo_classe = ? ORDER BY eo_value DESC",
            (eo_classe,),
        ).fetchall()
