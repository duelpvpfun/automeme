"""Concrete, independent safety checks.

Each check is deliberately narrow and conservative. Text-based checks operate on
the union of title + caption + OCR text so that words baked into an image are
screened just like a caption.

The word/pattern lists below are intentionally screening tools, not an attempt
to be exhaustive. They bias strongly toward rejection when in doubt.
"""

from __future__ import annotations

import re

from .base import CheckResult, SafetyContext, Verdict
from .registry import register

# ---------------------------------------------------------------------------
# Lexicons / patterns
# ---------------------------------------------------------------------------

# Slurs / hate: kept as regex fragments matched on word boundaries. This is a
# screening list; uncertain cases still get rejected by policy elsewhere.
_HATE_PATTERNS = [
    r"\bn[i1]gg", r"\bf[a4]gg", r"\bk[i1]ke\b", r"\bsp[i1]c\b", r"\bch[i1]nk\b",
    r"\btr[a4]nn(y|ie)", r"\bret[a4]rd", r"\bwetback", r"\bg[o0][o0]k\b",
    r"\bcoon\b", r"\bdyke\b", r"\bbeaner\b", r"\braghead", r"\bsandnigger",
    r"\bwhite power\b", r"\bheil hitler\b", r"\bgas the\b", r"\b1488\b",
    r"\bkkk\b", r"\bnazi scum\b",
]

_HARASSMENT_PATTERNS = [
    r"\bkill your ?self\b", r"\bkys\b", r"\byou should die\b",
    r"\bi hope you die\b", r"\bneck yourself\b",
]

_SEXUAL_PATTERNS = [
    r"\bporn\b", r"\bxxx\b", r"\bn[s5]fw\b", r"\bnudes?\b", r"\bonlyfans\b",
    r"\bblowjob\b", r"\bcum\b", r"\bdeepthroat\b", r"\bhentai\b", r"\bcreampie\b",
    r"\bchild\s*porn\b", r"\bcp\b", r"\bloli\b", r"\bunderage\b",
]

_GRAPHIC_PATTERNS = [
    r"\bbeheading\b", r"\bgore\b", r"\bdead body\b", r"\bsuicide\b",
    r"\bself[\s-]?harm\b", r"\bmutilat", r"\bmassacre\b", r"\bexecution video\b",
]

_SCAM_PATTERNS = [
    r"\bfree\s+(money|crypto|bitcoin|btc|eth|nft|giveaway)\b",
    r"\bairdrop\b", r"\bclaim your\b", r"\bdouble your\b", r"\bguaranteed returns?\b",
    r"\bdm me to\b", r"\bpump\b.*\bdump\b", r"\bpresale\b", r"\bwhitelist\b",
    r"\bconnect your wallet\b", r"\bseed phrase\b", r"\bget rich\b",
    r"\b1000x\b", r"\bmoonshot\b", r"\bfinancial advice\b",
]

_POLITICAL_PATTERNS = [
    r"\btrump\b", r"\bbiden\b", r"\bkamala\b", r"\bharris\b", r"\bobama\b",
    r"\bdemocrat", r"\brepublican", r"\bgop\b", r"\bmaga\b", r"\bantifa\b",
    r"\bleft[\s-]?wing\b", r"\bright[\s-]?wing\b", r"\babortion\b", r"\bpro[\s-]?life\b",
    r"\bpro[\s-]?choice\b", r"\bimmigration\b", r"\bwoke\b", r"\bgun control\b",
    r"\bpalestin", r"\bisrael", r"\bhamas\b", r"\bukraine\b", r"\bputin\b",
    r"\belection fraud\b", r"\bvaccine\b", r"\bcovid\b", r"\bplandemic\b",
]

_MISINFO_PATTERNS = [
    r"\bflat earth\b", r"\bstolen election\b", r"\bfake pandemic\b",
    r"\bmicrochip\b.*\bvaccine\b", r"\bdeep state\b", r"\bqanon\b",
    r"\b5g\b.*\b(virus|covid)\b", r"\bchemtrails\b",
]

# Crypto contract addresses (ETH/EVM 0x..40 hex, Solana base58 32-44)
_ETH_ADDR = re.compile(r"0x[a-fA-F0-9]{40}\b")
_SOL_ADDR = re.compile(r"\b[1-9A-HJ-NP-Za-km-z]{32,44}\b")
_CONTRACT_WORDS = re.compile(r"(?i)\b(contract address|ca[:\s]|token address|\$[A-Z]{2,6}\b)")

# URLs / suspicious links
_URL = re.compile(r"(?i)\b((?:https?://|www\.)[^\s]+|[a-z0-9-]+\.(?:com|net|io|xyz|gg|co|link|to|ru|cn|app|fun|top|click)\b)")
_SHORTENERS = {
    "bit.ly", "tinyurl.com", "t.co", "goo.gl", "ow.ly", "buff.ly", "cutt.ly",
    "is.gd", "rebrand.ly", "shorturl.at", "linktr.ee", "rb.gy",
}

# Private info (PII)
_EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_PHONE = re.compile(r"\b(?:\+?\d[\d\s().-]{7,}\d)\b")
_SSN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_CC = re.compile(r"\b(?:\d[ -]?){13,16}\b")

# Impersonation / verified-brand bait
_IMPERSONATION = re.compile(
    r"(?i)\b(official (account|page)|verified|elon musk|@?jack\b|customer support|"
    r"support team|account suspended|verify your account)\b"
)

# Watermark / advertisement markers commonly seen on scraped ad images
_AD_PATTERNS = [
    r"(?i)\bsponsored\b", r"(?i)\bad(vertisement)?\b\s*$", r"(?i)\bpromo code\b",
    r"(?i)\buse code\b", r"(?i)\bshop now\b", r"(?i)\blimited offer\b",
    r"(?i)\bbuy now\b", r"(?i)\bswipe up\b", r"(?i)\blink in bio\b",
    r"(?i)\bdiscount\b", r"(?i)\bwww\.\S+\.(shop|store)\b",
]


def _match_any(patterns, text: str) -> list[str]:
    hits = []
    for p in patterns:
        m = re.search(p, text, re.IGNORECASE)
        if m:
            hits.append(m.group(0))
    return hits


# ---------------------------------------------------------------------------
# Text-based checks (each independent)
# ---------------------------------------------------------------------------


class _TextCheck:
    name = "text"
    patterns: list[str] = []
    concern = "text"

    def run(self, ctx: SafetyContext) -> CheckResult:
        text = ctx.all_text
        hits = _match_any(self.patterns, text)
        if hits:
            return CheckResult(self.name, Verdict.REJECT,
                               f"{self.concern} content detected",
                               {"matches": hits[:5]})
        return CheckResult(self.name, Verdict.PASS)


class HateCheck(_TextCheck):
    name = "hate_slur"
    patterns = _HATE_PATTERNS
    concern = "hate speech / slur"


class HarassmentCheck(_TextCheck):
    name = "harassment"
    patterns = _HARASSMENT_PATTERNS
    concern = "harassment"


class SexualTextCheck(_TextCheck):
    name = "sexual_text"
    patterns = _SEXUAL_PATTERNS
    concern = "sexual / nudity"


class GraphicTextCheck(_TextCheck):
    name = "graphic_text"
    patterns = _GRAPHIC_PATTERNS
    concern = "graphic / violent"


class ScamCheck(_TextCheck):
    name = "scam"
    patterns = _SCAM_PATTERNS
    concern = "scam / financial bait"


class PoliticalCheck(_TextCheck):
    name = "political"
    patterns = _POLITICAL_PATTERNS
    concern = "political controversy"


class MisinfoCheck(_TextCheck):
    name = "misinformation"
    patterns = _MISINFO_PATTERNS
    concern = "misinformation"


class AdvertisementCheck(_TextCheck):
    name = "advertisement"
    patterns = _AD_PATTERNS
    concern = "watermarked advertisement"


class CryptoCheck:
    name = "crypto_contract"

    def run(self, ctx: SafetyContext) -> CheckResult:
        text = ctx.all_text
        if _ETH_ADDR.search(text) or _CONTRACT_WORDS.search(text):
            return CheckResult(self.name, Verdict.REJECT,
                               "crypto contract address / ticker detected")
        # Solana-like base58 strings are noisy: only reject if crypto words nearby.
        if _SOL_ADDR.search(text) and re.search(r"(?i)\b(sol|solana|token|coin|crypto|pump)\b", text):
            return CheckResult(self.name, Verdict.REJECT,
                               "possible Solana contract address in crypto context")
        return CheckResult(self.name, Verdict.PASS)


class LinkCheck:
    name = "suspicious_link"

    def run(self, ctx: SafetyContext) -> CheckResult:
        text = ctx.all_text
        lowered = text.lower()
        for shortener in _SHORTENERS:
            if shortener in lowered:
                return CheckResult(self.name, Verdict.REJECT,
                                   f"URL shortener / suspicious link: {shortener}")
        urls = _URL.findall(text)
        flat = [u[0] if isinstance(u, tuple) else u for u in urls]
        if flat:
            # Any embedded link in a meme is a strong ad/scam signal -> uncertain.
            return CheckResult(self.name, Verdict.UNCERTAIN,
                               "embedded link(s) present", {"links": flat[:5]})
        return CheckResult(self.name, Verdict.PASS)


class PIICheck:
    name = "private_info"

    def run(self, ctx: SafetyContext) -> CheckResult:
        text = ctx.all_text
        if _SSN.search(text):
            return CheckResult(self.name, Verdict.REJECT, "possible SSN present")
        if _CC.search(text) and re.search(r"(?i)\b(cvv|card|visa|mastercard)\b", text):
            return CheckResult(self.name, Verdict.REJECT, "possible payment card present")
        if _EMAIL.search(text):
            return CheckResult(self.name, Verdict.UNCERTAIN, "email address present")
        if _PHONE.search(text) and re.search(r"(?i)\b(call|text|whatsapp|phone|number)\b", text):
            return CheckResult(self.name, Verdict.UNCERTAIN, "possible phone number present")
        return CheckResult(self.name, Verdict.PASS)


class ImpersonationCheck:
    name = "impersonation"

    def run(self, ctx: SafetyContext) -> CheckResult:
        if _IMPERSONATION.search(ctx.all_text):
            return CheckResult(self.name, Verdict.REJECT,
                               "impersonation / account-bait language")
        return CheckResult(self.name, Verdict.PASS)


class InternalLeakCheck:
    """Ensures we never publish internal instructions / system output."""

    name = "internal_leak"
    _markers = re.compile(
        r"(?i)(system prompt|you are (an?|the) (ai|assistant|model|language model)|"
        r"as an ai\b|ignore (all )?previous instructions|api[_-]?key|secret[_-]?key|"
        r"bearer token|BEGIN (RSA|OPENSSH|PRIVATE)|traceback \(most recent|"
        r"</?safety|automeme\.|settings_store|def run\(|import os\b)"
    )

    def run(self, ctx: SafetyContext) -> CheckResult:
        blob = " ".join([ctx.title, ctx.caption, ctx.ocr_text])
        if self._markers.search(blob):
            return CheckResult(self.name, Verdict.REJECT,
                               "internal/system text detected in content")
        return CheckResult(self.name, Verdict.PASS)


# ---------------------------------------------------------------------------
# Image / metadata checks
# ---------------------------------------------------------------------------


class ImageSanityCheck:
    name = "image_sanity"

    def run(self, ctx: SafetyContext) -> CheckResult:
        if not ctx.local_path:
            return CheckResult(self.name, Verdict.REJECT, "no local image")
        if ctx.width < 200 or ctx.height < 200:
            return CheckResult(self.name, Verdict.REJECT, "image too small")
        ar = ctx.width / ctx.height if ctx.height else 0
        if ar and (ar > 5 or ar < 0.2):
            return CheckResult(self.name, Verdict.UNCERTAIN,
                               "extreme aspect ratio (banner/ad-like)")
        return CheckResult(self.name, Verdict.PASS)


class OCRAvailabilityCheck:
    """If an image is text-heavy but OCR is unavailable, we cannot screen the
    baked-in text -> uncertain (rejected under fail-closed policy)."""

    name = "ocr_coverage"

    def run(self, ctx: SafetyContext) -> CheckResult:
        if ctx.ocr_available:
            return CheckResult(self.name, Verdict.PASS)
        # OCR not available: without it we can't read text inside the image.
        if ctx.strictness == "high":
            return CheckResult(self.name, Verdict.UNCERTAIN,
                               "OCR unavailable; cannot screen in-image text")
        return CheckResult(self.name, Verdict.PASS,
                           "OCR unavailable (allowed at current strictness)")


class NudityImageCheck:
    """Optional ML nudity detector (NudeNet). Fail-closed at high strictness.

    If NudeNet is not installed we cannot analyze pixels for nudity. At high
    strictness this returns UNCERTAIN (=> reject). Install ``nudenet`` for real
    pixel-level screening.
    """

    name = "nudity_image"

    def __init__(self) -> None:
        self._detector = None
        self._available = False
        try:
            from nudenet import NudeDetector  # type: ignore

            self._detector = NudeDetector()
            self._available = True
        except Exception:
            self._available = False

    _UNSAFE_LABELS = {
        "FEMALE_GENITALIA_EXPOSED", "MALE_GENITALIA_EXPOSED",
        "FEMALE_BREAST_EXPOSED", "ANUS_EXPOSED", "BUTTOCKS_EXPOSED",
    }

    def run(self, ctx: SafetyContext) -> CheckResult:
        if not self._available or self._detector is None:
            if ctx.strictness == "high":
                return CheckResult(self.name, Verdict.UNCERTAIN,
                                   "nudity detector unavailable; cannot verify image")
            return CheckResult(self.name, Verdict.PASS,
                               "nudity detector unavailable (allowed at strictness)")
        try:
            detections = self._detector.detect(ctx.local_path)
        except Exception as exc:
            return CheckResult(self.name, Verdict.UNCERTAIN,
                               f"nudity detector error: {exc}")
        for d in detections or []:
            label = str(d.get("class", "")).upper()
            score = float(d.get("score", 0))
            if label in self._UNSAFE_LABELS and score >= 0.5:
                return CheckResult(self.name, Verdict.REJECT,
                                   f"nudity detected: {label} ({score:.2f})")
        return CheckResult(self.name, Verdict.PASS)


def register_defaults() -> None:
    for check in (
        HateCheck(), HarassmentCheck(), SexualTextCheck(), GraphicTextCheck(),
        ScamCheck(), PoliticalCheck(), MisinfoCheck(), AdvertisementCheck(),
        CryptoCheck(), LinkCheck(), PIICheck(), ImpersonationCheck(),
        InternalLeakCheck(), ImageSanityCheck(), OCRAvailabilityCheck(),
        NudityImageCheck(),
    ):
        register(check)
