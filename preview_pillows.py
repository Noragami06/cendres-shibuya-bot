from cogs.utils.image_gen import generate_clan_sort_image, generate_recompense_image, generate_reserve_image
import os

os.makedirs("temp", exist_ok=True)

# Exemple clan/sort
clans_table = [
    ("Gojo", "5%", True),
    ("Zenin", "15%", False),
    ("Inumaki", "15%", False),
    ("Kamo", "15%", False),
    ("Geto", "15%", False),
    ("Ryomen", "15%", False),
    ("Kashimo", "15%", False),
    ("Sans clan", "5%", False),
]
spells_table = [
    ("Restriction céleste", "10%", False, False),
    ("Sort inné", "50%", True, False),
    ("Sort héréditaire (déjà pris)", "5%", False, True),
    ("Sort héréditaire partiel", "35%", False, False),
]
generate_clan_sort_image("Gojo", clans_table, "Sort inné", spells_table, "temp/preview_clan_sort.png")

# Exemple récompense
generate_recompense_image(
    {"name": "Épée maudite", "qty": "Quantité x1"},
    {"name": "Pierre d'énergie occulte", "qty": "Quantité x50"},
    "temp/preview_recompense.png",
)

# Exemple réserve
ranking = [(1, "Han", 812, True), (2, "Yuji", 640, False), (3, "Sara", 522, False), (4, "Leo", 301, False)]
energy_table = [("Normal", "55%", False), ("Brute", "25%", True), ("Raffinée", "15%", False), ("Électrique", "5%", False)]
generate_reserve_image("4", 812, 100, 1000, ranking, energy_table, "temp/preview_reserve.png")

print("3 images générées dans le dossier temp/")
