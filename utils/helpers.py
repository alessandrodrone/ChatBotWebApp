"""
Funzioni helper generiche.
"""

import re


def sanitize_phone(phone: str) -> str:
    """Rimuove spazi e caratteri non numerici dal numero."""
    return re.sub(r"[^\d]", "", phone)


def truncate(text: str, max_len: int = 100) -> str:
    """Tronca una stringa aggiungendo '…' se necessario."""
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "…"
