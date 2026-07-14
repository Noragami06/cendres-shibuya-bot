import os
import uuid

from PIL import Image, ImageDraw, ImageFont

# ---------- Palette (modèle "sceau et registre") ----------
BG = "#100c07"
PANEL_BG = "#150f08"
PANEL_BORDER = "#4a3a1e"
GOLD = "#e8c579"
GOLD_BORDER = "#b4872f"
CLAN_DIM = "#7a6748"
SPELL_DIM = "#a3915f"
STRUCK = "#4a3a24"

# ---------- Dimensions ----------
CANVAS_W, CANVAS_H = 900, 420
PANEL_W, PANEL_H = 320, 380
PANEL_Y = (CANVAS_H - PANEL_H) // 2
LEFT_X = 90
RIGHT_X = CANVAS_W - PANEL_W - 90
PADDING = 20
RADIUS = 10

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "temp", "depart_images")

# Polices candidates, de la plus fidèle au repli le plus large
SERIF_REGULAR = [
    r"C:\Windows\Fonts\georgia.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
    "/System/Library/Fonts/Supplemental/Georgia.ttf",
]
SERIF_BOLD = [
    r"C:\Windows\Fonts\georgiab.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Georgia Bold.ttf",
]


def _load_font(candidates, size):
    """Charge la première police disponible, sans jamais planter."""
    for path in candidates:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    try:
        return ImageFont.load_default(size)
    except TypeError:  # Pillow < 9.2 : load_default() ne prend pas de taille
        return ImageFont.load_default()


def _text_width(draw, text, font):
    return draw.textbbox((0, 0), text, font=font)[2]


def _draw_panel(draw, x, y):
    draw.rounded_rectangle(
        [x, y, x + PANEL_W, y + PANEL_H],
        radius=RADIUS,
        fill=PANEL_BG,
        outline=PANEL_BORDER,
        width=1,
    )


def generate_clan_sort_image(clan_data: dict, spell_data: dict) -> str:
    """Génère l'image du résultat de tirage et retourne le chemin du PNG.

    clan_data  = {"title": str, "rows": [{"label": str, "pct": int, "selected": bool}, ...]}
    spell_data = {"result": str,
                  "rows": [{"label": str, "pct": int, "selected": bool, "unavailable": bool}, ...]}
    """
    image = Image.new("RGB", (CANVAS_W, CANVAS_H), BG)
    draw = ImageDraw.Draw(image)

    font_title = _load_font(SERIF_BOLD, 20)
    font_row = _load_font(SERIF_REGULAR, 15)
    font_row_bold = _load_font(SERIF_BOLD, 15)
    font_result = _load_font(SERIF_BOLD, 17)

    # ---------- Panneau GAUCHE : le clan ----------
    _draw_panel(draw, LEFT_X, PANEL_Y)

    title = clan_data["title"]
    title_x = LEFT_X + (PANEL_W - _text_width(draw, title, font_title)) // 2
    draw.text((title_x, PANEL_Y + PADDING), title, font=font_title, fill=GOLD)

    # Filet de séparation sous le titre
    line_y = PANEL_Y + PADDING + 34
    draw.line(
        [LEFT_X + PADDING, line_y, LEFT_X + PANEL_W - PADDING, line_y],
        fill=PANEL_BORDER,
        width=1,
    )

    row_y = line_y + 18
    for row in clan_data["rows"]:
        selected = row["selected"]
        color = GOLD if selected else CLAN_DIM
        font = font_row_bold if selected else font_row

        pct_text = f"{row['pct']}%"
        draw.text((LEFT_X + PADDING, row_y), row["label"], font=font, fill=color)
        draw.text(
            (LEFT_X + PANEL_W - PADDING - _text_width(draw, pct_text, font), row_y),
            pct_text,
            font=font,
            fill=color,
        )
        row_y += 28

    # ---------- Panneau DROIT : le sort ----------
    _draw_panel(draw, RIGHT_X, PANEL_Y)

    # Case du résultat, encadrée en doré
    box_top = PANEL_Y + PADDING
    box_bottom = box_top + 44
    draw.rounded_rectangle(
        [RIGHT_X + PADDING, box_top, RIGHT_X + PANEL_W - PADDING, box_bottom],
        radius=6,
        outline=GOLD_BORDER,
        width=1,
    )

    result_text = f"Sort : {spell_data['result']}"
    result_x = RIGHT_X + (PANEL_W - _text_width(draw, result_text, font_result)) // 2
    draw.text((result_x, box_top + 12), result_text, font=font_result, fill=GOLD)

    row_y = box_bottom + 24
    for row in spell_data["rows"]:
        unavailable = row.get("unavailable", False)
        selected = row["selected"]

        if unavailable:
            color = STRUCK
            font = font_row
        elif selected:
            color = GOLD
            font = font_row_bold
        else:
            color = SPELL_DIM
            font = font_row

        pct_text = f"{row['pct']}%"
        label_x = RIGHT_X + PADDING
        pct_x = RIGHT_X + PANEL_W - PADDING - _text_width(draw, pct_text, font)

        draw.text((label_x, row_y), row["label"], font=font, fill=color)
        draw.text((pct_x, row_y), pct_text, font=font, fill=color)

        # Option indisponible : trait barré par-dessus le texte
        if unavailable:
            strike_y = row_y + 9
            draw.line(
                [label_x, strike_y, RIGHT_X + PANEL_W - PADDING, strike_y],
                fill=STRUCK,
                width=1,
            )

        row_y += 30

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    path = os.path.join(OUTPUT_DIR, f"depart_{uuid.uuid4().hex}.png")
    image.save(path, "PNG")
    return path
