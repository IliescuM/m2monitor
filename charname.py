"""Read the player's own character name from M2 client screenshots.

The camera keeps your own character in the middle of the screen, with a
nameplate such as "Lv 105 Cavaler PlayerName" above its head. The name itself
is centred horizontally on the screen; the level and title stick out to its
left, and the guild name, if any, sits centred just above it.

Reading works on the last few screenshots of a client:
1. Merge them with a per-pixel median around the screen centre. The
   nameplate stays put on screen while monsters, damage numbers and spell
   effects move, so the median keeps the name and fades the clutter.
2. OCR that area with Windows' built-in OCR and pick the centred word with a
   level marker ("Lv") just before it, nearest the usual nameplate height.
   The guild line above has no level marker, so it is never picked.
3. OCR that word again on its own, enlarged, once in colour and once as
   high-contrast greyscale. The name counts only when both readings agree,
   which rejects nearly all misreads.

A read can still come back empty when the name is covered, so callers should
read repeatedly and keep the last agreed name.
"""
import asyncio
import os
import re

# See capture.py: keep numpy from reserving OpenBLAS buffers per CPU thread.
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")

import numpy as np  # noqa: E402
from PIL import Image, ImageChops, ImageOps  # noqa: E402
from winrt.windows.graphics.imaging import (  # noqa: E402
    BitmapAlphaMode, BitmapPixelFormat, SoftwareBitmap)
from winrt.windows.media.ocr import OcrEngine  # noqa: E402
from winrt.windows.storage.streams import DataWriter  # noqa: E402

# Search band, as fractions of the client size.
CROP_LEFT, CROP_TOP, CROP_RIGHT, CROP_BOTTOM = 0.34, 0.25, 0.66, 0.55
# A name's centre is at most this far from mid-screen, as a fraction of width.
# The own name measures within 1%; other players standing right below or
# above the character are often within 4%.
CENTER_TOLERANCE = 0.02
# Typical height of the own nameplate, as a fraction of client height. Camera
# zoom moves it a little; other players below or above sit further away.
NAMEPLATE_Y = 0.45
# How many words before a name may hold its "Lv" marker: "Lv", the level and
# a title of up to two words.
PREFIX_WORDS = 4
# Words of one nameplate are about one word height apart (measured 0.4-1.04);
# separate nameplates on one OCR line are much further apart.
WORD_GAP = 1.5
SEARCH_UPSCALE = 3
WORD_UPSCALE = 6
WORD_PADDING = 6

_LEVEL = re.compile(r"L[vV](?![A-Za-z])")
_NAME = re.compile(r"[A-Za-z][A-Za-z0-9]{2,15}")

_engine = OcrEngine.try_create_from_user_profile_languages()


def _to_software_bitmap(image):
    bgra = image.convert("RGBA").tobytes("raw", "BGRA")
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


def _high_contrast(image):
    """Brightest channel, stretched and inverted: dark text on white."""
    r, g, b = image.split()
    brightest = ImageChops.lighter(ImageChops.lighter(r, g), b)
    return ImageOps.invert(ImageOps.autocontrast(brightest, cutoff=1)).convert("RGB")


def ocr_words(image, origin=(0, 0), scale=1):
    """Return (word, prefix, box, gap_after) for each OCR word.

    `prefix` is the text of up to PREFIX_WORDS words before it on the same
    line, `box` is (left, top, right, bottom), and `gap_after` is the space
    to the next word on the line, or None for the last word. Boxes and gaps
    are mapped back through `scale` and `origin`, so they are in the
    coordinates of the image `image` was cropped and scaled from.
    """
    result = asyncio.run(_recognize(image))
    words = []
    for line in result.lines:
        line_words = list(line.words)
        for index, word in enumerate(line_words):
            prefix = " ".join(w.text for w in line_words[max(0, index - PREFIX_WORDS):index])
            rect = word.bounding_rect
            left = origin[0] + rect.x / scale
            top = origin[1] + rect.y / scale
            right = left + rect.width / scale
            gap_after = None
            if index + 1 < len(line_words):
                gap_after = origin[0] + line_words[index + 1].bounding_rect.x / scale - right
            words.append((word.text, prefix,
                          (left, top, right, top + rect.height / scale), gap_after))
    return words


def _center(box):
    left, top, right, bottom = box
    return (left + right) / 2, (top + bottom) / 2


def _search_band(size):
    width, height = size
    return (int(width * CROP_LEFT), int(height * CROP_TOP),
            int(width * CROP_RIGHT), int(height * CROP_BOTTOM))


def _merge(screenshots):
    """Return the newest screenshot with its search band replaced by the
    per-pixel median of all screenshots of the same size."""
    newest = screenshots[-1]
    same = [s for s in screenshots if s.size == newest.size]
    if len(same) < 3:
        return newest
    left, top, right, bottom = _search_band(newest.size)
    margin = WORD_PADDING + 4  # the second pass crops slightly past the band
    box = (max(left - margin, 0), max(top - margin, 0),
           min(right + margin, newest.width), min(bottom + margin, newest.height))
    stack = np.stack([np.asarray(s.crop(box).convert("RGB")) for s in same])
    merged = newest.convert("RGB")  # a copy, so the caller's image is untouched
    merged.paste(Image.fromarray(np.median(stack, axis=0).astype(np.uint8)), box[:2])
    return merged


def _find_name_word(screenshot):
    """Return the box of the own character's name word, or None."""
    width, height = screenshot.size
    band = _search_band(screenshot.size)
    words = ocr_words(_upscale(screenshot.crop(band), SEARCH_UPSCALE),
                      origin=band[:2], scale=SEARCH_UPSCALE)

    def last_on_plate(box, gap_after):
        # A name ends its nameplate. A word followed at normal word spacing is
        # a title or part of a monster's name ("Lv 97 Lemures Soldat*").
        return gap_after is None or gap_after > (box[3] - box[1]) * WORD_GAP

    # "Lv" right before the name, past at most a level and a title. Only
    # nearby words count: OCR can merge a neighbour's nameplate into the line.
    nameplates = [
        box for text, prefix, box, gap_after in words
        if abs(_center(box)[0] - width / 2) <= width * CENTER_TOLERANCE
        and "*" not in text  # monster names end in "*"
        and len(re.sub(r"\W", "", text)) >= 3
        and _LEVEL.search(prefix)
        and last_on_plate(box, gap_after)
    ]
    if not nameplates:
        return None
    return min(nameplates, key=lambda box: abs(_center(box)[1] - height * NAMEPLATE_Y))


def _read_word(image):
    """OCR an enlarged single-word crop; return the middle word if name-like."""
    words = ocr_words(_upscale(image, WORD_UPSCALE), scale=WORD_UPSCALE)
    if not words:
        return None
    # Neighbouring text can leak into the padding; keep the middle word.
    middle = (image.width / 2, image.height / 2)
    text = min(words, key=lambda w: (_center(w[2])[0] - middle[0]) ** 2
               + (_center(w[2])[1] - middle[1]) ** 2)[0]
    text = re.sub(r"[^A-Za-z0-9]", "", text)
    return text if _NAME.fullmatch(text) else None


def read_character_name(screenshots):
    """Return the character name in a client's recent screenshots, or None.

    `screenshots` is a list of PIL images, oldest first; a single image works
    too but misreads more often during fights.
    """
    if isinstance(screenshots, Image.Image):
        screenshots = [screenshots]
    screenshots = [s for s in screenshots or () if s is not None]
    if not screenshots or _engine is None:
        return None
    screenshot = _merge(screenshots)
    box = _find_name_word(screenshot)
    if box is None:
        return None

    left, top, right, bottom = box
    word = screenshot.crop((int(left) - WORD_PADDING, int(top) - 4,
                            int(right) + WORD_PADDING + 1, int(bottom) + 5))
    name = _read_word(word)
    if name is None or name != _read_word(_high_contrast(word)):
        return None
    return name
