from PIL import Image, ImageDraw, ImageFont, ImageOps, ImageFilter
import math
import os

def _load_font(size, bold=False):
    candidates = []
    if bold:
        candidates = [
            "C:\\Windows\\Fonts\\georgiab.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
        ]
    else:
        candidates = [
            "C:\\Windows\\Fonts\\georgia.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
        ]
    for path in candidates:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    try:
        return ImageFont.truetype("arial.ttf", size)
    except Exception:
        return ImageFont.load_default()

def font(size, bold=False):
    return _load_font(size, bold)

def text_w(draw, text, f):
    bbox = draw.textbbox((0, 0), text, font=f)
    return bbox[2] - bbox[0]

def wrap_text(draw, text, f, max_width):
    words = text.split()
    lines, cur = [], ""
    for w in words:
        trial = (cur + " " + w).strip()
        if text_w(draw, trial, f) <= max_width:
            cur = trial
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines

BG = (16, 12, 7, 255)
PANEL_BG = (21, 15, 8, 255)
PANEL_BORDER = (74, 58, 30, 255)
BOX_BG = (28, 21, 11, 255)
BOX_BORDER = (180, 135, 47, 255)
GOLD = (232, 197, 121, 255)
MUTED_LEFT = (122, 103, 72, 255)
MUTED_RIGHT = (163, 145, 95, 255)
STRIKE_COLOR = (74, 58, 36, 255)

def rounded_panel(draw, xy, radius, fill, outline, width=1):
    draw.rounded_rectangle(xy, radius=radius, fill=fill, outline=outline, width=width)

def strike_through(draw, x1, y, x2, color, width=1):
    draw.line([(x1, y), (x2, y)], fill=color, width=width)


def generate_clan_sort_image(clan_result: str, clans_table: list, spell_result: str, spells_table: list, out_path: str):
    W, H = 900, 420
    img = Image.new("RGBA", (W, H), BG)
    draw = ImageDraw.Draw(img)

    f_title = font(22, bold=True)
    f_row = font(15)
    f_row_b = font(15, bold=True)
    f_box = font(17)

    left_x0, left_y0, left_x1, left_y1 = 40, 40, 380, 380
    rounded_panel(draw, (left_x0, left_y0, left_x1, left_y1), radius=90, fill=PANEL_BG, outline=PANEL_BORDER, width=1)

    title_w = text_w(draw, clan_result, f_title)
    draw.text(((left_x0 + left_x1) / 2 - title_w / 2, left_y0 + 34), clan_result, font=f_title, fill=GOLD)

    row_y = left_y0 + 90
    row_x_left = left_x0 + 45
    row_x_right = left_x1 - 45
    for name, pct, is_hit in clans_table:
        f_use = f_row_b if is_hit else f_row
        color = GOLD if is_hit else MUTED_LEFT
        draw.text((row_x_left, row_y), name, font=f_use, fill=color)
        pct_w = text_w(draw, pct, f_use)
        draw.text((row_x_right - pct_w, row_y), pct, font=f_use, fill=color)
        row_y += 30

    right_x0, right_y0, right_x1, right_y1 = 420, 40, 860, 380
    rounded_panel(draw, (right_x0, right_y0, right_x1, right_y1), radius=14, fill=PANEL_BG, outline=PANEL_BORDER, width=1)

    box_x0, box_y0, box_x1, box_y1 = right_x0 + 20, right_y0 + 20, right_x1 - 20, right_y0 + 74
    rounded_panel(draw, (box_x0, box_y0, box_x1, box_y1), radius=10, fill=BOX_BG, outline=BOX_BORDER, width=1)
    box_text = f"Sort : {spell_result}"
    box_text_w = text_w(draw, box_text, f_box)
    draw.text(((box_x0 + box_x1) / 2 - box_text_w / 2, (box_y0 + box_y1) / 2 - 10), box_text, font=f_box, fill=GOLD)

    srow_y = box_y1 + 26
    srow_x_left = right_x0 + 24
    srow_x_right = right_x1 - 24
    for name, pct, is_hit, is_strike in spells_table:
        if is_strike:
            f_use = f_row
            color = STRIKE_COLOR
        elif is_hit:
            f_use = f_row_b
            color = GOLD
        else:
            f_use = f_row
            color = MUTED_RIGHT
        draw.text((srow_x_left, srow_y), name, font=f_use, fill=color)
        pct_w = text_w(draw, pct, f_use)
        draw.text((srow_x_right - pct_w, srow_y), pct, font=f_use, fill=color)
        if is_strike:
            name_w = text_w(draw, name, f_use)
            strike_through(draw, srow_x_left, srow_y + 10, srow_x_left + name_w, color)
            strike_through(draw, srow_x_right - pct_w, srow_y + 10, srow_x_right, color)
        srow_y += 32

    img.save(out_path)
    return out_path


def generate_recompense_image(option_a: dict, option_b: dict, out_path: str):
    CARD_BORDER = (180, 135, 47, 255)
    CARD_INNER_BORDER = (107, 78, 30, 255)
    FRAME_BG = (36, 26, 16, 255)
    NAME_COLOR = (240, 224, 184, 255)
    QTY_COLOR = (180, 135, 47, 255)
    CARD_BG_TOP = (28, 21, 11, 255)
    CARD_BG_BOTTOM = (18, 13, 7, 255)

    def make_card(letter, name, qty, size=(190, 320)):
        W, H = size
        card = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(card)
        for y in range(H):
            t = y / H
            r = int(CARD_BG_TOP[0] + (CARD_BG_BOTTOM[0] - CARD_BG_TOP[0]) * t)
            g = int(CARD_BG_TOP[1] + (CARD_BG_BOTTOM[1] - CARD_BG_TOP[1]) * t)
            b = int(CARD_BG_TOP[2] + (CARD_BG_BOTTOM[2] - CARD_BG_TOP[2]) * t)
            d.line([(0, y), (W, y)], fill=(r, g, b, 255))
        mask = Image.new("L", (W, H), 0)
        md = ImageDraw.Draw(mask)
        md.rounded_rectangle((0, 0, W - 1, H - 1), radius=16, fill=255)
        card.putalpha(mask)
        d.rounded_rectangle((2, 2, W - 3, H - 3), radius=15, outline=CARD_BORDER, width=3)
        d.rounded_rectangle((8, 8, W - 9, H - 9), radius=11, outline=CARD_INNER_BORDER, width=1)
        f_letter = font(14)
        cx, cy, r = W / 2, 32, 15
        d.ellipse((cx - r, cy - r, cx + r, cy + r), outline=CARD_BORDER, width=1)
        lw = text_w(d, letter, f_letter)
        d.text((cx - lw / 2, cy - 8), letter, font=f_letter, fill=GOLD)
        frame_xy = (18, 56, W - 18, H - 18)
        d.rounded_rectangle(frame_xy, radius=6, fill=FRAME_BG, outline=CARD_INNER_BORDER, width=1)
        f_name = font(16)
        max_w = (frame_xy[2] - frame_xy[0]) - 20
        lines = wrap_text(d, name, f_name, max_w)
        line_h = 22
        f_qty = font(12)
        total_h = len(lines) * line_h + 10 + 16
        fy = (frame_xy[1] + frame_xy[3]) / 2 - total_h / 2
        for line in lines:
            lw = text_w(d, line, f_name)
            d.text(((frame_xy[0] + frame_xy[2]) / 2 - lw / 2, fy), line, font=f_name, fill=NAME_COLOR)
            fy += line_h
        fy += 6
        qw = text_w(d, qty, f_qty)
        d.text(((frame_xy[0] + frame_xy[2]) / 2 - qw / 2, fy), qty, font=f_qty, fill=QTY_COLOR)
        return card

    W, H = 900, 460
    canvas = Image.new("RGBA", (W, H), BG)
    card_a = make_card("A", option_a["name"], option_a["qty"])
    card_b = make_card("B", option_b["name"], option_b["qty"])
    card_a = card_a.rotate(6, expand=True, resample=Image.BICUBIC)
    card_b = card_b.rotate(-6, expand=True, resample=Image.BICUBIC)
    gap = 90
    total_w = card_a.width + gap + card_b.width
    start_x = (W - total_w) // 2
    y_pos = (H - card_a.height) // 2 + 10
    canvas.alpha_composite(card_a, (start_x, y_pos))
    canvas.alpha_composite(card_b, (start_x + card_a.width + gap, y_pos))
    draw = ImageDraw.Draw(canvas)
    med_cx = start_x + card_a.width + gap / 2
    med_cy = H / 2
    med_r = 40
    draw.ellipse((med_cx - med_r, med_cy - med_r, med_cx + med_r, med_cy + med_r), fill=BG, outline=GOLD, width=2)
    f_ou = font(18, bold=True)
    ou_w = text_w(draw, "OU", f_ou)
    draw.text((med_cx - ou_w / 2, med_cy - 12), "OU", font=f_ou, fill=GOLD)
    f_title = font(16)
    title = "Choix de récompense"
    tw = text_w(draw, title, f_title)
    draw.text((W / 2 - tw / 2, 30), title, font=f_title, fill=GOLD)
    canvas.save(out_path)
    return out_path


def generate_reserve_image(classe: str, value: int, range_min: int, range_max: int, ranking: list, energy_table: list, out_path: str):
    HEAD_BG = (31, 24, 17, 255)
    BODY_BG = (22, 16, 6, 255)
    BORDER = (58, 46, 24, 255)
    MUTED_SUB = (107, 88, 56, 255)
    GAUGE_TRACK = (28, 21, 11, 255)
    GAUGE_BORDER = (74, 58, 30, 255)
    GAUGE_FILL_START = (107, 78, 30, 255)
    GAUGE_FILL_END = (232, 197, 121, 255)
    ROW_MUTED = (122, 103, 72, 255)

    def draw_gauge(draw, x0, y0, x1, y1, ratio):
        draw.rounded_rectangle((x0, y0, x1, y1), radius=(y1 - y0) / 2, fill=GAUGE_TRACK, outline=GAUGE_BORDER, width=1)
        fill_w = (x1 - x0) * ratio
        if fill_w > 4:
            for i in range(int(fill_w)):
                t = i / max(fill_w, 1)
                r = int(GAUGE_FILL_START[0] + (GAUGE_FILL_END[0] - GAUGE_FILL_START[0]) * t)
                g = int(GAUGE_FILL_START[1] + (GAUGE_FILL_END[1] - GAUGE_FILL_START[1]) * t)
                b = int(GAUGE_FILL_START[2] + (GAUGE_FILL_END[2] - GAUGE_FILL_START[2]) * t)
                draw.line([(x0 + i, y0 + 1), (x0 + i, y1 - 1)], fill=(r, g, b, 255))
        marker_x = x0 + fill_w
        draw.line([(marker_x, y0 - 4), (marker_x, y1 + 4)], fill=(240, 224, 184, 255), width=2)

    W, H = 900, 460
    img = Image.new("RGBA", (W, H), BODY_BG)
    draw = ImageDraw.Draw(img)
    outer = (30, 30, W - 30, H - 30)
    draw.rounded_rectangle(outer, radius=10, fill=BODY_BG, outline=BORDER, width=1)
    head_h = 150
    head_xy = (outer[0], outer[1], outer[2], outer[1] + head_h)
    draw.rounded_rectangle(head_xy, radius=10, fill=HEAD_BG, outline=None)
    draw.rectangle((outer[0], outer[1] + head_h - 10, outer[2], outer[1] + head_h), fill=HEAD_BG)
    draw.line([(outer[0], outer[1] + head_h), (outer[2], outer[1] + head_h)], fill=BORDER, width=1)
    f_sub = font(12)
    f_val = font(22, bold=True)
    sub = f"Réserve de classe {classe}"
    sub_w = text_w(draw, sub, f_sub)
    draw.text((W / 2 - sub_w / 2, outer[1] + 18), sub, font=f_sub, fill=MUTED_SUB)
    val_text = f"{value:,} EO"
    val_w = text_w(draw, val_text, f_val)
    draw.text((W / 2 - val_w / 2, outer[1] + 38), val_text, font=f_val, fill=GOLD)
    ratio = max(0.0, min(1.0, (value - range_min) / (range_max - range_min)))
    gx0, gx1 = outer[0] + 90, outer[2] - 90
    gy0 = outer[1] + 92
    f_glabel = font(10)
    draw.text((gx0, gy0 - 16), "Faible", font=f_glabel, fill=MUTED_SUB)
    hw = text_w(draw, "Élevé", f_glabel)
    draw.text((gx1 - hw, gy0 - 16), "Élevé", font=f_glabel, fill=MUTED_SUB)
    draw_gauge(draw, gx0, gy0, gx1, gy0 + 10, ratio)
    body_y0 = outer[1] + head_h
    body_y1 = outer[3]
    mid_x = (outer[0] + outer[2]) / 2
    draw.line([(mid_x, body_y0), (mid_x, body_y1)], fill=BORDER, width=1)
    f_h5 = font(12, bold=True)
    f_row = font(13)
    f_row_b = font(13, bold=True)
    lx0 = outer[0] + 30
    lx1 = mid_x - 30
    ly = body_y0 + 24
    draw.text((lx0, ly), "CLASSEMENT", font=f_h5, fill=MUTED_SUB)
    ly += 32
    for rank, name, val, is_hit in ranking:
        color = GOLD if is_hit else ROW_MUTED
        f_use = f_row_b if is_hit else f_row
        r_txt = str(rank)
        circle_r = 11
        draw.ellipse((lx0, ly - 2, lx0 + circle_r * 2, ly - 2 + circle_r * 2), outline=color, width=1)
        rw = text_w(draw, r_txt, f_row)
        draw.text((lx0 + circle_r - rw / 2, ly + 1), r_txt, font=f_row, fill=color)
        draw.text((lx0 + 32, ly), name, font=f_use, fill=color)
        vw = text_w(draw, str(val), f_use)
        draw.text((lx1 - vw, ly), str(val), font=f_use, fill=color)
        ly += 30
    rx0 = mid_x + 30
    rx1 = outer[2] - 30
    ry = body_y0 + 24
    draw.text((rx0, ry), "ÉNERGIE", font=f_h5, fill=MUTED_SUB)
    ry += 32
    for name, pct, is_hit in energy_table:
        color = GOLD if is_hit else (163, 145, 95, 255)
        f_use = f_row_b if is_hit else f_row
        draw.text((rx0, ry), name, font=f_use, fill=color)
        pw = text_w(draw, pct, f_use)
        draw.text((rx1 - pw, ry), pct, font=f_use, fill=color)
        ry += 28
    img.save(out_path)
    return out_path


def generate_slots_image(username: str, slots: list, out_path: str):
    """
    slots: liste de 3 dicts.
      Vide   : {"filled": False}
      Rempli : {"filled": True, "name": str, "camp_clan": str}
    Style hybride : cadre à coins coupés + bordure dorée épaisse + gemme + ruban/accents fleuris.
    """
    SLOT_GOLD = (232, 197, 121, 255)
    SLOT_GOLD_DIM = (150, 120, 60, 255)
    SLOT_NAME_COLOR = (240, 224, 184, 255)
    SLOT_MUTED = (140, 120, 85, 255)
    SLOT_GEM_RED = (210, 40, 60, 255)
    SLOT_BG = (12, 9, 6, 255)

    def placeholder_portrait(pa_w, pa_h):
        im = Image.new("RGB", (pa_w, pa_h), (35, 26, 15))
        dd = ImageDraw.Draw(im)
        for y in range(pa_h):
            t = y / pa_h
            c = (int(35 + 15 * t), int(26 + 10 * t), int(15 + 5 * t))
            dd.line([(0, y), (pa_w, y)], fill=c)
        return im

    def slot_hybrid(size, filled, name="", sub="", portrait_path=None):
        W, H = size
        img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        cut = 18
        outer = [(cut, 0), (W - cut, 0), (W, cut), (W, H - cut), (W - cut, H), (cut, H), (0, H - cut), (0, cut)]
        mask = Image.new("L", (W, H), 0)
        ImageDraw.Draw(mask).polygon(outer, fill=255)
        base = Image.new("RGBA", (W, H), (24, 17, 9, 255))
        img.paste(base, (0, 0), mask)

        d = ImageDraw.Draw(img)
        d.polygon(outer, outline=SLOT_GOLD, width=4)
        inner_cut = cut - 5
        inner = [(inner_cut + 5, 6), (W - inner_cut - 5, 6), (W - 6, inner_cut + 5), (W - 6, H - inner_cut - 5),
                  (W - inner_cut - 5, H - 6), (inner_cut + 5, H - 6), (6, H - inner_cut - 5), (6, inner_cut + 5)]
        d.polygon(inner, outline=SLOT_GOLD_DIM, width=1)

        gem_cx, gem_r = W / 2, 9
        d.polygon([(gem_cx, 2), (gem_cx + gem_r, 11), (gem_cx, 20), (gem_cx - gem_r, 11)], fill=SLOT_GEM_RED, outline=SLOT_GOLD)

        for cx, cy in [(16, H - 16), (W - 16, H - 16)]:
            d.ellipse((cx - 3, cy - 3, cx + 3, cy + 3), outline=SLOT_GOLD, width=1)
            d.line((cx - 8, cy, cx + 8, cy), fill=SLOT_GOLD_DIM, width=1)
            d.line((cx, cy - 8, cx, cy + 8), fill=SLOT_GOLD_DIM, width=1)

        if filled:
            pa = (16, 26, W - 16, H - 66)
            if portrait_path and os.path.exists(portrait_path):
                portrait = Image.open(portrait_path).convert("RGB")
                portrait = ImageOps.fit(portrait, (pa[2] - pa[0], pa[3] - pa[1]), method=Image.LANCZOS)
            else:
                portrait = placeholder_portrait(pa[2] - pa[0], pa[3] - pa[1])
            img.paste(portrait, (pa[0], pa[1]))
            d.rectangle((pa[0], pa[1], pa[2], pa[3]), outline=SLOT_GOLD_DIM, width=1)

            ribbon_y = H - 58
            d.polygon([(10, ribbon_y), (W - 10, ribbon_y), (W - 10, H - 14), (W / 2 + 12, H - 24),
                        (W / 2, H - 14), (W / 2 - 12, H - 24), (10, H - 14)], fill=(40, 28, 12, 255), outline=SLOT_GOLD)
            f_n = font(14, bold=True)
            nw = text_w(d, name, f_n)
            d.text((W / 2 - nw / 2, ribbon_y + 5), name, font=f_n, fill=SLOT_NAME_COLOR)
            f_s = font(10)
            sw = text_w(d, sub, f_s)
            d.text((W / 2 - sw / 2, ribbon_y + 23), sub, font=f_s, fill=SLOT_MUTED)
        else:
            cx, cy, r = W / 2, H / 2, 46
            d.ellipse((cx - r, cy - r, cx + r, cy + r), outline=SLOT_GOLD_DIM, width=1)
            for ang in range(0, 360, 45):
                rad = math.radians(ang)
                x1, y1 = cx + (r - 6) * math.cos(rad), cy + (r - 6) * math.sin(rad)
                x2, y2 = cx + (r + 2) * math.cos(rad), cy + (r + 2) * math.sin(rad)
                d.line((x1, y1, x2, y2), fill=SLOT_GOLD_DIM, width=1)
            f_plus = font(50, bold=True)
            pw = text_w(d, "+", f_plus)
            d.text((cx - pw / 2, cy - 40), "+", font=f_plus, fill=SLOT_GOLD)

        return img

    W, H = 900, 420
    canvas = Image.new("RGBA", (W, H), SLOT_BG)
    draw = ImageDraw.Draw(canvas)

    f_title = font(22, bold=True)
    title = "Sélection de personnage"
    tw = text_w(draw, title, f_title)
    draw.text((W / 2 - tw / 2, 34), title, font=f_title, fill=SLOT_GOLD)

    slot_size = (220, 280)
    gap = 40
    total_w = slot_size[0] * 3 + gap * 2
    start_x = (W - total_w) // 2
    y = 100

    for i, slot in enumerate(slots[:3]):
        piece = slot_hybrid(slot_size, slot.get("filled", False), slot.get("name", ""), slot.get("camp_clan", ""), slot.get("portrait_path"))
        canvas.alpha_composite(piece, (start_x + i * (slot_size[0] + gap), y))

    canvas.save(out_path)
    return out_path


def generate_economie_image(prenom: str, nom_clan: str, solde: int, livret_a: int, transactions: list, out_path: str):
    """
    transactions: liste de tuples (label, date, montant_str, is_positif)
    Exemple : [("Récompense de départ", "30/07/2026", "+34 000 ¥", True), ...]
    Affiche au maximum les 4 dernières transactions.
    """
    W, H = 1200, 650
    BG = (16, 15, 26, 255)
    TEXT = (245, 245, 250, 255)
    SUB = (170, 165, 190, 255)
    POS = (140, 255, 190, 255)
    NEG = (255, 140, 160, 255)
    LABEL = (255, 255, 255, 255)
    LINE = (50, 47, 65, 255)

    img = Image.new("RGBA", (W, H), BG)
    d = ImageDraw.Draw(img)

    d.rectangle((0, 0, W, 74), fill=(22, 20, 34, 255))
    d.text((40, 22), "Banque Phénix", font=font(18, True), fill=(255, 255, 255, 255))
    identite = f"{prenom} — {nom_clan}" if nom_clan else prenom
    name_w = text_w(d, identite, font(14, True))
    d.text((W - 40 - name_w, 27), identite, font=font(14, True), fill=TEXT)

    # carte SOLDE : degrade rose -> violet
    card = Image.new("RGBA", (550, 170), (0, 0, 0, 255))
    cd = ImageDraw.Draw(card)
    for x in range(550):
        t = x / 550
        r = int(255 * (1 - t) + 130 * t)
        g = int(90 * (1 - t) + 80 * t)
        b = int(160 * (1 - t) + 255 * t)
        cd.line([(x, 0), (x, 170)], fill=(r, g, b, 255))
    mask = Image.new("L", (550, 170), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, 549, 169), radius=20, fill=255)
    img.paste(card, (40, 110), mask)
    d.text((64, 134), "SOLDE ACTUEL", font=font(15, True), fill=LABEL)
    d.text((64, 168), f"{solde:,} ¥".replace(",", " "), font=font(38, True), fill=(255, 255, 255, 255))

    # carte LIVRET A : degrade orange -> dore (theme phenix)
    card2 = Image.new("RGBA", (550, 170), (0, 0, 0, 255))
    cd2 = ImageDraw.Draw(card2)
    for x in range(550):
        t = x / 550
        r = int(255 * (1 - t) + 230 * t)
        g = int(110 * (1 - t) + 170 * t)
        b = int(40 * (1 - t) + 50 * t)
        cd2.line([(x, 0), (x, 170)], fill=(r, g, b, 255))
    mask2 = Image.new("L", (550, 170), 0)
    ImageDraw.Draw(mask2).rounded_rectangle((0, 0, 549, 169), radius=20, fill=255)
    img.paste(card2, (610, 110), mask2)
    d.text((634, 134), "LIVRET A", font=font(15, True), fill=LABEL)
    d.text((634, 168), f"{livret_a:,} ¥".replace(",", " "), font=font(32, True), fill=(255, 255, 255, 255))

    d.text((40, 316), "Transactions récentes", font=font(16, True), fill=TEXT)
    rounded_panel(d, (40, 350, W - 40, 600), 20, fill=(28, 26, 40, 255), outline=None)
    y = 372
    display_transactions = transactions[:4]
    for i, (label, date, amount, pos) in enumerate(display_transactions):
        d.text((64, y), label, font=font(13, True), fill=TEXT)
        d.text((64, y + 20), date, font=font(11), fill=SUB)
        aw = text_w(d, amount, font(14, True))
        d.text((W - 64 - aw, y + 8), amount, font=font(14, True), fill=POS if pos else NEG)
        if i != len(display_transactions) - 1:
            d.line((64, y + 50, W - 64, y + 50), fill=LINE, width=1)
        y += 60

    if not display_transactions:
        d.text((64, 380), "Aucune transaction pour l'instant.", font=font(13), fill=SUB)

    img.save(out_path)
    return out_path


def generate_pin_image(portrait_path: str, values: list, out_path: str):
    """
    portrait_path : chemin vers la vraie photo du personnage (déjà utilisée ailleurs dans ce fichier via ImageOps.fit)
    values : liste de 4 éléments, chacun soit "*" (chiffre déjà saisi) soit "" (case vide)
             exemple : ["*", "*", "", ""] pour un code à moitié saisi
    """
    W, H = 500, 620
    BG = (16, 15, 26, 255)
    TEXT = (245, 245, 250, 255)
    GOLD1, GOLD2 = (255, 110, 40), (230, 170, 50)

    def grad_h(size, c1, c2):
        w, h = size
        card = Image.new("RGBA", (w, h), (0, 0, 0, 255))
        cd = ImageDraw.Draw(card)
        for x in range(w):
            t = x / w
            r = int(c1[0] * (1 - t) + c2[0] * t)
            g = int(c1[1] * (1 - t) + c2[1] * t)
            b = int(c1[2] * (1 - t) + c2[2] * t)
            cd.line([(x, 0), (x, h)], fill=(r, g, b, 255))
        return card

    def grad_ring_mask(size, thickness):
        w, h = size
        mask = Image.new("L", (w, h), 0)
        md = ImageDraw.Draw(mask)
        md.ellipse((0, 0, w - 1, h - 1), fill=255)
        md.ellipse((thickness, thickness, w - 1 - thickness, h - 1 - thickness), fill=0)
        return mask

    img = Image.new("RGBA", (W, H), BG)
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, W, 64), fill=(22, 20, 34, 255))
    d.text((24, 20), "Banque Phénix", font=font(15, True), fill=(255, 255, 255, 255))

    size = 150
    cx = W // 2
    ring = grad_h((size, size), GOLD1, GOLD2)
    mask = grad_ring_mask((size, size), 5)
    img.paste(ring, (cx - size // 2, 130), mask)

    inner = size - 16
    photo_mask = Image.new("L", (inner, inner), 0)
    ImageDraw.Draw(photo_mask).ellipse((0, 0, inner - 1, inner - 1), fill=255)

    if portrait_path and os.path.exists(portrait_path):
        photo = Image.open(portrait_path).convert("RGB")
        photo = ImageOps.fit(photo, (inner, inner), method=Image.LANCZOS)
    else:
        photo = Image.new("RGB", (inner, inner), (40, 38, 55))

    img.paste(photo, (cx - inner // 2, 130 + 8), photo_mask)

    d.text((24, 310), "Entre ton code secret", font=font(15, True), fill=TEXT)

    box = 70
    gap = 18
    total = box * 4 + gap * 3
    x0 = (W - total) // 2
    y0 = 350
    for i in range(4):
        v = values[i] if i < len(values) else ""
        x = x0 + i * (box + gap)
        d.rounded_rectangle((x, y0, x + box, y0 + box), radius=14, outline=GOLD2 if v else (60, 58, 78, 255), width=2, fill=(24, 22, 36, 255))
        if v:
            vw = text_w(d, v, font(28, True))
            d.text((x + box / 2 - vw / 2, y0 + box / 2 - 18), v, font=font(28, True), fill=TEXT)

    img.save(out_path)
    return out_path


CLASS_COLORS_INV = {
    "S": (255, 165, 0),
    "1": (235, 60, 100),
    "2": (170, 80, 240),
    "3": (60, 130, 240),
    "4": (40, 200, 150),
    "sans": (130, 130, 140),
}


def generate_inventaire_image(character_name: str, items: list, total_value: str, out_path: str):
    """
    items : liste de tuples (name, description, classe, quantite, valeur_str)
            classe est une chaîne parmi "S", "1", "2", "3", "4", "sans"
            valeur_str est déjà formatée, ex: "12 000 ¥"
    Affiche jusqu'à 8 objets (4 lignes de 2 colonnes). Si plus de 8 objets sont fournis,
    n'affiche que les 8 premiers pour l'instant (pagination à prévoir plus tard).
    """
    display_items = items[:8]
    rows = (len(display_items) + 1) // 2
    W = 1400
    H = 120 + rows * 144 + 100

    BG = (13, 13, 18, 255)
    CARD = (24, 24, 30, 255)
    TEXT = (235, 235, 240, 255)
    SUB = (140, 140, 150, 255)

    img = Image.new("RGBA", (W, H), BG)
    d = ImageDraw.Draw(img)
    d.text((40, 30), f"Inventaire de {character_name}", font=font(30, True), fill=TEXT)
    d.text((40, 74), f"{len(items)} objets  ·  Valeur totale : {total_value}", font=font(14), fill=SUB)

    col_w = (W - 40 * 3) // 2
    x_positions = [40, 40 + col_w + 40]
    y = 120
    row_h = 128
    for i, (name, desc, cls, qty, val) in enumerate(display_items):
        col = i % 2
        row = i // 2
        x = x_positions[col]
        yy = y + row * (row_h + 16)
        color = CLASS_COLORS_INV.get(cls, CLASS_COLORS_INV["sans"])
        d.rounded_rectangle((x, yy, x + col_w, yy + row_h), radius=10, fill=CARD)
        d.rounded_rectangle((x, yy, x + col_w, yy + 8), radius=4, fill=color)
        d.text((x + 24, yy + 22), name, font=font(15, True), fill=TEXT)
        d.text((x + 24, yy + 46), desc, font=font(11), fill=SUB)
        classe_label = f"Classe {cls}" if cls != "sans" else "Sans classe"
        d.text((x + 24, yy + 70), classe_label, font=font(11, True), fill=color)
        d.text((x + col_w - 140, yy + 22), f"x{qty}", font=font(13, True), fill=TEXT)
        vw = text_w(d, val, font(13, True))
        d.text((x + col_w - 24 - vw, yy + 22), val, font=font(13, True), fill=(120, 220, 160, 255))

    # legende
    ly = H - 60
    d.text((40, ly), "Légende :", font=font(12, True), fill=TEXT)
    lx = 40 + 90
    for key, label in [("S", "Classe S"), ("1", "Classe 1"), ("2", "Classe 2"), ("3", "Classe 3"), ("4", "Classe 4"), ("sans", "Sans classe")]:
        c = CLASS_COLORS_INV[key]
        d.ellipse((lx, ly + 2, lx + 12, ly + 14), fill=c)
        d.text((lx + 18, ly), label, font=font(11), fill=TEXT)
        lx += 100

    img.save(out_path)
    return out_path


SHOP_CLASS_COLORS = {
    "S": (255, 165, 0),
    "1": (235, 60, 100),
    "2": (170, 80, 240),
    "3": (60, 130, 240),
    "4": (40, 200, 150),
    "sans": (130, 130, 140),
}


def generate_shop_image(items: list, page: int, total_pages: int, out_path: str):
    """
    items : liste de tuples (name, description, classe, price_str) pour CETTE page uniquement (8 max)
            classe est une chaîne parmi "S", "1", "2", "3", "4", "sans"
            price_str est déjà formaté, ex: "12 000 ¥"
    page, total_pages : pour l'indicateur "Page X / Y" en haut à droite
    """
    W, H = 1300, 950
    BG, CARD, TEXT, SUB, ACCENT = (16, 16, 20, 255), (23, 23, 28, 255), (235, 235, 240, 255), (145, 145, 158, 255), (255, 210, 100, 255)
    img = Image.new("RGBA", (W, H), BG)
    d = ImageDraw.Draw(img)

    d.rectangle((0, 0, W, 84), fill=(20, 20, 25, 255))
    d.text((40, 26), "Boutique — Banque Phénix", font=font(22, True), fill=(255, 255, 255, 255))
    d.text((W - 260, 32), f"Page {page} / {total_pages}", font=font(13), fill=SUB)

    cols, gap = 2, 22
    card_w = (W - 40 * 2 - gap) // cols
    card_h = 170

    display_items = items[:8]
    for i, (name, desc, cls, price) in enumerate(display_items):
        col, row = i % cols, i // cols
        x, y = 40 + col * (card_w + gap), 108 + row * (card_h + gap)
        color = SHOP_CLASS_COLORS.get(cls, SHOP_CLASS_COLORS["sans"])

        d.rounded_rectangle((x, y, x + card_w, y + card_h), radius=10, fill=CARD)

        num = str(i + 1 + (page - 1) * 8)
        d.text((x + 20, y + card_h / 2 - 30), num, font=font(46, True), fill=(45, 45, 55, 255))

        d.text((x + 100, y + 18), name, font=font(18, True), fill=TEXT)
        d.text((x + 100, y + 50), desc, font=font(11), fill=SUB)
        d.text((x + 100, y + card_h - 34), price, font=font(16, True), fill=ACCENT)

        r = 34
        cx, cy = x + card_w - 46, y + card_h - 42
        d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=color)
        label = cls if cls != "sans" else "—"
        f_label = font(26, True)
        lw = text_w(d, label, f_label)
        d.text((cx - lw / 2, cy - 17), label, font=f_label, fill=(15, 15, 18, 255))

    # legende
    ly = H - 60
    d.text((40, ly), "Légende :", font=font(12, True), fill=TEXT)
    lx = 40 + 90
    for key, lbl in [("S", "Classe S"), ("1", "Classe 1"), ("2", "Classe 2"), ("3", "Classe 3"), ("4", "Classe 4"), ("sans", "Sans classe")]:
        c = SHOP_CLASS_COLORS[key]
        d.ellipse((lx, ly + 2, lx + 12, ly + 14), fill=c)
        d.text((lx + 18, ly), lbl, font=font(11), fill=TEXT)
        lx += 100

    img.save(out_path)
    return out_path


# math est déjà importé en haut du fichier.
def _profil_ring_gauge(d, cx, cy, r, pct, color, bg, width=8):
    d.ellipse((cx - r, cy - r, cx + r, cy + r), outline=bg, width=width)
    end = -90 + 360 * (pct / 100)
    d.arc((cx - r, cy - r, cx + r, cy + r), start=-90, end=end, fill=color, width=width)


def _profil_bar_gauge(d, x0, y0, x1, y1, pct, color, bg):
    d.rounded_rectangle((x0, y0, x1, y1), radius=(y1 - y0) // 2, fill=bg)
    w = (x1 - x0) * (pct / 100)
    if w > 4:
        d.rounded_rectangle((x0, y0, x0 + w, y1), radius=(y1 - y0) // 2, fill=color)


def _profil_frame(d, xy, gold):
    d.rounded_rectangle(xy, radius=14, outline=gold, width=3)


def generate_profil_image(name, pv, eo, level, xp, stats, maitrises, clan, rang, victoires, defaites, nuls,
                          out_path, portrait_path=None, background_path=None):
    """
    pv, eo, xp : tuples (valeur_actuelle, valeur_max)
    stats : liste de 3 tuples (nom, niveau, pourcentage, (xp_actuel, xp_max)) → Force, Vitesse, Défense dans cet ordre
    maitrises : liste de 4 tuples (nom, niveau, pourcentage[, is_max]) → Maîtrise EO, Maîtrise Sort, Maîtrise Territoire, RCT dans cet ordre. is_max (optionnel) → affiche « MAX » au lieu du pourcentage.
    clan, rang : chaînes
    victoires, defaites, nuls : entiers
    portrait_path : photo du personnage, découpée à la forme de l'hexagone (cadre en haut à droite).
                    Si None/absent : remplissage uni de l'hexagone (comportement d'origine).
    background_path : image de fond couvrant tout le canvas, floutée + assombrie pour la lisibilité.
                    Si None/absent : fond de couleur unie BG (comportement d'origine).
    """
    W, H = 1100, 900
    BG = (10, 9, 15, 255)
    TEXT = (235, 235, 240, 255)
    SUB = (150, 148, 160, 255)
    GOLD = (232, 197, 121, 255)
    XP_COLOR = (190, 100, 255, 255)
    HEADER_COLOR = (255, 200, 60, 255)
    CELL_BG = (22, 20, 28, 255)

    # Fond : image floutée + assombrie si fournie, sinon couleur unie (comme avant).
    if background_path and os.path.exists(background_path):
        try:
            bg = ImageOps.fit(Image.open(background_path).convert("RGB"), (W, H), method=Image.LANCZOS)
            bg = bg.filter(ImageFilter.GaussianBlur(radius=8)).convert("RGBA")
            # Calque noir semi transparent pour garder le texte lisible.
            img = Image.alpha_composite(bg, Image.new("RGBA", (W, H), (0, 0, 0, 140)))
        except Exception:
            img = Image.new("RGBA", (W, H), BG)
    else:
        img = Image.new("RGBA", (W, H), BG)
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, W, H), outline=GOLD, width=2)

    cx, cy, r = W - 110, 100, 70
    pts = [(cx + r * math.cos(math.radians(a)), cy + r * math.sin(math.radians(a))) for a in range(-90, 271, 60)]
    # Photo du personnage découpée à la forme de l'hexagone (masque), sinon remplissage uni.
    portrait_filled = False
    if portrait_path and os.path.exists(portrait_path):
        try:
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            x0, y0, x1, y1 = int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys))
            bw, bh = x1 - x0, y1 - y0
            photo = ImageOps.fit(Image.open(portrait_path).convert("RGBA"), (bw, bh), method=Image.LANCZOS)
            hex_mask = Image.new("L", (bw, bh), 0)
            ImageDraw.Draw(hex_mask).polygon([(px - x0, py - y0) for px, py in pts], fill=255)
            img.paste(photo, (x0, y0), hex_mask)
            portrait_filled = True
        except Exception:
            portrait_filled = False
    if portrait_filled:
        d.polygon(pts, outline=GOLD, width=3)
    else:
        d.polygon(pts, outline=GOLD, width=3, fill=(20, 20, 28, 255))

    title = f"Profil de {name}"
    tw = text_w(d, title, font(30, True))
    d.text((W / 2 - tw / 2, 40), title, font=font(30, True), fill=GOLD)

    top_y = 200
    gap = 26
    half_w = (W - 40 * 2 - gap) // 2

    f1 = (40, top_y, 40 + half_w, top_y + 250)
    _profil_frame(d, f1, GOLD)
    x, y = f1[0] + 24, f1[1] + 22
    d.text((x, y), "PV", font=font(13, True), fill=(230, 70, 70, 255))
    pv_pct = pv[0] / pv[1] * 100
    _profil_bar_gauge(d, x, y + 22, f1[2] - 24, y + 34, pv_pct, (230, 70, 70, 255), (35, 33, 42, 255))
    d.text((x, y + 40), f"{pv[0]:,} / {pv[1]:,}".replace(",", " "), font=font(11), fill=SUB)

    y += 74
    d.text((x, y), "ÉNERGIE OCCULTE", font=font(13, True), fill=(90, 160, 240, 255))
    eo_pct = eo[0] / eo[1] * 100
    _profil_bar_gauge(d, x, y + 22, f1[2] - 24, y + 34, eo_pct, (90, 160, 240, 255), (35, 33, 42, 255))
    d.text((x, y + 40), f"{eo[0]:,} / {eo[1]:,}".replace(",", " "), font=font(11), fill=SUB)

    y += 74
    ring_r = 30
    d.ellipse((x - ring_r + 30, y - ring_r + 30, x + ring_r + 30, y + ring_r + 30), outline=XP_COLOR, width=6)
    d.text((x + 30 - 10, y + 18), str(level), font=font(18, True), fill=TEXT)
    d.text((x + 74, y + 8), "LEVEL & XP", font=font(13, True), fill=HEADER_COLOR)
    d.text((x + 74, y + 26), f"{xp[0]:,} / {xp[1]:,} XP".replace(",", " "), font=font(11), fill=TEXT)
    xpbw = f1[2] - 24 - (x + 74)
    _profil_bar_gauge(d, x + 74, y + 46, x + 74 + xpbw, y + 54, xp[0] / xp[1] * 100, XP_COLOR, (35, 33, 42, 255))

    f2 = (40 + half_w + gap, top_y, W - 40, top_y + 250)
    _profil_frame(d, f2, GOLD)
    x, y = f2[0] + 24, f2[1] + 22
    d.text((x, y), "CLAN", font=font(12, True), fill=HEADER_COLOR)
    d.text((x, y + 18), clan, font=font(18, True), fill=TEXT)
    d.text((x + half_w / 2, y), "RANG", font=font(12, True), fill=HEADER_COLOR)
    d.text((x + half_w / 2, y + 18), rang, font=font(18, True), fill=GOLD)

    y += 66
    d.line((x, y, f2[2] - 24, y), fill=(55, 52, 65, 255), width=1)
    y += 20
    d.text((x, y), "COMBATS", font=font(12, True), fill=HEADER_COLOR)
    y += 26

    combat_data = [("VICTOIRES", victoires, (110, 220, 150, 255)), ("DÉFAITES", defaites, (230, 90, 90, 255)),
                   ("NULS", nuls, (190, 190, 198, 255)), ("TOTAL", victoires + defaites + nuls, GOLD)]
    cell_gap = 12
    cell_w = (f2[2] - 24 - x - cell_gap * 3) // 4
    for i, (label, val, col) in enumerate(combat_data):
        cxp = x + i * (cell_w + cell_gap)
        d.rounded_rectangle((cxp, y, cxp + cell_w, y + 76), radius=10, fill=CELL_BG, outline=col, width=1)
        lw = text_w(d, label, font(9, True))
        d.text((cxp + cell_w / 2 - lw / 2, y + 10), label, font=font(9, True), fill=SUB)
        vw = text_w(d, str(val), font(20, True))
        d.text((cxp + cell_w / 2 - vw / 2, y + 32), str(val), font=font(20, True), fill=col)

    bottom_y = top_y + 250 + gap
    frame_h = 340
    f3 = (40, bottom_y, 40 + half_w, bottom_y + frame_h)
    _profil_frame(d, f3, GOLD)
    x, y = f3[0] + 24, f3[1] + 22
    d.text((x, y), "STATISTIQUES DE COMBAT", font=font(13, True), fill=HEADER_COLOR)
    y += 50

    stat_colors = [(170, 100, 240, 255), (90, 200, 220, 255), (240, 150, 80, 255)]
    ring_r_stat = 38
    inner_w = (f3[2] - 24) - x
    seg_w = inner_w / 3
    row_y = y + ring_r_stat + 10
    for i, ((sname, slvl, spct, sxp), col) in enumerate(zip(stats, stat_colors)):
        px = x + seg_w * i + seg_w / 2
        _profil_ring_gauge(d, px, row_y, ring_r_stat, spct, col, (35, 33, 42, 255), width=7)
        pw = text_w(d, f"{spct}%", font(14, True))
        d.text((px - pw / 2, row_y - 9), f"{spct}%", font=font(14, True), fill=TEXT)
        nw = text_w(d, sname, font(12, True))
        d.text((px - nw / 2, row_y + ring_r_stat + 12), sname, font=font(12, True), fill=col)
        lvl_txt = f"Lvl {slvl}"
        # « MAX » quand le plafond de niveau est atteint (les courbes ne rendent xp_actuel == xp_max
        # QU'au plafond ; en dessous, xp_actuel < xp_max strictement).
        xp_txt = "MAX" if (sxp[1] and sxp[0] >= sxp[1]) else f"{sxp[0]}/{sxp[1]} XP"
        lw = text_w(d, lvl_txt, font(11, True))
        xw = text_w(d, xp_txt, font(11))
        d.text((px - lw / 2, row_y + ring_r_stat + 32), lvl_txt, font=font(11, True), fill=TEXT)
        d.text((px - xw / 2, row_y + ring_r_stat + 50), xp_txt, font=font(11), fill=SUB)

    f4 = (40 + half_w + gap, bottom_y, W - 40, bottom_y + frame_h)
    _profil_frame(d, f4, GOLD)
    x, y = f4[0] + 24, f4[1] + 22
    d.text((x, y), "MAÎTRISES", font=font(13, True), fill=HEADER_COLOR)

    maitrise_colors = [(90, 160, 240, 255), (170, 100, 240, 255), (100, 220, 150, 255), (240, 110, 130, 255)]
    ring_r_m = 46
    positions = [(f4[0] + half_w * 0.28, y + 74), (f4[0] + half_w * 0.72, y + 74),
                 (f4[0] + half_w * 0.28, y + 208), (f4[0] + half_w * 0.72, y + 208)]
    for md, (px, py), col in zip(maitrises, positions, maitrise_colors):
        mname, mlvl, mpct = md[:3]
        m_is_max = md[3] if len(md) > 3 else False  # 4e élément optionnel (rétro-compatible)
        _profil_ring_gauge(d, px, py, ring_r_m, mpct, col, (35, 33, 42, 255), width=8)
        lvl_txt = f"Lv{mlvl}"
        pct_txt = "MAX" if m_is_max else f"{mpct}%"
        lw = text_w(d, lvl_txt, font(15, True))
        d.text((px - lw / 2, py - 20), lvl_txt, font=font(15, True), fill=TEXT)
        pw = text_w(d, pct_txt, font(11))
        d.text((px - pw / 2, py + 4), pct_txt, font=font(11), fill=col)
        nw = text_w(d, mname, font(12, True))
        d.text((px - nw / 2, py + ring_r_m + 12), mname, font=font(12, True), fill=SUB)

    img.save(out_path)
    return out_path


BG_STATS = (10, 9, 15, 255)
TEXT_STATS = (235, 235, 240, 255)
SUB_STATS = (150, 148, 160, 255)
GOLD_STATS = (232, 197, 121, 255)
HEADER_COLOR_STATS = (255, 200, 60, 255)
TRANCHE_COLOR = (100, 225, 225, 255)
SEG_BG_STATS = (35, 33, 42, 255)


def _stats_frame(d, xy, gold, width=3, radius=14):
    d.rounded_rectangle(xy, radius=radius, outline=gold, width=width)


def _stats_seg_bar(d, x0, y0, x1, y1, pct, color, n_seg=20):
    d.rounded_rectangle((x0, y0, x1, y1), radius=4, fill=SEG_BG_STATS)
    total_w = x1 - x0
    seg_w = total_w / n_seg
    filled = int(n_seg * pct / 100)
    for i in range(n_seg):
        sx0 = x0 + i * seg_w + 1
        sx1 = x0 + (i + 1) * seg_w - 1
        if i < filled:
            d.rectangle((sx0, y0 + 1, sx1, y1 - 1), fill=color)
    d.rounded_rectangle((x0, y0, x1, y1), radius=4, outline=color, width=1)


# Reçoit directement l'objet Image (img) pour coller la photo : on évite d._image (attribut privé de
# ImageDraw pas garanti stable selon la version de Pillow).
def _stats_hexagon(d, img, cx, cy, r, gold, portrait_path=None):
    pts = [(cx + r * math.cos(math.radians(a)), cy + r * math.sin(math.radians(a))) for a in range(-90, 271, 60)]
    if portrait_path and os.path.exists(portrait_path):
        size = int(r * 1.6)
        photo = Image.open(portrait_path).convert("RGB")
        photo = ImageOps.fit(photo, (size, size), method=Image.LANCZOS)
        mask = Image.new("L", (size, size), 0)
        md = ImageDraw.Draw(mask)
        hex_local = [(size / 2 + r * math.cos(math.radians(a)), size / 2 + r * math.sin(math.radians(a))) for a in range(-90, 271, 60)]
        md.polygon(hex_local, fill=255)
        img.paste(photo, (int(cx - size / 2), int(cy - size / 2)), mask)
    else:
        d.polygon(pts, fill=(20, 20, 28, 255))
    d.polygon(pts, outline=gold, width=3)


def _stats_row(d, x, y, w, name, color, base, total, pct, tranche, show_tranche_text=True):
    d.text((x, y), name.upper(), font=font(14, True), fill=color)
    ptxt = f"{base:,} pts ({total:,})".replace(",", " ") if total != base else f"{base:,} pts".replace(",", " ")
    pw = text_w(d, ptxt, font(12, True))
    pctw = text_w(d, f"{pct}%", font(12, True))
    d.text((x + w - pw - pctw - 14, y), ptxt, font=font(12, True), fill=TEXT_STATS)
    d.text((x + w - pctw, y), f"{pct}%", font=font(12, True), fill=SUB_STATS)
    _stats_seg_bar(d, x, y + 22, x + w, y + 34, pct, color)
    # Ligne de texte sous la barre : tranche classique OU « X point(s) manquant(s) » / « MAX ». Masquée
    # quand show_tranche_text est False (ex: Armes maudites, barre pleine sans progression affichée).
    if show_tranche_text and tranche:
        d.text((x, y + 40), tranche, font=font(12, True), fill=TRANCHE_COLOR)


def _stats_buffs_frame(d, xy, gold, buffs):
    _stats_frame(d, xy, gold, width=3, radius=12)
    x0, y0, x1, y1 = xy
    title = "BUFFS ACTIFS"
    tw = text_w(d, title, font(13, True))
    d.text(((x0 + x1) / 2 - tw / 2, y0 + 14), title, font=font(13, True), fill=(230, 90, 90, 255))
    d.line((x0 + 20, y0 + 38, x1 - 20, y0 + 38), fill=(80, 40, 45, 255), width=1)
    yy = y0 + 52
    if not buffs:
        d.text((x0 + 20, yy), "Aucun buff actif.", font=font(11), fill=SUB_STATS)
    for b in buffs:
        d.text((x0 + 20, yy), b, font=font(11), fill=TEXT_STATS)
        yy += 22


def _stats_points_badge(d, x, y, w, h, gold, points_restants):
    _stats_frame(d, (x, y, x + w, y + h), gold, width=3, radius=10)
    txt = f"Points de stats restants : {points_restants}"
    tw = text_w(d, txt, font(13, True))
    d.text((x + w / 2 - tw / 2, y + h / 2 - 8), txt, font=font(13, True), fill=GOLD_STATS)


def generate_stats_image(name, stats, buffs, points_restants, out_path, portrait_path=None, background_path=None):
    """
    stats : liste de 8 tuples (nom, (r,g,b), base_pts, total_pts, pct, tranche_txt)
             ordre attendu : Force, RCT, Vitesse, Territoire, Endurance, Sorts, Armes maudites, Énergie occulte
    buffs : liste de chaines deja formatees, ex "Six Eyes  →  Force +200 · Vitesse +200"
    points_restants : entier
    portrait_path : chemin vers la photo du personnage, integree dans l'hexagone si fournie
    background_path : image de fond couvrant tout le canvas, floutée + assombrie. Si None/absent : fond uni.
    """
    W, H = 1100, 880
    # Fond : image floutée + assombrie si fournie, sinon couleur unie (comme avant).
    if background_path and os.path.exists(background_path):
        try:
            bg = ImageOps.fit(Image.open(background_path).convert("RGB"), (W, H), method=Image.LANCZOS)
            bg = bg.filter(ImageFilter.GaussianBlur(radius=8)).convert("RGBA")
            img = Image.alpha_composite(bg, Image.new("RGBA", (W, H), (0, 0, 0, 140)))
        except Exception:
            img = Image.new("RGBA", (W, H), BG_STATS)
    else:
        img = Image.new("RGBA", (W, H), BG_STATS)
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, W, H), outline=GOLD_STATS, width=2)

    _stats_hexagon(d, img, W // 2, 100, 70, GOLD_STATS, portrait_path)

    title = f"STATISTIQUES · {name}"
    tw = text_w(d, title, font(26, True))
    d.text((W / 2 - tw / 2, 190), title, font=font(26, True), fill=HEADER_COLOR_STATS)
    d.line((40, 230, W - 40, 230), fill=(55, 52, 65, 255), width=2)

    col_w = (W - 40 * 2 - 30) // 2
    y0 = 258
    for i, row_data in enumerate(stats[:8]):
        sname, color, base, total, pct, tranche = row_data[:6]
        show_tranche_text = row_data[6] if len(row_data) > 6 else True  # 7e élément optionnel (rétro-compatible)
        col, row = i % 2, i // 2
        x = 40 + col * (col_w + 30)
        y = y0 + row * 84
        _stats_row(d, x, y, col_w, sname, color, base, total, pct, tranche,
                   show_tranche_text=show_tranche_text)

    buffs_y = y0 + 4 * 84 + 16
    _stats_buffs_frame(d, (40, buffs_y, W - 40, H - 88), GOLD_STATS, buffs)
    _stats_points_badge(d, W - 320, H - 64, 280, 42, GOLD_STATS, points_restants)

    img.save(out_path)
    return out_path


REL_FAMILLE_COLOR = (230, 90, 90)
REL_AMIS_COLOR = (100, 200, 150)
REL_AUTRES_COLOR = (150, 120, 230)
REL_CATEGORIES_ORDER = [("Famille", REL_FAMILLE_COLOR), ("Amis", REL_AMIS_COLOR), ("Autres", REL_AUTRES_COLOR)]


def _rel_frame(d, xy, gold, width=3, radius=14):
    d.rounded_rectangle(xy, radius=radius, outline=gold, width=width)


def _rel_estimate_entry_height(d, person_name, max_card_w, f_name, f_label):
    """Hauteur (carte + marge) qu'occupera une entrée, selon si le nom tient sur 1 ou 2 lignes."""
    if text_w(d, person_name, f_name) > max_card_w - 24:
        return 58 + 14
    return 46 + 14


def _rel_build_columns(relations: dict, max_column_height: int, col_w: int) -> list:
    """
    Construit la liste des colonnes à afficher (chunks), dans l'ordre Famille -> Amis -> Autres.
    Chaque colonne déborde vers une NOUVELLE colonne de la MÊME catégorie si elle ne tient plus,
    plutôt que de s'étirer verticalement.
    Retourne une liste de dicts : {"category", "color", "is_continuation", "entries"}.
    """
    dummy_img = Image.new("RGBA", (10, 10))
    dd = ImageDraw.Draw(dummy_img)
    f_name = font(13, True)
    f_label = font(11)
    max_card_w = col_w - 40

    columns = []
    for cat, color in REL_CATEGORIES_ORDER:
        entries = relations.get(cat, [])
        if not entries:
            columns.append({"category": cat, "color": color, "is_continuation": False, "entries": []})
            continue
        current_chunk = []
        current_height = 0
        is_first = True
        for entry in entries:
            person_name = entry[0]
            entry_h = _rel_estimate_entry_height(dd, person_name, max_card_w, f_name, f_label)
            if current_chunk and current_height + entry_h > max_column_height:
                columns.append({"category": cat, "color": color, "is_continuation": not is_first, "entries": current_chunk})
                current_chunk = []
                current_height = 0
                is_first = False
            current_chunk.append(entry)
            current_height += entry_h
        columns.append({"category": cat, "color": color, "is_continuation": not is_first, "entries": current_chunk})
    return columns


def generate_relations_image(name: str, relations: dict, page: int, out_path: str, portrait_path=None, background_path=None):
    """
    relations : dict {"Famille": [(nom, lien), ...], "Amis": [...], "Autres": [...]}
    page : numéro de page à générer (1-indexé)
    background_path : image de fond couvrant tout le canvas, floutée + assombrie. Si None/absent : fond uni.
    Retourne (chemin_fichier, total_pages).
    """
    W, H = 1150, 780
    col_w = (W - 40 * 2 - 40) // 3
    max_column_height = H - 40 - 280  # espace vertical disponible dans une colonne

    all_columns = _rel_build_columns(relations, max_column_height, col_w)
    total_pages = max(1, (len(all_columns) + 2) // 3)
    page = max(1, min(page, total_pages))
    page_columns = all_columns[(page - 1) * 3: page * 3]

    BG_R = (10, 9, 15, 255)
    TEXT_R = (235, 235, 240, 255)
    HEADER_COLOR_R = (255, 200, 60, 255)
    gold = (232, 197, 121, 255)

    # Fond : image floutée + assombrie si fournie, sinon couleur unie (comme avant).
    if background_path and os.path.exists(background_path):
        try:
            bg = ImageOps.fit(Image.open(background_path).convert("RGB"), (W, H), method=Image.LANCZOS)
            bg = bg.filter(ImageFilter.GaussianBlur(radius=8)).convert("RGBA")
            img = Image.alpha_composite(bg, Image.new("RGBA", (W, H), (0, 0, 0, 140)))
        except Exception:
            img = Image.new("RGBA", (W, H), BG_R)
    else:
        img = Image.new("RGBA", (W, H), BG_R)
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, W, H), outline=gold, width=2)

    # Hexagone : contour doré + photo recadrée dedans si portrait_path fourni, sinon remplissage uni.
    # Un seul dessin cohérent, via _stats_hexagon (même principe que generate_stats_image).
    _stats_hexagon(d, img, W - 110, 90, 65, gold, portrait_path)

    title = f"RELATIONS DE {name.upper()}"
    d.text((40, 40), title, font=font(26, True), fill=HEADER_COLOR_R)
    if total_pages > 1:
        page_txt = f"Page {page} / {total_pages}"
        pw = text_w(d, page_txt, font(13))
        d.text((W - 40 - pw, 168), page_txt, font=font(13), fill=(150, 148, 160, 255))
    d.line((40, 190, W - 40, 190), fill=(55, 52, 65, 255), width=2)

    f_name = font(13, True)
    f_label = font(11)
    max_card_w = col_w - 40

    for i, col in enumerate(page_columns):
        x = 40 + i * (col_w + 20)
        color = col["color"]
        _rel_frame(d, (x, 220, x + col_w, H - 40), color, width=3, radius=12)
        cat_title = col["category"].upper() + (" (suite)" if col["is_continuation"] else "")
        cw = text_w(d, cat_title, font(15, True))
        d.text((x + col_w / 2 - cw / 2, 236), cat_title, font=font(15, True), fill=color)
        d.line((x + 20, 264, x + col_w - 20, 264), fill=(50, 47, 58, 255), width=1)

        yy = 280
        for person_name, lien in col["entries"]:
            content_w = max(text_w(d, person_name, f_name), text_w(d, lien, f_label))
            card_w = min(max_card_w, content_w + 24)
            if text_w(d, person_name, f_name) > max_card_w - 24:
                words = person_name.split()
                line1, line2 = "", ""
                for w in words:
                    trial = (line1 + " " + w).strip()
                    if text_w(d, trial, f_name) <= max_card_w - 24:
                        line1 = trial
                    else:
                        line2 = (line2 + " " + w).strip()
                card_h = 58
                cx0 = x + 20
                d.rounded_rectangle((cx0, yy, cx0 + max_card_w, yy + card_h), radius=8, fill=(22, 20, 28, 255))
                d.rectangle((cx0, yy, cx0 + 4, yy + card_h), fill=color)
                d.text((cx0 + 14, yy + 6), line1, font=f_name, fill=TEXT_R)
                d.text((cx0 + 14, yy + 22), line2, font=font(10, True), fill=TEXT_R)
                d.text((cx0 + 14, yy + 40), lien, font=f_label, fill=color)
            else:
                card_h = 46
                cx0 = x + 20
                d.rounded_rectangle((cx0, yy, cx0 + card_w, yy + card_h), radius=8, fill=(22, 20, 28, 255))
                d.rectangle((cx0, yy, cx0 + 4, yy + card_h), fill=color)
                d.text((cx0 + 14, yy + 8), person_name, font=f_name, fill=TEXT_R)
                d.text((cx0 + 14, yy + 26), lien, font=f_label, fill=color)
            yy += card_h + 14

    img.save(out_path)
    return out_path, total_pages


ORDRE_STATUS_COLORS = {
    "Acheté": (100, 220, 150),
    "Louée": (90, 150, 240),
    "Location": (200, 140, 240),
}

def _ordre_rounded(d, xy, r, **kw):
    d.rounded_rectangle(xy, radius=r, **kw)

def _ordre_member_row(d, x, y, w, label, count, color, text_color):
    d.rounded_rectangle((x, y, x + w, y + 40), radius=8, fill=(28, 28, 34, 255))
    d.rectangle((x, y, x + 4, y + 40), fill=color)
    d.text((x + 16, y + 11), label, font=font(12, True), fill=text_color)
    cw = text_w(d, str(count), font(14, True))
    d.text((x + w - 20 - cw, y + 9), str(count), font=font(14, True), fill=color)

def _ordre_line_chart(d, x0, y0, x1, y1, values, days, sub_color):
    w, h = x1 - x0, y1 - y0
    max_abs = max(abs(v) for v in values) * 1.2 if values else 1
    if max_abs == 0:
        max_abs = 1  # évite la division par zéro quand toutes les valeurs sont à 0 (ordre sans transaction)
    zero_y = y0 + h / 2
    d.line((x0, zero_y, x1, zero_y), fill=(40, 40, 48, 255), width=1)
    n = len(values)
    if n < 2:
        return
    step_x = w / (n - 1)
    pts = []
    for i, v in enumerate(values):
        px = x0 + i * step_x
        py = zero_y - (v / max_abs) * (h / 2)
        pts.append((px, py))
    for i in range(len(pts) - 1):
        x_a, y_a = pts[i]
        x_b, y_b = pts[i + 1]
        seg_color = (100, 220, 150, 255) if (values[i] + values[i + 1]) >= 0 else (230, 90, 90, 255)
        d.line((x_a, y_a, x_b, y_b), fill=seg_color, width=2)
    for (px, py), v in zip(pts, values):
        c = (100, 220, 150, 255) if v >= 0 else (230, 90, 90, 255)
        d.ellipse((px - 3, py - 3, px + 3, py + 3), fill=c)
    for i, day in enumerate(days):
        px = x0 + i * step_x
        dw = text_w(d, day, font(8))
        d.text((px - dw / 2, y1 + 4), day, font=font(8), fill=sub_color)


def generate_ordre_image(order_name: str, members: list, tresorerie: int, week_profit: list, week_days: list, salons: list, out_path: str):
    """
    members : liste de tuples (label, count, (r,g,b)) — n'inclut QUE les rôles réellement présents dans cet ordre
    week_profit : liste de 7 valeurs (int, peuvent être négatives)
    week_days : liste de 7 libellés courts, ex ["Lun","Mar",...]
    salons : liste de tuples (nom_salon, statut) — statut parmi "Acheté", "Louée", "Location"
    """
    W = 1300
    BG = (13, 13, 17, 255)
    CARD = (20, 20, 26, 255)
    TEXT = (235, 235, 240, 255)
    SUB = (150, 150, 160, 255)
    ACCENT = (255, 165, 60, 255)

    top_y = 100
    top_h = 320
    gap = 20

    # calcule la hauteur necessaire pour la zone salons AVANT de creer l'image (flux adaptatif)
    f_name = font(12, True)
    f_badge = font(10, True)
    card_h = 62
    gap_x = 14
    gap_y = 14
    zone_x0, zone_x1 = 60, W - 60

    dummy = Image.new("RGBA", (10, 10))
    dd = ImageDraw.Draw(dummy)
    cx = zone_x0
    rows = 1
    for salon_name, status in salons:
        name_txt = f"#{salon_name}"
        name_w = text_w(dd, name_txt, f_name)
        badge_w = text_w(dd, status, f_badge) + 20
        cw = max(name_w, badge_w) + 28
        if cx + cw > zone_x1:
            cx = zone_x0
            rows += 1
        cx += cw + gap_x
    salons_content_h = 66 + rows * (card_h + gap_y)

    salons_y0 = top_y + top_h + 30
    H = salons_y0 + salons_content_h + 30

    img = Image.new("RGBA", (W, H), BG)
    d = ImageDraw.Draw(img)

    d.text((40, 30), order_name, font=font(24, True), fill=ACCENT)
    d.line((40, 76, W - 40, 76), fill=(40, 40, 48, 255), width=1)

    col_w = (W - 40 * 2 - gap * 2) // 3

    # Effectifs
    x1_ = 40
    _ordre_rounded(d, (x1_, top_y, x1_ + col_w, top_y + top_h), 12, outline=ACCENT, width=1, fill=CARD)
    d.text((x1_ + 16, top_y + 14), "EFFECTIFS", font=font(13, True), fill=ACCENT)
    y = top_y + 46
    for label, count, color in members:
        _ordre_member_row(d, x1_ + 16, y, col_w - 32, label, count, color, TEXT)
        y += 46

    # Tresorerie
    x2_ = x1_ + col_w + gap
    _ordre_rounded(d, (x2_, top_y, x2_ + col_w, top_y + top_h), 12, outline=ACCENT, width=1, fill=CARD)
    d.text((x2_ + 16, top_y + 14), "TRÉSORERIE DE L'ORDRE", font=font(13, True), fill=ACCENT)
    solde_txt = f"{tresorerie:,} ¥".replace(",", " ")
    sw = text_w(d, solde_txt, font(30, True))
    solde_color = (120, 230, 170, 255) if tresorerie >= 0 else (230, 90, 90, 255)
    d.text((x2_ + col_w / 2 - sw / 2, top_y + top_h / 2 - 15), solde_txt, font=font(30, True), fill=solde_color)
    d.text((x2_ + 16, top_y + top_h - 30), "Solde actuel", font=font(10), fill=SUB)

    # Profit
    x3_ = x2_ + col_w + gap
    _ordre_rounded(d, (x3_, top_y, x3_ + col_w, top_y + top_h), 12, outline=ACCENT, width=1, fill=CARD)
    d.text((x3_ + 16, top_y + 14), "PROFIT DE LA SEMAINE", font=font(13, True), fill=ACCENT)
    _ordre_line_chart(d, x3_ + 24, top_y + 60, x3_ + col_w - 24, top_y + top_h - 40, week_profit, week_days, SUB)

    # Salons (flux adaptatif)
    _ordre_rounded(d, (40, salons_y0, W - 40, H - 40), 14, outline=ACCENT, width=1, fill=CARD)
    d.text((60, salons_y0 + 16), "SALONS", font=font(15, True), fill=ACCENT)
    d.line((60, salons_y0 + 46, W - 60, salons_y0 + 46), fill=(40, 40, 48, 255), width=1)

    if not salons:
        d.text((60, salons_y0 + 66), "Aucun salon pour l'instant.", font=font(12), fill=SUB)
    else:
        cx, cy = zone_x0, salons_y0 + 66
        for salon_name, status in salons:
            name_txt = f"#{salon_name}"
            status_color = ORDRE_STATUS_COLORS.get(status, (150, 150, 160))
            name_w = text_w(d, name_txt, f_name)
            badge_w = text_w(d, status, f_badge) + 20
            cw = max(name_w, badge_w) + 28

            if cx + cw > zone_x1:
                cx = zone_x0
                cy += card_h + gap_y

            d.rounded_rectangle((cx, cy, cx + cw, cy + card_h), radius=8, fill=(28, 28, 34, 255))
            d.rectangle((cx, cy, cx + 4, cy + card_h), fill=status_color)
            d.text((cx + 14, cy + 10), name_txt, font=f_name, fill=TEXT)
            d.rounded_rectangle((cx + 14, cy + 34, cx + 14 + badge_w, cy + 34 + 18), radius=8, fill=status_color)
            d.text((cx + 14 + 10, cy + 36), status, font=f_badge, fill=(15, 15, 18, 255))

            cx += cw + gap_x

    img.save(out_path)
    return out_path


def generate_ordre_educatif_image(order_name: str, members: list, ca_total: int, week_profit: list, week_days: list, out_path: str):
    """
    Variante ÉDUCATIF du dashboard : 3 cadres en haut (Effectifs / Chiffre d'affaire total des
    éducateurs à la place de Trésorerie / Profit de la semaine) et un cadre bas CONTRATS (placeholder
    tant que le système de contrats n'existe pas). Réutilise le même style que generate_ordre_image.
    members : liste de tuples (label, count, (r,g,b)).
    """
    W = 1300
    BG = (13, 13, 17, 255)
    CARD = (20, 20, 26, 255)
    TEXT = (235, 235, 240, 255)
    SUB = (150, 150, 160, 255)
    ACCENT = (255, 165, 60, 255)

    top_y = 100
    top_h = 320
    gap = 20

    contrats_y0 = top_y + top_h + 30
    contrats_h = 160
    H = contrats_y0 + contrats_h + 30

    img = Image.new("RGBA", (W, H), BG)
    d = ImageDraw.Draw(img)

    d.text((40, 30), order_name, font=font(24, True), fill=ACCENT)
    d.line((40, 76, W - 40, 76), fill=(40, 40, 48, 255), width=1)

    col_w = (W - 40 * 2 - gap * 2) // 3

    # Effectifs
    x1_ = 40
    _ordre_rounded(d, (x1_, top_y, x1_ + col_w, top_y + top_h), 12, outline=ACCENT, width=1, fill=CARD)
    d.text((x1_ + 16, top_y + 14), "EFFECTIFS", font=font(13, True), fill=ACCENT)
    y = top_y + 46
    for label, count, color in members:
        _ordre_member_row(d, x1_ + 16, y, col_w - 32, label, count, color, TEXT)
        y += 46

    # Chiffre d'affaire total des educateurs (a la place de la tresorerie)
    x2_ = x1_ + col_w + gap
    _ordre_rounded(d, (x2_, top_y, x2_ + col_w, top_y + top_h), 12, outline=ACCENT, width=1, fill=CARD)
    d.text((x2_ + 16, top_y + 14), "CHIFFRE D'AFFAIRE DES ÉDUCATEURS", font=font(11, True), fill=ACCENT)
    ca_txt = f"{ca_total:,} ¥".replace(",", " ")
    sw = text_w(d, ca_txt, font(30, True))
    ca_color = (120, 230, 170, 255) if ca_total >= 0 else (230, 90, 90, 255)
    d.text((x2_ + col_w / 2 - sw / 2, top_y + top_h / 2 - 15), ca_txt, font=font(30, True), fill=ca_color)
    d.text((x2_ + 16, top_y + top_h - 30), "Total reversé aux éducateurs", font=font(10), fill=SUB)

    # Profit
    x3_ = x2_ + col_w + gap
    _ordre_rounded(d, (x3_, top_y, x3_ + col_w, top_y + top_h), 12, outline=ACCENT, width=1, fill=CARD)
    d.text((x3_ + 16, top_y + 14), "PROFIT DE LA SEMAINE", font=font(13, True), fill=ACCENT)
    _ordre_line_chart(d, x3_ + 24, top_y + 60, x3_ + col_w - 24, top_y + top_h - 40, week_profit, week_days, SUB)

    # Contrats (placeholder)
    _ordre_rounded(d, (40, contrats_y0, W - 40, H - 40), 14, outline=ACCENT, width=1, fill=CARD)
    d.text((60, contrats_y0 + 16), "CONTRATS", font=font(15, True), fill=ACCENT)
    d.line((60, contrats_y0 + 46, W - 60, contrats_y0 + 46), fill=(40, 40, 48, 255), width=1)
    d.text((60, contrats_y0 + 70), "Aucun contrat pour l'instant (système à venir).", font=font(12), fill=SUB)

    img.save(out_path)
    return out_path


CONTRATS_BG = (13, 13, 17, 255)
CONTRATS_ACCENT = (255, 165, 60, 255)
CONTRATS_TEXT = (235, 235, 240, 255)
CONTRATS_SUB = (150, 150, 160, 255)
CONTRATS_CARD_COLOR = (100, 200, 220)
CONTRATS_MAX_PER_BOX = 15

def _contrats_frame(d, xy, gold, width=2, radius=12):
    d.rounded_rectangle(xy, radius=radius, outline=gold, width=width)

def _contrats_build_boxes(educateurs, max_per_box=CONTRATS_MAX_PER_BOX):
    """Decoupe chaque educateur en plusieurs boites de max_per_box disciples chacune (jamais plus)."""
    boxes = []
    for educ_name, disciples in educateurs:
        if not disciples:
            boxes.append({"educateur": educ_name, "is_continuation": False, "disciples": []})
            continue
        for i in range(0, len(disciples), max_per_box):
            chunk = disciples[i:i + max_per_box]
            boxes.append({"educateur": educ_name, "is_continuation": i > 0, "disciples": chunk})
    return boxes

def generate_contrats_educatifs_image(order_name: str, educateurs: list, page: int, out_path: str):
    """
    educateurs : liste de tuples (nom_educateur, [(nom_disciple, ordre_destination, revenu_str), ...])
                 Un éducateur SANS disciple doit quand même apparaître avec une case vide.
    page : page à générer (1-indexée)
    Retourne (chemin_fichier, total_pages)
    """
    W = 1300
    cols = 3
    col_gap = 24
    row_gap = 10
    box_w = (W - 40 * 2 - col_gap * (cols - 1)) // cols
    header_h = 46
    CARD_H = 58
    fixed_box_height = header_h + CONTRATS_MAX_PER_BOX * (CARD_H + row_gap) + 10

    f_educ = font(14, True)
    f_name = font(13, True)
    f_info = font(11)

    boxes = _contrats_build_boxes(educateurs)
    total_pages = max(1, (len(boxes) + cols - 1) // cols)
    page = max(1, min(page, total_pages))
    page_boxes = boxes[(page - 1) * cols: page * cols]

    H = 160 + fixed_box_height
    img = Image.new("RGBA", (W, H), CONTRATS_BG)
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, W, H), outline=CONTRATS_ACCENT, width=2)

    title = f"Contrats éducatifs — {order_name}"
    d.text((40, 30), title, font=font(24, True), fill=CONTRATS_ACCENT)
    if total_pages > 1:
        page_txt = f"Page {page} / {total_pages}"
        pw = text_w(d, page_txt, font(13))
        d.text((W - 40 - pw, 36), page_txt, font=font(13), fill=CONTRATS_SUB)
    d.line((40, 76, W - 40, 76), fill=(40, 40, 48, 255), width=2)

    y = 100
    for col, box in enumerate(page_boxes):
        x = 40 + col * (box_w + col_gap)
        _contrats_frame(d, (x, y, x + box_w, y + fixed_box_height), CONTRATS_ACCENT, width=2, radius=12)

        educ_title = box["educateur"] + (" (suite)" if box["is_continuation"] else "")
        et_w = text_w(d, educ_title, f_educ)
        d.text((x + box_w / 2 - et_w / 2, y + 12), educ_title, font=f_educ, fill=CONTRATS_ACCENT)
        d.line((x + 14, y + header_h - 6, x + box_w - 14, y + header_h - 6), fill=(45, 42, 50, 255), width=1)

        yy = y + header_h
        card_w = box_w - 24
        if not box["disciples"]:
            d.text((x + 14, yy + 4), "Aucun disciple.", font=f_info, fill=CONTRATS_SUB)
        for disciple_name, dest_ordre, revenu in box["disciples"]:
            cx0 = x + 12
            d.rounded_rectangle((cx0, yy, cx0 + card_w, yy + CARD_H), radius=8, fill=(22, 20, 28, 255))
            d.rectangle((cx0, yy, cx0 + 4, yy + CARD_H), fill=CONTRATS_CARD_COLOR)

            display_name = disciple_name
            max_text_w = card_w - 24
            if text_w(d, display_name, f_name) > max_text_w:
                while text_w(d, display_name + "...", f_name) > max_text_w and len(display_name) > 1:
                    display_name = display_name[:-1]
                display_name = display_name + "..."
            d.text((cx0 + 14, yy + 6), display_name, font=f_name, fill=CONTRATS_TEXT)

            info_txt = f"{revenu} · {dest_ordre}"
            display_info = info_txt
            if text_w(d, display_info, f_info) > max_text_w:
                while text_w(d, display_info + "...", f_info) > max_text_w and len(display_info) > 1:
                    display_info = display_info[:-1]
                display_info = display_info + "..."
            d.text((cx0 + 14, yy + 40), display_info, font=f_info, fill=CONTRATS_CARD_COLOR)

            yy += CARD_H + row_gap

    img.save(out_path)
    return out_path, total_pages


STAFF_BG = (13, 13, 17, 255)
STAFF_CARD = (20, 20, 26, 255)
STAFF_TEXT = (235, 235, 240, 255)
STAFF_SUB = (150, 150, 160, 255)
STAFF_ACCENT = (255, 165, 60, 255)

STAFF_ROLE_COLORS = {
    "Chef d'ordre": (255, 165, 60),
    "Sous-chef": (230, 90, 90),
    "Formateur": (100, 200, 150),
    "Chef d'équipe": (90, 150, 240),
    "Membre d'équipe": (170, 170, 180),
    "Corps administratif": (190, 100, 240),
}
STAFF_ROLE_ORDER = ["Chef d'ordre", "Sous-chef", "Formateur", "Chef d'équipe", "Membre d'équipe", "Corps administratif"]

STAFF_CARD_W = 220
STAFF_CARD_H = 46
STAFF_GAP_X = 14
STAFF_GAP_Y = 12
STAFF_MEMBERS_PER_PAGE = 24

def generate_staff_image(order_name: str, members: list, page: int, out_path: str):
    """
    members : liste de tuples (nom, role) — role doit être une clé de STAFF_ROLE_COLORS.
               Un rôle sans membre (0 personne) n'apparaît simplement pas dans le rendu.
    page : page à générer (1-indexée)
    Retourne (chemin_fichier, total_pages)
    """
    W = 1200
    cols = 4
    header_h = 34
    x0 = 40
    y_start = 96

    grouped = {role: [n for n, r in members if r == role] for role in STAFF_ROLE_ORDER}
    flat_items = []
    for role in STAFF_ROLE_ORDER:
        names = grouped[role]
        if not names:
            continue
        flat_items.append(("header", role, len(names)))
        for n in names:
            flat_items.append(("card", n, role))

    pages = []
    current = []
    card_count = 0
    for item in flat_items:
        if item[0] == "card" and card_count >= STAFF_MEMBERS_PER_PAGE:
            pages.append(current)
            current = []
            card_count = 0
        current.append(item)
        if item[0] == "card":
            card_count += 1
    if current:
        pages.append(current)

    total_pages = max(1, len(pages))
    page = max(1, min(page, total_pages))
    page_items = pages[page - 1] if pages else []

    def layout_pass(items):
        positions = []
        x, y = x0, y_start
        col_i = 0
        for item in items:
            if item[0] == "header":
                if col_i != 0:
                    y += STAFF_CARD_H + STAFF_GAP_Y
                x, col_i = x0, 0
                positions.append(("header", item[1], item[2], x, y))
                y += header_h
            else:
                if col_i >= cols:
                    col_i = 0
                    x = x0
                    y += STAFF_CARD_H + STAFF_GAP_Y
                positions.append(("card", item[1], item[2], x, y))
                x += STAFF_CARD_W + STAFF_GAP_X
                col_i += 1
        final_y = y + STAFF_CARD_H + 40
        return positions, final_y

    positions, H = layout_pass(page_items)

    img = Image.new("RGBA", (W, H), STAFF_BG)
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, W, H), outline=STAFF_ACCENT, width=2)
    d.text((40, 30), f"Staff — {order_name}", font=font(22, True), fill=STAFF_ACCENT)
    if total_pages > 1:
        page_txt = f"Page {page} / {total_pages}"
        pw = text_w(d, page_txt, font(12))
        d.text((W - 40 - pw, 36), page_txt, font=font(12), fill=STAFF_SUB)
    d.line((40, 74, W - 40, 74), fill=(40, 40, 48, 255), width=1)

    if not page_items:
        d.text((40, 96), "Aucun membre pour l'instant.", font=font(13), fill=STAFF_SUB)

    f_header = font(13, True)
    f_card = font(12, True)
    for kind, a, b, x, y in positions:
        if kind == "header":
            role, count = a, b
            color = STAFF_ROLE_COLORS.get(role, (150, 150, 160))
            d.text((x, y), f"{role.upper()}  ({count})", font=f_header, fill=color)
        else:
            name, role = a, b
            color = STAFF_ROLE_COLORS.get(role, (150, 150, 160))
            d.rounded_rectangle((x, y, x + STAFF_CARD_W, y + STAFF_CARD_H), radius=8, fill=STAFF_CARD)
            d.rectangle((x, y, x + 4, y + STAFF_CARD_H), fill=color)
            display = name
            max_text_w = STAFF_CARD_W - 24
            if text_w(d, display, f_card) > max_text_w:
                while text_w(d, display + "...", f_card) > max_text_w and len(display) > 1:
                    display = display[:-1]
                display = display + "..."
            d.text((x + 14, y + 14), display, font=f_card, fill=STAFF_TEXT)

    img.save(out_path)
    return out_path, total_pages


CDIR_BG = (13, 13, 17, 255)
CDIR_CARD = (20, 20, 26, 255)
CDIR_TEXT = (235, 235, 240, 255)
CDIR_SUB = (150, 150, 160, 255)
CDIR_ACCENT = (255, 165, 60, 255)
CDIR_ORDER_COLORS_CYCLE = [
    (255, 165, 60), (90, 150, 240), (100, 200, 150), (230, 90, 90), (190, 100, 240), (100, 220, 220),
]
CDIR_CARD_W = 240
CDIR_CARD_H = 50
CDIR_GAP_X = 14
CDIR_GAP_Y = 12
CDIR_PER_PAGE = 24

def generate_contrats_direct_image(order_name: str, contrats: list, page: int, out_path: str):
    """
    contrats : liste de tuples (nom_disciple, ordre_origine, educateur, montant_str)
               nom_disciple : le membre employé dans CET ordre (order_name), venant d'un ordre éducatif
               ordre_origine : le nom de l'ordre éducatif où il a été formé
               educateur : le nom de l'éducateur précis qui touche le %
               montant_str : déjà formaté, ex "45 000 ¥"
    page : page à générer (1-indexée)
    Retourne (chemin_fichier, total_pages)
    """
    W = 1200
    cols = 4
    header_h = 34
    x0 = 40
    y_start = 96

    orders_seen = []
    grouped = {}
    for nom, ordre_origine, educateur, montant in contrats:
        if ordre_origine not in grouped:
            grouped[ordre_origine] = []
            orders_seen.append(ordre_origine)
        grouped[ordre_origine].append((nom, educateur, montant))

    order_colors = {o: CDIR_ORDER_COLORS_CYCLE[i % len(CDIR_ORDER_COLORS_CYCLE)] for i, o in enumerate(orders_seen)}

    flat_items = []
    for ordre_origine in orders_seen:
        entries = grouped[ordre_origine]
        flat_items.append(("header", ordre_origine, len(entries)))
        for nom, educateur, montant in entries:
            flat_items.append(("card", nom, (educateur, montant, ordre_origine)))

    pages = []
    current = []
    card_count = 0
    for item in flat_items:
        if item[0] == "card" and card_count >= CDIR_PER_PAGE:
            pages.append(current)
            current = []
            card_count = 0
        current.append(item)
        if item[0] == "card":
            card_count += 1
    if current:
        pages.append(current)

    total_pages = max(1, len(pages))
    page = max(1, min(page, total_pages))
    page_items = pages[page - 1] if pages else []

    def layout_pass(items):
        positions = []
        x, y = x0, y_start
        col_i = 0
        for item in items:
            if item[0] == "header":
                if col_i != 0:
                    y += CDIR_CARD_H + CDIR_GAP_Y
                x, col_i = x0, 0
                positions.append(("header", item[1], item[2], x, y))
                y += header_h
            else:
                if col_i >= cols:
                    col_i = 0
                    x = x0
                    y += CDIR_CARD_H + CDIR_GAP_Y
                positions.append(("card", item[1], item[2], x, y))
                x += CDIR_CARD_W + CDIR_GAP_X
                col_i += 1
        final_y = y + CDIR_CARD_H + 40
        return positions, final_y

    positions, H = layout_pass(page_items)

    img = Image.new("RGBA", (W, H), CDIR_BG)
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, W, H), outline=CDIR_ACCENT, width=2)
    d.text((40, 30), f"Contrats — {order_name}", font=font(22, True), fill=CDIR_ACCENT)
    if total_pages > 1:
        page_txt = f"Page {page} / {total_pages}"
        pw = text_w(d, page_txt, font(12))
        d.text((W - 40 - pw, 36), page_txt, font=font(12), fill=CDIR_SUB)
    d.line((40, 74, W - 40, 74), fill=(40, 40, 48, 255), width=1)

    if not page_items:
        d.text((40, 96), "Aucun contrat pour l'instant.", font=font(13), fill=CDIR_SUB)

    for kind, a, b, x, y in positions:
        if kind == "header":
            ordre_origine, count = a, b
            color = order_colors[ordre_origine]
            d.text((x, y), f"{ordre_origine.upper()} ({count})", font=font(13, True), fill=color)
        else:
            nom = a
            educateur, montant, ordre_origine = b
            color = order_colors[ordre_origine]
            d.rounded_rectangle((x, y, x + CDIR_CARD_W, y + CDIR_CARD_H), radius=8, fill=CDIR_CARD)
            d.rectangle((x, y, x + 4, y + CDIR_CARD_H), fill=color)

            display_name = nom
            max_text_w = CDIR_CARD_W - 24
            if text_w(d, display_name, font(11, True)) > max_text_w:
                while text_w(d, display_name + "...", font(11, True)) > max_text_w and len(display_name) > 1:
                    display_name = display_name[:-1]
                display_name = display_name + "..."
            d.text((x + 14, y + 8), display_name, font=font(11, True), fill=CDIR_TEXT)

            info_txt = f"{educateur} · {montant}"
            display_info = info_txt
            if text_w(d, display_info, font(9)) > max_text_w:
                while text_w(d, display_info + "...", font(9)) > max_text_w and len(display_info) > 1:
                    display_info = display_info[:-1]
                display_info = display_info + "..."
            d.text((x + 14, y + 28), display_info, font=font(9), fill=color)

    img.save(out_path)
    return out_path, total_pages


def _tresorerie_ordre_grad_h(size, c1, c2):
    w, h = size
    card = Image.new("RGBA", (w, h), (0, 0, 0, 255))
    cd = ImageDraw.Draw(card)
    for x in range(w):
        t = x / w
        r = int(c1[0] * (1 - t) + c2[0] * t)
        g = int(c1[1] * (1 - t) + c2[1] * t)
        b = int(c1[2] * (1 - t) + c2[2] * t)
        cd.line([(x, 0), (x, h)], fill=(r, g, b, 255))
    return card

def generate_tresorerie_ordre_image(order_name, solde, nb_salons, taxe_par_salon, salaires_total, transactions, out_path):
    W, H = 1200, 650
    BG = (16, 15, 26, 255)
    TEXT = (245, 245, 250, 255)
    SUB = (170, 165, 190, 255)
    POS = (140, 255, 190, 255)
    NEG = (255, 140, 160, 255)
    LABEL = (255, 255, 255, 255)
    LINE = (50, 47, 65, 255)

    img = Image.new("RGBA", (W, H), BG)
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, W, 74), fill=(22, 20, 34, 255))
    d.text((40, 22), "Banque de l'Ordre", font=font(18, True), fill=(255, 255, 255, 255))
    name_w = text_w(d, order_name, font(14, True))
    d.text((W - 40 - name_w, 27), order_name, font=font(14, True), fill=TEXT)

    card_w = W - 80
    card = _tresorerie_ordre_grad_h((card_w, 150), (255, 90, 160), (130, 80, 255))
    mask = Image.new("L", (card_w, 150), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, card_w - 1, 149), radius=20, fill=255)
    img.paste(card, (40, 100), mask)
    d.text((64, 122), "SOLDE ACTUEL", font=font(15, True), fill=LABEL)
    d.text((64, 156), f"{solde:,} ¥".replace(",", " "), font=font(38, True), fill=(255, 255, 255, 255))

    mini_y = 270
    mini_h = 90
    mini_w = (W - 80 - 20) // 2

    taxe_totale = nb_salons * taxe_par_salon
    d.rounded_rectangle((40, mini_y, 40 + mini_w, mini_y + mini_h), radius=16, fill=(28, 26, 40, 255))
    d.text((60, mini_y + 14), "TAXES DE SALON / SEMAINE", font=font(11, True), fill=SUB)
    d.text((60, mini_y + 36), f"-{taxe_totale:,} ¥".replace(",", " "), font=font(22, True), fill=NEG)
    d.text((60, mini_y + 66), f"{nb_salons} salon(s) × {taxe_par_salon:,} ¥".replace(",", " "), font=font(10), fill=SUB)

    rx = 40 + mini_w + 20
    d.rounded_rectangle((rx, mini_y, rx + mini_w, mini_y + mini_h), radius=16, fill=(28, 26, 40, 255))
    d.text((rx + 20, mini_y + 14), "SALAIRES / SEMAINE", font=font(11, True), fill=SUB)
    if salaires_total is None:
        d.text((rx + 20, mini_y + 36), "À venir", font=font(18, True), fill=SUB)
    else:
        d.text((rx + 20, mini_y + 36), f"-{salaires_total:,} ¥".replace(",", " "), font=font(22, True), fill=NEG)

    d.text((40, 388), "Transactions récentes", font=font(16, True), fill=TEXT)
    d.rounded_rectangle((40, 420, W - 40, H - 30), radius=20, fill=(28, 26, 40, 255))
    y = 442
    display_transactions = transactions[:4]
    for i, (label, date, amount, pos) in enumerate(display_transactions):
        d.text((64, y), label, font=font(13, True), fill=TEXT)
        d.text((64, y + 20), date, font=font(11), fill=SUB)
        aw = text_w(d, amount, font(14, True))
        d.text((W - 64 - aw, y + 8), amount, font=font(14, True), fill=POS if pos else NEG)
        if i != len(display_transactions) - 1:
            d.line((64, y + 50, W - 64, y + 50), fill=LINE, width=1)
        y += 60
    if not display_transactions:
        d.text((64, 450), "Aucune transaction pour l'instant.", font=font(13), fill=SUB)

    img.save(out_path)
    return out_path


SALORD_BG = (13, 13, 17, 255)
SALORD_CARD = (20, 20, 26, 255)
SALORD_TEXT = (235, 235, 240, 255)
SALORD_SUB = (150, 150, 160, 255)
SALORD_ACCENT = (255, 165, 60, 255)
SALORD_STATUS_COLORS = {
    "Acheté": (100, 220, 150),
    "Louée": (90, 150, 240),
    "Location": (200, 140, 240),
}
SALORD_STATUS_ORDER = ["Acheté", "Louée", "Location"]
SALORD_CARD_W = 220
SALORD_CARD_H = 46
SALORD_GAP_X = 14
SALORD_GAP_Y = 12
SALORD_PER_PAGE = 24

def generate_salons_ordre_image(order_name: str, salons: list, page: int, out_path: str):
    """
    salons : liste de tuples (nom_salon, statut) — statut parmi "Acheté", "Louée", "Location"
    page : page à générer (1-indexée)
    Retourne (chemin_fichier, total_pages)
    """
    W = 1200
    cols = 4
    header_h = 34
    x0 = 40
    y_start = 96

    grouped = {status: [n for n, s in salons if s == status] for status in SALORD_STATUS_ORDER}
    flat_items = []
    for status in SALORD_STATUS_ORDER:
        names = grouped[status]
        if not names:
            continue
        flat_items.append(("header", status, len(names)))
        for n in names:
            flat_items.append(("card", n, status))

    pages = []
    current = []
    card_count = 0
    for item in flat_items:
        if item[0] == "card" and card_count >= SALORD_PER_PAGE:
            pages.append(current)
            current = []
            card_count = 0
        current.append(item)
        if item[0] == "card":
            card_count += 1
    if current:
        pages.append(current)

    total_pages = max(1, len(pages))
    page = max(1, min(page, total_pages))
    page_items = pages[page - 1] if pages else []

    def layout_pass(items):
        positions = []
        x, y = x0, y_start
        col_i = 0
        for item in items:
            if item[0] == "header":
                if col_i != 0:
                    y += SALORD_CARD_H + SALORD_GAP_Y
                x, col_i = x0, 0
                positions.append(("header", item[1], item[2], x, y))
                y += header_h
            else:
                if col_i >= cols:
                    col_i = 0
                    x = x0
                    y += SALORD_CARD_H + SALORD_GAP_Y
                positions.append(("card", item[1], item[2], x, y))
                x += SALORD_CARD_W + SALORD_GAP_X
                col_i += 1
        final_y = y + SALORD_CARD_H + 40
        return positions, final_y

    positions, H = layout_pass(page_items)

    img = Image.new("RGBA", (W, H), SALORD_BG)
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, W, H), outline=SALORD_ACCENT, width=2)
    d.text((40, 30), f"Salons — {order_name}", font=font(22, True), fill=SALORD_ACCENT)
    if total_pages > 1:
        page_txt = f"Page {page} / {total_pages}"
        pw = text_w(d, page_txt, font(12))
        d.text((W - 40 - pw, 36), page_txt, font=font(12), fill=SALORD_SUB)
    d.line((40, 74, W - 40, 74), fill=(40, 40, 48, 255), width=1)

    if not page_items:
        d.text((40, 96), "Aucun salon pour l'instant.", font=font(13), fill=SALORD_SUB)

    f_header = font(13, True)
    f_card = font(12, True)
    for kind, a, b, x, y in positions:
        if kind == "header":
            status, count = a, b
            color = SALORD_STATUS_COLORS.get(status, (150, 150, 160))
            d.text((x, y), f"{status.upper()}  ({count})", font=f_header, fill=color)
        else:
            name, status = a, b
            color = SALORD_STATUS_COLORS.get(status, (150, 150, 160))
            d.rounded_rectangle((x, y, x + SALORD_CARD_W, y + SALORD_CARD_H), radius=8, fill=SALORD_CARD)
            d.rectangle((x, y, x + 4, y + SALORD_CARD_H), fill=color)
            display = f"#{name}"
            max_text_w = SALORD_CARD_W - 24
            if text_w(d, display, f_card) > max_text_w:
                while text_w(d, display + "...", f_card) > max_text_w and len(display) > 1:
                    display = display[:-1]
                display = display + "..."
            d.text((x + 14, y + 14), display, font=f_card, fill=SALORD_TEXT)

    img.save(out_path)
    return out_path, total_pages


# =====================================================================
# TECHNIQUES OCCULTES (/profil → bouton ⚡ Technique)
# =====================================================================
TECH_BG = (10, 9, 15, 255)
TECH_TEXT = (235, 235, 240, 255)
TECH_SUB = (150, 148, 160, 255)
TECH_GOLD = (232, 197, 121, 255)
TECH_HEADER_COLOR = (255, 200, 60, 255)
TECH_LOCKED_COLOR = (70, 68, 80)

def _tech_frame(d, xy, gold, width=3, radius=14):
    d.rounded_rectangle(xy, radius=radius, outline=gold, width=width)

def _tech_hex_gauge(d, cx, cy, r, pct, color, bg, width=9):
    pts = [(cx + r * math.cos(math.radians(a)), cy + r * math.sin(math.radians(a))) for a in range(-90, 271, 60)]
    for i in range(6):
        d.line((pts[i], pts[(i + 1) % 6]), fill=bg, width=width)
    edge_len = 2 * r * math.sin(math.radians(30))
    total_dist = pct / 100 * (6 * edge_len)
    remaining = total_dist
    for i in range(6):
        if remaining <= 0:
            break
        p0, p1 = pts[i], pts[(i + 1) % 6]
        seg = min(edge_len, remaining)
        t = seg / edge_len
        mx = p0[0] + (p1[0] - p0[0]) * t
        my = p0[1] + (p1[1] - p0[1]) * t
        d.line((p0, (mx, my)), fill=color, width=width)
        remaining -= edge_len

def _tech_sort_card(d, x, y, w, h, name, level, color, locked=False):
    _tech_frame(d, (x, y, x + w, y + h), color, width=3, radius=12)
    if locked:
        qw = text_w(d, "???", font(20, True))
        d.text((x + w / 2 - qw / 2, y + h / 2 - 30), "???", font=font(20, True), fill=TECH_SUB)
        lock_txt = "Verrouillé"
        lw = text_w(d, lock_txt, font(11))
        d.rounded_rectangle((x + w / 2 - lw / 2 - 10, y + h - 40, x + w / 2 + lw / 2 + 10, y + h - 16), radius=8, outline=TECH_SUB, width=1)
        d.text((x + w / 2 - lw / 2, y + h - 35), lock_txt, font=font(11), fill=TECH_SUB)
        return
    d.text((x + 18, y + 16), name, font=font(16, True), fill=color)
    badge_txt = f"Lv{level}"
    bw = text_w(d, badge_txt, font(12, True))
    d.rounded_rectangle((x + w - bw - 26, y + 14, x + w - 14, y + 34), radius=10, fill=color)
    d.text((x + w - bw - 20, y + 17), badge_txt, font=font(12, True), fill=(15, 15, 18, 255))
    d.text((x + 18, y + h - 30), "Sorts : actifs", font=font(10), fill=TECH_SUB)

def _tech_maitrise_hex(d, cx, cy, name, level, xp_cur, xp_max, color):
    r = 62
    if level is None:
        _tech_hex_gauge(d, cx, cy, r, 0, TECH_LOCKED_COLOR, (60, 58, 70, 255), width=3)
        qw = text_w(d, "???", font(22, True))
        d.text((cx - qw / 2, cy - 14), "???", font=font(22, True), fill=TECH_SUB)
        return
    pct = (xp_cur / xp_max) * 100 if xp_max else 0
    _tech_hex_gauge(d, cx, cy, r, pct, color, (35, 33, 42, 255), width=9)
    lvl_txt = f"Lv{level}"
    lw = text_w(d, lvl_txt, font(24, True))
    d.text((cx - lw / 2, cy - 28), lvl_txt, font=font(24, True), fill=color)
    # « MAX » au plafond (ex: Phase 2, niveau 100) : xp_cur == xp_max uniquement au sommet.
    xp_txt = "MAX" if (xp_max and xp_cur >= xp_max) else f"{xp_cur}/{xp_max}"
    xw = text_w(d, xp_txt, font(11))
    d.text((cx - xw / 2, cy + 6), xp_txt, font=font(11), fill=TECH_SUB)
    nw = text_w(d, name, font(14, True))
    d.text((cx - nw / 2, cy + r + 14), name, font=font(14, True), fill=TECH_TEXT)

def _tech_maximum_box(d, xy, gold, names=None):
    x0, y0, x1, y1 = xy
    _tech_frame(d, xy, gold, width=3, radius=14)
    title = "TECHNIQUE MAXIMUM"
    tw = text_w(d, title, font(16, True))
    d.text(((x0 + x1) / 2 - tw / 2, y0 + 14), title, font=font(16, True), fill=gold)
    # Liste des sorts principaux promus « Technique Maximum » (une ligne centrée par sort, en TECH_GOLD).
    # Vide -> on garde uniquement le titre, comme avant.
    if names:
        yy = y0 + 52
        for nm in names:
            nw = text_w(d, nm, font(15, True))
            d.text(((x0 + x1) / 2 - nw / 2, yy), nm, font=font(15, True), fill=gold)
            yy += 30


def generate_technique_image(name: str, camp: str, sorts: list, out_path: str, portrait_path=None,
                             background_path=None, technique_maximum_list=None):
    """
    sorts : liste de jusqu'à 4 tuples (nom, niveau, couleur_rgb, xp_actuel, xp_max).
            Un slot verrouillé/vide est représenté par (None, None, None, None, None).
    technique_maximum_list : noms des sorts principaux ayant atteint la Technique Maximum (affichés dans
            l'encadré du bas). None/vide = aucun.
    """
    W, H = 1400, 950
    if background_path and os.path.exists(background_path):
        try:
            bg = ImageOps.fit(Image.open(background_path).convert("RGB"), (W, H), method=Image.LANCZOS)
            bg = bg.filter(ImageFilter.GaussianBlur(radius=8)).convert("RGBA")
            img = Image.alpha_composite(bg, Image.new("RGBA", (W, H), (0, 0, 0, 140)))
        except Exception:
            img = Image.new("RGBA", (W, H), TECH_BG)
    else:
        img = Image.new("RGBA", (W, H), TECH_BG)
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, W, H), outline=TECH_GOLD, width=2)

    cx, cy, r = W - 110, 90, 60
    pts = [(cx + r * math.cos(math.radians(a)), cy + r * math.sin(math.radians(a))) for a in range(-90, 271, 60)]
    portrait_filled = False
    if portrait_path and os.path.exists(portrait_path):
        try:
            xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
            x0, y0, x1, y1 = int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys))
            bw, bh = x1 - x0, y1 - y0
            photo = ImageOps.fit(Image.open(portrait_path).convert("RGBA"), (bw, bh), method=Image.LANCZOS)
            hex_mask = Image.new("L", (bw, bh), 0)
            ImageDraw.Draw(hex_mask).polygon([(px - x0, py - y0) for px, py in pts], fill=255)
            img.paste(photo, (x0, y0), hex_mask)
            portrait_filled = True
        except Exception:
            portrait_filled = False
    if portrait_filled:
        d.polygon(pts, outline=TECH_GOLD, width=3)
    else:
        d.polygon(pts, outline=TECH_GOLD, width=3, fill=(20, 20, 28, 255))

    title = "TECHNIQUES OCCULTES"
    d.text((40, 30), title, font=font(26, True), fill=TECH_HEADER_COLOR)
    sub = f"{name}  ·  {camp}"
    d.text((40, 64), sub, font=font(13, True), fill=TECH_GOLD)
    d.line((40, 150, W - 40, 150), fill=(55, 52, 65, 255), width=2)

    d.text((40, 168), "GRANDES CATÉGORIES", font=font(13, True), fill=TECH_HEADER_COLOR)
    gap = 20
    card_w = (W - 40 * 2 - gap * 3) // 4
    y0 = 198
    padded_sorts = (sorts + [(None, None, None, None, None)] * 4)[:4]
    for i, (sname, lvl, color, xp_cur, xp_max) in enumerate(padded_sorts):
        x = 40 + i * (card_w + gap)
        _tech_sort_card(d, x, y0, card_w, 150, sname, lvl, color if color else TECH_LOCKED_COLOR, locked=(sname is None))

    my = y0 + 190
    d.text((40, my), "MAÎTRISE", font=font(13, True), fill=TECH_HEADER_COLOR)
    for i, (sname, lvl, color, xp_cur, xp_max) in enumerate(padded_sorts):
        px = 40 + card_w / 2 + i * (card_w + gap)
        _tech_maitrise_hex(d, px, my + 120, sname, lvl, xp_cur, xp_max, color if color else TECH_LOCKED_COLOR)

    tm_y = my + 250
    _tech_maximum_box(d, (40, tm_y, W - 40, H - 40), TECH_GOLD, technique_maximum_list)

    img.save(out_path)
    return out_path


# =====================================================================
# TECHNIQUE — VUE DÉTAILLÉE D'UN SORT PRINCIPAL (ses 8 sorts secondaires)
# =====================================================================
TECHDET_BG = (10, 9, 15, 255)
TECHDET_TEXT = (235, 235, 240, 255)
TECHDET_SUB = (150, 148, 160, 255)
TECHDET_GOLD = (232, 197, 121, 255)
TECHDET_HEADER_COLOR = (255, 200, 60, 255)

TECHDET_CLASS_COLORS = {
    "S": (255, 165, 0),
    "1": (235, 60, 100),
    "2": (170, 80, 240),
    "3": (60, 130, 240),
    "4": (40, 200, 150),
}
TECHDET_CLASS_LABELS = {"S": "Ultime", "1": "Avancé", "2": "Normal", "3": "Passif"}

def _techdet_hexagon(d, cx, cy, r, gold, fill=(20, 20, 28, 255)):
    pts = [(cx + r * math.cos(math.radians(a)), cy + r * math.sin(math.radians(a))) for a in range(-90, 271, 60)]
    d.polygon(pts, outline=gold, width=3, fill=fill)

def _techdet_class_badge(d, x, y, classe, size=30):
    color = TECHDET_CLASS_COLORS.get(classe, (100, 100, 110))
    d.ellipse((x, y, x + size, y + size), fill=color)
    label = classe if classe else "?"
    f = font(13, True)
    lw = text_w(d, label, f)
    d.text((x + size / 2 - lw / 2, y + size / 2 - 8), label, font=f, fill=(15, 15, 18, 255))

def _techdet_secondary_card(d, x, y, w, h, name, classe, niveau_requis, debloque, principal_level):
    locked = not debloque or (principal_level < niveau_requis)
    color = TECHDET_CLASS_COLORS.get(classe, (90, 88, 100)) if not locked else (70, 68, 80)
    d.rounded_rectangle((x, y, x + w, y + h), radius=10, fill=(22, 20, 28, 255))
    d.rectangle((x, y, x + 5, y + h), fill=color)

    if locked:
        txt = f"??? (niveau requis {niveau_requis})"
        tw = text_w(d, txt, font(13, True))
        d.text((x + w / 2 - tw / 2, y + h / 2 - 9), txt, font=font(13, True), fill=TECHDET_SUB)
        return

    _techdet_class_badge(d, x + w - 46, y + 16, classe, size=30)
    display_name = name
    max_w = w - 70
    if text_w(d, display_name, font(13, True)) > max_w:
        while text_w(d, display_name + "...", font(13, True)) > max_w and len(display_name) > 1:
            display_name = display_name[:-1]
        display_name = display_name + "..."
    d.text((x + 18, y + 18), display_name, font=font(13, True), fill=TECHDET_TEXT)
    d.text((x + 18, y + 44), f"Niveau requis : {niveau_requis}", font=font(13), fill=TECHDET_SUB)
    d.text((x + 18, y + h - 56), "Coût EO : —", font=font(13), fill=TECHDET_SUB)
    d.text((x + 18, y + h - 30), "Dégâts : —", font=font(13), fill=TECHDET_SUB)


def generate_technique_detail_image(sort_principal_name: str, sort_principal_level: int, sort_principal_color: tuple,
                                     sorts_secondaires: list, out_path: str, background_path=None):
    """
    sorts_secondaires : liste de jusqu'à 8 tuples (nom, classe, niveau_requis, debloque)
                         classe est une chaîne parmi "S", "1", "2", "3", "4", ou None si le slot est vide.
                         nom est None si le slot n'a pas encore de sort assigné (vide, même déverrouillable).
    """
    W, H = 1300, 1300
    if background_path and os.path.exists(background_path):
        try:
            bg = ImageOps.fit(Image.open(background_path).convert("RGB"), (W, H), method=Image.LANCZOS)
            bg = bg.filter(ImageFilter.GaussianBlur(radius=8)).convert("RGBA")
            img = Image.alpha_composite(bg, Image.new("RGBA", (W, H), (0, 0, 0, 140)))
        except Exception:
            img = Image.new("RGBA", (W, H), TECHDET_BG)
    else:
        img = Image.new("RGBA", (W, H), TECHDET_BG)
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, W, H), outline=TECHDET_GOLD, width=2)
    d.rectangle((0, 0, 280, H), fill=(16, 15, 20, 255))

    _techdet_hexagon(d, 140, 100, 60, TECHDET_GOLD)
    tw = text_w(d, sort_principal_name, font(18, True))
    d.text((140 - tw / 2, 176), sort_principal_name, font=font(18, True), fill=sort_principal_color)
    lvl_txt = f"Niveau {sort_principal_level}"
    lw = text_w(d, lvl_txt, font(13, True))
    d.text((140 - lw / 2, 202), lvl_txt, font=font(13, True), fill=TECHDET_GOLD)
    d.line((24, 234, 256, 234), fill=(45, 42, 52, 255), width=1)
    d.text((24, 254), "LÉGENDE", font=font(13, True), fill=TECHDET_HEADER_COLOR)
    yy = 282
    for cl, label in [("S", "Ultime"), ("1", "Avancé"), ("2", "Normal"), ("3", "Passif")]:
        _techdet_class_badge(d, 24, yy, cl, size=26)
        d.text((60, yy + 5), label, font=font(13), fill=TECHDET_TEXT)
        yy += 40

    d.text((300, 30), "SORTS SECONDAIRES", font=font(20, True), fill=TECHDET_HEADER_COLOR)
    d.line((300, 70, W - 40, 70), fill=(55, 52, 65, 255), width=2)
    col_w = (W - 300 - 40 - 20) // 2
    row_h = 150
    gap = 20
    y0 = 90
    padded = (sorts_secondaires + [(None, None, 999, False)] * 8)[:8]
    for i, (name, classe, niveau, debloque) in enumerate(padded):
        col, row = i % 2, i // 2
        x = 300 + col * (col_w + gap)
        y = y0 + row * (row_h + gap)
        _techdet_secondary_card(d, x, y, col_w, row_h, name, classe, niveau, debloque, sort_principal_level)

    img.save(out_path)
    return out_path
