"""Compose the social/share banner PNG from mcu-agent.png + designed background."""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parents[1]
ICON = ROOT / "site" / "images" / "mcu-agent.png"
OUT = ROOT / "images" / "banner.png"
OUT.parent.mkdir(parents=True, exist_ok=True)

W, H = 1280, 640


def vgrad(size, top, bottom):
    img = Image.new("RGB", size, top)
    px = img.load()
    w, h = size
    for y in range(h):
        t = y / max(h - 1, 1)
        r = int(top[0] + (bottom[0] - top[0]) * t)
        g = int(top[1] + (bottom[1] - top[1]) * t)
        b = int(top[2] + (bottom[2] - top[2]) * t)
        for x in range(w):
            px[x, y] = (r, g, b)
    return img


def hgrad(size, left, right):
    img = Image.new("RGBA", size, left + (255,))
    px = img.load()
    w, h = size
    for x in range(w):
        t = x / max(w - 1, 1)
        r = int(left[0] + (right[0] - left[0]) * t)
        g = int(left[1] + (right[1] - left[1]) * t)
        b = int(left[2] + (right[2] - left[2]) * t)
        for y in range(h):
            px[x, y] = (r, g, b, 255)
    return img


def find_font(size, bold=False):
    candidates = [
        "C:/Windows/Fonts/segoeuib.ttf" if bold else "C:/Windows/Fonts/segoeui.ttf",
        "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf",
    ]
    for p in candidates:
        if Path(p).exists():
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def rounded_rect_outline(draw, box, radius, color, width=2):
    draw.rounded_rectangle(box, radius=radius, outline=color, width=width)


def main():
    # Background: diagonal-ish gradient via two passes (vertical + violet glow)
    bg = vgrad((W, H), top=(15, 23, 42), bottom=(76, 29, 149))  # slate-900 -> violet-900

    # Subtle grid overlay
    grid = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(grid)
    for x in range(0, W, 40):
        gd.line([(x, 0), (x, H)], fill=(255, 255, 255, 13))
    for y in range(0, H, 40):
        gd.line([(0, y), (W, y)], fill=(255, 255, 255, 13))
    bg = Image.alpha_composite(bg.convert("RGBA"), grid)

    # Glow behind icon
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gdr = ImageDraw.Draw(glow)
    gdr.ellipse([720, 50, 1340, 670], fill=(167, 139, 250, 110))
    glow = glow.filter(ImageFilter.GaussianBlur(60))
    bg = Image.alpha_composite(bg, glow)

    # Agent icon panel (right side)
    panel_box = (850, 120, 1210, 480)  # 360x360
    panel = Image.new("RGBA", (360, 360), (255, 255, 255, 18))
    # Round the panel corners
    mask = Image.new("L", (360, 360), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, 360, 360), radius=36, fill=255)
    panel.putalpha(mask)
    bg.alpha_composite(panel, (panel_box[0], panel_box[1]))

    # Place icon, fit/cover into panel with rounded mask
    icon = Image.open(ICON).convert("RGBA")
    iw, ih = icon.size
    scale = max(360 / iw, 360 / ih)
    icon_resized = icon.resize((int(iw * scale), int(ih * scale)), Image.LANCZOS)
    # Center crop to 360x360
    cx = (icon_resized.width - 360) // 2
    cy = (icon_resized.height - 360) // 2
    icon_crop = icon_resized.crop((cx, cy, cx + 360, cy + 360))
    icon_masked = Image.new("RGBA", (360, 360), (0, 0, 0, 0))
    icon_masked.paste(icon_crop, (0, 0), mask=mask)
    bg.alpha_composite(icon_masked, (panel_box[0], panel_box[1]))

    # Accent border around icon (gradient stroke approximated with solid violet)
    bd = ImageDraw.Draw(bg)
    bd.rounded_rectangle(panel_box, radius=36, outline=(167, 139, 250, 220), width=3)

    # Text
    f_huge = find_font(96, bold=True)
    f_sub = find_font(46, bold=True)
    f_tag = find_font(26, bold=True)
    f_pill = find_font(20, bold=True)
    f_foot = find_font(16)

    bd.text((80, 130), "MCU", font=f_huge, fill=(255, 255, 255))
    bd.text((80, 240), "mc-multimodal-agent", font=f_sub, fill=(226, 232, 240))

    # Tagline (gradient text), wrapped so it never collides with the icon panel
    tagline = "A Minecraft multimodal agent,\npackaged for AgentBeats / Amber."
    max_tag_w = 820 - 80  # icon panel begins at x=850
    # auto-shrink to fit if needed
    tag_size = 26
    while tag_size > 16:
        f_tag = find_font(tag_size, bold=True)
        if all(bd.textlength(line, font=f_tag) <= max_tag_w for line in tagline.split("\n")):
            break
        tag_size -= 1
    tag_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(tag_layer).multiline_text(
        (80, 310), tagline, font=f_tag, fill=(255, 255, 255, 255), spacing=6
    )
    grad = hgrad((W, H), left=(167, 139, 250), right=(34, 211, 238))
    grad.putalpha(tag_layer.split()[-1])
    bg.alpha_composite(grad)

    # Feature pills
    pills = [
        ("Amber Manifest", (167, 139, 250)),
        ("A2A Protocol", (34, 211, 238)),
        ("Dockerized", (96, 165, 250)),
        ("CI / GHCR", (244, 114, 182)),
    ]
    x = 80
    y = 410
    pad_x = 22
    pill_h = 44
    for label, border in pills:
        tw = bd.textlength(label, font=f_pill)
        pw = int(tw + pad_x * 2)
        # darker translucent fill so light text reads
        bd.rounded_rectangle((x, y, x + pw, y + pill_h), radius=22, fill=(15, 23, 42, 170))
        rounded_rect_outline(bd, (x, y, x + pw, y + pill_h), 22, border + (255,), width=2)
        bd.text((x + pad_x, y + 10), label, font=f_pill, fill=(255, 255, 255, 255))
        x += pw + 18

    # Footer line
    bd.text(
        (80, 540),
        "ghcr.io/madgaa-lab/mcu-mc-multimodal-agent  ·  port 9009  ·  Apache 2.0",
        font=f_foot,
        fill=(148, 163, 184),
    )

    # Bottom accent bar (gradient)
    bar = hgrad((W, 16), left=(167, 139, 250), right=(34, 211, 238))
    bg.alpha_composite(bar, (0, H - 16))

    bg.convert("RGB").save(OUT, "PNG", optimize=True)
    print(f"wrote {OUT} ({OUT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
