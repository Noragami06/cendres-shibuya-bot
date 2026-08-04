from cogs.utils.image_gen import generate_clan_sort_image, generate_recompense_image, generate_reserve_image
import os

os.makedirs("temp", exist_ok=True)

clans_table = [
    ("Gojo", "5%", True), ("Zenin", "15%", False), ("Inumaki", "15%", False),
    ("Kamo", "15%", False), ("Geto", "15%", False), ("Ryomen", "15%", False),
    ("Kashimo", "15%", False), ("Sans clan", "5%", False),
]
spells_table = [
    ("Restriction céleste", "10%", False, False),
    ("Sort inné", "50%", True, False),
    ("Sort héréditaire (déjà pris)", "5%", False, True),
    ("Sort héréditaire partiel", "35%", False, False),
]
generate_clan_sort_image("Gojo", clans_table, "Sort inné", spells_table, "temp/preview_clan_sort.png")

generate_recompense_image(
    {"name": "Argent", "qty": "47 320 yens"},
    {"name": "Relique de classe 2", "qty": "x1"},
    "temp/preview_recompense.png",
)

ranking = [(1, "Han", 812, True), (2, "Yuji", 640, False), (3, "Sara", 522, False), (4, "Leo", 301, False)]
energy_table = [("Normal", "55%", False), ("Brute", "25%", True), ("Raffinée", "15%", False), ("Électrique", "5%", False)]
generate_reserve_image("4", 812, 100, 1000, ranking, energy_table, "temp/preview_reserve.png")

print("3 images générées dans temp/")

from cogs.utils.image_gen import generate_economie_image

transactions_exemple = [
    ("Récompense de départ", "30/07/2026", "+34 000 ¥", True),
    ("Achat — Boutique", "29/07/2026", "-8 500 ¥", False),
    ("Virement reçu", "27/07/2026", "+12 000 ¥", True),
    ("Frais d'ordre", "25/07/2026", "-2 300 ¥", False),
]
generate_economie_image("Han Gojo", "Gojo", 234567, 58200, transactions_exemple, "temp/preview_economie.png")
print("5ème image (économie) générée dans temp/")

from cogs.utils.image_gen import generate_pin_image

generate_pin_image(None, ["*", "*", "", ""], "temp/preview_pin.png")
print("6ème image (code PIN) générée dans temp/")

from cogs.utils.image_gen import generate_inventaire_image

items_exemple = [
    ("Katana Maudit", "Arme maudite redoutable", "2", 1, "12 000 ¥"),
    ("Parchemin RCT", "Technique de récupération", "4", 3, "2 500 ¥"),
    ("Relique Zenin", "Artefact du clan Zenin", "S", 1, "85 000 ¥"),
    ("Talisman d'Acier", "Boost défensif temporaire", "4", 2, "1 800 ¥"),
]
generate_inventaire_image("Daisuke Gojo", items_exemple, "137 700 ¥", "temp/preview_inventaire.png")
print("7ème image (inventaire) générée dans temp/")

from cogs.utils.image_gen import generate_shop_image

items_shop_exemple = [
    ("Katana Maudit", "Arme maudite redoutable", "2", "12 000 ¥"),
    ("Parchemin RCT", "Technique de récupération", "4", "2 500 ¥"),
    ("Relique Zenin", "Artefact du clan Zenin", "S", "85 000 ¥"),
    ("Talisman d'Acier", "Boost défensif temporaire", "4", "1 800 ¥"),
    ("Lance Spirite", "Arme à distance maudite", "3", "6 400 ¥"),
    ("Grimoire Kamo", "Techniques du clan Kamo", "3", "7 100 ¥"),
    ("Essence Maudite", "Ressource de craft", "4", "900 ¥"),
    ("Anneau du Sceau", "Relique de puissance", "1", "22 000 ¥"),
]
generate_shop_image(items_shop_exemple, 1, 3, "temp/preview_shop.png")
print("8ème image (shop) générée dans temp/")

from cogs.utils.image_gen import generate_profil_image

generate_profil_image(
    "Daisuke Gojo",
    pv=(1850, 2000),
    eo=(28000, 32407),
    level=12, xp=(4200, 5000),
    stats=[("Force", 3, 60, (600, 1000)), ("Vitesse", 2, 37, (300, 800)), ("Défense", 4, 90, (900, 1000))],
    maitrises=[("Maîtrise EO", 7, 70), ("Maîtrise Sort", 4, 40), ("Maîtrise Territoire", 2, 20), ("RCT", 3, 37)],
    clan="Gojo", rang="Héritier",
    victoires=12, defaites=3, nuls=1,
    out_path="temp/preview_profil.png",
)
print("9ème image (profil) générée dans temp/")
