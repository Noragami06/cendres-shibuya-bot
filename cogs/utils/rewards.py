# -*- coding: utf-8 -*-
"""
rewards.py — Règle UNIVERSELLE de doublement des gains pour les rôles VIP / Booster.

Toute récompense numérique OU quantité d'objet distribuée à un joueur passe par ce module AVANT d'être
appliquée (UPDATE base, crédit bancaire, ajout d'inventaire). Le rôle est TOUJOURS porté par le compte
Discord (jamais par le personnage) : on résout donc le membre propriétaire du personnage.

- `apply_vip_booster_multiplier(member, amount)` : ×2 si le membre a le rôle Booster OU VIP (jamais ×4),
  type d'entrée préservé (int reste int, float reste float).
- `multiplier_for_character(guild, character_id)` / `apply_for_character(...)` : variante qui résout le
  membre propriétaire ET gère le VIP VIRTUEL des personnages slot 2/3 (VIP gagné via coffre), en plus des
  vrais rôles Discord (Booster/VIP réels, valables pour tous les slots du compte).
"""

from cogs.utils import database as db

BOOSTER_ROLE_ID = 1521563661204979802
VIP_ROLE_ID = 1549049286329761924


def apply_vip_booster_multiplier(member, amount):
    """Retourne amount ×2 si le membre possède le rôle Booster OU VIP (jamais cumulatif ×4), sinon
    amount inchangé. Fonctionne sur int comme sur float (préserve le type d'entrée)."""
    if member is None:
        return amount
    role_ids = {r.id for r in getattr(member, "roles", [])}
    if BOOSTER_ROLE_ID in role_ids or VIP_ROLE_ID in role_ids:
        return amount * 2
    return amount


def has_vip_or_booster_character(guild, character_id) -> bool:
    """True si le JOUEUR derrière ce personnage bénéficie du bonus : Booster/VIP réel sur son compte
    (tous slots), OU VIP VIRTUEL du personnage (slot 2/3, gagné via coffre). Jamais ×4 : c'est un booléen."""
    char = db.get_validated_character_by_id(character_id)
    if char is None:
        return False
    member = guild.get_member(char["user_id"]) if guild is not None else None
    if member is not None:
        rids = {r.id for r in getattr(member, "roles", [])}
        if BOOSTER_ROLE_ID in rids or VIP_ROLE_ID in rids:
            return True
    # VIP virtuel (slots 2/3) : le rôle VIP est enregistré dans character_virtual_roles.
    if char["slot_number"] in (2, 3) and VIP_ROLE_ID in db.get_virtual_roles(character_id):
        return True
    return False


def multiplier_for_character(guild, character_id) -> int:
    """2 si le joueur a le bonus VIP/Booster, sinon 1."""
    return 2 if has_vip_or_booster_character(guild, character_id) else 1


def apply_for_character(guild, character_id, amount):
    """amount ×2 si le joueur derrière ce personnage a le bonus VIP/Booster, sinon amount. Type préservé."""
    return amount * multiplier_for_character(guild, character_id)
