"""Synthetic M2 client screenshots for testing name reading.

Draws nameplates the way the game does (Tahoma 11 px with a 1 px dark
outline) over a procedural terrain background, in five frames with moving
clutter, so charname.read_character_name can be tested without real
screenshots or real character names.

Colours are the game client's own (colorInfo.py): title colours per rank
tier and name colours per relation (own empire, party, PvP...).
"""
import random

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

WIDTH, HEIGHT = 800, 600
FRAMES = 5

# Rank tiers from best to worst, with a sample title and the title colour.
TIERS = [
    ("chivalric", "Cavaler", (0, 204, 255)),
    ("noble", "Nobil", (0, 144, 255)),
    ("good", "Bun", (92, 110, 255)),
    ("friendly", "Prietenos", (155, 155, 255)),
    ("neutral", "Neutru", (255, 255, 255)),
    ("aggressive", "Agresiv", (207, 117, 0)),
    ("fraudulent", "Fraudulos", (235, 22, 9)),
    ("malicious", "Malitios", (227, 0, 0)),
    ("cruel", "Crud", (255, 0, 0)),
]
# Extra words for titles longer than one word, as some languages have.
TITLE_EXTRA = ["Foarte", "Sehr", "Molto"]
NAME_COLORS = [(255, 215, 76), (128, 192, 255), (238, 54, 223), (255, 255, 255)]
LEVEL_COLORS = [(144, 240, 48), (0, 96, 240), (255, 215, 76), (255, 255, 255)]
GUILD_COLOR = (224, 208, 240)
MONSTER_COLOR = (235, 22, 9)
OUTLINE = (0, 0, 0)

SYLLABLES = ["ka", "ri", "mon", "zel", "tor", "ve", "dra", "lin", "sha", "gor",
             "fen", "xa", "bel", "nyx", "qua", "rho", "sil", "vor", "wyn", "jax"]


def _font():
    try:
        return ImageFont.truetype("tahoma.ttf", 11)
    except OSError:  # not on Windows
        return ImageFont.load_default()


FONT = _font()


def random_name(rng):
    """A fictional, name-like string of 3-16 characters."""
    name = "".join(rng.choice(SYLLABLES) for _ in range(rng.randint(1, 4)))
    name = name[:14].capitalize()
    if rng.random() < 0.3:
        name += str(rng.randint(0, 99))
    if rng.random() < 0.3:
        name = name[:1] + name[1:].replace(name[1], name[1].upper(), 1)
    return name if len(name) >= 3 else name + "zor"


def background(rng):
    """Terrain-like noise: smooth colour patches plus fine grain."""
    base = Image.new("RGB", (WIDTH // 40, HEIGHT // 40))
    palette = [(52, 64, 38), (70, 62, 44), (40, 52, 46), (82, 74, 52), (58, 48, 40)]
    base.putdata([tuple(max(0, min(255, c + rng.randint(-12, 12))) for c in rng.choice(palette))
                  for _ in range(base.width * base.height)])
    img = base.resize((WIDTH, HEIGHT), Image.BICUBIC)
    # Grain from the same seed as everything else, so tests are repeatable.
    noise = np.random.default_rng(rng.randrange(2 ** 32)).normal(128, 18, (HEIGHT, WIDTH))
    grain = Image.fromarray(noise.clip(0, 255).astype(np.uint8)).convert("RGB")
    return Image.blend(img, grain, 0.12).filter(ImageFilter.GaussianBlur(0.6))


def _text(draw, xy, text, fill):
    draw.fontmode = "1"  # the game draws hard-edged pixel text, not anti-aliased
    x, y = xy
    # The game's outline is the text drawn dark one pixel left, right, above
    # and below, which is thinner than a full stroke.
    for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
        draw.text((x + dx, y + dy), text, font=FONT, fill=OUTLINE)
    draw.text((x, y), text, font=FONT, fill=fill)


def draw_plate(draw, centre_x, y, level, title, name, *, guild=None,
               level_color=LEVEL_COLORS[0], title_color=(255, 255, 255),
               name_color=NAME_COLORS[0]):
    """Draw "Lv <level> <title> <name>" with the name centred on centre_x,
    and the guild line above it if given."""
    space = draw.textlength(" ", font=FONT) + 2  # the game spaces words wider
    name_w = draw.textlength(name, font=FONT)
    x = centre_x - name_w / 2
    _text(draw, (x, y), name, name_color)
    if title:
        x -= space + draw.textlength(title, font=FONT)
        _text(draw, (x, y), title, title_color)
    lv = f"Lv {level}"
    x -= space + draw.textlength(lv, font=FONT)
    _text(draw, (x, y), lv, level_color)
    if guild:
        guild_w = 16 + 4 + draw.textlength(guild, font=FONT)
        gx = centre_x - guild_w / 2
        draw.rectangle((gx, y - 13, gx + 15, y - 2), fill=(240, 150, 40), outline=OUTLINE)
        _text(draw, (gx + 20, y - 15), guild, GUILD_COLOR)


def scene(rng, *, name, tier, title_words=1, guild=None, clutter=False, base=None,
          plate_y=None):
    """Return FRAMES screenshots of one client, oldest first.

    `tier` is an index into TIERS; `title_words` is 0-3 (0 = no title).
    `base` is an optional background image to use instead of noise.
    """
    _, title, title_color = TIERS[tier]
    if title_words == 0:
        title = ""
    elif title_words > 1:
        title = " ".join(TITLE_EXTRA[:title_words - 1] + [title])
    plate_y = plate_y if plate_y is not None else rng.uniform(0.40, 0.46) * HEIGHT
    centre_x = WIDTH / 2 + rng.uniform(-3, 3)
    level = rng.randint(1, 120)
    level_color = rng.choice(LEVEL_COLORS)
    name_color = rng.choice(NAME_COLORS)
    bg = (base or background(rng)).convert("RGB").resize((WIDTH, HEIGHT))

    others = []
    if clutter:
        for _ in range(rng.randint(2, 4)):  # monsters near the character
            others.append(("monster", rng.uniform(150, 650), rng.uniform(170, 320)))
        dx = rng.choice([-1, 1]) * rng.uniform(25, 60)
        dy = rng.choice([-1, 1]) * rng.uniform(18, 40)
        others.append(("player", centre_x + dx, plate_y + dy))

    frames = []
    for f in range(FRAMES):
        img = bg.copy()
        draw = ImageDraw.Draw(img)
        for kind, ox, oy in others:
            if kind == "monster":  # monsters wander a little between frames
                mx, my = ox + rng.uniform(-6, 6) * f, oy + rng.uniform(-4, 4) * f
                draw_plate(draw, mx, my, rng.randint(90, 110), "",
                           rng.choice(["Soldat*", "Druid*", "Razbonic*"]),
                           level_color=LEVEL_COLORS[0], name_color=MONSTER_COLOR)
            else:
                draw_plate(draw, ox, oy, 98, "Bun", "Otherguy", title_color=TIERS[2][2],
                           name_color=NAME_COLORS[0])
        draw_plate(draw, centre_x, plate_y, level, title, name, guild=guild,
                   level_color=level_color, title_color=title_color, name_color=name_color)
        if clutter:  # damage numbers float up over the character
            for k in range(3):
                _text(draw, (centre_x + rng.uniform(-40, 40), plate_y - 10 * f - 20 * k),
                      str(rng.randint(100, 9999)), (255, 230, 60))
        frames.append(img)
    return frames
