"""Arabic text normalization for TTS transcripts.

Adapted from the existing arabic_tts/text.py normalization rules:
- strip tatweel (ـ)
- normalize hamza forms (أ إ آ → ا, ؤ → و, ئ → ي)
- strip diacritics / tashkeel
- convert Arabic-Indic digits to ASCII
"""
from __future__ import annotations

TATWEEL = "ـ"
HAMZA_TRANSLATION = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ؤ": "و", "ئ": "ي"})
DIACRITICS = "ًٌٍَُِّْٰٓ"
ARABIC_INDIC_DIGITS = {chr(0x0660 + i): str(i) for i in range(10)}


def strip_tatweel(text: str) -> str:
    """Remove tatweel (ـ) which is elongation markup, not pronounced."""
    return text.replace(TATWEEL, "")


def normalize_hamza(text: str) -> str:
    """Map hamza forms to their plain alif/waw/ya equivalents."""
    return text.translate(HAMZA_TRANSLATION)


def strip_diacritics(text: str) -> str:
    """Remove harakat/tashkeel so the model sees bare letters."""
    return "".join(ch for ch in text if ch not in DIACRITICS)


def normalize_numbers(text: str) -> str:
    """Convert Arabic-Indic digits (٠-٩) to ASCII digits (0-9)."""
    return "".join(ARABIC_INDIC_DIGITS.get(ch, ch) for ch in text)


def normalize_arabic(text: str) -> str:
    """Canonical Arabic text form for TTS transcripts."""
    text = strip_tatweel(text)
    text = normalize_hamza(text)
    text = strip_diacritics(text)
    text = normalize_numbers(text)
    text = " ".join(text.split())
    return text.strip()


def is_arabic(text: str) -> bool:
    """Return True when the string contains Arabic script characters."""
    return any("\u0600" <= ch <= "\u06FF" for ch in text)
