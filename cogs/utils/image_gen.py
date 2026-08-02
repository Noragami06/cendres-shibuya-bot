from PIL import Image, ImageDraw, ImageFont, ImageOps
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
