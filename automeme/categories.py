"""Content categories: memes vs cute animals.

Category is derived from the source subject (subreddit) at runtime, so no schema
change is needed. Used to alternate posting between memes and viral cute animals.
"""

from __future__ import annotations

MEME = "meme"
ANIMAL = "animal"

# Subreddits treated as "cute animal" content.
ANIMAL_SUBREDDITS: tuple[str, ...] = (
    "aww",
    "rarepuppers",
    "cats",
    "dogpictures",
    "dogs",
    "AnimalsBeingBros",
    "AnimalsBeingDerps",
    "Eyebleach",
    "babyelephantgifs",
    "WhatsWrongWithYourDog",
    "IllegallySmolCats",
    "tuckedinkitties",
    "catpictures",
    "goldenretrievers",
    "shibe",
    "shibainu",
    "shiba_inu",
)

_ANIMAL_SET = {s.lower() for s in ANIMAL_SUBREDDITS}


def category_for(subject: str) -> str:
    return ANIMAL if (subject or "").lower() in _ANIMAL_SET else MEME
