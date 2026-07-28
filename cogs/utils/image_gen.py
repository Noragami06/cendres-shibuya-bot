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


def make_output_path(prefix: str = "img") -> str:
    """Retourne un chemin PNG unique dans le dossier temporaire des images."""
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    return os.path.join(OUTPUT_DIR, f"{prefix}_{uuid.uuid4().hex}.png")


def _draw_reward_panel(draw, x, header, option, font_head, font_name, font_qty):
    _draw_panel(draw, x, PANEL_Y)
    cx = x + PANEL_W // 2

    draw.text((cx - _text_width(draw, header, font_head) // 2, PANEL_Y + 26), header, font=font_head, fill=GOLD)
    draw.line(
        [x + PADDING, PANEL_Y + 58, x + PANEL_W - PADDING, PANEL_Y + 58],
        fill=PANEL_BORDER, width=1,
    )

    # Cadre doré autour de la récompense
    box_top = PANEL_Y + 140
    box_bottom = box_top + 100
    draw.rounded_rectangle(
        [x + PADDING, box_top, x + PANEL_W - PADDING, box_bottom],
        radius=8, outline=GOLD_BORDER, width=1,
    )

    name = option["name"]
    qty = option.get("qty", "")
    draw.text((cx - _text_width(draw, name, font_name) // 2, box_top + 24), name, font=font_name, fill=GOLD)
    if qty:
        draw.text((cx - _text_width(draw, qty, font_qty) // 2, box_top + 60), qty, font=font_qty, fill=SPELL_DIM)


def generate_recompense_image(option_a, option_b, output_path):
    """Image du choix de récompense (deux options côte à côte, même style que le reste).

    option_a / option_b : dicts contenant au moins {"name": str, "qty": str}
    """
    image = Image.new("RGB", (CANVAS_W, CANVAS_H), BG)
    draw = ImageDraw.Draw(image)

    font_head = _load_font(SERIF_BOLD, 18)
    font_name = _load_font(SERIF_BOLD, 19)
    font_qty = _load_font(SERIF_REGULAR, 15)

    _draw_reward_panel(draw, LEFT_X, "Récompense A", option_a, font_head, font_name, font_qty)
    _draw_reward_panel(draw, RIGHT_X, "Récompense B", option_b, font_head, font_name, font_qty)

    out_dir = os.path.dirname(output_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    image.save(output_path, "PNG")
    return output_path


def _format_number(n: int) -> str:
    return f"{n:,}".replace(",", " ")


def generate_reserve_image(classe, value, minimum, maximum, ranking, energy_table, output_path):
    """Image de l'étape "Réserve d'énergie occulte" (même style que le tirage clan/sort).

    classe        : str affichée telle quelle (ex "4", "S")
    value/min/max : entiers (jauge de position)
    ranking       : [(rang, nom, valeur, is_hit), ...]
    energy_table  : [(nom_nature, "65%", is_hit), ...] ou liste vide (pas de nature)
    """
    image = Image.new("RGB", (CANVAS_W, CANVAS_H), BG)
    draw = ImageDraw.Draw(image)

    font_title = _load_font(SERIF_BOLD, 17)
    font_big = _load_font(SERIF_BOLD, 34)
    font_label = _load_font(SERIF_REGULAR, 13)
    font_row = _load_font(SERIF_REGULAR, 15)
    font_row_bold = _load_font(SERIF_BOLD, 15)
    font_head = _load_font(SERIF_BOLD, 16)

    # ---------- Panneau GAUCHE : classe / valeur / jauge ----------
    _draw_panel(draw, LEFT_X, PANEL_Y)
    cx = LEFT_X + PANEL_W // 2

    title = "Réserve d'énergie occulte"
    draw.text((cx - _text_width(draw, title, font_title) // 2, PANEL_Y + 22), title, font=font_title, fill=GOLD)

    classe_line = f"Classe {classe}"
    draw.text((cx - _text_width(draw, classe_line, font_head) // 2, PANEL_Y + 58), classe_line, font=font_head, fill=CLAN_DIM)

    value_str = _format_number(value)
    draw.text((cx - _text_width(draw, value_str, font_big) // 2, PANEL_Y + 108), value_str, font=font_big, fill=GOLD)

    unit = "d'énergie occulte"
    draw.text((cx - _text_width(draw, unit, font_label) // 2, PANEL_Y + 154), unit, font=font_label, fill=CLAN_DIM)

    # Jauge de position entre min et max
    bar_x1 = LEFT_X + PADDING + 10
    bar_x2 = LEFT_X + PANEL_W - PADDING - 10
    bar_y = PANEL_Y + 214
    bar_h = 10
    draw.rounded_rectangle([bar_x1, bar_y, bar_x2, bar_y + bar_h], radius=5, fill=PANEL_BORDER)

    span = maximum - minimum
    frac = 0.0 if span <= 0 else max(0.0, min(1.0, (value - minimum) / span))
    fill_x = bar_x1 + int((bar_x2 - bar_x1) * frac)
    if fill_x > bar_x1:
        draw.rounded_rectangle([bar_x1, bar_y, fill_x, bar_y + bar_h], radius=5, fill=GOLD_BORDER)
    draw.ellipse([fill_x - 5, bar_y - 3, fill_x + 5, bar_y + bar_h + 3], fill=GOLD)

    draw.text((bar_x1, bar_y + 20), _format_number(minimum), font=font_label, fill=CLAN_DIM)
    max_str = _format_number(maximum)
    draw.text((bar_x2 - _text_width(draw, max_str, font_label), bar_y + 20), max_str, font=font_label, fill=CLAN_DIM)

    # ---------- Panneau DROIT : classement (+ natures si fournies) ----------
    _draw_panel(draw, RIGHT_X, PANEL_Y)

    draw.text((RIGHT_X + PADDING, PANEL_Y + 20), "Classement", font=font_head, fill=GOLD)
    draw.line(
        [RIGHT_X + PADDING, PANEL_Y + 46, RIGHT_X + PANEL_W - PADDING, PANEL_Y + 46],
        fill=PANEL_BORDER, width=1,
    )

    row_y = PANEL_Y + 58
    for rank, name, val, is_hit in ranking:
        color = GOLD if is_hit else SPELL_DIM
        font = font_row_bold if is_hit else font_row
        left = f"#{rank}  {name}"
        val_str = _format_number(val)
        draw.text((RIGHT_X + PADDING, row_y), left, font=font, fill=color)
        draw.text((RIGHT_X + PANEL_W - PADDING - _text_width(draw, val_str, font), row_y), val_str, font=font, fill=color)
        row_y += 26

    if energy_table:
        row_y += 10
        draw.line([RIGHT_X + PADDING, row_y, RIGHT_X + PANEL_W - PADDING, row_y], fill=PANEL_BORDER, width=1)
        row_y += 14
        draw.text((RIGHT_X + PADDING, row_y), "Nature de l'énergie", font=font_head, fill=GOLD)
        row_y += 30
        for name, pct, is_hit in energy_table:
            color = GOLD if is_hit else SPELL_DIM
            font = font_row_bold if is_hit else font_row
            draw.text((RIGHT_X + PADDING, row_y), name, font=font, fill=color)
            draw.text((RIGHT_X + PANEL_W - PADDING - _text_width(draw, pct, font), row_y), pct, font=font, fill=color)
            row_y += 26

    out_dir = os.path.dirname(output_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    image.save(output_path, "PNG")
    return output_path
