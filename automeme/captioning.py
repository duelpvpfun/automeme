"""Caption generation.

Modes (``caption_mode`` setting):

* ``none``  -- no caption (default; pure image repost).
* ``title`` -- reuse the source post's title, trimmed.
* ``ai``    -- generate a short, deadpan, lowercase, internet-native caption in
               the spirit of accounts like @s8n.

Design goals for ``ai`` mode:

* **Doesn't read like AI.** Output is short (usually 1-5 words), lowercase, no
  hashtags, no emoji spam, no "when you…" essay captions, no quotation marks,
  no trailing period, no explanations. A humanizer strips common AI tells.
* **No external service required.** By default it draws from a curated pool of
  human-written deadpan reactions (weighted, with anti-repetition), so it needs
  no API key and spends nothing. If an OpenAI-compatible key is configured it
  will use that instead and still run the same humanizer + safety pass.
* **Safety first.** Whatever text is produced is returned to the caller, which
  runs it through the full safety pipeline before posting. If the caption fails
  safety, the caller falls back to no caption (the image still posts).
* **Never touches the image.**
"""

from __future__ import annotations

import random
import re

from sqlalchemy import select

from . import settings_store
from .config import get_config
from .db import session_scope
from .models import Candidate, CandidateStatus

# Curated, human-written deadpan reactions. Intentionally generic so they land
# on a wide range of memes, and short so they read like a real shitposter.
_REACTIONS: tuple[str, ...] = (
    "me", "it me", "this is the one", "unfortunately me", "every time",
    "no notes", "the accuracy", "it's true", "painfully real", "who allowed this",
    "not the", "the audacity", "screaming", "i felt this", "real as hell",
    "why is this me", "too real", "the realest", "and i mean this", "on god",
    "certified moment", "this changed me", "i'm telling everyone", "help",
    "he's just like me", "she's just like me", "we are so back", "it's over",
    "found the meme of all time", "posting this and logging off", "goodnight",
    "average day", "nobody talks about this", "the disrespect", "i can't breathe",
    "genuinely though", "this one hurt", "no because why", "sir", "ma'am",
)

# Subject-flavored openers occasionally used to add slight variety.
_ANIMAL_SUBJECTS = {"aww", "animalsbeingderps", "rarepuppers", "cats", "dogs"}
_ANIMAL_REACTIONS = ("him", "her", "the goat", "protect at all costs", "cutest thing alive")

# Patterns that scream "AI wrote this" -> stripped/rejected.
_AI_TELLS = re.compile(
    r"(?i)\b(as an ai|when you|that feeling when|pov|caption|here('|)?s a|"
    r"funny caption|this meme|hilarious|lol so|check out|don'?t forget to|"
    r"like and|follow for|in this image|the image shows)\b"
)


def _humanize(text: str) -> str:
    """Force short, lowercase, tell-free output. Returns '' if nothing usable."""
    if not text:
        return ""
    # Check for AI tells on the raw text first (before punctuation is stripped).
    if _AI_TELLS.search(text):
        return ""
    t = text.strip()
    # Take first line only; models sometimes explain on later lines.
    t = t.splitlines()[0]
    # Strip surrounding quotes / markdown.
    t = t.strip().strip('"').strip("'").strip("*").strip()
    # Remove hashtags and @handles (impersonation/spam risk + AI tell).
    t = re.sub(r"[#@]\w+", "", t)
    # Remove emoji-ish and non-basic symbols, keep letters/space/basic punct.
    t = re.sub(r"[^\w\s',.!?-]", "", t, flags=re.UNICODE)
    # Collapse whitespace, lowercase (s8n vibe).
    t = re.sub(r"\s+", " ", t).strip().lower()
    # Drop trailing period(s) but keep a single ? or ! if present.
    t = re.sub(r"\.+$", "", t).strip()
    if _AI_TELLS.search(t):
        return ""
    # Length guard: keep it punchy.
    words = t.split()
    if len(words) > 7:
        return ""
    return t


def _recent_captions(limit: int = 30) -> set[str]:
    with session_scope() as s:
        rows = s.execute(
            select(Candidate.caption)
            .where(Candidate.status == CandidateStatus.POSTED.value)
            .order_by(Candidate.id.desc())
            .limit(limit)
        ).scalars()
        return {(c or "").strip().lower() for c in rows if c}


def _local_caption(cand: Candidate) -> str:
    recent = _recent_captions()
    pool = list(_REACTIONS)
    if cand.subject.lower() in _ANIMAL_SUBJECTS:
        pool = list(_ANIMAL_REACTIONS) + pool
    random.shuffle(pool)
    for choice in pool:
        if choice.lower() not in recent:
            return choice
    return random.choice(pool)


def _llm_caption(cand: Candidate) -> str:
    """Optional OpenAI-compatible generation. Returns '' if unavailable/fails."""
    cfg = get_config()
    key = cfg.openai_api_key
    if not key:
        return ""
    try:
        import httpx

        prompt = (
            "You write captions for a viral meme account like @s8n. "
            "Given a meme's context, reply with ONE caption only. Rules: "
            "lowercase, 1 to 5 words, deadpan and dry, no hashtags, no emojis, "
            "no quotation marks, no punctuation at the end, no explanation, "
            "must not describe the image. If nothing good, reply 'me'.\n\n"
            f"context: {cand.title or cand.subject}"
        )
        resp = httpx.post(
            f"{cfg.openai_base_url.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json={
                "model": cfg.openai_model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 20,
                "temperature": 1.0,
            },
            timeout=20.0,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]
    except Exception:
        return ""


# Words that look like names but aren't (avoid captioning "This"/"Meet" etc.).
_NAME_STOPWORDS = {
    "this", "meet", "my", "our", "the", "a", "an", "he", "she", "they", "his",
    "her", "here", "today", "just", "look", "say", "hi", "hello", "everyone",
    "reddit", "guys", "oc", "im", "i", "we", "adopted", "rescued", "found",
    "new", "little", "good", "best", "boy", "girl", "sir", "mr", "mrs", "ms",
    "doggo", "puppo", "kitty", "cat", "dog", "pup", "puppy", "kitten", "goodboy",
}

# Patterns that commonly introduce a pet's name in a Reddit title.
_NAME_PATTERNS = [
    re.compile(r"(?i)\bthis is ([A-Z][a-z]{1,15})"),
    re.compile(r"(?i)\bmeet ([A-Z][a-z]{1,15})"),
    re.compile(r"(?i)\bsay (?:hi|hello) to ([A-Z][a-z]{1,15})"),
    re.compile(r"(?i)\bmy (?:dog|cat|pup|puppy|kitten|kitty|boy|girl|doggo|floof|good boy|good girl)[,]? ([A-Z][a-z]{1,15})"),
    re.compile(r"(?i)\bnamed ([A-Z][a-z]{1,15})"),
    re.compile(r"(?i)\bhis name is ([A-Z][a-z]{1,15})"),
    re.compile(r"(?i)\bher name is ([A-Z][a-z]{1,15})"),
    re.compile(r"([A-Z][a-z]{1,15}),? (?:the|my|our) (?:dog|cat|pup|puppy|kitten|kitty|doggo|floof)"),
    re.compile(r"(?i)\b(?:me and|with|and) ([A-Z][a-z]{1,15})\b"),
    # A name at the very start followed by an action/preposition
    # (e.g. "Akimba snuggling", "Akimba in a hat", "Bonnie with her toy").
    re.compile(r"^([A-Z][a-z]{1,15}) (?:is |the |being |says |in |on |at |wearing |with |and |having |got |enjoy|loves|found|discover|snuggl|napp|sleep)"),
]


def extract_animal_name(title: str) -> str:
    """Best-effort extraction of a pet's name from a title. '' if none found."""
    if not title:
        return ""
    # Strip common leading tags like "[OC]", "(OC)", "PsBattle:" so start-anchored
    # patterns can still see a leading name.
    title = re.sub(r"^\s*(\[[^\]]*\]|\([^)]*\)|[A-Za-z]+:)\s*", "", title).strip()
    for pat in _NAME_PATTERNS:
        m = pat.search(title)
        if not m:
            continue
        name = m.group(1).strip()
        if name.lower() in _NAME_STOPWORDS:
            continue
        return name
    return ""


# Objects/props sometimes visible in a pet photo, keyed by title keywords.
# Used to build playful "wif" meme-jokes (doge-speak: "wif" = "with"),
# e.g. a dog with a backpack -> "wif backpack".
_PROP_KEYWORDS: dict[str, str] = {
    "backpack": "backpack", "hat": "hat", "sunglasses": "shades", "glasses": "glasses",
    "sweater": "sweater", "hoodie": "hoodie", "bowtie": "bowtie", "tie": "tie",
    "boots": "boots", "shoes": "shoes", "socks": "socks", "scarf": "scarf",
    "blanket": "blanket", "toy": "toy", "ball": "ball", "stick": "stick",
    "umbrella": "umbrella", "costume": "costume", "bandana": "bandana",
    "flowers": "flowers", "hat": "hat", "crown": "crown", "cone": "cone",
    "bag": "bag", "helmet": "helmet", "goggles": "goggles", "camera": "camera",
    "book": "book", "coffee": "coffee", "beer": "beer", "guitar": "guitar",
    "snow": "snow", "beach": "beach", "car": "car", "bike": "bike",
}


def _detect_prop(title: str) -> str:
    low = (title or "").lower()
    for kw, label in _PROP_KEYWORDS.items():
        if kw in low:
            return label
    return ""


def animal_caption(cand: Candidate) -> str:
    """Build a small, playful caption for an animal post.

    Combines the pet's name with a visual meme-joke when a prop is detected,
    e.g. dog wearing a backpack named Rex -> "rex wif backpack". Falls back to
    just the name, then to a cute reaction.
    """
    name = extract_animal_name(cand.title or "")
    prop = _detect_prop(cand.title or "")

    if name and prop:
        return f"{name.lower()} wif {prop}"     # "rex wif backpack"
    if prop:
        return f"wif {prop}"                     # "wif backpack"
    if name:
        return name                              # "Rex"
    return ""


def generate(cand: Candidate) -> str:
    """Return a caption for a candidate based on the current caption_mode.

    The returned text is NOT yet safety-checked -- the caller must screen it.
    For cute-animal posts, if the animal has a name in the title, that name is
    used as the caption (e.g. "Waffles"), optionally with a prop meme-joke
    (e.g. "rex wif backpack").
    """
    from .categories import ANIMAL, category_for

    mode = settings_store.get("caption_mode", "none")

    # Animal posts: prefer name (+ playful prop joke) as caption when available.
    if category_for(cand.subject) == ANIMAL and settings_store.get(
        "animal_name_caption", True
    ):
        cap = animal_caption(cand)
        if cap:
            return cap
        # No name/prop found: animals stay caption-free unless meme captioning on.
        if mode != "ai":
            return ""

    if mode == "none":
        return ""
    if mode == "title":
        max_len = int(settings_store.get("max_caption_length", 0))
        if max_len <= 0:
            return ""
        return (cand.title or "").strip()[:max_len]
    if mode == "ai":
        raw = _llm_caption(cand)
        caption = _humanize(raw)
        if not caption:
            caption = _humanize(_local_caption(cand)) or _local_caption(cand)
        return caption
    return ""
