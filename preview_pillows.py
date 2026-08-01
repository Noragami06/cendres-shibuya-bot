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
