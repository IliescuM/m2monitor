"""Tests for charname.read_character_name on synthetic screenshots.

The app reads each client about once a second and shows a name once the same
reading comes back twice, so a single read may come back empty. These tests
check what the app relies on: every kind of nameplate is read correctly
within a few attempts, and other text never comes back as a name.

Needs Windows with an OCR language installed. Run: python -m pytest tests
"""
import random
import sys

import pytest
from PIL import ImageDraw

sys.path.insert(0, __file__.rsplit("tests", 1)[0])
sys.path.insert(0, __file__.rsplit("\\", 1)[0].rsplit("/", 1)[0])

if sys.platform != "win32":
    pytest.skip("Windows OCR is only available on Windows", allow_module_level=True)

import charname  # noqa: E402
import synth  # noqa: E402

if charname._engine is None:
    pytest.skip("no OCR language installed in Windows", allow_module_level=True)

ATTEMPTS = 5
NAMES = ["Ravenshade", "LotusBlade", "Silvermoon", "Nightwolf", "Kardra",
         "IronMonk", "DragonHeart", "Moonfall", "Stormrider"]


def reads(name, attempts=ATTEMPTS, seed=0, **scene):
    """Read up to `attempts` fresh scenes; return every non-empty result."""
    rng = random.Random(seed)
    results = []
    for _ in range(attempts):
        result = charname.read_character_name(synth.scene(rng, name=name, **scene))
        if result is not None:
            results.append(result)
            if result == name:
                break
    return results


@pytest.mark.parametrize("tier", range(len(synth.TIERS)), ids=[t[0] for t in synth.TIERS])
@pytest.mark.parametrize("guild", [None, "Silk Road"], ids=["no-guild", "guild"])
def test_every_rank_title_is_read(tier, guild):
    name = NAMES[tier]
    results = reads(name, seed=tier * 10 + (guild is not None), tier=tier, guild=guild)
    assert name in results
    assert all(r == name for r in results), f"misread as {results}"


@pytest.mark.parametrize("title_words", [0, 2, 3])
def test_title_length(title_words):
    results = reads("Ravenshade", seed=100 + title_words, tier=0, title_words=title_words)
    assert "Ravenshade" in results
    assert all(r == "Ravenshade" for r in results), f"misread as {results}"


def test_read_during_a_fight():
    results = reads("Silvermoon", attempts=10, seed=200, tier=1, guild="Silk Road", clutter=True)
    assert "Silvermoon" in results
    assert all(r == "Silvermoon" for r in results), f"misread as {results}"


def _frames(draw_plates, seed):
    """FRAMES screenshots of a noise background with draw_plates(draw) applied."""
    rng = random.Random(seed)
    base = synth.background(rng)
    frames = []
    for _ in range(synth.FRAMES):
        image = base.copy()
        draw_plates(ImageDraw.Draw(image), rng)
        frames.append(image)
    return frames


DISTRACTORS = {
    # A monster plate whose first name word sits exactly in the middle.
    "centred-monster": lambda draw, rng: synth.draw_plate(
        draw, 400, 268, 97, "Lemures", "Soldat*", name_color=synth.MONSTER_COLOR),
    # Another player standing beside the character.
    "player-off-centre": lambda draw, rng: synth.draw_plate(
        draw, 400 + rng.choice([-1, 1]) * rng.uniform(30, 60), 268, 50, "Bun", "Otherguy"),
    # Only a guild line, as when the name below it is covered.
    "guild-only": lambda draw, rng: synth._text(draw, (360, 255), "Silk Road", synth.GUILD_COLOR),
}


@pytest.mark.parametrize("kind", DISTRACTORS)
def test_other_text_is_never_a_name(kind):
    for seed in range(3):
        assert charname.read_character_name(_frames(DISTRACTORS[kind], seed)) is None


def test_no_screenshots():
    assert charname.read_character_name([]) is None
    assert charname.read_character_name(None) is None
