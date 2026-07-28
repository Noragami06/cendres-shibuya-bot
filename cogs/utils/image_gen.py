from PIL import Image, ImageDraw, ImageFont
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
    val_text = f"{value} EO"
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
