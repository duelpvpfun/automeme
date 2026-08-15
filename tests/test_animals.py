"""Cute-animal category: name extraction, name captioning, alternation."""

from __future__ import annotations

from automeme import captioning, categories, settings_store
from automeme.captioning import extract_animal_name
from automeme.models import Candidate


def test_category_detection():
    assert categories.category_for("aww") == categories.ANIMAL
    assert categories.category_for("rarepuppers") == categories.ANIMAL
    assert categories.category_for("memes") == categories.MEME
    assert categories.category_for("dankmemes") == categories.MEME


def test_extract_animal_name():
    assert extract_animal_name("This is Waffles, my new pup") == "Waffles"
    assert extract_animal_name("Meet Luna!") == "Luna"
    assert extract_animal_name("say hi to Biscuit") == "Biscuit"
    assert extract_animal_name("my dog Cooper being derpy") == "Cooper"
    assert extract_animal_name("Bandit, the cat, judging me") == "Bandit"
    assert extract_animal_name("her name is Nala") == "Nala"


def test_extract_rejects_non_names():
    assert extract_animal_name("This is the cutest thing ever") == ""
    assert extract_animal_name("Look at this good boy") == ""
    assert extract_animal_name("just a happy pupper") == ""


def _cand(subject, title):
    return Candidate(source="memeapi", source_id="1", subject=subject, title=title,
                     image_url="http://x/y.png", phash="ab")


def test_animal_named_caption(env):
    settings_store.update({"caption_mode": "ai", "animal_name_caption": True})
    cap = captioning.generate(_cand("aww", "This is Mochi, adopted today"))
    assert cap == "Mochi"


def test_animal_without_name_no_caption_when_not_ai(env):
    settings_store.update({"caption_mode": "none", "animal_name_caption": True})
    cap = captioning.generate(_cand("aww", "look at this floof"))
    assert cap == ""


def test_meme_still_gets_ai_caption(env):
    settings_store.update({"caption_mode": "ai"})
    cap = captioning.generate(_cand("memes", "relatable content"))
    assert cap and cap == cap.lower()


def test_wif_prop_jokes(env):
    assert captioning.animal_caption(_cand("aww", "This is Rex, my dog with a backpack")) == "rex wif backpack"
    assert captioning.animal_caption(_cand("aww", "Meet Luna wearing sunglasses")) == "luna wif shades"
    assert captioning.animal_caption(_cand("aww", "Akimba in a tiny hat")) == "akimba wif hat"


def test_prop_without_name(env):
    # No detectable name, but a prop -> "wif <prop>"
    assert captioning.animal_caption(_cand("aww", "a doggo with a cute little scarf")) == "wif scarf"


def test_no_name_no_prop_empty(env):
    assert captioning.animal_caption(_cand("aww", "just a happy floof")) == ""
