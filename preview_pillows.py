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
    arme_maudite=("Arme Maudite", 6, 38),
    clan="Gojo", rang="Héritier",
    victoires=12, defaites=3, nuls=1,
    out_path="temp/preview_profil.png",
)
print("9ème image (profil) générée dans temp/")

from cogs.utils.image_gen import generate_stats_image

stats_exemple = [
    ("Force", (215, 80, 80), 45, 245, 38, "Tranche 2 : 10-100"),
    ("RCT", (100, 220, 150), 75, 75, 72, "Tranche 2 : 10-100"),
    ("Vitesse", (90, 150, 240), 320, 520, 24, "Tranche 3 : 100-1000"),
    ("Territoire", (190, 100, 240), 5, 155, 50, "Tranche 1 : 0-10"),
    ("Endurance", (230, 170, 60), 850, 850, 83, "Tranche 3 : 100-1000"),
    ("Sorts", (230, 220, 70), 990, 1290, 98, "Tranche 3 : 100-1000"),
    ("Armes maudites", (230, 140, 60), 250, 250, 16, "Tranche 3 : 100-1000"),
    ("Énergie occulte", (100, 160, 230), 1200, 1900, 2, "Tranche 4 : 1000-10000"),
]
buffs_exemple = [
    "Six Eyes  →  Force +200 · Vitesse +200 · Énergie occulte +500 · Sorts +300",
    "Clan Gojo  →  Territoire +150 · Énergie occulte +200",
]
generate_stats_image("Daisuke", stats_exemple, buffs_exemple, 12, "temp/preview_stats.png")
print("10ème image (stats) générée dans temp/")

from cogs.utils.image_gen import generate_ordre_image

members_exemple = [
    ("Chef d'ordre", 1, (255, 165, 60)),
    ("Sous-chef", 1, (230, 90, 90)),
    ("Formateur", 2, (100, 200, 150)),
    ("Chef d'équipe", 3, (90, 150, 240)),
    ("Membre d'équipe", 12, (170, 170, 180)),
    ("Corps administratif", 2, (190, 100, 240)),
]
salons_exemple = [
    ("quartier-historique", "Acheté"),
    ("pont-sumida", "Location"),
    ("marché-nocturne", "Louée"),
    ("quai-est", "Acheté"),
    ("place-du-marché", "Location"),
]
generate_ordre_image(
    "Ordre du Phénix Ardent",
    members_exemple,
    458200,
    [12000, -5000, 8000, 15000, -2000, 20000, 6000],
    ["Lun", "Mar", "Mer", "Jeu", "Ven", "Sam", "Dim"],
    salons_exemple,
    "temp/preview_ordre.png",
)
print("11ème image (ordre) générée dans temp/")

from cogs.utils.image_gen import generate_technique_image, generate_technique_detail_image, generate_territoire_image

# ---------- Technique : vue d'ensemble ----------
# Chaque sort est un 7-tuple (nom, niveau, couleur, xp_actuel, xp_max, locked, unlock_level).
sorts_exemple = [
    ("Limitless", 5, (90, 160, 240), 300, 1150, False, 1),
    ("Six Eyes", 3, (170, 100, 240), 120, 800, False, 1),
]
generate_technique_image("Satoru Gojo", "Exorciste", sorts_exemple, "temp/preview_technique.png")
print("Technique (vue d'ensemble) générée dans temp/")

# ---------- Technique : détail d'un sort principal ----------
# Chaque secondaire est un 6-tuple (nom, classe, niveau_requis, debloque, cout_pct, degats).
secondaires_exemple = [
    ("Azur Perforant", "3", 1, True, 45, 350),
    ("Marée Convergente", "1", 3, True, 29, 1200),
    ("Rupture Bleue", "S", 5, True, 40, 3800),
    (None, None, 10, False, None, None),
    (None, None, 15, False, None, None),
    (None, None, 20, False, None, None),
    (None, None, 25, False, None, None),
    (None, None, 30, False, None, None),
]
generate_technique_detail_image("Bleu", 5, (90, 160, 240), secondaires_exemple, "temp/preview_technique_detail.png")
print("Technique (détail) générée dans temp/")

# ---------- Territoire ----------
generate_territoire_image(
    "Satoru Gojo", "Domaine du Vide Infini", "Non maîtrisé",
    maitrise_level=3, maitrise_pct=42, cout_eo_pct=35, duree_tours=4,
    description="Un espace clos où le temps et la perception ralentissent, projetant l'utilisateur et sa cible dans un vide immatériel saturé d'informations sensorielles infinies.",
    effets="Toute cible prise dans le domaine subit un assaut informationnel continu, paralysant sa capacité de réaction tant qu'elle n'est pas protégée par une barrière adverse.",
    out_path="temp/preview_territoire.png",
)
print("Territoire générée dans temp/")

from cogs.utils.image_gen import generate_arme_maudite_image

generate_arme_maudite_image(
    "Satoru Gojo", "Horus", "S",
    "31% de la réserve", "2 763 pts",
    "Une baguette qui crée toute sorte de sort, mais principalement du feu. Le sort le plus célèbre étant le Protego Diabolica et l'Avada Kedavra.",
    weapon_image_path=None,  # remplace par un vrai chemin d'image locale si tu en as un pour tester
    out_path="temp/preview_arme_maudite.png",
)
print("Arme maudite générée dans temp/")
