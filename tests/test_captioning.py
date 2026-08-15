"""AI/title/none caption generation + humanizer + safety fallback."""

from __future__ import annotations

from automeme import captioning, settings_store
from automeme.captioning import _humanize
from automeme.models import Candidate


def _cand(title="funny meme", subject="memes"):
    return Candidate(source="reddit", source_id="1", title=title, subject=subject,
                     image_url="http://x/y.png", phash="ab")


def test_none_mode_empty(env):
    settings_store.set_value("caption_mode", "none")
    assert captioning.generate(_cand()) == ""


def test_ai_mode_produces_short_lowercase(env):
    settings_store.set_value("caption_mode", "ai")
    cap = captioning.generate(_cand())
    assert cap  # non-empty
    assert cap == cap.lower()
    assert len(cap.split()) <= 7
    assert "#" not in cap and "@" not in cap
    assert not cap.endswith(".")


def test_humanizer_strips_ai_tells():
    assert _humanize("When you finally get it") == ""       # 'when you' tell
    assert _humanize("POV: you are here") == ""             # 'pov:' tell
    assert _humanize("As an AI, here's a caption") == ""    # 'as an ai' tell
    assert _humanize('"This meme is hilarious"') == ""      # 'this meme'/'hilarious'


def test_humanizer_normalizes():
    assert _humanize("  IT ME.  ") == "it me"
    assert _humanize("Too Real 😭🔥 #relatable") == "too real"
    assert _humanize("this is a very long caption that goes on and on and on forever") == ""


def test_ai_caption_passes_text_safety(env):
    # Built-in reactions must all clear the text-based safety checks (the caption
    # screen only inspects text, not image checks).
    from automeme.safety.base import SafetyContext, Verdict
    from automeme.safety.checks import (
        HateCheck, HarassmentCheck, SexualTextCheck, GraphicTextCheck, ScamCheck,
        PoliticalCheck, MisinfoCheck, ImpersonationCheck, InternalLeakCheck,
    )
    settings_store.set_value("caption_mode", "ai")
    text_checks = [HateCheck(), HarassmentCheck(), SexualTextCheck(),
                   GraphicTextCheck(), ScamCheck(), PoliticalCheck(),
                   MisinfoCheck(), ImpersonationCheck(), InternalLeakCheck()]
    for _ in range(20):
        cap = captioning.generate(_cand())
        ctx = SafetyContext(title=cap, caption=cap, ocr_available=True)
        for chk in text_checks:
            assert chk.run(ctx).verdict != Verdict.REJECT, f"{cap!r} failed {chk.name}"


def test_title_mode_respects_maxlen(env):
    settings_store.update({"caption_mode": "title", "max_caption_length": 10})
    cap = captioning.generate(_cand(title="a very long title here"))
    assert len(cap) <= 10
