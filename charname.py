"""Read the player's own character name from a M2 client screenshot.

The camera keeps your own character in the middle of the screen, with a
nameplate such as "Lv 105 Cavaler PlayerName" above its head. The name itself
is centred horizontally on the screen; the level and title stick out to its
left, and the guild name, if any, sits centred just above it.

Reading works in two passes with Windows' built-in OCR:
1. OCR a band around the screen centre and pick the centred word nearest the
   usual nameplate height that looks like a nameplate (a level marker to its
   left or a guild line above).
2. OCR that word again on its own, enlarged further, which separates it from
   the title and from other nameplates that touch it.

A single screenshot can still be wrong when another nameplate overlaps
yours, so callers should vote over several reads.
"""
import asyncio
import re

from PIL import Image
from winrt.windows.graphics.imaging import BitmapAlphaMode, BitmapPixelFormat, SoftwareBitmap
from winrt.windows.media.ocr import OcrEngine
from winrt.windows.storage.streams import DataWriter

# Search band, as fractions of the client size.
CROP_LEFT, CROP_TOP, CROP_RIGHT, CROP_BOTTOM = 0.34, 0.25, 0.66, 0.55
# A name's centre is at most this far from mid-screen, as a fraction of width.
CENTER_TOLERANCE = 0.04
# Typical height of the own nameplate, as a fraction of client height. Camera
# zoom moves it a little; other players below or above sit further away.
NAMEPLATE_Y = 0.45
# Vertical distance between the guild line and the name line, in pixels at
# a 600 pixel high client.
GUILD_GAP_MIN, GUILD_GAP_MAX = 8, 25
SEARCH_UPSCALE = 3
WORD_UPSCALE = 6
WORD_PADDING = 6

_LEVEL = re.compile(r"L[vV](?![A-Za-z])")
_NAME = re.compile(r"[A-Za-z][A-Za-z0-9]{2,15}")

_engine = OcrEngine.try_create_from_user_profile_languages()


def _to_software_bitmap(image):
    rgba = image.convert("RGBA")
    r, g, b, a = rgba.split()
    bgra = Image.merge("RGBA", (b, g, r, a)).tobytes()
    writer = DataWriter()
    writer.write_bytes(bgra)
    return SoftwareBitmap.create_copy_from_buffer(
        writer.detach_buffer(), BitmapPixelFormat.BGRA8,
        image.width, image.height, BitmapAlphaMode.PREMULTIPLIED,
    )


async def _recognize(image):
    return await _engine.recognize_async(_to_software_bitmap(image))


def _upscale(image, factor):
    return image.resize((image.width * factor, image.height * factor), Image.LANCZOS)


def ocr_words(image, origin=(0, 0), scale=1):
    """Return (word, line_text, (left, top, right, bottom)) for each OCR word.

    Boxes are mapped back through `scale` and `origin`, so they are in the
    coordinates of the image `image` was cropped and scaled from.
    """
    result = asyncio.run(_recognize(image))
    words = []
    for line in result.lines:
        for word in line.words:
            rect = word.bounding_rect
            left = origin[0] + rect.x / scale
            top = origin[1] + rect.y / scale
            words.append((word.text, line.text, (
                left, top, left + rect.width / scale, top + rect.height / scale)))
    return words


def _center(box):
    left, top, right, bottom = box
    return (left + right) / 2, (top + bottom) / 2


def _find_name_word(screenshot):
    """Return the box of the own character's name word, or None."""
    width, height = screenshot.size
    band = (int(width * CROP_LEFT), int(height * CROP_TOP),
            int(width * CROP_RIGHT), int(height * CROP_BOTTOM))
    words = ocr_words(_upscale(screenshot.crop(band), SEARCH_UPSCALE),
                      origin=band[:2], scale=SEARCH_UPSCALE)

    centred = [
        (text, line_text, box) for text, line_text, box in words
        if abs(_center(box)[0] - width / 2) <= width * CENTER_TOLERANCE
        and "*" not in text  # monster names end in "*"
        and len(re.sub(r"\W", "", text)) >= 3
    ]
    gap_min = GUILD_GAP_MIN * height / 600
    gap_max = GUILD_GAP_MAX * height / 600

    def is_nameplate(word):
        text, line_text, box = word
        if _LEVEL.search(line_text.split(text)[0]):
            return True
        # A guild line above; damage numbers floating above don't count.
        y = _center(box)[1]
        return any(gap_min <= y - _center(other[2])[1] <= gap_max
                   and len(re.sub(r"[^A-Za-z]", "", other[0])) >= 3
                   for other in centred if other is not word)

    nameplates = [word for word in centred if is_nameplate(word)]
    if not nameplates:
        return None
    return min(nameplates,
               key=lambda word: abs(_center(word[2])[1] - height * NAMEPLATE_Y))[2]


def read_character_name(screenshot):
    """Return the character name in a client screenshot, or None."""
    if screenshot is None or _engine is None:
        return None
    box = _find_name_word(screenshot)
    if box is None:
        return None

    left, top, right, bottom = box
    word = screenshot.crop((int(left) - WORD_PADDING, int(top) - 4,
                            int(right) + WORD_PADDING + 1, int(bottom) + 5))
    words = ocr_words(_upscale(word, WORD_UPSCALE), scale=WORD_UPSCALE)
    if not words:
        return None
    # Neighbouring text can leak into the padding; keep the middle word.
    middle = (word.width / 2, word.height / 2)
    text = min(words, key=lambda w: (_center(w[2])[0] - middle[0]) ** 2
               + (_center(w[2])[1] - middle[1]) ** 2)[0]
    text = re.sub(r"[^A-Za-z0-9]", "", text)
    return text if _NAME.fullmatch(text) else None
